"""Serialized, bounded GitHub transport for original OpenCode finalization recovery.

The caller holds the ordinary OpenCode per-PR concurrency group. A comparison
before PATCH detects drift; GitHub issue comments do not provide atomic CAS.
No retry of a mutation is implicit, even when its response is lost.
"""
from __future__ import annotations

import argparse
import base64
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import quote

from evidence import (RecoveryError, RecoveryTarget, _budget_module, git_environment,
                      integer, require, sha256, strict_json, validate_canonical,
                      validate_evidence, validate_original_run, validate_pr)

budget = _budget_module()
CENTRAL = "jhw7500/automation"
NORMAL = ".github/workflows/opencode-auto-review.yml"
RECOVERY = ".github/workflows/opencode-recover-finalization.yml"
CHECK = "automation/opencode-finalization-recovery"
CANONICAL_CHECK = "automation/opencode-canonical-review"
RECEIPT_PREFIX = "<!-- automation-opencode-recovery:"


def positive_input(value: str) -> int:
    require(isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value) is not None
            and len(value) <= 16, "recovery_input_invalid")
    number = int(value)
    require(integer(number), "recovery_input_invalid")
    return number


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class GitHub:
    """One request per call, fixed host, isolated CLI config and bounded output."""

    def __init__(self, token: str):
        require(isinstance(token, str) and bool(token), "recovery_token_unavailable")
        executable = shutil.which("gh")
        require(executable is not None, "recovery_gh_unavailable")
        self.executable = str(Path(executable).resolve())
        self.token = token

    def _call(self, method, path, payload=None):
        require(method in {"GET", "PATCH", "POST"} and path.startswith("repos/")
                and "\n" not in path and "\r" not in path, "recovery_api_path_invalid")
        with tempfile.TemporaryDirectory(prefix="recovery-gh-") as directory:
            env = git_environment() | {"GH_TOKEN": self.token, "GH_HOST": "github.com",
                "GH_PROMPT_DISABLED": "1", "GH_CONFIG_DIR": directory}
            command = [self.executable, "api", "--hostname", "github.com", "--method", method,
                       "-H", "Accept: application/vnd.github+json",
                       "-H", "X-GitHub-Api-Version: 2022-11-28", path]
            if payload is not None:
                command += ["--input", "-"]
            try:
                result = subprocess.run(command, input=None if payload is None else
                    canonical_json(payload).encode(), env=env, capture_output=True,
                    check=True, timeout=45)
            except (OSError, subprocess.SubprocessError) as exc:
                raise RecoveryError("recovery_api_uncertain") from exc
            require(len(result.stdout) <= 8_000_000, "recovery_api_output_limit")
            return result.stdout

    def request(self, method, path, payload=None):
        return strict_json(self._call(method, path, payload))

    def download(self, path):
        return self._call("GET", path)


def get(api, path):
    try:
        return api.request("GET", path)
    except (RecoveryError, OSError, TimeoutError) as exc:
        raise RecoveryError("recovery_api_uncertain") from exc


def page(api, path, key, *, horizon=False):
    value = get(api, path + ("&" if "?" in path else "?") + "per_page=100&page=1")
    require(isinstance(value, dict) and isinstance(value.get(key), list)
            and integer(value.get("total_count"), minimum=0)
            and len(value[key]) <= 100
            and value["total_count"] >= len(value[key]), "recovery_api_page_invalid")
    if not horizon:
        require(value["total_count"] == len(value[key]), "recovery_api_page_limit")
    require(all(isinstance(item, dict) for item in value[key]), "recovery_api_page_invalid")
    return value[key]


def all_items(api, path):
    result = []
    for number in range(1, 5):
        value = get(api, path + f"?per_page=100&page={number}")
        require(isinstance(value, list) and len(value) <= 100
                and all(isinstance(item, dict) for item in value), "recovery_api_page_invalid")
        result.extend(value)
        require(len(result) <= 300, "recovery_api_page_limit")
        if len(value) < 100:
            return result
    raise RecoveryError("recovery_api_page_limit")


