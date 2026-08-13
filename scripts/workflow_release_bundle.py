#!/usr/bin/env python3
"""Materialize a verified workflow release tag into a temporary bundle."""

from __future__ import annotations

from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath
import tarfile
import tempfile
from typing import Iterator

from scripts.verify_workflow_release import (
    AnnotatedTag,
    ReleaseVerificationError,
    VerifiedCommitTree,
    assert_tag_unchanged,
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
from scripts.workflow_release_inventory import (
    RELEASE_PATHS,
    release_directory,
    release_file_modes,
    validate_release_listing,
)


@dataclass(frozen=True)
class ReleaseBundle:
    root: Path
    ref: str
    commit: str
    catalog: WorkflowCatalog
    config: FleetConfig
    canonical: Path


def _release_owned(path: PurePosixPath, *, directory: bool) -> bool:
    return release_directory(path) if directory else bool(release_file_modes(path))


def _build_git_archive(
    automation: Path,
    revision: str,
    *,
    tree: VerifiedCommitTree | None = None,
) -> bytes:
    verified = tree if tree is not None else VerifiedCommitTree.open(automation, revision)
    if verified.commit != revision:
        raise ReleaseVerificationError("unable to archive verified release")
    entries = validate_release_listing(verified.listing(RELEASE_PATHS))

    output = BytesIO()
    with tarfile.open(
        fileobj=output, mode="w", format=tarfile.USTAR_FORMAT
    ) as archive:
        for entry in sorted(entries, key=lambda item: str(item.path)):
            payload = verified.reader.read(entry.oid, "blob")
            member = tarfile.TarInfo(str(entry.path))
            member.size = len(payload)
            member.mode = entry.archive_mode
            member.mtime = 0
            member.uid = 0
            member.gid = 0
            member.uname = ""
            member.gname = ""
            archive.addfile(member, BytesIO(payload))
    return output.getvalue()


def _git_archive(
    automation: Path,
    revision: str,
    *,
    tree: VerifiedCommitTree | None = None,
) -> bytes:
    archive: bytes | None = None
    try:
        archive = _build_git_archive(automation, revision, tree=tree)
    except (OSError, UnicodeDecodeError, ValueError, ReleaseVerificationError):
        pass
    if archive is None:
        raise ReleaseVerificationError("unable to archive verified release") from None
    return archive


def _safe_member(member: tarfile.TarInfo) -> PurePosixPath:
    path = PurePosixPath(member.name)
    allowed_modes = release_file_modes(path)
    safe = (
        bool(path.parts)
        and not path.is_absolute()
        and ".." not in path.parts
        and _release_owned(path, directory=member.isdir())
        and (member.isdir() or member.isreg())
        and (member.isdir() or member.mode in allowed_modes)
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
    tag: AnnotatedTag = resolve_annotated_tag(automation, ref)
    if remote is not None:
        verify_remote_tag(automation, remote, tag)
    verified_tree = verify_tag_content(automation, ref, tag=tag)

    with tempfile.TemporaryDirectory(prefix="workflow-release-") as temporary:
        root = Path(temporary)
        _extract_archive(
            _git_archive(automation, tag.commit, tree=verified_tree), root
        )
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
