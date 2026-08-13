#!/usr/bin/env python3
"""Behavioral contract for deterministic managed workflow rendering."""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path, PurePosixPath
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.prepare_workflow_rollout import (  # noqa: E402
    FileChange,
    RenderPlan,
    RolloutError,
    apply_render_plan,
    render_repository,
)
from scripts.workflow_catalog import load_catalog, load_fleet_config  # noqa: E402


CATALOG = load_catalog(ROOT)
FLEET = load_fleet_config(ROOT, CATALOG)
PROFILES = FLEET.profiles
CANONICAL = ROOT / FLEET.canonical_dir
COMMIT = "a" * 40
ALL_SECRETS = {
    "CLAUDE_CODE_OAUTH_TOKEN",
    "GEMINI_API_KEY",
    "ZHIPU_API_KEY",
    "APP_PRIVATE_KEY",
}
ALL_VARIABLES = {"APP_ID"}


def make_existing_repo(
    root: Path, *, config: bytes = b"automation_ref: v1.39\n"
) -> Path:
    workflow_dir = root / ".github/workflows"
    workflow_dir.mkdir(parents=True)
    (root / ".github/workflow-config.yml").write_bytes(config)
    (workflow_dir / "project-build.yml").write_bytes(
        b"name: build\non: push\njobs: {}\n"
    )
    return root


def render_fixture(root: Path, *, auth: str) -> RenderPlan:
    repo = make_existing_repo(root)
    profile = dataclasses.replace(PROFILES["wlan-package"], repo_write_auth=auth)
    return render_repository(
        repo,
        CANONICAL,
        CATALOG,
        profile,
        "v1.40",
        COMMIT,
        ALL_SECRETS,
        ALL_VARIABLES,
    )


def render_existing(root: Path, *, config: bytes) -> RenderPlan:
    repo = make_existing_repo(root, config=config)
    return render_repository(
        repo,
        CANONICAL,
        CATALOG,
        PROFILES["wlan-package"],
        "v1.40",
        COMMIT,
        ALL_SECRETS,
        ALL_VARIABLES,
    )


def render_profile(repo: Path, name: str, *, bootstrap: bool = False) -> RenderPlan:
    return render_repository(
        repo,
        CANONICAL,
        CATALOG,
        PROFILES[name],
        "v1.40",
        COMMIT,
        ALL_SECRETS,
        ALL_VARIABLES,
        bootstrap=bootstrap,
    )


def test_app_and_token_profiles_differ_only_by_declared_auth_lines(
    tmp_path: Path,
) -> None:
    app = render_fixture(tmp_path / "app", auth="github_app")
    token = render_fixture(tmp_path / "token", auth="github_token")
    app_text = app.after(".github/workflows/gemini-review.yml").decode()
    token_text = token.after(".github/workflows/gemini-review.yml").decode()
    assert "repo_write_auth: github_app" in app_text
    assert "app_id: ${{ vars.APP_ID }}" in app_text
    assert "APP_PRIVATE_KEY: ${{ secrets.APP_PRIVATE_KEY }}" in app_text
    assert "repo_write_auth: github_token" in token_text
    assert "app_id:" not in token_text
    assert "APP_PRIVATE_KEY:" not in token_text
    assert "GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}" in token_text


def test_config_preserves_every_non_identity_byte(tmp_path: Path) -> None:
    original = b"# keep\nautomation_ref: v1.39 # keep comment\ncustom:\n  value: x\n"
    plan = render_existing(tmp_path, config=original)
    rendered = plan.after(".github/workflow-config.yml")
    assert rendered == (
        b"# keep\nautomation_ref: v1.40 # keep comment\n"
        + f"automation_commit: {COMMIT}\n".encode()
        + b"custom:\n  value: x\n"
    )


def test_existing_commit_is_moved_beside_ref_without_changing_other_bytes(
    tmp_path: Path,
) -> None:
    original = (
        b"automation_ref:  v1.39\r\n"
        b"custom: keep\r\n"
        b"automation_commit: old # identity\r\n"
        b"tail: yes\r\n"
    )
    plan = render_existing(tmp_path, config=original)
    assert plan.after(".github/workflow-config.yml") == (
        b"automation_ref:  v1.40\r\n"
        + f"automation_commit: {COMMIT} # identity\r\n".encode()
        + b"custom: keep\r\ntail: yes\r\n"
    )


