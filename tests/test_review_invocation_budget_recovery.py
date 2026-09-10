from dataclasses import replace

import pytest

from test_review_invocation_budget import (
    FINDING_1,
    HASH_1,
    HASH_2,
    HEAD_A,
    HEAD_B,
    ROUTES,
    budget,
    claim_provenances,
    claimed_state,
    finalize_request,
    request,
    valid_provenances,
)


def recovery_case():
    state = claimed_state("opencode")
    original_claim = state.invocations[0]
    recovery_request = finalize_request(
        reviewer="opencode",
        calls=1,
        elapsed=45,
        outcome="success",
        authenticated_review=budget.AuthenticatedReview(
            True,
            HEAD_A,
            HASH_1,
        ),
    )
    return state, original_claim, recovery_request


def test_recovery_finalizes_the_original_invocation_without_appending_a_round():
    state, original_claim, recovery_request = recovery_case()

    result = budget.recover_finalize(
        state,
        original_claim,
        recovery_request,
        valid_provenances(state),
    )

    assert result.decision == "finalized"
    assert result.state.invocations[0].run_attempt == 1
    assert result.state.invocations[0].call_count == 1
    assert result.state.invocations[0].elapsed_seconds == 45
    assert len(result.state.invocations) == 1
    assert result.allow_invocation is False
    assert result.mutate_comment is True


def test_recovery_preserves_authenticated_quality_filtered_outcome_and_findings():
    state, original_claim, recovery_request = recovery_case()
    recovery_request = replace(
        recovery_request,
        outcome="quality_filtered",
        stop_reason="quality_filtered",
        remaining_finding_ids=(FINDING_1,),
    )

    result = budget.recover_finalize(
        state,
        original_claim,
        recovery_request,
        valid_provenances(state),
    )

    assert result.decision == "finalized"
    assert result.state.invocations[0].outcome == "quality_filtered"
    assert result.state.invocations[0].remaining_finding_ids == (FINDING_1,)
    assert len(result.state.invocations) == 1


def test_identical_recovery_repeat_is_an_unchanged_successful_transition():
    state, original_claim, recovery_request = recovery_case()
    result = budget.recover_finalize(
        state,
        original_claim,
        recovery_request,
        valid_provenances(state),
    )

    repeat = budget.recover_finalize(
        result.state,
        original_claim,
        recovery_request,
        valid_provenances(result.state),
    )

    assert repeat.decision == "finalized"
    assert repeat.stop_reason == "success"
    assert repeat.state == result.state
    assert repeat.round_number == 1
    assert repeat.invocation_key == "700:1"
    assert repeat.allow_invocation is False
    assert repeat.mutate_comment is False


def test_ordinary_finalize_still_refuses_a_repeat():
    state, original_claim, recovery_request = recovery_case()
    recovered = budget.recover_finalize(
        state,
        original_claim,
        recovery_request,
        valid_provenances(state),
    )

    repeated = budget.finalize(
        recovered.state,
        recovery_request,
        valid_provenances(recovered.state),
    )

    assert repeated.decision == "state_invalid"
    assert repeated.stop_reason == "invocation_not_claimed"
    assert repeated.mutate_comment is False


def test_provider_failure_is_outside_recovery_even_with_inherited_reuse_findings():
    reuse_request = request(reviewer="opencode", diff_mode="unchanged", authenticated_review=budget.AuthenticatedReview(
        True, HEAD_A, HASH_1, (FINDING_1,)))
    reused = budget.claim(None, reuse_request, claim_provenances(None, reuse_request)).state
    assert not reused.invocations
    assert reused.handoff.remaining_finding_ids == (FINDING_1,)
    next_request = request(reviewer="opencode", head=HEAD_B, full_hash=HASH_2, run_id=701)
    state = budget.claim(reused, next_request, claim_provenances(reused, next_request)).state
    recovery_request = finalize_request(reviewer="opencode", head=HEAD_B, full_hash=HASH_2,
                                        run_id=701, outcome="provider_failure")
    original = state.invocations[-1]
    refused = budget.recover_finalize(state, original, recovery_request, valid_provenances(state))
    assert refused.decision == "state_invalid"
    assert refused.mutate_comment is False
    first = budget.finalize(state, recovery_request, valid_provenances(state))
    assert first.decision == "finalized"
    assert first.state.invocations[-1].remaining_finding_ids == (FINDING_1,)
    repeat = budget.recover_finalize(first.state, original, recovery_request,
                                     valid_provenances(first.state))
    assert repeat.decision == "state_invalid"
    assert repeat.state == first.state
    assert repeat.mutate_comment is False


