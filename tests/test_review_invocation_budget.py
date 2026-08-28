import importlib.util
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest


HELPER = Path(__file__).parents[1] / ".github/actions/review-invocation-budget/review_invocation_budget.py"
SPEC = importlib.util.spec_from_file_location("review_invocation_budget", HELPER)
assert SPEC and SPEC.loader
budget = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = budget
SPEC.loader.exec_module(budget)

REPOSITORY = "example/repo"
PR = 52
HEAD_A = "a" * 40
HEAD_B = "b" * 40
HEAD_C = "c" * 40
HASH_1 = "1" * 64
HASH_2 = "2" * 64
HASH_3 = "3" * 64
CENTRAL_SHA = "d" * 40
CENTRAL_REF = "refs/tags/v1.47"
FINDING_1 = "RVW-111111111111"
FINDING_2 = "RVW-222222222222"

ROUTES = {
    "claude": ("claude-code-action-default",),
    "gemini": ("gemini-3.7-flash",),
    "opencode": ("zai-coding-plan/glm-4.7",),
}
EFFORTS = {"claude": "final-review/default", "gemini": "medium", "opencode": "final-review/default"}
CALL_UNITS = {
    "claude": "claude-code-action review session",
    "gemini": "generate_content request",
    "opencode": "opencode run session",
}


def request(*, reviewer="claude", head=HEAD_A, full_hash=HASH_1, run_id=700, run_attempt=1, **changes):
    values = {
        "repository": REPOSITORY,
        "pr": PR,
        "reviewer": reviewer,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "head_sha": head,
        "full_diff_sha256": full_hash,
        "estimated_input_tokens": 50_000,
        "diff_mode": "changed",
        "authenticated_review": budget.AuthenticatedReview(False, None, None),
        "override_events": (),
        "model_route": ROUTES[reviewer],
        "effort": EFFORTS[reviewer],
        "call_unit": CALL_UNITS[reviewer],
    }
    values.update(changes)
    return budget.ClaimRequest(**values)


def finalize_request(*, reviewer="claude", head=HEAD_A, full_hash=HASH_1, run_id=700,
                     run_attempt=1, calls=1, elapsed=10, outcome="success", stop_reason=None,
                     authenticated_review=None, remaining=(), model_route=None, **changes):
    values = {
        "repository": REPOSITORY,
        "pr": PR,
        "reviewer": reviewer,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "head_sha": head,
        "full_diff_sha256": full_hash,
        "model_route": ROUTES[reviewer] if model_route is None else model_route,
        "effort": EFFORTS[reviewer],
        "call_count": calls,
        "elapsed_seconds": elapsed,
        "outcome": outcome,
        "stop_reason": outcome if stop_reason is None else stop_reason,
        "authenticated_review": authenticated_review or budget.AuthenticatedReview(False, None, None),
        "remaining_finding_ids": remaining,
    }
    values.update(changes)
    return budget.FinalizeRequest(**values)


def invocation(*, reviewer="claude", head=HEAD_A, full_hash=HASH_1, run_id=501, run_attempt=1,
               round_number=1, outcome="success", status="finalized", override_event_id=None,
               call_count=None, estimated_input_tokens=50_000, elapsed_seconds=None):
    if call_count is None:
        call_count = 1 if status == "finalized" else 0
    if elapsed_seconds is None:
        elapsed_seconds = 10 if status == "finalized" else 0
    return budget.Invocation(
        run_id=run_id, run_attempt=run_attempt, head_sha=head,
        full_diff_sha256=full_hash,
        caller_workflow_path=f".github/workflows/{reviewer}-caller.yml",
        caller_event="pull_request",
        referenced_workflow_path=(
            f"jhw7500/automation/{budget.WORKFLOWS[reviewer]}@{CENTRAL_SHA}"
        ),
        referenced_workflow_ref=CENTRAL_REF,
        referenced_workflow_sha=CENTRAL_SHA,
        round_number=round_number,
        override_event_id=override_event_id, model_route=ROUTES[reviewer],
        effort=EFFORTS[reviewer], call_unit=CALL_UNITS[reviewer],
        call_count=call_count, estimated_input_tokens=estimated_input_tokens,
        elapsed_seconds=elapsed_seconds, status=status,
        outcome=outcome if status == "finalized" else None,
        stop_reason=outcome if status == "finalized" else "claimed", remaining_finding_ids=(),
    )


def state_for(prior):
    if prior == "empty":
        return None
    first = invocation(outcome="provider_failure" if prior == "one-provider-failure" else "success",
                       status="claimed" if prior == "one-claimed" else "finalized")
    if prior.startswith("one-"):
        return budget.LedgerState.initial(REPOSITORY, PR, "claude", invocations=(first,))
    second = invocation(head=HEAD_B, full_hash=HASH_2, run_id=502, round_number=2)
    return budget.LedgerState.initial(REPOSITORY, PR, "claude", invocations=(first, second))


