"""Executable contract tests for deterministic PR-scoped review diffs."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from dataclasses import dataclass
from pathlib import Path
import shutil
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
                "files": [
                    files
                    if files is not None
                    else [{"status": "modified", "filename": "inside.txt"}]
                ],
                "files_error": files_error,
                "pr_diff_error": pr_diff_error,
                "pr_diff": pr_diff,
            }
        ),
        encoding="utf-8",
    )


def install_git_log_shim(gh_fixture: GhFixture, tmp_path: Path) -> Path:
    """Log exact Git argv while continuing to use the local Git executable."""
    real_git = shutil.which("git")
    assert real_git is not None
    log = tmp_path / "git.log"
    shim = Path(gh_fixture.env["PATH"].split(os.pathsep)[0]) / "git"
    shim.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys

with open(os.environ["GIT_FIXTURE_LOG"], "a", encoding="utf-8") as output:
    output.write(json.dumps(sys.argv[1:]) + "\\n")
os.execv(os.environ["REAL_GIT"], [os.environ["REAL_GIT"], *sys.argv[1:]])
""",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    gh_fixture.env.update({"GIT_FIXTURE_LOG": str(log), "REAL_GIT": real_git})
    return log


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
    """The full immutable merge-base range is the authoritative review input."""
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
    assert "OUT_OF_PR" in full.read_text(encoding="utf-8")
    assert not delta.exists()
    assert json.loads(manifest.read_text(encoding="utf-8")) == {
        "schema": 1,
        "repository": "o/r",
        "pr_number": 7,
        "merge_base_sha": history.base,
        "head_sha": history.head,
        "files": [
            {"status": "added", "filename": "decoy.txt"},
            {"status": "added", "filename": "inside.txt"},
        ],
    }
    calls = [json.loads(line) for line in gh_fixture.log.read_text(encoding="utf-8").splitlines()]
    assert [call[0] for call in calls] == ["api", "api"]


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
    assert "OUT_OF_PR" in delta.read_text(encoding="utf-8")
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
    assert "OUT_OF_PR" in full.read_text(encoding="utf-8")


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
    assert "OUT_OF_PR" in full.read_text(encoding="utf-8")


def test_remote_file_and_numbered_diff_failures_do_not_affect_local_full_diff(
    history: RepositoryHistory, gh_fixture: GhFixture, tmp_path: Path
) -> None:
    """Remote diff sources are irrelevant once exact local objects are available."""
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
    assert "OUT_OF_PR" in full.read_text(encoding="utf-8")
    assert "server-only" not in full.read_text(encoding="utf-8")
    calls = [json.loads(line) for line in gh_fixture.log.read_text(encoding="utf-8").splitlines()]
    assert not any(call[0] == "api" and call[1].endswith("/files") for call in calls)
    assert not any(call[:2] == ["pr", "diff"] for call in calls)
    scope = json.loads(manifest.read_text(encoding="utf-8"))
    assert {record["filename"] for record in scope["files"]} == {"decoy.txt", "inside.txt"}
    assert scope["merge_base_sha"] == history.base


def test_malformed_remote_rename_record_cannot_replace_local_scope(
    history: RepositoryHistory, gh_fixture: GhFixture, tmp_path: Path
) -> None:
    """A malformed mutable rename record never influences the local manifest."""
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
    assert "server rename" not in full.read_text(encoding="utf-8")
    scope = json.loads(manifest.read_text(encoding="utf-8"))
    assert scope["merge_base_sha"] == history.base
    assert {record["filename"] for record in scope["files"]} == {"decoy.txt", "inside.txt"}
    calls = [json.loads(line) for line in gh_fixture.log.read_text(encoding="utf-8").splitlines()]
    assert not any(call[0] == "api" and call[1].endswith("/files") for call in calls)
    assert not any(call[:2] == ["pr", "diff"] for call in calls)


def test_unavailable_local_head_object_removes_stale_outputs(
    history: RepositoryHistory, gh_fixture: GhFixture, tmp_path: Path
) -> None:
    """Missing captured objects fail closed without a mutable server-diff fallback."""
    configure_gh(gh_fixture, base=history.base, head="f" * 40)
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


def test_github_output_bridge_appends_action_safe_scalars_without_changing_stdout(
    history: RepositoryHistory, gh_fixture: GhFixture, tmp_path: Path
) -> None:
    """The composite bridge must not add diagnostics or unsafe values to stdout."""
    configure_gh(gh_fixture, base=history.base, head=history.head)
    full, delta, manifest = outputs(tmp_path)
    github_output = tmp_path / "github-output"
    github_output.write_text("existing=value\n", encoding="utf-8")

    result = run_prepare(
        history.repo,
        gh_fixture,
        *prepare_args(full, delta, manifest, "--github-output", str(github_output)),
    )

    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout)
    assert result.stdout == json.dumps(state, sort_keys=True) + "\n"
    assert github_output.read_text(encoding="utf-8") == (
        "existing=value\n"
        "diff_ready=true\n"
        "diff_mode=full\n"
        f"head_sha={history.head}\n"
        f"full_diff_sha256={state['full_diff_sha256']}\n"
        "unchanged_since_previous=false\n"
    )


