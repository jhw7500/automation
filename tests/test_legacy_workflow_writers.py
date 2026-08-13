from __future__ import annotations

import os
from pathlib import Path
import re
import stat
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
LEGACY_SCRIPTS = (
    ROOT / "scripts" / "setup-github-workflows.sh",
    ROOT / "scripts" / "sync-secrets.sh",
)
ROLLOUT_DOC = ROOT / "docs" / "workflow-fleet-rollout.md"
CONTRACT_DOC = ROOT / "docs" / "workflows" / "contracts.md"
BASELINE_README = ROOT / "examples" / "baseline-workflows" / "README.md"
DESIGN = (
    ROOT
    / "docs"
    / "superpowers"
    / "specs"
    / "2026-08-13-common-workflow-standardization-design.md"
)
CI_WORKFLOW = ROOT / ".github" / "workflows" / "test-fleet-tools.yml"
CENTRAL_WORKFLOWS = ROOT / ".github" / "workflows"

FORBIDDEN = {
    "--sync-missing-secrets",
    "--refresh-secret",
    "--allow-env-secret",
    "--allow-personal-oauth-fanout",
    "--mode prepare",
    "--force-with-lease",
}
PROVIDER_INPUTS = {
    "CLAUDE_CODE_OAUTH_TOKEN",
    "GEMINI_API_KEY",
    "ZHIPU_API_KEY",
    "GOOGLE_API_KEY",
    ".claude",
    ".credentials.json",
    "workflow-config.json",
}


@pytest.fixture
def command_sentinels(tmp_path: Path) -> tuple[Path, Path]:
    """Return a PATH prefix whose retired writer commands leave evidence."""

    marker = tmp_path / "called"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for command in ("git", "gh", "cp"):
        executable = bin_dir / command
        executable.write_text(
            "#!/usr/bin/env bash\n"
            f"printf '%s\\n' {command!r} >> {str(marker)!r}\n"
            "exit 97\n",
            encoding="utf-8",
        )
        executable.chmod(0o755)
    return bin_dir, marker


