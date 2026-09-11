#!/usr/bin/env python3
"""Behavioral tests for authenticated Claude rollout fallback admission."""

from __future__ import annotations

import importlib.util
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / ".github/actions/claude-rollout-fallback/contract.py"
SPEC = importlib.util.spec_from_file_location("claude_rollout_fallback_contract", CONTRACT_PATH)
assert SPEC is not None and SPEC.loader is not None
contract = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = contract
SPEC.loader.exec_module(contract)

ContractError = contract.ContractError
FallbackRequest = contract.FallbackRequest
canonical_request_body = contract.canonical_request_body
classify_event = contract.classify_event
parse_request_body = contract.parse_request_body


REQUEST = {
    "expected_base_sha": "b" * 40,
    "expected_head_sha": "a" * 40,
    "managed_diff_sha256": "c" * 64,
    "nonce": "d" * 32,
    "original_run_attempt": 1,
    "original_run_id": 34549275027,
    "pr": 109,
    "release_commit": "444a7347aee169ed178aae80e8bd8d10eca52e02",
    "repository": "jhw7500/gstApp",
}
REQUEST_BODY = (
    "@claude managed rollout review for PR 109\n"
    '<!-- automation:claude-rollout-review-request:v1 {"expected_base_sha":'
    '"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","expected_head_sha":'
    '"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","managed_diff_sha256":'
    '"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",'
    '"nonce":"dddddddddddddddddddddddddddddddd","original_run_attempt":1,'
    '"original_run_id":34549275027,"pr":109,"release_commit":'
    '"444a7347aee169ed178aae80e8bd8d10eca52e02","repository":"jhw7500/gstApp"} -->'
)


def issue_comment_event(body: object = REQUEST_BODY) -> dict[str, object]:
    return {"action": "created", "comment": {"id": 901, "body": body}}


def mutated_request_body(change: str) -> object:
    marker_prefix = "<!-- automation:claude-rollout-review-request:v1 "
    payload_text = REQUEST_BODY.split(marker_prefix, 1)[1][:-4]
    payload = dict(REQUEST)
    if change == "duplicate_key":
        payload_text = payload_text[:-1] + ',"pr":109}'
    elif change == "extra_key":
        payload["extra"] = True
        payload_text = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    elif change == "missing_key":
        payload.pop("nonce")
        payload_text = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    elif change == "uppercase_head":
        payload["expected_head_sha"] = "A" * 40
        payload_text = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    elif change == "zero_run":
        payload["original_run_id"] = 0
        payload_text = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    elif change == "unsafe_integer":
        payload["original_run_id"] = 2**53
        payload_text = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    elif change == "short_nonce":
        payload["nonce"] = "d" * 31
        payload_text = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    elif change == "control_character":
        payload["repository"] = "jhw7500/gstApp\u0001"
        payload_text = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    elif change == "malformed_json":
        payload_text = payload_text[:-1]
    body: object = (
        "@claude managed rollout review for PR 109\n"
        f"{marker_prefix}{payload_text} -->"
    )
    if change == "second_marker":
        body = f"{body}\n{marker_prefix}{{}} -->"
    elif change == "quoted_marker":
        body = "> " + str(body)
    elif change == "invalid_unicode":
        body = str(body) + "\ud800"
    elif change == "oversize":
        body = str(body) + "x" * 4096
    return body


def test_request_parser_accepts_only_the_exact_canonical_body() -> None:
    request = parse_request_body(REQUEST_BODY)
    assert request == FallbackRequest(**REQUEST)
    assert canonical_request_body(request) == REQUEST_BODY
    assert parse_request_body(REQUEST_BODY + "\n") == request


@pytest.mark.parametrize(
    "change",
    [
        "duplicate_key",
        "extra_key",
        "missing_key",
        "uppercase_head",
        "zero_run",
        "unsafe_integer",
        "short_nonce",
        "second_marker",
        "quoted_marker",
        "control_character",
        "invalid_unicode",
        "oversize",
        "malformed_json",
    ],
)
def test_request_parser_rejects_noncanonical_bytes(change: str) -> None:
    with pytest.raises(ContractError, match="^request_invalid$"):
        parse_request_body(mutated_request_body(change))


