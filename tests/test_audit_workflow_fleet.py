"""Tests for renderer-based repository and fleet auditing."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
import json
from pathlib import Path
from unittest import mock

import pytest


from scripts import audit_workflow_fleet as audit
from scripts.audit_workflow_fleet import AuditResult, audit_repository
from scripts.prepare_workflow_rollout import apply_render_plan, render_repository
from scripts.workflow_catalog import load_catalog, load_fleet_config
from scripts.workflow_fleet_git import RepositorySnapshot
from scripts.workflow_release_bundle import ReleaseBundle


ROOT = Path(__file__).resolve().parents[1]
ALL_SECRETS = {
    "CLAUDE_CODE_OAUTH_TOKEN",
    "GEMINI_API_KEY",
    "ZHIPU_API_KEY",
    "APP_PRIVATE_KEY",
}
ALL_VARIABLES = {"APP_ID"}
COMMIT = "1" * 40
BASE = "2" * 40


def test_cli_defaults_to_the_configured_release(tmp_path: Path) -> None:
    args = audit._parser().parse_args(["--workspace", str(tmp_path)])
    config = json.loads(
        (ROOT / "scripts" / "workflow-config.json").read_text(encoding="utf-8")
    )
    assert args.ref == config["automation_ref"]


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
        repo, bundle, profile, ALL_SECRETS, ALL_VARIABLES, base_branch="main"
    )
    assert result.status == "drift"
    assert result.repo == "gstApp"
    assert result.base_branch == "main"
    assert result.changed_paths == tuple(sorted(result.changed_paths))

    apply_bundle(repo, bundle, profile)
    assert (
        audit_repository(
            repo, bundle, profile, ALL_SECRETS, ALL_VARIABLES, base_branch="main"
        ).status
        == "current"
    )
    (repo / ".github/workflows/project-build.yml").write_text(
        "on: push\n", encoding="utf-8"
    )
    assert (
        audit_repository(
            repo, bundle, profile, ALL_SECRETS, ALL_VARIABLES, base_branch="main"
        ).status
        == "current"
    )


def test_audit_reports_managed_byte_mismatch_as_drift(
    repo: Path, bundle: ReleaseBundle, profile
) -> None:
    apply_bundle(repo, bundle, profile)
    managed = repo / ".github/workflows/claude.yml"
    managed.write_bytes(managed.read_bytes() + b"# drift\n")
    result = audit_repository(
        repo, bundle, profile, ALL_SECRETS, ALL_VARIABLES, base_branch="main"
    )
    assert result.status == "drift"
    assert ".github/workflows/claude.yml" in result.changed_paths


def test_audit_reports_unknown_or_malformed_content_as_blocked(
    repo: Path, bundle: ReleaseBundle, profile
) -> None:
    (repo / ".github/workflows/unknown.yml").write_text(
        "jobs:\n  call:\n    uses: jhw7500/automation/.github/workflows/claude.yml@v1.40\n"
    )
    result = audit_repository(
        repo, bundle, profile, ALL_SECRETS, ALL_VARIABLES, base_branch="main"
    )
    assert result == AuditResult(
        "gstApp",
        "main",
        "blocked",
        "unknown central caller path: .github/workflows/unknown.yml",
        (),
    )

    (repo / ".github/workflows/unknown.yml").unlink()
    (repo / ".github/workflow-config.yml").write_text(
        "automation_ref:\n  nested: value\n"
    )
    result = audit_repository(
        repo, bundle, profile, ALL_SECRETS, ALL_VARIABLES, base_branch="main"
    )
    assert result.status == "blocked"
    assert "automation_ref must be a scalar" in result.detail


def test_audit_reports_missing_prerequisite_names_as_blocked(
    repo: Path, bundle: ReleaseBundle, profile
) -> None:
    result = audit_repository(repo, bundle, profile, set(), set(), base_branch="main")
    assert result.status == "blocked"
    assert "missing secrets" in result.detail
    assert "missing variables" in result.detail


def test_audit_result_contains_no_history_or_publish_fields() -> None:
    assert set(AuditResult.__dataclass_fields__) == {
        "repo",
        "base_branch",
        "status",
        "detail",
        "changed_paths",
    }


def marked_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "audit"
    workspace.mkdir()
    (workspace / ".automation-fleet-workspace").write_text("managed\n")
    return workspace


def test_fleet_cli_audits_all_profiles_with_refetch_and_no_remote_write(
    tmp_path: Path, bundle: ReleaseBundle, capsys
) -> None:
    workspace = marked_workspace(tmp_path)
    cloned: list[tuple[str, str | None, Path]] = []
    fetched: list[tuple[str, str]] = []
    marked_clone_roots: list[bool] = []

    def clone(
        _owner: str, repo: str, root: Path, branch: str | None = None
    ) -> RepositorySnapshot:
        cloned.append((repo, branch, root))
        marked_clone_roots.append((root / audit.WORKSPACE_MARKER).is_file())
        path = root / repo
        path.mkdir()
        (path / ".git").mkdir()
        return RepositorySnapshot(
            path,
            "main",
            BASE,
            frozenset(ALL_SECRETS),
            frozenset(ALL_VARIABLES),
            branch or "main",
        )

    def refetch(snapshot: RepositorySnapshot) -> str:
        fetched.append((snapshot.path.name, snapshot.base_branch or snapshot.default_branch))
        return BASE

    def classify(path, _bundle, _profile, _secrets, _variables, *, base_branch, **_kwargs):
        return AuditResult(
            path.name,
            base_branch,
            "drift",
            "managed drift",
            (".github/workflows/claude.yml",),
        )

    commands: list[list[str]] = []
    with (
        mock.patch.object(
            audit,
            "materialize_release_bundle",
            side_effect=lambda *_a, **_k: nullcontext(bundle),
        ),
        mock.patch.object(audit.fleet_git, "clone_default_branch", side_effect=clone),
        mock.patch.object(audit.fleet_git, "clone_branch", side_effect=clone),
        mock.patch.object(audit.fleet_git, "refetch_default", side_effect=refetch),
        mock.patch.object(audit.fleet_git, "refetch_branch", side_effect=refetch),
        mock.patch.object(audit, "audit_repository", side_effect=classify),
        mock.patch.object(
            audit,
            "git",
            side_effect=lambda args, **_kwargs: commands.append(list(args)) or "",
        ),
    ):
        rc = audit.main(
            ["--automation", str(ROOT), "--workspace", str(workspace), "--ref", "v1.40"]
        )

    assert rc == 0
    assert len(cloned) == 17
    assert [(repo, branch) for repo, branch, _root in cloned] == [
        (repo, branch)
        for repo in sorted(bundle.config.profiles)
        for branch in (None, "ported")
        if repo == "wlan-driver-v2" or branch is None
    ]
    assert fetched == [(repo, branch or "main") for repo, branch, _root in cloned]
    assert len({root for _repo, _branch, root in cloned}) == 17
    assert all(marked_clone_roots)
    assert all(command[:2] == ["switch", "--detach"] for command in commands)
    output = capsys.readouterr().out
    assert "DRIFT   wlan-driver-v2[main]: managed drift" in output
    assert "DRIFT   wlan-driver-v2[ported]: managed drift" in output
    assert "total=17" in output and "drift=17" in output and "blocked=0" in output
    flattened = " ".join(" ".join(command) for command in commands)
    for forbidden in (
        "push",
        "merge",
        "auto-merge",
        "update-branch",
        "secret set",
        "variable set",
    ):
        assert forbidden not in flattened


def test_fleet_cli_selects_repositories_and_blocks_only_on_blocked(
    tmp_path: Path, bundle: ReleaseBundle
) -> None:
    workspace = marked_workspace(tmp_path)

    def clone(_owner: str, repo: str, root: Path, branch: str | None = None) -> RepositorySnapshot:
        path = root / repo
        path.mkdir()
        (path / ".git").mkdir()
        return RepositorySnapshot(path, "main", BASE, frozenset(), frozenset(), branch or "main")

    with (
        mock.patch.object(
            audit,
            "materialize_release_bundle",
            side_effect=lambda *_a, **_k: nullcontext(bundle),
        ),
        mock.patch.object(audit.fleet_git, "clone_default_branch", side_effect=clone),
        mock.patch.object(audit.fleet_git, "clone_branch", side_effect=clone),
        mock.patch.object(audit.fleet_git, "refetch_default", return_value=BASE),
        mock.patch.object(audit.fleet_git, "refetch_branch", return_value=BASE),
        mock.patch.object(audit, "git", return_value=""),
        mock.patch.object(
            audit,
            "audit_repository",
            side_effect=lambda path, *_a, **kwargs: AuditResult(
                path.name, kwargs["base_branch"], "blocked", "missing names", ()
            ),
        ),
    ):
        rc = audit.main(
            [
                "--automation",
                str(ROOT),
                "--workspace",
                str(workspace),
                "--ref",
                "v1.40",
                "--repo",
                "gstApp",
            ]
        )
    assert rc == 1


def test_selected_repository_audits_main_and_ported_and_ported_drift_prevents_all_current(
    tmp_path: Path, bundle: ReleaseBundle, capsys
) -> None:
    """Catch a selected active branch silently omitted from an otherwise current audit."""

    workspace = marked_workspace(tmp_path)

    def clone(_owner: str, repo: str, root: Path, branch: str | None) -> RepositorySnapshot:
        path = root / repo
        path.mkdir()
        (path / ".git").mkdir()
        return RepositorySnapshot(
            path, "main", BASE, frozenset(ALL_SECRETS), frozenset(ALL_VARIABLES), branch or "main"
        )

    def classify(path, _bundle, _profile, _secrets, _variables, *, base_branch, **_kwargs):
        status = "drift" if base_branch == "ported" else "current"
        return AuditResult(path.name, base_branch, status, f"{base_branch} {status}", ())

    with (
        mock.patch.object(
            audit, "materialize_release_bundle", side_effect=lambda *_a, **_k: nullcontext(bundle)
        ),
        mock.patch.object(audit.fleet_git, "clone_branch", side_effect=clone),
        mock.patch.object(audit.fleet_git, "refetch_branch", return_value=BASE),
        mock.patch.object(audit, "audit_repository", side_effect=classify),
        mock.patch.object(audit, "git", return_value=""),
    ):
        rc = audit.main(
            [
                "--automation", str(ROOT), "--workspace", str(workspace), "--ref", "v1.40",
                "--repo", "wlan-driver-v2",
            ]
        )

    assert rc == 0
    output = capsys.readouterr().out
    assert "CURRENT wlan-driver-v2[main]: main current" in output
    assert "DRIFT   wlan-driver-v2[ported]: ported drift" in output
    assert "total=2 current=1 drift=1 blocked=0" in output


def test_target_clone_failure_blocks_only_the_exact_target_and_stays_in_totals(
    tmp_path: Path, bundle: ReleaseBundle, capsys
) -> None:
    """Catch a target failure that suppresses another target or loses its branch identity."""

    workspace = marked_workspace(tmp_path)

    def clone(_owner: str, repo: str, root: Path, branch: str | None) -> RepositorySnapshot:
        if branch == "ported":
            raise audit.FleetGitError("ported fetch unavailable")
        path = root / repo
        path.mkdir()
        (path / ".git").mkdir()
        return RepositorySnapshot(
            path, "main", BASE, frozenset(ALL_SECRETS), frozenset(ALL_VARIABLES), "main"
        )

    with (
        mock.patch.object(
            audit, "materialize_release_bundle", side_effect=lambda *_a, **_k: nullcontext(bundle)
        ),
        mock.patch.object(audit.fleet_git, "clone_branch", side_effect=clone),
        mock.patch.object(audit.fleet_git, "refetch_branch", return_value=BASE),
        mock.patch.object(
            audit,
            "audit_repository",
            side_effect=lambda path, *_a, **kwargs: AuditResult(
                path.name, kwargs["base_branch"], "current", "managed content matches", ()
            ),
        ),
        mock.patch.object(audit, "git", return_value=""),
    ):
        rc = audit.main(
            [
                "--automation", str(ROOT), "--workspace", str(workspace), "--ref", "v1.40",
                "--repo", "wlan-driver-v2",
            ]
        )

    assert rc == 1
    output = capsys.readouterr().out
    assert "CURRENT wlan-driver-v2[main]: managed content matches" in output
    assert "BLOCKED wlan-driver-v2[ported]: repository audit failed" in output
    assert "total=2 current=1 drift=0 blocked=1" in output


def test_unresolved_implicit_default_has_a_label_distinct_from_a_branch_named_default(
    tmp_path: Path, bundle: ReleaseBundle, capsys
) -> None:
    """Catch a failed implicit-default clone mislabeled as a real `default` branch."""

    workspace = marked_workspace(tmp_path)
    profile = replace(bundle.config.profiles["gstApp"], additional_branches=("default",))
    scenario_bundle = replace(
        bundle,
        config=replace(bundle.config, profiles={**bundle.config.profiles, "gstApp": profile}),
    )

    def clone(_owner: str, repo: str, root: Path, branch: str | None) -> RepositorySnapshot:
        if branch is None:
            raise audit.FleetGitError("default metadata unavailable")
        path = root / repo
        path.mkdir()
        (path / ".git").mkdir()
        return RepositorySnapshot(
            path, "main", BASE, frozenset(ALL_SECRETS), frozenset(ALL_VARIABLES), branch
        )

    with (
        mock.patch.object(
            audit,
            "materialize_release_bundle",
            side_effect=lambda *_a, **_k: nullcontext(scenario_bundle),
        ),
        mock.patch.object(audit.fleet_git, "clone_branch", side_effect=clone),
        mock.patch.object(audit.fleet_git, "refetch_branch", return_value=BASE),
        mock.patch.object(
            audit,
            "audit_repository",
            side_effect=lambda path, *_a, **kwargs: AuditResult(
                path.name, kwargs["base_branch"], "current", "managed content matches", ()
            ),
        ),
        mock.patch.object(audit, "git", return_value=""),
    ):
        rc = audit.main(
            [
                "--automation", str(ROOT), "--workspace", str(workspace), "--ref", "v1.40",
                "--repo", "gstApp",
            ]
        )

    assert rc == 1
    output = capsys.readouterr().out
    assert "BLOCKED gstApp[<default-unresolved>]: repository audit failed" in output
    assert "CURRENT gstApp[default]: managed content matches" in output
    assert "total=2 current=1 drift=0 blocked=1" in output


def test_target_refetch_failure_blocks_only_ported_and_keeps_main_visible(
    tmp_path: Path, bundle: ReleaseBundle, capsys
) -> None:
    """Catch a post-clone ported refetch failure that hides the current main target."""

    workspace = marked_workspace(tmp_path)
    cloned: list[str] = []

    def clone(_owner: str, repo: str, root: Path, branch: str | None) -> RepositorySnapshot:
        cloned.append(branch or "main")
        path = root / repo
        path.mkdir()
        (path / ".git").mkdir()
        return RepositorySnapshot(
            path, "main", BASE, frozenset(ALL_SECRETS), frozenset(ALL_VARIABLES), branch or "main"
        )

    def refetch(snapshot: RepositorySnapshot) -> str:
        if snapshot.base_branch == "ported":
            raise audit.FleetGitError("ported refetch unavailable")
        return BASE

    with (
        mock.patch.object(
            audit, "materialize_release_bundle", side_effect=lambda *_a, **_k: nullcontext(bundle)
        ),
        mock.patch.object(audit.fleet_git, "clone_branch", side_effect=clone),
        mock.patch.object(audit.fleet_git, "refetch_branch", side_effect=refetch),
        mock.patch.object(
            audit,
            "audit_repository",
            side_effect=lambda path, *_a, **kwargs: AuditResult(
                path.name, kwargs["base_branch"], "current", "managed content matches", ()
            ),
        ),
        mock.patch.object(audit, "git", return_value=""),
    ):
        rc = audit.main(
            [
                "--automation", str(ROOT), "--workspace", str(workspace), "--ref", "v1.40",
                "--repo", "wlan-driver-v2",
            ]
        )

    assert rc == 1
    assert cloned == ["main", "ported"]
    output = capsys.readouterr().out
    assert "CURRENT wlan-driver-v2[main]: managed content matches" in output
    assert "BLOCKED wlan-driver-v2[ported]: repository audit failed" in output
    assert "total=2 current=1 drift=0 blocked=1" in output


@pytest.mark.parametrize(
    "args",
    [
        ["push", "origin", "main"],
        ["merge", "main"],
        ["update-branch"],
        ["secret", "set", "X"],
        ["variable", "set", "X"],
    ],
)
def test_audit_git_wrapper_rejects_every_mutation_before_child(
    args: list[str],
) -> None:
    with mock.patch.object(audit.fleet_git, "run") as child:
        with pytest.raises(audit.FleetGitError, match="not permitted"):
            audit.git(args)
        child.assert_not_called()
