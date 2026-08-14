from __future__ import annotations

import json
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
IMPLEMENTATION_PLAN = (
    ROOT
    / "docs"
    / "superpowers"
    / "plans"
    / "2026-08-13-common-workflow-pr-rollout.md"
)

FORBIDDEN = {
    "--sync-missing-secrets",
    "--refresh-secret",
    "--allow-env-secret",
    "--allow-personal-oauth-fanout",
    "--mode prepare",
    "force-with-lease",
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


def test_retired_scripts_use_only_bash_builtins_with_hostile_or_missing_path(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    marker = tmp_path / "external-cat-called"
    fake_cat = fake_bin / "cat"
    fake_cat.write_text(
        "#!/bin/bash\nprintf called > \"$MARKER\"\nexit 97\n",
        encoding="utf-8",
    )
    fake_cat.chmod(0o755)

    for script in LEGACY_SCRIPTS:
        source = script.read_text(encoding="utf-8")
        assert re.search(r"(?m)^\s*cat(?:\s|$)", source) is None, script.name

        missing_path = subprocess.run(
            [str(script)],
            env={"PATH": "/nonexistent", "HOME": str(tmp_path)},
            text=True,
            capture_output=True,
            check=False,
        )
        assert missing_path.returncode == 2, script.name
        assert "performs no changes" in missing_path.stderr

        hostile_path = subprocess.run(
            ["/bin/bash", str(script)],
            env={
                "PATH": str(fake_bin),
                "HOME": str(tmp_path),
                "MARKER": str(marker),
            },
            text=True,
            capture_output=True,
            check=False,
        )
        assert hostile_path.returncode == 2, script.name
        assert "performs no changes" in hostile_path.stderr
        assert not marker.exists()


def test_retired_options_are_absent_from_operator_surfaces() -> None:
    paths = (*LEGACY_SCRIPTS, ROLLOUT_DOC, CONTRACT_DOC, BASELINE_README, DESIGN)
    combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    assert sorted(option for option in FORBIDDEN if option in combined) == []


def test_rollout_document_describes_pr_only_operation_and_separate_tokens() -> None:
    text = ROLLOUT_DOC.read_text(encoding="utf-8")

    assert "## Workflow PR rollout" in text
    assert "## Token synchronization" in text
    assert (
        'python3 "$AUTOMATION_RELEASE_ROOT/scripts/rollout_workflow_fleet.py"' in text
    )
    assert "--mode plan" in text
    assert "--mode publish" in text
    assert 'python3 "$AUTOMATION_RELEASE_ROOT/scripts/audit_workflow_fleet.py"' in text
    assert "--bootstrap-repo cts-email-mcp-server" in text
    assert "--bootstrap-repo wpa-supplicant" in text
    assert "automation/common-workflows-v1.40" in text

    for status in ("current", "planned", "reusable", "blocked"):
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
    assert "public plan statuses" in text.lower()
    assert "missing config without explicit bootstrap" in text.lower()
    assert "renderer-only" in text.lower()
    assert "Audit reports `current`, `drift`, or `blocked`" in text


def test_rollout_recovery_matches_closed_pr_fail_closed_behavior() -> None:
    rollout = ROLLOUT_DOC.read_text(encoding="utf-8")
    design = DESIGN.read_text(encoding="utf-8")
    combined = f"{rollout}\n{design}"

    assert "closed PR history" in combined
    assert "blocks reuse" in combined
    assert "new immutable release" in combined
    assert "same repository/release attempt" in combined
    assert "close the PR and delete" not in combined.lower()
    assert "re-run plan and create a new PR" not in combined.lower()


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


def test_rollout_docs_define_mode_drift_and_complete_managed_tree_attestation() -> None:
    operator = ROLLOUT_DOC.read_text(encoding="utf-8")
    design = DESIGN.read_text(encoding="utf-8")

    for document in (operator, design):
        assert "mode-only drift" in document
        assert "`100644 blob`" in document
        assert "complete managed set" in document


def test_fleet_ci_covers_policy_canonical_code_tests_and_docs() -> None:
    workflow = yaml.load(CI_WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    expected_paths = {
        "scripts/workflow-catalog.json",
        "scripts/workflow-config.json",
        "scripts/*.py",
        "scripts/*.sh",
        "tests/*.py",
        ".github/workflows/*.yml",
        ".github/actions/**/*.yml",
        ".github/actions/**/*.yaml",
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


def test_fleet_ci_checks_out_complete_history_for_release_verification() -> None:
    workflow = yaml.load(CI_WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    steps = workflow["jobs"]["pytest"]["steps"]
    checkout_steps = [
        step
        for step in steps
        if step.get("uses", "").startswith("actions/checkout@")
    ]

    assert len(checkout_steps) == 1
    assert checkout_steps[0].get("with", {}).get("fetch-depth") == "0"


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
            for job in workflow.get("jobs", {}).values():
                for step in job.get("steps", []):
                    assert pattern.search(step.get("run", "")) is None, (
                        path.name,
                        step.get("name", step.get("id", "unnamed")),
                        step_id,
                    )

    assert consumers
    assert sorted(
        item for item in consumers if item[2] not in allowed_outputs
    ) == []


def _workflow_step(path: Path, name: str) -> dict:
    workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    matches = [
        step
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if step.get("name") == name
    ]
    assert len(matches) == 1, (path.name, name)
    return matches[0]


def _parse_github_outputs(text: str) -> dict[str, str]:
    lines = text.splitlines()
    parsed: dict[str, str] = {}
    index = 0
    while index < len(lines):
        header = lines[index]
        if "<<" not in header:
            name, value = header.split("=", 1)
            parsed[name] = value
            index += 1
            continue
        name, delimiter = header.split("<<", 1)
        index += 1
        value: list[str] = []
        while index < len(lines) and lines[index] != delimiter:
            value.append(lines[index])
            index += 1
        assert index < len(lines), (name, delimiter)
        parsed[name] = "\n".join(value)
        index += 1
    return parsed


def test_final_gemini_selection_treats_adversarial_model_output_as_data(
    tmp_path: Path,
) -> None:
    step = _workflow_step(
        CENTRAL_WORKFLOWS / "gemini-dispatch.yml", "Set final review result"
    )
    marker = tmp_path / "command-substitution-ran"
    backtick_marker = tmp_path / "backtick-ran"
    response = (
        f'alpha $(touch "{marker}") `touch "{backtick_marker}"` "double" \'single\'\n'
        "__AUTOMATION_OUTPUT__\nomega"
    )
    error = "failure %s $(false) `false`\nsecond line"
    output = tmp_path / "github-output"
    environment = {
        **os.environ,
        "GITHUB_OUTPUT": str(output),
        "PRIMARY_OUTCOME": "success",
        "PRIMARY_MODEL": "gemini-primary",
        "PRIMARY_RESPONSE": response,
        "PRIMARY_ERRORS": error,
        "FALLBACK_OUTCOME": "failure",
        "FALLBACK_MODEL": "gemini-fallback",
        "FALLBACK_RESPONSE": "unused",
        "FALLBACK_ERRORS": "unused",
    }

    completed = subprocess.run(
        ["/bin/bash", "-eu", "-o", "pipefail", "-c", step["run"]],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert not marker.exists()
    assert not backtick_marker.exists()
    assert _parse_github_outputs(output.read_text(encoding="utf-8")) == {
        "outcome": "success",
        "model": "gemini-primary",
        "response": response,
        "errors": error,
    }


@pytest.mark.parametrize(
    "step_name",
    ("Log primary model failure", "Log primary model failure (invoke)"),
)
def test_failure_comments_propagate_adversarial_error_text_as_data(
    tmp_path: Path, step_name: str
) -> None:
    step = _workflow_step(CENTRAL_WORKFLOWS / "gemini-dispatch.yml", step_name)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture = tmp_path / "captured-comment"
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        "while (($#)); do\n"
        "  if [[ $1 == --body-file ]]; then /bin/cp -- \"$2\" \"$CAPTURE\"; exit 0; fi\n"
        "  shift\n"
        "done\n"
        "exit 98\n",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    marker = tmp_path / "error-command-ran"
    backtick_marker = tmp_path / "error-backtick-ran"
    model = 'gemini "quoted" $(not-a-command)'
    errors = (
        f'first $(touch "{marker}") `touch "{backtick_marker}"` %s "quote"\n'
        "second line"
    )
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "CAPTURE": str(capture),
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_TOKEN": "token",
        "ISSUE_NUMBER": "17",
        "PRIMARY_MODEL": model,
        "FALLBACK_MODEL": "fallback-model",
        "ERRORS": errors,
        "REPOSITORY": "jhw7500/example",
    }

    completed = subprocess.run(
        ["/bin/bash", "-eu", "-o", "pipefail", "-c", step["run"]],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert not marker.exists()
    assert not backtick_marker.exists()
    body = capture.read_text(encoding="utf-8")
    assert model in body
    assert errors in body
    assert "$PRIMARY_MODEL" not in body
    assert "$ERRORS" not in body


def test_task9_is_fail_closed_sequential_and_reuses_the_marked_workspace() -> None:
    text = IMPLEMENTATION_PLAN.read_text(encoding="utf-8")

    assert "set -euo pipefail" in text
    assert "show-ref --verify --quiet refs/tags/v1.40" not in text
    assert "/usr/bin/git -C / ls-remote" in text
    assert "https://github.com/jhw7500/automation.git" in text
    main_index = text.index("refs/heads/main")
    tag_index = text.index("refs/tags/v1.40", main_index)
    verifier_command = "python3 -m scripts.verify_workflow_release"
    verify_index = text.index(verifier_command, tag_index)
    assert "python3 scripts/verify_workflow_release.py" not in text[tag_index:]
    publish_index = text.index("repos/jhw7500/automation/git/tags", verify_index)
    postverify_index = text.index("POST_REMOTE_TAGS", publish_index)
    assert main_index < tag_index < verify_index < publish_index < postverify_index
    assert "--repo wlan-package --repo wlan-driver" not in text
    package_index = text.index("--repo wlan-package", postverify_index)
    package_approval = text.index("wlan-package", package_index + len("--repo wlan-package"))
    driver_index = text.index("--repo wlan-driver", package_approval)
    bootstrap_index = text.index("--repo cts-email-mcp-server", driver_index)
    assert package_index < package_approval < driver_index < bootstrap_index
    assert "--workspace /tmp/automation-v1.40-audit" not in text
    expected_audit = (
        '"$AUTOMATION_RELEASE_ROOT/scripts/audit_workflow_fleet.py" \\\n'
        '  --automation "$AUTOMATION_RELEASE_ROOT" --workspace "$FLEET_WORKSPACE"'
    )
    assert expected_audit in text


def test_task9_materializes_and_uses_one_fresh_full_public_release_clone() -> None:
    text = IMPLEMENTATION_PLAN.read_text(encoding="utf-8")
    task9 = text.split("### Task 9:", 1)[1]
    post_tag = task9.index("POST_REMOTE_TAGS")
    clone = task9.index("public_git clone", post_tag)
    first_rollout = task9.index(
        'rtk python3 "$AUTOMATION_RELEASE_ROOT/scripts/rollout_workflow_fleet.py"',
        clone,
    )
    first_plan = task9.index("--mode plan", first_rollout)

    materialization = task9[post_tag:first_plan]
    assert "AUTOMATION_RELEASE_ROOT=/tmp/automation-v1.40-public" in materialization
    assert "https://github.com/jhw7500/automation.git" in materialization
    assert "public_git clone" in materialization
    assert "--no-recurse-submodules" in materialization
    assert "--is-shallow-repository" in materialization
    assert "remote get-url --all origin" in materialization
    assert "remote get-url --push --all origin" in materialization
    assert "refs/heads/main" in materialization
    assert "refs/tags/v1.40" in materialization
    assert '"refs/tags/v1.40^{}"' in materialization
    assert '"$DIRECT_COUNT" -eq 1' in materialization
    assert '"$PEELED_COUNT" -eq 1' in materialization
    assert '-e "$AUTOMATION_RELEASE_ROOT"' in materialization
    assert '-L "$AUTOMATION_RELEASE_ROOT"' in materialization
    for forbidden in ("--depth", "--filter", "--single-branch", "git clone origin"):
        assert forbidden not in materialization

    rollout_commands = task9[first_rollout:]
    assert "--automation ." not in rollout_commands
    assert "scripts/rollout_workflow_fleet.py" in rollout_commands
    assert "scripts/audit_workflow_fleet.py" in rollout_commands
    assert rollout_commands.count('--automation "$AUTOMATION_RELEASE_ROOT"') >= 7
    assert '--workspace "$FLEET_WORKSPACE"' in rollout_commands
    assert "--initialize-workspace" in rollout_commands
    assert (
        task9.index("public_git clone", post_tag)
        < task9.index("--is-shallow-repository", clone)
        < first_plan
    )


def test_design_and_plan_distinguish_public_plan_and_audit_statuses() -> None:
    design = DESIGN.read_text(encoding="utf-8")
    plan = IMPLEMENTATION_PLAN.read_text(encoding="utf-8")
    for text in (design, plan):
        assert "public plan statuses" in text.lower()
        for status in ("current", "planned", "reusable", "blocked"):
            assert f"`{status}`" in text
        assert "missing config without explicit bootstrap" in text.lower()
        assert "audit" in text.lower()
        assert "`drift`" in text


def test_release_verification_docs_define_the_public_hermetic_git_boundary() -> None:
    rollout = ROLLOUT_DOC.read_text(encoding="utf-8")
    design = DESIGN.read_text(encoding="utf-8")
    plan = IMPLEMENTATION_PLAN.read_text(encoding="utf-8")
    combined = f"{rollout}\n{design}\n{plan}"

    assert "https://github.com/jhw7500/automation.git" in combined
    assert "credential-free public HTTPS" in combined
    assert "system and global Git configuration" in combined
    assert "credential helpers" in combined
    assert "private or forked automation remote" in combined
    assert "isolated temporary Git directory" in combined
    assert "source `.git/config`" in combined
    assert "`.git/info/attributes`" in combined
    assert "replacement refs" in combined
    assert "alternates and promisor" in combined
    assert "GIT_CONFIG_NOSYSTEM=1" in plan
    assert "GIT_CONFIG_GLOBAL=/dev/null" in plan
    assert "GIT_NO_REPLACE_OBJECTS=1" in plan
    assert "GIT_OBJECT_DIRECTORY" in plan
    assert "GIT_ALLOW_PROTOCOL=https" in plan
    assert "/usr/bin/git -C / ls-remote" in plan
    assert "git ls-remote --tags origin refs/tags/v1.40" not in plan


def test_patch_tag_github_api_uses_an_empty_environment_and_private_fd() -> None:
    rollout = ROLLOUT_DOC.read_text(encoding="utf-8")
    release_shell = rollout.split("```bash\n", 1)[1].split("\n```", 1)[0]
    github_api = release_shell.split("github_api() (", 1)[1].split(
        "\nverify_annotated_tag()", 1
    )[0]

    assert "/usr/bin/python3 -I -S -B -c" in release_shell
    assert 'os.execve(\n    "/bin/bash"' in release_shell
    assert '["/bin/bash", "--noprofile", "--norc", "-s"]' in release_shell
    assert "read_fd, write_fd = os.pipe()" in release_shell
    assert "<<'PATCH_RELEASE_BASH'" in release_shell
    assert release_shell.rstrip().endswith("PATCH_RELEASE_BASH")
    assert "/usr/bin/env -i /usr/bin/python3 -I -S -B -c" in github_api
    assert '3<<<"$RELEASE_GITHUB_TOKEN"' in github_api
    assert 'with os.fdopen(3, "rb") as source:' in github_api
    assert 'args = ["/usr/bin/gh", "api", "--hostname", "github.com"' in github_api
    assert "os.execve(args[0], args, environment)" in github_api
    assert "compgen -e" not in github_api
    assert "export -n" not in github_api
    assert "\n  exec " not in github_api


def test_patch_tag_github_api_ignores_exported_exec_and_env_functions(
    tmp_path: Path,
) -> None:
    rollout = ROLLOUT_DOC.read_text(encoding="utf-8")
    release_shell = rollout.split("```bash\n", 1)[1].split("\n```", 1)[0]
    start = release_shell.index("github_api() (")
    end = release_shell.index("\nverify_annotated_tag()", start)
    github_api = release_shell[start:end]

    capture = tmp_path / "capture.json"
    fake_gh = tmp_path / "gh"
    fake_gh.write_text(
        "#!/usr/bin/python3\n"
        "import json, os, pathlib, sys\n"
        f"pathlib.Path({str(capture)!r}).write_text(\n"
        "    json.dumps({'argv': sys.argv, 'env': dict(os.environ)}),\n"
        "    encoding='utf-8',\n"
        ")\n",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    github_api = github_api.replace('"/usr/bin/gh"', json.dumps(str(fake_gh)))
    script = tmp_path / "run.sh"
    script.write_text(
        "set -euo pipefail\n"
        "TOKEN_KEY=GITHUB_TOKEN\n"
        "RELEASE_GITHUB_TOKEN=$GITHUB_TOKEN\n"
        f"{github_api}\n"
        "github_api rate_limit\n"
    )

    exec_marker = tmp_path / "exec-called"
    env_marker = tmp_path / "env-called"
    environment = {
        "PATH": "/usr/bin:/bin",
        "GITHUB_TOKEN": "github-token-sentinel",
        "BASH_FUNC_exec%%": (
            f"() {{ /usr/bin/printf called > {str(exec_marker)!r}; return 97; }}"
        ),
        "BASH_FUNC_env%%": (
            f"() {{ /usr/bin/printf called > {str(env_marker)!r}; return 98; }}"
        ),
    }
    completed = subprocess.run(
        ["/bin/bash", "--noprofile", "--norc", str(script)],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert not exec_marker.exists()
    assert not env_marker.exists()
    observed = json.loads(capture.read_text(encoding="utf-8"))
    assert observed["argv"] == [
        str(fake_gh),
        "api",
        "--hostname",
        "github.com",
        "rate_limit",
    ]
    assert observed["env"] == {
        "GITHUB_TOKEN": "github-token-sentinel",
        "HOME": "/nonexistent/automation-workflow-release/home",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
        "XDG_CONFIG_HOME": "/nonexistent/automation-workflow-release/xdg",
    }


def test_patch_tag_launcher_blocks_parent_xtrace_and_exported_functions(
    tmp_path: Path,
) -> None:
    rollout = ROLLOUT_DOC.read_text(encoding="utf-8")
    release_shell = rollout.split("```bash\n", 1)[1].split("\n```", 1)[0]
    launcher = release_shell.split(" <<'PATCH_RELEASE_BASH'", 1)[0]
    token_capture = tmp_path / "token"
    environment_capture = tmp_path / "environment"
    exec_marker = tmp_path / "exec-called"
    env_marker = tmp_path / "env-called"
    set_marker = tmp_path / "set-called"
    script = tmp_path / "launch.sh"
    script.write_text(
        f"{launcher} <<'PATCH_RELEASE_BASH'\n"
        "IFS= read -r observed <&3\n"
        f"/usr/bin/printf '%s' \"$observed\" > {str(token_capture)!r}\n"
        f"/usr/bin/env > {str(environment_capture)!r}\n"
        "PATCH_RELEASE_BASH\n",
        encoding="utf-8",
    )
    token = "github-token-sentinel"
    environment = {
        "PATH": "/usr/bin:/bin",
        "GITHUB_TOKEN": token,
        "EXPECTED_PATCH_MERGE_SHA": "a" * 40,
        "PROVIDER_SENTINEL": "must-not-reach-clean-bash",
        "BASH_FUNC_exec%%": (
            f"() {{ /usr/bin/printf called > {str(exec_marker)!r}; return 97; }}"
        ),
        "BASH_FUNC_env%%": (
            f"() {{ /usr/bin/printf called > {str(env_marker)!r}; return 98; }}"
        ),
        "BASH_FUNC_set%%": (
            f"() {{ /usr/bin/printf called > {str(set_marker)!r}; return 99; }}"
        ),
    }
    completed = subprocess.run(
        ["/bin/bash", "--noprofile", "--norc", "-x", str(script)],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert token not in completed.stdout
    assert token not in completed.stderr
    assert token_capture.read_text(encoding="utf-8") == token
    assert not exec_marker.exists()
    assert not env_marker.exists()
    assert not set_marker.exists()
    child_environment = environment_capture.read_text(encoding="utf-8")
    assert "PROVIDER_SENTINEL=" not in child_environment
    assert "BASH_FUNC_" not in child_environment
    assert "GH_TOKEN=" not in child_environment
    assert "GITHUB_TOKEN=" not in child_environment


def test_task9_binds_release_creation_to_exact_github_repository_and_main() -> None:
    text = IMPLEMENTATION_PLAN.read_text(encoding="utf-8")
    task9 = text.split("### Task 9:", 1)[1]
    release_shell = task9.split("rtk bash -s <<'BASH'", 1)[1].split(
        "\nBASH", 1
    )[0]

    assert "git push" not in release_shell
    assert "origin/main" not in release_shell
    assert "pushurl" not in release_shell.lower()
    assert "url." not in release_shell.lower()
    assert "refs/heads/main" in task9
    assert "https://github.com/jhw7500/automation.git" in task9
    assert "--commit-only" in task9
    assert "gh api --hostname github.com --method POST" in task9
    assert "repos/jhw7500/automation/git/tags" in task9
    assert "repos/jhw7500/automation/git/refs" in task9
    assert "ref=refs/tags/v1.40" in task9

    main_read = task9.index("refs/heads/main")
    tag_absence = task9.index("refs/tags/v1.40", main_read)
    preverify = task9.index("--commit-only", tag_absence)
    create_object = task9.index("repos/jhw7500/automation/git/tags", preverify)
    create_ref = task9.index("repos/jhw7500/automation/git/refs", create_object)
    postverify = task9.index("POST_REMOTE_TAGS", create_ref)
    assert main_read < tag_absence < preverify < create_object < create_ref < postverify


def _task9_release_shell() -> str:
    text = IMPLEMENTATION_PLAN.read_text(encoding="utf-8")
    return text.split("rtk bash -s <<'BASH'", 1)[1].split("\nBASH", 1)[0]


def _task9_mock_rtk(tmp_path: Path) -> tuple[Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "rtk.log"
    fake = fake_bin / "rtk"
    fake.write_text(
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        "printf '%s\\n' \"$*\" >> \"$MOCK_LOG\"\n"
        "all=\" $* \"\n"
        "if [[ $1 == env && $all == *' refs/heads/main '* ]]; then\n"
        "  printf '%s\\t%s\\n' \"$MOCK_MERGE_SHA\" refs/heads/main\n"
        "elif [[ $1 == env && $all == *' refs/tags/v1.40 '* ]]; then\n"
        "  count=0; [[ -f $MOCK_STATE ]] && read -r count < \"$MOCK_STATE\"\n"
        "  count=$((count + 1)); printf '%s\\n' \"$count\" > \"$MOCK_STATE\"\n"
        "  if ((count > 1)); then\n"
        "    printf '%s\\t%s\\n' \"$MOCK_TAG_SHA\" refs/tags/v1.40\n"
        "    printf '%s\\t%s\\n' \"$MOCK_MERGE_SHA\" 'refs/tags/v1.40^{}'\n"
        "  fi\n"
        "elif [[ $1 == python3 && $all == *' --commit-only '* ]]; then\n"
        "  :\n"
        "elif [[ $1 == gh && $all == *' repos/jhw7500/automation/git/tags '* ]]; then\n"
        "  printf '%s\\t%s\\t%s\\t%s\\n' \"$MOCK_TAG_SHA\" v1.40 \"$MOCK_MERGE_SHA\" commit\n"
        "elif [[ $1 == gh && $all == *' repos/jhw7500/automation/git/refs '* ]]; then\n"
        "  [[ ${MOCK_REF_FAIL:-0} != 1 ]] || exit 41\n"
        "  printf '%s\\t%s\\n' refs/tags/v1.40 \"$MOCK_TAG_SHA\"\n"
        "else\n"
        "  printf '%s\\n' 'unexpected mocked rtk invocation' >&2\n"
        "  exit 97\n"
        "fi\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return fake_bin, log


def test_task9_release_shell_executes_bound_sequence_with_mocked_clients(
    tmp_path: Path,
) -> None:
    fake_bin, log = _task9_mock_rtk(tmp_path)
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "MOCK_LOG": str(log),
        "MOCK_STATE": str(tmp_path / "state"),
        "MOCK_MERGE_SHA": "1" * 40,
        "MOCK_TAG_SHA": "2" * 40,
    }

    completed = subprocess.run(
        ["/bin/bash", "-s"],
        input=_task9_release_shell(),
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    main = next(i for i, call in enumerate(calls) if "refs/heads/main" in call)
    tag_reads = [i for i, call in enumerate(calls) if "refs/tags/v1.40" in call and call.startswith("env ")]
    preverify = next(i for i, call in enumerate(calls) if "--commit-only" in call)
    tag_object = next(i for i, call in enumerate(calls) if "/git/tags" in call)
    tag_ref = next(i for i, call in enumerate(calls) if "/git/refs" in call)
    assert main < tag_reads[0] < preverify < tag_object < tag_ref < tag_reads[1]
    assert all("git push" not in call and " origin" not in call for call in calls)


def test_task9_release_shell_stops_on_ref_creation_race_before_post_read(
    tmp_path: Path,
) -> None:
    fake_bin, log = _task9_mock_rtk(tmp_path)
    completed = subprocess.run(
        ["/bin/bash", "-s"],
        input=_task9_release_shell(),
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "MOCK_LOG": str(log),
            "MOCK_STATE": str(tmp_path / "state"),
            "MOCK_MERGE_SHA": "1" * 40,
            "MOCK_TAG_SHA": "2" * 40,
            "MOCK_REF_FAIL": "1",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 41
    calls = log.read_text(encoding="utf-8").splitlines()
    assert sum(call.startswith("env ") and "refs/tags/v1.40" in call for call in calls) == 1
    assert any("repos/jhw7500/automation/git/refs" in call for call in calls)