def test_classifier_never_routes_reserved_invalid_text_to_interactive() -> None:
    bodies = (
        "@claude managed rollout review for PR 109",
        "> @claude managed rollout review for PR 109",
        "<!-- automation:claude-rollout-review-request:v1 {} -->",
    )
    for body in bodies:
        result = classify_event("issue_comment", issue_comment_event(body))
        assert (result.route, result.reason, result.request) == (
            "invalid",
            "request_invalid",
            None,
        )


def test_classifier_routes_ordinary_text_without_server_evidence() -> None:
    result = classify_event("issue_comment", issue_comment_event("@claude explain this"))
    assert (result.route, result.reason, result.request) == ("interactive", "", None)


def test_classifier_accepts_only_an_issue_comment_managed_candidate() -> None:
    result = classify_event("issue_comment", issue_comment_event())
    assert (result.route, result.reason, result.request) == (
        "managed_candidate",
        "",
        FallbackRequest(**REQUEST),
    )


def test_classifier_rejects_reserved_tokens_in_other_event_text_fields() -> None:
    events = (
        ("pull_request_review_comment", {"comment": {"body": REQUEST_BODY}}),
        ("pull_request_review", {"review": {"body": REQUEST_BODY}}),
        ("issues", {"issue": {"title": REQUEST_BODY, "body": "ordinary"}}),
    )
    for event_name, event in events:
        result = classify_event(event_name, event)
        assert (result.route, result.reason, result.request) == (
            "invalid",
            "request_invalid",
            None,
        )


EvidenceBundle = contract.EvidenceBundle
admit = contract.admit

AUTOMATIC_STATE = {
    "schema": 3,
    "reviewer": "claude",
    "pr": 109,
    "run_id": 34549275027,
    "run_attempt": 1,
    "attempt_head": "a" * 40,
    "successful_head": None,
    "attempt_status": "failure",
    "diff_mode": "full",
    "review_execution": "not_performed",
    "full_diff_sha256": None,
    "quality_schema": 1,
    "accepted_count": None,
    "filtered_count": None,
    "normalized_count": None,
    "filtered_max_severity": None,
    "failure_reason": "workflow_validation_mismatch",
}
AUTOMATIC_STATE_BYTES = json.dumps(
    AUTOMATIC_STATE, ensure_ascii=True, separators=(",", ":")
).encode("ascii")
AUTOMATIC_BODY = (
    "## Claude Code Review (latest)\n"
    "<!-- automation:claude-code-review:v3 -->\n"
    f"<!-- automation-state:{AUTOMATIC_STATE_BYTES.decode('ascii')} -->\n\n"
    "- Status: failure\n"
    "- Execution: not_performed\n"
    "- Run: https://github.com/jhw7500/gstApp/actions/runs/34549275027\n"
    "- Last attempt: failure "
    "(https://github.com/jhw7500/gstApp/actions/runs/34549275027)\n\n"
    "### Error\n\nClaude review failed: `workflow_validation_mismatch`."
)


def _request_comment(comment_id: int = 901, body: str = REQUEST_BODY) -> dict[str, object]:
    return {
        "id": comment_id,
        "body": body,
        "created_at": "2026-09-11T03:04:06Z",
        "author_association": "MEMBER",
        "user": {"login": "jhw7500", "type": "User"},
    }


def _automatic_comment(comment_id: int = 887) -> dict[str, object]:
    return {
        "id": comment_id,
        "body": AUTOMATIC_BODY,
        "created_at": "2026-09-11T03:04:05Z",
        "author_association": "NONE",
        "user": {"login": "github-actions[bot]", "type": "Bot"},
    }


