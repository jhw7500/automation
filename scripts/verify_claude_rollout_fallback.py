#!/usr/bin/env python3
"""Read-only attestation of one managed Claude rollout fallback.

Invoke with Python -I -S -B. No project imports occur before checkout verification.
Remote bodies and consumer files are data, never executable inputs.
"""

from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import importlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from types import ModuleType
from typing import Protocol
from urllib.parse import quote


GITHUB_CLI = Path("/usr/bin") / ("g" + "h")
SHA = re.compile(r"[0-9a-f]{40}\Z")
REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
RECEIPT_KEYS = frozenset({"automatic", "base_sha", "effective_status", "fallback", "fleet", "head_sha",
    "managed_diff_sha256", "pr", "release_commit", "repository", "schema", "verifier_commit"})
AUTOMATIC_KEYS = frozenset({"admitted_state_sha256", "canonical_comment_id", "reason", "review_execution",
    "run_attempt", "run_id", "status"})
FALLBACK_KEYS = frozenset({"budget_status", "canonical_comment_id", "canonical_state_sha256", "driver_commit",
    "filtered_max_severity", "request_comment_id", "request_nonce", "review_execution", "route", "run_attempt", "run_id", "status"})
FLEET_KEYS = frozenset({"base_branch", "changed_paths", "head_repository", "pull_request_url", "rollout_branch"})
REQUIRED_MODULE_PATHS = (
    "scripts/verify_claude_rollout_fallback.py",
    "scripts/rollout_workflow_fleet.py",
    "scripts/workflow_release_bundle.py",
    "scripts/workflow_release_inventory.py",
    "scripts/verify_workflow_release.py",
    "scripts/workflow_catalog.py",
    "scripts/workflow_fleet_git.py",
    "scripts/prepare_workflow_rollout.py",
    "scripts/audit_workflow_fleet.py",
    ".github/actions/claude-rollout-fallback/contract.py",
    ".github/actions/review-invocation-budget/review_invocation_budget.py",
)


class VerificationError(ValueError):
    """A bounded diagnostic that never contains a remote response body."""


@dataclass(frozen=True)
class VerificationRequest:
    automation_root: Path
    release_ref: str
    remote: str
    repository: str
    pr: int
    expected_head: str
    expected_base: str
    output: Path


@dataclass(frozen=True)
class VerifiedModules:
    release_bundle: ModuleType
    inventory: ModuleType
    rollout: ModuleType


class EvidenceProvider(Protocol):
    def default_caller(self) -> dict[str, object]: ...
    def pull_request(self, number: int) -> dict[str, object]: ...
    def issue_comments(self, number: int) -> tuple[dict[str, object], ...]: ...
    def run_attempt(self, run_id: int, attempt: int) -> dict[str, object]: ...
    def run_jobs(self, run_id: int, attempt: int) -> tuple[dict[str, object], ...]: ...
    def check_annotations(self, check_run_id: int) -> tuple[dict[str, object], ...]: ...
    def required_checks(self, head_sha: str) -> tuple[dict[str, object], ...]: ...
    def workflow_runs(self) -> tuple[dict[str, object], ...]: ...
    def rollout_prs(self, branch: str) -> tuple[dict[str, object], ...]: ...
    def branch_sha(self, branch: str) -> str: ...
    def consumer_snapshot(self, request: VerificationRequest, modules: VerifiedModules): ...


def _git_bytes(root: Path, *args: str) -> bytes:
    environment = {
        "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
        "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/bin/false", "SSH_ASKPASS": "/bin/false",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_NO_REPLACE_OBJECTS": "1",
    }
    if args and args[0] == "fetch":
        # Only the fresh disposable repository uses this path. Supply credentials
        # in the child environment, never command arguments or diagnostics.
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if token:
            encoded = base64.b64encode(("x-access-token:" + token).encode()).decode()
            environment.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.https://github.com/.extraheader",
                               GIT_CONFIG_VALUE_0="AUTHORIZATION: basic " + encoded)
    try:
        result = subprocess.run(
            ["/usr/bin/git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
             "-c", "submodule.recurse=false", "-c", "core.untrackedCache=false", *args],
            cwd=root, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=60, check=True,
        )
        return result.stdout
    except (OSError, ValueError, subprocess.SubprocessError):
        raise VerificationError("git_read_failed") from None


def git_stdout(root: Path, *args: str) -> str:
    try:
        return _git_bytes(root, *args).decode("utf-8").rstrip("\n")
    except UnicodeError:
        raise VerificationError("git_read_failed") from None


def _no_symlinks(path: Path) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise OSError("invalid path")
    for node in (path, *path.parents):
        if stat.S_ISLNK(node.lstat().st_mode):
            raise OSError("symlink path")


