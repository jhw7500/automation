#!/usr/bin/env python3
"""Plan fleet workflow drift and publish creation-only rollout pull requests."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Literal, Sequence

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import workflow_fleet_git as fleet_git  # noqa: E402
from scripts.audit_workflow_fleet import audit_repository  # noqa: E402
from scripts.prepare_workflow_rollout import (  # noqa: E402
    ManagedResult,
    RenderPlan,
    RolloutError,
    render_repository,
)
from scripts.workflow_catalog import (  # noqa: E402
    WorkflowCatalog,
    configured_branch_targets,
)
from scripts.workflow_fleet_git import (  # noqa: E402
    FleetGitError,
    PullRequest,
    RepositorySnapshot,
)
from scripts.workflow_release_bundle import (  # noqa: E402
    ReleaseBundle,
    materialize_release_bundle,
)


VERSION_REF = re.compile(r"v[0-9]+(?:\.[0-9]+)+")
OBJECT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
MANAGED_GIT_MODE = "100644"
WORKSPACE_MARKER = ".automation-fleet-workspace"
GIT_PREFIX = (
    "git",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "submodule.recurse=false",
)
LOCAL_GIT_OPERATIONS = frozenset(
    {
        "diff-tree",
        "fetch",
        "hash-object",
        "ls-tree",
        "rev-list",
        "rev-parse",
        "switch",
    }
)
HERMETIC_GIT = Path("/usr/bin/git")
HERMETIC_GIT_OPERATIONS = frozenset(
    {
        "commit-tree",
        "diff",
        "hash-object",
        "init",
        "read-tree",
        "symbolic-ref",
        "update-index",
        "update-ref",
        "write-tree",
    }
)


class CommandError(RuntimeError):
    """A fleet operation cannot safely continue."""


class BranchPublishError(CommandError):
    """A prepared local head did not produce a confirmed remote branch."""

    def __init__(self, detail: str, head_sha: str) -> None:
        super().__init__(detail)
        self.head_sha = head_sha


@dataclass(frozen=True)
class RepoOutcome:
    repo: str
    status: str
    detail: str
    base_sha: str = ""
    head_sha: str = ""
    pr_url: str = ""
    changed_paths: tuple[str, ...] = ()
    stage: str = ""
    base_branch: str = ""


@dataclass(frozen=True)
class RolloutInspection:
    action: Literal["create_branch", "create_pr", "reuse"]
    branch_sha: str = ""
    pr_url: str = ""


@dataclass(frozen=True)
class PreparedRepo:
    repo: str
    action: str
    outcome: RepoOutcome
    plan: RenderPlan | None
    base_branch: str = ""
    target_branch: str | None = None


@dataclass
class PublicationProgress:
    stage: str = "clone"
    base_sha: str = ""
    head_sha: str = ""
    changed_paths: tuple[str, ...] = ()
    base_branch: str = ""


def _publication_blocked(
    repo: str,
    progress: PublicationProgress,
    detail: str,
    *,
    pr_url: str = "",
) -> RepoOutcome:
    return RepoOutcome(
        repo,
        "blocked",
        f"{progress.stage} stage failed: {detail}",
        progress.base_sha,
        progress.head_sha,
        pr_url,
        progress.changed_paths,
        progress.stage,
        progress.base_branch,
    )


def git(
    args: Sequence[str], *, cwd: Path | None = None, stdin: str | None = None
) -> str:
    """Run Git through the credential-scrubbing adapter boundary."""

    if not args:
        raise CommandError("Git operation is not permitted")
    operation = args[0]
    if operation not in LOCAL_GIT_OPERATIONS:
        raise CommandError("Git operation is not permitted")
    return fleet_git.run([*GIT_PREFIX, *args], cwd=cwd, stdin=stdin)


def _selected_base_branch(snapshot: RepositorySnapshot) -> str:
    return snapshot.base_branch or snapshot.default_branch


def _is_additional_branch(base_branch: str | None, default_branch: str | None) -> bool:
    return base_branch is not None and default_branch is not None and base_branch != default_branch


def rollout_branch(
    ref: str,
    base_branch: str | None = None,
    default_branch: str | None = None,
) -> str:
    if VERSION_REF.fullmatch(ref) is None:
        raise CommandError(f"invalid release ref: {ref}")
    branch = f"automation/common-workflows-{ref}"
    if _is_additional_branch(base_branch, default_branch):
        assert base_branch is not None
        return f"{branch}-{hashlib.sha256(base_branch.encode('utf-8')).hexdigest()}"
    return branch


def pr_title(
    ref: str,
    base_branch: str | None = None,
    default_branch: str | None = None,
) -> str:
    if _is_additional_branch(base_branch, default_branch):
        return f"ci: adopt common automation workflows ({ref}; base={base_branch})"
    return f"ci: adopt common automation workflows ({ref})"


def pr_body(
    ref: str,
    commit: str,
    changed_paths: Sequence[str],
    base_branch: str | None = None,
    default_branch: str | None = None,
) -> str:
    paths = "\n".join(f"- `{path}`" for path in sorted(changed_paths))
    base = (
        f"- base branch: `{base_branch}`\n"
        if _is_additional_branch(base_branch, default_branch)
        else ""
    )
    return (
        "Standardize only the catalogued common AI workflow callers.\n\n"
        f"- automation tag: `{ref}`\n- automation commit: `{commit}`\n"
        f"{base}- managed paths:\n{paths}\n\n"
        "Project-specific workflows are unchanged. This PR does not modify secrets. "
        "Merge and recovery use this repository's normal GitHub controls.\n"
    )


def _changed_paths(plan: RenderPlan) -> tuple[str, ...]:
    return tuple(sorted(change.path.as_posix() for change in plan.changes))


def _managed_results(plan: RenderPlan) -> tuple[ManagedResult, ...]:
    """Return the complete final managed set or reject an incomplete plan."""

    if not plan.managed_results:
        raise CommandError("render plan omits complete managed results")
    results = plan.managed_results
    seen: set[PurePosixPath] = set()
    for result in results:
        if result.path in seen:
            raise CommandError(f"duplicate managed result path: {result.path}")
        seen.add(result.path)
        if (result.content is None) != (result.mode is None):
            raise CommandError("managed result mode does not match its content")
        if result.content is not None and result.mode != MANAGED_GIT_MODE:
            raise CommandError("managed result mode is not canonical")
    return tuple(sorted(results, key=lambda result: result.path))


def _object_id(value: str, description: str) -> str:
    if OBJECT_ID.fullmatch(value) is None:
        raise CommandError(f"invalid {description}")
    return value


def _workspace(path: Path, initialize: bool, parser: argparse.ArgumentParser) -> Path:
    """Return a real marked disposable workspace, optionally creating an empty one."""

    created: list[Path] = []
    remove_marker_on_failure = False
    try:
        if path.exists():
            absolute = Path(os.path.abspath(path))
            resolved = path.resolve(strict=True)
            if resolved != absolute or path.is_symlink() or not path.is_dir():
                raise CommandError("workspace must be a real directory")
        else:
            if not initialize:
                raise CommandError(
                    f"workspace is not initialized: {path}; use --initialize-workspace "
                    "only for a dedicated disposable directory"
                )
            absolute = Path(os.path.abspath(path))
            components = (Path(absolute.anchor), *absolute.parents[-2::-1], absolute)
            first_missing = len(components)
            for index, component in enumerate(components):
                try:
                    mode = component.lstat().st_mode
                except FileNotFoundError:
                    first_missing = min(first_missing, index)
                    continue
                except OSError:
                    raise CommandError(
                        "unable to inspect workspace ancestors"
                    ) from None
                if stat.S_ISLNK(mode):
                    raise CommandError("workspace contains a symlink component")
                if not stat.S_ISDIR(mode):
                    raise CommandError("workspace ancestor is not a directory")
                if first_missing != len(components):
                    raise CommandError("workspace ancestry changed while inspected")
            for component in components[first_missing:]:
                component.mkdir()
                created.append(component)
                mode = component.lstat().st_mode
                if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                    raise CommandError("workspace creation was not a real directory")
            resolved = absolute.resolve(strict=True)
            if resolved != absolute:
                raise CommandError("workspace contains a symlink component")
        marker = resolved / WORKSPACE_MARKER
        if marker.exists() or marker.is_symlink():
            mode = marker.lstat().st_mode
            if marker.is_symlink() or not stat.S_ISREG(mode):
                raise CommandError("workspace marker must be a regular file")
        else:
            if not initialize:
                raise CommandError(
                    f"workspace is not initialized: {resolved}; use --initialize-workspace "
                    "only for a dedicated disposable directory"
                )
            if any(resolved.iterdir()):
                raise CommandError(
                    "refusing to mark a nonempty workspace as disposable"
                )
            remove_marker_on_failure = True
            marker.write_text("managed disposable clones only\n", encoding="utf-8")
        return resolved
    except (CommandError, OSError, RuntimeError) as exc:
        if initialize:
            if remove_marker_on_failure:
                try:
                    marker.unlink(missing_ok=True)
                except OSError:
                    pass
            for directory in reversed(created):
                try:
                    directory.rmdir()
                except OSError:
                    pass
        parser.error(str(exc))
    raise AssertionError("argparse.error must exit")


def _make_clone_workspace(
    workspace: Path, prefix: str
) -> tempfile.TemporaryDirectory[str]:
    temporary = tempfile.TemporaryDirectory(prefix=prefix, dir=workspace)
    root = Path(temporary.name)
    (root / WORKSPACE_MARKER).write_text(
        "managed disposable clones only\n", encoding="utf-8"
    )
    return temporary


def _resolve_actionlint(requested: Path | None) -> Path | None:
    candidate = requested
    if candidate is None:
        found = shutil.which("actionlint")
        candidate = Path(found) if found else None
    if candidate is None:
        return None
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        return None
    return resolved


def _copy_github_tree(source: Path, destination: Path) -> None:
    destination.mkdir()
    github = source / ".github"
    if github.exists() or github.is_symlink():
        shutil.copytree(github, destination / ".github", symlinks=True)


def _symlink_component(repo: Path, relative: PurePosixPath) -> Path | None:
    current = repo
    for component in relative.parts:
        current = current / component
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                return current
        except FileNotFoundError:
            return None
        except OSError:
            raise CommandError("unable to inspect a managed path") from None
    return None


def _release_path(relative: PurePosixPath, catalog: WorkflowCatalog) -> bool:
    return (
        not relative.is_absolute()
        and ".." not in relative.parts
        and relative.parts[:1] == (".github",)
        and relative in catalog.managed_paths
    )


def apply_release_plan(
    repo: Path, plan: RenderPlan, catalog: WorkflowCatalog
) -> tuple[PurePosixPath, ...]:
    """Apply a plan using only its verified release-bundle catalog authority."""

    if plan.status not in {"drift", "bootstrap_required"} or not plan.changes:
        raise RolloutError(f"render plan is not actionable: {plan.status}")
    entries = {entry.path: entry for entry in catalog.entries}
    for managed in sorted(catalog.managed_paths):
        if not _release_path(managed, catalog):
            raise RolloutError(f"catalog path is unsafe: {managed}")
        symlink = _symlink_component(repo, managed)
        if symlink is not None:
            raise RolloutError(
                f"managed path contains symlink: {symlink.relative_to(repo)}"
            )

    seen: set[PurePosixPath] = set()
    current: dict[PurePosixPath, bytes | None] = {}
    modes: dict[PurePosixPath, int] = {}
    for change in plan.changes:
        if change.path in seen:
            raise RolloutError(f"duplicate render path: {change.path}")
        seen.add(change.path)
        if not _release_path(change.path, catalog):
            raise RolloutError(
                f"render path is outside the release catalog: {change.path}"
            )
        entry = entries[change.path]
        if change.after is None and entry.kind not in {"optional", "retired"}:
            raise RolloutError(
                f"render path is not catalogued for deletion: {change.path}"
            )
        path = repo.joinpath(*change.path.parts)
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            observed = None
        except OSError:
            raise RolloutError(
                f"unable to inspect managed path: {change.path}"
            ) from None
        else:
            if not stat.S_ISREG(mode):
                raise RolloutError(f"managed path is not a regular file: {change.path}")
            observed = path.read_bytes()
            modes[change.path] = stat.S_IMODE(mode)
        if observed != change.before:
            raise RolloutError(f"{change.path}: changed since rendering")
        current[change.path] = observed

    changed: list[PurePosixPath] = []
    for change in sorted(plan.changes, key=lambda item: item.path):
        desired_mode = 0o644 if change.after is not None else None
        if (
            current[change.path] == change.after
            and (
                desired_mode is None
                or modes.get(change.path) == desired_mode
            )
        ):
            continue
        path = repo.joinpath(*change.path.parts)
        if change.after is None:
            path.unlink()
        elif current[change.path] == change.after:
            os.chmod(path, desired_mode)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, filename = tempfile.mkstemp(
                prefix=f".{path.name}.", dir=path.parent
            )
            temporary = Path(filename)
            try:
                os.fchmod(descriptor, desired_mode)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(change.after)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        changed.append(change.path)
    return tuple(changed)


def _parse_managed_yaml(repo: Path, bundle: ReleaseBundle) -> None:
    for relative in sorted(bundle.catalog.managed_paths):
        if relative.suffix not in {".yml", ".yaml"}:
            continue
        path = repo.joinpath(*relative.parts)
        if not path.exists():
            continue
        if path.is_symlink() or not path.is_file():
            raise CommandError(f"managed YAML is not a regular file: {relative}")
        try:
            document = yaml.load(
                path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader
            )
        except (OSError, UnicodeError, yaml.YAMLError):
            raise CommandError(f"managed YAML is invalid: {relative}") from None
        if not isinstance(document, dict):
            raise CommandError(f"managed YAML must be a mapping: {relative}")


def _run_actionlint(actionlint: Path, repo: Path, bundle: ReleaseBundle) -> None:
    workflows = tuple(
        repo.joinpath(*relative.parts)
        for relative in sorted(bundle.catalog.managed_paths)
        if relative.parts[:2] == (".github", "workflows")
        and relative.suffix in {".yml", ".yaml"}
        and repo.joinpath(*relative.parts).is_file()
    )
    if not workflows:
        return
    try:
        executable = actionlint.resolve(strict=True)
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise OSError
        completed = subprocess.run(
            [
                str(executable),
                "-shellcheck=",
                "-pyflakes=",
                *(str(path) for path in workflows),
            ],
            cwd=repo,
            input=None,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(repo),
                "TMPDIR": str(repo),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
            },
        )
    except (OSError, ValueError):
        raise CommandError("actionlint validation failed") from None
    if completed.returncode or completed.stdout:
        raise CommandError("actionlint validation failed")


def validate_managed_result(
    repo: Path,
    bundle: ReleaseBundle,
    plan: RenderPlan,
    actionlint: Path | None,
    *,
    bootstrap: bool,
) -> None:
    """Validate the proposed managed result in an isolated local Git sandbox."""

    if actionlint is None:
        raise CommandError("actionlint is unavailable")
    with tempfile.TemporaryDirectory(prefix="workflow-managed-result-") as temporary:
        sandbox = Path(temporary) / repo.name
        try:
            _copy_github_tree(repo, sandbox)
            apply_release_plan(sandbox, plan, bundle.catalog)
            _parse_managed_yaml(sandbox, bundle)
            profile = bundle.config.profiles[repo.name]
            result = audit_repository(
                sandbox,
                bundle,
                profile,
                set(plan.required_secrets),
                set(plan.required_variables),
            )
            if result.status != "current":
                raise CommandError(f"catalog audit failed: {result.detail}")
            validate_managed_diff(sandbox, plan)
            _run_actionlint(actionlint, sandbox, bundle)
        except CommandError:
            raise
        except (FleetGitError, RolloutError, OSError, KeyError):
            raise CommandError("managed-result validation failed") from None


def validate_existing_branch(
    snapshot: RepositorySnapshot,
    branch_sha: str,
    base_sha: str,
    plan: RenderPlan,
    *,
    branch: str | None = None,
) -> None:
    """Require one exact renderer-owned commit on the freshly observed base."""

    branch_sha = _object_id(branch_sha, "rollout branch object")
    base_sha = _object_id(base_sha, "default branch object")
    if plan.observed_revision is not None and plan.observed_revision != base_sha:
        raise CommandError("render plan does not match the observed base")
    if branch is not None:
        remote_ref = f"refs/remotes/origin/{branch}"
        git(
            [
                "fetch",
                "--no-recurse-submodules",
                "origin",
                f"refs/heads/{branch}:{remote_ref}",
            ],
            cwd=snapshot.path,
        )
        fetched = _object_id(
            git(["rev-parse", remote_ref], cwd=snapshot.path),
            "fetched branch object",
        )
        if fetched != branch_sha:
            raise CommandError("rollout branch changed while it was inspected")
    parents = git(
        ["rev-list", "--parents", "-n", "1", branch_sha], cwd=snapshot.path
    ).split()
    if parents != [branch_sha, base_sha]:
        raise CommandError("rollout branch parent does not match the observed base")
    observed_paths = tuple(
        sorted(
            filter(
                None,
                git(
                    [
                        "diff-tree",
                        "--no-commit-id",
                        "--name-only",
                        "-r",
                        branch_sha,
                    ],
                    cwd=snapshot.path,
                ).splitlines(),
            )
        )
    )
    if observed_paths != _changed_paths(plan):
        raise CommandError("rollout branch changed paths do not match the render plan")

    _validate_tree_contents(snapshot, branch_sha, plan)


def _validate_tree_contents(
    snapshot: RepositorySnapshot, revision: str, plan: RenderPlan
) -> None:
    for result in _managed_results(plan):
        path = result.path.as_posix()
        observed = git(["ls-tree", "-z", revision, "--", path], cwd=snapshot.path)
        if result.content is None:
            if observed:
                raise CommandError(
                    "rollout tree managed absence does not match the render plan"
                )
            continue
        try:
            text = result.content.decode("utf-8")
        except UnicodeDecodeError:
            raise CommandError("managed result is not UTF-8") from None
        expected_blob = git(
            ["hash-object", "--stdin", "--no-filters"],
            cwd=snapshot.path,
            stdin=text,
        )
        record = observed.removesuffix("\0")
        if "\0" in record or "\t" not in record:
            raise CommandError("rollout tree entry is malformed")
        metadata, observed_path = record.split("\t", 1)
        fields = metadata.split(" ")
        if fields != [MANAGED_GIT_MODE, "blob", expected_blob] or observed_path != path:
            raise CommandError(
                "rollout tree entry must be the canonical regular blob mode"
            )


def validate_commit_tree(
    snapshot: RepositorySnapshot,
    head_sha: str,
    base_sha: str,
    plan: RenderPlan,
) -> None:
    """Attest one proposed commit's parent, paths, blobs, and deletions."""

    head_sha = _object_id(head_sha, "rollout commit")
    base_sha = _object_id(base_sha, "default branch object")
    if plan.observed_revision is not None and plan.observed_revision != base_sha:
        raise CommandError("render plan does not match the observed base")
    parents = git(
        ["rev-list", "--parents", "-n", "1", head_sha], cwd=snapshot.path
    ).split()
    if parents != [head_sha, base_sha]:
        raise CommandError("rollout commit is not a single child of the observed base")
    paths = tuple(
        sorted(
            filter(
                None,
                git(
                    [
                        "diff-tree",
                        "--no-commit-id",
                        "--name-only",
                        "-r",
                        head_sha,
                    ],
                    cwd=snapshot.path,
                ).splitlines(),
            )
        )
    )
    if paths != _changed_paths(plan):
        raise CommandError("rollout commit paths do not match the render plan")
    _validate_tree_contents(snapshot, head_sha, plan)