def valid_provenances(state):
    return {
        (entry.run_id, entry.run_attempt): budget.RunProvenance(
            repository=REPOSITORY, pr=PR, head_sha=entry.head_sha,
            caller_workflow_path=entry.caller_workflow_path,
            caller_event=entry.caller_event,
            referenced_workflow_path=entry.referenced_workflow_path,
            referenced_workflow_ref=entry.referenced_workflow_ref,
            referenced_workflow_sha=entry.referenced_workflow_sha,
            run_id=entry.run_id,
            run_attempt=entry.run_attempt, status="completed", conclusion="cancelled",
        )
        for entry in state.invocations
    }


def current_provenances(state, *, conclusion=None):
    current = state.invocations[-1]
    provenances = valid_provenances(state)
    provenances[(current.run_id, current.run_attempt)] = replace(
        provenances[(current.run_id, current.run_attempt)], status="in_progress", conclusion=conclusion,
    )
    return provenances


def reusable_provenance(
    reviewer="claude", *, head=HEAD_A, run_id=700, run_attempt=1,
    status="in_progress", conclusion=None,
):
    return budget.RunProvenance(
        repository=REPOSITORY,
        pr=PR,
        head_sha=head,
        caller_workflow_path=f".github/workflows/{reviewer}-caller.yml",
        caller_event="pull_request",
        referenced_workflow_path=(
            f"jhw7500/automation/{budget.WORKFLOWS[reviewer]}@{CENTRAL_SHA}"
        ),
        referenced_workflow_ref=CENTRAL_REF,
        referenced_workflow_sha=CENTRAL_SHA,
        run_id=run_id,
        run_attempt=run_attempt,
        status=status,
        conclusion=conclusion,
    )


def claimed_state(reviewer="claude"):
    claim_request = request(reviewer=reviewer)
    provenance = reusable_provenance(reviewer)
    return budget.claim(
        None, claim_request,
        {(claim_request.run_id, claim_request.run_attempt): provenance},
    ).state


def claim_provenances(state, claim_request):
    provenances = {} if state is None else valid_provenances(state)
    provenances[(claim_request.run_id, claim_request.run_attempt)] = reusable_provenance(
        claim_request.reviewer,
        head=claim_request.head_sha,
        run_id=claim_request.run_id,
        run_attempt=claim_request.run_attempt,
    )
    return provenances