def _review_job() -> dict[str, object]:
    return {
        "id": 500,
        "name": "Claude Code Review / claude-review",
        "run_id": 34549275027,
        "run_attempt": 1,
        "status": "completed",
        "conclusion": "failure",
        "steps": [
            {"number": 1, "name": "Prepare review diff", "status": "completed", "conclusion": "success"},
            {"number": 2, "name": "Validate Claude caller workflow", "status": "completed", "conclusion": "failure"},
            {"number": 3, "name": "Claim Claude review budget", "status": "completed", "conclusion": "skipped"},
            {"number": 4, "name": "Run Claude Code Review", "status": "completed", "conclusion": "skipped"},
        ],
    }


def valid_bundle() -> EvidenceBundle:
    event_comment = _request_comment()
    event = {
        "action": "created",
        "repository": {"full_name": "jhw7500/gstApp"},
        "issue": {
            "number": 109,
            "state": "open",
            "pull_request": {"url": "https://api.github.com/repos/jhw7500/gstApp/pulls/109"},
        },
        "comment": event_comment,
    }
    pull_request = {
        "number": 109,
        "state": "open",
        "merged": False,
        "head": {
            "sha": "a" * 40,
            "repo": {"full_name": "jhw7500/gstApp", "fork": False},
        },
        "base": {
            "sha": "b" * 40,
            "repo": {"full_name": "jhw7500/gstApp"},
        },
    }
    original_run = {
        "id": 34549275027,
        "run_attempt": 1,
        "name": "Claude Code Review",
        "event": "pull_request",
        "status": "completed",
        "conclusion": "failure",
        "head_sha": "a" * 40,
        "updated_at": "2026-09-11T03:04:05Z",
        "repository": {"full_name": "jhw7500/gstApp"},
        "pull_requests": [
            {"number": 109, "head": {"sha": "a" * 40}, "base": {"sha": "b" * 40}}
        ],
        "referenced_workflows": [
            {
                "path": "jhw7500/automation/.github/workflows/claude-code-review.yml@"
                "refs/tags/v1.77",
                "sha": "444a7347aee169ed178aae80e8bd8d10eca52e02",
                "ref": "refs/tags/v1.77",
            }
        ],
    }
    return EvidenceBundle(
        event_name="issue_comment",
        event=event,
        comment=_request_comment(),
        pull_request=pull_request,
        original_run=original_run,
        original_jobs={"total_count": 1, "jobs": [_review_job()]},
        issue_comments=[_automatic_comment(), _request_comment()],
    )


def _fallback_success_comment() -> dict[str, object]:
    state = {
        **AUTOMATIC_STATE,
        "run_id": 34550000000,
        "attempt_status": "success",
        "successful_head": "a" * 40,
        "review_execution": "performed",
        "full_diff_sha256": "c" * 64,
        "accepted_count": 0,
        "filtered_count": 0,
        "normalized_count": 0,
        "filtered_max_severity": "none",
    }
    state.pop("failure_reason")
    state["route"] = {
        "managed_diff_sha256": "c" * 64,
        "original_failed_run_id": 34549275027,
        "release_commit": "444a7347aee169ed178aae80e8bd8d10eca52e02",
        "request_comment_id": 901,
        "reviewed_base_sha": "b" * 40,
        "route": "default_branch_rollout_fallback",
    }
    encoded = json.dumps(state, ensure_ascii=True, separators=(",", ":"))
    return {
        "id": 888,
        "body": (
            "## Claude Code Review (latest)\n"
            "<!-- automation:claude-code-review:v3 -->\n"
            f"<!-- automation-state:{encoded} -->\n\n- Status: success\n\n### New findings\nNone"
        ),
        "created_at": "2026-09-11T03:05:00Z",
        "user": {"login": "github-actions[bot]", "type": "Bot"},
    }