def test_retired_scripts_are_exit_two_guards_without_writer_side_effects(
    tmp_path: Path, command_sentinels: tuple[Path, Path]
) -> None:
    bin_dir, marker = command_sentinels
    home = tmp_path / "home"
    credentials = home / ".claude" / ".credentials.json"
    credentials.parent.mkdir(parents=True)
    credentials.write_text('"provider-file-sentinel"\n', encoding="utf-8")
    environment = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(home),
        "CLAUDE_CODE_OAUTH_TOKEN": "claude-environment-sentinel",
        "GEMINI_API_KEY": "gemini-environment-sentinel",
        "ZHIPU_API_KEY": "zhipu-environment-sentinel",
    }

    results: dict[str, subprocess.CompletedProcess[str]] = {}
    for script in LEGACY_SCRIPTS:
        result = subprocess.run(
            ["bash", str(script), "ignored-provider-value"],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        results[script.name] = result
        assert result.returncode == 2, (script.name, result.stderr)
        assert result.stdout == ""
        assert "retired" in result.stderr.lower()
        assert "performs no changes" in result.stderr.lower()
        assert "sentinel" not in result.stderr

    assert not marker.exists()
    assert "rollout_workflow_fleet.py --mode plan" in results[
        "setup-github-workflows.sh"
    ].stderr
    assert "rollout_workflow_fleet.py --mode publish" in results[
        "setup-github-workflows.sh"
    ].stderr
    assert "personal-ops/claude-token-sync" in results["sync-secrets.sh"].stderr
    assert "rollout_workflow_fleet.py" not in results["sync-secrets.sh"].stderr


def test_retired_script_sources_do_not_read_provider_inputs_or_invoke_writers() -> None:
    invocation = re.compile(
        r"(?m)^\s*(?:exec\s+|command\s+)?(?:git|gh|cp)(?:\s|$)|"
        r"\$\([^)]*\b(?:git|gh|cp)\b"
    )
    for script in LEGACY_SCRIPTS:
        source = script.read_text(encoding="utf-8")
        assert invocation.search(source) is None, script.name
        for provider_input in PROVIDER_INPUTS:
            assert provider_input not in source, (script.name, provider_input)


def test_retired_scripts_remain_directly_executable() -> None:
    for script in LEGACY_SCRIPTS:
        assert script.stat().st_mode & stat.S_IXUSR, script.name


def test_retired_options_are_absent_from_operator_surfaces() -> None:
    paths = (*LEGACY_SCRIPTS, ROLLOUT_DOC, CONTRACT_DOC, BASELINE_README, DESIGN)
    combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    assert sorted(option for option in FORBIDDEN if option in combined) == []


def test_rollout_document_describes_pr_only_operation_and_separate_tokens() -> None:
    text = ROLLOUT_DOC.read_text(encoding="utf-8")

    assert "## Workflow PR rollout" in text
    assert "## Token synchronization" in text
    assert "python3 scripts/rollout_workflow_fleet.py" in text
    assert "--mode plan" in text
    assert "--mode publish" in text
    assert "python3 scripts/audit_workflow_fleet.py" in text
    assert "--bootstrap-repo cts-email-mcp-server" in text
    assert "--bootstrap-repo wpa-supplicant" in text
    assert "automation/common-workflows-v1.40" in text

    for status in ("current", "drift", "bootstrap_required", "blocked"):
        assert f"`{status}`" in text
    for canary in ("wlan-package", "wlan-driver", "cts-email-mcp-server"):
        assert canary in text
    for rule in (
        "exact open PR",
        "multiple PRs",
        "closed or merged PR",
        "partial success",
        "GitHub-native merge",
        "GitHub-native revert",
        "personal-ops/claude-token-sync",
    ):
        assert rule in text


def test_consumer_docs_match_commit_pins_auth_modes_and_catalog_boundary() -> None:
    contract = CONTRACT_DOC.read_text(encoding="utf-8")
    baseline = BASELINE_README.read_text(encoding="utf-8")
    combined = f"{contract}\n{baseline}"

    assert "14 managed caller workflows" in combined
    assert "scripts/workflow-catalog.json" in combined
    assert "40-character commit" in combined
    assert "repo_write_auth: github_app" in combined
    assert "repo_write_auth: github_token" in combined
    assert "GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}" in combined
    assert "CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}" in combined
    assert "ZHIPU_API_KEY: ${{ secrets.ZHIPU_API_KEY }}" in combined
    assert "secrets: inherit" not in combined
    assert "GCP_PROJECT_ID" not in baseline
    assert "GCP_LOCATION" not in baseline


def test_design_is_approved_for_planned_implementation() -> None:
    assert "Status: approved; implementation planned" in DESIGN.read_text(
        encoding="utf-8"
    )


def test_fleet_ci_covers_policy_canonical_code_tests_and_docs() -> None:
    workflow = yaml.load(CI_WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    expected_paths = {
        "scripts/workflow-catalog.json",
        "scripts/workflow-config.json",
        "scripts/*.py",
        "scripts/*.sh",
        "tests/*.py",
        ".github/workflows/*.yml",
        "examples/baseline-workflows/.github/**/*.yml",
        "docs/workflow-fleet-rollout.md",
        "docs/workflows/**/*.md",
        "examples/baseline-workflows/README.md",
        "docs/superpowers/specs/2026-08-13-common-workflow-standardization-design.md",
    }
    for event in ("pull_request", "push"):
        assert expected_paths <= set(workflow["on"][event]["paths"])

    steps = workflow["jobs"]["pytest"]["steps"]
    run_steps = "\n".join(step.get("run", "") for step in steps)
    assert "pytest -q" in run_steps
    assert "yaml.BaseLoader" in run_steps
    assert ".github/workflows" in run_steps
    assert "examples/baseline-workflows/.github/workflows" in run_steps


def test_fleet_ci_installs_and_runs_only_digest_verified_actionlint() -> None:
    workflow = yaml.load(CI_WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    steps = workflow["jobs"]["pytest"]["steps"]
    run_steps = "\n".join(step.get("run", "") for step in steps)

    assert "actionlint_1.7.12_linux_amd64.tar.gz" in run_steps
    assert (
        "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8"
        in run_steps
    )
    assert "https://github.com/rhysd/actionlint/releases/download/v1.7.12/" in run_steps
    assert "sha256sum --check" in run_steps
    assert "actionlint" in steps[-1].get("run", "")
    assert "-shellcheck=" in steps[-1].get("run", "")
    assert "-pyflakes=" in steps[-1].get("run", "")
    assert ".github/workflows" in steps[-1].get("run", "")
    assert "examples/baseline-workflows/.github/workflows" in steps[-1].get(
        "run", ""
    )
    assert "curl |" not in run_steps
    assert "curl -s" not in run_steps
    assert "latest" not in run_steps.lower()
    assert "|| true" not in run_steps
    assert "-ignore" not in run_steps
    assert all(step.get("continue-on-error") != "true" for step in steps)


def test_run_gemini_cli_output_consumers_use_the_public_action_contract() -> None:
    allowed_outputs = {"summary", "error"}
    consumers: list[tuple[str, str, str]] = []

    for path in sorted(CENTRAL_WORKFLOWS.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        workflow = yaml.load(text, Loader=yaml.BaseLoader)
        gemini_steps = {
            step["id"]
            for job in workflow.get("jobs", {}).values()
            for step in job.get("steps", [])
            if str(step.get("uses", "")).strip("'\"").startswith(
                "google-github-actions/run-gemini-cli@"
            )
            and "id" in step
        }
        for step_id in gemini_steps:
            pattern = re.compile(
                rf"steps\.{re.escape(step_id)}\.outputs\.([A-Za-z0-9_-]+)"
            )
            consumers.extend(
                (path.name, step_id, match.group(1))
                for match in pattern.finditer(text)
            )

    assert consumers
    assert sorted(
        item for item in consumers if item[2] not in allowed_outputs
    ) == []
