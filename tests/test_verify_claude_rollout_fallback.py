"""Trust-boundary and evidence tests for the read-only fallback verifier."""

from contextlib import contextmanager
from dataclasses import replace
import hashlib
import importlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def verifier():
    return importlib.import_module("scripts.verify_claude_rollout_fallback")


def git(root, *args):
    return subprocess.run(
        ["/usr/bin/git", "-c", "core.hooksPath=/dev/null", *args], cwd=root,
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    ).stdout.strip()


@pytest.fixture
def source_root(tmp_path, verifier):
    root = tmp_path / "automation"
    root.mkdir()
    for name in verifier.REQUIRED_MODULE_PATHS:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# trusted fixture\n")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "commit", "-qm", "fixture")
    return root, git(root, "rev-parse", "HEAD")


def request_for(verifier, root, output):
    return verifier.VerificationRequest(root, "v1.52", "origin", "jhw7500/gstApp",
                                        109, "a" * 40, "b" * 40, output)


@pytest.mark.parametrize("change", ["wrong_head", "dirty_tracked", "untracked_shadow",
                                     "ignored_shadow", "symlinked_module", "mode", "symlink_root"])
def test_local_source_root_rejects_mutations(verifier, source_root, change, tmp_path):
    root, commit = source_root
    module = root / verifier.REQUIRED_MODULE_PATHS[0]
    if change == "wrong_head":
        commit = "a" * 40
    elif change == "dirty_tracked":
        module.write_text("raise RuntimeError('must never execute')\n")
        git(root, "update-index", "--assume-unchanged", str(module.relative_to(root)))
    elif change == "untracked_shadow":
        (root / "yaml.py").write_text("raise RuntimeError('shadow')")
    elif change == "ignored_shadow":
        (root / ".git/info/exclude").write_text("yaml.py\n")
        (root / "yaml.py").write_text("raise RuntimeError('shadow')")
    elif change == "symlinked_module":
        target = tmp_path / "target"
        target.write_bytes(module.read_bytes())
        module.unlink()
        module.symlink_to(target)
    elif change == "mode":
        module.chmod(0o755)
    else:
        link = tmp_path / "link"
        link.symlink_to(root, target_is_directory=True)
        root = link
    with pytest.raises(verifier.VerificationError, match="^verifier_root_invalid$"):
        verifier.verify_source_root(root, commit)


def test_local_source_root_accepts_exact_checkout(verifier, source_root):
    verifier.verify_source_root(*source_root)


@pytest.mark.parametrize("change", ["bad_repository", "zero_pr", "uppercase_head",
                                     "bad_release_ref", "output_inside_root", "remote_option"])
def test_local_request_rejects_mutations(verifier, tmp_path, change):
    root = tmp_path / "automation"
    root.mkdir()
    request = request_for(verifier, root, tmp_path / "receipt.json")
    values = {"bad_repository": {"repository": "../repo"}, "zero_pr": {"pr": 0},
              "uppercase_head": {"expected_head": "A" * 40},
              "bad_release_ref": {"release_ref": "v1.52;echo"},
              "output_inside_root": {"output": root / "receipt.json"},
              "remote_option": {"remote": "--upload-pack=evil"}}
    with pytest.raises(verifier.VerificationError):
        verifier.validate_request(replace(request, **values[change]))


@pytest.mark.parametrize("change", ["existing_output", "broken_output_symlink",
                                     "symlinked_output_parent", "public_parent"])
def test_local_output_rejection_preserves_original_node(verifier, tmp_path, change):
    parent = tmp_path / "private"
    parent.mkdir(mode=0o700)
    path = parent / "receipt.json"
    if change == "existing_output":
        path.write_bytes(b"original")
    elif change == "broken_output_symlink":
        path.symlink_to(parent / "missing")
    elif change == "symlinked_output_parent":
        link = tmp_path / "link"
        link.symlink_to(parent, target_is_directory=True)
        path = link / path.name
    else:
        parent.chmod(0o755)
    before = path.lstat() if path.is_symlink() or path.exists() else None
    with pytest.raises(verifier.VerificationError):
        verifier.write_receipt(path, {"schema": 1})
    assert (path.lstat() if path.is_symlink() or path.exists() else None) == before
    if change == "existing_output":
        assert path.read_bytes() == b"original"


@pytest.mark.parametrize("mask", [0o000, 0o077, 0o777])
def test_receipt_is_private_regular_and_owned(verifier, tmp_path, mask):
    tmp_path.chmod(0o700)
    output = tmp_path / "receipt.json"
    previous = os.umask(mask)
    try:
        verifier.write_receipt(output, {"z": 2, "a": 1})
    finally:
        os.umask(previous)
    observed = output.lstat()
    assert stat.S_ISREG(observed.st_mode)
    assert observed.st_uid == os.getuid()
    assert stat.S_IMODE(observed.st_mode) == 0o600
    assert output.read_bytes() == b'{"a":1,"z":2}\n'
    assert tuple(tmp_path.iterdir()) == (output,)


