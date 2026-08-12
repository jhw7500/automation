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
                  opencode-review:
                    permissions:
                      contents: read
                      pull-requests: write
                      issues: write
                    steps:
                      - name: Checkout repository
                        uses: actions/checkout@v4
                        with:
                          persist-credentials: true
                      - name: Run OpenCode PR review
                        env:
                          GITHUB_TOKEN: ${{ github.token }}
                        with:
                          use_github_token: true
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


if __name__ == "__main__":
    unittest.main()
