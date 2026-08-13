"""Tests for verifying the exact reusable-workflow release artifact."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]

from scripts.verify_workflow_release import ReleaseVerificationError, verify_release


RELEASE_PATHS = (
    ".github/workflows",
    ".github/actions/setup-gemini-auth/action.yml",
    "examples/baseline-workflows/.github",
    "scripts/workflow-catalog.json",
    "scripts/workflow-config.json",
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


def replace(path: Path, old: str, new: str, *, count: int = -1) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, count), encoding="utf-8")


def retag_bad_release(repo: Path, message: str) -> str:
    git(repo, "tag", "-d", "v1.40")
    bad_commit = commit(repo, message)
    git(repo, "tag", "-a", "v1.40", "-m", message)
    return bad_commit


def test_accepts_local_and_remote_annotated_tag_at_secure_commit(
    release_repo: tuple[Path, Path, str],
) -> None:
    repo, _, release_commit = release_repo
    assert verify_release(repo, "v1.40", release_commit, remote="origin") == release_commit


def test_rejects_lightweight_release_tag(
    release_repo: tuple[Path, Path, str],
) -> None:
    repo, _, release_commit = release_repo
    git(repo, "tag", "v1.41")
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
    ],
    ids=(
        "google-api-key",
        "oidc-permission",
        "ambient-app-id",
        "unpinned-setup-auth",
        "missing-explicit-mode",
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
