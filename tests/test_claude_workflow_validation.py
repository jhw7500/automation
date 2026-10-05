"""Issue #169: a successful action step is not proof of a model execution."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from test_review_workflow_logic import (
    _claude_upsert, _github_outputs, _load, _posted_state, node_required,
)


FALLBACK_INPUTS = {
    "fallback_request_comment_id": "901",
    "fallback_request_nonce": "dd" * 16,
    "fallback_expected_head_sha": "aa" * 20,
    "fallback_expected_base_sha": "bb" * 20,
    "fallback_original_run_id": "34549275027",
    "fallback_original_run_attempt": "1",
    "fallback_release_commit": "444a7347aee169ed178aae80e8bd8d10eca52e02",
    "fallback_managed_diff_sha256": "cc" * 32,
}


def _run_fallback_shell_step(tmp_path, name, env):
    output = tmp_path / f"{name}.output"
    step = _new_step(name)
    result = subprocess.run(
        ["bash", "-c", step.get("run", "")],
        cwd=tmp_path,
        env={**os.environ, **env, "GITHUB_OUTPUT": str(output)},
        text=True,
        capture_output=True,
        check=False,
    )
    return result, _github_outputs(output) if output.exists() else {}


def _fallback_inputs_json(values):
    return json.dumps({name: values.get(name, "") for name in FALLBACK_INPUTS})


def _route_step_env():
    return {
        "FALLBACK_MODE": "normal",
        "DIFF_MODE": "full",
        "COMPUTED_FULL_DIFF_SHA256": "12" * 32,
        "FALLBACK_MANAGED_DIFF_SHA256": "",
        "FORCE_REVIEW": "false",
    }


def _fallback_admission(tmp_path, **changes):
    fixture = {
        "route": "managed",
        "request-comment-id": "901",
        "request-nonce": "dd" * 16,
        "expected-head-sha": "aa" * 20,
        "expected-base-sha": "bb" * 20,
        "original-run-id": "34549275027",
        "original-run-attempt": "1",
        "release-commit": "444a7347aee169ed178aae80e8bd8d10eca52e02",
        "managed-diff-sha256": "cc" * 32,
        "liveHead": "aa" * 20,
        "liveBase": "bb" * 20,
        **changes,
    }
    step = _new_step("Validate Claude fallback admission")
    script = step.get("with", {}).get("script", "")
    harness = r"""
