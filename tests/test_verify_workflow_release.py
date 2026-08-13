"""Tests for verifying the exact reusable-workflow release artifact."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import threading
import traceback

import pytest
import yaml

import scripts.verify_workflow_release as release_verifier
from scripts.verify_workflow_release import ReleaseVerificationError, verify_release

ROOT = Path(__file__).resolve().parents[1]

CANONICAL_REMOTE = "https://github.com/jhw7500/automation.git"
HERMETIC_LOCAL_GIT_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent/automation-workflow-release/home",
    "XDG_CONFIG_HOME": "/nonexistent/automation-workflow-release/xdg",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "/bin/false",
    "SSH_ASKPASS": "/bin/false",
    "GCM_INTERACTIVE": "Never",
}
HERMETIC_REMOTE_GIT_ENV = {
    **HERMETIC_LOCAL_GIT_ENV,
    "GIT_ALLOW_PROTOCOL": "https",
    "GIT_PROTOCOL_FROM_USER": "0",
    "GIT_CEILING_DIRECTORIES": "/",
}


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
def release_repo(tmp_path: Path) -> tuple[Path, Path, str]:
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
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "origin", "v1.40")
    return repo, remote, release_commit


def replace(path: Path, old: str, new: str, *, count: int = -1) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, count), encoding="utf-8")


def retag_bad_release(repo: Path, message: str) -> str:
    git(repo, "tag", "-d", "v1.40")
    bad_commit = commit(repo, message)
    git(repo, "tag", "-a", "v1.40", "-m", message)
    return bad_commit


def alternate_tag_object(repo: Path) -> str:
    (repo / "race-marker").write_text("alternate", encoding="utf-8")
    commit(repo, "alternate release")
    git(repo, "tag", "-a", "race-target", "-m", "race target")
    return git(repo, "rev-parse", "refs/tags/race-target")


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def test_release_verifier_git_uses_a_minimal_provider_free_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sensitive = {
        "CLAUDE_CODE_OAUTH_TOKEN",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "ZHIPU_API_KEY",
        "APP_PRIVATE_KEY",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "AWS_SECRET_ACCESS_KEY",
        "UNRELATED_OPERATOR_SECRET",
        "GIT_CONFIG_COUNT",
    }
    for key in sensitive:
        monkeypatch.setenv(key, f"sentinel-{key}")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("SSH_AUTH_SOCK", str(tmp_path / "agent.sock"))
    observed: dict[str, object] = {}

    def child(args, **kwargs):
        observed.update({"args": list(args), **kwargs})
        return subprocess.CompletedProcess(args, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(release_verifier.subprocess, "run", child)
    assert release_verifier.git(tmp_path, "status") == "ok\n"

    assert observed["args"][0] == "/usr/bin/git"
    env = observed["env"]
    assert isinstance(env, dict)
    assert env == HERMETIC_LOCAL_GIT_ENV
    assert sensitive.isdisjoint(env)
    assert not any(str(value).startswith("sentinel-") for value in env.values())
    assert "SSH_AUTH_SOCK" not in env


def test_remote_git_uses_public_https_outside_the_repository_with_no_host_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def child(args, **kwargs):
        observed.update({"args": list(args), **kwargs})
        return subprocess.CompletedProcess(args, 0, stdout="remote\n", stderr="")

    monkeypatch.setattr(release_verifier.subprocess, "run", child)

    assert release_verifier.remote_git(CANONICAL_REMOTE, "refs/tags/v1.40") == (
        "remote\n"
    )
    assert observed["args"] == [
        "/usr/bin/git",
        "ls-remote",
        "--tags",
        CANONICAL_REMOTE,
        "refs/tags/v1.40",
    ]
    assert observed["cwd"] == "/"
    assert observed["env"] == HERMETIC_REMOTE_GIT_ENV
    assert "-C" not in observed["args"]


def test_remote_git_failure_does_not_expose_child_or_provider_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = "remote-provider-sentinel"
    raw = "remote-raw-child-sentinel"
    monkeypatch.setenv("ZHIPU_API_KEY", provider)

    def child(args, **kwargs):
        assert provider not in kwargs["env"].values()
        return subprocess.CompletedProcess(
            args,
            37,
            stdout=f"stdout {provider} {raw}",
            stderr=f"stderr {provider} {raw}",
        )

    monkeypatch.setattr(release_verifier.subprocess, "run", child)

    with pytest.raises(ReleaseVerificationError) as raised:
        release_verifier.remote_git(CANONICAL_REMOTE, raw)

    rendered = "".join(
        traceback.format_exception(
            type(raised.value), raised.value, raised.value.__traceback__
        )
    )
    assert provider not in rendered
    assert raw not in rendered
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_local_release_reads_ignore_host_user_and_xdg_git_includes(
    release_repo: tuple[Path, Path, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _, release_commit = release_repo
    marker = tmp_path / "host-config-command-ran"
    provider_token = tmp_path / "provider-token"
    provider_token.write_text("provider-secret", encoding="utf-8")
    helper = tmp_path / "host-ssh-command"
    helper.write_text(
        "#!/bin/sh\n"
        f"/bin/cat {provider_token} > {marker}\n"
        "exit 91\n",
        encoding="utf-8",
    )
    helper.chmod(0o755)
    included = tmp_path / "provider.gitconfig"
    included.write_text(
        f"[core]\n\tsshCommand = {helper}\n"
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

    assert verify_release(repo, "v1.40", release_commit) == release_commit
    assert not marker.exists()


def test_remote_verification_does_not_execute_included_host_ssh_command(
    release_repo: tuple[Path, Path, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _, _ = release_repo
    marker = tmp_path / "host-ssh-command-ran"
    provider_token = tmp_path / "provider-token"
    provider_token.write_text("provider-secret", encoding="utf-8")
    helper = tmp_path / "host-ssh-command"
    helper.write_text(
        "#!/bin/sh\n"
        f"/bin/cat {provider_token} > {marker}\n"
        "exit 92\n",
        encoding="utf-8",
    )
    helper.chmod(0o755)
    included = tmp_path / "provider.gitconfig"
    included.write_text(
        f"[core]\n\tsshCommand = {helper}\n", encoding="utf-8"
    )
    home = tmp_path / "host-home"
    home.mkdir()
    (home / ".gitconfig").write_text(
        f"[include]\n\tpath = {included}\n", encoding="utf-8"
    )
    monkeypatch.setenv("HOME", str(home))
    git(repo, "remote", "set-url", "origin", "ssh://git@127.0.0.1/provider")
    tag = release_verifier.resolve_annotated_tag(repo, "v1.40")

    with pytest.raises(ReleaseVerificationError) as raised:
        release_verifier.verify_remote_tag(repo, "origin", tag)

    assert not marker.exists()
    assert "canonical public HTTPS" in str(raised.value)


def test_remote_verification_does_not_execute_local_credential_helper(
    release_repo: tuple[Path, Path, str],
    tmp_path: Path,
) -> None:
    repo, _, _ = release_repo
    requests: list[str] = []

    class Unauthorized(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
            requests.append(self.path)
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="provider"')
            self.end_headers()

        def log_message(self, _format: str, *args: object) -> None:
            del args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Unauthorized)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    marker = tmp_path / "credential-helper-ran"
    provider_token = tmp_path / "provider-token"
    provider_token.write_text("provider-secret", encoding="utf-8")
    helper = tmp_path / "credential-helper"
    helper.write_text(
        "#!/bin/sh\n"
        f"/bin/cat {provider_token} > {marker}\n"
        "if [ \"$1\" = get ]; then\n"
        "  printf 'username=provider\\npassword=secret\\n'\n"
        "fi\n",
        encoding="utf-8",
    )
    helper.chmod(0o755)
    git(
        repo,
        "remote",
        "set-url",
        "origin",
        f"http://127.0.0.1:{server.server_port}/provider",
    )
    git(repo, "config", "credential.helper", f"!{helper}")
    tag = release_verifier.resolve_annotated_tag(repo, "v1.40")

    try:
        with pytest.raises(ReleaseVerificationError) as raised:
            release_verifier.verify_remote_tag(repo, "origin", tag)
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert not marker.exists()
    assert requests == []
    assert "canonical public HTTPS" in str(raised.value)


def test_remote_url_inspection_does_not_follow_local_config_includes(
    release_repo: tuple[Path, Path, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _, release_commit = release_repo
    tag = release_verifier.resolve_annotated_tag(repo, "v1.40")
    git(repo, "remote", "set-url", "origin", CANONICAL_REMOTE)
    provider_token = tmp_path / "provider-token"
    provider_token.write_text(
        "provider-secret-is-not-valid-git-config\n", encoding="utf-8"
    )
    with (repo / ".git/config").open("a", encoding="utf-8") as config:
        config.write(f"\n[include]\n\tpath = {provider_token}\n")

    def remote_git(_url: str, *_refs: str) -> str:
        return (
            f"{tag.tag_object}\trefs/tags/v1.40\n"
            f"{release_commit}\trefs/tags/v1.40^{{}}\n"
        )

    monkeypatch.setattr(release_verifier, "remote_git", remote_git)

    release_verifier.verify_remote_tag(repo, "origin", tag)


@pytest.mark.parametrize(
    "url",
    (
        "ext::/bin/false",
        "file:///tmp/provider-token",
        "/tmp/provider-token",
        "ssh://git@github.com/jhw7500/automation.git",
        "git@github.com:jhw7500/automation.git",
        "http://github.com/jhw7500/automation.git",
        "https://github.com/other/automation.git",
        "https://provider@github.com/jhw7500/automation.git",
    ),
)
def test_remote_verification_rejects_noncanonical_url_before_transport(
    release_repo: tuple[Path, Path, str],
    monkeypatch: pytest.MonkeyPatch,
    url: str,
) -> None:
    repo, _, _ = release_repo
    git(repo, "remote", "set-url", "origin", url)
    tag = release_verifier.resolve_annotated_tag(repo, "v1.40")

    def forbidden(*_args: object, **_kwargs: object) -> str:
        pytest.fail("unsafe remote reached transport")

    monkeypatch.setattr(release_verifier, "remote_git", forbidden)

    with pytest.raises(ReleaseVerificationError, match="canonical public HTTPS"):
        release_verifier.verify_remote_tag(repo, "origin", tag)


def test_remote_verification_rejects_non_origin_name_before_transport(
    release_repo: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _, _ = release_repo
    tag = release_verifier.resolve_annotated_tag(repo, "v1.40")

    def forbidden(*_args: object, **_kwargs: object) -> str:
        pytest.fail("unsafe remote reached transport")

    monkeypatch.setattr(release_verifier, "remote_git", forbidden)

    with pytest.raises(ReleaseVerificationError, match="only origin"):
        release_verifier.verify_remote_tag(repo, "upstream", tag)


def test_release_verifier_git_failure_does_not_expose_child_or_provider_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = "release-provider-sentinel"
    raw = "release-raw-child-sentinel"
    monkeypatch.setenv("ZHIPU_API_KEY", provider)

    def child(args, **kwargs):
        assert provider not in kwargs["env"].values()
        return subprocess.CompletedProcess(
            args, 31, stdout=f"stdout {provider} {raw}", stderr=f"stderr {provider} {raw}"
        )

    monkeypatch.setattr(release_verifier.subprocess, "run", child)
    with pytest.raises(ReleaseVerificationError) as raised:
        release_verifier.git(tmp_path, "show", raw)

    rendered = "".join(
        traceback.format_exception(
            type(raised.value), raised.value, raised.value.__traceback__
        )
    )
    assert provider not in rendered
    assert raw not in rendered
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def coordinated_permission_drift(repo: Path) -> None:
    catalog_path = repo / "scripts/workflow-catalog.json"
    catalog = load_json(catalog_path)
    entry = catalog["entries"][0]
    entry["caller_jobs"][0]["permissions"]["contents"] = "write"
    write_json(catalog_path, catalog)
    replace(
        repo / "examples/baseline-workflows/.github/workflows/claude.yml",
        "      contents: read",
        "      contents: write",
        count=1,
    )


def coordinated_trigger_drift(repo: Path) -> None:
    catalog_path = repo / "scripts/workflow-catalog.json"
    catalog = load_json(catalog_path)
    catalog["entries"][0]["trigger"]["issue_comment"]["types"] = [
        "created",
        "edited",
    ]
    write_json(catalog_path, catalog)
    replace(
        repo / "examples/baseline-workflows/.github/workflows/claude.yml",
        "    types: [created]",
        "    types: [created, edited]",
        count=1,
    )


def coordinated_central_target_drift(repo: Path) -> None:
    catalog_path = repo / "scripts/workflow-catalog.json"
    catalog = load_json(catalog_path)
    catalog["entries"][0]["central_workflow"] = "claude-code-review.yml"
    write_json(catalog_path, catalog)
    replace(
        repo / "examples/baseline-workflows/.github/workflows/claude.yml",
        "/claude.yml@__AUTOMATION_COMMIT__",
        "/claude-code-review.yml@__AUTOMATION_COMMIT__",
        count=1,
    )


def coordinated_profile_drift(repo: Path) -> None:
    config_path = repo / "scripts/workflow-config.json"
    config = load_json(config_path)
    config["repos"]["gstApp"]["repo_write_auth"] = "github_token"
    config["repos"]["gstApp"]["optional_workflows"] = [
        "opencode-auto-review.yml"
    ]
    write_json(config_path, config)


def comment_only_setup_pin(path: Path) -> None:
    approved = (
        "        uses: jhw7500/automation/.github/actions/setup-gemini-auth@"
        "2254f13aab44585c78954d20749f4fb677a8c2f1"
    )
    replace(path, approved, f"        # {approved.strip()}", count=1)


def unconditional_setup_input(path: Path) -> None:
    replace(
        path,
        "fallback-token: ${{ inputs.repo_write_auth == 'github_token' && github.token || '' }}",
        "fallback-token: ${{ github.token }}",
        count=1,
    )


def extra_local_setup_resolver(path: Path) -> None:
    document = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    steps = document["jobs"]["gemini-review"]["steps"]
    steps.append(
        {
            "name": "Extra unsafe resolver",
            "uses": "./.github/actions/setup-gemini-auth",
        }
    )
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def extra_direct_app_resolver(path: Path) -> None:
    document = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    steps = document["jobs"]["gemini-review"]["steps"]
    steps.append(
        {
            "name": "Unsafe direct App token",
            "uses": "actions/create-github-app-token@main",
            "with": {
                "app-id": "${{ inputs.app_id }}",
                "private-key": "${{ secrets.APP_PRIVATE_KEY }}",
            },
        }
    )
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def downstream_github_token(path: Path) -> None:
    replace(
        path,
        "${{ steps.auth.outputs.token }}",
        "${{ github.token }}",
        count=1,
    )


def validation_not_immediately_before_resolver(path: Path) -> None:
    needle = (
        "      - name: Resolve repository-write token\n"
        "        id: auth\n"
    )
    replacement = (
        "      - name: Intervening step\n"
        "        run: echo bypass\n\n"
        + needle
    )
    replace(path, needle, replacement, count=1)


def mutate_yaml(path: Path, mutate) -> None:
    document = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    mutate(document)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def inherited_write_without_resolver(path: Path) -> None:
    def mutate(document: dict) -> None:
        document["permissions"] = {"issues": "write"}
        document["jobs"]["inherited-writer"] = {
            "runs-on": "ubuntu-latest",
            "env": {"GH_TOKEN": "${{ github.token }}"},
            "steps": [{"run": "gh issue comment 1 --body inherited"}],
        }

    mutate_yaml(path, mutate)


def explicit_write_without_resolver(path: Path) -> None:
    def mutate(document: dict) -> None:
        document["jobs"]["direct-writer"] = {
            "runs-on": "ubuntu-latest",
            "permissions": {"issues": "write"},
            "steps": [{"run": 'echo "${{ github.token }}"'}],
        }

    mutate_yaml(path, mutate)


def github_token_in_write_job_env(path: Path) -> None:
    def mutate(document: dict) -> None:
        document["jobs"]["gemini-review"]["env"] = {
            "GH_TOKEN": "${{ github.token }}"
        }

    mutate_yaml(path, mutate)


def alternate_local_token_mint_action(path: Path) -> None:
    def mutate(document: dict) -> None:
        document["jobs"]["gemini-review"]["steps"].append(
            {
                "name": "Mint another repository token",
                "uses": "./.github/actions/mint-repository-token",
            }
        )

    mutate_yaml(path, mutate)


def github_token_in_workflow_env(path: Path) -> None:
    def mutate(document: dict) -> None:
        document["env"] = {"GH_TOKEN": "${{ github.token }}"}

    mutate_yaml(path, mutate)


def ambient_caller_write_without_permissions(path: Path) -> None:
    def mutate(document: dict) -> None:
        document["jobs"]["ambient-writer"] = {
            "runs-on": "ubuntu-latest",
            "env": {"GH_TOKEN": "${{ github.token }}"},
            "steps": [{"run": "gh issue comment 1 --body ambient"}],
        }

    mutate_yaml(path, mutate)


def test_accepts_local_and_remote_annotated_tag_at_secure_commit(
    release_repo: tuple[Path, Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _, release_commit = release_repo
    tag_object = git(repo, "rev-parse", "refs/tags/v1.40")
    git(
        repo,
        "remote",
        "set-url",
        "origin",
        "https://github.com/jhw7500/automation",
    )

    def remote_git(url: str, *refs: str) -> str:
        assert url == CANONICAL_REMOTE
        assert refs == ("refs/tags/v1.40", "refs/tags/v1.40^{}")
        return (
            f"{tag_object}\trefs/tags/v1.40\n"
            f"{release_commit}\trefs/tags/v1.40^{{}}\n"
        )

    monkeypatch.setattr(release_verifier, "remote_git", remote_git)

    assert verify_release(repo, "v1.40", release_commit, remote="origin") == release_commit


def test_rejects_lightweight_release_tag(
    release_repo: tuple[Path, Path, str],
) -> None:
    repo, _, release_commit = release_repo
    git(repo, "tag", "v1.41")
    with pytest.raises(ReleaseVerificationError, match="annotated tag"):
        verify_release(repo, "v1.41", release_commit)


def test_rejects_tag_that_does_not_point_at_expected_commit(
    release_repo: tuple[Path, Path, str],
) -> None:
    repo, _, _ = release_repo
    (repo / "new").write_text("new", encoding="utf-8")
    new_commit = commit(repo, "new")
    with pytest.raises(ReleaseVerificationError, match="expected commit"):
        verify_release(repo, "v1.40", new_commit)


def test_rejects_remote_lightweight_tag_for_local_annotated_release(
    release_repo: tuple[Path, Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _, release_commit = release_repo
    git(repo, "remote", "set-url", "origin", CANONICAL_REMOTE)

    def remote_git(_url: str, *_refs: str) -> str:
        return f"{release_commit}\trefs/tags/v1.40\n"

    monkeypatch.setattr(release_verifier, "remote_git", remote_git)

    with pytest.raises(ReleaseVerificationError, match="annotated.*peeled"):
        verify_release(repo, "v1.40", release_commit, remote="origin")


def test_verify_release_rejects_one_way_tag_movement_during_content_reads(
    release_repo: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _, release_commit = release_repo
    alternate = alternate_tag_object(repo)
    original_git = release_verifier.git
    moved = False

    def racing_git(repository: Path, *args: str) -> str:
        nonlocal moved
        if not moved and args[:1] in {("show",), ("ls-tree",)}:
            git(repository, "update-ref", "refs/tags/v1.40", alternate)
            moved = True
        return original_git(repository, *args)

    monkeypatch.setattr(release_verifier, "git", racing_git)
    with pytest.raises(ReleaseVerificationError, match="changed during verification"):
        verify_release(repo, "v1.40", release_commit)


def test_verify_release_binds_every_content_read_across_aba_tag_movement(
    release_repo: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _, release_commit = release_repo
    original_tag = git(repo, "rev-parse", "refs/tags/v1.40")
    alternate = alternate_tag_object(repo)
    original_git = release_verifier.git
    movements = 0
    content_revisions: list[str] = []

    def racing_git(repository: Path, *args: str) -> str:
        nonlocal movements
        if args[:1] in {("show",), ("ls-tree",)}:
            if movements == 0:
                git(repository, "update-ref", "refs/tags/v1.40", alternate)
                movements = 1
            elif movements == 1:
                git(repository, "update-ref", "refs/tags/v1.40", original_tag)
                movements = 2
            revision = (
                args[1].split(":", 1)[0]
                if args[0] == "show"
                else args[-2]
            )
            content_revisions.append(revision)
        return original_git(repository, *args)

    monkeypatch.setattr(release_verifier, "git", racing_git)
    assert verify_release(repo, "v1.40", release_commit) == release_commit
    assert movements == 2
    assert set(content_revisions) == {release_commit}


@pytest.mark.parametrize(
    "mutate",
    [
        coordinated_permission_drift,
        coordinated_trigger_drift,
        coordinated_central_target_drift,
        coordinated_profile_drift,
    ],
    ids=("permissions", "trigger", "central-target", "profile"),
)
def test_rejects_coordinated_drift_from_approved_v140_policy(
    release_repo: tuple[Path, Path, str], mutate
) -> None:
    repo, _, _ = release_repo
    mutate(repo)
    bad_commit = retag_bad_release(repo, "coordinated policy drift")

    with pytest.raises(ReleaseVerificationError, match="approved v1.40 policy"):
        verify_release(repo, "v1.40", bad_commit)


@pytest.mark.parametrize(
    ("filename", "old", "new", "error", "count"),
    [
        (
            "opencode-auto-review.yml",
            "      # id-token 없음(의도) — 이게 있으면 액션이 OIDC 토큰을 발급받아\n"
            "      # api.opencode.ai 에서 App 토큰으로 교환할 수 있고, 그 토큰은 아래 contents: read",
            "      id-token: write\n"
            "      # api.opencode.ai 에서 App 토큰으로 교환할 수 있고, 그 토큰은 아래 contents: read",
            "permissions",
            1,
        ),
        (
            "opencode-auto-review.yml",
            "      contents: read\n      pull-requests: write\n      issues: write",
            "      contents: write\n      pull-requests: write\n      issues: write",
            "permissions",
            1,
        ),
        (
            "opencode-auto-review.yml",
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            "actions/checkout@v7",
            "checkout reference",
            1,
        ),
        (
            "opencode-auto-review.yml",
            "OPENCODE_VERSION: '1.18.17'",
            "OPENCODE_VERSION: latest",
            "approved OpenCode CLI",
            1,
        ),
        (
            "opencode-auto-review.yml",
            "3f14a4c61c7f6b0d3b6d933d1d212e64e19683eba6fa453ad98e46303afe144a",
            "0" * 64,
            "approved OpenCode CLI",
            1,
        ),
        (
            "opencode-auto-review.yml",
            "persist-credentials: true",
            "persist-credentials: false",
            "private repository fetch",
            1,
        ),
        (
            "opencode.yml",
            "persist-credentials: true",
            "persist-credentials: false",
            "opencode.yml.*private",
            1,
        ),
        (
            "opencode-auto-review.yml",
            "needs.check-enabled.outputs.safe_pr == 'true'",
            "true",
            "same-repository PR guard",
            -1,
        ),
        (
            "opencode.yml",
            "github.event.pull_request.number || github.event.issue.number",
            "github.event.issue.number",
            "opencode.yml security",
            -1,
        ),
    ],
    ids=(
        "auto-oidc-permission",
        "auto-contents-write",
        "checkout-unpinned",
        "version-drift",
        "digest-drift",
        "auto-private-fetch",
        "command-private-fetch",
        "auto-same-repo-guard",
        "command-inline-review-fallback",
    ),
)
def test_preserves_opencode_release_regressions(
    release_repo: tuple[Path, Path, str],
    filename: str,
    old: str,
    new: str,
    error: str,
    count: int,
) -> None:
    repo, _, _ = release_repo
    replace(repo / ".github/workflows" / filename, old, new, count=count)
    bad_commit = retag_bad_release(repo, f"break {filename}")
    with pytest.raises(ReleaseVerificationError, match=error):
        verify_release(repo, "v1.40", bad_commit)


def test_rejects_opencode_command_oidc_app_token_path(
    release_repo: tuple[Path, Path, str],
) -> None:
    repo, _, _ = release_repo
    path = repo / ".github/workflows/opencode.yml"
    replace(
        path,
        "    permissions:\n      contents: read",
        "    permissions:\n      id-token: write\n      contents: read",
        count=1,
    )
    replace(path, "USE_GITHUB_TOKEN: 'true'", "USE_GITHUB_TOKEN: 'false'", count=1)
    bad_commit = retag_bad_release(repo, "restore App token path")
    with pytest.raises(ReleaseVerificationError, match="opencode.yml"):
        verify_release(repo, "v1.40", bad_commit)


@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        (
            lambda path: replace(
                path, "GEMINI_API_KEY", "GOOGLE_API_KEY", count=1
            ),
            "GOOGLE_API_KEY",
        ),
        (
            lambda path: replace(
                path,
                "    permissions:\n      contents: read\n      pull-requests: write\n      issues: write",
                "    permissions:\n      contents: read\n      pull-requests: write\n      issues: write\n      id-token: write",
                count=1,
            ),
            "OIDC",
        ),
        (
            lambda path: replace(path, "inputs.app_id", "vars.APP_ID", count=1),
            "ambient App",
        ),
        (
            lambda path: replace(
                path,
                "setup-gemini-auth@2254f13aab44585c78954d20749f4fb677a8c2f1",
                "setup-gemini-auth@main",
                count=1,
            ),
            "setup-gemini-auth",
        ),
        (
            lambda path: replace(
                path,
                "      repo_write_auth:\n"
                "        description: 'Repository write authentication: github_app or github_token'\n"
                "        type: string\n"
                "        required: true\n",
                "",
                count=1,
            ),
            "repo_write_auth",
        ),
        (comment_only_setup_pin, "resolver"),
        (unconditional_setup_input, "mode-controlled inputs"),
        (extra_local_setup_resolver, "resolver"),
        (extra_direct_app_resolver, "App token"),
        (downstream_github_token, "write token"),
        (validation_not_immediately_before_resolver, "immediately preceded"),
    ],
    ids=(
        "google-api-key",
        "oidc-permission",
        "ambient-app-id",
        "unpinned-setup-auth",
        "missing-explicit-mode",
        "comment-only-pin",
        "unconditional-with",
        "extra-local-resolver",
        "extra-direct-app-resolver",
        "downstream-github-token",
        "validation-gap",
    ),
)
def test_rejects_insecure_tagged_gemini_contracts(
    release_repo: tuple[Path, Path, str], mutate, error: str
) -> None:
    repo, _, _ = release_repo
    mutate(repo / ".github/workflows/gemini-auto-review.yml")
    bad_commit = retag_bad_release(repo, "break Gemini contract")
    with pytest.raises(ReleaseVerificationError, match=error):
        verify_release(repo, "v1.40", bad_commit)


@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        (inherited_write_without_resolver, "workflow-level write permissions"),
        (explicit_write_without_resolver, "exactly one.*resolver"),
        (github_token_in_write_job_env, "github.token"),
        (alternate_local_token_mint_action, "approved action"),
        (github_token_in_workflow_env, "workflow.*github.token"),
        (ambient_caller_write_without_permissions, "explicit permissions"),
    ],
    ids=(
        "inherited-write",
        "explicit-write-run-token",
        "job-env-token",
        "alternate-mint-action",
        "workflow-env-token",
        "ambient-caller-write",
    ),
)
def test_rejects_effective_gemini_write_path_auth_bypasses(
    release_repo: tuple[Path, Path, str], mutate, error: str
) -> None:
    repo, _, _ = release_repo
    mutate(repo / ".github/workflows/gemini-auto-review.yml")
    bad_commit = retag_bad_release(repo, "add effective write bypass")

    with pytest.raises(ReleaseVerificationError, match=error):
        verify_release(repo, "v1.40", bad_commit)
