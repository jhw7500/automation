#!/usr/bin/env python3
"""Pure parsing and authenticated admission for Claude rollout fallbacks."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys


class ContractError(ValueError):
    """A bounded refusal whose message is safe to expose as an output."""


VISIBLE_PREFIX = "@claude managed rollout review for PR "
HIDDEN_MARKER = "<!-- automation:claude-rollout-review-request:v1 "
REQUEST_BODY_RE = re.compile(
    r"\A@claude managed rollout review for PR (?P<visible_pr>[1-9][0-9]{0,15})\n"
    r"<!-- automation:claude-rollout-review-request:v1 (?P<json>\{[^\r\n]*\}) -->\n?\Z",
    re.ASCII,
)
REQUEST_KEYS = frozenset(
    {
        "repository",
        "pr",
        "expected_head_sha",
        "expected_base_sha",
        "original_run_id",
        "original_run_attempt",
        "release_commit",
        "managed_diff_sha256",
        "nonce",
    }
)
SHA_RE = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)
DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
NONCE_RE = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
REPOSITORY_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z", re.ASCII)
MAX_SAFE_INTEGER = 2**53 - 1
ALLOWED_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
AUTOMATIC_HEADER = "## Claude Code Review (latest)"
AUTOMATIC_MARKER = "<!-- automation:claude-code-review:v3 -->"
STATE_LINE_RE = re.compile(r"<!-- automation-state:(?P<json>\{[^\r\n]*\}) -->\Z")
FAILURE_STATE_KEYS = frozenset(
    {
        "schema",
        "reviewer",
        "pr",
        "run_id",
        "run_attempt",
        "attempt_head",
        "successful_head",
        "attempt_status",
        "diff_mode",
        "review_execution",
        "full_diff_sha256",
        "quality_schema",
        "accepted_count",
        "filtered_count",
        "normalized_count",
        "filtered_max_severity",
        "failure_reason",
    }
)
FALLBACK_ROUTE_KEYS = frozenset(
    {
        "managed_diff_sha256",
        "original_failed_run_id",
        "release_commit",
        "request_comment_id",
        "reviewed_base_sha",
        "route",
    }
)
OUTPUT_NAMES = (
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
)


@dataclass(frozen=True)
class FallbackRequest:
    repository: str
    pr: int
    expected_head_sha: str
    expected_base_sha: str
    original_run_id: int
    original_run_attempt: int
    release_commit: str
    managed_diff_sha256: str
    nonce: str

    @classmethod
    def from_dict(cls, value: object) -> "FallbackRequest":
        if not isinstance(value, dict) or set(value) != REQUEST_KEYS:
            raise ContractError("request_invalid")
        integers = (
            value["pr"],
            value["original_run_id"],
            value["original_run_attempt"],
        )
        if any(
            type(item) is not int or not 1 <= item <= MAX_SAFE_INTEGER
            for item in integers
        ):
            raise ContractError("request_invalid")
        repository = value["repository"]
        shas = (
            value["expected_head_sha"],
            value["expected_base_sha"],
            value["release_commit"],
        )
        if not isinstance(repository, str) or REPOSITORY_RE.fullmatch(repository) is None:
            raise ContractError("request_invalid")
        if any(
            not isinstance(item, str) or SHA_RE.fullmatch(item) is None
            for item in shas
        ):
            raise ContractError("request_invalid")
        digest, nonce = value["managed_diff_sha256"], value["nonce"]
        if not isinstance(digest, str) or DIGEST_RE.fullmatch(digest) is None:
            raise ContractError("request_invalid")
        if not isinstance(nonce, str) or NONCE_RE.fullmatch(nonce) is None:
            raise ContractError("request_invalid")
        return cls(
            repository=repository,
            pr=value["pr"],
            expected_head_sha=value["expected_head_sha"],
            expected_base_sha=value["expected_base_sha"],
            original_run_id=value["original_run_id"],
            original_run_attempt=value["original_run_attempt"],
            release_commit=value["release_commit"],
            managed_diff_sha256=digest,
            nonce=nonce,
        )

    def to_dict(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in REQUEST_KEYS}


@dataclass(frozen=True)
class RequestClassification:
    route: str
    reason: str
    request: FallbackRequest | None


@dataclass(frozen=True)
class EvidenceBundle:
    event_name: object
    event: object
    comment: object
    pull_request: object
    original_run: object
    original_jobs: object
    issue_comments: object


@dataclass(frozen=True)
class FallbackAdmission:
    request_comment_id: int
    request: FallbackRequest
    automatic_comment_id: int
    automatic_state_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "automatic_comment_id": self.automatic_comment_id,
            "automatic_state_sha256": self.automatic_state_sha256,
            "request": self.request.to_dict(),
            "request_comment_id": self.request_comment_id,
            "schema": 1,
        }


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError("request_invalid")
        result[key] = value
    return result


def canonical_request_body(request: FallbackRequest) -> str:
    payload = json.dumps(
        request.to_dict(), ensure_ascii=True, separators=(",", ":"), sort_keys=True
    )
    return f"{VISIBLE_PREFIX}{request.pr}\n{HIDDEN_MARKER}{payload} -->"


def parse_request_body(body: object) -> FallbackRequest:
    if not isinstance(body, str):
        raise ContractError("request_invalid")
    try:
        encoded = body.encode("utf-8")
    except UnicodeEncodeError:
        raise ContractError("request_invalid") from None
    if len(encoded) > 4096:
        raise ContractError("request_invalid")
    match = REQUEST_BODY_RE.fullmatch(body)
    if match is None:
        raise ContractError("request_invalid")
    try:
        value = json.loads(match.group("json"), object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, ContractError):
        raise ContractError("request_invalid") from None
    request = FallbackRequest.from_dict(value)
    canonical = canonical_request_body(request)
    if int(match.group("visible_pr")) != request.pr or body not in {
        canonical,
        canonical + "\n",
    }:
        raise ContractError("request_invalid")
    return request


def event_text_fields(event_name: object, event: object) -> tuple[str, ...]:
    if not isinstance(event, dict):
        return ()
    values: tuple[object, ...]
    if event_name in {"issue_comment", "pull_request_review_comment"}:
        comment = event.get("comment")
        values = (comment.get("body"),) if isinstance(comment, dict) else ()
    elif event_name == "pull_request_review":
        review = event.get("review")
        values = (review.get("body"),) if isinstance(review, dict) else ()
    elif event_name == "issues":
        issue = event.get("issue")
        values = (
            (issue.get("title"), issue.get("body"))
            if isinstance(issue, dict)
            else ()
        )
    else:
        values = ()
    return tuple(value for value in values if isinstance(value, str))


def classify_event(event_name: object, event: object) -> RequestClassification:
    texts = event_text_fields(event_name, event)
    reserved = any(
        VISIBLE_PREFIX in value or HIDDEN_MARKER in value for value in texts
    )
    if not reserved:
        return RequestClassification("interactive", "", None)
    if event_name != "issue_comment" or len(texts) != 1:
        return RequestClassification("invalid", "request_invalid", None)
    try:
        request = parse_request_body(texts[0])
    except ContractError as error:
        return RequestClassification("invalid", str(error), None)
    return RequestClassification("managed_candidate", "", request)


def event_comment_body(event: object) -> str:
    if not isinstance(event, dict) or not isinstance(event.get("comment"), dict):
        raise ContractError("event_invalid")
    body = event["comment"].get("body")
    if not isinstance(body, str):
        raise ContractError("event_invalid")
    return body


def positive_id(value: object, reason: str) -> int:
    if type(value) is not int or not 1 <= value <= MAX_SAFE_INTEGER:
        raise ContractError(reason)
    return value


def require_managed_created_event(
    event_name: object, event: object
) -> FallbackRequest:
    if event_name != "issue_comment" or not isinstance(event, dict):
        raise ContractError("event_invalid")
    if event.get("action") != "created":
        raise ContractError("event_invalid")
    request = parse_request_body(event_comment_body(event))
    repository = event.get("repository")
    issue = event.get("issue")
    comment = event.get("comment")
    if (
        not isinstance(repository, dict)
        or repository.get("full_name") != request.repository
        or not isinstance(issue, dict)
        or issue.get("number") != request.pr
        or issue.get("state") != "open"
        or not isinstance(issue.get("pull_request"), dict)
        or not isinstance(comment, dict)
    ):
        raise ContractError("event_invalid")
    positive_id(comment.get("id"), "event_invalid")
    return request


def require_exact_comment(
    comment: object, event: object, request: FallbackRequest
) -> None:
    if not isinstance(comment, dict) or not isinstance(event, dict):
        raise ContractError("comment_invalid")
    event_comment = event.get("comment")
    if not isinstance(event_comment, dict):
        raise ContractError("comment_invalid")
    comment_id = positive_id(comment.get("id"), "comment_invalid")
    event_comment_id = positive_id(event_comment.get("id"), "comment_invalid")
    body = comment.get("body")
    event_body = event_comment.get("body")
    if (
        comment_id != event_comment_id
        or not isinstance(body, str)
        or body != event_body
        or comment.get("created_at") != event_comment.get("created_at")
        or comment.get("author_association")
        != event_comment.get("author_association")
        or parse_request_body(body) != request
    ):
        raise ContractError("comment_invalid")
    user = comment.get("user")
    event_user = event_comment.get("user")
    if (
        comment.get("author_association") not in ALLOWED_ASSOCIATIONS
        or not isinstance(user, dict)
        or user.get("type") != "User"
        or not isinstance(user.get("login"), str)
        or not user["login"]
        or not isinstance(event_user, dict)
        or event_user.get("login") != user["login"]
        or event_user.get("type") != user["type"]
    ):
        raise ContractError("actor_unauthorized")


def require_exact_open_same_repository_pr(
    pull_request: object, request: FallbackRequest
) -> None:
    if not isinstance(pull_request, dict):
        raise ContractError("pull_request_invalid")
    head = pull_request.get("head")
    base = pull_request.get("base")
    if (
        pull_request.get("number") != request.pr
        or pull_request.get("state") != "open"
        or pull_request.get("merged") is not False
        or not isinstance(head, dict)
        or not isinstance(base, dict)
        or head.get("sha") != request.expected_head_sha
        or base.get("sha") != request.expected_base_sha
        or not isinstance(head.get("repo"), dict)
        or not isinstance(base.get("repo"), dict)
        or head["repo"].get("full_name") != request.repository
        or head["repo"].get("fork") is not False
        or base["repo"].get("full_name") != request.repository
    ):
        raise ContractError("pull_request_invalid")


def require_exact_automatic_run(run: object, request: FallbackRequest) -> None:
    if not isinstance(run, dict):
        raise ContractError("original_run_invalid")
    bindings = run.get("pull_requests")
    references = run.get("referenced_workflows")
    reference_prefix = "jhw7500/automation/.github/workflows/claude-code-review.yml@"
    reference_path = (
        references[0].get("path")
        if isinstance(references, list)
        and len(references) == 1
        and isinstance(references[0], dict)
        else None
    )
    if (
        run.get("id") != request.original_run_id
        or run.get("run_attempt") != request.original_run_attempt
        or run.get("name") != "Claude Code Review"
        or run.get("event") != "pull_request"
        or run.get("status") != "completed"
        or run.get("conclusion") != "failure"
        or run.get("head_sha") != request.expected_head_sha
        or not isinstance(run.get("repository"), dict)
        or run["repository"].get("full_name") != request.repository
        or not isinstance(bindings, list)
        or len(bindings) != 1
        or not isinstance(bindings[0], dict)
        or bindings[0].get("number") != request.pr
        or not isinstance(bindings[0].get("head"), dict)
        or bindings[0]["head"].get("sha") != request.expected_head_sha
        or not isinstance(bindings[0].get("base"), dict)
        or bindings[0]["base"].get("sha") != request.expected_base_sha
        or not isinstance(references, list)
        or len(references) != 1
        or not isinstance(references[0], dict)
        or not isinstance(reference_path, str)
        or not reference_path.startswith(reference_prefix)
        or not reference_path[len(reference_prefix) :]
        or references[0].get("sha") != request.release_commit
    ):
        raise ContractError("original_run_invalid")


def _github_time(value: object) -> datetime:
    if not isinstance(value, str) or re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value
    ) is None:
        raise ContractError("request_order_invalid")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        raise ContractError("request_order_invalid") from None


def require_causal_order(run: object, comment: object) -> None:
    if not isinstance(run, dict) or not isinstance(comment, dict):
        raise ContractError("request_order_invalid")
    if _github_time(run.get("updated_at")) > _github_time(comment.get("created_at")):
        raise ContractError("request_order_invalid")


def _completed_step(steps: list[dict[str, object]], name: str) -> dict[str, object]:
    matches = [step for step in steps if step.get("name") == name]
    if len(matches) != 1 or matches[0].get("status") != "completed":
        raise ContractError("original_jobs_invalid")
    return matches[0]


def require_failed_before_provider(jobs: object) -> None:
    if not isinstance(jobs, dict):
        raise ContractError("original_jobs_invalid")
    total = jobs.get("total_count")
    items = jobs.get("jobs")
    if (
        type(total) is not int
        or not 1 <= total <= 100
        or not isinstance(items, list)
        or len(items) != total
        or not all(isinstance(item, dict) for item in items)
    ):
        raise ContractError("original_jobs_invalid")
    matches = [
        item
        for item in items
        if re.search(r"(?:^| / )claude-review\Z", str(item.get("name", "")))
    ]
    if len(matches) != 1:
        raise ContractError("original_jobs_invalid")
    job = matches[0]
    if any(
        item is not job
        and (
            item.get("status") != "completed"
            or item.get("conclusion") not in {"success", "skipped"}
        )
        for item in items
    ):
        raise ContractError("original_jobs_invalid")
    steps_value = job.get("steps")
    if (
        job.get("status") != "completed"
        or job.get("conclusion") != "failure"
        or type(job.get("run_id")) is not int
        or not isinstance(steps_value, list)
        or not 1 <= len(steps_value) <= 100
        or not all(isinstance(step, dict) for step in steps_value)
    ):
        raise ContractError("original_jobs_invalid")
    steps: list[dict[str, object]] = steps_value
    numbers = [step.get("number") for step in steps]
    if (
        any(type(number) is not int or not 1 <= number <= MAX_SAFE_INTEGER for number in numbers)
        or numbers != sorted(set(numbers))
    ):
        raise ContractError("original_jobs_invalid")
    validation = _completed_step(steps, "Validate Claude caller workflow")
    claim = _completed_step(steps, "Claim Claude review budget")
    provider = _completed_step(steps, "Run Claude Code Review")
    if validation.get("conclusion") != "failure":
        raise ContractError("original_jobs_invalid")
    if claim.get("conclusion") != "skipped":
        raise ContractError("budget_claimed")
    if provider.get("conclusion") != "skipped":
        raise ContractError("provider_entered")
    if any(
        step is not validation
        and (
            step.get("status") != "completed"
            or step.get("conclusion") not in {"success", "skipped"}
        )
        for step in steps
    ):
        raise ContractError("original_jobs_invalid")
    if not validation["number"] < claim["number"] < provider["number"]:
        raise ContractError("original_jobs_invalid")


def _request_tuple(request: FallbackRequest) -> tuple[object, ...]:
    return (
        request.repository,
        request.pr,
        request.expected_head_sha,
        request.expected_base_sha,
        request.original_run_id,
        request.original_run_attempt,
        request.release_commit,
        request.managed_diff_sha256,
    )


def _comment_list(comments: object) -> list[dict[str, object]]:
    if not isinstance(comments, list) or not all(
        isinstance(comment, dict) for comment in comments
    ):
        raise ContractError("comments_invalid")
    if len(comments) >= 1000:
        raise ContractError("comment_horizon_exceeded")
    return comments


def require_unique_request(
    comments: object, current_comment_id: int, request: FallbackRequest
) -> None:
    matches: list[int] = []
    for comment in _comment_list(comments):
        body = comment.get("body")
        if not isinstance(body, str):
            continue
        try:
            candidate = parse_request_body(body)
        except ContractError:
            continue
        if _request_tuple(candidate) == _request_tuple(request):
            matches.append(positive_id(comment.get("id"), "request_ambiguous"))
    if matches != [current_comment_id]:
        raise ContractError("request_ambiguous")


def _strict_state_json(raw: str) -> dict[str, object]:
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ContractError("automatic_state_invalid")
            result[key] = value
        return result

    def reject_constant(_: str) -> object:
        raise ContractError("automatic_state_invalid")

    try:
        value = json.loads(
            raw, object_pairs_hook=unique, parse_constant=reject_constant
        )
    except (json.JSONDecodeError, ContractError, RecursionError):
        raise ContractError("automatic_state_invalid") from None
    if not isinstance(value, dict):
        raise ContractError("automatic_state_invalid")
    return value


def _state_comments(
    comments: object,
) -> list[tuple[dict[str, object], dict[str, object], bytes]]:
    records: list[tuple[dict[str, object], dict[str, object], bytes]] = []
    for comment in _comment_list(comments):
        user = comment.get("user")
        body = comment.get("body")
        if (
            not isinstance(user, dict)
            or user.get("login") != "github-actions[bot]"
            or user.get("type") != "Bot"
            or not isinstance(body, str)
        ):
            continue
        try:
            body_bytes = body.encode("utf-8")
        except UnicodeEncodeError:
            raise ContractError("automatic_state_invalid") from None
        if len(body_bytes) > 65536:
            raise ContractError("automatic_state_invalid")
        lines = body.split("\n")
        if len(lines) < 3 or lines[0] != AUTOMATIC_HEADER or lines[1] != AUTOMATIC_MARKER:
            continue
        match = STATE_LINE_RE.fullmatch(lines[2])
        if match is None:
            raise ContractError("automatic_state_invalid")
        raw = match.group("json")
        records.append((comment, _strict_state_json(raw), raw.encode("utf-8")))
    return records


def _valid_preserved_quality(state: dict[str, object]) -> bool:
    successful = state.get("successful_head")
    digest = state.get("full_diff_sha256")
    values = (
        state.get("accepted_count"),
        state.get("filtered_count"),
        state.get("normalized_count"),
    )
    severity = state.get("filtered_max_severity")
    if successful is None:
        return digest is None and all(value is None for value in values) and severity is None
    return (
        isinstance(successful, str)
        and SHA_RE.fullmatch(successful) is not None
        and isinstance(digest, str)
        and DIGEST_RE.fullmatch(digest) is not None
        and all(type(value) is int and 0 <= value <= MAX_SAFE_INTEGER for value in values)
        and severity in {"none", "MEDIUM", "HIGH", "CRITICAL"}
    )


def require_automatic_failure_state(
    comments: object, request: FallbackRequest
) -> tuple[int, bytes]:
    matches: list[tuple[int, bytes]] = []
    for comment, state, raw in _state_comments(comments):
        if (
            state.get("reviewer") != "claude"
            or state.get("pr") != request.pr
            or state.get("run_id") != request.original_run_id
            or state.get("run_attempt") != request.original_run_attempt
        ):
            continue
        if (
            set(state) != FAILURE_STATE_KEYS
            or state.get("schema") != 3
            or state.get("quality_schema") != 1
            or state.get("attempt_head") != request.expected_head_sha
            or state.get("attempt_status") != "failure"
            or state.get("diff_mode") not in {"full", "delta", "unavailable"}
            or state.get("review_execution") != "not_performed"
            or state.get("failure_reason") != "workflow_validation_mismatch"
            or not _valid_preserved_quality(state)
        ):
            raise ContractError("automatic_state_invalid")
        matches.append(
            (positive_id(comment.get("id"), "automatic_state_invalid"), raw)
        )
    if len(matches) != 1:
        raise ContractError("automatic_state_ambiguous")
    return matches[0]


def require_no_successful_fallback(
    comments: object, request: FallbackRequest
) -> None:
    for _, state, _ in _state_comments(comments):
        route = state.get("route")
        if (
            state.get("schema") == 3
            and state.get("reviewer") == "claude"
            and state.get("pr") == request.pr
            and state.get("attempt_status") == "success"
            and state.get("successful_head") == request.expected_head_sha
            and isinstance(route, dict)
            and set(route) == FALLBACK_ROUTE_KEYS
            and route.get("route") == "default_branch_rollout_fallback"
            and route.get("original_failed_run_id") == request.original_run_id
            and route.get("reviewed_base_sha") == request.expected_base_sha
            and route.get("release_commit") == request.release_commit
            and route.get("managed_diff_sha256") == request.managed_diff_sha256
        ):
            raise ContractError("fallback_already_succeeded")


def admit(bundle: EvidenceBundle) -> FallbackAdmission:
    request = require_managed_created_event(bundle.event_name, bundle.event)
    require_exact_comment(bundle.comment, bundle.event, request)
    require_exact_open_same_repository_pr(bundle.pull_request, request)
    require_exact_automatic_run(bundle.original_run, request)
    require_causal_order(bundle.original_run, bundle.comment)
    require_failed_before_provider(bundle.original_jobs)
    jobs = bundle.original_jobs["jobs"]
    if any(
        job.get("run_id") != request.original_run_id
        or (
            "run_attempt" in job
            and job.get("run_attempt") != request.original_run_attempt
        )
        for job in jobs
    ):
        raise ContractError("original_jobs_invalid")
    request_comment_id = positive_id(
        bundle.comment["id"], "request_comment_id_invalid"
    )
    require_unique_request(bundle.issue_comments, request_comment_id, request)
    comment_id, state_bytes = require_automatic_failure_state(
        bundle.issue_comments, request
    )
    require_no_successful_fallback(bundle.issue_comments, request)
    return FallbackAdmission(
        request_comment_id=request_comment_id,
        request=request,
        automatic_comment_id=comment_id,
        automatic_state_sha256=hashlib.sha256(state_bytes).hexdigest(),
    )


def _read_json(path: Path, reason: str, *, limit: int, private: bool = False) -> object:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise ContractError(reason)
        if private and (
            info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise ContractError(reason)
        payload = path.read_bytes()
    except (OSError, ContractError):
        raise ContractError(reason) from None
    if len(payload) > limit:
        raise ContractError(reason)

    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ContractError(reason)
            result[key] = value
        return result

    def reject_constant(_: str) -> object:
        raise ContractError(reason)

    try:
        return json.loads(
            payload, object_pairs_hook=unique, parse_constant=reject_constant
        )
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise ContractError(reason) from None


def _write_private_json(path: Path, value: dict[str, object]) -> None:
    payload = json.dumps(
        value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("ascii") + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.close(descriptor)
        info = path.lstat()
    except OSError:
        raise ContractError("private_file_invalid") from None
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise ContractError("private_file_invalid")


def _build_plan(
    event_name: str, event: dict[str, object], request: FallbackRequest
) -> dict[str, object]:
    comment = event.get("comment")
    if not isinstance(comment, dict):
        raise ContractError("event_invalid")
    comment_id = positive_id(comment.get("id"), "event_invalid")
    repository = request.repository
    run_root = (
        f"repos/{repository}/actions/runs/{request.original_run_id}/attempts/"
        f"{request.original_run_attempt}"
    )
    requests: list[dict[str, str]] = [
        {
            "endpoint": f"repos/{repository}/issues/comments/{comment_id}",
            "name": "comment.json",
        },
        {
            "endpoint": f"repos/{repository}/pulls/{request.pr}",
            "name": "pull_request.json",
        },
        {"endpoint": run_root, "name": "original_run.json"},
        {"endpoint": f"{run_root}/jobs?per_page=100", "name": "original_jobs.json"},
    ]
    requests.extend(
        {
            "endpoint": (
                f"repos/{repository}/issues/{request.pr}/comments"
                f"?per_page=100&page={page}"
            ),
            "name": f"comments-{page:02d}.json",
        }
        for page in range(1, 11)
    )
    return {
        "event": event,
        "event_name": event_name,
        "request": request.to_dict(),
        "requests": requests,
        "schema": 1,
    }


def _output_values(
    route: str, reason: str, admission: FallbackAdmission | None = None
) -> dict[str, str]:
    values = {name: "" for name in OUTPUT_NAMES}
    values["route"] = route
    values["reason"] = reason
    if admission is not None:
        request = admission.request
        values.update(
            {
                "request-comment-id": str(admission.request_comment_id),
                "request-nonce": request.nonce,
                "expected-head-sha": request.expected_head_sha,
                "expected-base-sha": request.expected_base_sha,
                "original-run-id": str(request.original_run_id),
                "original-run-attempt": str(request.original_run_attempt),
                "release-commit": request.release_commit,
                "managed-diff-sha256": request.managed_diff_sha256,
                "automatic-comment-id": str(admission.automatic_comment_id),
                "automatic-state-sha256": admission.automatic_state_sha256,
            }
        )
    return values


def _append_outputs(path: Path, values: dict[str, str], *, only_route: bool = False) -> None:
    names = OUTPUT_NAMES[:2] if only_route else OUTPUT_NAMES
    patterns = {
        "route": re.compile(r"(?:interactive|invalid|managed)\Z"),
        "reason": re.compile(r"[a-z_]*\Z"),
        "request-comment-id": re.compile(r"(?:|[1-9][0-9]{0,15})\Z"),
        "request-nonce": re.compile(r"(?:|[0-9a-f]{32})\Z"),
        "expected-head-sha": re.compile(r"(?:|[0-9a-f]{40})\Z"),
        "expected-base-sha": re.compile(r"(?:|[0-9a-f]{40})\Z"),
        "original-run-id": re.compile(r"(?:|[1-9][0-9]{0,15})\Z"),
        "original-run-attempt": re.compile(r"(?:|[1-9][0-9]{0,15})\Z"),
        "release-commit": re.compile(r"(?:|[0-9a-f]{40})\Z"),
        "managed-diff-sha256": re.compile(r"(?:|[0-9a-f]{64})\Z"),
        "automatic-comment-id": re.compile(r"(?:|[1-9][0-9]{0,15})\Z"),
        "automatic-state-sha256": re.compile(r"(?:|[0-9a-f]{64})\Z"),
    }
    if set(values) != set(OUTPUT_NAMES) or any(
        patterns[name].fullmatch(values[name]) is None for name in names
    ):
        raise ContractError("output_invalid")
    try:
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            for name in names:
                stream.write(f"{name}={values[name]}\n")
    except OSError:
        raise ContractError("output_invalid") from None


def _plan_command(arguments: argparse.Namespace) -> int:
    event_value = _read_json(Path(arguments.event_file), "event_invalid", limit=2_000_000)
    classification = classify_event(arguments.event_name, event_value)
    if classification.route != "managed_candidate":
        _append_outputs(
            Path(arguments.github_output),
            _output_values(classification.route, classification.reason),
            only_route=True,
        )
        return 0
    if not isinstance(event_value, dict) or classification.request is None:
        raise ContractError("event_invalid")
    request = require_managed_created_event(arguments.event_name, event_value)
    _write_private_json(
        Path(arguments.plan_file),
        _build_plan(arguments.event_name, event_value, request),
    )
    return 0


def _verified_plan(path: Path) -> tuple[str, dict[str, object], FallbackRequest]:
    plan = _read_json(path, "plan_invalid", limit=2_000_000, private=True)
    if not isinstance(plan, dict) or set(plan) != {
        "event",
        "event_name",
        "request",
        "requests",
        "schema",
    }:
        raise ContractError("plan_invalid")
    event_name = plan.get("event_name")
    event = plan.get("event")
    if not isinstance(event_name, str) or not isinstance(event, dict):
        raise ContractError("plan_invalid")
    request = require_managed_created_event(event_name, event)
    try:
        planned_request = FallbackRequest.from_dict(plan.get("request"))
    except ContractError:
        raise ContractError("plan_invalid") from None
    if planned_request != request or plan != _build_plan(event_name, event, request):
        raise ContractError("plan_invalid")
    return event_name, event, request


def _evidence_bundle(plan_path: Path, directory: Path) -> EvidenceBundle:
    event_name, event, _ = _verified_plan(plan_path)
    try:
        info = directory.lstat()
    except OSError:
        raise ContractError("evidence_invalid") from None
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise ContractError("evidence_invalid")
    fixed = {
        name: _read_json(directory / name, "evidence_invalid", limit=8_000_000, private=True)
        for name in (
            "comment.json",
            "pull_request.json",
            "original_run.json",
            "original_jobs.json",
        )
    }
    comments: list[object] = []
    complete = False
    for page in range(1, 11):
        page_path = directory / f"comments-{page:02d}.json"
        if not page_path.exists():
            if page == 1 or not complete:
                raise ContractError("evidence_invalid")
            break
        if complete:
            raise ContractError("evidence_invalid")
        value = _read_json(page_path, "evidence_invalid", limit=8_000_000, private=True)
        if not isinstance(value, list) or len(value) > 100:
            raise ContractError("evidence_invalid")
        comments.extend(value)
        complete = len(value) < 100
    if not complete:
        raise ContractError("comment_horizon_exceeded")
    return EvidenceBundle(
        event_name=event_name,
        event=event,
        comment=fixed["comment.json"],
        pull_request=fixed["pull_request.json"],
        original_run=fixed["original_run.json"],
        original_jobs=fixed["original_jobs.json"],
        issue_comments=comments,
    )


def _verify_command(arguments: argparse.Namespace) -> int:
    output_path = Path(arguments.github_output)
    try:
        admission = admit(
            _evidence_bundle(
                Path(arguments.plan_file), Path(arguments.evidence_directory)
            )
        )
        _write_private_json(Path(arguments.admission_file), admission.to_dict())
        _append_outputs(output_path, _output_values("managed", "", admission))
    except ContractError as error:
        reason = str(error)
        if re.fullmatch(r"[a-z_]+", reason) is None:
            reason = "admission_invalid"
        _append_outputs(output_path, _output_values("invalid", reason))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--event-name", required=True)
    plan.add_argument("--event-file", required=True)
    plan.add_argument("--plan-file", required=True)
    plan.add_argument("--github-output", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--plan-file", required=True)
    verify.add_argument("--evidence-directory", required=True)
    verify.add_argument("--admission-file", required=True)
    verify.add_argument("--github-output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "plan":
            return _plan_command(arguments)
        return _verify_command(arguments)
    except ContractError as error:
        if arguments.command == "plan":
            reason = str(error)
            if re.fullmatch(r"[a-z_]+", reason) is None:
                reason = "request_invalid"
            _append_outputs(
                Path(arguments.github_output),
                _output_values("invalid", reason),
                only_route=True,
            )
            return 0
        raise


if __name__ == "__main__":
    sys.exit(main())