def _hermetic_git(
    args: Sequence[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    stdin: bytes | None = None,
) -> bytes:
    """Run only filter/signing-free local plumbing with a closed environment."""

    if not args or args[0] not in HERMETIC_GIT_OPERATIONS:
        raise CommandError("hermetic Git operation is not permitted")
    try:
        executable = HERMETIC_GIT.resolve(strict=True)
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise OSError
        completed = subprocess.run(
            [
                str(executable),
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "submodule.recurse=false",
                "-c",
                "commit.gpgSign=false",
                "-c",
                "user.name=workflow-fleet",
                "-c",
                "user.email=workflow-fleet@invalid",
                *args,
            ],
            cwd=cwd,
            input=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
    except (OSError, ValueError):
        raise CommandError("rollout commit construction failed") from None
    if completed.returncode:
        raise CommandError("rollout commit construction failed")
    return completed.stdout.strip()


def _hermetic_environment(root: Path, index: Path) -> dict[str, str]:
    home = root / "home"
    home.mkdir(exist_ok=True)
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home),
        "TMPDIR": str(root),
        "LANG": "C",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_INDEX_FILE": str(index),
        "GIT_AUTHOR_NAME": "workflow-fleet",
        "GIT_AUTHOR_EMAIL": "workflow-fleet@invalid",
        "GIT_AUTHOR_DATE": "@946684800 +0000",
        "GIT_COMMITTER_NAME": "workflow-fleet",
        "GIT_COMMITTER_EMAIL": "workflow-fleet@invalid",
        "GIT_COMMITTER_DATE": "@946684800 +0000",
    }


