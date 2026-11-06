"""Contracts for the non-blocking, trusted-code commit audit workflow."""

from pathlib import Path
import json
import os
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/commit-evidence-audit.yml"


@pytest.fixture
def audit_job():
    document = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    assert document["on"] == {"workflow_call": {}}
    return document["jobs"]["audit"]


def test_audit_job_skips_non_default_branch_push(audit_job):
    assert audit_job["if"] == (
        "github.event_name == 'pull_request' || "
        "github.ref_name == github.event.repository.default_branch"
    )


def test_audit_never_fails(audit_job):
    assert audit_job["continue-on-error"] == "true"
    assert audit_job["steps"][-1]["continue-on-error"] == "true"
    assert audit_job["steps"][-1]["if"] == "always()"
    assert all(step["continue-on-error"] == "true" for step in audit_job["steps"][:-1])


def test_audit_checkout_uses_central_sha_not_workflow_sha(audit_job):
    checkouts = [step for step in audit_job["steps"] if step.get("uses", "").startswith("actions/checkout@")]
    central = next(step for step in checkouts if step["with"].get("repository") == "jhw7500/automation")
    assert central["with"]["ref"] == "${{ steps.central.outputs.central_sha }}"
    assert "github.workflow_sha" not in WORKFLOW.read_text()
    resolver = next(step for step in audit_job["steps"] if step.get("id") == "central")
    assert "referenced_workflows" in resolver["with"]["script"]
    assert audit_job["permissions"] == {"actions": "read", "contents": "read"}


def test_audit_does_not_persist_credentials(audit_job):
    checkouts = [step for step in audit_job["steps"] if step.get("uses", "").startswith("actions/checkout@")]
    assert len(checkouts) == 2
    assert all(step["with"]["persist-credentials"] == "false" for step in checkouts)


def test_audit_checkouts_use_separate_paths(audit_job):
    checkouts = [step for step in audit_job["steps"] if step.get("uses", "").startswith("actions/checkout@")]
    assert [step["with"]["path"] for step in checkouts] == ["automation-audit", "audit-target"]
    assert checkouts[1]["with"]["fetch-depth"] == "0"
    run = audit_job["steps"][-1]["run"]
    assert "python3 -I automation-audit/scripts/audit_commits.py" in run
    assert "--repo-dir audit-target" in run


def test_audit_setup_failure_is_warning_and_summary(audit_job, tmp_path):
    summary = tmp_path / "summary.md"
    result = subprocess.run(
        ["bash", "-e", "-c", audit_job["steps"][-1]["run"]],
        cwd=tmp_path,
        env={**os.environ, "AUDIT_CENTRAL_OUTCOME": "failure", "AUDIT_SOURCE_OUTCOME": "skipped",
             "AUDIT_TARGET_OUTCOME": "skipped", "GITHUB_STEP_SUMMARY": str(summary)},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0
    assert "::warning title=commit-evidence-audit::" in result.stdout
    assert "setup" in summary.read_text().lower()


@pytest.mark.parametrize("mutation", [None, "ambiguous", "unbound_sha", "foreign_repo", "wrong_attempt"])
def test_resolver_binds_the_current_run_to_one_pinned_implementation(audit_job, mutation):
    sha = "a" * 40
    reference = {"path": f"jhw7500/automation/.github/workflows/commit-evidence-audit.yml@{sha}", "sha": sha}
    run = {"id": 7, "run_attempt": 1, "repository": {"full_name": "jhw7500/demo"},
           "event": "push", "status": "in_progress", "referenced_workflows": [reference]}
    if mutation == "ambiguous":
        run["referenced_workflows"].append(dict(reference))
    elif mutation == "unbound_sha":
        reference["sha"] = "b" * 40
    elif mutation == "foreign_repo":
        run["repository"]["full_name"] = "someone/else"
    elif mutation == "wrong_attempt":
        run["run_attempt"] = 2
    resolver = next(step for step in audit_job["steps"] if step.get("id") == "central")
    harness = (
        "const github={rest:{actions:{getWorkflowRunAttempt:async()=>({data:"
        + json.dumps(run) + "})}}};\n"
        "const context={repo:{owner:'jhw7500',repo:'demo'},eventName:'push'};\n"
        "const core={setOutput:(key,value)=>console.log(JSON.stringify({key,value}))};\n"
        "(async()=>{\n" + resolver["with"]["script"] + "\n})().catch(()=>process.exit(1));"
    )
    result = subprocess.run(
        ["node", "-e", harness], capture_output=True, text=True, check=False,
        env={**os.environ, "GITHUB_RUN_ID": "7", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_REPOSITORY": "jhw7500/demo"},
    )
    assert result.returncode == (0 if mutation is None else 1)
    if mutation is None:
        assert json.loads(result.stdout) == {"key": "central_sha", "value": sha}
    else:
        assert result.stdout == ""
