#!/usr/bin/env python3
"""Classify managed repository content by comparing it with the renderer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from scripts.prepare_workflow_rollout import RolloutError, render_repository
from scripts.workflow_catalog import RepoProfile
from scripts.workflow_release_bundle import ReleaseBundle


@dataclass(frozen=True)
class AuditResult:
    repo: str
    status: Literal["current", "drift", "blocked"]
    detail: str
    changed_paths: tuple[str, ...]


def audit_repository(
    repo: Path,
    bundle: ReleaseBundle,
    profile: RepoProfile,
    secret_names: set[str],
    variable_names: set[str],
) -> AuditResult:
    """Return a renderer-derived content classification without writing."""

    try:
        plan = render_repository(
            repo,
            bundle.canonical,
            bundle.catalog,
            profile,
            bundle.ref,
            bundle.commit,
            secret_names,
            variable_names,
            bootstrap=False,
        )
    except RolloutError as exc:
        return AuditResult(repo.name, "blocked", str(exc), ())

    if plan.status == "blocked":
        return AuditResult(repo.name, "blocked", plan.reason, ())
    changed_paths = tuple(sorted(change.path.as_posix() for change in plan.changes))
    if plan.status in {"drift", "bootstrap_required"}:
        return AuditResult(repo.name, "drift", plan.reason, changed_paths)
    return AuditResult(repo.name, "current", plan.reason, ())