def mutated_bundle(change: str) -> EvidenceBundle:
    bundle = valid_bundle()
    values = {name: deepcopy(getattr(bundle, name)) for name in EvidenceBundle.__dataclass_fields__}
    if change == "event_not_created":
        values["event"]["action"] = "edited"
    elif change == "comment_bytes_changed":
        values["comment"]["body"] = REQUEST_BODY + "\n"
    elif change == "comment_timestamp_changed":
        values["comment"]["created_at"] = "2026-09-11T03:04:07Z"
    elif change == "actor_not_collaborator":
        values["comment"]["author_association"] = "CONTRIBUTOR"
    elif change == "pr_closed":
        values["pull_request"]["state"] = "closed"
    elif change == "fork_head":
        values["pull_request"]["head"]["repo"]["fork"] = True
    elif change == "head_changed":
        values["pull_request"]["head"]["sha"] = "f" * 40
    elif change == "base_changed":
        values["pull_request"]["base"]["sha"] = "f" * 40
    elif change == "wrong_attempt":
        values["original_run"]["run_attempt"] = 2
    elif change == "wrong_job_run":
        values["original_jobs"]["jobs"][0]["run_id"] = 34549275028
    elif change == "request_before_failure":
        values["comment"]["created_at"] = "2026-09-11T03:04:04Z"
        values["event"]["comment"]["created_at"] = "2026-09-11T03:04:04Z"
    elif change == "wrong_release":
        values["original_run"]["referenced_workflows"][0]["sha"] = "5" * 40
    elif change == "provider_entered":
        values["original_jobs"]["jobs"][0]["steps"][3]["conclusion"] = "success"
    elif change == "budget_claimed":
        values["original_jobs"]["jobs"][0]["steps"][2]["conclusion"] = "success"
    elif change == "extra_failed_job":
        values["original_jobs"]["jobs"].append(
            {
                "id": 501,
                "name": "Claude Code Review / post-review",
                "run_id": 34549275027,
                "run_attempt": 1,
                "status": "completed",
                "conclusion": "failure",
                "steps": [],
            }
        )
        values["original_jobs"]["total_count"] = 2
    elif change == "extra_failed_step":
        values["original_jobs"]["jobs"][0]["steps"].append(
            {
                "number": 5,
                "name": "Publish unexpected failure",
                "status": "completed",
                "conclusion": "failure",
            }
        )
    elif change == "duplicate_request":
        duplicate = FallbackRequest.from_dict({**REQUEST, "nonce": "e" * 32})
        values["issue_comments"].append(_request_comment(902, canonical_request_body(duplicate)))
    elif change == "duplicate_state":
        values["issue_comments"].append(_automatic_comment(886))
    elif change == "existing_fallback":
        values["issue_comments"].append(_fallback_success_comment())
    elif change == "job_overflow":
        values["original_jobs"]["total_count"] = 101
    elif change == "comment_horizon_exceeded":
        values["issue_comments"] += [
            {"id": 1000 + number, "body": "ordinary", "user": {"login": "u", "type": "User"}}
            for number in range(998)
        ]
    else:
        raise AssertionError(f"unknown mutation: {change}")
    return EvidenceBundle(**values)


def test_admission_binds_failure_before_provider_or_budget() -> None:
    admission = admit(valid_bundle())
    assert admission.request.original_run_id == 34549275027
    assert admission.request_comment_id == 901
    assert admission.automatic_comment_id == 887
    assert admission.automatic_state_sha256 == sha256(AUTOMATIC_STATE_BYTES).hexdigest()
    assert canonical_request_body(admission.request) == REQUEST_BODY


@pytest.mark.parametrize(
    "change",
    [
        "event_not_created",
        "comment_bytes_changed",
        "comment_timestamp_changed",
        "actor_not_collaborator",
        "pr_closed",
        "fork_head",
        "head_changed",
        "base_changed",
        "wrong_attempt",
        "wrong_job_run",
        "request_before_failure",
        "wrong_release",
        "provider_entered",
        "budget_claimed",
        "extra_failed_job",
        "extra_failed_step",
        "duplicate_request",
        "duplicate_state",
        "existing_fallback",
        "job_overflow",
        "comment_horizon_exceeded",
    ],
)
def test_admission_fails_closed(change: str) -> None:
    with pytest.raises(ContractError, match="^[a-z_]+$"):
        admit(mutated_bundle(change))


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ("request_before_failure", "request_order_invalid"),
        ("duplicate_request", "request_ambiguous"),
        ("comment_horizon_exceeded", "comment_horizon_exceeded"),
    ],
)
def test_admission_uses_required_bounded_reason_codes(change: str, reason: str) -> None:
    with pytest.raises(ContractError, match=f"^{reason}$"):
        admit(mutated_bundle(change))


