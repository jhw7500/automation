"""Pure, fail-closed state transitions for review invocation budgets."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal, Mapping, Sequence
from urllib.parse import quote


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
    repository: str | None = None
    pr: int | None = None
    reviewer: Reviewer | None = None
    current_head_sha: str | None = None
    current_full_diff_sha256: str | None = None
    current_run_id: int | None = None
    current_run_attempt: int | None = None
    automatic_rounds: int | None = None
    override_rounds: int | None = None
    round_usage: tuple[tuple[int, int, int, int], ...] = ()
    decision: str | None = None
    outcome: Outcome | None = None
    stop_reason: str | None = None
    authenticated_review_head_sha: str | None = None
    authenticated_review_full_diff_sha256: str | None = None
    remaining_finding_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        if self.repository is None:
            return {}
        return {
            "authenticated_review_full_diff_sha256": self.authenticated_review_full_diff_sha256,
            "authenticated_review_head_sha": self.authenticated_review_head_sha,
            "automatic_rounds": self.automatic_rounds,
            "current_full_diff_sha256": self.current_full_diff_sha256,
            "current_head_sha": self.current_head_sha,
            "current_run_attempt": self.current_run_attempt,
            "current_run_id": self.current_run_id,
            "decision": self.decision,
            "outcome": self.outcome,
            "override_rounds": self.override_rounds,
            "pr": self.pr,
            "remaining_finding_ids": list(self.remaining_finding_ids),
            "repository": self.repository,
            "reviewer": self.reviewer,
            "round_usage": [
                {
                    "call_count": call_count,
                    "elapsed_seconds": elapsed_seconds,
                    "estimated_input_tokens": estimated_input_tokens,
                    "round_number": round_number,
                }
                for round_number, call_count, estimated_input_tokens, elapsed_seconds in self.round_usage
            ],
            "stop_reason": self.stop_reason,
        }

    @classmethod
    def from_dict(cls, value: object) -> "Handoff":
        if not isinstance(value, dict):
            raise BudgetStateError("handoff_invalid")
        if not value:
            return cls()
        keys = {
            "authenticated_review_full_diff_sha256", "authenticated_review_head_sha",
            "automatic_rounds", "current_full_diff_sha256", "current_head_sha",
            "current_run_attempt", "current_run_id", "decision", "outcome",
            "override_rounds", "pr", "remaining_finding_ids", "repository", "reviewer",
            "round_usage", "stop_reason",
        }
        value = _exact_keys(value, keys, "handoff")
        reviewer = _string(value["reviewer"], "reviewer")
        if reviewer not in MARKERS:
            raise BudgetStateError("handoff_invalid")
        outcome = value["outcome"]
        if outcome is not None and (not isinstance(outcome, str) or outcome not in _OUTCOMES):
            raise BudgetStateError("handoff_invalid")
        authenticated_head = value["authenticated_review_head_sha"]
        authenticated_hash = value["authenticated_review_full_diff_sha256"]
        if (authenticated_head is None) != (authenticated_hash is None):
            raise BudgetStateError("handoff_invalid")
        if authenticated_head is not None:
            authenticated_head = _hash(authenticated_head, _HEAD, "authenticated_review_head_sha")
            authenticated_hash = _hash(authenticated_hash, _HASH, "authenticated_review_full_diff_sha256")
        findings = value["remaining_finding_ids"]
        if (not isinstance(findings, list) or len(findings) > 8 or len(findings) != len(set(findings)) or
                not all(isinstance(item, str) and _FINDING.fullmatch(item) for item in findings)):
            raise BudgetStateError("handoff_invalid")
        usage = value["round_usage"]
        if not isinstance(usage, list) or len(usage) > 3:
            raise BudgetStateError("handoff_invalid")
        parsed_usage: list[tuple[int, int, int, int]] = []
        for item in usage:
            item = _exact_keys(
                item,
                {"round_number", "call_count", "estimated_input_tokens", "elapsed_seconds"},
                "handoff_round_usage",
            )
            parsed_usage.append((
                _integer(item["round_number"], "round_number", positive=True),
                _integer(item["call_count"], "call_count", minimum=0),
                _integer(item["estimated_input_tokens"], "estimated_input_tokens", minimum=0),
                _integer(item["elapsed_seconds"], "elapsed_seconds", minimum=0),
            ))
        decision = _string(value["decision"], "decision")
        if decision not in {
            "claimed", "finalized", "state_invalid", "diff_unavailable", "authenticated_reuse",
            "duplicate_head", "duplicate_effective_diff", "input_budget_exhausted",
            "round_budget_exhausted", "total_usage_budget_exhausted",
        }:
            raise BudgetStateError("handoff_invalid")
        return cls(
            repository=_string(value["repository"], "repository"),
            pr=_integer(value["pr"], "pr", positive=True), reviewer=reviewer,
            current_head_sha=_hash(value["current_head_sha"], _HEAD, "current_head_sha"),
            current_full_diff_sha256=_hash(
                value["current_full_diff_sha256"], _HASH, "current_full_diff_sha256",
            ),
            current_run_id=_integer(value["current_run_id"], "current_run_id", positive=True),
            current_run_attempt=_integer(
                value["current_run_attempt"], "current_run_attempt", positive=True,
            ),
            automatic_rounds=_integer(value["automatic_rounds"], "automatic_rounds", minimum=0),
            override_rounds=_integer(value["override_rounds"], "override_rounds", minimum=0),
            round_usage=tuple(parsed_usage), decision=decision, outcome=outcome,
            stop_reason=_string(value["stop_reason"], "stop_reason"),
            authenticated_review_head_sha=authenticated_head,
            authenticated_review_full_diff_sha256=authenticated_hash,
            remaining_finding_ids=tuple(findings),
        )


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
    for item in state.invocations:
        Invocation.from_dict(item.to_dict())
        call_failure = (
            item.status == "finalized" and item.outcome == "checkpoint_failure" and
            item.stop_reason == "call_budget_exhausted"
        )
        wall_failure = (
            item.status == "finalized" and item.outcome == "wall_time_exhausted" and
            item.stop_reason == "wall_time_exhausted"
        )
        if item.call_count > state.budgets.max_calls_per_round and not call_failure:
            raise BudgetStateError("call_budget_exhausted")
        if item.estimated_input_tokens > state.budgets.max_estimated_tokens_per_round:
            raise BudgetStateError("input_budget_exhausted")
        call_first_dual_failure = call_failure and item.call_count > state.budgets.max_calls_per_round
        if (item.elapsed_seconds > state.budgets.max_wall_seconds_per_round and
                not (wall_failure or call_first_dual_failure)):
            raise BudgetStateError("wall_time_exhausted")
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
    automatic_total = sum(item.estimated_input_tokens for item in automatic)
    total_limit = state.budgets.max_estimated_tokens_total
    if automatic_total > total_limit:
        raise BudgetStateError("total_usage_budget_exhausted")
    if overrides:
        total_limit += state.budgets.max_estimated_tokens_per_round
    if sum(item.estimated_input_tokens for item in state.invocations) > total_limit:
        raise BudgetStateError("total_usage_budget_exhausted")
    DecisionRecord.from_dict(state.last_decision.to_dict())
    handoff = Handoff.from_dict(state.handoff.to_dict())
    if handoff.repository is not None:
        expected_usage = tuple(
            (item.round_number, item.call_count, item.estimated_input_tokens, item.elapsed_seconds)
            for item in state.invocations
        )
        matching_inputs = [
            item for item in state.invocations
            if (item.head_sha == handoff.current_head_sha or
                item.full_diff_sha256 == handoff.current_full_diff_sha256)
        ]
        exact_inputs = [
            item for item in state.invocations
            if (item.run_id, item.run_attempt, item.head_sha, item.full_diff_sha256) == (
                handoff.current_run_id, handoff.current_run_attempt,
                handoff.current_head_sha, handoff.current_full_diff_sha256,
            )
        ]
        if handoff.decision in {"claimed", "finalized"} and len(exact_inputs) != 1:
            raise BudgetStateError("handoff_mismatch")
        outcome_inputs = exact_inputs if exact_inputs else matching_inputs
        expected_outcome = outcome_inputs[-1].outcome if outcome_inputs else None
        if (
            (handoff.repository, handoff.pr, handoff.reviewer) !=
            (state.repository, state.pr, state.reviewer) or
            handoff.automatic_rounds != len(automatic) or
            handoff.override_rounds != len(overrides) or
            handoff.round_usage != expected_usage or
            handoff.decision != state.last_decision.decision or
            handoff.stop_reason != state.last_decision.stop_reason or
            handoff.current_run_id != state.last_decision.run_id or
            handoff.current_run_attempt != state.last_decision.run_attempt or
            handoff.outcome != expected_outcome
        ):
            raise BudgetStateError("handoff_mismatch")


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


def _validate_provenance(
        state: LedgerState, request: ClaimRequest | FinalizeRequest,
        provenances: Mapping[tuple[int, int], RunProvenance]) -> None:
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


def _handoff_outcome(state: LedgerState, request: ClaimRequest | FinalizeRequest) -> Outcome | None:
    for item in reversed(state.invocations):
        if item.head_sha == request.head_sha or item.full_diff_sha256 == request.full_diff_sha256:
            return item.outcome
    return None


def _build_handoff(
        state: LedgerState, request: ClaimRequest | FinalizeRequest, *, outcome: Outcome | None = None,
        remaining_finding_ids: tuple[str, ...] | None = None) -> Handoff:
    prior = state.handoff
    review = request.authenticated_review
    if outcome in {"success", "quality_filtered"}:
        authenticated_head = request.head_sha
        authenticated_hash = request.full_diff_sha256
    elif review.success:
        authenticated_head = review.head_sha
        authenticated_hash = review.full_diff_sha256
    else:
        authenticated_head = prior.authenticated_review_head_sha
        authenticated_hash = prior.authenticated_review_full_diff_sha256
    if remaining_finding_ids is None:
        if review.success:
            remaining_finding_ids = review.remaining_finding_ids
        else:
            remaining_finding_ids = prior.remaining_finding_ids
    return Handoff(
        repository=state.repository, pr=state.pr, reviewer=state.reviewer,
        current_head_sha=request.head_sha, current_full_diff_sha256=request.full_diff_sha256,
        current_run_id=request.run_id, current_run_attempt=request.run_attempt,
        automatic_rounds=automatic_rounds(state),
        override_rounds=sum(item.override_event_id is not None for item in state.invocations),
        round_usage=tuple(
            (item.round_number, item.call_count, item.estimated_input_tokens, item.elapsed_seconds)
            for item in state.invocations
        ),
        decision=state.last_decision.decision, outcome=outcome, stop_reason=state.last_decision.stop_reason,
        authenticated_review_head_sha=authenticated_head,
        authenticated_review_full_diff_sha256=authenticated_hash,
        remaining_finding_ids=remaining_finding_ids,
    )


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
    updated = replace(state, last_decision=DecisionRecord(decision, stop_reason, request.run_id, request.run_attempt))
    return replace(updated, handoff=_build_handoff(updated, request, outcome=_handoff_outcome(updated, request)))


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


def _invalid_transition(
        state: LedgerState | None, request: ClaimRequest | FinalizeRequest, reason: str) -> Transition:
    if state is None:
        try:
            state = LedgerState.initial(request.repository, request.pr, request.reviewer)
        except (AttributeError, BudgetStateError):
            state = LedgerState.initial("invalid/invalid", 1, "claude")
    return Transition(state, False, "state_invalid", reason, None, None, False)


def _finalization_refusal(
        state: LedgerState, request: FinalizeRequest, reason: str) -> Transition:
    updated = replace(
        state,
        last_decision=DecisionRecord("state_invalid", reason, request.run_id, request.run_attempt),
    )
    updated = replace(
        updated,
        handoff=_build_handoff(updated, request, outcome=_handoff_outcome(updated, request)),
    )
    _validate_state_shape(updated)
    return Transition(updated, False, "state_invalid", reason, None, None, False)


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


def _validate_finalize_request(request: FinalizeRequest) -> None:
    if request.reviewer not in MARKERS or not isinstance(request.repository, str) or not request.repository:
        raise BudgetStateError("identity_invalid")
    _integer(request.pr, "pr", positive=True)
    _integer(request.run_id, "run_id", positive=True)
    _integer(request.run_attempt, "run_attempt", positive=True)
    _hash(request.head_sha, _HEAD, "head_sha")
    _hash(request.full_diff_sha256, _HASH, "full_diff_sha256")
    _integer(request.call_count, "call_count", minimum=0)
    _integer(request.elapsed_seconds, "elapsed_seconds", minimum=0)
    if (not isinstance(request.model_route, tuple) or not request.model_route or
            not all(isinstance(item, str) and item for item in request.model_route)):
        raise BudgetStateError("model_route_invalid")
    _string(request.effort, "effort")
    if not isinstance(request.outcome, str) or request.outcome not in _OUTCOMES:
        raise BudgetStateError("outcome_invalid")
    if not isinstance(request.stop_reason, str) or not request.stop_reason:
        raise BudgetStateError("stop_reason_invalid")
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
    if (len(review.remaining_finding_ids) > 8 or
            len(review.remaining_finding_ids) != len(set(review.remaining_finding_ids)) or
            not all(isinstance(item, str) and _FINDING.fullmatch(item) for item in review.remaining_finding_ids)):
        raise BudgetStateError("authenticated_review_invalid")
    findings = request.remaining_finding_ids
    if (not isinstance(findings, tuple) or len(findings) > 8 or len(findings) != len(set(findings)) or
            not all(isinstance(item, str) and _FINDING.fullmatch(item) for item in findings)):
        raise BudgetStateError("remaining_finding_ids_invalid")


def _finalize_remaining_ids(state: LedgerState, request: FinalizeRequest) -> tuple[str, ...]:
    if request.outcome in {"success", "quality_filtered"}:
        return request.remaining_finding_ids
    if request.remaining_finding_ids:
        return request.remaining_finding_ids
    if request.authenticated_review.success:
        return tuple(request.authenticated_review.remaining_finding_ids)
    if request.outcome == "provider_failure":
        if state.handoff.repository is not None:
            return state.handoff.remaining_finding_ids
        for item in reversed(state.invocations):
            if item.status == "finalized" and item.remaining_finding_ids:
                return item.remaining_finding_ids
    return ()


def finalize(
        state: LedgerState, request: FinalizeRequest,
        provenances: Mapping[tuple[int, int], RunProvenance]) -> Transition:
    try:
        _validate_finalize_request(request)
        _validate_state_shape(state)
    except BudgetStateError as exc:
        return _invalid_transition(state, request, str(exc))
    try:
        if (state.repository, state.pr, state.reviewer) != (
                request.repository, request.pr, request.reviewer):
            raise BudgetStateError("identity_mismatch")
        _validate_provenance(state, request, provenances)
    except BudgetStateError as exc:
        return _finalization_refusal(state, request, str(exc))

    exact = [
        (index, item) for index, item in enumerate(state.invocations)
        if (item.run_id, item.run_attempt, item.head_sha, item.full_diff_sha256) == (
            request.run_id, request.run_attempt, request.head_sha, request.full_diff_sha256,
        )
    ]
    if len(exact) != 1:
        same_run = any(
            (item.run_id, item.run_attempt) == (request.run_id, request.run_attempt)
            for item in state.invocations
        )
        return _finalization_refusal(
            state, request, "finalization_identity_mismatch" if same_run else "invocation_not_claimed",
        )
    index, entry = exact[0]
    if entry.status != "claimed":
        return _finalization_refusal(state, request, "invocation_not_claimed")
    if request.model_route[0] != entry.model_route[0]:
        return _finalization_refusal(state, request, "model_route_unknown")
    outcome = request.outcome
    stop_reason = request.stop_reason
    if request.call_count > state.budgets.max_calls_per_round:
        outcome, stop_reason = "checkpoint_failure", "call_budget_exhausted"
    elif request.elapsed_seconds > state.budgets.max_wall_seconds_per_round:
        outcome, stop_reason = "wall_time_exhausted", "wall_time_exhausted"
    remaining = _finalize_remaining_ids(state, request)
    completed = replace(
        entry, status="finalized", outcome=outcome, stop_reason=stop_reason,
        call_count=request.call_count, elapsed_seconds=request.elapsed_seconds,
        model_route=request.model_route, effort=request.effort, remaining_finding_ids=remaining,
    )
    invocations = state.invocations[:index] + (completed,) + state.invocations[index + 1:]
    updated = replace(
        state, invocations=invocations,
        last_decision=DecisionRecord("finalized", stop_reason, request.run_id, request.run_attempt),
    )
    updated = replace(
        updated,
        handoff=_build_handoff(updated, request, outcome=outcome, remaining_finding_ids=remaining),
    )
    try:
        _validate_state_shape(updated)
    except BudgetStateError as exc:
        return _finalization_refusal(state, request, str(exc))
    return Transition(
        updated, False, "finalized", stop_reason, completed.round_number,
        f"{request.run_id}:{request.run_attempt}", True,
    )


_REVIEWER_TITLES = {"claude": "Claude", "gemini": "Gemini", "opencode": "OpenCode"}


def _summary(state: LedgerState, *, server_url: str) -> str:
    _validate_state_shape(state)
    handoff = state.handoff
    if handoff.repository is None:
        raise BudgetStateError("handoff_missing")
    if not isinstance(server_url, str) or not server_url or "\n" in server_url or "\r" in server_url:
        raise BudgetStateError("server_url_invalid")
    run_url = f"{server_url.rstrip('/')}/{state.repository}/actions/runs/{handoff.current_run_id}"
    return (
        f"## {_REVIEWER_TITLES[state.reviewer]} review invocation budget\n"
        f"- Decision: {handoff.decision}\n"
        f"- Automatic rounds: {handoff.automatic_rounds}/{state.budgets.max_rounds}\n"
        f"- Override rounds: {handoff.override_rounds}/{state.budgets.max_override_rounds}\n"
        f"- Current run: {run_url}\n"
        f"- Stop reason: {handoff.stop_reason}\n\n"
        "Budget exhaustion is not review approval. Use the authenticated review checkpoint and "
        "remaining finding IDs before merge."
    )


def render_summary(state: LedgerState) -> str:
    return _summary(state, server_url="https://github.com")


def render_comment(state: LedgerState, *, server_url: str) -> str:
    state_lines = (
        f"{MARKERS[state.reviewer]}\n"
        f"{STATE_PREFIX}{serialize_ledger(state)}{STATE_SUFFIX}"
    )
    return f"{state_lines}\n\n{_summary(state, server_url=server_url)}"


def render_checkpoint(state: LedgerState) -> bytes:
    _validate_state_shape(state)
    payload = {"schema": SCHEMA, "ledger": state.to_dict(), "handoff": state.handoff.to_dict()}
    return (
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii") + b"\n"
    )


def load_checkpoint(payload: bytes) -> LedgerState:
    if not isinstance(payload, bytes):
        raise BudgetStateError("checkpoint_invalid")
    try:
        text = payload.decode("ascii")
        raw = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise BudgetStateError("checkpoint_json_invalid") from exc
    raw = _exact_keys(raw, {"schema", "ledger", "handoff"}, "checkpoint")
    if _integer(raw["schema"], "schema", positive=True) != SCHEMA:
        raise BudgetStateError("checkpoint_schema_invalid")
    state = LedgerState.from_dict(raw["ledger"])
    handoff = Handoff.from_dict(raw["handoff"])
    if handoff != state.handoff:
        raise BudgetStateError("checkpoint_handoff_mismatch")
    if payload != render_checkpoint(state):
        raise BudgetStateError("checkpoint_json_noncanonical")
    return state


_BOT_LOGIN = "github-actions[bot]"
_CALL_UNITS = {
    "claude": "claude-code-action review session",
    "gemini": "generate_content request",
    "opencode": "opencode run session",
}
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_DECIMAL = re.compile(r"[1-9][0-9]*\Z")


class TransportError(BudgetStateError):
    """A GitHub API payload or file transport failed validation."""


def write_private(path: Path, payload: bytes) -> None:
    """Atomically replace one explicitly named output with mode 0600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.new")
    temporary.write_bytes(payload)
    temporary.chmod(0o600)
    temporary.replace(path)


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, separators=(",", ":"), sort_keys=True,
    ).encode("ascii") + b"\n"


