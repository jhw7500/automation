"""Tests for renderer-based workflow fleet content auditing."""

from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]

from scripts.audit_workflow_fleet import AuditResult, audit_repository
from scripts.prepare_workflow_rollout import apply_render_plan, render_repository
from scripts.workflow_catalog import load_catalog, load_fleet_config
from scripts.workflow_release_bundle import ReleaseBundle


ALL_SECRETS = {
    "CLAUDE_CODE_OAUTH_TOKEN",
    "GEMINI_API_KEY",
    "ZHIPU_API_KEY",
    "APP_PRIVATE_KEY",
}
ALL_VARIABLES = {"APP_ID"}
COMMIT = "1" * 40


@pytest.fixture
def bundle() -> ReleaseBundle:
    catalog = load_catalog(ROOT)
    config = load_fleet_config(ROOT, catalog)
    return ReleaseBundle(
        root=ROOT,
        ref="v1.40",
        commit=COMMIT,
        catalog=catalog,
        config=config,
        canonical=ROOT / config.canonical_dir,
    )


@pytest.fixture
def profile(bundle: ReleaseBundle):
    return bundle.config.profiles["gstApp"]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    target = tmp_path / "gstApp"
    (target / ".github/workflows").mkdir(parents=True)
    (target / ".github/workflow-config.yml").write_text(
        "automation_ref: v1.39\nreview:\n  auto: false\n", encoding="utf-8"
    )
    return target


def apply_bundle(repo: Path, bundle: ReleaseBundle, profile) -> None:
    plan = render_repository(
        repo,
        bundle.canonical,
        bundle.catalog,
        profile,
        bundle.ref,
        bundle.commit,
        ALL_SECRETS,
        ALL_VARIABLES,
        bootstrap=False,
    )
    assert plan.status == "drift"
    apply_render_plan(repo, plan)


def test_audit_classifies_content_not_history(
    repo: Path, bundle: ReleaseBundle, profile
) -> None:
    result = audit_repository(
        repo, bundle, profile, ALL_SECRETS, ALL_VARIABLES
    )
    assert result.status == "drift"
    assert result.repo == "gstApp"
    assert result.changed_paths == tuple(sorted(result.changed_paths))

    apply_bundle(repo, bundle, profile)
    assert (
        audit_repository(repo, bundle, profile, ALL_SECRETS, ALL_VARIABLES).status
        == "current"
    )
    (repo / ".github/workflows/project-build.yml").write_text(
        "on: push\n", encoding="utf-8"
    )
    assert (
        audit_repository(repo, bundle, profile, ALL_SECRETS, ALL_VARIABLES).status
        == "current"
    )


def test_audit_reports_managed_byte_mismatch_as_drift(
    repo: Path, bundle: ReleaseBundle, profile
) -> None:
    apply_bundle(repo, bundle, profile)
    managed = repo / ".github/workflows/claude.yml"
    managed.write_bytes(managed.read_bytes() + b"# drift\n")

    result = audit_repository(repo, bundle, profile, ALL_SECRETS, ALL_VARIABLES)

    assert result.status == "drift"
    assert ".github/workflows/claude.yml" in result.changed_paths
    assert "managed file" in result.detail


def test_audit_reports_unknown_central_caller_as_blocked(
    repo: Path, bundle: ReleaseBundle, profile
) -> None:
    (repo / ".github/workflows/unknown.yml").write_text(
        "jobs:\n  call:\n    uses: "
        "jhw7500/automation/.github/workflows/claude.yml@v1.40\n",
        encoding="utf-8",
    )

    result = audit_repository(repo, bundle, profile, ALL_SECRETS, ALL_VARIABLES)

    assert result == AuditResult(
        repo="gstApp",
        status="blocked",
        detail="unknown central caller path: .github/workflows/unknown.yml",
        changed_paths=(),
    )


def test_audit_reports_malformed_config_as_blocked(
    repo: Path, bundle: ReleaseBundle, profile
) -> None:
    (repo / ".github/workflow-config.yml").write_text(
        "automation_ref:\n  nested: value\n", encoding="utf-8"
    )

    result = audit_repository(repo, bundle, profile, ALL_SECRETS, ALL_VARIABLES)

    assert result.status == "blocked"
    assert "automation_ref must be a scalar" in result.detail
    assert result.changed_paths == ()


def test_audit_reports_missing_prerequisite_names_as_blocked(
    repo: Path, bundle: ReleaseBundle, profile
) -> None:
    result = audit_repository(repo, bundle, profile, set(), set())

    assert result.status == "blocked"
    assert "missing secrets" in result.detail
    assert "missing variables" in result.detail


def test_audit_result_contains_no_history_or_publish_fields() -> None:
    assert set(AuditResult.__dataclass_fields__) == {
        "repo",
        "status",
        "detail",
        "changed_paths",
    }
