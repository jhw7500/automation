"""Behavior tests for read-only fleet planning and PR-only publication."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import fields
import json
from pathlib import Path, PurePosixPath
import subprocess
from unittest import mock

import pytest

from scripts import rollout_workflow_fleet as rollout
from scripts.prepare_workflow_rollout import FileChange, RenderPlan
from scripts.workflow_fleet_git import PullRequest, RepositorySnapshot
from scripts.workflow_catalog import load_catalog, load_fleet_config
from scripts.workflow_release_bundle import ReleaseBundle


ROOT = Path(__file__).resolve().parents[1]
COMMIT = "1" * 40
BASE = "2" * 40
HEAD = "3" * 40
ALL_SECRETS = frozenset(
    {"CLAUDE_CODE_OAUTH_TOKEN", "GEMINI_API_KEY", "ZHIPU_API_KEY", "APP_PRIVATE_KEY"}
)
ALL_VARIABLES = frozenset({"APP_ID"})


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


def marked_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "fleet"
    workspace.mkdir()
    (workspace / ".automation-fleet-workspace").write_text(
        "managed disposable clones only\n", encoding="utf-8"
    )
    return workspace


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
        ["--repo", "wpa-supplicant", "--bootstrap-repo", "cts-email-mcp-server"],
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


def test_push_success_pr_failure_preserves_fresh_branch_outcome(
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
                "wlan-driver",
            ]
        )

    assert rc == 1
    assert calls == ["gstApp", "max9296", "wlan-driver"]
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
    )


def snapshot(tmp_path: Path) -> RepositorySnapshot:
    repo = tmp_path / "gstApp"
    (repo / ".github/workflows").mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / ".github/workflows/a.yml").write_bytes(b"new\n")
    return RepositorySnapshot(repo, "main", BASE, ALL_SECRETS, ALL_VARIABLES)


def exact_pr(
    body: str, *, state: str = "OPEN", title: str | None = None, base: str = "main"
) -> PullRequest:
    return PullRequest(
        7,
        "https://github.com/jhw7500/gstApp/pull/7",
        state,
        base,
        "automation/common-workflows-v1.40",
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


@pytest.mark.parametrize("mismatch", ["base", "title", "body", "closed", "multiple"])
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
        if args[:2] == ["ls-tree", "--name-only"]:
            return args[-1] if mismatch == "deletion" else ""
        if args[0] == "rev-parse":
            if ":" in args[-1]:
                return "wrong-blob" if mismatch == "blob" else "expected-blob"
            return HEAD
        raise AssertionError(args)

    with mock.patch.object(rollout, "git", side_effect=git):
        with pytest.raises(rollout.CommandError, match="rollout"):
            rollout.validate_existing_branch(snap, HEAD, BASE, plan)


def test_new_commit_tree_is_byte_attested_before_push(tmp_path: Path) -> None:
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
        if args[:2] == ["ls-tree", "--name-only"]:
            return ""
        if args[0] == "rev-parse":
            return "expected-blob"
        raise AssertionError(args)

    with mock.patch.object(rollout, "git", side_effect=git):
        rollout.validate_commit_tree(snap, HEAD, BASE, plan)


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
        if args[:2] == ["ls-tree", "--name-only"]:
            return ""
        if args[0] == "rev-parse":
            return "transformed-blob"
        raise AssertionError(args)

    with mock.patch.object(rollout, "git", side_effect=git):
        with pytest.raises(rollout.CommandError, match="blob"):
            rollout.validate_commit_tree(snap, HEAD, BASE, plan)


def test_new_branch_publication_uses_only_exact_non_force_refspec(
    tmp_path: Path, bundle: ReleaseBundle
) -> None:
    snap = snapshot(tmp_path)
    plan = make_plan()
    calls: list[list[str]] = []

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
            "apply_release_plan",
            return_value=tuple(item.path for item in plan.changes),
        ),
        mock.patch.object(rollout, "validate_commit_tree"),
        mock.patch.object(rollout, "validate_managed_result"),
        mock.patch.object(rollout.fleet_git, "remote_branch_sha", return_value=None),
    ):
        assert (
            rollout.publish_new_branch(
                snap, BASE, "v1.40", COMMIT, plan, Path("/bin/true"), bundle=bundle
            )
            == HEAD
        )

    push = next(args for args in calls if args and args[0] == "push")
    assert push == [
        "push",
        "--set-upstream",
        "origin",
        "HEAD:refs/heads/automation/common-workflows-v1.40",
    ]
    flattened = " ".join(" ".join(args) for args in calls)
    for forbidden in (
        "--force",
        "--force-with-lease",
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
            "apply_release_plan",
            side_effect=lambda *_a, **_k: (
                events.append("apply") or tuple(item.path for item in plan.changes)
            ),
        ),
        mock.patch.object(rollout, "validate_commit_tree"),
        mock.patch.object(rollout.fleet_git, "remote_branch_sha", return_value=None),
    ):
        rollout.publish_new_branch(
            snap, BASE, "v1.40", COMMIT, plan, Path("/bin/true"), bundle=bundle
        )

    assert events == ["validate", "apply"]


def test_accepted_push_with_lost_response_is_reconciled_as_success(
    tmp_path: Path, bundle: ReleaseBundle
) -> None:
    snap = snapshot(tmp_path)
    plan = make_plan()

    def git(args, *, cwd=None, stdin=None):
        if args[:3] == ["diff", "--cached", "--name-only"]:
            return "\n".join(str(item.path) for item in plan.changes)
        if args == ["rev-parse", "HEAD"]:
            return HEAD
        if args[0] == "push":
            raise rollout.FleetGitError("command failed (git, rc=1)")
        return ""

    with (
        mock.patch.object(rollout, "git", side_effect=git),
        mock.patch.object(rollout, "validate_managed_result"),
        mock.patch.object(rollout, "apply_release_plan"),
        mock.patch.object(rollout, "validate_commit_tree"),
        mock.patch.object(
            rollout.fleet_git,
            "remote_branch_sha",
            side_effect=[None, None, HEAD],
        ),
    ):
        assert (
            rollout.publish_new_branch(
                snap, BASE, "v1.40", COMMIT, plan, Path("/bin/true"), bundle=bundle
            )
            == HEAD
        )


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
    assert not (real_parent / "fleet" / ".automation-fleet-workspace").exists()


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
        ["merge", "main"],
        ["update-branch"],
        ["secret", "set", "X"],
        ["variable", "set", "X"],
        ["push", "--force", "origin", "main"],
    ],
)
def test_git_wrapper_rejects_forbidden_commands_before_child(
    args: list[str],
) -> None:
    with mock.patch.object(rollout.fleet_git, "run") as child:
        with pytest.raises(rollout.CommandError, match="not permitted"):
            rollout.git(args)
        child.assert_not_called()
