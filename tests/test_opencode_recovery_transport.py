"""Recovery transport tests: real evidence/replay/transition, only HTTP is simulated."""
import base64
import copy
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from opencode_recovery_fixtures import original_bundle, encode

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / ".github/actions/recover-opencode-review"
sys.path.insert(0, str(HELPER))


@pytest.fixture
def transport():
    import transport as module
    return module


class FakeAPI:
    def __init__(self, bundle):
        self.bundle = bundle
        self.comments = copy.deepcopy([bundle["ledger_comment"], bundle["canonical_comment"]])
        self.checks = [copy.deepcopy(bundle["original_check"])]
        self.driver = {"id": 800, "run_attempt": 1, "head_sha": "e" * 40,
            "status": "in_progress", "conclusion": None, "event": "workflow_dispatch",
            "repository": {"full_name": "example/repo"},
            "path": ".github/workflows/opencode-auto-review.yml",
            "referenced_workflows": [{"path": "jhw7500/automation/.github/workflows/"
                "opencode-recover-finalization.yml@" + "f" * 40, "sha": "f" * 40}]}
        self.calls = []
        self.patch_mode = "ok"
        self.post_mode = "ok"
        self.failed_patch = False
        self.timeline = []
        self.permissions = {}
        self.before_patch = None
        self.pr_reads = 0

    def request(self, method, path, payload=None):
        self.calls.append((method, path, copy.deepcopy(payload)))
        route = path.split("?")[0]
        b = self.bundle
        if method == "PATCH":
            self.failed_patch = True
            if self.patch_mode in ("ok", "committed_timeout"):
                self.comments[0]["body"] = payload["body"]
            elif self.patch_mode == "conflicting_timeout":
                self.comments[0]["body"] += "\nforeign-write"
            if self.patch_mode != "ok":
                raise TimeoutError("simulated uncertain PATCH")
            return copy.deepcopy(self.comments[0])
        if method == "POST":
            check = dict(payload, id=1000, app={"id": 15368, "slug": "github-actions"})
            check["output"] = dict(payload["output"], annotations_count=0,
                annotations_url="https://api.github.com/repos/example/repo/check-runs/1000/annotations")
            if self.post_mode != "unchanged_timeout":
                self.checks.append(check)
            if self.post_mode != "ok":
                raise TimeoutError("simulated uncertain POST")
            return copy.deepcopy(check)
        if route.endswith("/pulls/52"):
            self.pr_reads += 1
            if self.before_patch and self.pr_reads >= 2:
                self.before_patch(self)
            return copy.deepcopy(b["pr"])
        if route.endswith("/issues/52/comments"):
            if self.failed_patch and self.patch_mode == "unavailable_timeout":
                raise TimeoutError("simulated unavailable readback")
            return copy.deepcopy(self.comments)
        if "/issues/comments/" in route:
            if self.failed_patch and self.patch_mode == "unavailable_timeout":
                raise TimeoutError("simulated unavailable readback")
            return copy.deepcopy(next(c for c in self.comments if c["id"] == int(route.rsplit("/", 1)[1])))
        if route.endswith("/issues/52/timeline"):
            return copy.deepcopy(self.timeline)
        if "/collaborators/" in route:
            login = route.split("/collaborators/")[1].split("/")[0]
            return {"permission": self.permissions[login], "user": {"login": login}}
        if route.endswith("/actions/runs"):
            event = "workflow_dispatch" if "workflow_dispatch" in path else "pull_request"
            runs = [self.driver] if event == "workflow_dispatch" else b["history"]["runs"]
            return {"total_count": len(runs), "workflow_runs": copy.deepcopy(runs)}
        if re.search(r"/actions/runs/\d+/attempts/\d+$", route):
            run_id = int(route.split("/runs/")[1].split("/")[0])
            return copy.deepcopy(self.driver if run_id == 800 else b["original_run"])
        if route.endswith("/jobs"):
            if "/800/" in route:
                jobs = [{"id": 1201, "name": "opencode-recovery / opencode-recover-finalization", "run_id": 800,
                    "run_attempt": 1, "status": self.driver["status"], "conclusion": self.driver["conclusion"], "steps": []}]
            else:
                jobs = b["original_jobs"]
            return {"total_count": len(jobs), "jobs": copy.deepcopy(jobs)}
        if route.endswith("/check-runs"):
            head = route.split("/commits/")[1].split("/")[0]
            checks = [c for c in self.checks if c["head_sha"] == head]
            if "check_name=" in path:
                from urllib.parse import parse_qs, urlsplit
                name = parse_qs(urlsplit(path).query)["check_name"][0]
                checks = [c for c in checks if c["name"] == name]
            return {"total_count": len(checks), "check_runs": copy.deepcopy(checks)}
        if "/check-runs/" in route:
            return copy.deepcopy(next(c for c in self.checks if c["id"] == int(route.rsplit("/", 1)[1])))
        if route.endswith("/artifacts") and "/runs/" in route:
            values = [copy.deepcopy(a["metadata"]) for a in b["artifacts"].values()]
            return {"total_count": len(values), "artifacts": values}
        if "/actions/artifacts/" in route:
            return copy.deepcopy(next(a["metadata"] for a in b["artifacts"].values()
                                      if a["metadata"]["id"] == int(route.rsplit("/", 1)[1])))
        if "/contents/" in route:
            name = route.split("/contents/")[1]
            return {"type": "file", "path": name, "encoding": "base64",
                "size": len(b["workflow_source"].encode()),
                "content": base64.b64encode(b["workflow_source"].encode()).decode()}
        raise AssertionError((method, path))

    def download(self, path):
        self.calls.append(("DOWNLOAD", path, None))
        identifier = int(path.split("/artifacts/")[1].split("/")[0])
        return next(a["payload"] for a in self.bundle["artifacts"].values()
                    if a["metadata"]["id"] == identifier)

    @property
    def patches(self):
        return [c for c in self.calls if c[0] == "PATCH"]

    @property
    def posts(self):
        return [c for c in self.calls if c[0] == "POST"]


