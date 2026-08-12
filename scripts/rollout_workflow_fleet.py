#!/usr/bin/env python3
"""Orchestrate a fail-closed, per-repository workflow and secret rollout.

The command keeps workflow delivery (Git PRs) and secret writes as separate phases in
one run because GitHub cannot make them atomic. Secret prerequisites are checked first;
only then are existing callers transformed, validated, committed, pushed and opened as
independent PRs. No repository-owned trigger or permission block is replaced.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_workflow_fleet import audit_repository
from scripts.prepare_workflow_rollout import RolloutError, prepare_repository


BRANCH = "codex/automation-v1.35-fleet"


@dataclass
class RepoOutcome:
    repo: str
    status: str
    detail: str
    base_sha: str = ""
    head_sha: str = ""
    pr_url: str = ""
    synced_secrets: tuple[str, ...] = ()


class CommandError(RuntimeError):
    pass


def run(
    args: list[str], *, cwd: Path | None = None, input_text: str | None = None
) -> str:
    result = subprocess.run(
        args,
        cwd=cwd,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={key: value for key, value in os.environ.items() if key != "GITHUB_TOKEN"},
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise CommandError(f"{' '.join(args)}: {detail}")
    return result.stdout.strip()


def gh_json(args: list[str]) -> object:
    output = run(["gh", *args])
    return json.loads(output or "null")


def default_branch(owner: str, repo: str) -> str:
    data = gh_json(["repo", "view", f"{owner}/{repo}", "--json", "defaultBranchRef"])
    return data["defaultBranchRef"]["name"]  # type: ignore[index]


def remote_names(owner: str, repo: str, kind: str) -> set[str]:
    data = gh_json([kind, "list", "-R", f"{owner}/{repo}", "--json", "name"])
    return {item["name"] for item in data}  # type: ignore[union-attr]


def secret_source(name: str) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    if name != "CLAUDE_CODE_OAUTH_TOKEN":
        return None
    credentials = Path.home() / ".claude/.credentials.json"
    if not credentials.is_file():
        return None
    try:
        data = json.loads(credentials.read_text(encoding="utf-8"))
        value = data["claudeAiOauth"]["accessToken"]
    except (KeyError, TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, str) and value else None


def clone_or_reset(workspace: Path, owner: str, repo: str, branch: str) -> tuple[Path, str]:
    path = workspace / repo
    if not (path / ".git").is_dir():
        run(
            [
                "git",
                "clone",
                "--quiet",
                "--filter=blob:none",
                "--no-tags",
                f"https://github.com/{owner}/{repo}.git",
                str(path),
            ]
        )
    run(["git", "fetch", "--no-tags", "origin", branch], cwd=path)
    base = run(["git", "rev-parse", f"origin/{branch}"], cwd=path)
    run(["git", "checkout", "-q", "-B", BRANCH, f"origin/{branch}"], cwd=path)
    run(["git", "reset", "--hard", "-q", f"origin/{branch}"], cwd=path)
    run(["git", "clean", "-fdq"], cwd=path)
    return path, base


def preview_copy(repo: Path) -> tempfile.TemporaryDirectory[str]:
    temp = tempfile.TemporaryDirectory()
    target = Path(temp.name)
    github = repo / ".github"
    if github.is_dir():
        shutil.copytree(github, target / ".github")
    return temp


def sync_missing(
    owner: str, repo: str, missing: set[str], enabled: bool
) -> tuple[set[str], tuple[str, ...]]:
    available = remote_names(owner, repo, "secret")
    synced: list[str] = []
    for name in sorted(missing - available):
        value = secret_source(name) if enabled else None
        if value is None:
            continue
        run(
            ["gh", "secret", "set", name, "-R", f"{owner}/{repo}", "--body", "-"],
            input_text=value,
        )
        available.add(name)
        synced.append(name)
    return available, tuple(synced)


def prepare_with_prerequisites(
    repo_path: Path,
    automation: Path,
    ref: str,
    owner: str,
    repo: str,
    sync_missing_enabled: bool,
) -> tuple[object, tuple[str, ...]]:
    secrets = remote_names(owner, repo, "secret")
    variables = remote_names(owner, repo, "variable")
    synced: tuple[str, ...] = ()
    try:
        return prepare_repository(repo_path, automation, ref, secrets, variables), synced
    except RolloutError as first:
        text = str(first)
        candidates: set[str] = set()
        if "missing required secrets:" in text:
            candidates.update(text.split("missing required secrets:", 1)[1].split(", "))
        if "APP_ID exists but APP_PRIVATE_KEY is missing" in text:
            candidates.add("APP_PRIVATE_KEY")
        if "no usable Gemini authentication path" in text:
            candidates.update({"GEMINI_API_KEY"})
        if not candidates:
            raise
        secrets, synced = sync_missing(
            owner, repo, candidates, enabled=sync_missing_enabled
        )
        return prepare_repository(repo_path, automation, ref, secrets, variables), synced


def validate_repository(
    repo: Path,
    automation: Path,
    actionlint: Path | None,
    baseline_repo: Path | None = None,
) -> None:
    issues = audit_repository(repo, automation)
    if issues:
        raise CommandError("; ".join(issues))
    if baseline_repo is None:
        run(["git", "diff", "--check"], cwd=repo)
    if actionlint is not None:
        workflows = sorted((repo / ".github/workflows").glob("*.y*ml"))
        if workflows:
            current = actionlint_diagnostics(actionlint, repo)
            if baseline_repo is not None:
                baseline = actionlint_diagnostics(actionlint, baseline_repo)
            else:
                with tempfile.TemporaryDirectory() as temp:
                    archive = subprocess.run(
                        ["git", "archive", "HEAD", ".github/workflows"],
                        cwd=repo,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                    if archive.returncode != 0:
                        raise CommandError("unable to archive baseline workflows for actionlint")
                    extract = subprocess.run(
                        ["tar", "-x", "-C", temp],
                        input=archive.stdout,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                    if extract.returncode != 0:
                        raise CommandError("unable to extract baseline workflows for actionlint")
                    baseline = actionlint_diagnostics(actionlint, Path(temp))
            new_diagnostics = current - baseline
            if new_diagnostics:
                detail = "; ".join(
                    f"{message} ({count})" for message, count in new_diagnostics.items()
                )
                raise CommandError(f"new actionlint diagnostics: {detail}")


def actionlint_diagnostics(actionlint: Path, root: Path) -> Counter[str]:
    workflows = sorted((root / ".github/workflows").glob("*.y*ml"))
    if not workflows:
        return Counter()
    result = subprocess.run(
        [str(actionlint), "-shellcheck=", *map(str, workflows)],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    diagnostics: Counter[str] = Counter()
    for line in result.stdout.splitlines():
        if ".yml:" not in line and ".yaml:" not in line:
            continue
        normalized = line.replace(str(root) + "/", "")
        normalized = re.sub(r":\d+:\d+:", ":", normalized, count=1)
        diagnostics[normalized] += 1
    return diagnostics


def publish_repository(
    repo: Path, owner: str, name: str, default: str, ref: str
) -> tuple[str, str]:
    run(["git", "add", ".github"], cwd=repo)
    run(
        ["git", "commit", "-m", f"ci: restrict shared workflow secrets ({ref})"],
        cwd=repo,
    )
    head = run(["git", "rev-parse", "HEAD"], cwd=repo)
    run(["git", "push", "--force-with-lease", "-u", "origin", BRANCH], cwd=repo)
    existing = gh_json(
        [
            "pr",
            "list",
            "-R",
            f"{owner}/{name}",
            "--head",
            BRANCH,
            "--base",
            default,
            "--state",
            "open",
            "--json",
            "url",
        ]
    )
    if existing:
        return head, existing[0]["url"]  # type: ignore[index]
    body = (
        f"Update existing reusable-workflow callers to verified automation@{ref} and "
        "replace `secrets: inherit` with contract-derived explicit mappings. "
        "Repository-owned triggers, guards and permissions are preserved.\n\n"
        "Pre-push gates: required secret-name inventory, YAML parse, caller contract audit, "
        "`git diff --check`, and actionlint when configured.\n\n"
        "Rollback: restore the previous automation ref and `secrets: inherit` together; "
        "do not move the release tag."
    )
    url = run(
        [
            "gh",
            "pr",
            "create",
            "-R",
            f"{owner}/{name}",
            "--base",
            default,
            "--head",
            BRANCH,
            "--title",
            f"ci: restrict shared workflow secrets ({ref})",
            "--body",
            body,
        ]
    )
    return head, url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--automation", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--config", type=Path)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--ref", default="v1.35")
    parser.add_argument("--mode", choices=("plan", "prepare", "publish"), default="plan")
    parser.add_argument("--repo", action="append", default=[])
    parser.add_argument("--sync-missing-secrets", action="store_true")
    parser.add_argument("--actionlint", type=Path)
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument(
        "--initialize-workspace",
        action="store_true",
        help="create the safety marker required before managed clones may be reset/cleaned",
    )
    args = parser.parse_args(argv)

    automation = args.automation.resolve()
    config_path = args.config or automation / "scripts/workflow-config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    owner = config["gh_owner"]
    configured = sorted(config["repos"])
    repos = args.repo or configured
    unknown = sorted(set(repos) - set(configured))
    if unknown:
        parser.error(f"repositories not in config: {', '.join(unknown)}")
    if args.mode == "publish" and not args.confirm:
        parser.error("--mode publish requires --confirm")

    marker = args.workspace / ".automation-fleet-workspace"
    if not marker.is_file():
        if not args.initialize_workspace:
            parser.error(
                f"workspace is not initialized: {args.workspace}; "
                "use --initialize-workspace only for a dedicated disposable directory"
            )
        args.workspace.mkdir(parents=True, exist_ok=True)
        marker.write_text("managed disposable clones only\n", encoding="utf-8")
    outcomes: list[RepoOutcome] = []
    for name in repos:
        try:
            default = default_branch(owner, name)
            repo, base = clone_or_reset(args.workspace, owner, name, default)
            target = repo
            preview = None
            if args.mode == "plan":
                preview = preview_copy(repo)
                target = Path(preview.name)
            result, synced = prepare_with_prerequisites(
                target,
                automation,
                args.ref,
                owner,
                name,
                args.sync_missing_secrets and args.mode != "plan",
            )
            if result.callers == 0:
                outcomes.append(RepoOutcome(name, "skipped", "no existing central callers", base))
            elif not result.changed_files:
                outcomes.append(RepoOutcome(name, "current", "already matches contract", base))
            else:
                validate_repository(
                    target,
                    automation,
                    args.actionlint,
                    baseline_repo=repo if args.mode == "plan" else None,
                )
                if args.mode == "publish":
                    head, url = publish_repository(repo, owner, name, default, args.ref)
                    outcomes.append(
                        RepoOutcome(name, "published", f"{result.callers} callers", base, head, url, synced)
                    )
                else:
                    outcomes.append(
                        RepoOutcome(name, args.mode, f"{result.callers} callers", base, synced_secrets=synced)
                    )
            if preview is not None:
                preview.cleanup()
        except (CommandError, RolloutError, OSError, json.JSONDecodeError) as exc:
            outcomes.append(RepoOutcome(name, "blocked", str(exc)))

    manifest = args.manifest or args.workspace / "rollout-manifest.json"
    manifest.write_text(
        json.dumps([asdict(item) for item in outcomes], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for item in outcomes:
        suffix = f" {item.pr_url}" if item.pr_url else ""
        print(f"{item.status.upper():9} {item.repo}: {item.detail}{suffix}")
    blocked = sum(item.status == "blocked" for item in outcomes)
    print(f"SUMMARY total={len(outcomes)} blocked={blocked} manifest={manifest}")
    return 1 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
