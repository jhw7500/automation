#!/usr/bin/env python3
"""Prepare existing automation reusable-workflow callers for a safe fleet rollout.

This module deliberately does not add workflows or replace whole caller files. It keeps
repository-owned triggers, guards, permissions and inputs, changing only the central
release ref, secret mapping, optional app_id forwarding and automation_ref config.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import sys

import yaml


USE_RE = re.compile(
    r"^(?P<indent> +)uses:\s*['\"]?"
    r"jhw7500/automation/\.github/workflows/(?P<workflow>[^@\s'\"]+)@"
    r"(?P<ref>[^\s'\"]+)['\"]?\s*$"
)
CONFIG_REF_RE = re.compile(r"(?m)^(automation_ref:\s*)\S+\s*$")
GEMINI_AUTH_SECRETS = {"APP_PRIVATE_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"}


class RolloutError(RuntimeError):
    pass


class SecretPrerequisiteError(RolloutError):
    def __init__(self, message: str, missing_secrets: set[str]) -> None:
        super().__init__(message)
        self.missing_secrets = frozenset(missing_secrets)


@dataclass(frozen=True)
class Contract:
    declared: set[str]
    required: set[str]
    has_app_id: bool


@dataclass(frozen=True)
class RolloutResult:
    callers: int
    changed_files: tuple[Path, ...]
    required_secrets: frozenset[str]


def load_yaml(path: Path) -> dict:
    data = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    return data if isinstance(data, dict) else {}


def workflow_contract(automation: Path, filename: str) -> Contract:
    path = automation / ".github/workflows" / filename
    if not path.is_file():
        raise RolloutError(f"central workflow not found: {filename}")
    workflow = load_yaml(path)
    on = workflow.get("on", {})
    if not isinstance(on, dict) or "workflow_call" not in on:
        raise RolloutError(f"central workflow is not reusable: {filename}")
    call = on.get("workflow_call") or {}
    if not isinstance(call, dict):
        raise RolloutError(f"invalid workflow_call contract: {filename}")
    secrets = call.get("secrets", {})
    inputs = call.get("inputs", {})
    if not isinstance(secrets, dict):
        secrets = {}
    if not isinstance(inputs, dict):
        inputs = {}
    required = {
        name
        for name, values in secrets.items()
        if isinstance(values, dict) and values.get("required", "false") == "true"
    }
    return Contract(set(secrets), required, "app_id" in inputs)


def leading_spaces(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def block_end(lines: list[str], start: int, parent_indent: int) -> int:
    for index in range(start + 1, len(lines)):
        stripped = lines[index].strip()
        if not stripped or stripped.startswith("#"):
            continue
        if leading_spaces(lines[index]) <= parent_indent:
            return index
    return len(lines)


def secret_names_for(
    filename: str,
    contract: Contract,
    available_secrets: set[str],
    available_variables: set[str],
) -> tuple[list[str], bool]:
    missing = contract.required - available_secrets
    if missing:
        raise SecretPrerequisiteError(
            f"{filename}: missing required secrets: {', '.join(sorted(missing))}",
            missing,
        )

    selected = contract.declared & available_secrets
    has_gemini_auth_contract = GEMINI_AUTH_SECRETS <= contract.declared
    app_enabled = "APP_ID" in available_variables
    if "APP_PRIVATE_KEY" in contract.declared:
        if app_enabled and "APP_PRIVATE_KEY" not in available_secrets:
            raise SecretPrerequisiteError(
                f"{filename}: APP_ID exists but APP_PRIVATE_KEY is missing",
                {"APP_PRIVATE_KEY"},
            )
        if not app_enabled:
            selected.discard("APP_PRIVATE_KEY")

    if has_gemini_auth_contract:
        usable_api_key = bool({"GEMINI_API_KEY", "GOOGLE_API_KEY"} & available_secrets)
        usable_app = app_enabled and "APP_PRIVATE_KEY" in available_secrets
        if not (usable_api_key or usable_app):
            raise SecretPrerequisiteError(
                f"{filename}: no usable Gemini authentication path",
                {"GEMINI_API_KEY"},
            )

    return sorted(selected), contract.has_app_id and app_enabled


def replace_job_segment(
    segment: list[str],
    indent: str,
    new_ref: str,
    names: list[str],
    pass_app_id: bool,
) -> list[str]:
    use_match = USE_RE.match(segment[0].rstrip("\n"))
    if use_match is None:
        raise RolloutError("internal error: reusable workflow use line not found")
    newline = "\n" if segment[0].endswith("\n") else ""
    segment[0] = re.sub(
        r"(@)[^\s'\"]+(['\"]?\s*)$",
        lambda match: match.group(1) + new_ref + match.group(2),
        segment[0].rstrip("\n"),
    ) + newline

    width = len(indent)
    secret_index = None
    secret_end = None
    with_index = None
    for index, line in enumerate(segment):
        stripped = line.strip()
        if leading_spaces(line) == width and re.match(r"secrets\s*:", stripped):
            secret_index = index
            secret_end = block_end(segment, index, width)
        if leading_spaces(line) == width and stripped == "with:":
            with_index = index

    if secret_index is not None and secret_end is not None:
        del segment[secret_index:secret_end]
        if with_index is not None and secret_index < with_index:
            with_index -= secret_end - secret_index

    if pass_app_id:
        app_line = f"{indent}  app_id: ${{{{ vars.APP_ID }}}}\n"
        if with_index is None:
            insertion = len(segment)
            segment[insertion:insertion] = [f"{indent}with:\n", app_line]
        else:
            with_end = block_end(segment, with_index, width)
            existing = any(
                leading_spaces(line) > width and line.strip().startswith("app_id:")
                for line in segment[with_index + 1 : with_end]
            )
            if not existing:
                segment.insert(with_end, app_line)

    if names:
        mapping = [f"{indent}secrets:\n"] + [
            f"{indent}  {name}: ${{{{ secrets.{name} }}}}\n" for name in names
        ]
        segment.extend(mapping)
    return segment


def transform_workflow(
    path: Path,
    automation: Path,
    new_ref: str,
    available_secrets: set[str],
    available_variables: set[str],
) -> tuple[str, int, set[str]]:
    original = path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)
    uses: list[tuple[int, re.Match[str]]] = []
    for index, line in enumerate(lines):
        match = USE_RE.match(line.rstrip("\n"))
        if match is not None:
            uses.append((index, match))

    required: set[str] = set()
    for index, match in reversed(uses):
        indent = match.group("indent")
        end = block_end(lines, index, len(indent) - 2)
        contract = workflow_contract(automation, match.group("workflow"))
        names, pass_app_id = secret_names_for(
            match.group("workflow"), contract, available_secrets, available_variables
        )
        required.update(names)
        segment = replace_job_segment(lines[index:end], indent, new_ref, names, pass_app_id)
        lines[index:end] = segment

    return "".join(lines), len(uses), required


def prepare_repository(
    repo: Path,
    automation: Path,
    new_ref: str,
    available_secrets: set[str],
    available_variables: set[str],
) -> RolloutResult:
    repo = repo.resolve()
    automation = automation.resolve()
    workflow_dir = repo / ".github/workflows"
    planned: dict[Path, str] = {}
    caller_count = 0
    required: set[str] = set()

    if workflow_dir.is_dir():
        for path in sorted(workflow_dir.glob("*.y*ml")):
            transformed, callers, names = transform_workflow(
                path, automation, new_ref, available_secrets, available_variables
            )
            caller_count += callers
            required.update(names)
            if transformed != path.read_text(encoding="utf-8"):
                planned[path] = transformed

    if caller_count == 0:
        return RolloutResult(0, (), frozenset())

    config = repo / ".github/workflow-config.yml"
    if config.is_file():
        old_config = config.read_text(encoding="utf-8")
        if CONFIG_REF_RE.search(old_config):
            new_config = CONFIG_REF_RE.sub(rf"\g<1>{new_ref}", old_config, count=1)
        else:
            new_config = f"automation_ref: {new_ref}\n" + old_config
    else:
        new_config = f"automation_ref: {new_ref}\n"
    if not config.is_file() or new_config != config.read_text(encoding="utf-8"):
        planned[config] = new_config

    # Validate all generated YAML before writing any file.
    for path, text in planned.items():
        try:
            yaml.load(text, Loader=yaml.BaseLoader)
        except yaml.YAMLError as exc:
            raise RolloutError(f"generated invalid YAML for {path}: {exc}") from exc

    for path, text in planned.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    changed = tuple(sorted(path.relative_to(repo) for path in planned))
    return RolloutResult(caller_count, changed, frozenset(required))


def csv_set(value: str) -> set[str]:
    return {item for item in value.split(",") if item}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--automation", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--ref", default="v1.35")
    parser.add_argument("--available-secrets", default="")
    parser.add_argument("--available-variables", default="")
    args = parser.parse_args(argv)
    try:
        result = prepare_repository(
            args.repo,
            args.automation,
            args.ref,
            csv_set(args.available_secrets),
            csv_set(args.available_variables),
        )
    except RolloutError as exc:
        print(f"FAIL {args.repo}: {exc}", file=sys.stderr)
        return 1
    print(
        f"PASS {args.repo}: {result.callers} caller(s), "
        f"{len(result.changed_files)} changed file(s), "
        f"secrets={','.join(sorted(result.required_secrets)) or 'none'}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