def execute(module, bundle, api):
    target = module.RecoveryTarget(**bundle["target"])
    return module.recover(target, api, bundle["workspace"], driver_run_id=800,
                          driver_run_attempt=1, central_sha="f" * 40)


@pytest.mark.parametrize("patch_mode", ["ok", "committed_timeout"])
def test_finalizes_original_once_after_normal_or_lost_patch_response(tmp_path, transport, patch_mode):
    bundle = original_bundle(tmp_path)
    api = FakeAPI(bundle)
    api.patch_mode = patch_mode
    result = execute(transport, bundle, api)
    assert result["decision"] == "finalized"
    assert result["provider_calls"] == 0
    assert len(api.patches) == len(api.posts) == 1
    state = transport.budget.parse_ledger(api.comments[0]["body"], repository="example/repo", pr=52, reviewer="opencode")
    assert len(state.invocations) == 1
    item = state.invocations[0]
    assert (item.run_id, item.run_attempt, item.call_count, item.elapsed_seconds, item.round_number) == (700, 1, 1, 45, 1)
    receipt = transport.parse_receipt(api.checks[-1])
    assert receipt["original"]["run_id"] == 700
    assert receipt["recovery"]["run_id"] == 800
    assert receipt["recovery"]["provider_calls"] == 0
    assert api.bundle["original_run"]["conclusion"] == "failure"


@pytest.mark.parametrize("mode", ["unchanged_timeout", "conflicting_timeout", "unavailable_timeout"])
def test_uncertain_patch_never_blindly_retries_or_publishes_receipt(tmp_path, transport, mode):
    b = original_bundle(tmp_path)
    api = FakeAPI(b)
    api.patch_mode = mode
    with pytest.raises(transport.RecoveryError):
        execute(transport, b, api)
    assert len(api.patches) == 1
    assert not api.posts


@pytest.mark.parametrize("mode,success", [("committed_timeout", True), ("unchanged_timeout", False)])
def test_lost_receipt_response_is_resolved_without_duplicate_post(tmp_path, transport, mode, success):
    b = original_bundle(tmp_path)
    api = FakeAPI(b)
    api.post_mode = mode
    if success:
        assert execute(transport, b, api)["decision"] == "finalized"
    else:
        with pytest.raises(transport.RecoveryError):
            execute(transport, b, api)
    assert len(api.patches) == len(api.posts) == 1


@pytest.mark.parametrize("change", ["head", "base", "canonical", "ledger", "check"])
def test_prewrite_drift_refuses_without_any_write(tmp_path, transport, change):
    b = original_bundle(tmp_path)
    api = FakeAPI(b)
    def drift(a):
        if change in ("head", "base"):
            b["pr"][change]["sha"] = "9" * 40
        elif change == "canonical":
            a.comments[1]["body"] += "\nchanged"
        elif change == "ledger":
            a.comments[0]["body"] += "\nchanged"
        else:
            a.checks[0]["conclusion"] = "failure"
    api.before_patch = drift
    with pytest.raises(transport.RecoveryError):
        execute(transport, b, api)
    assert not api.patches and not api.posts


def test_existing_completed_receipt_allows_noop_after_artifact_expiry(tmp_path, transport):
    b = original_bundle(tmp_path)
    api = FakeAPI(b)
    execute(transport, b, api)
    api.driver.update(status="completed", conclusion="success")
    for artifact in b["artifacts"].values():
        artifact["metadata"]["expired"] = True
    api.calls.clear()
    result = execute(transport, b, api)
    assert result["decision"] == "already_recovered"
    assert not api.patches and not api.posts
    assert not any("artifacts" in path for _, path, _ in api.calls)


