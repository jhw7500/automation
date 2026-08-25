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
    "gemini-auto-review.yml": {"APP_PRIVATE_KEY": False, "GEMINI_API_KEY": True},
    "gemini-chat.yml": {"APP_PRIVATE_KEY": False, "GEMINI_API_KEY": True},
    "gemini-dispatch.yml": {"APP_PRIVATE_KEY": False, "GEMINI_API_KEY": True},
    "gemini-invoke.yml": {"APP_PRIVATE_KEY": False, "GEMINI_API_KEY": True},
    "gemini-review.yml": {"APP_PRIVATE_KEY": False, "GEMINI_API_KEY": True},
    "gemini-scheduled-triage.yml": {
        "APP_PRIVATE_KEY": False,
        "GEMINI_API_KEY": True,
    },
    "gemini-triage.yml": {"APP_PRIVATE_KEY": False, "GEMINI_API_KEY": True},
    "opencode-auto-review.yml": {"ZHIPU_API_KEY": True},
    "opencode.yml": {"ZHIPU_API_KEY": True},
}

GEMINI_WORKFLOWS = {
    "gemini-auto-review.yml",
    "gemini-chat.yml",
    "gemini-dispatch.yml",
    "gemini-invoke.yml",
    "gemini-review.yml",
    "gemini-scheduled-triage.yml",
    "gemini-triage.yml",
}
SETUP_AUTH = (
    "jhw7500/automation/.github/actions/setup-gemini-auth@"
    "2254f13aab44585c78954d20749f4fb677a8c2f1"
)
RELEASE_BOUND_REVIEW_SETUP_AUTH = "$/.github/actions/setup-gemini-auth"