def _read_json(path: Path, name: str) -> object:
    try:
        file_stat = path.lstat()
        if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
            raise TransportError(f"{name}_not_regular")
        return json.loads(path.read_bytes())
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TransportError(f"{name}_invalid") from exc


def _read_pages(path: Path, name: str) -> list[object]:
    """Read one JSON array or gh's concatenated --paginate arrays."""
    try:
        file_stat = path.lstat()
        if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
            raise TransportError(f"{name}_not_regular")
        text = path.read_text(encoding="utf-8")
        decoder = json.JSONDecoder()
        cursor = 0
        values: list[object] = []
        while cursor < len(text):
            while cursor < len(text) and text[cursor].isspace():
                cursor += 1
            if cursor == len(text):
                break
            value, cursor = decoder.raw_decode(text, cursor)
            if not isinstance(value, list):
                raise TransportError(f"{name}_invalid")
            values.extend(value)
        return values
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TransportError(f"{name}_invalid") from exc


def _decimal(value: object, name: str) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        text = str(value)
    elif isinstance(value, str):
        text = value
    else:
        raise TransportError(f"{name}_invalid")
    if _DECIMAL.fullmatch(text) is None:
        raise TransportError(f"{name}_invalid")
    parsed = int(text)
    if parsed > 9_223_372_036_854_775_807:
        raise TransportError(f"{name}_invalid")
    return parsed


