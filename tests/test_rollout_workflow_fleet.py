#!/usr/bin/env python3
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.rollout_workflow_fleet import (
    materialize_release_contract,
    publish_repository,
    rollout_branch,
    secret_source,
    sync_missing,
)


SECURE_WORKFLOW = """\
on:
  workflow_call:
jobs:
  opencode-review:
    permissions:
      contents: read
      pull-requests: write
      issues: write
    steps:
      - name: Run OpenCode PR review
        env:
          GITHUB_TOKEN: ${{ github.token }}
        with:
          use_github_token: true
"""


class RolloutWorkflowFleetTest(unittest.TestCase):
    def test_branch_is_derived_from_release_ref(self) -> None:
        self.assertEqual("codex/automation-v1.35-fleet", rollout_branch("v1.35"))
        self.assertEqual("codex/automation-v1.36-fleet", rollout_branch("v1.36"))
        self.assertNotEqual(rollout_branch("v1.35"), rollout_branch("v1.36"))

    def test_personal_oauth_source_requires_explicit_fanout_consent(self) -> None:
        with patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "test-token"}):
            self.assertIsNone(secret_source("CLAUDE_CODE_OAUTH_TOKEN", False))
            self.assertEqual(
                "test-token", secret_source("CLAUDE_CODE_OAUTH_TOKEN", True)
            )

    def test_sync_missing_passes_secret_via_stdin_and_never_argv(self) -> None:
        calls: list[tuple[list[str], str | None]] = []

        def fake_run(args: list[str], **kwargs: object) -> str:
            calls.append((args, kwargs.get("input_text")))  # type: ignore[arg-type]
            return ""

        with patch("scripts.rollout_workflow_fleet.remote_names", return_value=set()), patch(
            "scripts.rollout_workflow_fleet.secret_source", return_value="sensitive-value"
        ), patch("scripts.rollout_workflow_fleet.run", side_effect=fake_run):
            available, synced = sync_missing(
                "owner", "repo", {"TOKEN"}, True, False
            )
        self.assertEqual({"TOKEN"}, available)
        self.assertEqual(("TOKEN",), synced)
        self.assertEqual("sensitive-value", calls[0][1])
        self.assertNotIn("sensitive-value", calls[0][0])

    def test_release_contract_is_read_from_verified_tag_not_newer_head(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            remote = Path(temp) / "remote.git"
            workflow = repo / ".github/workflows/opencode-auto-review.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(SECURE_WORKFLOW)
            self.git(repo, "init", "-q")
            self.git(repo, "config", "user.name", "Test")
            self.git(repo, "config", "user.email", "test@example.com")
            self.git(repo, "add", ".")
            self.git(repo, "commit", "-qm", "v1.35 contract")
            tagged = self.git(repo, "rev-parse", "HEAD").strip()
            self.git(repo, "tag", "-a", "v1.35", "-m", "v1.35")
            subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
            self.git(repo, "remote", "add", "origin", str(remote))
            self.git(repo, "push", "-q", "origin", "v1.35")

            workflow.write_text(SECURE_WORKFLOW + "# newer untagged contract\n")
            self.git(repo, "add", ".")
            self.git(repo, "commit", "-qm", "newer head")

            extracted_temp, extracted, commit = materialize_release_contract(repo, "v1.35")
            try:
                text = (
                    extracted / ".github/workflows/opencode-auto-review.yml"
                ).read_text()
                self.assertEqual(tagged, commit)
                self.assertNotIn("newer untagged", text)
            finally:
                extracted_temp.cleanup()

    def test_publish_uses_release_specific_branch_for_push_and_pr(self) -> None:
        calls: list[list[str]] = []
        outputs = iter(["", "", "head-sha", "", "https://example.test/pr/1"])

        def fake_run(args: list[str], **_: object) -> str:
            calls.append(args)
            return next(outputs)

        with patch("scripts.rollout_workflow_fleet.run", side_effect=fake_run), patch(
            "scripts.rollout_workflow_fleet.gh_json", return_value=[]
        ):
            head, url = publish_repository(
                Path("/tmp/repo"),
                "owner",
                "repo",
                "main",
                "v1.36",
                "codex/automation-v1.36-fleet",
            )
        self.assertEqual("head-sha", head)
        self.assertEqual("https://example.test/pr/1", url)
        flattened = [item for command in calls for item in command]
        self.assertIn("codex/automation-v1.36-fleet", flattened)
        self.assertNotIn("codex/automation-v1.35-fleet", flattened)

    @staticmethod
    def git(repo: Path, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout


if __name__ == "__main__":
    unittest.main()
