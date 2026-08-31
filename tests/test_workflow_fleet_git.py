"""Boundary tests for the restricted workflow-fleet Git/GitHub adapter."""

from __future__ import annotations

import base64
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import stat
import subprocess
import traceback

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
PR_HEAD_SHA = "3" * 40


def object_oid(kind: str, payload: bytes) -> str:
    return hashlib.sha1(
        f"{kind} {len(payload)}\0".encode("ascii") + payload
    ).hexdigest()


def atomic_publication_values(
    base_sha: str = SHA,
    message: str = "ci: adopt common automation workflows (v1.40)",
) -> dict[str, object]:
    content = b"name: managed\n"
    blob_sha = object_oid("blob", content)
    base_tree_sha = "4" * 40
    tree_sha = "5" * 40
    commit_payload = (
        f"tree {tree_sha}\n"
        f"parent {base_sha}\n"
        "author workflow-fleet <workflow-fleet@invalid> 946684800 +0000\n"
        "committer workflow-fleet <workflow-fleet@invalid> 946684800 +0000\n"
        f"\n{message}\n"
    ).encode("utf-8")
    return {
        "base_sha": base_sha,
        "base_tree_sha": base_tree_sha,
        "tree_sha": tree_sha,
        "head_sha": object_oid("commit", commit_payload),
        "content": content,
        "blob_sha": blob_sha,
        "message": message,
    }


def rollout_commit(
    values: dict[str, object], *, deletions: tuple[str, ...] = ()
) -> workflow_fleet_git.RolloutCommit:
    blob = workflow_fleet_git.GitBlob(
        ".github/workflows/managed.yml",
        "100644",
        str(values["blob_sha"]),
        values["content"],  # type: ignore[arg-type]
    )
    return workflow_fleet_git.RolloutCommit(
        head_sha=str(values["head_sha"]),
        tree_sha=str(values["tree_sha"]),
        base_tree_sha=str(values["base_tree_sha"]),
        base_sha=str(values["base_sha"]),
        message=str(values["message"]),
        blobs=(blob,),
        deletions=deletions,
    )


def pr_item(**overrides: object) -> dict[str, object]:
    item: dict[str, object] = {
        "number": 7,
        "url": "https://github.com/jhw7500/repo/pull/7",
        "state": "OPEN",
        "baseRefName": "main",
        "headRefName": "release",
        "headRefOid": PR_HEAD_SHA,
        "headRepository": {"nameWithOwner": "jhw7500/repo"},
        "headRepositoryOwner": {"login": "jhw7500"},
        "title": "Roll out workflows",
        "body": "Body",
        "isDraft": False,
        "mergedAt": None,
    }
    item.update(overrides)
    return item


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
    path.parent.mkdir(parents=True, exist_ok=True)
    workspace(path.parent)
    path.mkdir(exist_ok=True)
    (path / ".git").mkdir(exist_ok=True)
    return raw_snapshot(path)


def raw_snapshot(path: Path) -> workflow_fleet_git.RepositorySnapshot:
    return workflow_fleet_git.RepositorySnapshot(
        path=path,
        default_branch="main",
        base_sha=SHA,
        secret_names=frozenset({"A_SECRET"}),
        variable_names=frozenset({"A_VARIABLE"}),
        base_branch="main",
    )


def assert_sanitized_exception(
    error: BaseException, *sentinels: str, expected: str
) -> None:
    rendered = "".join(
        traceback.format_exception(type(error), error, error.__traceback__)
    )
    assert str(error) == expected
    assert error.__cause__ is None
    assert error.__context__ is None
    related: list[BaseException] = [error]
    seen: set[int] = set()
    while related:
        current = related.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        for sentinel in sentinels:
            assert sentinel not in rendered
            assert sentinel not in str(current)
            assert sentinel not in repr(current)
        if current.__cause__ is not None:
            related.append(current.__cause__)
        if current.__context__ is not None:
            related.append(current.__context__)


def test_child_env_scrubs_provider_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        return completed(args, "{}\n")

    monkeypatch.setattr(workflow_fleet_git.subprocess, "run", fake_run)
    for key in PROVIDER_KEYS:
        monkeypatch.setenv(key, f"sentinel-{key}")
    monkeypatch.setenv("GH_TOKEN", "operator-github-token")
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-github-token")

    workflow_fleet_git.run(["gh", "repo", "view", "jhw7500/wlan-package"])

    env = calls[0][1]["env"]
    assert isinstance(env, dict)
    assert PROVIDER_KEYS.isdisjoint(env)
    assert env["GH_TOKEN"] == "operator-github-token"
    assert "GITHUB_TOKEN" not in env


