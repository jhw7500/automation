"""Tests for verifying the exact reusable-workflow release artifact."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import traceback
import zlib

import pytest
import yaml

import scripts.verify_workflow_release as release_verifier
from scripts.verify_workflow_release import ReleaseVerificationError, verify_release
from scripts.workflow_release_inventory import RELEASE_PATHS

ROOT = Path(__file__).resolve().parents[1]

CANONICAL_REMOTE = "https://github.com/jhw7500/automation.git"
HERMETIC_LOCAL_GIT_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent/automation-workflow-release/home",
    "XDG_CONFIG_HOME": "/nonexistent/automation-workflow-release/xdg",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "/bin/false",
    "SSH_ASKPASS": "/bin/false",
    "GCM_INTERACTIVE": "Never",
}
HERMETIC_REMOTE_GIT_ENV = {
    **HERMETIC_LOCAL_GIT_ENV,
    "GIT_ALLOW_PROTOCOL": "https",
    "GIT_PROTOCOL_FROM_USER": "0",
    "GIT_CEILING_DIRECTORIES": "/",
}
HARDENED_MANUAL_OUTPUT_BLOCK = """          write_output() {
            local name="$1"
            local value="$2"
            local delimiter='__AUTOMATION_OUTPUT__'
            while [[ "$value" == *"$delimiter"* ]]; do
              delimiter="${delimiter}_X"
            done
            {
              printf '%s<<%s\\n' "$name" "$delimiter"
              printf '%s\\n' "$value"
              printf '%s\\n' "$delimiter"
            } >> "$GITHUB_OUTPUT"
          }

          write_output title "$title"
          write_output body "$body"
"""
LEGACY_MANUAL_OUTPUT_BLOCK = """          echo "title<<EOF" >> "$GITHUB_OUTPUT"
          echo "$title" >> "$GITHUB_OUTPUT"
          echo "EOF" >> "$GITHUB_OUTPUT"

          echo "body<<EOF" >> "$GITHUB_OUTPUT"
          echo "$body" >> "$GITHUB_OUTPUT"
          echo "EOF" >> "$GITHUB_OUTPUT"
