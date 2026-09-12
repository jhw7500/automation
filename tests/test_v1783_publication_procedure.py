"""Exercise the documented publisher without network access or real tag writes."""

import ast
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest


DOCUMENT = Path(__file__).resolve().parents[1] / "docs/workflows/v1.78.3-boundary-canary.md"


def publisher(tmp_path):
    document = DOCUMENT.read_text()
    source = document.split("' <<'BOUNDARY_RELEASE_PY'\n", 1)[1].split("\nBOUNDARY_RELEASE_PY", 1)[0]
    tree = ast.parse(source)
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                and node.name in {"require", "tag_pairs", "guard_before_write", "post"}]
    assert len(selected) == 4
    state = {"history_ok": True, "main_ok": True, "target_absent": True,
             "owned_clean": True, "scope_clean": True, "posts": [], "exit": 0}
    history = {"v1.78.2": ("a" * 40, "b" * 40)}
    allowed = {"docs/a.md", "tests/test_a.py"}

    def tags(name):
        if name == "v1.78.3":
            return "" if state["target_absent"] else "c" * 40 + "\trefs/tags/v1.78.3"
        direct, peeled = history[name]
        if not state["history_ok"]:
            direct = "d" * 40
        return direct + "\trefs/tags/" + name + "\n" + peeled + "\trefs/tags/" + name + "^{}"

    def git(*args, **kwargs):
        if args[:3] == ("ls-remote", "--heads", "https://github.com/jhw7500/automation.git"):
            return ("c" if state["main_ok"] else "d") * 40 + "\trefs/heads/main"
        if args[:2] == ("diff", "--raw"):
            return "" if state["owned_clean"] else ":100644 100755 modified"
        if args[:2] == ("diff", "--name-only"):
            return "\n".join(sorted(allowed if state["scope_clean"] else allowed | {"scripts/unapproved.py"}))
        raise AssertionError(args)

    def transport(args, **kwargs):
        state["posts"].append(args)
        kwargs["stdout"].write(b'{ "sha": "exact-response" }\n')
        return SimpleNamespace(returncode=state["exit"])

    env = {"os": os, "json": json, "stat": stat, "root": tmp_path,
           "runtime": {"PATH": "/usr/bin:/bin"}, "token_key": "GH_TOKEN", "token": "test-token",
           "subprocess": SimpleNamespace(run=transport, PIPE=-1), "git": git,
           "public_tags": tags, "history": history, "tag": "v1.78.3", "commit": "c" * 40,
           "main": "c" * 40 + "\trefs/heads/main", "url": "https://github.com/jhw7500/automation.git",
           "checkout": tmp_path, "baseline_commit": "b" * 40, "owned": [".github/workflows"],
           "allowed_changes": allowed}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(DOCUMENT), "exec"), env)
    return env["post"], state


@pytest.mark.parametrize("fault", ["history_ok", "main_ok", "target_absent", "owned_clean", "scope_clean"])
@pytest.mark.parametrize("after_first", [False, True])
def test_each_write_rechecks_the_live_release_boundary(tmp_path, fault, after_first):
    post, state = publisher(tmp_path)
    if after_first:
        post("repos/jhw7500/automation/git/tags", {}, "object.json")
    prior = len(state["posts"])
    state[fault] = False
    with pytest.raises(SystemExit):
        post("repos/jhw7500/automation/git/refs", {}, "ref.json")
    assert len(state["posts"]) == prior
    assert not (tmp_path / "ref.json").exists()


def test_response_bytes_are_private_even_under_permissive_umask(tmp_path):
    post, state = publisher(tmp_path)
    prior = os.umask(0)
    try:
        assert post("repos/jhw7500/automation/git/tags", {}, "object.json") == {"sha": "exact-response"}
    finally:
        os.umask(prior)
    path = tmp_path / "object.json"
    info = path.lstat()
    assert stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
    assert stat.S_IMODE(info.st_mode) == 0o600
    assert path.read_bytes() == b'{ "sha": "exact-response" }\n'
    assert len(state["posts"]) == 1