def test_all_19_profiles_render_deterministically(tmp_path: Path) -> None:
    assert len(PROFILES) == 19
    for name in PROFILES:
        first = render_profile(make_existing_repo(tmp_path / "first" / name), name)
        second = render_profile(make_existing_repo(tmp_path / "second" / name), name)
        assert first == second
        assert first.status == "drift"
        assert all(change.path in CATALOG.managed_paths for change in first.changes)
        assert all(
            change.after is None or b"__AUTOMATION_COMMIT__" not in change.after
            for change in first.changes
        )


def test_second_render_is_current_and_after_rejects_unchanged_path(
    tmp_path: Path,
) -> None:
    repo = make_existing_repo(tmp_path / "repo")
    first = render_profile(repo, "wlan-package")
    changed = apply_render_plan(repo, first)
    assert changed == tuple(sorted(change.path for change in first.changes))

    second = render_profile(repo, "wlan-package")
    assert second.status == "current"
    assert second.changes == ()
    with pytest.raises(KeyError):
        second.after(".github/workflows/claude.yml")


def test_selected_optional_callers_are_created(tmp_path: Path) -> None:
    plan = render_profile(make_existing_repo(tmp_path / "repo"), "wlan-package")
    assert plan.after(".github/workflows/opencode.yml") is not None
    assert plan.after(".github/workflows/opencode-auto-review.yml") is not None


def test_unselected_optional_caller_is_deleted(tmp_path: Path) -> None:
    repo = make_existing_repo(tmp_path / "repo")
    optional = repo / ".github/workflows/opencode.yml"
    optional.write_bytes(b"project drift\n")
    plan = render_profile(repo, "pcap-analyzer")
    assert plan.after(".github/workflows/opencode.yml") is None


def test_retired_bump_workflow_is_deleted(tmp_path: Path) -> None:
    repo = make_existing_repo(tmp_path / "repo")
    retired = repo / ".github/workflows/bump-automation-ref.yml"
    retired.write_bytes(b"retired\n")
    plan = render_profile(repo, "pcap-analyzer")
    assert plan.after(".github/workflows/bump-automation-ref.yml") is None


def test_missing_required_callers_are_created_from_canonical_bytes(
    tmp_path: Path,
) -> None:
    plan = render_profile(make_existing_repo(tmp_path / "repo"), "pcap-analyzer")
    rendered = plan.after(".github/workflows/claude.yml")
    assert rendered is not None
    assert f"claude.yml@{COMMIT}".encode() in rendered
    assert b"__AUTOMATION_COMMIT__" not in rendered


def test_unknown_central_caller_blocks_without_proposing_changes(
    tmp_path: Path,
) -> None:
    repo = make_existing_repo(tmp_path / "repo")
    unknown = repo / ".github/workflows/project-central.yml"
    unknown.write_text(
        "jobs:\n  call:\n    uses: "
        "jhw7500/automation/.github/workflows/unknown.yml@v1.39\n"
    )
    plan = render_profile(repo, "wlan-package")
    assert plan.status == "blocked"
    assert plan.changes == ()
    assert "project-central.yml" in plan.reason


@pytest.mark.parametrize(
    "config",
    [
        b"automation_ref: v1.39\nautomation_ref: v1.38\n",
        b"automation_ref:\n  nested: v1.39\n",
        b"automation_ref: [v1.39]\n",
        b"automation_ref: v1.39\nautomation_commit: old\nautomation_commit: older\n",
        b"automation_ref: v1.39\nautomation_commit:\n  nested: old\n",
    ],
)
def test_malformed_or_duplicate_identity_scalar_blocks(
    tmp_path: Path, config: bytes
) -> None:
    plan = render_existing(tmp_path, config=config)
    assert plan.status == "blocked"
    assert plan.changes == ()
    assert "identity" in plan.reason


def test_rendered_config_must_still_be_valid_yaml(tmp_path: Path) -> None:
    repo = make_existing_repo(tmp_path / "repo")
    plan = render_repository(
        repo,
        CANONICAL,
        CATALOG,
        PROFILES["wlan-package"],
        "[",
        COMMIT,
        ALL_SECRETS,
        ALL_VARIABLES,
    )
    assert plan.status == "blocked"
    assert plan.changes == ()
    assert "rendered workflow config" in plan.reason


def test_missing_non_bootstrap_config_blocks(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".github/workflows").mkdir(parents=True)
    plan = render_profile(repo, "wpa-supplicant")
    assert plan.status == "blocked"
    assert plan.changes == ()
    assert "config" in plan.reason


