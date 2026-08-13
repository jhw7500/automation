#!/usr/bin/env python3
"""Restricted Git and GitHub operations for workflow-fleet PR rollout.

This module deliberately exposes only repository reads, prerequisite inventory reads,
new-branch publication, and pull-request creation.  It has no merge, revert, or
repository secret/variable mutation surface.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
from typing import Sequence


OWNER = "jhw7500"
PROVIDER_KEYS = frozenset(
    {
        "CLAUDE_CODE_OAUTH_TOKEN",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "ZHIPU_API_KEY",
        "APP_PRIVATE_KEY",
    }
)
WORKSPACE_MARKER = ".automation-fleet-workspace"
_REPO_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z")
_OBJECT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_PULL_URL = re.compile(
    r"https://github\.com/jhw7500/([A-Za-z0-9][A-Za-z0-9._-]{0,99})/pull/([1-9][0-9]*)\Z"
)
_GIT_PREFIX = (
    "git",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "submodule.recurse=false",
)


class FleetGitError(RuntimeError):
    """A sanitized restricted-adapter failure."""


@dataclass(frozen=True)
class RepositorySnapshot:
    path: Path
    default_branch: str
    base_sha: str
    secret_names: frozenset[str]
    variable_names: frozenset[str]


@dataclass(frozen=True)
class PullRequest:
    number: int
    url: str
    state: str
    base: str
    head: str
    title: str
    body: str


__all__ = (
    "FleetGitError",
    "PullRequest",
    "RepositorySnapshot",
    "clone_default_branch",
    "create_pull_request",
    "list_rollout_prs",
    "push_new_branch",
    "refetch_default",
    "remote_branch_sha",
)


def child_env() -> dict[str, str]:
    """Return the operator environment without model/provider credentials."""

    return {
        key: value for key, value in os.environ.items() if key not in PROVIDER_KEYS
    }


def run(
    args: Sequence[str], *, cwd: Path | None = None, stdin: str | None = None
) -> str:
    """Run a child process while keeping arguments and output out of failures."""

    if not args:
        raise FleetGitError("command is empty")
    completed = subprocess.run(
        list(args),
        cwd=cwd,
        input=stdin,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=child_env(),
    )
    if completed.returncode:
        raise FleetGitError(
            f"command failed ({Path(args[0]).name}, rc={completed.returncode})"
        )
    return completed.stdout.strip()


def _git(args: Sequence[str], *, cwd: Path | None = None) -> str:
    return run([*_GIT_PREFIX, *args], cwd=cwd)


def _json(args: Sequence[str]) -> object:
    output = run(args)
    try:
        return json.loads(output or "null")
    except json.JSONDecodeError as exc:
        raise FleetGitError("GitHub returned malformed JSON") from exc


def _validate_target(owner: str, repo: str) -> None:
    if owner != OWNER:
        raise FleetGitError("repository owner is not permitted")
    if not _REPO_NAME.fullmatch(repo) or repo in {".", ".."}:
        raise FleetGitError("repository name is invalid")


def _validate_branch(branch: str) -> None:
    forbidden = " ~^:?*[\\"
    components = branch.split("/")
    if (
        not branch
        or branch.startswith(("-", "/"))
        or branch.endswith(("/", "."))
        or "//" in branch
        or ".." in branch
        or "@{" in branch
        or any(character in branch for character in forbidden)
        or any(ord(character) < 32 or ord(character) == 127 for character in branch)
        or any(
            not component
            or component.startswith(".")
            or component.endswith(".lock")
            for component in components
        )
    ):
        raise FleetGitError("branch name is invalid")


def _validate_object_id(value: str) -> str:
    if not _OBJECT_ID.fullmatch(value):
        raise FleetGitError("Git returned an invalid object identifier")
    return value


def _https_url(repo: str) -> str:
    return f"https://github.com/{OWNER}/{repo}.git"


def _ssh_url(repo: str) -> str:
    return f"git@github.com:{OWNER}/{repo}.git"


def _ssh_scheme_url(repo: str) -> str:
    return f"ssh://git@github.com/{OWNER}/{repo}.git"


def _permitted_urls(repo: str) -> frozenset[str]:
    urls = {_https_url(repo), _ssh_url(repo), _ssh_scheme_url(repo)}
    return frozenset({*urls, *(url.removesuffix(".git") for url in urls)})


def _clone_url(repo: str, reported_url: object) -> str:
    allowed = {
        _https_url(repo): _https_url(repo),
        _https_url(repo).removesuffix(".git"): _https_url(repo),
        _ssh_url(repo): _ssh_url(repo),
        _ssh_url(repo).removesuffix(".git"): _ssh_url(repo),
        _ssh_scheme_url(repo): _ssh_scheme_url(repo),
        _ssh_scheme_url(repo).removesuffix(".git"): _ssh_scheme_url(repo),
    }
    if not isinstance(reported_url, str) or reported_url not in allowed:
        raise FleetGitError("repository metadata URL is not permitted")
    return allowed[reported_url]


def _resolved_without_symlinks(path: Path, kind: str) -> Path:
    try:
        absolute = Path(os.path.abspath(path))
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError, TypeError) as exc:
        raise FleetGitError(f"{kind} is not a real path") from exc
    if resolved != absolute:
        raise FleetGitError(f"{kind} contains a symlink component")
    return resolved


def _validate_repository_path(path: Path, repo: str) -> Path:
    resolved = _resolved_without_symlinks(path, "repository root")
    workspace = _resolved_without_symlinks(resolved.parent, "workspace")
    if resolved.parent != workspace or resolved.name != repo:
        raise FleetGitError("repository is not a direct workspace child")
    if not workspace.is_dir() or workspace.is_symlink():
        raise FleetGitError("workspace is not a real directory")
    marker = workspace / WORKSPACE_MARKER
    try:
        marker_mode = marker.lstat().st_mode
    except OSError as exc:
        raise FleetGitError("workspace marker is unavailable") from exc
    if (
        marker.is_symlink()
        or not stat.S_ISREG(marker_mode)
        or _resolved_without_symlinks(marker, "workspace marker") != marker
    ):
        raise FleetGitError("workspace marker is not a regular file")
    git_directory = resolved / ".git"
    if (
        not resolved.is_dir()
        or resolved.is_symlink()
        or not git_directory.is_dir()
        or git_directory.is_symlink()
        or _resolved_without_symlinks(git_directory, "Git directory")
        != git_directory
    ):
        raise FleetGitError("repository does not have clone-shaped Git state")
    return resolved


def _verify_origin(path: Path, repo: str) -> None:
    permitted = _permitted_urls(repo)
    for args in (
        ["remote", "get-url", "--all", "origin"],
        ["remote", "get-url", "--push", "--all", "origin"],
    ):
        urls = _git(args, cwd=path).splitlines()
        if not urls or any(url not in permitted for url in urls):
            raise FleetGitError(
                "repository origin does not match the permitted target"
            )


def _snapshot_repo(snapshot: RepositorySnapshot) -> tuple[str, Path]:
    if not isinstance(snapshot.path, Path):
        raise FleetGitError("repository path is invalid")
    repo = snapshot.path.name
    _validate_target(OWNER, repo)
    _validate_branch(snapshot.default_branch)
    _validate_object_id(snapshot.base_sha)
    return repo, _validate_repository_path(snapshot.path, repo)


def _inventory(owner: str, repo: str, kind: str) -> frozenset[str]:
    data = _json(
        ["gh", kind, "list", "-R", f"{owner}/{repo}", "--json", "name"]
    )
    if not isinstance(data, list):
        raise FleetGitError("GitHub returned malformed prerequisite inventory")
    names: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            raise FleetGitError("GitHub returned malformed prerequisite inventory")
        name = item.get("name")
        if not isinstance(name, str) or not name:
            raise FleetGitError("GitHub returned malformed prerequisite inventory")
        names.add(name)
    return frozenset(names)


def clone_default_branch(
    owner: str, repo: str, workspace: Path
) -> RepositorySnapshot:
    """Clone exactly one permitted repository and inventory prerequisite names."""

    _validate_target(owner, repo)
    try:
        workspace = _resolved_without_symlinks(workspace, "workspace")
        marker_mode = (workspace / WORKSPACE_MARKER).lstat().st_mode
    except FleetGitError:
        raise
    except OSError as exc:
        raise FleetGitError("workspace is not marked for fleet automation") from exc
    if (
        workspace.is_symlink()
        or not workspace.is_dir()
        or (workspace / WORKSPACE_MARKER).is_symlink()
        or not stat.S_ISREG(marker_mode)
    ):
        raise FleetGitError("workspace is not marked for fleet automation")
    target = workspace / repo
    if target.is_symlink() or target.exists():
        raise FleetGitError("clone target must be a new non-symlink path")

    metadata = _json(
        [
            "gh",
            "repo",
            "view",
            f"{owner}/{repo}",
            "--json",
            "defaultBranchRef,url",
        ]
    )
    if not isinstance(metadata, dict):
        raise FleetGitError("GitHub returned malformed repository metadata")
    default_ref = metadata.get("defaultBranchRef")
    if not isinstance(default_ref, dict) or not isinstance(
        default_ref.get("name"), str
    ):
        raise FleetGitError("GitHub returned malformed repository metadata")
    default_branch = default_ref["name"]
    _validate_branch(default_branch)
    clone_url = _clone_url(repo, metadata.get("url"))

    _git(
        [
            "clone",
            "--no-recurse-submodules",
            "--single-branch",
            "--branch",
            default_branch,
            clone_url,
            str(target),
        ]
    )
    target = _validate_repository_path(target, repo)
    _verify_origin(target, repo)
    base_sha = _validate_object_id(_git(["rev-parse", "HEAD"], cwd=target))
    secret_names = _inventory(owner, repo, "secret")
    variable_names = _inventory(owner, repo, "variable")
    return RepositorySnapshot(
        path=target,
        default_branch=default_branch,
        base_sha=base_sha,
        secret_names=secret_names,
        variable_names=variable_names,
    )


def refetch_default(snapshot: RepositorySnapshot) -> str:
    """Fetch and return the current permitted origin's default-branch SHA."""

    repo, path = _snapshot_repo(snapshot)
    _verify_origin(path, repo)
    _git(
        [
            "fetch",
            "--no-recurse-submodules",
            "origin",
            snapshot.default_branch,
        ],
        cwd=path,
    )
    return _validate_object_id(
        _git(
            ["rev-parse", f"refs/remotes/origin/{snapshot.default_branch}"],
            cwd=path,
        )
    )


