#!/usr/bin/env python3
"""Tests for verifying the exact reusable-workflow release artifact."""

from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.verify_workflow_release import ReleaseVerificationError, verify_release


class VerifyWorkflowReleaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name) / "repo"
        self.remote = Path(self.tempdir.name) / "remote.git"
        (self.repo / ".github/workflows").mkdir(parents=True)
        (self.repo / ".github/workflows/opencode-auto-review.yml").write_text(
            textwrap.dedent(
                """\
                on:
                  workflow_call:
                jobs:
                  check-enabled:
                    outputs:
                      safe_pr: ${{ steps.pr_scope.outputs.safe_pr }}
                    steps:
                      - id: pr_scope
                        env:
                          PR_NUMBER: ${{ inputs.pr_number || github.event.pull_request.number || github.event.issue.number }}
                        run: gh api example
                  opencode-review:
                    if: >-
                      needs.check-enabled.outputs.safe_pr == 'true'
                    permissions:
                      contents: read
                      pull-requests: write
                      issues: write
                    env:
                      OPENCODE_VERSION: '1.18.17'
                      OPENCODE_ARCHIVE_SHA256: '3f14a4c61c7f6b0d3b6d933d1d212e64e19683eba6fa453ad98e46303afe144a'
                    steps:
                      - name: Checkout repository
                        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
                        with:
                          persist-credentials: true
                      - name: Cache pinned OpenCode CLI archive
                        uses: actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9
                      - name: Install pinned OpenCode CLI
                        run: |
                          curl "releases/download/v${OPENCODE_VERSION}/opencode-linux-x64.tar.gz"
                          sha256sum --check -
                          "$install_dir/opencode" --version
                      - name: Run OpenCode PR review
                        run: opencode github run
                        env:
                          GITHUB_TOKEN: ${{ github.token }}
                          USE_GITHUB_TOKEN: 'true'
                          MODEL: zai-coding-plan/glm-4.7
                """
            )
        )
        (self.repo / ".github/workflows/opencode.yml").write_text(
            textwrap.dedent(
                """\
                on:
                  workflow_call:
                jobs:
                  check-enabled:
                    outputs:
                      safe_pr: ${{ steps.pr_scope.outputs.safe_pr }}
                    steps:
                      - id: pr_scope
                        env:
                          PR_NUMBER: ${{ github.event.pull_request.number || github.event.issue.number }}
                        run: gh api example
                  opencode:
                    if: needs.check-enabled.outputs.safe_pr == 'true'
                    permissions:
                      contents: read
                      pull-requests: write
                      issues: write
                    env:
                      OPENCODE_VERSION: '1.18.17'
                      OPENCODE_ARCHIVE_SHA256: '3f14a4c61c7f6b0d3b6d933d1d212e64e19683eba6fa453ad98e46303afe144a'
                    steps:
                      - name: Checkout repository
                        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
                        with:
                          persist-credentials: true
                      - name: Cache pinned OpenCode CLI archive
                        uses: actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9
                      - name: Install pinned OpenCode CLI
                        run: |
                          curl "releases/download/v${OPENCODE_VERSION}/opencode-linux-x64.tar.gz"
                          sha256sum --check -
                          "$install_dir/opencode" --version
                      - name: Run opencode
                        run: opencode github run
                        env:
                          GITHUB_TOKEN: ${{ github.token }}
                          USE_GITHUB_TOKEN: 'true'
                          MODEL: zai-coding-plan/glm-4.7
                """
            )
        )
        self.git("init", "-q")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.com")
        self.git("add", ".")
        self.git("commit", "-qm", "release")
        self.commit = self.git("rev-parse", "HEAD").strip()
        self.git("tag", "-a", "v1.35", "-m", "v1.35")
        subprocess.run(["git", "init", "--bare", "-q", str(self.remote)], check=True)
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "-q", "origin", "v1.35")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.repo), *args],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout

    def test_accepts_local_and_remote_tag_at_expected_secure_commit(self) -> None:
        verify_release(self.repo, "v1.35", self.commit, remote="origin")

    def test_rejects_tag_that_does_not_point_at_expected_commit(self) -> None:
        (self.repo / "new").write_text("new")
        self.git("add", "new")
        self.git("commit", "-qm", "new")
        new_commit = self.git("rev-parse", "HEAD").strip()
        with self.assertRaisesRegex(ReleaseVerificationError, "expected commit"):
            verify_release(self.repo, "v1.35", new_commit)

    def test_rejects_insecure_opencode_release_content(self) -> None:
        self.git("tag", "-d", "v1.35")
        path = self.repo / ".github/workflows/opencode-auto-review.yml"
        path.write_text(path.read_text().replace("contents: read", "id-token: write"))
        self.git("add", ".")
        self.git("commit", "-qm", "insecure")
        bad_commit = self.git("rev-parse", "HEAD").strip()
        self.git("tag", "-a", "v1.35", "-m", "bad")
        with self.assertRaisesRegex(ReleaseVerificationError, "permissions"):
            verify_release(self.repo, "v1.35", bad_commit)

    def test_rejects_opencode_release_with_contents_write(self) -> None:
        self.git("tag", "-d", "v1.35")
        path = self.repo / ".github/workflows/opencode-auto-review.yml"
        path.write_text(path.read_text().replace("contents: read", "contents: write"))
        self.git("add", ".")
        self.git("commit", "-qm", "write permission")
        bad_commit = self.git("rev-parse", "HEAD").strip()
        self.git("tag", "-a", "v1.35", "-m", "bad")
        with self.assertRaisesRegex(ReleaseVerificationError, "permissions"):
            verify_release(self.repo, "v1.35", bad_commit)

    def test_rejects_release_with_unpinned_checkout(self) -> None:
        self.git("tag", "-d", "v1.35")
        path = self.repo / ".github/workflows/opencode-auto-review.yml"
        path.write_text(
            path.read_text().replace(
                "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
                "actions/checkout@v7",
                1,
            )
        )
        self.git("add", ".")
        self.git("commit", "-qm", "unpin checkout")
        bad_commit = self.git("rev-parse", "HEAD").strip()
        self.git("tag", "-a", "v1.35", "-m", "bad")
        with self.assertRaisesRegex(ReleaseVerificationError, "checkout reference"):
            verify_release(self.repo, "v1.35", bad_commit)

    def test_rejects_release_with_opencode_version_drift(self) -> None:
        self.git("tag", "-d", "v1.35")
        path = self.repo / ".github/workflows/opencode-auto-review.yml"
        path.write_text(path.read_text().replace("1.18.17", "latest", 1))
        self.git("add", ".")
        self.git("commit", "-qm", "unpin opencode")
        bad_commit = self.git("rev-parse", "HEAD").strip()
        self.git("tag", "-a", "v1.35", "-m", "bad")
        with self.assertRaisesRegex(ReleaseVerificationError, "approved OpenCode CLI"):
            verify_release(self.repo, "v1.35", bad_commit)

    def test_rejects_release_with_opencode_digest_drift(self) -> None:
        self.git("tag", "-d", "v1.35")
        path = self.repo / ".github/workflows/opencode-auto-review.yml"
        path.write_text(
            path.read_text().replace(
                "3f14a4c61c7f6b0d3b6d933d1d212e64e19683eba6fa453ad98e46303afe144a",
                "0" * 64,
                1,
            )
        )
        self.git("add", ".")
        self.git("commit", "-qm", "change opencode digest")
        bad_commit = self.git("rev-parse", "HEAD").strip()
        self.git("tag", "-a", "v1.35", "-m", "bad")
        with self.assertRaisesRegex(ReleaseVerificationError, "approved OpenCode CLI"):
            verify_release(self.repo, "v1.35", bad_commit)

    def test_rejects_opencode_release_without_private_repo_fetch_auth(self) -> None:
        self.git("tag", "-d", "v1.35")
        path = self.repo / ".github/workflows/opencode-auto-review.yml"
        path.write_text(
            path.read_text().replace(
                "persist-credentials: true", "persist-credentials: false"
            )
        )
        self.git("add", ".")
        self.git("commit", "-qm", "break private fetch")
        bad_commit = self.git("rev-parse", "HEAD").strip()
        self.git("tag", "-a", "v1.35", "-m", "bad")
        with self.assertRaisesRegex(ReleaseVerificationError, "private repository fetch"):
            verify_release(self.repo, "v1.35", bad_commit)

    def test_rejects_opencode_command_without_private_repo_fetch_auth(self) -> None:
        self.git("tag", "-d", "v1.35")
        path = self.repo / ".github/workflows/opencode.yml"
        path.write_text(
            path.read_text().replace(
                "persist-credentials: true", "persist-credentials: false"
            )
        )
        self.git("add", ".")
        self.git("commit", "-qm", "break private command fetch")
        bad_commit = self.git("rev-parse", "HEAD").strip()
        self.git("tag", "-a", "v1.35", "-m", "bad")
        with self.assertRaisesRegex(ReleaseVerificationError, "opencode.yml.*private"):
            verify_release(self.repo, "v1.35", bad_commit)

    def test_rejects_auto_review_without_central_same_repo_guard(self) -> None:
        self.git("tag", "-d", "v1.35")
        path = self.repo / ".github/workflows/opencode-auto-review.yml"
        path.write_text(
            path.read_text().replace(
                "needs.check-enabled.outputs.safe_pr == 'true'",
                "true",
            )
        )
        self.git("add", ".")
        self.git("commit", "-qm", "remove same repo guard")
        bad_commit = self.git("rev-parse", "HEAD").strip()
        self.git("tag", "-a", "v1.35", "-m", "bad")
        with self.assertRaisesRegex(ReleaseVerificationError, "same-repository PR guard"):
            verify_release(self.repo, "v1.35", bad_commit)

    def test_rejects_opencode_command_oidc_app_token_path(self) -> None:
        self.git("tag", "-d", "v1.35")
        path = self.repo / ".github/workflows/opencode.yml"
        text = path.read_text().replace(
            "    permissions:\n      contents: read",
            "    permissions:\n      id-token: write\n      contents: read",
        )
        text = text.replace("USE_GITHUB_TOKEN: 'true'", "USE_GITHUB_TOKEN: 'false'")
        path.write_text(text)
        self.git("add", ".")
        self.git("commit", "-qm", "restore app token path")
        bad_commit = self.git("rev-parse", "HEAD").strip()
        self.git("tag", "-a", "v1.35", "-m", "bad")
        with self.assertRaisesRegex(ReleaseVerificationError, "opencode.yml"):
            verify_release(self.repo, "v1.35", bad_commit)

    def test_rejects_command_scope_without_inline_review_fallback(self) -> None:
        self.git("tag", "-d", "v1.35")
        path = self.repo / ".github/workflows/opencode.yml"
        path.write_text(
            path.read_text().replace(
                "github.event.pull_request.number || github.event.issue.number",
                "github.event.issue.number",
            )
        )
        self.git("add", ".")
        self.git("commit", "-qm", "break review comment scope")
        bad_commit = self.git("rev-parse", "HEAD").strip()
        self.git("tag", "-a", "v1.35", "-m", "bad")
        with self.assertRaisesRegex(ReleaseVerificationError, "opencode.yml security"):
            verify_release(self.repo, "v1.35", bad_commit)


if __name__ == "__main__":
    unittest.main()
