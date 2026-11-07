"""Contracts for the non-blocking, trusted-code commit audit workflow."""

from pathlib import Path
import base64
import hashlib
import json
import os
import re
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/commit-evidence-audit.yml"
SUMMARY_LOG_PREFIX = "automation-commit-evidence-summary:v1:"


@pytest.fixture
def audit_job():
    document = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    assert document["on"] == {"workflow_call": {}}
    return document["jobs"]["audit"]


def _logged_summary(stdout):
    records = [line for line in stdout.splitlines() if line.startswith(SUMMARY_LOG_PREFIX)]
    assert records, "actual step summary was not emitted to the log"
    match = re.fullmatch(
        re.escape(SUMMARY_LOG_PREFIX) + r"begin:([0-9]+):([0-9a-f]{64})", records[0]
    )
    assert match is not None
    assert records[-1] == SUMMARY_LOG_PREFIX + "end"
    data_prefix = SUMMARY_LOG_PREFIX + "data:"
    assert all(line.startswith(data_prefix) for line in records[1:-1])
    chunks = [line[len(data_prefix):] for line in records[1:-1]]
    assert chunks and all(1 <= len(chunk) <= 76 for chunk in chunks)
    payload = base64.b64decode("".join(chunks), validate=True)
    assert len(payload) == int(match[1])
    assert hashlib.sha256(payload).hexdigest() == match[2]
    return payload


def _run_report_with_summary(audit_job, tmp_path, payload, exit_code=0):
    script = tmp_path / "automation-audit/scripts/audit_commits.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "from pathlib import Path\nimport sys\n"
        + (
            "Path(sys.argv[sys.argv.index('--summary-path') + 1]).write_bytes("
            + repr(payload) + ")\n"
            if payload is not None else ""
        )
        + f"raise SystemExit({exit_code})\n"
    )
    summary = tmp_path / "summary.md"
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c",
         audit_job["steps"][-1]["run"]],
        cwd=tmp_path,
        env={
            **os.environ,
            "AUDIT_CENTRAL_OUTCOME": "success",
            "AUDIT_SOURCE_OUTCOME": "success",
            "AUDIT_TARGET_OUTCOME": "success",
            "AUDIT_AUTOMATION_COMMIT": "a" * 40,
            "AUDIT_EVENT_NAME": "pull_request",
            "GITHUB_EVENT_PATH": str(tmp_path / "event.json"),
            "GITHUB_STEP_SUMMARY": str(summary),
        },
        capture_output=True, text=True, check=False, timeout=5,
    )
    return result, summary


@pytest.mark.parametrize(
    ("event", "ref_type", "ref_name", "expected"),
    [
        ("pull_request", "branch", "7/merge", True),
        ("push", "branch", "main", True),
        ("push", "branch", "feature", False),
        ("push", "tag", "main", False),
        ("push", "tag", "v1.87", False),
        ("workflow_dispatch", "branch", "main", False),
    ],
)
def test_audit_job_selects_only_prs_and_default_branch_pushes(
    audit_job, event, ref_type, ref_name, expected
):
    github = {
        "event_name": event, "ref_type": ref_type, "ref_name": ref_name,
        "event": {"repository": {"default_branch": "main"}},
    }
    # The workflow's boolean operators and comparisons are also JavaScript;
    # evaluate the actual condition against event fixtures rather than its text.
    program = (
        "const github=" + json.dumps(github) + ";\n"
        "process.stdout.write(JSON.stringify(Boolean(" + audit_job["if"] + ")));"
    )
    result = subprocess.run(
        ["node", "-e", program], capture_output=True, text=True, check=True
    )
    assert json.loads(result.stdout) is expected


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
    assert _logged_summary(result.stdout) == summary.read_bytes()


def test_audit_log_preserves_actual_summary_without_evaluating_its_text(audit_job, tmp_path):
    payload = (
        "# 감사 보고서 한글 café 😀\n\n| SHA | 판정 |\n| abcdef0 | ok |\n".encode()
        + b"::error::injected\n$(touch executed) `touch executed`\n\x1b[31m\x00\n"
    )
    result, summary = _run_report_with_summary(audit_job, tmp_path, payload)
    assert result.returncode == 0
    assert summary.read_bytes() == payload
    assert _logged_summary(result.stdout) == payload
    assert "::error::injected" not in result.stdout
    assert "$(touch executed)" not in result.stdout
    assert "\x1b" not in result.stdout
    assert not (tmp_path / "executed").exists()


def test_audit_log_uses_bounded_lines_for_large_summaries(audit_job, tmp_path):
    payload = b"| abcdef0 | audited commit | - | - | ok |\n" * 500
    result, summary = _run_report_with_summary(audit_job, tmp_path, payload)
    assert result.returncode == 0
    assert _logged_summary(result.stdout) == summary.read_bytes() == payload
    assert len(result.stdout.splitlines()) > 2


def test_audit_log_does_not_mask_the_audit_exit_status(audit_job, tmp_path):
    payload = b"Audit failed after writing its report.\n"
    result, summary = _run_report_with_summary(audit_job, tmp_path, payload, exit_code=7)
    assert result.returncode == 7
    assert _logged_summary(result.stdout) == summary.read_bytes() == payload


@pytest.mark.parametrize("payload", [None, b"", b"x" * (1024 * 1024 + 1)],
                         ids=["missing", "empty", "oversized"])
def test_audit_log_skips_unavailable_or_oversized_summaries(audit_job, tmp_path, payload):
    result, _ = _run_report_with_summary(audit_job, tmp_path, payload)
    assert result.returncode == 0
    assert SUMMARY_LOG_PREFIX not in result.stdout


@pytest.mark.parametrize("kind", ["symlink", "directory", "fifo"])
def test_audit_log_never_reads_nonregular_summary_paths(audit_job, tmp_path, kind):
    summary = tmp_path / "summary.md"
    if kind == "symlink":
        target = tmp_path / "unrelated.md"
        target.write_text("must not be logged\n")
        summary.symlink_to(target)
    elif kind == "directory":
        summary.mkdir()
    else:
        os.mkfifo(summary)
    result, _ = _run_report_with_summary(audit_job, tmp_path, None)
    assert result.returncode == 0
    assert SUMMARY_LOG_PREFIX not in result.stdout


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