def test_local_cli_exact_arguments_and_isolated_startup(verifier, tmp_path):
    script = ROOT / "scripts/verify_claude_rollout_fallback.py"
    poison = tmp_path / "sitecustomize.py"
    poison.write_text("raise RuntimeError('site code executed')")
    result = subprocess.run([sys.executable, "-I", "-S", "-B", str(script), "--help"],
                            env={**os.environ, "PYTHONPATH": str(tmp_path)},
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    for name in ("automation-root", "release-ref", "remote", "repository", "pr",
                 "expected-head", "expected-base", "output"):
        assert "--" + name in result.stdout
    assert not tuple(tmp_path.rglob("*.pyc"))
    assert set(verifier.parser()._option_string_actions) == {
        "-h", "--help", "--automation-root", "--release-ref", "--remote",
        "--repository", "--pr", "--expected-head", "--expected-base", "--output"}


def test_local_git_does_not_execute_filters_or_hooks(verifier, source_root, tmp_path):
    root, commit = source_root
    sentinel = tmp_path / "executed"
    git(root, "config", "core.fsmonitor", f"touch {sentinel}")
    git(root, "config", "filter.evil.clean", f"touch {sentinel}")
    (root / ".git/info/attributes").write_text("* filter=evil\n")
    verifier.verify_source_root(root, commit)
    assert not sentinel.exists()


def test_local_trusted_dependency_available_without_site_processing(verifier, tmp_path):
    script = ROOT / "scripts/verify_claude_rollout_fallback.py"
    code = ("import importlib.util,sys; "
            f"s=importlib.util.spec_from_file_location('verifier',{str(script)!r}); "
            "m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m); "
            "m.load_trusted_yaml(); import yaml; print(yaml.__file__)")
    (tmp_path / "yaml.py").write_text("raise RuntimeError('shadow')")
    (tmp_path / "evil.pth").write_text("import os; raise RuntimeError('pth')")
    result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code], cwd=tmp_path,
                            env={**os.environ, "PYTHONPATH": str(tmp_path),
                                 "PYTHONUSERBASE": str(tmp_path)}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert str(tmp_path) not in result.stdout


@pytest.mark.parametrize("kind", ["writable", "symlink", "user_owned"])
def test_local_dependency_directory_rejects_untrusted_paths(verifier, tmp_path, kind):
    candidate = tmp_path / "site"
    candidate.mkdir()
    if kind == "writable":
        candidate.chmod(0o777)
    elif kind == "symlink":
        link = tmp_path / "link"
        link.symlink_to(candidate)
        candidate = link
    with pytest.raises(verifier.VerificationError, match="^verifier_dependency_invalid$"):
        verifier.require_trusted_dependency_directory(candidate)


@pytest.fixture
def exact_rollout_fixture(verifier, tmp_path, monkeypatch):
    import test_rollout_workflow_fleet as existing
    from types import SimpleNamespace
    from contextlib import nullcontext
    from scripts import rollout_workflow_fleet as rollout
    from scripts import workflow_release_bundle as release
    from scripts import workflow_release_inventory as inventory

    bundle = existing.bundle.__wrapped__()
    snapshot = existing.initialized_canonical_fleet_repository(tmp_path, bundle)
    bundle = replace(bundle, commit="4" * 40)
    plan = rollout._render(snapshot, bundle, "gstApp", bootstrap=False)
    assert plan.status == "drift"
    existing.apply_render_plan(snapshot.path, plan)
    git(snapshot.path, "add", ".")
    head = existing.commit_managed_fixture(snapshot.path, "rollout")
    git(snapshot.path, "checkout", "--detach", snapshot.base_sha)
    changed = tuple(sorted(str(change.path) for change in plan.changes))
    pr = dict(number=109, html_url="https://github.com/jhw7500/gstApp/pull/109",
              state="open", merged=False, title=rollout.pr_title(bundle.ref),
              body=rollout.pr_body(bundle.ref, bundle.commit, changed),
              head={"sha": head, "ref": rollout.rollout_branch(bundle.ref),
                    "repo": {"full_name": "jhw7500/gstApp", "fork": False}},
              base={"sha": snapshot.base_sha, "ref": "main",
                    "repo": {"full_name": "jhw7500/gstApp"}})
    class Provider:
        moved = False
        requests = [pr]
        def pull_request(self, number):
            return pr
        def consumer_snapshot(self, request, modules):
            return nullcontext(snapshot)
        def rollout_prs(self, branch):
            return tuple(self.requests)
        def branch_sha(self, branch):
            return "e" * 40 if self.moved else pr["head"]["sha"]
    provider = Provider()
    request = replace(request_for(verifier, ROOT, tmp_path / "receipt.json"),
                      release_ref=bundle.ref, expected_head=head, expected_base=snapshot.base_sha)
    monkeypatch.setattr(release, "materialize_release_bundle", lambda *a, **kw: nullcontext(bundle))
    return SimpleNamespace(request=request, provider=provider, bundle=bundle, snapshot=snapshot,
                           modules=verifier.VerifiedModules(release, inventory, rollout), pr=pr,
                           plan=plan)


