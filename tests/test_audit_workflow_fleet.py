#!/usr/bin/env python3
"""Tests for repository caller contract auditing."""

from pathlib import Path
import tempfile
import textwrap
import unittest

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


if __name__ == "__main__":
    unittest.main()
