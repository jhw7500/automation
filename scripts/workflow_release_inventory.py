"""Typed, closed inventory for bytes owned by a workflow release."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import re
from typing import Literal


@dataclass(frozen=True)
class ReleaseRoot:
    """One release-owned tree or one exact release-owned regular file."""

    path: PurePosixPath
    kind: Literal["tree", "file"]
    mode: Literal["100644", "100755"] | None = None

    def __post_init__(self) -> None:
        if (
            self.path.is_absolute()
            or ".." in self.path.parts
            or (self.kind == "tree") != (self.mode is None)
        ):
            raise ValueError("invalid release root")


@dataclass(frozen=True)
class ReleaseBlob:
    """One raw blob accepted from the exact commit tree."""

    path: PurePosixPath
    oid: str
    git_mode: Literal["100644", "100755"]

    @property
    def archive_mode(self) -> int:
        return 0o755 if self.git_mode == "100755" else 0o644


CENTRAL_WORKFLOW_ROOT = ReleaseRoot(
    PurePosixPath(".github/workflows"), "tree"
)
SETUP_GEMINI_AUTH_ROOT = ReleaseRoot(
    PurePosixPath(".github/actions/setup-gemini-auth/action.yml"),
    "file",
    "100644",
)
CANONICAL_WORKFLOW_ROOT = ReleaseRoot(
    PurePosixPath("examples/baseline-workflows/.github"), "tree"
)
WORKFLOW_CATALOG_ROOT = ReleaseRoot(
    PurePosixPath("scripts/workflow-catalog.json"), "file", "100644"
)
WORKFLOW_CONFIG_ROOT = ReleaseRoot(
    PurePosixPath("scripts/workflow-config.json"), "file", "100644"
)

RELEASE_ROOTS = (
    CENTRAL_WORKFLOW_ROOT,
    SETUP_GEMINI_AUTH_ROOT,
    CANONICAL_WORKFLOW_ROOT,
    WORKFLOW_CATALOG_ROOT,
    WORKFLOW_CONFIG_ROOT,
)
RELEASE_PATHS = tuple(root.path.as_posix() for root in RELEASE_ROOTS)
EXACT_RELEASE_ROOTS = tuple(root for root in RELEASE_ROOTS if root.kind == "file")
TREE_RELEASE_ROOTS = tuple(root for root in RELEASE_ROOTS if root.kind == "tree")

_EXACT_BY_PATH = {root.path: root for root in EXACT_RELEASE_ROOTS}
_OID = re.compile(r"[0-9a-f]{40}")
_TREE_FILE_MODES = frozenset({"100644", "100755"})


def _tree_owner(path: PurePosixPath) -> ReleaseRoot | None:
    return next(
        (
            root
            for root in TREE_RELEASE_ROOTS
            if path != root.path and path.is_relative_to(root.path)
        ),
        None,
    )


def validate_release_listing(listing: bytes) -> tuple[ReleaseBlob, ...]:
    """Validate raw ``git ls-tree -r -z`` output against the closed inventory."""

    blobs: list[ReleaseBlob] = []
    seen: set[PurePosixPath] = set()
    populated_trees: set[PurePosixPath] = set()
    try:
        records = listing.split(b"\0")
        for record in records:
            if not record:
                continue
            metadata, raw_path = record.split(b"\t", 1)
            raw_mode, kind, raw_oid = metadata.split(b" ", 2)
            rendered = raw_path.decode("utf-8")
            path = PurePosixPath(rendered)
            mode = raw_mode.decode("ascii")
            oid = raw_oid.decode("ascii")
            exact = _EXACT_BY_PATH.get(path)
            tree = _tree_owner(path)
            if (
                not path.parts
                or path.is_absolute()
                or ".." in path.parts
                or rendered != path.as_posix()
                or path in seen
                or kind != b"blob"
                or _OID.fullmatch(oid) is None
                or (
                    exact is not None
                    and (mode != exact.mode or tree is not None)
                )
                or (
                    exact is None
                    and (tree is None or mode not in _TREE_FILE_MODES)
                )
            ):
                raise ValueError
            seen.add(path)
            if tree is not None:
                populated_trees.add(tree.path)
            blobs.append(ReleaseBlob(path, oid, mode))
    except (UnicodeDecodeError, ValueError):
        raise ValueError("invalid release tree listing") from None

    if (
        not {root.path for root in EXACT_RELEASE_ROOTS} <= seen
        or populated_trees != {root.path for root in TREE_RELEASE_ROOTS}
    ):
        raise ValueError("incomplete release tree listing")
    return tuple(blobs)


def release_file_modes(path: PurePosixPath) -> frozenset[int]:
    """Return the allowed archive modes for an owned file."""

    exact = _EXACT_BY_PATH.get(path)
    if exact is not None:
        assert exact.mode is not None
        return frozenset({0o755 if exact.mode == "100755" else 0o644})
    return frozenset({0o644, 0o755}) if _tree_owner(path) is not None else frozenset()


def release_directory(path: PurePosixPath) -> bool:
    """Return whether an archive directory is owned or an owned-path ancestor."""

    if path in _EXACT_BY_PATH:
        return False
    return any(
        path == root.path
        or path.is_relative_to(root.path)
        or root.path.is_relative_to(path)
        for root in TREE_RELEASE_ROOTS
    ) or any(
        path != root.path and root.path.is_relative_to(path)
        for root in EXACT_RELEASE_ROOTS
    )