def remote_branch_sha(snapshot: RepositorySnapshot, branch: str) -> str | None:
    """Return a remote branch SHA without fetching or changing local state."""

    _validate_branch(branch)
    repo, path = _snapshot_repo(snapshot)
    _verify_origin(path, repo)
    ref = f"refs/heads/{branch}"
    output = _git(["ls-remote", "--heads", "origin", ref], cwd=path)
    if not output:
        return None
    lines = output.splitlines()
    if len(lines) != 1:
        raise FleetGitError("Git returned an ambiguous remote branch")
    fields = lines[0].split()
    if len(fields) != 2 or fields[1] != ref:
        raise FleetGitError("Git returned a malformed remote branch")
    return _validate_object_id(fields[0])


def push_new_branch(snapshot: RepositorySnapshot, branch: str) -> str:
    """Create and publish a branch only when no remote branch exists."""

    _validate_branch(branch)
    if branch == snapshot.default_branch:
        raise FleetGitError("default branch publication is not permitted")
    repo, path = _snapshot_repo(snapshot)
    if remote_branch_sha(snapshot, branch) is not None:
        raise FleetGitError("remote branch already exists")
    fresh_base = refetch_default(snapshot)
    _git(["switch", "-c", branch, fresh_base], cwd=path)
    head_sha = _validate_object_id(_git(["rev-parse", "HEAD"], cwd=path))
    _verify_origin(path, repo)
    _git(
        ["push", "--set-upstream", "origin", f"HEAD:refs/heads/{branch}"],
        cwd=path,
    )
    return head_sha