def test_recovery_requires_authenticated_success_before_any_finalization():
    state, original, recovery_request = recovery_case()
    result = budget.recover_finalize(state, original, replace(recovery_request,
        authenticated_review=budget.AuthenticatedReview(False, None, None)), valid_provenances(state))
    assert result.decision == "state_invalid"
    assert result.state == state
    assert result.mutate_comment is False


def test_recovery_is_restricted_to_opencode():
    state = claimed_state("claude")
    original_claim = state.invocations[0]
    recovery_request = finalize_request(reviewer="claude", calls=1, elapsed=45)

    result = budget.recover_finalize(
        state,
        original_claim,
        recovery_request,
        valid_provenances(state),
    )

    assert result.decision == "state_invalid"
    assert result.stop_reason == "recovery_reviewer_invalid"
    assert result.state == state
    assert result.mutate_comment is False


@pytest.mark.parametrize(
    ("change", "value"),
    [
        ("run_id", 701),
        ("run_attempt", 2),
        ("head_sha", HEAD_B),
        ("full_diff_sha256", HASH_2),
    ],
)
def test_recovery_rejects_request_identity_drift(change, value):
    state, original_claim, recovery_request = recovery_case()

    result = budget.recover_finalize(
        state,
        original_claim,
        replace(recovery_request, **{change: value}),
        valid_provenances(state),
    )

    assert result.decision == "state_invalid"
    assert result.stop_reason == "recovery_request_mismatch"
    assert result.state == state
    assert result.mutate_comment is False


@pytest.mark.parametrize(
    ("change", "value"),
    [
        ("caller_workflow_path", ".github/workflows/other-caller.yml"),
        ("referenced_workflow_sha", "e" * 40),
        ("estimated_input_tokens", 49_999),
        ("effort", "high"),
    ],
)
def test_recovery_rejects_changed_original_claim_fields(change, value):
    state, original_claim, recovery_request = recovery_case()

    result = budget.recover_finalize(
        state,
        replace(original_claim, **{change: value}),
        recovery_request,
        valid_provenances(state),
    )

    assert result.decision == "state_invalid"
    assert result.stop_reason == "original_claim_mismatch"
    assert result.state == state
    assert result.mutate_comment is False


def test_recovery_rejects_an_original_invocation_with_a_newer_generation():
    state, original_claim, recovery_request = recovery_case()
    finalized = budget.finalize(
        state,
        recovery_request,
        valid_provenances(state),
    ).state
    next_request = request(
        reviewer="opencode",
        head=HEAD_B,
        full_hash=HASH_2,
        run_id=701,
    )
    newer = budget.claim(
        finalized,
        next_request,
        claim_provenances(finalized, next_request),
    ).state

    result = budget.recover_finalize(
        newer,
        original_claim,
        recovery_request,
        valid_provenances(newer),
    )

    assert result.decision == "state_invalid"
    assert result.stop_reason == "newer_invocation_exists"
    assert result.state == newer
    assert result.mutate_comment is False


def test_recovery_repeat_rejects_changed_original_immutable_fields():
    state, original_claim, recovery_request = recovery_case()
    recovered = budget.recover_finalize(
        state,
        original_claim,
        recovery_request,
        valid_provenances(state),
    )

    repeat = budget.recover_finalize(
        recovered.state,
        replace(original_claim, referenced_workflow_ref="refs/tags/v1.48"),
        recovery_request,
        valid_provenances(recovered.state),
    )

    assert repeat.decision == "state_invalid"
    assert repeat.stop_reason == "original_claim_mismatch"
    assert repeat.state == recovered.state
    assert repeat.mutate_comment is False


