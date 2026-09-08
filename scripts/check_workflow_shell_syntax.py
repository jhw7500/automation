#!/usr/bin/env python3
"""Parse Bash workflow run blocks without executing them."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Iterator

import yaml


EXPRESSION_TOKEN = "__GITHUB_EXPRESSION__"


class WorkflowSyntaxError(ValueError):
    """A workflow cannot be checked safely."""


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.name


def _neutralize_expressions(program: str) -> str:
    token = EXPRESSION_TOKEN
    suffix = 0
    while token in program:
        suffix += 1
        token = f"__GITHUB_EXPRESSION_{suffix}__"

    output: list[str] = []
    cursor = 0
    while True:
        start = program.find("${{", cursor)
        if start < 0:
            output.append(program[cursor:])
            return "".join(output)

        output.append(program[cursor:start])
        index = start + 3
        in_string = False
        while index < len(program):
            if program[index] == "'":
                if (
                    in_string
                    and index + 1 < len(program)
                    and program[index + 1] == "'"
                ):
                    index += 2
                    continue
                in_string = not in_string
                index += 1
                continue
            if not in_string and program.startswith("}}", index):
                output.append(token)
                cursor = index + 2
                break
            index += 1
        else:
            raise WorkflowSyntaxError("unclosed GitHub expression")


def _configured_shell(document: object) -> str | None:
    if not isinstance(document, dict):
        return None
    defaults = document.get("defaults")
    if not isinstance(defaults, dict):
        return None
    run = defaults.get("run")
    if not isinstance(run, dict):
        return None
    shell = run.get("shell")
    return shell if isinstance(shell, str) else None


def _uses_bash(shell: str | None) -> bool:
    if shell is None:
        return True
    if "${{" in shell:
        raise WorkflowSyntaxError("shell setting is dynamic; use a literal shell")
    try:
        words = shlex.split(shell)
    except ValueError as error:
        raise WorkflowSyntaxError(f"invalid shell setting: {error}") from error
    if not words:
        return False
    executable = 0
    if Path(words[0]).name == "env":
        executable = 1
        while executable < len(words):
            name, separator, _ = words[executable].partition("=")
            if separator and name.isidentifier():
                executable += 1
                continue
            break
        if executable == len(words):
            return False
        if words[executable].startswith("-"):
            raise WorkflowSyntaxError(
                "env-wrapped shell is ambiguous; use a direct shell command"
            )
    return Path(words[executable]).name == "bash"


def _runner_labels(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    if isinstance(value, dict):
        return _runner_labels(value.get("labels"))
    return []


def _runs_on_windows(value: object) -> bool:
    return any("windows" in label.casefold() for label in _runner_labels(value))


def _runs_on_bash_host(value: object) -> bool:
    bash_labels = ("ubuntu", "linux", "macos")
    return any(
        any(name in label.casefold() for name in bash_labels)
        for label in _runner_labels(value)
    )


def _contains_expression(value: object) -> bool:
    if isinstance(value, str):
        return "${{" in value
    if isinstance(value, list):
        return any(_contains_expression(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_expression(item) for item in value.values())
    return False


def _bash_blocks(document: object) -> Iterator[tuple[str, str]]:
    if not isinstance(document, dict):
        raise WorkflowSyntaxError("workflow root must be a mapping")
    jobs = document.get("jobs")
    if not isinstance(jobs, dict):
        raise WorkflowSyntaxError("workflow jobs must be a mapping")

    workflow_shell = _configured_shell(document)
    for job_id, job in jobs.items():
        if not isinstance(job, dict):
            continue
        job_shell = _configured_shell(job) or workflow_shell
        steps = job.get("steps")
        if steps is None:
            continue
        if not isinstance(steps, list):
            raise WorkflowSyntaxError(f"jobs.{job_id}.steps must be a list")
        for index, step in enumerate(steps):
            if not isinstance(step, dict) or "run" not in step:
                continue
            run = step["run"]
            if not isinstance(run, str):
                raise WorkflowSyntaxError(
                    f"jobs.{job_id}.steps[{index}].run must be a string"
                )
            shell = step.get("shell", job_shell)
            if shell is not None and not isinstance(shell, str):
                raise WorkflowSyntaxError(
                    f"jobs.{job_id}.steps[{index}].shell must be a string"
                )
            if shell is None:
                if job.get("container") is not None:
                    continue
                runs_on = job.get("runs-on")
                if _contains_expression(runs_on):
                    raise WorkflowSyntaxError(
                        "implicit shell is ambiguous; set a literal shell"
                    )
                if _runs_on_windows(runs_on):
                    continue
                if not _runs_on_bash_host(runs_on):
                    raise WorkflowSyntaxError(
                        "implicit shell is ambiguous; set a literal shell"
                    )
            if _uses_bash(shell):
                yield f"jobs.{job_id}.steps[{index}]", run


def _workflow_paths(inputs: list[Path]) -> list[Path]:
    paths: set[Path] = set()
    for item in inputs:
        if item.is_dir():
            paths.update(item.glob("*.yml"))
            paths.update(item.glob("*.yaml"))
        elif item.is_file():
            paths.add(item)
        else:
            raise WorkflowSyntaxError(f"workflow path does not exist: {item}")
    return sorted(paths, key=lambda path: path.as_posix())


def _check_block(program: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-n"],
        input=program,
        env={"LC_ALL": "C", "PATH": os.defpath},
        text=True,
        capture_output=True,
        check=False,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check Bash syntax in GitHub Actions run blocks."
    )
    parser.add_argument("paths", nargs="+", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        paths = _workflow_paths(args.paths)
    except WorkflowSyntaxError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    checked = 0
    failed = False
    for path in paths:
        display = _display_path(path)
        try:
            document = yaml.load(
                path.read_text(encoding="utf-8"),
                Loader=yaml.BaseLoader,
            )
            blocks = list(_bash_blocks(document))
        except (OSError, UnicodeError, yaml.YAMLError, WorkflowSyntaxError) as error:
            print(f"{display}: {error}", file=sys.stderr)
            failed = True
            continue

        for location, program in blocks:
            checked += 1
            try:
                neutralized = _neutralize_expressions(program)
                result = _check_block(neutralized)
            except (OSError, WorkflowSyntaxError) as error:
                print(f"{display}:{location}: {error}", file=sys.stderr)
                failed = True
                continue
            if result.returncode != 0:
                print(
                    f"{display}:{location}: Bash syntax error",
                    file=sys.stderr,
                )
                if result.stderr:
                    print(result.stderr.strip(), file=sys.stderr)
                failed = True

    if failed:
        return 2
    print(f"PASS: checked {checked} Bash workflow run blocks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