def _plan_tree(
    repo: Path,
    plan: RenderPlan,
    environment: dict[str, str],
    *,
    after: bool,
) -> str:
    _hermetic_git(["read-tree", "--empty"], cwd=repo, environment=environment)
    for change in sorted(plan.changes, key=lambda item: item.path):
        content = change.after if after else change.before
        if content is None:
            continue
        blob = _object_id(
            _hermetic_git(
                ["hash-object", "-w", "--stdin", "--no-filters"],
                cwd=repo,
                environment=environment,
                stdin=content,
            ).decode("ascii"),
            "managed validation blob",
        )
        _hermetic_git(
            [
                "update-index",
                "--add",
                "--cacheinfo",
                MANAGED_GIT_MODE,
                blob,
                change.path.as_posix(),
            ],
            cwd=repo,
            environment=environment,
        )
    return _object_id(
        _hermetic_git(["write-tree"], cwd=repo, environment=environment).decode(
            "ascii"
        ),
        "managed validation tree",
    )


def validate_managed_diff(repo: Path, plan: RenderPlan) -> None:
    """Check proposed whitespace with exact blobs and no filters or operator config."""

    try:
        with tempfile.TemporaryDirectory(
            prefix="workflow-validation-index-"
        ) as temporary:
            root = Path(temporary)
            environment = _hermetic_environment(root, root / "before-index")
            _hermetic_git(["init", "-q"], cwd=repo, environment=environment)
            before = _plan_tree(repo, plan, environment, after=False)
            environment["GIT_INDEX_FILE"] = str(root / "after-index")
            after = _plan_tree(repo, plan, environment, after=True)
            _hermetic_git(
                ["diff", "--check", before, after, "--"],
                cwd=repo,
                environment=environment,
            )
    except (CommandError, OSError, UnicodeError):
        raise CommandError("managed diff validation failed") from None


