#!/usr/bin/env python3
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.rollout_workflow_fleet import (
    CommandError,
    default_branch,
    main,
    materialize_release_contract,
    prepare_with_prerequisites,
    publish_repository,
    rollout_branch,
    secret_source,
    sync_missing,
)
from scripts.prepare_workflow_rollout import (
    RolloutError,
    RolloutResult,
    SecretPrerequisiteError,
)


SECURE_WORKFLOW = """\
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

SECURE_COMMAND_WORKFLOW = """\
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
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4
        with:
          persist-credentials: true
      - name: Run opencode
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

    def test_prepare_mode_rejects_any_secret_sync_request_before_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch(
            "scripts.rollout_workflow_fleet.materialize_release_contract"
        ) as materialize:
            with self.assertRaises(SystemExit):
                main(
                    [
                        "--workspace",
                        temp,
                        "--mode",
                        "prepare",
                        "--sync-missing-secrets",
                    ]
                )
            materialize.assert_not_called()

    def test_personal_oauth_source_requires_explicit_fanout_consent(self) -> None:
        with patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "test-token"}):
            self.assertIsNone(secret_source("CLAUDE_CODE_OAUTH_TOKEN", False))
            self.assertEqual(
                "test-token", secret_source("CLAUDE_CODE_OAUTH_TOKEN", True)
            )

    def test_ambient_environment_secret_requires_name_allowlist(self) -> None:
        with patch.dict(os.environ, {"GEMINI_API_KEY": "personal-key"}):
            self.assertIsNone(secret_source("GEMINI_API_KEY", False, set()))
            self.assertEqual(
                "personal-key",
                secret_source("GEMINI_API_KEY", False, {"GEMINI_API_KEY"}),
            )

    def test_refresh_secret_requires_publish_sync_and_exact_source_allowlist(self) -> None:
        cases = [
            ["--mode", "plan", "--refresh-secret", "ZHIPU_API_KEY"],
            ["--mode", "publish", "--confirm", "--refresh-secret", "ZHIPU_API_KEY"],
            [
                "--mode",
                "publish",
                "--confirm",
                "--sync-missing-secrets",
                "--refresh-secret",
                "ZHIPU_API_KEY",
            ],
        ]
        with tempfile.TemporaryDirectory() as temp, patch(
            "scripts.rollout_workflow_fleet.materialize_release_contract"
        ) as materialize:
            for extra in cases:
                with self.subTest(extra=extra), self.assertRaises(SystemExit):
                    main(["--workspace", temp, *extra])
            materialize.assert_not_called()

    def test_refresh_secret_requires_an_explicit_repository_before_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / ".automation-fleet-workspace").write_text("managed\n")
            config = root / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "gh_owner": "owner",
                        "repos": {"repo": {"workflows": True, "secrets": True}},
                    }
                )
            )
            with patch(
                "scripts.rollout_workflow_fleet.materialize_release_contract"
            ) as materialize, self.assertRaises(SystemExit):
                main(
                    [
                        "--automation",
                        str(ROOT),
                        "--config",
                        str(config),
                        "--workspace",
                        str(workspace),
                        "--mode",
                        "publish",
                        "--confirm",
                        "--sync-missing-secrets",
                        "--allow-env-secret",
                        "ZHIPU_API_KEY",
                        "--refresh-secret",
                        "ZHIPU_API_KEY",
                    ]
                )
            materialize.assert_not_called()

    def test_refresh_secret_requires_an_exact_allowed_source_before_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / ".automation-fleet-workspace").write_text("managed\n")
            config = root / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "gh_owner": "owner",
                        "repos": {"repo": {"workflows": True, "secrets": True}},
                    }
                )
            )
            with patch(
                "scripts.rollout_workflow_fleet.materialize_release_contract"
            ) as materialize, self.assertRaises(SystemExit):
                main(
                    [
                        "--automation",
                        str(ROOT),
                        "--config",
                        str(config),
                        "--workspace",
                        str(workspace),
                        "--mode",
                        "publish",
                        "--confirm",
                        "--sync-missing-secrets",
                        "--refresh-secret",
                        "ZHIPU_API_KEY",
                        "--repo",
                        "repo",
                    ]
                )
            materialize.assert_not_called()

    def test_refresh_secret_overwrites_existing_value_via_stdin_only(self) -> None:
        calls: list[tuple[list[str], str | None]] = []

        def fake_run(args: list[str], **kwargs: object) -> str:
            calls.append((args, kwargs.get("input_text")))  # type: ignore[arg-type]
            return ""

        with patch.dict(os.environ, {"ZHIPU_API_KEY": "rotated-value"}), patch(
            "scripts.rollout_workflow_fleet.run", side_effect=fake_run
        ):
            from scripts.rollout_workflow_fleet import refresh_secrets

            refreshed = refresh_secrets(
                "owner",
                "repo",
                {"ZHIPU_API_KEY"},
                False,
                {"ZHIPU_API_KEY"},
            )
        self.assertEqual(("ZHIPU_API_KEY",), refreshed)
        self.assertEqual("rotated-value", calls[0][1])
        self.assertNotIn("rotated-value", calls[0][0])

    def test_refresh_secret_reports_partial_writes_when_a_later_source_is_missing(self) -> None:
        completed: list[str] = []
        with patch(
            "scripts.rollout_workflow_fleet.secret_source",
            side_effect=["first-value", None],
        ), patch("scripts.rollout_workflow_fleet.run") as run:
            from scripts.rollout_workflow_fleet import refresh_secrets

            with self.assertRaisesRegex(RolloutError, "no explicitly allowed source"):
                refresh_secrets(
                    "owner",
                    "repo",
                    {"FIRST", "SECOND"},
                    False,
                    {"FIRST", "SECOND"},
                    completed,
                )
        self.assertEqual(["FIRST"], completed)
        run.assert_called_once()

    def test_missing_default_branch_becomes_a_command_error(self) -> None:
        with patch(
            "scripts.rollout_workflow_fleet.gh_json",
            return_value={"defaultBranchRef": None},
        ):
            with self.assertRaisesRegex(CommandError, "default branch is unavailable"):
                default_branch("owner", "empty-repo")

    def test_sync_missing_passes_secret_via_stdin_and_never_argv(self) -> None:
        calls: list[tuple[list[str], str | None]] = []

        def fake_run(args: list[str], **kwargs: object) -> str:
            calls.append((args, kwargs.get("input_text")))  # type: ignore[arg-type]
            return ""

        with patch("scripts.rollout_workflow_fleet.remote_names", return_value=set()), patch(
            "scripts.rollout_workflow_fleet.secret_source", return_value="sensitive-value"
        ), patch("scripts.rollout_workflow_fleet.run", side_effect=fake_run):
            available, synced = sync_missing(
                "owner", "repo", {"TOKEN"}, True, False, {"TOKEN"}
            )
        self.assertEqual({"TOKEN"}, available)
        self.assertEqual(("TOKEN",), synced)
        self.assertEqual("sensitive-value", calls[0][1])
        self.assertNotIn("sensitive-value", calls[0][0])

    def test_sync_missing_reports_partial_writes_when_a_later_write_fails(self) -> None:
        completed: list[str] = []
        with patch(
            "scripts.rollout_workflow_fleet.remote_names", return_value=set()
        ), patch(
            "scripts.rollout_workflow_fleet.secret_source", return_value="value"
        ), patch(
            "scripts.rollout_workflow_fleet.run",
            side_effect=["", CommandError("second write failed")],
        ):
            with self.assertRaisesRegex(CommandError, "second write failed"):
                sync_missing(
                    "owner",
                    "repo",
                    {"FIRST", "SECOND"},
                    True,
                    False,
                    {"FIRST", "SECOND"},
                    completed,
                )
        self.assertEqual(["FIRST"], completed)

    def test_multiple_missing_secrets_are_synced_until_prepare_succeeds(self) -> None:
        first = SecretPrerequisiteError("missing first", {"FIRST_TOKEN"})
        second = SecretPrerequisiteError("missing second", {"SECOND_TOKEN"})
        prepared = object()
        with patch(
            "scripts.rollout_workflow_fleet.remote_names",
            side_effect=[set(), set()],
        ), patch(
            "scripts.rollout_workflow_fleet.prepare_repository",
            side_effect=[first, second, prepared],
        ), patch(
            "scripts.rollout_workflow_fleet.sync_missing",
            side_effect=[
                ({"FIRST_TOKEN"}, ("FIRST_TOKEN",)),
                ({"FIRST_TOKEN", "SECOND_TOKEN"}, ("SECOND_TOKEN",)),
            ],
        ) as sync:
            result, synced = prepare_with_prerequisites(
                Path("/tmp/repo"),
                Path("/tmp/automation"),
                "v1.35",
                "owner",
                "repo",
                True,
                False,
                {"FIRST_TOKEN", "SECOND_TOKEN"},
            )
        self.assertIs(prepared, result)
        self.assertEqual(("FIRST_TOKEN", "SECOND_TOKEN"), synced)
        self.assertEqual(2, sync.call_count)

    def test_deferred_refresh_secret_is_available_to_prepare_without_early_write(self) -> None:
        prepared = object()
        completed: list[str] = []
        with patch(
            "scripts.rollout_workflow_fleet.remote_names",
            side_effect=[set(), set()],
        ), patch(
            "scripts.rollout_workflow_fleet.prepare_repository",
            return_value=prepared,
        ) as prepare, patch(
            "scripts.rollout_workflow_fleet.sync_missing"
        ) as sync:
            result, synced = prepare_with_prerequisites(
                Path("/tmp/repo"),
                Path("/tmp/automation"),
                "v1.35",
                "owner",
                "repo",
                True,
                False,
                {"TOKEN"},
                {"TOKEN"},
                completed,
            )
        self.assertIs(prepared, result)
        self.assertEqual((), synced)
        self.assertEqual({"TOKEN"}, prepare.call_args.args[3])
        sync.assert_not_called()

    def test_release_contract_is_read_from_verified_tag_not_newer_head(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            remote = Path(temp) / "remote.git"
            workflow = repo / ".github/workflows/opencode-auto-review.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(SECURE_WORKFLOW)
            (workflow.parent / "opencode.yml").write_text(SECURE_COMMAND_WORKFLOW)
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

    def test_release_contract_rejects_non_version_tag(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            workflow = repo / ".github/workflows/opencode-auto-review.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(SECURE_WORKFLOW)
            (workflow.parent / "opencode.yml").write_text(SECURE_COMMAND_WORKFLOW)
            self.git(repo, "init", "-q")
            self.git(repo, "config", "user.name", "Test")
            self.git(repo, "config", "user.email", "test@example.com")
            self.git(repo, "add", ".")
            self.git(repo, "commit", "-qm", "contract")
            self.git(repo, "tag", "release-candidate")
            with self.assertRaisesRegex(RuntimeError, "invalid release ref"):
                materialize_release_contract(repo, "release-candidate")

    def test_publish_uses_release_specific_branch_for_push_and_pr(self) -> None:
        calls: list[list[str]] = []
        outputs = iter(
            [
                "",
                "",
                "head-sha",
                "remote-sha\trefs/heads/codex/automation-v1.36-fleet",
                "",
                "https://example.test/pr/1",
            ]
        )

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
        self.assertIn(
            "--force-with-lease=refs/heads/codex/automation-v1.36-fleet:remote-sha",
            flattened,
        )

    def test_main_publishes_one_repo_contains_next_failure_and_writes_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / ".automation-fleet-workspace").write_text("managed\n")
            config = root / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "gh_owner": "owner",
                        "repos": {
                            "a-ready": {"workflows": True, "secrets": True},
                            "b-broken": {"workflows": True, "secrets": True},
                        },
                    }
                )
            )
            release_temp = Mock()
            prepared = RolloutResult(
                callers=1,
                changed_files=(Path(".github/workflows/caller.yml"),),
                required_secrets=frozenset({"TOKEN"}),
            )
            with patch(
                "scripts.rollout_workflow_fleet.materialize_release_contract",
                return_value=(release_temp, Path("/contract"), "release-sha"),
            ), patch(
                "scripts.rollout_workflow_fleet.default_branch",
                side_effect=["main", ValueError("unexpected metadata shape")],
            ), patch(
                "scripts.rollout_workflow_fleet.clone_or_reset",
                return_value=(Path("/clone/a-ready"), "base-sha"),
            ), patch(
                "scripts.rollout_workflow_fleet.prepare_with_prerequisites",
                return_value=(prepared, ()),
            ), patch(
                "scripts.rollout_workflow_fleet.validate_repository"
            ), patch(
                "scripts.rollout_workflow_fleet.publish_repository",
                return_value=("head-sha", "https://example.test/pr/1"),
            ):
                rc = main(
                    [
                        "--automation",
                        str(ROOT),
                        "--config",
                        str(config),
                        "--workspace",
                        str(workspace),
                        "--mode",
                        "publish",
                        "--confirm",
                    ]
                )
            self.assertEqual(1, rc)
            manifest = json.loads((workspace / "rollout-manifest.json").read_text())
            self.assertEqual(["published", "blocked"], [item["status"] for item in manifest])
            self.assertEqual("https://example.test/pr/1", manifest[0]["pr_url"])
            self.assertIn("unexpected ValueError", manifest[1]["detail"])
            release_temp.cleanup.assert_called_once()

    def test_main_refreshes_existing_secret_before_current_contract_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / ".automation-fleet-workspace").write_text("managed\n")
            config = root / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "gh_owner": "owner",
                        "repos": {"repo": {"workflows": True, "secrets": True}},
                    }
                )
            )
            release_temp = Mock()
            current = RolloutResult(1, (), frozenset({"ZHIPU_API_KEY"}))

            def complete_refresh(*args: object) -> tuple[str, ...]:
                completed = args[-1]
                self.assertIsInstance(completed, list)
                completed.append("ZHIPU_API_KEY")  # type: ignore[union-attr]
                return ("ZHIPU_API_KEY",)

            with patch(
                "scripts.rollout_workflow_fleet.materialize_release_contract",
                return_value=(release_temp, Path("/contract"), "release-sha"),
            ), patch(
                "scripts.rollout_workflow_fleet.refresh_secrets",
                side_effect=complete_refresh,
            ) as refresh, patch(
                "scripts.rollout_workflow_fleet.default_branch", return_value="main"
            ), patch(
                "scripts.rollout_workflow_fleet.clone_or_reset",
                return_value=(Path("/clone/repo"), "base-sha"),
            ), patch(
                "scripts.rollout_workflow_fleet.prepare_with_prerequisites",
                return_value=(current, ()),
            ):
                rc = main(
                    [
                        "--automation",
                        str(ROOT),
                        "--config",
                        str(config),
                        "--workspace",
                        str(workspace),
                        "--mode",
                        "publish",
                        "--confirm",
                        "--sync-missing-secrets",
                        "--allow-env-secret",
                        "ZHIPU_API_KEY",
                        "--refresh-secret",
                        "ZHIPU_API_KEY",
                        "--repo",
                        "repo",
                    ]
                )
            self.assertEqual(0, rc)
            refresh.assert_called_once()
            manifest = json.loads((workspace / "rollout-manifest.json").read_text())
            self.assertEqual("current", manifest[0]["status"])
            self.assertEqual(["ZHIPU_API_KEY"], manifest[0]["synced_secrets"])
            release_temp.cleanup.assert_called_once()

    def test_main_blocks_refresh_when_secret_writes_are_disabled_by_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / ".automation-fleet-workspace").write_text("managed\n")
            config = root / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "gh_owner": "owner",
                        "repos": {"repo": {"workflows": True, "secrets": False}},
                    }
                )
            )
            release_temp = Mock()
            with patch(
                "scripts.rollout_workflow_fleet.materialize_release_contract",
                return_value=(release_temp, Path("/contract"), "release-sha"),
            ), patch("scripts.rollout_workflow_fleet.refresh_secrets") as refresh:
                rc = main(
                    [
                        "--automation",
                        str(ROOT),
                        "--config",
                        str(config),
                        "--workspace",
                        str(workspace),
                        "--mode",
                        "publish",
                        "--confirm",
                        "--sync-missing-secrets",
                        "--allow-env-secret",
                        "ZHIPU_API_KEY",
                        "--refresh-secret",
                        "ZHIPU_API_KEY",
                        "--repo",
                        "repo",
                    ]
                )
            self.assertEqual(1, rc)
            refresh.assert_not_called()
            manifest = json.loads((workspace / "rollout-manifest.json").read_text())
            self.assertEqual("blocked", manifest[0]["status"])
            self.assertIn("secret writes are disabled", manifest[0]["detail"])
            self.assertEqual([], manifest[0]["synced_secrets"])
            release_temp.cleanup.assert_called_once()

    def test_main_skips_refresh_when_workflows_are_disabled_by_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / ".automation-fleet-workspace").write_text("managed\n")
            config = root / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "gh_owner": "owner",
                        "repos": {"repo": {"workflows": False, "secrets": True}},
                    }
                )
            )
            release_temp = Mock()
            with patch(
                "scripts.rollout_workflow_fleet.materialize_release_contract",
                return_value=(release_temp, Path("/contract"), "release-sha"),
            ), patch("scripts.rollout_workflow_fleet.refresh_secrets") as refresh:
                rc = main(
                    [
                        "--automation",
                        str(ROOT),
                        "--config",
                        str(config),
                        "--workspace",
                        str(workspace),
                        "--mode",
                        "publish",
                        "--confirm",
                        "--sync-missing-secrets",
                        "--allow-env-secret",
                        "ZHIPU_API_KEY",
                        "--refresh-secret",
                        "ZHIPU_API_KEY",
                        "--repo",
                        "repo",
                    ]
                )
            self.assertEqual(0, rc)
            refresh.assert_not_called()
            manifest = json.loads((workspace / "rollout-manifest.json").read_text())
            self.assertEqual("skipped", manifest[0]["status"])
            self.assertEqual([], manifest[0]["synced_secrets"])
            release_temp.cleanup.assert_called_once()

    def test_main_does_not_refresh_secret_when_repository_metadata_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / ".automation-fleet-workspace").write_text("managed\n")
            config = root / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "gh_owner": "owner",
                        "repos": {"repo": {"workflows": True, "secrets": True}},
                    }
                )
            )
            release_temp = Mock()

            with patch(
                "scripts.rollout_workflow_fleet.materialize_release_contract",
                return_value=(release_temp, Path("/contract"), "release-sha"),
            ), patch(
                "scripts.rollout_workflow_fleet.refresh_secrets",
            ) as refresh, patch(
                "scripts.rollout_workflow_fleet.default_branch",
                side_effect=CommandError("metadata unavailable"),
            ):
                rc = main(
                    [
                        "--automation",
                        str(ROOT),
                        "--config",
                        str(config),
                        "--workspace",
                        str(workspace),
                        "--mode",
                        "publish",
                        "--confirm",
                        "--sync-missing-secrets",
                        "--allow-env-secret",
                        "ZHIPU_API_KEY",
                        "--refresh-secret",
                        "ZHIPU_API_KEY",
                        "--repo",
                        "repo",
                    ]
                )
            self.assertEqual(1, rc)
            manifest = json.loads((workspace / "rollout-manifest.json").read_text())
            self.assertEqual("blocked", manifest[0]["status"])
            self.assertEqual([], manifest[0]["synced_secrets"])
            refresh.assert_not_called()
            release_temp.cleanup.assert_called_once()

    def test_main_records_refresh_when_publish_is_blocked_after_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / ".automation-fleet-workspace").write_text("managed\n")
            config = root / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "gh_owner": "owner",
                        "repos": {"repo": {"workflows": True, "secrets": True}},
                    }
                )
            )
            release_temp = Mock()
            changed = RolloutResult(
                1,
                (Path(".github/workflows/opencode.yml"),),
                frozenset({"ZHIPU_API_KEY"}),
            )

            def complete_refresh(*args: object) -> tuple[str, ...]:
                completed = args[-1]
                self.assertIsInstance(completed, list)
                completed.append("ZHIPU_API_KEY")  # type: ignore[union-attr]
                return ("ZHIPU_API_KEY",)

            with patch(
                "scripts.rollout_workflow_fleet.materialize_release_contract",
                return_value=(release_temp, Path("/contract"), "release-sha"),
            ), patch(
                "scripts.rollout_workflow_fleet.default_branch", return_value="main"
            ), patch(
                "scripts.rollout_workflow_fleet.clone_or_reset",
                return_value=(Path("/clone/repo"), "base-sha"),
            ), patch(
                "scripts.rollout_workflow_fleet.prepare_with_prerequisites",
                return_value=(changed, ()),
            ), patch(
                "scripts.rollout_workflow_fleet.validate_repository"
            ) as validate, patch(
                "scripts.rollout_workflow_fleet.refresh_secrets",
                side_effect=complete_refresh,
            ), patch(
                "scripts.rollout_workflow_fleet.publish_repository",
                side_effect=CommandError("push failed"),
            ):
                rc = main(
                    [
                        "--automation",
                        str(ROOT),
                        "--config",
                        str(config),
                        "--workspace",
                        str(workspace),
                        "--mode",
                        "publish",
                        "--confirm",
                        "--sync-missing-secrets",
                        "--allow-env-secret",
                        "ZHIPU_API_KEY",
                        "--refresh-secret",
                        "ZHIPU_API_KEY",
                        "--repo",
                        "repo",
                    ]
                )
            self.assertEqual(1, rc)
            validate.assert_called_once()
            manifest = json.loads((workspace / "rollout-manifest.json").read_text())
            self.assertEqual("blocked", manifest[0]["status"])
            self.assertEqual(["ZHIPU_API_KEY"], manifest[0]["synced_secrets"])
            release_temp.cleanup.assert_called_once()

    def test_main_blocks_refresh_name_not_required_by_managed_callers(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / ".automation-fleet-workspace").write_text("managed\n")
            config = root / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "gh_owner": "owner",
                        "repos": {"repo": {"workflows": True, "secrets": True}},
                    }
                )
            )
            release_temp = Mock()
            current = RolloutResult(1, (), frozenset())
            with patch(
                "scripts.rollout_workflow_fleet.materialize_release_contract",
                return_value=(release_temp, Path("/contract"), "release-sha"),
            ), patch(
                "scripts.rollout_workflow_fleet.default_branch", return_value="main"
            ), patch(
                "scripts.rollout_workflow_fleet.clone_or_reset",
                return_value=(Path("/clone/repo"), "base-sha"),
            ), patch(
                "scripts.rollout_workflow_fleet.prepare_with_prerequisites",
                return_value=(current, ()),
            ), patch(
                "scripts.rollout_workflow_fleet.refresh_secrets"
            ) as refresh:
                rc = main(
                    [
                        "--automation",
                        str(ROOT),
                        "--config",
                        str(config),
                        "--workspace",
                        str(workspace),
                        "--mode",
                        "publish",
                        "--confirm",
                        "--sync-missing-secrets",
                        "--allow-env-secret",
                        "ZHIPU_API_KEY",
                        "--refresh-secret",
                        "ZHIPU_API_KEY",
                        "--repo",
                        "repo",
                    ]
                )
            self.assertEqual(1, rc)
            refresh.assert_not_called()
            manifest = json.loads((workspace / "rollout-manifest.json").read_text())
            self.assertEqual("blocked", manifest[0]["status"])
            self.assertIn("not required by managed callers", manifest[0]["detail"])

    def test_main_records_partially_synced_secret_when_prepare_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / ".automation-fleet-workspace").write_text("managed\n")
            config = root / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "gh_owner": "owner",
                        "repos": {"repo": {"workflows": True, "secrets": True}},
                    }
                )
            )
            release_temp = Mock()

            def fail_after_sync(*args: object) -> object:
                completed = args[-1]
                self.assertIsInstance(completed, list)
                completed.append("FIRST")  # type: ignore[union-attr]
                raise CommandError("second write failed")

            with patch(
                "scripts.rollout_workflow_fleet.materialize_release_contract",
                return_value=(release_temp, Path("/contract"), "release-sha"),
            ), patch(
                "scripts.rollout_workflow_fleet.default_branch", return_value="main"
            ), patch(
                "scripts.rollout_workflow_fleet.clone_or_reset",
                return_value=(Path("/clone/repo"), "base-sha"),
            ), patch(
                "scripts.rollout_workflow_fleet.prepare_with_prerequisites",
                side_effect=fail_after_sync,
            ):
                rc = main(
                    [
                        "--automation",
                        str(ROOT),
                        "--config",
                        str(config),
                        "--workspace",
                        str(workspace),
                        "--mode",
                        "publish",
                        "--confirm",
                        "--sync-missing-secrets",
                    ]
                )
            self.assertEqual(1, rc)
            manifest = json.loads((workspace / "rollout-manifest.json").read_text())
            self.assertEqual("blocked", manifest[0]["status"])
            self.assertEqual(["FIRST"], manifest[0]["synced_secrets"])
            release_temp.cleanup.assert_called_once()

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