def test_project_owned_bytes_are_not_planned_or_changed(tmp_path: Path) -> None:
    repo = make_existing_repo(tmp_path / "repo")
    project_workflow = repo / ".github/workflows/project-build.yml"
    project_config = repo / "project-owned.bin"
    workflow_before = project_workflow.read_bytes()
    project_config.write_bytes(b"\x00project\xff\n")

    plan = render_profile(repo, "wlan-package")
    assert PurePosixPath(".github/workflows/project-build.yml") not in {
        change.path for change in plan.changes
    }
    apply_render_plan(repo, plan)
    assert project_workflow.read_bytes() == workflow_before
    assert project_config.read_bytes() == b"\x00project\xff\n"


def test_missing_prerequisite_names_block_normal_repositories(tmp_path: Path) -> None:
    repo = make_existing_repo(tmp_path / "repo")
    plan = render_repository(
        repo,
        CANONICAL,
        CATALOG,
        PROFILES["wlan-package"],
        "v1.40",
        COMMIT,
        {"CLAUDE_CODE_OAUTH_TOKEN"},
        set(),
    )
    assert plan.status == "blocked"
    assert plan.changes == ()
    assert plan.required_secrets == frozenset(
        {"CLAUDE_CODE_OAUTH_TOKEN", "GEMINI_API_KEY", "ZHIPU_API_KEY", "APP_PRIVATE_KEY"}
    )
    assert plan.required_variables == frozenset({"APP_ID"})
    assert "APP_PRIVATE_KEY" in plan.reason
    assert "APP_ID" in plan.reason


@pytest.mark.parametrize("name", ["wpa-supplicant", "cts-email-mcp-server"])
def test_allowed_disabled_bootstrap_renders_required_callers_and_config_only(
    tmp_path: Path, name: str
) -> None:
    repo = tmp_path / name
    repo.mkdir()
    plan = render_repository(
        repo,
        CANONICAL,
        CATALOG,
        PROFILES[name],
        "v1.40",
        COMMIT,
        set(),
        set(),
        bootstrap=True,
    )
    assert plan.status == "bootstrap_required"
    paths = {change.path for change in plan.changes}
    assert PurePosixPath(".github/workflow-config.yml") in paths
    assert not any(
        entry.path in paths for entry in CATALOG.entries if entry.kind == "optional"
    )
    assert all(
        entry.path in paths for entry in CATALOG.entries if entry.kind == "required"
    )
    assert "CLAUDE_CODE_OAUTH_TOKEN" in plan.reason
    assert "GEMINI_API_KEY" in plan.reason
    config = plan.after(".github/workflow-config.yml")
    assert config is not None
    assert b"automation_ref: v1.40" in config
    assert f"automation_commit: {COMMIT}".encode() in config


@pytest.mark.parametrize(
    "name", [name for name, profile in PROFILES.items() if not profile.bootstrap_allowed]
)
def test_disabled_bootstrap_is_rejected_for_every_other_profile(
    tmp_path: Path, name: str
) -> None:
    repo = tmp_path / name
    repo.mkdir()
    plan = render_profile(repo, name, bootstrap=True)
    assert plan.status == "blocked"
    assert plan.changes == ()
    assert "bootstrap" in plan.reason


def test_bootstrap_flag_is_required_even_for_allowed_profiles(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    plan = render_profile(repo, "wpa-supplicant", bootstrap=False)
    assert plan.status == "blocked"
    assert plan.changes == ()


def test_bootstrap_with_any_existing_central_caller_blocks(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    workflow_dir = repo / ".github/workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "claude.yml").write_text(
        "jobs:\n  claude:\n    uses: "
        "jhw7500/automation/.github/workflows/claude.yml@v1.39\n"
    )
    plan = render_profile(repo, "wpa-supplicant", bootstrap=True)
    assert plan.status == "blocked"
    assert plan.changes == ()
    assert "existing central caller" in plan.reason


def test_provider_secret_values_never_enter_plan_bytes_repr_or_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinels = {
        "CLAUDE_CODE_OAUTH_TOKEN": "provider-claude-value-9f45",
        "GEMINI_API_KEY": "provider-gemini-value-03a1",
        "ZHIPU_API_KEY": "provider-zhipu-value-722b",
        "APP_PRIVATE_KEY": "provider-private-key-value-501c",
    }
    for name, value in sentinels.items():
        monkeypatch.setenv(name, value)
    repo = make_existing_repo(tmp_path / "repo")
    plan = render_repository(
        repo,
        CANONICAL,
        CATALOG,
        PROFILES["wlan-package"],
        "v1.40",
        COMMIT,
        set(sentinels),
        ALL_VARIABLES,
    )
    exposed = repr(plan) + plan.reason
    exposed += "".join(
        change.after.decode("utf-8", errors="replace")
        for change in plan.changes
        if change.after is not None
    )
    assert not any(value in exposed for value in sentinels.values())

    with pytest.raises(RolloutError) as error:
        render_repository(
            repo,
            CANONICAL,
            CATALOG,
            PROFILES["wlan-package"],
            "v1.40",
            "not-a-commit",
            set(sentinels),
            ALL_VARIABLES,
        )
    assert not any(value in str(error.value) for value in sentinels.values())


def test_apply_rejects_non_actionable_and_stale_plans(tmp_path: Path) -> None:
    repo = make_existing_repo(tmp_path / "repo")
    current = RenderPlan("current", "already current", (), frozenset(), frozenset())
    with pytest.raises(RolloutError, match="not actionable"):
        apply_render_plan(repo, current)

    plan = render_profile(repo, "pcap-analyzer")
    path = repo / plan.changes[0].path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"concurrent edit\n")
    with pytest.raises(RolloutError, match="changed since rendering"):
        apply_render_plan(repo, plan)
    assert path.read_bytes() == b"concurrent edit\n"