def test_github_env_uses_github_token_only_when_preferred_token_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "fallback-github-token")

    environment = workflow_fleet_git.github_env()

    assert environment["GITHUB_TOKEN"] == "fallback-github-token"
    assert "GH_TOKEN" not in environment


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


def test_child_launch_error_clears_provider_and_argv_exception_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = "provider-launch-sentinel"
    raw_argv = "raw-child-argv-sentinel"
    monkeypatch.setenv("GEMINI_API_KEY", provider)

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        env = kwargs["env"]
        assert isinstance(env, dict)
        assert "GEMINI_API_KEY" not in env
        raise OSError(f"raw child launch text: {provider} {raw_argv} {args!r}")

    monkeypatch.setattr(workflow_fleet_git.subprocess, "run", fake_run)

    with pytest.raises(workflow_fleet_git.FleetGitError) as raised:
        workflow_fleet_git.run(["gh", "repo", "view", raw_argv])

    assert_sanitized_exception(
        raised.value,
        provider,
        raw_argv,
        "raw child launch text",
        expected="command failed (gh, rc=unavailable)",
    )


def test_malformed_json_error_clears_raw_response_exception_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = "provider-json-sentinel"
    monkeypatch.setattr(
        workflow_fleet_git.subprocess,
        "run",
        lambda args, **kwargs: completed(args, "{" + provider),
    )

    with pytest.raises(workflow_fleet_git.FleetGitError) as raised:
        workflow_fleet_git.list_rollout_prs("jhw7500", "repo", "release")

    assert_sanitized_exception(
        raised.value,
        provider,
        expected="GitHub returned malformed JSON",
    )


@pytest.mark.parametrize("failure", ["missing-repo", "missing-marker"])
def test_snapshot_path_error_clears_raw_path_exception_chain(
    tmp_path: Path, failure: str
) -> None:
    sentinel = f"raw-path-sentinel-{failure}"
    root = tmp_path / sentinel
    repo = root / "repo"
    if failure == "missing-marker":
        (repo / ".git").mkdir(parents=True)

    with pytest.raises(workflow_fleet_git.FleetGitError) as raised:
        workflow_fleet_git.refetch_default(raw_snapshot(repo))

    assert_sanitized_exception(
        raised.value,
        sentinel,
        str(repo),
        expected=(
            "repository root is not a real path"
            if failure == "missing-repo"
            else "workspace marker is unavailable"
        ),
    )


def test_clone_marker_error_clears_raw_workspace_exception_chain(
    tmp_path: Path,
) -> None:
    sentinel = "raw-clone-workspace-sentinel"
    root = tmp_path / sentinel
    root.mkdir()

    with pytest.raises(workflow_fleet_git.FleetGitError) as raised:
        workflow_fleet_git.clone_default_branch("jhw7500", "repo", root)

    assert_sanitized_exception(
        raised.value,
        sentinel,
        str(root),
        expected="workspace is not marked for fleet automation",
    )


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
        if payload in [
            ["remote", "get-url", "--all", "origin"],
            ["remote", "get-url", "--push", "--all", "origin"],
        ]:
            return completed(args, "https://github.com/jhw7500/wlan-package.git\n")
        if payload == ["rev-parse", "HEAD"]:
            return completed(args, f"{SHA}\n")
        raise AssertionError(args)

    monkeypatch.setattr(workflow_fleet_git.subprocess, "run", fake_run)

    result = workflow_fleet_git.clone_default_branch("jhw7500", "wlan-package", root)

    assert result == workflow_fleet_git.RepositorySnapshot(
        path=root / "wlan-package",
        default_branch="main",
        base_sha=SHA,
        secret_names=frozenset({"CLAUDE_CODE_OAUTH_TOKEN"}),
        variable_names=frozenset({"APP_ID"}),
        base_branch="main",
    )
    clone = next(
        git_payload(args) for args, _ in calls if args[0] == "git" and "clone" in args
    )
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


