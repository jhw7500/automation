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


def test_same_head_noop_requires_exact_authenticated_checkpoint():
    """A rerun may be green only when the prior checkpoint covers this exact head and diff."""
    existing = budget.LedgerState.initial(
        REPOSITORY,
        PR,
        "gemini",
        invocations=(invocation(reviewer="gemini", outcome="success"),),
    )
    exact_request = replace(
        request(reviewer="gemini"),
        authenticated_review=budget.AuthenticatedReview(True, HEAD_A, HASH_1),
    )
    exact = budget.claim(
        existing, exact_request, claim_provenances(existing, exact_request)
    )
    missing_request = replace(
        exact_request,
        authenticated_review=budget.AuthenticatedReview(False, None, None),
    )
    missing = budget.claim(
        existing, missing_request, claim_provenances(existing, missing_request)
    )

    assert exact.decision == "authenticated_reuse"
    assert not exact.allow_invocation
    assert missing.decision == "duplicate_head"
    assert not missing.allow_invocation


def test_force_review_claims_same_head_once_with_dispatch_and_authorized_override():
    existing = budget.LedgerState.initial(
        REPOSITORY,
        PR,
        "claude",
        invocations=(invocation(),),
    )
    force_request = replace(
        request(run_id=700),
        force_review=True,
        override_events=(
            budget.OverrideEvent(9001, "labeled", "review-budget-override", "write"),
        ),
    )
    provenances = valid_provenances(existing)
    provenances[(700, 1)] = replace(
        reusable_provenance(run_id=700), caller_event="workflow_dispatch"
    )

    result = budget.claim(existing, force_request, provenances)

    assert result.allow_invocation
    assert result.decision == "claimed"
    assert result.round_number == 2
    assert result.state.consumed_override_event_ids == (9001,)
    assert result.state.invocations[-1].head_sha == HEAD_A
    assert result.state.invocations[-1].full_diff_sha256 == HASH_1
    assert result.state.invocations[-1].caller_event == "workflow_dispatch"


def test_force_review_fails_closed_without_dispatch_or_authorized_override():
    existing = budget.LedgerState.initial(
        REPOSITORY,
        PR,
        "claude",
        invocations=(invocation(),),
    )
    force_request = replace(request(run_id=700), force_review=True)
    pull_request_provenances = claim_provenances(existing, force_request)

    wrong_event = budget.claim(existing, force_request, pull_request_provenances)
    assert not wrong_event.allow_invocation
    assert wrong_event.decision == "state_invalid"
    assert wrong_event.stop_reason == "provenance_mismatch"

    dispatch_provenances = dict(pull_request_provenances)
    dispatch_provenances[(700, 1)] = replace(
        dispatch_provenances[(700, 1)], caller_event="workflow_dispatch"
    )
    no_override = budget.claim(existing, force_request, dispatch_provenances)
    assert not no_override.allow_invocation
    assert no_override.decision == "round_budget_exhausted"


def test_normal_same_head_remains_zero_call_when_override_label_exists():
    existing = budget.LedgerState.initial(
        REPOSITORY,
        PR,
        "claude",
        invocations=(invocation(),),
    )
    normal_request = replace(
        request(run_id=700),
        override_events=(
            budget.OverrideEvent(9001, "labeled", "review-budget-override", "write"),
        ),
    )

    result = budget.claim(
        existing, normal_request, claim_provenances(existing, normal_request)
    )

    assert not result.allow_invocation
    assert result.decision == "duplicate_head"
    assert result.state.consumed_override_event_ids == ()


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
        "- Stop reason: duplicate_head\n"
        "- Dismissed findings: none\n\n"
        f"{DISMISS_GUIDANCE}"
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


