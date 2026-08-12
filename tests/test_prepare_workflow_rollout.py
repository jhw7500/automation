#!/usr/bin/env python3
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.prepare_workflow_rollout import RolloutError, prepare_repository


class PrepareWorkflowRolloutTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.automation = self.root / "automation"
        wf = self.automation / ".github/workflows"
        wf.mkdir(parents=True)
        (wf / "claude.yml").write_text(textwrap.dedent("""\
            on:
              workflow_call:
                secrets:
                  CLAUDE_CODE_OAUTH_TOKEN:
                    required: true
            jobs: {}
        """))
        (wf / "gemini.yml").write_text(textwrap.dedent("""\
            on:
              workflow_call:
                inputs:
                  app_id:
                    type: string
                    required: false
                secrets:
                  APP_PRIVATE_KEY:
                    required: false
                  GEMINI_API_KEY:
                    required: false
                  GOOGLE_API_KEY:
                    required: false
            jobs: {}
        """))
        (wf / "notice.yml").write_text(textwrap.dedent("""\
            on:
              workflow_call:
            jobs: {}
        """))
        self.repo = self.root / "consumer"
        (self.repo / ".github/workflows").mkdir(parents=True)
        (self.repo / ".github/workflow-config.yml").write_text(
            "automation_ref: v1.33\nworkflows:\n  custom:\n    enabled: true\n"
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, name: str, body: str) -> Path:
        p = self.repo / ".github/workflows" / name
        p.write_text(textwrap.dedent(body))
        return p

    def test_preserves_trigger_permissions_and_maps_only_available_secrets(self) -> None:
        p = self.write("caller.yml", """\
            on:
              pull_request:
                types: [opened]
            jobs:
              call:
                if: github.actor != 'bot'
                permissions:
                  contents: read
                uses: jhw7500/automation/.github/workflows/claude.yml@v1.33
                secrets: inherit
        """)
        result = prepare_repository(
            self.repo, self.automation, "v1.35", {"CLAUDE_CODE_OAUTH_TOKEN"}, set()
        )
        text = p.read_text()
        self.assertIn("types: [opened]", text)
        self.assertIn("if: github.actor != 'bot'", text)
        self.assertIn("contents: read", text)
        self.assertIn("claude.yml@v1.35", text)
        self.assertIn("CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}", text)
        self.assertNotIn("secrets: inherit", text)
        self.assertEqual(1, result.callers)

    def test_preserves_inline_comment_on_uses_line(self) -> None:
        p = self.write("caller.yml", """\
            jobs:
              call:
                uses: 'jhw7500/automation/.github/workflows/claude.yml@v1.33' # pinned
                secrets: inherit
        """)
        prepare_repository(
            self.repo,
            self.automation,
            "v1.35",
            {"CLAUDE_CODE_OAUTH_TOKEN"},
            set(),
        )
        self.assertIn("claude.yml@v1.35' # pinned", p.read_text())

    def test_app_id_and_private_key_are_mapped_only_when_app_id_variable_exists(self) -> None:
        p = self.write("gemini.yml", """\
            jobs:
              call:
                uses: jhw7500/automation/.github/workflows/gemini.yml@v1.32
                secrets: inherit
        """)
        prepare_repository(
            self.repo,
            self.automation,
            "v1.35",
            {"GEMINI_API_KEY", "APP_PRIVATE_KEY"},
            {"APP_ID"},
        )
        text = p.read_text()
        self.assertIn("with:\n      app_id: ${{ vars.APP_ID }}", text)
        self.assertIn("APP_PRIVATE_KEY: ${{ secrets.APP_PRIVATE_KEY }}", text)
        self.assertIn("GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}", text)
        self.assertNotIn("GOOGLE_API_KEY:", text)

    def test_app_id_without_private_key_uses_available_api_key_path(self) -> None:
        p = self.write("gemini.yml", """\
            jobs:
              call:
                uses: jhw7500/automation/.github/workflows/gemini.yml@v1.32
                secrets: inherit
        """)
        prepare_repository(
            self.repo,
            self.automation,
            "v1.35",
            {"GEMINI_API_KEY"},
            {"APP_ID"},
        )
        text = p.read_text()
        self.assertNotIn("app_id:", text)
        self.assertNotIn("APP_PRIVATE_KEY:", text)
        self.assertIn("GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}", text)

    def test_optional_auth_requires_at_least_one_usable_gemini_path(self) -> None:
        self.write("gemini.yml", """\
            jobs:
              call:
                uses: jhw7500/automation/.github/workflows/gemini.yml@v1.33
                secrets: inherit
        """)
        with self.assertRaisesRegex(RolloutError, "no usable Gemini authentication"):
            prepare_repository(self.repo, self.automation, "v1.35", set(), set())

    def test_missing_required_secret_fails_before_writing(self) -> None:
        p = self.write("caller.yml", """\
            jobs:
              call:
                uses: jhw7500/automation/.github/workflows/claude.yml@v1.33
                secrets: inherit
        """)
        before = p.read_text()
        with self.assertRaisesRegex(RolloutError, "missing required secrets"):
            prepare_repository(self.repo, self.automation, "v1.35", set(), set())
        self.assertEqual(before, p.read_text())

    def test_secretless_workflow_removes_inherit(self) -> None:
        p = self.write("notice.yml", """\
            jobs:
              call:
                uses: jhw7500/automation/.github/workflows/notice.yml@v1.31
                secrets: inherit
        """)
        prepare_repository(self.repo, self.automation, "v1.35", set(), set())
        self.assertNotIn("secrets:", p.read_text())

    def test_no_callers_is_a_noop_and_does_not_change_config(self) -> None:
        before = (self.repo / ".github/workflow-config.yml").read_text()
        result = prepare_repository(self.repo, self.automation, "v1.35", set(), set())
        self.assertEqual(0, result.callers)
        self.assertEqual(before, (self.repo / ".github/workflow-config.yml").read_text())

    def test_updates_config_without_overwriting_repository_settings(self) -> None:
        self.write("caller.yml", """\
            jobs:
              call:
                uses: jhw7500/automation/.github/workflows/claude.yml@v1.33
                secrets: inherit
        """)
        prepare_repository(
            self.repo, self.automation, "v1.35", {"CLAUDE_CODE_OAUTH_TOKEN"}, set()
        )
        config = (self.repo / ".github/workflow-config.yml").read_text()
        self.assertIn("automation_ref: v1.35", config)
        self.assertIn("custom:\n    enabled: true", config)

    def test_updates_commented_config_ref_without_duplicate_key(self) -> None:
        config_path = self.repo / ".github/workflow-config.yml"
        config_path.write_text(
            "automation_ref: v1.33  # pinned by fleet\nworkflows:\n  custom:\n    enabled: true\n"
        )
        self.write("caller.yml", """\
            jobs:
              call:
                uses: jhw7500/automation/.github/workflows/claude.yml@v1.33
                secrets: inherit
        """)
        prepare_repository(
            self.repo,
            self.automation,
            "v1.35",
            {"CLAUDE_CODE_OAUTH_TOKEN"},
            set(),
        )
        config = config_path.read_text()
        self.assertEqual(1, config.count("automation_ref:"))
        self.assertIn("automation_ref: v1.35  # pinned by fleet", config)


if __name__ == "__main__":
    unittest.main()