def ledger_body(state):
    payload = json.dumps(state.to_dict(), ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return f"{budget.MARKERS[state.reviewer]}\n{budget.STATE_PREFIX}{payload}{budget.STATE_SUFFIX}"


def assert_stored_state_rejected(state, reason):
    with pytest.raises(budget.BudgetStateError, match=reason):
        budget.serialize_ledger(state)
    with pytest.raises(budget.BudgetStateError, match=reason):
        budget.parse_ledger(
            ledger_body(state), repository=state.repository, pr=state.pr, reviewer=state.reviewer,
        )


@pytest.fixture
def two_round_state():
    return state_for("two-successes")


@pytest.fixture
def claim_request():
    return request(head=HEAD_C, full_hash=HASH_3)


@pytest.fixture
def empty_state():
    return None


@pytest.fixture
def unchanged_request():
    return request(diff_mode="unchanged")


def test_fixed_claim_vectors():
    vectors = json.loads((Path(__file__).parent / "fixtures/review-invocation-budget/cases.json").read_text())
    for vector in (item for item in vectors if item.get("kind", "claim") == "claim"):
        events = ()
        if "override_event" in vector:
            events = (budget.OverrideEvent(vector["override_event"], "labeled", "review-budget-override", "write"),)
        prior_state = state_for(vector["prior"])
        claim_request = request(
            head=vector["head"], full_hash=vector["full_hash"],
            override_events=events,
        )
        result = budget.claim(
            prior_state, claim_request, claim_provenances(prior_state, claim_request),
        )
        assert result.decision == vector["expected"], vector["name"]
        assert result.allow_invocation is vector["allow"], vector["name"]
        expected_rounds = {"empty": 0, "one-success": 1, "one-provider-failure": 1,
                           "one-claimed": 1, "two-successes": 2}[vector["prior"]]
        assert len(result.state.invocations) == expected_rounds + int(vector["allow"])
        if vector["expected"] == "claimed":
            assert result.round_number == (3 if vector["prior"] == "two-successes" else 1)
        if "override_event" in vector:
            assert result.state.consumed_override_event_ids == (vector["override_event"],)


@pytest.mark.parametrize("reviewer", ["claude", "gemini", "opencode"])
def test_reusable_run_provenance_survives_first_finalize_and_later_claim(reviewer):
    first_request = request(reviewer=reviewer)
    first_provenance = reusable_provenance(reviewer)
    first = budget.claim(
        None,
        first_request,
        {(first_request.run_id, first_request.run_attempt): first_provenance},
    )
    assert first.allow_invocation
    stored = first.state.invocations[-1]
    assert stored.caller_workflow_path == first_provenance.caller_workflow_path
    assert stored.caller_event == "pull_request"
    assert stored.referenced_workflow_path == first_provenance.referenced_workflow_path
    assert stored.referenced_workflow_ref == CENTRAL_REF
    assert stored.referenced_workflow_sha == CENTRAL_SHA

    completed_provenance = replace(
        first_provenance, status="completed", conclusion="success",
    )
    finalized = budget.finalize(
        first.state,
        finalize_request(reviewer=reviewer),
        {(first_request.run_id, first_request.run_attempt): completed_provenance},
    )
    assert finalized.decision == "finalized"

    second_request = request(
        reviewer=reviewer, head=HEAD_B, full_hash=HASH_2, run_id=701,
    )
    second_provenance = reusable_provenance(
        reviewer, head=HEAD_B, run_id=701,
    )
    second = budget.claim(
        finalized.state,
        second_request,
        {
            (700, 1): completed_provenance,
            (701, 1): second_provenance,
        },
    )
    assert second.allow_invocation
    assert second.round_number == 2


def test_reusable_run_accepts_symbolic_ref_resolved_to_workflow_sha():
    provenance = reusable_provenance()

    result = budget.claim(None, request(), {(700, 1): provenance})

    assert result.allow_invocation
    stored = result.state.invocations[-1]
    assert stored.referenced_workflow_ref == "refs/tags/v1.47"
    assert stored.referenced_workflow_path.endswith("@" + CENTRAL_SHA)


@pytest.mark.parametrize(
    "changes",
    [
        {"caller_event": "workflow_dispatch"},
        {"caller_workflow_path": ""},
        {"referenced_workflow_path": (
            "jhw7500/automation/.github/workflows/claude-code-review.yml@" + "e" * 40
        )},
        {"referenced_workflow_ref": "refs/tags/v1.47\n"},
        {"referenced_workflow_sha": "not-a-sha"},
    ],
)
def test_reusable_run_provenance_fails_closed_when_central_identity_is_malformed(changes):
    provenance = replace(reusable_provenance(), **changes)
    result = budget.claim(None, request(), {(700, 1): provenance})
    assert not result.allow_invocation
    assert result.decision == "state_invalid"
    assert result.stop_reason == "provenance_mismatch"


def test_fixed_finalize_vectors():
    vectors = json.loads((Path(__file__).parent / "fixtures/review-invocation-budget/cases.json").read_text())
    for vector in (item for item in vectors if item.get("kind") == "finalize"):
        reviewer = vector["reviewer"]
        state = claimed_state(reviewer)
        result = budget.finalize(
            state,
            finalize_request(
                reviewer=reviewer,
                calls=vector.get("calls", 1),
                elapsed=vector.get("elapsed", 10),
                outcome=vector.get("outcome", "success"),
                remaining=tuple(vector.get("remaining", ())),
            ),
            current_provenances(state),
        )
        completed = result.state.invocations[-1]
        assert result.decision == "finalized", vector["name"]
        assert completed.outcome == vector["expected_outcome"], vector["name"]
        assert completed.call_count == vector.get("calls", 1), vector["name"]
        assert completed.elapsed_seconds == vector.get("elapsed", 10), vector["name"]
        assert completed.remaining_finding_ids == tuple(vector.get("remaining", ())), vector["name"]
        assert completed.stop_reason, vector["name"]


def test_gemini_primary_retry_and_fallback_share_one_three_request_cap():
    state = claimed_state("gemini")
    allowed = budget.finalize(
        state, finalize_request(reviewer="gemini", calls=3), current_provenances(state),
    )
    refused = budget.finalize(
        state, finalize_request(reviewer="gemini", calls=4), current_provenances(state),
    )
    assert allowed.state.invocations[-1].outcome == "success"
    assert refused.state.invocations[-1].outcome == "checkpoint_failure"
    assert refused.state.invocations[-1].stop_reason == "call_budget_exhausted"


def test_call_overage_wins_when_call_and_wall_caps_are_both_exceeded():
    state = claimed_state()
    result = budget.finalize(
        state,
        finalize_request(calls=2, elapsed=601),
        current_provenances(state),
    )
    completed = result.state.invocations[-1]
    assert result.decision == "finalized"
    assert completed.call_count == 2
    assert completed.elapsed_seconds == 601
    assert completed.outcome == "checkpoint_failure"
    assert completed.stop_reason == "call_budget_exhausted"
    assert budget.load_checkpoint(budget.render_checkpoint(result.state)) == result.state


def test_quality_filtered_is_terminal_and_duplicate_input_stays_blocked():
    state = claimed_state()
    done = budget.finalize(
        state, finalize_request(outcome="quality_filtered"), current_provenances(state),
    )
    again = budget.claim(done.state, request(), valid_provenances(done.state))
    assert again.decision == "duplicate_head"
    assert not again.allow_invocation


def test_finalization_matches_one_claim_and_rejects_identity_or_status_drift():
    state = claimed_state()
    finalized = budget.finalize(state, finalize_request(), current_provenances(state))
    second = budget.finalize(finalized.state, finalize_request(), valid_provenances(finalized.state))
    drift = budget.finalize(
        state, replace(finalize_request(), full_diff_sha256=HASH_2), current_provenances(state),
    )
    assert second.decision == "state_invalid"
    assert second.stop_reason == "invocation_not_claimed"
    assert drift.decision == "state_invalid"
    assert drift.stop_reason == "finalization_identity_mismatch"


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"call_count": -1}, "call_count_invalid"),
        ({"call_count": True}, "call_count_invalid"),
        ({"elapsed_seconds": -1}, "elapsed_seconds_invalid"),
        ({"elapsed_seconds": False}, "elapsed_seconds_invalid"),
        ({"model_route": ("unknown-model",)}, "model_route_unknown"),
        ({"remaining_finding_ids": (FINDING_1,) * 2}, "remaining_finding_ids_invalid"),
        ({"remaining_finding_ids": tuple(f"RVW-{index:012x}" for index in range(9))}, "remaining_finding_ids_invalid"),
    ],
)
def test_finalization_rejects_invalid_metrics_route_and_findings(changes, reason):
    state = claimed_state()
    result = budget.finalize(state, replace(finalize_request(), **changes), current_provenances(state))
    assert result.decision == "state_invalid"
    assert result.stop_reason == reason


