"""Executable contract tests for deterministic PR-scoped review diffs."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys
import types

import pytest


SCRIPT = (
    Path(__file__).parents[1]
    / ".github/actions/prepare-review-diff/prepare_review_diff.py"
)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, text=True, capture_output=True
    ).stdout.strip()


def commit(repo: Path, message: str) -> str:
    git(repo, "add", "--all")
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


@dataclass
class RepositoryHistory:
    repo: Path
    base: str
    previous: str
    head: str
    non_ancestor: str


@pytest.fixture
def history(tmp_path: Path) -> RepositoryHistory:
    repo = tmp_path / "consumer"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Test User")
    git(repo, "config", "user.email", "test@example.invalid")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    base = commit(repo, "base")
    (repo / "inside.txt").write_text("before\n", encoding="utf-8")
    previous = commit(repo, "previous")
    (repo / "inside.txt").write_text("after\n", encoding="utf-8")
    (repo / "decoy.txt").write_text("OUT_OF_PR\n", encoding="utf-8")
    head = commit(repo, "head")
    git(repo, "checkout", "-b", "side", base)
    (repo / "side.txt").write_text("side\n", encoding="utf-8")
    non_ancestor = commit(repo, "side")
    git(repo, "checkout", "main")
    return RepositoryHistory(repo, base, previous, head, non_ancestor)


@dataclass
class GhFixture:
    env: dict[str, str]
    log: Path


@pytest.fixture
def gh_fixture(tmp_path: Path) -> GhFixture:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    config = tmp_path / "gh.json"
    log = tmp_path / "gh.log"
    shim = bin_dir / "gh"
    shim.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys

config = json.loads(open(os.environ[\"GH_FIXTURE_CONFIG\"], encoding=\"utf-8\").read())
args = sys.argv[1:]
with open(os.environ[\"GH_FIXTURE_LOG\"], \"a\", encoding=\"utf-8\") as output:
    output.write(json.dumps(args) + \"\\n\")
if args[:1] == [\"api\"]:
    endpoint = next((arg for arg in args[1:] if not arg.startswith(\"-\")), \"\")
    if endpoint.endswith(\"/files\"):
        if config.get(\"files_error\"):
            print(\"files unavailable\", file=sys.stderr)
            raise SystemExit(1)
        print(json.dumps(config[\"files\"]))
    else:
        snapshots = config[\"metadata\"]
        index = config.setdefault(\"metadata_index\", 0)
        print(json.dumps(snapshots[min(index, len(snapshots) - 1)]))
        config[\"metadata_index\"] = index + 1
        open(os.environ[\"GH_FIXTURE_CONFIG\"], \"w\", encoding=\"utf-8\").write(json.dumps(config))
elif args[:2] == [\"pr\", \"diff\"]:
    if config.get(\"pr_diff_error\"):
        print(\"server diff unavailable\", file=sys.stderr)
        raise SystemExit(1)
    sys.stdout.write(config.get(\"pr_diff\", \"server diff\\n\"))
else:
    print(\"unexpected gh invocation\", file=sys.stderr)
    raise SystemExit(2)
""",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
            "GH_TOKEN": "test-token",
            "GH_FIXTURE_CONFIG": str(config),
            "GH_FIXTURE_LOG": str(log),
        }
    )
    return GhFixture(env, log)


def configure_gh(
    gh_fixture: GhFixture,
    *,
    base: str,
    head: str,
    files: list[dict[str, str]] | None = None,
    metadata: list[dict[str, object]] | None = None,
    files_error: bool = False,
    pr_diff_error: bool = False,
    pr_diff: str = "server diff\n",
) -> None:
    snapshot = {"base": {"sha": base}, "head": {"sha": head}}
    Path(gh_fixture.env["GH_FIXTURE_CONFIG"]).write_text(
        json.dumps(
            {
                "metadata": metadata or [snapshot, snapshot],
                "files": [files or [{"status": "modified", "filename": "inside.txt"}]],
                "files_error": files_error,
                "pr_diff_error": pr_diff_error,
                "pr_diff": pr_diff,
            }
        ),
        encoding="utf-8",
    )


def outputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    return tmp_path / "full.diff", tmp_path / "delta.diff", tmp_path / "scope.json"


def run_prepare(repo: Path, gh_fixture: GhFixture, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repository", "o/r", "--pr-number", "7", *extra],
        cwd=repo,
        env=gh_fixture.env,
        text=True,
        capture_output=True,
    )


def prepare_args(full: Path, delta: Path, manifest: Path, *extra: str) -> tuple[str, ...]:
    return (
        "--previous-sha",
        "",
        "--previous-full-hash",
        "",
        "--context-lines",
        "3",
        "--full-output",
        str(full),
        "--delta-output",
        str(delta),
        "--manifest-output",
        str(manifest),
        *extra,
    )


def test_first_round_writes_pr_scoped_full_diff_and_manifest(
    history: RepositoryHistory, gh_fixture: GhFixture, tmp_path: Path
) -> None:
    """Removing path restriction would leak the decoy into the reviewed full input."""
    configure_gh(gh_fixture, base=history.base, head=history.head)
    full, delta, manifest = outputs(tmp_path)

    result = run_prepare(history.repo, gh_fixture, *prepare_args(full, delta, manifest))

    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout)
    assert state == {
        "diff_ready": True,
        "diff_mode": "full",
        "head_sha": history.head,
        "base_sha": history.base,
        "full_diff_sha256": hashlib.sha256(full.read_bytes()).hexdigest(),
        "unchanged_since_previous": False,
        "warning": "",
    }
    assert "after" in full.read_text(encoding="utf-8")
    assert "OUT_OF_PR" not in full.read_text(encoding="utf-8")
    assert not delta.exists()
    assert json.loads(manifest.read_text(encoding="utf-8")) == {
        "schema": 1,
        "repository": "o/r",
        "pr_number": 7,
        "merge_base_sha": history.base,
        "head_sha": history.head,
        "files": [{"status": "modified", "filename": "inside.txt"}],
    }
    calls = [json.loads(line) for line in gh_fixture.log.read_text(encoding="utf-8").splitlines()]
    assert [call[0] for call in calls] == ["api", "api", "api"]


def test_valid_previous_ancestor_writes_scoped_incremental_diff(
    history: RepositoryHistory, gh_fixture: GhFixture, tmp_path: Path
) -> None:
    """Breaking ancestor delta selection would make a valid rereview use the full diff."""
    configure_gh(gh_fixture, base=history.base, head=history.head)
    full, delta, manifest = outputs(tmp_path)

    result = run_prepare(
        history.repo,
        gh_fixture,
        *prepare_args(full, delta, manifest, "--previous-sha", history.previous),
    )

    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout)
    assert state["diff_mode"] == "delta"
    assert "after" in delta.read_text(encoding="utf-8")
    assert "OUT_OF_PR" not in delta.read_text(encoding="utf-8")
    assert full.exists()


def test_non_ancestor_previous_falls_back_to_pr_scoped_full_diff(
    history: RepositoryHistory, gh_fixture: GhFixture, tmp_path: Path
) -> None:
    """Treating a divergent commit as a delta base would broaden review scope."""
    configure_gh(gh_fixture, base=history.base, head=history.head)
    full, delta, manifest = outputs(tmp_path)

    result = run_prepare(
        history.repo,
        gh_fixture,
        *prepare_args(full, delta, manifest, "--previous-sha", history.non_ancestor),
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["diff_mode"] == "full"
    assert not delta.exists()
    assert "OUT_OF_PR" not in full.read_text(encoding="utf-8")


def test_unavailable_previous_commit_falls_back_to_pr_scoped_full_diff(
    history: RepositoryHistory, gh_fixture: GhFixture, tmp_path: Path
) -> None:
    """A missing old checkpoint must not make the authoritative full input unavailable."""
    configure_gh(gh_fixture, base=history.base, head=history.head)
    full, delta, manifest = outputs(tmp_path)

    result = run_prepare(
        history.repo,
        gh_fixture,
        *prepare_args(full, delta, manifest, "--previous-sha", "0" * 40),
    )

    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout)
    assert state["diff_ready"] is True
    assert state["diff_mode"] == "full"
    assert "OUT_OF_PR" not in full.read_text(encoding="utf-8")


