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


REVIEW_MODE_EXPRESSION = " ".join(
    """
    ${{
      github.event_name == 'workflow_dispatch' && inputs.force_review && 'request' ||
      contains(github.event.pull_request.labels.*.name, 'review:request') &&
      contains(github.event.pull_request.labels.*.name, 'review:skip') && 'conflict' ||
      contains(github.event.pull_request.labels.*.name, 'review:request') && 'request' ||
      contains(github.event.pull_request.labels.*.name, 'review:skip') && 'skip' ||
      'auto'
    }}
    """.split()
)
SELF_REVIEW_MODE_EXPRESSION = " ".join(
    REVIEW_MODE_EXPRESSION.replace(
        "github.event_name == 'workflow_dispatch' && inputs.force_review && 'request' || ",
        "",
    ).split()
)


ISSUE_RUN_NAME_VALUE = "jhw-review-comment-${{ github.event.comment.id || github.run_id }}"
ISSUE_RUN_NAME_LINE = f"run-name: {ISSUE_RUN_NAME_VALUE}"
ISSUE_RUN_NAME_CALLERS = ("claude.yml", "gemini-dispatch.yml")


EXPECTED_WORKFLOW_NAMES = {
    "auto-rereview-request",
    "claude",
    "claude-code-review",
    "gemini-auto-review",
    "gemini-chat",
    "gemini-dispatch",
    "gemini-invoke",
    "gemini-issue-triage",
    "gemini-pr-review",
    "gemini-review",
    "gemini-scheduled-triage",
    "gemini-triage",
    "opencode",
    "opencode-auto-review",
}

EXPECTED_TRIGGERS: dict[str, object] = {
    "auto-rereview-request.yml": {
        "pull_request": {"types": ["synchronize"]},
        "workflow_dispatch": {
            "inputs": {
                "pr_number": {
                    "description": "PR number to notify reviewers",
                    "required": "true",
                    "type": "number",
                },
                "force_run": {
                    "description": "Run even if disabled in config",
                    "type": "boolean",
                    "default": "false",
                },
            }
        },
    },
    "claude.yml": {
        "issue_comment": {"types": ["created"]},
        "pull_request_review_comment": {"types": ["created"]},
        "issues": {"types": ["opened", "assigned"]},
    },
    "claude-code-review.yml": {
        "pull_request": {"types": ["opened", "synchronize", "ready_for_review", "labeled"]},
        "workflow_dispatch": {
            "inputs": {
                "pr_number": {
                    "description": "Pull request number",
                    "type": "number",
                    "required": "true",
                },
                "force_review": {
                    "description": "Perform one authorized same-HEAD override round; requires the review-budget-override label",
                    "type": "boolean",
                    "required": "false",
                    "default": "false",
                },
            }
        },
    },
    "gemini-auto-review.yml": {
        "pull_request": {"types": ["opened", "synchronize", "ready_for_review", "labeled"]},
        "workflow_dispatch": {
            "inputs": {
                "pr_number": {
                    "description": "Pull request number",
                    "type": "number",
                    "required": "true",
                },
                "force_review": {
                    "description": "Perform one authorized same-HEAD override round; requires the review-budget-override label",
                    "type": "boolean",
                    "required": "false",
                    "default": "false",
                },
            }
        },
    },
    "gemini-chat.yml": {
        "issue_comment": {"types": ["created"]},
        "pull_request_review_comment": {"types": ["created"]},
        "issues": {"types": ["opened", "assigned"]},
    },
    "gemini-dispatch.yml": {
        "pull_request_review_comment": {"types": ["created"]},
        "pull_request": {"types": ["opened"]},
        "issues": {"types": ["opened", "reopened"]},
        "issue_comment": {"types": ["created"]},
    },
    "gemini-invoke.yml": {
        "workflow_call": {
            "inputs": {
                "item_number": {
                    "type": "string",
                    "description": "Issue or pull request number",
                    "required": "true",
                },
                "item_title": {
                    "type": "string",
                    "description": "Issue or pull request title",
                    "required": "true",
                },
                "item_body": {
                    "type": "string",
                    "description": "Issue or pull request body",
                    "required": "true",
                },
                "event_name": {
                    "type": "string",
                    "description": "Caller event name",
                    "required": "true",
                },
                "is_pull_request": {
                    "type": "boolean",
                    "description": (
                        "Whether the caller event is for a pull request"
                    ),
                    "required": "true",
                },
                "additional_context": {
                    "type": "string",
                    "description": "Any additional context from the request",
                    "required": "false",
                },
            }
        }
    },
    "gemini-issue-triage.yml": {
        "workflow_dispatch": {
            "inputs": {
                "issue_number": {
                    "description": "Issue number to triage (e.g. 123)",
                    "required": "true",
                    "type": "string",
                }
            }
        }
    },
    "gemini-pr-review.yml": {
        "workflow_dispatch": {
            "inputs": {
                "pr_number": {
                    "description": "Pull request number to review (e.g. 45)",
                    "required": "true",
                    "type": "string",
                },
                "additional_context": {
                    "description": "Optional extra context for the review prompt",
                    "required": "false",
                    "type": "string",
                },
            }
        }
    },
    "gemini-review.yml": {
        "workflow_call": {
            "inputs": {
                "pr_number": {
                    "type": "string",
                    "description": "Pull request number",
                    "required": "true",
                },
                "issue_title": {
                    "type": "string",
                    "description": "Pull request title",
                    "required": "true",
                },
                "issue_body": {
                    "type": "string",
                    "description": "Pull request body",
                    "required": "true",
                },
                "additional_context": {
                    "type": "string",
                    "description": "Any additional context from the request",
                    "required": "false",
                },
            }
        }
    },
    "gemini-scheduled-triage.yml": {"workflow_dispatch": ""},
    "gemini-triage.yml": {
        "workflow_call": {
            "inputs": {
                "issue_number": {
                    "type": "string",
                    "description": "Issue number",
                    "required": "true",
                },
                "issue_title": {
                    "type": "string",
                    "description": "Issue title",
                    "required": "true",
                },
                "issue_body": {
                    "type": "string",
                    "description": "Issue body",
                    "required": "true",
                },
                "additional_context": {
                    "type": "string",
                    "description": "Any additional context from the request",
                    "required": "false",
                },
            }
        }
    },
    "opencode.yml": {
        "issue_comment": {"types": ["created"]},
        "pull_request_review_comment": {"types": ["created"]},
    },
    "opencode-auto-review.yml": {
        "pull_request": {"types": ["opened", "synchronize", "ready_for_review", "labeled"]},
        "workflow_dispatch": {
            "inputs": {
                "pr_number": {
                    "description": "Pull request number",
                    "type": "number",
                    "required": "true",
                },
                "force_review": {
                    "description": "Perform one authorized same-HEAD override round; requires the review-budget-override label",
                    "type": "boolean",
                    "required": "false",
                    "default": "false",
                },
            }
        },
    },
}

