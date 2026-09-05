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
PREPARE_REVIEW_DIFF_ACTION_ROOT = ReleaseRoot(
    PurePosixPath(".github/actions/prepare-review-diff/action.yml"),
    "file",
    "100644",
)
PREPARE_REVIEW_DIFF_HELPER_ROOT = ReleaseRoot(
    PurePosixPath(".github/actions/prepare-review-diff/prepare_review_diff.py"),
    "file",
    "100644",
)
CANONICALIZE_REVIEW_ACTION_ROOT = ReleaseRoot(
    PurePosixPath(".github/actions/canonicalize-review/action.yml"),
    "file",
    "100644",
)
CANONICALIZE_REVIEW_HELPER_ROOT = ReleaseRoot(
    PurePosixPath(".github/actions/canonicalize-review/canonicalize_review.py"),
    "file",
    "100644",
)
REVIEW_SCOPE_HELPER_ROOT = ReleaseRoot(
    PurePosixPath(".github/actions/canonicalize-review/review_scope.py"),
    "file",
    "100644",
)
REVIEW_INVOCATION_BUDGET_ACTION_ROOT = ReleaseRoot(
    PurePosixPath(".github/actions/review-invocation-budget/action.yml"),
    "file",
    "100644",
)
REVIEW_INVOCATION_BUDGET_HELPER_ROOT = ReleaseRoot(
    PurePosixPath(
        ".github/actions/review-invocation-budget/review_invocation_budget.py"
    ),
    "file",
    "100644",
)
REVIEW_POLICY_ACTION_ROOT = ReleaseRoot(
    PurePosixPath(".github/actions/resolve-review-policy/action.yml"),
    "file",
    "100644",
)
REVIEW_POLICY_HELPER_ROOT = ReleaseRoot(
    PurePosixPath(
        ".github/actions/resolve-review-policy/resolve_review_policy.py"
    ),
    "file",
    "100644",
)

HISTORICAL_RELEASE_ROOTS = (
    CENTRAL_WORKFLOW_ROOT,
    SETUP_GEMINI_AUTH_ROOT,
    CANONICAL_WORKFLOW_ROOT,
    WORKFLOW_CATALOG_ROOT,
    WORKFLOW_CONFIG_ROOT,
)
PREPARE_REVIEW_DIFF_ROOTS = (
    PREPARE_REVIEW_DIFF_ACTION_ROOT,
    PREPARE_REVIEW_DIFF_HELPER_ROOT,
)
CANONICALIZE_REVIEW_ROOTS = (
    CANONICALIZE_REVIEW_ACTION_ROOT,
    CANONICALIZE_REVIEW_HELPER_ROOT,
    REVIEW_SCOPE_HELPER_ROOT,
)
REVIEW_INVOCATION_BUDGET_ROOTS = (
    REVIEW_INVOCATION_BUDGET_ACTION_ROOT,
    REVIEW_INVOCATION_BUDGET_HELPER_ROOT,
)
REVIEW_POLICY_ROOTS = (
    REVIEW_POLICY_ACTION_ROOT,
    REVIEW_POLICY_HELPER_ROOT,
)
RELEASE_ROOTS = (
    HISTORICAL_RELEASE_ROOTS
    + PREPARE_REVIEW_DIFF_ROOTS
    + CANONICALIZE_REVIEW_ROOTS
    + REVIEW_INVOCATION_BUDGET_ROOTS
    + REVIEW_POLICY_ROOTS
)
RELEASE_PATHS = tuple(root.path.as_posix() for root in RELEASE_ROOTS)
EXACT_RELEASE_ROOTS = tuple(root for root in RELEASE_ROOTS if root.kind == "file")
TREE_RELEASE_ROOTS = tuple(root for root in RELEASE_ROOTS if root.kind == "tree")

_OID = re.compile(r"[0-9a-f]{40}")
_TREE_FILE_MODES = frozenset({"100644", "100755"})
_RELEASE_REF = re.compile(r"v[0-9]+(?:\.[0-9]+)+")
PREPARE_REVIEW_DIFF_RELEASE = (1, 45)
CANONICALIZE_REVIEW_RELEASE = (1, 46)
REVIEW_INVOCATION_BUDGET_RELEASE = (1, 47)
REVIEW_POLICY_RELEASE = (1, 51)
REVIEW_OPTIN_RELEASE = (1, 59)
REVIEW_ROUNDS_VARIABLE_RELEASE = (1, 60)
SAME_HEAD_CANCEL_RELEASE = (1, 61)
FILTER_REASON_SURFACE_RELEASE = (1, 62)
FINDING_DISMISSAL_RELEASE = (1, 63)
LABEL_REVIEW_TRIGGER_RELEASE = (1, 64)
SKIP_REASON_NOTICE_RELEASE = (1, 65)
LABEL_MISMATCH_DECLINE_RELEASE = (1, 66)


def _release_version(ref: str) -> tuple[int, ...]:
    if _RELEASE_REF.fullmatch(ref) is None:
        raise ValueError("invalid release ref")
    return tuple(int(part) for part in ref.removeprefix("v").split("."))


def release_supports_prepare_review_diff(ref: str) -> bool:
    """Return whether ``ref`` owns and may use the shared review-diff action."""

    return _release_version(ref) >= PREPARE_REVIEW_DIFF_RELEASE


def release_supports_canonicalize_review(ref: str) -> bool:
    """Return whether ``ref`` owns and may use the shared canonicalizer."""

    return _release_version(ref) >= CANONICALIZE_REVIEW_RELEASE


