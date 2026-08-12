#!/usr/bin/env python3
"""Audit consumer repositories that call jhw7500/automation workflows."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

import yaml


AUTOMATION_WORKFLOW_USE = re.compile(
    r"jhw7500/automation/\.github/workflows/([^@\s'\"]+)@([^\s'\"]+)"
)


def load_yaml(path: Path) -> dict:
    data = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    return data if isinstance(data, dict) else {}


def configured_ref(repo: Path) -> str | None:
    path = repo / ".github" / "workflow-config.yml"
    if not path.is_file():
        return None
    value = load_yaml(path).get("automation_ref")
    return value if isinstance(value, str) and value else None


def workflow_contract(automation: Path, filename: str) -> tuple[set[str], set[str]] | None:
    path = automation / ".github" / "workflows" / filename
    if not path.is_file():
        return None
    workflow = load_yaml(path)
    call = workflow.get("on", {}).get("workflow_call", {})
    declarations = call.get("secrets", {}) if isinstance(call, dict) else {}
    if not isinstance(declarations, dict):
        declarations = {}
    declared = set(declarations)
    required = {
        name
        for name, config in declarations.items()
        if isinstance(config, dict) and config.get("required", "false") == "true"
    }
    return declared, required


def audit_template_contract(repo: Path, template: Path) -> list[str]:
    issues: list[str] = []
    if not template.is_dir():
        return [f"managed template directory missing: {template}"]
    canonical_paths = sorted(template.glob("*.y*ml"))
    if not canonical_paths:
        return [f"managed template contains no workflow YAML: {template}"]
    target_dir = repo / ".github" / "workflows"
    for canonical_path in canonical_paths:
        target_path = target_dir / canonical_path.name
        location = f".github/workflows/{canonical_path.name}"
        if not target_path.is_file():
            issues.append(f"{location}: managed caller missing")
            continue
        canonical = load_yaml(canonical_path)
        target = load_yaml(target_path)
        if canonical.get("on") != target.get("on"):
            issues.append(f"{location}: trigger drift from managed template")

        canonical_jobs = canonical.get("jobs", {})
        target_jobs = target.get("jobs", {})
        if not isinstance(canonical_jobs, dict) or not isinstance(target_jobs, dict):
            continue
        for job_name, canonical_job in canonical_jobs.items():
            if not isinstance(canonical_job, dict):
                continue
            use = canonical_job.get("uses")
            if not isinstance(use, str) or AUTOMATION_WORKFLOW_USE.fullmatch(
                use.strip("'\"")
            ) is None:
                continue
            target_job = target_jobs.get(job_name)
            if not isinstance(target_job, dict):
                issues.append(f"{location}:job {job_name}: managed caller job missing")
                continue
            if canonical_job.get("permissions", {}) != target_job.get("permissions", {}):
                issues.append(
                    f"{location}:job {job_name}: permissions drift from managed template"
                )
    return issues


def audit_repository(
    repo: Path, automation: Path, template: Path | None = None
) -> list[str]:
    issues: list[str] = []
    expected_ref = configured_ref(repo)
    if expected_ref is None:
        issues.append(".github/workflow-config.yml: missing automation_ref")

    workflows = repo / ".github" / "workflows"
    if not workflows.is_dir():
        return issues + [".github/workflows: directory missing"]

    for path in sorted(workflows.glob("*.y*ml")):
        relative = path.relative_to(repo)
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            for match in AUTOMATION_WORKFLOW_USE.finditer(line):
                actual_ref = match.group(2)
                if expected_ref is not None and actual_ref != expected_ref:
                    issues.append(
                        f"{relative}:{lineno}: ref drift: configured {expected_ref}, uses {actual_ref}"
                    )

        workflow = load_yaml(path)
        jobs = workflow.get("jobs", {})
        if not isinstance(jobs, dict):
            continue
        for job_name, job in jobs.items():
            if not isinstance(job, dict):
                continue
            use = job.get("uses")
            if not isinstance(use, str):
                continue
            match = AUTOMATION_WORKFLOW_USE.fullmatch(use.strip("'\""))
            if match is None:
                continue
            filename = match.group(1)
            contract = workflow_contract(automation, filename)
            location = f"{relative}:job {job_name}"
            if contract is None:
                issues.append(f"{location}: central workflow not found: {filename}")
                continue
            declared, required = contract
            passed = job.get("secrets", {})
            if passed == "inherit":
                issues.append(f"{location}: secrets: inherit exposes all caller secrets")
                continue
            if passed is None:
                passed = {}
            if not isinstance(passed, dict):
                issues.append(f"{location}: invalid secrets mapping")
                continue
            passed_names = set(passed)
            missing = sorted(required - passed_names)
            unknown = sorted(passed_names - declared)
            if missing:
                issues.append(f"{location}: missing required secrets: {', '.join(missing)}")
            if unknown:
                issues.append(f"{location}: undeclared secret mapping: {', '.join(unknown)}")
            for name, value in passed.items():
                if name not in declared or not isinstance(value, str):
                    continue
                expected_source = re.compile(
                    rf"^\$\{{\{{\s*secrets\.{re.escape(name)}\s*\}}\}}$"
                )
                if expected_source.fullmatch(value) is None:
                    issues.append(
                        f"{location}: secret source mismatch for {name}; "
                        f"expected secrets.{name}"
                    )
    if template is not None:
        issues.extend(audit_template_contract(repo, template))
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--automation", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--template",
        type=Path,
        help="optional managed caller directory for missing/trigger/permission drift checks",
    )
    args = parser.parse_args(argv)

    template = args.template.resolve() if args.template is not None else None
    issues = audit_repository(args.repo.resolve(), args.automation.resolve(), template)
    if issues:
        print(f"FAIL {args.repo}: {len(issues)} issue(s)")
        for issue in issues:
            print(f"- {issue}")
        return 1
    print(f"PASS {args.repo}: caller refs and secret mappings match central contracts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
