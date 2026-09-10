"""Bounded, side-effect-free evidence checks for OpenCode finalization recovery."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile


class RecoveryError(ValueError):
    """A bounded refusal; untrusted evidence text never becomes a diagnostic."""


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise RecoveryError(reason)


def integer(value: object, *, minimum: int = 1) -> bool:
    return type(value) is int and minimum <= value <= 9007199254740991


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def strict_json(payload: bytes | str) -> object:
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "evidence_json_duplicate_key")
            result[key] = value
        return result

    def constant(_):
        raise RecoveryError("evidence_json_nonfinite")

    try:
        return json.loads(payload, object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        if isinstance(exc, RecoveryError):
            raise
        raise RecoveryError("evidence_json_invalid") from exc


def read_artifact(metadata: dict, payload: bytes, expected_name: str, run_id: int) -> dict[str, bytes]:
    """Verify a ZIP in memory. Never extract attacker-selected paths."""
    require(isinstance(metadata, dict) and isinstance(payload, bytes), "artifact_invalid")
    require(integer(metadata.get("id")), "artifact_identity_invalid")
    require(metadata.get("name") == expected_name, "artifact_identity_invalid")
    require(metadata.get("expired") is False, "artifact_expired_or_unavailable")
    require(isinstance(metadata.get("workflow_run"), dict)
            and metadata["workflow_run"].get("id") == run_id, "artifact_identity_invalid")
    require(integer(metadata.get("size_in_bytes"))
            and metadata["size_in_bytes"] == len(payload)
            and len(payload) <= 8_000_000, "artifact_size_invalid")
    require(metadata.get("digest") == "sha256:" + sha256(payload), "artifact_digest_invalid")
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            entries = archive.infolist()
            require(1 <= len(entries) <= 8, "artifact_inventory_invalid")
            require(len({entry.filename for entry in entries}) == len(entries),
                    "artifact_inventory_invalid")
            require(sum(entry.file_size for entry in entries) <= 8_000_000,
                    "artifact_size_invalid")
            for entry in entries:
                require(re.fullmatch(r"[a-z][a-z0-9-]*\.(?:json|md|diff)", entry.filename) is not None,
                        "artifact_path_invalid")
                mode = entry.external_attr >> 16
                require(not entry.is_dir() and stat.S_IFMT(mode) in (0, stat.S_IFREG)
                        and not entry.flag_bits & 1, "artifact_type_invalid")
                require(0 <= entry.file_size <= 4_000_000, "artifact_size_invalid")
            return {entry.filename: archive.read(entry) for entry in entries}
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError) as exc:
        raise RecoveryError("artifact_zip_invalid") from exc

# Only the reviewed v1.74/v1.75 and v1.76 canonicalizers are executable in recovery.
# Future formats must add a reviewed replay contract; unknown source fails closed.
APPROVED_REPLAY_SHA256 = frozenset({
    "b8804d0c387e4f7e554443a1d0edd5862d9c4b1c1435de4140c621c241a25df5",
    "3915f9bc32985745996d2246017cff9122460d25a071ca72568ac73c75339371",
})
APPROVED_WORKFLOW_SHA256 = frozenset({
    "f81db9665847aea815c8691caea7c6458011595cbed28c13809f051c381b5e91",
    "9cc171e9c11de4c6719d73922fed0373c5db0281feae7488c55c7447025bf0ab",
})

def _budget_module():
    name = "_opencode_recovery_budget"
    if name not in sys.modules:
        path = Path(__file__).parents[1] / "review-invocation-budget/review_invocation_budget.py"
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


@dataclass(frozen=True)
class RecoveryTarget:
    repository: str
    pr: int
    run_id: int
    run_attempt: int
    head_sha: str
    base_sha: str

    def __post_init__(self):
        require(isinstance(self.repository, str)
                and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repository) is not None,
                "recovery_identity_invalid")
        require(all(integer(value) for value in (self.pr, self.run_id, self.run_attempt)),
                "recovery_identity_invalid")
        require(all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value)
                    for value in (self.head_sha, self.base_sha)), "recovery_identity_invalid")


@dataclass(frozen=True)
class ValidatedEvidence:
    claim_state: object
    original_claim: object
    request: object
    canonical_body: str
    state: dict
    attestation: dict
    evidence_digests: dict


def validate_pr(target: RecoveryTarget, pr: dict) -> None:
    require(isinstance(pr, dict) and pr.get("number") == target.pr
            and pr.get("state") == "open" and pr.get("merged") is False, "pr_not_open")
    require(pr.get("head", {}).get("sha") == target.head_sha
            and pr.get("base", {}).get("sha") == target.base_sha, "pr_identity_changed")
    require(pr["head"].get("repo", {}).get("full_name") == target.repository
            and pr["head"]["repo"].get("fork") is False
            and pr["base"].get("repo", {}).get("full_name") == target.repository,
            "pr_repository_invalid")


def validate_original_run(target: RecoveryTarget, run: dict, jobs: list) -> None:
    require(isinstance(run, dict) and run.get("id") == target.run_id
            and run.get("run_attempt") == target.run_attempt
            and integer(run.get("id")) and integer(run.get("run_attempt"))
            and isinstance(run.get("head_sha"), str)
            and re.fullmatch(r"[0-9a-f]{40}", run["head_sha"])
            and run.get("event") == "pull_request"
            and run.get("status") == "completed" and run.get("conclusion") == "failure"
            and run.get("repository", {}).get("full_name") == target.repository,
            "original_run_invalid")
    bindings = run.get("pull_requests")
    require(isinstance(bindings, list) and len(bindings) == 1, "original_pr_binding_invalid")
    binding = bindings[0]
    require(binding.get("number") == target.pr
            and binding.get("head", {}).get("sha") == target.head_sha
            and binding.get("base", {}).get("sha") == target.base_sha, "original_pr_binding_invalid")
    require(isinstance(jobs, list) and 1 <= len(jobs) <= 100, "original_jobs_invalid")
    expected = {
        "opencode-prepare": ("success", {
            "Claim OpenCode review budget": "success",
            "Build sealed canonicalization handoff": "success",
            "Upload sealed canonicalization handoff": "success",
        }),
        "opencode-review": ("success", {
            "Run OpenCode PR review": "success",
            "Materialize sealed OpenCode candidate": "success",
            "Upload untrusted OpenCode candidate": "success",
        }),
        "opencode-canonicalize": ("failure", {
            "Canonicalize OpenCode review": "success",
            "Resolve OpenCode budget outcome": "success",
            "Finalize OpenCode review budget": "failure",
        }),
    }
    for name, (conclusion, required_steps) in expected.items():
        matches = [job for job in jobs if isinstance(job, dict)
                   and re.search(r"(?:^|\s/\s)" + re.escape(name) + r"$", job.get("name", ""))]
        require(len(matches) == 1, "original_job_ambiguous")
        job = matches[0]
        require(job.get("status") == "completed" and job.get("conclusion") == conclusion
                and job.get("run_id") == target.run_id, "original_job_invalid")
        # GitHub's jobs response does not always include run_attempt; its exact-attempt
        # endpoint is mandatory in the collector. If present it must agree.
        require("run_attempt" not in job or job["run_attempt"] == target.run_attempt,
                "original_job_invalid")
        steps = job.get("steps")
        require(isinstance(steps, list) and len(steps) <= 100, "original_steps_invalid")
        numbers = [step.get("number") for step in steps if isinstance(step, dict)]
        require(len(numbers) == len(steps) and all(integer(number) for number in numbers)
                and numbers == sorted(set(numbers)), "original_step_order_invalid")
        previous_number = 0
        for step_name, step_conclusion in required_steps.items():
            found = [s for s in steps if isinstance(s, dict) and s.get("name") == step_name]
            require(len(found) == 1 and found[0].get("status") == "completed"
                    and found[0].get("conclusion") == step_conclusion, "original_step_invalid")
            require(found[0]["number"] > previous_number, "original_step_order_invalid")
            previous_number = found[0]["number"]
        failed = [s.get("name") for s in steps if s.get("conclusion") not in ("success", "skipped")]
        require(failed == (["Finalize OpenCode review budget"] if conclusion == "failure" else []),
                "original_failure_not_finalizer")


def validate_canonical(target: RecoveryTarget, comment: dict, check: dict,
                       workflow_head: str | None = None) -> tuple[dict, dict]:
    workflow_head = target.head_sha if workflow_head is None else workflow_head
    require(isinstance(comment, dict) and integer(comment.get("id"))
            and comment.get("user", {}).get("login") == "github-actions[bot]"
            and comment["user"].get("type") == "Bot", "canonical_author_invalid")
    body = comment.get("body")
    require(isinstance(body, str) and len(body.encode("utf-8")) <= 65536, "canonical_body_invalid")
    lines = body.split("\n")
    require(len(lines) > 3 and lines[:2] == [
        "## OpenCode Review (latest)", "<!-- automation:opencode-auto-review:v2 -->"],
        "canonical_body_invalid")
    match = re.fullmatch(r"<!-- automation-state:(\{.*\}) -->", lines[2])
    require(match is not None, "canonical_state_invalid")
    state_text = match[1]
    state = strict_json(state_text)
    require(isinstance(state, dict) and set(state) == {
        "schema", "reviewer", "pr", "run_id", "run_attempt", "attempt_head",
        "successful_head", "attempt_status", "diff_mode", "full_diff_sha256"},
        "canonical_state_invalid")
    require(state["schema"] == 2 and state["reviewer"] == "opencode"
            and state["pr"] == target.pr and state["run_id"] == target.run_id
            and state["run_attempt"] == target.run_attempt
            and state["attempt_head"] == state["successful_head"] == target.head_sha
            and state["attempt_status"] == "success" and state["diff_mode"] in ("full", "delta")
            and re.fullmatch(r"[0-9a-f]{64}", str(state["full_diff_sha256"])),
            "canonical_state_invalid")
    require(isinstance(check, dict) and integer(check.get("id"))
            and check.get("name") == "automation/opencode-canonical-review"
            and check.get("status") == "completed" and check.get("conclusion") == "success"
            and check.get("head_sha") == workflow_head
            and check.get("app", {}).get("slug") == "github-actions"
            and check["app"].get("id") == 15368, "canonical_check_invalid")
    raw = check.get("output", {}).get("text")
    match = re.fullmatch(r"<!-- automation-attestation:(\{.*\}) -->", raw or "")
    require(match is not None, "canonical_attestation_invalid")
    attestation = strict_json(match[1])
    expected_keys = {"schema", "repository", "workflow", "pr", "caller_workflow_path",
        "caller_event", "referenced_workflow_path", "referenced_workflow_sha", "attempt_head",
        "workflow_head", "successful_head", "run_id", "run_attempt", "comment_id",
        "prepared_run_attempt", "body_sha256", "state_sha256"}
    require(isinstance(attestation, dict) and set(attestation) == expected_keys,
            "canonical_attestation_invalid")
    require(all(integer(attestation[key]) for key in (
        "schema", "pr", "run_id", "run_attempt", "prepared_run_attempt", "comment_id")),
        "canonical_attestation_invalid")
    expected = {"schema": 1, "repository": target.repository,
        "workflow": ".github/workflows/opencode-auto-review.yml", "pr": target.pr,
        "caller_event": "pull_request", "run_id": target.run_id, "run_attempt": target.run_attempt,
        "prepared_run_attempt": target.run_attempt, "attempt_head": target.head_sha,
        "workflow_head": workflow_head, "successful_head": target.head_sha,
        "comment_id": comment["id"], "body_sha256": sha256(body.encode()),
        "state_sha256": sha256(state_text.encode())}
    require(all(attestation.get(key) == value for key, value in expected.items()),
            "canonical_attestation_invalid")
    require(check.get("external_id") == (
        f"automation-opencode-canonical:{target.repository}:pr:{target.pr}:"
        f"run:{target.run_id}:{target.run_attempt}:comment:{comment['id']}"),
        "canonical_attestation_invalid")
    require([line for line in lines if line.startswith("- Attestation: ")]
            == [f"- Attestation: {check['id']}"], "canonical_attestation_invalid")
    return state, attestation


def replay_source(workflow: str) -> str:
    require(isinstance(workflow, str) and len(workflow.encode()) <= 2_000_000,
            "original_workflow_invalid")
    require(sha256(workflow.encode()) in APPROVED_WORKFLOW_SHA256,
            "original_replay_unsupported")
    lines = workflow.splitlines(keepends=True)
    names = [i for i, line in enumerate(lines)
             if line == "      - name: Canonicalize OpenCode review\n"]
    require(len(names) == 1, "original_workflow_invalid")
    start = next((i for i in range(names[0], len(lines))
                  if lines[i] == "          script: |\n"), None)
    require(start is not None, "original_workflow_invalid")
    collected = []
    for line in lines[start + 1:]:
        require(line.startswith("            ") or not line.strip(), "original_workflow_invalid")
        line = line[12:] if line.startswith("            ") else line
        if line == "if (!(await repairComments())) return;\n":
            source = "".join(collected)
            require(sha256(source.encode()) in APPROVED_REPLAY_SHA256, "original_replay_unsupported")
            return source
        collected.append(line)
    raise RecoveryError("original_workflow_invalid")


def git_environment() -> dict[str, str]:
    return {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_SYSTEM": "/dev/null", "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/false", "SSH_ASKPASS": "/bin/false",
        "GIT_NO_REPLACE_OBJECTS": "1", "GIT_OPTIONAL_LOCKS": "0"}


def git_read(workspace: Path, *arguments: str) -> bytes:
    try:
        result = subprocess.run(["/usr/bin/git", "--no-replace-objects", "--literal-pathspecs",
            "-c", "diff.external=", "-c", "color.ui=false", *arguments], cwd=workspace,
            env=git_environment(), check=True, capture_output=True, timeout=30)
        require(len(result.stdout) <= 8_000_000, "recovery_git_output_limit")
        return result.stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise RecoveryError("recovery_git_unavailable") from exc


def _replay(target, bundle, workspace, handoff, files, candidate_files, candidate) -> dict:
    with tempfile.TemporaryDirectory(prefix="opencode-recovery-") as directory:
        root = Path(directory)
        for dirname, inventory in (("handoff", files), ("candidate", candidate_files)):
            folder = root / dirname
            folder.mkdir(mode=0o700)
            for name, payload in inventory.items():
                path = folder / name
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(payload)
        environment = {
            "OPENCODE_RECOVERY_HELPER": str(Path(__file__).with_name("receipt.js").resolve()),
            "PR_NUMBER": str(target.pr), "RUN_ID": str(target.run_id),
            "RUN_ATTEMPT": str(target.run_attempt), "ATTEMPT_HEAD": target.head_sha,
            "WORKFLOW_HEAD": handoff["workflow_head"], "FULL_DIFF_SHA256": handoff["full_diff_sha256"],
            "RUN_URL": f"https://github.com/{target.repository}/actions/runs/{target.run_id}",
            "SERVER_URL": "https://github.com", "REPOSITORY": target.repository,
            "WORKFLOW_NAME": ".github/workflows/opencode-auto-review.yml",
            "TRUSTED_WORKSPACE": str(workspace.resolve()), "DIFF_READY": "true",
            "DIFF_MODE": handoff["diff_mode"], "UNCHANGED_SINCE_PREVIOUS": "false",
            "BUDGET_ALLOW_INVOCATION": "true", "BUDGET_DECISION": "claimed",
            "BUDGET_CHECKPOINT_SHA256": handoff["budget_checkpoint_sha256"],
            "REVIEW_CALL_COUNT": str(candidate["call_count"]),
            "REVIEW_ELAPSED_SECONDS": str(candidate["elapsed_seconds"]),
            "REVIEW_MODEL_ROUTE_JSON": json.dumps(candidate["model_route"]),
            "REVIEW_OUTCOME": "success", "REVIEW_FAILURE_REASON": "none",
            "CANDIDATE_DOWNLOAD_OUTCOME": "success",
        }
        path_names = {"HANDOFF_PATH": "handoff/handoff.json",
            "SNAPSHOT_PATH": "handoff/opencode-comments-before.json",
            "ATTESTATIONS_PATH": "handoff/opencode-attestations-before.json",
            "BUDGET_CLAIM_PATH": "handoff/review-budget-claim.json",
            "REVIEW_DIFF_PATH": "handoff/review-full.diff", "REVIEW_SCOPE_PATH": "handoff/review-scope.json",
            "CANDIDATE_PATH": "candidate/review.md", "CANDIDATE_ENVELOPE_PATH": "candidate/candidate.json"}
        environment.update({key: str(root / path) for key, path in path_names.items()})
        for role in ("handoff", "candidate"):
            metadata = bundle["artifacts"][role]["metadata"]
            environment[role.upper() + "_ARTIFACT_ID"] = str(metadata["id"])
            environment[role.upper() + "_ARTIFACT_DIGEST"] = metadata["digest"][7:]
            environment[role.upper() + "_ARTIFACT_NAME"] = metadata["name"]
        payload = {"source": replay_source(bundle["workflow_source"]), "env": environment,
            "target": target.__dict__, "run": bundle["original_run"], "pr": bundle["pr"],
            "artifacts": [item["metadata"] for item in bundle["artifacts"].values()],
            "history": bundle["history"], "check_id": bundle["original_check"]["id"]}
        try:
            node = shutil.which("node")
            require(node is not None, "recovery_node_unavailable")
            result = subprocess.run([str(Path(node).resolve()), str(Path(__file__).with_name("replay.js"))],
                input=json.dumps(payload).encode(), env=git_environment(), cwd=root,
                capture_output=True, timeout=45, check=True)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RecoveryError("canonical_replay_failed") from exc
        require(len(result.stdout) <= 131072, "canonical_replay_output_limit")
        return strict_json(result.stdout)


def validate_evidence(target: RecoveryTarget, bundle: dict, workspace: Path) -> ValidatedEvidence:
    try:
        validate_pr(target, bundle["pr"])
        validate_original_run(target, bundle["original_run"], bundle["original_jobs"])
        state, attestation = validate_canonical(target, bundle["canonical_comment"],
            bundle["original_check"], bundle["original_run"]["head_sha"])
        artifacts = bundle["artifacts"]
        require(set(artifacts) == {"handoff", "claim", "candidate"}, "artifact_inventory_invalid")
        inventories = {}
        for role, prefix in (("handoff", "opencode-handoff"), ("claim", "opencode-review-budget-claim"),
                             ("candidate", "opencode-candidate")):
            item = artifacts[role]
            inventories[role] = read_artifact(item["metadata"], item["payload"],
                f"{prefix}-{target.run_id}-{target.run_attempt}", target.run_id)
        files = inventories["handoff"]
        require(set(files) == {"handoff.json", "opencode-comments-before.json",
            "opencode-attestations-before.json", "review-budget-claim.json",
            "review-full.diff", "review-scope.json"}, "handoff_inventory_invalid")
        require(set(inventories["candidate"]) == {"candidate.json", "review.md"}
                and set(inventories["claim"]) == {"opencode-review-budget-claim.json"},
                "artifact_inventory_invalid")
        handoff = strict_json(files["handoff.json"])
        require(isinstance(handoff, dict) and isinstance(handoff.get("files"), dict),
                "handoff_invalid")
        require(set(handoff["files"]) == set(files) - {"handoff.json"}, "handoff_inventory_invalid")
        require(all(handoff["files"][name] == sha256(payload) for name, payload in files.items()
                    if name != "handoff.json"), "handoff_digest_invalid")
        require(files["review-budget-claim.json"] == inventories["claim"]["opencode-review-budget-claim.json"],
                "claim_checkpoint_mismatch")
        run = bundle["original_run"]
        central = [entry for entry in run.get("referenced_workflows", [])
                   if entry.get("path") == handoff.get("referenced_workflow_path")
                   and entry.get("sha") == handoff.get("referenced_workflow_sha")]
        require(len(central) == 1 and attestation["referenced_workflow_path"] == central[0]["path"]
                and attestation["referenced_workflow_sha"] == central[0]["sha"]
                and attestation["caller_workflow_path"] == run["path"] == handoff["caller_workflow_path"],
                "original_workflow_identity_invalid")
        require(handoff.get("run_attempt") == target.run_attempt
                and handoff.get("run_id") == target.run_id, "original_claim_identity_invalid")
        candidate = strict_json(inventories["candidate"]["candidate.json"])
        require(isinstance(candidate, dict) and candidate.get("run_attempt") == target.run_attempt
                and candidate.get("run_id") == target.run_id, "original_candidate_identity_invalid")
        budget = _budget_module()
        claim_state = budget.load_checkpoint(files["review-budget-claim.json"])
        require(claim_state.repository == target.repository and claim_state.pr == target.pr
                and claim_state.reviewer == "opencode" and claim_state.invocations,
                "original_claim_identity_invalid")
        original_claim = claim_state.invocations[-1]
        require(original_claim.status == "claimed" and original_claim.run_id == target.run_id
                and original_claim.run_attempt == target.run_attempt
                and original_claim.head_sha == target.head_sha
                and original_claim.full_diff_sha256 == state["full_diff_sha256"],
                "original_claim_identity_invalid")
        require(original_claim.caller_workflow_path == run["path"] == handoff.get("caller_workflow_path")
                and original_claim.caller_event == run.get("event") == handoff.get("caller_event") == "pull_request"
                and original_claim.referenced_workflow_path == central[0]["path"]
                and original_claim.referenced_workflow_sha == central[0]["sha"]
                and original_claim.referenced_workflow_ref == central[0].get("ref", central[0]["sha"]),
                "original_claim_provenance_invalid")
        require(git_read(workspace, "rev-parse", "HEAD").decode().strip() == target.head_sha,
                "workspace_head_invalid")
        base = git_read(workspace, "merge-base", target.base_sha, target.head_sha).decode().strip()
        require(base == handoff["merge_base_sha"], "original_merge_base_invalid")
        diff = git_read(workspace, "diff", "--no-ext-diff", "--no-textconv", "--find-renames=50%",
                        "--ignore-submodules=none", "-U3", base + ".." + target.head_sha)
        require(diff == files["review-full.diff"]
                and sha256(diff) == state["full_diff_sha256"], "original_diff_invalid")
        replay = _replay(target, bundle, workspace, handoff, files, inventories["candidate"], candidate)
        require(isinstance(replay, dict) and replay.get("succeeded") is True
                and replay.get("body") == bundle["canonical_comment"]["body"]
                and replay.get("state") == state, "canonical_replay_mismatch")
        require(integer(candidate.get("call_count")) and candidate["call_count"] <= 2
                and integer(candidate.get("elapsed_seconds"), minimum=0)
                and candidate["elapsed_seconds"] <= 600, "original_metrics_invalid")
        remaining = tuple(replay["remaining_finding_ids"])
        outcome = "quality_filtered" if replay["quality_filtered"] else "success"
        request = budget.FinalizeRequest(target.repository, target.pr, "opencode", target.run_id,
            target.run_attempt, target.head_sha, state["full_diff_sha256"],
            tuple(candidate["model_route"]), original_claim.effort, candidate["call_count"],
            candidate["elapsed_seconds"], outcome, outcome,
            budget.AuthenticatedReview(True, target.head_sha, state["full_diff_sha256"], remaining),
            remaining)
        budget._validate_finalize_request(request)
        digests = {role: {"id": item["metadata"]["id"], "sha256": sha256(item["payload"])}
                   for role, item in artifacts.items()}
        digests["claim_checkpoint_sha256"] = sha256(files["review-budget-claim.json"])
        return ValidatedEvidence(claim_state, original_claim, request, replay["body"], state,
                                 attestation, digests)
    except RecoveryError:
        raise
    except (KeyError, TypeError, AttributeError, ValueError, IndexError) as exc:
        raise RecoveryError("recovery_evidence_invalid") from exc