def test_finalization_requires_current_run_provenance():
    state = claimed_state()
    result = budget.finalize(state, finalize_request(), {})
    assert result.decision == "state_invalid"
    assert result.stop_reason == "provenance_mismatch"


def test_provider_failure_preserves_prior_authenticated_findings_without_newer_list():
    first_claim = claimed_state()
    first = budget.finalize(
        first_claim,
        finalize_request(outcome="success", remaining=(FINDING_1, FINDING_2)),
        current_provenances(first_claim),
    ).state
    second_claim = budget.claim(
        first,
        request(head=HEAD_B, full_hash=HASH_2, run_id=701),
        claim_provenances(
            first, request(head=HEAD_B, full_hash=HASH_2, run_id=701),
        ),
    ).state
    failed = budget.finalize(
        second_claim,
        finalize_request(
            head=HEAD_B, full_hash=HASH_2, run_id=701, outcome="provider_failure", remaining=(),
        ),
        current_provenances(second_claim),
    ).state
    assert failed.invocations[-1].remaining_finding_ids == (FINDING_1, FINDING_2)
    assert failed.handoff.remaining_finding_ids == (FINDING_1, FINDING_2)
    assert failed.handoff.authenticated_review_head_sha == HEAD_A
    assert failed.handoff.authenticated_review_full_diff_sha256 == HASH_1


@pytest.mark.parametrize("outcome", ["success", "quality_filtered"])
def test_empty_current_canonical_findings_clear_prior_ids(outcome):
    first_claim = claimed_state()
    first = budget.finalize(
        first_claim,
        finalize_request(outcome="success", remaining=(FINDING_1, FINDING_2)),
        current_provenances(first_claim),
    ).state
    second_claim = budget.claim(
        first,
        request(head=HEAD_B, full_hash=HASH_2, run_id=701),
        claim_provenances(
            first, request(head=HEAD_B, full_hash=HASH_2, run_id=701),
        ),
    ).state
    prior_review = budget.AuthenticatedReview(
        True, HEAD_A, HASH_1, (FINDING_1, FINDING_2),
    )
    completed = budget.finalize(
        second_claim,
        finalize_request(
            head=HEAD_B, full_hash=HASH_2, run_id=701, outcome=outcome,
            authenticated_review=prior_review, remaining=(),
        ),
        current_provenances(second_claim),
    ).state
    assert completed.invocations[-1].remaining_finding_ids == ()
    assert completed.handoff.remaining_finding_ids == ()


