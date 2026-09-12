"""Exercise the documented publisher without network access or real tag writes."""

import ast
import json
import os
from pathlib import Path
import stat
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