const fx = JSON.parse(process.argv[1]);
const script = JSON.parse(process.argv[2]);
const outputs = {}, failures = [], calls = [];
const context = {repo: {owner: 'o', repo: 'r'}};
for (const [key, value] of Object.entries(fx)) {
  if (!['liveHead', 'liveBase'].includes(key)) process.env[key.toUpperCase().replaceAll('-', '_')] = value;
}
const inputMap = {
  'request-comment-id': 'INPUT_REQUEST_COMMENT_ID',
  'request-nonce': 'INPUT_REQUEST_NONCE',
  'expected-head-sha': 'INPUT_EXPECTED_HEAD_SHA',
  'expected-base-sha': 'INPUT_EXPECTED_BASE_SHA',
  'original-run-id': 'INPUT_ORIGINAL_RUN_ID',
  'original-run-attempt': 'INPUT_ORIGINAL_RUN_ATTEMPT',
  'release-commit': 'INPUT_RELEASE_COMMIT',
  'managed-diff-sha256': 'INPUT_MANAGED_DIFF_SHA256',
};
for (const [key, envName] of Object.entries(inputMap)) process.env[envName] = {
  'request-comment-id': '901',
  'request-nonce': 'dddddddddddddddddddddddddddddddd',
  'expected-head-sha': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
  'expected-base-sha': 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
  'original-run-id': '34549275027',
  'original-run-attempt': '1',
  'release-commit': '444a7347aee169ed178aae80e8bd8d10eca52e02',
  'managed-diff-sha256': 'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',
}[key];
process.env.PR_NUMBER = '109';
const github = {rest: {pulls: {get: async args => {
  calls.push(args);
  return {data: {head: {sha: fx.liveHead}, base: {sha: fx.liveBase}}};
}}}};
const core = {
  setOutput: (key, value) => {outputs[key] = value;},
  setFailed: message => failures.push(message),
};
(async () => {
  await new Function('github', 'context', 'core',
    'return (async () => {' + script + '})();')(github, context, core);
  console.log(JSON.stringify({outputs, failures, calls}));
})().catch(error => {console.error(error); process.exit(1);});
"""
    result = subprocess.run(
        ["node", "-e", harness, json.dumps(fixture), json.dumps(script)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_fallback_workflow_call_inputs_are_exact_optional_strings():
    inputs = _load("claude-code-review.yml")["on"]["workflow_call"]["inputs"]
    for name in FALLBACK_INPUTS:
        assert inputs[name] == {"type": "string", "required": "false", "default": ""}


@pytest.mark.parametrize("present", range(1, 8))
def test_partial_fallback_inputs_fail_before_diff(tmp_path, present):
    values = dict(list(FALLBACK_INPUTS.items())[:present])
    result, outputs = _run_fallback_shell_step(
        tmp_path, "Resolve Claude fallback mode",
        {"FALLBACK_INPUTS_JSON": _fallback_inputs_json(values)},
    )
    assert result.returncode != 0
    assert outputs == {}


@pytest.mark.parametrize(("values", "mode"), (({}, "normal"), (FALLBACK_INPUTS, "fallback")))
def test_fallback_mode_is_all_or_none(tmp_path, values, mode):
    result, outputs = _run_fallback_shell_step(
        tmp_path, "Resolve Claude fallback mode",
        {"FALLBACK_INPUTS_JSON": _fallback_inputs_json(values)},
    )
    assert result.returncode == 0, result.stderr
    assert outputs == {"mode": mode}


@pytest.mark.parametrize("step_name", (
    "Resolve Claude fallback mode", "Resolve Claude invocation route",
))
@pytest.mark.parametrize("module_name", ("json", "pathlib"))
def test_fallback_python_steps_ignore_checkout_module_shadowing(
    tmp_path, step_name, module_name,
):
    (tmp_path / f"{module_name}.py").write_text(
        "open('consumer-code-ran', 'w').write('executed')\n",
        encoding="utf-8",
    )
    env = (
        {"FALLBACK_INPUTS_JSON": _fallback_inputs_json({})}
        if step_name == "Resolve Claude fallback mode"
        else _route_step_env()
    )
    result, outputs = _run_fallback_shell_step(tmp_path, step_name, env)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "consumer-code-ran").exists()
    assert outputs


@node_required
@pytest.mark.parametrize("coordinate", tuple(FALLBACK_INPUTS))
def test_fallback_admission_rejects_every_immutable_coordinate_mutation(tmp_path, coordinate):
    output_name = {
        "fallback_request_comment_id": "request-comment-id",
        "fallback_request_nonce": "request-nonce",
        "fallback_expected_head_sha": "expected-head-sha",
        "fallback_expected_base_sha": "expected-base-sha",
        "fallback_original_run_id": "original-run-id",
        "fallback_original_run_attempt": "original-run-attempt",
        "fallback_release_commit": "release-commit",
        "fallback_managed_diff_sha256": "managed-diff-sha256",
    }[coordinate]
    result = _fallback_admission(tmp_path, **{output_name: "mismatch"})
    assert result["failures"] == ["fallback_admission_mismatch"]
    assert result["outputs"] == {"allowed": "false", "reason": "fallback_admission_mismatch"}


@node_required
@pytest.mark.parametrize("change", ({"route": "interactive"}, {"liveHead": "ee" * 20}, {"liveBase": "ff" * 20}))
def test_fallback_admission_requires_managed_route_and_live_pr_coordinates(tmp_path, change):
    result = _fallback_admission(tmp_path, **change)
    assert result["failures"] == ["fallback_admission_mismatch"]
    assert result["outputs"] == {"allowed": "false", "reason": "fallback_admission_mismatch"}


@node_required
def test_fallback_admission_accepts_exact_replayed_coordinates(tmp_path):
    result = _fallback_admission(tmp_path)
    assert result["failures"] == []
    assert result["outputs"] == {"allowed": "true", "reason": ""}
    assert result["calls"] == [{
        "owner": "o", "repo": "r", "pull_number": 109, "request": {"timeout": 15000},
    }]


def test_fallback_requires_exact_recomputed_full_diff(tmp_path):
    result, outputs = _run_fallback_shell_step(
        tmp_path,
        "Resolve Claude invocation route",
        {
            "FALLBACK_MODE": "fallback",
            "DIFF_MODE": "full",
            "COMPUTED_FULL_DIFF_SHA256": "12" * 32,
            "FALLBACK_MANAGED_DIFF_SHA256": "34" * 32,
            "FORCE_REVIEW": "false",
            **{name.upper(): value for name, value in FALLBACK_INPUTS.items()},
            "AUTOMATIC_COMMENT_ID": "887",
            "AUTOMATIC_STATE_SHA256": "56" * 32,
        },
    )
    assert result.returncode != 0
    assert outputs == {}


@pytest.mark.parametrize(
    ("force_review", "expected"),
    (("false", {"kind": "automatic"}), ("true", {"kind": "authorized_override"})),
)
def test_normal_claude_invocation_route_is_explicit(tmp_path, force_review, expected):
    result, outputs = _run_fallback_shell_step(
        tmp_path,
        "Resolve Claude invocation route",
        {
            "FALLBACK_MODE": "normal",
            "DIFF_MODE": "full",
            "COMPUTED_FULL_DIFF_SHA256": "12" * 32,
            "FALLBACK_MANAGED_DIFF_SHA256": "",
            "FORCE_REVIEW": force_review,
        },
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(outputs["invocation_route_json"]) == expected
    assert outputs["state_route_json"] == ""


def test_fallback_route_json_binds_fresh_admission_outputs(tmp_path):
    result, outputs = _run_fallback_shell_step(
        tmp_path,
        "Resolve Claude invocation route",
        {
            "FALLBACK_MODE": "fallback",
            "DIFF_MODE": "full",
            "COMPUTED_FULL_DIFF_SHA256": "cc" * 32,
            "FORCE_REVIEW": "false",
            **{name.upper(): value for name, value in FALLBACK_INPUTS.items()},
            "AUTOMATIC_COMMENT_ID": "887",
            "AUTOMATIC_STATE_SHA256": "56" * 32,
        },
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(outputs["invocation_route_json"]) == {
        "kind": "default_branch_rollout_fallback",
        "request_comment_id": 901,
        "request_nonce": "dd" * 16,
        "original_run_id": 34549275027,
        "original_run_attempt": 1,
        "expected_base_sha": "bb" * 20,
        "release_commit": "444a7347aee169ed178aae80e8bd8d10eca52e02",
        "managed_diff_sha256": "cc" * 32,
        "automatic_comment_id": 887,
        "automatic_state_sha256": "56" * 32,
    }
    assert json.loads(outputs["state_route_json"]) == {
        "managed_diff_sha256": "cc" * 32,
        "original_failed_run_id": 34549275027,
        "release_commit": "444a7347aee169ed178aae80e8bd8d10eca52e02",
        "request_comment_id": 901,
        "reviewed_base_sha": "bb" * 20,
        "route": "default_branch_rollout_fallback",
    }


@node_required
def test_fallback_does_not_bypass_caller_validation(tmp_path):
    result = _preflight(tmp_path, currentBlob="11" * 20, defaultBlob="22" * 20)
    assert result["outputs"] == {
        "allowed": "false", "reason": "workflow_validation_mismatch",
    }


def test_fallback_still_requires_live_request_policy():
    workflow = _load("claude-code-review.yml")
    assert workflow["jobs"]["claude-review"]["if"] == (
        "needs.check-enabled.outputs.enabled == 'true' && "
        "needs.check-enabled.outputs.policy_run == 'true'"
    )


def _new_step(name):
    steps = _load("claude-code-review.yml")["jobs"]["claude-review"]["steps"]
    return next((step for step in steps if step.get("name") == name), {})


def _preflight(tmp_path, script=None, **changes):
    fixture = {
        "workflowRef": "o/r/.github/workflows/claude.yml@refs/pull/326/merge",
        "workflowSha": "a" * 40,
        "defaultBranch": "master",
        "defaultSha": "b" * 40,
        "currentBlob": "c" * 40,
        "defaultBlob": "c" * 40,
        **changes,
    }
    if script is None:
        script = _new_step("Validate Claude caller workflow").get("with", {}).get("script", "")
    harness = r"""