def test_repeat_finalization_refusal_updates_checkpoint_state_without_comment_mutation():
    claimed = claimed_state()
    finalized = budget.finalize(
        claimed, finalize_request(), current_provenances(claimed),
    ).state
    refused = budget.finalize(
        finalized, finalize_request(), valid_provenances(finalized),
    )
    assert refused.decision == "state_invalid"
    assert refused.stop_reason == "invocation_not_claimed"
    assert not refused.mutate_comment
    assert refused.state.last_decision == budget.DecisionRecord(
        "state_invalid", "invocation_not_claimed", 700, 1,
    )
    assert refused.state.handoff.current_head_sha == HEAD_A
    assert refused.state.handoff.current_full_diff_sha256 == HASH_1
    assert refused.state.handoff.outcome == "success"
    assert refused.state.handoff.stop_reason == "invocation_not_claimed"
    assert budget.load_checkpoint(budget.render_checkpoint(refused.state)) == refused.state


def test_identity_drift_refusal_records_current_request_in_checkpoint_state():
    claimed = claimed_state()
    refused = budget.finalize(
        claimed,
        replace(finalize_request(), full_diff_sha256=HASH_2),
        current_provenances(claimed),
    )
    assert refused.decision == "state_invalid"
    assert refused.stop_reason == "finalization_identity_mismatch"
    assert not refused.mutate_comment
    assert refused.state.last_decision == budget.DecisionRecord(
        "state_invalid", "finalization_identity_mismatch", 700, 1,
    )
    assert refused.state.handoff.current_head_sha == HEAD_A
    assert refused.state.handoff.current_full_diff_sha256 == HASH_2
    assert refused.state.handoff.outcome is None
    assert budget.load_checkpoint(budget.render_checkpoint(refused.state)) == refused.state


def test_comment_summary_and_handoff_are_exact_and_workflow_owned():
    state = claimed_state()
    finalized = budget.finalize(
        state,
        finalize_request(outcome="success", remaining=(FINDING_1,)),
        current_provenances(state),
    ).state
    duplicate = budget.claim(finalized, request(), valid_provenances(finalized)).state
    visible = (
        "## Claude review invocation budget\n"
        "- Decision: duplicate_head\n"
        "- Automatic rounds: 1/2\n"
        "- Override rounds: 0/1\n"
        "- Current run: https://github.com/example/repo/actions/runs/700\n"
        "- Stop reason: duplicate_head\n\n"
        "Budget exhaustion is not review approval. Use the authenticated review checkpoint and remaining finding IDs before merge."
    )
    state_lines = (
        f"{budget.MARKERS['claude']}\n"
        f"{budget.STATE_PREFIX}{budget.serialize_ledger(duplicate)}{budget.STATE_SUFFIX}"
    )
    assert budget.render_summary(duplicate) == visible
    assert budget.render_comment(duplicate, server_url="https://github.com") == f"{state_lines}\n\n{visible}"
    assert duplicate.handoff.to_dict() == {
        "repository": REPOSITORY,
        "pr": PR,
        "reviewer": "claude",
        "current_head_sha": HEAD_A,
        "current_full_diff_sha256": HASH_1,
        "current_run_id": 700,
        "current_run_attempt": 1,
        "automatic_rounds": 1,
        "override_rounds": 0,
        "round_usage": [{
            "round_number": 1,
            "call_count": 1,
            "estimated_input_tokens": 50_000,
            "elapsed_seconds": 10,
        }],
        "decision": "duplicate_head",
        "outcome": "success",
        "stop_reason": "duplicate_head",
        "authenticated_review_head_sha": HEAD_A,
        "authenticated_review_full_diff_sha256": HASH_1,
        "remaining_finding_ids": [FINDING_1],
    }


def test_checkpoint_is_canonical_and_round_trips_without_external_context():
    state = claimed_state()
    finalized = budget.finalize(state, finalize_request(), current_provenances(state)).state
    payload = budget.render_checkpoint(finalized)
    expected = json.dumps(
        {"schema": 1, "ledger": finalized.to_dict(), "handoff": finalized.handoff.to_dict()},
        ensure_ascii=True, separators=(",", ":"), sort_keys=True,
    ).encode("ascii") + b"\n"
    assert payload == expected
    assert budget.load_checkpoint(payload) == finalized
    with pytest.raises(budget.BudgetStateError, match="checkpoint_json_noncanonical"):
        budget.load_checkpoint(json.dumps(json.loads(payload)).encode() + b"\n")
    mismatched = json.loads(payload)
    mismatched["handoff"]["stop_reason"] = "tampered"
    tampered = json.dumps(mismatched, separators=(",", ":"), sort_keys=True).encode() + b"\n"
    with pytest.raises(budget.BudgetStateError, match="checkpoint_handoff_mismatch"):
        budget.load_checkpoint(tampered)


