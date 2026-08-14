#!/usr/bin/env python3
"""Restricted Git and GitHub operations for workflow-fleet PR rollout.

This module deliberately exposes only repository reads, prerequisite inventory reads,
atomic new-ref publication, and pull-request creation.  It has no ordinary Git push,
merge, revert, or repository secret/variable mutation surface.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
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
_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_ROLLOUT_BRANCH = re.compile(r"automation/common-workflows-v[0-9]+(?:\.[0-9]+)+\Z")
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
    head_repo: str
    head_sha: str
    title: str
    body: str


@dataclass(frozen=True)
class GitBlob:
    path: str
    mode: str
    sha: str
    content: bytes


@dataclass(frozen=True)
class RolloutCommit:
    head_sha: str
    tree_sha: str
    base_tree_sha: str
    base_sha: str
    message: str
    blobs: tuple[GitBlob, ...]
    deletions: tuple[str, ...]


__all__ = (
    "FleetGitError",
    "GitBlob",
    "PullRequest",
    "RepositorySnapshot",
    "RolloutCommit",
    "clone_default_branch",
    "create_rollout_branch",
    "create_pull_request",
    "list_rollout_prs",
    "refetch_default",
    "remote_branch_sha",
)


def child_env() -> dict[str, str]:
    """Return the operator environment without model/provider credentials."""

    return {key: value for key, value in os.environ.items() if key not in PROVIDER_KEYS}


def github_env() -> dict[str, str]:
    """Return only GitHub CLI runtime/config and intended credential variables."""

    environment = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }
    for key in (
        "HOME",
        "XDG_CONFIG_HOME",
        "GH_CONFIG_DIR",
    ):
        value = os.environ.get(key)
        if value is not None:
            environment[key] = value
    token_key = "GH_TOKEN" if os.environ.get("GH_TOKEN") else "GITHUB_TOKEN"
    token = os.environ.get(token_key)
    if token:
        environment[token_key] = token
    return environment


def run(
    args: Sequence[str], *, cwd: Path | None = None, stdin: str | None = None
) -> str:
    """Run a child process while keeping arguments and output out of failures."""

    if not args:
        raise FleetGitError("command is empty")
    operation = Path(args[0]).name
    if operation not in {"git", "gh"}:
        operation = "child"
    launch_failed = False
    try:
        completed = subprocess.run(
            list(args),
            cwd=cwd,
            input=stdin,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=github_env() if operation == "gh" else child_env(),
        )
    except (OSError, ValueError):
        launch_failed = True
    if launch_failed:
        raise FleetGitError(f"command failed ({operation}, rc=unavailable)") from None
    if completed.returncode:
        raise FleetGitError(f"command failed ({operation}, rc={completed.returncode})")
    return completed.stdout.strip()


def _git(args: Sequence[str], *, cwd: Path | None = None) -> str:
    return run([*_GIT_PREFIX, *args], cwd=cwd)


def _json(args: Sequence[str]) -> object:
    output = run(args)
    malformed = False
    try:
        data = json.loads(output or "null")
    except json.JSONDecodeError:
        malformed = True
    if malformed:
        raise FleetGitError("GitHub returned malformed JSON") from None
    return data


def _github_post(repo: str, collection: str, body: object) -> object:
    """POST one closed Git Data schema through JSON stdin, never argv fields."""

    _validate_target(OWNER, repo)
    if collection not in {"blobs", "trees", "commits", "refs"}:
        raise FleetGitError("GitHub operation is not permitted")
    output = run(
        [
            "gh",
            "api",
            "--hostname",
            "github.com",
            "--method",
            "POST",
            f"repos/{OWNER}/{repo}/git/{collection}",
            "--input",
            "-",
        ],
        stdin=json.dumps(body, separators=(",", ":"), sort_keys=True) + "\n",
    )
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        raise FleetGitError("GitHub returned malformed JSON") from None


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
            not component or component.startswith(".") or component.endswith(".lock")
            for component in components
        )
    ):
        raise FleetGitError("branch name is invalid")


def _validate_object_id(value: str) -> str:
    if not _OBJECT_ID.fullmatch(value):
        raise FleetGitError("Git returned an invalid object identifier")
    return value


def _validate_sha1(value: str, description: str) -> str:
    if not isinstance(value, str) or _SHA1.fullmatch(value) is None:
        raise FleetGitError(f"{description} is invalid")
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
    invalid = False
    try:
        absolute = Path(os.path.abspath(path))
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError, TypeError):
        invalid = True
    if invalid:
        raise FleetGitError(f"{kind} is not a real path") from None
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
    marker_mode: int | None = None
    try:
        marker_mode = marker.lstat().st_mode
    except OSError:
        pass
    if marker_mode is None:
        raise FleetGitError("workspace marker is unavailable") from None
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
        or _resolved_without_symlinks(git_directory, "Git directory") != git_directory
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
            raise FleetGitError("repository origin does not match the permitted target")


def _snapshot_repo(snapshot: RepositorySnapshot) -> tuple[str, Path]:
    if not isinstance(snapshot.path, Path):
        raise FleetGitError("repository path is invalid")
    repo = snapshot.path.name
    _validate_target(OWNER, repo)
    _validate_branch(snapshot.default_branch)
    _validate_object_id(snapshot.base_sha)
    return repo, _validate_repository_path(snapshot.path, repo)


def _inventory(owner: str, repo: str, kind: str) -> frozenset[str]:
    data = _json(["gh", kind, "list", "-R", f"{owner}/{repo}", "--json", "name"])
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


def clone_default_branch(owner: str, repo: str, workspace: Path) -> RepositorySnapshot:
    """Clone exactly one permitted repository and inventory prerequisite names."""

    _validate_target(owner, repo)
    workspace = _resolved_without_symlinks(workspace, "workspace")
    marker_mode: int | None = None
    try:
        marker_mode = (workspace / WORKSPACE_MARKER).lstat().st_mode
    except OSError:
        pass
    if marker_mode is None:
        raise FleetGitError("workspace is not marked for fleet automation") from None
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


def _exact_url(value: object, expected: str) -> bool:
    return isinstance(value, str) and value == expected


def _validate_blob_response(repo: str, expected: GitBlob, response: object) -> None:
    root = f"https://api.github.com/repos/{OWNER}/{repo}/git"
    if not isinstance(response, dict) or (
        response.get("sha") != expected.sha
        or not _exact_url(response.get("url"), f"{root}/blobs/{expected.sha}")
    ):
        raise FleetGitError("GitHub returned an invalid blob identity")


def _validate_tree_response(repo: str, expected: str, response: object) -> None:
    root = f"https://api.github.com/repos/{OWNER}/{repo}/git"
    if (
        not isinstance(response, dict)
        or response.get("sha") != expected
        or not _exact_url(response.get("url"), f"{root}/trees/{expected}")
        or response.get("truncated") is not False
        or not isinstance(response.get("tree"), list)
    ):
        raise FleetGitError("GitHub returned an invalid tree identity")


def _commit_identity() -> dict[str, str]:
    return {
        "name": "workflow-fleet",
        "email": "workflow-fleet@invalid",
        "date": "2000-01-01T00:00:00Z",
    }


def _validate_commit_response(
    repo: str, commit: RolloutCommit, response: object
) -> None:
    root = f"https://api.github.com/repos/{OWNER}/{repo}/git"
    if not isinstance(response, dict):
        raise FleetGitError("GitHub returned an invalid commit identity")
    tree = response.get("tree")
    parents = response.get("parents")
    if (
        response.get("sha") != commit.head_sha
        or not _exact_url(response.get("url"), f"{root}/commits/{commit.head_sha}")
        or response.get("message") != commit.message
        or response.get("author") != _commit_identity()
        or response.get("committer") != _commit_identity()
        or not isinstance(tree, dict)
        or tree.get("sha") != commit.tree_sha
        or not _exact_url(tree.get("url"), f"{root}/trees/{commit.tree_sha}")
        or not isinstance(parents, list)
        or len(parents) != 1
        or not isinstance(parents[0], dict)
        or parents[0].get("sha") != commit.base_sha
        or not _exact_url(parents[0].get("url"), f"{root}/commits/{commit.base_sha}")
    ):
        raise FleetGitError("GitHub returned an invalid commit identity")


def _validate_ref_response(
    repo: str, branch: str, head_sha: str, response: object
) -> None:
    root = f"https://api.github.com/repos/{OWNER}/{repo}/git"
    ref = f"refs/heads/{branch}"
    if not isinstance(response, dict):
        raise FleetGitError("GitHub returned an invalid ref identity")
    linked = response.get("object")
    if (
        response.get("ref") != ref
        or not _exact_url(response.get("url"), f"{root}/refs/heads/{branch}")
        or not isinstance(linked, dict)
        or linked.get("sha") != head_sha
        or linked.get("type") != "commit"
        or not _exact_url(linked.get("url"), f"{root}/commits/{head_sha}")
    ):
        raise FleetGitError("GitHub returned an invalid ref identity")


def _validate_rollout_commit(commit: RolloutCommit, branch: str) -> None:
    if (
        not isinstance(commit, RolloutCommit)
        or not isinstance(commit.message, str)
        or not isinstance(commit.blobs, tuple)
        or not isinstance(commit.deletions, tuple)
    ):
        raise FleetGitError("rollout commit identity is invalid")
    for value, description in (
        (commit.head_sha, "rollout commit identity"),
        (commit.tree_sha, "rollout tree identity"),
        (commit.base_tree_sha, "default tree identity"),
        (commit.base_sha, "default commit identity"),
    ):
        _validate_sha1(value, description)
    ref = branch.removeprefix("automation/common-workflows-")
    expected_message = f"ci: adopt common automation workflows ({ref})"
    raw_commit = (
        f"tree {commit.tree_sha}\n"
        f"parent {commit.base_sha}\n"
        "author workflow-fleet <workflow-fleet@invalid> 946684800 +0000\n"
        "committer workflow-fleet <workflow-fleet@invalid> 946684800 +0000\n"
        f"\n{commit.message}\n"
    ).encode("utf-8")
    computed_head = hashlib.sha1(
        f"commit {len(raw_commit)}\0".encode("ascii") + raw_commit
    ).hexdigest()
    if commit.message != expected_message or commit.head_sha != computed_head:
        raise FleetGitError("rollout commit message is invalid")
    paths: set[str] = set()
    for blob in commit.blobs:
        if not isinstance(blob, GitBlob) or not isinstance(blob.content, bytes):
            raise FleetGitError("rollout blob is invalid")
        _validate_sha1(blob.sha, "rollout blob identity")
        computed = hashlib.sha1(
            f"blob {len(blob.content)}\0".encode("ascii") + blob.content
        ).hexdigest()
        if (
            blob.sha != computed
            or blob.mode != "100644"
            or not blob.path
            or blob.path in paths
        ):
            raise FleetGitError("rollout blob is invalid")
        _validate_branch_path(blob.path)
        paths.add(blob.path)
    for path in commit.deletions:
        if not isinstance(path, str) or not path or path in paths:
            raise FleetGitError("rollout deletion is invalid")
        _validate_branch_path(path)
        paths.add(path)
    if not paths:
        raise FleetGitError("rollout commit has no managed changes")


def _validate_branch_path(path: str) -> None:
    components = path.split("/")
    if (
        path.startswith("/")
        or path.endswith("/")
        or any(component in {"", ".", ".."} for component in components)
        or "\x00" in path
    ):
        raise FleetGitError("rollout path is invalid")


def create_rollout_branch(
    snapshot: RepositorySnapshot,
    branch: str,
    *,
    commit: RolloutCommit,
) -> str:
    """Atomically create one exact rollout ref through the closed Git Data API."""

    _validate_branch(branch)
    if _ROLLOUT_BRANCH.fullmatch(branch) is None or branch == snapshot.default_branch:
        raise FleetGitError("rollout branch publication is not permitted")
    repo, path = _snapshot_repo(snapshot)
    _verify_origin(path, repo)
    _validate_rollout_commit(commit, branch)
    if commit.base_sha != snapshot.base_sha:
        raise FleetGitError("rollout base does not match the repository snapshot")

    tree_entries: list[dict[str, object]] = []
    for blob in commit.blobs:
        response = _github_post(
            repo,
            "blobs",
            {
                "content": base64.b64encode(blob.content).decode("ascii"),
                "encoding": "base64",
            },
        )
        _validate_blob_response(repo, blob, response)
        tree_entries.append(
            {
                "mode": blob.mode,
                "path": blob.path,
                "sha": blob.sha,
                "type": "blob",
            }
        )
    tree_entries.extend(
        {"mode": "100644", "path": path, "sha": None, "type": "blob"}
        for path in commit.deletions
    )
    tree_response = _github_post(
        repo,
        "trees",
        {"base_tree": commit.base_tree_sha, "tree": tree_entries},
    )
    _validate_tree_response(repo, commit.tree_sha, tree_response)
    commit_response = _github_post(
        repo,
        "commits",
        {
            "author": _commit_identity(),
            "committer": _commit_identity(),
            "message": commit.message,
            "parents": [commit.base_sha],
            "tree": commit.tree_sha,
        },
    )
    _validate_commit_response(repo, commit, commit_response)
    try:
        ref_response = _github_post(
            repo,
            "refs",
            {"ref": f"refs/heads/{branch}", "sha": commit.head_sha},
        )
    except FleetGitError:
        try:
            observed = remote_branch_sha(snapshot, branch)
        except FleetGitError:
            raise FleetGitError("atomic ref creation result is unavailable") from None
        if observed != commit.head_sha:
            raise FleetGitError("atomic ref creation failed") from None
        return commit.head_sha
    _validate_ref_response(repo, branch, commit.head_sha, ref_response)
    if remote_branch_sha(snapshot, branch) != commit.head_sha:
        raise FleetGitError("atomic ref creation attestation failed")
    return commit.head_sha


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
        "headRefOid": str,
        "headRepository": dict,
        "headRepositoryOwner": dict,
        "title": str,
        "body": str,
        "isDraft": bool,
    }
    if (
        any(
            key not in item or not isinstance(item[key], expected)
            for key, expected in fields.items()
        )
        or "mergedAt" not in item
    ):
        raise FleetGitError("GitHub returned malformed pull request metadata")
    number = item["number"]
    if isinstance(number, bool) or number < 1:
        raise FleetGitError("GitHub returned malformed pull request metadata")
    expected_url = f"https://github.com/{owner}/{repo}/pull/{number}"
    expected_head_repo = f"{owner}/{repo}"
    state = item["state"]
    base = item["baseRefName"]
    head_repository = item["headRepository"]
    head_repository_owner = item["headRepositoryOwner"]
    head_sha = item["headRefOid"]
    merged_at = item.get("mergedAt")
    if (
        item["url"] != expected_url
        or state not in {"OPEN", "CLOSED", "MERGED"}
        or not base
        or item["headRefName"] != expected_head
        or head_repository.get("nameWithOwner") != expected_head_repo
        or head_repository_owner.get("login") != owner
        or (merged_at is not None and not isinstance(merged_at, str))
        or (state == "MERGED" and not isinstance(merged_at, str))
        or (state == "MERGED" and not merged_at)
        or (state != "MERGED" and merged_at is not None)
    ):
        raise FleetGitError("GitHub returned inconsistent pull request metadata")
    _validate_branch(base)
    _validate_object_id(head_sha)
    return PullRequest(
        number=number,
        url=item["url"],
        state=state,
        base=base,
        head=item["headRefName"],
        head_repo=expected_head_repo,
        head_sha=head_sha,
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
            "number,url,state,baseRefName,headRefName,headRefOid,headRepository,"
            "headRepositoryOwner,title,body,isDraft,mergedAt",
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
    head_sha: str,
    title: str,
    body: str,
) -> PullRequest:
    """Create, but never merge, a pull request using a private body file."""

    _validate_target(owner, repo)
    _validate_branch(base)
    _validate_branch(head)
    head_sha = _validate_object_id(head_sha)
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
        head_repo=f"{owner}/{repo}",
        head_sha=head_sha,
        title=title,
        body=body,
    )