const fx = JSON.parse(process.argv[1]);
const script = JSON.parse(process.argv[2]);
const calls = [], outputs = {}, failures = [], notices = [], summary = [];
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
     sha: args.ref === fx.workflowSha ? fx.currentBlob : fx.defaultBlob,
     ...((args.ref === fx.workflowSha ? fx.currentContent : fx.defaultContent) === undefined ? {} : {
       encoding: 'base64',
       content: Buffer.from(args.ref === fx.workflowSha ? fx.currentContent : fx.defaultContent)
         .toString('base64').replace(/(.{60})/g, '$1\n')})}
  ),
}}};
const core = {
  setOutput: (key, value) => {outputs[key] = value;},
  setFailed: message => failures.push(message),
};
if (!fx.legacyCore) {
  core.notice = message => notices.push(message);
  core.summary = {addRaw(text) { summary.push(text); return this; }, write: async () => {}};
}
(async () => {
  await new Function('github', 'context', 'core',
    'return (async () => {' + script + '})();')(github, context, core);
  console.log(JSON.stringify({calls, outputs, failures, notices, summary}));
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


OLD_PIN = "3d60f3baaa45820f7260912efea3ffcf45c10b07"
NEW_PIN = "444a7347aee169ed178aae80e8bd8d10eca52e02"
RECOVERY_ANCHOR = "docs/workflows/contracts.md#open-work-branches-after-a-fleet-rollout"


def _caller(*uses):
    lines = "".join(f"    uses: {value}\n" for value in uses)
    return f"name: Claude\non:\n  pull_request:\njobs:\n  claude:\n{lines}    secrets: inherit\n"


def _pin_ref(pin):
    return f"jhw7500/automation/.github/workflows/claude.yml@{pin}"


def _v182_script():
    from release_fixture_helpers import V182_CALLER_PIN_DIAGNOSTICS_BOUNDARY_COMMIT
    from scripts.verify_workflow_release import VerifiedCommitTree, _load_release_yaml

    root = Path(__file__).resolve().parents[1]
    workflow = _load_release_yaml(VerifiedCommitTree.open(
        root, V182_CALLER_PIN_DIAGNOSTICS_BOUNDARY_COMMIT,
    ).read_file(".github/workflows/claude-code-review.yml"), reject_duplicate_keys=True)
    steps = workflow["jobs"]["claude-review"]["steps"]
    step = next(step for step in steps if step.get("name") == "Validate Claude caller workflow")
    return step["with"]["script"]


@node_required
def test_mismatch_names_running_and_default_branch_caller_pins(tmp_path):
    result = _preflight(
        tmp_path, defaultBlob="d" * 40,
        currentContent=_caller(_pin_ref(OLD_PIN)),
        defaultContent=_caller(f"'{_pin_ref(NEW_PIN)}' # v1.76"),
    )
    assert result["outputs"] == {"allowed": "false", "reason": "workflow_validation_mismatch"}
    assert result["failures"] == ["workflow_validation_mismatch"]
    assert len(result["notices"]) == 1
    notice = result["notices"][0]
    assert f"running caller pin {OLD_PIN}" in notice
    assert f"default-branch caller pin {NEW_PIN}" in notice
    assert "omit --ref" in notice and RECOVERY_ANCHOR in notice
    assert result["summary"] == [f"workflow_validation_mismatch: {notice}\n"]


@node_required
@pytest.mark.parametrize(("current", "default", "running", "branch"), [
    (_caller("./.github/workflows/local.yml"), _caller(_pin_ref(NEW_PIN)), "unavailable", NEW_PIN),
    (_caller(_pin_ref(OLD_PIN), _pin_ref(OLD_PIN)), _caller(_pin_ref(NEW_PIN)), "ambiguous", NEW_PIN),
    (_caller(_pin_ref(OLD_PIN)), _caller(_pin_ref(OLD_PIN), _pin_ref(NEW_PIN)), OLD_PIN, "ambiguous"),
    (_caller(_pin_ref("abc123")), None, "unavailable", "unavailable"),
    (None, _caller(_pin_ref(NEW_PIN)), "unavailable", NEW_PIN),
])
def test_mismatch_pin_diagnostic_never_guesses(tmp_path, current, default, running, branch):
    fixture = {"defaultBlob": "d" * 40}
    if current is not None:
        fixture["currentContent"] = current
    if default is not None:
        fixture["defaultContent"] = default
    result = _preflight(tmp_path, **fixture)
    assert result["outputs"] == {"allowed": "false", "reason": "workflow_validation_mismatch"}
    assert len(result["notices"]) == 1
    assert f"running caller pin {running} /" in result["notices"][0]
    assert f"default-branch caller pin {branch} /" in result["notices"][0]


@node_required
def test_missing_default_caller_reports_unavailable_default_pin(tmp_path):
    result = _preflight(
        tmp_path, errorMethod="default", errorStatus=404,
        currentContent=_caller(_pin_ref(OLD_PIN)),
    )
    assert result["outputs"] == {"allowed": "false", "reason": "workflow_validation_mismatch"}
    assert len(result["notices"]) == 1
    assert f"running caller pin {OLD_PIN} / default-branch caller pin unavailable /" in result["notices"][0]


@node_required
@pytest.mark.parametrize("changes", [
    {},
    {"currentContent": _caller(_pin_ref(OLD_PIN)), "defaultContent": _caller(_pin_ref(OLD_PIN))},
    {"defaultBlob": "d" * 40},
    {"defaultBlob": "d" * 40, "currentContent": _caller(_pin_ref(OLD_PIN)),
     "defaultContent": _caller(_pin_ref(NEW_PIN))},
    {"errorMethod": "default", "errorStatus": 404, "currentContent": _caller(_pin_ref(OLD_PIN))},
    {"errorMethod": "default", "errorStatus": 403},
    {"errorMethod": "current", "errorStatus": 404},
    {"fileType": "symlink", "defaultBlob": "d" * 40},
    {"workflowSha": ""},
    {"defaultBlob": "d" * 40, "currentContent": _caller(_pin_ref(OLD_PIN)), "legacyCore": True},
])
def test_pin_diagnostic_leaves_the_v182_decision_unchanged(tmp_path, changes):
    old = _preflight(tmp_path, script=_v182_script(), **changes)
    new = _preflight(tmp_path, **changes)
    for key in ("outputs", "failures", "calls"):
        assert new[key] == old[key], key
    assert not old["notices"] and not old["summary"]
    mismatch = new["outputs"]["reason"] == "workflow_validation_mismatch"
    assert len(new["notices"]) == (mismatch and not changes.get("legacyCore"))