def verify_source_root(root: Path, driver_commit: str) -> None:
    try:
        _no_symlinks(root)
        if SHA.fullmatch(driver_commit) is None or git_stdout(root, "rev-parse", "HEAD") != driver_commit:
            raise ValueError
        if Path(git_stdout(root, "rev-parse", "--show-toplevel")) != root:
            raise ValueError
        def verify_object(oid, kind):
            raw = _git_bytes(root, "cat-file", kind, oid)
            actual = hashlib.sha1(kind.encode() + b" " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
            if actual != oid:
                raise ValueError
            return raw
        commit = verify_object(driver_commit, "commit")
        tree = commit.splitlines()[0].decode("ascii")
        if not tree.startswith("tree ") or SHA.fullmatch(tree[5:]) is None:
            raise ValueError
        verify_object(tree[5:], "tree")
        # Read actual bytes: Git status can trust assume-unchanged or invoke clean filters.
        records = git_stdout(root, "ls-tree", "-rtz", driver_commit).split("\0")
        tracked = {}
        for record in filter(None, records):
            metadata, name = record.split("\t", 1)
            mode, kind, oid = metadata.split()
            if kind == "tree" and mode == "040000":
                verify_object(oid, "tree")
                continue
            node = root / name
            _no_symlinks(node)
            observed = node.lstat()
            if kind != "blob" or mode not in {"100644", "100755"} or not stat.S_ISREG(observed.st_mode):
                raise ValueError
            if bool(observed.st_mode & 0o111) != (mode == "100755"):
                raise ValueError
            payload = node.read_bytes()
            actual = hashlib.sha1(b"blob " + str(len(payload)).encode() + b"\0" + payload).hexdigest()
            if actual != oid:
                raise ValueError
            tracked[name] = mode
        if any(tracked.get(name) != "100644" for name in REQUIRED_MODULE_PATHS):
            raise ValueError
        # No exclude flags: ignored Python shadows are untrusted too.
        if git_stdout(root, "ls-files", "--others", "-z"):
            raise ValueError
        if git_stdout(root, "diff-index", "--cached", "--name-only", driver_commit, "--"):
            raise ValueError
    except (OSError, ValueError, VerificationError):
        raise VerificationError("verifier_root_invalid") from None


def require_trusted_dependency_directory(path: Path) -> None:
    try:
        _no_symlinks(path)
        for node in (path, *path.parents):
            observed = node.lstat()
            if not stat.S_ISDIR(observed.st_mode) or observed.st_uid != 0 or observed.st_mode & 0o022:
                raise ValueError
    except (OSError, ValueError):
        raise VerificationError("verifier_dependency_invalid") from None


def load_trusted_yaml() -> None:
    # Enumerate paths only; never run site.main/addsitedir or execute .pth files.
    import site

    for candidate in site.getsitepackages():
        parent = Path(candidate)
        if not parent.exists():
            continue
        require_trusted_dependency_directory(parent)
        specification = importlib.machinery.PathFinder.find_spec("yaml", [str(parent)])
        if specification is None:
            continue
        try:
            origin = Path(specification.origin or "")
            if not origin.is_relative_to(parent) or specification.submodule_search_locations is None:
                raise ValueError
            package = origin.parent
            require_trusted_dependency_directory(package)
            for node in package.rglob("*"):
                observed = node.lstat()
                if (stat.S_ISLNK(observed.st_mode) or observed.st_uid != 0
                        or observed.st_mode & 0o022
                        or not (stat.S_ISREG(observed.st_mode) or stat.S_ISDIR(observed.st_mode))):
                    raise ValueError
            existing = sys.modules.get("yaml")
            if existing is not None and Path(existing.__file__) != origin:
                raise ValueError
            if str(parent) not in sys.path:
                sys.path.append(str(parent))
            importlib.import_module("yaml")
            return
        except (OSError, ValueError, ImportError):
            raise VerificationError("verifier_dependency_invalid") from None
    raise VerificationError("verifier_dependency_invalid")


def load_verified_modules(root: Path) -> VerifiedModules:
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root))
    load_trusted_yaml()
    try:
        modules = tuple(importlib.import_module("scripts." + name) for name in (
            "workflow_release_bundle", "workflow_release_inventory", "rollout_workflow_fleet"))
        for module in modules:
            if not Path(module.__file__).is_relative_to(root):
                raise ImportError
        return VerifiedModules(*modules)
    except (ImportError, OSError, ValueError):
        raise VerificationError("verifier_root_invalid") from None


