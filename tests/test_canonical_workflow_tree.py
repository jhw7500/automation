#!/usr/bin/env python3
"""Contracts for the one repository-consumer workflow tree."""

from pathlib import Path
import re
import sys

import yaml


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "examples/baseline-workflows/.github"
sys.path.insert(0, str(ROOT))

from scripts.workflow_catalog import (  # noqa: E402
    CallerJobContract,
    CatalogEntry,
    extract_caller_jobs,
    load_catalog,
)


def load_yaml(path: Path) -> dict[str, object]:
    value = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(value, dict), path
    return value


def canonical_text(entry: CatalogEntry) -> str:
    return (CANONICAL / entry.path.relative_to(".github")).read_text(
        encoding="utf-8"
    )


def caller_job_contracts(
    workflow: dict[str, object],
) -> tuple[CallerJobContract, ...]:
    return extract_caller_jobs(workflow)


def central_accepts(entry: CatalogEntry, central_root: Path) -> bool:
    assert entry.central_workflow is not None
    central = load_yaml(central_root / entry.central_workflow)
    call = central["on"]["workflow_call"]
    declared_inputs = set(call.get("inputs", {}))
    declared_secrets = call.get("secrets", {})
    required_secrets = {
        name
        for name, value in declared_secrets.items()
        if value.get("required", "false") == "true"
    }
    return all(
        set(job.with_keys) <= declared_inputs
        and set(job.secrets) <= set(declared_secrets)
        and required_secrets <= set(job.secrets)
        for job in entry.caller_jobs
    )


def test_canonical_tree_is_exactly_catalogued() -> None:
    catalog = load_catalog(ROOT)
    root = CANONICAL
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }
    expected = {
        entry.path.relative_to(".github").as_posix()
        for entry in catalog.entries
        if entry.kind != "retired"
    }
    assert actual == expected
    assert not (ROOT / "examples/baseline-workflows/workflows").exists()
    assert not (ROOT / "examples/baseline-workflows/workflow-config.yml").exists()


def test_canonical_callers_match_catalog_and_central_contracts() -> None:
    for entry in load_catalog(ROOT).callers:
        workflow = load_yaml(CANONICAL / entry.path.relative_to(".github"))
        assert workflow["on"] == entry.trigger
        assert caller_job_contracts(workflow) == entry.caller_jobs
        assert "@__AUTOMATION_COMMIT__" in canonical_text(entry)
        assert central_accepts(entry, ROOT / ".github/workflows")


def test_canonical_callers_use_only_the_selected_auth_contract() -> None:
    reusable_ref = re.compile(
        r"jhw7500/automation/\.github/workflows/[^@\s'\"]+@"
        r"(?!__AUTOMATION_COMMIT__)[^\s'\"]+"
    )
    for entry in load_catalog(ROOT).callers:
        path = CANONICAL / entry.path.relative_to(".github")
        text = path.read_text(encoding="utf-8")
        workflow = load_yaml(path)

        assert "secrets: inherit" not in text, path
        assert "GOOGLE_API_KEY" not in text, path
        if entry.auth_family == "gemini":
            assert "id-token:" not in text, path
        assert reusable_ref.search(text) is None, path

        reusable_jobs = [
            job
            for job in workflow["jobs"].values()
            if isinstance(job, dict)
            and "jhw7500/automation/.github/workflows/" in job.get("uses", "")
        ]
        assert reusable_jobs, path
        for job in reusable_jobs:
            if entry.auth_family == "gemini":
                assert job["with"]["repo_write_auth"] == "github_app"
                assert job["with"]["app_id"] == "${{ vars.APP_ID }}"
                assert job["secrets"] == {
                    "APP_PRIVATE_KEY": "${{ secrets.APP_PRIVATE_KEY }}",
                    "GEMINI_API_KEY": "${{ secrets.GEMINI_API_KEY }}",
                }
            elif entry.auth_family == "claude":
                assert job["secrets"] == {
                    "CLAUDE_CODE_OAUTH_TOKEN": (
                        "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}"
                    )
                }
            elif entry.auth_family == "opencode":
                assert job["secrets"] == {
                    "ZHIPU_API_KEY": "${{ secrets.ZHIPU_API_KEY }}"
                }
            else:
                assert "secrets" not in job