def test_checkpoint_rejects_coordinated_current_identity_drift():
    claimed = claimed_state()
    finalized = budget.finalize(
        claimed, finalize_request(), current_provenances(claimed),
    ).state
    raw = json.loads(budget.render_checkpoint(finalized))
    for handoff in (raw["ledger"]["handoff"], raw["handoff"]):
        handoff["current_head_sha"] = HEAD_B
        handoff["current_full_diff_sha256"] = HASH_2
        handoff["outcome"] = None
    coordinated = json.dumps(raw, separators=(",", ":"), sort_keys=True).encode() + b"\n"
    with pytest.raises(budget.BudgetStateError, match="handoff_mismatch"):
        budget.load_checkpoint(coordinated)


def test_ledger_rejects_handoff_outcome_drift():
    state = claimed_state()
    finalized = budget.finalize(state, finalize_request(), current_provenances(state)).state
    tampered = replace(
        finalized, handoff=replace(finalized.handoff, outcome="provider_failure"),
    )
    assert_stored_state_rejected(tampered, "handoff_mismatch")


def test_checkpoint_alone_reconstructs_next_session(tmp_path):
    claimed = claimed_state()
    provider_failed = budget.finalize(
        claimed,
        finalize_request(outcome="provider_failure"),
        current_provenances(claimed),
    ).state
    checkpoint = tmp_path / "budget.json"
    checkpoint.write_bytes(budget.render_checkpoint(provider_failed))
    restored = budget.load_checkpoint(checkpoint.read_bytes())
    same_request = request(run_id=701)
    same = budget.claim(
        restored, same_request, claim_provenances(restored, same_request),
    )
    second_request = request(head=HEAD_B, full_hash=HASH_2, run_id=701)
    second = budget.claim(
        restored,
        second_request,
        claim_provenances(restored, second_request),
    )
    assert same.decision == "duplicate_head"
    assert second.allow_invocation


