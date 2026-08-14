"""Tests for immutable, safely extracted workflow release bundles."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import json
import shutil
import subprocess
import tarfile
import traceback
import zlib

import pytest

from scripts.verify_workflow_release import ReleaseVerificationError
import scripts.verify_workflow_release as release_verifier
import scripts.workflow_release_bundle as release_bundle
from scripts.workflow_release_bundle import materialize_release_bundle
from scripts.workflow_release_inventory import EXACT_RELEASE_ROOTS, RELEASE_PATHS

ROOT = Path(__file__).resolve().parents[1]

EXACT_RELEASE_FILES = tuple(
    root.path.as_posix() for root in EXACT_RELEASE_ROOTS
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


def common_git_dir(repo: Path) -> Path:
    value = Path(git(repo, "rev-parse", "--git-common-dir"))
    return value if value.is_absolute() else (repo / value).resolve()


def raw_git_object(repo: Path, kind: str, oid: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repo), "cat-file", kind, oid],
        check=True,
        stdout=subprocess.PIPE,
    ).stdout


def replace_loose_object_payload(
    repo: Path, oid: str, kind: str, payload: bytes
) -> None:
    object_path = common_git_dir(repo) / "objects" / oid[:2] / oid[2:]
    assert object_path.is_file(), f"expected loose test object: {oid}"
    header = f"{kind} {len(payload)}\0".encode("ascii")
    object_path.chmod(0o600)
    object_path.write_bytes(zlib.compress(header + payload))


def install_local_release_filter_attack(
    repo: Path, tmp_path: Path, *, target: str
) -> tuple[Path, bytes]:
    common = common_git_dir(repo)
    provider = tmp_path / "LOCAL-PROVIDER-SECRET"
    provider.write_text("LOCAL-PROVIDER-SECRET", encoding="utf-8")
    marker = tmp_path / "archive-local-provider-read"
    substituted = b'{"substituted": "LOCAL-PROVIDER-SECRET"}\n'
    helper = tmp_path / "archive-local-filter-helper"
    helper.write_text(
        "#!/bin/sh\n"
        f"/bin/cat {provider} > {marker}\n"
        "/bin/cat >/dev/null\n"
        f"/usr/bin/printf '%s' '{substituted.decode().strip()}'\n",
        encoding="utf-8",
    )
    helper.chmod(0o755)
    included = tmp_path / "archive-local-provider.gitconfig"
    included.write_text(
        '[filter "local-provider"]\n'
        f"\tsmudge = {helper}\n"
        "\trequired = true\n"
        "[core]\n"
        f"\tsshCommand = {helper}\n"
        "[credential]\n"
        f"\thelper = !{helper}\n",
        encoding="utf-8",
    )
    with (common / "config").open("a", encoding="utf-8") as config:
        config.write(f"\n[include]\n\tpath = {included}\n")
    info = common / "info"
    info.mkdir(exist_ok=True)
    with (info / "attributes").open("a", encoding="utf-8") as attributes:
        attributes.write(f"{target} filter=local-provider\n")
    return marker, substituted


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


def alternate_tag_object(repo: Path) -> str:
    (repo / "race-marker").write_text("alternate", encoding="utf-8")
    commit(repo, "alternate release")
    git(repo, "tag", "-a", "race-target", "-m", "race target")
    return git(repo, "rev-parse", "refs/tags/race-target")


def archive_with(member: tarfile.TarInfo, payload: bytes = b"bad") -> bytes:
    stream = BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        if member.isreg():
            member.size = len(payload)
            archive.addfile(member, BytesIO(payload))
        else:
            archive.addfile(member)
    return stream.getvalue()


def test_release_archive_uses_only_authenticated_tree_and_blob_reads(
    release_repo: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, release_commit = release_repo
    original = release_verifier.subprocess.run

    def authenticated_only(args, **kwargs):
        if args[0] == "/usr/bin/git":
            assert args[1:] == ["cat-file", "--batch"]
        return original(args, **kwargs)

    monkeypatch.setattr(release_verifier.subprocess, "run", authenticated_only)

    assert release_bundle._git_archive(repo, release_commit)


def test_release_archive_rejects_semantically_valid_blob_at_wrong_object_name(
    release_repo: tuple[Path, str],
) -> None:
    repo, release_commit = release_repo
    path = ".github/workflows/claude.yml"
    oid = git(repo, "rev-parse", f"{release_commit}:{path}")
    payload = raw_git_object(repo, "blob", oid) + b"\n# checksum mismatch\n"
    replace_loose_object_payload(repo, oid, "blob", payload)

    with pytest.raises(ReleaseVerificationError, match="archive verified release"):
        release_bundle._git_archive(repo, release_commit)


@pytest.mark.parametrize("layout", ("loose", "packed", "linked"))
def test_authenticated_release_archive_supports_normal_storage_layouts(
    release_repo: tuple[Path, str], tmp_path: Path, layout: str
) -> None:
    repo, release_commit = release_repo
    checkout = repo
    if layout == "packed":
        git(repo, "repack", "-ad")
        git(repo, "prune-packed")
        assert not (
            common_git_dir(repo)
            / "objects"
            / release_commit[:2]
            / release_commit[2:]
        ).exists()
    elif layout == "linked":
        checkout = tmp_path / "linked-authenticated-archive"
        git(repo, "worktree", "add", "--detach", str(checkout), release_commit)

    assert release_bundle._git_archive(checkout, release_commit)


@pytest.mark.parametrize(
    "mutation",
    ("missing", "directory-collision", "executable-mode", "gitlink"),
)
@pytest.mark.parametrize("relative", EXACT_RELEASE_FILES)
def test_release_archive_requires_each_exact_file_as_one_0644_blob(
    release_repo: tuple[Path, str], relative: str, mutation: str
) -> None:
    repo, release_commit = release_repo
    target = repo / relative
    if mutation == "missing":
        target.unlink()
        bad_commit = commit(repo, f"remove {relative}")
    elif mutation == "directory-collision":
        target.unlink()
        target.mkdir()
        (target / "dummy").write_text("not the release file\n", encoding="utf-8")
        bad_commit = commit(repo, f"replace {relative} with a directory")
    elif mutation == "executable-mode":
        target.chmod(0o755)
        bad_commit = commit(repo, f"make {relative} executable")
    else:
        target.unlink()
        git(repo, "add", "-u", "--", relative)
        git(
            repo,
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{release_commit},{relative}",
        )
        git(repo, "commit", "-qm", f"replace {relative} with a gitlink")
        bad_commit = git(repo, "rev-parse", "HEAD")

    with pytest.raises(ReleaseVerificationError, match="archive verified release"):
        release_bundle._git_archive(repo, bad_commit)


def test_release_archive_rejects_lexical_parent_descendant(
    release_repo: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, release_commit = release_repo
    oid = "f" * 40
    original = release_verifier.VerifiedCommitTree.listing

    def malicious_listing(
        tree: release_verifier.VerifiedCommitTree, paths: object
    ) -> bytes:
        return original(tree, paths) + (
            f"100644 blob {oid}\t.github/workflows/../escape\0".encode()
        )

    monkeypatch.setattr(
        release_verifier.VerifiedCommitTree, "listing", malicious_listing
    )

    with pytest.raises(ReleaseVerificationError, match="archive verified release"):
        release_bundle._git_archive(repo, release_commit)


def test_bundle_rejects_action_file_replaced_by_directory_and_dummy_blob(
    release_repo: tuple[Path, str],
) -> None:
    repo, _ = release_repo
    action = repo / ".github/actions/setup-gemini-auth/action.yml"
    action.unlink()
    action.mkdir()
    (action / "dummy").write_text("not a composite action\n", encoding="utf-8")
    retag(repo, "v1.41")

    with pytest.raises(ReleaseVerificationError, match="release inventory"):
        with materialize_release_bundle(repo, "v1.41", remote=None):
            pass


def test_release_archive_ignores_host_user_and_xdg_git_includes(
    release_repo: tuple[Path, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, release_commit = release_repo
    marker = tmp_path / "archive-host-config-command-ran"
    provider_token = tmp_path / "provider-token"
    provider_token.write_text("examples/** export-ignore\n", encoding="utf-8")
    helper = tmp_path / "host-command"
    helper.write_text(
        "#!/bin/sh\n"
        f"/bin/cat {provider_token} > {marker}\n"
        "exit 93\n",
        encoding="utf-8",
    )
    helper.chmod(0o755)
    included = tmp_path / "provider.gitconfig"
    included.write_text(
        f"[core]\n\tsshCommand = {helper}\n"
        f"\tattributesFile = {provider_token}\n"
        f"[credential]\n\thelper = !{helper}\n",
        encoding="utf-8",
    )
    home = tmp_path / "host-home"
    xdg = tmp_path / "host-xdg"
    home.mkdir()
    (xdg / "git").mkdir(parents=True)
    (home / ".gitconfig").write_text(
        f"[include]\n\tpath = {included}\n", encoding="utf-8"
    )
    (xdg / "git/config").write_text(
        f"[include]\n\tpath = {included}\n", encoding="utf-8"
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(included))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(included))

    archive = release_bundle._git_archive(repo, release_commit)

    assert archive
    with tarfile.open(fileobj=BytesIO(archive), mode="r:") as stream:
        names = set(stream.getnames())
    assert "examples/baseline-workflows/.github/workflows/claude.yml" in names
    assert not marker.exists()


def test_release_archive_ignores_source_local_filter_and_info_attributes(
    release_repo: tuple[Path, str], tmp_path: Path
) -> None:
    repo, release_commit = release_repo
    target = "scripts/workflow-config.json"
    expected = subprocess.run(
        ["/usr/bin/git", "-C", str(repo), "show", f"{release_commit}:{target}"],
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    marker, substituted = install_local_release_filter_attack(
        repo, tmp_path, target=target
    )

    archive = release_bundle._git_archive(repo, release_commit)

    with tarfile.open(fileobj=BytesIO(archive), mode="r:") as stream:
        archived = stream.extractfile(target)
        assert archived is not None
        payload = archived.read()
    assert payload == expected
    assert payload != substituted
    assert not marker.exists()


def test_bundle_uses_original_commit_tree_despite_replace_ref(
    release_repo: tuple[Path, str],
) -> None:
    repo, release_commit = release_repo
    original = json.loads(
        git(repo, "show", f"{release_commit}:scripts/workflow-config.json")
    )
    config_path = repo / "scripts/workflow-config.json"
    replacement = json.loads(config_path.read_text(encoding="utf-8"))
    replacement["automation_ref"] = "v9.99"
    config_path.write_text(json.dumps(replacement) + "\n", encoding="utf-8")
    alternate = commit(repo, "replacement payload")
    git(repo, "replace", release_commit, alternate)

    with materialize_release_bundle(repo, "v1.40", remote=None) as bundle:
        extracted = json.loads(
            (bundle.root / "scripts/workflow-config.json").read_text(encoding="utf-8")
        )
        assert bundle.commit == release_commit
        assert extracted == original


def test_release_archive_failure_does_not_expose_child_or_provider_data(
    release_repo: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _ = release_repo
    provider = "archive-provider-sentinel"
    raw = "archive-raw-child-sentinel"
    def child(_repo: Path, *_args: str) -> bytes:
        raise ReleaseVerificationError(f"{provider} {raw}")

    monkeypatch.setattr(release_verifier, "read_git_object", child)
    with pytest.raises(ReleaseVerificationError) as raised:
        release_bundle._git_archive(repo, raw)

    rendered = "".join(
        traceback.format_exception(
            type(raised.value), raised.value, raised.value.__traceback__
        )
    )
    assert provider not in rendered
    assert raw not in rendered
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


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
    release_repo: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, release_commit = release_repo
    original_tag = git(repo, "rev-parse", "refs/tags/v1.40")
    git(
        repo,
        "remote",
        "add",
        "origin",
        "https://github.com/jhw7500/automation.git",
    )
    (repo / "new").write_text("new", encoding="utf-8")
    commit(repo, "new release")
    git(repo, "tag", "-d", "v1.40")
    git(repo, "tag", "-a", "v1.40", "-m", "local replacement")

    def remote_git(_url: str, *_refs: str) -> str:
        return (
            f"{original_tag}\trefs/tags/v1.40\n"
            f"{release_commit}\trefs/tags/v1.40^{{}}\n"
        )

    monkeypatch.setattr(release_verifier, "remote_git", remote_git)

    with pytest.raises(ReleaseVerificationError, match="remote tag.*expected commit"):
        with materialize_release_bundle(repo, "v1.40", remote="origin"):
            pass


def test_bundle_rejects_tag_changed_during_verification(
    release_repo: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _ = release_repo
    alternate = alternate_tag_object(repo)
    original_read = release_verifier.read_git_object
    moved = False

    def racing_read(repository: Path, oid: str, expected_type: str) -> bytes:
        nonlocal moved
        if not moved and expected_type == "tree":
            git(repository, "update-ref", "refs/tags/v1.40", alternate)
            moved = True
        return original_read(repository, oid, expected_type)

    monkeypatch.setattr(release_verifier, "read_git_object", racing_read)
    with pytest.raises(ReleaseVerificationError, match="changed during verification"):
        with materialize_release_bundle(repo, "v1.40", remote=None):
            pass


def test_bundle_binds_content_and_archive_across_aba_tag_movement(
    release_repo: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, release_commit = release_repo
    original_tag = git(repo, "rev-parse", "refs/tags/v1.40")
    alternate = alternate_tag_object(repo)
    original_read = release_verifier.read_git_object
    original_archive = release_bundle._git_archive
    movements = 0
    opened_revisions: list[str] = []
    archive_revisions: list[str] = []

    def racing_read(repository: Path, oid: str, expected_type: str) -> bytes:
        nonlocal movements
        if expected_type in {"tree", "blob"}:
            if movements == 0:
                git(repository, "update-ref", "refs/tags/v1.40", alternate)
                movements = 1
            elif movements == 1:
                git(repository, "update-ref", "refs/tags/v1.40", original_tag)
                movements = 2
        return original_read(repository, oid, expected_type)

    original_open = release_verifier.VerifiedCommitTree.open.__func__

    def capture_open(
        cls: type[release_verifier.VerifiedCommitTree],
        repository: Path,
        revision: str,
    ) -> release_verifier.VerifiedCommitTree:
        opened_revisions.append(revision)
        return original_open(cls, repository, revision)

    def capture_archive(
        automation: Path,
        revision: str,
        *,
        tree: release_verifier.VerifiedCommitTree | None = None,
    ) -> bytes:
        archive_revisions.append(revision)
        assert tree is not None
        return original_archive(automation, revision, tree=tree)

    monkeypatch.setattr(release_verifier, "read_git_object", racing_read)
    monkeypatch.setattr(
        release_verifier.VerifiedCommitTree,
        "open",
        classmethod(capture_open),
    )
    monkeypatch.setattr(release_bundle, "_git_archive", capture_archive)
    with materialize_release_bundle(repo, "v1.40", remote=None) as bundle:
        assert bundle.commit == release_commit
    assert movements == 2
    assert opened_revisions == [release_commit]
    assert archive_revisions == [release_commit]


def test_bundle_rejects_tag_change_before_context_completion(
    release_repo: tuple[Path, str]
) -> None:
    repo, _ = release_repo
    alternate = alternate_tag_object(repo)

    with pytest.raises(ReleaseVerificationError, match="changed during verification"):
        with materialize_release_bundle(repo, "v1.40", remote=None):
            git(repo, "update-ref", "refs/tags/v1.40", alternate)


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
        lambda automation, ref, **_kwargs: malicious,
    )

    with pytest.raises(ReleaseVerificationError, match="unsafe archive member"):
        with materialize_release_bundle(repo, "v1.40", remote=None):
            pass
