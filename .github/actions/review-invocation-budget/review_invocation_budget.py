"""Pure, fail-closed state transitions for review invocation budgets."""

from __future__ import annotations

import json
import math
import re
import stat
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal, Mapping, Sequence


Reviewer = Literal["claude", "gemini", "opencode"]
Outcome = Literal["success", "provider_failure", "quality_filtered", "checkpoint_failure", "wall_time_exhausted"]
Decision = Literal["claimed", "finalized", "state_invalid", "diff_unavailable", "authenticated_reuse", "duplicate_head", "duplicate_effective_diff", "input_budget_exhausted", "round_budget_exhausted", "total_usage_budget_exhausted"]

SCHEMA = 1
STATE_PREFIX = "<!-- automation-budget-state:"
STATE_SUFFIX = " -->"
MARKERS = {
    "claude": "<!-- automation:review-invocation-budget:claude:v1 -->",
    "gemini": "<!-- automation:review-invocation-budget:gemini:v1 -->",
    "opencode": "<!-- automation:review-invocation-budget:opencode:v1 -->",
}
WORKFLOWS = {
    "claude": ".github/workflows/claude-code-review.yml",
    "gemini": ".github/workflows/gemini-auto-review.yml",
    "opencode": ".github/workflows/opencode-auto-review.yml",
}

