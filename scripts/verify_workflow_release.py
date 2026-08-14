#!/usr/bin/env python3
"""Verify that a workflow release tag is the intended, secure Git artifact."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass, field
import errno
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
from typing import Iterable, Iterator

import yaml

from scripts.workflow_catalog import (
    CatalogError,
    WorkflowCatalog,
    extract_caller_jobs,
    load_catalog,
    load_fleet_config,
)
from scripts.workflow_release_inventory import (
    RELEASE_PATHS,
    SETUP_GEMINI_AUTH_ROOT,
    validate_release_listing,
)


CHECKOUT_ACTION = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
CACHE_ACTION = "actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9"
OPENCODE_VERSION = "1.18.17"
OPENCODE_ARCHIVE_SHA256 = (
    "3f14a4c61c7f6b0d3b6d933d1d212e64e19683eba6fa453ad98e46303afe144a"
)
SETUP_GEMINI_AUTH = (
    "jhw7500/automation/.github/actions/setup-gemini-auth@"
    "2254f13aab44585c78954d20749f4fb677a8c2f1"
)
APPROVED_V140_POLICY_FILES = (
    ".github/actions/setup-gemini-auth/action.yml",
    "scripts/workflow-catalog.json",
    "scripts/workflow-config.json",
    "examples/baseline-workflows/.github/workflow-config.yml",
    "examples/baseline-workflows/.github/workflows/auto-rereview-request.yml",
    "examples/baseline-workflows/.github/workflows/claude-code-review.yml",
    "examples/baseline-workflows/.github/workflows/claude.yml",
    "examples/baseline-workflows/.github/workflows/gemini-auto-review.yml",
    "examples/baseline-workflows/.github/workflows/gemini-chat.yml",
    "examples/baseline-workflows/.github/workflows/gemini-dispatch.yml",
    "examples/baseline-workflows/.github/workflows/gemini-invoke.yml",
    "examples/baseline-workflows/.github/workflows/gemini-issue-triage.yml",
    "examples/baseline-workflows/.github/workflows/gemini-pr-review.yml",
    "examples/baseline-workflows/.github/workflows/gemini-review.yml",
    "examples/baseline-workflows/.github/workflows/gemini-scheduled-triage.yml",
    "examples/baseline-workflows/.github/workflows/gemini-triage.yml",
    "examples/baseline-workflows/.github/workflows/opencode-auto-review.yml",
    "examples/baseline-workflows/.github/workflows/opencode.yml",
)
APPROVED_V140_POLICY_SHA256 = (
    "56d1672a70e2edc81b902894eba9b437c70fb7af54376e105bd1862637389642"
)
EXPECTED_GEMINI_MODE_INPUT = {
    "description": "Repository write authentication: github_app or github_token",
    "type": "string",
    "required": "true",
}
EXPECTED_GEMINI_APP_ID_INPUT = {
    "description": "GitHub App ID; used only when repo_write_auth is github_app",
    "type": "string",
    "required": "false",
    "default": "",
}
EXPECTED_GEMINI_SECRETS = {
    "APP_PRIVATE_KEY": {
        "description": "GitHub App private key; used only for github_app mode",
        "required": "false",
    },
    "GEMINI_API_KEY": {
        "description": "Gemini API key",
        "required": "true",
    },
}
EXPECTED_GEMINI_AUTH_WITH = {
    "app-id": "${{ inputs.repo_write_auth == 'github_app' && inputs.app_id || '' }}",
    "private-key": (
        "${{ inputs.repo_write_auth == 'github_app' && "
        "secrets.APP_PRIVATE_KEY || '' }}"
    ),
    "fallback-token": (
        "${{ inputs.repo_write_auth == 'github_token' && github.token || '' }}"
    ),
}
EXPECTED_GEMINI_VALIDATION = {
    "name": "Validate repository-write auth",
    "shell": "bash",
    "env": {
        "MODE": "${{ inputs.repo_write_auth }}",
        "APP_ID": "${{ inputs.app_id }}",
        "APP_PRIVATE_KEY": "${{ secrets.APP_PRIVATE_KEY }}",
    },
    "run": (
        'case "$MODE" in\n'
        "  github_app)\n"
        '    test -n "$APP_ID" && test -n "$APP_PRIVATE_KEY" || {\n'
        "      echo 'github_app requires app_id and APP_PRIVATE_KEY' >&2\n"
        "      exit 1\n"
        "    }\n"
        "    ;;\n"
        "  github_token)\n"
        '    test -z "$APP_ID" && test -z "$APP_PRIVATE_KEY" || {\n'
        "      echo 'github_token forbids App credentials' >&2\n"
        "      exit 1\n"
        "    }\n"
        "    ;;\n"
        "  *)\n"
        '    echo "invalid repo_write_auth: $MODE" >&2\n'
        "    exit 1\n"
        "    ;;\n"
        "esac\n"
    ),
}
EXPECTED_SETUP_GEMINI_AUTH = {
    "name": "Setup Gemini Auth",
    "description": "Mint GitHub App token for Gemini workflows",
    "inputs": {
        "app-id": {"description": "GitHub App ID", "required": "false"},
        "private-key": {
            "description": "GitHub App Private Key",
            "required": "false",
        },
        "fallback-token": {
            "description": "Fallback token if App credentials not provided",
            "required": "false",
        },
    },
    "outputs": {
        "token": {
            "description": "Generated or fallback token",
            "value": "${{ steps.resolve.outputs.token }}",
        }
    },
    "runs": {
        "using": "composite",
        "steps": [
            {
                "name": "Mint identity token",
                "id": "mint_token",
                "if": "${{ inputs.app-id != '' }}",
                "uses": (
                    "actions/create-github-app-token@"
                    "a8d616148505b5069dccd32f177bb87d7f39123b"
                ),
                "with": {
                    "app-id": "${{ inputs.app-id }}",
                    "private-key": "${{ inputs.private-key }}",
                    "permission-contents": "read",
                    "permission-issues": "write",
                    "permission-pull-requests": "write",
                },
            },
            {
                "name": "Resolve token",
                "id": "resolve",
                "shell": "bash",
                "env": {
                    "MINTED": "${{ steps.mint_token.outputs.token }}",
                    "FALLBACK": "${{ inputs.fallback-token }}",
                },
                "run": (
                    'if [[ -n "$MINTED" ]]; then\n'
                    "  printf 'token=%s\\n' \"$MINTED\" >> \"$GITHUB_OUTPUT\"\n"
                    '  echo "::notice::Using GitHub App token"\n'
                    "else\n"
                    "  printf 'token=%s\\n' \"$FALLBACK\" >> \"$GITHUB_OUTPUT\"\n"
                    '  echo "::notice::Using fallback token"\n'
                    "fi\n"
                ),
            },
        ],
    },
}
MANUAL_GEMINI_FETCH_CONTRACTS = {
    "gemini-issue-triage.yml": (
        "Fetch issue",
        "issue",
        "ISSUE_NUMBER",
        "${{ inputs.issue_number }}",
        "gh issue view",
        "issues",
        "issue",
        "triage",
    ),
    "gemini-pr-review.yml": (
        "Fetch PR",
        "pr",
        "PR_NUMBER",
        "${{ inputs.pr_number }}",
        "gh pr view",
        "pull-requests",
        "pr",
        "review",
    ),
}
GEMINI_AUTH_OUTPUT = "${{ steps.auth.outputs.token }}"
APPROVED_GEMINI_ACTIONS = frozenset(
    {
        CHECKOUT_ACTION,
        "actions/github-script@60a0d83039c74a4aee543508d2ffcb1c3799cdea",
        "google-github-actions/run-gemini-cli@v0",
        "jhw7500/automation/.github/actions/check-workflow-enabled@v1.1",
        SETUP_GEMINI_AUTH,
    }
)
GIT_EXECUTABLE = "/usr/bin/git"
CANONICAL_AUTOMATION_REMOTE = "https://github.com/jhw7500/automation.git"
ACCEPTED_AUTOMATION_REMOTES = frozenset(
    {
        CANONICAL_AUTOMATION_REMOTE,
        "https://github.com/jhw7500/automation",
    }
)
HERMETIC_GIT_HOME = "/nonexistent/automation-workflow-release/home"
HERMETIC_GIT_XDG = "/nonexistent/automation-workflow-release/xdg"
OID = re.compile(r"[0-9a-f]{40}")
RELEASE_REF = re.compile(r"v[0-9]+(?:\.[0-9]+)+")
TAGGER_IDENT = re.compile(
    rb"(?P<name>[^\x00-\x1f\x7f<>]+) <[^\x00-\x1f\x7f<> ]+> "
    rb"(?P<timestamp>[0-9]+) "
    rb"(?P<sign>[+-])(?P<hour>[0-9]{2})(?P<minute>[0-9]{2})\Z"
)
MAX_GIT_METADATA_BYTES = 1024 * 1024


class ReleaseVerificationError(RuntimeError):
    """The requested release is absent, points elsewhere, or violates invariants."""


@dataclass(frozen=True)
class AnnotatedTag:
    ref: str
    tag_object: str
    commit: str


@dataclass(frozen=True)
class GitStorage:
    common_fd: int
    object_fd: int
    owner_uid: int


def git_child_env() -> dict[str, str]:
    """Return a fixed environment that excludes host and provider Git state."""

    return {
        "PATH": "/usr/bin:/bin",
        "HOME": HERMETIC_GIT_HOME,
        "XDG_CONFIG_HOME": HERMETIC_GIT_XDG,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/bin/false",
        "SSH_ASKPASS": "/bin/false",
        "GCM_INTERACTIVE": "Never",
    }


_SECURE_FILESYSTEM_NAMES = ("O_CLOEXEC", "O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK")


def _require_secure_filesystem() -> None:
    supported = (
        os.name == "posix"
        and os.open in os.supports_dir_fd
        and os.stat in os.supports_dir_fd
        and os.stat in os.supports_follow_symlinks
        and hasattr(os, "geteuid")
        and all(hasattr(os, name) for name in _SECURE_FILESYSTEM_NAMES)
        and Path("/proc/self/fd").is_dir()
    )
    if not supported:
        raise ReleaseVerificationError("unsupported Git repository layout")


def _directory_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
    )


def _metadata_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _open_child_directory(
    directory_fd: int,
    name: str,
    *,
    expected_uid: int | None,
    missing_ok: bool = False,
) -> int | None:
    if not name or name in {".", ".."} or "/" in name or "\0" in name:
        raise ReleaseVerificationError("unsupported Git repository layout")
    descriptor: int | None = None
    try:
        before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise ReleaseVerificationError("unsupported Git repository layout") from None
    except OSError:
        raise ReleaseVerificationError("unsupported Git repository layout") from None
    try:
        if not stat.S_ISDIR(before.st_mode) or stat.S_ISLNK(before.st_mode):
            raise OSError
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
        opened = os.fstat(descriptor)
        after = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or _directory_identity(before) != _directory_identity(opened)
            or _directory_identity(opened) != _directory_identity(after)
            or (expected_uid is not None and opened.st_uid != expected_uid)
        ):
            raise OSError
        return descriptor
    except OSError:
        pass
    if descriptor is not None:
        os.close(descriptor)
    raise ReleaseVerificationError("unsupported Git repository layout") from None


def _absolute_metadata_path(base: Path, raw: str) -> Path:
    if not raw or "\0" in raw:
        raise ReleaseVerificationError("unsupported Git repository layout")
    candidate = Path(raw)
    parts = candidate.parts
    parent_prefix = 0
    if candidate.is_absolute():
        if ".." in parts:
            raise ReleaseVerificationError("unsupported Git repository layout")
    else:
        while parent_prefix < len(parts) and parts[parent_prefix] == "..":
            parent_prefix += 1
        if ".." in parts[parent_prefix:]:
            raise ReleaseVerificationError("unsupported Git repository layout")
        candidate = base / candidate
    return Path(os.path.abspath(os.fspath(candidate)))


def _open_directory_path(path: Path, *, expected_uid: int | None) -> tuple[Path, int]:
    _require_secure_filesystem()
    if ".." in path.parts:
        raise ReleaseVerificationError("unsupported Git repository layout")
    absolute = Path(os.path.abspath(os.fspath(path)))
    descriptor: int | None = None
    try:
        descriptor = os.open(
            "/", os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        for component in absolute.parts[1:]:
            child = _open_child_directory(
                descriptor, component, expected_uid=None
            )
            assert child is not None
            os.close(descriptor)
            descriptor = child
        metadata = os.fstat(descriptor)
        if expected_uid is not None and metadata.st_uid != expected_uid:
            raise OSError
        return absolute, descriptor
    except (OSError, ReleaseVerificationError):
        if descriptor is not None:
            os.close(descriptor)
        raise ReleaseVerificationError("unsupported Git repository layout") from None


def _read_metadata_at(
    directory_fd: int,
    name: str,
    *,
    maximum: int = MAX_GIT_METADATA_BYTES,
    expected_uid: int,
    missing_ok: bool = False,
) -> bytes | None:
    """Read one stable, owned regular metadata file without following names."""

    _require_secure_filesystem()
    if (
        not name
        or name in {".", ".."}
        or "/" in name
        or "\0" in name
        or maximum < 0
    ):
        raise ReleaseVerificationError("unsupported Git repository metadata")
    descriptor: int | None = None
    try:
        named_before = os.stat(
            name, dir_fd=directory_fd, follow_symlinks=False
        )
    except OSError as exc:
        if missing_ok and exc.errno == errno.ENOENT:
            return None
        raise ReleaseVerificationError(
            "unsupported Git repository metadata"
        ) from None
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY
            | os.O_CLOEXEC
            | os.O_NOFOLLOW
            | os.O_NONBLOCK,
            dir_fd=directory_fd,
        )
    except OSError:
        raise ReleaseVerificationError(
            "unsupported Git repository metadata"
        ) from None

    try:
        opened_before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened_before.st_mode)
            or stat.S_ISLNK(named_before.st_mode)
            or opened_before.st_uid != expected_uid
            or opened_before.st_nlink != 1
            or opened_before.st_size > maximum
            or _metadata_identity(named_before)
            != _metadata_identity(opened_before)
        ):
            raise OSError
        chunks: list[bytes] = []
        length = 0
        while length <= maximum:
            chunk = os.read(descriptor, min(64 * 1024, maximum + 1 - length))
            if not chunk:
                break
            chunks.append(chunk)
            length += len(chunk)
        value = b"".join(chunks)
        after = os.fstat(descriptor)
        current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if (
            length > maximum
            or length != opened_before.st_size
            or _metadata_identity(opened_before) != _metadata_identity(after)
            or _metadata_identity(after) != _metadata_identity(current)
        ):
            raise OSError
        return value
    except OSError:
        raise ReleaseVerificationError(
            "unsupported Git repository metadata"
        ) from None
    finally:
        os.close(descriptor)


def _one_metadata_line_at(
    directory_fd: int,
    name: str,
    *,
    expected_uid: int,
    missing_ok: bool = False,
) -> str | None:
    value = _read_metadata_at(
        directory_fd,
        name,
        maximum=4096,
        expected_uid=expected_uid,
        missing_ok=missing_ok,
    )
    if value is None:
        return None
    try:
        lines = value.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        lines = []
    if len(lines) != 1 or not lines[0]:
        raise ReleaseVerificationError("unsupported Git repository metadata")
    return lines[0]


def _entry_exists_at(directory_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        raise ReleaseVerificationError("unsupported Git object storage") from None


def _unsupported_object_storage(
    git_fd: int, common_fd: int, object_fd: int, *, owner_uid: int
) -> bool:
    if _entry_exists_at(common_fd, "shallow") or _entry_exists_at(git_fd, "shallow"):
        return True
    opened: list[int] = []
    try:
        info_fd = _open_child_directory(
            object_fd, "info", expected_uid=owner_uid, missing_ok=True
        )
        if info_fd is not None:
            opened.append(info_fd)
            if _entry_exists_at(info_fd, "alternates") or _entry_exists_at(
                info_fd, "http-alternates"
            ):
                return True
        pack_fd = _open_child_directory(
            object_fd, "pack", expected_uid=owner_uid, missing_ok=True
        )
        if pack_fd is not None:
            opened.append(pack_fd)
            if any(name.endswith(".promisor") for name in os.listdir(pack_fd)):
                return True
        return False
    except (OSError, ReleaseVerificationError):
        raise ReleaseVerificationError("unsupported Git object storage") from None
    finally:
        for descriptor in reversed(opened):
            os.close(descriptor)


@contextmanager
def git_storage(repo: Path) -> Iterator[GitStorage]:
    """Pin a normal or linked-worktree object store without invoking Git."""

    owner_uid = os.geteuid() if hasattr(os, "geteuid") else -1
    descriptors: list[int] = []
    try:
        root, root_fd = _open_directory_path(repo, expected_uid=owner_uid)
        descriptors.append(root_fd)
        try:
            dot_git = os.stat(".git", dir_fd=root_fd, follow_symlinks=False)
        except OSError:
            raise ReleaseVerificationError(
                "unsupported Git repository layout"
            ) from None
        if stat.S_ISDIR(dot_git.st_mode) and not stat.S_ISLNK(dot_git.st_mode):
            opened = _open_child_directory(
                root_fd, ".git", expected_uid=owner_uid
            )
            assert opened is not None
            git_dir = root / ".git"
            git_fd = opened
        elif stat.S_ISREG(dot_git.st_mode) and not stat.S_ISLNK(dot_git.st_mode):
            pointer = _one_metadata_line_at(
                root_fd, ".git", expected_uid=owner_uid
            )
            assert pointer is not None
            if not pointer.startswith("gitdir: "):
                raise ReleaseVerificationError("unsupported Git repository layout")
            git_dir = _absolute_metadata_path(
                root, pointer.removeprefix("gitdir: ")
            )
            if git_dir.parent.name != "worktrees":
                raise ReleaseVerificationError("unsupported Git repository layout")
            git_dir, git_fd = _open_directory_path(
                git_dir, expected_uid=owner_uid
            )
        else:
            raise ReleaseVerificationError("unsupported Git repository layout")
        descriptors.append(git_fd)

        common_value = _one_metadata_line_at(
            git_fd, "commondir", expected_uid=owner_uid, missing_ok=True
        )
        if common_value is None:
            common_dir = git_dir
            common_fd = os.dup(git_fd)
        else:
            common_dir = _absolute_metadata_path(git_dir, common_value)
            if common_dir != git_dir.parent.parent:
                raise ReleaseVerificationError("unsupported Git repository layout")
            common_dir, common_fd = _open_directory_path(
                common_dir, expected_uid=owner_uid
            )
        descriptors.append(common_fd)

        opened = _open_child_directory(
            common_fd, "objects", expected_uid=owner_uid
        )
        assert opened is not None
        object_fd = opened
        descriptors.append(object_fd)
        if os.fstat(object_fd).st_dev != os.fstat(common_fd).st_dev:
            raise ReleaseVerificationError("unsupported Git repository layout")
        if _unsupported_object_storage(
            git_fd, common_fd, object_fd, owner_uid=owner_uid
        ):
            raise ReleaseVerificationError("unsupported Git object storage")
        yield GitStorage(common_fd, object_fd, owner_uid)
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


@contextmanager
def _raw_git_environment(
    repo: Path,
) -> Iterator[tuple[dict[str, str], tuple[int, ...]]]:
    with git_storage(repo) as storage:
        with tempfile.TemporaryDirectory(prefix="workflow-release-git-") as temporary:
            isolated = Path(temporary) / "git"
            (isolated / "objects").mkdir(parents=True)
            (isolated / "refs").mkdir()
            (isolated / "HEAD").write_text(
                "ref: refs/heads/unborn\n", encoding="ascii"
            )
            yield (
                {
                    **git_child_env(),
                    "GIT_DIR": str(isolated),
                    "GIT_OBJECT_DIRECTORY": f"/proc/self/fd/{storage.object_fd}",
                    "GIT_NO_REPLACE_OBJECTS": "1",
                    "GIT_OPTIONAL_LOCKS": "0",
                },
                (storage.object_fd,),
            )


def remote_git_env() -> dict[str, str]:
    """Restrict remote verification to unauthenticated public HTTPS."""

    return {
        **git_child_env(),
        "GIT_ALLOW_PROTOCOL": "https",
        "GIT_PROTOCOL_FROM_USER": "0",
        "GIT_CEILING_DIRECTORIES": "/",
    }


def _git_object_frame(repo: Path, oid: str) -> bytes:
    result: subprocess.CompletedProcess[bytes] | None = None
    try:
        with _raw_git_environment(repo) as (environment, pass_fds):
            result = subprocess.run(
                [GIT_EXECUTABLE, "cat-file", "--batch"],
                cwd="/",
                input=f"{oid}\n".encode("ascii"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                pass_fds=pass_fds,
            )
    except (OSError, UnicodeEncodeError, ValueError):
        pass
    if result is None or result.returncode != 0:
        raise ReleaseVerificationError("Git object is invalid") from None
    return result.stdout


def read_git_object(repo: Path, oid: str, expected_type: str) -> bytes:
    """Return one object only after authenticating its standard Git SHA-1 name."""

    if not _valid_oid(oid) or expected_type not in {"tag", "commit", "tree", "blob"}:
        raise ReleaseVerificationError("Git object is invalid")
    try:
        header, separator, framed = _git_object_frame(repo, oid).partition(b"\n")
        raw_oid, raw_type, raw_size = header.split(b" ")
        object_oid = raw_oid.decode("ascii")
        object_type = raw_type.decode("ascii")
        rendered_size = raw_size.decode("ascii")
        if (
            separator != b"\n"
            or object_oid != oid
            or object_type != expected_type
            or re.fullmatch(r"0|[1-9][0-9]*", rendered_size) is None
        ):
            raise ValueError
        size = int(rendered_size)
        if len(framed) != size + 1 or framed[-1:] != b"\n":
            raise ValueError
        payload = framed[:size]
        digest = hashlib.sha1(
            f"{object_type} {len(payload)}\0".encode("ascii") + payload
        ).hexdigest()
        if digest != oid:
            raise ValueError
        return payload
    except (UnicodeDecodeError, ValueError):
        raise ReleaseVerificationError("Git object is invalid") from None


@dataclass
class GitObjectReader:
    """Shared checksum gate for release-owned Git object payloads."""

    repo: Path
    _cache: dict[tuple[str, str], bytes] = field(default_factory=dict)

    def read(self, oid: str, expected_type: str) -> bytes:
        key = (oid, expected_type)
        if key not in self._cache:
            self._cache[key] = read_git_object(self.repo, oid, expected_type)
        return self._cache[key]


@dataclass(frozen=True)
class GitTreeEntry:
    mode: str
    name: bytes
    oid: str

    @property
    def object_type(self) -> str:
        if self.mode == "40000":
            return "tree"
        if self.mode == "160000":
            return "commit"
        return "blob"


@dataclass(frozen=True)
class GitTreeFile:
    path: PurePosixPath
    mode: str
    oid: str
    object_type: str


_TREE_ENTRY_MODES = frozenset({"40000", "100644", "100755", "120000", "160000"})


def _linked_oid(payload: bytes, prefix: bytes) -> str:
    first, separator, _remainder = payload.partition(b"\n")
    if separator != b"\n" or not first.startswith(prefix):
        raise ReleaseVerificationError("Git object is invalid")
    try:
        oid = first.removeprefix(prefix).decode("ascii")
    except UnicodeDecodeError:
        raise ReleaseVerificationError("Git object is invalid") from None
    if not _valid_oid(oid):
        raise ReleaseVerificationError("Git object is invalid")
    return oid


def _tag_commit_oid(payload: bytes, requested_ref: str) -> str:
    """Parse the fail-closed release contract: one annotated tag -> one commit."""

    if RELEASE_REF.fullmatch(requested_ref) is None:
        raise ReleaseVerificationError("Git object is invalid")
    try:
        expected_tag = requested_ref.encode("ascii")
    except UnicodeEncodeError:
        raise ReleaseVerificationError("Git object is invalid") from None
    header, separator, _message = payload.partition(b"\n\n")
    if separator != b"\n\n":
        raise ReleaseVerificationError("Git object is invalid")
    parsed: list[tuple[bytes, bytes]] = []
    for line in header.split(b"\n"):
        if line.startswith(b" "):
            raise ReleaseVerificationError("Git object is invalid")
        key, space, value = line.partition(b" ")
        if (
            space != b" "
            or not key
            or not value
            or any(byte < 0x21 or byte > 0x7E for byte in key)
        ):
            raise ReleaseVerificationError("Git object is invalid")
        parsed.append((key, value))
    if [key for key, _value in parsed] != [b"object", b"type", b"tag", b"tagger"]:
        raise ReleaseVerificationError("Git object is invalid")
    tagger = TAGGER_IDENT.fullmatch(parsed[3][1])
    if (
        tagger is None
        or not tagger.group("name").strip(b" ")
        or (
            len(tagger.group("timestamp")) > 1
            and tagger.group("timestamp").startswith(b"0")
        )
        or int(tagger.group("timestamp")) > 2**63 - 1
        or int(tagger.group("hour")) > 23
        or int(tagger.group("minute")) > 59
    ):
        raise ReleaseVerificationError("Git object is invalid")
    protected = {
        key: [value for observed, value in parsed if observed == key]
        for key in (b"object", b"type", b"tag")
    }
    if (
        len(protected[b"object"]) != 1
        or len(protected[b"type"]) != 1
        or len(protected[b"tag"]) != 1
        or protected[b"type"][0] != b"commit"
        or protected[b"tag"][0] != expected_tag
        or parsed[:3]
        != [
            (b"object", protected[b"object"][0]),
            (b"type", b"commit"),
            (b"tag", expected_tag),
        ]
    ):
        raise ReleaseVerificationError("Git object is invalid")
    oid = protected[b"object"][0]
    try:
        decoded = oid.decode("ascii")
    except UnicodeDecodeError:
        raise ReleaseVerificationError("Git object is invalid") from None
    if not _valid_oid(decoded):
        raise ReleaseVerificationError("Git object is invalid")
    return decoded


def _parse_tree(payload: bytes) -> tuple[GitTreeEntry, ...]:
    entries: list[GitTreeEntry] = []
    seen: set[bytes] = set()
    cursor = 0
    previous_key: bytes | None = None
    try:
        while cursor < len(payload):
            space = payload.index(b" ", cursor)
            nul = payload.index(b"\0", space + 1)
            mode = payload[cursor:space].decode("ascii")
            name = payload[space + 1 : nul]
            oid_end = nul + 21
            ordering_key = name + (b"/" if mode == "40000" else b"\0")
            if (
                mode not in _TREE_ENTRY_MODES
                or not name
                or name in {b".", b".."}
                or b"/" in name
                or name in seen
                or oid_end > len(payload)
                or (previous_key is not None and ordering_key <= previous_key)
            ):
                raise ValueError
            oid = payload[nul + 1 : oid_end].hex()
            if not _valid_oid(oid):
                raise ValueError
            seen.add(name)
            entries.append(GitTreeEntry(mode, name, oid))
            previous_key = ordering_key
            cursor = oid_end
    except (UnicodeDecodeError, ValueError):
        raise ReleaseVerificationError("Git tree is invalid") from None
    return tuple(entries)


def _release_path(path: str | PurePosixPath) -> PurePosixPath:
    rendered = str(path)
    parsed = PurePosixPath(rendered)
    if (
        not parsed.parts
        or parsed.is_absolute()
        or ".." in parsed.parts
        or rendered != parsed.as_posix()
    ):
        raise ReleaseVerificationError("release path is invalid")
    return parsed


class VerifiedCommitTree:
    """Python traversal of one authenticated commit and its selected tree paths."""

    def __init__(self, reader: GitObjectReader, commit: str) -> None:
        self.reader = reader
        self.commit = commit
        commit_payload = reader.read(commit, "commit")
        self.root_oid = _linked_oid(commit_payload, b"tree ")
        self._trees: dict[str, tuple[GitTreeEntry, ...]] = {}
        self._entries(self.root_oid)

    @classmethod
    def open(cls, repo: Path, commit: str) -> VerifiedCommitTree:
        if not _valid_oid(commit):
            raise ReleaseVerificationError("commit has an invalid Git identity")
        return cls(GitObjectReader(repo), commit)

    def _entries(self, oid: str) -> tuple[GitTreeEntry, ...]:
        if oid not in self._trees:
            self._trees[oid] = _parse_tree(self.reader.read(oid, "tree"))
        return self._trees[oid]

    def entry(self, path: str | PurePosixPath) -> GitTreeEntry | None:
        release_path = _release_path(path)
        tree_oid = self.root_oid
        for index, component in enumerate(release_path.parts):
            raw_component = component.encode("utf-8")
            entry = next(
                (
                    candidate
                    for candidate in self._entries(tree_oid)
                    if candidate.name == raw_component
                ),
                None,
            )
            if entry is None:
                return None
            if index == len(release_path.parts) - 1:
                return entry
            if entry.object_type != "tree":
                return None
            tree_oid = entry.oid
        return None

    def read_file(self, path: str | PurePosixPath) -> bytes:
        entry = self.entry(path)
        if entry is None or entry.object_type != "blob":
            raise ReleaseVerificationError("release file is unavailable")
        return self.reader.read(entry.oid, "blob")

    def read_text(self, path: str | PurePosixPath) -> str:
        try:
            return self.read_file(path).decode("utf-8")
        except UnicodeDecodeError:
            raise ReleaseVerificationError("release file contains invalid text") from None

    def _walk_tree(
        self, tree_oid: str, parent: PurePosixPath
    ) -> Iterator[GitTreeFile]:
        for entry in self._entries(tree_oid):
            try:
                component = entry.name.decode("utf-8")
            except UnicodeDecodeError:
                raise ReleaseVerificationError("Git tree is invalid") from None
            path = parent / component
            if entry.object_type == "tree":
                yield from self._walk_tree(entry.oid, path)
            else:
                yield GitTreeFile(path, entry.mode, entry.oid, entry.object_type)

    def files(self, path: str | PurePosixPath) -> tuple[GitTreeFile, ...]:
        release_path = _release_path(path)
        entry = self.entry(release_path)
        if entry is None:
            return ()
        if entry.object_type == "tree":
            return tuple(self._walk_tree(entry.oid, release_path))
        return (
            GitTreeFile(release_path, entry.mode, entry.oid, entry.object_type),
        )

    def listing(self, paths: Iterable[str | PurePosixPath]) -> bytes:
        records: list[bytes] = []
        for path in paths:
            for entry in self.files(path):
                kind = entry.object_type
                mode = "040000" if entry.mode == "40000" else entry.mode
                records.append(
                    f"{mode} {kind} {entry.oid}\t{entry.path.as_posix()}\0".encode(
                        "utf-8"
                    )
                )
        return b"".join(records)


def remote_git(url: str, *refs: str) -> str:
    """Read tag refs from the one public automation endpoint without host config."""

    if url != CANONICAL_AUTOMATION_REMOTE:
        raise ReleaseVerificationError(
            "remote must be the canonical public HTTPS automation endpoint"
        )
    result: subprocess.CompletedProcess[str] | None = None
    try:
        result = subprocess.run(
            [GIT_EXECUTABLE, "ls-remote", "--tags", url, *refs],
            cwd="/",
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=remote_git_env(),
        )
    except (OSError, ValueError):
        pass
    if result is None:
        raise ReleaseVerificationError(
            "Remote Git command failed (rc=unavailable)"
        ) from None
    if result.returncode != 0:
        raise ReleaseVerificationError(
            f"Remote Git command failed (rc={result.returncode})"
        ) from None
    return result.stdout


def _valid_oid(value: str) -> bool:
    return OID.fullmatch(value) is not None


def read_tag_oid(repo: Path, ref: str) -> str:
    """Read one version tag ref directly, without loading any Git configuration."""

    if RELEASE_REF.fullmatch(ref) is None:
        raise ReleaseVerificationError("invalid release ref")
    with git_storage(repo) as storage:
        opened: list[int] = []
        try:
            loose_value: str | None = None
            refs_fd = _open_child_directory(
                storage.common_fd,
                "refs",
                expected_uid=storage.owner_uid,
                missing_ok=True,
            )
            if refs_fd is not None:
                opened.append(refs_fd)
                tags_fd = _open_child_directory(
                    refs_fd,
                    "tags",
                    expected_uid=storage.owner_uid,
                    missing_ok=True,
                )
                if tags_fd is not None:
                    opened.append(tags_fd)
                    loose_value = _one_metadata_line_at(
                        tags_fd,
                        ref,
                        expected_uid=storage.owner_uid,
                        missing_ok=True,
                    )
            if loose_value is not None:
                if not _valid_oid(loose_value):
                    raise ReleaseVerificationError(
                        "release tag has an invalid Git identity"
                    )
                return loose_value

            packed = _read_metadata_at(
                storage.common_fd,
                "packed-refs",
                maximum=16 * 1024 * 1024,
                expected_uid=storage.owner_uid,
                missing_ok=True,
            )
            if packed is None:
                raise ReleaseVerificationError(
                    "release tag identity is unavailable"
                )
            lines = packed.decode("ascii").splitlines()
        except UnicodeDecodeError:
            raise ReleaseVerificationError(
                "release tag identity is unavailable"
            ) from None
        except ReleaseVerificationError as exc:
            if "invalid Git identity" in str(exc):
                raise
            raise ReleaseVerificationError(
                "release tag identity is unavailable"
            ) from None
        finally:
            for descriptor in reversed(opened):
                os.close(descriptor)
    target = f"refs/tags/{ref}"
    matches: list[str] = []
    for line in lines:
        if not line or line.startswith(("#", "^")):
            continue
        fields = line.split(" ")
        if len(fields) == 2 and fields[1] == target:
            matches.append(fields[0])
    if len(matches) != 1 or not _valid_oid(matches[0]):
        raise ReleaseVerificationError("release tag identity is unavailable")
    return matches[0]


def resolve_commit(repo: Path, revision: str) -> str:
    if not _valid_oid(revision):
        raise ReleaseVerificationError("commit has an invalid Git identity")
    try:
        read_git_object(repo, revision, "commit")
    except ReleaseVerificationError:
        raise ReleaseVerificationError("commit has an invalid Git identity") from None
    return revision


def resolve_annotated_tag(repo: Path, ref: str) -> AnnotatedTag:
    tag_object = read_tag_oid(repo, ref)
    try:
        payload = read_git_object(repo, tag_object, "tag")
        commit = _tag_commit_oid(payload, ref)
        read_git_object(repo, commit, "commit")
    except ReleaseVerificationError:
        raise ReleaseVerificationError(f"release {ref} must be an annotated tag")
    if not all(_valid_oid(value) for value in (tag_object, commit)):
        raise ReleaseVerificationError(f"release {ref} has an invalid Git identity")
    return AnnotatedTag(ref, tag_object, commit)


def assert_tag_unchanged(repo: Path, tag: AnnotatedTag) -> None:
    try:
        current = read_tag_oid(repo, tag.ref)
    except ReleaseVerificationError as exc:
        raise ReleaseVerificationError(
            f"tag {tag.ref} changed during verification"
        ) from exc
    if current != tag.tag_object:
        raise ReleaseVerificationError(f"tag {tag.ref} changed during verification")


def _direct_origin_urls(repo: Path) -> list[str]:
    """Parse only the literal origin URL in the common config; never follow includes."""

    with git_storage(repo) as storage:
        try:
            config = _read_metadata_at(
                storage.common_fd,
                "config",
                expected_uid=storage.owner_uid,
            )
            assert config is not None
            lines = config.decode("utf-8").splitlines()
        except UnicodeDecodeError:
            raise ReleaseVerificationError(
                "origin configuration is invalid"
            ) from None
    in_origin = False
    urls: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";")):
            continue
        if stripped.startswith("["):
            in_origin = (
                re.fullmatch(
                    r'\[remote\s+"origin"\](?:\s*[#;].*)?',
                    stripped,
                    flags=re.IGNORECASE,
                )
                is not None
            )
            continue
        if not in_origin:
            continue
        match = re.fullmatch(r"url\s*=\s*(\S+)", stripped, flags=re.IGNORECASE)
        if match is not None:
            urls.append(match.group(1))
    return urls


def _canonical_remote_url(repo: Path, remote: str) -> str:
    if remote != "origin":
        raise ReleaseVerificationError(
            "remote verification supports only origin"
        )
    try:
        configured = _direct_origin_urls(repo)
    except ReleaseVerificationError:
        configured = []
    if len(configured) != 1 or configured[0] not in ACCEPTED_AUTOMATION_REMOTES:
        raise ReleaseVerificationError(
            "origin must be the canonical public HTTPS automation remote"
        ) from None
    return CANONICAL_AUTOMATION_REMOTE


def verify_remote_tag(repo: Path, remote: str, tag: AnnotatedTag) -> None:
    url = _canonical_remote_url(repo, remote)
    result = remote_git(
        url,
        f"refs/tags/{tag.ref}",
        f"refs/tags/{tag.ref}^{{}}",
    )
    refs: dict[str, list[str]] = {}
    for line in result.splitlines():
        fields = line.split()
        if len(fields) != 2:
            raise ReleaseVerificationError(
                f"remote tag {tag.ref} returned an ambiguous identity"
            )
        sha, name = fields
        refs.setdefault(name, []).append(sha)
    direct = refs.get(f"refs/tags/{tag.ref}", [])
    peeled = refs.get(f"refs/tags/{tag.ref}^{{}}", [])
    if len(direct) != 1 or len(peeled) != 1:
        raise ReleaseVerificationError(
            f"remote tag {tag.ref} must be one annotated tag with one peeled commit"
        )
    if direct[0] != tag.tag_object or peeled[0] != tag.commit:
        raise ReleaseVerificationError(
            f"remote tag {tag.ref} identity mismatch: tag object {direct[0]} "
            f"(expected {tag.tag_object}), commit {peeled[0]} "
            f"(expected commit {tag.commit})"
        )


def verify_opencode_runtime(job: dict, step_name: str, workflow_name: str) -> dict:
    """Require a digest-verified CLI and the restricted repository token path."""
    try:
        cache = next(
            item
            for item in job["steps"]
            if item.get("name") == "Cache pinned OpenCode CLI archive"
        )
        install = next(
            item
            for item in job["steps"]
            if item.get("name") == "Install pinned OpenCode CLI"
        )
        run_step = next(item for item in job["steps"] if item.get("name") == step_name)
    except (KeyError, TypeError, StopIteration) as exc:
        raise ReleaseVerificationError(
            f"{workflow_name} pinned OpenCode runtime structure is missing"
        ) from exc

    job_env = job.get("env", {})
    install_script = install.get("run", "")
    expected_url = (
        "releases/download/v${OPENCODE_VERSION}/opencode-linux-x64.tar.gz"
    )
    runtime_is_pinned = (
        job_env.get("OPENCODE_VERSION") == OPENCODE_VERSION
        and job_env.get("OPENCODE_ARCHIVE_SHA256") == OPENCODE_ARCHIVE_SHA256
        and cache.get("uses") == CACHE_ACTION
        and expected_url in install_script
        and "sha256sum --check -" in install_script
        and '"$install_dir/opencode" --version' in install_script
        and run_step.get("run") == "opencode github run"
        and run_step.get("env", {}).get("USE_GITHUB_TOKEN") == "true"
        and run_step.get("env", {}).get("MODEL") == "zai-coding-plan/glm-4.7"
    )
    if not runtime_is_pinned:
        raise ReleaseVerificationError(
            f"{workflow_name} does not pin and verify the approved OpenCode CLI runtime"
        )
    return run_step


def _verify_approved_v140_policy(tree: VerifiedCommitTree, ref: str) -> None:
    if ref not in {"v1.40", "v1.40.1"}:
        return
    digest = hashlib.sha256()
    for path in APPROVED_V140_POLICY_FILES:
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(tree.read_file(path))
        digest.update(b"\0")
    if digest.hexdigest() != APPROVED_V140_POLICY_SHA256:
        raise ReleaseVerificationError(
            f"tag {ref} differs from the approved v1.40 policy snapshot"
        )


def _release_inventory(tree: VerifiedCommitTree) -> None:
    try:
        entries = validate_release_listing(tree.listing(RELEASE_PATHS))
        for entry in entries:
            tree.reader.read(entry.oid, "blob")
    except (ReleaseVerificationError, ValueError):
        raise ReleaseVerificationError(
            "release inventory is incomplete or invalid"
        ) from None


def _release_version(ref: str) -> tuple[int, ...]:
    if RELEASE_REF.fullmatch(ref) is None:
        raise ReleaseVerificationError(f"invalid release ref: {ref}")
    return tuple(int(part) for part in ref.removeprefix("v").split("."))


def _verify_setup_gemini_auth(tree: VerifiedCommitTree) -> None:
    path = SETUP_GEMINI_AUTH_ROOT.path.as_posix()
    try:
        document = yaml.load(
            tree.read_text(path), Loader=yaml.BaseLoader
        )
    except (ReleaseVerificationError, yaml.YAMLError):
        raise ReleaseVerificationError(
            "setup-gemini-auth action contract is invalid"
        ) from None
    if document != EXPECTED_SETUP_GEMINI_AUTH:
        raise ReleaseVerificationError(
            "setup-gemini-auth action contract is invalid"
        )


def _verify_tag_catalog(
    tree: VerifiedCommitTree, ref: str
) -> WorkflowCatalog | None:
    """Run the catalog/config/canonical contracts against tag-owned bytes."""

    import tempfile

    paths = (
        "scripts/workflow-catalog.json",
        "scripts/workflow-config.json",
        "examples/baseline-workflows/.github",
    )
    missing_inventory = {
        path
        for path in paths[:2]
        if (entry := tree.entry(path)) is None or entry.object_type != "blob"
    }
    if missing_inventory:
        # Historical tags predate the closed release inventory.  Keep their
        # OpenCode verifier regression path readable, while all v1.40+ tags
        # must carry the renderer-owned catalog and fleet config.
        if _release_version(ref) < (1, 40):
            return None
        raise ReleaseVerificationError(
            f"tag {ref} release inventory is missing: {sorted(missing_inventory)}"
        )
    with tempfile.TemporaryDirectory(prefix="verify-workflow-tag-") as temporary:
        root = Path(temporary)
        for relative in paths[:2]:
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(tree.read_file(relative))
        try:
            catalog = load_catalog(root)
            config = load_fleet_config(root, catalog)
        except CatalogError as exc:
            raise ReleaseVerificationError(
                f"tag {ref} catalog/config is invalid: {exc}"
            ) from exc

        if ref == "v1.40" and (
            config.owner != "jhw7500"
            or config.automation_ref != "v1.40"
            or config.canonical_dir.as_posix()
            != "examples/baseline-workflows/.github"
            or len(config.profiles) != 19
            or len(catalog.callers) != 14
        ):
            raise ReleaseVerificationError(
                "tag v1.40 violates the approved v1.40 semantic identity"
            )

        canonical_names = {entry.path.as_posix() for entry in tree.files(paths[2])}
        expected_names = {
            f"{config.canonical_dir}/{entry.path.relative_to('.github')}"
            for entry in catalog.entries
            if entry.kind != "retired"
        }
        if canonical_names != expected_names:
            missing = sorted(expected_names - canonical_names)
            unknown = sorted(canonical_names - expected_names)
            raise ReleaseVerificationError(
                f"tag {ref} canonical tree mismatch: missing={missing}, unknown={unknown}"
            )
        for entry in catalog.callers:
            relative = entry.path.relative_to(".github")
            name = f"{config.canonical_dir}/{relative}"
            text = tree.read_text(name)
            try:
                document = yaml.load(text, Loader=yaml.BaseLoader)
            except yaml.YAMLError as exc:
                raise ReleaseVerificationError(f"{name} is invalid YAML") from exc
            if not isinstance(document, dict):
                raise ReleaseVerificationError(f"{name} must be a mapping")
            if document.get("on") != entry.trigger:
                raise ReleaseVerificationError(f"{name} trigger violates the catalog")
            try:
                jobs = extract_caller_jobs(document)
            except CatalogError as exc:
                raise ReleaseVerificationError(
                    f"{name} caller contract is invalid: {exc}"
                ) from exc
            if jobs != entry.caller_jobs:
                raise ReleaseVerificationError(f"{name} caller jobs violate the catalog")
            uses = re.findall(
                r"jhw7500/automation/\.github/workflows/([^@\s'\"]+)@"
                r"([^\s'\"]+)",
                text,
            )
            if uses != [(entry.central_workflow, "__AUTOMATION_COMMIT__")]:
                raise ReleaseVerificationError(
                    f"{name} central target or release placeholder violates the catalog"
                )

        config_name = f"{config.canonical_dir}/workflow-config.yml"
        try:
            canonical_config = yaml.load(
                tree.read_text(config_name),
                Loader=yaml.BaseLoader,
            )
        except yaml.YAMLError as exc:
            raise ReleaseVerificationError(f"{config_name} is invalid YAML") from exc
        expected_workflows = {entry.path.stem for entry in catalog.callers}
        workflow_values = (
            canonical_config.get("workflows", {})
            if isinstance(canonical_config, dict)
            else {}
        )
        config_is_closed = (
            isinstance(canonical_config, dict)
            and set(canonical_config)
            == {"automation_ref", "automation_commit", "review", "workflows"}
            and canonical_config.get("automation_ref") == "__AUTOMATION_REF__"
            and canonical_config.get("automation_commit") == "__AUTOMATION_COMMIT__"
            and canonical_config.get("review") == {"auto": "false"}
            and isinstance(workflow_values, dict)
            and set(workflow_values) == expected_workflows
            and all(value == {"enabled": "false"} for value in workflow_values.values())
        )
        if not config_is_closed:
            raise ReleaseVerificationError(
                f"{config_name} violates the disabled bootstrap contract"
            )
        return catalog


def _expected_manual_fetch_step(
    contract: tuple[str, str, str, str, str, str, str, str],
) -> dict[str, object]:
    step_name, step_id, number_name, number_expression, command, _, _, _ = contract
    number_reference = f"${number_name}"
    run = (
        f'title="$({command} "{number_reference}" --repo "$REPO" '
        '--json title --jq .title)"\n'
        f'body="$({command} "{number_reference}" --repo "$REPO" '
        '--json body --jq .body)"\n\n'
        "write_output() {\n"
        '  local name="$1"\n'
        '  local value="$2"\n'
        "  local delimiter='__AUTOMATION_OUTPUT__'\n"
        '  while [[ "$value" == *"$delimiter"* ]]; do\n'
        '    delimiter="${delimiter}_X"\n'
        "  done\n"
        "  {\n"
        "    printf '%s<<%s\\n' \"$name\" \"$delimiter\"\n"
        "    printf '%s\\n' \"$value\"\n"
        "    printf '%s\\n' \"$delimiter\"\n"
        '  } >> "$GITHUB_OUTPUT"\n'
        "}\n\n"
        'write_output title "$title"\n'
        'write_output body "$body"\n'
    )
    return {
        "name": step_name,
        "id": step_id,
        "shell": "bash",
        "env": {
            "GH_TOKEN": "${{ github.token }}",
            "REPO": "${{ github.repository }}",
            number_name: number_expression,
        },
        "run": run,
    }


def _expected_manual_prepare_job(
    contract: tuple[str, str, str, str, str, str, str, str],
) -> dict[str, object]:
    (
        _,
        step_id,
        number_name,
        number_expression,
        _,
        permission_name,
        output_prefix,
        _,
    ) = contract
    input_name = number_name.lower()
    number_reference = f"${number_name}"
    validation = {
        "name": f"Validate {input_name}",
        "shell": "bash",
        "env": {number_name: number_expression},
        "run": (
            f'if ! [[ "{number_reference}" =~ ^[0-9]+$ ]]; then\n'
            f'  echo "{input_name} must be a positive integer"\n'
            "  exit 1\n"
            "fi\n"
        ),
    }
    return {
        "runs-on": "ubuntu-latest",
        "permissions": {permission_name: "read"},
        "outputs": {
            f"{output_prefix}_title": (
                f"${{{{ steps.{step_id}.outputs.title }}}}"
            ),
            f"{output_prefix}_body": f"${{{{ steps.{step_id}.outputs.body }}}}",
        },
        "steps": [validation, _expected_manual_fetch_step(contract)],
    }


def _verify_manual_gemini_output_contract(
    tree: VerifiedCommitTree, ref: str
) -> None:
    if _release_version(ref) < (1, 40, 2):
        return
    root = "examples/baseline-workflows/.github/workflows"
    for filename, contract in MANUAL_GEMINI_FETCH_CONTRACTS.items():
        path = f"{root}/{filename}"
        try:
            document = yaml.load(tree.read_text(path), Loader=yaml.BaseLoader)
            prepare = document["jobs"]["prepare"]
            downstream = document["jobs"][contract[7]]
            downstream_with = downstream["with"]
        except (ReleaseVerificationError, yaml.YAMLError, KeyError, TypeError):
            raise ReleaseVerificationError(
                f"{path} manual Gemini output contract is invalid"
            ) from None
        if prepare != _expected_manual_prepare_job(contract):
            raise ReleaseVerificationError(
                f"{path} manual Gemini output contract is invalid"
            )
        output_prefix = contract[6]
        expected_downstream = {
            "issue_title": (
                f"${{{{ needs.prepare.outputs.{output_prefix}_title }}}}"
            ),
            "issue_body": f"${{{{ needs.prepare.outputs.{output_prefix}_body }}}}",
        }
        if (
            downstream.get("needs") != "prepare"
            or not isinstance(downstream_with, dict)
            or any(
                downstream_with.get(name) != value
                for name, value in expected_downstream.items()
            )
        ):
            raise ReleaseVerificationError(
                f"{path} manual Gemini output contract is invalid"
            )


def _values(value: object) -> list[str]:
    result: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            result.append(str(key))
            result.extend(_values(item))
    elif isinstance(value, list):
        for item in value:
            result.extend(_values(item))
    elif isinstance(value, str):
        result.append(value)
    return result


def _resolver_candidate(step: dict) -> bool:
    uses = step.get("uses", "")
    return (
        step.get("id") == "auth"
        or step.get("name") == "Resolve repository-write token"
        or (isinstance(uses, str) and "setup-gemini-auth" in uses)
    )


def _grants_write(permissions: object) -> bool:
    return permissions == "write-all" or (
        isinstance(permissions, dict)
        and any(value == "write" for value in permissions.values())
    )


def _action_references(value: object) -> list[str]:
    result: list[str] = []
    if isinstance(value, dict):
        uses = value.get("uses")
        if isinstance(uses, str):
            result.append(uses)
        for item in value.values():
            result.extend(_action_references(item))
    elif isinstance(value, list):
        for item in value:
            result.extend(_action_references(item))
    return result


def _verify_token_mapping(
    name: str, location: str, mapping: object, *, allow_empty: bool = False
) -> int:
    if not isinstance(mapping, dict):
        return 0
    sinks = 0
    for key, value in mapping.items():
        token_key = str(key).lower().replace("_", "-")
        if token_key == "token" or token_key.endswith("-token"):
            if allow_empty:
                if value in {None, ""}:
                    continue
                raise ReleaseVerificationError(
                    f"{name}:{location} sets a repository token before resolver"
                )
            sinks += 1
            if value != GEMINI_AUTH_OUTPUT:
                raise ReleaseVerificationError(
                    f"{name}:{location} repository token sink must use steps.auth"
                )
    return sinks


def _verify_gemini_workflow(name: str, document: dict) -> None:
    try:
        call = document["on"]["workflow_call"]
        inputs = call["inputs"]
        secrets = call["secrets"]
    except (KeyError, TypeError) as exc:
        raise ReleaseVerificationError(
            f"{name} Gemini workflow_call contract is missing"
        ) from exc
    mode = inputs.get("repo_write_auth") if isinstance(inputs, dict) else None
    if (
        set(document.get("on", {})) != {"workflow_call"}
        or mode != EXPECTED_GEMINI_MODE_INPUT
        or inputs.get("app_id") != EXPECTED_GEMINI_APP_ID_INPUT
    ):
        raise ReleaseVerificationError(
            f"{name} must declare exact repo_write_auth and app_id inputs"
        )
    values = _values(document)
    normalized = "\n".join(values)
    normalized_lower = normalized.lower()
    if "google_api_key" in normalized_lower:
        raise ReleaseVerificationError(f"{name} contains forbidden GOOGLE_API_KEY")
    if secrets != EXPECTED_GEMINI_SECRETS:
        raise ReleaseVerificationError(
            f"{name} must declare only APP_PRIVATE_KEY and GEMINI_API_KEY"
        )
    if any(
        forbidden in normalized_lower
        for forbidden in (
            "google-github-actions/auth",
            "google_application_credentials",
            "workload_identity_provider",
            "gcp_",
            "id-token",
        )
    ):
        raise ReleaseVerificationError(f"{name} contains forbidden Google/GCP/OIDC auth")
    if "actions/create-github-app-token" in normalized_lower:
        raise ReleaseVerificationError(
            f"{name} contains forbidden direct App token resolver"
        )
    if "vars.app_id" in normalized_lower:
        raise ReleaseVerificationError(f"{name} contains forbidden ambient App fallback")
    if _grants_write(document.get("permissions")):
        raise ReleaseVerificationError(
            f"{name} must not grant workflow-level write permissions"
        )
    unapproved_actions = sorted(
        set(_action_references(document)) - APPROVED_GEMINI_ACTIONS
    )
    if unapproved_actions:
        raise ReleaseVerificationError(
            f"{name} uses a resolver/action outside the approved action allowlist: "
            f"{unapproved_actions[0]}"
        )
    workflow_metadata = {key: value for key, value in document.items() if key != "jobs"}
    if "github.token" in "\n".join(_values(workflow_metadata)):
        raise ReleaseVerificationError(
            f"{name}:workflow contains forbidden github.token outside resolver"
        )
    _verify_token_mapping(name, "workflow.env", document.get("env", {}), allow_empty=True)

    jobs = document.get("jobs", {})
    if not isinstance(jobs, dict):
        raise ReleaseVerificationError(f"{name} jobs must be a mapping")
    resolver_count = 0
    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            raise ReleaseVerificationError(f"{name}:{job_name} job must be a mapping")
        steps = job.get("steps", [])
        if not isinstance(steps, list) or not all(
            isinstance(step, dict) for step in steps
        ):
            raise ReleaseVerificationError(f"{name}:{job_name} steps are invalid")
        if "permissions" not in job or not isinstance(job["permissions"], dict):
            raise ReleaseVerificationError(
                f"{name}:{job_name} must declare an explicit permissions mapping"
            )
        candidates = [
            index for index, step in enumerate(steps) if _resolver_candidate(step)
        ]
        permissions = job["permissions"]
        writes = _grants_write(permissions)
        if writes and len(candidates) != 1:
            raise ReleaseVerificationError(
                f"{name}:{job_name} must contain exactly one setup-gemini-auth resolver"
            )
        if not writes and candidates:
            raise ReleaseVerificationError(
                f"{name}:{job_name} contains a resolver outside a write job"
            )

        metadata = {key: value for key, value in job.items() if key != "steps"}
        if "github.token" in "\n".join(_values(metadata)):
            raise ReleaseVerificationError(
                f"{name}:{job_name} contains forbidden github.token outside resolver"
            )
        for step_index, step in enumerate(steps):
            if step_index not in candidates and "github.token" in "\n".join(
                _values(step)
            ):
                raise ReleaseVerificationError(
                    f"{name}:{job_name} bypasses the resolved repository-write token"
                )

        if not candidates:
            continue
        resolver_count += 1
        index = candidates[0]
        resolver = steps[index]
        if resolver != {
            "name": "Resolve repository-write token",
            "id": "auth",
            "uses": SETUP_GEMINI_AUTH,
            "with": EXPECTED_GEMINI_AUTH_WITH,
        }:
            raise ReleaseVerificationError(
                f"{name}:{job_name} setup-gemini-auth resolver must use the exact "
                "pin and mode-controlled inputs"
            )
        if index == 0 or steps[index - 1] != EXPECTED_GEMINI_VALIDATION:
            raise ReleaseVerificationError(
                f"{name}:{job_name} resolver must be immediately preceded by exact "
                "repository-write auth validation"
            )

        write_token_sinks = _verify_token_mapping(
            name, f"{job_name}.env", job.get("env", {}), allow_empty=True
        )
        for step_index, step in enumerate(steps):
            if step_index == index:
                continue
            for mapping_name in ("env", "with"):
                write_token_sinks += _verify_token_mapping(
                    name,
                    f"{job_name}.steps[{step_index}].{mapping_name}",
                    step.get(mapping_name, {}),
                    allow_empty=step_index < index,
                )
        if write_token_sinks == 0:
            raise ReleaseVerificationError(
                f"{name}:{job_name} has no resolved repository-write token sink"
            )
    if resolver_count == 0:
        raise ReleaseVerificationError(f"{name} has no setup-gemini-auth resolver")


def _verify_commit_content(
    repo: Path, ref: str, revision: str
) -> VerifiedCommitTree:
    tree = VerifiedCommitTree.open(repo, revision)
    if _release_version(ref) >= (1, 40):
        _release_inventory(tree)
        _verify_setup_gemini_auth(tree)
    _verify_approved_v140_policy(tree, ref)
    _verify_manual_gemini_output_contract(tree, ref)
    catalog = _verify_tag_catalog(tree, ref)
    names = [entry.path.as_posix() for entry in tree.files(".github/workflows")]
    workflows = [name for name in names if name.endswith((".yml", ".yaml"))]
    if not workflows:
        raise ReleaseVerificationError(f"tag {ref} contains no reusable workflows")

    documents: dict[str, dict] = {}
    for name in workflows:
        text = tree.read_text(name)
        if "secrets.GITHUB_TOKEN" in text:
            raise ReleaseVerificationError(f"{name} uses secrets.GITHUB_TOKEN")
        for match in re.finditer(r"actions/checkout@([^'\"\s#]+)", text):
            checkout = f"actions/checkout@{match.group(1)}"
            if checkout != CHECKOUT_ACTION:
                raise ReleaseVerificationError(
                    f"{name} checkout reference is not the approved immutable commit"
                )
        data = yaml.load(text, Loader=yaml.BaseLoader)
        documents[Path(name).name] = data if isinstance(data, dict) else {}

    if catalog is not None:
        gemini_targets = sorted(
            {
                entry.central_workflow
                for entry in catalog.callers
                if entry.auth_family == "gemini"
            }
        )
        for target in gemini_targets:
            if target is None or target not in documents:
                raise ReleaseVerificationError(
                    f"central Gemini workflow is missing: {target}"
                )
            _verify_gemini_workflow(target, documents[target])

        for entry in catalog.callers:
            assert entry.central_workflow is not None
            central = documents.get(entry.central_workflow)
            if central is None:
                raise ReleaseVerificationError(
                    f"central workflow is missing: {entry.central_workflow}"
                )
            try:
                call = central["on"]["workflow_call"]
                declared_inputs = set(call.get("inputs", {}))
                declared_secrets = call.get("secrets", {})
            except (KeyError, TypeError) as exc:
                raise ReleaseVerificationError(
                    f"{entry.central_workflow} workflow_call contract is missing"
                ) from exc
            if not isinstance(declared_secrets, dict):
                raise ReleaseVerificationError(
                    f"{entry.central_workflow} secrets must be a mapping"
                )
            required_secrets = {
                name
                for name, value in declared_secrets.items()
                if isinstance(value, dict) and value.get("required", "false") == "true"
            }
            if any(
                not set(job.with_keys) <= declared_inputs
                or not set(job.secrets) <= set(declared_secrets)
                or not required_secrets <= set(job.secrets)
                for job in entry.caller_jobs
            ):
                raise ReleaseVerificationError(
                    f"{entry.path} is incompatible with {entry.central_workflow}"
                )

    auto = documents.get("opencode-auto-review.yml")
    if auto is None:
        raise ReleaseVerificationError("opencode-auto-review.yml is missing")
    try:
        check_job = auto["jobs"]["check-enabled"]
        job = auto["jobs"]["opencode-review"]
        permissions = job["permissions"]
        checkout = next(
            item for item in job["steps"] if item.get("name") == "Checkout repository"
        )
    except (KeyError, TypeError, StopIteration) as exc:
        raise ReleaseVerificationError("OpenCode security structure is missing") from exc
    step = verify_opencode_runtime(
        job, "Run OpenCode PR review", "opencode-auto-review.yml"
    )
    expected_permissions = {
        "contents": "read",
        "pull-requests": "write",
        "issues": "write",
    }
    if permissions != expected_permissions:
        raise ReleaseVerificationError(
            f"OpenCode auto review permissions differ from {expected_permissions}"
        )
    safe_output = check_job.get("outputs", {}).get("safe_pr")
    scope_step = next(
        (item for item in check_job.get("steps", []) if item.get("id") == "pr_scope"),
        {},
    )
    condition = job.get("if", "")
    if (
        safe_output != "${{ steps.pr_scope.outputs.safe_pr }}"
        or "gh api" not in scope_step.get("run", "")
        or not isinstance(condition, str)
        or "needs.check-enabled.outputs.safe_pr == 'true'" not in condition
    ):
        raise ReleaseVerificationError(
            "OpenCode auto review lacks a central same-repository PR guard"
        )
    if checkout.get("with", {}).get("persist-credentials") != "true":
        raise ReleaseVerificationError(
            "OpenCode auto review cannot authenticate private repository fetch"
        )
    if step.get("env", {}).get("GITHUB_TOKEN") != "${{ github.token }}":
        raise ReleaseVerificationError("OpenCode auto review does not use github.token")

    command = documents.get("opencode.yml")
    try:
        command_check = command["jobs"]["check-enabled"]
        command_job = command["jobs"]["opencode"]
        command_checkout = next(
            item
            for item in command_job["steps"]
            if item.get("name") == "Checkout repository"
        )
    except (KeyError, TypeError, StopIteration) as exc:
        raise ReleaseVerificationError("opencode.yml structure is missing") from exc
    command_step = verify_opencode_runtime(command_job, "Run opencode", "opencode.yml")
    if command_checkout.get("with", {}).get("persist-credentials") != "true":
        raise ReleaseVerificationError(
            "opencode.yml cannot authenticate private repository fetch"
        )
    command_scope = next(
        (item for item in command_check.get("steps", []) if item.get("id") == "pr_scope"),
        {},
    )
    command_condition = command_job.get("if", "")
    command_permissions = {
        "contents": "read",
        "pull-requests": "write",
        "issues": "write",
    }
    command_is_secure = (
        command_check.get("outputs", {}).get("safe_pr")
        == "${{ steps.pr_scope.outputs.safe_pr }}"
        and "gh api" in command_scope.get("run", "")
        and command_scope.get("env", {}).get("PR_NUMBER")
        == "${{ github.event.pull_request.number || github.event.issue.number }}"
        and isinstance(command_condition, str)
        and "needs.check-enabled.outputs.safe_pr == 'true'" in command_condition
        and command_job.get("permissions") == command_permissions
        and command_step.get("env", {}).get("USE_GITHUB_TOKEN") == "true"
        and command_step.get("env", {}).get("GITHUB_TOKEN") == "${{ github.token }}"
    )
    if not command_is_secure:
        raise ReleaseVerificationError(
            "opencode.yml security contract permits unsafe PR or App-token access"
        )
    return tree


def verify_commit_content(repo: Path, ref: str, commit: str) -> str:
    """Verify one exact raw commit before an immutable release tag is published."""

    if RELEASE_REF.fullmatch(ref) is None:
        raise ReleaseVerificationError(f"invalid release ref: {ref}")
    revision = resolve_commit(repo, commit)
    _verify_commit_content(repo, ref, revision)
    return revision


def verify_tag_content(
    repo: Path, ref: str, *, tag: AnnotatedTag | None = None
) -> VerifiedCommitTree:
    captured = tag if tag is not None else resolve_annotated_tag(repo, ref)
    if captured.ref != ref:
        raise ReleaseVerificationError("captured tag identity does not match release ref")
    tree = _verify_commit_content(repo, ref, captured.commit)
    assert_tag_unchanged(repo, captured)
    return tree


def verify_release(
    repo: Path, ref: str, expected_commit: str, remote: str | None = None
) -> str:
    if re.fullmatch(r"v[0-9]+(?:\.[0-9]+)+", ref) is None:
        raise ReleaseVerificationError(f"invalid release ref: {ref}")
    tag = resolve_annotated_tag(repo, ref)
    expected = resolve_commit(repo, expected_commit)
    if tag.commit != expected:
        raise ReleaseVerificationError(
            f"tag {ref} points to {tag.commit}, expected commit {expected}"
        )
    verify_tag_content(repo, ref, tag=tag)
    if remote is not None:
        verify_remote_tag(repo, remote, tag)
    assert_tag_unchanged(repo, tag)
    return tag.commit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--automation", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--ref", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--remote")
    parser.add_argument("--commit-only", action="store_true")
    args = parser.parse_args(argv)
    if args.commit_only and args.remote is not None:
        parser.error("--commit-only does not accept --remote")
    try:
        commit = (
            verify_commit_content(args.automation, args.ref, args.expected_commit)
            if args.commit_only
            else verify_release(
                args.automation, args.ref, args.expected_commit, remote=args.remote
            )
        )
    except ReleaseVerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    if args.commit_only:
        print(f"PASS: {args.ref} commit content is secure at {commit}")
    else:
        remote_note = f" and remote {args.remote}" if args.remote else ""
        print(f"PASS: {args.ref} resolves to secure commit {commit}{remote_note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