def _transport_request(path: Path) -> dict[str, object]:
    value = _read_json(path, "request")
    keys = {
        "operation", "repository", "pr", "reviewer", "run_id", "run_attempt",
        "head_sha", "full_diff_sha256", "diff_mode", "input_files_json",
        "authenticated_review_json", "model_route_json", "effort", "actual_call_count",
        "elapsed_seconds", "outcome", "stop_reason", "remaining_finding_ids_json",
        "checkpoint_file", "github_workspace", "server_url",
    }
    value = dict(_exact_keys(value, keys, "request"))
    repository = _string(value["repository"], "repository")
    if _REPOSITORY.fullmatch(repository) is None or os.environ.get("GITHUB_REPOSITORY") != repository:
        raise TransportError("repository_identity_mismatch")
    operation = _string(value["operation"], "operation")
    if operation not in {"claim", "finalize"}:
        raise TransportError("operation_invalid")
    reviewer = _string(value["reviewer"], "reviewer")
    if reviewer not in MARKERS:
        raise TransportError("reviewer_invalid")
    value["repository"] = repository
    value["operation"] = operation
    value["reviewer"] = reviewer
    value["pr"] = _decimal(value["pr"], "pr")
    value["run_id"] = _decimal(value["run_id"], "run_id")
    value["run_attempt"] = _decimal(value["run_attempt"], "run_attempt")
    value["head_sha"] = _hash(value["head_sha"], _HEAD, "head_sha")
    value["full_diff_sha256"] = _hash(
        value["full_diff_sha256"], _HASH, "full_diff_sha256",
    )
    if value["diff_mode"] not in {"changed", "unchanged"}:
        raise TransportError("diff_mode_invalid")
    for name in (
        "input_files_json", "authenticated_review_json", "model_route_json", "effort",
        "outcome", "stop_reason", "remaining_finding_ids_json", "checkpoint_file",
        "github_workspace", "server_url",
    ):
        value[name] = _string(value[name], name)
    return value


