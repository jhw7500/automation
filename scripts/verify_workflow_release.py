#!/usr/bin/env python3
"""Verify that a workflow release tag is the intended, secure Git artifact."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys

import yaml

from scripts.workflow_catalog import (
    CatalogError,
    WorkflowCatalog,
    extract_caller_jobs,
    load_catalog,
    load_fleet_config,
)


CHECKOUT_ACTION = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
CACHE_ACTION = "actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9"
OPENCODE_VERSION = "1.18.17"
OPENCODE_ARCHIVE_SHA256 = (
    "3f14a4c61c7f6b0d3b6d933d1d212e64e19683eba6fa453ad98e46303afe144a"
)
SETUP_GEMINI_AUTH = (
    "jhw7500/automation/.github/actions/setup-gemini-auth@"
    "2254f13aab44585c78954d20749f4fb677a8c2f1"
)
GEMINI_SECRETS = {"APP_PRIVATE_KEY", "GEMINI_API_KEY"}


class ReleaseVerificationError(RuntimeError):
    """The requested release is absent, points elsewhere, or violates invariants."""


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ReleaseVerificationError(detail or f"git {' '.join(args)} failed")
    return result.stdout


def resolve_commit(repo: Path, revision: str) -> str:
    return git(repo, "rev-parse", "--verify", f"{revision}^{{commit}}").strip()


def require_annotated_tag(repo: Path, ref: str) -> None:
    object_type = git(repo, "cat-file", "-t", f"refs/tags/{ref}").strip()
    if object_type != "tag":
        raise ReleaseVerificationError(f"release {ref} must be an annotated tag")


def verify_remote_tag(repo: Path, remote: str, ref: str, expected: str) -> None:
    result = git(
        repo,
        "ls-remote",
        "--tags",
        remote,
        f"refs/tags/{ref}",
        f"refs/tags/{ref}^{{}}",
    )
    refs = {}
    for line in result.splitlines():
        sha, name = line.split(maxsplit=1)
        refs[name] = sha
    remote_commit = refs.get(f"refs/tags/{ref}^{{}}") or refs.get(f"refs/tags/{ref}")
    if remote_commit is None:
        raise ReleaseVerificationError(f"remote tag {ref} is missing from {remote}")
    if remote_commit != expected:
        raise ReleaseVerificationError(
            f"remote tag {ref} points to {remote_commit}, expected commit {expected}"
        )


def verify_opencode_runtime(job: dict, step_name: str, workflow_name: str) -> dict:
    """Require a digest-verified CLI and the restricted repository token path."""
    try:
        cache = next(
            item
            for item in job["steps"]
            if item.get("name") == "Cache pinned OpenCode CLI archive"
        )
        install = next(
            item
            for item in job["steps"]
            if item.get("name") == "Install pinned OpenCode CLI"
        )
        run_step = next(item for item in job["steps"] if item.get("name") == step_name)
    except (KeyError, TypeError, StopIteration) as exc:
        raise ReleaseVerificationError(
            f"{workflow_name} pinned OpenCode runtime structure is missing"
        ) from exc

    job_env = job.get("env", {})
    install_script = install.get("run", "")
    expected_url = (
        "releases/download/v${OPENCODE_VERSION}/opencode-linux-x64.tar.gz"
    )
    runtime_is_pinned = (
        job_env.get("OPENCODE_VERSION") == OPENCODE_VERSION
        and job_env.get("OPENCODE_ARCHIVE_SHA256") == OPENCODE_ARCHIVE_SHA256
        and cache.get("uses") == CACHE_ACTION
        and expected_url in install_script
        and "sha256sum --check -" in install_script
        and '"$install_dir/opencode" --version' in install_script
        and run_step.get("run") == "opencode github run"
        and run_step.get("env", {}).get("USE_GITHUB_TOKEN") == "true"
        and run_step.get("env", {}).get("MODEL") == "zai-coding-plan/glm-4.7"
    )
    if not runtime_is_pinned:
        raise ReleaseVerificationError(
            f"{workflow_name} does not pin and verify the approved OpenCode CLI runtime"
        )
    return run_step


def _verify_tag_catalog(repo: Path, ref: str) -> WorkflowCatalog | None:
    """Run the catalog/config/canonical contracts against tag-owned bytes."""

    import tempfile

    paths = (
        "scripts/workflow-catalog.json",
        "scripts/workflow-config.json",
        "examples/baseline-workflows/.github",
    )
    inventory_names = set(
        git(repo, "ls-tree", "-r", "--name-only", ref, "scripts").splitlines()
    )
    missing_inventory = set(paths[:2]) - inventory_names
    if missing_inventory:
        # Historical tags predate the closed release inventory.  Keep their
        # OpenCode verifier regression path readable, while all v1.40+ tags
        # must carry the renderer-owned catalog and fleet config.
        version = tuple(int(part) for part in ref.removeprefix("v").split("."))
        if version < (1, 40):
            return None
        raise ReleaseVerificationError(
            f"tag {ref} release inventory is missing: {sorted(missing_inventory)}"
        )
    with tempfile.TemporaryDirectory(prefix="verify-workflow-tag-") as temporary:
        root = Path(temporary)
        for relative in paths[:2]:
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(git(repo, "show", f"{ref}:{relative}"), encoding="utf-8")
        try:
            catalog = load_catalog(root)
            config = load_fleet_config(root, catalog)
        except CatalogError as exc:
            raise ReleaseVerificationError(
                f"tag {ref} catalog/config is invalid: {exc}"
            ) from exc

        canonical_names = set(
            git(repo, "ls-tree", "-r", "--name-only", ref, paths[2]).splitlines()
        )
        expected_names = {
            f"{config.canonical_dir}/{entry.path.relative_to('.github')}"
            for entry in catalog.entries
            if entry.kind != "retired"
        }
        if canonical_names != expected_names:
            missing = sorted(expected_names - canonical_names)
            unknown = sorted(canonical_names - expected_names)
            raise ReleaseVerificationError(
                f"tag {ref} canonical tree mismatch: missing={missing}, unknown={unknown}"
            )
        for entry in catalog.callers:
            relative = entry.path.relative_to(".github")
            name = f"{config.canonical_dir}/{relative}"
            text = git(repo, "show", f"{ref}:{name}")
            try:
                document = yaml.load(text, Loader=yaml.BaseLoader)
            except yaml.YAMLError as exc:
                raise ReleaseVerificationError(f"{name} is invalid YAML") from exc
            if not isinstance(document, dict):
                raise ReleaseVerificationError(f"{name} must be a mapping")
            if document.get("on") != entry.trigger:
                raise ReleaseVerificationError(f"{name} trigger violates the catalog")
            try:
                jobs = extract_caller_jobs(document)
            except CatalogError as exc:
                raise ReleaseVerificationError(
                    f"{name} caller contract is invalid: {exc}"
                ) from exc
            if jobs != entry.caller_jobs:
                raise ReleaseVerificationError(f"{name} caller jobs violate the catalog")
            uses = re.findall(
                r"jhw7500/automation/\.github/workflows/([^@\s'\"]+)@"
                r"([^\s'\"]+)",
                text,
            )
            if uses != [(entry.central_workflow, "__AUTOMATION_COMMIT__")]:
                raise ReleaseVerificationError(
                    f"{name} central target or release placeholder violates the catalog"
                )

        config_name = f"{config.canonical_dir}/workflow-config.yml"
        try:
            canonical_config = yaml.load(
                git(repo, "show", f"{ref}:{config_name}"), Loader=yaml.BaseLoader
            )
        except yaml.YAMLError as exc:
            raise ReleaseVerificationError(f"{config_name} is invalid YAML") from exc
        expected_workflows = {entry.path.stem for entry in catalog.callers}
        workflow_values = (
            canonical_config.get("workflows", {})
            if isinstance(canonical_config, dict)
            else {}
        )
        config_is_closed = (
            isinstance(canonical_config, dict)
            and set(canonical_config)
            == {"automation_ref", "automation_commit", "review", "workflows"}
            and canonical_config.get("automation_ref") == "__AUTOMATION_REF__"
            and canonical_config.get("automation_commit") == "__AUTOMATION_COMMIT__"
            and canonical_config.get("review") == {"auto": "false"}
            and isinstance(workflow_values, dict)
            and set(workflow_values) == expected_workflows
            and all(value == {"enabled": "false"} for value in workflow_values.values())
        )
        if not config_is_closed:
            raise ReleaseVerificationError(
                f"{config_name} violates the disabled bootstrap contract"
            )
        return catalog


def _verify_gemini_workflow(name: str, document: dict, text: str) -> None:
    try:
        call = document["on"]["workflow_call"]
        inputs = call["inputs"]
        secrets = call["secrets"]
    except (KeyError, TypeError) as exc:
        raise ReleaseVerificationError(
            f"{name} Gemini workflow_call contract is missing"
        ) from exc
    mode = inputs.get("repo_write_auth") if isinstance(inputs, dict) else None
    if (
        set(document.get("on", {})) != {"workflow_call"}
        or not isinstance(mode, dict)
        or mode.get("type") != "string"
        or mode.get("required") != "true"
    ):
        raise ReleaseVerificationError(f"{name} must require repo_write_auth")
    if "GOOGLE_API_KEY" in text:
        raise ReleaseVerificationError(f"{name} contains forbidden GOOGLE_API_KEY")
    if not isinstance(secrets, dict) or set(secrets) != GEMINI_SECRETS:
        raise ReleaseVerificationError(
            f"{name} must declare only APP_PRIVATE_KEY and GEMINI_API_KEY"
        )
    if (
        not isinstance(secrets["APP_PRIVATE_KEY"], dict)
        or secrets["APP_PRIVATE_KEY"].get("required", "false") != "false"
        or not isinstance(secrets["GEMINI_API_KEY"], dict)
        or secrets["GEMINI_API_KEY"].get("required") != "true"
    ):
        raise ReleaseVerificationError(f"{name} Gemini secret requirements are invalid")
    if re.search(
        r"(?i)(?:google-github-actions/auth|google_application_credentials|"
        r"workload_identity_provider|\bgcp\b|oidc|id-token\s*:)",
        text,
    ):
        raise ReleaseVerificationError(f"{name} contains forbidden Google/GCP/OIDC auth")
    if "vars.APP_ID" in text:
        raise ReleaseVerificationError(f"{name} contains forbidden ambient App fallback")
    setup_refs = re.findall(
        r"jhw7500/automation/\.github/actions/setup-gemini-auth@[^'\"\s#]+",
        text,
    )
    if not setup_refs or any(reference != SETUP_GEMINI_AUTH for reference in setup_refs):
        raise ReleaseVerificationError(f"{name} setup-gemini-auth is not pinned")


def verify_tag_content(repo: Path, ref: str) -> None:
    catalog = _verify_tag_catalog(repo, ref)
    names = git(repo, "ls-tree", "-r", "--name-only", ref, ".github/workflows").splitlines()
    workflows = [name for name in names if name.endswith((".yml", ".yaml"))]
    if not workflows:
        raise ReleaseVerificationError(f"tag {ref} contains no reusable workflows")

    documents: dict[str, dict] = {}
    for name in workflows:
        text = git(repo, "show", f"{ref}:{name}")
        if "secrets.GITHUB_TOKEN" in text:
            raise ReleaseVerificationError(f"{name} uses secrets.GITHUB_TOKEN")
        for match in re.finditer(r"actions/checkout@([^'\"\s#]+)", text):
            checkout = f"actions/checkout@{match.group(1)}"
            if checkout != CHECKOUT_ACTION:
                raise ReleaseVerificationError(
                    f"{name} checkout reference is not the approved immutable commit"
                )
        data = yaml.load(text, Loader=yaml.BaseLoader)
        documents[Path(name).name] = data if isinstance(data, dict) else {}
        if Path(name).name.startswith("gemini-"):
            _verify_gemini_workflow(Path(name).name, documents[Path(name).name], text)

    if catalog is not None:
        for entry in catalog.callers:
            assert entry.central_workflow is not None
            central = documents.get(entry.central_workflow)
            if central is None:
                raise ReleaseVerificationError(
                    f"central workflow is missing: {entry.central_workflow}"
                )
            try:
                call = central["on"]["workflow_call"]
                declared_inputs = set(call.get("inputs", {}))
                declared_secrets = call.get("secrets", {})
            except (KeyError, TypeError) as exc:
                raise ReleaseVerificationError(
                    f"{entry.central_workflow} workflow_call contract is missing"
                ) from exc
            if not isinstance(declared_secrets, dict):
                raise ReleaseVerificationError(
                    f"{entry.central_workflow} secrets must be a mapping"
                )
            required_secrets = {
                name
                for name, value in declared_secrets.items()
                if isinstance(value, dict) and value.get("required", "false") == "true"
            }
            if any(
                not set(job.with_keys) <= declared_inputs
                or not set(job.secrets) <= set(declared_secrets)
                or not required_secrets <= set(job.secrets)
                for job in entry.caller_jobs
            ):
                raise ReleaseVerificationError(
                    f"{entry.path} is incompatible with {entry.central_workflow}"
                )

    auto = documents.get("opencode-auto-review.yml")
    if auto is None:
        raise ReleaseVerificationError("opencode-auto-review.yml is missing")
    try:
        check_job = auto["jobs"]["check-enabled"]
        job = auto["jobs"]["opencode-review"]
        permissions = job["permissions"]
        checkout = next(
            item for item in job["steps"] if item.get("name") == "Checkout repository"
        )
    except (KeyError, TypeError, StopIteration) as exc:
        raise ReleaseVerificationError("OpenCode security structure is missing") from exc
    step = verify_opencode_runtime(
        job, "Run OpenCode PR review", "opencode-auto-review.yml"
    )
    expected_permissions = {
        "contents": "read",
        "pull-requests": "write",
        "issues": "write",
    }
    if permissions != expected_permissions:
        raise ReleaseVerificationError(
            f"OpenCode auto review permissions differ from {expected_permissions}"
        )
    safe_output = check_job.get("outputs", {}).get("safe_pr")
    scope_step = next(
        (item for item in check_job.get("steps", []) if item.get("id") == "pr_scope"),
        {},
    )
    condition = job.get("if", "")
    if (
        safe_output != "${{ steps.pr_scope.outputs.safe_pr }}"
        or "gh api" not in scope_step.get("run", "")
        or not isinstance(condition, str)
        or "needs.check-enabled.outputs.safe_pr == 'true'" not in condition
    ):
        raise ReleaseVerificationError(
            "OpenCode auto review lacks a central same-repository PR guard"
        )
    if checkout.get("with", {}).get("persist-credentials") != "true":
        raise ReleaseVerificationError(
            "OpenCode auto review cannot authenticate private repository fetch"
        )
    if step.get("env", {}).get("GITHUB_TOKEN") != "${{ github.token }}":
        raise ReleaseVerificationError("OpenCode auto review does not use github.token")

    command = documents.get("opencode.yml")
    try:
        command_check = command["jobs"]["check-enabled"]
        command_job = command["jobs"]["opencode"]
        command_checkout = next(
            item
            for item in command_job["steps"]
            if item.get("name") == "Checkout repository"
        )
    except (KeyError, TypeError, StopIteration) as exc:
        raise ReleaseVerificationError("opencode.yml structure is missing") from exc
    command_step = verify_opencode_runtime(command_job, "Run opencode", "opencode.yml")
    if command_checkout.get("with", {}).get("persist-credentials") != "true":
        raise ReleaseVerificationError(
            "opencode.yml cannot authenticate private repository fetch"
        )
    command_scope = next(
        (item for item in command_check.get("steps", []) if item.get("id") == "pr_scope"),
        {},
    )
    command_condition = command_job.get("if", "")
    command_permissions = {
        "contents": "read",
        "pull-requests": "write",
        "issues": "write",
    }
    command_is_secure = (
        command_check.get("outputs", {}).get("safe_pr")
        == "${{ steps.pr_scope.outputs.safe_pr }}"
        and "gh api" in command_scope.get("run", "")
        and command_scope.get("env", {}).get("PR_NUMBER")
        == "${{ github.event.pull_request.number || github.event.issue.number }}"
        and isinstance(command_condition, str)
        and "needs.check-enabled.outputs.safe_pr == 'true'" in command_condition
        and command_job.get("permissions") == command_permissions
        and command_step.get("env", {}).get("USE_GITHUB_TOKEN") == "true"
        and command_step.get("env", {}).get("GITHUB_TOKEN") == "${{ github.token }}"
    )
    if not command_is_secure:
        raise ReleaseVerificationError(
            "opencode.yml security contract permits unsafe PR or App-token access"
        )


def verify_release(
    repo: Path, ref: str, expected_commit: str, remote: str | None = None
) -> str:
    repo = repo.resolve()
    if re.fullmatch(r"v\d+(?:\.\d+)+", ref) is None:
        raise ReleaseVerificationError(f"invalid release ref: {ref}")
    require_annotated_tag(repo, ref)
    expected = resolve_commit(repo, expected_commit)
    tag_commit = resolve_commit(repo, f"refs/tags/{ref}")
    if tag_commit != expected:
        raise ReleaseVerificationError(
            f"tag {ref} points to {tag_commit}, expected commit {expected}"
        )
    verify_tag_content(repo, ref)
    if remote is not None:
        verify_remote_tag(repo, remote, ref, expected)
    return tag_commit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--automation", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--ref", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--remote")
    args = parser.parse_args(argv)
    try:
        commit = verify_release(
            args.automation, args.ref, args.expected_commit, remote=args.remote
        )
    except ReleaseVerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    remote_note = f" and remote {args.remote}" if args.remote else ""
    print(f"PASS: {args.ref} resolves to secure commit {commit}{remote_note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
