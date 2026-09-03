"""Tests for verifying the exact reusable-workflow release artifact."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
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
import scripts.workflow_release_inventory as release_inventory
from scripts.verify_workflow_release import ReleaseVerificationError, verify_release
from scripts.workflow_release_inventory import RELEASE_PATHS
from release_fixture_helpers import (
    HISTORICAL_REVIEW_WORKFLOWS,
    V145_REVIEW_FIXTURE_ROOT,
    restore_historical_review_workflows,
    restore_pre_force_review_callers,
    restore_pre_v151_review_policy,
    restore_v145_review_workflows,
)

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
REQUEST_SCOPED_RUN_NAME_LINE = (
    "run-name: jhw-review-comment-${{ github.event.comment.id || github.run_id }}\n"
)


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

CANONICALIZE_REVIEW_ACTION = "$/.github/actions/canonicalize-review"
CANONICALIZER_RELEASE_FILES = (
    ".github/actions/canonicalize-review/action.yml",
    ".github/actions/canonicalize-review/canonicalize_review.py",
    ".github/actions/canonicalize-review/review_scope.py",
)
REVIEW_INVOCATION_BUDGET_RELEASE_FILES = (
    ".github/actions/review-invocation-budget/action.yml",
    ".github/actions/review-invocation-budget/review_invocation_budget.py",
)
REVIEW_POLICY_RELEASE_FILES = (
    ".github/actions/resolve-review-policy/action.yml",
    ".github/actions/resolve-review-policy/resolve_review_policy.py",
)
V1462_WORKFLOW_FIXTURE_COMMIT = "d42c28ddd827554e6e46a2ab49dfe34c838c0425"
V158_REVIEW_POLICY_HELPER_COMMIT = "1b98172325533ef6ad37f3d2cfc3870073fac26d"
V159_ROUND_BUDGET_COMMIT = "96d66e1d17952f01b19bb957057830a2b2a6318b"
V1462_WORKFLOW_FIXTURE_SHA256 = {
    "claude-code-review.yml": (
        "008bbdcdeacdaf7796c1e3b59d22194d3f1ce380735d36dada5efab8ff52d112"
    ),
    "gemini-auto-review.yml": (
        "d97561900e869f1f8d6f5e6278160967e4630b389832256d6b722cade602b4bb"
    ),
    "opencode-auto-review.yml": (
        "a38218bc27e672f7f7bde1873b9fa3de811057490f3fab7dc91c74d03d80ba97"
    ),
}
REVIEWER_WORKFLOW_CONTRACTS = {
    "claude-code-review.yml": {
        "job": "claude-review",
        "provider_step": "Run Claude Code Review",
        "collector_step": "Collect previous review context",
        "prompt_key": "prompt",
        "raw": "claude-review.md",
        "canonical": "claude-review-canonical.md",
        "marker": "<!-- automation:claude-code-review:v3 -->",
        "v2_marker": "<!-- automation:claude-code-review:v2 -->",
    },
    "gemini-auto-review.yml": {
        "job": "gemini-review",
        "provider_step": "Run Gemini Code Review",
        "collector_step": "Get PR details",
        "prompt_key": "run",
        "raw": "gemini_review.md",
        "canonical": "gemini-review-canonical.md",
        "marker": "<!-- automation:gemini-auto-review:v3 -->",
        "v2_marker": "<!-- automation:gemini-auto-review:v2 -->",
    },
}


def restore_historical_v140_manual_outputs(repo: Path) -> None:
    # v1.40 태그 픽스처는 그 시대의 fleet 상태를 담아야 한다. v1.40 정책 스냅샷
    # 해시는 workflow-config.json 전체 바이트를 커버하므로, 플릿 구성이 변한 뒤에는
    # automation_ref 치환만으로 역사적 바이트를 재현할 수 없다 — 태그에서 뽑아 둔
    # 스냅샷 픽스처(tests/fixtures/workflow-config-v1.40.json)를 통째로 복원한다.
    fixture_root = Path(__file__).parent / "fixtures"
    snapshot = fixture_root / "workflow-config-v1.40.json"
    (repo / "scripts/workflow-config.json").write_bytes(snapshot.read_bytes())
    root = repo / "examples/baseline-workflows/.github/workflows"
    # 요청 범위 run-name 은 v1.40 이후에 추가됐다 — 정책 스냅샷 바이트를 맞추려면
    # 태그 픽스처에서 그 줄을 제거해야 한다.
    for filename in ("claude.yml", "gemini-dispatch.yml"):
        path = root / filename
        text = path.read_text(encoding="utf-8")
        assert text.count(REQUEST_SCOPED_RUN_NAME_LINE) == 1, (
            f"{filename} 에서 요청 범위 run-name 을 정확히 1회 찾지 못했습니다 — "
            "라이브 워크플로우가 바뀌었으면 이 픽스처 상수를 함께 갱신하세요"
        )
        path.write_text(
            text.replace(REQUEST_SCOPED_RUN_NAME_LINE, "", 1), encoding="utf-8"
        )
    for filename in ("gemini-issue-triage.yml", "gemini-pr-review.yml"):
        path = root / filename
        text = path.read_text(encoding="utf-8")
        assert text.count(HARDENED_MANUAL_OUTPUT_BLOCK) == 1, (
            f"{filename} 에서 hardened write_output 블록을 정확히 1회 찾지 못했습니다 — "
            "라이브 워크플로우가 바뀌었으면 이 픽스처 상수를 함께 갱신하세요"
        )
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
    # This synthetic v1.40 fixture uses genuine committed v1.44 central review bytes,
    # the last release before prepare-review-diff became a release dependency.
    restore_historical_review_workflows(repo)
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
    restore_pre_v151_review_policy(repo)
    restore_pre_v160_round_budget(repo)
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    return repo, commit(repo, "current release")


def restore_v1462_workflow_fixtures(repo: Path) -> None:
    """Restore authenticated v1.46.2 workflow bytes without consulting the worktree."""

    restore_pre_force_review_callers(repo)
    tree = release_verifier.VerifiedCommitTree.open(
        ROOT, V1462_WORKFLOW_FIXTURE_COMMIT
    )
    for filename, expected_sha256 in V1462_WORKFLOW_FIXTURE_SHA256.items():
        payload = tree.read_file(f".github/workflows/{filename}")
        assert hashlib.sha256(payload).hexdigest() == expected_sha256
        (repo / ".github/workflows" / filename).write_bytes(payload)


@pytest.fixture
def v1462_release_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "v1462-automation"
    repo.mkdir()
    for relative in RELEASE_PATHS:
        source = ROOT / relative
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
    restore_v1462_workflow_fixtures(repo)
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    return repo, commit(repo, "immutable v1.46.2 release fixture")


def test_v1462_immutable_workflow_fixture_is_accepted(
    v1462_release_repo: tuple[Path, str],
) -> None:
    repo, release_commit = v1462_release_repo

    assert (
        release_verifier.verify_commit_content(
            repo, "v1.46.2", release_commit
        )
        == release_commit
    )


def prepare_v147(repo: Path) -> str:
    action_root = repo / ".github/actions/review-invocation-budget"
    if not action_root.exists():
        shutil.copytree(
            ROOT / ".github/actions/review-invocation-budget",
            action_root,
        )
    return (
        commit(repo, "v1.47 candidate")
        if git(repo, "status", "--porcelain")
        else git(repo, "rev-parse", "HEAD")
    )


def restore_pre_v159_review_policy_helper(repo: Path) -> None:
    """Restore the authenticated v1.51-v1.58 policy-helper bytes.

    v1.59 flipped the unconfigured automatic decision to opt-in, so the worktree
    helper no longer satisfies the earlier release line's pinned digest.
    """

    relative = ".github/actions/resolve-review-policy/resolve_review_policy.py"
    tree = release_verifier.VerifiedCommitTree.open(
        ROOT, V158_REVIEW_POLICY_HELPER_COMMIT
    )
    payload = tree.read_file(relative)
    assert (
        hashlib.sha256(payload).hexdigest()
        == release_verifier.EXPECTED_REVIEW_POLICY_HELPER_SHA256
    )
    (repo / relative).write_bytes(payload)


def restore_pre_v160_round_budget(repo: Path) -> None:
    """Restore the authenticated pre-v1.60 invocation-budget wiring.

    v1.60 reads the automatic-round budget from REVIEW_MAX_ROUNDS, which changed the
    budget action, its helper, and the three reusable review workflows.
    """

    tree = release_verifier.VerifiedCommitTree.open(ROOT, V159_ROUND_BUDGET_COMMIT)
    for relative, expected in (
        (
            ".github/actions/review-invocation-budget/action.yml",
            release_verifier.EXPECTED_REVIEW_INVOCATION_BUDGET_ACTION_SHA256,
        ),
        (
            ".github/actions/review-invocation-budget/review_invocation_budget.py",
            release_verifier.EXPECTED_REVIEW_INVOCATION_BUDGET_HELPER_SHA256,
        ),
    ):
        payload = tree.read_file(relative)
        assert hashlib.sha256(payload).hexdigest() == expected
        (repo / relative).write_bytes(payload)
    for filename in release_verifier.REVIEWER_WORKFLOWS.values():
        path = repo / ".github/workflows" / filename
        text = path.read_text(encoding="utf-8")
        path.write_text(
            text.replace("          max-rounds: ${{ vars.REVIEW_MAX_ROUNDS }}\n", ""),
            encoding="utf-8",
        )


def assert_pre_v160_workflow_bytes(repo: Path) -> None:
    """The stripped workflows must be exactly the published v1.51-v1.59 bytes."""

    for reviewer, filename in release_verifier.REVIEWER_WORKFLOWS.items():
        payload = (repo / ".github/workflows" / filename).read_bytes()
        assert (
            hashlib.sha256(payload).hexdigest()
            == release_verifier.EXPECTED_REVIEW_POLICY_WORKFLOW_SHA256[reviewer]
        )


def copy_review_policy_release_files(repo: Path) -> None:
    for relative in (
        ".github/actions/resolve-review-policy/action.yml",
        ".github/actions/resolve-review-policy/resolve_review_policy.py",
        ".github/workflows/claude-code-review.yml",
        ".github/workflows/gemini-auto-review.yml",
        ".github/workflows/opencode-auto-review.yml",
        "examples/baseline-workflows/.github/workflows/claude-code-review.yml",
        "examples/baseline-workflows/.github/workflows/gemini-auto-review.yml",
        "examples/baseline-workflows/.github/workflows/opencode-auto-review.yml",
        "scripts/workflow-catalog.json",
    ):
        source = ROOT / relative
        target = repo / relative
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists() or target.is_symlink():
            target.unlink()
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def prepare_v151(repo: Path) -> str:
    copy_review_policy_release_files(repo)
    restore_pre_v159_review_policy_helper(repo)
    restore_pre_v160_round_budget(repo)
    return commit(repo, "v1.51 candidate")


def prepare_v159(repo: Path) -> str:
    copy_review_policy_release_files(repo)
    restore_pre_v160_round_budget(repo)
    assert_pre_v160_workflow_bytes(repo)
    return commit(repo, "v1.59 candidate")


def prepare_v160(repo: Path) -> str:
    copy_review_policy_release_files(repo)
    for relative in (
        ".github/actions/review-invocation-budget/action.yml",
        ".github/actions/review-invocation-budget/review_invocation_budget.py",
    ):
        shutil.copy2(ROOT / relative, repo / relative)
    return commit(repo, "v1.60 candidate")


def verify_v147(repo: Path, message: str) -> str:
    candidate = commit(repo, message)
    return release_verifier.verify_commit_content(repo, "v1.47", candidate)


def test_current_commit_gate_accepts_pinned_claude_action(
    current_release_repo: tuple[Path, str],
) -> None:
    repo, current = current_release_repo

    assert (
        release_verifier.verify_commit_content(repo, "v1.47", current)
        == current
    )


@pytest.mark.parametrize(
    "filename", ("claude-code-review.yml", "claude.yml")
)
def test_current_commit_gate_rejects_moving_claude_action(
    current_release_repo: tuple[Path, str],
    filename: str,
) -> None:
    repo, _ = current_release_repo
    path = repo / ".github/workflows" / filename
    replace(
        path,
        "anthropics/claude-code-action@6bcfb8263aca9b0eab0aba20d96dddd74de2875f",
        "anthropics/claude-code-action@v1",
        count=1,
    )
    bad_commit = commit(repo, "restore moving Claude action tag")

    with pytest.raises(ReleaseVerificationError, match="Claude Code action"):
        release_verifier.verify_commit_content(repo, "v1.47", bad_commit)


def test_current_commit_gate_cannot_be_spoofed_by_nested_workflow_basename(
    current_release_repo: tuple[Path, str],
) -> None:
    repo, _ = current_release_repo
    root_workflow = repo / ".github/workflows/claude-code-review.yml"
    nested_workflow = repo / ".github/workflows/zz/claude-code-review.yml"
    nested_workflow.parent.mkdir(parents=True)
    shutil.copy2(root_workflow, nested_workflow)
    replace(
        root_workflow,
        "anthropics/claude-code-action@6bcfb8263aca9b0eab0aba20d96dddd74de2875f",
        "anthropics/claude-code-action@v1",
        count=1,
    )
    bad_commit = commit(repo, "spoof root Claude action pin")

    with pytest.raises(
        ReleaseVerificationError,
        match="unexpected nested central review workflow",
    ):
        release_verifier.verify_commit_content(repo, "v1.47", bad_commit)


def replace(path: Path, old: str, new: str, *, count: int = -1) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, count), encoding="utf-8")


def mutate_named_step_text(path: Path, step_name: str, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    marker = f"      - name: {step_name}\n"
    start = text.index(marker)
    end = text.find("\n      - name:", start + len(marker))
    if end < 0:
        end = len(text)
    block = text[start:end]
    assert block.count(old) == 1
    path.write_text(text[:start] + block.replace(old, new, 1) + text[end:])


def move_named_step(path: Path, step_name: str, anchor_name: str, *, after: bool) -> None:
    text = path.read_text(encoding="utf-8")

    def bounds(value: str, name: str) -> tuple[int, int]:
        marker = f"      - name: {name}\n"
        start = value.index(marker)
        end = value.find("\n      - name:", start + len(marker))
        return start, len(value) if end < 0 else end + 1

    start, end = bounds(text, step_name)
    block = text[start:end]
    text = text[:start] + text[end:]
    anchor_start, anchor_end = bounds(text, anchor_name)
    insertion = anchor_end if after else anchor_start
    path.write_text(text[:insertion] + block + text[insertion:], encoding="utf-8")


def append_action_reference(path: Path, reference: str) -> None:
    def append(document: dict) -> None:
        job = next(job for job in document["jobs"].values() if "steps" in job)
        job["steps"].append({"uses": reference})

    mutate_yaml(path, append)


def mutate_named_step(path: Path, job_name: str, step_name: str, mutate) -> None:
    def apply(document: dict) -> None:
        step = next(
            item
            for item in document["jobs"][job_name]["steps"]
            if item.get("name") == step_name
        )
        mutate(step)

    mutate_yaml(path, apply)


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
        release_verifier.verify_commit_content(repo, "v1.47", current)
        == current
    )


@pytest.mark.parametrize(
    ("ref", "revision"),
    (
        ("v1.45", "9bfe6f4a9991d21ae95472e939d9e6b197174e9f"),
        ("v1.45.1", "41131bb7843770259246e4125325a2ef4e95731f"),
        ("v1.45.2", "abf5e65cf6188277d9984be062d0b069c82cf25f"),
    ),
)
def test_approved_legacy_opencode_releases_remain_verifiable(
    ref: str, revision: str
) -> None:
    assert release_verifier.verify_commit_content(ROOT, ref, revision) == revision


@pytest.mark.parametrize(
    ("old", "new"),
    (
        (
            "          CANDIDATE_NONCE: ${{ needs.opencode-prepare.outputs.candidate_nonce }}",
            "          CANDIDATE_NONCE: unbound",
        ),
        (
            "--file review-full.diff --file review-scope.json",
            "--file review-full.diff",
        ),
        (
            "BEGIN_UNTRUSTED_CANDIDATE_JSON",
            "BEGIN_INSTRUCTION_CANDIDATE_JSON",
        ),
        (
            'candidate_outer_format_valid "$candidate_dir/review-repaired.md"',
            "true",
        ),
        (
            'if ! candidate_outer_format_valid "$candidate_dir/review.md" initial; then',
            'if candidate_outer_format_valid "$candidate_dir/review.md" initial; then',
        ),
        (
            'echo "OpenCode format repair still violates the required outer grammar" >&2\n'
            "              exit 1",
            'echo "OpenCode format repair still violates the required outer grammar" >&2\n'
            "              true",
        ),
        (
            'if [[ "$initial_signature" != "$repaired_signature" ]]; then',
            'if [[ "$initial_signature" == "$repaired_signature" ]]; then',
        ),
        (
            'if ! initial_signature="$(candidate_substance_signature "$candidate_dir/review.md")"; then',
            'if initial_signature="$(candidate_substance_signature "$candidate_dir/review.md")"; then',
        ),
        (
            'if ! repaired_signature="$(candidate_substance_signature "$candidate_dir/review-repaired.md")"; then',
            'if repaired_signature="$(candidate_substance_signature "$candidate_dir/review-repaired.md")"; then',
        ),
        (
            "              if len(nonce_bound_candidates) == 1:",
            "              if nonce_bound_candidates:",
        ),
        (
            "              if len(fence_candidates) == 1:",
            "              if fence_candidates:",
        ),
        (
            '          EXPLICIT_DEFECT_LABEL = (\n'
            '              r"(?:findings?|bugs?|defects?|issues?|"\n'
            '              r"vulnerabilit(?:y|ies)|regressions?|problems?|"\n'
            '              r"risks?|concerns?|flaws?|errors?)"\n'
            '          )',
            '          EXPLICIT_DEFECT_LABEL = r"(?!)"',
        ),
        (
            "                      return BENIGN_WRAPPER_HEADING.fullmatch(title) is None",
            "                      return False",
        ),
        (
            "                      if len(heading.group(1)) >= 4:",
            "                      if False:",
        ),
        (
            "                  if has_matching_markdown_title_decoration(remainder):",
            "                  if False:",
        ),
        (
            "                  or prefix_length * 2 >= len(value)",
            "                  or True",
        ),
        (
            "              return backslash_count % 2 == 0",
            "              return True",
        ),
        (
            "                  if is_markdown_thematic_break(remainder):",
            "                  if False:",
        ),
        (
            "              if raw_decorated_title_is_unapproved(line):",
            "              if False:",
        ),
        (
            '              rf"^(#{{1,6}})(?:{HORIZONTAL_SPACE}+(.*)|{HORIZONTAL_SPACE}*)$"',
            '              r"^(#{1,6})(?:[ \\t]+(.*)|[ \\t]*)$"',
        ),
        (
            "                  or has_unapproved_setext_heading(prefix)",
            "                  or False",
        ),
        (
            "                  line = raw_line.expandtabs(4)",
            "                  line = raw_line",
        ),
        (
            "          def parse_markdown_list_item(line):",
            "          def parse_markdown_list_item_disabled(line):",
        ),
        (
            "          def html_block_start(line, paragraph_open=False):",
            "          def html_block_start_disabled(line, paragraph_open=False):",
        ),
        (
            "          def classify_link_reference_start(line):",
            "          def classify_link_reference_start_disabled(line):",
        ),
        (
            "                                      and not line_interrupts_setext_paragraph(",
            "                                      and False and line_interrupts_setext_paragraph(",
        ),
        (
            "              re.IGNORECASE | re.ASCII,",
            "              re.IGNORECASE,",
        ),
        (
            "                  lines[first_section:], signature_mode=True",
            "                  lines[first_section:]",
        ),
        (
            "          def has_unapproved_plain_wrapper_prose(lines):",
            "          def has_unapproved_plain_wrapper_prose_disabled(lines):",
        ),
        (
            "          def wrapper_line_closes_setext_paragraph(",
            "          def wrapper_line_closes_setext_paragraph_disabled(",
        ),
        (
            "          def wrapper_setext_underline_indices(lines):",
            "          def wrapper_setext_underline_indices_disabled(lines):",
        ),
        (
            "                      underlines.add(index)",
            "                      pass",
        ),
        (
            "                      has_unapproved_plain_wrapper_prose(suffix)",
            "                      False",
        ),
        (
            "                  has_unapproved_plain_wrapper_prose(prefix)",
            "                  False",
        ),
        (
            "                  if not is_benign_wrapper_prose_line(lines[index]):",
            "                  if False:",
        ),
        (
            "                      or BENIGN_WRAPPER_PROSE.fullmatch(remainder) is not None",
            "                      or True",
        ),
        (
            "                  if normalized_name not in ALLOWLIST_SIMPLE_HTML_TAG_NAMES:",
            "                  if False:",
        ),
        (
            "                      or has_unapproved_markdown_list_item(suffix)",
            "                      or False",
        ),
        (
            "                  or has_unapproved_markdown_list_item(prefix)",
            "                  or False",
        ),
        (
            "                  if index in setext_underline_indices:",
            "                  if False:",
        ),
        (
            "                  literal_end = wrapper_literal_block_end(lines, index)",
            "                  literal_end = None",
        ),
        (
            "                          or incomplete_html_type in (1, 2, 3, 4, 5)",
            "                          or False",
        ),
        (
            '                              and incomplete_fence[1].strip(" \\t")',
            "                              and False",
        ),
        (
            '                          and opening_fence[1].strip(" \\t")',
            "                          and False",
        ),
        (
            "                      if is_benign_wrapper_list_item(content):",
            "                      if False:",
        ),
        (
            "                          if benign_list_item_has_unapproved_continuation(",
            "                          if False and benign_list_item_has_unapproved_continuation(",
        ),
        (
            "                  if saw_blank:",
            "                  if False:",
        ),
        (
            '              return re.fullmatch(r"[ \\t]*", line) is not None',
            "              return not line.strip()",
        ),
        (
            "          def strip_setext_containers(line, containers):",
            "          def strip_setext_containers_disabled(line, containers):",
        ),
        (
            '              if re.match(r"^<![A-Z]", content) is not None:',
            '              if re.match(r"^<![A-Za-z]", content) is not None:',
        ),
        (
            "                  or MARKDOWN_EMPTY_LIST_ITEM.fullmatch(remainder) is not None",
            "                  or False",
        ),
        (
            '              remainder = line.strip()',
            '              remainder = unicodedata.normalize("NFKC", line).strip()',
        ),
        (
            '                  or unicodedata.category(character) == "Zs"',
            '                  or character.isspace()',
        ),
        (
            "                      ).strip()\n"
            "                      return BENIGN_WRAPPER_HEADING.fullmatch(normalized) is None",
            "                      ).lstrip()\n"
            "                      return BENIGN_WRAPPER_HEADING.fullmatch(normalized) is None",
        ),
        (
            '                  " " if unicodedata.category(character) == "Cf" else character',
            '                  character',
        ),
        (
            '                  "blocks": groups,',
            '                  "blocks": {name: [] for name in ALLOWED},',
        ),
        (
            '          chmod 0500 "$contract_tool"',
            '          chmod 0500 "$contract_tool"\n'
            "          sed -i 's/\"blocks\": groups/\"blocks\": {name: [] for name in ALLOWED}/' \"$contract_tool\"",
        ),
    ),
    ids=(
        "nonce",
        "repair-files",
        "untrusted-boundary",
        "repair-preflight",
        "initial-preflight-polarity",
        "terminal-exit",
        "substance-comparison-polarity",
        "initial-signature-polarity",
        "repaired-signature-polarity",
        "nonce-bound-fence-uniqueness",
        "unbound-fence-ambiguity",
        "explicit-defect-label",
        "unapproved-wrapper-heading",
        "canonical-finding-heading-depth",
        "unapproved-decorated-title",
        "nonempty-decorated-title",
        "escaped-decoration-close",
        "thematic-break-exclusion",
        "raw-decoration-signal",
        "unicode-space-hash-heading-lookalike",
        "setext-prefix-guard",
        "setext-tab-expansion",
        "setext-list-container",
        "setext-html-block",
        "setext-link-reference",
        "setext-reference-block-precedence",
        "ascii-case-only-section-heading",
        "signature-section-canonicalization",
        "outside-plain-prose-helper",
        "setext-paragraph-state-helper",
        "setext-underline-indexer",
        "setext-underline-index-record",
        "outside-plain-prose-suffix-guard",
        "outside-plain-prose-prefix-guard",
        "outside-plain-prose-rejection",
        "outside-plain-prose-allowlist",
        "closed-inline-html-tag-names",
        "outside-list-suffix-guard",
        "outside-list-prefix-guard",
        "outside-list-setext-underline-skip",
        "outside-list-literal-block-skip",
        "incomplete-html-fail-fast",
        "incomplete-root-fence-fail-fast",
        "incomplete-list-fence-fail-fast",
        "outside-list-benign-vocabulary",
        "outside-list-benign-continuation-guard",
        "outside-list-postblank-container-replay",
        "commonmark-blank-line-semantics",
        "setext-container-matching",
        "setext-html-declaration-case",
        "setext-empty-list-item",
        "raw-decoration-provenance",
        "commonmark-whitespace",
        "decorated-title-trailing-space",
        "format-control-normalization",
        "constant-signature-groups",
        "post-heredoc-contract-rewrite",
    ),
)
def test_current_release_rejects_opencode_format_repair_runtime_drift(
    current_release_repo: tuple[Path, str], old: str, new: str
) -> None:
    repo, _ = current_release_repo
    replace(repo / ".github/workflows/opencode-auto-review.yml", old, new, count=1)
    bad_commit = commit(repo, "weaken OpenCode format repair runtime")

    with pytest.raises(ReleaseVerificationError, match="OpenCode CLI runtime"):
        release_verifier.verify_commit_content(repo, "v1.47", bad_commit)


def test_current_release_rejects_full_legacy_opencode_runtime_downgrade(
    current_release_repo: tuple[Path, str],
) -> None:
    repo, _ = current_release_repo
    path = repo / ".github/workflows/opencode-auto-review.yml"

    def downgrade(document: dict) -> None:
        job = document["jobs"]["opencode-review"]
        step = next(
            item for item in job["steps"]
            if item.get("name") == "Run OpenCode PR review"
        )
        step["env"].pop("CANDIDATE_NONCE")
        step["run"] = (
            "opencode run --model zai-coding-plan/glm-4.7 --format json "
            "--file review-full.diff --file review-scope.json\n"
            "jq -Rrs 'map(fromjson) | if length == 0 then error(\"empty\") "
            "else last end' opencode-review.jsonl\n"
        )

    mutate_yaml(path, downgrade)
    bad_commit = commit(repo, "downgrade OpenCode format runtime")

    with pytest.raises(ReleaseVerificationError, match="OpenCode CLI runtime"):
        release_verifier.verify_commit_content(repo, "v1.47", bad_commit)


def test_current_release_rejects_opencode_custom_shell_preprocessor(
    current_release_repo: tuple[Path, str],
) -> None:
    repo, _ = current_release_repo
    path = repo / ".github/workflows/opencode-auto-review.yml"

    def mutate_shell(document: dict) -> None:
        step = next(
            item
            for item in document["jobs"]["opencode-review"]["steps"]
            if item.get("name") == "Run OpenCode PR review"
        )
        step["shell"] = "python3 {0}"

    mutate_yaml(path, mutate_shell)
    bad_commit = commit(repo, "preprocess OpenCode review script with a custom shell")

    with pytest.raises(ReleaseVerificationError, match="OpenCode CLI runtime"):
        release_verifier.verify_commit_content(repo, "v1.47", bad_commit)


@pytest.mark.parametrize("placement", ("install-body", "extra-step"))
def test_current_release_rejects_opencode_interpreter_poisoning_before_review(
    current_release_repo: tuple[Path, str], placement: str
) -> None:
    repo, _ = current_release_repo
    path = repo / ".github/workflows/opencode-auto-review.yml"
    poison = (
        "printf '%s\\n' '#!/bin/sh' 'printf \"{}\\n\"' "
        '> "$RUNNER_TEMP/opencode-cli/python3"\n'
        'chmod 0755 "$RUNNER_TEMP/opencode-cli/python3"'
    )

    def mutate_runtime(document: dict) -> None:
        steps = document["jobs"]["opencode-review"]["steps"]
        run_index = next(
            index
            for index, item in enumerate(steps)
            if item.get("name") == "Run OpenCode PR review"
        )
        if placement == "install-body":
            install = next(
                item
                for item in steps
                if item.get("name") == "Install pinned OpenCode CLI"
            )
            install["run"] += "\n" + poison
        else:
            steps.insert(
                run_index,
                {
                    "name": "Poison Python before review",
                    "shell": "bash",
                    "run": poison,
                },
            )

    mutate_yaml(path, mutate_runtime)
    bad_commit = commit(repo, f"poison OpenCode interpreter via {placement}")

    with pytest.raises(ReleaseVerificationError, match="OpenCode CLI runtime"):
        release_verifier.verify_commit_content(repo, "v1.47", bad_commit)


@pytest.mark.parametrize(
    ("relative", "old", "new"),
    (
        (
            ".github/workflows/opencode-auto-review.yml",
            "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
            "actions/upload-artifact@" + "0" * 40,
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "          merge-multiple: true",
            "          merge-multiple: false",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "          HANDOFF_ARTIFACT_ID: ${{ needs.opencode-prepare.outputs.handoff_artifact_id }}\n"
            "          HANDOFF_ARTIFACT_DIGEST: ${{ needs.opencode-prepare.outputs.handoff_artifact_digest }}",
            "          HANDOFF_ARTIFACT_ID: ${{ needs.opencode-prepare.outputs.handoff_artifact_id }}\n"
            "          HANDOFF_ARTIFACT_DIGEST: unsealed",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "      actions: read\n      checks: read\n      contents: read",
            "      actions: write\n      checks: read\n      contents: read",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "    permissions: {}",
            "    permissions:\n      actions: read\n      checks: read\n"
            "      contents: read",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "      actions: read\n      checks: write\n      contents: read",
            "      actions: read\n      checks: read\n      contents: read",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "github.rest.checks.create",
            "github.rest.checks.listForRef",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "github.rest.actions.listWorkflowRunsForRepo",
            "github.rest.actions.getWorkflowRunAttempt",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "event: 'pull_request', per_page: 100, page: 1",
            "event: 'pull_request', status: 'success', per_page: 100, page: 1",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "-f event=pull_request -F per_page=100 -F page=1",
            "-f event=pull_request -f status=success -F per_page=100 -F page=1",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "run_id: a.run_id, attempt_number: a.run_attempt,",
            "run_id: a.run_id, attempt_number: selectedRun.run_attempt,",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "if (bounded.length > 40)",
            "if (bounded.length > 400)",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "const claimed = comments.map(parseRecord).filter(Boolean);",
            "const claimed = comments.map(parseRecord).filter(Boolean).filter(() => false);",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "if (run?.status !== 'completed')",
            "if (false)",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "const authenticateLive = async (comments) => {",
            "const authenticateLive = async (comments) => { unresolvedAttemptEvidence.clear();",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "unresolvedAttemptEvidence.set(cacheKey, candidate);",
            "unresolvedAttemptEvidence.delete(cacheKey);",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "name: 'automation/opencode-canonical-review', head_sha: workflowHead",
            "name: 'automation/opencode-canonical-review', head_sha: attemptHead",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "prepared_run_attempt: handoff.run_attempt",
            "prepared_run_attempt: runAttempt",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "const maxUntrustedCleanupComments = 20;",
            "const maxUntrustedCleanupComments = 200;",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "raw !== JSON.stringify({ path: anchor.path, line: anchor.line })",
            "false",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "evidence.removedLines[0] !== previous[0].currentLines[0]",
            "false",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "'merge-base', '--is-ancestor', previousHead, attemptHead",
            "'merge-base', previousHead, attemptHead",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "!['changed', 'modified', 'removed'].includes(identityRecords[0].status)",
            "!['changed', 'modified', 'removed', 'renamed'].includes(identityRecords[0].status)",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "ranges.removedLines.get(removal.line) !== removal.currentLine",
            "false",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "hasMarkdownEvidenceField(normalizedEvidenceLine)",
            "false",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "HTML_NUMERIC_EVIDENCE_ENTITY, (raw, hex, decimal) => {",
            "HTML_NUMERIC_EVIDENCE_ENTITY, () => {",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "if (quote !== null) {",
            "if (false) {",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "NAMED_EVIDENCE_REPLACEMENTS.get(name) ?? raw",
            "' '",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "normalized = normalized.replace(/\\p{Cf}/gu, ' ');",
            "normalized = normalized;",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "'diff', '--no-ext-diff', '--no-textconv', '--text', "
            "'--find-renames=50%',",
            "'diff', '--no-ext-diff', '--no-textconv', '--find-renames=50%',",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "'--output-indicator-new=%', `${previousHead}..${attemptHead}`",
            "'--output-indicator-new=+', `${previousHead}..${attemptHead}`",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "globallyAddedLines.has(removal.currentLine)",
            "false",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "? [file.previous_filename, file.filename] : [file.filename]",
            "? [file.filename] : [file.filename]",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "'diff', '--no-ext-diff', '--no-textconv', '--name-status', '-z',\n"
            "                  '--find-renames=50%', '--ignore-submodules=none',",
            "'diff', '--no-ext-diff', '--no-textconv', '--name-status', '-z',\n"
            "                  '--find-renames=50%',",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "'diff', '--no-ext-diff', '--no-textconv', '--find-renames=50%',\n"
            "                  '--ignore-submodules=none', '--inter-hunk-context=0', "
            "'--no-color', '-U0',",
            "'diff', '--no-ext-diff', '--find-renames=50%',\n"
            "                  '--ignore-submodules=none', '--inter-hunk-context=0', "
            "'--no-color', '-U0',",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "'--ignore-submodules=none', '--inter-hunk-context=0', '--no-color', '-U0',",
            "'--ignore-submodules=none', '--no-color', '-U0',",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "'--ignore-submodules=none', '--inter-hunk-context=0', '--no-color', '-U0',",
            "'--ignore-submodules=none', '--inter-hunk-context=0', '-U0',",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "} else if (line.startsWith('+')) {",
            "} else if (line.startsWith(' ')) {",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "const ranges = parseAddedRanges(result.stdout);",
            "const ranges = [[1, Number.MAX_SAFE_INTEGER]];",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "if (inHunk && (oldRemaining !== 0 || newRemaining !== 0)) return null;",
            "if (false) return null;",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "if (!inHunk || lastBodyPrefix === null",
            "if (lastBodyPrefix === null",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "|| (lastBodyPrefix === '+' && newRemaining !== 0)",
            "|| (lastBodyPrefix === '+' && false)",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "const oldEnd = oldStart + oldCount;",
            "const oldEnd = oldStart;",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "oldStart < previousOldEnd",
            "oldStart > previousOldEnd",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "oldStart === previousOldStart",
            "false",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "|| (oldCount === 0 && newCount === 0)",
            "|| false",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "if (inHunk && (oldEofMarked || newEofMarked)) return null;",
            "if (false) return null;",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "if (lastBodyPrefix === '+' || lastBodyPrefix === ' ') newEofMarked = true;",
            "if (false) newEofMarked = true;",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "`${manifest.merge_base_sha}..${manifest.head_sha}`, '--', ...pathspecs,",
            "`${manifest.merge_base_sha}..${attemptHead}`, '--', ...pathspecs,",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "records.length !== 1 || records[0].status !== file.status",
            "records.length < 1 || records[0].status !== file.status",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "            if (Buffer.byteLength(bodyFor(Number.MAX_SAFE_INTEGER), 'utf8') > 65536) {\n"
            "              throw new Error('canonical OpenCode comment exceeds 65,536-byte publication limit');\n"
            "            }\n"
            "            if (!(await repairComments())) return;",
            "            if (!(await repairComments())) return;\n"
            "            if (Buffer.byteLength(bodyFor(Number.MAX_SAFE_INTEGER), 'utf8') > 65536) {\n"
            "              throw new Error('canonical OpenCode comment exceeds 65,536-byte publication limit');\n"
            "            }",
        ),
        (
            ".github/workflows/opencode-auto-review.yml",
            "const maxUntrustedCleanupComments = 20;",
            "const maxUntrustedCleanupComments = 20;\n"
            "            for (const raw of commentCandidates) {}",
        ),
        (
            "examples/baseline-workflows/.github/workflows/opencode-auto-review.yml",
            "      actions: read\n      checks: write\n",
            "",
        ),
        (
            ".github/workflows/_self-opencode-auto-review.yml",
            "      actions: read\n      checks: write\n",
            "",
        ),
    ),
    ids=(
        "upload-pin",
        "download-layout",
        "artifact-digest",
        "prepare-write",
        "model-checks-actions",
        "canonical-checks",
        "check-protocol",
        "server-run-discovery",
        "live-latest-success-only",
        "prepare-latest-success-only",
        "exact-historical-attempt",
        "historical-overflow",
        "all-strict-live-records",
        "incomplete-attempt-cache",
        "pending-reset",
        "pending-forgetting",
        "workflow-head-binding",
        "prepared-attempt-binding",
        "bounded-marker-cleanup",
        "canonical-json-anchor",
        "canonical-removed-prior-binding",
        "canonical-removed-ancestor",
        "canonical-removed-rename-rejection",
        "canonical-removed-line-membership",
        "canonical-evidence-wrapper-normalization",
        "canonical-evidence-entity-normalization",
        "canonical-evidence-link-title-quoting",
        "canonical-evidence-named-entity-whitelist",
        "canonical-evidence-format-control-normalization",
        "canonical-global-added-text-mode",
        "canonical-global-added-indicator",
        "canonical-global-readd-rejection",
        "canonical-rename-endpoints",
        "canonical-name-status-flags",
        "canonical-hunk-flags",
        "canonical-inter-hunk-zero",
        "canonical-no-color",
        "canonical-plus-lines-only",
        "canonical-body-parser-call",
        "canonical-hunk-count-exhaustion",
        "canonical-no-newline-marker-location",
        "canonical-no-newline-marker-side-exhaustion",
        "canonical-hunk-exclusive-end",
        "canonical-hunk-monotonicity",
        "canonical-zero-count-coordinate-duplicates",
        "canonical-empty-hunk",
        "canonical-no-later-hunk-after-eof",
        "canonical-eof-side-tracking",
        "canonical-same-graph-range",
        "canonical-exact-scope-record",
        "canonical-size-before-repair",
        "unbounded-candidate-cleanup",
        "baseline-caller-ceiling",
        "self-caller-ceiling",
    ),
)
def test_current_release_rejects_opencode_attestation_boundary_drift(
    current_release_repo: tuple[Path, str], relative: str, old: str, new: str
) -> None:
    repo, _ = current_release_repo
    replace(repo / relative, old, new, count=1)
    bad_commit = commit(repo, "weaken OpenCode attestation boundary")

    with pytest.raises(ReleaseVerificationError):
        release_verifier.verify_commit_content(repo, "v1.47", bad_commit)


def test_v147_rejects_opencode_filtered_candidate_location_drift(
    current_release_repo: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _ = current_release_repo
    path = repo / ".github/workflows/opencode-auto-review.yml"
    mutate_named_step_text(
        path,
        "Canonicalize OpenCode review",
        "` → \\`review.md\\``",
        "` → \\`filtered.md\\``",
    )
    bad_commit = commit(repo, "drift filtered OpenCode candidate location")
    monkeypatch.setitem(
        release_verifier.EXPECTED_REVIEW_INVOCATION_BUDGET_WORKFLOW_SHA256,
        "opencode",
        hashlib.sha256(path.read_bytes()).hexdigest(),
    )

    with pytest.raises(
        ReleaseVerificationError,
        match="OpenCode sealed handoff/attestation contract is invalid",
    ):
        release_verifier.verify_commit_content(repo, "v1.47", bad_commit)


def test_prepare_diff_capability_boundary_is_shared_with_release_inventory() -> None:
    capability = getattr(
        release_inventory, "release_supports_prepare_review_diff", None
    )
    assert callable(capability)
    assert capability("v1.44") is False
    assert capability("v1.45") is True
    assert (
        release_inventory.PREPARE_REVIEW_DIFF_ACTION_ROOT
        not in release_inventory.release_roots_for("v1.44")
    )
    assert (
        release_inventory.PREPARE_REVIEW_DIFF_ACTION_ROOT
        in release_inventory.release_roots_for("v1.45")
    )


def test_v147_budget_capability_boundary_is_shared_with_release_inventory() -> None:
    capability = getattr(
        release_inventory, "release_supports_review_invocation_budget", None
    )
    assert callable(capability)
    assert capability("v1.46.2") is False
    assert capability("v1.47") is True


def test_v147_accepts_current_budget_release_contract(
    current_release_repo: tuple[Path, str],
) -> None:
    repo, _ = current_release_repo
    candidate = prepare_v147(repo)

    assert release_verifier.verify_commit_content(repo, "v1.47", candidate) == candidate


def test_v151_accepts_current_review_policy_release_contract(
    current_release_repo: tuple[Path, str],
) -> None:
    repo, _ = current_release_repo
    candidate = prepare_v151(repo)

    assert release_verifier.verify_commit_content(repo, "v1.51", candidate) == candidate


def test_v159_accepts_current_review_policy_release_contract(
    current_release_repo: tuple[Path, str],
) -> None:
    repo, _ = current_release_repo
    candidate = prepare_v159(repo)

    assert release_verifier.verify_commit_content(repo, "v1.59", candidate) == candidate


def test_v159_opt_in_helper_is_rejected_on_the_v151_release_line(
    current_release_repo: tuple[Path, str],
) -> None:
    repo, _ = current_release_repo
    candidate = prepare_v159(repo)

    with pytest.raises(ReleaseVerificationError, match="review-policy helper"):
        release_verifier.verify_commit_content(repo, "v1.51", candidate)


def test_v160_accepts_current_round_budget_release_contract(
    current_release_repo: tuple[Path, str],
) -> None:
    repo, _ = current_release_repo
    candidate = prepare_v160(repo)

    assert release_verifier.verify_commit_content(repo, "v1.60", candidate) == candidate


def test_v160_round_budget_wiring_is_rejected_on_the_v159_release_line(
    current_release_repo: tuple[Path, str],
) -> None:
    repo, _ = current_release_repo
    candidate = prepare_v160(repo)

    with pytest.raises(ReleaseVerificationError):
        release_verifier.verify_commit_content(repo, "v1.59", candidate)


def test_pre_v160_round_budget_wiring_is_rejected_on_the_v160_release_line(
    current_release_repo: tuple[Path, str],
) -> None:
    repo, _ = current_release_repo
    candidate = prepare_v159(repo)

    with pytest.raises(ReleaseVerificationError):
        release_verifier.verify_commit_content(repo, "v1.60", candidate)


def test_pre_v159_helper_is_rejected_on_the_v159_release_line(
    current_release_repo: tuple[Path, str],
) -> None:
    repo, _ = current_release_repo
    candidate = prepare_v151(repo)

    with pytest.raises(ReleaseVerificationError, match="review-policy helper"):
        release_verifier.verify_commit_content(repo, "v1.59", candidate)


@pytest.mark.parametrize("relative", REVIEW_POLICY_RELEASE_FILES)
@pytest.mark.parametrize("mutation", ("missing", "executable", "symlink"))
def test_v151_requires_each_review_policy_file_as_one_regular_0644_blob(
    current_release_repo: tuple[Path, str], relative: str, mutation: str
) -> None:
    repo, _ = current_release_repo
    prepare_v151(repo)
    target = repo / relative
    if mutation == "missing":
        target.unlink()
    elif mutation == "executable":
        target.chmod(0o755)
    else:
        target.unlink()
        target.symlink_to("untrusted-policy")
    bad_commit = commit(repo, f"mutate v1.51 policy file {relative}: {mutation}")

    with pytest.raises(ReleaseVerificationError, match="release inventory"):
        release_verifier.verify_commit_content(repo, "v1.51", bad_commit)


def test_v151_rejects_files_outside_closed_review_policy_action_inventory(
    current_release_repo: tuple[Path, str],
) -> None:
    repo, _ = current_release_repo
    prepare_v151(repo)
    extra = repo / ".github/actions/resolve-review-policy/unowned.py"
    extra.write_text("raise RuntimeError('unowned')\n", encoding="utf-8")
    bad_commit = commit(repo, "add unowned review-policy helper")

    with pytest.raises(ReleaseVerificationError, match="review-policy inventory"):
        release_verifier.verify_commit_content(repo, "v1.51", bad_commit)


@pytest.mark.parametrize(
    ("section", "mutation"),
    (
        ("inputs", "missing"),
        ("inputs", "extra"),
        ("inputs", "reordered"),
        ("outputs", "missing"),
        ("outputs", "extra"),
        ("outputs", "reordered"),
    ),
)
def test_v151_rejects_review_policy_action_interface_mutations(
    current_release_repo: tuple[Path, str], section: str, mutation: str
) -> None:
    repo, _ = current_release_repo
    prepare_v151(repo)
    path = repo / REVIEW_POLICY_RELEASE_FILES[0]

    def mutate(document: dict) -> None:
        values = document[section]
        first = next(iter(values))
        if mutation == "missing":
            values.pop(first)
        elif mutation == "extra":
            values["unowned"] = {"required": "false"}
        else:
            value = values.pop(first)
            values[first] = value

    mutate_yaml(path, mutate)
    bad_commit = commit(repo, f"mutate review-policy {section}: {mutation}")

    with pytest.raises(ReleaseVerificationError, match="review-policy action"):
        release_verifier.verify_commit_content(repo, "v1.51", bad_commit)


@pytest.mark.parametrize(
    ("old", "new"),
    (
        ("set -euo pipefail", "set -eu"),
        (
            'gh api "repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}"',
            'gh pr view "$PR_NUMBER"',
        ),
        (
            '--request-file "$policy_dir/request.json"',
            '--request-json "$(cat "$policy_dir/request.json")"',
        ),
    ),
)
def test_v151_rejects_review_policy_action_transport_mutations(
    current_release_repo: tuple[Path, str], old: str, new: str
) -> None:
    repo, _ = current_release_repo
    prepare_v151(repo)
    path = repo / REVIEW_POLICY_RELEASE_FILES[0]
    replace(path, old, new, count=1)
    bad_commit = commit(repo, "weaken review-policy action transport")

    with pytest.raises(ReleaseVerificationError, match="review-policy action"):
        release_verifier.verify_commit_content(repo, "v1.51", bad_commit)


def test_v151_rejects_duplicate_top_level_review_policy_action_key(
    current_release_repo: tuple[Path, str],
) -> None:
    repo, _ = current_release_repo
    prepare_v151(repo)
    path = repo / REVIEW_POLICY_RELEASE_FILES[0]
    replace(
        path,
        "\nruns:\n",
        "\nruns:\n  using: docker\n\nruns:\n",
        count=1,
    )
    bad_commit = commit(repo, "duplicate top-level review-policy action key")

    with pytest.raises(ReleaseVerificationError, match="review-policy action"):
        release_verifier.verify_commit_content(repo, "v1.51", bad_commit)


def test_v151_rejects_duplicate_nested_reusable_workflow_key(
    current_release_repo: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _ = current_release_repo
    prepare_v151(repo)
    path = repo / ".github/workflows/claude-code-review.yml"
    replace(
        path,
        "      review_mode:\n",
        "      review_mode:\n"
        "        description: ignored duplicate\n"
        "      review_mode:\n",
        count=1,
    )
    monkeypatch.setitem(
        release_verifier.EXPECTED_REVIEW_POLICY_WORKFLOW_SHA256,
        "claude",
        hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    bad_commit = commit(repo, "duplicate nested reusable-workflow key")

    with pytest.raises(ReleaseVerificationError):
        release_verifier.verify_commit_content(repo, "v1.51", bad_commit)


def test_v151_rejects_duplicate_nested_review_policy_caller_key(
    current_release_repo: tuple[Path, str],
) -> None:
    repo, _ = current_release_repo
    prepare_v151(repo)
    path = (
        repo
        / "examples/baseline-workflows/.github/workflows/opencode-auto-review.yml"
    )
    replace(
        path,
        "      review_mode: >-\n",
        "      review_mode: skip\n      review_mode: >-\n",
        count=1,
    )
    bad_commit = commit(repo, "duplicate nested review-policy caller key")

    with pytest.raises(ReleaseVerificationError):
        release_verifier.verify_commit_content(repo, "v1.51", bad_commit)


@pytest.mark.parametrize(
    ("old", "new"),
    (
        ("review:request", "review:ask"),
        ("workflow_auto_", "workflow_default_"),
        ("    workflow_name: str\n", "    workflow_name: int\n"),
        (
            '    if workflow is not None and "auto" in workflow:\n',
            '    if False and workflow is not None and "auto" in workflow:\n',
        ),
    ),
)
def test_v151_rejects_review_policy_helper_mutations(
    current_release_repo: tuple[Path, str], old: str, new: str
) -> None:
    repo, _ = current_release_repo
    prepare_v151(repo)
    path = repo / REVIEW_POLICY_RELEASE_FILES[1]
    replace(path, old, new, count=1)
    bad_commit = commit(repo, "weaken review-policy helper")

    with pytest.raises(ReleaseVerificationError, match="review-policy helper"):
        release_verifier.verify_commit_content(repo, "v1.51", bad_commit)


@pytest.mark.parametrize(
    ("workflow", "mutation"),
    (
        ("claude-code-review.yml", "missing"),
        ("gemini-auto-review.yml", "duplicate"),
        ("opencode-auto-review.yml", "missing"),
    ),
)
def test_v151_requires_each_reusable_to_call_review_policy_exactly_once(
    current_release_repo: tuple[Path, str], workflow: str, mutation: str
) -> None:
    repo, _ = current_release_repo
    prepare_v151(repo)
    path = repo / ".github/workflows" / workflow

    def mutate(document: dict) -> None:
        job = document["jobs"]["check-enabled"]
        step = next(
            item
            for item in job["steps"]
            if item.get("uses") == "$/.github/actions/resolve-review-policy"
        )
        if mutation == "missing":
            job["steps"].remove(step)
        else:
            job["steps"].append(dict(step))

    mutate_yaml(path, mutate)
    bad_commit = commit(repo, f"{mutation} review-policy action in {workflow}")

    with pytest.raises(ReleaseVerificationError, match="review action dependency"):
        release_verifier.verify_commit_content(repo, "v1.51", bad_commit)


@pytest.mark.parametrize(
    ("reviewer", "workflow", "job"),
    (
        ("claude", "claude-code-review.yml", "claude-review"),
        ("gemini", "gemini-auto-review.yml", "gemini-review"),
        ("opencode", "opencode-auto-review.yml", "opencode-review"),
    ),
)
def test_v151_rejects_provider_job_without_policy_run_dependency(
    current_release_repo: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    reviewer: str,
    workflow: str,
    job: str,
) -> None:
    repo, _ = current_release_repo
    prepare_v151(repo)
    path = repo / ".github/workflows" / workflow

    def mutate(document: dict) -> None:
        condition = document["jobs"][job]["if"]
        document["jobs"][job]["if"] = condition.replace(
            "needs.check-enabled.outputs.policy_run == 'true'", "true", 1
        )

    mutate_yaml(path, mutate)
    monkeypatch.setitem(
        release_verifier.EXPECTED_REVIEW_POLICY_WORKFLOW_SHA256,
        reviewer,
        hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    bad_commit = commit(repo, f"remove {reviewer} policy_run dependency")

    with pytest.raises(ReleaseVerificationError, match="review-policy workflow"):
        release_verifier.verify_commit_content(repo, "v1.51", bad_commit)


@pytest.mark.parametrize(
    ("workflow", "mutation"),
    (
        ("claude-code-review.yml", "ready-trigger"),
        ("gemini-auto-review.yml", "draft-guard"),
        ("opencode-auto-review.yml", "review-mode"),
        ("claude-code-review.yml", "label-spelling"),
        ("gemini-auto-review.yml", "precedence"),
        ("opencode-auto-review.yml", "dispatch-shape"),
    ),
)
def test_v151_rejects_review_policy_caller_mutations(
    current_release_repo: tuple[Path, str], workflow: str, mutation: str
) -> None:
    repo, _ = current_release_repo
    prepare_v151(repo)
    path = (
        repo
        / "examples/baseline-workflows/.github/workflows"
        / workflow
    )
    if mutation == "ready-trigger":
        replace(path, ", ready_for_review", "", count=1)
    elif mutation == "draft-guard":
        replace(
            path,
            "      github.event.pull_request.head.repo.full_name == github.repository &&\n"
            "      github.event.pull_request.draft == false) ||\n",
            "      github.event.pull_request.head.repo.full_name == github.repository) ||\n",
            count=1,
        )
    elif mutation == "review-mode":
        mutate_yaml(
            path,
            lambda document: document["jobs"]["opencode-review"]["with"].pop(
                "review_mode"
            ),
        )
    elif mutation == "label-spelling":
        replace(path, "review:request", "review:ask")
    elif mutation == "precedence":
        text = path.read_text(encoding="utf-8")
        request = "          contains(github.event.pull_request.labels.*.name, 'review:request') && 'request' ||\n"
        skip = "          contains(github.event.pull_request.labels.*.name, 'review:skip') && 'skip' ||\n"
        assert text.count(request) == 1 and text.count(skip) == 1
        path.write_text(
            text.replace(request, "__REQUEST_BRANCH__\n", 1)
            .replace(skip, request, 1)
            .replace("__REQUEST_BRANCH__\n", skip, 1),
            encoding="utf-8",
        )
    else:
        replace(
            path,
            "      (github.event_name == 'workflow_dispatch' && inputs.force_review)\n",
            "      github.event_name == 'workflow_dispatch'\n",
            count=1,
        )
    bad_commit = commit(repo, f"mutate {workflow} policy caller: {mutation}")

    with pytest.raises(ReleaseVerificationError, match="review-policy caller"):
        release_verifier.verify_commit_content(repo, "v1.51", bad_commit)


@pytest.mark.parametrize("relative", REVIEW_INVOCATION_BUDGET_RELEASE_FILES)
@pytest.mark.parametrize(
    "mutation", ("missing", "directory", "executable", "symlink", "gitlink")
)
def test_v147_requires_each_budget_file_as_one_regular_0644_blob(
    current_release_repo: tuple[Path, str], relative: str, mutation: str
) -> None:
    repo, _ = current_release_repo
    prepare_v147(repo)
    target = repo / relative
    if mutation == "missing":
        target.unlink()
    elif mutation == "directory":
        target.unlink()
        target.mkdir()
        (target / "dummy").write_text("not the release file\n", encoding="utf-8")
    elif mutation == "executable":
        target.chmod(0o755)
    elif mutation == "symlink":
        target.unlink()
        target.symlink_to("untrusted-target")
    else:
        target.unlink()
        parent_commit = git(repo, "rev-parse", "HEAD")
        git(repo, "add", "-u", "--", relative)
        git(
            repo,
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{parent_commit},{relative}",
        )
        git(repo, "commit", "-qm", f"replace {relative} with a gitlink")
        bad_commit = git(repo, "rev-parse", "HEAD")
        with pytest.raises(ReleaseVerificationError, match="release inventory"):
            release_verifier.verify_commit_content(repo, "v1.47", bad_commit)
        return
    bad_commit = commit(repo, f"mutate v1.47 budget file {relative}")

    with pytest.raises(ReleaseVerificationError, match="release inventory"):
        release_verifier.verify_commit_content(repo, "v1.47", bad_commit)


def test_v147_rejects_files_outside_the_closed_budget_action_inventory(
    current_release_repo: tuple[Path, str],
) -> None:
    repo, _ = current_release_repo
    prepare_v147(repo)
    extra = repo / ".github/actions/review-invocation-budget/unowned_helper.py"
    extra.write_text("raise RuntimeError('not release owned')\n", encoding="utf-8")
    bad_commit = commit(repo, "add unowned invocation-budget helper")

    with pytest.raises(
        ReleaseVerificationError, match="invocation-budget inventory"
    ):
        release_verifier.verify_commit_content(repo, "v1.47", bad_commit)


@pytest.mark.parametrize(
    "needle",
    (
        "duplicate_head",
        "duplicate_effective_diff",
        "round_budget_exhausted",
        "input_budget_exhausted",
        "total_usage_budget_exhausted",
        "call_budget_exhausted",
        "wall_time_exhausted",
        "provenance_mismatch",
        "compare_and_swap_failed",
    ),
)
def test_v147_rejects_budget_helper_gate_removal(
    current_release_repo: tuple[Path, str], needle: str
) -> None:
    repo, _ = current_release_repo
    prepare_v147(repo)
    helper = repo / REVIEW_INVOCATION_BUDGET_RELEASE_FILES[1]
    replace(helper, needle, "weakened", count=1)
    bad_commit = commit(repo, f"remove invocation-budget gate {needle}")

    with pytest.raises(
        ReleaseVerificationError, match="invocation-budget helper contract"
    ):
        release_verifier.verify_commit_content(repo, "v1.47", bad_commit)


@pytest.mark.parametrize(
    "mutation",
    ("ledger-schema", "decision-ast-order", "reviewer-call-caps", "rvw-identity"),
)
def test_v147_budget_helper_semantics_reject_authenticated_mutations(
    monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    source = (
        ROOT
        / ".github/actions/review-invocation-budget/review_invocation_budget.py"
    ).read_text(encoding="utf-8")
    if mutation == "ledger-schema":
        source = source.replace(
            'keys = {"schema", "repository", "pr", "reviewer", "budgets", '
            '"invocations", "consumed_override_event_ids", "last_decision", '
            '"handoff"}',
            'keys = {"schema", "repository", "pr", "reviewer", "budgets", '
            '"invocations", "consumed_override_event_ids", "last_decision", '
            '"handoff", "unowned"}',
            1,
        )
    elif mutation == "decision-ast-order":
        signature = (
            "def claim(state: LedgerState | None, request: ClaimRequest,\n"
            "          provenances: Mapping[tuple[int, int], RunProvenance]) -> Transition:\n"
        )
        decoy = (
            signature
            + "    _decision_order_decoy = ('authenticated_reuse duplicate_head "
            "duplicate_effective_diff input_budget_exhausted "
            "round_budget_exhausted total_usage_budget_exhausted')\n"
        )
        source = source.replace(signature, decoy, 1)
        source = source.replace(
            'return refuse(validated, request, "duplicate_head")',
            'return refuse(validated, request, "decision-swap")',
            1,
        )
        source = source.replace(
            'return refuse(validated, request, "duplicate_effective_diff")',
            'return refuse(validated, request, "duplicate_head")',
            1,
        )
        source = source.replace(
            'return refuse(validated, request, "decision-swap")',
            'return refuse(validated, request, "duplicate_effective_diff")',
            1,
        )
    elif mutation == "reviewer-call-caps":
        source = source.replace(
            '{"claude": 1, "gemini": 3, "opencode": 2}[reviewer]',
            '{"claude": 1, "gemini": 4, "opencode": 2}[reviewer]',
            1,
        )
    else:
        source = source.replace(
            're.compile(r"RVW-[0-9a-f]{12}\\Z")',
            're.compile(r"RVW-[0-9a-f]{13}\\Z")',
            1,
        )
    monkeypatch.setattr(
        release_verifier,
        "EXPECTED_REVIEW_INVOCATION_BUDGET_HELPER_SHA256",
        hashlib.sha256(source.encode("utf-8")).hexdigest(),
    )

    with pytest.raises(
        ReleaseVerificationError, match="invocation-budget helper contract"
    ):
        release_verifier.require_budget_helper_contract(source)


@pytest.mark.parametrize(
    "mutation",
    (
        "schema-annotation",
        "record-shape",
        "claim-gate-dead-body",
        "final-cap-dead-decoy",
        "rvw-bound-dead-decoy",
        "provenance-dead-decoy",
        "caller-event-dead-decoy",
        "referenced-workflow-cardinality-dead-decoy",
        "empty-input-guard-dead-decoy",
    ),
)
def test_v147_budget_helper_semantics_bind_live_ast_relationships(
    mutation: str,
) -> None:
    source = (
        ROOT
        / ".github/actions/review-invocation-budget/review_invocation_budget.py"
    ).read_text(encoding="utf-8")

    def substitute(old: str, new: str, *, count: int = 1) -> None:
        nonlocal source
        assert source.count(old) == count
        source = source.replace(old, new, count)

    if mutation == "schema-annotation":
        substitute(
            "    call_count: int\n    estimated_input_tokens: int\n",
            "    call_count: str\n    estimated_input_tokens: int\n",
        )
    elif mutation == "record-shape":
        substitute(
            "@dataclass(frozen=True)\nclass LedgerState:\n",
            "@dataclass\nclass LedgerState:\n",
        )
    elif mutation == "claim-gate-dead-body":
        substitute(
            "    if not request.force_review and any(\n"
            "        item.head_sha == request.head_sha "
            "for item in validated.invocations\n"
            "    ):\n"
            "        if (\n"
            "            request.authenticated_review.head_sha == request.head_sha\n"
            "            and request.authenticated_review.covers_hash("
            "request.full_diff_sha256)\n"
            "        ):\n"
            "            return refuse(validated, request, \"authenticated_reuse\")\n"
            "        return refuse(validated, request, \"duplicate_head\")\n",
            "    if False:\n"
            "        return refuse(validated, request, \"duplicate_head\")\n",
        )
    elif mutation == "final-cap-dead-decoy":
        substitute(
            "    if request.call_count > state.budgets.max_calls_per_round:\n"
            "        outcome, stop_reason = \"checkpoint_failure\", "
            "\"call_budget_exhausted\"\n",
            "    if False:\n"
            "        if request.call_count > state.budgets.max_calls_per_round:\n"
            "            outcome, stop_reason = \"checkpoint_failure\", "
            "\"call_budget_exhausted\"\n"
            "    if request.call_count > state.budgets.max_calls_per_round + 1:\n"
            "        outcome, stop_reason = \"provider_failure\", "
            "\"provider_failure\"\n",
        )
    elif mutation == "rvw-bound-dead-decoy":
        count = source.count("len(findings) > 8")
        assert count > 1
        source = source.replace("len(findings) > 8", "len(findings) > 80")
        source += (
            "\n\ndef _dead_rvw_bound_decoy(findings):\n"
            "    if False:\n"
            "        return len(findings) > 8\n"
        )
    elif mutation == "provenance-dead-decoy":
        substitute(
            '(not current and provenance.status != "completed")',
            "(not current and False)",
        )
        source += (
            "\n\ndef _dead_provenance_decoy(current, provenance):\n"
            "    if False:\n"
            '        return not current and provenance.status != "completed"\n'
        )
    elif mutation == "caller-event-dead-decoy":
        substitute(
            'provenance.caller_event not in {"pull_request", "workflow_dispatch"}',
            'provenance.caller_event not in {"pull_request"}',
        )
        source += (
            "\n\ndef _dead_caller_event_decoy(provenance):\n"
            "    if False:\n"
            '        return provenance.caller_event not in '
            '{"pull_request", "workflow_dispatch"}\n'
        )
    elif mutation == "referenced-workflow-cardinality-dead-decoy":
        substitute("        if len(central) != 1:\n", "        if not central:\n")
        source += (
            "\n\ndef _dead_referenced_cardinality_decoy(central):\n"
            "    if False:\n"
            "        return len(central) != 1\n"
        )
    else:
        substitute(
            'request.get("diff_mode") in {"full", "delta"} and not value',
            'request.get("diff_mode") in {"full", "delta"} and False',
        )
        source += (
            "\n\ndef _dead_empty_input_decoy(request, value):\n"
            "    if False:\n"
            "        return request.get('operation') == 'claim' and "
            "request.get('diff_mode') in {'full', 'delta'} and not value\n"
        )

    with pytest.raises(
        ReleaseVerificationError, match="invocation-budget helper contract"
    ):
        release_verifier.require_budget_helper_contract(source)


@pytest.mark.parametrize(
    ("workflow", "job", "mutation"),
    (
        ("claude-code-review.yml", "claude-review", "claim-after-provider"),
        ("claude-code-review.yml", "claude-review", "remove-allow"),
        ("claude-code-review.yml", "claude-review", "raise-timeout"),
        ("gemini-auto-review.yml", "gemini-review", "omit-finalize"),
        ("gemini-auto-review.yml", "gemini-review", "omit-artifact"),
        ("opencode-auto-review.yml", "opencode-prepare", "break-handoff-hash"),
        ("opencode-auto-review.yml", "opencode-review", "raise-call-cap"),
        ("claude-code-review.yml", "claude-review", "cross-reviewer-fallback"),
    ),
)
def test_v147_rejects_budget_workflow_safety_gate_mutations(
    current_release_repo: tuple[Path, str],
    workflow: str,
    job: str,
    mutation: str,
) -> None:
    repo, _ = current_release_repo
    prepare_v147(repo)
    path = repo / ".github/workflows" / workflow

    def weaken(document: dict) -> None:
        target_job = document["jobs"][job]
        steps = target_job["steps"]
        if mutation == "claim-after-provider":
            claim_step = next(
                item for item in steps if item.get("id") == "review-budget-claim"
            )
            provider = next(
                item for item in steps if item.get("name") == "Run Claude Code Review"
            )
            steps.remove(claim_step)
            steps.insert(steps.index(provider) + 1, claim_step)
        elif mutation == "remove-allow":
            provider = next(
                item for item in steps if item.get("name") == "Run Claude Code Review"
            )
            provider["if"] = "${{ steps.prepare-diff.outputs.diff-ready == 'true' }}"
        elif mutation == "raise-timeout":
            target_job["timeout-minutes"] = "21"
        elif mutation == "omit-finalize":
            steps.remove(
                next(item for item in steps if item.get("name") == "Finalize Gemini review budget")
            )
        elif mutation == "omit-artifact":
            steps.remove(
                next(
                    item
                    for item in steps
                    if item.get("name") == "Upload Gemini review budget claim checkpoint"
                )
            )
        elif mutation == "break-handoff-hash":
            build = next(
                item
                for item in steps
                if item.get("name") == "Build sealed canonicalization handoff"
            )
            build["run"] = build["run"].replace(
                "budget_checkpoint_sha256", "unsealed_budget_checkpoint_sha256", 1
            )
        elif mutation == "raise-call-cap":
            provider = next(
                item for item in steps if item.get("name") == "Run OpenCode PR review"
            )
            provider["run"] = provider["run"].replace(
                '(( count < 2 )) || {', '(( count < 3 )) || {', 1
            )
        else:
            steps.append(
                {
                    "name": "Fallback to Gemini reviewer",
                    "if": "${{ failure() }}",
                    "run": "echo dispatch gemini-auto-review",
                }
            )

    mutate_yaml(path, weaken)
    bad_commit = commit(repo, f"weaken {workflow} budget contract: {mutation}")

    with pytest.raises(ReleaseVerificationError):
        release_verifier.verify_commit_content(repo, "v1.47", bad_commit)


@pytest.mark.parametrize(
    ("reviewer", "workflow", "mutation"),
    (
        ("claude", "claude-code-review.yml", "provider-predicate"),
        ("gemini", "gemini-auto-review.yml", "provider-predicate"),
        ("opencode", "opencode-auto-review.yml", "provider-predicate"),
        ("claude", "claude-code-review.yml", "staging-claim-guard"),
        ("gemini", "gemini-auto-review.yml", "staging-claim-guard"),
        ("claude", "claude-code-review.yml", "metrics-finalize-guard"),
        ("gemini", "gemini-auto-review.yml", "metrics-finalize-guard"),
        ("claude", "claude-code-review.yml", "metrics-publication"),
        ("gemini", "gemini-auto-review.yml", "metrics-publication"),
        ("claude", "claude-code-review.yml", "publication-order"),
        ("gemini", "gemini-auto-review.yml", "publication-order"),
        ("opencode", "opencode-auto-review.yml", "publication-order"),
        ("opencode", "opencode-auto-review.yml", "cross-job-claim-order"),
        ("claude", "claude-code-review.yml", "claim-artifact-path"),
        ("gemini", "gemini-auto-review.yml", "claim-artifact-path"),
        ("opencode", "opencode-auto-review.yml", "claim-artifact-path"),
        ("claude", "claude-code-review.yml", "call-cap"),
        ("gemini", "gemini-auto-review.yml", "call-cap"),
        ("opencode", "opencode-auto-review.yml", "call-cap"),
    ),
)
def test_v147_budget_workflow_semantics_reject_authenticated_mutations(
    current_release_repo: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    reviewer: str,
    workflow: str,
    mutation: str,
) -> None:
    repo, _ = current_release_repo
    path = repo / ".github/workflows" / workflow
    provider_name = {
        "claude": "Run Claude Code Review",
        "gemini": "Run Gemini Code Review",
        "opencode": "Run OpenCode PR review",
    }[reviewer]
    if mutation == "provider-predicate":
        provider_if = {
            "claude": (
                "${{ steps.prepare-diff.outputs.diff-ready == 'true' && "
                "steps.prepare-diff.outputs.diff-mode != 'unchanged' && "
                "steps.review-budget-claim.outputs.allow-invocation == 'true' }}"
            ),
            "gemini": (
                "${{ steps.reset-gemini-artifacts.outcome == 'success' && "
                "steps.prepare-diff.outputs.diff-ready == 'true' && "
                "steps.prepare-diff.outputs.diff-mode != 'unchanged' && "
                "steps.review-budget-claim.outputs.allow-invocation == 'true' }}"
            ),
            "opencode": (
                "needs.opencode-prepare.outputs.allow_invocation == 'true' && "
                "needs.opencode-prepare.outputs.diff_ready == 'true' && "
                "needs.opencode-prepare.outputs.diff_mode != 'unchanged'"
            ),
        }[reviewer]
        mutate_named_step_text(
            path, provider_name, provider_if, provider_if + " || always()"
        )
    elif mutation == "staging-claim-guard":
        stage_id = f"stage-{reviewer}-budget-input"
        mutate_named_step_text(
            path,
            f"Claim {reviewer.title()} review budget",
            f"steps.{stage_id}.outcome == 'success'",
            "always()",
        )
    elif mutation == "metrics-finalize-guard":
        mutate_named_step_text(
            path,
            f"Finalize {reviewer.title()} review budget",
            f"steps.{reviewer}-budget-metrics.outputs.metrics_valid == 'true'",
            "always()",
        )
    elif mutation == "metrics-publication" and reviewer == "claude":
        mutate_named_step_text(
            path,
            "Validate Claude review metrics",
            "printf 'metrics_valid=false\\n'",
            "printf 'metrics_valid=true\\n'",
        )
    elif mutation == "metrics-publication":
        mutate_named_step_text(
            path,
            "Read Gemini review metrics",
            "if not valid:\n              raise SystemExit(0)",
            "if False:\n              raise SystemExit(0)",
        )
    elif mutation == "publication-order":
        move_named_step(
            path,
            {
                "claude": "Finalize Claude review budget",
                "gemini": "Finalize Gemini review budget",
                "opencode": "Finalize OpenCode review budget",
            }[reviewer],
            {
                "claude": "Upsert review comment",
                "gemini": "Upsert review comment",
                "opencode": "Canonicalize OpenCode review",
            }[reviewer],
            after=False,
        )
    elif mutation == "cross-job-claim-order":
        move_named_step(
            path,
            "Claim OpenCode review budget",
            "Build sealed canonicalization handoff",
            after=True,
        )
    elif mutation == "claim-artifact-path":
        step_name = {
            "claude": "Upload Claude review budget claim checkpoint",
            "gemini": "Upload Gemini review budget claim checkpoint",
            "opencode": "Upload OpenCode review budget claim checkpoint",
        }[reviewer]
        original = f"{reviewer}-review-budget-claim.json"
        mutate_named_step_text(path, step_name, original, original + ".untrusted")
    elif reviewer == "claude":
        mutate_named_step_text(
            path, "Start Claude review metrics", "call_count=1", "call_count=2"
        )
    elif reviewer == "gemini":
        mutate_named_step_text(
            path, provider_name, "if count >= 3:", "if count >= 4:"
        )
    else:
        mutate_named_step_text(
            path, provider_name, "(( count < 2 )) || {", "(( count < 3 )) || {"
        )
    bad_commit = commit(repo, f"authenticated semantic mutation: {mutation}")
    payload = path.read_bytes()
    monkeypatch.setitem(
        release_verifier.EXPECTED_REVIEW_INVOCATION_BUDGET_WORKFLOW_SHA256,
        reviewer,
        hashlib.sha256(payload).hexdigest(),
    )
    tree = release_verifier.VerifiedCommitTree.open(repo, bad_commit)

    with pytest.raises(
        ReleaseVerificationError, match="invocation-budget workflow contract"
    ):
        release_verifier.require_budget_workflow_contract(tree, workflow, reviewer)


@pytest.mark.parametrize(
    ("reviewer", "workflow"),
    (
        ("claude", "claude-code-review.yml"),
        ("gemini", "gemini-auto-review.yml"),
        ("opencode", "opencode-auto-review.yml"),
    ),
)
def test_v147_budget_workflow_semantics_bind_live_reviewer_call_caps(
    current_release_repo: tuple[Path, str],
    reviewer: str,
    workflow: str,
) -> None:
    repo, _ = current_release_repo
    path = repo / ".github/workflows" / workflow
    if reviewer == "claude":
        mutate_named_step_text(
            path,
            "Start Claude review metrics",
            "printf 'call_count=1\\n' >> \"$GITHUB_OUTPUT\"",
            "printf 'call_count=2\\n' >> \"$GITHUB_OUTPUT\"\n"
            "          : 'call_count=1'",
        )
    elif reviewer == "gemini":
        mutate_named_step_text(
            path,
            "Run Gemini Code Review",
            "if count >= 3:\n"
            "                  raise ProviderFailure('call_budget_exhausted')",
            "if count >= 4:\n"
            "                  raise ProviderFailure('call_budget_exhausted')\n"
            "              if False:\n"
            "                  _call_cap_decoy = 'if count >= 3:'",
        )
    else:
        mutate_named_step_text(
            path,
            "Run OpenCode PR review",
            "(( count < 2 )) || {\n"
            "              review_failure_reason=call_budget_exhausted\n"
            "              return 1\n"
            "            }",
            "(( count < 3 )) || {\n"
            "              review_failure_reason=call_budget_exhausted\n"
            "              return 1\n"
            "            }\n"
            "            : '(( count < 2 )) || {'",
        )
    bad_commit = commit(repo, f"weaken live {reviewer} reviewer call cap")
    tree = release_verifier.VerifiedCommitTree.open(repo, bad_commit)

    with pytest.raises(
        ReleaseVerificationError, match="invocation-budget workflow contract"
    ):
        release_verifier.require_budget_workflow_contract(tree, workflow, reviewer)


def test_v147_opencode_call_cap_rejects_complete_unreachable_sequence_decoy(
    current_release_repo: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _ = current_release_repo
    path = repo / ".github/workflows/opencode-auto-review.yml"
    mutate_named_step_text(
        path,
        "Run OpenCode PR review",
        'count="$(cat "$call_count_file")"\n'
        '            [[ "$count" =~ ^[0-9]+$ ]]\n'
        "            (( count < 2 )) || {\n"
        "              review_failure_reason=call_budget_exhausted\n"
        "              return 1\n"
        "            }\n"
        '            python3 - "$call_count_file" "$((count + 1))" <<\'PY\'',
        'count="$(printf 0)"\n'
        '            [[ "$count" =~ ^[0-9]+$ ]]\n'
        "            if false; then\n"
        '              count="$(cat "$call_count_file")"\n'
        '              [[ "$count" =~ ^[0-9]+$ ]]\n'
        "              (( count < 2 )) || {\n"
        "                review_failure_reason=call_budget_exhausted\n"
        "                return 1\n"
        "              }\n"
        '              python3 - "$call_count_file" "$((count + 1))" <<\'PY\'',
    )
    mutate_named_step_text(
        path,
        "Run OpenCode PR review",
        "          PY\n            env -i \\",
        "          PY\n            fi\n            env -i \\",
    )
    bad_commit = commit(repo, "hide OpenCode budget sequence below if false")
    payload = path.read_bytes()
    monkeypatch.setitem(
        release_verifier.EXPECTED_REVIEW_INVOCATION_BUDGET_WORKFLOW_SHA256,
        "opencode",
        hashlib.sha256(payload).hexdigest(),
    )
    tree = release_verifier.VerifiedCommitTree.open(repo, bad_commit)
    assert hashlib.sha256(
        tree.read_file(".github/workflows/opencode-auto-review.yml")
    ).hexdigest() == (
        release_verifier.EXPECTED_REVIEW_INVOCATION_BUDGET_WORKFLOW_SHA256[
            "opencode"
        ]
    )

    with pytest.raises(
        ReleaseVerificationError, match="invocation-budget workflow contract"
    ):
        release_verifier.require_budget_workflow_contract(
            tree, "opencode-auto-review.yml", "opencode"
        )


def test_v147_opencode_call_cap_rejects_dead_canonical_function_decoy(
    current_release_repo: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _ = current_release_repo
    path = repo / ".github/workflows/opencode-auto-review.yml"
    source = path.read_text(encoding="utf-8")
    function_start = source.index("          run_opencode() {\n")
    function_end = source.index(
        "\n\n          extract_candidate() {", function_start
    )
    canonical_function = source[function_start:function_end]
    assert canonical_function.count("          run_opencode() {\n") == 1
    assert canonical_function.count("(( count < 2 )) || {") == 1
    weakened_live_function = canonical_function.replace(
        "          run_opencode() {\n",
        "          function run_opencode {\n",
        1,
    ).replace("(( count < 2 )) || {", "(( count < 3 )) || {", 1)
    dead_canonical_function = (
        "          if false; then\n"
        f"{canonical_function}\n"
        "          fi"
    )
    mutate_named_step_text(
        path,
        "Run OpenCode PR review",
        canonical_function,
        f"{dead_canonical_function}\n\n{weakened_live_function}",
    )
    bad_commit = commit(
        repo, "hide canonical OpenCode function below top-level if false"
    )
    payload = path.read_bytes()
    monkeypatch.setitem(
        release_verifier.EXPECTED_REVIEW_INVOCATION_BUDGET_WORKFLOW_SHA256,
        "opencode",
        hashlib.sha256(payload).hexdigest(),
    )
    tree = release_verifier.VerifiedCommitTree.open(repo, bad_commit)
    assert hashlib.sha256(
        tree.read_file(".github/workflows/opencode-auto-review.yml")
    ).hexdigest() == (
        release_verifier.EXPECTED_REVIEW_INVOCATION_BUDGET_WORKFLOW_SHA256[
            "opencode"
        ]
    )

    with pytest.raises(
        ReleaseVerificationError, match="invocation-budget workflow contract"
    ):
        release_verifier.require_budget_workflow_contract(
            tree, "opencode-auto-review.yml", "opencode"
        )


@pytest.mark.parametrize(
    "compact_redefinition",
    (
        (
            "function run_opencode { local prompt_path=\"$1\"; "
            "local output_path=\"$2\"; shift 2; "
            "opencode run --model zai-coding-plan/glm-4.7 \"$@\" "
            "< \"$prompt_path\" > \"$output_path\"; }"
        ),
        (
            "run_opencode(){ local prompt_path=\"$1\"; "
            "local output_path=\"$2\"; shift 2; "
            "opencode run --model zai-coding-plan/glm-4.7 \"$@\" "
            "< \"$prompt_path\" > \"$output_path\"; }"
        ),
    ),
    ids=("bash-function-form", "posix-name-form"),
)
def test_v147_opencode_call_cap_rejects_compact_function_redefinition(
    current_release_repo: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    compact_redefinition: str,
) -> None:
    repo, _ = current_release_repo
    path = repo / ".github/workflows/opencode-auto-review.yml"
    syntax = subprocess.run(
        ["bash", "--noprofile", "--norc", "-n", "-c", compact_redefinition],
        check=False,
        capture_output=True,
        text=True,
    )
    assert syntax.returncode == 0, syntax.stderr

    source = path.read_text(encoding="utf-8")
    function_start = source.index("          run_opencode() {\n")
    function_end = source.index(
        "\n\n          extract_candidate() {", function_start
    )
    canonical_function = source[function_start:function_end]
    mutate_named_step_text(
        path,
        "Run OpenCode PR review",
        canonical_function,
        f"{canonical_function}\n\n          {compact_redefinition}",
    )
    bad_commit = commit(repo, "add compact unbounded OpenCode redefinition")
    payload = path.read_bytes()
    monkeypatch.setitem(
        release_verifier.EXPECTED_REVIEW_INVOCATION_BUDGET_WORKFLOW_SHA256,
        "opencode",
        hashlib.sha256(payload).hexdigest(),
    )
    tree = release_verifier.VerifiedCommitTree.open(repo, bad_commit)
    assert hashlib.sha256(
        tree.read_file(".github/workflows/opencode-auto-review.yml")
    ).hexdigest() == (
        release_verifier.EXPECTED_REVIEW_INVOCATION_BUDGET_WORKFLOW_SHA256[
            "opencode"
        ]
    )

    with pytest.raises(
        ReleaseVerificationError, match="invocation-budget workflow contract"
    ):
        release_verifier.require_budget_workflow_contract(
            tree, "opencode-auto-review.yml", "opencode"
        )


def test_v147_opencode_call_cap_rejects_redefinition_after_quoted_heredoc_data(
    current_release_repo: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _ = current_release_repo
    path = repo / ".github/workflows/opencode-auto-review.yml"
    bypass_script = (
        "printf '%s\\n' \"<<'true'\"\n"
        "run_opencode(){ opencode run --model "
        "zai-coding-plan/glm-4.7 \"$@\"; }\n"
        "true"
    )
    syntax = subprocess.run(
        ["bash", "--noprofile", "--norc", "-n", "-c", bypass_script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert syntax.returncode == 0, syntax.stderr

    source = path.read_text(encoding="utf-8")
    function_start = source.index("          run_opencode() {\n")
    function_end = source.index(
        "\n\n          extract_candidate() {", function_start
    )
    canonical_function = source[function_start:function_end]
    indented_bypass = "          " + bypass_script.replace(
        "\n", "\n          "
    )
    mutate_named_step_text(
        path,
        "Run OpenCode PR review",
        canonical_function,
        f"{canonical_function}\n\n{indented_bypass}",
    )
    bad_commit = commit(repo, "hide OpenCode redefinition after quoted heredoc data")
    payload = path.read_bytes()
    monkeypatch.setitem(
        release_verifier.EXPECTED_REVIEW_INVOCATION_BUDGET_WORKFLOW_SHA256,
        "opencode",
        hashlib.sha256(payload).hexdigest(),
    )
    tree = release_verifier.VerifiedCommitTree.open(repo, bad_commit)
    assert hashlib.sha256(
        tree.read_file(".github/workflows/opencode-auto-review.yml")
    ).hexdigest() == (
        release_verifier.EXPECTED_REVIEW_INVOCATION_BUDGET_WORKFLOW_SHA256[
            "opencode"
        ]
    )

    with pytest.raises(
        ReleaseVerificationError, match="invocation-budget workflow contract"
    ):
        release_verifier.require_budget_workflow_contract(
            tree, "opencode-auto-review.yml", "opencode"
        )


def test_v147_opencode_call_cap_rejects_computed_executor_redefinition(
    current_release_repo: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _ = current_release_repo
    path = repo / ".github/workflows/opencode-auto-review.yml"
    bypass_script = (
        "e=e; e+=val; n=run_open; n+=code; "
        "\"$e\" \"${n}(){ opencode run --model "
        "zai-coding-plan/glm-4.7 \\\"\\$@\\\"; }\""
    )
    syntax = subprocess.run(
        ["bash", "--noprofile", "--norc", "-n", "-c", bypass_script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert syntax.returncode == 0, syntax.stderr

    source = path.read_text(encoding="utf-8")
    function_start = source.index("          run_opencode() {\n")
    function_end = source.index(
        "\n\n          extract_candidate() {", function_start
    )
    canonical_function = source[function_start:function_end]
    mutate_named_step_text(
        path,
        "Run OpenCode PR review",
        canonical_function,
        f"{canonical_function}\n\n          {bypass_script}",
    )
    bad_commit = commit(repo, "redefine OpenCode through computed executor")
    payload = path.read_bytes()
    monkeypatch.setitem(
        release_verifier.EXPECTED_REVIEW_INVOCATION_BUDGET_WORKFLOW_SHA256,
        "opencode",
        hashlib.sha256(payload).hexdigest(),
    )
    tree = release_verifier.VerifiedCommitTree.open(repo, bad_commit)
    assert hashlib.sha256(
        tree.read_file(".github/workflows/opencode-auto-review.yml")
    ).hexdigest() == (
        release_verifier.EXPECTED_REVIEW_INVOCATION_BUDGET_WORKFLOW_SHA256[
            "opencode"
        ]
    )

    with pytest.raises(
        ReleaseVerificationError, match="invocation-budget workflow contract"
    ):
        release_verifier.require_budget_workflow_contract(
            tree, "opencode-auto-review.yml", "opencode"
        )


def test_v147_opencode_call_cap_rejects_alias_executor_redefinition(
    current_release_repo: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _ = current_release_repo
    path = repo / ".github/workflows/opencode-auto-review.yml"
    bypass_script = (
        "shopt -s expand_aliases\n"
        "alias e=eval\n"
        "x=run_open\n"
        "y=code\n"
        "e \"$x$y(){ opencode run --model "
        "zai-coding-plan/glm-4.7 \\\"\\$@\\\"; }\""
    )
    syntax = subprocess.run(
        ["bash", "--noprofile", "--norc", "-n", "-c", bypass_script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert syntax.returncode == 0, syntax.stderr
    execution = subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            f"{bypass_script}\ndeclare -f run_opencode",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert execution.returncode == 0, execution.stderr
    assert "run_opencode" in execution.stdout
    assert (
        "opencode run --model zai-coding-plan/glm-4.7"
        in execution.stdout
    )

    source = path.read_text(encoding="utf-8")
    function_start = source.index("          run_opencode() {\n")
    function_end = source.index(
        "\n\n          extract_candidate() {", function_start
    )
    canonical_function = source[function_start:function_end]
    indented_bypass = "          " + bypass_script.replace(
        "\n", "\n          "
    )
    mutate_named_step_text(
        path,
        "Run OpenCode PR review",
        canonical_function,
        f"{canonical_function}\n\n{indented_bypass}",
    )
    bad_commit = commit(repo, "redefine OpenCode through alias executor")
    payload = path.read_bytes()
    monkeypatch.setitem(
        release_verifier.EXPECTED_REVIEW_INVOCATION_BUDGET_WORKFLOW_SHA256,
        "opencode",
        hashlib.sha256(payload).hexdigest(),
    )
    tree = release_verifier.VerifiedCommitTree.open(repo, bad_commit)
    assert hashlib.sha256(
        tree.read_file(".github/workflows/opencode-auto-review.yml")
    ).hexdigest() == (
        release_verifier.EXPECTED_REVIEW_INVOCATION_BUDGET_WORKFLOW_SHA256[
            "opencode"
        ]
    )

    with pytest.raises(
        ReleaseVerificationError, match="invocation-budget workflow contract"
    ):
        release_verifier.require_budget_workflow_contract(
            tree, "opencode-auto-review.yml", "opencode"
        )


@pytest.mark.parametrize(
    "target_affecting_statement",
    (
        (
            "if true; then function run_opencode { "
            "opencode run --model zai-coding-plan/glm-4.7 \"$@\"; }; fi"
        ),
        (
            "eval 'run_opencode(){ opencode run --model "
            "zai-coding-plan/glm-4.7 \"$@\"; }'"
        ),
        (
            "command builtin eval 'run_opencode(){ opencode run --model "
            "zai-coding-plan/glm-4.7 \"$@\"; }'"
        ),
        (
            "target_name=run_open; target_name+=code; "
            "LC_ALL=C eval \"${target_name}(){ opencode run --model "
            "zai-coding-plan/glm-4.7 \\\"\\$@\\\"; }\""
        ),
        (
            "if true; then run_opencode \"$initial_prompt\" "
            "\"$RUNNER_TEMP/unmetered-opencode.jsonl\"; fi"
        ),
        "command alias e=eval",
        "builtin unalias -a",
        "shopt -u expand_aliases",
        "BASH_ALIASES[e]=eval",
    ),
    ids=(
        "conditional-definition",
        "dynamic-redefinition",
        "wrapped-dynamic-redefinition",
        "assignment-prefixed-dynamic-redefinition",
        "hidden-call",
        "wrapped-alias-declaration",
        "wrapped-alias-removal",
        "alias-expansion-option",
        "alias-array-declaration",
    ),
)
def test_v147_opencode_call_cap_rejects_unparsed_target_affecting_syntax(
    current_release_repo: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    target_affecting_statement: str,
) -> None:
    repo, _ = current_release_repo
    path = repo / ".github/workflows/opencode-auto-review.yml"
    syntax = subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-n",
            "-c",
            target_affecting_statement,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert syntax.returncode == 0, syntax.stderr

    source = path.read_text(encoding="utf-8")
    function_start = source.index("          run_opencode() {\n")
    function_end = source.index(
        "\n\n          extract_candidate() {", function_start
    )
    canonical_function = source[function_start:function_end]
    mutate_named_step_text(
        path,
        "Run OpenCode PR review",
        canonical_function,
        f"{canonical_function}\n\n          {target_affecting_statement}",
    )
    bad_commit = commit(repo, "add unparsed OpenCode target-affecting syntax")
    payload = path.read_bytes()
    monkeypatch.setitem(
        release_verifier.EXPECTED_REVIEW_INVOCATION_BUDGET_WORKFLOW_SHA256,
        "opencode",
        hashlib.sha256(payload).hexdigest(),
    )
    tree = release_verifier.VerifiedCommitTree.open(repo, bad_commit)
    assert hashlib.sha256(
        tree.read_file(".github/workflows/opencode-auto-review.yml")
    ).hexdigest() == (
        release_verifier.EXPECTED_REVIEW_INVOCATION_BUDGET_WORKFLOW_SHA256[
            "opencode"
        ]
    )

    with pytest.raises(
        ReleaseVerificationError, match="invocation-budget workflow contract"
    ):
        release_verifier.require_budget_workflow_contract(
            tree, "opencode-auto-review.yml", "opencode"
        )


@pytest.mark.parametrize(
    "mutation",
    (
        "conditional-definition",
        "alternate-redefinition",
        "spaced-posix-redefinition",
        "multiline-alternate-redefinition",
        "predefinition-invocation",
    ),
)
def test_v147_opencode_call_cap_rejects_ambiguous_function_binding(
    current_release_repo: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    repo, _ = current_release_repo
    path = repo / ".github/workflows/opencode-auto-review.yml"
    source = path.read_text(encoding="utf-8")
    function_start = source.index("          run_opencode() {\n")
    function_end = source.index(
        "\n\n          extract_candidate() {", function_start
    )
    canonical_function = source[function_start:function_end]
    if mutation == "conditional-definition":
        replacement = (
            "          if true; then\n"
            f"{canonical_function}\n"
            "          fi"
        )
    elif mutation in {
        "alternate-redefinition",
        "spaced-posix-redefinition",
        "multiline-alternate-redefinition",
    }:
        alternate_declaration = {
            "alternate-redefinition": "          function run_opencode {\n",
            "spaced-posix-redefinition": "          run_opencode () {\n",
            "multiline-alternate-redefinition": (
                "          function run_opencode\n          {\n"
            ),
        }[mutation]
        weakened_alternate = canonical_function.replace(
            "          run_opencode() {\n",
            alternate_declaration,
            1,
        ).replace("(( count < 2 )) || {", "(( count < 3 )) || {", 1)
        replacement = f"{canonical_function}\n\n{weakened_alternate}"
    else:
        replacement = (
            "          run_opencode \"$initial_prompt\" "
            "\"$RUNNER_TEMP/opencode-review.jsonl\"\n\n"
            f"{canonical_function}"
        )
    mutate_named_step_text(
        path,
        "Run OpenCode PR review",
        canonical_function,
        replacement,
    )
    bad_commit = commit(repo, f"make OpenCode function binding {mutation}")
    payload = path.read_bytes()
    monkeypatch.setitem(
        release_verifier.EXPECTED_REVIEW_INVOCATION_BUDGET_WORKFLOW_SHA256,
        "opencode",
        hashlib.sha256(payload).hexdigest(),
    )
    tree = release_verifier.VerifiedCommitTree.open(repo, bad_commit)
    assert hashlib.sha256(
        tree.read_file(".github/workflows/opencode-auto-review.yml")
    ).hexdigest() == (
        release_verifier.EXPECTED_REVIEW_INVOCATION_BUDGET_WORKFLOW_SHA256[
            "opencode"
        ]
    )

    with pytest.raises(
        ReleaseVerificationError, match="invocation-budget workflow contract"
    ):
        release_verifier.require_budget_workflow_contract(
            tree, "opencode-auto-review.yml", "opencode"
        )


@pytest.mark.parametrize(
    ("filename", "size", "oid"),
    (
        (
            "claude-code-review.yml",
            33_206,
            "4361b51d34dbc9be85652d73f595ebd8f9775e23",
        ),
        (
            "gemini-auto-review.yml",
            49_844,
            "4b3d341c0c97714140a3787d83e89eb035408792",
        ),
    ),
)
def test_v145_review_fixtures_match_the_immutable_release_blobs(
    filename: str, size: int, oid: str
) -> None:
    path = V145_REVIEW_FIXTURE_ROOT / filename
    payload = path.read_bytes()

    assert len(payload) == size
    assert hashlib.sha1(
        f"blob {len(payload)}\0".encode("ascii") + payload
    ).hexdigest() == oid
    assert path.stat().st_mode & 0o777 == 0o644


def test_v145_review_workflows_restore_without_invoking_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "historical-v145-fixture"
    workflow_root = repo / ".github/workflows"
    workflow_root.mkdir(parents=True)

    def reject_git(*args, **kwargs):
        raise AssertionError("v1.45 fixture restoration invoked subprocess")

    monkeypatch.setattr(subprocess, "run", reject_git)
    restore_v145_review_workflows(repo)

    for filename in ("claude-code-review.yml", "gemini-auto-review.yml"):
        assert (workflow_root / filename).read_bytes() == (
            V145_REVIEW_FIXTURE_ROOT / filename
        ).read_bytes()


def test_v145_inventory_does_not_require_future_canonicalizer_files(
    current_release_repo: tuple[Path, str],
) -> None:
    repo, _ = current_release_repo
    restore_v145_review_workflows(repo)
    for relative in (
        ".github/actions/canonicalize-review/action.yml",
        ".github/actions/canonicalize-review/canonicalize_review.py",
        ".github/actions/canonicalize-review/review_scope.py",
    ):
        (repo / relative).unlink(missing_ok=True)
    historical = commit(repo, "v1.45 historical inventory")

    tree = release_verifier.VerifiedCommitTree.open(repo, historical)
    assert (
        release_verifier._release_inventory(tree, "v1.45")
        is None
    )


@pytest.mark.parametrize("relative", CANONICALIZER_RELEASE_FILES)
@pytest.mark.parametrize(
    "mutation",
    ("missing", "directory", "executable", "symlink"),
)
def test_v146_requires_each_canonicalizer_file_as_one_regular_0644_blob(
    v1462_release_repo: tuple[Path, str], relative: str, mutation: str
) -> None:
    repo, _ = v1462_release_repo
    target = repo / relative
    if mutation == "missing":
        target.unlink()
    elif mutation == "directory":
        target.unlink()
        target.mkdir()
        (target / "dummy").write_text("not the release file\n", encoding="utf-8")
    elif mutation == "executable":
        target.chmod(0o755)
    else:
        target.unlink()
        target.symlink_to("untrusted-target")
    bad_commit = commit(repo, f"mutate {relative} as {mutation}")

    with pytest.raises(ReleaseVerificationError, match="release inventory"):
        release_verifier.verify_commit_content(repo, "v1.46.2", bad_commit)


def test_v146_rejects_files_outside_the_closed_canonicalizer_inventory(
    v1462_release_repo: tuple[Path, str],
) -> None:
    repo, _ = v1462_release_repo
    extra = repo / ".github/actions/canonicalize-review/unowned_helper.py"
    extra.write_text("raise RuntimeError('not release owned')\n", encoding="utf-8")
    bad_commit = commit(repo, "add unowned canonicalizer helper")

    with pytest.raises(
        ReleaseVerificationError, match="canonicalize-review inventory"
    ):
        release_verifier.verify_commit_content(repo, "v1.46.2", bad_commit)


@pytest.mark.parametrize(
    "mutation",
    ("input", "output", "runner", "environment", "argv"),
)
def test_v146_rejects_canonicalizer_composite_action_contract_drift(
    v1462_release_repo: tuple[Path, str], mutation: str
) -> None:
    repo, _ = v1462_release_repo
    action = repo / ".github/actions/canonicalize-review/action.yml"

    def drift(document: dict) -> None:
        if mutation == "input":
            document["inputs"]["previous-review-file"]["default"] = "unsafe"
        elif mutation == "output":
            document["outputs"]["accepted-count"]["value"] = "0"
        elif mutation == "runner":
            document["runs"]["using"] = "docker"
        elif mutation == "environment":
            document["runs"]["steps"][0]["env"].pop("PREVIOUS_SHA")
        else:
            document["runs"]["steps"][0]["run"] = document["runs"]["steps"][0][
                "run"
            ].replace('"$PREVIOUS_REVIEW_FILE"', "$PREVIOUS_REVIEW_FILE")

    mutate_yaml(action, drift)
    bad_commit = commit(repo, f"drift canonicalizer action {mutation}")

    with pytest.raises(
        ReleaseVerificationError, match="canonicalize-review action contract"
    ):
        release_verifier.verify_commit_content(repo, "v1.46.2", bad_commit)


@pytest.mark.parametrize(
    "reason",
    (
        "candidate_missing",
        "invalid_utf8",
        "candidate_oversize",
        "ambiguous_document",
        "scope_invalid",
        "canonicalizer_error",
        "invalid_anchor",
        "invalid_trigger_evidence",
        "invalid_severity",
        "invalid_impact_class",
        "missing_material_impact",
        "unsupported_performance_basis",
        "non_actionable_category",
        "unknown_prior_id",
        "duplicate_prior_binding",
        "missing_fix_anchor",
    ),
)
def test_v146_rejects_any_missing_canonicalizer_reason_literal(
    v1462_release_repo: tuple[Path, str], reason: str
) -> None:
    repo, _ = v1462_release_repo
    helper = repo / ".github/actions/canonicalize-review/canonicalize_review.py"
    replace(helper, f'"{reason}"', '"not_a_contract_reason"', count=1)
    bad_commit = commit(repo, f"remove canonicalizer reason {reason}")

    with pytest.raises(
        ReleaseVerificationError, match="canonicalize-review helper contract"
    ):
        release_verifier.verify_commit_content(repo, "v1.46.2", bad_commit)


@pytest.mark.parametrize(
    ("relative", "old", "new"),
    (
        (
            ".github/actions/canonicalize-review/canonicalize_review.py",
            'SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM")',
            'SEVERITIES = ("CRITICAL", "HIGH")',
        ),
        (
            ".github/actions/canonicalize-review/canonicalize_review.py",
            '"data-integrity"',
            '"data-loss"',
        ),
        (
            ".github/actions/canonicalize-review/canonicalize_review.py",
            "MAX_CANDIDATE_BYTES = 60_000",
            "MAX_CANDIDATE_BYTES = 60_001",
        ),
        (
            ".github/actions/canonicalize-review/canonicalize_review.py",
            "MAX_CANONICAL_BYTES = 64_000",
            "MAX_CANONICAL_BYTES = 64_001",
        ),
        (
            ".github/actions/canonicalize-review/canonicalize_review.py",
            "MAX_PREVIOUS_CANONICAL_BYTES = 65_536",
            "MAX_PREVIOUS_CANONICAL_BYTES = 65_535",
        ),
        (
            ".github/actions/canonicalize-review/canonicalize_review.py",
            "MAX_CANDIDATE_BLOCKS = 512",
            "MAX_CANDIDATE_BLOCKS = 511",
        ),
        (
            ".github/actions/canonicalize-review/canonicalize_review.py",
            "MAX_SAFE_INTEGER = (1 << 53) - 1",
            "MAX_SAFE_INTEGER = (1 << 52) - 1",
        ),
        (
            ".github/actions/canonicalize-review/review_scope.py",
            '"GIT_CONFIG_NOSYSTEM": "1"',
            '"GIT_CONFIG_NOSYSTEM": "0"',
        ),
    ),
)
def test_v146_rejects_canonicalizer_constant_drift(
    v1462_release_repo: tuple[Path, str], relative: str, old: str, new: str
) -> None:
    repo, _ = v1462_release_repo
    replace(repo / relative, old, new, count=1)
    bad_commit = commit(repo, f"drift canonicalizer constant {old}")

    with pytest.raises(
        ReleaseVerificationError, match="canonicalize-review helper contract"
    ):
        release_verifier.verify_commit_content(repo, "v1.46.2", bad_commit)


def test_v146_rejects_review_quality_behavior_drift(
    v1462_release_repo: tuple[Path, str],
) -> None:
    repo, _ = v1462_release_repo
    helper = repo / ".github/actions/canonicalize-review/canonicalize_review.py"
    replace(
        helper,
        "if not evidence or any(item is None or not scope.validate_trigger(item) for item in evidence):",
        "if not evidence or any(item is None for item in evidence):",
        count=1,
    )
    bad_commit = commit(repo, "bypass review trigger evidence validation")

    with pytest.raises(
        ReleaseVerificationError, match="canonicalize-review helper contract"
    ):
        release_verifier.verify_commit_content(repo, "v1.46.2", bad_commit)


@pytest.mark.parametrize(
    ("relative", "rebind"),
    (
        (
            ".github/actions/canonicalize-review/canonicalize_review.py",
            "HARD_REASONS: object = frozenset({'bypass_reason'})",
        ),
        (
            ".github/actions/canonicalize-review/canonicalize_review.py",
            "MAX_CANDIDATE_BLOCKS += 1",
        ),
        (
            ".github/actions/canonicalize-review/canonicalize_review.py",
            "del MAX_CANDIDATE_BYTES",
        ),
        (
            ".github/actions/canonicalize-review/canonicalize_review.py",
            "import json as MAX_SAFE_INTEGER",
        ),
        (
            ".github/actions/canonicalize-review/canonicalize_review.py",
            "for MAX_PREVIOUS_CANONICAL_BYTES in [1]:\n    pass",
        ),
        (
            ".github/actions/canonicalize-review/canonicalize_review.py",
            "with open(__file__, encoding='utf-8') as SEVERITIES:\n    pass",
        ),
        (
            ".github/actions/canonicalize-review/canonicalize_review.py",
            "try:\n    raise ValueError\nexcept ValueError as IMPACT_CLASSES:\n    pass",
        ),
        (
            ".github/actions/canonicalize-review/canonicalize_review.py",
            "match object():\n    case SOFT_REASONS:\n        pass",
        ),
        (
            ".github/actions/canonicalize-review/canonicalize_review.py",
            "if True:\n    HARD_REASONS = frozenset({'nested_bypass'})",
        ),
        (
            ".github/actions/canonicalize-review/canonicalize_review.py",
            "_sentinel = lambda value=(HARD_REASONS := "
            "frozenset({'lambda_default'})): value",
        ),
        (
            ".github/actions/canonicalize-review/canonicalize_review.py",
            "canonicalize = lambda request: None",
        ),
        (
            ".github/actions/canonicalize-review/canonicalize_review.py",
            "CanonicalizationResult = object",
        ),
    ),
)
def test_v146_rejects_every_effective_protected_module_rebinding(
    v1462_release_repo: tuple[Path, str], relative: str, rebind: str
) -> None:
    repo, _ = v1462_release_repo
    helper = repo / relative
    helper.write_text(
        helper.read_text(encoding="utf-8") + "\n" + rebind + "\n",
        encoding="utf-8",
    )
    bad_commit = commit(repo, "rebind protected canonicalizer symbol")

    with pytest.raises(
        ReleaseVerificationError, match="canonicalize-review helper contract"
    ):
        release_verifier.verify_commit_content(repo, "v1.46.2", bad_commit)


@pytest.mark.parametrize(
    ("relative", "suffix"),
    (
        (
            ".github/actions/canonicalize-review/canonicalize_review.py",
            "\n",
        ),
        (
            ".github/actions/canonicalize-review/review_scope.py",
            "\n",
        ),
        (
            ".github/actions/canonicalize-review/canonicalize_review.py",
            "\n# harmless authenticated-source drift\n",
        ),
        (
            ".github/actions/canonicalize-review/review_scope.py",
            "\n# harmless authenticated-source drift\n",
        ),
        (
            ".github/actions/canonicalize-review/canonicalize_review.py",
            "\nglobals().update({})\n",
        ),
        (
            ".github/actions/canonicalize-review/review_scope.py",
            "\nglobals()[\"unprotected_probe\"] = None\n",
        ),
    ),
    ids=(
        "canonicalizer-whitespace",
        "scope-whitespace",
        "canonicalizer-comment-append",
        "scope-comment-append",
        "canonicalizer-globals-update",
        "scope-globals-subscript",
    ),
)
def test_v146_authenticates_exact_helper_source_bytes(
    v1462_release_repo: tuple[Path, str], relative: str, suffix: str
) -> None:
    repo, _ = v1462_release_repo
    helper = repo / relative
    helper.write_bytes(helper.read_bytes() + suffix.encode("utf-8"))
    bad_commit = commit(repo, f"drift authenticated helper bytes in {relative}")

    with pytest.raises(
        ReleaseVerificationError, match="canonicalize-review helper contract"
    ):
        release_verifier.verify_commit_content(repo, "v1.46.2", bad_commit)


@pytest.mark.parametrize(
    ("relative", "old", "new"),
    (
        (
            ".github/actions/canonicalize-review/canonicalize_review.py",
            "class CanonicalizationRequest:",
            "class BrokenCanonicalizationRequest:",
        ),
        (
            ".github/actions/canonicalize-review/canonicalize_review.py",
            "class CandidateReason:",
            "class BrokenCandidateReason:",
        ),
        (
            ".github/actions/canonicalize-review/canonicalize_review.py",
            "class CanonicalizationResult:",
            "class BrokenCanonicalizationResult:",
        ),
        (
            ".github/actions/canonicalize-review/canonicalize_review.py",
            "def stable_finding_id(",
            "def broken_stable_finding_id(",
        ),
        (
            ".github/actions/canonicalize-review/canonicalize_review.py",
            "def canonicalize(",
            "def broken_canonicalize(",
        ),
        (
            ".github/actions/canonicalize-review/review_scope.py",
            "class SourceAnchor:",
            "class BrokenSourceAnchor:",
        ),
        (
            ".github/actions/canonicalize-review/review_scope.py",
            "class TriggerEvidence:",
            "class BrokenTriggerEvidence:",
        ),
        (
            ".github/actions/canonicalize-review/review_scope.py",
            "class ScopeValidationError(ValueError):",
            "class BrokenScopeValidationError(ValueError):",
        ),
        (
            ".github/actions/canonicalize-review/review_scope.py",
            "class ReviewScope:",
            "class BrokenReviewScope:",
        ),
        (
            ".github/actions/canonicalize-review/review_scope.py",
            "    def validate_changed_anchor(",
            "    def broken_validate_changed_anchor(",
        ),
        (
            ".github/actions/canonicalize-review/review_scope.py",
            "    def validate_fix_anchor(",
            "    def broken_validate_fix_anchor(",
        ),
        (
            ".github/actions/canonicalize-review/review_scope.py",
            "    def validate_trigger(",
            "    def broken_validate_trigger(",
        ),
        (
            ".github/actions/canonicalize-review/review_scope.py",
            "def load_review_scope(",
            "def broken_load_review_scope(",
        ),
    ),
)
def test_v146_rejects_missing_canonicalizer_public_signatures(
    v1462_release_repo: tuple[Path, str], relative: str, old: str, new: str
) -> None:
    repo, _ = v1462_release_repo
    replace(repo / relative, old, new, count=1)
    bad_commit = commit(repo, f"remove public helper signature {old}")

    with pytest.raises(
        ReleaseVerificationError, match="canonicalize-review helper contract"
    ):
        release_verifier.verify_commit_content(repo, "v1.46.2", bad_commit)


def test_v146_helper_verification_compiles_but_never_executes_commit_code(
    v1462_release_repo: tuple[Path, str], tmp_path: Path
) -> None:
    repo, _ = v1462_release_repo
    sentinel = tmp_path / "commit-helper-executed"
    helper = repo / ".github/actions/canonicalize-review/canonicalize_review.py"
    helper.write_text(
        helper.read_text(encoding="utf-8")
        + f"\n__import__('pathlib').Path({str(sentinel)!r}).write_text('executed')\n",
        encoding="utf-8",
    )
    commit_with_side_effect = commit(repo, "prove helper verifier is static")

    with pytest.raises(
        ReleaseVerificationError, match="canonicalize-review helper contract"
    ):
        release_verifier.verify_commit_content(
            repo, "v1.46", commit_with_side_effect
        )
    assert not sentinel.exists()


@pytest.mark.parametrize(
    "workflow", ("claude-code-review.yml", "gemini-auto-review.yml")
)
@pytest.mark.parametrize("mutation", ("missing", "duplicate", "near-match"))
def test_v146_rejects_nonexact_reviewer_canonicalizer_dependencies(
    v1462_release_repo: tuple[Path, str], workflow: str, mutation: str
) -> None:
    repo, _ = v1462_release_repo
    path = repo / ".github/workflows" / workflow
    contract = REVIEWER_WORKFLOW_CONTRACTS[workflow]

    def drift(document: dict) -> None:
        steps = document["jobs"][contract["job"]]["steps"]
        step = next(item for item in steps if item.get("uses") == CANONICALIZE_REVIEW_ACTION)
        if mutation == "missing":
            steps.remove(step)
        elif mutation == "duplicate":
            steps.append({"uses": CANONICALIZE_REVIEW_ACTION})
        else:
            step["uses"] = CANONICALIZE_REVIEW_ACTION + "/action.yml"

    mutate_yaml(path, drift)
    bad_commit = commit(repo, f"drift {workflow} canonicalizer dependency")

    with pytest.raises(
        ReleaseVerificationError, match="review action dependency contract"
    ):
        release_verifier.verify_commit_content(repo, "v1.46.2", bad_commit)


def test_v146_requires_review_auth_helper_from_the_release_commit(
    v1462_release_repo: tuple[Path, str],
) -> None:
    repo, _ = v1462_release_repo
    path = repo / ".github/workflows/gemini-auto-review.yml"
    replace(
        path,
        "$/.github/actions/setup-gemini-auth",
        "jhw7500/automation/.github/actions/setup-gemini-auth@"
        "2254f13aab44585c78954d20749f4fb677a8c2f1",
        count=1,
    )
    bad_commit = commit(repo, "detach review auth helper from release")

    with pytest.raises(ReleaseVerificationError, match="review action dependency"):
        release_verifier.verify_commit_content(repo, "v1.46.2", bad_commit)


@pytest.mark.parametrize(
    "workflow", ("claude-code-review.yml", "gemini-auto-review.yml")
)
@pytest.mark.parametrize(
    "mutation", ("missing", "wrong-ref", "cleaning", "late", "canonicalizer-early")
)
def test_v146_requires_exact_prepared_head_checkout_before_the_provider(
    v1462_release_repo: tuple[Path, str], workflow: str, mutation: str,
) -> None:
    repo, _ = v1462_release_repo
    path = repo / ".github/workflows" / workflow
    contract = REVIEWER_WORKFLOW_CONTRACTS[workflow]

    def drift(document: dict) -> None:
        steps = document["jobs"][contract["job"]]["steps"]
        checkout = next(
            item for item in steps if item.get("name") == "Checkout prepared review head"
        )
        if mutation == "missing":
            steps.remove(checkout)
        elif mutation == "wrong-ref":
            checkout["with"]["ref"] = "${{ github.sha }}"
        elif mutation == "cleaning":
            checkout["with"]["clean"] = "true"
        elif mutation == "late":
            steps.remove(checkout)
            provider_index = next(
                index for index, item in enumerate(steps)
                if item.get("name") == contract["provider_step"]
            )
            steps.insert(provider_index + 1, checkout)
        else:
            canonicalizer = next(
                item for item in steps
                if item.get("uses") == CANONICALIZE_REVIEW_ACTION
            )
            steps.remove(canonicalizer)
            checkout_index = steps.index(checkout)
            steps.insert(checkout_index, canonicalizer)

    mutate_yaml(path, drift)
    bad_commit = commit(repo, f"drift {workflow} prepared head checkout")

    with pytest.raises(ReleaseVerificationError, match="review publication contract"):
        release_verifier.verify_commit_content(repo, "v1.46.2", bad_commit)


@pytest.mark.parametrize(
    "workflow", ("claude-code-review.yml", "gemini-auto-review.yml")
)
def test_v146_requires_review_diff_outputs_outside_the_checkout_workspace(
    v1462_release_repo: tuple[Path, str],
    workflow: str,
) -> None:
    repo, _ = v1462_release_repo
    path = repo / ".github/workflows" / workflow
    contract = REVIEWER_WORKFLOW_CONTRACTS[workflow]

    def move_into_workspace(step: dict) -> None:
        step["with"]["output-directory"] = "${{ github.workspace }}"

    mutate_named_step(path, contract["job"], "Prepare review diff", move_into_workspace)
    bad_commit = commit(repo, f"move {workflow} review inputs into checkout workspace")

    with pytest.raises(ReleaseVerificationError, match="review publication contract"):
        release_verifier.verify_commit_content(repo, "v1.46.2", bad_commit)


def test_v146_requires_explicit_opencode_review_diff_output_directory(
    v1462_release_repo: tuple[Path, str],
) -> None:
    repo, _ = v1462_release_repo
    path = repo / ".github/workflows/opencode-auto-review.yml"

    def remove_output_directory(step: dict) -> None:
        step["with"].pop("output-directory")

    mutate_named_step(
        path,
        "opencode-prepare",
        "Prepare review diff",
        remove_output_directory,
    )
    bad_commit = commit(repo, "remove OpenCode review diff output directory")

    with pytest.raises(ReleaseVerificationError, match="output directory contract"):
        release_verifier.verify_commit_content(repo, "v1.46.2", bad_commit)


def test_v146_rejects_an_opencode_canonicalizer_dependency(
    v1462_release_repo: tuple[Path, str],
) -> None:
    repo, _ = v1462_release_repo
    append_action_reference(
        repo / ".github/workflows/opencode-auto-review.yml",
        CANONICALIZE_REVIEW_ACTION,
    )
    bad_commit = commit(repo, "wire canonicalizer into OpenCode")

    with pytest.raises(
        ReleaseVerificationError, match="review action dependency contract"
    ):
        release_verifier.verify_commit_content(repo, "v1.46.2", bad_commit)


@pytest.mark.parametrize(
    "workflow",
    ("claude-code-review.yml", "gemini-auto-review.yml", "opencode-auto-review.yml"),
)
@pytest.mark.parametrize("action_files_present", (True, False))
def test_v145_rejects_canonicalizer_dependency_regardless_of_future_files(
    current_release_repo: tuple[Path, str],
    workflow: str,
    action_files_present: bool,
) -> None:
    repo, _ = current_release_repo
    restore_v145_review_workflows(repo)
    append_action_reference(
        repo / ".github/workflows" / workflow,
        CANONICALIZE_REVIEW_ACTION,
    )
    if not action_files_present:
        for relative in CANONICALIZER_RELEASE_FILES:
            (repo / relative).unlink()
    bad_commit = commit(repo, f"add pre-v1.46 canonicalizer dependency to {workflow}")

    with pytest.raises(
        ReleaseVerificationError, match="review action dependency contract"
    ):
        release_verifier.verify_commit_content(repo, "v1.45", bad_commit)


def test_v146_binds_review_contract_to_the_exact_root_workflow_path(
    v1462_release_repo: tuple[Path, str],
) -> None:
    repo, _ = v1462_release_repo
    root = repo / ".github/workflows/claude-code-review.yml"
    nested = repo / ".github/workflows/zz/claude-code-review.yml"
    nested.parent.mkdir(parents=True)
    shutil.copy2(root, nested)

    def corrupt_root(step: dict) -> None:
        step["with"]["script"] = step["with"]["script"].replace(
            "state.schema === 3", "state.schema === 2", 1
        )

    mutate_named_step(root, "claude-review", "Upsert review comment", corrupt_root)
    bad_commit = commit(repo, "shadow malicious root review with nested decoy")

    with pytest.raises(ReleaseVerificationError, match="central review workflow"):
        release_verifier.verify_commit_content(repo, "v1.46.2", bad_commit)


def test_v146_rejects_nested_central_workflow_before_the_root_sort_order(
    v1462_release_repo: tuple[Path, str],
) -> None:
    repo, _ = v1462_release_repo
    root = repo / ".github/workflows/claude-code-review.yml"
    nested = repo / ".github/workflows/aa/claude-code-review.yml"
    nested.parent.mkdir(parents=True)
    shutil.copy2(root, nested)
    bad_commit = commit(repo, "add early-sorting nested central workflow")

    with pytest.raises(ReleaseVerificationError, match="central review workflow"):
        release_verifier.verify_commit_content(repo, "v1.46.2", bad_commit)


def test_v146_allows_an_unrelated_nested_workflow_without_shadowing_root_contracts(
    v1462_release_repo: tuple[Path, str],
) -> None:
    repo, _ = v1462_release_repo
    nested = repo / ".github/workflows/zz/unrelated.yml"
    nested.parent.mkdir(parents=True)
    nested.write_text(
        "name: Nested fixture\n"
        "on:\n"
        "  workflow_call:\n"
        "jobs: {}\n",
        encoding="utf-8",
    )
    commit_with_nested = commit(repo, "add unrelated nested workflow")

    assert (
        release_verifier.verify_commit_content(
                repo, "v1.46.2", commit_with_nested
        )
        == commit_with_nested
    )


@pytest.mark.parametrize(
    "workflow", ("claude-code-review.yml", "gemini-auto-review.yml")
)
@pytest.mark.parametrize(
    ("old", "new"),
    (
        (
            'select(.user.type == "Bot" and .user.login == $bot_login)',
            'select(.user.type == "Bot")',
        ),
        (
            ".referenced_workflows[]?",
            ".pull_requests[]?",
        ),
        (
            "actions/runs/${candidate_run_id}/attempts/${candidate_attempt}",
            "actions/runs/${candidate_run_id}",
        ),
    ),
    ids=("exact-bot", "reusable-workflow", "run-attempt"),
)
def test_v146_authenticates_collected_review_state_against_its_run(
    v1462_release_repo: tuple[Path, str],
    workflow: str,
    old: str,
    new: str,
) -> None:
    repo, _ = v1462_release_repo
    path = repo / ".github/workflows" / workflow
    contract = REVIEWER_WORKFLOW_CONTRACTS[workflow]

    def weaken_provenance(step: dict) -> None:
        script = step["run"]
        target_old, target_new = old, new
        if workflow == "gemini-auto-review.yml" and old.startswith("select(.user.type"):
            target_old = (
                "select(publisher_matches($bot_login; $auth_mode; "
                "$publisher_app_id))"
            )
        assert target_old in script
        step["run"] = script.replace(target_old, target_new, 1)

    mutate_named_step(
        path, contract["job"], contract["collector_step"], weaken_provenance
    )
    bad_commit = commit(repo, f"weaken {workflow} collected state provenance")

    with pytest.raises(ReleaseVerificationError, match="review publication contract"):
        release_verifier.verify_commit_content(repo, "v1.46.2", bad_commit)


@pytest.mark.parametrize(
    "workflow", ("claude-code-review.yml", "gemini-auto-review.yml")
)
@pytest.mark.parametrize(
    ("old", "new"),
    (
        ("gh api --include", "gh api"),
        ("lookup_status\" = '404'", "lookup_status\" = '503'"),
        (
            "sort_by(.state.run_id, .state.run_attempt, .comment.id)",
            "sort_by(.state.run_id, .state.run_attempt)",
        ),
        (
            "Failed to fetch the prior review comment snapshot",
            "Failed to fetch optional review context",
        ),
    ),
    ids=("http-status", "404-policy", "comment-id-tie", "comment-snapshot"),
)
def test_v146_rejects_fail_open_prior_state_collection(
    v1462_release_repo: tuple[Path, str],
    workflow: str,
    old: str,
    new: str,
) -> None:
    repo, _ = v1462_release_repo
    path = repo / ".github/workflows" / workflow
    contract = REVIEWER_WORKFLOW_CONTRACTS[workflow]

    def weaken_collection(step: dict) -> None:
        script = step["run"]
        assert old in script
        step["run"] = script.replace(old, new, 1)

    mutate_named_step(
        path, contract["job"], contract["collector_step"], weaken_collection
    )
    bad_commit = commit(repo, f"weaken {workflow} prior-state collection")

    with pytest.raises(ReleaseVerificationError, match="review publication contract"):
        release_verifier.verify_commit_content(repo, "v1.46.2", bad_commit)


@pytest.mark.parametrize(
    "workflow", ("claude-code-review.yml", "gemini-auto-review.yml")
)
def test_v146_requires_publication_to_skip_after_collection_failure(
    v1462_release_repo: tuple[Path, str], workflow: str
) -> None:
    repo, _ = v1462_release_repo
    path = repo / ".github/workflows" / workflow
    contract = REVIEWER_WORKFLOW_CONTRACTS[workflow]

    def remove_collection_guard(step: dict) -> None:
        step["if"] = "${{ !cancelled() }}"

    mutate_named_step(
        path, contract["job"], "Upsert review comment", remove_collection_guard
    )
    bad_commit = commit(repo, f"remove {workflow} prior-state publication guard")

    with pytest.raises(ReleaseVerificationError, match="review publication contract"):
        release_verifier.verify_commit_content(repo, "v1.46.2", bad_commit)


@pytest.mark.parametrize(
    "workflow", ("claude-code-review.yml", "gemini-auto-review.yml")
)
@pytest.mark.parametrize(
    ("old", "new"),
    (
        ("comment.user?.login !== botLogin", "false"),
        (
            "attempt_number: record.state.run_attempt",
            "attempt_number: 1",
        ),
        ("run?.referenced_workflows", "run?.pull_requests"),
    ),
    ids=("exact-bot", "run-attempt", "reusable-workflow"),
)
def test_v146_authenticates_published_review_state_before_stale_guarding(
    v1462_release_repo: tuple[Path, str],
    workflow: str,
    old: str,
    new: str,
) -> None:
    repo, _ = v1462_release_repo
    path = repo / ".github/workflows" / workflow
    contract = REVIEWER_WORKFLOW_CONTRACTS[workflow]

    def weaken_provenance(step: dict) -> None:
        script = step["with"]["script"]
        target_old, target_new = old, new
        if workflow == "gemini-auto-review.yml" and old == "comment.user?.login !== botLogin":
            target_old = "if (!publisherMatches(comment)) return null;"
            target_new = "if (false) return null;"
        assert target_old in script
        step["with"]["script"] = script.replace(target_old, target_new, 1)

    mutate_named_step(path, contract["job"], "Upsert review comment", weaken_provenance)
    bad_commit = commit(repo, f"weaken {workflow} published state provenance")

    with pytest.raises(ReleaseVerificationError, match="review publication contract"):
        release_verifier.verify_commit_content(repo, "v1.46.2", bad_commit)


@pytest.mark.parametrize(
    "workflow", ("claude-code-review.yml", "gemini-auto-review.yml")
)
@pytest.mark.parametrize("read_style", ("literal", "variable"))
def test_v146_rejects_reviewer_upsert_reading_the_raw_candidate(
    v1462_release_repo: tuple[Path, str], workflow: str, read_style: str
) -> None:
    repo, _ = v1462_release_repo
    contract = REVIEWER_WORKFLOW_CONTRACTS[workflow]
    path = repo / ".github/workflows" / workflow

    def publish_raw(step: dict) -> None:
        script = step["with"]["script"]
        assert contract["canonical"] in script
        if read_style == "literal":
            step["with"]["script"] = script.replace(
                contract["canonical"], contract["raw"], 1
            )
            return
        canonical_read = (
            f"fs.readFileSync('{contract['canonical']}', 'utf8')"
        )
        assert canonical_read in script
        step["with"]["script"] = (
            f"const rawCandidate = '{contract['raw']}';\n"
            + script.replace(
                canonical_read,
                "fs.readFileSync(rawCandidate, 'utf8')",
                1,
            )
        )

    mutate_named_step(path, contract["job"], "Upsert review comment", publish_raw)
    bad_commit = commit(repo, f"publish raw candidate from {workflow}")

    with pytest.raises(
        ReleaseVerificationError, match="review publication contract"
    ):
        release_verifier.verify_commit_content(repo, "v1.46.2", bad_commit)


@pytest.mark.parametrize(
    ("workflow", "upload_step"),
    (
        (
            "claude-code-review.yml",
            "Upload rejected Claude review diagnostic",
        ),
        (
            "gemini-auto-review.yml",
            "Upload rejected Gemini review diagnostic",
        ),
    ),
)
def test_v146_binds_rejected_review_diagnostics_to_one_day(
    v1462_release_repo: tuple[Path, str],
    workflow: str,
    upload_step: str,
) -> None:
    repo, _ = v1462_release_repo
    contract = REVIEWER_WORKFLOW_CONTRACTS[workflow]
    path = repo / ".github/workflows" / workflow

    def retain_too_long(step: dict) -> None:
        step["with"]["retention-days"] = "90"

    mutate_named_step(
        path,
        contract["job"],
        upload_step,
        retain_too_long,
    )
    bad_commit = commit(repo, f"retain rejected {workflow} diagnostic too long")

    with pytest.raises(
        ReleaseVerificationError,
        match="review publication contract",
    ):
        release_verifier.verify_commit_content(repo, "v1.46.2", bad_commit)


@pytest.mark.parametrize(
    ("workflow", "upload_step"),
    (
        (
            "claude-code-review.yml",
            "Upload rejected Claude review diagnostic",
        ),
        (
            "gemini-auto-review.yml",
            "Upload rejected Gemini review diagnostic",
        ),
    ),
)
def test_v146_never_uploads_a_rejected_raw_review_candidate(
    v1462_release_repo: tuple[Path, str],
    workflow: str,
    upload_step: str,
) -> None:
    repo, _ = v1462_release_repo
    contract = REVIEWER_WORKFLOW_CONTRACTS[workflow]
    path = repo / ".github/workflows" / workflow

    def expose_raw_candidate(step: dict) -> None:
        step["with"]["path"] = f"${{{{ github.workspace }}}}/{contract['raw']}"

    mutate_named_step(path, contract["job"], upload_step, expose_raw_candidate)
    bad_commit = commit(repo, f"upload rejected raw candidate from {workflow}")

    with pytest.raises(ReleaseVerificationError, match="review publication contract"):
        release_verifier.verify_commit_content(repo, "v1.46.2", bad_commit)


@pytest.mark.parametrize(
    "workflow", ("claude-code-review.yml", "gemini-auto-review.yml")
)
@pytest.mark.parametrize(
    "read_variant", ("concatenated-path", "aliased-reader", "helper-reader")
)
def test_v146_rejects_dead_canonical_read_decoys_and_dynamic_raw_reads(
    v1462_release_repo: tuple[Path, str], workflow: str, read_variant: str
) -> None:
    repo, _ = v1462_release_repo
    contract = REVIEWER_WORKFLOW_CONTRACTS[workflow]
    path = repo / ".github/workflows" / workflow

    def bypass_canonical_read(step: dict) -> None:
        script = step["with"]["script"]
        canonical_read = (
            f"fs.readFileSync('{contract['canonical']}', 'utf8')"
        )
        live_statement = f"review = stripValidation({canonical_read});"
        assert script.count(canonical_read) == 1
        assert live_statement in script

        separator = "-" if "-" in contract["raw"] else "_"
        head, tail = contract["raw"].split(separator, 1)
        dynamic_path = f"'{head}{separator}' + '{tail}'"
        decoy = f"if (false) {{ stripValidation({canonical_read}); }}\n"
        if read_variant == "concatenated-path":
            live_read = f"fs.readFileSync({dynamic_path}, 'utf8')"
            prefix = ""
        elif read_variant == "aliased-reader":
            live_read = f"candidateRead({dynamic_path}, 'utf8')"
            prefix = "const candidateRead = fs.readFileSync;\n"
        else:
            live_read = "readCandidate()"
            prefix = (
                "const readCandidate = () => "
                f"fs.readFileSync([{dynamic_path}].join(''), 'utf8');\n"
            )
        step["with"]["script"] = (
            prefix
            + decoy
            + script.replace(
                live_statement,
                f"review = stripValidation({live_read});",
                1,
            )
        )

    mutate_named_step(
        path, contract["job"], "Upsert review comment", bypass_canonical_read
    )
    bad_commit = commit(repo, f"bypass canonical read in {workflow}")

    with pytest.raises(
        ReleaseVerificationError, match="review publication contract"
    ):
        release_verifier.verify_commit_content(repo, "v1.46.2", bad_commit)


@pytest.mark.parametrize(
    "workflow", ("claude-code-review.yml", "gemini-auto-review.yml")
)
@pytest.mark.parametrize(
    ("old", "new"),
    (
        ("state.schema === 3", "state.schema === 2"),
        ("state.quality_schema === 1", "state.quality_schema === 2"),
        ("- Validation: accepted=", "- Validated: accepted="),
    ),
    ids=("schema", "quality-schema", "validation-spelling"),
)
def test_v146_rejects_reviewer_v3_publication_state_drift(
    v1462_release_repo: tuple[Path, str], workflow: str, old: str, new: str
) -> None:
    repo, _ = v1462_release_repo
    contract = REVIEWER_WORKFLOW_CONTRACTS[workflow]
    path = repo / ".github/workflows" / workflow

    def drift(step: dict) -> None:
        script = step["with"]["script"]
        assert old in script
        step["with"]["script"] = script.replace(old, new, 1)

    mutate_named_step(path, contract["job"], "Upsert review comment", drift)
    bad_commit = commit(repo, f"drift {workflow} v3 publication state")

    with pytest.raises(
        ReleaseVerificationError, match="review publication contract"
    ):
        release_verifier.verify_commit_content(repo, "v1.46.2", bad_commit)


@pytest.mark.parametrize(
    "workflow", ("claude-code-review.yml", "gemini-auto-review.yml")
)
@pytest.mark.parametrize(
    "quality_key",
    (
        "quality_schema",
        "accepted_count",
        "filtered_count",
        "normalized_count",
        "filtered_max_severity",
    ),
)
def test_v146_rejects_missing_quality_state_keys(
    v1462_release_repo: tuple[Path, str], workflow: str, quality_key: str
) -> None:
    repo, _ = v1462_release_repo
    contract = REVIEWER_WORKFLOW_CONTRACTS[workflow]
    path = repo / ".github/workflows" / workflow

    def remove_key(step: dict) -> None:
        script = step["with"]["script"]
        token = f"'{quality_key}', "
        assert token in script
        step["with"]["script"] = script.replace(token, "", 1)

    mutate_named_step(path, contract["job"], "Upsert review comment", remove_key)
    bad_commit = commit(repo, f"remove {workflow} quality key {quality_key}")

    with pytest.raises(
        ReleaseVerificationError, match="review publication contract"
    ):
        release_verifier.verify_commit_content(repo, "v1.46.2", bad_commit)


@pytest.mark.parametrize(
    "workflow", ("claude-code-review.yml", "gemini-auto-review.yml")
)
@pytest.mark.parametrize("marker_kind", ("v3", "v2"))
def test_v146_rejects_reviewer_marker_drift(
    v1462_release_repo: tuple[Path, str], workflow: str, marker_kind: str
) -> None:
    repo, _ = v1462_release_repo
    contract = REVIEWER_WORKFLOW_CONTRACTS[workflow]
    marker = contract["marker" if marker_kind == "v3" else "v2_marker"]
    path = repo / ".github/workflows" / workflow

    def drift(step: dict) -> None:
        script = step["with"]["script"]
        assert marker in script
        step["with"]["script"] = script.replace(marker, marker + "-drift", 1)

    mutate_named_step(path, contract["job"], "Upsert review comment", drift)
    bad_commit = commit(repo, f"drift {workflow} {marker_kind} marker")

    with pytest.raises(
        ReleaseVerificationError, match="review publication contract"
    ):
        release_verifier.verify_commit_content(repo, "v1.46.2", bad_commit)


@pytest.mark.parametrize(
    "workflow", ("claude-code-review.yml", "gemini-auto-review.yml")
)
def test_v146_rejects_reviewer_specific_canonicalizer_inputs(
    v1462_release_repo: tuple[Path, str], workflow: str
) -> None:
    repo, _ = v1462_release_repo
    contract = REVIEWER_WORKFLOW_CONTRACTS[workflow]
    path = repo / ".github/workflows" / workflow

    def drift(step: dict) -> None:
        step["with"]["reviewer"] = "gemini" if workflow.startswith("claude") else "claude"

    mutate_named_step(path, contract["job"], "Canonicalize " + (
        "Claude review" if workflow.startswith("claude") else "Gemini review"
    ), drift)
    bad_commit = commit(repo, f"cross-wire {workflow} canonicalizer identity")

    with pytest.raises(
        ReleaseVerificationError, match="review publication contract"
    ):
        release_verifier.verify_commit_content(repo, "v1.46.2", bad_commit)


@pytest.mark.parametrize(
    "workflow", ("claude-code-review.yml", "gemini-auto-review.yml")
)
@pytest.mark.parametrize(
    "field",
    ("Trigger evidence:", "Material impact:", "Performance basis:", "RVW-<12hex>"),
)
def test_v146_requires_quality_prompt_rules_in_both_reviewers(
    v1462_release_repo: tuple[Path, str], workflow: str, field: str
) -> None:
    repo, _ = v1462_release_repo
    contract = REVIEWER_WORKFLOW_CONTRACTS[workflow]
    path = repo / ".github/workflows" / workflow

    def remove_rule(step: dict) -> None:
        container = step["with"] if contract["prompt_key"] == "prompt" else step
        prompt = container[contract["prompt_key"]]
        assert field in prompt
        container[contract["prompt_key"]] = prompt.replace(
            field, "REMOVED QUALITY FIELD:"
        )

    mutate_named_step(
        path,
        contract["job"],
        contract["provider_step"],
        remove_rule,
    )
    bad_commit = commit(repo, f"remove {field} prompt rule from {workflow}")

    with pytest.raises(
        ReleaseVerificationError, match="review publication contract"
    ):
        release_verifier.verify_commit_content(repo, "v1.46.2", bad_commit)


def test_historical_review_workflows_restore_without_invoking_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "historical-fixture"
    workflow_root = repo / ".github/workflows"
    workflow_root.mkdir(parents=True)

    def reject_git(*args, **kwargs):
        raise AssertionError("historical fixture restoration invoked subprocess")

    monkeypatch.setattr(subprocess, "run", reject_git)
    restore_historical_review_workflows(repo)

    fixture_root = Path(__file__).parent / "fixtures/review-workflows-v1.44"
    for filename in (
        "claude-code-review.yml",
        "gemini-auto-review.yml",
        "opencode-auto-review.yml",
    ):
        assert (workflow_root / filename).read_bytes() == (
            fixture_root / filename
        ).read_bytes()


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
        release_verifier.verify_commit_content(repo, "v1.46.2", bad_commit)


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
        release_verifier.verify_commit_content(repo, "v1.46.2", bad_commit)


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
        release_verifier.verify_commit_content(repo, "v1.47", bad_commit)


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
        release_verifier.verify_commit_content(repo, "v1.47", bad_commit)


def test_release_verifier_preserves_pre_inventory_v139_contract(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "historical-automation"
    shutil.copytree(ROOT / ".github/workflows", repo / ".github/workflows")
    restore_historical_review_workflows(repo)
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    commit_oid = commit(repo, "historical release")
    git(repo, "tag", "-a", "v1.39", "-m", "v1.39")

    assert verify_release(repo, "v1.39", commit_oid) == commit_oid


def test_v144_release_keeps_the_historical_inventory_without_prepare_diff_action(
    current_release_repo: tuple[Path, str],
) -> None:
    """Adding a v1.45 action must not retroactively invalidate immutable v1.44 tags."""
    repo, _ = current_release_repo
    restore_historical_review_workflows(repo)
    for relative in (
        ".github/actions/prepare-review-diff/action.yml",
        ".github/actions/prepare-review-diff/prepare_review_diff.py",
    ):
        (repo / relative).unlink()
    historical_commit = commit(repo, "v1.44 historical inventory")
    git(repo, "tag", "-a", "v1.44", "-m", "v1.44")

    assert verify_release(repo, "v1.44", historical_commit) == historical_commit


def test_v140_release_without_prepare_dependency_or_action_files_passes(
    release_repo: tuple[Path, Path, str],
) -> None:
    repo, _, _ = release_repo
    for relative in (
        ".github/actions/prepare-review-diff/action.yml",
        ".github/actions/prepare-review-diff/prepare_review_diff.py",
    ):
        (repo / relative).unlink()
    historical_commit = commit(repo, "v1.40 without future review action")

    assert (
        release_verifier.verify_commit_content(repo, "v1.40", historical_commit)
        == historical_commit
    )


@pytest.mark.parametrize(
    "workflow",
    ("claude-code-review.yml", "gemini-auto-review.yml", "opencode-auto-review.yml"),
)
@pytest.mark.parametrize("action_files_present", (True, False))
def test_pre_v145_rejects_prepare_dependency_regardless_of_future_action_files(
    current_release_repo: tuple[Path, str],
    workflow: str,
    action_files_present: bool,
) -> None:
    repo, _ = current_release_repo
    restore_historical_review_workflows(
        repo,
        tuple(name for name in HISTORICAL_REVIEW_WORKFLOWS if name != workflow),
    )
    if not action_files_present:
        for relative in (
            ".github/actions/prepare-review-diff/action.yml",
            ".github/actions/prepare-review-diff/prepare_review_diff.py",
        ):
            (repo / relative).unlink()
    bad_commit = commit(repo, f"pre-v1.45 {workflow} dependency")

    with pytest.raises(ReleaseVerificationError, match="prepare-review-diff dependency"):
        release_verifier.verify_commit_content(repo, "v1.44", bad_commit)


@pytest.mark.parametrize(
    "workflow",
    ("claude-code-review.yml", "gemini-auto-review.yml", "opencode-auto-review.yml"),
)
@pytest.mark.parametrize(
    "replacement",
    (
        "$/.github/actions/prepare-review-diff/action.yml",
        "$/.github/actions/check-workflow-enabled",
    ),
    ids=("near-match", "other-local-action"),
)
def test_v145_rejects_nonexact_local_review_action_dependencies(
    current_release_repo: tuple[Path, str], workflow: str, replacement: str
) -> None:
    repo, _ = current_release_repo
    restore_v145_review_workflows(repo)
    replace(
        repo / ".github/workflows" / workflow,
        "$/.github/actions/prepare-review-diff",
        replacement,
        count=1,
    )
    bad_commit = commit(repo, f"invalid {workflow} local action")

    with pytest.raises(ReleaseVerificationError, match="prepare-review-diff dependency"):
        release_verifier.verify_commit_content(repo, "v1.45", bad_commit)


@pytest.mark.parametrize(
    "workflow",
    ("claude-code-review.yml", "gemini-auto-review.yml", "opencode-auto-review.yml"),
)
@pytest.mark.parametrize(
    "reference",
    (
        "./.github/actions/unowned",
        "$/.github/actions/prepare-review-diff",
    ),
    ids=("dot-local-action", "duplicate-exact-action"),
)
def test_v145_rejects_appended_local_review_action_dependencies(
    current_release_repo: tuple[Path, str], workflow: str, reference: str
) -> None:
    repo, _ = current_release_repo
    restore_v145_review_workflows(repo)
    append_action_reference(repo / ".github/workflows" / workflow, reference)
    bad_commit = commit(repo, f"append invalid {workflow} local action")

    with pytest.raises(ReleaseVerificationError, match="prepare-review-diff dependency"):
        release_verifier.verify_commit_content(repo, "v1.45", bad_commit)


@pytest.mark.parametrize(
    "workflow",
    ("claude-code-review.yml", "gemini-auto-review.yml", "opencode-auto-review.yml"),
)
def test_pre_v145_rejects_appended_dot_local_review_action_dependency(
    current_release_repo: tuple[Path, str], workflow: str
) -> None:
    repo, _ = current_release_repo
    restore_historical_review_workflows(repo)
    append_action_reference(
        repo / ".github/workflows" / workflow,
        "./.github/actions/unowned",
    )
    bad_commit = commit(repo, f"append pre-v1.45 {workflow} local action")

    with pytest.raises(ReleaseVerificationError, match="prepare-review-diff dependency"):
        release_verifier.verify_commit_content(repo, "v1.44", bad_commit)


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


@pytest.mark.parametrize(
    "relative",
    (
        ".github/actions/prepare-review-diff/action.yml",
        ".github/actions/prepare-review-diff/prepare_review_diff.py",
    ),
)
@pytest.mark.parametrize("mutation", ("missing", "executable-mode"))
def test_v145_commit_gate_requires_each_prepare_diff_action_file_as_0644_blob(
    release_repo: tuple[Path, Path, str], relative: str, mutation: str
) -> None:
    """The next release line fails closed on absent or non-regular action artifacts."""
    repo, _, _ = release_repo
    target = repo / relative
    if mutation == "missing":
        target.unlink()
    else:
        target.chmod(0o755)
    bad_commit = commit(repo, f"mutate {relative}")

    with pytest.raises(ReleaseVerificationError, match="release inventory"):
        release_verifier.verify_commit_content(repo, "v1.45", bad_commit)


def test_v145_commit_gate_rejects_an_unsafe_prepare_diff_action_contract(
    release_repo: tuple[Path, Path, str],
) -> None:
    """Release verification must reject a shell that interpolates a PR-controlled input."""
    repo, _, _ = release_repo
    action = repo / ".github/actions/prepare-review-diff/action.yml"
    replace(action, '"$PR_NUMBER"', '"${{ inputs.pr-number }}"')
    bad_commit = commit(repo, "weaken prepare diff action boundary")

    with pytest.raises(ReleaseVerificationError, match="prepare-review-diff action contract"):
        release_verifier.verify_commit_content(repo, "v1.45", bad_commit)


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
        (extra_local_setup_resolver, "prepare-review-diff dependency"),
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
        (alternate_local_token_mint_action, "prepare-review-diff dependency"),
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