def _embedded_json(request: Mapping[str, object], key: str) -> object:
    try:
        return json.loads(_string(request[key], key))
    except json.JSONDecodeError as exc:
        raise TransportError(f"{key}_invalid") from exc


def _authenticated_review(request: Mapping[str, object]) -> AuthenticatedReview:
    value = _exact_keys(
        _embedded_json(request, "authenticated_review_json"),
        {"success", "head_sha", "full_diff_sha256", "remaining_finding_ids"},
        "authenticated_review",
    )
    findings = value["remaining_finding_ids"]
    if not isinstance(findings, list):
        raise TransportError("authenticated_review_invalid")
    return AuthenticatedReview(
        success=value["success"],
        head_sha=value["head_sha"],
        full_diff_sha256=value["full_diff_sha256"],
        remaining_finding_ids=tuple(findings),
    )


def _model_route(request: Mapping[str, object]) -> tuple[str, ...]:
    value = _embedded_json(request, "model_route_json")
    if not isinstance(value, list):
        raise TransportError("model_route_invalid")
    return tuple(value)


def _remaining_findings(request: Mapping[str, object]) -> tuple[str, ...]:
    value = _embedded_json(request, "remaining_finding_ids_json")
    if not isinstance(value, list):
        raise TransportError("remaining_finding_ids_invalid")
    return tuple(value)