def _pull_request(
    item: object, *, owner: str, repo: str, expected_head: str
) -> PullRequest:
    if not isinstance(item, dict):
        raise FleetGitError("GitHub returned malformed pull request metadata")
    fields = {
        "number": int,
        "url": str,
        "state": str,
        "baseRefName": str,
        "headRefName": str,
        "title": str,
        "body": str,
        "isDraft": bool,
    }
    if any(
        key not in item or not isinstance(item[key], expected)
        for key, expected in fields.items()
    ) or "mergedAt" not in item:
        raise FleetGitError("GitHub returned malformed pull request metadata")
    number = item["number"]
    if isinstance(number, bool) or number < 1:
        raise FleetGitError("GitHub returned malformed pull request metadata")
    expected_url = f"https://github.com/{owner}/{repo}/pull/{number}"
    state = item["state"]
    base = item["baseRefName"]
    merged_at = item.get("mergedAt")
    if (
        item["url"] != expected_url
        or state not in {"OPEN", "CLOSED", "MERGED"}
        or not base
        or item["headRefName"] != expected_head
        or (merged_at is not None and not isinstance(merged_at, str))
        or (state == "MERGED" and not isinstance(merged_at, str))
        or (state == "MERGED" and not merged_at)
        or (state != "MERGED" and merged_at is not None)
    ):
        raise FleetGitError("GitHub returned inconsistent pull request metadata")
    _validate_branch(base)
    return PullRequest(
        number=number,
        url=item["url"],
        state=state,
        base=base,
        head=item["headRefName"],
        title=item["title"],
        body=item["body"],
    )