def release_supports_review_invocation_budget(ref: str) -> bool:
    """Return whether ``ref`` owns the review invocation-budget action."""

    return _release_version(ref) >= REVIEW_INVOCATION_BUDGET_RELEASE


def release_supports_review_policy(ref: str) -> bool:
    """Return whether ``ref`` owns the deterministic review-policy action."""

    return _release_version(ref) >= REVIEW_POLICY_RELEASE


def release_supports_review_optin(ref: str) -> bool:
    """Return whether ``ref`` resolves an unconfigured automatic review to ``false``."""

    return _release_version(ref) >= REVIEW_OPTIN_RELEASE


def release_supports_review_rounds_variable(ref: str) -> bool:
    """Return whether ``ref`` reads the automatic-round budget from a repository variable."""

    return _release_version(ref) >= REVIEW_ROUNDS_VARIABLE_RELEASE


def release_supports_same_head_cancel_guard(ref: str) -> bool:
    """Return whether ``ref`` cancels a review only when a new commit supersedes it."""

    return _release_version(ref) >= SAME_HEAD_CANCEL_RELEASE


def release_supports_filter_reason_surface(ref: str) -> bool:
    """Return whether ``ref`` surfaces filtered-finding reasons and refuses OpenCode overrides."""

    return _release_version(ref) >= FILTER_REASON_SURFACE_RELEASE


def release_supports_finding_dismissal(ref: str) -> bool:
    """Return whether ``ref`` lets a write collaborator dismiss a finding by comment."""

    return _release_version(ref) >= FINDING_DISMISSAL_RELEASE


def release_supports_label_review_trigger(ref: str) -> bool:
    """Return whether ``ref``'s managed callers start a review when `review:request` is added."""

    return _release_version(ref) >= LABEL_REVIEW_TRIGGER_RELEASE


def release_supports_skip_reason_notice(ref: str) -> bool:
    """Return whether ``ref``'s skipped notices name the reason the review declined."""

    return _release_version(ref) >= SKIP_REASON_NOTICE_RELEASE


def release_supports_label_mismatch_decline(ref: str) -> bool:
    """Return whether ``ref`` declines, rather than fails, an event-triggered label change."""

    return _release_version(ref) >= LABEL_MISMATCH_DECLINE_RELEASE


def release_roots_for(ref: str) -> tuple[ReleaseRoot, ...]:
    """Select the authenticated inventory that existed at ``ref``'s release line."""
    roots = HISTORICAL_RELEASE_ROOTS
    if release_supports_prepare_review_diff(ref):
        roots += PREPARE_REVIEW_DIFF_ROOTS
    if release_supports_canonicalize_review(ref):
        roots += CANONICALIZE_REVIEW_ROOTS
    if release_supports_review_invocation_budget(ref):
        roots += REVIEW_INVOCATION_BUDGET_ROOTS
    if release_supports_review_policy(ref):
        roots += REVIEW_POLICY_ROOTS
    return roots


def release_paths_for(ref: str) -> tuple[str, ...]:
    return tuple(root.path.as_posix() for root in release_roots_for(ref))


def _exact_by_path(roots: tuple[ReleaseRoot, ...]) -> dict[PurePosixPath, ReleaseRoot]:
    return {root.path: root for root in roots if root.kind == "file"}


def _tree_roots(roots: tuple[ReleaseRoot, ...]) -> tuple[ReleaseRoot, ...]:
    return tuple(root for root in roots if root.kind == "tree")


def _tree_owner(path: PurePosixPath, roots: tuple[ReleaseRoot, ...]) -> ReleaseRoot | None:
    return next(
        (
            root
            for root in _tree_roots(roots)
            if path != root.path and path.is_relative_to(root.path)
        ),
        None,
    )


def validate_release_listing(
    listing: bytes, roots: tuple[ReleaseRoot, ...] = RELEASE_ROOTS
) -> tuple[ReleaseBlob, ...]:
    """Validate raw ``git ls-tree -r -z`` output against the closed inventory."""

    blobs: list[ReleaseBlob] = []
    seen: set[PurePosixPath] = set()
    populated_trees: set[PurePosixPath] = set()
    exact_by_path = _exact_by_path(roots)
    tree_roots = _tree_roots(roots)
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
            exact = exact_by_path.get(path)
            tree = _tree_owner(path, roots)
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
        not {root.path for root in exact_by_path.values()} <= seen
        or populated_trees != {root.path for root in tree_roots}
    ):
        raise ValueError("incomplete release tree listing")
    return tuple(blobs)


def release_file_modes(
    path: PurePosixPath, roots: tuple[ReleaseRoot, ...] = RELEASE_ROOTS
) -> frozenset[int]:
    """Return the allowed archive modes for an owned file."""

    exact = _exact_by_path(roots).get(path)
    if exact is not None:
        assert exact.mode is not None
        return frozenset({0o755 if exact.mode == "100755" else 0o644})
    return (
        frozenset({0o644, 0o755})
        if _tree_owner(path, roots) is not None
        else frozenset()
    )


def release_directory(
    path: PurePosixPath, roots: tuple[ReleaseRoot, ...] = RELEASE_ROOTS
) -> bool:
    """Return whether an archive directory is owned or an owned-path ancestor."""

    exact_by_path = _exact_by_path(roots)
    tree_roots = _tree_roots(roots)
    if path in exact_by_path:
        return False
    return any(
        path == root.path
        or path.is_relative_to(root.path)
        or root.path.is_relative_to(path)
        for root in tree_roots
    ) or any(
        path != root.path and root.path.is_relative_to(path)
        for root in exact_by_path.values()
    )