def test_fleet_exact_rendered_rollout_passes(verifier, exact_rollout_fixture):
    fixture = exact_rollout_fixture
    result = verifier.verify_fleet_request(fixture.request, fixture.provider, fixture.modules)
    assert result["release_commit"] == fixture.bundle.commit
    assert result["fleet"]["head_repository"] == fixture.request.repository
    assert result["fleet"]["changed_paths"] == sorted(str(c.path) for c in fixture.plan.changes)


@pytest.mark.parametrize("change", ["extra_path", "changed_blob", "executable_mode", "merge_commit",
                                     "wrong_parent", "wrong_profile", "wrong_target_pin", "moved_branch",
                                     "duplicate_pr", "fork_head", "stale_head", "changed_pr_body"])
def test_fleet_attestation_rejects_mutation(verifier, exact_rollout_fixture, change):
    fixture = exact_rollout_fixture
    request = fixture.request
    repo = fixture.snapshot.path
    if change in {"extra_path", "changed_blob", "executable_mode"}:
        git(repo, "checkout", "--detach", request.expected_head)
        path = repo / str(fixture.plan.changes[0].path)
        if change == "extra_path":
            (repo / "extra").write_text("extra")
        elif change == "changed_blob":
            path.write_bytes(path.read_bytes() + b"\n# altered\n")
        else:
            path.chmod(0o755)
        git(repo, "add", ".")
        git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--amend", "--no-edit", "-q")
        request = replace(request, expected_head=git(repo, "rev-parse", "HEAD"))
        fixture.pr["head"]["sha"] = request.expected_head
        git(repo, "checkout", "--detach", request.expected_base)
    elif change in {"merge_commit", "wrong_parent"}:
        tree = git(repo, "rev-parse", request.expected_head + "^{tree}")
        parents = ["-p", request.expected_base, "-p", request.expected_head] if change == "merge_commit" else ["-p", request.expected_head]
        head = git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit-tree", tree, *parents, "-m", "invalid")
        request = replace(request, expected_head=head)
        fixture.pr["head"]["sha"] = head
    elif change == "wrong_profile":
        request = replace(request, repository="jhw7500/unknown-profile")
    elif change == "wrong_target_pin":
        fixture.pr["body"] = fixture.pr["body"].replace(fixture.bundle.commit, "9" * 40)
    elif change == "moved_branch":
        fixture.provider.moved = True
    elif change == "duplicate_pr":
        fixture.provider.requests.append(dict(fixture.pr))
    elif change == "fork_head":
        fixture.pr["head"]["repo"] = {"full_name": "fork/gstApp", "fork": True}
    elif change == "stale_head":
        fixture.pr["head"]["sha"] = "9" * 40
    elif change == "changed_pr_body":
        fixture.pr["body"] += "altered"
    with pytest.raises(verifier.VerificationError, match="^fleet_attestation_failed$"):
        verifier.verify_fleet_request(request, fixture.provider, fixture.modules)