def test_parser_requires_exact_schema_and_serializes_deterministically():
    state = budget.LedgerState.initial(REPOSITORY, PR, "gemini")
    payload = budget.serialize_ledger(state)
    body = f"{budget.MARKERS['gemini']}\n{budget.STATE_PREFIX}{payload}{budget.STATE_SUFFIX}"
    assert budget.parse_ledger(body, repository=REPOSITORY, pr=PR, reviewer="gemini") == state
    assert payload == json.dumps(state.to_dict(), ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    with pytest.raises(budget.BudgetStateError):
        budget.parse_ledger(body.replace('"schema":1', '"schema":true'), repository=REPOSITORY, pr=PR, reviewer="gemini")


def test_parser_rejects_noncanonical_json_encoding():
    state = budget.LedgerState.initial(REPOSITORY, PR, "claude")
    noncanonical = json.dumps(state.to_dict(), sort_keys=True)
    body = f"{budget.MARKERS['claude']}\n{budget.STATE_PREFIX}{noncanonical}{budget.STATE_SUFFIX}"
    with pytest.raises(budget.BudgetStateError, match="ledger_json_noncanonical"):
        budget.parse_ledger(body, repository=REPOSITORY, pr=PR, reviewer="claude")


def test_estimate_input_tokens_rejects_non_regular_path(tmp_path):
    file_path = tmp_path / "diff"
    file_path.write_bytes(b"abcde")
    assert budget.estimate_input_tokens([file_path]) == 20_002
    with pytest.raises(budget.BudgetStateError, match="input_not_regular"):
        budget.estimate_input_tokens([tmp_path])


@pytest.mark.parametrize(
    "field", ["repository", "pr", "head_sha", "caller_workflow_path", "run_attempt"],
)
def test_claim_fails_closed_when_historical_provenance_mismatches(field, two_round_state, claim_request):
    provenances = claim_provenances(two_round_state, claim_request)
    mismatch = {"repository": "other/repo", "pr": 53, "head_sha": HEAD_B,
                "caller_workflow_path": "other.yml", "run_attempt": 2}[field]
    provenances[(501, 1)] = replace(provenances[(501, 1)], **{field: mismatch})
    result = budget.claim(two_round_state, claim_request, provenances)
    assert not result.allow_invocation
    assert result.decision == "state_invalid"
    assert result.stop_reason == "provenance_mismatch"


@pytest.mark.parametrize("conclusion", ["cancelled", "timed_out"])
def test_claim_accepts_current_in_progress_and_historical_terminal_provenance(
        conclusion, two_round_state, claim_request):
    provenances = claim_provenances(two_round_state, claim_request)
    provenances = {
        key: replace(value, conclusion=conclusion)
        if key != (claim_request.run_id, claim_request.run_attempt) else value
        for key, value in provenances.items()
    }
    result = budget.claim(two_round_state, claim_request, provenances)
    assert result.decision == "round_budget_exhausted"
    claimed = budget.claim(
        None, claim_request, claim_provenances(None, claim_request),
    )
    assert claimed.allow_invocation
    existing = claimed.state
    same_request = request(head=HEAD_C, full_hash=HASH_3)
    provenances = {(700, 1): reusable_provenance(head=HEAD_C)}
    result = budget.claim(existing, same_request, provenances)
    assert result.decision == "duplicate_head"
    assert not result.allow_invocation


def test_claim_rejects_historical_in_progress_provenance(two_round_state, claim_request):
    provenances = claim_provenances(two_round_state, claim_request)
    provenances[(501, 1)] = replace(provenances[(501, 1)], status="in_progress", conclusion=None)
    result = budget.claim(two_round_state, claim_request, provenances)
    assert not result.allow_invocation
    assert result.decision == "state_invalid"
    assert result.stop_reason == "provenance_mismatch"


def test_claim_rejects_boolean_authenticated_success_and_duplicate_run_identity():
    malformed_review = request(
        diff_mode="unchanged", authenticated_review=budget.AuthenticatedReview(1, HEAD_A, HASH_1),
    )
    assert budget.claim(
        None, malformed_review, claim_provenances(None, malformed_review),
    ).decision == "state_invalid"
    initial_request = request()
    existing = budget.claim(
        None, initial_request, claim_provenances(None, initial_request),
    ).state
    duplicate_run = request(head=HEAD_B, full_hash=HASH_2)
    current = {(700, 1): reusable_provenance()}
    result = budget.claim(existing, duplicate_run, current)
    assert result.decision == "state_invalid"
    assert result.stop_reason == "duplicate_run_identity"


def test_parser_rejects_claimed_usage_before_provider_execution():
    state = budget.LedgerState.initial(REPOSITORY, PR, "claude", invocations=(invocation(status="claimed"),))
    body = f"{budget.MARKERS['claude']}\n{budget.STATE_PREFIX}{budget.serialize_ledger(state)}{budget.STATE_SUFFIX}"
    raw = json.loads(body[len(budget.MARKERS["claude"]) + 1 + len(budget.STATE_PREFIX):-len(budget.STATE_SUFFIX)])
    raw["invocations"][0]["call_count"] = 1
    invalid = f"{budget.MARKERS['claude']}\n{budget.STATE_PREFIX}{json.dumps(raw)}{budget.STATE_SUFFIX}"
    with pytest.raises(budget.BudgetStateError, match="status_invalid"):
        budget.parse_ledger(invalid, repository=REPOSITORY, pr=PR, reviewer="claude")


def test_unchanged_requires_authenticated_exact_coverage(empty_state, unchanged_request):
    refused = budget.claim(
        empty_state, unchanged_request,
        claim_provenances(empty_state, unchanged_request),
    )
    assert refused.decision == "state_invalid"
    assert refused.stop_reason == "unchanged_without_authenticated_review"


def test_new_head_with_authenticated_same_hash_reuses_without_a_call(empty_state, unchanged_request):
    request_with_authenticated = replace(
        unchanged_request, head_sha=HEAD_B,
        authenticated_review=budget.AuthenticatedReview(True, HEAD_A, unchanged_request.full_diff_sha256),
    )
    result = budget.claim(
        empty_state, request_with_authenticated,
        claim_provenances(empty_state, request_with_authenticated),
    )
    assert result.decision == "authenticated_reuse"
    assert not result.allow_invocation


def test_invalid_duplicate_and_noncanonical_ledger_values_are_rejected():
    state = budget.LedgerState.initial(REPOSITORY, PR, "claude", invocations=(invocation(),))
    raw = state.to_dict()
    raw["invocations"].append(raw["invocations"][0])
    body = f"{budget.MARKERS['claude']}\n{budget.STATE_PREFIX}{json.dumps(raw)}{budget.STATE_SUFFIX}"
    with pytest.raises(budget.BudgetStateError):
        budget.parse_ledger(body, repository=REPOSITORY, pr=PR, reviewer="claude")


def test_parser_rejects_override_before_two_automatic_rounds():
    impossible = budget.LedgerState.initial(
        REPOSITORY,
        PR,
        "claude",
        invocations=(invocation(round_number=3, override_event_id=9001),),
        consumed_override_event_ids=(9001,),
    )
    payload = json.dumps(impossible.to_dict(), ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    body = f"{budget.MARKERS['claude']}\n{budget.STATE_PREFIX}{payload}{budget.STATE_SUFFIX}"
    with pytest.raises(budget.BudgetStateError, match="override_invalid"):
        budget.parse_ledger(body, repository=REPOSITORY, pr=PR, reviewer="claude")


def test_serializer_rejects_boolean_reviewer_budget():
    state = budget.LedgerState.initial(REPOSITORY, PR, "claude")
    malformed = replace(state, budgets=replace(state.budgets, max_calls_per_round=True))
    with pytest.raises(budget.BudgetStateError, match="max_calls_per_round_invalid"):
        budget.serialize_ledger(malformed)


@pytest.mark.parametrize("reviewer,max_calls", [("claude", 1), ("gemini", 3), ("opencode", 2)])
def test_stored_call_count_enforces_each_reviewer_boundary(reviewer, max_calls):
    at_limit = budget.LedgerState.initial(
        REPOSITORY, PR, reviewer,
        invocations=(invocation(reviewer=reviewer, call_count=max_calls),),
    )
    payload = budget.serialize_ledger(at_limit)
    assert budget.parse_ledger(
        f"{budget.MARKERS[reviewer]}\n{budget.STATE_PREFIX}{payload}{budget.STATE_SUFFIX}",
        repository=REPOSITORY, pr=PR, reviewer=reviewer,
    ) == at_limit

    above_limit = replace(
        at_limit, invocations=(replace(at_limit.invocations[0], call_count=max_calls + 1),),
    )
    assert_stored_state_rejected(above_limit, "call_budget_exhausted")

    normalized_failure = replace(
        above_limit,
        invocations=(replace(
            above_limit.invocations[0],
            outcome="checkpoint_failure",
            stop_reason="call_budget_exhausted",
        ),),
    )
    assert budget.parse_ledger(
        ledger_body(normalized_failure), repository=REPOSITORY, pr=PR, reviewer=reviewer,
    ) == normalized_failure
    assert_stored_state_rejected(
        replace(normalized_failure, invocations=(replace(
            normalized_failure.invocations[0], stop_reason="wrong",
        ),)),
        "call_budget_exhausted",
    )


def test_stored_elapsed_seconds_enforces_round_boundary():
    at_limit = budget.LedgerState.initial(
        REPOSITORY, PR, "claude", invocations=(invocation(elapsed_seconds=600),),
    )
    assert budget.parse_ledger(
        ledger_body(at_limit), repository=REPOSITORY, pr=PR, reviewer="claude",
    ) == at_limit
    above_limit = replace(
        at_limit, invocations=(replace(at_limit.invocations[0], elapsed_seconds=601),),
    )
    assert_stored_state_rejected(above_limit, "wall_time_exhausted")

    normalized_failure = replace(
        above_limit,
        invocations=(replace(
            above_limit.invocations[0],
            outcome="wall_time_exhausted",
            stop_reason="wall_time_exhausted",
        ),),
    )
    assert budget.parse_ledger(
        ledger_body(normalized_failure), repository=REPOSITORY, pr=PR, reviewer="claude",
    ) == normalized_failure
    assert_stored_state_rejected(
        replace(normalized_failure, invocations=(replace(
            normalized_failure.invocations[0], outcome="checkpoint_failure",
        ),)),
        "wall_time_exhausted",
    )


def test_stored_estimated_input_enforces_round_boundary():
    at_limit = budget.LedgerState.initial(
        REPOSITORY, PR, "claude", invocations=(invocation(estimated_input_tokens=200_000),),
    )
    assert budget.parse_ledger(
        ledger_body(at_limit), repository=REPOSITORY, pr=PR, reviewer="claude",
    ) == at_limit
    above_limit = replace(
        at_limit,
        invocations=(replace(at_limit.invocations[0], estimated_input_tokens=200_001),),
    )
    assert_stored_state_rejected(above_limit, "input_budget_exhausted")


def test_stored_automatic_and_override_aggregate_boundaries():
    first = invocation(estimated_input_tokens=200_000)
    second = invocation(
        head=HEAD_B, full_hash=HASH_2, run_id=502, round_number=2,
        estimated_input_tokens=200_000,
    )
    automatic_limit = budget.LedgerState.initial(
        REPOSITORY, PR, "claude", invocations=(first, second),
    )
    assert budget.parse_ledger(
        ledger_body(automatic_limit), repository=REPOSITORY, pr=PR, reviewer="claude",
    ) == automatic_limit

    third = invocation(
        head=HEAD_C, full_hash=HASH_3, run_id=503, round_number=3,
        override_event_id=9001, estimated_input_tokens=200_000,
    )
    override_limit = budget.LedgerState.initial(
        REPOSITORY, PR, "claude", invocations=(first, second, third),
        consumed_override_event_ids=(9001,),
    )
    assert budget.parse_ledger(
        ledger_body(override_limit), repository=REPOSITORY, pr=PR, reviewer="claude",
    ) == override_limit

    above_override_limit = replace(
        override_limit,
        invocations=override_limit.invocations[:2] + (
            replace(override_limit.invocations[2], estimated_input_tokens=200_001),
        ),
    )
    assert_stored_state_rejected(above_override_limit, "input_budget_exhausted")
