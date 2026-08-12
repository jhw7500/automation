#!/usr/bin/env python3
"""Verify that a workflow release tag is the intended, secure Git artifact."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys

import yaml


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


def verify_tag_content(repo: Path, ref: str) -> None:
    names = git(repo, "ls-tree", "-r", "--name-only", ref, ".github/workflows").splitlines()
    workflows = [name for name in names if name.endswith((".yml", ".yaml"))]
    if not workflows:
        raise ReleaseVerificationError(f"tag {ref} contains no reusable workflows")

    documents: dict[str, dict] = {}
    for name in workflows:
        text = git(repo, "show", f"{ref}:{name}")
        if "secrets.GITHUB_TOKEN" in text:
            raise ReleaseVerificationError(f"{name} uses secrets.GITHUB_TOKEN")
        data = yaml.load(text, Loader=yaml.BaseLoader)
        documents[Path(name).name] = data if isinstance(data, dict) else {}

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
        step = next(
            item for item in job["steps"] if item.get("name") == "Run OpenCode PR review"
        )
    except (KeyError, TypeError, StopIteration) as exc:
        raise ReleaseVerificationError("OpenCode security structure is missing") from exc
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
    if step.get("with", {}).get("use_github_token") != "true":
        raise ReleaseVerificationError("OpenCode auto review does not force use_github_token")
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
        command_step = next(
            item for item in command_job["steps"] if item.get("name") == "Run opencode"
        )
    except (KeyError, TypeError, StopIteration) as exc:
        raise ReleaseVerificationError("opencode.yml structure is missing") from exc
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
        and isinstance(command_condition, str)
        and "needs.check-enabled.outputs.safe_pr == 'true'" in command_condition
        and command_job.get("permissions") == command_permissions
        and command_step.get("with", {}).get("use_github_token") == "true"
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