def test_clone_branch_retains_repository_default_and_selected_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = workspace(tmp_path)
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
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
        if args[:3] in (["gh", "secret", "list"], ["gh", "variable", "list"]):
            return completed(args, "[]")
        payload = git_payload(args)
        if payload[0] == "clone":
            Path(payload[-1]).mkdir()
            (Path(payload[-1]) / ".git").mkdir()
            return completed(args)
        if payload in [
            ["remote", "get-url", "--all", "origin"],
            ["remote", "get-url", "--push", "--all", "origin"],
        ]:
            return completed(args, "https://github.com/jhw7500/wlan-package.git\n")
        if payload == ["rev-parse", "HEAD"]:
            return completed(args, SHA)
        raise AssertionError(args)

    monkeypatch.setattr(workflow_fleet_git.subprocess, "run", fake_run)

    result = workflow_fleet_git.clone_branch(
        "jhw7500", "wlan-package", root, "ported"
    )

    assert result.default_branch == "main"
    assert result.base_branch == "ported"
    assert next(
        git_payload(args)
        for args in calls
        if args[0] == "git" and "clone" in args
    ) == [
        "clone",
        "--no-recurse-submodules",
        "--single-branch",
        "--branch",
        "ported",
        "https://github.com/jhw7500/wlan-package.git",
        str(root / "wlan-package"),
    ]


@pytest.mark.parametrize(
    "metadata",
    [
        {"url": "https://github.com/jhw7500/repo"},
        {"defaultBranchRef": None, "url": "https://github.com/jhw7500/repo"},
        {
            "defaultBranchRef": {"name": "bad branch"},
            "url": "https://github.com/jhw7500/repo",
        },
    ],
)
def test_clone_branch_rejects_missing_or_malformed_default_branch_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, metadata: dict[str, object]
) -> None:
    monkeypatch.setattr(
        workflow_fleet_git.subprocess,
        "run",
        lambda args, **kwargs: completed(args, json.dumps(metadata)),
    )

    with pytest.raises(
        workflow_fleet_git.FleetGitError,
        match="malformed repository metadata|branch name is invalid",
    ):
        workflow_fleet_git.clone_branch("jhw7500", "repo", workspace(tmp_path), "ported")


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
        if calls[-1] in [
            ["remote", "get-url", "--all", "origin"],
            ["remote", "get-url", "--push", "--all", "origin"],
        ]:
            return completed(args, "https://github.com/jhw7500/repo.git")
        if calls[-1] == ["rev-parse", "refs/remotes/origin/main"]:
            return completed(args, HEAD_SHA)
        return completed(args)

    monkeypatch.setattr(workflow_fleet_git.subprocess, "run", fake_run)

    assert workflow_fleet_git.refetch_default(snapshot(tmp_path / "repo")) == HEAD_SHA
    assert calls == [
        ["remote", "get-url", "--all", "origin"],
        ["remote", "get-url", "--push", "--all", "origin"],
        ["fetch", "--no-recurse-submodules", "origin", "main"],
        ["rev-parse", "refs/remotes/origin/main"],
    ]


def test_refetch_branch_uses_selected_base_origin_tracking_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(git_payload(args))
        if calls[-1] in [
            ["remote", "get-url", "--all", "origin"],
            ["remote", "get-url", "--push", "--all", "origin"],
        ]:
            return completed(args, "https://github.com/jhw7500/repo.git")
        if calls[-1] == ["rev-parse", "refs/remotes/origin/ported"]:
            return completed(args, HEAD_SHA)
        return completed(args)

    monkeypatch.setattr(workflow_fleet_git.subprocess, "run", fake_run)
    item = replace(snapshot(tmp_path / "repo"), base_branch="ported")

    assert workflow_fleet_git.refetch_branch(item) == HEAD_SHA
    assert calls == [
        ["remote", "get-url", "--all", "origin"],
        ["remote", "get-url", "--push", "--all", "origin"],
        ["fetch", "--no-recurse-submodules", "origin", "ported"],
        ["rev-parse", "refs/remotes/origin/ported"],
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
        if payload in [
            ["remote", "get-url", "--all", "origin"],
            ["remote", "get-url", "--push", "--all", "origin"],
        ]:
            return completed(args, "https://github.com/jhw7500/repo.git")
        return completed(args, output)

    monkeypatch.setattr(workflow_fleet_git.subprocess, "run", fake_run)

    assert (
        workflow_fleet_git.remote_branch_sha(snapshot(tmp_path / "repo"), "release")
        == expected
    )
    assert calls[-1] == ["ls-remote", "--heads", "origin", "refs/heads/release"]


def test_additional_base_rollout_identity_requires_its_exact_digest_and_commit_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base_branch = "ported"
    digest = hashlib.sha256(base_branch.encode("utf-8")).hexdigest()
    branch = f"automation/common-workflows-v1.40-{digest}"
    message = f"ci: adopt common automation workflows (v1.40; base={base_branch})"
    item = replace(snapshot(tmp_path / "repo"), base_branch=base_branch)
    values = atomic_publication_values(message=message)

    monkeypatch.setattr(workflow_fleet_git, "_verify_origin", lambda *_args: None)
    monkeypatch.setattr(
        workflow_fleet_git,
        "_github_post",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("reached GitHub API")),
    )

    with pytest.raises(RuntimeError, match="reached GitHub API"):
        workflow_fleet_git.create_rollout_branch(
            item, branch, commit=rollout_commit(values)
        )

    for invalid_branch, invalid_message in (
        (f"automation/common-workflows-v1.40-{'0' * 64}", message),
        ("automation/common-workflows-v1.40", message),
        (branch, "ci: adopt common automation workflows (v1.40)"),
    ):
        invalid_values = atomic_publication_values(message=invalid_message)
        with pytest.raises(workflow_fleet_git.FleetGitError):
            workflow_fleet_git.create_rollout_branch(
                item, invalid_branch, commit=rollout_commit(invalid_values)
            )


