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


def request(*, head=HEAD_A, full_hash=HASH_1, run_id=700, run_attempt=1, **changes):
    values = {
        "repository": REPOSITORY,
        "pr": PR,
        "reviewer": "claude",
        "run_id": run_id,
        "run_attempt": run_attempt,
        "head_sha": head,
        "full_diff_sha256": full_hash,
        "estimated_input_tokens": 50_000,
        "diff_mode": "changed",
        "authenticated_review": budget.AuthenticatedReview(False, None, None),
        "override_events": (),
        "model_route": ("claude-code-action-default",),
        "effort": "final-review/default",
        "call_unit": "claude-code-action review session",
    }
    values.update(changes)
    return budget.ClaimRequest(**values)


def invocation(*, head=HEAD_A, full_hash=HASH_1, run_id=501, run_attempt=1,
               round_number=1, outcome="success", status="finalized", override_event_id=None,
               call_count=None, estimated_input_tokens=50_000, elapsed_seconds=None):
    if call_count is None:
        call_count = 1 if status == "finalized" else 0
    if elapsed_seconds is None:
        elapsed_seconds = 10 if status == "finalized" else 0
    return budget.Invocation(
        run_id=run_id, run_attempt=run_attempt, head_sha=head,
        full_diff_sha256=full_hash, round_number=round_number,
        override_event_id=override_event_id, model_route=("claude-code-action-default",),
        effort="final-review/default", call_unit="claude-code-action review session",
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
            workflow_path=budget.WORKFLOWS["claude"], run_id=entry.run_id,
            run_attempt=entry.run_attempt, status="completed", conclusion="cancelled",
        )
        for entry in state.invocations
    }


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
    for vector in vectors:
        events = ()
        if "override_event" in vector:
            events = (budget.OverrideEvent(vector["override_event"], "labeled", "review-budget-override", "write"),)
        result = budget.claim(
            state_for(vector["prior"]),
            request(head=vector["head"], full_hash=vector["full_hash"], override_events=events),
            valid_provenances(state_for(vector["prior"])) if vector["prior"] != "empty" else {},
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


@pytest.mark.parametrize("field", ["repository", "pr", "head_sha", "workflow_path", "run_attempt"])
def test_claim_fails_closed_when_historical_provenance_mismatches(field, two_round_state, claim_request):
    provenances = valid_provenances(two_round_state)
    mismatch = {"repository": "other/repo", "pr": 53, "head_sha": HEAD_B,
                "workflow_path": "other.yml", "run_attempt": 2}[field]
    provenances[(501, 1)] = replace(provenances[(501, 1)], **{field: mismatch})
    result = budget.claim(two_round_state, claim_request, provenances)
    assert not result.allow_invocation
    assert result.decision == "state_invalid"
    assert result.stop_reason == "provenance_mismatch"


@pytest.mark.parametrize("conclusion", ["cancelled", "timed_out"])
def test_claim_accepts_current_in_progress_and_historical_terminal_provenance(
        conclusion, two_round_state, claim_request):
    provenances = valid_provenances(two_round_state)
    provenances = {key: replace(value, conclusion=conclusion) for key, value in provenances.items()}
    result = budget.claim(two_round_state, claim_request, provenances)
    assert result.decision == "round_budget_exhausted"
    claimed = budget.claim(None, claim_request, {})
    assert claimed.allow_invocation
    existing = claimed.state
    same_request = request(head=HEAD_C, full_hash=HASH_3)
    provenances = {
        (700, 1): budget.RunProvenance(REPOSITORY, PR, HEAD_C, budget.WORKFLOWS["claude"], 700, 1, "in_progress", None)
    }
    result = budget.claim(existing, same_request, provenances)
    assert result.decision == "duplicate_head"
    assert not result.allow_invocation


def test_claim_rejects_historical_in_progress_provenance(two_round_state, claim_request):
    provenances = valid_provenances(two_round_state)
    provenances[(501, 1)] = replace(provenances[(501, 1)], status="in_progress", conclusion=None)
    result = budget.claim(two_round_state, claim_request, provenances)
    assert not result.allow_invocation
    assert result.decision == "state_invalid"
    assert result.stop_reason == "provenance_mismatch"


def test_claim_rejects_boolean_authenticated_success_and_duplicate_run_identity():
    malformed_review = request(
        diff_mode="unchanged", authenticated_review=budget.AuthenticatedReview(1, HEAD_A, HASH_1),
    )
    assert budget.claim(None, malformed_review, {}).decision == "state_invalid"
    existing = budget.claim(None, request(), {}).state
    duplicate_run = request(head=HEAD_B, full_hash=HASH_2)
    current = {
        (700, 1): budget.RunProvenance(REPOSITORY, PR, HEAD_A, budget.WORKFLOWS["claude"], 700, 1, "in_progress", None)
    }
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
    refused = budget.claim(empty_state, unchanged_request, {})
    assert refused.decision == "state_invalid"
    assert refused.stop_reason == "unchanged_without_authenticated_review"


def test_new_head_with_authenticated_same_hash_reuses_without_a_call(empty_state, unchanged_request):
    request_with_authenticated = replace(
        unchanged_request, head_sha=HEAD_B,
        authenticated_review=budget.AuthenticatedReview(True, HEAD_A, unchanged_request.full_diff_sha256),
    )
    result = budget.claim(empty_state, request_with_authenticated, {})
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
        REPOSITORY, PR, reviewer, invocations=(invocation(call_count=max_calls),),
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
