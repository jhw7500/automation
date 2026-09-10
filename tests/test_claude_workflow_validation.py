"""Issue #169: a successful action step is not proof of a model execution."""

import json
import os
import subprocess

import pytest

from test_review_workflow_logic import (
    _claude_upsert, _github_outputs, _load, _posted_state, node_required,
)


def _new_step(name):
    steps = _load("claude-code-review.yml")["jobs"]["claude-review"]["steps"]
    return next((step for step in steps if step.get("name") == name), {})


def _preflight(tmp_path, **changes):
    fixture = {
        "workflowRef": "o/r/.github/workflows/claude.yml@refs/pull/326/merge",
        "workflowSha": "a" * 40,
        "defaultBranch": "master",
        "defaultSha": "b" * 40,
        "currentBlob": "c" * 40,
        "defaultBlob": "c" * 40,
        **changes,
    }
    script = _new_step("Validate Claude caller workflow").get("with", {}).get("script", "")
    harness = r"""
const fx = JSON.parse(process.argv[1]);
const script = JSON.parse(process.argv[2]);
const calls = [], outputs = {}, failures = [];
const context = {repo: {owner: 'o', repo: 'r'}};
process.env.WORKFLOW_REF = fx.workflowRef;
process.env.WORKFLOW_SHA = fx.workflowSha;
const respond = (method, args, data) => {
  calls.push([method, args]);
  if (fx.errorMethod === method) {
    const error = new Error('sensitive remote error');
    error.status = fx.errorStatus;
    throw error;
  }
  return {data};
};
const github = {rest: {repos: {
  get: async args => respond('get', args, {default_branch: fx.defaultBranch}),
  getBranch: async args => respond('branch', args, {commit: {sha: fx.defaultSha}}),
  getContent: async args => respond(
    args.ref === fx.workflowSha ? 'current' : 'default', args,
    {type: fx.fileType || 'file', path: '.github/workflows/claude.yml',
     sha: args.ref === fx.workflowSha ? fx.currentBlob : fx.defaultBlob}
  ),
}}};
const core = {
  setOutput: (key, value) => {outputs[key] = value;},
  setFailed: message => failures.push(message),
};
(async () => {
  await new Function('github', 'context', 'core',
    'return (async () => {' + script + '})();')(github, context, core);
  console.log(JSON.stringify({calls, outputs, failures}));
})().catch(error => {console.error(error); process.exit(1);});
"""
    result = subprocess.run(
        ["node", "-e", harness, json.dumps(fixture), json.dumps(script)],
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@node_required
@pytest.mark.parametrize("ref", [
    "refs/pull/326/merge", "refs/heads/feature", "refs/heads/release/1.x",
])
def test_identical_caller_bytes_allow_review_on_different_commits(tmp_path, ref):
    result = _preflight(tmp_path, workflowRef=f"o/r/.github/workflows/claude.yml@{ref}")
    assert result["outputs"] == {"allowed": "true", "reason": ""}
    assert not result["failures"]
    assert [args["ref"] for method, args in result["calls"]
            if method in {"current", "default"}] == ["a" * 40, "b" * 40]
    assert all(args["owner"] == "o" and args["repo"] == "r"
               for _, args in result["calls"])


@node_required
def test_default_rollout_during_open_feature_pr_blocks_before_claim(tmp_path):
    result = _preflight(tmp_path, defaultBlob="d" * 40)
    assert result["outputs"] == {
        "allowed": "false", "reason": "workflow_validation_mismatch",
    }
    assert result["failures"] == ["workflow_validation_mismatch"]


@node_required
@pytest.mark.parametrize(("changes", "reason"), [
    ({"workflowRef": "other/repo/.github/workflows/claude.yml@refs/heads/main"},
     "workflow_validation_unavailable"),
    ({"workflowRef": "o/r/.github/workflows/../claude.yml@refs/heads/main"},
     "workflow_validation_unavailable"),
    ({"workflowSha": ""}, "workflow_validation_unavailable"),
    ({"defaultBranch": ""}, "workflow_validation_unavailable"),
    ({"defaultSha": "main"}, "workflow_validation_unavailable"),
    ({"currentBlob": ""}, "workflow_validation_unavailable"),
    ({"fileType": "symlink"}, "workflow_validation_unavailable"),
    ({"errorMethod": "default", "errorStatus": 404}, "workflow_validation_mismatch"),
    ({"errorMethod": "current", "errorStatus": 404}, "workflow_validation_unavailable"),
    ({"errorMethod": "default", "errorStatus": 403}, "workflow_validation_unavailable"),
    ({"errorMethod": "get", "errorStatus": 500}, "workflow_validation_unavailable"),
    ({"errorMethod": "branch", "errorStatus": None}, "workflow_validation_unavailable"),
])
def test_unverifiable_caller_fails_closed_with_bounded_diagnostic(tmp_path, changes, reason):
    result = _preflight(tmp_path, **changes)
    assert result["outputs"] == {"allowed": "false", "reason": reason}
    assert result["failures"] == [reason]
    assert "sensitive remote error" not in json.dumps(result)


def _execution(tmp_path, outcome, conclusion):
    output = tmp_path / "outputs"
    step = _new_step("Resolve Claude execution")
    result = subprocess.run(
        ["bash", "-c", step.get("run", "")],
        env={
            **os.environ, "GITHUB_OUTPUT": str(output),
            "ACTION_OUTCOME": outcome, "EXECUTION_CONCLUSION": conclusion,
        },
        text=True, capture_output=True, check=False,
    )
    return result, _github_outputs(output) if output.exists() else {}


@pytest.mark.parametrize(("outcome", "conclusion", "expected", "failed"), [
    ("success", "", ("0", "skipped", "provider_not_executed"), True),
    ("success", "success", ("1", "success", ""), False),
    ("success", "failure", ("1", "failure", "provider_failure"), True),
    ("failure", "success", ("1", "failure", "provider_failure"), True),
    ("failure", "", ("1", "failure", "provider_failure"), True),
    ("skipped", "", ("0", "skipped", ""), False),
    ("", "", ("0", "skipped", ""), False),
])
def test_execution_accounting_uses_action_conclusion(tmp_path, outcome, conclusion, expected, failed):
    result, outputs = _execution(tmp_path, outcome, conclusion)
    assert outputs == dict(zip(
        ("call_count", "provider_outcome", "failure_reason"), expected,
    ))
    assert (result.returncode != 0) is failed


@pytest.mark.parametrize(("outcome", "conclusion"), [
    ("success", "unknown"), ("cancelled", ""), ("skipped", "success"),
])
def test_uncertain_execution_does_not_invent_zero_call_metrics(tmp_path, outcome, conclusion):
    result, outputs = _execution(tmp_path, outcome, conclusion)
    assert result.returncode != 0
    assert outputs == {}


@node_required
def test_skipped_action_cannot_publish_a_seeded_valid_candidate(tmp_path):
    result, outputs = _execution(tmp_path, "success", "")
    assert result.returncode != 0
    calls = _claude_upsert(
        tmp_path, outputs["provider_outcome"], [], True,
        provider_failure_reason=outputs["failure_reason"],
    )
    body = next(call[1]["body"] for call in calls if call[0] == "create")
    assert _posted_state(body)["review_execution"] == "not_performed"
    assert _posted_state(body)["successful_head"] is None
    assert "REVIEW BODY OK" not in body


@node_required
@pytest.mark.parametrize("reason", [
    "workflow_validation_mismatch", "workflow_validation_unavailable", "provider_not_executed",
])
def test_nonexecution_publishes_reason_without_candidate_missing(tmp_path, reason):
    calls = _claude_upsert(
        tmp_path, "skipped", [], False, canonical_outcome="skipped",
        canonical_failure_reason="candidate_missing", provider_failure_reason=reason,
    )
    body = next(call[1]["body"] for call in calls if call[0] == "create")
    assert _posted_state(body)["review_execution"] == "not_performed"
    assert _posted_state(body)["attempt_status"] == "failure"
    assert f"`{reason}`" in body
    assert "candidate_missing" not in body