def _base_claim_request(
        request: Mapping[str, object], *, estimated_input_tokens: int = 0,
        override_events: tuple[OverrideEvent, ...] = ()) -> ClaimRequest:
    return ClaimRequest(
        repository=request["repository"], pr=request["pr"], reviewer=request["reviewer"],
        run_id=request["run_id"], run_attempt=request["run_attempt"],
        head_sha=request["head_sha"], full_diff_sha256=request["full_diff_sha256"],
        estimated_input_tokens=estimated_input_tokens, diff_mode=request["diff_mode"],
        authenticated_review=_authenticated_review(request), override_events=override_events,
        model_route=_model_route(request), effort=request["effort"],
        call_unit=_CALL_UNITS[request["reviewer"]],
    )


def _finalize_request(request: Mapping[str, object]) -> FinalizeRequest:
    outcome = request["outcome"]
    stop_reason = request["stop_reason"] or outcome
    return FinalizeRequest(
        repository=request["repository"], pr=request["pr"], reviewer=request["reviewer"],
        run_id=request["run_id"], run_attempt=request["run_attempt"],
        head_sha=request["head_sha"], full_diff_sha256=request["full_diff_sha256"],
        model_route=_model_route(request), effort=request["effort"],
        call_count=_decimal_or_zero(request["actual_call_count"], "actual_call_count"),
        elapsed_seconds=_decimal_or_zero(request["elapsed_seconds"], "elapsed_seconds"),
        outcome=outcome, stop_reason=stop_reason,
        authenticated_review=_authenticated_review(request),
        remaining_finding_ids=_remaining_findings(request),
    )


