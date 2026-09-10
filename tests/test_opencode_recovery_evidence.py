"""Recovery refuses untrusted artifacts before any file extraction or mutation."""
import hashlib
import importlib.util
import io
import json
import stat
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / ".github/actions/recover-opencode-review/evidence.py"


def evidence_module():
    if not MODULE.is_file():
        pytest.fail("Recovery evidence validator is not implemented")
    spec = importlib.util.spec_from_file_location("opencode_recovery_evidence", MODULE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_current_workflow_replays_the_same_original_success(tmp_path):
    module = evidence_module()
    bundle = original_bundle(tmp_path)
    bundle["workflow_source"] = (ROOT / ".github/workflows/opencode-auto-review.yml").read_text()
    result = module.validate_evidence(module.RecoveryTarget(**bundle["target"]), bundle, bundle["workspace"])
    assert result.request.call_count == 1
    assert result.request.elapsed_seconds == 45
    assert result.canonical_body == bundle["canonical_comment"]["body"]


def archive(entries=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as stream:
        for name, data in entries or [("candidate.json", b'{"schema":2}'), ("review.md", b"review")]:
            stream.writestr(name, data)
    payload = output.getvalue()
    metadata = {
        "id": 101, "name": "opencode-candidate-42-1", "expired": False,
        "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
        "size_in_bytes": len(payload), "workflow_run": {"id": 42},
    }
    return metadata, payload


def test_original_artifact_bytes_are_returned_without_extracting(tmp_path):
    module = evidence_module()
    metadata, payload = archive()
    assert module.read_artifact(metadata, payload, "opencode-candidate-42-1", 42) == {
        "candidate.json": b'{"schema":2}', "review.md": b"review",
    }
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("change", [
    {"id": True}, {"id": 0}, {"name": "opencode-candidate-42-2"},
    {"expired": True}, {"expired": None}, {"workflow_run": {"id": 43}},
    {"digest": "sha256:" + "0" * 64}, {"digest": None},
    {"size_in_bytes": 1},
])
def test_artifact_identity_or_expiry_refuses(change):
    module = evidence_module()
    metadata, payload = archive()
    metadata.update(change)
    with pytest.raises(module.RecoveryError):
        module.read_artifact(metadata, payload, "opencode-candidate-42-1", 42)


@pytest.mark.parametrize("name", ["../review.md", "/review.md", "a/review.md", "a\\review.md", ".hidden", ""])
def test_unsafe_artifact_paths_refuse(name):
    module = evidence_module()
    # ZIP itself cannot encode an empty filename; empty inventory is checked separately.
    if not name:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w"):
            pass
        metadata, _ = archive()
        payload = output.getvalue()
        metadata.update(size_in_bytes=len(payload), digest="sha256:" + hashlib.sha256(payload).hexdigest())
    else:
        metadata, payload = archive([(name, b"bad")])
    with pytest.raises(module.RecoveryError):
        module.read_artifact(metadata, payload, "opencode-candidate-42-1", 42)


def test_duplicate_zip_entry_refuses():
    module = evidence_module()
    with pytest.warns(UserWarning, match="Duplicate name"):
        metadata, payload = archive([("review.md", b"first"), ("review.md", b"second")])
    with pytest.raises(module.RecoveryError):
        module.read_artifact(metadata, payload, "opencode-candidate-42-1", 42)


def test_zip_symlink_refuses():
    module = evidence_module()
    entry = zipfile.ZipInfo("review.md")
    entry.create_system = 3
    entry.external_attr = (stat.S_IFLNK | 0o777) << 16
    metadata, payload = archive([(entry, b"/etc/passwd")])
    with pytest.raises(module.RecoveryError):
        module.read_artifact(metadata, payload, "opencode-candidate-42-1", 42)


def test_expanding_zip_refuses_before_reading_entries(monkeypatch):
    module = evidence_module()
    metadata, payload = archive([("review.md", b"x" * 4_000_001)])
    def unexpected_read(*args, **kwargs):
        pytest.fail("oversized ZIP entry was read")
    monkeypatch.setattr(zipfile.ZipFile, "read", unexpected_read)
    with pytest.raises(module.RecoveryError):
        module.read_artifact(metadata, payload, "opencode-candidate-42-1", 42)


@pytest.mark.parametrize("raw", [b'{"schema":1,"schema":2}', b'{"n":NaN}', b'{"n":Infinity}', b"\xff"])
def test_json_evidence_rejects_ambiguous_or_nonstandard_values(raw):
    module = evidence_module()
    with pytest.raises(module.RecoveryError):
        module.strict_json(raw)

from opencode_recovery_fixtures import original_bundle


def test_verified_original_success_returns_original_metrics(tmp_path):
    module = evidence_module()
    bundle = original_bundle(tmp_path)
    assert hasattr(module, "validate_evidence"), "Original evidence validation is not implemented"
    target = module.RecoveryTarget(**bundle["target"])
    validated = module.validate_evidence(target, bundle, bundle["workspace"])
    assert validated.request.run_id == 700
    assert validated.request.run_attempt == 1
    assert validated.request.call_count == 1
    assert validated.request.elapsed_seconds == 45
    assert validated.request.authenticated_review.success is True
    assert validated.request.remaining_finding_ids == ()
    assert validated.canonical_body == bundle["canonical_comment"]["body"]

def test_original_workflow_head_can_differ_from_reviewed_head(tmp_path):
    module = evidence_module()
    bundle = original_bundle(tmp_path)
    workflow_head = "e" * 40
    bundle["original_run"]["head_sha"] = workflow_head
    bundle["original_check"]["head_sha"] = workflow_head
    raw = bundle["original_check"]["output"]["text"]
    attestation = json.loads(raw[len("<!-- automation-attestation:"):-len(" -->")])
    attestation["workflow_head"] = workflow_head
    bundle["original_check"]["output"]["text"] = "<!-- automation-attestation:" + json.dumps(attestation) + " -->"
    _rewrite_payload(bundle, "handoff", "handoff.json", lambda h: h.update(workflow_head=workflow_head))
    validated = module.validate_evidence(module.RecoveryTarget(**bundle["target"]), bundle, bundle["workspace"])
    assert validated.request.head_sha == bundle["target"]["head_sha"]
    assert validated.attestation["workflow_head"] == workflow_head


@pytest.mark.parametrize("field", ["schema", "run_attempt", "prepared_run_attempt"])
def test_attestation_boolean_is_not_an_integer_identity(tmp_path, field):
    module = evidence_module()
    bundle = original_bundle(tmp_path)
    check = bundle["original_check"]
    raw = check["output"]["text"]
    attestation = json.loads(raw[len("<!-- automation-attestation:"):-len(" -->")])
    attestation[field] = True
    check["output"]["text"] = "<!-- automation-attestation:" + json.dumps(attestation) + " -->"
    with pytest.raises(module.RecoveryError):
        module.validate_evidence(module.RecoveryTarget(**bundle["target"]), bundle, bundle["workspace"])

def _rewrite_payload(bundle, role, filename, mutate):
    from opencode_recovery_fixtures import artifact, encode
    item = bundle["artifacts"][role]
    with zipfile.ZipFile(io.BytesIO(item["payload"])) as stream:
        files = {name: stream.read(name) for name in stream.namelist()}
    content = json.loads(files[filename])
    mutate(content)
    files[filename] = encode(content)
    bundle["artifacts"][role] = artifact(item["metadata"]["id"], item["metadata"]["name"], files)


@pytest.mark.parametrize('changes', [
    {'caller_workflow_path': '.github/workflows/other.yml'},
    {'caller_event': 'workflow_dispatch'},
    {'referenced_workflow_ref': 'refs/tags/other'},
    {'referenced_workflow_sha': 'e' * 40,
     'referenced_workflow_path': 'jhw7500/automation/.github/workflows/opencode-auto-review.yml@' + 'e' * 40},
])
def test_claim_provenance_must_match_original_publication_even_with_sealed_hashes(tmp_path, changes):
    from dataclasses import replace
    from opencode_recovery_fixtures import artifact, budget, digest, encode
    module = evidence_module()
    bundle = original_bundle(tmp_path)
    original = bundle['claim_state']
    changed = replace(original, invocations=(replace(original.invocations[0], **changes),))
    checkpoint = budget.render_checkpoint(changed)
    with zipfile.ZipFile(io.BytesIO(bundle['artifacts']['handoff']['payload'])) as stream:
        files = {name: stream.read(name) for name in stream.namelist()}
    handoff = json.loads(files['handoff.json'])
    files['review-budget-claim.json'] = checkpoint
    handoff['files']['review-budget-claim.json'] = digest(checkpoint)
    handoff['budget_checkpoint_sha256'] = digest(checkpoint)
    files['handoff.json'] = encode(handoff)
    bundle['artifacts']['handoff'] = artifact(101, 'opencode-handoff-700-1', files)
    bundle['artifacts']['claim'] = artifact(102, 'opencode-review-budget-claim-700-1',
        {'opencode-review-budget-claim.json': checkpoint})
    _rewrite_payload(bundle, 'candidate', 'candidate.json',
        lambda c: c.update(claim_checkpoint_sha256=digest(checkpoint)))
    with pytest.raises(module.RecoveryError, match='original_claim_provenance_invalid'):
        module.validate_evidence(module.RecoveryTarget(**bundle['target']), bundle, bundle['workspace'])


@pytest.mark.parametrize('job_index', [0, 1, 2])
@pytest.mark.parametrize('change', ['reverse', 'numbers', 'duplicate', 'boolean'])
def test_original_step_order_is_part_of_finalizer_failure_proof(tmp_path, job_index, change):
    module = evidence_module()
    bundle = original_bundle(tmp_path)
    steps = bundle['original_jobs'][job_index]['steps']
    if change == 'reverse':
        steps.reverse()
    elif change == 'numbers':
        for index, step in enumerate(steps):
            step['number'] = len(steps) - index
    elif change == 'duplicate':
        steps[1]['number'] = steps[0]['number']
    else:
        steps[0]['number'] = True
    with pytest.raises(module.RecoveryError, match='original_step_order_invalid'):
        module.validate_evidence(module.RecoveryTarget(**bundle['target']), bundle, bundle['workspace'])


@pytest.mark.parametrize("field,value", [
    ("call_count", 3), ("call_count", True), ("elapsed_seconds", 601),
    ("run_attempt", 2), ("head_sha", "f" * 40), ("full_diff_sha256", "f" * 64),
    ("claim_checkpoint_sha256", "e" * 64), ("outcome", "failure"),
    ("model_route", ["unapproved-model"]), ("failure_reason", "provider_failed"),
    ("review_sha256", "e" * 64), ("candidate_validations", []),
])
def test_candidate_claim_or_usage_drift_refuses_even_with_valid_zip_digest(tmp_path, field, value):
    module = evidence_module()
    bundle = original_bundle(tmp_path)
    _rewrite_payload(bundle, "candidate", "candidate.json", lambda c: c.update({field: value}))
    with pytest.raises(module.RecoveryError):
        module.validate_evidence(module.RecoveryTarget(**bundle["target"]), bundle, bundle["workspace"])


def test_unsupported_original_code_is_not_executed(tmp_path, monkeypatch):
    module = evidence_module()
    bundle = original_bundle(tmp_path)
    bundle["workflow_source"] = bundle["workflow_source"].replace(
        "const fs = require('fs');", "throw new Error('unapproved');")
    subprocess_run = module.subprocess.run
    def refuse_node(argv, *args, **kwargs):
        assert not str(argv[0]).endswith("/node"), "unapproved code reached replay"
        return subprocess_run(argv, *args, **kwargs)
    monkeypatch.setattr(module.subprocess, "run", refuse_node)
    with pytest.raises(module.RecoveryError, match="original_replay_unsupported"):
        module.validate_evidence(module.RecoveryTarget(**bundle["target"]), bundle, bundle["workspace"])


def test_forged_canonical_success_refuses_when_candidate_replays_differently(tmp_path):
    module = evidence_module()
    bundle = original_bundle(tmp_path)
    comment = bundle["canonical_comment"]
    comment["body"] = comment["body"].replace("### New findings\nNone", "### New findings\nForged clean result")
    check = bundle["original_check"]
    raw = check["output"]["text"]
    attestation = json.loads(raw[len("<!-- automation-attestation:"):-len(" -->")])
    attestation["body_sha256"] = hashlib.sha256(comment["body"].encode()).hexdigest()
    check["output"]["text"] = "<!-- automation-attestation:" + json.dumps(attestation) + " -->"
    with pytest.raises(module.RecoveryError, match="canonical_replay_mismatch"):
        module.validate_evidence(module.RecoveryTarget(**bundle["target"]), bundle, bundle["workspace"])


def test_changed_local_full_diff_refuses(tmp_path):
    module = evidence_module()
    bundle = original_bundle(tmp_path)
    from opencode_recovery_fixtures import git
    (bundle["workspace"] / "app.py").write_text("ready = 'different'\n")
    git(bundle["workspace"], "add", "app.py")
    git(bundle["workspace"], "commit", "-qm", "unreviewed")
    with pytest.raises(module.RecoveryError, match="workspace_head_invalid"):
        module.validate_evidence(module.RecoveryTarget(**bundle["target"]), bundle, bundle["workspace"])


def test_noncanonical_claim_json_is_a_bounded_refusal(tmp_path):
    module = evidence_module()
    bundle = original_bundle(tmp_path)
    from opencode_recovery_fixtures import artifact, encode, digest
    with zipfile.ZipFile(io.BytesIO(bundle["artifacts"]["handoff"]["payload"])) as stream:
        files = {name: stream.read(name) for name in stream.namelist()}
    invalid = files["review-budget-claim.json"] + b" "
    files["review-budget-claim.json"] = invalid
    handoff = json.loads(files["handoff.json"])
    handoff["files"]["review-budget-claim.json"] = digest(invalid)
    handoff["budget_checkpoint_sha256"] = digest(invalid)
    files["handoff.json"] = encode(handoff)
    bundle["artifacts"]["handoff"] = artifact(101, "opencode-handoff-700-1", files)
    bundle["artifacts"]["claim"] = artifact(102, "opencode-review-budget-claim-700-1",
                                           {"opencode-review-budget-claim.json": invalid})
    with pytest.raises(module.RecoveryError):
        module.validate_evidence(module.RecoveryTarget(**bundle["target"]), bundle, bundle["workspace"])



@pytest.mark.parametrize("field,value", [
    ("run_attempt", 2), ("head_sha", "f" * 40), ("base_sha", "e" * 40),
    ("repository", "foreign/repo"), ("pr", True),
])
def test_wrong_original_target_refuses(tmp_path, field, value):
    module = evidence_module()
    bundle = original_bundle(tmp_path)
    assert hasattr(module, "validate_evidence"), "Original evidence validation is not implemented"
    bundle["target"][field] = value
    with pytest.raises(module.RecoveryError):
        target = module.RecoveryTarget(**bundle["target"])
        module.validate_evidence(target, bundle, bundle["workspace"])


@pytest.mark.parametrize("case", ["run_cancelled", "job_cancelled", "publication_failed",
    "outcome_failed", "finalizer_succeeded", "duplicate_job", "foreign_check",
    "changed_comment", "wrong_base_binding", "missing_candidate"])
def test_unverified_success_refuses(tmp_path, case):
    module = evidence_module()
    bundle = original_bundle(tmp_path)
    assert hasattr(module, "validate_evidence"), "Original evidence validation is not implemented"
    if case == "run_cancelled":
        bundle["original_run"]["conclusion"] = "cancelled"
    elif case == "job_cancelled":
        bundle["original_jobs"][1]["conclusion"] = "cancelled"
    elif case == "publication_failed":
        bundle["original_jobs"][2]["steps"][0]["conclusion"] = "failure"
    elif case == "outcome_failed":
        bundle["original_jobs"][2]["steps"][1]["conclusion"] = "failure"
    elif case == "finalizer_succeeded":
        bundle["original_jobs"][2]["steps"][2]["conclusion"] = "success"
    elif case == "duplicate_job":
        bundle["original_jobs"].append(bundle["original_jobs"][2])
    elif case == "foreign_check":
        bundle["original_check"]["app"]["slug"] = "unrelated-app"
    elif case == "changed_comment":
        bundle["canonical_comment"]["body"] += "\nchanged"
    elif case == "wrong_base_binding":
        bundle["original_run"]["pull_requests"][0]["base"]["sha"] = "e" * 40
    else:
        del bundle["artifacts"]["candidate"]
    with pytest.raises(module.RecoveryError):
        module.validate_evidence(module.RecoveryTarget(**bundle["target"]), bundle, bundle["workspace"])