def test_render_and_apply_refuse_symlinks_at_managed_paths(tmp_path: Path) -> None:
    repo = make_existing_repo(tmp_path / "repo")
    target = tmp_path / "outside"
    target.write_bytes(b"outside\n")
    managed = repo / ".github/workflows/claude.yml"
    managed.symlink_to(target)
    plan = render_profile(repo, "pcap-analyzer")
    assert plan.status == "blocked"
    assert "symlink" in plan.reason

    managed.unlink()
    actionable = render_profile(repo, "pcap-analyzer")
    managed.symlink_to(target)
    with pytest.raises(RolloutError, match="symlink"):
        apply_render_plan(repo, actionable)
    assert target.read_bytes() == b"outside\n"


def test_file_change_paths_are_posix_and_apply_uses_atomic_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_existing_repo(tmp_path / "repo")
    plan = render_profile(repo, "pcap-analyzer")
    assert all(type(change.path) is PurePosixPath for change in plan.changes)

    replacements: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def recording_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        replacements.append((Path(source), Path(target)))
        real_replace(source, target)

    monkeypatch.setattr(os, "replace", recording_replace)
    changed = apply_render_plan(repo, plan)
    written = [change for change in plan.changes if change.after is not None]
    assert len(replacements) == len(written)
    assert all(source.parent == target.parent for source, target in replacements)
    assert changed == tuple(sorted(change.path for change in plan.changes))


def test_apply_never_deletes_project_owned_paths(tmp_path: Path) -> None:
    repo = make_existing_repo(tmp_path / "repo")
    project = repo / ".github/workflows/project-build.yml"
    plan = render_profile(repo, "pcap-analyzer")
    assert all(change.path != PurePosixPath(".github/workflows/project-build.yml") for change in plan.changes)
    apply_render_plan(repo, plan)
    assert project.read_bytes() == b"name: build\non: push\njobs: {}\n"


def test_apply_rejects_a_forged_change_outside_the_catalog(tmp_path: Path) -> None:
    repo = make_existing_repo(tmp_path / "repo")
    project = repo / ".github/workflows/project-build.yml"
    forged = RenderPlan(
        "drift",
        "forged",
        (
            FileChange(
                PurePosixPath(".github/workflows/project-build.yml"),
                project.read_bytes(),
                None,
            ),
        ),
        frozenset(),
        frozenset(),
    )
    with pytest.raises(RolloutError, match="outside the workflow catalog"):
        apply_render_plan(repo, forged)
    assert project.read_bytes() == b"name: build\non: push\njobs: {}\n"


def test_apply_only_deletes_catalogued_deletion_paths(tmp_path: Path) -> None:
    repo = make_existing_repo(tmp_path / "repo")
    required = repo / ".github/workflows/claude.yml"
    required.write_bytes(b"required caller\n")
    forged = RenderPlan(
        "drift",
        "forged",
        (
            FileChange(
                PurePosixPath(".github/workflows/claude.yml"),
                required.read_bytes(),
                None,
            ),
        ),
        frozenset(),
        frozenset(),
    )
    with pytest.raises(RolloutError, match="not catalogued for deletion"):
        apply_render_plan(repo, forged)
    assert required.read_bytes() == b"required caller\n"


def test_file_change_and_render_plan_are_immutable() -> None:
    change = FileChange(PurePosixPath(".github/workflows/x.yml"), None, b"x\n")
    plan = RenderPlan("drift", "x", (change,), frozenset(), frozenset())
    assert plan.after(".github/workflows/x.yml") == b"x\n"
    with pytest.raises(dataclasses.FrozenInstanceError):
        change.after = b"y\n"  # type: ignore[misc]