def test_pr_files_failure_uses_numbered_full_diff_not_commit_range(
    history: RepositoryHistory, gh_fixture: GhFixture, tmp_path: Path
) -> None:
    """An API file-list outage must use only GitHub's numbered authoritative diff."""
    configure_gh(
        gh_fixture,
        base=history.base,
        head=history.head,
        files_error=True,
        pr_diff="diff --git a/inside.txt b/inside.txt\n+server-only\n",
    )
    full, delta, manifest = outputs(tmp_path)

    result = run_prepare(history.repo, gh_fixture, *prepare_args(full, delta, manifest))

    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout)
    assert state["diff_mode"] == "full"
    assert "OUT_OF_PR" not in full.read_text(encoding="utf-8")
    assert "server-only" in full.read_text(encoding="utf-8")
    calls = [json.loads(line) for line in gh_fixture.log.read_text(encoding="utf-8").splitlines()]
    assert ["pr", "diff", "7"] in calls
    scope = json.loads(manifest.read_text(encoding="utf-8"))
    assert scope["files"] == []
    assert scope["merge_base_sha"] == history.base


def test_renamed_file_without_previous_name_uses_numbered_full_diff(
    history: RepositoryHistory, gh_fixture: GhFixture, tmp_path: Path
) -> None:
    """Accepting a partial rename record can silently omit the old path from review."""
    configure_gh(
        gh_fixture,
        base=history.base,
        head=history.head,
        files=[{"status": "renamed", "filename": "moved.txt"}],
        pr_diff="diff --git a/inside.txt b/moved.txt\n+server rename\n",
    )
    full, delta, manifest = outputs(tmp_path)

    result = run_prepare(history.repo, gh_fixture, *prepare_args(full, delta, manifest))

    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout)
    assert state["diff_ready"] is True
    assert state["diff_mode"] == "full"
    assert full.read_text(encoding="utf-8").endswith("+server rename\n")
    scope = json.loads(manifest.read_text(encoding="utf-8"))
    assert scope["merge_base_sha"] == history.base
    assert scope["files"] == []
    calls = [json.loads(line) for line in gh_fixture.log.read_text(encoding="utf-8").splitlines()]
    assert ["pr", "diff", "7"] in calls


def test_total_preparation_failure_removes_stale_outputs(
    history: RepositoryHistory, gh_fixture: GhFixture, tmp_path: Path
) -> None:
    """Publishing an old artifact after both full-diff sources fail is unsafe."""
    configure_gh(gh_fixture, base=history.base, head=history.head, files_error=True, pr_diff_error=True)
    full, delta, manifest = outputs(tmp_path)
    for output in (full, delta, manifest):
        output.write_text("stale", encoding="utf-8")

    result = run_prepare(history.repo, gh_fixture, *prepare_args(full, delta, manifest))

    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout)
    assert state["diff_ready"] is False
    assert state["diff_mode"] == "unavailable"
    assert all(not output.exists() for output in (full, delta, manifest))


@pytest.mark.parametrize("changed", ["head", "base"])
def test_changed_metadata_snapshot_fails_closed(
    history: RepositoryHistory, gh_fixture: GhFixture, tmp_path: Path, changed: str
) -> None:
    """Accepting mutable PR bytes across a base/head change can stamp the wrong head."""
    changed_sha = history.previous if changed == "head" else history.previous
    first = {"base": {"sha": history.base}, "head": {"sha": history.head}}
    second = {"base": {"sha": changed_sha if changed == "base" else history.base}, "head": {"sha": changed_sha if changed == "head" else history.head}}
    configure_gh(gh_fixture, base=history.base, head=history.head, metadata=[first, second])
    full, delta, manifest = outputs(tmp_path)
    full.write_text("stale", encoding="utf-8")

    result = run_prepare(history.repo, gh_fixture, *prepare_args(full, delta, manifest))

    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout)
    assert state["diff_ready"] is False
    assert state["diff_mode"] == "unavailable"
    assert not full.exists()
    assert not manifest.exists()