def test_atomic_rollout_branch_creation_uses_only_literal_git_data_post_schemas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    values = atomic_publication_values()
    branch = "automation/common-workflows-v1.40"
    repo = snapshot(tmp_path / "repo")
    calls: list[tuple[list[str], dict[str, object]]] = []
    provider_sentinels = {
        "CLAUDE_CODE_OAUTH_TOKEN": "claude-provider-sentinel",
        "GEMINI_API_KEY": "gemini-provider-sentinel",
        "UNRELATED_OPERATOR_SECRET": "unrelated-secret-sentinel",
    }
    for key, value in provider_sentinels.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("GH_TOKEN", "github-credential-sentinel")

    api_root = "https://api.github.com/repos/jhw7500/repo/git"

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((list(args), dict(kwargs)))
        if args[0] == "git":
            payload = git_payload(args)
            if payload in (
                ["remote", "get-url", "--all", "origin"],
                ["remote", "get-url", "--push", "--all", "origin"],
            ):
                return completed(args, "https://github.com/jhw7500/repo.git\n")
            if payload == [
                "ls-remote",
                "--heads",
                "origin",
                f"refs/heads/{branch}",
            ]:
                return completed(args, f"{values['head_sha']}\trefs/heads/{branch}\n")
            raise AssertionError(args)

        assert args[:5] == [
            "gh",
            "api",
            "--hostname",
            "github.com",
            "--method",
        ]
        assert args[5] == "POST"
        assert args[-2:] == ["--input", "-"]
        endpoint = args[6]
        body = json.loads(str(kwargs["input"]))
        if endpoint == "repos/jhw7500/repo/git/blobs":
            assert body == {
                "content": base64.b64encode(values["content"]).decode("ascii"),
                "encoding": "base64",
            }
            response = {
                "sha": values["blob_sha"],
                "url": f"{api_root}/blobs/{values['blob_sha']}",
            }
        elif endpoint == "repos/jhw7500/repo/git/trees":
            assert body == {
                "base_tree": values["base_tree_sha"],
                "tree": [
                    {
                        "mode": "100644",
                        "path": ".github/workflows/managed.yml",
                        "sha": values["blob_sha"],
                        "type": "blob",
                    },
                    {
                        "mode": "100644",
                        "path": ".github/workflows/deleted.yml",
                        "sha": None,
                        "type": "blob",
                    },
                ],
            }
            response = {
                "sha": values["tree_sha"],
                "url": f"{api_root}/trees/{values['tree_sha']}",
                "tree": [],
                "truncated": False,
            }
        elif endpoint == "repos/jhw7500/repo/git/commits":
            identity = {
                "name": "workflow-fleet",
                "email": "workflow-fleet@invalid",
                "date": "2000-01-01T00:00:00Z",
            }
            assert body == {
                "author": identity,
                "committer": identity,
                # GitHub hashes the message exactly as submitted.  The local
                # commit-tree identity includes one terminal LF, while the API
                # response presents the normalized subject without that LF.
                "message": f"{values['message']}\n",
                "parents": [values["base_sha"]],
                "tree": values["tree_sha"],
            }
            response = {
                "sha": values["head_sha"],
                "url": f"{api_root}/commits/{values['head_sha']}",
                "message": values["message"],
                "author": identity,
                "committer": identity,
                "tree": {
                    "sha": values["tree_sha"],
                    "url": f"{api_root}/trees/{values['tree_sha']}",
                },
                "parents": [
                    {
                        "sha": values["base_sha"],
                        "url": f"{api_root}/commits/{values['base_sha']}",
                    }
                ],
            }
        elif endpoint == "repos/jhw7500/repo/git/refs":
            assert body == {
                "ref": f"refs/heads/{branch}",
                "sha": values["head_sha"],
            }
            response = {
                "ref": f"refs/heads/{branch}",
                "url": f"https://api.github.com/repos/jhw7500/repo/git/refs/heads/{branch}",
                "object": {
                    "sha": values["head_sha"],
                    "type": "commit",
                    "url": f"{api_root}/commits/{values['head_sha']}",
                },
            }
        else:
            raise AssertionError(endpoint)
        return completed(args, json.dumps(response))

    monkeypatch.setattr(workflow_fleet_git.subprocess, "run", fake_run)
    result = workflow_fleet_git.create_rollout_branch(
        repo,
        branch,
        commit=rollout_commit(values, deletions=(".github/workflows/deleted.yml",)),
    )

    assert result == values["head_sha"]
    gh_calls = [(args, kwargs) for args, kwargs in calls if args[0] == "gh"]
    assert [args[6] for args, _ in gh_calls] == [
        "repos/jhw7500/repo/git/blobs",
        "repos/jhw7500/repo/git/trees",
        "repos/jhw7500/repo/git/commits",
        "repos/jhw7500/repo/git/refs",
    ]
    for args, kwargs in gh_calls:
        assert not any(item.lstrip("-").startswith("force") for item in args)
        assert values["content"].decode("utf-8") not in " ".join(args)
        env = kwargs["env"]
        assert isinstance(env, dict)
        assert env["GH_TOKEN"] == "github-credential-sentinel"
        assert provider_sentinels.keys().isdisjoint(env)
        assert "UNRELATED_OPERATOR_SECRET" not in env


