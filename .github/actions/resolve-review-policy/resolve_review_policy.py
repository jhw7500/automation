#!/usr/bin/env python3
"""Resolve whether a pull request review may invoke a model."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


class PolicyError(ValueError):
    pass


@dataclass(frozen=True)
class PolicyRequest:
    workflow_name: str
    review_mode: str
    force_run: bool
    force_review: bool
    event_name: str
    repository: str
    pr: dict[str, object]
    config: dict[str, object]


@dataclass(frozen=True)
class PolicyDecision:
    run_review: bool
    effective_mode: str
    reason: str
    head_sha: str


_REPOSITORY_PATTERN = re.compile(r"[^/\s]+/[^/\s]+\Z")
_SHA_PATTERN = re.compile(r"[0-9a-fA-F]{40}\Z")
_MODES = {"auto", "request", "skip", "conflict"}


def resolve_policy(request: PolicyRequest) -> PolicyDecision:
    _validate_request(request)
    labels = _label_names(request.pr)
    if {"review:request", "review:skip"} <= labels:
        raise PolicyError("review_label_conflict")
    label_mode = (
        "request"
        if "review:request" in labels
        else "skip"
        if "review:skip" in labels
        else "auto"
    )
    if request.review_mode not in _MODES:
        raise PolicyError("review_mode_invalid")
    if request.review_mode == "conflict":
        raise PolicyError("review_label_conflict")
    manual_request = request.event_name == "workflow_dispatch" and request.force_review
    if manual_request and request.review_mode != "request":
        raise PolicyError("force_review_mode_invalid")
    if request.review_mode != label_mode and not (
        manual_request and label_mode == "auto"
    ):
        # A label that moved between the trigger and this read declines the run.
        # No review happens under either outcome, so failing would only report a
        # broken reviewer for an ordinary opt-in race, and the labeled event that
        # follows carries the real verdict. A manual dispatch has no such follow-up
        # and its request would vanish, so that one still fails.
        if manual_request:
            raise PolicyError("review_mode_label_mismatch")
        return PolicyDecision(
            False, request.review_mode, "review_mode_label_mismatch", ""
        )
    head_sha = _validated_head(request.pr, request.repository)
    if request.pr.get("state") != "open":
        return PolicyDecision(False, request.review_mode, "closed", head_sha)
    if request.pr.get("draft") is True:
        return PolicyDecision(False, request.review_mode, "draft", head_sha)
    if head_sha == "":
        return PolicyDecision(False, request.review_mode, "unsafe_pr", "")
    if not _workflow_enabled(request.config, request.workflow_name) and not request.force_run:
        return PolicyDecision(False, request.review_mode, "workflow_disabled", head_sha)
    if request.review_mode == "skip":
        return PolicyDecision(False, "skip", "skip", head_sha)
    if request.review_mode == "request" or request.force_run:
        return PolicyDecision(True, "request", "request", head_sha)
    return _automatic_decision(request, head_sha)


def _validate_request(request: PolicyRequest) -> None:
    if not isinstance(request.workflow_name, str) or not request.workflow_name:
        raise PolicyError("workflow_name_invalid")
    if not isinstance(request.review_mode, str):
        raise PolicyError("review_mode_invalid")
    if type(request.force_run) is not bool:
        raise PolicyError("force_run_invalid")
    if type(request.force_review) is not bool:
        raise PolicyError("force_review_invalid")
    if not isinstance(request.event_name, str) or not request.event_name:
        raise PolicyError("event_name_invalid")
    if not isinstance(request.pr, dict):
        raise PolicyError("pr_invalid")
    if not isinstance(request.config, dict):
        raise PolicyError("config_invalid")
    _validate_repository(request.repository, "repository_invalid")


def _label_names(pr: dict[str, object]) -> set[str]:
    labels = pr.get("labels", [])
    if not isinstance(labels, list):
        raise PolicyError("pr_labels_invalid")
    names: set[str] = set()
    for label in labels:
        if not isinstance(label, dict) or not isinstance(label.get("name"), str):
            raise PolicyError("pr_label_invalid")
        names.add(label["name"])
    return names


def _validated_head(pr: dict[str, object], repository: str) -> str:
    head = pr.get("head")
    if not isinstance(head, dict):
        raise PolicyError("pr_head_invalid")
    head_repository = head.get("repo")
    if not isinstance(head_repository, dict):
        return ""
    full_name = head_repository.get("full_name")
    fork = head_repository.get("fork")
    if not isinstance(full_name, str):
        raise PolicyError("head_repository_invalid")
    _validate_repository(full_name, "head_repository_invalid")
    if type(fork) is not bool:
        raise PolicyError("head_repository_invalid")
    if fork or full_name != repository:
        return ""
    sha = head.get("sha")
    if not isinstance(sha, str) or _SHA_PATTERN.fullmatch(sha) is None:
        raise PolicyError("head_sha_invalid")
    return sha


def _validate_repository(repository: str, error: str) -> None:
    if not isinstance(repository, str) or _REPOSITORY_PATTERN.fullmatch(repository) is None:
        raise PolicyError(error)


def _workflow_enabled(config: dict[str, object], workflow_name: str) -> bool:
    workflows = config.get("workflows")
    if workflows is None:
        return True
    if not isinstance(workflows, dict):
        raise PolicyError("workflows_config_invalid")
    workflow = workflows.get(workflow_name)
    if workflow is None:
        return True
    if not isinstance(workflow, dict):
        raise PolicyError("workflow_config_invalid")
    enabled = workflow.get("enabled")
    if enabled is None:
        return True
    if type(enabled) is not bool:
        raise PolicyError("workflow_enabled_invalid")
    return enabled


def _automatic_decision(request: PolicyRequest, head_sha: str) -> PolicyDecision:
    workflows = request.config.get("workflows")
    if workflows is not None and not isinstance(workflows, dict):
        raise PolicyError("workflows_config_invalid")
    workflow = workflows.get(request.workflow_name) if workflows else None
    if workflow is not None and not isinstance(workflow, dict):
        raise PolicyError("workflow_config_invalid")
    if workflow is not None and "auto" in workflow:
        automatic = _required_boolean(workflow["auto"], "workflow_auto_invalid")
        return PolicyDecision(
            automatic,
            "auto",
            f"workflow_auto_{str(automatic).lower()}",
            head_sha,
        )
    review = request.config.get("review")
    if review is not None and not isinstance(review, dict):
        raise PolicyError("review_config_invalid")
    if review is not None and "auto" in review:
        automatic = _required_boolean(review["auto"], "review_auto_invalid")
        return PolicyDecision(
            automatic,
            "auto",
            f"review_auto_{str(automatic).lower()}",
            head_sha,
        )
    return PolicyDecision(False, "auto", "default_auto_false", head_sha)


def _required_boolean(value: object, error: str) -> bool:
    if type(value) is not bool:
        raise PolicyError(error)
    return value


def _read_request(path: Path) -> PolicyRequest:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise PolicyError("request_invalid")
    try:
        return PolicyRequest(**payload)
    except TypeError as error:
        raise PolicyError("request_invalid") from error


def _write_result(path: Path, decision: PolicyDecision) -> None:
    path.write_text(
        json.dumps(asdict(decision), separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _append_github_outputs(path: Path, decision: PolicyDecision) -> None:
    values = {
        "run-review": str(decision.run_review).lower(),
        "effective-mode": decision.effective_mode,
        "reason": decision.reason,
        "head-sha": decision.head_sha,
    }
    with path.open("a", encoding="utf-8") as output:
        for key, value in values.items():
            output.write(f"{key}={value}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request-file", type=Path, required=True)
    parser.add_argument("--result-file", type=Path, required=True)
    parser.add_argument("--github-output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        decision = resolve_policy(_read_request(args.request_file))
        _write_result(args.result_file, decision)
        _append_github_outputs(args.github_output, decision)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