@pytest.mark.parametrize(
    "changes",
    [
        {"call_count": 2},
        {"elapsed_seconds": 46},
        {"outcome": "provider_failure", "stop_reason": "provider_failure"},
        {"stop_reason": "different_success_reason"},
        {"remaining_finding_ids": (FINDING_1,)},
        {"model_route": ROUTES["opencode"] + ("unexpected-fallback",)},
        {"effort": "high"},
    ],
)
def test_recovery_rejects_changed_finalization_details_on_repeat(changes):
    state, original_claim, recovery_request = recovery_case()
    recovered = budget.recover_finalize(
        state,
        original_claim,
        recovery_request,
        valid_provenances(state),
    )

    repeat = budget.recover_finalize(
        recovered.state,
        original_claim,
        replace(recovery_request, **changes),
        valid_provenances(recovered.state),
    )

    assert repeat.decision == "state_invalid"
    assert repeat.stop_reason == "recovery_finalization_conflict"
    assert repeat.state == recovered.state
    assert repeat.mutate_comment is False


@pytest.mark.parametrize(
    "changes",
    [
        {"call_count": 2},
        {"elapsed_seconds": 46},
        {"outcome": "provider_failure", "stop_reason": "provider_failure"},
        {"remaining_finding_ids": (FINDING_1,)},
    ],
)
def test_recovery_repeat_rejects_changed_live_finalized_fields(changes):
    state, original_claim, recovery_request = recovery_case()
    recovered = budget.recover_finalize(
        state,
        original_claim,
        recovery_request,
        valid_provenances(state),
    )
    changed_entry = replace(recovered.state.invocations[0], **changes)
    changed_handoff = replace(
        recovered.state.handoff,
        round_usage=((
            changed_entry.round_number,
            changed_entry.call_count,
            changed_entry.estimated_input_tokens,
            changed_entry.elapsed_seconds,
        ),),
        outcome=changed_entry.outcome,
        stop_reason=changed_entry.stop_reason,
        remaining_finding_ids=changed_entry.remaining_finding_ids,
    )
    changed_state = replace(
        recovered.state,
        invocations=(changed_entry,),
        last_decision=replace(
            recovered.state.last_decision,
            stop_reason=changed_entry.stop_reason,
        ),
        handoff=changed_handoff,
    )
    budget.serialize_ledger(changed_state)

    repeat = budget.recover_finalize(
        changed_state,
        original_claim,
        recovery_request,
        valid_provenances(changed_state),
    )

    assert repeat.decision == "state_invalid"
    assert repeat.stop_reason == "recovery_finalization_conflict"
    assert repeat.state == changed_state
    assert repeat.mutate_comment is False


def test_recovery_repeat_requires_live_original_run_provenance():
    state, original_claim, recovery_request = recovery_case()
    recovered = budget.recover_finalize(
        state,
        original_claim,
        recovery_request,
        valid_provenances(state),
    )

    repeat = budget.recover_finalize(
        recovered.state,
        original_claim,
        recovery_request,
        {},
    )

    assert repeat.decision == "state_invalid"
    assert repeat.stop_reason == "provenance_mismatch"
    assert repeat.state == recovered.state


def test_recovery_rejects_malformed_live_state_without_mutating_it():
    state, original_claim, recovery_request = recovery_case()
    malformed = replace(
        state,
        invocations=(replace(state.invocations[0], call_count=1),),
    )

    result = budget.recover_finalize(
        malformed,
        original_claim,
        recovery_request,
        valid_provenances(state),
    )

    assert result.decision == "state_invalid"
    assert result.stop_reason == "status_invalid"
    assert result.state == malformed
    assert result.mutate_comment is False
