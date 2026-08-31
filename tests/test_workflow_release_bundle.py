"""Tests for immutable, safely extracted workflow release bundles."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import traceback
import zlib

import pytest
import yaml

from scripts.verify_workflow_release import ReleaseVerificationError
import scripts.verify_workflow_release as release_verifier
import scripts.workflow_release_bundle as release_bundle
import scripts.workflow_release_inventory as release_inventory
from scripts.workflow_release_bundle import materialize_release_bundle
from scripts.workflow_release_inventory import EXACT_RELEASE_ROOTS, RELEASE_PATHS

from release_fixture_helpers import (
    restore_historical_automation_ref,
    restore_historical_review_workflows,
)

ROOT = Path(__file__).resolve().parents[1]
RELEASE_REF = "v1.40.2"

EXACT_RELEASE_FILES = tuple(
    root.path.as_posix() for root in EXACT_RELEASE_ROOTS
)
V1462_RELEASE_FILES = tuple(
    root.path.as_posix()
    for root in release_inventory.release_roots_for("v1.46.2")
    if root.kind == "file"
)
PREPARE_REVIEW_DIFF_ACTION = (
    ROOT / ".github/actions/prepare-review-diff/action.yml"
)
CANONICALIZE_REVIEW_ACTION = (
    ROOT / ".github/actions/canonicalize-review/action.yml"
)
REVIEW_INVOCATION_BUDGET_ACTION = (
    ROOT / ".github/actions/review-invocation-budget/action.yml"
)
REVIEW_INVOCATION_BUDGET_RELEASE_FILES = {
    ".github/actions/review-invocation-budget/action.yml",
    ".github/actions/review-invocation-budget/review_invocation_budget.py",
}
REVIEW_POLICY_RELEASE_FILES = {
    ".github/actions/resolve-review-policy/action.yml",
    ".github/actions/resolve-review-policy/resolve_review_policy.py",
}


def test_canonicalize_review_composite_action_has_exact_safe_shell_contract() -> None:
    """The public action surface must remain a mutation-free helper adapter."""
    document = yaml.load(
        CANONICALIZE_REVIEW_ACTION.read_text(encoding="utf-8"), Loader=yaml.BaseLoader
    )

    assert document == {
        "name": "Canonicalize review",
        "description": "Canonicalize evidenced Claude or Gemini review findings",
        "inputs": {
            "reviewer": {"required": "true"},
            "candidate-file": {"required": "true"},
            "canonical-file": {"required": "true"},
            "result-file": {"required": "true"},
            "scope-manifest": {"required": "true"},
            "selected-diff": {"required": "true"},
            "diff-mode": {"required": "true"},
            "previous-sha": {"required": "false", "default": ""},
            "previous-review-file": {"required": "false", "default": ""},
        },
        "outputs": {
            "document-valid": {"value": "${{ steps.canonicalize.outputs.document_valid }}"},
            "accepted-count": {"value": "${{ steps.canonicalize.outputs.accepted_count }}"},
            "filtered-count": {"value": "${{ steps.canonicalize.outputs.filtered_count }}"},
            "normalized-count": {"value": "${{ steps.canonicalize.outputs.normalized_count }}"},
            "filtered-max-severity": {
                "value": "${{ steps.canonicalize.outputs.filtered_max_severity }}"
            },
            "failure-reason": {"value": "${{ steps.canonicalize.outputs.failure_reason }}"},
        },
        "runs": {
            "using": "composite",
            "steps": [
                {
                    "id": "canonicalize",
                    "shell": "bash",
                    "env": {
                        "REVIEWER": "${{ inputs.reviewer }}",
                        "CANDIDATE_FILE": "${{ inputs.candidate-file }}",
                        "CANONICAL_FILE": "${{ inputs.canonical-file }}",
                        "RESULT_FILE": "${{ inputs.result-file }}",
                        "SCOPE_MANIFEST": "${{ inputs.scope-manifest }}",
                        "SELECTED_DIFF": "${{ inputs.selected-diff }}",
                        "DIFF_MODE": "${{ inputs.diff-mode }}",
                        "PREVIOUS_SHA": "${{ inputs.previous-sha }}",
                        "PREVIOUS_REVIEW_FILE": "${{ inputs.previous-review-file }}",
                    },
                    "run": (
                        'python3 "$GITHUB_ACTION_PATH/canonicalize_review.py" '
                        '--reviewer "$REVIEWER" '
                        '--candidate-file "$CANDIDATE_FILE" '
                        '--canonical-file "$CANONICAL_FILE" '
                        '--result-file "$RESULT_FILE" '
                        '--scope-manifest "$SCOPE_MANIFEST" '
                        '--selected-diff "$SELECTED_DIFF" '
                        '--diff-mode "$DIFF_MODE" '
                        '--previous-sha "$PREVIOUS_SHA" '
                        '--previous-review-file "$PREVIOUS_REVIEW_FILE" '
                        '--repository-root "$GITHUB_WORKSPACE" '
                        '--expected-repository "$GITHUB_REPOSITORY" '
                        '--github-output "$GITHUB_OUTPUT"'
                    ),
                }
            ],
        },
    }


def test_canonicalize_review_action_run_passes_quoted_environment_values_to_helper(
    tmp_path: Path,
) -> None:
    """Every action value is one inert argv entry, even when it resembles shell code."""
    document = yaml.load(
        CANONICALIZE_REVIEW_ACTION.read_text(encoding="utf-8"), Loader=yaml.BaseLoader
    )
    run = document["runs"]["steps"][0]["run"]
    action_path = tmp_path / "action"
    action_path.mkdir()
    captured = tmp_path / "argv.json"
    marker = tmp_path / "injection-ran"
    (action_path / "canonicalize_review.py").write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['ARGV_CAPTURE']).write_text(json.dumps(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "-- workspace ; δ"
    workspace.mkdir()
    github_output = tmp_path / "-- output ; δ"
    hostile = f'-- path ; $(touch {marker}) ; λ value'
    values = {
        "CANDIDATE_FILE": hostile + " candidate",
        "CANONICAL_FILE": hostile + " canonical",
        "RESULT_FILE": hostile + " result",
        "SCOPE_MANIFEST": hostile + " manifest",
        "SELECTED_DIFF": hostile + " diff",
        "PREVIOUS_REVIEW_FILE": hostile + " previous",
    }
    environment = {
        **os.environ,
        "ARGV_CAPTURE": str(captured),
        "GITHUB_ACTION_PATH": str(action_path),
        "GITHUB_REPOSITORY": "owner/repository ; λ",
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_OUTPUT": str(github_output),
        "REVIEWER": "claude",
        "DIFF_MODE": "full",
        "PREVIOUS_SHA": "-- sha ; λ value",
        **values,
    }

    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", run],
        env=environment,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert not marker.exists()
    assert json.loads(captured.read_text(encoding="utf-8")) == [
        "--reviewer", "claude",
        "--candidate-file", values["CANDIDATE_FILE"],
        "--canonical-file", values["CANONICAL_FILE"],
        "--result-file", values["RESULT_FILE"],
        "--scope-manifest", values["SCOPE_MANIFEST"],
        "--selected-diff", values["SELECTED_DIFF"],
        "--diff-mode", "full",
        "--previous-sha", "-- sha ; λ value",
        "--previous-review-file", values["PREVIOUS_REVIEW_FILE"],
        "--repository-root", str(workspace),
        "--expected-repository", "owner/repository ; λ",
        "--github-output", str(github_output),
    ]


def test_prepare_review_diff_composite_action_has_exact_safe_shell_contract() -> None:
    """Action inputs must cross into bash only through quoted environment values."""
    document = yaml.load(PREPARE_REVIEW_DIFF_ACTION.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)

    assert document == {
        "name": "Prepare review diff",
        "description": "Prepare a fail-closed full or incremental PR diff",
        "inputs": {
            "github-token": {"required": "true"},
            "pr-number": {"required": "true"},
            "previous-sha": {"required": "false", "default": ""},
            "previous-full-hash": {"required": "false", "default": ""},
            "force-full": {"required": "false", "default": "false"},
            "context-lines": {"required": "false", "default": "3"},
            "output-directory": {"required": "true"},
        },
        "outputs": {
            "diff-ready": {"value": "${{ steps.prepare.outputs.diff_ready }}"},
            "diff-mode": {"value": "${{ steps.prepare.outputs.diff_mode }}"},
            "head-sha": {"value": "${{ steps.prepare.outputs.head_sha }}"},
            "full-diff-sha256": {
                "value": "${{ steps.prepare.outputs.full_diff_sha256 }}"
            },
            "unchanged-since-previous": {
                "value": "${{ steps.prepare.outputs.unchanged_since_previous }}"
            },
        },
        "runs": {
            "using": "composite",
            "steps": [
                {
                    "id": "prepare",
                    "shell": "bash",
                    "env": {
                        "GH_TOKEN": "${{ inputs.github-token }}",
                        "PR_NUMBER": "${{ inputs.pr-number }}",
                        "PREVIOUS_SHA": "${{ inputs.previous-sha }}",
                        "PREVIOUS_FULL_HASH": "${{ inputs.previous-full-hash }}",
                        "FORCE_FULL": "${{ inputs.force-full }}",
                        "CONTEXT_LINES": "${{ inputs.context-lines }}",
                        "OUTPUT_DIRECTORY": "${{ inputs.output-directory }}",
                    },
                    "run": (
                        "set -euo pipefail\n"
                        "force_args=()\n"
                        "if [[ \"$FORCE_FULL\" == 'true' ]]; then\n"
                        "  force_args+=(--force-full)\n"
                        "elif [[ \"$FORCE_FULL\" != 'false' ]]; then\n"
                        "  echo 'force-full must be true or false' >&2\n"
                        "  exit 2\n"
                        "fi\n"
                        'python3 "$GITHUB_ACTION_PATH/prepare_review_diff.py" \\\n'
                        '  --repository "$GITHUB_REPOSITORY" \\\n'
                        '  --pr-number "$PR_NUMBER" \\\n'
                        '  --previous-sha "$PREVIOUS_SHA" \\\n'
                        '  --previous-full-hash "$PREVIOUS_FULL_HASH" \\\n'
                        '  --context-lines "$CONTEXT_LINES" \\\n'
                        '  --full-output "$OUTPUT_DIRECTORY/review-full.diff" \\\n'
                        '  --delta-output "$OUTPUT_DIRECTORY/review-delta.diff" \\\n'
                        '  --manifest-output "$OUTPUT_DIRECTORY/review-scope.json" \\\n'
                        '  --github-output "$GITHUB_OUTPUT" \\\n'
                        '  "${force_args[@]}"\n'
                    ),
                }
            ],
        },
    }


def test_prepare_review_diff_action_run_passes_quoted_environment_values_to_helper(
    tmp_path: Path,
) -> None:
    """Runner-style Bash receives one helper command with every fixed artifact flag."""
    document = yaml.load(
        PREPARE_REVIEW_DIFF_ACTION.read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    run = document["runs"]["steps"][0]["run"]
    action_path = tmp_path / "action"
    action_path.mkdir()
    captured = tmp_path / "argv.json"
    (action_path / "prepare_review_diff.py").write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['ARGV_CAPTURE']).write_text(json.dumps(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    github_output = tmp_path / "github-output"
    pr_number = "7; still-one-quoted-value"
    environment = {
        **os.environ,
        "ARGV_CAPTURE": str(captured),
        "GITHUB_ACTION_PATH": str(action_path),
        "GITHUB_REPOSITORY": "owner/repository",
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_OUTPUT": str(github_output),
        "PR_NUMBER": pr_number,
        "PREVIOUS_SHA": "a" * 40,
        "PREVIOUS_FULL_HASH": "b" * 64,
        "FORCE_FULL": "false",
        "CONTEXT_LINES": "20",
        "OUTPUT_DIRECTORY": str(runner_temp),
    }

    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", run],
        env=environment,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(captured.read_text(encoding="utf-8")) == [
        "--repository",
        "owner/repository",
        "--pr-number",
        pr_number,
        "--previous-sha",
        "a" * 40,
        "--previous-full-hash",
        "b" * 64,
        "--context-lines",
        "20",
        "--full-output",
        str(runner_temp / "review-full.diff"),
        "--delta-output",
        str(runner_temp / "review-delta.diff"),
        "--manifest-output",
        str(runner_temp / "review-scope.json"),
        "--github-output",
        str(github_output),
    ]


def test_prepare_review_diff_action_is_bundled_as_regular_release_files() -> None:
    """The helper and metadata travel at the same immutable automation commit."""
    assert ".github/actions/prepare-review-diff/action.yml" in EXACT_RELEASE_FILES
    assert ".github/actions/prepare-review-diff/prepare_review_diff.py" in EXACT_RELEASE_FILES


def test_canonicalize_review_action_is_bundled_as_regular_release_files() -> None:
    """The action and both helpers travel at one immutable automation commit."""
    assert {
        ".github/actions/canonicalize-review/action.yml",
        ".github/actions/canonicalize-review/canonicalize_review.py",
        ".github/actions/canonicalize-review/review_scope.py",
    } <= set(EXACT_RELEASE_FILES)


def test_canonicalizer_capability_boundary_is_closed() -> None:
    capability = getattr(
        release_inventory, "release_supports_canonicalize_review", None
    )
    assert callable(capability)
    assert capability("v1.45.2") is False
    assert capability("v1.46") is True
    canonicalizer_paths = {
        ".github/actions/canonicalize-review/action.yml",
        ".github/actions/canonicalize-review/canonicalize_review.py",
        ".github/actions/canonicalize-review/review_scope.py",
    }
    assert canonicalizer_paths <= {
        root.path.as_posix()
        for root in release_inventory.release_roots_for("v1.46")
    }
    assert canonicalizer_paths.isdisjoint(
        root.path.as_posix()
        for root in release_inventory.release_roots_for("v1.45.2")
    )


def test_review_invocation_budget_capability_boundary_is_closed() -> None:
    capability = getattr(
        release_inventory, "release_supports_review_invocation_budget", None
    )
    assert callable(capability)
    assert capability("v1.46.2") is False
    assert capability("v1.47") is True
    assert REVIEW_INVOCATION_BUDGET_RELEASE_FILES <= {
        root.path.as_posix()
        for root in release_inventory.release_roots_for("v1.47")
    }
    assert REVIEW_INVOCATION_BUDGET_RELEASE_FILES.isdisjoint(
        root.path.as_posix()
        for root in release_inventory.release_roots_for("v1.46.2")
    )
    budget_roots = tuple(
        root
        for root in release_inventory.release_roots_for("v1.47")
        if root.path.as_posix() in REVIEW_INVOCATION_BUDGET_RELEASE_FILES
    )
    assert {root.mode for root in budget_roots} == {"100644"}
    assert {root.kind for root in budget_roots} == {"file"}


def test_review_policy_release_boundary() -> None:
    capability = getattr(
        release_inventory, "release_supports_review_policy", None
    )
    assert callable(capability)
    assert capability("v1.50") is False
    assert capability("v1.51") is True
    paths = {
        root.path.as_posix()
        for root in release_inventory.release_roots_for("v1.51")
    }
    assert REVIEW_POLICY_RELEASE_FILES <= paths
    assert REVIEW_POLICY_RELEASE_FILES.isdisjoint(
        root.path.as_posix()
        for root in release_inventory.release_roots_for("v1.50")
    )
    policy_roots = tuple(
        root
        for root in release_inventory.release_roots_for("v1.51")
        if root.path.as_posix() in REVIEW_POLICY_RELEASE_FILES
    )
    assert {root.mode for root in policy_roots} == {"100644"}
    assert {root.kind for root in policy_roots} == {"file"}


def test_review_invocation_budget_action_has_exact_safe_contract() -> None:
    payload = REVIEW_INVOCATION_BUDGET_ACTION.read_bytes()
    document = yaml.load(payload, Loader=yaml.BaseLoader)

    assert hashlib.sha256(payload).hexdigest() == (
        "70b50ce482ff0e54df9fff88d5126cd8e760ed8bdabfefcc2f2ccdc639cb693b"
    )
    assert document == release_verifier.EXPECTED_REVIEW_INVOCATION_BUDGET_ACTION


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
    marker = tmp_path / "archive-local-provider-read"
    substituted = b'{"substituted": "LOCAL-PROVIDER-SECRET"}\n'
    helper = tmp_path / "archive-local-filter-helper"
    helper.write_text(
        "#!/bin/sh\n"
        f"/bin/cat {provider} > {marker}\n"
        "/bin/cat >/dev/null\n"
        f"/usr/bin/printf '%s' '{substituted.decode().strip()}'\n",
        encoding="utf-8",
    )
    helper.chmod(0o755)
    included = tmp_path / "archive-local-provider.gitconfig"
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


@pytest.fixture
def release_repo(tmp_path: Path) -> tuple[Path, str]:
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
    # 역사적 automation_ref 복원. test_verify_workflow_release 의
    # restore_historical_v140_manual_outputs 와 달리 config 값만 되돌린다 — 이 파일의
    # 태그(v1.40.2)는 manual-output contract 게이트(>=1.40.2) 대상이라 hardened 블록을
    # 유지해야 하기 때문이다(전체 v1.40 복원을 쓰면 검증이 실패한다).
    restore_historical_automation_ref(repo, "v1.40")
    # v1.40.2 predates the shared review action; use genuine committed v1.44
    # central workflow bytes rather than deleting dependencies from live workflows.
    restore_historical_review_workflows(repo)
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    release_commit = commit(repo, "release")
    git(repo, "tag", "-a", RELEASE_REF, "-m", RELEASE_REF)
    return repo, release_commit


def retag(repo: Path, ref: str, *, annotated: bool = True) -> str:
    release_commit = commit(repo, ref)
    args = ("tag", "-a", ref, "-m", ref) if annotated else ("tag", ref)
    git(repo, *args)
    return release_commit


def alternate_tag_object(repo: Path) -> str:
    (repo / "race-marker").write_text("alternate", encoding="utf-8")
    commit(repo, "alternate release")
    git(repo, "tag", "-a", "race-target", "-m", "race target")
    return git(repo, "rev-parse", "refs/tags/race-target")


def archive_with(member: tarfile.TarInfo, payload: bytes = b"bad") -> bytes:
    stream = BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        if member.isreg():
            member.size = len(payload)
            archive.addfile(member, BytesIO(payload))
        else:
            archive.addfile(member)
    return stream.getvalue()


def test_release_archive_uses_only_authenticated_tree_and_blob_reads(
    release_repo: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, release_commit = release_repo
    original = release_verifier.subprocess.run

    def authenticated_only(args, **kwargs):
        if args[0] == "/usr/bin/git":
            assert args[1:] == ["cat-file", "--batch"]
        return original(args, **kwargs)

    monkeypatch.setattr(release_verifier.subprocess, "run", authenticated_only)

    assert release_bundle._git_archive(repo, release_commit)


def test_latest_release_archive_default_includes_v151_release_files(
    release_repo: tuple[Path, str],
) -> None:
    repo, release_commit = release_repo

    archive = release_bundle._git_archive(repo, release_commit)

    with tarfile.open(fileobj=BytesIO(archive), mode="r:") as stream:
        names = set(stream.getnames())
    assert {
        ".github/actions/canonicalize-review/action.yml",
        ".github/actions/canonicalize-review/canonicalize_review.py",
        ".github/actions/canonicalize-review/review_scope.py",
    } | REVIEW_POLICY_RELEASE_FILES <= names


def test_v150_archive_preserves_historical_inventory_without_review_policy(
    release_repo: tuple[Path, str],
) -> None:
    repo, _ = release_repo
    for relative in REVIEW_POLICY_RELEASE_FILES:
        (repo / relative).unlink()
    historical_commit = commit(repo, "historical v1.50 without review policy")

    archive = release_bundle._git_archive(
        repo, historical_commit, ref="v1.50"
    )

    with tarfile.open(fileobj=BytesIO(archive), mode="r:") as stream:
        names = set(stream.getnames())
    assert REVIEW_POLICY_RELEASE_FILES.isdisjoint(names)


@pytest.mark.parametrize("mutation", ("missing", "executable"))
def test_v151_archive_requires_closed_review_policy_inventory(
    release_repo: tuple[Path, str], mutation: str
) -> None:
    repo, _ = release_repo
    action = repo / ".github/actions/resolve-review-policy/action.yml"
    if mutation == "missing":
        action.unlink()
    elif mutation == "executable":
        action.chmod(0o755)
    candidate = commit(repo, f"v1.51 review policy {mutation}")

    with pytest.raises(ReleaseVerificationError, match="archive verified release"):
        release_bundle._git_archive(repo, candidate, ref="v1.51")


def test_v151_release_listing_rejects_extra_review_policy_descendant(
    release_repo: tuple[Path, str],
) -> None:
    repo, release_commit = release_repo
    roots = release_inventory.release_roots_for("v1.51")
    tree = release_verifier.VerifiedCommitTree.open(repo, release_commit)
    listing = tree.listing(release_inventory.release_paths_for("v1.51"))
    listing += (
        b"100644 blob "
        + b"f" * 40
        + b"\t.github/actions/resolve-review-policy/unowned.py\0"
    )

    with pytest.raises(ValueError, match="invalid release tree listing"):
        release_inventory.validate_release_listing(listing, roots)


def test_release_archive_rejects_semantically_valid_blob_at_wrong_object_name(
    release_repo: tuple[Path, str],
) -> None:
    repo, release_commit = release_repo
    path = ".github/workflows/claude.yml"
    oid = git(repo, "rev-parse", f"{release_commit}:{path}")
    payload = raw_git_object(repo, "blob", oid) + b"\n# checksum mismatch\n"
    replace_loose_object_payload(repo, oid, "blob", payload)

    with pytest.raises(ReleaseVerificationError, match="archive verified release"):
        release_bundle._git_archive(repo, release_commit)


@pytest.mark.parametrize("layout", ("loose", "packed", "linked"))
def test_authenticated_release_archive_supports_normal_storage_layouts(
    release_repo: tuple[Path, str], tmp_path: Path, layout: str
) -> None:
    repo, release_commit = release_repo
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
        checkout = tmp_path / "linked-authenticated-archive"
        git(repo, "worktree", "add", "--detach", str(checkout), release_commit)

    assert release_bundle._git_archive(checkout, release_commit)


@pytest.mark.parametrize(
    "mutation",
    ("missing", "directory-collision", "executable-mode", "gitlink"),
)
@pytest.mark.parametrize("relative", V1462_RELEASE_FILES)
def test_release_archive_requires_each_exact_file_as_one_0644_blob(
    release_repo: tuple[Path, str], relative: str, mutation: str
) -> None:
    repo, release_commit = release_repo
    target = repo / relative
    if mutation == "missing":
        target.unlink()
        bad_commit = commit(repo, f"remove {relative}")
    elif mutation == "directory-collision":
        target.unlink()
        target.mkdir()
        (target / "dummy").write_text("not the release file\n", encoding="utf-8")
        bad_commit = commit(repo, f"replace {relative} with a directory")
    elif mutation == "executable-mode":
        target.chmod(0o755)
        bad_commit = commit(repo, f"make {relative} executable")
    else:
        target.unlink()
        git(repo, "add", "-u", "--", relative)
        git(
            repo,
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{release_commit},{relative}",
        )
        git(repo, "commit", "-qm", f"replace {relative} with a gitlink")
        bad_commit = git(repo, "rev-parse", "HEAD")

    with pytest.raises(ReleaseVerificationError, match="archive verified release"):
        release_bundle._git_archive(repo, bad_commit)


def test_release_archive_rejects_lexical_parent_descendant(
    release_repo: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, release_commit = release_repo
    oid = "f" * 40
    original = release_verifier.VerifiedCommitTree.listing

    def malicious_listing(
        tree: release_verifier.VerifiedCommitTree, paths: object
    ) -> bytes:
        return original(tree, paths) + (
            f"100644 blob {oid}\t.github/workflows/../escape\0".encode()
        )

    monkeypatch.setattr(
        release_verifier.VerifiedCommitTree, "listing", malicious_listing
    )

    with pytest.raises(ReleaseVerificationError, match="archive verified release"):
        release_bundle._git_archive(repo, release_commit)


def test_bundle_rejects_action_file_replaced_by_directory_and_dummy_blob(
    release_repo: tuple[Path, str],
) -> None:
    repo, _ = release_repo
    action = repo / ".github/actions/setup-gemini-auth/action.yml"
    action.unlink()
    action.mkdir()
    (action / "dummy").write_text("not a composite action\n", encoding="utf-8")
    retag(repo, "v1.41")

    with pytest.raises(ReleaseVerificationError, match="release inventory"):
        with materialize_release_bundle(repo, "v1.41", remote=None):
            pass


def test_v144_bundle_materializes_without_future_prepare_diff_action(
    release_repo: tuple[Path, str],
) -> None:
    """Version-aware extraction preserves an historical action inventory."""
    repo, _ = release_repo
    for relative in (
        ".github/actions/prepare-review-diff/action.yml",
        ".github/actions/prepare-review-diff/prepare_review_diff.py",
    ):
        (repo / relative).unlink()
    historical_commit = retag(repo, "v1.44")

    with materialize_release_bundle(repo, "v1.44", remote=None) as bundle:
        assert bundle.commit == historical_commit
        assert not (bundle.root / ".github/actions/prepare-review-diff").exists()


def test_release_archive_ignores_host_user_and_xdg_git_includes(
    release_repo: tuple[Path, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, release_commit = release_repo
    marker = tmp_path / "archive-host-config-command-ran"
    provider_token = tmp_path / "provider-token"
    provider_token.write_text("examples/** export-ignore\n", encoding="utf-8")
    helper = tmp_path / "host-command"
    helper.write_text(
        "#!/bin/sh\n"
        f"/bin/cat {provider_token} > {marker}\n"
        "exit 93\n",
        encoding="utf-8",
    )
    helper.chmod(0o755)
    included = tmp_path / "provider.gitconfig"
    included.write_text(
        f"[core]\n\tsshCommand = {helper}\n"
        f"\tattributesFile = {provider_token}\n"
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

    archive = release_bundle._git_archive(repo, release_commit)

    assert archive
    with tarfile.open(fileobj=BytesIO(archive), mode="r:") as stream:
        names = set(stream.getnames())
    assert "examples/baseline-workflows/.github/workflows/claude.yml" in names
    assert not marker.exists()


def test_release_archive_ignores_source_local_filter_and_info_attributes(
    release_repo: tuple[Path, str], tmp_path: Path
) -> None:
    repo, release_commit = release_repo
    target = "scripts/workflow-config.json"
    expected = subprocess.run(
        ["/usr/bin/git", "-C", str(repo), "show", f"{release_commit}:{target}"],
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    marker, substituted = install_local_release_filter_attack(
        repo, tmp_path, target=target
    )

    archive = release_bundle._git_archive(repo, release_commit)

    with tarfile.open(fileobj=BytesIO(archive), mode="r:") as stream:
        archived = stream.extractfile(target)
        assert archived is not None
        payload = archived.read()
    assert payload == expected
    assert payload != substituted
    assert not marker.exists()


def test_bundle_uses_original_commit_tree_despite_replace_ref(
    release_repo: tuple[Path, str],
) -> None:
    repo, release_commit = release_repo
    original = json.loads(
        git(repo, "show", f"{release_commit}:scripts/workflow-config.json")
    )
    config_path = repo / "scripts/workflow-config.json"
    replacement = json.loads(config_path.read_text(encoding="utf-8"))
    replacement["automation_ref"] = "v9.99"
    config_path.write_text(json.dumps(replacement) + "\n", encoding="utf-8")
    alternate = commit(repo, "replacement payload")
    git(repo, "replace", release_commit, alternate)

    with materialize_release_bundle(repo, RELEASE_REF, remote=None) as bundle:
        extracted = json.loads(
            (bundle.root / "scripts/workflow-config.json").read_text(encoding="utf-8")
        )
        assert bundle.commit == release_commit
        assert extracted == original


def test_release_archive_failure_does_not_expose_child_or_provider_data(
    release_repo: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _ = release_repo
    provider = "archive-provider-sentinel"
    raw = "archive-raw-child-sentinel"
    def child(_repo: Path, *_args: str) -> bytes:
        raise ReleaseVerificationError(f"{provider} {raw}")

    monkeypatch.setattr(release_verifier, "read_git_object", child)
    with pytest.raises(ReleaseVerificationError) as raised:
        release_bundle._git_archive(repo, raw)

    rendered = "".join(
        traceback.format_exception(
            type(raised.value), raised.value, raised.value.__traceback__
        )
    )
    assert provider not in rendered
    assert raw not in rendered
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_bundle_reads_catalog_config_and_canonical_tree_from_tag(
    release_repo: tuple[Path, str],
) -> None:
    repo, release_commit = release_repo
    config_path = repo / "scripts/workflow-config.json"
    changed = json.loads(config_path.read_text(encoding="utf-8"))
    changed["automation_ref"] = "v9.99"
    changed["repos"]["outside-tag"] = changed["repos"]["gstApp"]
    config_path.write_text(json.dumps(changed), encoding="utf-8")
    (repo / "examples/baseline-workflows/.github/workflows/claude.yml").unlink()
    commit(repo, "newer working tree")

    with materialize_release_bundle(repo, RELEASE_REF, remote=None) as bundle:
        extracted = bundle.root
        assert bundle.ref == RELEASE_REF
        assert bundle.commit == release_commit
        assert bundle.config.automation_ref == "v1.40"
        assert "outside-tag" not in bundle.config.profiles
        assert (bundle.canonical / "workflows/claude.yml").is_file()
        assert bundle.canonical.is_relative_to(bundle.root)

    assert not extracted.exists()


def test_bundle_rejects_lightweight_tag(release_repo: tuple[Path, str]) -> None:
    repo, _ = release_repo
    git(repo, "tag", "v1.41")
    with pytest.raises(ReleaseVerificationError, match="annotated tag"):
        with materialize_release_bundle(repo, "v1.41", remote=None):
            pass


def test_bundle_rejects_local_remote_tag_mismatch(
    release_repo: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, release_commit = release_repo
    original_tag = git(repo, "rev-parse", f"refs/tags/{RELEASE_REF}")
    git(
        repo,
        "remote",
        "add",
        "origin",
        "https://github.com/jhw7500/automation.git",
    )
    (repo / "new").write_text("new", encoding="utf-8")
    commit(repo, "new release")
    git(repo, "tag", "-d", RELEASE_REF)
    git(repo, "tag", "-a", RELEASE_REF, "-m", "local replacement")

    def remote_git(_url: str, *_refs: str) -> str:
        return (
            f"{original_tag}\trefs/tags/{RELEASE_REF}\n"
            f"{release_commit}\trefs/tags/{RELEASE_REF}^{{}}\n"
        )

    monkeypatch.setattr(release_verifier, "remote_git", remote_git)

    with pytest.raises(ReleaseVerificationError, match="remote tag.*expected commit"):
        with materialize_release_bundle(repo, RELEASE_REF, remote="origin"):
            pass


def test_bundle_rejects_tag_changed_during_verification(
    release_repo: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _ = release_repo
    alternate = alternate_tag_object(repo)
    original_read = release_verifier.read_git_object
    moved = False

    def racing_read(repository: Path, oid: str, expected_type: str) -> bytes:
        nonlocal moved
        if not moved and expected_type == "tree":
            git(repository, "update-ref", f"refs/tags/{RELEASE_REF}", alternate)
            moved = True
        return original_read(repository, oid, expected_type)

    monkeypatch.setattr(release_verifier, "read_git_object", racing_read)
    with pytest.raises(ReleaseVerificationError, match="changed during verification"):
        with materialize_release_bundle(repo, RELEASE_REF, remote=None):
            pass


def test_bundle_binds_content_and_archive_across_aba_tag_movement(
    release_repo: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, release_commit = release_repo
    original_tag = git(repo, "rev-parse", f"refs/tags/{RELEASE_REF}")
    alternate = alternate_tag_object(repo)
    original_read = release_verifier.read_git_object
    original_archive = release_bundle._git_archive
    movements = 0
    opened_revisions: list[str] = []
    archive_revisions: list[str] = []

    def racing_read(repository: Path, oid: str, expected_type: str) -> bytes:
        nonlocal movements
        if expected_type in {"tree", "blob"}:
            if movements == 0:
                git(repository, "update-ref", f"refs/tags/{RELEASE_REF}", alternate)
                movements = 1
            elif movements == 1:
                git(
                    repository,
                    "update-ref",
                    f"refs/tags/{RELEASE_REF}",
                    original_tag,
                )
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

    def capture_archive(
        automation: Path,
        revision: str,
        *,
        ref: str = "v1.46",
        tree: release_verifier.VerifiedCommitTree | None = None,
    ) -> bytes:
        archive_revisions.append(revision)
        assert tree is not None
        return original_archive(automation, revision, ref=ref, tree=tree)

    monkeypatch.setattr(release_verifier, "read_git_object", racing_read)
    monkeypatch.setattr(
        release_verifier.VerifiedCommitTree,
        "open",
        classmethod(capture_open),
    )
    monkeypatch.setattr(release_bundle, "_git_archive", capture_archive)
    with materialize_release_bundle(repo, RELEASE_REF, remote=None) as bundle:
        assert bundle.commit == release_commit
    assert movements == 2
    assert opened_revisions == [release_commit]
    assert archive_revisions == [release_commit]


def test_bundle_rejects_tag_change_before_context_completion(
    release_repo: tuple[Path, str]
) -> None:
    repo, _ = release_repo
    alternate = alternate_tag_object(repo)

    with pytest.raises(ReleaseVerificationError, match="changed during verification"):
        with materialize_release_bundle(repo, RELEASE_REF, remote=None):
            git(repo, "update-ref", f"refs/tags/{RELEASE_REF}", alternate)


def test_bundle_rejects_absent_canonical_path(
    release_repo: tuple[Path, str],
) -> None:
    repo, _ = release_repo
    (repo / "examples/baseline-workflows/.github/workflows/claude.yml").unlink()
    retag(repo, "v1.41")
    with pytest.raises(ReleaseVerificationError, match="canonical"):
        with materialize_release_bundle(repo, "v1.41", remote=None):
            pass


def test_bundle_rejects_profile_inventory_outside_tag(
    release_repo: tuple[Path, str],
) -> None:
    repo, _ = release_repo
    config_path = repo / "scripts/workflow-config.json"
    changed = json.loads(config_path.read_text(encoding="utf-8"))
    changed["repos"]["outside-tag"] = changed["repos"]["gstApp"]
    config_path.write_text(json.dumps(changed), encoding="utf-8")
    retag(repo, "v1.41")

    with pytest.raises(ReleaseVerificationError, match="repository set"):
        with materialize_release_bundle(repo, "v1.41", remote=None):
            pass


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("../escape", "file"),
        ("/absolute", "file"),
        ("not-release-owned/file", "file"),
        ("scripts/not-release-owned.txt", "file"),
        (".github/workflows/link", "symlink"),
        (".github/workflows/hardlink", "hardlink"),
    ],
    ids=(
        "parent",
        "absolute",
        "unexpected-top-level",
        "unexpected-release-sibling",
        "symlink",
        "hardlink",
    ),
)
def test_bundle_rejects_unsafe_archive_members(
    release_repo: tuple[Path, str],
    name: str,
    kind: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _ = release_repo
    member = tarfile.TarInfo(name)
    if kind == "symlink":
        member.type = tarfile.SYMTYPE
        member.linkname = "target"
    if kind == "hardlink":
        member.type = tarfile.LNKTYPE
        member.linkname = ".github/workflows/claude.yml"
    malicious = archive_with(member)
    monkeypatch.setattr(
        "scripts.workflow_release_bundle._git_archive",
        lambda automation, revision, **_kwargs: malicious,
    )

    with pytest.raises(ReleaseVerificationError, match="unsafe archive member"):
        with materialize_release_bundle(repo, RELEASE_REF, remote=None):
            pass
