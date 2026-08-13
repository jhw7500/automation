"""Tests for immutable, safely extracted workflow release bundles."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import json
import shutil
import subprocess
import tarfile

import pytest


ROOT = Path(__file__).resolve().parents[1]

from scripts.verify_workflow_release import ReleaseVerificationError
import scripts.workflow_release_bundle as release_bundle
from scripts.workflow_release_bundle import materialize_release_bundle


RELEASE_PATHS = (
    ".github/workflows",
    ".github/actions/setup-gemini-auth/action.yml",
    "examples/baseline-workflows/.github",
    "scripts/workflow-catalog.json",
    "scripts/workflow-config.json",
)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()


def commit(repo: Path, message: str) -> str:
    git(repo, "add", ".")
    git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def release_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "automation"
    repo.mkdir()
    for relative in RELEASE_PATHS:
        source = ROOT / relative
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    release_commit = commit(repo, "release")
    git(repo, "tag", "-a", "v1.40", "-m", "v1.40")
    return repo, release_commit


def retag(repo: Path, ref: str, *, annotated: bool = True) -> str:
    release_commit = commit(repo, ref)
    args = ("tag", "-a", ref, "-m", ref) if annotated else ("tag", ref)
    git(repo, *args)
    return release_commit


def archive_with(member: tarfile.TarInfo, payload: bytes = b"bad") -> bytes:
    stream = BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        if member.isreg():
            member.size = len(payload)
            archive.addfile(member, BytesIO(payload))
        else:
            archive.addfile(member)
    return stream.getvalue()


def test_bundle_reads_catalog_config_and_canonical_tree_from_tag(
    release_repo: tuple[Path, str],
) -> None:
    repo, release_commit = release_repo
    config_path = repo / "scripts/workflow-config.json"
    changed = json.loads(config_path.read_text(encoding="utf-8"))
    changed["automation_ref"] = "v9.99"
    changed["repos"]["outside-tag"] = changed["repos"]["gstApp"]
    config_path.write_text(json.dumps(changed), encoding="utf-8")
    (repo / "examples/baseline-workflows/.github/workflows/claude.yml").unlink()
    commit(repo, "newer working tree")

    with materialize_release_bundle(repo, "v1.40", remote=None) as bundle:
        extracted = bundle.root
        assert bundle.ref == "v1.40"
        assert bundle.commit == release_commit
        assert bundle.config.automation_ref == "v1.40"
        assert "outside-tag" not in bundle.config.profiles
        assert (bundle.canonical / "workflows/claude.yml").is_file()
        assert bundle.canonical.is_relative_to(bundle.root)

    assert not extracted.exists()


def test_bundle_rejects_lightweight_tag(release_repo: tuple[Path, str]) -> None:
    repo, _ = release_repo
    git(repo, "tag", "v1.41")
    with pytest.raises(ReleaseVerificationError, match="annotated tag"):
        with materialize_release_bundle(repo, "v1.41", remote=None):
            pass


def test_bundle_rejects_local_remote_tag_mismatch(
    release_repo: tuple[Path, str], tmp_path: Path
) -> None:
    repo, _ = release_repo
    remote = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "origin", "v1.40")
    (repo / "new").write_text("new", encoding="utf-8")
    commit(repo, "new release")
    git(repo, "tag", "-d", "v1.40")
    git(repo, "tag", "-a", "v1.40", "-m", "local replacement")

    with pytest.raises(ReleaseVerificationError, match="remote tag.*expected commit"):
        with materialize_release_bundle(repo, "v1.40", remote="origin"):
            pass


def test_bundle_rejects_tag_changed_during_verification(
    release_repo: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _ = release_repo
    verify_tag_content = release_bundle.verify_tag_content

    def move_tag(automation: Path, ref: str) -> None:
        verify_tag_content(automation, ref)
        (automation / "new").write_text("new", encoding="utf-8")
        commit(automation, "move release")
        git(automation, "tag", "-d", ref)
        git(automation, "tag", "-a", ref, "-m", "moved")

    monkeypatch.setattr(release_bundle, "verify_tag_content", move_tag)
    with pytest.raises(ReleaseVerificationError, match="changed during verification"):
        with materialize_release_bundle(repo, "v1.40", remote=None):
            pass


def test_bundle_rejects_absent_canonical_path(
    release_repo: tuple[Path, str],
) -> None:
    repo, _ = release_repo
    (repo / "examples/baseline-workflows/.github/workflows/claude.yml").unlink()
    retag(repo, "v1.41")
    with pytest.raises(ReleaseVerificationError, match="canonical"):
        with materialize_release_bundle(repo, "v1.41", remote=None):
            pass


def test_bundle_rejects_profile_inventory_outside_tag(
    release_repo: tuple[Path, str],
) -> None:
    repo, _ = release_repo
    config_path = repo / "scripts/workflow-config.json"
    changed = json.loads(config_path.read_text(encoding="utf-8"))
    changed["repos"]["outside-tag"] = changed["repos"]["gstApp"]
    config_path.write_text(json.dumps(changed), encoding="utf-8")
    retag(repo, "v1.41")

    with pytest.raises(ReleaseVerificationError, match="repository set"):
        with materialize_release_bundle(repo, "v1.41", remote=None):
            pass


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("../escape", "file"),
        ("/absolute", "file"),
        ("not-release-owned/file", "file"),
        ("scripts/not-release-owned.txt", "file"),
        (".github/workflows/link", "symlink"),
        (".github/workflows/hardlink", "hardlink"),
    ],
    ids=(
        "parent",
        "absolute",
        "unexpected-top-level",
        "unexpected-release-sibling",
        "symlink",
        "hardlink",
    ),
)
def test_bundle_rejects_unsafe_archive_members(
    release_repo: tuple[Path, str],
    name: str,
    kind: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _ = release_repo
    member = tarfile.TarInfo(name)
    if kind == "symlink":
        member.type = tarfile.SYMTYPE
        member.linkname = "target"
    if kind == "hardlink":
        member.type = tarfile.LNKTYPE
        member.linkname = ".github/workflows/claude.yml"
    malicious = archive_with(member)
    monkeypatch.setattr(
        "scripts.workflow_release_bundle._git_archive",
        lambda automation, ref: malicious,
    )

    with pytest.raises(ReleaseVerificationError, match="unsafe archive member"):
        with materialize_release_bundle(repo, "v1.40", remote=None):
            pass