def test_matching_full_hash_reports_unchanged_and_removes_delta(
    history: RepositoryHistory, gh_fixture: GhFixture, tmp_path: Path
) -> None:
    """Ignoring the previous full hash would unnecessarily rerun the model."""
    configure_gh(gh_fixture, base=history.base, head=history.head)
    full, delta, manifest = outputs(tmp_path)
    baseline = run_prepare(history.repo, gh_fixture, *prepare_args(full, delta, manifest))
    baseline_hash = json.loads(baseline.stdout)["full_diff_sha256"]
    delta.write_text("stale", encoding="utf-8")
    configure_gh(gh_fixture, base=history.base, head=history.head)

    result = run_prepare(
        history.repo,
        gh_fixture,
        *prepare_args(full, delta, manifest, "--previous-full-hash", baseline_hash),
    )

    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout)
    assert state["diff_mode"] == "unchanged"
    assert state["unchanged_since_previous"] is True
    assert not delta.exists()


def test_invalid_invocation_is_nonzero_and_does_not_call_gh(
    history: RepositoryHistory, gh_fixture: GhFixture, tmp_path: Path
) -> None:
    """Relaxing CLI validation could execute external commands for malformed input."""
    full, delta, manifest = outputs(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--repository",
            "not-a-repository",
            "--pr-number",
            "7",
            *prepare_args(full, delta, manifest),
        ],
        cwd=history.repo,
        env=gh_fixture.env,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert not gh_fixture.log.exists()


