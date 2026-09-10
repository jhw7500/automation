"""Synthetic original-attempt evidence, with real Git objects and a literal clean review."""
import copy
import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "recovery_fixture_budget", ROOT / ".github/actions/review-invocation-budget/review_invocation_budget.py")
budget = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = budget
spec.loader.exec_module(budget)
REPOSITORY = "example/repo"
PR = 52
RUN = 700
ATTEMPT = 1
CENTRAL = "d" * 40
WORKFLOW = "jhw7500/automation/.github/workflows/opencode-auto-review.yml@" + CENTRAL


def encode(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def artifact(identifier, name, files):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for filename, data in files.items():
            z.writestr(filename, data)
    data = out.getvalue()
    return {"metadata": {"id": identifier, "name": name, "expired": False,
            "digest": "sha256:" + digest(data), "size_in_bytes": len(data),
            "workflow_run": {"id": RUN}}, "payload": data}


def git(root, *args):
    return subprocess.run(["/usr/bin/git", *args], cwd=root, check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


def original_bundle(tmp_path):
    repo = tmp_path / "target"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Recovery Test")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "app.py").write_text("ready = False\n")
    git(repo, "add", "app.py")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD").decode().strip()
    (repo / "app.py").write_text("ready = True\n")
    git(repo, "add", "app.py")
    git(repo, "commit", "-qm", "head")
    head = git(repo, "rev-parse", "HEAD").decode().strip()
    diff = git(repo, "--no-replace-objects", "--literal-pathspecs", "-c", "diff.external=",
               "diff", "--no-ext-diff", "--no-textconv", "--find-renames=50%",
               "--ignore-submodules=none", "-U3", base + ".." + head)
    full_hash = digest(diff)
    provenance = budget.RunProvenance(
        REPOSITORY, PR, head, ".github/workflows/opencode-auto-review.yml", "pull_request",
        WORKFLOW, CENTRAL, CENTRAL, RUN, ATTEMPT, "in_progress", None)
    request = budget.ClaimRequest(
        REPOSITORY, PR, "opencode", RUN, ATTEMPT, head, full_hash, 1200, "changed",
        budget.AuthenticatedReview(False, None, None), (), ("zai-coding-plan/glm-4.7",),
        "final-review/default", "opencode run session")
    claim_state = budget.claim(None, request, {(RUN, ATTEMPT): provenance}).state
    checkpoint = budget.render_checkpoint(claim_state)
    scope = {"schema": 1, "repository": REPOSITORY, "pr_number": PR,
             "merge_base_sha": base, "head_sha": head,
             "files": [{"filename": "app.py", "status": "modified"}]}
    handoff_files = {"opencode-comments-before.json": b"[]",
        "opencode-attestations-before.json": encode({"check_runs": [], "workflow_runs": []}),
        "review-budget-claim.json": checkpoint, "review-full.diff": diff,
        "review-scope.json": encode(scope)}
    handoff = {"schema": 1, "repository": REPOSITORY, "pr": PR, "server_url": "https://github.com",
        "workflow": ".github/workflows/opencode-auto-review.yml",
        "caller_workflow_path": ".github/workflows/opencode-auto-review.yml", "caller_event": "pull_request",
        "candidate_nonce": "ab" * 32, "workflow_head": head,
        "referenced_workflow_path": WORKFLOW, "referenced_workflow_sha": CENTRAL,
        "run_id": RUN, "run_attempt": ATTEMPT, "attempt_head": head, "merge_base_sha": base,
        "diff_ready": True, "diff_mode": "full", "unchanged_since_previous": False,
        "full_diff_sha256": full_hash, "allow_invocation": True, "budget_decision": "claimed",
        "budget_checkpoint_sha256": digest(checkpoint),
        "files": {name: digest(data) for name, data in handoff_files.items()}}
    handoff_files["handoff.json"] = encode(handoff)
    review = ("<!-- automation:opencode-auto-review -->\n<!-- automation-candidate:"
              + "ab" * 32 + " -->\n### New findings\nNone").encode()
    candidate = {"schema": 2, "repository": REPOSITORY, "pr": PR,
        "run_id": RUN, "run_attempt": ATTEMPT, "head_sha": head, "full_diff_sha256": full_hash,
        "diff_mode": "full", "claim_checkpoint_sha256": digest(checkpoint), "call_count": 1,
        "elapsed_seconds": 45, "model_route": ["zai-coding-plan/glm-4.7"], "outcome": "success",
        "failure_reason": "none", "review_sha256": digest(review),
        "candidate_validations": [{"attempt": "initial", "sha256": digest(review), "valid": True,
                                  "rule": None, "line": None, "column": None}]}
    state = {"schema": 2, "reviewer": "opencode", "pr": PR, "run_id": RUN, "run_attempt": ATTEMPT,
        "attempt_head": head, "successful_head": head, "attempt_status": "success",
        "diff_mode": "full", "full_diff_sha256": full_hash}
    state_text = encode(state).decode()
    body = ("## OpenCode Review (latest)\n<!-- automation:opencode-auto-review:v2 -->\n"
        + "<!-- automation-state:" + state_text + " -->\n<!-- automation:opencode-auto-review -->\n\n"
        + "- Status: success\n- Run: https://github.com/example/repo/actions/runs/700\n"
        + "- Attestation: 903\n- Reviewed: " + head + "\n\n### New findings\nNone")
    comment = {"id": 902, "body": body, "user": {"type": "Bot", "login": "github-actions[bot]"}}
    attestation = {"schema": 1, "repository": REPOSITORY, "workflow": handoff["workflow"], "pr": PR,
        "caller_workflow_path": handoff["caller_workflow_path"], "caller_event": "pull_request",
        "referenced_workflow_path": WORKFLOW, "referenced_workflow_sha": CENTRAL,
        "attempt_head": head, "workflow_head": head, "successful_head": head, "run_id": RUN,
        "run_attempt": ATTEMPT, "comment_id": 902, "prepared_run_attempt": ATTEMPT,
        "body_sha256": digest(body.encode()), "state_sha256": digest(state_text.encode())}
    check = {"id": 903, "name": "automation/opencode-canonical-review", "status": "completed",
        "conclusion": "success", "head_sha": head, "app": {"id": 15368, "slug": "github-actions"},
        "external_id": "automation-opencode-canonical:example/repo:pr:52:run:700:1:comment:902",
        "output": {"text": "<!-- automation-attestation:" + encode(attestation).decode() + " -->"}}
    pr = {"number": PR, "state": "open", "merged": False,
        "head": {"sha": head, "repo": {"full_name": REPOSITORY, "fork": False}},
        "base": {"sha": base, "repo": {"full_name": REPOSITORY}}}
    run = {"id": RUN, "run_attempt": ATTEMPT, "head_sha": head, "status": "completed",
        "conclusion": "failure", "event": "pull_request", "path": handoff["caller_workflow_path"],
        "repository": {"full_name": REPOSITORY}, "referenced_workflows": [{"path": WORKFLOW, "sha": CENTRAL}],
        "pull_requests": [{"number": PR, "head": {"sha": head, "repo": {"name": "repo"}},
                          "base": {"sha": base, "repo": {"name": "repo"}}}]}
    def job(suffix, conclusion, steps):
        return {"id": {"opencode-prepare": 1101, "opencode-review": 1102, "opencode-canonicalize": 1103}[suffix],
            "name": "opencode-review / " + suffix, "status": "completed", "conclusion": conclusion,
            "run_id": RUN, "run_attempt": ATTEMPT,
            "steps": [{"number": i + 1, "name": name, "status": "completed", "conclusion": outcome}
                      for i, (name, outcome) in enumerate(steps)]}
    jobs = [
        job("opencode-prepare", "success", [("Claim OpenCode review budget", "success"),
            ("Build sealed canonicalization handoff", "success"),
            ("Upload sealed canonicalization handoff", "success")]),
        job("opencode-review", "success", [("Run OpenCode PR review", "success"),
            ("Materialize sealed OpenCode candidate", "success"), ("Upload untrusted OpenCode candidate", "success")]),
        job("opencode-canonicalize", "failure", [("Canonicalize OpenCode review", "success"),
            ("Resolve OpenCode budget outcome", "success"), ("Finalize OpenCode review budget", "failure")]),
    ]
    artifacts = {
        "handoff": artifact(101, "opencode-handoff-700-1", handoff_files),
        "claim": artifact(102, "opencode-review-budget-claim-700-1",
                          {"opencode-review-budget-claim.json": checkpoint}),
        "candidate": artifact(103, "opencode-candidate-700-1",
                              {"candidate.json": encode(candidate), "review.md": review}),
    }
    return {"target": {"repository": REPOSITORY, "pr": PR, "run_id": RUN, "run_attempt": ATTEMPT,
                       "head_sha": head, "base_sha": base},
        "pr": pr, "original_run": run, "original_jobs": jobs,
        "artifacts": artifacts, "canonical_comment": comment, "original_check": check,
        "ledger_comment": {"id": 901, "user": {"type": "Bot", "login": "github-actions[bot]"},
                           "body": budget.render_comment(claim_state, server_url="https://github.com")},
        "workspace": repo, "claim_state": claim_state,
        "history": {"runs": [run], "checks": [check], "attempts": {"700:1": {"run": run, "jobs": jobs}},
                    "comments": [comment]},
        "workflow_source": (ROOT / "tests/fixtures/opencode-recovery/original-workflow.yml").read_text(),
    }