@pytest.fixture
def exact_evidence(verifier, exact_rollout_fixture, monkeypatch):
    import test_claude_rollout_fallback as admission
    import test_review_invocation_budget as ledger
    from copy import deepcopy
    fixture = exact_rollout_fixture
    request = fixture.request
    driver = "9" * 40
    fleet = verifier.verify_fleet_request(request, fixture.provider, fixture.modules)
    contract_request = replace(admission.parse_request_body(admission.REQUEST_BODY),
                               expected_head_sha=request.expected_head, expected_base_sha=request.expected_base,
                               release_commit=fixture.bundle.commit,
                               managed_diff_sha256=fleet["managed_diff_sha256"])
    comment = admission._request_comment(body=admission.canonical_request_body(contract_request))
    auto = deepcopy(admission.valid_bundle().original_run)
    auto.update(head_sha=request.expected_head)
    auto["pull_requests"][0].update(head={"sha": request.expected_head}, base={"sha": request.expected_base})
    auto["referenced_workflows"][0]["sha"] = fixture.bundle.commit
    job = admission._review_job()
    job["check_run_url"] = "https://api.github.com/repos/jhw7500/gstApp/check-runs/500"
    fallback_run = {"id": 34550000000, "run_attempt": 1, "name": "Claude Code",
                    "event": "issue_comment", "status": "completed", "conclusion": "success",
                    "path": ".github/workflows/claude.yml", "head_sha": request.expected_base,
                    "repository": {"full_name": request.repository}, "actor": comment["user"],
                    "created_at": "2026-09-11T03:04:07Z", "updated_at": "2026-09-11T03:06:00Z",
                    "referenced_workflows": [
                        {"path": f"jhw7500/automation/.github/workflows/claude.yml@{driver}", "sha": driver},
                        {"path": f"jhw7500/automation/.github/workflows/claude-code-review.yml@{driver}", "sha": driver}]}
    fallback_job = {"id": 501, "run_id": fallback_run["id"], "run_attempt": 1,
                    "name": "Claude / managed-rollout-review / claude-review", "status": "completed",
                    "conclusion": "success", "steps": [
                        {"number": 1, "name": "Claim Claude review budget", "status": "completed", "conclusion": "success"},
                        {"number": 2, "name": "Run Claude Code Review", "status": "completed", "conclusion": "success"},
                        {"number": 3, "name": "Finalize Claude review budget", "status": "completed", "conclusion": "success"}]}
    state = {**admission.AUTOMATIC_STATE, "run_id": fallback_run["id"], "attempt_head": request.expected_head,
             "successful_head": request.expected_head, "attempt_status": "success", "diff_mode": "full",
             "review_execution": "performed", "full_diff_sha256": fleet["managed_diff_sha256"],
             "accepted_count": 0, "filtered_count": 0, "normalized_count": 0, "filtered_max_severity": "none",
             "route": {"managed_diff_sha256": fleet["managed_diff_sha256"], "original_failed_run_id": auto["id"],
                       "release_commit": fixture.bundle.commit, "request_comment_id": 901,
                       "reviewed_base_sha": request.expected_base, "route": "default_branch_rollout_fallback"}}
    state.pop("failure_reason")
    canonical = admission._automatic_comment()
    def state_body():
        return "## Claude Code Review (latest)\n<!-- automation:claude-code-review:v3 -->\n<!-- automation-state:" + json.dumps(state, separators=(",", ":")) + " -->\n"
    canonical["body"] = state_body()
    route = ledger.budget.InvocationRoute(kind="default_branch_rollout_fallback", request_comment_id=901,
        request_nonce=contract_request.nonce, original_run_id=auto["id"], original_run_attempt=1,
        expected_base_sha=request.expected_base, release_commit=fixture.bundle.commit,
        managed_diff_sha256=fleet["managed_diff_sha256"], automatic_comment_id=887, automatic_state_sha256="e" * 64)
    invocation = replace(ledger.invocation(head=request.expected_head, full_hash=fleet["managed_diff_sha256"],
        run_id=fallback_run["id"]), caller_workflow_path=".github/workflows/claude.yml", caller_event="issue_comment",
        referenced_workflow_path=f"jhw7500/automation/.github/workflows/claude-code-review.yml@{driver}",
        referenced_workflow_sha=driver, route=route)
    ledger_state = ledger.budget.LedgerState.initial(request.repository, request.pr, "claude", invocations=(invocation,))
    budget_comment = {"id": 902, "user": canonical["user"], "body": ledger.budget.MARKERS["claude"] + "\n<!-- automation-budget-state:" + ledger.budget.serialize_ledger(ledger_state) + " -->"}
    class Provider(type(fixture.provider)):
        comments = [comment, canonical, budget_comment]
        runs = [fallback_run]
        required = set()
        annotations = [{"annotation_level": "failure", "message": "workflow_validation_mismatch"}]
        def default_caller(self):
            import base64
            content = f"jobs:\n  claude:\n    uses: jhw7500/automation/.github/workflows/claude.yml@{driver}\n"
            return {"type": "file", "path": ".github/workflows/claude.yml", "encoding": "base64",
                    "content": base64.b64encode(content.encode()).decode()}
        def issue_comments(self, number):
            return tuple(self.comments)
        def run_attempt(self, run_id, attempt):
            return auto if run_id == auto["id"] else fallback_run
        def run_jobs(self, run_id, attempt):
            return (job,) if run_id == auto["id"] else (fallback_job,)
        def check_annotations(self, check_run_id):
            return tuple(self.annotations)
        def workflow_runs(self):
            return tuple(self.runs)
        def required_checks(self, head):
            return tuple({"name": name, "status": "completed", "conclusion": "failure"} for name in self.required)
    fixture.provider = Provider()
    fixture.provider.requests = [fixture.pr]
    fixture.auto, fixture.job, fixture.fallback_run, fixture.fallback_job = auto, job, fallback_run, fallback_job
    fixture.state, fixture.canonical, fixture.state_body = state, canonical, state_body
    fixture.comment, fixture.budget_comment, fixture.ledger_state = comment, budget_comment, ledger_state
    fixture.driver = driver
    monkeypatch.setattr(verifier, "verify_source_root", lambda root, commit: None)
    monkeypatch.setattr(verifier, "load_verified_modules", lambda root: fixture.modules)
    return fixture


