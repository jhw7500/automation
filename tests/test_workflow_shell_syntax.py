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


def test_expression_placeholder_cannot_collide_with_heredoc(
    tmp_path: Path,
) -> None:
    workflow = tmp_path / "collision.yml"
    workflow.write_text(
        """
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - shell: bash
        run: |
          cat <<"${{ github.ref }}"
          __GITHUB_EXPRESSION__
          if true; then
          ${{ github.ref }}
          fi
""".lstrip(),
        encoding="utf-8",
    )

    result = run_checker(workflow)

    assert result.returncode == 2
    assert "collision.yml:jobs.check.steps[0]: Bash syntax error" in result.stderr


def test_expression_derived_shell_fails_closed(tmp_path: Path) -> None:
    workflow = tmp_path / "dynamic-shell.yml"
    workflow.write_text(
        """
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - shell: ${{ 'bash' }}
        run: |
          if true; then
""".lstrip(),
        encoding="utf-8",
    )

    result = run_checker(workflow)

    assert result.returncode == 2
    assert "dynamic-shell.yml: shell setting is dynamic" in result.stderr


def test_dynamic_runner_implicit_shell_fails_closed(tmp_path: Path) -> None:
    workflow = tmp_path / "dynamic-runner.yml"
    workflow.write_text(
        """
jobs:
  check:
    runs-on: ${{ matrix.os }}
    steps:
      - run: |
          Write-Host (Get-Location)
""".lstrip(),
        encoding="utf-8",
    )

    result = run_checker(workflow)

    assert result.returncode == 2
    assert "dynamic-runner.yml: implicit shell is ambiguous" in result.stderr


def test_container_default_shell_is_not_parsed_as_bash(tmp_path: Path) -> None:
    workflow = tmp_path / "container.yml"
    workflow.write_text(
        """
jobs:
  check:
    runs-on: ubuntu-latest
    container: alpine:3.20
    steps:
      - run: echo container-default-shell
""".lstrip(),
        encoding="utf-8",
    )

    result = run_checker(workflow)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "PASS: checked 0 Bash workflow run blocks\n"


def test_env_wrapped_bash_shell_is_checked(tmp_path: Path) -> None:
    workflow = tmp_path / "wrapped-bash.yml"
    workflow.write_text(
        """
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - shell: /usr/bin/env bash {0}
        run: |
          if true; then
""".lstrip(),
        encoding="utf-8",
    )

    result = run_checker(workflow)

    assert result.returncode == 2
    assert "wrapped-bash.yml:jobs.check.steps[0]: Bash syntax error" in result.stderr


def test_env_assignment_wrapped_bash_shell_is_checked(tmp_path: Path) -> None:
    workflow = tmp_path / "wrapped-bash-env.yml"
    workflow.write_text(
        """
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - shell: /usr/bin/env MODE=ci bash {0}
        run: |
          if true; then
""".lstrip(),
        encoding="utf-8",
    )

    result = run_checker(workflow)

    assert result.returncode == 2
    assert (
        "wrapped-bash-env.yml:jobs.check.steps[0]: Bash syntax error"
        in result.stderr
    )


def test_unclassified_env_wrapper_fails_closed(tmp_path: Path) -> None:
    workflow = tmp_path / "wrapped-bash-option.yml"
    workflow.write_text(
        """
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - shell: /usr/bin/env -i bash {0}
        run: echo isolated-environment
""".lstrip(),
        encoding="utf-8",
    )

    result = run_checker(workflow)

    assert result.returncode == 2
    assert "wrapped-bash-option.yml: env-wrapped shell is ambiguous" in result.stderr


def test_unknown_runner_implicit_shell_fails_closed(tmp_path: Path) -> None:
    workflow = tmp_path / "unknown-runner.yml"
    workflow.write_text(
        """
jobs:
  check:
    runs-on: gpu-pool
    steps:
      - run: echo unknown-default-shell
""".lstrip(),
        encoding="utf-8",
    )

    result = run_checker(workflow)

    assert result.returncode == 2
    assert "unknown-runner.yml: implicit shell is ambiguous" in result.stderr


def test_mapping_windows_runner_default_shell_is_not_bash(tmp_path: Path) -> None:
    workflow = tmp_path / "runner-group.yml"
    workflow.write_text(
        """
jobs:
  check:
    runs-on:
      group: managed-runners
      labels: windows-2025
    steps:
      - run: Write-Host (Get-Location)
""".lstrip(),
        encoding="utf-8",
    )

    result = run_checker(workflow)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "PASS: checked 0 Bash workflow run blocks\n"


def test_dynamic_mapping_runner_implicit_shell_fails_closed(
    tmp_path: Path,
) -> None:
    workflow = tmp_path / "dynamic-runner-group.yml"
    workflow.write_text(
        """
jobs:
  check:
    runs-on:
      group: managed-runners
      labels: ${{ matrix.os == 'windows' && 'windows-2025' || 'ubuntu-latest' }}
    steps:
      - run: echo dynamic-default-shell
""".lstrip(),
        encoding="utf-8",
    )

    result = run_checker(workflow)

    assert result.returncode == 2
    assert "dynamic-runner-group.yml: implicit shell is ambiguous" in result.stderr


def test_fleet_ci_triggers_for_both_workflow_extensions() -> None:
    workflow = yaml.load(
        CI_WORKFLOW.read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )

    for event in ("pull_request", "push"):
        paths = workflow["on"][event]["paths"]
        assert ".github/workflows/*.yml" in paths
        assert ".github/workflows/*.yaml" in paths
        assert "examples/baseline-workflows/.github/**/*.yml" in paths
        assert "examples/baseline-workflows/.github/**/*.yaml" in paths


def test_fleet_ci_wires_the_bash_syntax_gate_once() -> None:
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
    assert matching_steps[0]["shell"] == "bash"
    assert matching_steps[0]["run"].split() == [
        "python3",
        "scripts/check_workflow_shell_syntax.py",
        "\\",
        ".github/workflows",
        "\\",
        "examples/baseline-workflows/.github/workflows",
    ]