OUTPUT_NAMES = {
    "route",
    "reason",
    "request-comment-id",
    "request-nonce",
    "expected-head-sha",
    "expected-base-sha",
    "original-run-id",
    "original-run-attempt",
    "release-commit",
    "managed-diff-sha256",
    "automatic-comment-id",
    "automatic-state-sha256",
}


def test_action_contract_exposes_only_closed_outputs_and_two_shell_steps() -> None:
    action = yaml.load(
        (CONTRACT_PATH.with_name("action.yml")).read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    assert action["inputs"] == {"github-token": {"required": "true"}}
    assert set(action["outputs"]) == OUTPUT_NAMES
    assert [step["id"] for step in action["runs"]["steps"]] == ["collect", "verify"]
    assert all(step["shell"] == "bash" for step in action["runs"]["steps"])


def _run_contract(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CONTRACT_PATH), *arguments],
        cwd=ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        capture_output=True,
        check=False,
    )


def test_plan_cli_emits_no_evidence_plan_for_interactive_text(tmp_path: Path) -> None:
    event_file = tmp_path / "event.json"
    plan_file = tmp_path / "plan.json"
    output_file = tmp_path / "output"
    event_file.write_text(json.dumps(issue_comment_event("@claude explain this")), encoding="utf-8")
    result = _run_contract(
        "plan",
        "--event-name",
        "issue_comment",
        "--event-file",
        str(event_file),
        "--plan-file",
        str(plan_file),
        "--github-output",
        str(output_file),
    )
    assert result.returncode == 0
    assert not plan_file.exists()
    assert output_file.read_text(encoding="utf-8") == "route=interactive\nreason=\n"


def test_plan_cli_writes_private_fixed_endpoint_plan_for_candidate(tmp_path: Path) -> None:
    event_file = tmp_path / "event.json"
    plan_file = tmp_path / "plan.json"
    output_file = tmp_path / "output"
    event_file.write_text(json.dumps(valid_bundle().event), encoding="utf-8")
    result = _run_contract(
        "plan",
        "--event-name",
        "issue_comment",
        "--event-file",
        str(event_file),
        "--plan-file",
        str(plan_file),
        "--github-output",
        str(output_file),
    )
    assert result.returncode == 0
    assert not output_file.exists() or output_file.read_text(encoding="utf-8") == ""
    assert stat.S_IMODE(plan_file.stat().st_mode) == 0o600
    assert json.loads(plan_file.read_text(encoding="utf-8")) == {
        "event": valid_bundle().event,
        "event_name": "issue_comment",
        "request": REQUEST,
        "requests": [
            {
                "endpoint": "repos/jhw7500/gstApp/issues/comments/901",
                "name": "comment.json",
            },
            {
                "endpoint": "repos/jhw7500/gstApp/pulls/109",
                "name": "pull_request.json",
            },
            {
                "endpoint": "repos/jhw7500/gstApp/actions/runs/34549275027/attempts/1",
                "name": "original_run.json",
            },
            {
                "endpoint": "repos/jhw7500/gstApp/actions/runs/34549275027/attempts/1/jobs?per_page=100",
                "name": "original_jobs.json",
            },
            *[
                {
                    "endpoint": (
                        "repos/jhw7500/gstApp/issues/109/comments"
                        f"?per_page=100&page={page}"
                    ),
                    "name": f"comments-{page:02d}.json",
                }
                for page in range(1, 11)
            ],
        ],
        "schema": 1,
    }


