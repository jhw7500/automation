#!/usr/bin/env python3
"""Contract checks for credentials accepted by reusable workflows."""

from pathlib import Path
import re
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

EXPECTED_REQUIRED = {
    "claude-code-review.yml": {"CLAUDE_CODE_OAUTH_TOKEN": True},
    "claude.yml": {"CLAUDE_CODE_OAUTH_TOKEN": True},
    "gemini-auto-review.yml": {"GEMINI_API_KEY": True},
    "gemini-chat.yml": {
        "APP_PRIVATE_KEY": False,
        "GEMINI_API_KEY": False,
        "GOOGLE_API_KEY": False,
    },
    "gemini-dispatch.yml": {
        "APP_PRIVATE_KEY": False,
        "GEMINI_API_KEY": False,
        "GOOGLE_API_KEY": False,
    },
    "gemini-invoke.yml": {
        "APP_PRIVATE_KEY": False,
        "GEMINI_API_KEY": False,
        "GOOGLE_API_KEY": False,
    },
    "gemini-review.yml": {
        "APP_PRIVATE_KEY": False,
        "GEMINI_API_KEY": False,
        "GOOGLE_API_KEY": False,
    },
    "gemini-scheduled-triage.yml": {
        "APP_PRIVATE_KEY": False,
        "GEMINI_API_KEY": False,
        "GOOGLE_API_KEY": False,
    },
    "gemini-triage.yml": {
        "APP_PRIVATE_KEY": False,
        "GEMINI_API_KEY": False,
        "GOOGLE_API_KEY": False,
    },
    "opencode-auto-review.yml": {"ZHIPU_API_KEY": True},
    "opencode.yml": {"ZHIPU_API_KEY": True},
}


def load_workflow(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


class WorkflowSecretContractsTest(unittest.TestCase):
    def test_declares_exactly_the_external_secrets_it_references(self) -> None:
        for filename, expected in EXPECTED_REQUIRED.items():
            with self.subTest(workflow=filename):
                path = WORKFLOWS / filename
                text = path.read_text(encoding="utf-8")
                referenced = set(re.findall(r"secrets\.([A-Z][A-Z0-9_]*)", text))
                referenced.discard("GITHUB_TOKEN")

                workflow = load_workflow(path)
                declared = workflow["on"]["workflow_call"].get("secrets", {})

                self.assertEqual(set(expected), referenced)
                self.assertEqual(set(expected), set(declared))
                actual_required = {
                    name: values.get("required", "false") == "true"
                    for name, values in declared.items()
                }
                self.assertEqual(expected, actual_required)

    def test_uses_github_context_instead_of_secret_contract_for_default_token(self) -> None:
        offenders = []
        for path in sorted(WORKFLOWS.glob("*.yml")):
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "secrets.GITHUB_TOKEN" in line:
                    offenders.append(f"{path.name}:{lineno}")
        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()