def test_uncertain_post_keeps_evidence_and_cannot_repeat(tmp_path):
    post, state = publisher(tmp_path)
    state["exit"] = 1
    with pytest.raises(SystemExit):
        post("repos/jhw7500/automation/git/tags", {}, "object.json")
    saved = (tmp_path / "object.json").read_bytes()
    with pytest.raises(FileExistsError):
        post("repos/jhw7500/automation/git/tags", {}, "object.json")
    assert len(state["posts"]) == 1
    assert (tmp_path / "object.json").read_bytes() == saved


def test_response_symlink_is_never_followed(tmp_path):
    post, state = publisher(tmp_path)
    target = tmp_path / "retained"
    target.write_text("keep")
    (tmp_path / "object.json").symlink_to(target)
    with pytest.raises(FileExistsError):
        post("repos/jhw7500/automation/git/tags", {}, "object.json")
    assert state["posts"] == [] and target.read_text() == "keep"


def canary_publisher():
    document = DOCUMENT.read_text()
    source = document.split("<!-- approved-canary-python -->\n```python\n", 1)[1].split("\n```", 1)[0]
    namespace = {}
    exec(compile(source, str(DOCUMENT), "exec"), namespace)
    return namespace["publish_approved_canary"]


def test_documented_preparation_interpreter_loads_dependencies_without_consumer_imports(tmp_path):
    launch = DOCUMENT.read_text().split("controller process launched with\n`", 1)[1].split("`", 1)[0]
    command = shlex.split(launch)
    assert command[:2] == ["rtk", "python3"]
    (tmp_path / "yaml.py").write_text("raise RuntimeError('consumer dependency imported')\n")
    source = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "from scripts import rollout_workflow_fleet; "
        "assert callable(rollout_workflow_fleet.construct_rollout_commit); "
        "print('fleet dependency import: PASS')"
    )
    result = subprocess.run(
        [sys.executable, *command[2:], "-c", source, str(DOCUMENT.parents[2])],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "fleet dependency import: PASS\n"


def canary_fixture():
    approved = {"repository": "jhw7500/wlan-package", "base_ref": "master", "base_sha": "b" * 40,
                "head_sha": "c" * 40, "tree_sha": "d" * 40,
                "branch": "automation/common-workflows-v1.78.3", "title": "canonical title", "body": "canonical body"}
    state = {"base": approved["base_sha"], "head": None, "writes": [], "move_after_blob": False}
    fleet = SimpleNamespace()

    def post(repo, section, payload):
        state["writes"].append(section)
        if section == "blobs" and state["move_after_blob"]:
            state["base"] = "e" * 40
        if section == "refs":
            state["head"] = payload["sha"]
        return {}

    def branch(snapshot, name, *, commit):
        fleet._github_post("wlan-package", "blobs", {})
        fleet._github_post("wlan-package", "refs", {"ref": "refs/heads/" + name, "sha": commit.head_sha})
        return commit.head_sha

    def pr(*args):
        state["writes"].append("pr")
        return args

    fleet._github_post, fleet.create_rollout_branch, fleet.create_pull_request = post, branch, pr
    snapshot = SimpleNamespace(path=Path("/tmp/wlan-package"), default_branch="master", base_branch="master", base_sha="b" * 40)
    candidate = SimpleNamespace(head_sha="c" * 40, tree_sha="d" * 40, base_sha="b" * 40)
    observe = lambda: {"repository": "jhw7500/wlan-package", "base_ref": "master", "base_sha": state["base"], "head_sha": state["head"]}

    def action():
        fleet.create_rollout_branch(snapshot, approved["branch"], commit=candidate)
        return fleet.create_pull_request("jhw7500", "wlan-package", "master", approved["branch"], candidate.head_sha, approved["title"], approved["body"])

    return approved, state, fleet, snapshot, candidate, observe, action


@pytest.mark.parametrize("field", ["base_sha", "head_sha", "tree_sha"])
def test_unreviewed_recomputed_candidate_never_reaches_a_remote_writer(field):
    publish = canary_publisher()
    approved, state, fleet, snapshot, candidate, observe, action = canary_fixture()
    setattr(candidate, field, "f" * 40)
    with pytest.raises(ValueError):
        publish(approved, fleet, observe, action)
    assert state["writes"] == []


def test_advanced_live_base_causes_zero_remote_writes():
    publish = canary_publisher()
    approved, state, fleet, snapshot, candidate, observe, action = canary_fixture()
    state["base"] = "e" * 40
    with pytest.raises(ValueError):
        publish(approved, fleet, observe, action)
    assert state["writes"] == []


def test_mid_publication_base_drift_stops_before_ref_and_pr():
    publish = canary_publisher()
    approved, state, fleet, snapshot, candidate, observe, action = canary_fixture()
    state["move_after_blob"] = True
    with pytest.raises(ValueError):
        publish(approved, fleet, observe, action)
    assert state["writes"] == ["blobs"] and state["head"] is None


@pytest.mark.parametrize("wrong", ["head", "body"])
def test_unapproved_existing_branch_or_pr_body_causes_zero_writes(wrong):
    publish = canary_publisher()
    approved, state, fleet, snapshot, candidate, observe, action = canary_fixture()
    state["head"] = approved["head_sha"] if wrong == "body" else "e" * 40
    def create():
        return fleet.create_pull_request("jhw7500", "wlan-package", "master", approved["branch"], approved["head_sha"], approved["title"], "changed" if wrong == "body" else approved["body"])
    with pytest.raises(ValueError):
        publish(approved, fleet, observe, create)
    assert state["writes"] == []


def test_approved_candidate_publishes_exact_metadata_and_restores_adapters():
    publish = canary_publisher()
    approved, state, fleet, snapshot, candidate, observe, action = canary_fixture()
    original = fleet._github_post, fleet.create_rollout_branch, fleet.create_pull_request
    result = publish(approved, fleet, observe, action)
    assert state["writes"] == ["blobs", "refs", "pr"]
    assert result == ("jhw7500", "wlan-package", "master", approved["branch"], approved["head_sha"], approved["title"], approved["body"])
    assert (fleet._github_post, fleet.create_rollout_branch, fleet.create_pull_request) == original


@pytest.mark.parametrize("base_moves", [False, True])
def test_documented_candidate_survives_review_then_uses_released_adapters(tmp_path, monkeypatch, base_moves):
    """Execute both snippets using real render/Git/adapters; fake only external I/O."""
    root = DOCUMENT.parents[2]
    monkeypatch.syspath_prepend(str(root))
    from scripts import rollout_workflow_fleet as rollout
    from scripts import workflow_fleet_git as fleet
    from scripts import workflow_release_bundle as bundles
    from scripts.prepare_workflow_rollout import apply_render_plan
    from scripts.workflow_catalog import load_catalog, load_fleet_config

    catalog = load_catalog(root)
    config = load_fleet_config(root, catalog)
    repo = tmp_path / "wlan-package"
    (repo / ".github/workflows").mkdir(parents=True)
    (repo / ".github/workflow-config.yml").write_text("automation_ref: v1.78.1\nreview:\n  auto: false\n")
    secrets = frozenset({"CLAUDE_CODE_OAUTH_TOKEN", "GEMINI_API_KEY", "ZHIPU_API_KEY", "APP_PRIVATE_KEY"})
    variables = frozenset({"APP_ID"})
    labels = frozenset({"review:request", "review:skip", "review-budget-override"})
    canonical = root / config.canonical_dir
    initial = rollout.render_repository(repo, canonical, catalog, config.profiles[repo.name],
                                        "v1.78.2", "a" * 40, set(secrets), set(variables), label_names=labels)
    assert initial.status == "drift"
    apply_render_plan(repo, initial)
    for args in (["init", "-q", "-b", "master"], ["config", "user.name", "fixture"],
                 ["config", "user.email", "fixture@invalid"], ["add", "--all"],
                 ["commit", "-q", "-m", "base"], ["remote", "add", "origin", "https://github.com/jhw7500/wlan-package.git"]):
        subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=repo, check=True)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo).decode().strip()
    snapshot = fleet.RepositorySnapshot(repo, "master", base, secrets, variables, "master", labels)
    bundle = bundles.ReleaseBundle(root, "v1.78.3", "f" * 40, catalog, config, canonical)
    state = {"head": None, "base": base, "writes": []}
    monkeypatch.setattr(fleet, "clone_default_branch", lambda *args: snapshot)
    monkeypatch.setattr(fleet, "refetch_default", lambda *args: base)
    monkeypatch.setattr(fleet, "remote_branch_sha", lambda *args: state["head"])
    monkeypatch.setattr(fleet, "list_rollout_prs", lambda *args: ())
    monkeypatch.setattr(bundles, "materialize_release_bundle", lambda *args, **kwargs: nullcontext(bundle))
    monkeypatch.setattr(rollout, "_run_actionlint", lambda *args: None)
    blocks = re.findall(r"```python\n(.*?)\n```", DOCUMENT.read_text(), re.S)
    assert len(blocks) == 3
    namespace = {"target": root, "workspace": tmp_path, "publish_approved_canary": canary_publisher()}
    exec(compile(blocks[1], str(DOCUMENT), "exec"), namespace)
    raw = (tmp_path / "approved-candidate.json").read_bytes()
    namespace["approved_digest"] = hashlib.sha256(raw).hexdigest()
    approved = json.loads(raw)["approved"]
    assert approved["base_sha"] == base and approved["repository"] == "jhw7500/wlan-package"
    if base_moves:
        state["base"] = "e" * 40

    original_output, original_run = subprocess.check_output, fleet.run
    def output(args, **kwargs):
        if args[0] != "gh":
            return original_output(args, **kwargs)
        endpoint = args[-1]
        if endpoint.endswith("/wlan-package"):
            result = {"full_name": "jhw7500/wlan-package", "default_branch": "master"}
        elif endpoint.endswith("/git/ref/heads/master"):
            result = {"ref": "refs/heads/master", "object": {"sha": state["base"], "type": "commit"}}
        else:
            assert endpoint.endswith("/git/matching-refs/heads/" + approved["branch"])
            result = [] if state["head"] is None else [{"ref": "refs/heads/" + approved["branch"], "object": {"sha": state["head"], "type": "commit"}}]
        return json.dumps(result).encode()

    def post(repo, section, payload):
        import base64
        state["writes"].append(section)
        api = "https://api.github.com/repos/jhw7500/wlan-package/git/"
        if section == "blobs":
            content = base64.b64decode(payload["content"])
            sha = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
            return {"sha": sha, "url": api + "blobs/" + sha}
        if section == "trees":
            return {"sha": approved["tree_sha"], "url": api + "trees/" + approved["tree_sha"], "tree": [], "truncated": False}
        if section == "commits":
            return {"sha": approved["head_sha"], "url": api + "commits/" + approved["head_sha"],
                    "message": payload["message"].rstrip("\n"), "author": payload["author"], "committer": payload["committer"],
                    "tree": {"sha": payload["tree"], "url": api + "trees/" + payload["tree"]},
                    "parents": [{"sha": base, "url": api + "commits/" + base}]}
        assert section == "refs"
        state["head"] = payload["sha"]
        return {"ref": payload["ref"], "url": api + "refs/heads/" + approved["branch"],
                "object": {"sha": state["head"], "type": "commit", "url": api + "commits/" + state["head"]}}

    def run(args, **kwargs):
        if args[:3] == ["gh", "pr", "create"]:
            state["writes"].append("pr")
            body = Path(args[args.index("--body-file") + 1])
            assert body.read_text() == approved["body"] and stat.S_IMODE(body.stat().st_mode) == 0o600
            return "https://github.com/jhw7500/wlan-package/pull/999"
        return original_run(args, **kwargs)

    monkeypatch.setattr(subprocess, "check_output", output)
    monkeypatch.setattr(fleet, "_github_post", post)
    monkeypatch.setattr(fleet, "run", run)
    if base_moves:
        with pytest.raises(ValueError):
            exec(compile(blocks[2], str(DOCUMENT), "exec"), namespace)
        assert state["writes"] == [] and not (tmp_path / "rollout-publish.json").exists()
    else:
        exec(compile(blocks[2], str(DOCUMENT), "exec"), namespace)
        assert state["writes"] == ["blobs"] * 11 + ["trees", "commits", "refs", "pr"]
        result = json.loads((tmp_path / "rollout-publish.json").read_bytes())[0]
        assert {key: result[key] for key in approved} == approved
    assert (tmp_path / "approved-candidate.json").read_bytes() == raw