"""


def restore_historical_v140_manual_outputs(repo: Path) -> None:
    root = repo / "examples/baseline-workflows/.github/workflows"
    for filename in ("gemini-issue-triage.yml", "gemini-pr-review.yml"):
        path = root / filename
        text = path.read_text(encoding="utf-8")
        assert text.count(HARDENED_MANUAL_OUTPUT_BLOCK) == 1
        assert text.count("        shell: bash\n") == 2
        path.write_text(
            text.replace(
                HARDENED_MANUAL_OUTPUT_BLOCK,
                LEGACY_MANUAL_OUTPUT_BLOCK,
                1,
            ).replace("        shell: bash\n", "", 2),
            encoding="utf-8",
        )


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()


def commit(repo: Path, message: str) -> str:
    git(repo, "add", ".")
    git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def release_repo(tmp_path: Path) -> tuple[Path, Path, str]:
    repo = tmp_path / "automation"
    repo.mkdir()
    for relative in RELEASE_PATHS:
        source = ROOT / relative
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
    restore_historical_v140_manual_outputs(repo)
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    release_commit = commit(repo, "release")
    git(repo, "tag", "-a", "v1.40", "-m", "v1.40")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "origin", "v1.40")
    return repo, remote, release_commit


@pytest.fixture
def current_release_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "current-automation"
    repo.mkdir()
    for relative in RELEASE_PATHS:
        source = ROOT / relative
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    return repo, commit(repo, "current release")


def replace(path: Path, old: str, new: str, *, count: int = -1) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, count), encoding="utf-8")


def retag_bad_release(repo: Path, message: str) -> str:
    git(repo, "tag", "-d", "v1.40")
    bad_commit = commit(repo, message)
    git(repo, "tag", "-a", "v1.40", "-m", message)
    return bad_commit


def alternate_tag_object(repo: Path) -> str:
    (repo / "race-marker").write_text("alternate", encoding="utf-8")
    commit(repo, "alternate release")
    git(repo, "tag", "-a", "race-target", "-m", "race target")
    return git(repo, "rev-parse", "refs/tags/race-target")


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def common_git_dir(repo: Path) -> Path:
    value = Path(git(repo, "rev-parse", "--git-common-dir"))
    return value if value.is_absolute() else (repo / value).resolve()


def raw_git_object(repo: Path, kind: str, oid: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repo), "cat-file", kind, oid],
        check=True,
        stdout=subprocess.PIPE,
    ).stdout


def replace_loose_object_payload(
    repo: Path, oid: str, kind: str, payload: bytes
) -> None:
    object_path = common_git_dir(repo) / "objects" / oid[:2] / oid[2:]
    assert object_path.is_file(), f"expected loose test object: {oid}"
    header = f"{kind} {len(payload)}\0".encode("ascii")
    object_path.chmod(0o600)
    object_path.write_bytes(zlib.compress(header + payload))


def install_local_release_filter_attack(
    repo: Path, tmp_path: Path, *, target: str
) -> tuple[Path, bytes]:
    common = common_git_dir(repo)
    provider = tmp_path / "LOCAL-PROVIDER-SECRET"
    provider.write_text("LOCAL-PROVIDER-SECRET", encoding="utf-8")
    marker = tmp_path / "local-filter-provider-read"
    substituted = b'{"substituted": "LOCAL-PROVIDER-SECRET"}\n'
    helper = tmp_path / "local-filter-helper"
    helper.write_text(
        "#!/bin/sh\n"
        f"/bin/cat {provider} > {marker}\n"
        "/bin/cat >/dev/null\n"
        f"/usr/bin/printf '%s' '{substituted.decode().strip()}'\n",
        encoding="utf-8",
    )
    helper.chmod(0o755)
    included = tmp_path / "local-provider.gitconfig"
    included.write_text(
        '[filter "local-provider"]\n'
        f"\tsmudge = {helper}\n"
        "\trequired = true\n"
        "[core]\n"
        f"\tsshCommand = {helper}\n"
        "[credential]\n"
        f"\thelper = !{helper}\n",
        encoding="utf-8",
    )
    with (common / "config").open("a", encoding="utf-8") as config:
        config.write(f"\n[include]\n\tpath = {included}\n")
    info = common / "info"
    info.mkdir(exist_ok=True)
    with (info / "attributes").open("a", encoding="utf-8") as attributes:
        attributes.write(f"{target} filter=local-provider\n")
    return marker, substituted


def install_commit_replacement(repo: Path, commit_oid: str) -> str:
    config_path = repo / "scripts/workflow-config.json"
    replacement = load_json(config_path)
    replacement["automation_ref"] = "v9.99"
    write_json(config_path, replacement)
    alternate = commit(repo, "replacement payload")
    git(repo, "replace", commit_oid, alternate)
    return alternate


def test_release_verifier_git_uses_a_minimal_provider_free_environment(
    release_repo: tuple[Path, Path, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _, release_commit = release_repo
    expected_object_dir = common_git_dir(repo) / "objects"
    sensitive = {
        "CLAUDE_CODE_OAUTH_TOKEN",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "ZHIPU_API_KEY",
        "APP_PRIVATE_KEY",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "AWS_SECRET_ACCESS_KEY",
        "UNRELATED_OPERATOR_SECRET",
        "GIT_CONFIG_COUNT",
    }
    for key in sensitive:
        monkeypatch.setenv(key, f"sentinel-{key}")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("SSH_AUTH_SOCK", str(tmp_path / "agent.sock"))
    observed: dict[str, object] = {}

    def child(args, **kwargs):
        passed = kwargs["pass_fds"]
        assert len(passed) == 1
        descriptor = passed[0]
        observed["object_stat"] = os.fstat(descriptor)
        observed.update({"args": list(args), **kwargs})
        return subprocess.CompletedProcess(args, 0, stdout=b"ok\n", stderr=b"")

    monkeypatch.setattr(release_verifier.subprocess, "run", child)
    assert release_verifier._git_object_frame(repo, release_commit) == b"ok\n"

    assert observed["args"] == [
        "/usr/bin/git",
        "cat-file",
        "--batch",
    ]
    assert observed["input"] == f"{release_commit}\n".encode("ascii")
    assert observed["cwd"] == "/"
    env = observed["env"]
    assert isinstance(env, dict)
    for key, value in HERMETIC_LOCAL_GIT_ENV.items():
        assert env[key] == value
    assert env["GIT_NO_REPLACE_OBJECTS"] == "1"
    assert env["GIT_OPTIONAL_LOCKS"] == "0"
    assert Path(env["GIT_DIR"]).name == "git"
    assert env["GIT_OBJECT_DIRECTORY"] == f"/proc/self/fd/{observed['pass_fds'][0]}"
    expected_stat = expected_object_dir.stat()
    object_stat = observed["object_stat"]
    assert isinstance(object_stat, os.stat_result)
    assert (object_stat.st_dev, object_stat.st_ino) == (
        expected_stat.st_dev,
        expected_stat.st_ino,
    )
    assert sensitive.isdisjoint(env)
    assert not any(str(value).startswith("sentinel-") for value in env.values())
    assert "SSH_AUTH_SOCK" not in env


def test_tag_ref_resolution_does_not_invoke_git_or_read_local_includes(
    release_repo: tuple[Path, Path, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _, _ = release_repo
    expected = git(repo, "rev-parse", "refs/tags/v1.40")
    common = common_git_dir(repo)
    provider = tmp_path / "LOCAL-PROVIDER-SECRET"
    provider.write_text("LOCAL-PROVIDER-SECRET is not Git config\n", encoding="utf-8")
    with (common / "config").open("a", encoding="utf-8") as config:
        config.write(f"\n[include]\n\tpath = {provider}\n")

    def forbidden(*_args: object, **_kwargs: object) -> object:
        pytest.fail("tag ref resolution invoked Git")

    monkeypatch.setattr(release_verifier.subprocess, "run", forbidden)

    assert release_verifier.read_tag_oid(repo, "v1.40") == expected


def test_release_verification_ignores_replace_ref_payload(
    release_repo: tuple[Path, Path, str],
) -> None:
    repo, _, release_commit = release_repo
    tag_object = git(repo, "rev-parse", "refs/tags/v1.40")
    install_commit_replacement(repo, release_commit)

    tag = release_verifier.resolve_annotated_tag(repo, "v1.40")

    assert tag.tag_object == tag_object
    assert tag.commit == release_commit
    assert verify_release(repo, "v1.40", release_commit) == release_commit


def test_release_rejects_annotated_tag_whose_authenticated_name_is_not_requested_ref(
    release_repo: tuple[Path, Path, str],
) -> None:
    repo, _, release_commit = release_repo
    git(repo, "tag", "-d", "v1.40")
    payload = (
        f"object {release_commit}\n"
        "type commit\n"
        "tag v9.99\n"
        "tagger Test <test@example.invalid> 1700000000 +0000\n"
        "\nmisnamed release\n"
    )
    tag_object = subprocess.run(
        ["git", "-C", str(repo), "mktag"],
        input=payload,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    git(repo, "update-ref", "refs/tags/v1.40", tag_object)
    assert b"tag v9.99\n" in raw_git_object(repo, "tag", tag_object)

    with pytest.raises(ReleaseVerificationError, match="annotated tag"):
        release_verifier.resolve_annotated_tag(repo, "v1.40")


@pytest.mark.parametrize(
    "tag_headers",
    (
        b"",
        b"tag v1.40\ntag v1.40\n",
        b"tag v9.99\n",
        b"tag v1.40 extra\n",
        b"tag\n",
        b"tag v1.40\n tag v9.99\n",
        b"tag v1.40\nunknown value\n",
    ),
    ids=(
        "missing",
        "duplicate",
        "misnamed",
        "extra-field",
        "malformed",
        "continuation",
        "unknown",
    ),
)
def test_authenticated_tag_parser_requires_one_exact_canonical_name_header(
    tag_headers: bytes,
) -> None:
    payload = (
        b"object "
        + (b"1" * 40)
        + b"\ntype commit\n"
        + tag_headers
        + b"tagger Test <test@example.invalid> 1700000000 +0000\n\nmessage\n"
    )

    with pytest.raises(ReleaseVerificationError, match="Git object is invalid"):
        release_verifier._tag_commit_oid(payload, "v1.40")


def test_authenticated_tag_parser_rejects_non_ascii_version_as_typed_error() -> None:
    payload = (
        b"object "
        + (b"1" * 40)
        + b"\ntype commit\ntag v1.40\n"
        + b"tagger Test <test@example.invalid> 1700000000 +0000\n\nmessage\n"
    )

    with pytest.raises(ReleaseVerificationError, match="Git object is invalid"):
        release_verifier._tag_commit_oid(payload, "v\u0661.\u0664\u0660")


@pytest.mark.parametrize(
    "tagger",
    (
        b"x",
        b"T <t@x>",
        b"T <t@x> nope +0000",
        b"T <t@x> 1700000000 UTC",
        b"T <t@x> 1700000000 +2400",
        b"T <t@x> 1700000000 +0060",
        b"T <t@x> 1700000000 +0000\x01",
        b"  <t@x> 1700000000 +0000",
        b"T <t@x> 01700000000 +0000",
        b"T <t@x> 999999999999999999999999 +0000",
    ),
)
def test_authenticated_tag_parser_rejects_malformed_tagger(tagger: bytes) -> None:
    payload = (
        b"object "
        + (b"1" * 40)
        + b"\ntype commit\ntag v1.40\ntagger "
        + tagger
        + b"\n\nmessage\n"
    )

    with pytest.raises(ReleaseVerificationError, match="Git object is invalid"):
        release_verifier._tag_commit_oid(payload, "v1.40")


def test_authenticated_tag_parser_accepts_valid_non_utc_offset() -> None:
    payload = (
        b"object "
        + (b"1" * 40)
        + b"\ntype commit\ntag v1.40\n"
        + b"tagger Test <test@example.invalid> 1700000000 -0930\n\nmessage\n"
    )

    assert release_verifier._tag_commit_oid(payload, "v1.40") == "1" * 40


@pytest.mark.parametrize("kind", ("tag", "commit", "tree", "blob"))
def test_verified_object_reader_rejects_checksum_mismatch_for_each_object_type(
    release_repo: tuple[Path, Path, str], kind: str
) -> None:
    repo, _, release_commit = release_repo
    oid = {
        "tag": git(repo, "rev-parse", "refs/tags/v1.40"),
        "commit": release_commit,
        "tree": git(repo, "rev-parse", f"{release_commit}^{{tree}}"),
        "blob": git(
            repo,
            "rev-parse",
            f"{release_commit}:.github/workflows/claude.yml",
        ),
    }[kind]
    payload = raw_git_object(repo, kind, oid) + b"checksum-mismatch"
    replace_loose_object_payload(repo, oid, kind, payload)

    with pytest.raises(
        ReleaseVerificationError, match="Git object is invalid"
    ) as raised:
        release_verifier.read_git_object(repo, oid, kind)
    rendered = "".join(
        traceback.format_exception(
            type(raised.value), raised.value, raised.value.__traceback__
        )
    )
    assert "checksum-mismatch" not in rendered


def test_verified_object_reader_rejects_an_authentic_object_of_the_wrong_type(
    release_repo: tuple[Path, Path, str],
) -> None:
    repo, _, release_commit = release_repo
    blob = git(
        repo,
        "rev-parse",
        f"{release_commit}:.github/workflows/claude.yml",
    )

    with pytest.raises(ReleaseVerificationError, match="Git object is invalid"):
        release_verifier.read_git_object(repo, blob, "tree")


def test_binary_tree_parser_rejects_noncanonical_git_entry_order() -> None:
    later = b"40000 foo\0" + bytes.fromhex("11" * 20)
    earlier = b"100644 foo.bar\0" + bytes.fromhex("22" * 20)

    with pytest.raises(ReleaseVerificationError, match="Git tree is invalid"):
        release_verifier._parse_tree(later + earlier)


def test_release_verifier_rejects_semantically_valid_blob_at_wrong_object_name(
    release_repo: tuple[Path, Path, str],
) -> None:
    repo, _, release_commit = release_repo
    path = ".github/workflows/claude.yml"
    oid = git(repo, "rev-parse", f"{release_commit}:{path}")
    payload = raw_git_object(repo, "blob", oid) + b"\n# checksum mismatch\n"
    replace_loose_object_payload(repo, oid, "blob", payload)

    with pytest.raises(ReleaseVerificationError):
        verify_release(repo, "v1.40", release_commit)


def test_release_inventory_authenticates_even_nonsemantic_owned_blob_payloads(
    release_repo: tuple[Path, Path, str],
) -> None:
    repo, _, _ = release_repo
    unparsed = repo / ".github/workflows/release-note.txt"
    unparsed.write_text("release note\n", encoding="utf-8")
    release_commit = commit(repo, "release with nonsemantic owned blob")
    git(repo, "tag", "-a", "v1.41", "-m", "v1.41")
    oid = git(repo, "rev-parse", f"{release_commit}:.github/workflows/release-note.txt")
    replace_loose_object_payload(repo, oid, "blob", b"forged release note\n")

    with pytest.raises(ReleaseVerificationError, match="release inventory"):
        verify_release(repo, "v1.41", release_commit)


def test_release_verifier_never_uses_unverified_show_or_ls_tree_content(
    release_repo: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _, release_commit = release_repo
    original = release_verifier.subprocess.run

    def authenticated_only(args, **kwargs):
        if args[0] == "/usr/bin/git":
            assert args[1:] == ["cat-file", "--batch"]
        return original(args, **kwargs)

    monkeypatch.setattr(release_verifier.subprocess, "run", authenticated_only)

    assert verify_release(repo, "v1.40", release_commit) == release_commit


@pytest.mark.parametrize("layout", ("loose", "packed", "linked"))
def test_authenticated_release_objects_support_normal_storage_layouts(
    release_repo: tuple[Path, Path, str], tmp_path: Path, layout: str
) -> None:
    repo, _, release_commit = release_repo
    checkout = repo
    if layout == "packed":
        git(repo, "repack", "-ad")
        git(repo, "prune-packed")
        assert not (
            common_git_dir(repo)
            / "objects"
            / release_commit[:2]
            / release_commit[2:]
        ).exists()
    elif layout == "linked":
        checkout = tmp_path / "linked-authenticated-release"
        git(repo, "worktree", "add", "--detach", str(checkout), release_commit)

    assert verify_release(checkout, "v1.40", release_commit) == release_commit


def test_current_release_commit_only_uses_authenticated_objects(
    current_release_repo: tuple[Path, str],
) -> None:
    repo, current = current_release_repo

    assert (
        release_verifier.verify_commit_content(repo, "v1.40.2", current)
        == current
    )


@pytest.mark.parametrize(
    ("old", "new"),
    (
        (
            '            while [[ "$value" == *"$delimiter"* ]]; do\n'
            '              delimiter="${delimiter}_X"\n'
            "            done\n",
            "",
        ),
        (
            '          write_output title "$title"\n',
            "          printf 'title<<EOF\\n%s\\nEOF\\n' \"$title\" "
            '>> "$GITHUB_OUTPUT"\n',
        ),
    ),
    ids=("missing-collision-loop", "fixed-eof-restored"),
)
@pytest.mark.parametrize(
    "filename",
    ("gemini-issue-triage.yml", "gemini-pr-review.yml"),
)
def test_commit_gate_rejects_unsafe_manual_gemini_output_writer(
    current_release_repo: tuple[Path, str], filename: str, old: str, new: str
) -> None:
    repo, _ = current_release_repo
    replace(
        repo / "examples/baseline-workflows/.github/workflows" / filename,
        old,
        new,
        count=1,
    )
    bad_commit = commit(repo, "weaken manual Gemini output writer")

    with pytest.raises(ReleaseVerificationError, match="manual Gemini output"):
        release_verifier.verify_commit_content(repo, "v1.40.2", bad_commit)


@pytest.mark.parametrize(
    ("filename", "step_id", "output_prefix"),
    (
        ("gemini-issue-triage.yml", "issue", "issue"),
        ("gemini-pr-review.yml", "pr", "pr"),
    ),
)
def test_commit_gate_rejects_manual_gemini_outputs_rewired_to_unsafe_step(
    current_release_repo: tuple[Path, str],
    filename: str,
    step_id: str,
    output_prefix: str,
) -> None:
    repo, _ = current_release_repo
    path = repo / "examples/baseline-workflows/.github/workflows" / filename

    def rewire(document: dict) -> None:
        prepare = document["jobs"]["prepare"]
        prepare["steps"].append(
            {
                "name": "Unsafe fixed-delimiter writer",
                "id": "unsafe",
                "run": (
                    "printf 'title<<EOF\\nunsafe\\nEOF\\n' >> \"$GITHUB_OUTPUT\"\n"
                    "printf 'body<<EOF\\nunsafe\\nEOF\\n' >> \"$GITHUB_OUTPUT\"\n"
                ),
            }
        )
        prepare["outputs"] = {
            f"{output_prefix}_title": "${{ steps.unsafe.outputs.title }}",
            f"{output_prefix}_body": "${{ steps.unsafe.outputs.body }}",
        }
        assert any(step.get("id") == step_id for step in prepare["steps"])

    mutate_yaml(path, rewire)
    bad_commit = commit(repo, "rewire manual Gemini outputs")

    with pytest.raises(ReleaseVerificationError, match="manual Gemini output"):
        release_verifier.verify_commit_content(repo, "v1.40.2", bad_commit)


@pytest.mark.parametrize(
    ("filename", "step_id"),
    (
        ("gemini-issue-triage.yml", "issue"),
        ("gemini-pr-review.yml", "pr"),
    ),
)
def test_commit_gate_rejects_manual_gemini_fetch_without_explicit_bash(
    current_release_repo: tuple[Path, str], filename: str, step_id: str
) -> None:
    repo, _ = current_release_repo
    path = repo / "examples/baseline-workflows/.github/workflows" / filename

    def use_sh_default(document: dict) -> None:
        document["defaults"] = {"run": {"shell": "sh"}}
        fetch = next(
            step
            for step in document["jobs"]["prepare"]["steps"]
            if step.get("id") == step_id
        )
        fetch.pop("shell", None)

    mutate_yaml(path, use_sh_default)
    bad_commit = commit(repo, "remove explicit Bash execution context")

    with pytest.raises(ReleaseVerificationError, match="manual Gemini output"):
        release_verifier.verify_commit_content(repo, "v1.40.2", bad_commit)


@pytest.mark.parametrize(
    ("filename", "job_name", "title_key"),
    (
        ("gemini-issue-triage.yml", "triage", "issue_title"),
        ("gemini-pr-review.yml", "review", "issue_title"),
    ),
)
def test_commit_gate_rejects_manual_gemini_downstream_output_rewiring(
    current_release_repo: tuple[Path, str],
    filename: str,
    job_name: str,
    title_key: str,
) -> None:
    repo, _ = current_release_repo
    path = repo / "examples/baseline-workflows/.github/workflows" / filename

    def rewire(document: dict) -> None:
        document["jobs"][job_name]["with"][title_key] = "unsafe literal"

    mutate_yaml(path, rewire)
    bad_commit = commit(repo, "rewire downstream manual Gemini title")

    with pytest.raises(ReleaseVerificationError, match="manual Gemini output"):
        release_verifier.verify_commit_content(repo, "v1.40.2", bad_commit)


def test_release_verifier_preserves_pre_inventory_v139_contract(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "historical-automation"
    shutil.copytree(ROOT / ".github/workflows", repo / ".github/workflows")
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    commit_oid = commit(repo, "historical release")
    git(repo, "tag", "-a", "v1.39", "-m", "v1.39")

    assert verify_release(repo, "v1.39", commit_oid) == commit_oid


@pytest.mark.parametrize("unsupported", ("alternates", "promisor"))
def test_release_verification_fails_closed_on_unsupported_object_storage(
    release_repo: tuple[Path, Path, str],
    tmp_path: Path,
    unsupported: str,
) -> None:
    repo, _, release_commit = release_repo
    objects = common_git_dir(repo) / "objects"
    if unsupported == "alternates":
        alternate = tmp_path / "alternate-objects"
        alternate.mkdir()
        (objects / "info/alternates").write_text(
            f"{alternate}\n", encoding="utf-8"
        )
    else:
        pack = objects / "pack"
        pack.mkdir(exist_ok=True)
        (pack / "pack-provider.promisor").write_text("", encoding="utf-8")

    with pytest.raises(ReleaseVerificationError, match="object storage"):
        verify_release(repo, "v1.40", release_commit)


def test_release_raw_object_boundary_supports_linked_worktree(
    release_repo: tuple[Path, Path, str],
    tmp_path: Path,
) -> None:
    repo, _, release_commit = release_repo
    linked = tmp_path / "linked-worktree"
    git(repo, "worktree", "add", "--detach", str(linked), release_commit)
    install_commit_replacement(repo, release_commit)

    assert verify_release(linked, "v1.40", release_commit) == release_commit


@pytest.mark.parametrize("linked_worktree", (False, True), ids=("normal", "linked"))
def test_release_rejects_external_object_directory_symlink(
    release_repo: tuple[Path, Path, str],
    tmp_path: Path,
    linked_worktree: bool,
) -> None:
    repo, _, release_commit = release_repo
    checkout = repo
    if linked_worktree:
        checkout = tmp_path / "linked-worktree"
        git(repo, "worktree", "add", "--detach", str(checkout), release_commit)
    objects = common_git_dir(repo) / "objects"
    external = tmp_path / "external-object-store"
    objects.rename(external)
    objects.symlink_to(external, target_is_directory=True)

    with pytest.raises(ReleaseVerificationError, match="repository layout"):
        verify_release(checkout, "v1.40", release_commit)


def test_release_rejects_symlink_in_linked_gitdir_chain(
    release_repo: tuple[Path, Path, str], tmp_path: Path
) -> None:
    repo, _, release_commit = release_repo
    linked = tmp_path / "linked-worktree"
    git(repo, "worktree", "add", "--detach", str(linked), release_commit)
    pointer = Path(
        (linked / ".git")
        .read_text(encoding="utf-8")
        .removeprefix("gitdir: ")
        .strip()
    )
    moved = pointer.with_name(f"{pointer.name}-real")
    pointer.rename(moved)
    pointer.symlink_to(moved, target_is_directory=True)

    with pytest.raises(ReleaseVerificationError, match="repository layout"):
        verify_release(linked, "v1.40", release_commit)


def test_release_rejects_gitdir_symlink_hidden_before_parent_traversal(
    release_repo: tuple[Path, Path, str], tmp_path: Path
) -> None:
    repo, _, release_commit = release_repo
    linked = tmp_path / "linked-worktree"
    git(repo, "worktree", "add", "--detach", str(linked), release_commit)
    pointer_file = linked / ".git"
    git_dir = Path(
        pointer_file.read_text(encoding="utf-8")
        .removeprefix("gitdir: ")
        .strip()
    )
    alias = git_dir.parent / "gitdir-link"
    alias.symlink_to(git_dir, target_is_directory=True)
    pointer_file.write_text(
        f"gitdir: {alias}/../{git_dir.name}\n", encoding="utf-8"
    )

    with pytest.raises(ReleaseVerificationError, match="repository layout"):
        verify_release(linked, "v1.40", release_commit)


def test_release_rejects_symlinked_commondir_target(
    release_repo: tuple[Path, Path, str], tmp_path: Path
) -> None:
    repo, _, release_commit = release_repo
    linked = tmp_path / "linked-worktree"
    git(repo, "worktree", "add", "--detach", str(linked), release_commit)
    git_dir = Path(
        (linked / ".git")
        .read_text(encoding="utf-8")
        .removeprefix("gitdir: ")
        .strip()
    )
    (git_dir / "common-link").symlink_to(
        common_git_dir(repo), target_is_directory=True
    )
    (git_dir / "commondir").write_text("common-link\n", encoding="utf-8")

    with pytest.raises(ReleaseVerificationError, match="repository layout"):
        verify_release(linked, "v1.40", release_commit)


def test_release_rejects_repository_path_symlink(
    release_repo: tuple[Path, Path, str], tmp_path: Path
) -> None:
    repo, _, release_commit = release_repo
    linked_path = tmp_path / "repository-link"
    linked_path.symlink_to(repo, target_is_directory=True)

    with pytest.raises(ReleaseVerificationError, match="repository layout"):
        verify_release(linked_path, "v1.40", release_commit)


def test_release_rejects_repository_path_symlink_before_parent_traversal(
    release_repo: tuple[Path, Path, str], tmp_path: Path
) -> None:
    repo, _, release_commit = release_repo
    linked_path = tmp_path / "repository-link"
    linked_path.symlink_to(repo, target_is_directory=True)
    traversal = linked_path / ".." / repo.name

    with pytest.raises(ReleaseVerificationError, match="repository layout"):
        verify_release(traversal, "v1.40", release_commit)


def test_packed_refs_fifo_fails_without_blocking(
    release_repo: tuple[Path, Path, str],
) -> None:
    repo, _, _ = release_repo
    expected = git(repo, "rev-parse", "refs/tags/v1.40")
    git(repo, "pack-refs", "--all")
    packed = common_git_dir(repo) / "packed-refs"
    packed.unlink()
    os.mkfifo(packed)
    program = """