def central_reference(run, workflow, *, pinned=False):
    references = run.get("referenced_workflows")
    require(isinstance(references, list) and len(references) <= 100, "workflow_reference_invalid")
    prefix = f"{CENTRAL}/{workflow}@"
    matches = [r for r in references if isinstance(r, dict)
               and isinstance(r.get("path"), str) and r["path"].startswith(prefix)]
    require(len(matches) == 1, "workflow_reference_invalid")
    ref = matches[0]
    require(re.fullmatch(r"[0-9a-f]{40}", str(ref.get("sha"))) is not None
            and bool(ref["path"][len(prefix):]), "workflow_reference_invalid")
    if pinned:
        require(ref["path"] == prefix + ref["sha"], "recovery_workflow_not_pinned")
    return ref


def driver_identity(target, run, run_id, attempt, central_sha):
    require(isinstance(run, dict) and integer(run.get("id")) and run["id"] == run_id
            and integer(run.get("run_attempt")) and run["run_attempt"] == attempt
            and run.get("event") == "workflow_dispatch"
            and run.get("repository", {}).get("full_name") == target.repository
            and re.fullmatch(r"[0-9a-f]{40}", str(run.get("head_sha"))) is not None
            and re.fullmatch(r"\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml", str(run.get("path")))
            and ".." not in run["path"], "recovery_driver_invalid")
    ref = central_reference(run, RECOVERY, pinned=True)
    require(ref["sha"] == central_sha, "recovery_driver_invalid")
    return {"run_id": run_id, "run_attempt": attempt, "workflow_head": run["head_sha"],
        "caller_workflow_path": run["path"], "referenced_workflow_path": ref["path"],
        "referenced_workflow_sha": ref["sha"], "provider_calls": 0}


def comment_identity(comment):
    require(isinstance(comment, dict) and integer(comment.get("id"))
            and comment.get("user", {}).get("login") == "github-actions[bot]"
            and comment["user"].get("type") == "Bot"
            and isinstance(comment.get("body"), str), "recovery_comment_invalid")
    return {key: comment[key] for key in ("id", "body", "user")}


def ledger_comment(target, comments):
    marker = budget.MARKERS["opencode"]
    matches = [c for c in comments if isinstance(c.get("body"), str)
               and (c["body"] == marker or c["body"].startswith(marker + "\n"))]
    require(len(matches) == 1, "recovery_ledger_ambiguous")
    comment = matches[0]
    comment_identity(comment)
    try:
        state = budget.parse_ledger(comment["body"], repository=target.repository,
                                    pr=target.pr, reviewer="opencode")
        require(state is not None, "recovery_ledger_invalid")
    except budget.BudgetStateError as exc:
        raise RecoveryError("recovery_ledger_invalid") from exc
    return comment, state


def named_checks(api, repository, head, name):
    require(re.fullmatch(r"[0-9a-f]{40}", str(head)) is not None, "recovery_check_head_invalid")
    return page(api, f"repos/{repository}/commits/{head}/check-runs?"
                f"check_name={quote(name, safe='')}&filter=all", "check_runs")


def original_publication(target, run, comments, checks):
    matches = []
    for check in checks:
        for comment in comments:
            try:
                validate_canonical(target, comment, check, run["head_sha"])
            except (RecoveryError, KeyError, TypeError, AttributeError):
                continue
            matches.append((comment, check))
    require(len(matches) == 1, "original_canonical_ambiguous")
    return matches[0]


def parse_receipt(check):
    raw = check.get("output", {}).get("text")
    require(isinstance(raw, str) and len(raw) <= 32768 and raw.startswith(RECEIPT_PREFIX)
            and raw.endswith(" -->") and "\n" not in raw, "recovery_receipt_invalid")
    value = strict_json(raw[len(RECEIPT_PREFIX):-4])
    require(isinstance(value, dict), "recovery_receipt_invalid")
    return value