def _decimal_or_zero(value: object, name: str) -> int:
    if value in {0, "0"}:
        return 0
    return _decimal(value, name)


def _verify_pr(request: Mapping[str, object], output_directory: Path) -> None:
    value = _read_json(output_directory / "pr.json", "pr")
    if not isinstance(value, dict):
        raise TransportError("pr_invalid")
    head = value.get("head")
    if (
        value.get("number") != request["pr"] or not isinstance(head, dict) or
        head.get("sha") != request["head_sha"]
    ):
        raise TransportError("pr_head_mismatch")


def _ledger_comment(
        comments_file: Path, request: Mapping[str, object],
    ) -> tuple[LedgerState | None, dict[str, object] | None]:
    comments = _read_pages(comments_file, "comments")
    marker = MARKERS[request["reviewer"]]
    matches = [
        item for item in comments
        if isinstance(item, dict) and isinstance(item.get("body"), str) and
        (item["body"] == marker or item["body"].startswith(f"{marker}\n"))
    ]
    if len(matches) > 1:
        raise TransportError("duplicate_ledger_comments")
    if not matches:
        return None, None
    comment = matches[0]
    user = comment.get("user")
    if not isinstance(user, dict) or user.get("login") != _BOT_LOGIN:
        raise TransportError("ledger_author_invalid")
    comment_id = comment.get("id")
    if isinstance(comment_id, bool) or not isinstance(comment_id, int) or comment_id <= 0:
        raise TransportError("comment_id_invalid")
    state = parse_ledger(
        comment["body"], repository=request["repository"], pr=request["pr"],
        reviewer=request["reviewer"],
    )
    return state, comment


def _timeline_permission_actors(output_directory: Path) -> list[dict[str, object]]:
    actors: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in _read_pages(output_directory / "timeline.json", "timeline"):
        if not isinstance(item, dict) or item.get("event") != "labeled":
            continue
        label = item.get("label")
        if not isinstance(label, dict) or label.get("name") != "review-budget-override":
            continue
        actor = item.get("actor")
        login = actor.get("login") if isinstance(actor, dict) else None
        if not isinstance(login, str) or not login:
            raise TransportError("override_actor_invalid")
        if login in seen:
            continue
        seen.add(login)
        actors.append({"index": len(actors), "login": login, "encoded_login": quote(login, safe="")})
    return actors


def _override_events(output_directory: Path) -> tuple[OverrideEvent, ...]:
    manifest = _read_json(output_directory / "run-identities.json", "run_identities")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("permission_actors"), list):
        raise TransportError("run_identities_invalid")
    permissions: dict[str, str] = {}
    for actor in manifest["permission_actors"]:
        actor = _exact_keys(actor, {"index", "login", "encoded_login"}, "permission_actor")
        index = actor["index"]
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise TransportError("permission_actor_invalid")
        payload = _read_json(output_directory / f"permission-{index}.json", "permission")
        if not isinstance(payload, dict) or not isinstance(payload.get("user"), dict):
            raise TransportError("permission_invalid")
        if payload["user"].get("login") != actor["login"] or payload.get("permission") not in {
                "admin", "maintain", "write", "triage", "read", "none"}:
            raise TransportError("permission_invalid")
        permissions[actor["login"]] = payload["permission"]
    events: list[OverrideEvent] = []
    for item in _read_pages(output_directory / "timeline.json", "timeline"):
        if not isinstance(item, dict) or item.get("event") != "labeled":
            continue
        label = item.get("label")
        if not isinstance(label, dict) or label.get("name") != "review-budget-override":
            continue
        actor = item.get("actor")
        login = actor.get("login") if isinstance(actor, dict) else None
        event_id = item.get("id")
        if isinstance(event_id, bool) or not isinstance(event_id, int) or event_id <= 0:
            raise TransportError("override_event_invalid")
        if not isinstance(login, str) or login not in permissions:
            raise TransportError("override_actor_invalid")
        events.append(OverrideEvent(event_id, "labeled", "review-budget-override", permissions[login]))
    return tuple(events)


