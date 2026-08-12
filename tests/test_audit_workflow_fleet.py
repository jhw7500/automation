#!/usr/bin/env python3
"""Tests for repository caller contract auditing."""

from pathlib import Path
import json
import re
import shutil
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.audit_workflow_fleet import audit_repository


class AuditWorkflowFleetTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.automation = self.root / "automation"
        (self.automation / ".github/workflows").mkdir(parents=True)
        (self.automation / ".github/workflows/demo.yml").write_text(
            textwrap.dedent(
                """\
                on:
                  workflow_call:
                    secrets:
                      TOKEN:
                        required: true
                      OPTIONAL:
                        required: false
                jobs: {}
                """
            ),
            encoding="utf-8",
        )

        self.repo = self.root / "consumer"
        (self.repo / ".github/workflows").mkdir(parents=True)
        (self.repo / ".github/workflow-config.yml").write_text(
            "automation_ref: v1.35\n", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def write_caller(self, body: str) -> None:
        (self.repo / ".github/workflows/caller.yml").write_text(
            textwrap.dedent(body), encoding="utf-8"
        )

    def test_reports_ref_drift_and_inherit(self) -> None:
        self.write_caller(
            """\
            jobs:
              call:
                uses: jhw7500/automation/.github/workflows/demo.yml@v1.34
                secrets: inherit
            """
        )
        issues = audit_repository(self.repo, self.automation)
        self.assertTrue(any("ref drift" in issue for issue in issues), issues)
        self.assertTrue(any("secrets: inherit" in issue for issue in issues), issues)

    def test_accepts_required_mapping_and_optional_omission(self) -> None:
        self.write_caller(
            """\
            jobs:
              call:
                uses: jhw7500/automation/.github/workflows/demo.yml@v1.35
                secrets:
                  TOKEN: ${{ secrets.TOKEN }}
            """
        )
        self.assertEqual([], audit_repository(self.repo, self.automation))

    def test_reports_missing_required_and_unknown_mapping(self) -> None:
        self.write_caller(
            """\
            jobs:
              call:
                uses: jhw7500/automation/.github/workflows/demo.yml@v1.35
                secrets:
                  UNKNOWN: ${{ secrets.UNKNOWN }}
            """
        )
        issues = audit_repository(self.repo, self.automation)
        self.assertTrue(any("missing required" in issue for issue in issues), issues)
        self.assertTrue(any("undeclared secret" in issue for issue in issues), issues)

    def test_reports_secret_mapping_from_wrong_source(self) -> None:
        self.write_caller(
            """\
            jobs:
              call:
                uses: jhw7500/automation/.github/workflows/demo.yml@v1.35
                secrets:
                  TOKEN: ${{ secrets.SUBMODULE_TOKEN }}
            """
        )
        issues = audit_repository(self.repo, self.automation)
        self.assertTrue(any("source mismatch" in issue for issue in issues), issues)

    def test_does_not_compare_direct_action_ref_to_workflow_release_ref(self) -> None:
        self.write_caller(
            """\
            jobs:
              local:
                runs-on: ubuntu-latest
                steps:
                  - uses: jhw7500/automation/.github/actions/check-workflow-enabled@v1.1
              call:
                uses: jhw7500/automation/.github/workflows/demo.yml@v1.35
                secrets:
                  TOKEN: ${{ secrets.TOKEN }}
            """
        )
        self.assertEqual([], audit_repository(self.repo, self.automation))

    def test_active_baseline_templates_match_central_contracts_and_config(self) -> None:
        template = ROOT / "examples" / "baseline-workflows"
        materialized = self.root / "baseline-consumer"
        (materialized / ".github").mkdir(parents=True)
        shutil.copytree(template / "workflows", materialized / ".github" / "workflows")
        shutil.copy2(
            template / "workflow-config.yml",
            materialized / ".github" / "workflow-config.yml",
        )

        setup_config = json.loads((ROOT / "scripts" / "workflow-config.json").read_text())
        template_config = load_config_ref(template / "workflow-config.yml")
        self.assertEqual(setup_config["automation_ref"], template_config)
        self.assertEqual([], audit_repository(materialized, ROOT))
        self.assertEqual([], audit_repository(template, ROOT))

        inherited = []
        for path in template.rglob("*.yml"):
            if re.search(r"secrets:\s*['\"]?inherit", path.read_text(encoding="utf-8")):
                inherited.append(str(path.relative_to(template)))
        self.assertEqual([], inherited)

        app_id_missing = []
        for path in template.rglob("gemini-*.yml"):
            text = path.read_text(encoding="utf-8")
            if "APP_PRIVATE_KEY:" in text and "app_id: ${{ vars.APP_ID }}" not in text:
                app_id_missing.append(str(path.relative_to(template)))
        self.assertEqual([], app_id_missing)

    def test_distribution_only_rewrites_workflow_refs_and_rejects_zero_rewrites(self) -> None:
        setup = (ROOT / "scripts" / "setup-github-workflows.sh").read_text()
        self.assertNotIn("(workflows|actions)", setup)

        for relative in (
            "examples/baseline-workflows/workflows/bump-automation-ref.yml",
            "examples/baseline-workflows/.github/workflows/bump-automation-ref.yml",
        ):
            with self.subTest(path=relative):
                bump = (ROOT / relative).read_text()
                self.assertNotIn("(?:workflows|actions)", bump)
                self.assertIn("if changed_files == 0:", bump)
                self.assertIn("raise SystemExit", bump)

    def test_optional_template_reports_missing_caller_trigger_and_permission_drift(self) -> None:
        template = self.root / "templates"
        template.mkdir()
        canonical = textwrap.dedent(
            """\
            on:
              pull_request:
            jobs:
              call:
                permissions:
                  contents: read
                uses: jhw7500/automation/.github/workflows/demo.yml@v1.35
                secrets:
                  TOKEN: ${{ secrets.TOKEN }}
            """
        )
        (template / "caller.yml").write_text(canonical)

        missing = audit_repository(self.repo, self.automation, template=template)
        self.assertTrue(any("managed caller missing" in issue for issue in missing), missing)

        self.write_caller(
            canonical.replace("pull_request:", "push:").replace(
                "contents: read", "contents: write"
            )
        )
        drift = audit_repository(self.repo, self.automation, template=template)
        self.assertTrue(any("trigger drift" in issue for issue in drift), drift)
        self.assertTrue(any("permissions drift" in issue for issue in drift), drift)


def load_config_ref(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("automation_ref:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"automation_ref missing: {path}")


if __name__ == "__main__":
    unittest.main()