def list_rollout_prs(owner: str, repo: str, branch: str) -> tuple[PullRequest, ...]:
    """List every PR state for one exact rollout head branch."""

    _validate_target(owner, repo)
    _validate_branch(branch)
    data = _json(
        [
            "gh",
            "pr",
            "list",
            "-R",
            f"{owner}/{repo}",
            "--head",
            branch,
            "--state",
            "all",
            "--json",
            "number,url,state,baseRefName,headRefName,title,body,isDraft,mergedAt",
        ]
    )
    if not isinstance(data, list):
        raise FleetGitError("GitHub returned malformed pull request list")
    return tuple(
        _pull_request(item, owner=owner, repo=repo, expected_head=branch)
        for item in data
    )


def create_pull_request(
    owner: str,
    repo: str,
    base: str,
    head: str,
    title: str,
    body: str,
) -> PullRequest:
    """Create, but never merge, a pull request using a private body file."""

    _validate_target(owner, repo)
    _validate_branch(base)
    _validate_branch(head)
    descriptor, filename = tempfile.mkstemp(prefix="workflow-fleet-pr-", text=True)
    body_path = Path(filename)
    try:
        os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(body)
        url = run(
            [
                "gh",
                "pr",
                "create",
                "-R",
                f"{owner}/{repo}",
                "--base",
                base,
                "--head",
                head,
                "--title",
                title,
                "--body-file",
                str(body_path),
            ]
        )
    finally:
        body_path.unlink(missing_ok=True)

    match = _PULL_URL.fullmatch(url)
    if match is None or match.group(1) != repo:
        raise FleetGitError("GitHub returned an unexpected pull request URL")
    return PullRequest(
        number=int(match.group(2)),
        url=url,
        state="OPEN",
        base=base,
        head=head,
        title=title,
        body=body,
    )