def test_exact_chain_emits_effective_clean_receipt(verifier, exact_evidence):
    f = exact_evidence
    receipt = verifier.verify(f.request, f.provider)
    assert set(receipt) == verifier.RECEIPT_KEYS
    assert set(receipt["automatic"]) == verifier.AUTOMATIC_KEYS
    assert set(receipt["fallback"]) == verifier.FALLBACK_KEYS
    assert set(receipt["fleet"]) == verifier.FLEET_KEYS
    assert receipt["automatic"]["reason"] == "workflow_validation_mismatch"
    assert receipt["automatic"]["review_execution"] == "not_performed"
    assert receipt["automatic"]["admitted_state_sha256"] == "e" * 64
    assert receipt["fallback"]["review_execution"] == "performed"
    assert receipt["fallback"]["budget_status"] == "finalized"
    assert receipt["fallback"]["driver_commit"] == receipt["verifier_commit"] == f.driver
    assert receipt["verifier_commit"] != receipt["release_commit"] == f.bundle.commit
    assert receipt["effective_status"] == "CLEAN"


@pytest.mark.parametrize("change", ["missing_request", "duplicate_request", "unauthorized_request",
    "app_request", "freeform_request", "stale_request", "provider_entered", "missing_annotation",
    "altered_annotation", "duplicate_annotation", "wrong_auto_run", "wrong_auto_attempt", "wrong_auto_base",
    "call_zero", "call_two", "ledger_claimed", "wrong_driver", "wrong_target", "wrong_diff", "wrong_base",
    "active_finding", "blocking_severity", "missing_state", "duplicate_state", "app_state", "app_ledger",
    "missing_run", "duplicate_run", "wrong_fallback_attempt", "provider_skipped", "duplicate_provider",
    "wrong_admitted_comment", "wrong_nonce", "required_automatic", "pending_required"])
def test_evidence_mutations_fail_closed(verifier, exact_evidence, change):
    from copy import deepcopy
    import test_review_invocation_budget as ledger
    f = exact_evidence
    if change == "missing_request": f.provider.comments.remove(f.comment)
    elif change == "duplicate_request": f.provider.comments.append(deepcopy(f.comment))
    elif change == "unauthorized_request": f.comment["author_association"] = "NONE"
    elif change == "app_request": f.comment["user"]["type"] = "Bot"
    elif change == "freeform_request": f.comment["body"] = "@claude looks good"
    elif change == "stale_request": f.comment["body"] = f.comment["body"].replace(f.request.expected_head, "f" * 40)
    elif change == "provider_entered": f.job["steps"][-1]["conclusion"] = "success"
    elif change == "missing_annotation": f.provider.annotations = []
    elif change == "altered_annotation": f.provider.annotations[0]["message"] += " extra"
    elif change == "duplicate_annotation": f.provider.annotations *= 2
    elif change == "wrong_auto_run": f.auto["id"] += 1
    elif change == "wrong_auto_attempt": f.auto["run_attempt"] = 2
    elif change == "wrong_auto_base": f.auto["pull_requests"][0]["base"]["sha"] = "f" * 40
    elif change in {"call_zero", "call_two", "ledger_claimed", "wrong_admitted_comment", "wrong_nonce"}:
        invocation = f.ledger_state.invocations[0]
        if change.startswith("call_"): invocation = replace(invocation, call_count=0 if change == "call_zero" else 2)
        elif change == "ledger_claimed": invocation = replace(invocation, status="claimed", outcome=None, call_count=0, elapsed_seconds=0, stop_reason="claimed")
        elif change == "wrong_admitted_comment": invocation = replace(invocation, route=replace(invocation.route, automatic_comment_id=999))
        else: invocation = replace(invocation, route=replace(invocation.route, request_nonce="0" * 32))
        raw = replace(f.ledger_state, invocations=(invocation,)).to_dict()
        f.budget_comment["body"] = ledger.budget.MARKERS["claude"] + "\n<!-- automation-budget-state:" + json.dumps(raw, separators=(",", ":")) + " -->"
    elif change == "wrong_driver": f.fallback_run["referenced_workflows"][-1]["sha"] = "f" * 40
    elif change in {"wrong_target", "wrong_diff", "wrong_base"}:
        key = {"wrong_target": "release_commit", "wrong_diff": "managed_diff_sha256", "wrong_base": "reviewed_base_sha"}[change]
        f.state["route"][key] = "f" * (64 if change == "wrong_diff" else 40)
    elif change == "active_finding": f.state["accepted_count"] = 1
    elif change == "blocking_severity": f.state["filtered_max_severity"] = "HIGH"
    elif change == "missing_state": f.provider.comments.remove(f.canonical)
    elif change == "duplicate_state": f.provider.comments.append(deepcopy(f.canonical))
    elif change == "app_state": f.canonical["user"] = {"login": "claude[bot]", "type": "Bot"}
    elif change == "app_ledger": f.budget_comment["user"] = {"login": "claude[bot]", "type": "Bot"}
    elif change == "missing_run": f.provider.runs = []
    elif change == "duplicate_run": f.provider.runs *= 2
    elif change == "wrong_fallback_attempt": f.fallback_run["run_attempt"] = 2
    elif change == "provider_skipped": f.fallback_job["steps"][1]["conclusion"] = "skipped"
    elif change == "duplicate_provider": f.fallback_job["steps"].append(deepcopy(f.fallback_job["steps"][1]))
    elif change == "required_automatic": f.provider.required.add("Claude Code Review / claude-review")
    elif change == "pending_required": f.provider.required_checks = lambda head: ({"name": "CI", "status": "queued", "conclusion": None},)
    f.canonical["body"] = f.state_body()
    reason = "^required_check_failed$" if change in {"required_automatic", "pending_required"} else None
    with pytest.raises(verifier.VerificationError, match=reason):
        verifier.verify(f.request, f.provider)
    assert not f.request.output.exists()


