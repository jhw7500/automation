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

APP_TOKEN_WORKFLOWS = {
    "gemini-chat.yml",
    "gemini-dispatch.yml",
    "gemini-invoke.yml",
    "gemini-review.yml",
    "gemini-scheduled-triage.yml",
    "gemini-triage.yml",
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

    def test_repository_callers_do_not_forward_all_secrets(self) -> None:
        offenders = []
        for path in sorted(WORKFLOWS.glob("*.yml")):
            if re.search(r"secrets:\s*['\"]?inherit", path.read_text(encoding="utf-8")):
                offenders.append(path.name)
        self.assertEqual([], offenders)

    def test_opencode_auto_review_cannot_mint_an_oidc_app_token(self) -> None:
        path = WORKFLOWS / "opencode-auto-review.yml"
        workflow = load_workflow(path)
        job = workflow["jobs"]["opencode-review"]
        permissions = job["permissions"]
        self.assertNotIn("id-token", permissions)

        run_step = next(
            step for step in job["steps"] if step.get("name") == "Run OpenCode PR review"
        )
        self.assertEqual("true", run_step["with"]["use_github_token"])
        self.assertEqual("${{ github.token }}", run_step["env"]["GITHUB_TOKEN"])

    def test_opencode_auto_review_keeps_read_only_checkout_auth_for_private_repos(self) -> None:
        workflow = load_workflow(WORKFLOWS / "opencode-auto-review.yml")
        job = workflow["jobs"]["opencode-review"]
        checkout = next(
            step for step in job["steps"] if step.get("name") == "Checkout repository"
        )
        self.assertEqual("true", checkout["with"]["persist-credentials"])

    def test_app_token_workflows_accept_an_explicit_app_id_with_legacy_fallback(self) -> None:
        for filename in APP_TOKEN_WORKFLOWS:
            with self.subTest(workflow=filename):
                path = WORKFLOWS / filename
                workflow = load_workflow(path)
                app_id = workflow["on"]["workflow_call"]["inputs"]["app_id"]
                self.assertEqual("string", app_id["type"])
                self.assertEqual("false", app_id["required"])
                self.assertEqual("", app_id["default"])
                text = path.read_text(encoding="utf-8")
                self.assertIn("inputs.app_id || vars.APP_ID", text)


if __name__ == "__main__":
    unittest.main()