def construct_rollout_commit(
    snapshot: RepositorySnapshot,
    base_sha: str,
    ref: str,
    plan: RenderPlan,
    catalog: WorkflowCatalog,
) -> fleet_git.RolloutCommit:
    """Build the exact managed commit through a private filter-free index."""

    base_sha = _object_id(base_sha, "default branch object")
    if plan.observed_revision is not None and plan.observed_revision != base_sha:
        raise RolloutError("render plan does not match the observed base")
    managed_results = _managed_results(plan)
    if {result.path for result in managed_results} != catalog.managed_paths:
        raise RolloutError("render plan does not cover the complete managed catalog")
    results_by_path = {result.path: result for result in managed_results}
    for change in plan.changes:
        result = results_by_path.get(change.path)
        if (
            result is None
            or result.content != change.after
            or (
                change.after_mode is not None
                and result.mode != change.after_mode
            )
        ):
            raise RolloutError("render change does not match the managed result")
    selected_base = _selected_base_branch(snapshot)
    branch = rollout_branch(ref, selected_base, snapshot.default_branch)
    with tempfile.TemporaryDirectory(prefix="workflow-fleet-index-") as temporary:
        root = Path(temporary)
        environment = _hermetic_environment(root, root / "index")
        _hermetic_git(
            ["read-tree", base_sha], cwd=snapshot.path, environment=environment
        )
        base_tree = _object_id(
            _hermetic_git(
                ["write-tree"], cwd=snapshot.path, environment=environment
            ).decode("ascii"),
            "default branch tree",
        )
        apply_release_plan(snapshot.path, plan, catalog)
        managed_oids: dict[PurePosixPath, str] = {}
        for result in managed_results:
            relative = result.path.as_posix()
            if result.content is None:
                _hermetic_git(
                    ["update-index", "--remove", "--", relative],
                    cwd=snapshot.path,
                    environment=environment,
                )
                continue
            blob = _object_id(
                _hermetic_git(
                    ["hash-object", "-w", "--stdin", "--no-filters"],
                    cwd=snapshot.path,
                    environment=environment,
                    stdin=result.content,
                ).decode("ascii"),
                "managed blob",
            )
            managed_oids[result.path] = blob
            _hermetic_git(
                [
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    MANAGED_GIT_MODE,
                    blob,
                    relative,
                ],
                cwd=snapshot.path,
                environment=environment,
            )
        blobs = [
            fleet_git.GitBlob(
                change.path.as_posix(),
                MANAGED_GIT_MODE,
                managed_oids[change.path],
                change.after,
            )
            for change in sorted(plan.changes, key=lambda item: item.path)
            if change.after is not None
        ]
        tree = _object_id(
            _hermetic_git(
                ["write-tree"], cwd=snapshot.path, environment=environment
            ).decode("ascii"),
            "rollout tree",
        )
        head_sha = _object_id(
            _hermetic_git(
                ["commit-tree", tree, "-p", base_sha],
                cwd=snapshot.path,
                environment=environment,
                stdin=(pr_title(ref, selected_base, snapshot.default_branch) + "\n").encode("utf-8"),
            ).decode("ascii"),
            "rollout commit",
        )
        local_ref = f"refs/heads/{branch}"
        try:
            _hermetic_git(
                ["update-ref", local_ref, head_sha, "0" * len(base_sha)],
                cwd=snapshot.path,
                environment=environment,
            )
            _hermetic_git(
                ["symbolic-ref", "HEAD", local_ref],
                cwd=snapshot.path,
                environment=environment,
            )
        except CommandError as exc:
            raise BranchPublishError(str(exc), head_sha) from None
    return fleet_git.RolloutCommit(
        head_sha=head_sha,
        tree_sha=tree,
        base_tree_sha=base_tree,
        base_sha=base_sha,
        message=pr_title(ref, selected_base, snapshot.default_branch),
        blobs=tuple(blobs),
        deletions=tuple(
            change.path.as_posix()
            for change in sorted(plan.changes, key=lambda item: item.path)
            if change.after is None
        ),
    )