@pytest.mark.parametrize("kind,limit", [("runs", 10), ("jobs", 1), ("annotations", 10), ("comments", 10), ("checks", 10), ("contexts", 10)])
def test_remote_enumeration_full_final_page_is_overflow(verifier, monkeypatch, kind, limit):
    provider = verifier.GitHubEvidenceProvider("jhw7500/gstApp")
    calls = []
    def response(endpoint):
        calls.append(endpoint)
        return [{} for _ in range(100)]
    monkeypatch.setattr(provider, "_json", response)
    with pytest.raises(verifier.VerificationError, match="^evidence_overflow$"):
        provider._pages("repos/jhw7500/gstApp/items", pages=limit)
    assert len(calls) == limit


def test_local_corrupted_commit_object_cannot_authorize_modified_source(verifier, source_root):
    import zlib
    root, commit = source_root
    raw = subprocess.check_output(["/usr/bin/git", "cat-file", "commit", commit], cwd=root)
    forged = raw.replace(b"fixture", b"forged!")
    object_file = root / ".git/objects" / commit[:2] / commit[2:]
    object_file.chmod(0o600)
    object_file.write_bytes(zlib.compress(b"commit " + str(len(forged)).encode() + b"\0" + forged))
    with pytest.raises(verifier.VerificationError, match="^verifier_root_invalid$"):
        verifier.verify_source_root(root, commit)


def test_local_output_publish_race_preserves_winner(verifier, tmp_path, monkeypatch):
    output = tmp_path / "receipt.json"
    tmp_path.chmod(0o700)
    original = os.link
    def concurrent_link(src, dst, **kwargs):
        output.write_bytes(b"winner")
        return original(src, dst, **kwargs)
    monkeypatch.setattr(os, "link", concurrent_link)
    with pytest.raises(verifier.VerificationError, match="^receipt_path_exists$"):
        verifier.write_receipt(output, {"schema": 1})
    assert output.read_bytes() == b"winner"
    assert list(tmp_path.iterdir()) == [output]


def test_local_fsync_failure_never_publishes_receipt(verifier, tmp_path, monkeypatch):
    output = tmp_path / "receipt.json"
    tmp_path.chmod(0o700)
    def failed_sync(fd):
        raise OSError("sensitive failure")
    monkeypatch.setattr(os, "fsync", failed_sync)
    with pytest.raises(verifier.VerificationError, match="^receipt_write_failed$"):
        verifier.write_receipt(output, {"schema": 1})
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("response", [None, {}, "body", [1], {"total_count": 1, "jobs": []}])
def test_remote_malformed_pages_are_bounded(verifier, monkeypatch, response):
    provider = verifier.GitHubEvidenceProvider("jhw7500/gstApp")
    monkeypatch.setattr(provider, "_json", lambda endpoint: response)
    with pytest.raises(verifier.VerificationError, match="^evidence_invalid$"):
        provider._pages(provider.prefix + "/items", key="jobs" if type(response) is dict and "jobs" in response else None)


