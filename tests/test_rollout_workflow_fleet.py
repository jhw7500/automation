"""Behavior tests for read-only fleet planning and PR-only publication."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import fields, replace
import json
from pathlib import Path, PurePosixPath
import subprocess
from unittest import mock

import pytest

from scripts import rollout_workflow_fleet as rollout
from scripts.prepare_workflow_rollout import (
    FileChange,
    ManagedResult,
    RenderPlan,
    apply_render_plan,
)
from scripts.workflow_fleet_git import PullRequest, RepositorySnapshot
from scripts.workflow_catalog import load_catalog, load_fleet_config
from scripts.workflow_release_bundle import ReleaseBundle


ROOT = Path(__file__).resolve().parents[1]
COMMIT = "1" * 40
BASE = "2" * 40
HEAD = "3" * 40
FRESH_BASE = "4" * 40
FRESH_HEAD = "5" * 40
ALL_SECRETS = frozenset(
    {"CLAUDE_CODE_OAUTH_TOKEN", "GEMINI_API_KEY", "ZHIPU_API_KEY", "APP_PRIVATE_KEY"}
)
ALL_VARIABLES = frozenset({"APP_ID"})
CATALOG = load_catalog(ROOT)


@pytest.fixture
def bundle() -> ReleaseBundle:
    catalog = CATALOG
    config = load_fleet_config(ROOT, catalog)
    return ReleaseBundle(
        root=ROOT,
        ref="v1.40",
        commit=COMMIT,
        catalog=catalog,
        config=config,
        canonical=ROOT / config.canonical_dir,
    )


def marked_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "fleet"
    workspace.mkdir()
    (workspace / ".automation-fleet-workspace").write_text(
        "managed disposable clones only\n", encoding="utf-8"
    )
    return workspace


def test_cli_defaults_to_the_configured_release(tmp_path: Path) -> None:
    args = rollout._parser().parse_args(["--workspace", str(tmp_path)])
    config = json.loads(
        (ROOT / "scripts" / "workflow-config.json").read_text(encoding="utf-8")
    )
    assert args.ref == config["automation_ref"]


def outcome(repo: str, status: str = "planned") -> rollout.RepoOutcome:
    return rollout.RepoOutcome(
        repo=repo,
        status=status,
        detail="one managed file differs",
        base_sha=BASE,
        changed_paths=(".github/workflows/claude.yml",),
    )


def prepared(repo: str, status: str = "planned") -> rollout.PreparedRepo:
    plan = RenderPlan(
        "drift",
        "one managed file differs",
        (
            FileChange(
                PurePosixPath(".github/workflows/claude.yml"), b"old\n", b"new\n"
            ),
        ),
        frozenset({"CLAUDE_CODE_OAUTH_TOKEN"}),
        frozenset(),
    )
    return rollout.PreparedRepo(repo, "create_branch", outcome(repo, status), plan)


def fake_bundle_context(bundle: ReleaseBundle):
    return nullcontext(bundle)


def test_public_report_model_and_exact_release_text() -> None:
    assert [item.name for item in fields(rollout.RepoOutcome)] == [
        "repo",
        "status",
        "detail",
        "base_sha",
        "head_sha",
        "pr_url",
        "changed_paths",
        "stage",
        "base_branch",
    ]
    assert rollout.rollout_branch("v1.40") == "automation/common-workflows-v1.40"
    assert rollout.rollout_branch("v2.3.4") == "automation/common-workflows-v2.3.4"
    assert rollout.pr_title("v1.40") == "ci: adopt common automation workflows (v1.40)"
    assert rollout.pr_body("v1.40", COMMIT, ("z.yml", "a.yml")) == (
        "Standardize only the catalogued common AI workflow callers.\n\n"
        "- automation tag: `v1.40`\n"
        f"- automation commit: `{COMMIT}`\n"
        "- managed paths:\n- `a.yml`\n- `z.yml`\n\n"
        "Project-specific workflows are unchanged. This PR does not modify secrets. "
        "Merge and recovery use this repository's normal GitHub controls.\n"
    )


def test_ported_identity_binds_the_head_title_body_and_pr_base(
    tmp_path: Path,
) -> None:
    """Catch a rollout that reuses the default identity for an active port."""

    digest = "8b6809aa4897b8f29d43ba741152d8c90bd46f96c609af9fc5daaee8e5348ea5"
    branch = f"automation/common-workflows-v1.40-{digest}"
    title = "ci: adopt common automation workflows (v1.40; base=ported)"
    changed = (".github/workflows/claude.yml",)
    body = (
        "Standardize only the catalogued common AI workflow callers.\n\n"
        "- automation tag: `v1.40`\n"
        f"- automation commit: `{COMMIT}`\n"
        "- base branch: `ported`\n"
        "- managed paths:\n- `.github/workflows/claude.yml`\n\n"
        "Project-specific workflows are unchanged. This PR does not modify secrets. "
        "Merge and recovery use this repository's normal GitHub controls.\n"
    )
    snap = replace(snapshot(tmp_path), base_branch="ported")
    request = PullRequest(
        7,
        "https://github.com/jhw7500/gstApp/pull/7",
        "OPEN",
        "ported",
        branch,
        "jhw7500/gstApp",
        HEAD,
        title,
        body,
    )
    plan = RenderPlan(
        "drift",
        "managed bytes differ",
        (
            FileChange(
                PurePosixPath(".github/workflows/claude.yml"), b"old\n", b"new\n"
            ),
        ),
        frozenset(),
        frozenset(),
    )

    assert rollout.rollout_branch("v1.40", "ported", "main") == branch
    assert rollout.pr_title("v1.40", "ported", "main") == title
    assert rollout.pr_body("v1.40", COMMIT, changed, "ported", "main") == body
    with (
        mock.patch.object(rollout.fleet_git, "remote_branch_sha", return_value=HEAD),
        mock.patch.object(rollout, "validate_existing_branch"),
        mock.patch.object(rollout.fleet_git, "list_rollout_prs", return_value=(request,)),
    ):
        assert (
            rollout.inspect_rollout(snap, BASE, "v1.40", COMMIT, plan).action
            == "reuse"
        )


def test_selected_profile_expands_default_and_ported_before_publication(
    tmp_path: Path, bundle: ReleaseBundle
) -> None:
    """Catch an operator selection that silently skips the configured port."""

    workspace = marked_workspace(tmp_path)
    checked: list[tuple[str, str | None]] = []
    published: list[tuple[str, str | None]] = []

    def prevalidate(*_args, repo: str, base_branch: str | None, **_kwargs):
        checked.append((repo, base_branch))
        exact_base = "main" if base_branch is None else base_branch
        return rollout.PreparedRepo(
            repo,
            "create_branch",
            rollout.RepoOutcome(repo, "planned", "ready", BASE, base_branch=exact_base),
            None,
            exact_base,
            base_branch,
        )

    def publish(*_args, repo: str, base_branch: str | None, **_kwargs):
        published.append((repo, base_branch))
        exact_base = "main" if base_branch is None else base_branch
        return rollout.RepoOutcome(
            repo, "published", "ready", BASE, base_branch=exact_base
        )

    with (
        mock.patch.object(
            rollout,
            "materialize_release_bundle",
            side_effect=lambda *_a, **_k: fake_bundle_context(bundle),
        ),
        mock.patch.object(rollout, "prevalidate_repository", side_effect=prevalidate),
        mock.patch.object(rollout, "publish_repository", side_effect=publish),
    ):
        assert rollout.main(
            [
                "--mode", "publish", "--confirm", "--workspace", str(workspace),
                "--repo", "wlan-driver-v2",
            ]
        ) == 0

    assert checked == [("wlan-driver-v2", None), ("wlan-driver-v2", "ported")]
    assert published == checked


def test_blocked_ported_prevalidation_prevents_all_selected_publication(
    tmp_path: Path, bundle: ReleaseBundle
) -> None:
    """Catch publication starting before every expanded branch target is gated."""

    workspace = marked_workspace(tmp_path)
    checked: list[tuple[str, str | None]] = []

    def prevalidate(*_args, repo: str, base_branch: str | None, **_kwargs):
        checked.append((repo, base_branch))
        exact_base = "main" if base_branch is None else base_branch
        status = "blocked" if base_branch == "ported" else "planned"
        return rollout.PreparedRepo(
            repo,
            "blocked" if status == "blocked" else "create_branch",
            rollout.RepoOutcome(repo, status, "ported is blocked", BASE, base_branch=exact_base),
            None,
            exact_base,
            base_branch,
        )

    with (
        mock.patch.object(
            rollout,
            "materialize_release_bundle",
            side_effect=lambda *_a, **_k: fake_bundle_context(bundle),
        ),
        mock.patch.object(rollout, "prevalidate_repository", side_effect=prevalidate),
        mock.patch.object(rollout, "publish_repository") as publish,
    ):
        assert rollout.main(
            [
                "--mode", "publish", "--confirm", "--workspace", str(workspace),
                "--repo", "wlan-driver-v2", "--repo", "gstApp",
            ]
        ) == 1

    assert checked == [
        ("wlan-driver-v2", None),
        ("wlan-driver-v2", "ported"),
        ("gstApp", None),
    ]
    publish.assert_not_called()


@pytest.mark.parametrize(
    "ref", ["", "1.40", "v1", "v1.", "v1.40-rc1", " v1.40", "v1.40/main"]
)
def test_release_ref_parser_fails_closed(ref: str) -> None:
    with pytest.raises(rollout.CommandError, match="invalid release ref"):
        rollout.rollout_branch(ref)


@pytest.mark.parametrize(
    "flag",
    [
        "--sync-missing-secrets",
        "--allow-personal-oauth-fanout",
        "--allow-env-secret",
        "--refresh-secret",
        "--config",
    ],
)
def test_legacy_flags_are_rejected_before_any_command(
    tmp_path: Path, flag: str
) -> None:
    with mock.patch("scripts.workflow_fleet_git.subprocess.run") as child:
        with pytest.raises(SystemExit):
            rollout.main(["--mode", "publish", "--workspace", str(tmp_path), flag])
        child.assert_not_called()


def test_prepare_mode_is_rejected_before_any_command(tmp_path: Path) -> None:
    with mock.patch("scripts.workflow_fleet_git.subprocess.run") as child:
        with pytest.raises(SystemExit):
            rollout.main(["--mode", "prepare", "--workspace", str(tmp_path)])
        child.assert_not_called()


@pytest.mark.parametrize(
    "argv",
    [
        ["--mode", "publish", "--repo", "gstApp"],
        ["--mode", "publish", "--confirm"],
    ],
)
def test_publish_requires_confirmation_and_explicit_repo(
    tmp_path: Path, argv: list[str]
) -> None:
    with mock.patch(
        "scripts.rollout_workflow_fleet.materialize_release_bundle"
    ) as load:
        with pytest.raises(SystemExit):
            rollout.main(["--workspace", str(tmp_path), *argv])
        load.assert_not_called()


@pytest.mark.parametrize(
    "argv",
    [
        ["--repo", "wpa-supplicant"],
        [
            "--repo",
            "wpa-supplicant",
            "--repo",
            "gstApp",
            "--bootstrap-repo",
            "wpa-supplicant",
        ],
        ["--repo", "wpa-supplicant", "--bootstrap-repo", "pim-check"],
    ],
)
def test_bootstrap_requires_one_matching_allowed_repository_before_bundle_load(
    tmp_path: Path, argv: list[str]
) -> None:
    if "--bootstrap-repo" not in argv:
        argv = [*argv, "--bootstrap-repo", "gstApp"]
    with mock.patch(
        "scripts.rollout_workflow_fleet.materialize_release_bundle"
    ) as load:
        with pytest.raises(SystemExit):
            rollout.main(["--workspace", str(tmp_path), *argv])
        load.assert_not_called()


def test_bootstrap_authorization_comes_from_release_profile(
    tmp_path: Path, bundle: ReleaseBundle
) -> None:
    workspace = marked_workspace(tmp_path)
    with (
        mock.patch.object(
            rollout,
            "materialize_release_bundle",
            side_effect=lambda *_a, **_k: fake_bundle_context(bundle),
        ),
        mock.patch.object(rollout, "prevalidate_repository") as prevalidate,
    ):
        with pytest.raises(SystemExit, match="2"):
            rollout.main(
                [
                    "--workspace",
                    str(workspace),
                    "--repo",
                    "gstApp",
                    "--bootstrap-repo",
                    "gstApp",
                ]
            )
        prevalidate.assert_not_called()


def test_allowed_bootstrap_is_explicit_and_single_repository(
    tmp_path: Path, bundle: ReleaseBundle
) -> None:
    workspace = marked_workspace(tmp_path)
    seen: list[tuple[str, bool]] = []

    def prevalidate(*_args, repo: str, bootstrap: bool, **_kwargs):
        seen.append((repo, bootstrap))
        return prepared(repo)

    with (
        mock.patch.object(
            rollout,
            "materialize_release_bundle",
            side_effect=lambda *_a, **_k: fake_bundle_context(bundle),
        ),
        mock.patch.object(rollout, "prevalidate_repository", side_effect=prevalidate),
    ):
        assert (
            rollout.main(
                [
                    "--workspace",
                    str(workspace),
                    "--repo",
                    "wpa-supplicant",
                    "--bootstrap-repo",
                    "wpa-supplicant",
                ]
            )
            == 0
        )
    assert seen == [("wpa-supplicant", True)]


def test_plan_prevalidates_all_repositories_and_never_enters_publish(
    tmp_path: Path, bundle: ReleaseBundle
) -> None:
    workspace = marked_workspace(tmp_path)
    seen: list[str] = []

    def prevalidate(*_args, repo: str, **_kwargs) -> rollout.PreparedRepo:
        seen.append(repo)
        return prepared(repo)

    with (
        mock.patch.object(
            rollout,
            "materialize_release_bundle",
            side_effect=lambda *_a, **_k: fake_bundle_context(bundle),
        ),
        mock.patch.object(rollout, "prevalidate_repository", side_effect=prevalidate),
        mock.patch.object(rollout, "publish_repository") as publish,
    ):
        rc = rollout.main(
            [
                "--mode",
                "plan",
                "--workspace",
                str(workspace),
                "--repo",
                "gstApp",
                "--repo",
                "max9296",
            ]
        )

    assert rc == 0
    assert seen == ["gstApp", "max9296"]
    publish.assert_not_called()
    report = json.loads((workspace / "rollout-manifest.json").read_text())
    assert [item["repo"] for item in report] == seen
    assert all(item["authoritative"] is False for item in report)
    assert all(item["release_commit"] == COMMIT for item in report)
    assert report[0]["observed_base"] == BASE
    assert report[0]["required_secrets"] == ["CLAUDE_CODE_OAUTH_TOKEN"]
    assert report[0]["managed_diff_paths"] == [".github/workflows/claude.yml"]


def test_publish_blocks_every_write_when_any_prevalidation_blocks(
    tmp_path: Path, bundle: ReleaseBundle
) -> None:
    workspace = marked_workspace(tmp_path)
    checked: list[str] = []

    def prevalidate(*_args, repo: str, **_kwargs) -> rollout.PreparedRepo:
        checked.append(repo)
        if repo == "max9296":
            return rollout.PreparedRepo(
                repo,
                "blocked",
                rollout.RepoOutcome(repo, "blocked", "mismatched branch", BASE),
                None,
            )
        return prepared(repo)

    with (
        mock.patch.object(
            rollout,
            "materialize_release_bundle",
            side_effect=lambda *_a, **_k: fake_bundle_context(bundle),
        ),
        mock.patch.object(rollout, "prevalidate_repository", side_effect=prevalidate),
        mock.patch.object(rollout, "publish_repository") as publish,
    ):
        rc = rollout.main(
            [
                "--mode",
                "publish",
                "--confirm",
                "--workspace",
                str(workspace),
                "--repo",
                "gstApp",
                "--repo",
                "max9296",
            ]
        )

    assert rc == 1
    assert checked == ["gstApp", "max9296"]
    publish.assert_not_called()


def test_prevalidation_block_preserves_observed_base_and_render_metadata(
    tmp_path: Path, bundle: ReleaseBundle
) -> None:
    work = marked_workspace(tmp_path)
    snap = snapshot(work)
    plan = make_plan()
    with (
        mock.patch.object(rollout.fleet_git, "clone_default_branch", return_value=snap),
        mock.patch.object(rollout.fleet_git, "refetch_default", return_value=BASE),
        mock.patch.object(rollout, "git", return_value=""),
        mock.patch.object(rollout, "_render", return_value=plan),
        mock.patch.object(rollout, "validate_managed_result"),
        mock.patch.object(
            rollout,
            "inspect_rollout",
            side_effect=rollout.CommandError("mismatched branch"),
        ),
    ):
        result = rollout.prevalidate_repository(
            bundle, work, repo="gstApp", bootstrap=False, actionlint=Path("/bin/true")
        )

    assert result.outcome.status == "blocked"
    assert result.outcome.base_sha == BASE
    assert result.plan is plan
    record = rollout._plan_record(result, COMMIT)
    assert record["managed_diff_paths"] == list(rollout._changed_paths(plan))


def test_publish_repository_refetches_and_renders_again_before_inspection(
    tmp_path: Path, bundle: ReleaseBundle
) -> None:
    workspace = marked_workspace(tmp_path)
    snap = snapshot(workspace)
    plan = make_plan()
    calls: list[str] = []

    def refetch(_snapshot):
        calls.append("refetch")
        return BASE

    def render(*_args, **_kwargs):
        calls.append("render")
        return plan

    def inspect(*_args, **_kwargs):
        calls.append("inspect")
        return rollout.RolloutInspection(
            "reuse", HEAD, "https://github.com/jhw7500/gstApp/pull/1"
        )

    with (
        mock.patch.object(
            rollout, "_make_clone_workspace", return_value=nullcontext(str(workspace))
        ),
        mock.patch.object(rollout.fleet_git, "clone_default_branch", return_value=snap),
        mock.patch.object(rollout.fleet_git, "refetch_default", side_effect=refetch),
        mock.patch.object(rollout, "git", return_value=""),
        mock.patch.object(rollout, "_render", side_effect=render),
        mock.patch.object(rollout, "validate_managed_result"),
        mock.patch.object(rollout, "inspect_rollout", side_effect=inspect),
    ):
        result = rollout.publish_repository(
            bundle,
            workspace,
            repo="gstApp",
            bootstrap=False,
            actionlint=Path("/bin/true"),
        )

    assert result.status == "reused"
    assert calls == ["refetch", "render", "inspect"]


def test_fresh_publication_rebinds_snapshot_to_refetched_base_before_atomic_creation(
    tmp_path: Path, bundle: ReleaseBundle
) -> None:
    workspace = marked_workspace(tmp_path)
    snap = snapshot(workspace)
    plan = make_plan()
    constructed = mock.Mock(head_sha=FRESH_HEAD, base_sha=FRESH_BASE)
    observed_snapshot_bases: list[str] = []

    def create_branch(snapshot_arg, _branch, *, commit):
        observed_snapshot_bases.append(snapshot_arg.base_sha)
        if snapshot_arg.base_sha != commit.base_sha:
            raise rollout.FleetGitError(
                "rollout base does not match the repository snapshot"
            )
        return commit.head_sha

    with (
        mock.patch.object(
            rollout, "_make_clone_workspace", return_value=nullcontext(str(workspace))
        ),
        mock.patch.object(rollout.fleet_git, "clone_default_branch", return_value=snap),
        mock.patch.object(
            rollout.fleet_git, "refetch_default", return_value=FRESH_BASE
        ),
        mock.patch.object(rollout, "git", return_value=""),
        mock.patch.object(rollout, "_render", return_value=plan),
        mock.patch.object(rollout, "validate_managed_result"),
        mock.patch.object(
            rollout,
            "inspect_rollout",
            return_value=rollout.RolloutInspection("create_branch"),
        ),
        mock.patch.object(
            rollout, "construct_rollout_commit", return_value=constructed
        ),
        mock.patch.object(rollout, "validate_commit_tree"),
        mock.patch.object(rollout.fleet_git, "remote_branch_sha", return_value=None),
        mock.patch.object(
            rollout.fleet_git, "create_rollout_branch", side_effect=create_branch
        ),
        mock.patch.object(
            rollout.fleet_git,
            "create_pull_request",
            side_effect=rollout.FleetGitError("PR sentinel"),
        ),
        mock.patch.object(rollout.fleet_git, "list_rollout_prs", return_value=()),
    ):
        result = rollout.publish_repository(
            bundle,
            workspace,
            repo="gstApp",
            bootstrap=False,
            actionlint=Path("/bin/true"),
        )

    assert observed_snapshot_bases == [FRESH_BASE]
    assert result.status == "blocked"
    assert result.base_sha == FRESH_BASE
    assert result.head_sha == FRESH_HEAD
    assert "branch published" in result.detail


def test_atomic_branch_success_pr_failure_preserves_fresh_branch_outcome(
    tmp_path: Path, bundle: ReleaseBundle
) -> None:
    workspace = marked_workspace(tmp_path)
    snap = snapshot(workspace)
    plan = make_plan()
    with (
        mock.patch.object(
            rollout,
            "_make_clone_workspace",
            return_value=nullcontext(str(workspace)),
        ),
        mock.patch.object(rollout.fleet_git, "clone_default_branch", return_value=snap),
        mock.patch.object(rollout.fleet_git, "refetch_default", return_value=BASE),
        mock.patch.object(rollout, "git", return_value=""),
        mock.patch.object(rollout, "_render", return_value=plan),
        mock.patch.object(rollout, "validate_managed_result"),
        mock.patch.object(
            rollout,
            "inspect_rollout",
            return_value=rollout.RolloutInspection("create_branch"),
        ),
        mock.patch.object(rollout, "publish_new_branch", return_value=HEAD),
        mock.patch.object(
            rollout.fleet_git,
            "create_pull_request",
            side_effect=rollout.FleetGitError("command failed (gh, rc=1)"),
        ),
        mock.patch.object(rollout.fleet_git, "list_rollout_prs", return_value=()),
    ):
        result = rollout.publish_repository(
            bundle,
            workspace,
            repo="gstApp",
            bootstrap=False,
            actionlint=Path("/bin/true"),
        )

    assert result.status == "blocked"
    assert result.base_sha == BASE
    assert result.head_sha == HEAD
    assert result.changed_paths == rollout._changed_paths(plan)
    assert "branch published" in result.detail


@pytest.mark.parametrize("stage", ["render", "validation", "branch", "pr"])
def test_fresh_publication_failures_report_fresh_stage_metadata(
    tmp_path: Path, bundle: ReleaseBundle, stage: str
) -> None:
    workspace = marked_workspace(tmp_path)
    snap = snapshot(workspace)
    plan = make_plan()

    def render(*_args, **_kwargs):
        if stage == "render":
            raise rollout.RolloutError("render stage sentinel")
        return plan

    def validate(*_args, **_kwargs):
        if stage == "validation":
            raise rollout.CommandError("validation stage sentinel")

    def inspect(*_args, **_kwargs):
        if stage == "branch":
            raise rollout.CommandError("branch stage sentinel")
        return rollout.RolloutInspection("create_branch")

    patches = [
        mock.patch.object(
            rollout, "_make_clone_workspace", return_value=nullcontext(str(workspace))
        ),
        mock.patch.object(rollout.fleet_git, "clone_default_branch", return_value=snap),
        mock.patch.object(
            rollout.fleet_git, "refetch_default", return_value=FRESH_BASE
        ),
        mock.patch.object(rollout, "git", return_value=""),
        mock.patch.object(rollout, "_render", side_effect=render),
        mock.patch.object(rollout, "validate_managed_result", side_effect=validate),
        mock.patch.object(rollout, "inspect_rollout", side_effect=inspect),
        mock.patch.object(rollout, "publish_new_branch", return_value=FRESH_HEAD),
        mock.patch.object(
            rollout.fleet_git,
            "create_pull_request",
            side_effect=rollout.FleetGitError("PR sentinel"),
        ),
        mock.patch.object(rollout.fleet_git, "list_rollout_prs", return_value=()),
    ]
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        patches[6],
        patches[7],
        patches[8],
        patches[9],
    ):
        result = rollout.publish_repository(
            bundle,
            workspace,
            repo="gstApp",
            bootstrap=False,
            actionlint=Path("/bin/true"),
        )

    assert result.status == "blocked"
    assert result.base_sha == FRESH_BASE
    assert result.changed_paths == (
        () if stage == "render" else rollout._changed_paths(plan)
    )
    assert result.head_sha == (FRESH_HEAD if stage == "pr" else "")
    assert result.stage == stage
    assert stage in result.detail


def test_branch_failure_after_commit_reports_the_fresh_prepared_head(
    tmp_path: Path, bundle: ReleaseBundle
) -> None:
    workspace = marked_workspace(tmp_path)
    snap = snapshot(workspace)
    plan = make_plan()
    with (
        mock.patch.object(
            rollout, "_make_clone_workspace", return_value=nullcontext(str(workspace))
        ),
        mock.patch.object(rollout.fleet_git, "clone_default_branch", return_value=snap),
        mock.patch.object(
            rollout.fleet_git, "refetch_default", return_value=FRESH_BASE
        ),
        mock.patch.object(rollout, "git", return_value=""),
        mock.patch.object(rollout, "_render", return_value=plan),
        mock.patch.object(rollout, "validate_managed_result"),
        mock.patch.object(
            rollout,
            "inspect_rollout",
            return_value=rollout.RolloutInspection("create_branch"),
        ),
        mock.patch.object(
            rollout,
            "construct_rollout_commit",
            return_value=mock.Mock(head_sha=FRESH_HEAD),
        ),
        mock.patch.object(
            rollout,
            "validate_commit_tree",
            side_effect=rollout.CommandError("commit validation sentinel"),
        ),
        mock.patch.object(rollout.fleet_git, "remote_branch_sha", return_value=None),
    ):
        result = rollout.publish_repository(
            bundle,
            workspace,
            repo="gstApp",
            bootstrap=False,
            actionlint=Path("/bin/true"),
        )

    assert result.status == "blocked"
    assert result.stage == "branch"
    assert result.base_sha == FRESH_BASE
    assert result.head_sha == FRESH_HEAD
    assert result.changed_paths == rollout._changed_paths(plan)


def test_publish_journal_uses_fresh_failure_metadata_not_prevalidation_base(
    tmp_path: Path, bundle: ReleaseBundle
) -> None:
    workspace = marked_workspace(tmp_path)
    fresh = rollout.RepoOutcome(
        "gstApp",
        "blocked",
        "publication validation failed: sentinel",
        FRESH_BASE,
        "",
        "",
        (".github/workflows/claude.yml",),
        "validation",
    )
    with (
        mock.patch.object(
            rollout,
            "materialize_release_bundle",
            side_effect=lambda *_a, **_k: fake_bundle_context(bundle),
        ),
        mock.patch.object(
            rollout,
            "prevalidate_repository",
            return_value=prepared("gstApp"),
        ),
        mock.patch.object(rollout, "publish_repository", return_value=fresh),
    ):
        assert (
            rollout.main(
                [
                    "--mode",
                    "publish",
                    "--confirm",
                    "--workspace",
                    str(workspace),
                    "--repo",
                    "gstApp",
                ]
            )
            == 1
        )
    report = json.loads((workspace / "rollout-manifest.json").read_text())
    assert report[0]["base_sha"] == FRESH_BASE
    assert report[0]["base_sha"] != BASE
    assert report[0]["changed_paths"] == [".github/workflows/claude.yml"]
    assert report[0]["stage"] == "validation"


@pytest.mark.parametrize("stage", ["render", "validation", "branch", "pr"])
def test_main_journals_actual_fresh_stage_failure_after_prevalidation_base_moves(
    tmp_path: Path, bundle: ReleaseBundle, stage: str
) -> None:
    workspace = marked_workspace(tmp_path)
    snap = snapshot(workspace)
    plan = make_plan()

    def render(*_args, **_kwargs):
        if stage == "render":
            raise rollout.RolloutError("render stage sentinel")
        return plan

    def validate(*_args, **_kwargs):
        if stage == "validation":
            raise rollout.CommandError("validation stage sentinel")

    def inspect(*_args, **_kwargs):
        if stage == "branch":
            raise rollout.CommandError("branch stage sentinel")
        return rollout.RolloutInspection("create_branch")

    with (
        mock.patch.object(
            rollout,
            "materialize_release_bundle",
            side_effect=lambda *_a, **_k: fake_bundle_context(bundle),
        ),
        mock.patch.object(
            rollout, "_make_clone_workspace", return_value=nullcontext(str(workspace))
        ),
        mock.patch.object(
            rollout,
            "prevalidate_repository",
            return_value=prepared("gstApp"),
        ),
        mock.patch.object(rollout.fleet_git, "clone_default_branch", return_value=snap),
        mock.patch.object(
            rollout.fleet_git, "refetch_default", return_value=FRESH_BASE
        ),
        mock.patch.object(rollout, "git", return_value=""),
        mock.patch.object(rollout, "_render", side_effect=render),
        mock.patch.object(rollout, "validate_managed_result", side_effect=validate),
        mock.patch.object(rollout, "inspect_rollout", side_effect=inspect),
        mock.patch.object(rollout, "publish_new_branch", return_value=FRESH_HEAD),
        mock.patch.object(
            rollout.fleet_git,
            "create_pull_request",
            side_effect=rollout.FleetGitError("PR stage sentinel"),
        ),
        mock.patch.object(rollout.fleet_git, "list_rollout_prs", return_value=()),
    ):
        assert (
            rollout.main(
                [
                    "--mode",
                    "publish",
                    "--confirm",
                    "--workspace",
                    str(workspace),
                    "--repo",
                    "gstApp",
                ]
            )
            == 1
        )

    report = json.loads((workspace / "rollout-manifest.json").read_text())
    assert report[0]["base_sha"] == FRESH_BASE
    assert report[0]["base_sha"] != BASE
    assert report[0]["stage"] == stage
    assert report[0]["changed_paths"] == (
        [] if stage == "render" else list(rollout._changed_paths(plan))
    )
    assert report[0]["head_sha"] == (FRESH_HEAD if stage == "pr" else "")


def test_publish_refetches_each_repo_and_reports_partial_rerunnable_outcomes(
    tmp_path: Path, bundle: ReleaseBundle
) -> None:
    workspace = marked_workspace(tmp_path)
    calls: list[str] = []

    def publish(*_args, repo: str, **_kwargs) -> rollout.RepoOutcome:
        calls.append(repo)
        if repo == "max9296":
            raise rollout.CommandError("refetch failed")
        return rollout.RepoOutcome(
            repo,
            "published" if repo == "gstApp" else "reused",
            "pull request ready",
            BASE,
            HEAD,
            f"https://github.com/jhw7500/{repo}/pull/1",
            (".github/workflows/claude.yml",),
        )

    with (
        mock.patch.object(
            rollout,
            "materialize_release_bundle",
            side_effect=lambda *_a, **_k: fake_bundle_context(bundle),
        ),
        mock.patch.object(
            rollout,
            "prevalidate_repository",
            side_effect=lambda *_a, repo, **_k: prepared(repo),
        ),
        mock.patch.object(rollout, "publish_repository", side_effect=publish),
    ):
        rc = rollout.main(
            [
                "--mode",
                "publish",
                "--confirm",
                "--workspace",
                str(workspace),
                "--repo",
                "gstApp",
                "--repo",
                "max9296",
                "--repo",
                "wlan-bridge",
            ]
        )

    assert rc == 1
    assert calls == ["gstApp", "max9296", "wlan-bridge"]
    report = json.loads((workspace / "rollout-manifest.json").read_text())
    assert [item["status"] for item in report] == ["published", "blocked", "reused"]
    assert report[0]["pr_url"].endswith("/gstApp/pull/1")
    assert "refetch failed" in report[1]["detail"]


def make_plan() -> RenderPlan:
    return RenderPlan(
        "drift",
        "managed bytes differ",
        (
            FileChange(PurePosixPath(".github/workflows/a.yml"), b"old\n", b"new\n"),
            FileChange(PurePosixPath(".github/workflows/deleted.yml"), b"gone\n", None),
        ),
        frozenset(),
        frozenset(),
        (
            ManagedResult(
                PurePosixPath(".github/workflows/a.yml"), b"new\n", "100644"
            ),
            ManagedResult(
                PurePosixPath(".github/workflows/deleted.yml"), None, None
            ),
        ),
    )


def snapshot(tmp_path: Path) -> RepositorySnapshot:
    repo = tmp_path / "gstApp"
    (repo / ".github/workflows").mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / ".github/workflows/a.yml").write_bytes(b"new\n")
    return RepositorySnapshot(repo, "main", BASE, ALL_SECRETS, ALL_VARIABLES)


def exact_pr(
    body: str,
    *,
    state: str = "OPEN",
    title: str | None = None,
    base: str = "main",
    head_repo: str = "jhw7500/gstApp",
    head_sha: str = HEAD,
) -> PullRequest:
    return PullRequest(
        7,
        "https://github.com/jhw7500/gstApp/pull/7",
        state,
        base,
        "automation/common-workflows-v1.40",
        head_repo,
        head_sha,
        title or rollout.pr_title("v1.40"),
        body,
    )


def test_absent_branch_requests_creation_and_matching_branch_without_pr_requests_only_pr(
    tmp_path: Path,
) -> None:
    snap = snapshot(tmp_path)
    plan = make_plan()
    body = rollout.pr_body("v1.40", COMMIT, tuple(str(c.path) for c in plan.changes))
    with (
        mock.patch.object(rollout.fleet_git, "remote_branch_sha", return_value=None),
        mock.patch.object(rollout.fleet_git, "list_rollout_prs", return_value=()),
    ):
        assert (
            rollout.inspect_rollout(snap, BASE, "v1.40", COMMIT, plan).action
            == "create_branch"
        )

    with (
        mock.patch.object(rollout.fleet_git, "remote_branch_sha", return_value=HEAD),
        mock.patch.object(rollout, "validate_existing_branch"),
        mock.patch.object(rollout.fleet_git, "list_rollout_prs", return_value=()),
    ):
        assert (
            rollout.inspect_rollout(snap, BASE, "v1.40", COMMIT, plan).action
            == "create_pr"
        )

    with (
        mock.patch.object(rollout.fleet_git, "remote_branch_sha", return_value=HEAD),
        mock.patch.object(rollout, "validate_existing_branch"),
        mock.patch.object(
            rollout.fleet_git, "list_rollout_prs", return_value=(exact_pr(body),)
        ),
    ):
        state = rollout.inspect_rollout(snap, BASE, "v1.40", COMMIT, plan)
        assert state.action == "reuse"
        assert state.pr_url.endswith("/pull/7")


def test_current_default_blocks_when_the_exact_rollout_branch_still_exists(
    tmp_path: Path,
) -> None:
    snap = snapshot(tmp_path)
    with mock.patch.object(rollout.fleet_git, "remote_branch_sha", return_value=HEAD):
        with pytest.raises(rollout.CommandError, match="branch"):
            rollout.require_no_current_rollout_branch(snap, "v1.40")


def test_existing_branch_is_validated_before_pr_read_and_rechecked_afterward(
    tmp_path: Path,
) -> None:
    snap = snapshot(tmp_path)
    plan = make_plan()
    events: list[str] = []
    body = rollout.pr_body(
        "v1.40", COMMIT, tuple(str(item.path) for item in plan.changes)
    )

    def branch_sha(*_args, **_kwargs):
        events.append("branch")
        return HEAD

    def validate(*_args, **_kwargs):
        events.append("validate")

    def requests(*_args, **_kwargs):
        events.append("prs")
        return (exact_pr(body),)

    with (
        mock.patch.object(
            rollout.fleet_git, "remote_branch_sha", side_effect=branch_sha
        ),
        mock.patch.object(rollout, "validate_existing_branch", side_effect=validate),
        mock.patch.object(rollout.fleet_git, "list_rollout_prs", side_effect=requests),
    ):
        rollout.inspect_rollout(snap, BASE, "v1.40", COMMIT, plan)

    assert events == ["branch", "validate", "prs", "branch"]


@pytest.mark.parametrize(
    "mismatch", ["base", "title", "body", "closed", "multiple", "fork", "head_sha"]
)
def test_existing_pr_requires_one_exact_open_match(
    tmp_path: Path, mismatch: str
) -> None:
    snap = snapshot(tmp_path)
    plan = make_plan()
    body = rollout.pr_body("v1.40", COMMIT, tuple(str(c.path) for c in plan.changes))
    pr = exact_pr(body)
    if mismatch == "base":
        pr = exact_pr(body, base="develop")
    elif mismatch == "title":
        pr = exact_pr(body, title="wrong")
    elif mismatch == "body":
        pr = exact_pr("wrong")
    elif mismatch == "closed":
        pr = exact_pr(body, state="CLOSED")
    elif mismatch == "fork":
        pr = exact_pr(body, head_repo="fork-owner/gstApp")
    elif mismatch == "head_sha":
        pr = exact_pr(body, head_sha="9" * 40)
    prs = (pr, exact_pr(body)) if mismatch == "multiple" else (pr,)
    with (
        mock.patch.object(rollout.fleet_git, "remote_branch_sha", return_value=HEAD),
        mock.patch.object(rollout, "validate_existing_branch"),
        mock.patch.object(rollout.fleet_git, "list_rollout_prs", return_value=prs),
    ):
        with pytest.raises(rollout.CommandError, match="pull request"):
            rollout.inspect_rollout(snap, BASE, "v1.40", COMMIT, plan)


@pytest.mark.parametrize("mismatch", ["parent", "paths", "blob", "deletion"])
def test_existing_branch_requires_exact_parent_paths_and_managed_bytes(
    tmp_path: Path, mismatch: str
) -> None:
    snap = snapshot(tmp_path)
    plan = make_plan()
    (snap.path / ".github/workflows/deleted.yml").unlink(missing_ok=True)

    def git(args, *, cwd=None, stdin=None):
        if args[:3] == ["rev-list", "--parents", "-n"]:
            parent = "9" * 40 if mismatch == "parent" else BASE
            return f"{HEAD} {parent}"
        if args[:2] == ["diff-tree", "--no-commit-id"]:
            paths = [str(item.path) for item in plan.changes]
            if mismatch == "paths":
                paths.append("project-owned.txt")
            return "\n".join(paths)
        if args[0] == "fetch":
            return ""
        if args[:2] == ["hash-object", "--stdin"]:
            return "expected-blob"
        if args[:2] == ["ls-tree", "-z"]:
            path = args[-1]
            if path.endswith("deleted.yml"):
                return (
                    f"100644 blob expected-blob\t{path}\0"
                    if mismatch == "deletion"
                    else ""
                )
            blob = "wrong-blob" if mismatch == "blob" else "expected-blob"
            return f"100644 blob {blob}\t{path}\0"
        raise AssertionError(args)

    with mock.patch.object(rollout, "git", side_effect=git):
        with pytest.raises(rollout.CommandError, match="rollout"):
            rollout.validate_existing_branch(snap, HEAD, BASE, plan)


def test_new_commit_tree_is_byte_attested_before_publication(tmp_path: Path) -> None:
    snap = snapshot(tmp_path)
    plan = make_plan()
    (snap.path / ".github/workflows/deleted.yml").unlink(missing_ok=True)

    def git(args, *, cwd=None, stdin=None):
        if args[:3] == ["rev-list", "--parents", "-n"]:
            return f"{HEAD} {BASE}"
        if args[:2] == ["diff-tree", "--no-commit-id"]:
            return "\n".join(str(item.path) for item in plan.changes)
        if args[:2] == ["hash-object", "--stdin"]:
            return "expected-blob"
        if args[:2] == ["ls-tree", "-z"]:
            path = args[-1]
            if path.endswith("deleted.yml"):
                return ""
            return f"100644 blob expected-blob\t{path}\0"
        raise AssertionError(args)

    with mock.patch.object(rollout, "git", side_effect=git):
        rollout.validate_commit_tree(snap, HEAD, BASE, plan)


def test_final_attestation_rejects_a_plan_without_complete_managed_results(
    tmp_path: Path,
) -> None:
    snap = snapshot(tmp_path)
    incomplete = RenderPlan(
        "drift",
        "incomplete",
        (
            FileChange(
                PurePosixPath(".github/workflows/a.yml"), b"old\n", b"new\n"
            ),
        ),
        frozenset(),
        frozenset(),
    )

    def git(args, *, cwd=None, stdin=None):
        if args[:3] == ["rev-list", "--parents", "-n"]:
            return f"{HEAD} {BASE}"
        if args[:2] == ["diff-tree", "--no-commit-id"]:
            return ".github/workflows/a.yml"
        if args[:2] == ["hash-object", "--stdin"]:
            return "expected-blob"
        if args[:2] == ["ls-tree", "-z"]:
            return "100644 blob expected-blob\t.github/workflows/a.yml\0"
        raise AssertionError(args)

    with mock.patch.object(rollout, "git", side_effect=git):
        with pytest.raises(rollout.CommandError, match="complete managed results"):
            rollout.validate_commit_tree(snap, HEAD, BASE, incomplete)


def test_new_commit_tree_rejects_clean_filter_blob_transformation(
    tmp_path: Path,
) -> None:
    snap = snapshot(tmp_path)
    plan = make_plan()

    def git(args, *, cwd=None, stdin=None):
        if args[:3] == ["rev-list", "--parents", "-n"]:
            return f"{HEAD} {BASE}"
        if args[:2] == ["diff-tree", "--no-commit-id"]:
            return "\n".join(str(item.path) for item in plan.changes)
        if args[:2] == ["hash-object", "--stdin"]:
            return "expected-blob"
        if args[:2] == ["ls-tree", "-z"]:
            path = args[-1]
            if path.endswith("deleted.yml"):
                return ""
            return f"100644 blob transformed-blob\t{path}\0"
        raise AssertionError(args)

    with mock.patch.object(rollout, "git", side_effect=git):
        with pytest.raises(rollout.CommandError, match="blob"):
            rollout.validate_commit_tree(snap, HEAD, BASE, plan)


def initialized_repository(tmp_path: Path) -> tuple[RepositorySnapshot, RenderPlan]:
    repo = tmp_path / "gstApp"
    workflow = repo / ".github/workflows/claude.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_bytes(b"old\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "baseline"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "baseline@example.invalid"],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "add", "--all"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "baseline"], cwd=repo, check=True)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    plan = RenderPlan(
        "drift",
        "one managed file differs",
        (
            FileChange(
                PurePosixPath(".github/workflows/claude.yml"),
                b"old\n",
                b"new\n",
            ),
        ),
        frozenset(),
        frozenset(),
        tuple(
            ManagedResult(
                entry.path,
                b"new\n"
                if entry.path == PurePosixPath(".github/workflows/claude.yml")
                else None,
                "100644"
                if entry.path == PurePosixPath(".github/workflows/claude.yml")
                else None,
            )
            for entry in CATALOG.entries
        ),
    )
    return RepositorySnapshot(repo, "main", base, frozenset(), frozenset()), plan


def initialized_canonical_fleet_repository(
    tmp_path: Path, bundle: ReleaseBundle
) -> RepositorySnapshot:
    """Create a real base commit whose managed tree is initially canonical."""

    repo = tmp_path / "gstApp"
    (repo / ".github/workflows").mkdir(parents=True)
    (repo / ".github/workflow-config.yml").write_text(
        "automation_ref: v1.39\nreview:\n  auto: false\n", encoding="utf-8"
    )
    initial = rollout.render_repository(
        repo,
        bundle.canonical,
        bundle.catalog,
        bundle.config.profiles[repo.name],
        bundle.ref,
        bundle.commit,
        set(ALL_SECRETS),
        set(ALL_VARIABLES),
    )
    assert initial.status == "drift"
    apply_render_plan(repo, initial)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "baseline"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "baseline@example.invalid"],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "add", "--all"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "canonical"], cwd=repo, check=True)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    return RepositorySnapshot(
        repo, "main", base, ALL_SECRETS, ALL_VARIABLES
    )


def commit_managed_fixture(repo: Path, message: str) -> str:
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()


def tree_entry(repo: Path, revision: str, relative: str) -> tuple[str, str, str]:
    record = subprocess.run(
        ["git", "ls-tree", revision, "--", relative],
        cwd=repo,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    metadata, observed_path = record.split("\t", 1)
    mode, object_type, oid = metadata.split(" ")
    assert observed_path == relative
    return mode, object_type, oid


def replace_committed_tree_entry(
    repo: Path,
    base: str,
    relative: str,
    mode: str,
    object_type: str,
    oid: str,
) -> str:
    """Create a real commit with one raw nested tree entry replaced."""

    def replace(tree_oid: str, components: tuple[str, ...]) -> str:
        listing = subprocess.run(
            ["git", "ls-tree", "-z", tree_oid],
            cwd=repo,
            check=True,
            stdout=subprocess.PIPE,
        ).stdout
        records = listing.split(b"\0")[:-1]
        target = components[0].encode("utf-8")
        replaced: list[bytes] = []
        found = False
        for record in records:
            metadata, name = record.split(b"\t", 1)
            if name != target:
                replaced.append(record)
                continue
            found = True
            if len(components) == 1:
                replacement = f"{mode} {object_type} {oid}".encode("ascii")
            else:
                current_mode, current_type, current_oid = metadata.decode(
                    "ascii"
                ).split(" ")
                assert current_mode == "040000" and current_type == "tree"
                nested = replace(current_oid, components[1:])
                replacement = f"040000 tree {nested}".encode("ascii")
            replaced.append(replacement + b"\t" + name)
        assert found
        return subprocess.run(
            ["git", "mktree", "-z"],
            cwd=repo,
            check=True,
            input=b"\0".join(replaced) + b"\0",
            stdout=subprocess.PIPE,
        ).stdout.decode("ascii").strip()

    root = subprocess.run(
        ["git", "rev-parse", f"{base}^{{tree}}"],
        cwd=repo,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    modified = replace(root, tuple(PurePosixPath(relative).parts))
    head = subprocess.run(
        ["git", "commit-tree", modified, "-p", base],
        cwd=repo,
        check=True,
        input=b"tree type drift\n",
        stdout=subprocess.PIPE,
    ).stdout.decode("ascii").strip()
    subprocess.run(["git", "update-ref", "HEAD", head, base], cwd=repo, check=True)
    return head


def test_real_rollout_repairs_content_and_separate_mode_drift_and_attests_all_managed(
    tmp_path: Path, bundle: ReleaseBundle
) -> None:
    """The final reviewer fixture must not preserve an omitted executable entry."""

    snap = initialized_canonical_fleet_repository(tmp_path, bundle)
    content_path = ".github/workflows/claude.yml"
    mode_path = ".github/workflows/gemini-triage.yml"
    content = snap.path / content_path
    content.write_bytes(content.read_bytes() + b"# content drift\n")
    subprocess.run(["git", "add", "--", content_path], cwd=snap.path, check=True)
    subprocess.run(
        ["git", "update-index", "--chmod=+x", "--", mode_path],
        cwd=snap.path,
        check=True,
    )
    base = commit_managed_fixture(snap.path, "content and mode drift")
    snap = RepositorySnapshot(
        snap.path, snap.default_branch, base, snap.secret_names, snap.variable_names
    )
    assert tree_entry(snap.path, base, mode_path)[:2] == ("100755", "blob")

    plan = rollout._render(snap, bundle, snap.path.name, bootstrap=False)
    constructed = rollout.construct_rollout_commit(
        snap, base, bundle.ref, plan, bundle.catalog
    )
    # Before the fix this returned successfully despite leaving mode_path at 100755.
    rollout.validate_commit_tree(snap, constructed.head_sha, base, plan)

    assert plan.status == "drift"
    assert rollout._changed_paths(plan) == tuple(sorted((content_path, mode_path)))
    assert tree_entry(snap.path, constructed.head_sha, mode_path)[:2] == (
        "100644",
        "blob",
    )


def test_real_mode_only_drift_is_planned_audited_and_published_as_a_repair(
    tmp_path: Path, bundle: ReleaseBundle
) -> None:
    snap = initialized_canonical_fleet_repository(tmp_path, bundle)
    relative = ".github/workflows/gemini-triage.yml"
    subprocess.run(
        ["git", "update-index", "--chmod=+x", "--", relative],
        cwd=snap.path,
        check=True,
    )
    base = commit_managed_fixture(snap.path, "mode-only drift")
    snap = RepositorySnapshot(
        snap.path, snap.default_branch, base, snap.secret_names, snap.variable_names
    )

    plan = rollout._render(snap, bundle, snap.path.name, bootstrap=False)
    result = rollout.audit_repository(
        snap.path,
        bundle,
        bundle.config.profiles[snap.path.name],
        set(snap.secret_names),
        set(snap.variable_names),
        observed_revision=base,
    )

    assert plan.status == "drift"
    assert plan.reason == "1 managed file(s) differ"
    assert rollout._changed_paths(plan) == (relative,)
    assert result.status == "drift"
    assert result.changed_paths == (relative,)
    published = rollout._prepared_outcome(
        snap.path.name,
        base,
        snap.base_branch or snap.default_branch,
        plan,
        rollout.RolloutInspection("create_branch"),
    )
    assert published.status == "planned"
    assert published.changed_paths == (relative,)
    change = plan.changes[0]
    assert change.before == change.after
    assert change.before_mode == "100755"
    assert change.after_mode == "100644"

    constructed = rollout.construct_rollout_commit(
        snap, base, bundle.ref, plan, bundle.catalog
    )
    rollout.validate_commit_tree(snap, constructed.head_sha, base, plan)
    rollout.fleet_git._validate_rollout_commit(
        constructed,
        bundle.ref,
        snap.base_branch or snap.default_branch,
        snap.default_branch,
    )
    assert tree_entry(snap.path, constructed.head_sha, relative)[:2] == (
        "100644",
        "blob",
    )
    changed = subprocess.run(
        [
            "git",
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-r",
            constructed.head_sha,
        ],
        cwd=snap.path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.splitlines()
    assert changed == [relative]


@pytest.mark.parametrize(
    ("mode", "object_type"),
    (("120000", "blob"), ("160000", "commit"), ("040000", "tree")),
)
def test_real_tracked_nonregular_managed_entry_blocks_before_checkout_bytes_are_read(
    tmp_path: Path,
    bundle: ReleaseBundle,
    mode: str,
    object_type: str,
) -> None:
    snap = initialized_canonical_fleet_repository(tmp_path, bundle)
    relative = ".github/workflows/gemini-triage.yml"
    if mode == "120000":
        oid = subprocess.run(
            ["git", "hash-object", "-w", "--stdin"],
            cwd=snap.path,
            check=True,
            input=b"../../outside-target",
            stdout=subprocess.PIPE,
        ).stdout.decode("ascii").strip()
    elif mode == "160000":
        oid = snap.base_sha
    else:
        child = subprocess.run(
            ["git", "hash-object", "-w", "--stdin"],
            cwd=snap.path,
            check=True,
            input=b"nested\n",
            stdout=subprocess.PIPE,
        ).stdout.decode("ascii").strip()
        oid = subprocess.run(
            ["git", "mktree"],
            cwd=snap.path,
            check=True,
            input=f"100644 blob {child}\tnested.yml\n".encode("ascii"),
            stdout=subprocess.PIPE,
        ).stdout.decode("ascii").strip()
    if mode == "040000":
        base = replace_committed_tree_entry(
            snap.path, snap.base_sha, relative, mode, object_type, oid
        )
    else:
        subprocess.run(
            ["git", "update-index", "--add", "--cacheinfo", mode, oid, relative],
            cwd=snap.path,
            check=True,
        )
        base = commit_managed_fixture(snap.path, f"{object_type} type drift")
    snap = RepositorySnapshot(
        snap.path, snap.default_branch, base, snap.secret_names, snap.variable_names
    )
    assert tree_entry(snap.path, base, relative)[:2] == (mode, object_type)
    # Deliberately leave canonical regular bytes in the checkout: only the observed
    # base tree exposes the unsafe entry, so filesystem-only inspection would miss it.
    assert (snap.path / relative).is_file()
    assert not (snap.path / relative).is_symlink()

    plan = rollout._render(snap, bundle, snap.path.name, bootstrap=False)

    assert plan.status == "blocked"
    assert plan.changes == ()
    assert relative in plan.reason
    assert "Git entry" in plan.reason


@pytest.mark.parametrize(
    ("mode", "object_type"), (("120000", "blob"), ("160000", "commit"))
)
def test_real_tracked_nonregular_managed_ancestor_blocks_before_checkout_scan(
    tmp_path: Path,
    bundle: ReleaseBundle,
    mode: str,
    object_type: str,
) -> None:
    snap = initialized_canonical_fleet_repository(tmp_path, bundle)
    workflows = ".github/workflows"
    # Replace the complete managed workflow directory in the observed tree with a
    # gitlink, while retaining ordinary checkout files to prove leaf-only ls-tree
    # queries and filesystem inspection cannot hide the unsafe ancestor.
    if mode == "120000":
        oid = subprocess.run(
            ["git", "hash-object", "-w", "--stdin"],
            cwd=snap.path,
            check=True,
            input=b"../../outside-workflows",
            stdout=subprocess.PIPE,
        ).stdout.decode("ascii").strip()
    else:
        oid = snap.base_sha
    base = replace_committed_tree_entry(
        snap.path,
        snap.base_sha,
        workflows,
        mode,
        object_type,
        oid,
    )
    snap = RepositorySnapshot(
        snap.path, snap.default_branch, base, snap.secret_names, snap.variable_names
    )
    assert tree_entry(snap.path, base, workflows)[:2] == (mode, object_type)
    assert (snap.path / ".github/workflows/claude.yml").is_file()

    plan = rollout._render(snap, bundle, snap.path.name, bootstrap=False)

    assert plan.status == "blocked"
    assert plan.changes == ()
    assert workflows in plan.reason
    assert "ancestor" in plan.reason


@pytest.mark.parametrize(
    ("replacement", "observed_mode"),
    (("symlink", "120000"), ("executable", "100755")),
)
def test_existing_branch_rejects_noncanonical_managed_tree_modes_even_when_blob_matches(
    tmp_path: Path, replacement: str, observed_mode: str
) -> None:
    """A matching blob cannot disguise a symlink or executable-bit mode drift."""

    snap, plan = initialized_repository(tmp_path)
    workflow = snap.path / ".github/workflows/claude.yml"
    if replacement == "symlink":
        workflow.unlink()
        workflow.symlink_to("new\n")
    else:
        workflow.write_bytes(b"new\n")
        workflow.chmod(0o755)
    subprocess.run(["git", "add", "--all"], cwd=snap.path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", replacement], cwd=snap.path, check=True
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=snap.path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    entry = subprocess.run(
        [
            "git",
            "ls-tree",
            head,
            "--",
            ".github/workflows/claude.yml",
        ],
        cwd=snap.path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout
    assert entry.startswith(f"{observed_mode} blob ")

    with pytest.raises(rollout.CommandError, match="mode|regular blob"):
        rollout.validate_existing_branch(snap, head, snap.base_sha, plan)


def executable_sentinel(path: Path, marker: Path, *, passthrough: bool) -> None:
    suffix = "cat\n" if passthrough else "exit 1\n"
    path.write_text(f"#!/bin/sh\nprintf ran > '{marker}'\n{suffix}")
    path.chmod(0o755)


def test_commit_plumbing_never_executes_a_configured_clean_filter(
    tmp_path: Path, bundle: ReleaseBundle
) -> None:
    snap, plan = initialized_repository(tmp_path)
    marker = tmp_path / "clean-filter-ran"
    helper = tmp_path / "clean-filter"
    executable_sentinel(helper, marker, passthrough=True)
    (snap.path / ".gitattributes").write_text(
        ".github/workflows/claude.yml filter=tripwire\n"
    )
    subprocess.run(
        ["git", "config", "filter.tripwire.clean", str(helper)],
        cwd=snap.path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "filter.tripwire.smudge", "cat"],
        cwd=snap.path,
        check=True,
    )

    constructed = rollout.construct_rollout_commit(
        snap, snap.base_sha, "v1.40", plan, bundle.catalog
    )

    assert not marker.exists()
    committed = subprocess.run(
        [
            "git",
            "cat-file",
            "blob",
            f"{constructed.head_sha}:.github/workflows/claude.yml",
        ],
        cwd=snap.path,
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    assert committed == b"new\n"
    subprocess.run(
        [
            "git",
            "hash-object",
            "-w",
            "--path=.github/workflows/claude.yml",
            "--stdin",
        ],
        cwd=snap.path,
        check=True,
        input=b"calibration\n",
        stdout=subprocess.PIPE,
    )
    assert marker.read_text() == "ran"


def test_commit_plumbing_normalizes_managed_files_to_canonical_regular_mode(
    tmp_path: Path, bundle: ReleaseBundle
) -> None:
    snap, plan = initialized_repository(tmp_path)
    (snap.path / ".github/workflows/claude.yml").chmod(0o755)

    constructed = rollout.construct_rollout_commit(
        snap, snap.base_sha, "v1.40", plan, bundle.catalog
    )
    entry = subprocess.run(
        [
            "git",
            "ls-tree",
            constructed.head_sha,
            "--",
            ".github/workflows/claude.yml",
        ],
        cwd=snap.path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout

    assert entry.startswith("100644 blob ")


def test_local_commit_identity_matches_atomic_api_commit_contract(
    tmp_path: Path, bundle: ReleaseBundle
) -> None:
    snap, plan = initialized_repository(tmp_path)

    constructed = rollout.construct_rollout_commit(
        snap, snap.base_sha, "v1.40", plan, bundle.catalog
    )

    rollout.fleet_git._validate_rollout_commit(
        constructed,
        "v1.40",
        snap.base_branch or snap.default_branch,
        snap.default_branch,
    )


def test_commit_plumbing_never_executes_a_configured_signing_helper(
    tmp_path: Path, bundle: ReleaseBundle
) -> None:
    snap, plan = initialized_repository(tmp_path)
    marker = tmp_path / "signing-helper-ran"
    helper = tmp_path / "signing-helper"
    executable_sentinel(helper, marker, passthrough=False)
    subprocess.run(
        ["git", "config", "commit.gpgSign", "true"], cwd=snap.path, check=True
    )
    subprocess.run(
        ["git", "config", "gpg.program", str(helper)], cwd=snap.path, check=True
    )

    constructed = rollout.construct_rollout_commit(
        snap, snap.base_sha, "v1.40", plan, bundle.catalog
    )

    assert not marker.exists()
    metadata = subprocess.run(
        [
            "git",
            "show",
            "-s",
            "--format=%P%n%s%n%an%n%ae",
            constructed.head_sha,
        ],
        cwd=snap.path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.splitlines()
    assert metadata == [
        snap.base_sha,
        rollout.pr_title("v1.40"),
        "workflow-fleet",
        "workflow-fleet@invalid",
    ]
    calibration = subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "calibration"],
        cwd=snap.path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert calibration.returncode != 0
    assert marker.read_text() == "ran"


def test_new_branch_publication_delegates_only_to_atomic_git_data_adapter(
    tmp_path: Path, bundle: ReleaseBundle
) -> None:
    snap = snapshot(tmp_path)
    plan = make_plan()
    calls: list[list[str]] = []
    constructed = mock.Mock(head_sha=HEAD)

    def git(args, *, cwd=None):
        calls.append(list(args))
        if args[:3] == ["diff", "--cached", "--name-only"]:
            return "\n".join(str(item.path) for item in plan.changes)
        if args == ["rev-parse", "HEAD"]:
            return HEAD
        if args[:3] == ["rev-list", "--parents", "-n"]:
            return f"{HEAD} {BASE}"
        return ""

    with (
        mock.patch.object(rollout, "git", side_effect=git),
        mock.patch.object(
            rollout,
            "construct_rollout_commit",
            return_value=constructed,
        ),
        mock.patch.object(rollout, "validate_commit_tree"),
        mock.patch.object(rollout, "validate_managed_result"),
        mock.patch.object(rollout.fleet_git, "remote_branch_sha", return_value=None),
        mock.patch.object(
            rollout.fleet_git, "create_rollout_branch", return_value=HEAD
        ) as create,
    ):
        assert (
            rollout.publish_new_branch(
                snap, BASE, "v1.40", COMMIT, plan, Path("/bin/true"), bundle=bundle
            )
            == HEAD
        )

    create.assert_called_once_with(
        snap,
        "automation/common-workflows-v1.40",
        commit=constructed,
    )
    flattened = " ".join(" ".join(args) for args in calls)
    for forbidden in (
        "push",
        "force",
        "merge",
        "auto-merge",
        "update-branch",
        "secret set",
        "variable set",
        "HEAD:refs/heads/main",
    ):
        assert forbidden not in flattened


def test_new_branch_revalidates_before_applying_to_the_publish_clone(
    tmp_path: Path, bundle: ReleaseBundle
) -> None:
    snap = snapshot(tmp_path)
    plan = make_plan()
    events: list[str] = []

    def git(args, *, cwd=None):
        if args[:3] == ["diff", "--cached", "--name-only"]:
            return "\n".join(str(item.path) for item in plan.changes)
        if args == ["rev-parse", "HEAD"]:
            return HEAD
        if args[:3] == ["rev-list", "--parents", "-n"]:
            return f"{HEAD} {BASE}"
        return ""

    with (
        mock.patch.object(rollout, "git", side_effect=git),
        mock.patch.object(
            rollout,
            "validate_managed_result",
            side_effect=lambda *_a, **_k: events.append("validate"),
        ),
        mock.patch.object(
            rollout,
            "construct_rollout_commit",
            side_effect=lambda *_a, **_k: (
                events.append("construct") or mock.Mock(head_sha=HEAD)
            ),
        ),
        mock.patch.object(rollout, "validate_commit_tree"),
        mock.patch.object(rollout.fleet_git, "remote_branch_sha", return_value=None),
        mock.patch.object(
            rollout.fleet_git, "create_rollout_branch", return_value=HEAD
        ),
    ):
        rollout.publish_new_branch(
            snap, BASE, "v1.40", COMMIT, plan, Path("/bin/true"), bundle=bundle
        )

    assert events == ["validate", "construct"]


@pytest.mark.parametrize(
    "script", ["scripts/rollout_workflow_fleet.py", "scripts/audit_workflow_fleet.py"]
)
def test_cli_scripts_support_direct_help_execution(script: str) -> None:
    completed = subprocess.run(
        ["python3", script, "--help"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert completed.returncode == 0, completed.stderr
    assert "--workspace" in completed.stdout


def test_actionlint_is_fail_closed_and_receives_only_managed_files(
    tmp_path: Path, bundle: ReleaseBundle
) -> None:
    repo = tmp_path / "gstApp"
    (repo / ".github/workflows").mkdir(parents=True)
    (repo / ".github/workflows/project-owned.yml").write_text("on: push\n")
    plan = RenderPlan(
        "drift",
        "diff",
        (
            FileChange(
                PurePosixPath(".github/workflows/claude.yml"), None, b"on: push\n"
            ),
        ),
        frozenset(),
        frozenset(),
    )
    with pytest.raises(rollout.CommandError, match="actionlint"):
        rollout.validate_managed_result(repo, bundle, plan, None, bootstrap=False)

    observed: list[list[str]] = []
    real_run = subprocess.run

    def child(args, **kwargs):
        observed.append(list(args))
        if Path(args[0]).name == "actionlint":
            return subprocess.CompletedProcess(
                args, 1, stdout="diagnostic\n", stderr=""
            )
        return real_run(args, **kwargs)

    actionlint = tmp_path / "actionlint"
    actionlint.write_text("#!/bin/sh\nexit 0\n")
    actionlint.chmod(0o755)
    with (
        mock.patch.object(
            rollout,
            "audit_repository",
            return_value=mock.Mock(
                status="current", detail="managed files are current"
            ),
        ),
        mock.patch("scripts.workflow_fleet_git.subprocess.run", side_effect=child),
    ):
        with pytest.raises(rollout.CommandError, match="actionlint"):
            rollout.validate_managed_result(
                repo, bundle, plan, actionlint, bootstrap=False
            )
    action_call = next(args for args in observed if Path(args[0]).name == "actionlint")
    assert any("claude.yml" in item for item in action_call)
    assert all("project-owned.yml" not in item for item in action_call)


def test_actionlint_child_receives_only_a_fixed_credential_free_environment(
    tmp_path: Path, bundle: ReleaseBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "gstApp"
    workflow = repo / ".github/workflows/claude.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("on: push\n")
    actionlint = tmp_path / "actionlint"
    actionlint.write_text("#!/bin/sh\nexit 0\n")
    actionlint.chmod(0o755)
    sensitive = {
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "ZHIPU_API_KEY",
        "APP_PRIVATE_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "UNRELATED_OPERATOR_SECRET",
    }
    for key in sensitive:
        monkeypatch.setenv(key, f"sentinel-{key}")
    observed: list[dict[str, object]] = []

    def child(args, **kwargs):
        observed.append({"args": list(args), **kwargs})
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    with mock.patch("scripts.rollout_workflow_fleet.subprocess.run", side_effect=child):
        rollout._run_actionlint(actionlint.resolve(), repo, bundle)

    assert len(observed) == 1
    call = observed[0]
    assert call["args"][0] == str(actionlint.resolve())
    assert call["args"] == [
        str(actionlint.resolve()),
        "-shellcheck=",
        "-pyflakes=",
        str(workflow),
    ]
    env = call["env"]
    assert isinstance(env, dict)
    assert set(env) == {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL"}
    assert env == {
        "PATH": "/usr/bin:/bin",
        "HOME": str(repo),
        "TMPDIR": str(repo),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }
    assert not any(str(value).startswith("sentinel-") for value in env.values())


def test_managed_result_passes_yaml_catalog_diff_and_local_actionlint_gates(
    tmp_path: Path, bundle: ReleaseBundle
) -> None:
    repo = tmp_path / "gstApp"
    (repo / ".github/workflows").mkdir(parents=True)
    (repo / ".github/workflow-config.yml").write_text(
        "automation_ref: v1.39\nreview:\n  auto: false\n", encoding="utf-8"
    )
    profile = bundle.config.profiles[repo.name]
    plan = rollout.render_repository(
        repo,
        bundle.canonical,
        bundle.catalog,
        profile,
        bundle.ref,
        bundle.commit,
        set(ALL_SECRETS),
        set(ALL_VARIABLES),
        bootstrap=False,
    )
    assert plan.status == "drift"

    rollout.validate_managed_result(
        repo, bundle, plan, Path("/bin/true"), bootstrap=False
    )


def test_managed_result_validation_never_executes_a_configured_clean_filter(
    tmp_path: Path, bundle: ReleaseBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "gstApp"
    workflow = repo / ".github/workflows/claude.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_bytes(b"name: old\n")
    (repo / ".github/.gitattributes").write_text(
        "workflows/claude.yml filter=validation-tripwire\n"
    )
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    marker = tmp_path / "validation-filter-ran"
    helper = tmp_path / "validation-filter"
    executable_sentinel(helper, marker, passthrough=True)
    global_config = tmp_path / "operator-gitconfig"
    global_config.write_text(
        '[filter "validation-tripwire"]\n'
        f"\tclean = {helper}\n"
        "\tsmudge = cat\n"
        "\trequired = true\n"
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    plan = RenderPlan(
        "drift",
        "managed bytes differ",
        (
            FileChange(
                PurePosixPath(".github/workflows/claude.yml"),
                b"name: old\n",
                b"name: new\n",
            ),
        ),
        frozenset(),
        frozenset(),
    )
    with mock.patch.object(
        rollout,
        "audit_repository",
        return_value=mock.Mock(status="current", detail="managed files are current"),
    ):
        rollout.validate_managed_result(
            repo, bundle, plan, Path("/bin/true"), bootstrap=False
        )

    assert not marker.exists()
    subprocess.run(
        [
            "git",
            "hash-object",
            "--path=.github/workflows/claude.yml",
            "--stdin",
        ],
        cwd=repo,
        check=True,
        input=b"calibration\n",
        stdout=subprocess.PIPE,
    )
    assert marker.read_text() == "ran"


def test_managed_result_filter_free_diff_still_rejects_trailing_whitespace(
    tmp_path: Path, bundle: ReleaseBundle
) -> None:
    repo = tmp_path / "gstApp"
    workflow = repo / ".github/workflows/claude.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_bytes(b"name: old\n")
    plan = RenderPlan(
        "drift",
        "managed bytes differ",
        (
            FileChange(
                PurePosixPath(".github/workflows/claude.yml"),
                b"name: old\n",
                b"name: new \n",
            ),
        ),
        frozenset(),
        frozenset(),
    )
    with mock.patch.object(
        rollout,
        "audit_repository",
        return_value=mock.Mock(status="current", detail="managed files are current"),
    ):
        with pytest.raises(rollout.CommandError, match="managed diff"):
            rollout.validate_managed_result(
                repo, bundle, plan, Path("/bin/true"), bootstrap=False
            )


def test_initialize_workspace_refuses_to_mark_nonempty_directory(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "not-disposable"
    workspace.mkdir()
    (workspace / "valuable.txt").write_text("keep")
    with mock.patch.object(rollout, "materialize_release_bundle") as load:
        with pytest.raises(SystemExit):
            rollout.main(
                [
                    "--mode",
                    "plan",
                    "--workspace",
                    str(workspace),
                    "--initialize-workspace",
                    "--repo",
                    "gstApp",
                ]
            )
        load.assert_not_called()
    assert (workspace / "valuable.txt").read_text() == "keep"
    assert not (workspace / ".automation-fleet-workspace").exists()


def test_initialize_workspace_rejects_a_symlinked_parent_before_bundle_load(
    tmp_path: Path,
) -> None:
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    workspace = linked_parent / "fleet"
    with mock.patch.object(rollout, "materialize_release_bundle") as load:
        with pytest.raises(SystemExit):
            rollout.main(
                [
                    "--mode",
                    "plan",
                    "--workspace",
                    str(workspace),
                    "--initialize-workspace",
                    "--repo",
                    "gstApp",
                ]
            )
        load.assert_not_called()
    assert list(real_parent.iterdir()) == []


def test_initialize_workspace_cleans_up_created_directories_after_marker_failure(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "new-parent" / "fleet"
    original_write = Path.write_text

    def fail_marker(path: Path, *args, **kwargs):
        if path.name == rollout.WORKSPACE_MARKER:
            raise OSError("marker write sentinel")
        return original_write(path, *args, **kwargs)

    with (
        mock.patch.object(Path, "write_text", fail_marker),
        mock.patch.object(rollout, "materialize_release_bundle") as load,
    ):
        with pytest.raises(SystemExit):
            rollout.main(
                [
                    "--mode",
                    "plan",
                    "--workspace",
                    str(workspace),
                    "--initialize-workspace",
                    "--repo",
                    "gstApp",
                ]
            )
        load.assert_not_called()
    assert not (tmp_path / "new-parent").exists()


def test_manifest_sink_is_prevalidated_before_any_publish(
    tmp_path: Path, bundle: ReleaseBundle
) -> None:
    workspace = marked_workspace(tmp_path)
    missing_parent_manifest = tmp_path / "missing" / "report.json"
    with (
        mock.patch.object(
            rollout,
            "materialize_release_bundle",
            side_effect=lambda *_a, **_k: fake_bundle_context(bundle),
        ),
        mock.patch.object(
            rollout,
            "prevalidate_repository",
            side_effect=lambda *_a, repo, **_k: prepared(repo),
        ),
        mock.patch.object(rollout, "publish_repository") as publish,
    ):
        with pytest.raises(rollout.CommandError, match="manifest"):
            rollout.main(
                [
                    "--mode",
                    "publish",
                    "--confirm",
                    "--workspace",
                    str(workspace),
                    "--repo",
                    "gstApp",
                    "--manifest",
                    str(missing_parent_manifest),
                ]
            )
    publish.assert_not_called()


def test_new_pull_request_is_attested_against_branch_and_exact_pr_metadata(
    tmp_path: Path,
) -> None:
    snap = snapshot(tmp_path)
    changed = (".github/workflows/claude.yml",)
    body = rollout.pr_body("v1.40", COMMIT, changed)
    request = exact_pr(body)
    with (
        mock.patch.object(rollout.fleet_git, "remote_branch_sha", return_value=HEAD),
        mock.patch.object(
            rollout.fleet_git, "list_rollout_prs", return_value=(request,)
        ),
    ):
        rollout.attest_pull_request(snap, "v1.40", COMMIT, HEAD, changed, request)

    with (
        mock.patch.object(
            rollout.fleet_git, "remote_branch_sha", return_value="9" * 40
        ),
        mock.patch.object(
            rollout.fleet_git, "list_rollout_prs", return_value=(request,)
        ),
    ):
        with pytest.raises(rollout.CommandError, match="branch"):
            rollout.attest_pull_request(snap, "v1.40", COMMIT, HEAD, changed, request)


@pytest.mark.parametrize(
    "observed",
    [
        exact_pr(
            rollout.pr_body("v1.40", COMMIT, (".github/workflows/claude.yml",)),
            head_repo="fork-owner/gstApp",
        ),
        exact_pr(
            rollout.pr_body("v1.40", COMMIT, (".github/workflows/claude.yml",)),
            head_sha="9" * 40,
        ),
    ],
)
def test_created_pr_attestation_rejects_fork_or_wrong_head_oid(
    tmp_path: Path, observed: PullRequest
) -> None:
    snap = snapshot(tmp_path)
    changed = (".github/workflows/claude.yml",)
    with (
        mock.patch.object(rollout.fleet_git, "remote_branch_sha", return_value=HEAD),
        mock.patch.object(
            rollout.fleet_git, "list_rollout_prs", return_value=(observed,)
        ),
    ):
        with pytest.raises(rollout.CommandError, match="attestation"):
            rollout.attest_pull_request(snap, "v1.40", COMMIT, HEAD, changed, observed)


@pytest.mark.parametrize(
    "reconciled",
    [
        exact_pr(
            rollout.pr_body(
                "v1.40",
                COMMIT,
                (
                    ".github/workflows/a.yml",
                    ".github/workflows/deleted.yml",
                ),
            ),
            head_repo="fork-owner/gstApp",
            head_sha=FRESH_HEAD,
        ),
        exact_pr(
            rollout.pr_body(
                "v1.40",
                COMMIT,
                (
                    ".github/workflows/a.yml",
                    ".github/workflows/deleted.yml",
                ),
            ),
            head_sha="9" * 40,
        ),
    ],
)
def test_pr_creation_reconciliation_rejects_fork_or_wrong_head_oid(
    tmp_path: Path, bundle: ReleaseBundle, reconciled: PullRequest
) -> None:
    workspace = marked_workspace(tmp_path)
    snap = snapshot(workspace)
    plan = make_plan()
    with (
        mock.patch.object(
            rollout, "_make_clone_workspace", return_value=nullcontext(str(workspace))
        ),
        mock.patch.object(rollout.fleet_git, "clone_default_branch", return_value=snap),
        mock.patch.object(
            rollout.fleet_git, "refetch_default", return_value=FRESH_BASE
        ),
        mock.patch.object(rollout, "git", return_value=""),
        mock.patch.object(rollout, "_render", return_value=plan),
        mock.patch.object(rollout, "validate_managed_result"),
        mock.patch.object(
            rollout,
            "inspect_rollout",
            return_value=rollout.RolloutInspection("create_branch"),
        ),
        mock.patch.object(rollout, "publish_new_branch", return_value=FRESH_HEAD),
        mock.patch.object(
            rollout.fleet_git,
            "create_pull_request",
            side_effect=rollout.FleetGitError("command failed (gh, rc=1)"),
        ),
        mock.patch.object(
            rollout.fleet_git, "list_rollout_prs", return_value=(reconciled,)
        ),
        mock.patch.object(
            rollout.fleet_git, "remote_branch_sha", return_value=FRESH_HEAD
        ),
    ):
        result = rollout.publish_repository(
            bundle,
            workspace,
            repo="gstApp",
            bootstrap=False,
            actionlint=Path("/bin/true"),
        )
    assert result.status == "blocked"
    assert result.base_sha == FRESH_BASE
    assert result.head_sha == FRESH_HEAD


def test_release_aware_applier_uses_bundle_catalog_not_current_checkout(
    tmp_path: Path, bundle: ReleaseBundle
) -> None:
    repo = tmp_path / "gstApp"
    path = repo / ".github/workflows/claude.yml"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"old\n")
    plan = RenderPlan(
        "drift",
        "one managed file differs",
        (
            FileChange(
                PurePosixPath(".github/workflows/claude.yml"), b"old\n", b"new\n"
            ),
        ),
        frozenset(),
        frozenset(),
    )
    with mock.patch(
        "scripts.prepare_workflow_rollout.load_catalog",
        side_effect=AssertionError("current checkout catalog must not be read"),
    ):
        assert rollout.apply_release_plan(repo, plan, bundle.catalog) == (
            PurePosixPath(".github/workflows/claude.yml"),
        )
    assert path.read_bytes() == b"new\n"


def test_release_aware_applier_prevalidates_every_change_before_writing(
    tmp_path: Path, bundle: ReleaseBundle
) -> None:
    repo = tmp_path / "gstApp"
    first = repo / ".github/workflows/claude.yml"
    stale = repo / ".github/workflows/gemini-review.yml"
    first.parent.mkdir(parents=True)
    first.write_bytes(b"first-old\n")
    stale.write_bytes(b"changed-after-render\n")
    plan = RenderPlan(
        "drift",
        "two files differ",
        (
            FileChange(
                PurePosixPath(".github/workflows/claude.yml"),
                b"first-old\n",
                b"first-new\n",
            ),
            FileChange(
                PurePosixPath(".github/workflows/gemini-review.yml"),
                b"stale-old\n",
                b"stale-new\n",
            ),
        ),
        frozenset(),
        frozenset(),
    )
    with pytest.raises(rollout.RolloutError, match="changed since rendering"):
        rollout.apply_release_plan(repo, plan, bundle.catalog)
    assert first.read_bytes() == b"first-old\n"
    assert stale.read_bytes() == b"changed-after-render\n"


@pytest.mark.parametrize(
    "args",
    [
        ["add", "--all"],
        ["commit", "-m", "unsafe"],
        ["diff", "--check"],
        ["init", "-q"],
        ["merge", "main"],
        ["update-branch"],
        ["secret", "set", "X"],
        ["variable", "set", "X"],
        ["push", "origin", "main"],
    ],
)
def test_git_wrapper_rejects_forbidden_commands_before_child(
    args: list[str],
) -> None:
    with mock.patch.object(rollout.fleet_git, "run") as child:
        with pytest.raises(rollout.CommandError, match="not permitted"):
            rollout.git(args)
        child.assert_not_called()