CLAUDE_COMMAND_PERMISSIONS = {
    "actions": "read",
    "contents": "read",
    "id-token": "write",
    "issues": "read",
    "pull-requests": "read",
}
CLAUDE_REVIEW_PERMISSIONS = {
    "actions": "read",
    "contents": "read",
    "id-token": "write",
    "issues": "read",
    "pull-requests": "write",
}
GEMINI_CHAT_PERMISSIONS = {
    "actions": "read",
    "contents": "read",
    "issues": "write",
    "pull-requests": "write",
}
STANDARD_WRITE_PERMISSIONS = {
    "contents": "read",
    "issues": "write",
    "pull-requests": "write",
}
GEMINI_AUTO_REVIEW_PERMISSIONS = {
    "actions": "read",
    **STANDARD_WRITE_PERMISSIONS,
}
OPENCODE_ATTESTED_PERMISSIONS = {
    "actions": "read",
    "checks": "write",
    **STANDARD_WRITE_PERMISSIONS,
}

EXPECTED_CALLER_PERMISSIONS = {
    "auto-rereview-request.yml": ("rereview", STANDARD_WRITE_PERMISSIONS),
    "claude.yml": ("claude", CLAUDE_COMMAND_PERMISSIONS),
    "claude-code-review.yml": ("claude-review", CLAUDE_REVIEW_PERMISSIONS),
    "gemini-auto-review.yml": ("gemini-review", GEMINI_AUTO_REVIEW_PERMISSIONS),
    "gemini-chat.yml": ("gemini-chat", GEMINI_CHAT_PERMISSIONS),
    "gemini-dispatch.yml": ("dispatch", STANDARD_WRITE_PERMISSIONS),
    "gemini-invoke.yml": ("invoke", STANDARD_WRITE_PERMISSIONS),
    "gemini-issue-triage.yml": ("triage", STANDARD_WRITE_PERMISSIONS),
    "gemini-pr-review.yml": ("review", STANDARD_WRITE_PERMISSIONS),
    "gemini-review.yml": ("review", STANDARD_WRITE_PERMISSIONS),
    "gemini-scheduled-triage.yml": ("triage", STANDARD_WRITE_PERMISSIONS),
    "gemini-triage.yml": ("triage", STANDARD_WRITE_PERMISSIONS),
    "opencode.yml": ("opencode", STANDARD_WRITE_PERMISSIONS),
    "opencode-auto-review.yml": ("opencode-review", OPENCODE_ATTESTED_PERMISSIONS),
}


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


def test_bootstrap_config_matches_the_approved_disabled_policy() -> None:
    config = load_yaml(CANONICAL / "workflow-config.yml")

    assert set(config) == {
        "automation_ref",
        "automation_commit",
        "review",
        "workflows",
    }
    assert config["automation_ref"] == "__AUTOMATION_REF__"
    assert config["automation_commit"] == "__AUTOMATION_COMMIT__"
    assert config["review"] == {"auto": "false"}
    assert set(config["workflows"]) == EXPECTED_WORKFLOW_NAMES
    assert all(
        value == {"enabled": "false"}
        for value in config["workflows"].values()
    )


def test_automation_config_has_an_explicit_disabled_review_default() -> None:
    config = load_yaml(ROOT / ".github/workflow-config.yml")

    assert config["review"] == {"auto": "false"}