def test_remote_comments_are_enumerated_once_per_verification(verifier, monkeypatch):
    provider = verifier.GitHubEvidenceProvider("jhw7500/gstApp")
    calls = []
    def response(endpoint):
        calls.append(endpoint)
        return []
    monkeypatch.setattr(provider, "_json", response)
    assert provider.issue_comments(109) == provider.issue_comments(109) == ()
    assert len(calls) == 1


def test_required_legacy_failure_cannot_hide_behind_successful_check(verifier, monkeypatch):
    provider = verifier.GitHubEvidenceProvider("jhw7500/gstApp")
    provider.base_branch = "main"
    monkeypatch.setattr(provider, "_json", lambda *a, **kw: {"checks": [{"context": "CI", "app_id": None}], "contexts": ["CI"]})
    def pages(endpoint, **kwargs):
        if "/rules/" in endpoint: return ()
        if "/check-runs" in endpoint: return ({"name": "CI", "head_sha": "a" * 40, "status": "completed", "conclusion": "success"},)
        return ({"context": "CI", "state": "failure"},)
    monkeypatch.setattr(provider, "_pages", pages)
    with pytest.raises(verifier.VerificationError, match="^required_check_failed$"):
        verifier.require_required_checks_clean(provider.required_checks("a" * 40))


def test_managed_diff_digest_matches_real_review_preparation(verifier, exact_rollout_fixture):
    fixture = exact_rollout_fixture
    helper_path = ROOT / ".github/actions/prepare-review-diff/prepare_review_diff.py"
    spec = importlib.util.spec_from_file_location("prepare_diff_oracle", helper_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    expected = module.git_full_diff(fixture.request.expected_base, fixture.request.expected_head, 3, fixture.snapshot.path)
    observed = verifier.verify_fleet_request(fixture.request, fixture.provider, fixture.modules)
    assert observed["managed_diff_sha256"] == hashlib.sha256(expected).hexdigest()


def test_local_delayed_project_imports_use_verified_root_under_isolation(verifier, tmp_path):
    script = ROOT / "scripts/verify_claude_rollout_fallback.py"
    code = ("import importlib.util,sys,pathlib; "
            f"s=importlib.util.spec_from_file_location('verifier',{str(script)!r}); "
            "m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m); "
            f"result=m.load_verified_modules(pathlib.Path({str(ROOT)!r})); "
            "print(result.rollout.__file__)")
    result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code], cwd=tmp_path,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(ROOT / "scripts/rollout_workflow_fleet.py")


@pytest.mark.parametrize("mutation", ["head", "base", "body"])
def test_evidence_final_current_pr_change_rejects_receipt(verifier, exact_evidence, mutation):
    f = exact_evidence
    def checks(head):
        if mutation == "body": f.pr["body"] += "changed"
        else: f.pr[mutation]["sha"] = "f" * 40
        return ()
    f.provider.required_checks = checks
    with pytest.raises(verifier.VerificationError):
        verifier.verify(f.request, f.provider)


def test_fleet_never_executes_consumer_path_binary(verifier, exact_rollout_fixture, monkeypatch):
    fixture = exact_rollout_fixture
    poison = fixture.snapshot.path / "bin"
    poison.mkdir()
    sentinel = fixture.snapshot.path / "executed"
    executable = poison / "git"
    executable.write_text(f"#!/bin/sh\necho executed > '{sentinel}'\nexit 1\n")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(poison) + ":/usr/bin:/bin")
    verifier.verify_fleet_request(fixture.request, fixture.provider, fixture.modules)
    assert not sentinel.exists()


@pytest.mark.parametrize("record,field", [("auto", "run_attempt"), ("job", "run_attempt"),
    ("fallback_run", "run_attempt"), ("fallback_job", "run_attempt"), ("state", "quality_schema")])
def test_evidence_boolean_coordinates_are_not_integer_evidence(verifier, exact_evidence, record, field):
    fixture = exact_evidence
    getattr(fixture, record)[field] = True
    fixture.canonical["body"] = fixture.state_body()
    with pytest.raises(verifier.VerificationError):
        verifier.verify(fixture.request, fixture.provider)