def _exact_pr(
    request: PullRequest,
    *,
    base: str,
    branch: str,
    head_repo: str,
    head_sha: str,
    title: str,
    body: str,
) -> bool:
    return (
        request.state == "OPEN"
        and request.base == base
        and request.head == branch
        and request.head_repo == head_repo
        and request.head_sha == head_sha
        and request.title == title
        and request.body == body
    )


def inspect_rollout(
    snapshot: RepositorySnapshot,
    base_sha: str,
    ref: str,
    commit: str,
    plan: RenderPlan,
) -> RolloutInspection:
    """Classify the exact release branch and pull-request identity."""

    selected_base = _selected_base_branch(snapshot)
    branch = rollout_branch(ref, selected_base, snapshot.default_branch)
    changed = _changed_paths(plan)
    title = pr_title(ref, selected_base, snapshot.default_branch)
    body = pr_body(ref, commit, changed, selected_base, snapshot.default_branch)
    branch_sha = fleet_git.remote_branch_sha(snapshot, branch)
    if branch_sha is None:
        requests = fleet_git.list_rollout_prs(
            fleet_git.OWNER, snapshot.path.name, branch
        )
        if requests:
            raise CommandError("pull request history exists without its rollout branch")
        return RolloutInspection("create_branch")

    validate_existing_branch(snapshot, branch_sha, base_sha, plan, branch=branch)
    requests = fleet_git.list_rollout_prs(fleet_git.OWNER, snapshot.path.name, branch)
    if fleet_git.remote_branch_sha(snapshot, branch) != branch_sha:
        raise CommandError("rollout branch changed while pull requests were inspected")
    if not requests:
        return RolloutInspection("create_pr", branch_sha)
    if len(requests) != 1 or not _exact_pr(
        requests[0],
        base=selected_base,
        branch=branch,
        head_repo=f"{fleet_git.OWNER}/{snapshot.path.name}",
        head_sha=branch_sha,
        title=title,
        body=body,
    ):
        raise CommandError("pull request does not exactly match the rollout contract")
    return RolloutInspection("reuse", branch_sha, requests[0].url)


def require_no_current_rollout_branch(snapshot: RepositorySnapshot, ref: str) -> None:
    """Refuse a stale exact release branch when the default is already current."""

    selected_base = _selected_base_branch(snapshot)
    if fleet_git.remote_branch_sha(
        snapshot, rollout_branch(ref, selected_base, snapshot.default_branch)
    ) is not None:
        raise CommandError(
            "rollout branch exists although the observed default is already current"
        )


def attest_pull_request(
    snapshot: RepositorySnapshot,
    ref: str,
    commit: str,
    head_sha: str,
    changed_paths: Sequence[str],
    request: PullRequest,
) -> None:
    """Re-read the release branch and exact PR before reporting publication."""

    selected_base = _selected_base_branch(snapshot)
    branch = rollout_branch(ref, selected_base, snapshot.default_branch)
    if fleet_git.remote_branch_sha(snapshot, branch) != head_sha:
        raise CommandError("rollout branch changed after pull request creation")
    requests = fleet_git.list_rollout_prs(fleet_git.OWNER, snapshot.path.name, branch)
    if (
        len(requests) != 1
        or requests[0].number != request.number
        or requests[0].url != request.url
        or not _exact_pr(
            requests[0],
            base=selected_base,
            branch=branch,
            head_repo=f"{fleet_git.OWNER}/{snapshot.path.name}",
            head_sha=head_sha,
            title=pr_title(ref, selected_base, snapshot.default_branch),
            body=pr_body(
                ref, commit, changed_paths, selected_base, snapshot.default_branch
            ),
        )
    ):
        raise CommandError("pull request attestation failed")