def canonical_json_bytes(payload: dict[str, object]) -> bytes:
    return (json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("ascii")


def require_private_output_parent(path: Path) -> None:
    try:
        _no_symlinks(path)
        observed = path.lstat()
        if (not stat.S_ISDIR(observed.st_mode) or observed.st_uid != os.getuid()
                or stat.S_IMODE(observed.st_mode) != 0o700):
            raise ValueError
    except (OSError, ValueError):
        raise VerificationError("receipt_parent_invalid") from None


def require_private_regular_owned(path: Path) -> None:
    try:
        observed = path.lstat()
        if (not stat.S_ISREG(observed.st_mode) or observed.st_uid != os.getuid()
                or stat.S_IMODE(observed.st_mode) != 0o600):
            raise ValueError
    except (OSError, ValueError):
        raise VerificationError("receipt_file_invalid") from None


def write_receipt(path: Path, payload: dict[str, object]) -> None:
    require_private_output_parent(path.parent)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    created = False
    try:
        try:
            path.lstat()
        except FileNotFoundError:
            pass
        else:
            raise VerificationError("receipt_path_exists")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        created = True
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical_json_bytes(payload))
            os.fchmod(stream.fileno(), 0o600)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            raise VerificationError("receipt_path_exists") from None
    except (OSError, ValueError) as error:
        if isinstance(error, VerificationError):
            raise
        raise VerificationError("receipt_write_failed") from None
    finally:
        if created:
            temporary.unlink(missing_ok=True)
    require_private_regular_owned(path)