def test_omitted_github_output_bridge_does_not_create_an_output_file(
    history: RepositoryHistory, gh_fixture: GhFixture, tmp_path: Path
) -> None:
    """Standalone use must leave an unrequested Actions output file untouched."""
    configure_gh(gh_fixture, base=history.base, head=history.head)
    full, delta, manifest = outputs(tmp_path)
    github_output = tmp_path / "github-output"

    result = run_prepare(history.repo, gh_fixture, *prepare_args(full, delta, manifest))

    assert result.returncode == 0, result.stderr
    assert not github_output.exists()


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
    git(history.repo, "config", "diff.ignoreSubmodules", "all")
    git(history.repo, "config", "submodule.vendor/module.ignore", "all")
    configure_gh(
        gh_fixture,
        base=previous,
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
    scope = json.loads(manifest.read_text(encoding="utf-8"))
    assert "vendor/module" in {record["filename"] for record in scope["files"]}
    assert b"OUT_OF_PR" not in diff


def test_oversized_delta_argv_uses_already_prepared_immutable_full_diff(
    history: RepositoryHistory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unsafe delta argv must reuse the already prepared immutable full diff."""
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
    monkeypatch.setattr(module, "ensure_commit", lambda *arguments: None)
    monkeypatch.setattr(module, "merge_base", lambda *arguments: history.base)
    monkeypatch.setattr(module, "local_scope", lambda *arguments: records)
    monkeypatch.setattr(
        module,
        "git_full_diff",
        lambda *arguments: b"diff --git a/inside.txt b/inside.txt\n+immutable-full\n",
    )
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
            *prepare_args(full, delta, manifest, "--previous-sha", history.base),
        ]
    )

    state = module.prepare(args, history.repo)

    assert state.diff_ready is True
    assert state.diff_mode == "full"
    assert state.warning == "incremental diff unavailable; used full PR diff"
    assert full.read_text(encoding="utf-8").endswith("+immutable-full\n")
    assert not delta.exists()


def test_non_utf8_local_git_path_fails_closed(
    history: RepositoryHistory, gh_fixture: GhFixture, tmp_path: Path
) -> None:
    """The UTF-8 JSON manifest must never lossy-decode a local Git pathname."""
    raw_path = os.fsencode(history.repo) + b"/invalid-\xff.txt"
    descriptor = os.open(raw_path, os.O_WRONLY | os.O_CREAT, 0o644)
    try:
        os.write(descriptor, b"NON_UTF8_PATH\n")
    finally:
        os.close(descriptor)
    head = commit(history.repo, "non-UTF-8 path")
    configure_gh(gh_fixture, base=history.base, head=head)
    full, delta, manifest = outputs(tmp_path)

    result = run_prepare(history.repo, gh_fixture, *prepare_args(full, delta, manifest))

    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout)
    assert state["diff_ready"] is False
    assert state["diff_mode"] == "unavailable"
    assert state["warning"] == "local Git scope contains a non-UTF-8 path"
    assert all(not output.exists() for output in (full, delta, manifest))


def test_cli_ignores_oversized_mutable_file_response(
    history: RepositoryHistory, gh_fixture: GhFixture, tmp_path: Path
) -> None:
    """A hostile oversized server file response cannot select local Git argv."""
    component = "x" * 240
    filename = f"overflow/00000000/{component}/{component}/{component}/{component}"
    available = max(os.sysconf("SC_ARG_MAX") - 128 * 1024, 0)
    record_count = min(3000, max(1, available // (len(os.fsencode(filename)) + 1) + 2))
    records = [
        {
            "status": "modified",
            "filename": f"overflow/{index:08d}/{component}/{component}/{component}/{component}",
        }
        for index in range(record_count)
    ]
    assert record_count <= 3000
    assert len(os.fsencode(filename)) < 4096
    assert all(
        0 < len(os.fsencode(path_component)) <= 255
        for path_component in filename.split("/")
    )
    assert sum(len(os.fsencode(record["filename"])) + 1 for record in records) > available
    configure_gh(
        gh_fixture,
        base=history.base,
        head=history.head,
        files=records,
        pr_diff="diff --git a/server-only.txt b/server-only.txt\n+server-only\n",
    )
    git_log = install_git_log_shim(gh_fixture, tmp_path)
    full, delta, manifest = outputs(tmp_path)

    result = run_prepare(history.repo, gh_fixture, *prepare_args(full, delta, manifest))

    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout)
    assert state["diff_ready"] is True
    assert state["diff_mode"] == "full"
    assert state["warning"] == ""
    assert b"server-only" not in full.read_bytes()
    assert b"OUT_OF_PR" in full.read_bytes()
    assert not delta.exists()
    assert {record["filename"] for record in json.loads(manifest.read_text(encoding="utf-8"))["files"]} == {
        "decoy.txt",
        "inside.txt",
    }
    gh_calls = [json.loads(line) for line in gh_fixture.log.read_text(encoding="utf-8").splitlines()]
    assert not any(call[0] == "api" and call[1].endswith("/files") for call in gh_calls)
    assert not any(call[:2] == ["pr", "diff"] for call in gh_calls)
    git_calls = [json.loads(line) for line in git_log.read_text(encoding="utf-8").splitlines()]
    assert any(call[:3] == ["--no-replace-objects", "cat-file", "-e"] for call in git_calls)
    assert any(call[:2] == ["--no-replace-objects", "merge-base"] for call in git_calls)
    assert any("diff" in call for call in git_calls)


def test_h1_h2_h1_file_response_skew_cannot_change_prepared_h1_bytes(
    history: RepositoryHistory, gh_fixture: GhFixture, tmp_path: Path
) -> None:
    """Mutable files from another head must not select bytes for the captured H1."""
    configure_gh(
        gh_fixture,
        base=history.base,
        head=history.head,
        files=[{"status": "added", "filename": "side.txt"}],
    )
    full, delta, manifest = outputs(tmp_path)

    result = run_prepare(history.repo, gh_fixture, *prepare_args(full, delta, manifest))

    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout)
    assert state["diff_ready"] is True
    assert "after" in full.read_text(encoding="utf-8")
    scope = json.loads(manifest.read_text(encoding="utf-8"))
    assert {record["filename"] for record in scope["files"]} == {
        "inside.txt",
        "decoy.txt",
    }
    calls = [
        json.loads(line)
        for line in gh_fixture.log.read_text(encoding="utf-8").splitlines()
    ]
    assert not any(call[0] == "api" and call[1].endswith("/files") for call in calls)
    assert not any(call[:2] == ["pr", "diff"] for call in calls)


def test_local_manifest_and_full_diff_include_file_beyond_server_3000_ceiling(
    history: RepositoryHistory, gh_fixture: GhFixture, tmp_path: Path
) -> None:
    """A 3,000-record mutable server view must not silently omit file 3,001."""
    filenames = [f"bulk/{index:04d}.txt" for index in range(3001)]
    for index, filename in enumerate(filenames):
        destination = history.repo / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(f"LOCAL_FILE_{index}\n", encoding="utf-8")
    head = commit(history.repo, "3001 changed files")
    configure_gh(
        gh_fixture,
        base=history.head,
        head=head,
        files=[{"status": "added", "filename": name} for name in filenames[:3000]],
    )
    full, delta, manifest = outputs(tmp_path)

    result = run_prepare(history.repo, gh_fixture, *prepare_args(full, delta, manifest))

    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout)
    assert state["diff_ready"] is True
    scope = json.loads(manifest.read_text(encoding="utf-8"))
    assert len(scope["files"]) == 3001
    assert scope["files"][-1]["filename"] == filenames[-1]
    assert "LOCAL_FILE_3000" in full.read_text(encoding="utf-8")
    calls = [
        json.loads(line)
        for line in gh_fixture.log.read_text(encoding="utf-8").splitlines()
    ]
    assert not any(call[0] == "api" and call[1].endswith("/files") for call in calls)
    assert not any(call[:2] == ["pr", "diff"] for call in calls)


def test_delta_excludes_path_restored_out_of_immutable_final_scope(
    history: RepositoryHistory, gh_fixture: GhFixture, tmp_path: Path
) -> None:
    """A delta-only removal restored to merge-base state is not final PR scope."""
    (history.repo / "decoy.txt").unlink()
    (history.repo / "target.txt").write_text("TARGET_CHANGE\n", encoding="utf-8")
    head = commit(history.repo, "restore decoy and change target")
    configure_gh(
        gh_fixture,
        base=history.base,
        head=head,
        files=[
            {"status": "removed", "filename": "decoy.txt"},
            {"status": "added", "filename": "target.txt"},
        ],
    )
    full, delta, manifest = outputs(tmp_path)

    result = run_prepare(
        history.repo,
        gh_fixture,
        *prepare_args(full, delta, manifest, "--previous-sha", history.head),
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["diff_mode"] == "delta"
    scope = json.loads(manifest.read_text(encoding="utf-8"))
    assert "decoy.txt" not in {record["filename"] for record in scope["files"]}
    assert "TARGET_CHANGE" in delta.read_text(encoding="utf-8")
    assert "decoy.txt" not in delta.read_text(encoding="utf-8")