def test_finalized_without_receipt_retries_only_receipt_publication(tmp_path, transport):
    b = original_bundle(tmp_path)
    api = FakeAPI(b)
    api.post_mode = "unchanged_timeout"
    with pytest.raises(transport.RecoveryError):
        execute(transport, b, api)
    api.calls.clear()
    api.post_mode = "ok"
    assert execute(transport, b, api)["decision"] == "finalized"
    assert not api.patches and len(api.posts) == 1


def test_current_workflow_uses_tokenless_recovery_discovery_during_replay(tmp_path, transport):
    b = original_bundle(tmp_path, ledger_schema=2)
    b["workflow_source"] = (ROOT / ".github/workflows/opencode-auto-review.yml").read_text()
    api = FakeAPI(b)
    assert execute(transport, b, api)["decision"] == "finalized"
    assert len(api.patches) == len(api.posts) == 1


def test_current_history_authenticates_completed_receipt_from_server_facts(tmp_path, transport):
    b = original_bundle(tmp_path, ledger_schema=2)
    b["workflow_source"] = (ROOT / ".github/workflows/opencode-auto-review.yml").read_text()
    api = FakeAPI(b)
    execute(transport, b, api)
    api.driver.update(status="completed", conclusion="success")
    api.calls.clear()
    target = transport.RecoveryTarget(**b['target'])
    bundle = transport.collect_evidence(target, api, b['workspace'], b['pr'], api.comments)
    history = bundle['history']
    # An already-recovered original is handled before replay by recover(). Check
    # that the read-only history nevertheless supplies every verifier dependency.
    assert transport.receipt_valid({'receipt': transport.parse_receipt(api.checks[-1]),
        'check': api.checks[-1], 'driverRun': history['attempts']['800:1']['run'],
        'driverJobs': history['attempts']['800:1']['jobs'],
        'originalRun': history['attempts']['700:1']['run'],
        'originalJobs': history['attempts']['700:1']['jobs'],
        'canonicalCheck': b['original_check'], 'comment': api.comments[1],
        'ledgerComment': api.comments[0]})
    assert not api.patches and not api.posts


@pytest.mark.parametrize('permission', ['write', 'maintain', 'admin', 'read'])
def test_fresh_dismissal_permissions_are_preserved_in_finalized_ledger(tmp_path, transport, permission):
    b = original_bundle(tmp_path)
    api = FakeAPI(b)
    api.timeline = [{"event": "commented", "id": 999, "actor": {"login": "maintainer"},
        "body": "dismiss RVW-111111111111 resolved upstream"}]
    api.permissions = {"maintainer": permission}
    execute(transport, b, api)
    state = transport.budget.parse_ledger(api.comments[0]["body"], repository="example/repo", pr=52, reviewer="opencode")
    assert bool(state.dismissed_findings) == (permission != 'read')
    assert len(api.patches) == 1


@pytest.mark.parametrize('role', ['handoff', 'claim', 'candidate'])
def test_expired_original_evidence_cannot_start_recovery(tmp_path, transport, role):
    b = original_bundle(tmp_path)
    b['artifacts'][role]['metadata']['expired'] = True
    api = FakeAPI(b)
    with pytest.raises(transport.RecoveryError):
        execute(transport, b, api)
    assert not api.patches and not api.posts


def test_driver_requires_literal_immutable_central_reference(tmp_path, transport):
    b = original_bundle(tmp_path)
    api = FakeAPI(b)
    api.driver['referenced_workflows'][0]['path'] = api.driver['referenced_workflows'][0]['path'].split('@')[0] + '@main'
    with pytest.raises(transport.RecoveryError):
        execute(transport, b, api)
    assert not api.patches and not api.posts


@pytest.mark.parametrize("field,value", [
    ("event", "pull_request"), ("id", 801), ("run_attempt", 2),
    ("head_sha", "bad"), ("path", "../wrong.yml"),
])
def test_wrong_driver_identity_cannot_write(tmp_path, transport, field, value):
    b = original_bundle(tmp_path)
    api = FakeAPI(b)
    api.driver[field] = value
    with pytest.raises(transport.RecoveryError):
        execute(transport, b, api)
    assert not api.patches and not api.posts


@pytest.mark.parametrize("value", ["01", "0", "-1", "1.0", "1e2", " 1", "1\n", "9007199254740992"])
def test_noncanonical_numeric_inputs_refused(transport, value):
    with pytest.raises(transport.RecoveryError):
        transport.positive_input(value)