@pytest.mark.parametrize("corrupt", ("blob", "tree", "commit", "ref"))
def test_atomic_rollout_branch_creation_rejects_each_mismatched_api_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    corrupt: str,
) -> None:
    values = atomic_publication_values()
    branch = "automation/common-workflows-v1.40"
    repo = snapshot(tmp_path / "repo")
    identity = {
        "name": "workflow-fleet",
        "email": "workflow-fleet@invalid",
        "date": "2000-01-01T00:00:00Z",
    }
    api_root = "https://api.github.com/repos/jhw7500/repo/git"
    responses = {
        "blobs": {
            "sha": values["blob_sha"],
            "url": f"{api_root}/blobs/{values['blob_sha']}",
        },
        "trees": {
            "sha": values["tree_sha"],
            "url": f"{api_root}/trees/{values['tree_sha']}",
            "tree": [],
            "truncated": False,
        },
        "commits": {
            "sha": values["head_sha"],
            "url": f"{api_root}/commits/{values['head_sha']}",
            "message": values["message"],
            "author": identity,
            "committer": identity,
            "tree": {
                "sha": values["tree_sha"],
                "url": f"{api_root}/trees/{values['tree_sha']}",
            },
            "parents": [
                {
                    "sha": values["base_sha"],
                    "url": f"{api_root}/commits/{values['base_sha']}",
                }
            ],
        },
        "refs": {
            "ref": f"refs/heads/{branch}",
            "url": f"https://api.github.com/repos/jhw7500/repo/git/refs/heads/{branch}",
            "object": {
                "sha": values["head_sha"],
                "type": "commit",
                "url": f"{api_root}/commits/{values['head_sha']}",
            },
        },
    }
    response = responses[
        {"blob": "blobs", "tree": "trees", "commit": "commits", "ref": "refs"}[corrupt]
    ]
    if corrupt == "ref":
        response["object"] = {  # type: ignore[index]
            "sha": "9" * 40,
            "type": "commit",
            "url": f"{api_root}/commits/{'9' * 40}",
        }
    else:
        response["sha"] = "9" * 40

    monkeypatch.setattr(
        workflow_fleet_git,
        "_github_post",
        lambda _repo, collection, _body: responses[collection],
    )
    monkeypatch.setattr(
        workflow_fleet_git,
        "remote_branch_sha",
        lambda *_args, **_kwargs: SHA,
    )
    monkeypatch.setattr(workflow_fleet_git, "_verify_origin", lambda *_args: None)
    with pytest.raises(workflow_fleet_git.FleetGitError, match="GitHub returned"):
        workflow_fleet_git.create_rollout_branch(
            repo,
            branch,
            commit=rollout_commit(values),
        )


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("head_sha", "9" * 40),
        ("message", "ci: adopt common automation workflows (v9.99)"),
    ),
    ids=("commit-object-id", "branch-bound-message"),
)
def test_atomic_branch_rejects_incoherent_local_commit_identity_before_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    replacement: str,
) -> None:
    values = atomic_publication_values()
    commit = rollout_commit(values)
    commit = replace(commit, **{field: replacement})
    repo = snapshot(tmp_path / "repo")
    monkeypatch.setattr(workflow_fleet_git, "_verify_origin", lambda *_args: None)
    monkeypatch.setattr(
        workflow_fleet_git,
        "_github_post",
        lambda *_args, **_kwargs: pytest.fail(
            "incoherent local identity reached the GitHub API"
        ),
    )

    with pytest.raises(workflow_fleet_git.FleetGitError, match="identity|message"):
        workflow_fleet_git.create_rollout_branch(
            repo,
            "automation/common-workflows-v1.40",
            commit=commit,
        )