def _run_provenances(
        state: LedgerState | None, request: Mapping[str, object], output_directory: Path,
    ) -> dict[tuple[int, int], RunProvenance]:
    if state is None:
        return {}
    result: dict[tuple[int, int], RunProvenance] = {}
    for invocation in state.invocations:
        path = output_directory / f"run-{invocation.run_id}-{invocation.run_attempt}.json"
        value = _read_json(path, "run")
        if not isinstance(value, dict):
            raise TransportError("provenance_mismatch")
        repository = value.get("repository")
        pulls = value.get("pull_requests")
        if not isinstance(repository, dict) or not isinstance(pulls, list):
            raise TransportError("provenance_mismatch")
        pull_numbers = {
            item.get("number") for item in pulls if isinstance(item, dict) and
            isinstance(item.get("number"), int) and not isinstance(item.get("number"), bool)
        }
        provenance = RunProvenance(
            repository=repository.get("full_name"), pr=request["pr"],
            head_sha=value.get("head_sha"), workflow_path=value.get("path"),
            run_id=value.get("id"), run_attempt=value.get("run_attempt"),
            status=value.get("status"), conclusion=value.get("conclusion"),
        )
        if request["pr"] not in pull_numbers:
            raise TransportError("provenance_mismatch")
        result[(invocation.run_id, invocation.run_attempt)] = provenance
    return result


def _validated_input_paths(request: Mapping[str, object]) -> tuple[Path, ...]:
    value = _embedded_json(request, "input_files_json")
    if not isinstance(value, list) or len(value) > 128 or not all(isinstance(item, str) for item in value):
        raise TransportError("input_files_invalid")
    workspace_value = os.environ.get("GITHUB_WORKSPACE")
    if not workspace_value or request["github_workspace"] != workspace_value:
        raise TransportError("workspace_identity_mismatch")
    workspace = Path(workspace_value)
    try:
        workspace = workspace.resolve(strict=True)
        if not workspace.is_dir():
            raise TransportError("workspace_invalid")
    except OSError as exc:
        raise TransportError("workspace_invalid") from exc
    paths: list[Path] = []
    for item in value:
        candidate = Path(item)
        try:
            file_stat = candidate.lstat()
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(workspace)
        except (FileNotFoundError, OSError, ValueError) as exc:
            raise TransportError("input_path_invalid") from exc
        if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
            raise TransportError("input_not_regular")
        paths.append(resolved)
    return tuple(paths)


def _recorded_refusal(
        state: LedgerState | None, request: ClaimRequest | FinalizeRequest, reason: str,
    ) -> Transition:
    if state is None:
        state = LedgerState.initial(request.repository, request.pr, request.reviewer)
    updated = replace(
        state, last_decision=DecisionRecord("state_invalid", reason, request.run_id, request.run_attempt),
    )
    updated = replace(
        updated, handoff=_build_handoff(updated, request, outcome=_handoff_outcome(updated, request)),
    )
    _validate_state_shape(updated)
    return Transition(updated, False, "state_invalid", reason, None, None, False)


def _write_transition(
        output_directory: Path, request: Mapping[str, object], transition: Transition,
        prior_state: LedgerState | None, prior_comment: Mapping[str, object] | None,
    ) -> None:
    checkpoint = render_checkpoint(transition.state)
    summary = render_summary(transition.state).encode("utf-8") + b"\n"
    comment = render_comment(transition.state, server_url=request["server_url"]).encode("utf-8")
    comment_id = "" if prior_comment is None else str(prior_comment["id"])
    mutation = "none"
    if transition.mutate_comment:
        mutation = "create" if prior_comment is None else "patch"
    output = {
        "allow-invocation": "true" if transition.allow_invocation else "false",
        "decision": transition.decision,
        "round": "" if transition.round_number is None else str(transition.round_number),
        "invocation-key": transition.invocation_key or "",
        "checkpoint-sha256": hashlib.sha256(checkpoint).hexdigest(),
        "comment-id": comment_id,
        "mutate-comment": transition.mutate_comment,
        "mutation": mutation,
        "expected-head-sha": request["head_sha"],
        "prior-comment-id": comment_id,
        "prior-comment-body": None if prior_comment is None else prior_comment["body"],
        "marker": MARKERS[request["reviewer"]],
    }
    write_private(output_directory / "state.json", _json_bytes(transition.state.to_dict()))
    write_private(
        output_directory / "prior-state.json",
        b"null\n" if prior_state is None else _json_bytes(prior_state.to_dict()),
    )
    write_private(output_directory / "comment-body.txt", comment)
    write_private(output_directory / "comment-payload.json", _json_bytes({"body": comment.decode("utf-8")}))
    write_private(output_directory / "checkpoint.json", checkpoint)
    write_private(output_directory / "summary.md", summary)
    write_private(output_directory / "output.json", _json_bytes(output))
    write_private(Path(request["checkpoint_file"]), checkpoint)