def _render(
    snapshot: RepositorySnapshot,
    bundle: ReleaseBundle,
    repo: str,
    *,
    bootstrap: bool,
) -> RenderPlan:
    return render_repository(
        snapshot.path,
        bundle.canonical,
        bundle.catalog,
        bundle.config.profiles[repo],
        bundle.ref,
        bundle.commit,
        set(snapshot.secret_names),
        set(snapshot.variable_names),
        bootstrap=bootstrap,
        observed_revision=snapshot.base_sha,
    )


def _prepared_outcome(
    repo: str,
    base_sha: str,
    base_branch: str,
    plan: RenderPlan,
    inspection: RolloutInspection | None,
) -> RepoOutcome:
    changed = _changed_paths(plan)
    if plan.status == "current":
        return RepoOutcome(repo, "current", plan.reason, base_sha, base_branch=base_branch)
    assert inspection is not None
    details = {
        "create_branch": "would create an exact branch, commit, and pull request",
        "create_pr": "matching branch exists; would create only the pull request",
        "reuse": "exact open pull request is reusable",
    }
    status = "reusable" if inspection.action == "reuse" else "planned"
    return RepoOutcome(
        repo,
        status,
        details[inspection.action],
        base_sha,
        inspection.branch_sha,
        inspection.pr_url,
        changed,
        base_branch=base_branch,
    )


def prevalidate_repository(
    bundle: ReleaseBundle,
    workspace: Path,
    *,
    repo: str,
    base_branch: str | None = None,
    bootstrap: bool,
    actionlint: Path | None,
) -> PreparedRepo:
    """Perform every read/render/validation gate without remote mutation."""

    base_sha = ""
    selected_base = base_branch or ""
    plan: RenderPlan | None = None
    try:
        if base_branch is None:
            snapshot = fleet_git.clone_default_branch(
                bundle.config.owner, repo, workspace
            )
            base_sha = fleet_git.refetch_default(snapshot)
        else:
            snapshot = fleet_git.clone_branch(
                bundle.config.owner, repo, workspace, base_branch
            )
            base_sha = fleet_git.refetch_branch(snapshot)
        selected_base = _selected_base_branch(snapshot)
        snapshot = replace(snapshot, base_sha=base_sha)
        if bootstrap and selected_base != snapshot.default_branch:
            return PreparedRepo(
                repo,
                "blocked",
                RepoOutcome(
                    repo,
                    "blocked",
                    "bootstrap is permitted only on the default branch",
                    base_sha,
                    base_branch=selected_base,
                ),
                None,
                selected_base,
                base_branch,
            )
        git(["switch", "--detach", base_sha], cwd=snapshot.path)
        plan = _render(snapshot, bundle, repo, bootstrap=bootstrap)
        if plan.status == "blocked":
            return PreparedRepo(
                repo,
                "blocked",
                RepoOutcome(repo, "blocked", plan.reason, base_sha, base_branch=selected_base),
                plan,
                selected_base,
                base_branch,
            )
        if plan.status == "current":
            require_no_current_rollout_branch(snapshot, bundle.ref)
            return PreparedRepo(
                repo,
                "current",
                _prepared_outcome(repo, base_sha, selected_base, plan, None),
                plan,
                selected_base,
                base_branch,
            )
        validate_managed_result(
            snapshot.path, bundle, plan, actionlint, bootstrap=bootstrap
        )
        inspection = inspect_rollout(
            snapshot, base_sha, bundle.ref, bundle.commit, plan
        )
        return PreparedRepo(
            repo,
            inspection.action,
            _prepared_outcome(repo, base_sha, selected_base, plan, inspection),
            plan,
            selected_base,
            base_branch,
        )
    except (CommandError, FleetGitError, RolloutError) as exc:
        return PreparedRepo(
            repo,
            "blocked",
            RepoOutcome(repo, "blocked", str(exc), base_sha, base_branch=selected_base),
            plan,
            selected_base,
            base_branch,
        )
    except (OSError, KeyError, ValueError):
        return PreparedRepo(
            repo,
            "blocked",
            RepoOutcome(
                repo, "blocked", "local prevalidation failed", base_sha,
                base_branch=selected_base,
            ),
            plan,
            selected_base,
            base_branch,
        )


def publish_new_branch(
    snapshot: RepositorySnapshot,
    base_sha: str,
    ref: str,
    commit: str,
    plan: RenderPlan,
    actionlint: Path | None,
    *,
    bundle: ReleaseBundle | None,
) -> str:
    """Create one local commit and publish one previously absent exact branch."""

    selected_base = _selected_base_branch(snapshot)
    branch = rollout_branch(ref, selected_base, snapshot.default_branch)
    if bundle is not None:
        validate_managed_result(
            snapshot.path,
            bundle,
            plan,
            actionlint,
            bootstrap=plan.status == "bootstrap_required",
        )
    if fleet_git.remote_branch_sha(snapshot, branch) is not None:
        raise CommandError("rollout branch appeared before publication")
    if bundle is None:
        raise CommandError("release bundle is required for publication")
    constructed = construct_rollout_commit(
        snapshot, base_sha, ref, plan, bundle.catalog
    )
    head_sha = constructed.head_sha
    try:
        validate_commit_tree(snapshot, head_sha, base_sha, plan)
    except (CommandError, FleetGitError) as exc:
        raise BranchPublishError(str(exc), head_sha) from None
    try:
        published = fleet_git.create_rollout_branch(
            snapshot, branch, commit=constructed
        )
    except FleetGitError as exc:
        raise BranchPublishError(str(exc), head_sha) from None
    if published != head_sha:
        raise BranchPublishError("branch publication failed", head_sha)
    return head_sha