def validate_request(request: VerificationRequest) -> None:
    if (not isinstance(request.repository, str) or REPOSITORY.fullmatch(request.repository) is None
            or type(request.pr) is not int or not 1 <= request.pr < 2**53
            or SHA.fullmatch(request.expected_head) is None or SHA.fullmatch(request.expected_base) is None
            or re.fullmatch(r"v[0-9]+(?:\.[0-9]+)+", request.release_ref) is None
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", request.remote) is None):
        raise VerificationError("request_invalid")
    if (not request.automation_root.is_absolute() or not request.output.is_absolute()
            or request.output.resolve().is_relative_to(request.automation_root.resolve())):
        raise VerificationError("request_invalid")


@contextmanager
def _fixed_tool_path():
    previous = os.environ.get("PATH")
    os.environ["PATH"] = "/usr/bin:/bin"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = previous


def verify_fleet_request(request: VerificationRequest, evidence: EvidenceProvider,
                         modules: VerifiedModules) -> dict[str, object]:
    with _fixed_tool_path():
        return _verify_fleet_request(request, evidence, modules)


def _verify_fleet_request(request, evidence, modules):
    rollout = modules.rollout
    try:
        with modules.release_bundle.materialize_release_bundle(
                request.automation_root, request.release_ref, remote=request.remote) as bundle:
            with evidence.consumer_snapshot(request, modules) as snapshot:
                repo = request.repository.split("/", 1)[1]
                if snapshot.base_sha != request.expected_base:
                    raise ValueError
                plan = rollout.render_rollout_plan(snapshot, bundle, repo, bootstrap=False)
                if plan.status != "drift":
                    raise ValueError
                changed = tuple(sorted(change.path.as_posix() for change in plan.changes))
                rollout.validate_commit_tree(snapshot, request.expected_head, request.expected_base, plan)
                branch = rollout.rollout_branch(bundle.ref, snapshot.base_branch, snapshot.default_branch)
                if evidence.branch_sha(branch) != request.expected_head:
                    raise ValueError
                observed = evidence.pull_request(request.pr)
                candidates = evidence.rollout_prs(branch)
                if len(candidates) != 1 or candidates[0] != observed:
                    raise ValueError
                url = f"https://github.com/{request.repository}/pull/{request.pr}"
                if (observed["number"] != request.pr or observed["html_url"] != url
                        or observed["state"] != "open" or observed["merged"] is not False
                        or observed["head"]["repo"]["fork"] is not False
                        or observed["base"]["repo"]["full_name"] != request.repository
                        or observed["base"]["sha"] != request.expected_base):
                    raise ValueError
                pr = rollout.PullRequest(
                    observed["number"], observed["html_url"], observed["state"].upper(),
                    observed["base"]["ref"], observed["head"]["ref"],
                    observed["head"]["repo"]["full_name"], observed["head"]["sha"],
                    observed["title"], observed["body"],
                )
                rollout.attest_pull_request(snapshot, bundle.ref, bundle.commit,
                                           request.expected_head, changed, pr)
                if evidence.branch_sha(branch) != request.expected_head:
                    raise ValueError
                diff = git_stdout(snapshot.path, "diff", "--no-ext-diff", "--no-textconv",
                                  "--find-renames=50%", "--ignore-submodules=none", "-U3",
                                  request.expected_base + ".." + request.expected_head)
                return {"release_commit": bundle.commit,
                        "_default_branch": snapshot.default_branch,
                        "managed_diff_sha256": hashlib.sha256((diff + "\n").encode()).hexdigest(),
                        "fleet": {"base_branch": pr.base, "changed_paths": list(changed),
                                  "head_repository": pr.head_repo, "pull_request_url": pr.url,
                                  "rollout_branch": branch}}
    except Exception:
        # Includes bounded release/catalog/transport errors; never relay remote text.
        raise VerificationError("fleet_attestation_failed") from None


def parse_default_caller_pin(document: object) -> str:
    try:
        if (type(document) is not dict or document.get("type") != "file"
                or document.get("path") != ".github/workflows/claude.yml"
                or document.get("encoding") != "base64"
                or document.get("target") or document.get("submodule_git_url")
                or type(document.get("content")) is not str or len(document["content"]) > 100000):
            raise ValueError
        content = base64.b64decode("".join(document["content"].split()), validate=True).decode("utf-8")
        pins = re.findall(r"^    uses: jhw7500/automation/\.github/workflows/claude\.yml@([0-9a-f]{40})(?: +#[^\r\n]*)?$",
                          content, re.MULTILINE)
        if len(pins) != 1:
            raise ValueError
        return pins[0]
    except (ValueError, TypeError, UnicodeError):
        raise VerificationError("driver_pin_invalid") from None


def _driver_module(root: Path, relative: str, name: str) -> ModuleType:
    try:
        spec = importlib.util.spec_from_file_location(name, root / relative)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    except (OSError, ImportError, ValueError):
        raise VerificationError("verifier_root_invalid") from None


def _contract(root: Path) -> ModuleType:
    return _driver_module(root, ".github/actions/claude-rollout-fallback/contract.py", "_verified_fallback_contract")


def _budget(root: Path) -> ModuleType:
    return _driver_module(root, ".github/actions/review-invocation-budget/review_invocation_budget.py", "_verified_fallback_budget")


def _authenticated_bot(comment: dict[str, object]) -> bool:
    return (type(comment.get("user")) is dict and comment["user"].get("login") == "github-actions[bot]"
            and comment["user"].get("type") == "Bot")


def _request_evidence(request: VerificationRequest, evidence: EvidenceProvider):
    contract = _contract(request.automation_root)
    comments = evidence.issue_comments(request.pr)
    if type(comments) is not tuple or not all(type(item) is dict for item in comments):
        raise VerificationError("evidence_invalid")
    candidates = []
    for comment in comments:
        try:
            parsed = contract.parse_request_body(comment.get("body"))
        except contract.ContractError:
            continue
        if parsed.repository == request.repository and parsed.pr == request.pr:
            candidates.append((comment, parsed))
    if len(candidates) != 1:
        raise VerificationError("request_evidence_invalid")
    comment, parsed = candidates[0]
    contract.require_exact_comment(comment, {"comment": comment}, parsed)
    if parsed.expected_head_sha != request.expected_head or parsed.expected_base_sha != request.expected_base:
        raise VerificationError("request_evidence_invalid")
    contract.require_exact_open_same_repository_pr(evidence.pull_request(request.pr), parsed)
    return contract, comments, comment, parsed


def _ledger(request: VerificationRequest, comments):
    budget = _budget(request.automation_root)
    candidates = [comment for comment in comments if _authenticated_bot(comment)
                  and isinstance(comment.get("body"), str)
                  and comment["body"].startswith(budget.MARKERS["claude"] + "\n")]
    if len(candidates) != 1:
        raise VerificationError("budget_evidence_invalid")
    body = candidates[0]["body"]
    ledger = budget.parse_ledger(body, repository=request.repository, pr=request.pr, reviewer="claude")
    if ledger is None:
        raise VerificationError("budget_evidence_invalid")
    return ledger


def _fallback_invocation(ledger, parsed, comment):
    candidates = [entry for entry in ledger.invocations if entry.route.kind == "default_branch_rollout_fallback"
                  and entry.head_sha == parsed.expected_head_sha]
    if len(candidates) != 1:
        raise VerificationError("budget_evidence_invalid")
    entry = candidates[0]
    route = entry.route
    if (entry.status != "finalized" or entry.outcome != "success" or entry.call_count != 1
            or entry.remaining_finding_ids or entry.full_diff_sha256 != parsed.managed_diff_sha256
            or route.request_comment_id != comment["id"] or route.request_nonce != parsed.nonce
            or route.original_run_id != parsed.original_run_id or route.original_run_attempt != parsed.original_run_attempt
            or route.expected_base_sha != parsed.expected_base_sha or route.release_commit != parsed.release_commit
            or route.managed_diff_sha256 != parsed.managed_diff_sha256
            or any(item.run_id == parsed.original_run_id for item in ledger.invocations)):
        raise VerificationError("budget_evidence_invalid")
    return entry


def verify_automatic_failure(request: VerificationRequest, evidence: EvidenceProvider) -> dict[str, object]:
    try:
        contract, comments, comment, parsed = _request_evidence(request, evidence)
        run = evidence.run_attempt(parsed.original_run_id, parsed.original_run_attempt)
        if any(type(run.get(key)) is not int for key in ("id", "run_attempt")):
            raise ValueError
        contract.require_exact_automatic_run(run, parsed)
        contract.require_causal_order(run, comment)
        jobs = evidence.run_jobs(parsed.original_run_id, parsed.original_run_attempt)
        contract.require_failed_before_provider({"total_count": len(jobs), "jobs": list(jobs)})
        if any(type(job.get("run_attempt")) is not int or job.get("run_id") != parsed.original_run_id
               or job.get("run_attempt") != parsed.original_run_attempt for job in jobs):
            raise ValueError
        job = next(job for job in jobs if re.search(r"(?:^| / )claude-review\Z", job["name"]))
        check_url = job.get("check_run_url", "")
        match = re.fullmatch(r"https://api\.github\.com/repos/" + re.escape(request.repository) + r"/check-runs/([1-9][0-9]*)", check_url)
        if match is None:
            raise ValueError
        annotations = evidence.check_annotations(int(match[1]))
        errors = [item for item in annotations if item.get("annotation_level") == "failure"]
        if len(errors) != 1 or errors[0].get("message") != "workflow_validation_mismatch":
            raise ValueError
        entry = _fallback_invocation(_ledger(request, comments), parsed, comment)
        return {"canonical_comment_id": entry.route.automatic_comment_id,
                "admitted_state_sha256": entry.route.automatic_state_sha256,
                "reason": "workflow_validation_mismatch", "review_execution": "not_performed",
                "run_attempt": parsed.original_run_attempt, "run_id": parsed.original_run_id, "status": "FAILED"}
    except Exception:
        raise VerificationError("automatic_evidence_invalid") from None


def verify_fallback_success(request: VerificationRequest, evidence: EvidenceProvider,
                            automatic: dict[str, object], driver_commit: str) -> dict[str, object]:
    try:
        contract, comments, comment, parsed = _request_evidence(request, evidence)
        entry = _fallback_invocation(_ledger(request, comments), parsed, comment)
        if (entry.route.automatic_comment_id != automatic["canonical_comment_id"]
                or entry.route.automatic_state_sha256 != automatic["admitted_state_sha256"]
                or entry.referenced_workflow_sha != driver_commit):
            raise ValueError
        selected = [run for run in evidence.workflow_runs() if run.get("id") == entry.run_id]
        if len(selected) != 1 or selected[0].get("run_attempt") != entry.run_attempt:
            raise ValueError
        run = evidence.run_attempt(entry.run_id, entry.run_attempt)
        if (type(run.get("id")) is not int or type(run.get("run_attempt")) is not int
                or run.get("id") != entry.run_id or run.get("run_attempt") != entry.run_attempt
                or run.get("event") != "issue_comment" or run.get("status") != "completed"
                or run.get("conclusion") != "success" or run.get("path") != ".github/workflows/claude.yml"
                or run.get("repository", {}).get("full_name") != request.repository
                or run.get("actor", {}).get("login") != comment["user"]["login"]
                or contract._github_time(run.get("created_at")) < contract._github_time(comment.get("created_at"))):
            raise ValueError
        references = run.get("referenced_workflows")
        if type(references) is not list or not 1 <= len(references) <= 2:
            raise ValueError
        nested = [ref for ref in references if type(ref) is dict and ref.get("path") == entry.referenced_workflow_path]
        if len(nested) != 1 or nested[0].get("sha") != driver_commit:
            raise ValueError
        for ref in references:
            if (type(ref) is not dict or ref.get("sha") != driver_commit
                    or ref.get("path") not in {
                        f"jhw7500/automation/.github/workflows/claude.yml@{driver_commit}",
                        f"jhw7500/automation/.github/workflows/claude-code-review.yml@{driver_commit}"}):
                raise ValueError
        if len({ref["path"] for ref in references}) != len(references):
            raise ValueError
        jobs = evidence.run_jobs(entry.run_id, entry.run_attempt)
        providers = []
        for job in jobs:
            if (type(job.get("run_attempt")) is not int
                    or job.get("run_id") != entry.run_id or job.get("run_attempt") != entry.run_attempt
                    or job.get("status") != "completed" or job.get("conclusion") not in {"success", "skipped"}):
                raise ValueError
            for step in job.get("steps", []):
                if step.get("name") in {"Run Claude Code Review", "Run Claude Code"} and step.get("conclusion") != "skipped":
                    providers.append((job, step))
        if len(providers) != 1 or providers[0][1].get("name") != "Run Claude Code Review":
            raise ValueError
        job, provider = providers[0]
        if provider.get("status") != "completed" or provider.get("conclusion") != "success":
            raise ValueError
        for name in ("Claim Claude review budget", "Finalize Claude review budget"):
            steps = [step for step in job["steps"] if step.get("name") == name]
            if len(steps) != 1 or steps[0].get("status") != "completed" or steps[0].get("conclusion") != "success":
                raise ValueError
        states = contract._state_comments(list(comments))
        if len(states) != 1:
            raise ValueError
        canonical, state, raw = states[0]
        route = {"managed_diff_sha256": parsed.managed_diff_sha256, "original_failed_run_id": parsed.original_run_id,
                 "release_commit": parsed.release_commit, "request_comment_id": comment["id"],
                 "reviewed_base_sha": parsed.expected_base_sha, "route": "default_branch_rollout_fallback"}
        if (canonical.get("id") != automatic["canonical_comment_id"]
                or any(type(state.get(key)) is not int for key in ("schema", "quality_schema", "pr", "run_id", "run_attempt"))
                or set(state) != (contract.FAILURE_STATE_KEYS - {"failure_reason"}) | {"route"}
                or state.get("schema") != 3 or state.get("quality_schema") != 1
                or state.get("reviewer") != "claude" or state.get("pr") != request.pr
                or state.get("run_id") != entry.run_id or state.get("run_attempt") != entry.run_attempt
                or state.get("attempt_head") != request.expected_head or state.get("successful_head") != request.expected_head
                or state.get("attempt_status") != "success" or state.get("review_execution") != "performed"
                or state.get("diff_mode") != "full" or state.get("full_diff_sha256") != parsed.managed_diff_sha256
                or state.get("route") != route or state.get("filtered_max_severity") != "none"
                or any(type(state.get(key)) is not int or state[key] != 0 for key in ("accepted_count", "filtered_count", "normalized_count"))):
            raise ValueError
        return {"budget_status": "finalized", "canonical_comment_id": canonical["id"],
                "canonical_state_sha256": hashlib.sha256(raw).hexdigest(), "driver_commit": driver_commit,
                "filtered_max_severity": "none", "request_comment_id": comment["id"], "request_nonce": parsed.nonce,
                "review_execution": "performed", "route": "default_branch_rollout_fallback",
                "run_attempt": entry.run_attempt, "run_id": entry.run_id, "status": "CLEAN"}
    except Exception:
        raise VerificationError("fallback_evidence_invalid") from None


def require_required_checks_clean(checks: tuple[dict[str, object], ...]) -> None:
    if type(checks) is not tuple or any(type(check) is not dict or check.get("status") != "completed"
            or check.get("conclusion") not in {"success", "neutral", "skipped"} for check in checks):
        raise VerificationError("required_check_failed")


def build_receipt(request: VerificationRequest, fleet: dict[str, object], automatic: dict[str, object],
                  fallback: dict[str, object], driver_commit: str) -> dict[str, object]:
    return {"automatic": automatic, "base_sha": request.expected_base, "effective_status": "CLEAN",
            "fallback": fallback, "fleet": fleet["fleet"], "head_sha": request.expected_head,
            "managed_diff_sha256": fleet["managed_diff_sha256"], "pr": request.pr,
            "release_commit": fleet["release_commit"], "repository": request.repository,
            "schema": 1, "verifier_commit": driver_commit}


def verify(request: VerificationRequest, evidence_provider: EvidenceProvider) -> dict[str, object]:
    validate_request(request)
    evidence = evidence_provider
    driver = parse_default_caller_pin(evidence.default_caller())
    verify_source_root(request.automation_root, driver)
    modules = load_verified_modules(request.automation_root)
    fleet = verify_fleet_request(request, evidence, modules)
    if driver == fleet["release_commit"]:
        raise VerificationError("driver_pin_invalid")
    try:
        _, _, _, parsed = _request_evidence(request, evidence)
        if parsed.release_commit != fleet["release_commit"] or parsed.managed_diff_sha256 != fleet["managed_diff_sha256"]:
            raise ValueError
    except Exception:
        raise VerificationError("request_evidence_invalid") from None
    automatic = verify_automatic_failure(request, evidence)
    fallback = verify_fallback_success(request, evidence, automatic, driver)
    require_required_checks_clean(evidence.required_checks(request.expected_head))
    try:
        contract, _, _, parsed = _request_evidence(request, evidence)
        observed = evidence.pull_request(request.pr)
        contract.require_exact_open_same_repository_pr(observed, parsed)
        rollout = modules.rollout
        base = fleet["fleet"]["base_branch"]
        default = fleet["_default_branch"]
        if (observed["title"] != rollout.pr_title(request.release_ref, base, default)
                or observed["body"] != rollout.pr_body(request.release_ref, fleet["release_commit"],
                    fleet["fleet"]["changed_paths"], base, default)
                or observed["head"]["ref"] != fleet["fleet"]["rollout_branch"]
                or observed["base"]["ref"] != base
                or evidence.branch_sha(fleet["fleet"]["rollout_branch"]) != request.expected_head):
            raise ValueError
    except Exception:
        raise VerificationError("fleet_attestation_failed") from None
    return build_receipt(request, fleet, automatic, fallback, driver)


def _unique_json(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


class GitHubEvidenceProvider:
    """Fixed-origin bounded REST reads and disposable object-only Git fetches."""

    def __init__(self, repository: str):
        if REPOSITORY.fullmatch(repository) is None:
            raise VerificationError("request_invalid")
        self.repository = repository
        self.prefix = "repos/" + repository
        self.base_branch = None
        self._cache = {}

    def _json(self, endpoint: str, *, missing: bool = False):
        if not endpoint.startswith(self.prefix + "/") and endpoint != self.prefix:
            raise VerificationError("evidence_invalid")
        environment = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
                       "GH_HOST": "github.com", "GH_PROMPT_DISABLED": "1"}
        for key in ("HOME", "GH_TOKEN", "GITHUB_TOKEN", "GH_CONFIG_DIR", "XDG_CONFIG_HOME"):
            if key in os.environ:
                environment[key] = os.environ[key]
        try:
            result = subprocess.run([str(GITHUB_CLI), "api", "--hostname", "github.com", "--method", "GET",
                                     "--include", endpoint], env=environment, capture_output=True, timeout=30)
            raw = result.stdout
            if len(raw) > 8 * 1024 * 1024:
                raise ValueError
            # gh's include header is CRLF on supported releases; normalize only headers.
            separator = b"\r\n\r\n" if b"\r\n\r\n" in raw else b"\n\n"
            headers, body = raw.split(separator, 1)
            status = headers.splitlines()[0].split()[1]
            if missing and status == b"404":
                return None
            if result.returncode or status != b"200":
                raise ValueError
            return json.loads(body, object_pairs_hook=_unique_json,
                              parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
        except (OSError, ValueError, IndexError, UnicodeError, subprocess.SubprocessError):
            raise VerificationError("evidence_read_failed") from None

    def _object(self, endpoint):
        value = self._json(endpoint)
        if type(value) is not dict:
            raise VerificationError("evidence_invalid")
        return value

    def _pages(self, endpoint, *, key=None, pages=10):
        if type(pages) is not int or not 1 <= pages <= 10:
            raise VerificationError("evidence_invalid")
        collected = []
        for number in range(1, pages + 1):
            separator = "&" if "?" in endpoint else "?"
            response = self._json(f"{endpoint}{separator}per_page=100&page={number}")
            values = response
            if key is not None:
                if type(response) is not dict or type(response.get("total_count")) is not int:
                    raise VerificationError("evidence_invalid")
                values = response.get(key)
            if type(values) is not list or len(values) > 100 or not all(type(item) is dict for item in values):
                raise VerificationError("evidence_invalid")
            collected.extend(values)
            if len(values) < 100:
                if key is not None and response["total_count"] != len(collected):
                    raise VerificationError("evidence_invalid")
                return tuple(collected)
        raise VerificationError("evidence_overflow")

    def _cached_pages(self, endpoint, *, key=None, pages=10):
        identity = (endpoint, key, pages)
        if identity not in self._cache:
            self._cache[identity] = self._pages(endpoint, key=key, pages=pages)
        return self._cache[identity]

    def default_caller(self):
        metadata = self._object(self.prefix)
        branch = metadata.get("default_branch")
        if type(branch) is not str or not branch:
            raise VerificationError("evidence_invalid")
        sha = self.branch_sha(branch)
        return self._object(self.prefix + "/contents/.github/workflows/claude.yml?ref=" + sha)

    def branch_sha(self, branch):
        value = self._object(self.prefix + "/branches/" + quote(branch, safe=""))
        if type(value.get("commit")) is not dict:
            raise VerificationError("evidence_invalid")
        sha = value["commit"].get("sha")
        if type(sha) is not str or SHA.fullmatch(sha) is None:
            raise VerificationError("evidence_invalid")
        return sha

    def pull_request(self, number):
        if type(number) is not int or number < 1:
            raise VerificationError("evidence_invalid")
        result = self._object(self.prefix + f"/pulls/{number}")
        if (type(result.get("number")) is not int or result["number"] != number
                or type(result.get("base")) is not dict or type(result["base"].get("ref")) is not str):
            raise VerificationError("evidence_invalid")
        self.base_branch = result["base"]["ref"]
        return result

    def rollout_prs(self, branch):
        values = self._pages(self.prefix + "/pulls?state=all&head=" + quote(self.repository.split("/")[0] + ":" + branch, safe=""))
        # List and detail APIs differ in incidental fields. Fetch the detail for each identity.
        return tuple(self.pull_request(item.get("number")) for item in values)

    def issue_comments(self, number):
        return self._cached_pages(self.prefix + f"/issues/{number}/comments")

    def run_attempt(self, run_id, attempt):
        return self._object(self.prefix + f"/actions/runs/{run_id}/attempts/{attempt}")

    def run_jobs(self, run_id, attempt):
        return self._cached_pages(self.prefix + f"/actions/runs/{run_id}/attempts/{attempt}/jobs", key="jobs", pages=1)

    def check_annotations(self, check_run_id):
        return self._cached_pages(self.prefix + f"/check-runs/{check_run_id}/annotations")

    def workflow_runs(self):
        return self._cached_pages(self.prefix + "/actions/workflows/claude.yml/runs?event=issue_comment", key="workflow_runs")

    def required_checks(self, head_sha):
        try:
            if type(self.base_branch) is not str or not self.base_branch:
                raise ValueError
            branch = quote(self.base_branch, safe="")
            protection = self._json(self.prefix + "/branches/" + branch + "/protection/required_status_checks", missing=True)
            required = set()
            if protection is not None:
                if type(protection) is not dict or type(protection.get("checks")) is not list or len(protection["checks"]) >= 100:
                    raise ValueError
                for item in protection["checks"]:
                    required.add((item["context"], item.get("app_id")))
                if type(protection.get("contexts")) is not list or len(protection["contexts"]) >= 100:
                    raise ValueError
                for name in protection["contexts"]:
                    if not any(context == name for context, _ in required):
                        required.add((name, None))
            rules = self._pages(self.prefix + "/rules/branches/" + branch)
            for rule in rules:
                if rule.get("type") in {"workflows", "required_workflows"}:
                    # Required workflow identities cannot safely be inferred from
                    # status-context names; leave the native gate closed.
                    raise ValueError
                if rule.get("type") == "required_status_checks":
                    contexts = rule["parameters"]["required_status_checks"]
                    if type(contexts) is not list or len(contexts) >= 100:
                        raise ValueError
                    required.update((item["context"], item.get("integration_id")) for item in contexts)
            if any(type(name) is not str or not name or (app is not None and type(app) is not int) for name, app in required):
                raise ValueError
            checks = self._pages(self.prefix + f"/commits/{head_sha}/check-runs?filter=latest", key="check_runs")
            statuses = self._pages(self.prefix + f"/commits/{head_sha}/statuses")
            result = []
            for name, app in sorted(required, key=lambda item: (item[0], str(item[1]))):
                matches = [check for check in checks if check.get("name") == name
                           and (app in {None, -1} or check.get("app", {}).get("id") == app)]
                if len(matches) == 1:
                    if matches[0].get("head_sha") != head_sha:
                        raise ValueError
                    result.append(matches[0])
                elif matches:
                    raise ValueError
                legacy = [status for status in statuses if status.get("context") == name]
                if app in {None, -1} and legacy:
                    if legacy[0].get("state") != "success":
                        raise ValueError
                    result.append({"name": name, "status": "completed", "conclusion": "success"})
                elif not matches:
                    raise ValueError
            return tuple(result)
        except Exception:
            raise VerificationError("required_check_failed") from None

    @contextmanager
    def consumer_snapshot(self, request, modules):
        try:
            metadata = self._object(self.prefix)
            pr = self.pull_request(request.pr)
            default = metadata["default_branch"]
            base_branch = pr["base"]["ref"]
            if pr["base"]["sha"] != request.expected_base:
                raise ValueError
            with tempfile.TemporaryDirectory(prefix="claude-fleet-") as temporary:
                root = Path(temporary) / self.repository.split("/", 1)[1]
                root.mkdir()
                git_stdout(root, "init", "--quiet", "--template=")
                git_stdout(root, "fetch", "--no-tags", "--no-recurse-submodules",
                           "https://github.com/" + self.repository + ".git", request.expected_base, request.expected_head)
                # Materialize only regular .github data directly from objects. No
                # checkout: consumer attributes, filters, hooks, and binaries never run.
                for record in filter(None, git_stdout(root, "ls-tree", "-rz", request.expected_base, "--", ".github").split("\0")):
                    metadata_record, relative = record.split("\t", 1)
                    mode, kind, oid = metadata_record.split()
                    parts = Path(relative).parts
                    if mode != "100644" or kind != "blob" or not parts or parts[0] != ".github" or ".." in parts:
                        raise ValueError
                    path = root / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(_git_bytes(root, "cat-file", "blob", oid))
                secrets = self._pages(self.prefix + "/actions/secrets", key="secrets")
                variables = self._pages(self.prefix + "/actions/variables", key="variables")
                labels = self._pages(self.prefix + "/labels")
                yield modules.rollout.RepositorySnapshot(root, default, request.expected_base,
                    frozenset(item["name"] for item in secrets), frozenset(item["name"] for item in variables),
                    base_branch=base_branch, label_names=frozenset(item["name"] for item in labels))
        except Exception:
            raise VerificationError("fleet_attestation_failed") from None


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    for name in ("automation-root", "release-ref", "remote", "repository", "pr",
                 "expected-head", "expected-base", "output"):
        result.add_argument("--" + name, required=True,
                            type=Path if name in {"automation-root", "output"} else int if name == "pr" else str)
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        request = VerificationRequest(**vars(arguments))
        validate_request(request)
        require_private_output_parent(request.output.parent)
        if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
            raise VerificationError("verifier_root_invalid")
        receipt = verify(request, GitHubEvidenceProvider(request.repository))
        write_receipt(request.output, receipt)
        return 0
    except VerificationError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