_HEAD = re.compile(r"[0-9a-f]{40}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_FINDING = re.compile(r"RVW-[0-9a-f]{12}\Z")
_OUTCOMES = {"success", "provider_failure", "quality_filtered", "checkpoint_failure", "wall_time_exhausted"}


class BudgetStateError(ValueError):
    """The persisted state or a state-machine input is unsafe to use."""


def _integer(value: object, name: str, *, positive: bool = False, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BudgetStateError(f"{name}_invalid")
    if positive and value <= 0:
        raise BudgetStateError(f"{name}_invalid")
    if minimum is not None and value < minimum:
        raise BudgetStateError(f"{name}_invalid")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise BudgetStateError(f"{name}_invalid")
    return value


def _hash(value: object, pattern: re.Pattern[str], name: str) -> str:
    value = _string(value, name)
    if pattern.fullmatch(value) is None:
        raise BudgetStateError(f"{name}_invalid")
    return value


def _exact_keys(value: object, keys: set[str], name: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise BudgetStateError(f"{name}_invalid")
    return value


@dataclass(frozen=True)
class BudgetPolicy:
    max_rounds: int = 2
    max_override_rounds: int = 1
    max_calls_per_round: int = 1
    max_wall_seconds_per_round: int = 600
    max_estimated_tokens_per_round: int = 200_000
    max_estimated_tokens_total: int = 400_000

    @classmethod
    def for_reviewer(cls, reviewer: Reviewer) -> "BudgetPolicy":
        if reviewer not in MARKERS:
            raise BudgetStateError("reviewer_invalid")
        return cls(max_calls_per_round={"claude": 1, "gemini": 3, "opencode": 2}[reviewer])

    def to_dict(self) -> dict[str, int]:
        return {
            "max_rounds": self.max_rounds,
            "max_override_rounds": self.max_override_rounds,
            "max_calls_per_round": self.max_calls_per_round,
            "max_wall_seconds_per_round": self.max_wall_seconds_per_round,
            "max_estimated_tokens_per_round": self.max_estimated_tokens_per_round,
            "max_estimated_tokens_total": self.max_estimated_tokens_total,
        }

    @classmethod
    def from_dict(cls, value: object) -> "BudgetPolicy":
        keys = set(cls().to_dict())
        value = _exact_keys(value, keys, "budgets")
        return cls(**{key: _integer(value[key], key, positive=True) for key in keys})


@dataclass(frozen=True)
class AuthenticatedReview:
    success: bool
    head_sha: str | None
    full_diff_sha256: str | None
    remaining_finding_ids: tuple[str, ...] = ()

    def covers_hash(self, full_diff_sha256: str) -> bool:
        return self.success and self.full_diff_sha256 == full_diff_sha256


@dataclass(frozen=True)
class OverrideEvent:
    event_id: int
    event: str
    label: str | None
    actor_permission: str | None


@dataclass(frozen=True)
class RunProvenance:
    repository: str
    pr: int
    head_sha: str
    workflow_path: str
    run_id: int
    run_attempt: int
    status: str
    conclusion: str | None


@dataclass(frozen=True)
class Invocation:
    run_id: int
    run_attempt: int
    head_sha: str
    full_diff_sha256: str
    round_number: int
    override_event_id: int | None
    model_route: tuple[str, ...]
    effort: str
    call_unit: str
    call_count: int
    estimated_input_tokens: int
    elapsed_seconds: int
    status: str
    outcome: Outcome | None
    stop_reason: str
    remaining_finding_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "call_count": self.call_count,
            "call_unit": self.call_unit,
            "effort": self.effort,
            "elapsed_seconds": self.elapsed_seconds,
            "full_diff_sha256": self.full_diff_sha256,
            "head_sha": self.head_sha,
            "model_route": list(self.model_route),
            "outcome": self.outcome,
            "override_event_id": self.override_event_id,
            "remaining_finding_ids": list(self.remaining_finding_ids),
            "round_number": self.round_number,
            "run_attempt": self.run_attempt,
            "run_id": self.run_id,
            "status": self.status,
            "stop_reason": self.stop_reason,
            "estimated_input_tokens": self.estimated_input_tokens,
        }

    @classmethod
    def from_dict(cls, value: object) -> "Invocation":
        keys = {
            "run_id", "run_attempt", "head_sha", "full_diff_sha256", "round_number",
            "override_event_id", "model_route", "effort", "call_unit", "call_count",
            "estimated_input_tokens", "elapsed_seconds", "status", "outcome", "stop_reason",
            "remaining_finding_ids",
        }
        value = _exact_keys(value, keys, "invocation")
        route = value["model_route"]
        findings = value["remaining_finding_ids"]
        if not isinstance(route, list) or not route or not all(isinstance(item, str) and item for item in route):
            raise BudgetStateError("model_route_invalid")
        if not isinstance(findings, list) or len(findings) > 8 or len(findings) != len(set(findings)):
            raise BudgetStateError("remaining_finding_ids_invalid")
        if not all(isinstance(item, str) and _FINDING.fullmatch(item) for item in findings):
            raise BudgetStateError("remaining_finding_ids_invalid")
        override = value["override_event_id"]
        if override is not None:
            override = _integer(override, "override_event_id", positive=True)
        status = _string(value["status"], "status")
        outcome = value["outcome"]
        if status not in {"claimed", "finalized"} or (status == "claimed") != (outcome is None):
            raise BudgetStateError("status_invalid")
        if status == "claimed" and (value["call_count"] != 0 or value["elapsed_seconds"] != 0 or value["stop_reason"] != "claimed"):
            raise BudgetStateError("status_invalid")
        if outcome is not None and (not isinstance(outcome, str) or outcome not in _OUTCOMES):
            raise BudgetStateError("outcome_invalid")
        return cls(
            run_id=_integer(value["run_id"], "run_id", positive=True),
            run_attempt=_integer(value["run_attempt"], "run_attempt", positive=True),
            head_sha=_hash(value["head_sha"], _HEAD, "head_sha"),
            full_diff_sha256=_hash(value["full_diff_sha256"], _HASH, "full_diff_sha256"),
            round_number=_integer(value["round_number"], "round_number", positive=True),
            override_event_id=override, model_route=tuple(route), effort=_string(value["effort"], "effort"),
            call_unit=_string(value["call_unit"], "call_unit"),
            call_count=_integer(value["call_count"], "call_count", minimum=0),
            estimated_input_tokens=_integer(value["estimated_input_tokens"], "estimated_input_tokens", minimum=0),
            elapsed_seconds=_integer(value["elapsed_seconds"], "elapsed_seconds", minimum=0),
            status=status, outcome=outcome, stop_reason=_string(value["stop_reason"], "stop_reason"),
            remaining_finding_ids=tuple(findings),
        )


@dataclass(frozen=True)
class DecisionRecord:
    decision: str | None = None
    stop_reason: str | None = None
    run_id: int | None = None
    run_attempt: int | None = None

    def to_dict(self) -> dict[str, object]:
        if self.decision is None:
            return {}
        return {"decision": self.decision, "run_attempt": self.run_attempt, "run_id": self.run_id, "stop_reason": self.stop_reason}

    @classmethod
    def from_dict(cls, value: object) -> "DecisionRecord":
        if value == {}:
            return cls()
        value = _exact_keys(value, {"decision", "stop_reason", "run_id", "run_attempt"}, "last_decision")
        decision = _string(value["decision"], "decision")
        if decision not in {"claimed", "finalized", "state_invalid", "diff_unavailable", "authenticated_reuse", "duplicate_head", "duplicate_effective_diff", "input_budget_exhausted", "round_budget_exhausted", "total_usage_budget_exhausted"}:
            raise BudgetStateError("decision_invalid")
        return cls(decision, _string(value["stop_reason"], "stop_reason"),
                   _integer(value["run_id"], "run_id", positive=True), _integer(value["run_attempt"], "run_attempt", positive=True))


@dataclass(frozen=True)
class Handoff:
    values: tuple[tuple[str, object], ...] = ()

    def to_dict(self) -> dict[str, object]:
        return dict(self.values)

    @classmethod
    def from_dict(cls, value: object) -> "Handoff":
        if not isinstance(value, dict):
            raise BudgetStateError("handoff_invalid")
        if value:
            raise BudgetStateError("handoff_invalid")
        return cls()


@dataclass(frozen=True)
class LedgerState:
    repository: str
    pr: int
    reviewer: Reviewer
    budgets: BudgetPolicy
    invocations: tuple[Invocation, ...] = ()
    consumed_override_event_ids: tuple[int, ...] = ()
    last_decision: DecisionRecord = field(default_factory=DecisionRecord)
    handoff: Handoff = field(default_factory=Handoff)

    @classmethod
    def initial(cls, repository: str, pr: int, reviewer: Reviewer, *, invocations: tuple[Invocation, ...] = (),
                consumed_override_event_ids: tuple[int, ...] = ()) -> "LedgerState":
        return cls(repository, pr, reviewer, BudgetPolicy.for_reviewer(reviewer), invocations, consumed_override_event_ids)

    def to_dict(self) -> dict[str, object]:
        return {
            "budgets": self.budgets.to_dict(), "consumed_override_event_ids": list(self.consumed_override_event_ids),
            "handoff": self.handoff.to_dict(), "invocations": [item.to_dict() for item in self.invocations],
            "last_decision": self.last_decision.to_dict(), "pr": self.pr, "repository": self.repository,
            "reviewer": self.reviewer, "schema": SCHEMA,
        }

    @classmethod
    def from_dict(cls, value: object) -> "LedgerState":
        keys = {"schema", "repository", "pr", "reviewer", "budgets", "invocations", "consumed_override_event_ids", "last_decision", "handoff"}
        value = _exact_keys(value, keys, "ledger")
        if _integer(value["schema"], "schema", positive=True) != SCHEMA:
            raise BudgetStateError("schema_invalid")
        reviewer = _string(value["reviewer"], "reviewer")
        if reviewer not in MARKERS:
            raise BudgetStateError("reviewer_invalid")
        invocations = value["invocations"]
        consumed = value["consumed_override_event_ids"]
        if not isinstance(invocations, list) or not isinstance(consumed, list):
            raise BudgetStateError("ledger_invalid")
        state = cls(
            repository=_string(value["repository"], "repository"), pr=_integer(value["pr"], "pr", positive=True),
            reviewer=reviewer, budgets=BudgetPolicy.from_dict(value["budgets"]),
            invocations=tuple(Invocation.from_dict(item) for item in invocations),
            consumed_override_event_ids=tuple(_integer(item, "consumed_override_event_id", positive=True) for item in consumed),
            last_decision=DecisionRecord.from_dict(value["last_decision"]), handoff=Handoff.from_dict(value["handoff"]),
        )
        _validate_state_shape(state)
        return state


@dataclass(frozen=True)
class ClaimRequest:
    repository: str
    pr: int
    reviewer: Reviewer
    run_id: int
    run_attempt: int
    head_sha: str
    full_diff_sha256: str
    estimated_input_tokens: int
    diff_mode: str
    authenticated_review: AuthenticatedReview
    override_events: tuple[OverrideEvent, ...]
    model_route: tuple[str, ...]
    effort: str
    call_unit: str


@dataclass(frozen=True)
class FinalizeRequest:
    repository: str
    pr: int
    reviewer: Reviewer
    run_id: int
    run_attempt: int
    head_sha: str
    full_diff_sha256: str
    model_route: tuple[str, ...]
    effort: str
    call_count: int
    elapsed_seconds: int
    outcome: Outcome
    stop_reason: str
    authenticated_review: AuthenticatedReview
    remaining_finding_ids: tuple[str, ...]


@dataclass(frozen=True)
class Transition:
    state: LedgerState
    allow_invocation: bool
    decision: str
    stop_reason: str
    round_number: int | None
    invocation_key: str | None
    mutate_comment: bool


def _validate_state_shape(state: LedgerState) -> None:
    if state.reviewer not in MARKERS or not isinstance(state.repository, str) or not state.repository:
        raise BudgetStateError("identity_invalid")
    _integer(state.pr, "pr", positive=True)
    BudgetPolicy.from_dict(state.budgets.to_dict())
    if state.budgets != BudgetPolicy.for_reviewer(state.reviewer):
        raise BudgetStateError("budgets_invalid")
    if len(state.invocations) > 3 or len(state.consumed_override_event_ids) != len(set(state.consumed_override_event_ids)):
        raise BudgetStateError("ledger_invalid")
    if any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in state.consumed_override_event_ids):
        raise BudgetStateError("consumed_override_event_ids_invalid")
    run_keys = {(item.run_id, item.run_attempt) for item in state.invocations}
    if len(run_keys) != len(state.invocations):
        raise BudgetStateError("duplicate_run_identity")
    if len({item.head_sha for item in state.invocations}) != len(state.invocations):
        raise BudgetStateError("duplicate_head")
    if len({item.full_diff_sha256 for item in state.invocations}) != len(state.invocations):
        raise BudgetStateError("duplicate_effective_diff")
    automatic = [item for item in state.invocations if item.override_event_id is None]
    overrides = [item for item in state.invocations if item.override_event_id is not None]
    if len(automatic) > state.budgets.max_rounds or len(overrides) > state.budgets.max_override_rounds:
        raise BudgetStateError("rounds_invalid")
    if [item.round_number for item in automatic] != list(range(1, len(automatic) + 1)):
        raise BudgetStateError("rounds_invalid")
    if overrides:
        item = overrides[0]
        if (len(automatic) != state.budgets.max_rounds or state.invocations[-1] != item or
                item.round_number != state.budgets.max_rounds + 1 or
                item.override_event_id not in state.consumed_override_event_ids):
            raise BudgetStateError("override_invalid")
    if len(state.consumed_override_event_ids) != len(overrides):
        raise BudgetStateError("override_invalid")
    for item in state.invocations:
        Invocation.from_dict(item.to_dict())
    DecisionRecord.from_dict(state.last_decision.to_dict())
    Handoff.from_dict(state.handoff.to_dict())


def serialize_ledger(state: LedgerState) -> str:
    _validate_state_shape(state)
    return json.dumps(state.to_dict(), ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def parse_ledger(body: str | None, *, repository: str, pr: int, reviewer: Reviewer) -> LedgerState | None:
    if body is None:
        return None
    if reviewer not in MARKERS or not body.startswith(f"{MARKERS[reviewer]}\n{STATE_PREFIX}"):
        raise BudgetStateError("ledger_marker_invalid")
    start = len(MARKERS[reviewer]) + 1 + len(STATE_PREFIX)
    end = body.find(STATE_SUFFIX, start)
    if end < 0 or "\n" in body[start:end]:
        raise BudgetStateError("ledger_marker_invalid")
    payload = body[start:end]
    try:
        state = LedgerState.from_dict(json.loads(payload))
    except (json.JSONDecodeError, TypeError) as exc:
        raise BudgetStateError("ledger_json_invalid") from exc
    if payload != serialize_ledger(state):
        raise BudgetStateError("ledger_json_noncanonical")
    if state.repository != repository or state.pr != pr or state.reviewer != reviewer:
        raise BudgetStateError("ledger_identity_mismatch")
    return state


def estimate_input_tokens(paths: Sequence[Path]) -> int:
    total = 0
    for path in paths:
        stat_result = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(stat_result.st_mode):
            raise BudgetStateError("input_not_regular")
        total += stat_result.st_size
    return math.ceil(total / 4) + 20_000


def _validate_request(request: ClaimRequest) -> None:
    if request.reviewer not in MARKERS or not isinstance(request.repository, str) or not request.repository:
        raise BudgetStateError("identity_invalid")
    _integer(request.pr, "pr", positive=True)
    _integer(request.run_id, "run_id", positive=True)
    _integer(request.run_attempt, "run_attempt", positive=True)
    _integer(request.estimated_input_tokens, "estimated_input_tokens", minimum=0)
    _hash(request.head_sha, _HEAD, "head_sha")
    _hash(request.full_diff_sha256, _HASH, "full_diff_sha256")
    if request.diff_mode not in {"changed", "unchanged"}:
        raise BudgetStateError("diff_mode_invalid")
    if not isinstance(request.authenticated_review, AuthenticatedReview):
        raise BudgetStateError("authenticated_review_invalid")
    review = request.authenticated_review
    if not isinstance(review.success, bool):
        raise BudgetStateError("authenticated_review_invalid")
    if review.success:
        if review.head_sha is None or review.full_diff_sha256 is None:
            raise BudgetStateError("authenticated_review_invalid")
        _hash(review.head_sha, _HEAD, "authenticated_head_sha")
        _hash(review.full_diff_sha256, _HASH, "authenticated_full_diff_sha256")
    elif review.head_sha is not None or review.full_diff_sha256 is not None:
        raise BudgetStateError("authenticated_review_invalid")
    if (len(review.remaining_finding_ids) > 8 or len(review.remaining_finding_ids) != len(set(review.remaining_finding_ids)) or
            not all(isinstance(item, str) and _FINDING.fullmatch(item) for item in review.remaining_finding_ids)):
        raise BudgetStateError("authenticated_review_invalid")
    if not isinstance(request.override_events, tuple):
        raise BudgetStateError("override_events_invalid")
    for event in request.override_events:
        if (not isinstance(event, OverrideEvent) or isinstance(event.event_id, bool) or not isinstance(event.event_id, int) or event.event_id <= 0 or
                not isinstance(event.event, str) or (event.label is not None and not isinstance(event.label, str)) or
                (event.actor_permission is not None and not isinstance(event.actor_permission, str))):
            raise BudgetStateError("override_events_invalid")
    if not isinstance(request.model_route, tuple) or not request.model_route or not all(isinstance(item, str) and item for item in request.model_route):
        raise BudgetStateError("model_route_invalid")
    _string(request.effort, "effort")
    _string(request.call_unit, "call_unit")


def _validate_provenance(state: LedgerState, request: ClaimRequest, provenances: Mapping[tuple[int, int], RunProvenance]) -> None:
    for item in state.invocations:
        provenance = provenances.get((item.run_id, item.run_attempt))
        if not isinstance(provenance, RunProvenance):
            raise BudgetStateError("provenance_mismatch")
        current = (item.run_id, item.run_attempt) == (request.run_id, request.run_attempt)
        if (provenance.repository != state.repository or provenance.pr != state.pr or
                provenance.head_sha != item.head_sha or provenance.workflow_path != WORKFLOWS[state.reviewer] or
                provenance.run_id != item.run_id or provenance.run_attempt != item.run_attempt or
                (not current and provenance.status != "completed") or
                (current and provenance.status not in {"in_progress", "completed"})):
            raise BudgetStateError("provenance_mismatch")


def validate_or_initialize(state: LedgerState | None, request: ClaimRequest,
                           provenances: Mapping[tuple[int, int], RunProvenance]) -> LedgerState:
    _validate_request(request)
    validated = LedgerState.initial(request.repository, request.pr, request.reviewer) if state is None else state
    _validate_state_shape(validated)
    if (validated.repository, validated.pr, validated.reviewer) != (request.repository, request.pr, request.reviewer):
        raise BudgetStateError("identity_mismatch")
    _validate_provenance(validated, request, provenances)
    return validated


def automatic_rounds(state: LedgerState) -> int:
    return sum(item.override_event_id is None for item in state.invocations)


def estimated_total(state: LedgerState) -> int:
    return sum(item.estimated_input_tokens for item in state.invocations)


def choose_override(state: LedgerState, events: Sequence[OverrideEvent]) -> OverrideEvent | None:
    if any(item.override_event_id is not None for item in state.invocations):
        return None
    eligible: list[OverrideEvent] = []
    for event in events:
        if (isinstance(event, OverrideEvent) and isinstance(event.event_id, int) and not isinstance(event.event_id, bool) and
                event.event_id > 0 and event.event == "labeled" and event.label == "review-budget-override" and
                event.actor_permission in {"admin", "maintain", "write"} and event.event_id not in state.consumed_override_event_ids):
            eligible.append(event)
    return max(eligible, key=lambda item: item.event_id, default=None)


def _decision_state(state: LedgerState, request: ClaimRequest, decision: str, stop_reason: str) -> LedgerState:
    return replace(state, last_decision=DecisionRecord(decision, stop_reason, request.run_id, request.run_attempt))


def refuse(state: LedgerState, request: ClaimRequest, decision: str) -> Transition:
    updated = _decision_state(state, request, decision, decision)
    return Transition(updated, False, decision, decision, None, None, True)


def append_claim(state: LedgerState, request: ClaimRequest, override: OverrideEvent | None) -> Transition:
    round_number = len(state.invocations) + 1
    item = Invocation(
        run_id=request.run_id, run_attempt=request.run_attempt, head_sha=request.head_sha,
        full_diff_sha256=request.full_diff_sha256, round_number=round_number,
        override_event_id=None if override is None else override.event_id, model_route=request.model_route,
        effort=request.effort, call_unit=request.call_unit, call_count=0,
        estimated_input_tokens=request.estimated_input_tokens, elapsed_seconds=0, status="claimed",
        outcome=None, stop_reason="claimed", remaining_finding_ids=(),
    )
    consumed = state.consumed_override_event_ids if override is None else state.consumed_override_event_ids + (override.event_id,)
    updated = replace(state, invocations=state.invocations + (item,), consumed_override_event_ids=consumed)
    updated = _decision_state(updated, request, "claimed", "claimed")
    return Transition(updated, True, "claimed", "claimed", round_number, f"{request.run_id}:{request.run_attempt}", True)


def _invalid_transition(state: LedgerState | None, request: ClaimRequest, reason: str) -> Transition:
    if state is None:
        try:
            state = LedgerState.initial(request.repository, request.pr, request.reviewer)
        except (AttributeError, BudgetStateError):
            state = LedgerState.initial("invalid/invalid", 1, "claude")
    return Transition(state, False, "state_invalid", reason, None, None, False)


def claim(state: LedgerState | None, request: ClaimRequest,
          provenances: Mapping[tuple[int, int], RunProvenance]) -> Transition:
    try:
        validated = validate_or_initialize(state, request, provenances)
    except BudgetStateError as exc:
        return _invalid_transition(state, request, str(exc))
    same_run = [item for item in validated.invocations if (item.run_id, item.run_attempt) == (request.run_id, request.run_attempt)]
    if same_run and any((item.head_sha, item.full_diff_sha256) != (request.head_sha, request.full_diff_sha256) for item in same_run):
        return _invalid_transition(validated, request, "duplicate_run_identity")
    if request.diff_mode == "unchanged":
        if not request.authenticated_review.covers_hash(request.full_diff_sha256):
            return _invalid_transition(validated, request, "unchanged_without_authenticated_review")
        return refuse(validated, request, "authenticated_reuse")
    if any(item.head_sha == request.head_sha for item in validated.invocations):
        return refuse(validated, request, "duplicate_head")
    if any(item.full_diff_sha256 == request.full_diff_sha256 for item in validated.invocations):
        return refuse(validated, request, "duplicate_effective_diff")
    if request.estimated_input_tokens > validated.budgets.max_estimated_tokens_per_round:
        return refuse(validated, request, "input_budget_exhausted")
    override = None
    if automatic_rounds(validated) >= validated.budgets.max_rounds:
        override = choose_override(validated, request.override_events)
        if override is None:
            return refuse(validated, request, "round_budget_exhausted")
    total_limit = 600_000 if override is not None else 400_000
    if estimated_total(validated) + request.estimated_input_tokens > total_limit:
        return refuse(validated, request, "total_usage_budget_exhausted")
    return append_claim(validated, request, override)