def _publish_repository_fresh(
    bundle: ReleaseBundle,
    workspace: Path,
    *,
    repo: str,
    base_branch: str | None,
    bootstrap: bool,
    actionlint: Path | None,
    progress: PublicationProgress,
) -> RepoOutcome:
    """Refetch/recompute one repository and create or reuse only its exact PR."""

    with _make_clone_workspace(workspace, f".publish-{repo}-") as temporary:
        if base_branch is None:
            snapshot = fleet_git.clone_default_branch(
                bundle.config.owner, repo, Path(temporary)
            )
        else:
            snapshot = fleet_git.clone_branch(
                bundle.config.owner, repo, Path(temporary), base_branch
            )
        selected_base = _selected_base_branch(snapshot)
        progress.base_branch = selected_base
        branch = rollout_branch(bundle.ref, selected_base, snapshot.default_branch)
        progress.stage = "refetch"
        base_sha = (
            fleet_git.refetch_default(snapshot)
            if base_branch is None
            else fleet_git.refetch_branch(snapshot)
        )
        snapshot = replace(snapshot, base_sha=base_sha)
        progress.base_sha = base_sha
        git(["switch", "--detach", base_sha], cwd=snapshot.path)
        progress.stage = "render"
        plan = _render(snapshot, bundle, repo, bootstrap=bootstrap)
        changed = _changed_paths(plan)
        progress.changed_paths = changed
        if plan.status == "blocked":
            raise CommandError(plan.reason)
        if plan.status == "current":
            progress.stage = "branch"
            require_no_current_rollout_branch(snapshot, bundle.ref)
            return RepoOutcome(
                repo, "current", plan.reason, base_sha, stage="complete",
                base_branch=selected_base,
            )
        progress.stage = "validation"
        validate_managed_result(
            snapshot.path, bundle, plan, actionlint, bootstrap=bootstrap
        )
        progress.stage = "branch"
        inspection = inspect_rollout(
            snapshot, base_sha, bundle.ref, bundle.commit, plan
        )
        body = pr_body(
            bundle.ref, bundle.commit, changed, selected_base, snapshot.default_branch
        )
        title = pr_title(bundle.ref, selected_base, snapshot.default_branch)
        if inspection.action == "reuse":
            return RepoOutcome(
                repo,
                "reused",
                "exact open pull request reused",
                base_sha,
                inspection.branch_sha,
                inspection.pr_url,
                changed,
                "complete",
                selected_base,
            )
        if inspection.action == "create_branch":
            try:
                head_sha = publish_new_branch(
                    snapshot,
                    base_sha,
                    bundle.ref,
                    bundle.commit,
                    plan,
                    actionlint,
                    bundle=bundle,
                )
            except BranchPublishError as exc:
                progress.head_sha = exc.head_sha
                return _publication_blocked(repo, progress, str(exc))
        else:
            head_sha = inspection.branch_sha
        progress.head_sha = head_sha
        progress.stage = "pr"
        try:
            request = fleet_git.create_pull_request(
                bundle.config.owner,
                repo,
                selected_base,
                branch,
                head_sha,
                title,
                body,
            )
        except FleetGitError:
            try:
                requests = fleet_git.list_rollout_prs(bundle.config.owner, repo, branch)
                exact = tuple(
                    item
                    for item in requests
                    if _exact_pr(
                        item,
                        base=selected_base,
                        branch=branch,
                        head_repo=f"{bundle.config.owner}/{repo}",
                        head_sha=head_sha,
                        title=title,
                        body=body,
                    )
                )
                if (
                    len(requests) == 1
                    and len(exact) == 1
                    and fleet_git.remote_branch_sha(snapshot, branch) == head_sha
                ):
                    request = exact[0]
                else:
                    return _publication_blocked(
                        repo,
                        progress,
                        "branch published but pull request creation failed",
                    )
            except FleetGitError:
                return _publication_blocked(
                    repo,
                    progress,
                    "branch published but pull request state is unavailable",
                )
        try:
            attest_pull_request(
                snapshot, bundle.ref, bundle.commit, head_sha, changed, request
            )
        except (CommandError, FleetGitError) as exc:
            return _publication_blocked(repo, progress, str(exc), pr_url=request.url)
        detail = (
            "new branch, commit, and pull request created"
            if inspection.action == "create_branch"
            else "matching branch reused; pull request created"
        )
        return RepoOutcome(
            repo,
            "published",
            detail,
            base_sha,
            head_sha,
            request.url,
            changed,
            "complete",
            selected_base,
        )


def publish_repository(
    bundle: ReleaseBundle,
    workspace: Path,
    *,
    repo: str,
    base_branch: str | None = None,
    bootstrap: bool,
    actionlint: Path | None,
) -> RepoOutcome:
    """Publish one repository while preserving only fresh failure metadata."""

    progress = PublicationProgress()
    try:
        return _publish_repository_fresh(
            bundle,
            workspace,
            repo=repo,
            base_branch=base_branch,
            bootstrap=bootstrap,
            actionlint=actionlint,
            progress=progress,
        )
    except (CommandError, FleetGitError, RolloutError) as exc:
        return _publication_blocked(repo, progress, str(exc))
    except Exception:
        return _publication_blocked(repo, progress, "local publication failed")


def _plan_record(prepared: PreparedRepo, commit: str) -> dict[str, object]:
    record: dict[str, object] = asdict(prepared.outcome)
    plan = prepared.plan
    record.update(
        {
            "authoritative": False,
            "release_commit": commit,
            "observed_base": prepared.outcome.base_sha,
            "reason": prepared.outcome.detail,
            "required_secrets": sorted(plan.required_secrets) if plan else [],
            "required_variables": sorted(plan.required_variables) if plan else [],
            "managed_diff_paths": list(_changed_paths(plan)) if plan else [],
        }
    )
    return record