def receipt_valid(facts):
    node = shutil.which("node")
    require(node is not None, "recovery_node_unavailable")
    try:
        output = subprocess.run([str(Path(node).resolve()), str(Path(__file__).with_name("receipt.js"))],
            input=canonical_json(facts).encode(), env=git_environment(), capture_output=True,
            check=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise RecoveryError("recovery_receipt_verifier_failed") from exc
    require(len(output) <= 1024, "recovery_receipt_verifier_failed")
    return strict_json(output) == {"valid": True}


def completed_receipt(target, api, comments):
    """Server heads seed discovery; embedded IDs are used only after intersection."""
    repository = target.repository
    runs = page(api, f"repos/{repository}/actions/runs?event=workflow_dispatch",
                "workflow_runs", horizon=True)
    matches = []
    for run in runs:
        try:
            central_reference(run, RECOVERY, pinned=True)
        except RecoveryError:
            continue
        matches.append(run)
    require(len(matches) <= 20, "recovery_driver_horizon_limit")
    checked = set()
    for run in matches:
        require(integer(run.get("id")) and integer(run.get("run_attempt")), "recovery_driver_invalid")
        for check in named_checks(api, repository, run.get("head_sha"), CHECK):
            try:
                receipt = parse_receipt(check)
                original = receipt["original"]
                driver = receipt["recovery"]
                selected = (receipt.get("repository") == repository and receipt.get("pr") == target.pr
                    and original["run_id"] == target.run_id and original["run_attempt"] == target.run_attempt
                    and original["head_sha"] == target.head_sha and original["base_sha"] == target.base_sha
                    and driver["run_id"] == run["id"] and driver["workflow_head"] == run["head_sha"]
                    and integer(driver["run_attempt"]) and driver["run_attempt"] <= run["run_attempt"])
            except (RecoveryError, KeyError, TypeError):
                continue
            if not selected or check.get("id") in checked:
                continue
            checked.add(check.get("id"))
            require(len(checked) <= 40, "recovery_receipt_candidate_limit")
            driver_run = get(api, f"repos/{repository}/actions/runs/{run['id']}/attempts/{driver['run_attempt']}")
            if driver_run.get("status") != "completed":
                # The active driver has not published a completed recovery yet.
                continue
            if driver_run.get("conclusion") != "success":
                continue
            original_run = get(api, f"repos/{repository}/actions/runs/{target.run_id}/attempts/{target.run_attempt}")
            original_jobs = page(api, f"repos/{repository}/actions/runs/{target.run_id}/attempts/"
                                 f"{target.run_attempt}/jobs", "jobs")
            checks = named_checks(api, repository, original_run.get("head_sha"), CANONICAL_CHECK)
            comment, canonical = original_publication(target, original_run, comments, checks)
            ledger, _ = ledger_comment(target, comments)
            facts = {"receipt": receipt, "check": check, "driverRun": driver_run,
                "driverJobs": page(api, f"repos/{repository}/actions/runs/{run['id']}/attempts/"
                                   f"{driver['run_attempt']}/jobs", "jobs"),
                "originalRun": original_run, "originalJobs": original_jobs,
                "canonicalCheck": canonical, "comment": comment, "ledgerComment": ledger}
            if receipt_valid(facts):
                return check["id"]
    return None


def workflow_source(api, run):
    ref = central_reference(run, NORMAL)
    path = NORMAL
    value = get(api, f"repos/{CENTRAL}/contents/{path}?ref={ref['sha']}")
    require(isinstance(value, dict) and value.get("type") == "file" and value.get("path") == path
            and value.get("encoding") == "base64" and integer(value.get("size"))
            and value["size"] <= 2_000_000 and isinstance(value.get("content"), str),
            "original_workflow_invalid")
    try:
        data = base64.b64decode("".join(value["content"].split()), validate=True)
        require(len(data) == value["size"], "original_workflow_invalid")
        return data.decode("utf-8")
    except (ValueError, UnicodeError) as exc:
        raise RecoveryError("original_workflow_invalid") from exc


def history_evidence(target, api, comments, original_run, original_jobs, original_check):
    repository = target.repository
    runs = page(api, f"repos/{repository}/actions/runs?event=pull_request",
                "workflow_runs", horizon=True)
    selected = []
    for run in runs:
        try:
            central_reference(run, NORMAL)
        except RecoveryError:
            continue
        if integer(run.get("id")) and integer(run.get("run_attempt")):
            selected.append(run)
    selected = selected[:20]
    checks = {original_check["id"]: original_check}
    attempts = {f"{target.run_id}:{target.run_attempt}": {"run": original_run, "jobs": original_jobs}}
    for run in selected:
        for check in named_checks(api, repository, run.get("head_sha"), CANONICAL_CHECK):
            checks[check["id"]] = check
            raw = check.get("output", {}).get("text", "")
            match = re.fullmatch(r"<!-- automation-attestation:(\{.*\}) -->", raw)
            if match is None:
                continue
            a = strict_json(match[1])
            if not isinstance(a, dict) or not integer(a.get("run_attempt")):
                continue
            if (a.get("run_id") != run["id"] or a.get("workflow_head") != run["head_sha"]
                    or not any(c.get("id") == a.get("comment_id")
                        and sha256(c.get("body", "").encode()) == a.get("body_sha256") for c in comments)):
                continue
            key = f"{run['id']}:{a['run_attempt']}"
            if key in attempts:
                continue
            require(len(attempts) < 40, "recovery_attempt_limit")
            exact = get(api, f"repos/{repository}/actions/runs/{run['id']}/attempts/{a['run_attempt']}")
            jobs = page(api, f"repos/{repository}/actions/runs/{run['id']}/attempts/"
                        f"{a['run_attempt']}/jobs", "jobs")
            attempts[key] = {"run": exact, "jobs": jobs}
    # New canonicalizers can authenticate prior recoveries. Seed these only from
    # server-selected dispatch runs and their named Checks, with the same limits
    # and exact-attempt API facts exposed to the tokenless verifier.
    dispatch = page(api, f"repos/{repository}/actions/runs?event=workflow_dispatch",
                    "workflow_runs", horizon=True)
    selected_dispatch = []
    for run in dispatch:
        try:
            central_reference(run, RECOVERY, pinned=True)
        except RecoveryError:
            continue
        if integer(run.get("id")) and integer(run.get("run_attempt")):
            selected_dispatch.append(run)
    candidates = set()
    for run in selected_dispatch[:20]:
        for check in named_checks(api, repository, run.get("head_sha"), CHECK):
            checks[check["id"]] = check
            try:
                receipt = parse_receipt(check)
                driver, original = receipt["recovery"], receipt["original"]
                selected = (receipt.get("repository") == repository and receipt.get("pr") == target.pr
                    and driver["run_id"] == run["id"] and driver["workflow_head"] == run["head_sha"]
                    and integer(driver["run_attempt"]) and driver["run_attempt"] <= run["run_attempt"]
                    and integer(original["run_id"]) and integer(original["run_attempt"])
                    and check.get("app", {}).get("id") == 15368
                    and check.get("app", {}).get("slug") == "github-actions"
                    and check.get("external_id") == check_payload(receipt)["external_id"]
                    and any(c.get("id") == original["comment_id"]
                        and sha256(c.get("body", "").encode()) == original["body_sha256"] for c in comments))
            except (RecoveryError, KeyError, TypeError):
                continue
            if not selected:
                continue
            candidates.add(check["id"])
            require(len(candidates) <= 40, "recovery_receipt_candidate_limit")
            for binding in (driver, original):
                key = f"{binding['run_id']}:{binding['run_attempt']}"
                if key not in attempts:
                    require(len(attempts) < 80, "recovery_attempt_limit")
                    exact = get(api, f"repos/{repository}/actions/runs/{binding['run_id']}/attempts/{binding['run_attempt']}")
                    jobs = page(api, f"repos/{repository}/actions/runs/{binding['run_id']}/attempts/"
                                f"{binding['run_attempt']}/jobs", "jobs")
                    attempts[key] = {"run": exact, "jobs": jobs}
            original_exact = attempts[f"{original['run_id']}:{original['run_attempt']}"]["run"]
            for canonical in named_checks(api, repository, original_exact.get("head_sha"), CANONICAL_CHECK):
                checks[canonical["id"]] = canonical
    return {"runs": runs + dispatch, "checks": list(checks.values()), "attempts": attempts, "comments": comments}


def collect_evidence(target, api, workspace, pr, comments):
    repository = target.repository
    run = get(api, f"repos/{repository}/actions/runs/{target.run_id}/attempts/{target.run_attempt}")
    jobs = page(api, f"repos/{repository}/actions/runs/{target.run_id}/attempts/{target.run_attempt}/jobs", "jobs")
    validate_original_run(target, run, jobs)
    comment, check = original_publication(target, run, comments,
        named_checks(api, repository, run["head_sha"], CANONICAL_CHECK))
    ledger, _ = ledger_comment(target, comments)
    artifacts = page(api, f"repos/{repository}/actions/runs/{target.run_id}/artifacts", "artifacts")
    collected = {}
    for role, prefix in (("handoff", "opencode-handoff"), ("claim", "opencode-review-budget-claim"),
                         ("candidate", "opencode-candidate")):
        name = f"{prefix}-{target.run_id}-{target.run_attempt}"
        matches = [a for a in artifacts if a.get("name") == name]
        require(len(matches) == 1 and integer(matches[0].get("id")), "artifact_identity_invalid")
        metadata = get(api, f"repos/{repository}/actions/artifacts/{matches[0]['id']}")
        require(metadata.get("id") == matches[0]["id"] and metadata.get("name") == name
                and metadata.get("expired") is False, "artifact_expired_or_unavailable")
        require(integer(metadata.get("size_in_bytes")) and metadata["size_in_bytes"] <= 8_000_000,
                "artifact_size_invalid")
        try:
            payload = api.download(f"repos/{repository}/actions/artifacts/{metadata['id']}/zip")
        except (RecoveryError, OSError, TimeoutError) as exc:
            raise RecoveryError("artifact_expired_or_unavailable") from exc
        collected[role] = {"metadata": metadata, "payload": payload}
    return {"pr": pr, "original_run": run, "original_jobs": jobs,
        "artifacts": collected, "canonical_comment": comment, "original_check": check,
        "ledger_comment": ledger, "workflow_source": workflow_source(api, run),
        "history": history_evidence(target, api, comments, run, jobs, check)}


def dismissals(target, api):
    events = all_items(api, f"repos/{target.repository}/issues/{target.pr}/timeline")
    result, permissions = [], {}
    for event in events:
        if event.get("event") != "commented":
            continue
        finding = budget.parse_dismiss_command(event.get("body"))
        if finding is None:
            continue
        actor = event.get("actor", {}).get("login")
        require(isinstance(actor, str) and 1 <= len(actor) <= 100 and integer(event.get("id")),
                "recovery_dismissal_invalid")
        if actor not in permissions:
            require(len(permissions) < budget.MAX_PERMISSION_ACTORS, "recovery_permission_limit")
            value = get(api, f"repos/{target.repository}/collaborators/{quote(actor, safe='')}/permission")
            require(value.get("user", {}).get("login") == actor and value.get("permission") in
                {"admin", "maintain", "write", "triage", "read", "none"}, "recovery_permission_invalid")
            permissions[actor] = value["permission"]
        result.append(budget.DismissEvent(event["id"], finding, permissions[actor]))
    return tuple(result)


def provenances(target, api, state):
    result = {}
    for item in state.invocations:
        run = get(api, f"repos/{target.repository}/actions/runs/{item.run_id}/attempts/{item.run_attempt}")
        ref = central_reference(run, NORMAL)
        head = item.head_sha
        if item.caller_event == "pull_request":
            pulls = run.get("pull_requests")
            require(isinstance(pulls, list) and len(pulls) <= 100
                    and len([p for p in pulls if p.get("number") == target.pr
                             and p.get("head", {}).get("sha") == head]) == 1,
                    "recovery_provenance_invalid")
        result[(item.run_id, item.run_attempt)] = budget.RunProvenance(
            run.get("repository", {}).get("full_name"), target.pr, head, run.get("path"),
            run.get("event"), ref["path"], ref.get("ref", ref["sha"]), ref["sha"],
            run.get("id"), run.get("run_attempt"), run.get("status"), run.get("conclusion"))
    return result


def receipt_for(target, validated, driver, bundle, invocation):
    attestation = validated.attestation
    original = {key: attestation[key] for key in ("workflow_head", "caller_workflow_path",
        "referenced_workflow_path", "referenced_workflow_sha", "comment_id", "body_sha256", "state_sha256")}
    original.update(run_id=target.run_id, run_attempt=target.run_attempt,
        head_sha=target.head_sha, base_sha=target.base_sha,
        full_diff_sha256=validated.state["full_diff_sha256"], attestation_id=bundle["original_check"]["id"],
        claim_checkpoint_sha256=validated.evidence_digests["claim_checkpoint_sha256"],
        finalized_invocation_sha256=sha256(canonical_json(invocation.to_dict()).encode()),
        artifacts={role: validated.evidence_digests[role] for role in ("handoff", "claim", "candidate")})
    return {"schema": 1, "repository": target.repository, "pr": target.pr, "original": original, "recovery": driver}


def check_payload(receipt):
    original, driver = receipt["original"], receipt["recovery"]
    return {"name": CHECK, "head_sha": driver["workflow_head"], "status": "completed",
        "conclusion": "success",
        "external_id": f"automation-opencode-recovery:{receipt['repository']}:pr:{receipt['pr']}:"
            f"original:{original['run_id']}:{original['run_attempt']}:"
            f"recovery:{driver['run_id']}:{driver['run_attempt']}",
        "output": {"title": "Original OpenCode finalization recovered",
            "summary": "Original invocation finalized with zero additional provider calls.",
            "text": RECEIPT_PREFIX + canonical_json(receipt) + " -->"}}


def expected_check(check, payload):
    return (isinstance(check, dict) and integer(check.get("id"))
        and check.get("app", {}).get("slug") == "github-actions" and check["app"].get("id") == 15368
        and all(check.get(key) == value for key, value in payload.items() if key != "output")
        and isinstance(check.get("output"), dict)
        and all(check["output"].get(key) == value for key, value in payload["output"].items()))


def recover(target, api, workspace, *, driver_run_id, driver_run_attempt, central_sha):
    require(integer(driver_run_id) and integer(driver_run_attempt), "recovery_driver_invalid")
    repository = target.repository
    pr = get(api, f"repos/{repository}/pulls/{target.pr}")
    validate_pr(target, pr)
    driver_run = get(api, f"repos/{repository}/actions/runs/{driver_run_id}/attempts/{driver_run_attempt}")
    driver = driver_identity(target, driver_run, driver_run_id, driver_run_attempt, central_sha)
    comments = all_items(api, f"repos/{repository}/issues/{target.pr}/comments")
    existing = completed_receipt(target, api, comments)
    if existing is not None:
        return {"decision": "already_recovered", "provider_calls": 0, "receipt_id": existing}
    require(driver_run.get("status") == "in_progress", "recovery_driver_not_running")
    bundle = collect_evidence(target, api, workspace, pr, comments)
    validated = validate_evidence(target, bundle, Path(workspace))
    ledger, state = ledger_comment(target, comments)
    events = dismissals(target, api)
    request = replace(validated.request, dismiss_events=events)
    transition = budget.recover_finalize(state, validated.original_claim, request, provenances(target, api, state))
    require(transition.decision == "finalized", "recovery_finalization_conflict")
    expected = budget.render_comment(transition.state, server_url="https://github.com")

    # Fresh comparison immediately before the only ledger mutation.
    validate_pr(target, get(api, f"repos/{repository}/pulls/{target.pr}"))
    fresh_comments = all_items(api, f"repos/{repository}/issues/{target.pr}/comments")
    fresh_ledger, _ = ledger_comment(target, fresh_comments)
    require(comment_identity(fresh_ledger) == comment_identity(ledger), "recovery_ledger_changed")
    fresh_comment, fresh_check = original_publication(target, bundle["original_run"], fresh_comments,
        named_checks(api, repository, bundle["original_run"]["head_sha"], CANONICAL_CHECK))
    require(comment_identity(fresh_comment) == comment_identity(bundle["canonical_comment"])
            and fresh_check == bundle["original_check"], "recovery_canonical_changed")
    require(dismissals(target, api) == events, "recovery_dismissals_changed")
    if transition.mutate_comment:
        try:
            api.request("PATCH", f"repos/{repository}/issues/comments/{ledger['id']}", {"body": expected})
        except (RecoveryError, OSError, TimeoutError):
            pass  # One readback resolves uncertainty; never repeat this PATCH.
    else:
        require(ledger["body"] == expected, "recovery_finalization_conflict")
    readback = get(api, f"repos/{repository}/issues/comments/{ledger['id']}")
    require(comment_identity(readback) == comment_identity(dict(ledger, body=expected)),
            "recovery_ledger_readback_mismatch")
    receipt = receipt_for(target, validated, driver, bundle, transition.state.invocations[-1])
    payload = check_payload(receipt)
    # A prior ambiguous receipt publication from this exact driver is never duplicated.
    prior = named_checks(api, repository, driver["workflow_head"], CHECK)
    same = [c for c in prior if c.get("external_id") == payload["external_id"]]
    require(len(same) <= 1 and (not same or expected_check(same[0], payload)), "recovery_receipt_conflict")
    if not same:
        try:
            api.request("POST", f"repos/{repository}/check-runs", payload)
        except (RecoveryError, OSError, TimeoutError):
            pass
        prior = named_checks(api, repository, driver["workflow_head"], CHECK)
        same = [c for c in prior if c.get("external_id") == payload["external_id"]]
    require(len(same) == 1 and expected_check(same[0], payload), "recovery_receipt_readback_mismatch")
    verified = get(api, f"repos/{repository}/check-runs/{same[0]['id']}")
    require(expected_check(verified, payload), "recovery_receipt_readback_mismatch")
    return {"decision": "finalized", "provider_calls": 0, "receipt_id": verified["id"],
        "original_run_id": target.run_id, "original_run_attempt": target.run_attempt,
        "original_call_count": request.call_count, "original_elapsed_seconds": request.elapsed_seconds}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--checkpoint", required=True)
    args = parser.parse_args()
    result = {"decision": "refused", "provider_calls": 0}
    status = 1
    try:
        require(os.environ.get("GITHUB_SERVER_URL") == "https://github.com"
                and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch", "recovery_driver_invalid")
        target = RecoveryTarget(os.environ.get("GITHUB_REPOSITORY"),
            positive_input(os.environ.get("RECOVERY_PR")), positive_input(os.environ.get("ORIGINAL_RUN_ID")),
            positive_input(os.environ.get("ORIGINAL_RUN_ATTEMPT")), os.environ.get("EXPECTED_HEAD_SHA"),
            os.environ.get("EXPECTED_BASE_SHA"))
        result = recover(target, GitHub(os.environ.get("GH_TOKEN")), Path(args.workspace),
            driver_run_id=positive_input(os.environ.get("GITHUB_RUN_ID")),
            driver_run_attempt=positive_input(os.environ.get("GITHUB_RUN_ATTEMPT")),
            central_sha=os.environ.get("RECOVERY_CENTRAL_SHA"))
        status = 0
    except (RecoveryError, ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
        # Only bounded local reason codes are emitted, never API/source content.
        reason = str(exc) if isinstance(exc, RecoveryError) else "recovery_evidence_invalid"
        result["reason"] = reason if re.fullmatch(r"[a-z_]+", reason) else "recovery_refused"
    finally:
        budget.write_private(Path(args.checkpoint), canonical_json(result).encode() + b"\n")
        print(canonical_json(result))
    return status


if __name__ == "__main__":
    sys.exit(main())