def _safe_request_for_refusal(request: Mapping[str, object]) -> ClaimRequest | FinalizeRequest:
    if request["operation"] == "claim":
        return _base_claim_request(request)
    return _finalize_request(request)


def _fallback_request(request: Mapping[str, object]) -> ClaimRequest | FinalizeRequest:
    review = AuthenticatedReview(False, None, None)
    if request["operation"] == "claim":
        return ClaimRequest(
            repository=request["repository"], pr=request["pr"], reviewer=request["reviewer"],
            run_id=request["run_id"], run_attempt=request["run_attempt"],
            head_sha=request["head_sha"], full_diff_sha256=request["full_diff_sha256"],
            estimated_input_tokens=0, diff_mode="changed", authenticated_review=review,
            override_events=(), model_route=("invalid",), effort=request["effort"],
            call_unit=_CALL_UNITS[request["reviewer"]],
        )
    return FinalizeRequest(
        repository=request["repository"], pr=request["pr"], reviewer=request["reviewer"],
        run_id=request["run_id"], run_attempt=request["run_attempt"],
        head_sha=request["head_sha"], full_diff_sha256=request["full_diff_sha256"],
        model_route=("invalid",), effort=request["effort"], call_count=0, elapsed_seconds=0,
        outcome="checkpoint_failure", stop_reason="checkpoint_failure",
        authenticated_review=review, remaining_finding_ids=(),
    )


def _list_run_identities(
        request: Mapping[str, object], comments_file: Path, output_directory: Path,
    ) -> None:
    error = None
    state = None
    try:
        state, _ = _ledger_comment(comments_file, request)
        actors = _timeline_permission_actors(output_directory)
    except BudgetStateError as exc:
        error = str(exc)
        actors = []
    runs = [] if state is None else [
        {"run_id": item.run_id, "run_attempt": item.run_attempt} for item in state.invocations
    ]
    if len(runs) > 3:
        error = "ledger_invalid"
        runs = []
    write_private(output_directory / "run-identities.json", _json_bytes({
        "error": error, "permission_actors": actors, "runs": runs,
    }))


def _run_transition(
        request: Mapping[str, object], comments_file: Path, output_directory: Path,
    ) -> None:
    state: LedgerState | None = None
    comment: dict[str, object] | None = None
    request_object = _fallback_request(request)
    try:
        request_object = _safe_request_for_refusal(request)
        _verify_pr(request, output_directory)
        state, comment = _ledger_comment(comments_file, request)
        provenances = _run_provenances(state, request, output_directory)
        if request["operation"] == "claim":
            events = _override_events(output_directory)
            preliminary_request = _base_claim_request(request, override_events=events)
            preliminary = claim(state, preliminary_request, provenances)
            if preliminary.decision in {
                    "claimed", "round_budget_exhausted", "total_usage_budget_exhausted"}:
                estimate = estimate_input_tokens(_validated_input_paths(request))
                request_object = replace(preliminary_request, estimated_input_tokens=estimate)
                transition = claim(state, request_object, provenances)
            else:
                request_object = preliminary_request
                transition = preliminary
        else:
            if state is None:
                raise TransportError("ledger_missing")
            transition = finalize(state, request_object, provenances)
        if transition.state.handoff.repository is None:
            transition = _recorded_refusal(state, request_object, transition.stop_reason)
    except (BudgetStateError, KeyError, TypeError) as exc:
        transition = _recorded_refusal(state, request_object, str(exc))
    _write_transition(output_directory, request, transition, state, comment)


def _cas_failed(
        request: Mapping[str, object], comments_file: Path, output_directory: Path,
    ) -> None:
    del comments_file
    prior = _read_json(output_directory / "prior-state.json", "prior_state")
    prior_state = None if prior is None else LedgerState.from_dict(prior)
    prior_output = _read_json(output_directory / "output.json", "output")
    if not isinstance(prior_output, dict):
        raise TransportError("output_invalid")
    request_object = _safe_request_for_refusal(request)
    transition = _recorded_refusal(prior_state, request_object, "compare_and_swap_failed")
    comment = None
    comment_id = prior_output.get("prior-comment-id")
    comment_body = prior_output.get("prior-comment-body")
    if comment_id:
        comment = {"id": _decimal(comment_id, "comment_id"), "body": comment_body}
    _write_transition(output_directory, request, transition, prior_state, comment)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    commands = parser.add_subparsers(dest="operation", required=True)
    for operation in ("list-run-identities", "claim", "finalize", "cas-failed"):
        command = commands.add_parser(operation, allow_abbrev=False)
        command.add_argument("--request-file", type=Path, required=True)
        command.add_argument("--comments-file", type=Path, required=True)
        command.add_argument("--output-directory", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    request = _transport_request(arguments.request_file)
    if arguments.operation not in {request["operation"], "list-run-identities", "cas-failed"}:
        raise TransportError("operation_mismatch")
    if arguments.operation == "list-run-identities":
        _list_run_identities(request, arguments.comments_file, arguments.output_directory)
    elif arguments.operation == "cas-failed":
        _cas_failed(request, arguments.comments_file, arguments.output_directory)
    else:
        _run_transition(request, arguments.comments_file, arguments.output_directory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