def _write_evidence(directory: Path, bundle: EvidenceBundle) -> None:
    directory.mkdir(mode=0o700)
    values = {
        "comment.json": bundle.comment,
        "pull_request.json": bundle.pull_request,
        "original_run.json": bundle.original_run,
        "original_jobs.json": bundle.original_jobs,
        "comments-01.json": bundle.issue_comments,
    }
    for name, value in values.items():
        path = directory / name
        path.write_text(json.dumps(value), encoding="utf-8")
        path.chmod(0o600)


def test_verify_cli_writes_private_admission_and_closed_scalar_outputs(tmp_path: Path) -> None:
    event_file = tmp_path / "event.json"
    plan_file = tmp_path / "plan.json"
    plan_output = tmp_path / "plan-output"
    evidence_directory = tmp_path / "evidence"
    admission_file = tmp_path / "admission.json"
    output_file = tmp_path / "output"
    event_file.write_text(json.dumps(valid_bundle().event), encoding="utf-8")
    planned = _run_contract(
        "plan", "--event-name", "issue_comment", "--event-file", str(event_file),
        "--plan-file", str(plan_file), "--github-output", str(plan_output),
    )
    assert planned.returncode == 0
    _write_evidence(evidence_directory, valid_bundle())
    result = _run_contract(
        "verify", "--plan-file", str(plan_file),
        "--evidence-directory", str(evidence_directory),
        "--admission-file", str(admission_file), "--github-output", str(output_file),
    )
    assert result.returncode == 0
    assert stat.S_IMODE(admission_file.stat().st_mode) == 0o600
    assert json.loads(admission_file.read_text(encoding="utf-8")) == {
        "automatic_comment_id": 887,
        "automatic_state_sha256": sha256(AUTOMATIC_STATE_BYTES).hexdigest(),
        "request": REQUEST,
        "request_comment_id": 901,
        "schema": 1,
    }
    assert output_file.read_text(encoding="utf-8") == (
        "route=managed\n"
        "reason=\n"
        "request-comment-id=901\n"
        "request-nonce=dddddddddddddddddddddddddddddddd\n"
        f"expected-head-sha={'a' * 40}\n"
        f"expected-base-sha={'b' * 40}\n"
        "original-run-id=34549275027\n"
        "original-run-attempt=1\n"
        "release-commit=444a7347aee169ed178aae80e8bd8d10eca52e02\n"
        f"managed-diff-sha256={'c' * 64}\n"
        "automatic-comment-id=887\n"
        f"automatic-state-sha256={sha256(AUTOMATIC_STATE_BYTES).hexdigest()}\n"
    )


def test_verify_cli_fails_closed_without_an_admission_file(tmp_path: Path) -> None:
    event_file = tmp_path / "event.json"
    plan_file = tmp_path / "plan.json"
    evidence_directory = tmp_path / "evidence"
    admission_file = tmp_path / "admission.json"
    output_file = tmp_path / "output"
    event_file.write_text(json.dumps(valid_bundle().event), encoding="utf-8")
    assert _run_contract(
        "plan", "--event-name", "issue_comment", "--event-file", str(event_file),
        "--plan-file", str(plan_file), "--github-output", str(tmp_path / "plan-output"),
    ).returncode == 0
    _write_evidence(evidence_directory, mutated_bundle("provider_entered"))
    result = _run_contract(
        "verify", "--plan-file", str(plan_file),
        "--evidence-directory", str(evidence_directory),
        "--admission-file", str(admission_file), "--github-output", str(output_file),
    )
    assert result.returncode == 0
    assert not admission_file.exists()
    outputs = dict(
        line.split("=", 1)
        for line in output_file.read_text(encoding="utf-8").splitlines()
    )
    assert outputs == {
        **{name: "" for name in OUTPUT_NAMES - {"route", "reason"}},
        "route": "invalid",
        "reason": "provider_entered",
    }