def test_unicode_and_newline_paths_stay_scoped_in_incremental_diff(
    history: RepositoryHistory, gh_fixture: GhFixture, tmp_path: Path
) -> None:
    """Path quoting or shell transport must not drop unusual PR paths."""
    special_paths = [
        "pages/[id].tsx",
        "emoji-한글-😀.py",
        "line\nbreak.py",
        "leading-dash/-n.txt",
    ]
    records = [{"status": "modified", "filename": "normal.py"}]
    (history.repo / "normal.py").write_text("NORMAL_CHANGE\n", encoding="utf-8")
    for index, path in enumerate(special_paths):
        destination = history.repo / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(f"SPECIAL_CHANGE_{index}\n", encoding="utf-8")
        records.append({"status": "added", "filename": path})
    head = commit(history.repo, "unusual paths")
    configure_gh(gh_fixture, base=history.base, head=head, files=records)
    full, delta, manifest = outputs(tmp_path)

    result = run_prepare(
        history.repo,
        gh_fixture,
        *prepare_args(full, delta, manifest, "--previous-sha", history.head),
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["diff_mode"] == "delta"
    diff = delta.read_text(encoding="utf-8")
    assert "NORMAL_CHANGE" in diff
    for index in range(len(special_paths)):
        assert f"SPECIAL_CHANGE_{index}" in diff
    assert "OUT_OF_PR" not in diff


def test_rename_binary_mode_symlink_and_submodule_changes_are_preserved(
    history: RepositoryHistory, gh_fixture: GhFixture, tmp_path: Path
) -> None:
    """Scope both rename endpoints so Git retains object and rename semantics."""
    (history.repo / "rename-from.txt").write_text("rename me\n", encoding="utf-8")
    (history.repo / "deleted.txt").write_text("delete me\n", encoding="utf-8")
    (history.repo / "binary.bin").write_bytes(b"\x00before\xff")
    executable = history.repo / "executable.sh"
    executable.write_text("#!/bin/sh\necho executable\n", encoding="utf-8")
    executable.chmod(0o644)
    link = history.repo / "target-link"
    link.symlink_to("before-target")
    source = tmp_path / "submodule-source"
    source.mkdir()
    git(source, "init", "-b", "main")
    git(source, "config", "user.name", "Test User")
    git(source, "config", "user.email", "test@example.invalid")
    (source / "module.txt").write_text("before\n", encoding="utf-8")
    previous_submodule = commit(source, "submodule before")
    git(
        history.repo,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        str(source),
        "vendor/module",
    )
    previous = commit(history.repo, "object baseline")

    git(history.repo, "mv", "rename-from.txt", "rename-to.txt")
    (history.repo / "deleted.txt").unlink()
    (history.repo / "binary.bin").write_bytes(b"\x00after\xfe")
    executable.chmod(0o755)
    link.unlink()
    link.symlink_to("after-target")
    (source / "module.txt").write_text("after\n", encoding="utf-8")
    updated_submodule = commit(source, "submodule after")
    git(
        history.repo,
        "-c",
        "protocol.file.allow=always",
        "-C",
        "vendor/module",
        "fetch",
        "origin",
        updated_submodule,
    )
    git(history.repo, "-C", "vendor/module", "checkout", updated_submodule)
    head = commit(history.repo, "object changes")
    configure_gh(
        gh_fixture,
        base=history.base,
        head=head,
        files=[
            {
                "status": "renamed",
                "filename": "rename-to.txt",
                "previous_filename": "rename-from.txt",
            },
            {"status": "removed", "filename": "deleted.txt"},
            {"status": "modified", "filename": "binary.bin"},
            {"status": "modified", "filename": "executable.sh"},
            {"status": "modified", "filename": "target-link"},
            {"status": "modified", "filename": "vendor/module"},
        ],
    )
    full, delta, manifest = outputs(tmp_path)

    result = run_prepare(
        history.repo,
        gh_fixture,
        *prepare_args(full, delta, manifest, "--previous-sha", previous),
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["diff_mode"] == "delta"
    diff = delta.read_bytes()
    assert b"rename from rename-from.txt" in diff
    assert b"rename to rename-to.txt" in diff
    assert b"new file mode" not in diff
    assert b"deleted file mode 100644" in diff
    assert b"Binary files a/binary.bin and b/binary.bin differ" in diff
    assert b"old mode 100644" in diff
    assert b"new mode 100755" in diff
    assert b"before-target" in diff
    assert b"after-target" in diff
    assert f"-Subproject commit {previous_submodule}".encode() in diff
    assert f"+Subproject commit {updated_submodule}".encode() in diff
    assert b"OUT_OF_PR" not in diff


def test_oversized_unicode_path_argv_uses_numbered_full_diff(
    history: RepositoryHistory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unsafe local argv must fall back without a broad commit-range diff."""
    spec = importlib.util.spec_from_file_location("prepare_review_diff_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    limited_os = types.SimpleNamespace(
        fsencode=os.fsencode,
        fsync=os.fsync,
        replace=os.replace,
        sysconf=lambda name: 131072,
    )
    with monkeypatch.context() as size_limit:
        size_limit.setattr(module, "os", limited_os)
        with pytest.raises(module.PreparationUnavailable, match="argument limit"):
            module.git_diff(
                history.base,
                history.head,
                3,
                ["emoji-한글-😀.py"],
                history.repo,
            )

    records = [{"status": "added", "filename": "emoji-한글-😀.py"}]
    monkeypatch.setattr(module, "metadata", lambda *arguments: (history.base, history.head))
    monkeypatch.setattr(module, "pr_files", lambda *arguments: records)
    numbered_calls: list[int] = []

    def numbered(pr_number: int, cwd: Path) -> bytes:
        numbered_calls.append(pr_number)
        return b"diff --git a/inside.txt b/inside.txt\n+numbered-only\n"

    monkeypatch.setattr(module, "numbered_pr_diff", numbered)
    original_git_diff = module.git_diff

    def limited_git_diff(*arguments: object) -> bytes:
        with monkeypatch.context() as size_limit:
            size_limit.setattr(module, "os", limited_os)
            return original_git_diff(*arguments)

    monkeypatch.setattr(module, "git_diff", limited_git_diff)
    full, delta, manifest = outputs(tmp_path)
    args = module.parse_args(
        [
            "--repository",
            "o/r",
            "--pr-number",
            "7",
            *prepare_args(full, delta, manifest),
        ]
    )

    state = module.prepare(args, history.repo)

    assert state.diff_ready is True
    assert state.diff_mode == "full"
    assert "numbered PR diff" in state.warning
    assert full.read_text(encoding="utf-8").endswith("+numbered-only\n")
    assert numbered_calls == [7]
