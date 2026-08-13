"""Boundary tests for the restricted workflow-fleet Git/GitHub adapter."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess

import pytest

from scripts import workflow_fleet_git


PROVIDER_KEYS = {
    "CLAUDE_CODE_OAUTH_TOKEN",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "ZHIPU_API_KEY",
    "APP_PRIVATE_KEY",
}
SHA = "1" * 40
HEAD_SHA = "2" * 40


def workspace(tmp_path: Path) -> Path:
    (tmp_path / ".automation-fleet-workspace").touch()
    return tmp_path


def completed(args: list[str], stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")


def git_payload(args: list[str]) -> list[str]:
    assert args[:5] == [
        "git",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "submodule.recurse=false",
    ]
    return args[5:]


def snapshot(path: Path) -> workflow_fleet_git.RepositorySnapshot:
    return workflow_fleet_git.RepositorySnapshot(
        path=path,
        default_branch="main",
        base_sha=SHA,
        secret_names=frozenset({"A_SECRET"}),
        variable_names=frozenset({"A_VARIABLE"}),
    )


def test_child_env_scrubs_provider_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        return completed(args, "{}\n")

    monkeypatch.setattr(workflow_fleet_git.subprocess, "run", fake_run)
    for key in PROVIDER_KEYS:
        monkeypatch.setenv(key, f"sentinel-{key}")
    monkeypatch.setenv("GH_TOKEN", "operator-github-token")

    workflow_fleet_git.run(["gh", "repo", "view", "jhw7500/wlan-package"])

    env = calls[0][1]["env"]
    assert isinstance(env, dict)
    assert PROVIDER_KEYS.isdisjoint(env)
    assert env["GH_TOKEN"] == "operator-github-token"


def test_run_uses_closed_process_contract_and_sanitizes_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "sentinel-command-input"

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs["cwd"] is None
        assert kwargs["input"] == secret
        assert kwargs["text"] is True
        assert kwargs["stdout"] is subprocess.PIPE
        assert kwargs["stderr"] is subprocess.PIPE
        return subprocess.CompletedProcess(
            args, 17, stdout=f"leaked {secret}", stderr=f"leaked {secret}"
        )

    monkeypatch.setattr(workflow_fleet_git.subprocess, "run", fake_run)

    with pytest.raises(workflow_fleet_git.FleetGitError) as raised:
        workflow_fleet_git.run(["gh", "secret", secret], stdin=secret)

    assert str(raised.value) == "command failed (gh, rc=17)"
    assert secret not in str(raised.value)


def test_clone_reads_only_metadata_and_prerequisite_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = workspace(tmp_path)
    calls: list[tuple[list[str], Path | None]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        cwd = kwargs.get("cwd")
        assert cwd is None or isinstance(cwd, Path)
        calls.append((args, cwd))
        if args[:3] == ["gh", "repo", "view"]:
            return completed(
                args,
                json.dumps(
                    {
                        "defaultBranchRef": {"name": "main"},
                        "url": "https://github.com/jhw7500/wlan-package",
                    }
                ),
            )
        if args[:3] == ["gh", "secret", "list"]:
            return completed(args, '[{"name":"CLAUDE_CODE_OAUTH_TOKEN"}]')
        if args[:3] == ["gh", "variable", "list"]:
            return completed(args, '[{"name":"APP_ID"}]')
        payload = git_payload(args)
        if payload[0] == "clone":
            Path(payload[-1]).mkdir()
            (Path(payload[-1]) / ".git").mkdir()
            return completed(args)
        if payload == ["remote", "get-url", "origin"]:
            return completed(args, "https://github.com/jhw7500/wlan-package.git\n")
        if payload == ["rev-parse", "HEAD"]:
            return completed(args, f"{SHA}\n")
        raise AssertionError(args)

    monkeypatch.setattr(workflow_fleet_git.subprocess, "run", fake_run)

    result = workflow_fleet_git.clone_default_branch(
        "jhw7500", "wlan-package", root
    )

    assert result == workflow_fleet_git.RepositorySnapshot(
        path=root / "wlan-package",
        default_branch="main",
        base_sha=SHA,
        secret_names=frozenset({"CLAUDE_CODE_OAUTH_TOKEN"}),
        variable_names=frozenset({"APP_ID"}),
    )
    clone = next(git_payload(args) for args, _ in calls if args[0] == "git" and "clone" in args)
    assert clone == [
        "clone",
        "--no-recurse-submodules",
        "--single-branch",
        "--branch",
        "main",
        "https://github.com/jhw7500/wlan-package.git",
        str(root / "wlan-package"),
    ]
    gh_calls = [args for args, _ in calls if args[0] == "gh"]
    assert gh_calls == [
        [
            "gh",
            "repo",
            "view",
            "jhw7500/wlan-package",
            "--json",
            "defaultBranchRef,url",
        ],
        [
            "gh",
            "secret",
            "list",
            "-R",
            "jhw7500/wlan-package",
            "--json",
            "name",
        ],
        [
            "gh",
            "variable",
            "list",
            "-R",
            "jhw7500/wlan-package",
            "--json",
            "name",
        ],
    ]


@pytest.mark.parametrize(
    ("owner", "repo"),
    [
        ("someone-else", "wlan-package"),
        ("jhw7500", "../wlan-package"),
        ("jhw7500", "owner/wlan-package"),
        ("jhw7500", "--upload-pack=bad"),
    ],
)
def test_clone_rejects_open_ended_owner_or_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owner: str,
    repo: str,
) -> None:
    monkeypatch.setattr(
        workflow_fleet_git.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("invalid target reached a child process"),
    )

    with pytest.raises(workflow_fleet_git.FleetGitError):
        workflow_fleet_git.clone_default_branch(owner, repo, workspace(tmp_path))


def test_clone_requires_marker_and_rejects_symlinked_clone_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        workflow_fleet_git.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("unsafe workspace reached a child process"),
    )
    with pytest.raises(workflow_fleet_git.FleetGitError):
        workflow_fleet_git.clone_default_branch("jhw7500", "repo", tmp_path)

    root = workspace(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "repo").symlink_to(outside, target_is_directory=True)
    with pytest.raises(workflow_fleet_git.FleetGitError):
        workflow_fleet_git.clone_default_branch("jhw7500", "repo", root)


def test_clone_rejects_metadata_or_origin_outside_exact_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = workspace(tmp_path)

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if args[:3] == ["gh", "repo", "view"]:
            return completed(
                args,
                '{"defaultBranchRef":{"name":"main"},'
                '"url":"https://github.com/jhw7500/a-different-repo"}',
            )
        raise AssertionError("untrusted metadata must be rejected before clone")

    monkeypatch.setattr(workflow_fleet_git.subprocess, "run", fake_run)
    with pytest.raises(workflow_fleet_git.FleetGitError):
        workflow_fleet_git.clone_default_branch("jhw7500", "repo", root)


def test_refetch_default_uses_origin_tracking_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(git_payload(args))
        if calls[-1] == ["remote", "get-url", "origin"]:
            return completed(args, "https://github.com/jhw7500/repo.git")
        if calls[-1] == ["rev-parse", "refs/remotes/origin/main"]:
            return completed(args, HEAD_SHA)
        return completed(args)

    monkeypatch.setattr(workflow_fleet_git.subprocess, "run", fake_run)

    assert workflow_fleet_git.refetch_default(snapshot(tmp_path / "repo")) == HEAD_SHA
    assert calls == [
        ["remote", "get-url", "origin"],
        ["fetch", "--no-recurse-submodules", "origin", "main"],
        ["rev-parse", "refs/remotes/origin/main"],
    ]


@pytest.mark.parametrize(
    ("output", "expected"),
    [("", None), (f"{HEAD_SHA}\trefs/heads/release\n", HEAD_SHA)],
)
def test_remote_branch_sha_is_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    output: str,
    expected: str | None,
) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        payload = git_payload(args)
        calls.append(payload)
        if payload == ["remote", "get-url", "origin"]:
            return completed(args, "https://github.com/jhw7500/repo.git")
        return completed(args, output)

    monkeypatch.setattr(workflow_fleet_git.subprocess, "run", fake_run)

    assert (
        workflow_fleet_git.remote_branch_sha(snapshot(tmp_path / "repo"), "release")
        == expected
    )
    assert calls[-1] == ["ls-remote", "--heads", "origin", "refs/heads/release"]


def test_push_new_branch_proves_absence_and_never_forces_or_pushes_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        payload = git_payload(args)
        calls.append(payload)
        if payload == ["remote", "get-url", "origin"]:
            return completed(args, "https://github.com/jhw7500/repo.git")
        if payload == ["ls-remote", "--heads", "origin", "refs/heads/automation/common-workflows-v1.40"]:
            return completed(args, "")
        if payload == ["rev-parse", "refs/remotes/origin/main"]:
            return completed(args, HEAD_SHA)
        if payload == ["rev-parse", "HEAD"]:
            return completed(args, HEAD_SHA)
        return completed(args)

    monkeypatch.setattr(workflow_fleet_git.subprocess, "run", fake_run)

    result = workflow_fleet_git.push_new_branch(
        snapshot(tmp_path / "repo"), "automation/common-workflows-v1.40"
    )

    assert result == HEAD_SHA
    push = calls[-1]
    assert push == [
        "push",
        "--set-upstream",
        "origin",
        "HEAD:refs/heads/automation/common-workflows-v1.40",
    ]
    assert not any("force" in item for item in push)
    assert all("main" not in item for item in push)
    assert calls.index(
        ["ls-remote", "--heads", "origin", "refs/heads/automation/common-workflows-v1.40"]
    ) < calls.index(push)
    assert ["switch", "-c", "automation/common-workflows-v1.40", HEAD_SHA] in calls


def test_push_new_branch_refuses_existing_remote_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        payload = git_payload(args)
        calls.append(payload)
        if payload == ["remote", "get-url", "origin"]:
            return completed(args, "https://github.com/jhw7500/repo.git")
        return completed(args, f"{HEAD_SHA}\trefs/heads/release")

    monkeypatch.setattr(workflow_fleet_git.subprocess, "run", fake_run)

    with pytest.raises(workflow_fleet_git.FleetGitError):
        workflow_fleet_git.push_new_branch(snapshot(tmp_path / "repo"), "release")

    assert not any(payload and payload[0] in {"switch", "push"} for payload in calls)


def test_push_new_branch_rejects_default_branch_before_any_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        workflow_fleet_git.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail(
            "default-branch publication reached a child process"
        ),
    )

    with pytest.raises(workflow_fleet_git.FleetGitError):
        workflow_fleet_git.push_new_branch(snapshot(tmp_path / "repo"), "main")


def test_list_rollout_prs_queries_all_states_for_exact_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    raw = [
        {
            "number": 7,
            "url": "https://github.com/jhw7500/repo/pull/7",
            "state": "OPEN",
            "baseRefName": "main",
            "headRefName": "automation/common-workflows-v1.40",
            "title": "Roll out workflows",
            "body": "Body",
            "isDraft": False,
            "mergedAt": None,
        }
    ]

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return completed(args, json.dumps(raw))

    monkeypatch.setattr(workflow_fleet_git.subprocess, "run", fake_run)

    result = workflow_fleet_git.list_rollout_prs(
        "jhw7500", "repo", "automation/common-workflows-v1.40"
    )

    assert result == (
        workflow_fleet_git.PullRequest(
            number=7,
            url="https://github.com/jhw7500/repo/pull/7",
            state="OPEN",
            base="main",
            head="automation/common-workflows-v1.40",
            title="Roll out workflows",
            body="Body",
        ),
    )
    assert calls == [
        [
            "gh",
            "pr",
            "list",
            "-R",
            "jhw7500/repo",
            "--head",
            "automation/common-workflows-v1.40",
            "--state",
            "all",
            "--json",
            "number,url,state,baseRefName,headRefName,title,body,isDraft,mergedAt",
        ]
    ]


def test_create_pull_request_uses_0600_body_file_not_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    body = "private rollout details\nsecond line"

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        body_path = Path(args[args.index("--body-file") + 1])
        assert body_path.read_text(encoding="utf-8") == body
        assert stat.S_IMODE(body_path.stat().st_mode) == 0o600
        assert body not in args
        return completed(args, "https://github.com/jhw7500/repo/pull/42\n")

    monkeypatch.setattr(workflow_fleet_git.subprocess, "run", fake_run)

    result = workflow_fleet_git.create_pull_request(
        "jhw7500", "repo", "main", "release", "Roll out workflows", body
    )

    assert result == workflow_fleet_git.PullRequest(
        number=42,
        url="https://github.com/jhw7500/repo/pull/42",
        state="OPEN",
        base="main",
        head="release",
        title="Roll out workflows",
        body=body,
    )
    assert calls[0][:4] == ["gh", "pr", "create", "-R"]
    assert calls[0][4:12] == [
        "jhw7500/repo",
        "--base",
        "main",
        "--head",
        "release",
        "--title",
        "Roll out workflows",
        "--body-file",
    ]
    assert not Path(calls[0][-1]).exists()


def test_adapter_exposes_no_merge_revert_or_prerequisite_write_api() -> None:
    forbidden = {
        "merge",
        "revert",
        "set_secret",
        "set_variable",
        "delete_secret",
        "delete_variable",
    }
    assert forbidden.isdisjoint(vars(workflow_fleet_git))
    assert set(workflow_fleet_git.__all__) == {
        "FleetGitError",
        "PullRequest",
        "RepositorySnapshot",
        "clone_default_branch",
        "create_pull_request",
        "list_rollout_prs",
        "push_new_branch",
        "refetch_default",
        "remote_branch_sha",
    }