from pathlib import Path
import sys
from scripts.verify_workflow_release import ReleaseVerificationError, read_tag_oid
try:
    read_tag_oid(Path(sys.argv[1]), "v1.40")
except ReleaseVerificationError:
    raise SystemExit(0)
raise SystemExit(3)
"""

    result = subprocess.run(
        [sys.executable, "-c", program, str(repo)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=2,
    )

    assert result.returncode == 0, (expected, result.stdout, result.stderr)


def test_read_tag_oid_accepts_normal_packed_refs_and_rejects_duplicate(
    release_repo: tuple[Path, Path, str],
) -> None:
    repo, _, _ = release_repo
    expected = git(repo, "rev-parse", "refs/tags/v1.40")
    git(repo, "pack-refs", "--all")
    packed = common_git_dir(repo) / "packed-refs"
    assert not (common_git_dir(repo) / "refs/tags/v1.40").exists()
    assert release_verifier.read_tag_oid(repo, "v1.40") == expected

    with packed.open("a", encoding="ascii") as handle:
        handle.write(f"{'0' * 40} refs/tags/v1.40\n")
    with pytest.raises(ReleaseVerificationError, match="identity is unavailable"):
        release_verifier.read_tag_oid(repo, "v1.40")


@pytest.mark.parametrize("kind", ("symlink", "oversize", "hardlink"))
def test_read_tag_oid_rejects_ambiguous_packed_refs_storage(
    release_repo: tuple[Path, Path, str], tmp_path: Path, kind: str
) -> None:
    repo, _, _ = release_repo
    git(repo, "pack-refs", "--all")
    packed = common_git_dir(repo) / "packed-refs"
    if kind == "symlink":
        external = tmp_path / "external-packed-refs"
        packed.rename(external)
        packed.symlink_to(external)
    elif kind == "oversize":
        with packed.open("wb") as handle:
            handle.truncate(16 * 1024 * 1024 + 1)
    else:
        os.link(packed, tmp_path / "packed-refs-hardlink")

    with pytest.raises(ReleaseVerificationError, match="identity is unavailable"):
        release_verifier.read_tag_oid(repo, "v1.40")


def test_direct_git_config_reader_rejects_hardlink_ambiguity(
    release_repo: tuple[Path, Path, str], tmp_path: Path
) -> None:
    repo, _, _ = release_repo
    git(repo, "remote", "set-url", "origin", CANONICAL_REMOTE)
    config = common_git_dir(repo) / "config"
    os.link(config, tmp_path / "config-hardlink")

    with pytest.raises(ReleaseVerificationError, match="canonical public HTTPS"):
        release_verifier._canonical_remote_url(repo, "origin")


def test_safe_metadata_reader_reconstructs_short_reads_and_checks_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "metadata"
    payload = b"0123456789abcdef\n"
    path.write_bytes(payload)
    directory_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    original_read = os.read

    def short_read(descriptor: int, maximum: int) -> bytes:
        return original_read(descriptor, min(maximum, 3))

    monkeypatch.setattr(release_verifier.os, "read", short_read)
    try:
        assert release_verifier._read_metadata_at(
            directory_fd,
            path.name,
            maximum=4096,
            expected_uid=os.geteuid(),
        ) == payload
        with pytest.raises(ReleaseVerificationError, match="repository metadata"):
            release_verifier._read_metadata_at(
                directory_fd,
                path.name,
                maximum=4096,
                expected_uid=os.geteuid() + 1,
            )
    finally:
        os.close(directory_fd)


def test_safe_metadata_reader_rejects_same_size_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "metadata"
    payload = b"a" * (128 * 1024)
    path.write_bytes(payload)
    old = 1_600_000_000_000_000_000
    os.utime(path, ns=(old, old))
    directory_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    original_read = os.read
    reads = 0

    def racing_read(descriptor: int, maximum: int) -> bytes:
        nonlocal reads
        value = original_read(descriptor, min(maximum, 64 * 1024))
        reads += 1
        if reads == 1:
            with path.open("r+b") as handle:
                handle.seek(len(payload) - 1)
                handle.write(b"b")
        return value

    monkeypatch.setattr(release_verifier.os, "read", racing_read)
    try:
        with pytest.raises(ReleaseVerificationError, match="repository metadata"):
            release_verifier._read_metadata_at(
                directory_fd,
                path.name,
                maximum=len(payload),
                expected_uid=os.geteuid(),
            )
    finally:
        os.close(directory_fd)


def test_commit_gate_rejects_action_file_replaced_by_directory_and_dummy_blob(
    release_repo: tuple[Path, Path, str],
) -> None:
    repo, _, _ = release_repo
    action = repo / ".github/actions/setup-gemini-auth/action.yml"
    action.unlink()
    action.mkdir()
    (action / "dummy").write_text("not a composite action\n", encoding="utf-8")
    bad_commit = commit(repo, "replace setup action with directory")

    with pytest.raises(ReleaseVerificationError, match="release inventory"):
        release_verifier.verify_commit_content(repo, "v1.41", bad_commit)


def test_commit_gate_verifies_setup_gemini_auth_action_contract(
    release_repo: tuple[Path, Path, str],
) -> None:
    repo, _, _ = release_repo
    action = repo / ".github/actions/setup-gemini-auth/action.yml"
    replace(
        action,
        "actions/create-github-app-token@a8d616148505b5069dccd32f177bb87d7f39123b",
        "actions/create-github-app-token@main",
    )
    bad_commit = commit(repo, "weaken setup action pin")

    with pytest.raises(ReleaseVerificationError, match="setup-gemini-auth"):
        release_verifier.verify_commit_content(repo, "v1.41", bad_commit)


def test_commit_only_cli_verifies_content_before_a_release_tag_exists(
    release_repo: tuple[Path, Path, str], capsys: pytest.CaptureFixture[str]
) -> None:
    repo, _, release_commit = release_repo
    git(repo, "tag", "-d", "v1.40")

    rc = release_verifier.main(
        [
            "--automation",
            str(repo),
            "--ref",
            "v1.40",
            "--expected-commit",
            release_commit,
            "--commit-only",
        ]
    )

    captured = capsys.readouterr()
    assert rc == 0
    assert release_commit in captured.out
    assert "commit content" in captured.out


def test_remote_git_uses_public_https_outside_the_repository_with_no_host_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def child(args, **kwargs):
        observed.update({"args": list(args), **kwargs})
        return subprocess.CompletedProcess(args, 0, stdout="remote\n", stderr="")

    monkeypatch.setattr(release_verifier.subprocess, "run", child)

    assert release_verifier.remote_git(CANONICAL_REMOTE, "refs/tags/v1.40") == (
        "remote\n"
    )
    assert observed["args"] == [
        "/usr/bin/git",
        "ls-remote",
        "--tags",
        CANONICAL_REMOTE,
        "refs/tags/v1.40",
    ]
    assert observed["cwd"] == "/"
    assert observed["env"] == HERMETIC_REMOTE_GIT_ENV
    assert "-C" not in observed["args"]


def test_remote_git_failure_does_not_expose_child_or_provider_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = "remote-provider-sentinel"
    raw = "remote-raw-child-sentinel"
    monkeypatch.setenv("ZHIPU_API_KEY", provider)

    def child(args, **kwargs):
        assert provider not in kwargs["env"].values()
        return subprocess.CompletedProcess(
            args,
            37,
            stdout=f"stdout {provider} {raw}",
            stderr=f"stderr {provider} {raw}",
        )

    monkeypatch.setattr(release_verifier.subprocess, "run", child)

    with pytest.raises(ReleaseVerificationError) as raised:
        release_verifier.remote_git(CANONICAL_REMOTE, raw)

    rendered = "".join(
        traceback.format_exception(
            type(raised.value), raised.value, raised.value.__traceback__
        )
    )
    assert provider not in rendered
    assert raw not in rendered
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_local_release_reads_ignore_host_user_and_xdg_git_includes(
    release_repo: tuple[Path, Path, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _, release_commit = release_repo
    marker = tmp_path / "host-config-command-ran"
    provider_token = tmp_path / "provider-token"
    provider_token.write_text("provider-secret", encoding="utf-8")
    helper = tmp_path / "host-ssh-command"
    helper.write_text(
        "#!/bin/sh\n"
        f"/bin/cat {provider_token} > {marker}\n"
        "exit 91\n",
        encoding="utf-8",
    )
    helper.chmod(0o755)
    included = tmp_path / "provider.gitconfig"
    included.write_text(
        f"[core]\n\tsshCommand = {helper}\n"
        f"[credential]\n\thelper = !{helper}\n",
        encoding="utf-8",
    )
    home = tmp_path / "host-home"
    xdg = tmp_path / "host-xdg"
    home.mkdir()
    (xdg / "git").mkdir(parents=True)
    (home / ".gitconfig").write_text(
        f"[include]\n\tpath = {included}\n", encoding="utf-8"
    )
    (xdg / "git/config").write_text(
        f"[include]\n\tpath = {included}\n", encoding="utf-8"
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(included))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(included))

    assert verify_release(repo, "v1.40", release_commit) == release_commit
    assert not marker.exists()


def test_remote_verification_does_not_execute_included_host_ssh_command(
    release_repo: tuple[Path, Path, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _, _ = release_repo
    marker = tmp_path / "host-ssh-command-ran"
    provider_token = tmp_path / "provider-token"
    provider_token.write_text("provider-secret", encoding="utf-8")
    helper = tmp_path / "host-ssh-command"
    helper.write_text(
        "#!/bin/sh\n"
        f"/bin/cat {provider_token} > {marker}\n"
        "exit 92\n",
        encoding="utf-8",
    )
    helper.chmod(0o755)
    included = tmp_path / "provider.gitconfig"
    included.write_text(
        f"[core]\n\tsshCommand = {helper}\n", encoding="utf-8"
    )
    home = tmp_path / "host-home"
    home.mkdir()
    (home / ".gitconfig").write_text(
        f"[include]\n\tpath = {included}\n", encoding="utf-8"
    )
    monkeypatch.setenv("HOME", str(home))
    git(repo, "remote", "set-url", "origin", "ssh://git@127.0.0.1/provider")
    tag = release_verifier.resolve_annotated_tag(repo, "v1.40")

    with pytest.raises(ReleaseVerificationError) as raised:
        release_verifier.verify_remote_tag(repo, "origin", tag)

    assert not marker.exists()
    assert "canonical public HTTPS" in str(raised.value)


def test_remote_verification_does_not_execute_local_credential_helper(
    release_repo: tuple[Path, Path, str],
    tmp_path: Path,
) -> None:
    repo, _, _ = release_repo
    requests: list[str] = []

    class Unauthorized(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
            requests.append(self.path)
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="provider"')
            self.end_headers()

        def log_message(self, _format: str, *args: object) -> None:
            del args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Unauthorized)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    marker = tmp_path / "credential-helper-ran"
    provider_token = tmp_path / "provider-token"
    provider_token.write_text("provider-secret", encoding="utf-8")
    helper = tmp_path / "credential-helper"
    helper.write_text(
        "#!/bin/sh\n"
        f"/bin/cat {provider_token} > {marker}\n"
        "if [ \"$1\" = get ]; then\n"
        "  printf 'username=provider\\npassword=secret\\n'\n"
        "fi\n",
        encoding="utf-8",
    )
    helper.chmod(0o755)
    git(
        repo,
        "remote",
        "set-url",
        "origin",
        f"http://127.0.0.1:{server.server_port}/provider",
    )
    git(repo, "config", "credential.helper", f"!{helper}")
    tag = release_verifier.resolve_annotated_tag(repo, "v1.40")

    try:
        with pytest.raises(ReleaseVerificationError) as raised:
            release_verifier.verify_remote_tag(repo, "origin", tag)
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert not marker.exists()
    assert requests == []
    assert "canonical public HTTPS" in str(raised.value)


def test_remote_url_inspection_does_not_follow_local_config_includes(
    release_repo: tuple[Path, Path, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _, release_commit = release_repo
    tag = release_verifier.resolve_annotated_tag(repo, "v1.40")
    git(repo, "remote", "set-url", "origin", CANONICAL_REMOTE)
    provider_token = tmp_path / "provider-token"
    provider_token.write_text(
        "provider-secret-is-not-valid-git-config\n", encoding="utf-8"
    )
    with (repo / ".git/config").open("a", encoding="utf-8") as config:
        config.write(f"\n[include]\n\tpath = {provider_token}\n")

    def remote_git(_url: str, *_refs: str) -> str:
        return (
            f"{tag.tag_object}\trefs/tags/v1.40\n"
            f"{release_commit}\trefs/tags/v1.40^{{}}\n"
        )

    monkeypatch.setattr(release_verifier, "remote_git", remote_git)

    release_verifier.verify_remote_tag(repo, "origin", tag)


@pytest.mark.parametrize(
    "url",
    (
        "ext::/bin/false",
        "file:///tmp/provider-token",
        "/tmp/provider-token",
        "ssh://git@github.com/jhw7500/automation.git",
        "git@github.com:jhw7500/automation.git",
        "http://github.com/jhw7500/automation.git",
        "https://github.com/other/automation.git",
        "https://provider@github.com/jhw7500/automation.git",
    ),
)
def test_remote_verification_rejects_noncanonical_url_before_transport(
    release_repo: tuple[Path, Path, str],
    monkeypatch: pytest.MonkeyPatch,
    url: str,
) -> None:
    repo, _, _ = release_repo
    git(repo, "remote", "set-url", "origin", url)
    tag = release_verifier.resolve_annotated_tag(repo, "v1.40")

    def forbidden(*_args: object, **_kwargs: object) -> str:
        pytest.fail("unsafe remote reached transport")

    monkeypatch.setattr(release_verifier, "remote_git", forbidden)

    with pytest.raises(ReleaseVerificationError, match="canonical public HTTPS"):
        release_verifier.verify_remote_tag(repo, "origin", tag)


def test_remote_verification_rejects_non_origin_name_before_transport(
    release_repo: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _, _ = release_repo
    tag = release_verifier.resolve_annotated_tag(repo, "v1.40")

    def forbidden(*_args: object, **_kwargs: object) -> str:
        pytest.fail("unsafe remote reached transport")

    monkeypatch.setattr(release_verifier, "remote_git", forbidden)

    with pytest.raises(ReleaseVerificationError, match="only origin"):
        release_verifier.verify_remote_tag(repo, "upstream", tag)


def test_release_verifier_git_failure_does_not_expose_child_or_provider_data(
    release_repo: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _, _ = release_repo
    provider = "release-provider-sentinel"
    raw = "release-raw-child-sentinel"
    monkeypatch.setenv("ZHIPU_API_KEY", provider)

    def child(args, **kwargs):
        assert provider not in kwargs["env"].values()
        return subprocess.CompletedProcess(
            args,
            31,
            stdout=f"stdout {provider} {raw}".encode(),
            stderr=f"stderr {provider} {raw}".encode(),
        )

    monkeypatch.setattr(release_verifier.subprocess, "run", child)
    with pytest.raises(ReleaseVerificationError) as raised:
        release_verifier.read_git_object(repo, "f" * 40, "blob")

    rendered = "".join(
        traceback.format_exception(
            type(raised.value), raised.value, raised.value.__traceback__
        )
    )
    assert provider not in rendered
    assert raw not in rendered
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def coordinated_permission_drift(repo: Path) -> None:
    catalog_path = repo / "scripts/workflow-catalog.json"
    catalog = load_json(catalog_path)
    entry = catalog["entries"][0]
    entry["caller_jobs"][0]["permissions"]["contents"] = "write"
    write_json(catalog_path, catalog)
    replace(
        repo / "examples/baseline-workflows/.github/workflows/claude.yml",
        "      contents: read",
        "      contents: write",
        count=1,
    )


def coordinated_trigger_drift(repo: Path) -> None:
    catalog_path = repo / "scripts/workflow-catalog.json"
    catalog = load_json(catalog_path)
    catalog["entries"][0]["trigger"]["issue_comment"]["types"] = [
        "created",
        "edited",
    ]
    write_json(catalog_path, catalog)
    replace(
        repo / "examples/baseline-workflows/.github/workflows/claude.yml",
        "    types: [created]",
        "    types: [created, edited]",
        count=1,
    )


def coordinated_central_target_drift(repo: Path) -> None:
    catalog_path = repo / "scripts/workflow-catalog.json"
    catalog = load_json(catalog_path)
    catalog["entries"][0]["central_workflow"] = "claude-code-review.yml"
    write_json(catalog_path, catalog)
    replace(
        repo / "examples/baseline-workflows/.github/workflows/claude.yml",
        "/claude.yml@__AUTOMATION_COMMIT__",
        "/claude-code-review.yml@__AUTOMATION_COMMIT__",
        count=1,
    )


def coordinated_profile_drift(repo: Path) -> None:
    config_path = repo / "scripts/workflow-config.json"
    config = load_json(config_path)
    config["repos"]["gstApp"]["repo_write_auth"] = "github_token"
    config["repos"]["gstApp"]["optional_workflows"] = [
        "opencode-auto-review.yml"
    ]
    write_json(config_path, config)


def comment_only_setup_pin(path: Path) -> None:
    approved = (
        "        uses: jhw7500/automation/.github/actions/setup-gemini-auth@"
        "2254f13aab44585c78954d20749f4fb677a8c2f1"
    )
    replace(path, approved, f"        # {approved.strip()}", count=1)


def unconditional_setup_input(path: Path) -> None:
    replace(
        path,
        "fallback-token: ${{ inputs.repo_write_auth == 'github_token' && github.token || '' }}",
        "fallback-token: ${{ github.token }}",
        count=1,
    )


def extra_local_setup_resolver(path: Path) -> None:
    document = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    steps = document["jobs"]["gemini-review"]["steps"]
    steps.append(
        {
            "name": "Extra unsafe resolver",
            "uses": "./.github/actions/setup-gemini-auth",
        }
    )
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def extra_direct_app_resolver(path: Path) -> None:
    document = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    steps = document["jobs"]["gemini-review"]["steps"]
    steps.append(
        {
            "name": "Unsafe direct App token",
            "uses": "actions/create-github-app-token@main",
            "with": {
                "app-id": "${{ inputs.app_id }}",
                "private-key": "${{ secrets.APP_PRIVATE_KEY }}",
            },
        }
    )
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def downstream_github_token(path: Path) -> None:
    replace(
        path,
        "${{ steps.auth.outputs.token }}",
        "${{ github.token }}",
        count=1,
    )


def validation_not_immediately_before_resolver(path: Path) -> None:
    needle = (
        "      - name: Resolve repository-write token\n"
        "        id: auth\n"
    )
    replacement = (
        "      - name: Intervening step\n"
        "        run: echo bypass\n\n"
        + needle
    )
    replace(path, needle, replacement, count=1)


def mutate_yaml(path: Path, mutate) -> None:
    document = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    mutate(document)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def inherited_write_without_resolver(path: Path) -> None:
    def mutate(document: dict) -> None:
        document["permissions"] = {"issues": "write"}
        document["jobs"]["inherited-writer"] = {
            "runs-on": "ubuntu-latest",
            "env": {"GH_TOKEN": "${{ github.token }}"},
            "steps": [{"run": "gh issue comment 1 --body inherited"}],
        }

    mutate_yaml(path, mutate)


def explicit_write_without_resolver(path: Path) -> None:
    def mutate(document: dict) -> None:
        document["jobs"]["direct-writer"] = {
            "runs-on": "ubuntu-latest",
            "permissions": {"issues": "write"},
            "steps": [{"run": 'echo "${{ github.token }}"'}],
        }

    mutate_yaml(path, mutate)


def github_token_in_write_job_env(path: Path) -> None:
    def mutate(document: dict) -> None:
        document["jobs"]["gemini-review"]["env"] = {
            "GH_TOKEN": "${{ github.token }}"
        }

    mutate_yaml(path, mutate)


def alternate_local_token_mint_action(path: Path) -> None:
    def mutate(document: dict) -> None:
        document["jobs"]["gemini-review"]["steps"].append(
            {
                "name": "Mint another repository token",
                "uses": "./.github/actions/mint-repository-token",
            }
        )

    mutate_yaml(path, mutate)


def github_token_in_workflow_env(path: Path) -> None:
    def mutate(document: dict) -> None:
        document["env"] = {"GH_TOKEN": "${{ github.token }}"}

    mutate_yaml(path, mutate)


def ambient_caller_write_without_permissions(path: Path) -> None:
    def mutate(document: dict) -> None:
        document["jobs"]["ambient-writer"] = {
            "runs-on": "ubuntu-latest",
            "env": {"GH_TOKEN": "${{ github.token }}"},
            "steps": [{"run": "gh issue comment 1 --body ambient"}],
        }

    mutate_yaml(path, mutate)


def test_accepts_local_and_remote_annotated_tag_at_secure_commit(
    release_repo: tuple[Path, Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _, release_commit = release_repo
    tag_object = git(repo, "rev-parse", "refs/tags/v1.40")
    git(
        repo,
        "remote",
        "set-url",
        "origin",
        "https://github.com/jhw7500/automation",
    )

    def remote_git(url: str, *refs: str) -> str:
        assert url == CANONICAL_REMOTE
        assert refs == ("refs/tags/v1.40", "refs/tags/v1.40^{}")
        return (
            f"{tag_object}\trefs/tags/v1.40\n"
            f"{release_commit}\trefs/tags/v1.40^{{}}\n"
        )

    monkeypatch.setattr(release_verifier, "remote_git", remote_git)

    assert verify_release(repo, "v1.40", release_commit, remote="origin") == release_commit


def test_rejects_lightweight_release_tag(
    release_repo: tuple[Path, Path, str],
) -> None:
    repo, _, release_commit = release_repo
    git(repo, "tag", "v1.41")
    with pytest.raises(ReleaseVerificationError, match="annotated tag"):
        verify_release(repo, "v1.41", release_commit)


def test_release_requires_an_annotated_tag_to_link_directly_to_a_commit(
    release_repo: tuple[Path, Path, str],
) -> None:
    repo, _, release_commit = release_repo
    git(repo, "tag", "-a", "v1.41", "v1.40", "-m", "v1.41")

    with pytest.raises(ReleaseVerificationError, match="annotated tag"):
        verify_release(repo, "v1.41", release_commit)


def test_rejects_tag_that_does_not_point_at_expected_commit(
    release_repo: tuple[Path, Path, str],
) -> None:
    repo, _, _ = release_repo
    (repo / "new").write_text("new", encoding="utf-8")
    new_commit = commit(repo, "new")
    with pytest.raises(ReleaseVerificationError, match="expected commit"):
        verify_release(repo, "v1.40", new_commit)


def test_rejects_remote_lightweight_tag_for_local_annotated_release(
    release_repo: tuple[Path, Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _, release_commit = release_repo
    git(repo, "remote", "set-url", "origin", CANONICAL_REMOTE)

    def remote_git(_url: str, *_refs: str) -> str:
        return f"{release_commit}\trefs/tags/v1.40\n"

    monkeypatch.setattr(release_verifier, "remote_git", remote_git)

    with pytest.raises(ReleaseVerificationError, match="annotated.*peeled"):
        verify_release(repo, "v1.40", release_commit, remote="origin")


def test_verify_release_rejects_one_way_tag_movement_during_content_reads(
    release_repo: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _, release_commit = release_repo
    alternate = alternate_tag_object(repo)
    original_read = release_verifier.read_git_object
    moved = False

    def racing_read(repository: Path, oid: str, expected_type: str) -> bytes:
        nonlocal moved
        if not moved and expected_type == "tree":
            git(repository, "update-ref", "refs/tags/v1.40", alternate)
            moved = True
        return original_read(repository, oid, expected_type)

    monkeypatch.setattr(release_verifier, "read_git_object", racing_read)
    with pytest.raises(ReleaseVerificationError, match="changed during verification"):
        verify_release(repo, "v1.40", release_commit)


def test_verify_release_binds_every_content_read_across_aba_tag_movement(
    release_repo: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _, release_commit = release_repo
    original_tag = git(repo, "rev-parse", "refs/tags/v1.40")
    alternate = alternate_tag_object(repo)
    original_read = release_verifier.read_git_object
    movements = 0
    opened_revisions: list[str] = []

    def racing_read(repository: Path, oid: str, expected_type: str) -> bytes:
        nonlocal movements
        if expected_type in {"tree", "blob"}:
            if movements == 0:
                git(repository, "update-ref", "refs/tags/v1.40", alternate)
                movements = 1
            elif movements == 1:
                git(repository, "update-ref", "refs/tags/v1.40", original_tag)
                movements = 2
        return original_read(repository, oid, expected_type)

    original_open = release_verifier.VerifiedCommitTree.open.__func__

    def capture_open(
        cls: type[release_verifier.VerifiedCommitTree],
        repository: Path,
        revision: str,
    ) -> release_verifier.VerifiedCommitTree:
        opened_revisions.append(revision)
        return original_open(cls, repository, revision)

    monkeypatch.setattr(release_verifier, "read_git_object", racing_read)
    monkeypatch.setattr(
        release_verifier.VerifiedCommitTree,
        "open",
        classmethod(capture_open),
    )
    assert verify_release(repo, "v1.40", release_commit) == release_commit
    assert movements == 2
    assert opened_revisions == [release_commit]


@pytest.mark.parametrize(
    "mutate",
    [
        coordinated_permission_drift,
        coordinated_trigger_drift,
        coordinated_central_target_drift,
        coordinated_profile_drift,
    ],
    ids=("permissions", "trigger", "central-target", "profile"),
)
def test_rejects_coordinated_drift_from_approved_v140_policy(
    release_repo: tuple[Path, Path, str], mutate
) -> None:
    repo, _, _ = release_repo
    mutate(repo)
    bad_commit = retag_bad_release(repo, "coordinated policy drift")

    with pytest.raises(ReleaseVerificationError, match="approved v1.40 policy"):
        verify_release(repo, "v1.40", bad_commit)


def test_patch_release_must_preserve_the_approved_v140_policy(
    release_repo: tuple[Path, Path, str],
) -> None:
    repo, _, _ = release_repo
    config_path = repo / "scripts/workflow-config.json"
    config = load_json(config_path)
    config["automation_ref"] = "v1.40.1"
    write_json(config_path, config)
    bad_commit = commit(repo, "patch policy drift")
    git(repo, "tag", "-a", "v1.40.1", "-m", "v1.40.1")

    with pytest.raises(ReleaseVerificationError, match="approved v1.40 policy"):
        verify_release(repo, "v1.40.1", bad_commit)


@pytest.mark.parametrize(
    ("filename", "old", "new", "error", "count"),
    [
        (
            "opencode-auto-review.yml",
            "      # id-token 없음(의도) — 이게 있으면 액션이 OIDC 토큰을 발급받아\n"
            "      # api.opencode.ai 에서 App 토큰으로 교환할 수 있고, 그 토큰은 아래 contents: read",
            "      id-token: write\n"
            "      # api.opencode.ai 에서 App 토큰으로 교환할 수 있고, 그 토큰은 아래 contents: read",
            "permissions",
            1,
        ),
        (
            "opencode-auto-review.yml",
            "      contents: read\n      pull-requests: write\n      issues: write",
            "      contents: write\n      pull-requests: write\n      issues: write",
            "permissions",
            1,
        ),
        (
            "opencode-auto-review.yml",
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            "actions/checkout@v7",
            "checkout reference",
            1,
        ),
        (
            "opencode-auto-review.yml",
            "OPENCODE_VERSION: '1.18.17'",
            "OPENCODE_VERSION: latest",
            "approved OpenCode CLI",
            1,
        ),
        (
            "opencode-auto-review.yml",
            "3f14a4c61c7f6b0d3b6d933d1d212e64e19683eba6fa453ad98e46303afe144a",
            "0" * 64,
            "approved OpenCode CLI",
            1,
        ),
        (
            "opencode-auto-review.yml",
            "persist-credentials: true",
            "persist-credentials: false",
            "private repository fetch",
            1,
        ),
        (
            "opencode.yml",
            "persist-credentials: true",
            "persist-credentials: false",
            "opencode.yml.*private",
            1,
        ),
        (
            "opencode-auto-review.yml",
            "needs.check-enabled.outputs.safe_pr == 'true'",
            "true",
            "same-repository PR guard",
            -1,
        ),
        (
            "opencode.yml",
            "github.event.pull_request.number || github.event.issue.number",
            "github.event.issue.number",
            "opencode.yml security",
            -1,
        ),
    ],
    ids=(
        "auto-oidc-permission",
        "auto-contents-write",
        "checkout-unpinned",
        "version-drift",
        "digest-drift",
        "auto-private-fetch",
        "command-private-fetch",
        "auto-same-repo-guard",
        "command-inline-review-fallback",
    ),
)
def test_preserves_opencode_release_regressions(
    release_repo: tuple[Path, Path, str],
    filename: str,
    old: str,
    new: str,
    error: str,
    count: int,
) -> None:
    repo, _, _ = release_repo
    replace(repo / ".github/workflows" / filename, old, new, count=count)
    bad_commit = retag_bad_release(repo, f"break {filename}")
    with pytest.raises(ReleaseVerificationError, match=error):
        verify_release(repo, "v1.40", bad_commit)


def test_rejects_opencode_command_oidc_app_token_path(
    release_repo: tuple[Path, Path, str],
) -> None:
    repo, _, _ = release_repo
    path = repo / ".github/workflows/opencode.yml"
    replace(
        path,
        "    permissions:\n      contents: read",
        "    permissions:\n      id-token: write\n      contents: read",
        count=1,
    )
    replace(path, "USE_GITHUB_TOKEN: 'true'", "USE_GITHUB_TOKEN: 'false'", count=1)
    bad_commit = retag_bad_release(repo, "restore App token path")
    with pytest.raises(ReleaseVerificationError, match="opencode.yml"):
        verify_release(repo, "v1.40", bad_commit)


@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        (
            lambda path: replace(
                path, "GEMINI_API_KEY", "GOOGLE_API_KEY", count=1
            ),
            "GOOGLE_API_KEY",
        ),
        (
            lambda path: replace(
                path,
                "    permissions:\n      contents: read\n      pull-requests: write\n      issues: write",
                "    permissions:\n      contents: read\n      pull-requests: write\n      issues: write\n      id-token: write",
                count=1,
            ),
            "OIDC",
        ),
        (
            lambda path: replace(path, "inputs.app_id", "vars.APP_ID", count=1),
            "ambient App",
        ),
        (
            lambda path: replace(
                path,
                "setup-gemini-auth@2254f13aab44585c78954d20749f4fb677a8c2f1",
                "setup-gemini-auth@main",
                count=1,
            ),
            "setup-gemini-auth",
        ),
        (
            lambda path: replace(
                path,
                "      repo_write_auth:\n"
                "        description: 'Repository write authentication: github_app or github_token'\n"
                "        type: string\n"
                "        required: true\n",
                "",
                count=1,
            ),
            "repo_write_auth",
        ),
        (comment_only_setup_pin, "resolver"),
        (unconditional_setup_input, "mode-controlled inputs"),
        (extra_local_setup_resolver, "resolver"),
        (extra_direct_app_resolver, "App token"),
        (downstream_github_token, "write token"),
        (validation_not_immediately_before_resolver, "immediately preceded"),
    ],
    ids=(
        "google-api-key",
        "oidc-permission",
        "ambient-app-id",
        "unpinned-setup-auth",
        "missing-explicit-mode",
        "comment-only-pin",
        "unconditional-with",
        "extra-local-resolver",
        "extra-direct-app-resolver",
        "downstream-github-token",
        "validation-gap",
    ),
)
def test_rejects_insecure_tagged_gemini_contracts(
    release_repo: tuple[Path, Path, str], mutate, error: str
) -> None:
    repo, _, _ = release_repo
    mutate(repo / ".github/workflows/gemini-auto-review.yml")
    bad_commit = retag_bad_release(repo, "break Gemini contract")
    with pytest.raises(ReleaseVerificationError, match=error):
        verify_release(repo, "v1.40", bad_commit)


@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        (inherited_write_without_resolver, "workflow-level write permissions"),
        (explicit_write_without_resolver, "exactly one.*resolver"),
        (github_token_in_write_job_env, "github.token"),
        (alternate_local_token_mint_action, "approved action"),
        (github_token_in_workflow_env, "workflow.*github.token"),
        (ambient_caller_write_without_permissions, "explicit permissions"),
    ],
    ids=(
        "inherited-write",
        "explicit-write-run-token",
        "job-env-token",
        "alternate-mint-action",
        "workflow-env-token",
        "ambient-caller-write",
    ),
)
def test_rejects_effective_gemini_write_path_auth_bypasses(
    release_repo: tuple[Path, Path, str], mutate, error: str
) -> None:
    repo, _, _ = release_repo
    mutate(repo / ".github/workflows/gemini-auto-review.yml")
    bad_commit = retag_bad_release(repo, "add effective write bypass")

    with pytest.raises(ReleaseVerificationError, match=error):
        verify_release(repo, "v1.40", bad_commit)
