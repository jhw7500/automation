#!/usr/bin/env python3
"""Classify one repository or the configured fleet without remote mutation."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Literal

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import workflow_fleet_git as fleet_git  # noqa: E402
from scripts.prepare_workflow_rollout import (  # noqa: E402
    RolloutError,
    render_repository,
)
from scripts.workflow_fleet_git import FleetGitError  # noqa: E402
from scripts.workflow_catalog import (  # noqa: E402
    CatalogError,
    RepoProfile,
    configured_branch_targets,
    validate_resolved_branch_targets,
)
from scripts.workflow_release_bundle import (  # noqa: E402
    ReleaseBundle,
    materialize_release_bundle,
)


VERSION_REF = re.compile(r"v[0-9]+(?:\.[0-9]+)+")
WORKSPACE_MARKER = ".automation-fleet-workspace"
UNRESOLVED_DEFAULT_BRANCH = "default:unresolved"
GIT_PREFIX = (
    "git",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "submodule.recurse=false",
)


@dataclass(frozen=True)
class AuditResult:
    repo: str
    base_branch: str
    status: Literal["current", "drift", "blocked"]
    detail: str
    changed_paths: tuple[str, ...]


def audit_repository(
    repo: Path,
    bundle: ReleaseBundle,
    profile: RepoProfile,
    secret_names: set[str],
    variable_names: set[str],
    *,
    observed_revision: str | None = None,
    base_branch: str = "",
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
            observed_revision=observed_revision,
        )
    except RolloutError as exc:
        return AuditResult(repo.name, base_branch, "blocked", str(exc), ())

    if plan.status == "blocked":
        return AuditResult(repo.name, base_branch, "blocked", plan.reason, ())
    changed_paths = tuple(sorted(change.path.as_posix() for change in plan.changes))
    if plan.status in {"drift", "bootstrap_required"}:
        return AuditResult(repo.name, base_branch, "drift", plan.reason, changed_paths)
    return AuditResult(repo.name, base_branch, "current", plan.reason, ())


def _block_inconsistent_audit_targets(
    profile: RepoProfile,
    targets: list[tuple[str | None, AuditResult, tuple[str, str] | None]],
) -> list[AuditResult]:
    """Keep every configured target visible when metadata makes them ambiguous."""

    if any(resolution is None for _, _, resolution in targets):
        return [outcome for _, outcome, _ in targets]
    resolved = tuple(
        (target, resolution[0], resolution[1])
        for target, _, resolution in targets
        if resolution is not None
    )
    try:
        validate_resolved_branch_targets(profile, resolved)
    except CatalogError as exc:
        detail = f"branch target consistency failed: {exc}"
        return [
            AuditResult(
                outcome.repo,
                f"default:{outcome.base_branch}"
                if target is None
                else outcome.base_branch,
                "blocked",
                detail,
                (),
            )
            for target, outcome, _ in targets
        ]
    return [outcome for _, outcome, _ in targets]


def git(args: list[str], *, cwd: Path | None = None) -> str:
    """Run read-only local Git operations through the scrubbed adapter."""

    if (
        len(args) != 3
        or args[:2] != ["switch", "--detach"]
        or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", args[2]) is None
    ):
        raise FleetGitError("audit Git operation is not permitted")
    return fleet_git.run([*GIT_PREFIX, *args], cwd=cwd)


def _workspace(path: Path, parser: argparse.ArgumentParser) -> Path:
    try:
        absolute = Path(os.path.abspath(path))
        resolved = path.resolve(strict=True)
        marker = resolved / WORKSPACE_MARKER
        mode = marker.lstat().st_mode
        if (
            resolved != absolute
            or path.is_symlink()
            or not resolved.is_dir()
            or marker.is_symlink()
            or not stat.S_ISREG(mode)
        ):
            raise OSError
        return resolved
    except (OSError, RuntimeError):
        parser.error("audit workspace must be a real marked disposable directory")
    raise AssertionError("argparse.error must exit")


def _default_ref() -> str:
    # 릴리즈 정체성의 단일 출처는 scripts/workflow-config.json이다. CLI 기본값을 여기서
    # 읽어, 버전 범프 때 하드코딩 기본값이 직전 릴리즈로 남는 사고(--ref 없이 실행하면
    # 플릿 전체가 drift로 보이거나 구버전 재핀 PR이 열리는 것)를 구조적으로 없앤다.
    config_path = ROOT / "scripts" / "workflow-config.json"
    return str(json.loads(config_path.read_text(encoding="utf-8"))["automation_ref"])


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--automation", type=Path, default=ROOT)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--ref", default=_default_ref())
    parser.add_argument("--repo", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if VERSION_REF.fullmatch(args.ref) is None:
        parser.error(f"invalid release ref: {args.ref}")
    if len(args.repo) != len(set(args.repo)):
        parser.error("--repo values must be unique")
    workspace = _workspace(args.workspace, parser)

    with materialize_release_bundle(
        args.automation, args.ref, remote="origin"
    ) as bundle:
        configured = set(bundle.config.profiles)
        repos = args.repo or sorted(configured)
        unknown = sorted(set(repos) - configured)
        if unknown:
            parser.error(f"repositories not in release bundle: {', '.join(unknown)}")

        outcomes: list[AuditResult] = []
        for repo in repos:
            profile = bundle.config.profiles[repo]
            target_outcomes: list[
                tuple[str | None, AuditResult, tuple[str, str] | None]
            ] = []
            for target_branch in configured_branch_targets(profile):
                selected_base = target_branch or UNRESOLVED_DEFAULT_BRANCH
                resolution: tuple[str, str] | None = None
                with tempfile.TemporaryDirectory(
                    prefix=f".audit-{repo}-", dir=workspace
                ) as temporary:
                    clone_workspace = Path(temporary)
                    (clone_workspace / WORKSPACE_MARKER).write_text(
                        "managed disposable clones only\n", encoding="utf-8"
                    )
                    try:
                        snapshot = fleet_git.clone_branch(
                            bundle.config.owner, repo, clone_workspace, target_branch
                        )
                        selected_base = snapshot.base_branch or snapshot.default_branch
                        resolution = (snapshot.default_branch, selected_base)
                        base_sha = fleet_git.refetch_branch(snapshot)
                        git(["switch", "--detach", base_sha], cwd=snapshot.path)
                        target_outcomes.append(
                            (
                                target_branch,
                                audit_repository(
                                snapshot.path,
                                bundle,
                                profile,
                                set(snapshot.secret_names),
                                set(snapshot.variable_names),
                                observed_revision=base_sha,
                                base_branch=selected_base,
                                ),
                                resolution,
                            )
                        )
                    except (FleetGitError, RolloutError):
                        target_outcomes.append(
                            (
                                target_branch,
                                AuditResult(
                                    repo,
                                    selected_base,
                                    "blocked",
                                    "repository audit failed",
                                    (),
                                ),
                                resolution,
                            )
                        )
                    except (OSError, KeyError, ValueError):
                        target_outcomes.append(
                            (
                                target_branch,
                                AuditResult(
                                    repo,
                                    selected_base,
                                    "blocked",
                                    "local audit failed",
                                    (),
                                ),
                                resolution,
                            )
                        )
            outcomes.extend(_block_inconsistent_audit_targets(profile, target_outcomes))

        for outcome in outcomes:
            print(
                f"{outcome.status.upper():7} "
                f"{outcome.repo}[{outcome.base_branch}]: {outcome.detail}"
            )
        counts = {
            status: sum(item.status == status for item in outcomes)
            for status in ("current", "drift", "blocked")
        }
        print(
            f"SUMMARY ref={bundle.ref} commit={bundle.commit} total={len(outcomes)} "
            f"current={counts['current']} drift={counts['drift']} blocked={counts['blocked']}"
        )
        return 1 if counts["blocked"] else 0


if __name__ == "__main__":
    sys.exit(main())