def _preflight_manifest(manifest: Path) -> Path:
    """Prove the report sink is a real writable path before remote mutation."""

    try:
        absolute = Path(os.path.abspath(manifest))
        parent = absolute.parent.resolve(strict=True)
        if parent != Path(os.path.abspath(absolute.parent)) or not parent.is_dir():
            raise OSError
        target = parent / absolute.name
        if target.exists() or target.is_symlink():
            mode = target.lstat().st_mode
            if target.is_symlink() or not stat.S_ISREG(mode):
                raise OSError
        descriptor, filename = tempfile.mkstemp(prefix=f".{target.name}.", dir=parent)
        os.close(descriptor)
        Path(filename).unlink()
        return target
    except (OSError, RuntimeError, ValueError):
        raise CommandError("manifest destination is not a safe writable file") from None


def _write_report(
    manifest: Path,
    outcomes: Sequence[RepoOutcome],
    *,
    mode: str,
    commit: str,
    prepared: Sequence[PreparedRepo],
) -> None:
    if mode == "plan":
        records = [_plan_record(item, commit) for item in prepared]
    else:
        records = [asdict(item) for item in outcomes]
    payload = json.dumps(records, ensure_ascii=False, indent=2) + "\n"
    descriptor, filename = tempfile.mkstemp(
        prefix=f".{manifest.name}.", dir=manifest.parent
    )
    temporary = Path(filename)
    try:
        os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if manifest.is_symlink():
            raise CommandError("manifest destination became a symlink")
        os.replace(temporary, manifest)
    except OSError:
        raise CommandError("manifest write failed") from None
    finally:
        temporary.unlink(missing_ok=True)


def _default_ref() -> str:
    # 릴리즈 정체성의 단일 출처는 scripts/workflow-config.json이다. CLI 기본값을 여기서
    # 읽어, 버전 범프 때 하드코딩 기본값이 직전 릴리즈로 남는 사고(--ref 없이 publish가
    # 소비 리포 19곳을 구버전으로 재핀하는 PR을 여는 것)를 구조적으로 없앤다.
    config_path = ROOT / "scripts" / "workflow-config.json"
    return str(json.loads(config_path.read_text(encoding="utf-8"))["automation_ref"])


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--automation", type=Path, default=ROOT)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--ref", default=_default_ref())
    parser.add_argument("--mode", choices=("plan", "publish"), default="plan")
    parser.add_argument("--repo", action="append", default=[])
    parser.add_argument("--initialize-workspace", action="store_true")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--bootstrap-repo")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--actionlint", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)

    if args.mode == "publish" and not args.confirm:
        parser.error("--mode publish requires --confirm")
    if args.mode == "publish" and not args.repo:
        parser.error("--mode publish requires at least one explicit --repo")
    if len(args.repo) != len(set(args.repo)):
        parser.error("--repo values must be unique")
    if VERSION_REF.fullmatch(args.ref) is None:
        parser.error(f"invalid release ref: {args.ref}")
    if args.bootstrap_repo is not None and (
        len(args.repo) != 1 or args.repo[0] != args.bootstrap_repo
    ):
        parser.error(
            "--bootstrap-repo requires exactly one matching bootstrap-allowed --repo"
        )
    workspace = _workspace(args.workspace, args.initialize_workspace, parser)
    actionlint = _resolve_actionlint(args.actionlint)
    manifest = _preflight_manifest(args.manifest or workspace / "rollout-manifest.json")

    with materialize_release_bundle(
        args.automation, args.ref, remote="origin"
    ) as bundle:
        configured = set(bundle.config.profiles)
        repos = args.repo or sorted(configured)
        unknown = sorted(set(repos) - configured)
        if unknown:
            parser.error(f"repositories not in release bundle: {', '.join(unknown)}")
        if (
            args.bootstrap_repo is not None
            and not bundle.config.profiles[args.bootstrap_repo].bootstrap_allowed
        ):
            parser.error("release profile does not allow bootstrap")
        rollout_branch(bundle.ref)  # parse the default identity before prevalidation

        prepared: list[PreparedRepo] = []
        for repo in repos:
            profile = bundle.config.profiles[repo]
            for target_branch in configured_branch_targets(profile):
                with _make_clone_workspace(
                    workspace, f".prevalidate-{repo}-"
                ) as temporary:
                    prepared.append(
                        prevalidate_repository(
                            bundle,
                            Path(temporary),
                            repo=repo,
                            base_branch=target_branch,
                            bootstrap=(
                                args.bootstrap_repo == repo and target_branch is None
                            ),
                            actionlint=actionlint,
                        )
                    )

        if args.mode == "plan" or any(
            item.outcome.status == "blocked" for item in prepared
        ):
            outcomes = [item.outcome for item in prepared]
        else:
            outcomes: list[RepoOutcome] = []
            _write_report(
                manifest,
                outcomes,
                mode=args.mode,
                commit=bundle.commit,
                prepared=prepared,
            )
            for item in prepared:
                try:
                    outcomes.append(
                        publish_repository(
                            bundle,
                            workspace,
                            repo=item.repo,
                            base_branch=item.target_branch,
                            bootstrap=(
                                args.bootstrap_repo == item.repo
                                and item.target_branch is None
                            ),
                            actionlint=actionlint,
                        )
                    )
                except (CommandError, FleetGitError, RolloutError) as exc:
                    outcomes.append(
                        RepoOutcome(
                            item.repo,
                            "blocked",
                            f"unexpected stage failed: {exc}",
                            stage="unexpected",
                        )
                    )
                except Exception:
                    outcomes.append(
                        RepoOutcome(
                            item.repo,
                            "blocked",
                            "unexpected stage failed: local publication failed",
                            stage="unexpected",
                        )
                    )
                _write_report(
                    manifest,
                    outcomes,
                    mode=args.mode,
                    commit=bundle.commit,
                    prepared=prepared,
                )

        _write_report(
            manifest,
            outcomes,
            mode=args.mode,
            commit=bundle.commit,
            prepared=prepared,
        )
        for item in outcomes:
            suffix = f" {item.pr_url}" if item.pr_url else ""
            target = item.base_branch or "unknown"
            print(
                f"{item.status.upper():9} {item.repo}[{target}]: "
                f"{item.detail}{suffix}"
            )
        blocked = sum(item.status == "blocked" for item in outcomes)
        print(
            f"SUMMARY mode={args.mode} ref={bundle.ref} commit={bundle.commit} "
            f"total={len(outcomes)} blocked={blocked} manifest={manifest}"
        )
        return 1 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