def test_evidence_driver_cannot_equal_target_release(verifier, exact_evidence, monkeypatch):
    import test_review_invocation_budget as ledger
    fixture = exact_evidence
    driver = fixture.bundle.commit
    invocation = fixture.ledger_state.invocations[0]
    invocation = replace(invocation, referenced_workflow_sha=driver,
                         referenced_workflow_path=invocation.referenced_workflow_path.replace(fixture.driver, driver))
    fixture.budget_comment["body"] = ledger.budget.MARKERS["claude"] + "\n<!-- automation-budget-state:" + ledger.budget.serialize_ledger(replace(fixture.ledger_state, invocations=(invocation,))) + " -->"
    for reference in fixture.fallback_run["referenced_workflows"]:
        reference["path"] = reference["path"].replace(fixture.driver, driver)
        reference["sha"] = driver
    monkeypatch.setattr(verifier, "parse_default_caller_pin", lambda document: driver)
    with pytest.raises(verifier.VerificationError):
        verifier.verify(fixture.request, fixture.provider)


def test_local_private_parent_wrong_owner_is_rejected(verifier, tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    monkeypatch.setattr(os, "getuid", lambda: tmp_path.lstat().st_uid + 1)
    with pytest.raises(verifier.VerificationError, match="^receipt_parent_invalid$"):
        verifier.require_private_output_parent(tmp_path)


def test_remote_fixed_gh_transport_ignores_response_body_in_errors(verifier, monkeypatch):
    seen = []
    def run(args, **kwargs):
        seen.append((args, kwargs))
        return subprocess.CompletedProcess(args, 1, b"HTTP/2.0 403 Forbidden\r\n\r\nSECRET_REMOTE_BODY", b"secret error")
    monkeypatch.setattr(subprocess, "run", run)
    provider = verifier.GitHubEvidenceProvider("jhw7500/gstApp")
    with pytest.raises(verifier.VerificationError, match="^evidence_read_failed$"):
        provider.default_caller()
    assert seen[0][0][0] == "/usr/bin/gh"
    assert "--method" in seen[0][0] and "GET" in seen[0][0]
    assert not seen[0][1].get("shell")


def test_fleet_production_snapshot_materializes_only_git_object_data(verifier, exact_rollout_fixture, monkeypatch):
    fixture = exact_rollout_fixture
    provider = verifier.GitHubEvidenceProvider(fixture.request.repository)
    original = verifier._git_bytes
    def local_transport(root, *args):
        if args[0] == "fetch":
            return original(root, "fetch", "--no-tags", str(fixture.snapshot.path),
                            fixture.request.expected_base, fixture.request.expected_head)
        return original(root, *args)
    monkeypatch.setattr(verifier, "_git_bytes", local_transport)
    monkeypatch.setattr(provider, "_object", lambda endpoint: fixture.pr if "/pulls/" in endpoint else {"default_branch": "main"})
    def pages(endpoint, **kwargs):
        names = fixture.snapshot.secret_names if endpoint.endswith("/secrets") else fixture.snapshot.variable_names if endpoint.endswith("/variables") else fixture.snapshot.label_names
        return tuple({"name": name} for name in names)
    monkeypatch.setattr(provider, "_pages", pages)
    with provider.consumer_snapshot(fixture.request, fixture.modules) as observed:
        assert observed.base_sha == fixture.snapshot.base_sha
        assert observed.secret_names == fixture.snapshot.secret_names
        plan = fixture.modules.rollout.render_rollout_plan(observed, fixture.bundle, "gstApp", bootstrap=False)
        fixture.modules.rollout.validate_commit_tree(observed, fixture.request.expected_head,
                                                     fixture.request.expected_base, plan)
        assert not (observed.path / "README.md").exists()
    assert not observed.path.exists()


@pytest.mark.parametrize("method,response", [("branch", {"commit": []}), ("branch", {"commit": None}),
                                            ("pr", {"base": []}), ("pr", {"base": None})])
def test_remote_nested_type_errors_are_bounded(verifier, monkeypatch, method, response):
    provider = verifier.GitHubEvidenceProvider("jhw7500/gstApp")
    monkeypatch.setattr(provider, "_json", lambda endpoint: response)
    with pytest.raises(verifier.VerificationError, match="^evidence_invalid$"):
        provider.branch_sha("main") if method == "branch" else provider.pull_request(109)


def test_required_workflow_rule_cannot_be_silently_ignored(verifier, monkeypatch):
    provider = verifier.GitHubEvidenceProvider("jhw7500/gstApp")
    provider.base_branch = "main"
    monkeypatch.setattr(provider, "_json", lambda *a, **kw: None)
    monkeypatch.setattr(provider, "_pages", lambda endpoint, **kw: ({"type": "workflows", "parameters": {}},) if "/rules/" in endpoint else ())
    with pytest.raises(verifier.VerificationError, match="^required_check_failed$"):
        provider.required_checks("a" * 40)