def load_workflow(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


class WorkflowSecretContractsTest(unittest.TestCase):
    def test_gemini_jobs_declare_explicit_permissions(self) -> None:
        for filename in GEMINI_WORKFLOWS:
            workflow = load_workflow(WORKFLOWS / filename)
            for job_name, job in workflow["jobs"].items():
                with self.subTest(workflow=filename, job=job_name):
                    self.assertIn("permissions", job)
                    self.assertIsInstance(job["permissions"], dict)
                    if job_name == "check-enabled":
                        self.assertEqual({"contents": "read"}, job["permissions"])
                    elif job_name == "skipped":
                        self.assertEqual({}, job["permissions"])

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

    def test_opencode_auto_review_model_has_no_github_mutation_authority(self) -> None:
        path = WORKFLOWS / "opencode-auto-review.yml"
        workflow = load_workflow(path)
        job = workflow["jobs"]["opencode-review"]
        permissions = job["permissions"]
        self.assertEqual({}, permissions)
        self.assertNotIn("id-token", permissions)

        run_step = next(
            step for step in job["steps"] if step.get("name") == "Run OpenCode PR review"
        )
        self.assertNotIn("opencode github run", run_step["run"])
        self.assertIn(
            "opencode run --model zai-coding-plan/glm-4.7 --format json",
            run_step["run"],
        )
        for token in ("USE_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
            self.assertNotIn(token, run_step.get("env", {}))

    def test_opencode_auto_review_model_has_no_repository_checkout(self) -> None:
        workflow = load_workflow(WORKFLOWS / "opencode-auto-review.yml")
        job = workflow["jobs"]["opencode-review"]
        self.assertFalse(
            any("actions/checkout@" in step.get("uses", "") for step in job["steps"])
        )

    def test_opencode_auto_review_enforces_same_repository_prs_centrally(self) -> None:
        workflow = load_workflow(WORKFLOWS / "opencode-auto-review.yml")
        check = workflow["jobs"]["check-enabled"]
        self.assertEqual("${{ steps.pr_scope.outputs.safe_pr }}", check["outputs"]["safe_pr"])
        scope_step = next(step for step in check["steps"] if step.get("id") == "pr_scope")
        self.assertIn("gh api", scope_step["run"])
        condition = workflow["jobs"]["opencode-review"]["if"]
        self.assertIn("needs.check-enabled.outputs.safe_pr == 'true'", condition)

    def test_opencode_command_keeps_read_only_checkout_auth_for_private_repos(self) -> None:
        workflow = load_workflow(WORKFLOWS / "opencode.yml")
        job = workflow["jobs"]["opencode"]
        checkout = next(
            step for step in job["steps"] if step.get("name") == "Checkout repository"
        )
        self.assertEqual("true", checkout["with"]["persist-credentials"])

    def test_opencode_command_cannot_mint_app_token_and_requires_same_repo_pr(self) -> None:
        workflow = load_workflow(WORKFLOWS / "opencode.yml")
        check = workflow["jobs"]["check-enabled"]
        self.assertEqual("${{ steps.pr_scope.outputs.safe_pr }}", check["outputs"]["safe_pr"])
        job = workflow["jobs"]["opencode"]
        self.assertEqual(
            {"contents": "read", "pull-requests": "write", "issues": "write"},
            job["permissions"],
        )
        self.assertIn("needs.check-enabled.outputs.safe_pr == 'true'", job["if"])
        run_step = next(step for step in job["steps"] if step.get("name") == "Run opencode")
        self.assertEqual("opencode github run", run_step["run"])
        self.assertEqual("true", run_step["env"]["USE_GITHUB_TOKEN"])
        self.assertEqual("${{ github.token }}", run_step["env"]["GITHUB_TOKEN"])
        scope_step = next(step for step in check["steps"] if step.get("id") == "pr_scope")
        self.assertEqual(
            "${{ github.event.pull_request.number || github.event.issue.number }}",
            scope_step["env"]["PR_NUMBER"],
        )

    def test_opencode_baseline_caller_grants_only_required_review_permissions(self) -> None:
        path = ROOT / "examples/baseline-workflows/.github/workflows/opencode.yml"
        workflow = load_workflow(path)
        job = workflow["jobs"]["opencode"]
        self.assertEqual(
            {"contents": "read", "pull-requests": "write", "issues": "write"},
            job["permissions"],
        )
        self.assertNotIn("id-token", job["permissions"])

    def test_gemini_contract_is_api_key_only_and_mode_explicit(self) -> None:
        for filename in GEMINI_WORKFLOWS:
            with self.subTest(workflow=filename):
                path = WORKFLOWS / filename
                workflow = load_workflow(path)
                self.assertEqual({"workflow_call"}, set(workflow["on"]))
                call = workflow["on"]["workflow_call"]
                self.assertEqual(
                    {
                        "description": (
                            "Repository write authentication: github_app or github_token"
                        ),
                        "type": "string",
                        "required": "true",
                    },
                    call["inputs"]["repo_write_auth"],
                )
                self.assertEqual("false", call["inputs"]["app_id"]["required"])
                self.assertEqual(
                    {"APP_PRIVATE_KEY", "GEMINI_API_KEY"}, set(call["secrets"])
                )
                self.assertEqual(
                    "false", call["secrets"]["APP_PRIVATE_KEY"]["required"]
                )
                self.assertEqual("true", call["secrets"]["GEMINI_API_KEY"]["required"])
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("GOOGLE_API_KEY", text)
                self.assertNotIn("vars.APP_ID", text)
                self.assertNotIn("id-token:", text)
                expected_setup = (
                    RELEASE_BOUND_REVIEW_SETUP_AUTH
                    if filename == "gemini-auto-review.yml"
                    else SETUP_AUTH
                )
                self.assertIn(expected_setup, text)

                auth_steps = [
                    step
                    for job in workflow["jobs"].values()
                    for step in job.get("steps", [])
                    if "setup-gemini-auth" in step.get("uses", "")
                ]
                self.assertTrue(auth_steps)
                self.assertEqual(
                    {expected_setup}, {step["uses"] for step in auth_steps}
                )
                for step in auth_steps:
                    self.assertEqual(
                        {
                            "app-id": "${{ inputs.repo_write_auth == 'github_app' && inputs.app_id || '' }}",
                            "private-key": "${{ inputs.repo_write_auth == 'github_app' && secrets.APP_PRIVATE_KEY || '' }}",
                            "fallback-token": "${{ inputs.repo_write_auth == 'github_token' && github.token || '' }}",
                        },
                        step["with"],
                    )

    def test_auto_rereview_gh_cli_has_repository_context_without_checkout(self) -> None:
        workflow = load_workflow(WORKFLOWS / "auto-rereview-request.yml")
        job = workflow["jobs"]["notify-reviewers"]
        self.assertEqual("${{ github.repository }}", job["env"]["GH_REPO"])
        self.assertFalse(
            any("actions/checkout@" in step.get("uses", "") for step in job["steps"])
        )


if __name__ == "__main__":
    unittest.main()
