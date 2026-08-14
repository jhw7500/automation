#!/usr/bin/env python3
"""Classify one repository or the configured fleet without remote mutation."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
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
from scripts.workflow_catalog import RepoProfile  # noqa: E402
from scripts.workflow_release_bundle import (  # noqa: E402
    ReleaseBundle,
    materialize_release_bundle,
)


VERSION_REF = re.compile(r"v[0-9]+(?:\.[0-9]+)+")
WORKSPACE_MARKER = ".automation-fleet-workspace"
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
        return AuditResult(repo.name, "blocked", str(exc), ())

    if plan.status == "blocked":
        return AuditResult(repo.name, "blocked", plan.reason, ())
    changed_paths = tuple(sorted(change.path.as_posix() for change in plan.changes))
    if plan.status in {"drift", "bootstrap_required"}:
        return AuditResult(repo.name, "drift", plan.reason, changed_paths)
    return AuditResult(repo.name, "current", plan.reason, ())


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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--automation", type=Path, default=ROOT)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--ref", default="v1.40.1")
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
        with tempfile.TemporaryDirectory(prefix=".audit-", dir=workspace) as temporary:
            clone_workspace = Path(temporary)
            (clone_workspace / WORKSPACE_MARKER).write_text(
                "managed disposable clones only\n", encoding="utf-8"
            )
            for repo in repos:
                try:
                    snapshot = fleet_git.clone_default_branch(
                        bundle.config.owner, repo, clone_workspace
                    )
                    base_sha = fleet_git.refetch_default(snapshot)
                    git(["switch", "--detach", base_sha], cwd=snapshot.path)
                    outcomes.append(
                        audit_repository(
                            snapshot.path,
                            bundle,
                            bundle.config.profiles[repo],
                            set(snapshot.secret_names),
                            set(snapshot.variable_names),
                            observed_revision=base_sha,
                        )
                    )
                except (FleetGitError, RolloutError):
                    outcomes.append(
                        AuditResult(repo, "blocked", "repository audit failed", ())
                    )
                except (OSError, KeyError, ValueError):
                    outcomes.append(
                        AuditResult(repo, "blocked", "local audit failed", ())
                    )

        for outcome in outcomes:
            print(f"{outcome.status.upper():7} {outcome.repo}: {outcome.detail}")
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
