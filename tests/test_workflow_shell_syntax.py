from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import yaml


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "check_workflow_shell_syntax.py"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "test-fleet-tools.yml"


def run_checker(*paths: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER), *(str(path) for path in paths)],
        cwd=ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        capture_output=True,
        check=False,
    )


def test_default_and_explicit_bash_blocks_accept_github_expressions(
    tmp_path: Path,
) -> None:
    workflow = tmp_path / "valid.yml"
    workflow.write_text(
        """
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - run: |
          printf '%s\\n' "${{ github.ref }}"
      - shell: bash
        run: |
          printf '%s\\n' "${{ '}}' }}"
""".lstrip(),
        encoding="utf-8",
    )

    result = run_checker(workflow)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "PASS: checked 2 Bash workflow run blocks\n"
    assert result.stderr == ""


def test_awk_apostrophe_break_is_reported_with_step_location(
    tmp_path: Path,
) -> None:
    workflow = tmp_path / "broken.yml"
    workflow.write_text(
        """
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - shell: bash
        run: |
          printf '%s\\n' "${{ github.ref }}"
          awk '
            # the model's output
            { print $0 }
          '
""".lstrip(),
        encoding="utf-8",
    )

    result = run_checker(workflow)

    assert result.returncode == 2
    assert "broken.yml:jobs.check.steps[0]: Bash syntax error" in result.stderr
    assert result.stdout == ""


def test_non_bash_run_blocks_are_not_parsed_as_bash(tmp_path: Path) -> None:
    workflow = tmp_path / "python.yml"
    workflow.write_text(
        """
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - shell: python
        run: |
          print("Python is not Bash")
""".lstrip(),
        encoding="utf-8",
    )

    result = run_checker(workflow)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "PASS: checked 0 Bash workflow run blocks\n"


def test_implicit_windows_run_blocks_are_not_parsed_as_bash(
    tmp_path: Path,
) -> None:
    workflow = tmp_path / "windows.yml"
    workflow.write_text(
        """
jobs:
  check:
    runs-on: windows-latest
    steps:
      - run: |
          Write-Host (Get-Location)
""".lstrip(),
        encoding="utf-8",
    )

    result = run_checker(workflow)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "PASS: checked 0 Bash workflow run blocks\n"


def test_unclosed_github_expression_fails_closed(tmp_path: Path) -> None:
    workflow = tmp_path / "expression.yml"
    workflow.write_text(
        """
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - run: |
          printf '%s\\n' "${{ github.ref"
""".lstrip(),
        encoding="utf-8",
    )

    result = run_checker(workflow)

    assert result.returncode == 2
    assert (
        "expression.yml:jobs.check.steps[0]: unclosed GitHub expression"
        in result.stderr
    )


def test_fleet_ci_executes_the_bash_syntax_gate() -> None:
    workflow = yaml.load(
        CI_WORKFLOW.read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    matching_steps = [
        step
        for step in workflow["jobs"]["pytest"]["steps"]
        if step.get("name") == "Check Bash syntax in workflow run blocks"
    ]

    assert len(matching_steps) == 1
    result = subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-euo",
            "pipefail",
            "-c",
            matching_steps[0]["run"],
        ],
        cwd=ROOT,
        env={
            "HOME": os.environ["HOME"],
            "PATH": os.environ["PATH"],
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("PASS: checked ")