@pytest.mark.parametrize(
    ("concurrent_sha", "succeeds"),
    ((SHA, False), (None, True)),
    ids=("mismatched-concurrent-ref", "exact-idempotent-reconciliation"),
)
def test_atomic_ref_post_blocks_concurrent_branch_without_advancing_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    concurrent_sha: str | None,
    succeeds: bool,
) -> None:
    """Calibrate the old race, then prove POST refs is create-only."""

    old = tmp_path / "old"
    remote = tmp_path / "old-remote.git"
    old.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=old, check=True)
    subprocess.run(["git", "config", "user.name", "Race"], cwd=old, check=True)
    subprocess.run(
        ["git", "config", "user.email", "race@example.invalid"],
        cwd=old,
        check=True,
    )
    (old / "file").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "file"], cwd=old, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=old, check=True)
    old_base = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=old,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=old, check=True)
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=old, check=True)
    (old / "file").write_text("head\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "head"], cwd=old, check=True)
    old_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=old,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    race_ref = "refs/heads/automation/common-workflows-v1.40"
    subprocess.run(
        ["git", "--git-dir", str(remote), "update-ref", race_ref, old_base],
        check=True,
    )
    subprocess.run(
        ["git", "push", "-q", "origin", f"HEAD:{race_ref}"], cwd=old, check=True
    )
    assert (
        subprocess.run(
            ["git", "--git-dir", str(remote), "rev-parse", race_ref],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        == old_head
    )

    values = atomic_publication_values()
    branch = "automation/common-workflows-v1.40"
    state = {
        "sha": str(values["head_sha"]) if concurrent_sha is None else concurrent_sha
    }
    calls: list[list[str]] = []
    api_repo = snapshot(tmp_path / "api" / "repo")

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(args))
        if args[0] == "git":
            payload = git_payload(args)
            if payload in (
                ["remote", "get-url", "--all", "origin"],
                ["remote", "get-url", "--push", "--all", "origin"],
            ):
                return completed(args, "https://github.com/jhw7500/repo.git")
            if payload[0] == "ls-remote":
                return completed(args, f"{state['sha']}\trefs/heads/{branch}\n")
            raise AssertionError(args)
        endpoint = args[6]
        if endpoint.endswith("/refs"):
            return subprocess.CompletedProcess(
                args, 17, stdout="secret", stderr="secret"
            )
        response: dict[str, object]
        if endpoint.endswith("/blobs"):
            response = {
                "sha": values["blob_sha"],
                "url": "https://api.github.com/repos/jhw7500/repo/git/blobs/"
                + str(values["blob_sha"]),
            }
        elif endpoint.endswith("/trees"):
            response = {
                "sha": values["tree_sha"],
                "url": "https://api.github.com/repos/jhw7500/repo/git/trees/"
                + str(values["tree_sha"]),
                "tree": [],
                "truncated": False,
            }
        else:
            identity = {
                "name": "workflow-fleet",
                "email": "workflow-fleet@invalid",
                "date": "2000-01-01T00:00:00Z",
            }
            response = {
                "sha": values["head_sha"],
                "url": "https://api.github.com/repos/jhw7500/repo/git/commits/"
                + str(values["head_sha"]),
                "message": values["message"],
                "author": identity,
                "committer": identity,
                "tree": {
                    "sha": values["tree_sha"],
                    "url": "https://api.github.com/repos/jhw7500/repo/git/trees/"
                    + str(values["tree_sha"]),
                },
                "parents": [
                    {
                        "sha": values["base_sha"],
                        "url": "https://api.github.com/repos/jhw7500/repo/git/commits/"
                        + str(values["base_sha"]),
                    }
                ],
            }
        return completed(args, json.dumps(response))

    monkeypatch.setattr(workflow_fleet_git.subprocess, "run", fake_run)

    def call() -> str:
        return workflow_fleet_git.create_rollout_branch(
            api_repo,
            branch,
            commit=rollout_commit(values),
        )

    if succeeds:
        assert call() == values["head_sha"]
    else:
        with pytest.raises(workflow_fleet_git.FleetGitError, match="ref creation"):
            call()
        assert state["sha"] == SHA
    assert not any(args[0] == "git" and "push" in git_payload(args) for args in calls)


def test_atomic_branch_creation_rejects_default_branch_before_any_child(
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
        workflow_fleet_git.create_rollout_branch(
            snapshot(tmp_path / "repo"),
            "main",
            commit=rollout_commit(atomic_publication_values()),
        )


@pytest.mark.parametrize(
    "redirected_push_url",
    [
        pytest.param(
            "https://github.com/jhw7500/repo.git\nhttps://github.com/attacker/repo.git",
            id="remote-pushurl",
        ),
        pytest.param(
            "ssh://git@attacker.invalid/jhw7500/repo.git",
            id="url-pushInsteadOf",
        ),
    ],
)
def test_snapshot_operation_rejects_effective_push_url_redirects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    redirected_push_url: str,
) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        payload = git_payload(args)
        calls.append(payload)
        if payload == ["remote", "get-url", "--all", "origin"]:
            return completed(args, "https://github.com/jhw7500/repo.git")
        if payload == ["remote", "get-url", "--push", "--all", "origin"]:
            return completed(args, redirected_push_url)
        raise AssertionError("redirected origin reached a remote operation")

    monkeypatch.setattr(workflow_fleet_git.subprocess, "run", fake_run)

    with pytest.raises(workflow_fleet_git.FleetGitError):
        workflow_fleet_git.remote_branch_sha(snapshot(tmp_path / "repo"), "release")

    assert calls == [
        ["remote", "get-url", "--all", "origin"],
        ["remote", "get-url", "--push", "--all", "origin"],
    ]


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(
            lambda item: workflow_fleet_git.refetch_default(item), id="refetch"
        ),
        pytest.param(
            lambda item: workflow_fleet_git.remote_branch_sha(item, "release"),
            id="remote-branch",
        ),
        pytest.param(
            lambda item: workflow_fleet_git.create_rollout_branch(
                item,
                "automation/common-workflows-v1.40",
                commit=rollout_commit(atomic_publication_values()),
            ),
            id="atomic-create",
        ),
    ],
)
def test_every_snapshot_operation_rejects_unmarked_ordinary_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: object,
) -> None:
    repo = tmp_path / "ordinary" / "repo"
    (repo / ".git").mkdir(parents=True)
    item = raw_snapshot(repo)
    monkeypatch.setattr(
        workflow_fleet_git.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail(
            "unmarked checkout reached a child process"
        ),
    )

    with pytest.raises(workflow_fleet_git.FleetGitError):
        operation(item)  # type: ignore[operator]


