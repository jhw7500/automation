#!/usr/bin/env python3
"""Materialize a verified workflow release tag into a temporary bundle."""

from __future__ import annotations

from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import tempfile
from typing import Iterator

from scripts.verify_workflow_release import (
    AnnotatedTag,
    GIT_EXECUTABLE,
    ReleaseVerificationError,
    assert_tag_unchanged,
    git_child_env,
    resolve_annotated_tag,
    verify_remote_tag,
    verify_tag_content,
)
from scripts.workflow_catalog import (
    CatalogError,
    FleetConfig,
    WorkflowCatalog,
    load_catalog,
    load_fleet_config,
)


RELEASE_PATHS = (
    ".github/workflows",
    ".github/actions/setup-gemini-auth/action.yml",
    "examples/baseline-workflows/.github",
    "scripts/workflow-catalog.json",
    "scripts/workflow-config.json",
)
_RELEASE_ROOTS = tuple(PurePosixPath(path) for path in RELEASE_PATHS)


@dataclass(frozen=True)
class ReleaseBundle:
    root: Path
    ref: str
    commit: str
    catalog: WorkflowCatalog
    config: FleetConfig
    canonical: Path


def _release_owned(path: PurePosixPath, *, directory: bool) -> bool:
    return any(
        path == release_root or path.is_relative_to(release_root)
        for release_root in _RELEASE_ROOTS
    ) or (
        directory
        and any(release_root.is_relative_to(path) for release_root in _RELEASE_ROOTS)
    )


def _git_archive(automation: Path, revision: str) -> bytes:
    result: subprocess.CompletedProcess[bytes] | None = None
    try:
        result = subprocess.run(
            [
                GIT_EXECUTABLE,
                "-C",
                str(automation),
                "archive",
                "--format=tar",
                revision,
                *RELEASE_PATHS,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=git_child_env(),
        )
    except (OSError, ValueError):
        pass
    if result is None:
        raise ReleaseVerificationError(
            "unable to archive verified release (rc=unavailable)"
        ) from None
    if result.returncode != 0:
        raise ReleaseVerificationError(
            f"unable to archive verified release (rc={result.returncode})"
        ) from None
    return result.stdout


def _safe_member(member: tarfile.TarInfo) -> PurePosixPath:
    path = PurePosixPath(member.name)
    safe = (
        bool(path.parts)
        and not path.is_absolute()
        and ".." not in path.parts
        and _release_owned(path, directory=member.isdir())
        and (member.isdir() or member.isreg())
        and not member.issym()
        and not member.islnk()
    )
    if not safe:
        raise ReleaseVerificationError(f"unsafe archive member: {member.name}")
    return path


def _extract_archive(archive_bytes: bytes, destination: Path) -> None:
    try:
        with tarfile.open(fileobj=BytesIO(archive_bytes), mode="r:") as archive:
            members = tuple(archive.getmembers())
            paths = tuple(_safe_member(member) for member in members)
            for member, relative in zip(members, paths):
                target = destination.joinpath(*relative.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                source = archive.extractfile(member)
                if source is None:
                    raise ReleaseVerificationError(
                        f"unable to read archive member: {member.name}"
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as handle:
                    handle.write(source.read())
    except (tarfile.TarError, OSError) as exc:
        raise ReleaseVerificationError(f"unable to safely extract release: {exc}") from exc


@contextmanager
def _materialize(
    automation: Path, ref: str, *, remote: str | None
) -> Iterator[ReleaseBundle]:
    automation = automation.resolve()
    tag: AnnotatedTag = resolve_annotated_tag(automation, ref)
    if remote is not None:
        verify_remote_tag(automation, remote, tag)
    verify_tag_content(automation, ref, tag=tag)

    with tempfile.TemporaryDirectory(prefix="workflow-release-") as temporary:
        root = Path(temporary)
        _extract_archive(_git_archive(automation, tag.commit), root)
        try:
            catalog = load_catalog(root)
            config = load_fleet_config(root, catalog)
        except CatalogError as exc:
            raise ReleaseVerificationError(
                f"tag {ref} contains an invalid release inventory: {exc}"
            ) from exc
        canonical = root.joinpath(*config.canonical_dir.parts)
        if not canonical.is_dir():
            raise ReleaseVerificationError(
                f"tag {ref} canonical path is missing: {config.canonical_dir}"
            )
        assert_tag_unchanged(automation, tag)
        yield ReleaseBundle(root, ref, tag.commit, catalog, config, canonical)
        assert_tag_unchanged(automation, tag)


def materialize_release_bundle(
    automation: Path, ref: str, *, remote: str | None
) -> AbstractContextManager[ReleaseBundle]:
    """Verify and extract release-owned content from an annotated tag."""

    return _materialize(automation, ref, remote=remote)