def test_automation_gemini_app_is_manual_review_only() -> None:
    config = yaml.safe_load((ROOT / ".gemini/config.yaml").read_text())

    assert config == {"code_review": {"pull_request_opened": {"code_review": False}}}
    assert config["code_review"].get("disable") is None


def test_triggers_and_permissions_match_the_approved_policy() -> None:
    workflow_root = CANONICAL / "workflows"
    actual_names = {path.name for path in workflow_root.glob("*.yml")}
    assert actual_names == set(EXPECTED_TRIGGERS)
    assert set(EXPECTED_CALLER_PERMISSIONS) == set(EXPECTED_TRIGGERS)

    for filename, expected_trigger in EXPECTED_TRIGGERS.items():
        workflow = load_yaml(workflow_root / filename)
        assert workflow["on"] == expected_trigger, filename

        contracts = caller_job_contracts(workflow)
        assert len(contracts) == 1, filename
        expected_name, expected_permissions = EXPECTED_CALLER_PERMISSIONS[filename]
        assert contracts[0].name == expected_name, filename
        assert dict(contracts[0].permissions) == expected_permissions, filename


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
                if entry.central_workflow == "gemini-auto-review.yml":
                    assert job["with"]["publisher_app_id"] == "${{ vars.APP_ID }}"
                else:
                    assert "publisher_app_id" not in job["with"]
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


def test_auto_review_callers_forward_the_resolved_review_mode() -> None:
    callers = {
        "claude-code-review.yml": "claude-review",
        "gemini-auto-review.yml": "gemini-review",
        "opencode-auto-review.yml": "opencode-review",
    }
    for filename, job_name in callers.items():
        caller = load_yaml(CANONICAL / "workflows" / filename)
        job = caller["jobs"][job_name]

        assert caller["on"]["pull_request"]["types"] == [
            "opened", "synchronize", "ready_for_review", "labeled",
        ]
        assert "(github.event.action != 'labeled' || github.event.label.name == 'review:request')" in " ".join(job["if"].split())
        assert " ".join(job["with"]["review_mode"].split()) == REVIEW_MODE_EXPRESSION
        assert "github.event.pull_request.draft == false" in job["if"]


def test_self_auto_review_callers_forward_label_review_mode() -> None:
    callers = {
        "_self-claude-review.yml": "claude-review",
        "_self-gemini-auto-review.yml": "gemini-review",
        "_self-opencode-auto-review.yml": "opencode-review",
    }
    for filename, job_name in callers.items():
        caller = load_yaml(ROOT / ".github/workflows" / filename)
        job = caller["jobs"][job_name]

        assert caller["on"]["pull_request"]["types"] == [
            "opened", "synchronize", "ready_for_review", "labeled",
        ]
        assert "(github.event.action != 'labeled' || github.event.label.name == 'review:request')" in " ".join(job["if"].split())
        assert " ".join(job["with"]["review_mode"].split()) == REVIEW_MODE_EXPRESSION
        assert "github.event.pull_request.draft == false" in job["if"]

        # 라운드 소진 후 재판정을 요청할 수 있어야 한다. 예산 액션이 override 라운드에
        # workflow_dispatch provenance 를 요구하므로 트리거와 전달이 함께 있어야 한다.
        assert caller["on"]["workflow_dispatch"]["inputs"]["pr_number"]["type"] == "number"
        assert caller["on"]["workflow_dispatch"]["inputs"]["pr_number"]["required"] == "true"
        force_input = caller["on"]["workflow_dispatch"]["inputs"]["force_review"]
        assert force_input["type"] == "boolean"
        assert force_input["default"] == "false"
        assert job["with"]["force_review"] == (
            "${{ github.event_name == 'workflow_dispatch' && inputs.force_review }}"
        )
        assert job["with"]["pr_number"] == (
            "${{ github.event.pull_request.number || inputs.pr_number }}"
        )
        assert (
            "(github.event_name == 'workflow_dispatch' && inputs.force_review)"
            in " ".join(job["if"].split())
        )

        # && 가 || 보다 강하게 결합하므로, 시크릿 게이트가 있는 caller 는 이벤트 분기
        # 전체를 괄호로 묶어야 workflow_dispatch 경로가 그 게이트를 건너뛰지 않는다.
        condition = " ".join(job["if"].split())
        if "needs.check-secret" in condition:
            assert condition.startswith(
                "needs.check-secret.outputs.has_key == 'true' "
                "&& ((github.event_name == 'pull_request'"
            ), filename
            assert condition.endswith(
                "|| (github.event_name == 'workflow_dispatch' && inputs.force_review))"
            ), filename


def test_comment_callers_carry_the_request_scoped_run_name() -> None:
    for filename in ISSUE_RUN_NAME_CALLERS:
        path = CANONICAL / "workflows" / filename
        matches = [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if re.sub(r"\s+#.*$", "", line) == ISSUE_RUN_NAME_LINE
        ]

        assert len(matches) == 1, filename
        assert load_yaml(path)["run-name"] == ISSUE_RUN_NAME_VALUE, filename