@pytest.mark.parametrize(
    ("reviewer", "max_elapsed"),
    (("claude", 1080), ("gemini", 600), ("opencode", 600)),
)
def test_stored_elapsed_seconds_enforces_each_reviewer_round_boundary(
    reviewer, max_elapsed,
):
    at_limit = budget.LedgerState.initial(
        REPOSITORY, PR, reviewer,
        invocations=(invocation(reviewer=reviewer, elapsed_seconds=max_elapsed),),
    )
    assert budget.parse_ledger(
        ledger_body(at_limit), repository=REPOSITORY, pr=PR, reviewer=reviewer,
    ) == at_limit
    above_limit = replace(
        at_limit,
        invocations=(replace(
            at_limit.invocations[0], elapsed_seconds=max_elapsed + 1,
        ),),
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
        ledger_body(normalized_failure),
        repository=REPOSITORY, pr=PR, reviewer=reviewer,
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


# --- configurable automatic round budget (issue #114) ---


@pytest.fixture(autouse=True)
def isolate_round_budget_variable(monkeypatch):
    """The round budget reads a repository variable, so keep it out of every other test."""

    monkeypatch.delenv(budget.MAX_ROUNDS_VARIABLE, raising=False)


def rounds(count):
    return tuple(
        invocation(
            head=chr(ord("a") + index) * 40,
            full_hash=str(index + 1) * 64,
            run_id=600 + index,
            round_number=index + 1,
        )
        for index in range(count)
    )


def test_round_budget_defaults_to_two_without_the_variable():
    assert budget.BudgetPolicy.for_reviewer("claude").max_rounds == 2


@pytest.mark.parametrize("reviewer", ("claude", "gemini", "opencode"))
@pytest.mark.parametrize("raw", ("1", "3", "5"))
def test_round_budget_honours_the_configured_variable(monkeypatch, reviewer, raw):
    monkeypatch.setenv(budget.MAX_ROUNDS_VARIABLE, raw)

    assert budget.BudgetPolicy.for_reviewer(reviewer).max_rounds == int(raw)


def test_round_budget_ignores_an_empty_variable_without_warning(monkeypatch, capsys):
    monkeypatch.setenv(budget.MAX_ROUNDS_VARIABLE, "")

    assert budget.BudgetPolicy.for_reviewer("claude").max_rounds == 2
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("raw", ("0", "6", "-1", "two", "3.0", " 3", "+3", "0x3"))
def test_round_budget_falls_back_and_warns_on_an_unusable_variable(monkeypatch, capsys, raw):
    monkeypatch.setenv(budget.MAX_ROUNDS_VARIABLE, raw)

    assert budget.BudgetPolicy.for_reviewer("claude").max_rounds == 2
    assert budget.MAX_ROUNDS_VARIABLE in capsys.readouterr().err


def test_ledger_capacity_follows_the_configured_round_budget(monkeypatch):
    monkeypatch.setenv(budget.MAX_ROUNDS_VARIABLE, "5")
    state = budget.LedgerState.initial(REPOSITORY, PR, "claude", invocations=rounds(4))

    budget._validate_state_shape(state)


def test_ledger_capacity_still_rejects_more_rounds_than_budgeted():
    state = budget.LedgerState.initial(REPOSITORY, PR, "claude", invocations=rounds(4))

    with pytest.raises(budget.BudgetStateError):
        budget._validate_state_shape(state)


@pytest.mark.parametrize(
    ("recorded", "configured", "effective"), ((2, 5, 5), (5, 2, 5), (2, 2, 2))
)
def test_effective_round_budget_is_the_higher_of_recorded_and_configured(
    monkeypatch, recorded, configured, effective
):
    monkeypatch.setenv(budget.MAX_ROUNDS_VARIABLE, str(configured))
    state = replace(
        budget.LedgerState.initial(REPOSITORY, PR, "claude"),
        budgets=replace(budget.BudgetPolicy.for_reviewer("claude"), max_rounds=recorded),
    )

    budget._validate_state_shape(state)

    assert budget.effective_budgets(state).max_rounds == effective


@pytest.mark.parametrize("raw", ("²", "٣", "１"))
def test_round_budget_rejects_non_ascii_digits(monkeypatch, capsys, raw):
    """str.isdigit() accepts superscripts and other scripts that int() may reject."""

    monkeypatch.setenv(budget.MAX_ROUNDS_VARIABLE, raw)

    assert budget.BudgetPolicy.for_reviewer("claude").max_rounds == 2
    assert budget.MAX_ROUNDS_VARIABLE in capsys.readouterr().err


def overridden_state():
    """Two automatic rounds recorded under a budget of two, then a label override."""

    automatic = rounds(2)
    override = invocation(
        head=HEAD_C, full_hash=HASH_3, run_id=700, round_number=3, override_event_id=9001
    )
    return budget.LedgerState.initial(
        REPOSITORY,
        PR,
        "claude",
        invocations=automatic + (override,),
        consumed_override_event_ids=(9001,),
    )


def test_recorded_override_stays_valid_without_a_raised_round_budget():
    budget._validate_state_shape(overridden_state())


def test_recorded_override_survives_a_raised_round_budget(monkeypatch):
    """A raise must not retroactively change when the override became eligible."""

    state = overridden_state()
    monkeypatch.setenv(budget.MAX_ROUNDS_VARIABLE, "5")

    budget._validate_state_shape(state)


# --- OpenCode cannot publish a dispatch round, so it must not spend one (issue #118) ---


def override_event(event_id: int = 9101):
    return budget.OverrideEvent(event_id, "labeled", "review-budget-override", "write")


@pytest.mark.parametrize("reviewer", ("claude", "gemini"))
def test_override_stays_available_for_the_publishing_reviewers(reviewer):
    state = budget.LedgerState.initial(REPOSITORY, PR, reviewer)

    assert budget.choose_override(state, (override_event(),)) is not None


def test_override_is_refused_for_opencode():
    """OpenCode's canonicalizer only accepts pull_request provenance, so a dispatch
    round can never publish. Spending the override there loses the verdict."""

    state = budget.LedgerState.initial(REPOSITORY, PR, "opencode")

    assert budget.choose_override(state, (override_event(),)) is None


# --- A collaborator can dismiss a false-positive finding by comment (issue #112) ---


FINDING_3 = "RVW-333333333333"
DISMISS_GUIDANCE = (
    "Budget exhaustion is not review approval. Use the authenticated review checkpoint and "
    "remaining finding IDs before merge. A collaborator with write permission can dismiss a "
    "false positive by commenting `dismiss RVW-<12 hex> <reason>` on this pull request; the "
    "dismissal takes effect on the next review run and is revoked by deleting that comment."
)


def dismiss_event(event_id=200, finding_id=FINDING_1, permission="write"):
    return budget.DismissEvent(event_id, finding_id, permission)


def dismissed(finding_id=FINDING_1, comment_id=200):
    return budget.DismissedFinding(finding_id, comment_id)


def authenticated(remaining=(FINDING_1, FINDING_2), head=HEAD_A, full_hash=HASH_1):
    return budget.AuthenticatedReview(True, head, full_hash, tuple(remaining))


@pytest.mark.parametrize(
    ("body", "expected"),
    (
        ("dismiss RVW-111111111111 the cited constant table is wrong", FINDING_1),
        ("dismiss RVW-111111111111 wrong\r\n", FINDING_1),
        ("dismiss RVW-111111111111 사유는 어느 언어로 써도 된다", FINDING_1),
        ("dismiss RVW-111111111111 punctuation: `code`, <tags>, -- fine", FINDING_1),
        ("dismiss RVW-111111111111", None),
        ("dismiss RVW-111111111111 ", None),
        ("dismiss RVW-111111111111   ", None),
        ("dismiss RVW-111111111111  double space", None),
        ("Dismiss RVW-111111111111 reason", None),
        ("dismiss rvw-111111111111 reason", None),
        ("dismiss RVW-11111111111 reason", None),
        ("dismiss RVW-1111111111111 reason", None),
        ("dismiss RVW-11111111111g reason", None),
        ("dismiss RVW-111111111111 reason\nsecond line", None),
        ("dismiss RVW-111111111111 reason\r\nsecond line", None),
        ("please dismiss RVW-111111111111 reason", None),
        (" dismiss RVW-111111111111 reason", None),
        ("dismiss  RVW-111111111111 reason", None),
        ("dismiss\tRVW-111111111111 reason", None),
        ("", None),
        ("dismiss", None),
    ),
)
def test_dismiss_command_grammar_is_fixed(body, expected):
    assert budget.parse_dismiss_command(body) == expected


def test_dismissals_require_write_permission_and_bind_the_earliest_comment():
    events = (
        dismiss_event(300, FINDING_1, "write"),
        dismiss_event(200, FINDING_1, "admin"),
        dismiss_event(400, FINDING_2, "read"),
        dismiss_event(500, FINDING_2, "triage"),
        dismiss_event(501, FINDING_2, None),
        dismiss_event(600, FINDING_3, "maintain"),
    )

    state = budget.LedgerState.initial(REPOSITORY, PR, "claude")

    assert budget.choose_dismissals(state, events) == (dismissed(FINDING_1, 200), dismissed(FINDING_3, 600))
    assert budget.choose_dismissals(state, ()) == ()


def test_dismissals_beyond_the_ledger_bound_fail_closed():
    """The bound is a documented contract value, so the test names the literal."""

    assert budget.MAX_DISMISSED_FINDINGS == 16
    state = budget.LedgerState.initial(REPOSITORY, PR, "claude")
    events = tuple(dismiss_event(1000 + index, f"RVW-{index:012x}", "write") for index in range(17))

    with pytest.raises(budget.BudgetStateError, match="dismissed_findings_invalid"):
        budget.choose_dismissals(state, events)
    assert len(budget.choose_dismissals(state, events[1:])) == 16


def test_dismissals_apply_to_opencode_like_every_other_reviewer():
    """OpenCode 도 기각을 받는다 — v1.70 이 그 finding 에 ID 를 부여했기 때문이다 (#112).

    ID 가 없던 동안에는 기각이 가리킬 대상이 없어 이 리뷰어만 제외돼 있었다. 이제
    제외할 이유가 사라졌고, 제외를 유지하면 사람이 OpenCode 오탐을 기각할 방법이
    끝내 생기지 않는다.
    """

    state = budget.LedgerState.initial(REPOSITORY, PR, "opencode")

    assert budget.choose_dismissals(state, (dismiss_event(),)) == (dismissed(FINDING_1, 200),)
    claim_request = request(reviewer="opencode", dismiss_events=(dismiss_event(),))
    claimed = budget.claim(None, claim_request, claim_provenances(None, claim_request)).state
    assert claimed.dismissed_findings == (dismissed(FINDING_1, 200),)

    summary = budget.render_summary(claimed)
    # 원장 코멘트가 기각 내역과 문법을 보여주지 않으면 사람은 이 경로의 존재를 알 수 없다.
    assert f"- Dismissed findings: [{FINDING_1}](" in summary
    assert "dismiss RVW-<12 hex> <reason>" in summary


def test_a_present_but_empty_dismissal_list_is_not_a_ledger_shape():
    payload = budget.LedgerState.initial(REPOSITORY, PR, "claude").to_dict()
    payload["dismissed_findings"] = []

    with pytest.raises(budget.BudgetStateError, match="dismissed_findings_invalid"):
        budget.LedgerState.from_dict(payload)


def test_ledger_serializes_dismissals_only_when_present():
    """A ledger without dismissals keeps the pre-v1.63 bytes, so open PRs stay valid."""

    state = budget.LedgerState.initial(REPOSITORY, PR, "claude")
    with_dismissal = replace(state, dismissed_findings=(dismissed(),))

    assert "dismissed_findings" not in state.to_dict()
    assert with_dismissal.to_dict()["dismissed_findings"] == [
        {"comment_id": 200, "finding_id": FINDING_1},
    ]
    assert budget.LedgerState.from_dict(with_dismissal.to_dict()) == with_dismissal
    for candidate in (state, with_dismissal):
        assert budget.parse_ledger(
            ledger_body(candidate), repository=REPOSITORY, pr=PR, reviewer="claude",
        ) == candidate
        assert budget.load_checkpoint(budget.render_checkpoint(candidate)) == candidate


@pytest.mark.parametrize(
    "entries",
    (
        (dismissed(FINDING_1, 200), dismissed(FINDING_1, 300)),
        (dismissed(FINDING_2, 200), dismissed(FINDING_1, 300)),
        (dismissed(FINDING_1, 200), dismissed(FINDING_2, 200)),
        (dismissed("RVW-not-a-finding", 200),),
        (dismissed(FINDING_1, 0),),
        (dismissed(FINDING_1, True),),
        tuple(
            dismissed(f"RVW-{index:012x}", 1000 + index)
            for index in range(budget.MAX_DISMISSED_FINDINGS + 1)
        ),
    ),
)
def test_stored_dismissals_are_validated(entries):
    state = replace(budget.LedgerState.initial(REPOSITORY, PR, "claude"), dismissed_findings=entries)

    assert_stored_state_rejected(state, "dismissed_findings_invalid")


def test_the_dismissal_bound_is_pinned_to_the_documented_literal():
    assert budget.MAX_DISMISSED_FINDINGS == 16
    assert budget.MAX_PERMISSION_ACTORS == 16


def test_a_dismissed_id_can_never_remain_in_the_handoff():
    state = claimed_state()
    finalized = budget.finalize(
        state, finalize_request(remaining=(FINDING_1, FINDING_2)), current_provenances(state),
    ).state
    assert finalized.handoff.remaining_finding_ids == (FINDING_1, FINDING_2)

    stale = replace(finalized, dismissed_findings=(dismissed(FINDING_1),))

    assert_stored_state_rejected(stale, "handoff_mismatch")


def test_claim_rejects_malformed_dismiss_events():
    malformed = request(dismiss_events=(("not", "an", "event"),))

    transition = budget.claim(None, malformed, claim_provenances(None, malformed))

    assert (transition.decision, transition.stop_reason) == ("state_invalid", "dismiss_events_invalid")


def test_claim_records_authorized_dismissals_and_drops_them_from_remaining_ids():
    state = claimed_state()
    first = budget.finalize(
        state, finalize_request(remaining=(FINDING_1, FINDING_2)), current_provenances(state),
    ).state
    second = request(
        head=HEAD_B, full_hash=HASH_2, run_id=701, authenticated_review=authenticated(),
        dismiss_events=(dismiss_event(200, FINDING_1, "write"), dismiss_event(201, FINDING_2, "read")),
    )

    transition = budget.claim(first, second, claim_provenances(first, second))

    assert transition.decision == "claimed"
    assert transition.state.dismissed_findings == (dismissed(FINDING_1, 200),)
    assert transition.state.handoff.remaining_finding_ids == (FINDING_2,)
    budget.serialize_ledger(transition.state)


def test_refused_claim_still_records_dismissals_and_updates_the_handoff():
    """The issue #112 shape: both rounds are spent, then a human dismisses the false positive."""

    state = claimed_state()
    first = budget.finalize(
        state, finalize_request(remaining=(FINDING_1, FINDING_2)), current_provenances(state),
    ).state
    second_request = request(head=HEAD_B, full_hash=HASH_2, run_id=701, authenticated_review=authenticated())
    second = budget.claim(first, second_request, claim_provenances(first, second_request)).state
    second = budget.finalize(
        second,
        finalize_request(
            head=HEAD_B, full_hash=HASH_2, run_id=701, remaining=(FINDING_1, FINDING_2),
            authenticated_review=authenticated(),
        ),
        current_provenances(second),
    ).state
    third_request = request(
        head=HEAD_C, full_hash=HASH_3, run_id=702,
        authenticated_review=authenticated(head=HEAD_B, full_hash=HASH_2),
        dismiss_events=(dismiss_event(200, FINDING_1, "write"),),
    )

    refused = budget.claim(second, third_request, claim_provenances(second, third_request))

    assert (refused.decision, refused.mutate_comment) == ("round_budget_exhausted", True)
    assert refused.state.dismissed_findings == (dismissed(FINDING_1, 200),)
    assert refused.state.handoff.remaining_finding_ids == (FINDING_2,)
    budget.serialize_ledger(refused.state)


def test_dismissal_is_revoked_when_the_comment_no_longer_exists():
    state = claimed_state()
    first = budget.finalize(
        state, finalize_request(remaining=(FINDING_1, FINDING_2)), current_provenances(state),
    ).state
    dismissing = request(
        head=HEAD_B, full_hash=HASH_2, run_id=701, authenticated_review=authenticated(),
        dismiss_events=(dismiss_event(),),
    )
    with_dismissal = budget.claim(first, dismissing, claim_provenances(first, dismissing)).state
    assert with_dismissal.handoff.remaining_finding_ids == (FINDING_2,)
    revoking = request(head=HEAD_C, full_hash=HASH_3, run_id=702, authenticated_review=authenticated())

    revoked = budget.claim(with_dismissal, revoking, claim_provenances(with_dismissal, revoking)).state

    assert revoked.dismissed_findings == ()
    assert revoked.handoff.remaining_finding_ids == (FINDING_1, FINDING_2)


def test_finalize_refreshes_dismissals_and_never_records_a_dismissed_id_as_remaining():
    claim_request = request(dismiss_events=(dismiss_event(200, FINDING_1),))
    state = budget.claim(None, claim_request, claim_provenances(None, claim_request)).state
    assert state.dismissed_findings == (dismissed(FINDING_1, 200),)

    finalized = budget.finalize(
        state,
        finalize_request(
            remaining=(FINDING_1, FINDING_2),
            dismiss_events=(dismiss_event(200, FINDING_1), dismiss_event(300, FINDING_2)),
        ),
        current_provenances(state),
    ).state

    assert finalized.dismissed_findings == (dismissed(FINDING_1, 200), dismissed(FINDING_2, 300))
    assert finalized.invocations[-1].remaining_finding_ids == ()
    assert finalized.handoff.remaining_finding_ids == ()
    budget.serialize_ledger(finalized)


def test_comment_summary_lists_dismissals_and_documents_the_command():
    claim_request = request(dismiss_events=(dismiss_event(200, FINDING_1), dismiss_event(300, FINDING_2)))
    state = budget.claim(None, claim_request, claim_provenances(None, claim_request)).state

    assert budget.render_summary(state) == (
        "## Claude review invocation budget\n"
        "- Decision: claimed\n"
        "- Automatic rounds: 1/2\n"
        "- Override rounds: 0/1\n"
        "- Current run: https://github.com/example/repo/actions/runs/700\n"
        "- Stop reason: claimed\n"
        "- Dismissed findings: "
        "[RVW-111111111111](https://github.com/example/repo/pull/52#issuecomment-200), "
        "[RVW-222222222222](https://github.com/example/repo/pull/52#issuecomment-300)\n\n"
        f"{DISMISS_GUIDANCE}"
    )


def test_invalid_claim_after_a_dismissal_still_renders_a_checkpoint():
    """A refusal that keeps the validated state must not leave a handoff naming a dismissed ID."""

    state = claimed_state()
    finalized = budget.finalize(
        state, finalize_request(remaining=(FINDING_1, FINDING_2)), current_provenances(state),
    ).state
    conflicting = request(head=HEAD_B, full_hash=HASH_2, dismiss_events=(dismiss_event(),))

    transition = budget.claim(finalized, conflicting, valid_provenances(finalized))

    assert (transition.decision, transition.stop_reason) == ("state_invalid", "duplicate_run_identity")
    assert transition.state.dismissed_findings == (dismissed(),)
    assert transition.state.handoff.remaining_finding_ids == (FINDING_2,)
    budget.render_checkpoint(transition.state)