def test_snapshot_operation_rejects_moved_repo_and_non_clone_git_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    safe = snapshot(tmp_path / "marked" / "repo")
    moved = tmp_path / "outside" / "repo"
    moved.parent.mkdir()
    safe.path.rename(moved)

    worktree = tmp_path / "worktree"
    worktree.mkdir()
    workspace(worktree)
    checkout = worktree / "repo"
    checkout.mkdir()
    (checkout / ".git").write_text("gitdir: ../real.git\n", encoding="utf-8")

    monkeypatch.setattr(
        workflow_fleet_git.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail(
            "invalid snapshot shape reached a child process"
        ),
    )

    with pytest.raises(workflow_fleet_git.FleetGitError):
        workflow_fleet_git.refetch_default(safe)
    with pytest.raises(workflow_fleet_git.FleetGitError):
        workflow_fleet_git.refetch_default(raw_snapshot(checkout))


def test_snapshot_operation_rejects_symlinked_workspace_component(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_workspace = tmp_path / "real"
    snapshot(real_workspace / "repo")
    alias = tmp_path / "alias"
    alias.symlink_to(real_workspace, target_is_directory=True)
    item = raw_snapshot(alias / "repo")
    monkeypatch.setattr(
        workflow_fleet_git.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail(
            "symlinked snapshot reached a child process"
        ),
    )

    with pytest.raises(workflow_fleet_git.FleetGitError):
        workflow_fleet_git.remote_branch_sha(item, "release")


def test_list_rollout_prs_queries_all_states_for_exact_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    raw = [pr_item(headRefName="automation/common-workflows-v1.40")]

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
            head_repo="jhw7500/repo",
            head_sha=PR_HEAD_SHA,
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
            "number,url,state,baseRefName,headRefName,headRefOid,headRepository,"
            "headRepositoryOwner,title,body,isDraft,mergedAt",
        ]
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("number", 0),
        ("number", True),
        ("url", "https://github.com/jhw7500/other/pull/7"),
        ("url", "https://github.com/jhw7500/repo/pull/8"),
        ("state", "UNKNOWN"),
        ("baseRefName", ""),
        ("baseRefName", "bad branch"),
        ("headRefName", "another-head"),
        ("headRefOid", "not-an-object-id"),
        ("headRepository", {"nameWithOwner": "fork-owner/repo"}),
        ("headRepositoryOwner", {"login": "fork-owner"}),
        ("title", None),
        ("body", None),
        ("isDraft", "false"),
        ("mergedAt", "2026-08-13T00:00:00Z"),
    ],
)
def test_list_rollout_prs_rejects_malformed_or_mismatched_metadata(
    monkeypatch: pytest.MonkeyPatch, field: str, value: object
) -> None:
    item = pr_item()
    item[field] = value
    monkeypatch.setattr(
        workflow_fleet_git.subprocess,
        "run",
        lambda args, **kwargs: completed(args, json.dumps([item])),
    )

    with pytest.raises(workflow_fleet_git.FleetGitError):
        workflow_fleet_git.list_rollout_prs("jhw7500", "repo", "release")


@pytest.mark.parametrize(
    ("state", "merged_at"),
    [
        ("MERGED", None),
        ("MERGED", ""),
        ("CLOSED", "2026-08-13T00:00:00Z"),
        ("OPEN", 42),
    ],
)
def test_list_rollout_prs_rejects_inconsistent_merged_state(
    monkeypatch: pytest.MonkeyPatch, state: str, merged_at: object
) -> None:
    item = pr_item(state=state, mergedAt=merged_at)
    monkeypatch.setattr(
        workflow_fleet_git.subprocess,
        "run",
        lambda args, **kwargs: completed(args, json.dumps([item])),
    )

    with pytest.raises(workflow_fleet_git.FleetGitError):
        workflow_fleet_git.list_rollout_prs("jhw7500", "repo", "release")


@pytest.mark.parametrize(
    "missing",
    [
        "isDraft",
        "mergedAt",
        "headRefOid",
        "headRepository",
        "headRepositoryOwner",
    ],
)
def test_list_rollout_prs_requires_auxiliary_metadata_fields(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    item = pr_item()
    del item[missing]
    monkeypatch.setattr(
        workflow_fleet_git.subprocess,
        "run",
        lambda args, **kwargs: completed(args, json.dumps([item])),
    )

    with pytest.raises(workflow_fleet_git.FleetGitError):
        workflow_fleet_git.list_rollout_prs("jhw7500", "repo", "release")


def test_list_rollout_prs_accepts_consistent_merged_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = pr_item(state="MERGED", mergedAt="2026-08-13T00:00:00Z")
    monkeypatch.setattr(
        workflow_fleet_git.subprocess,
        "run",
        lambda args, **kwargs: completed(args, json.dumps([item])),
    )

    assert workflow_fleet_git.list_rollout_prs("jhw7500", "repo", "release") == (
        workflow_fleet_git.PullRequest(
            number=7,
            url="https://github.com/jhw7500/repo/pull/7",
            state="MERGED",
            base="main",
            head="release",
            head_repo="jhw7500/repo",
            head_sha=PR_HEAD_SHA,
            title="Roll out workflows",
            body="Body",
        ),
    )


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
        "jhw7500",
        "repo",
        "main",
        "release",
        PR_HEAD_SHA,
        "Roll out workflows",
        body,
    )

    assert result == workflow_fleet_git.PullRequest(
        number=42,
        url="https://github.com/jhw7500/repo/pull/42",
        state="OPEN",
        base="main",
        head="release",
        head_repo="jhw7500/repo",
        head_sha=PR_HEAD_SHA,
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
        "GitBlob",
        "PullRequest",
        "RepositorySnapshot",
        "RolloutCommit",
        "clone_branch",
        "clone_default_branch",
        "create_pull_request",
        "create_rollout_branch",
        "list_rollout_prs",
        "refetch_branch",
        "refetch_default",
        "remote_branch_sha",
    }
