from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/validate_change_evidence.py"
FIXTURES = ROOT / "tests/fixtures/change-evidence"

SPEC = importlib.util.spec_from_file_location("validate_change_evidence", SCRIPT)
assert SPEC and SPEC.loader
validator = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = validator
SPEC.loader.exec_module(validator)


@pytest.mark.parametrize(
    ("kind", "name"),
    [
        ("issue", "valid-issue.md"),
        ("pull-request", "valid-pull-request.md"),
        ("pull-request", "valid-pull-request-no-validation.md"),
        ("commit", "valid-commit.md"),
    ],
)
def test_valid_fixtures_satisfy_v1(kind: str, name: str) -> None:
    result = validator.validate_text((FIXTURES / name).read_text(), kind=kind)

    assert result.valid
    assert result.version == "v1"
    assert result.findings == ()


@pytest.mark.parametrize(
    ("kind", "name", "code"),
    [
        ("issue", "invalid-issue-placeholder.md", "placeholder"),
        ("issue", "invalid-issue-heading-order.md", "heading-contract"),
        ("pull-request", "invalid-pull-request-reference.md", "reference-required"),
        ("commit", "invalid-commit-title.md", "commit-title-generic"),
        ("commit", "invalid-commit-version.md", "version-mismatch"),
    ],
)
def test_invalid_fixtures_fail_with_stable_code(kind: str, name: str, code: str) -> None:
    result = validator.validate_text((FIXTURES / name).read_text(), kind=kind)

    assert not result.valid
    assert code in {finding.code for finding in result.findings}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("Validation", "Unknown: the test owner has not replied."),
        ("Impact and risks", "Not run: risk review was not performed."),
        ("Impact and risks", "Unknown:"),
        ("Impact and risks", "Unknown: TBD"),
    ],
)
def test_absence_sentinels_are_field_specific(field: str, value: str) -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text()
    start = text.index(f"### {field}\n") + len(f"### {field}\n")
    end = text.find("\n### ", start)
    text = text[:start] + value + "\n" + text[end:]

    result = validator.validate_text(text, kind="pull-request")

    assert not result.valid
    assert {item.code for item in result.findings} & {
        "absence-not-allowed",
        "invalid-absence-reason",
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("Validation", "- Not run:"),
        ("Impact and risks", "- Unknown:"),
        ("Impact and risks", "- Unknown: owner response is pending."),
    ],
)
def test_list_wrapped_absence_sentinels_are_rejected(field: str, value: str) -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text()
    start = text.index(f"### {field}\n") + len(f"### {field}\n")
    end = text.find("\n### ", start)
    text = text[:start] + value + "\n" + text[end:]

    result = validator.validate_text(text, kind="pull-request")

    assert "invalid-absence-syntax" in {item.code for item in result.findings}


def test_concrete_prose_may_discuss_placeholder_tokens() -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text().replace(
        "Adds deterministic validation for structured change evidence.",
        "Rejects TODO and N/A placeholders in structured change evidence.",
    ).replace(
        "Repositories must adopt the template before enabling enforcement.",
        "Not applicable: this change removes TODO comments from documentation.",
    )

    result = validator.validate_text(text, kind="pull-request")

    assert result.valid, result.findings


def test_comment_headings_do_not_create_contract_fields() -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text()
    text = text.replace(
        "Adds deterministic validation for structured change evidence.",
        "Adds deterministic validation for structured change evidence.\n<!--\n### Changes\n-->",
    )

    result = validator.validate_text(text, kind="pull-request")

    assert result.valid, result.findings


def test_comment_delimiter_inside_fence_does_not_hide_later_heading() -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text()
    text = text.replace(
        "Adds deterministic validation for structured change evidence.",
        """Adds deterministic validation for structured change evidence.

```markdown
<!-- literal code
```
### Unexpected field
-->""",
    )

    result = validator.validate_text(text, kind="pull-request")

    assert not result.valid
    assert {item.code for item in result.findings} & {
        "heading-contract",
        "unknown-heading",
    }


@pytest.mark.parametrize("literal", ["`<!--`", r"\<!--"])
def test_inline_comment_opener_cannot_hide_later_heading(literal: str) -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text()
    text = text.replace(
        "Adds deterministic validation for structured change evidence.",
        f"""Adds deterministic validation for structured change evidence.
{literal}
### Unexpected field
-->""",
    )

    result = validator.validate_text(text, kind="pull-request")

    assert not result.valid
    assert "ambiguous-comment-opener" in {item.code for item in result.findings}


@pytest.mark.parametrize(
    "prefix",
    [
        "<!-- allowed --> `<!--`",
        r"<!-- allowed --> \<!--",
        "<!--\nallowed --> `<!--`",
    ],
)
def test_later_comment_opener_cannot_hide_heading(prefix: str) -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text()
    text = text.replace(
        "Adds deterministic validation for structured change evidence.",
        f"""Adds deterministic validation for structured change evidence.
{prefix}
### Unexpected field
-->""",
    )

    result = validator.validate_text(text, kind="pull-request")

    assert not result.valid
    assert "ambiguous-comment-opener" in {item.code for item in result.findings}


def test_comment_opener_inside_fence_is_allowed_as_literal_code() -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text()
    text = text.replace(
        "Adds deterministic validation for structured change evidence.",
        """Adds deterministic validation for structured change evidence.

```markdown
<!-- literal code
```
""",
    )

    result = validator.validate_text(text, kind="pull-request")

    assert result.valid, result.findings


def test_comment_opener_inside_indented_code_is_rejected() -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text()
    text = text.replace(
        "Adds deterministic validation for structured change evidence.",
        "Adds deterministic validation for structured change evidence.\n\n    <!-- literal -->",
    )

    result = validator.validate_text(text, kind="pull-request")

    assert not result.valid
    assert "ambiguous-comment-opener" in {item.code for item in result.findings}


def test_fence_markers_inside_comment_do_not_change_scanner_state() -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text()
    text = text.replace(
        "Adds deterministic validation for structured change evidence.",
        """Adds deterministic validation for structured change evidence.
<!--
```markdown
### Hidden comment heading
```
-->""",
    )

    result = validator.validate_text(text, kind="pull-request")

    assert result.valid, result.findings


def test_non_comment_preamble_is_rejected() -> None:
    text = "Unversioned summary\n\n" + (FIXTURES / "valid-issue.md").read_text()

    result = validator.validate_text(text, kind="issue")

    assert "unexpected-preamble" in {item.code for item in result.findings}


def test_indented_extra_heading_is_rejected() -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text().replace(
        "Adds deterministic validation for structured change evidence.",
        "Delivered result.\n ### Unexpected heading\nAdditional section.",
    )

    result = validator.validate_text(text, kind="pull-request")

    assert not result.valid
    assert {item.code for item in result.findings} & {
        "heading-contract",
        "unknown-heading",
    }


def test_four_backtick_fence_keeps_nested_triple_fence_and_heading_as_code() -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text()
    text = text.replace(
        "Adds deterministic validation for structured change evidence.",
        """Adds deterministic validation for structured change evidence.

````markdown
```not-a-close-for-four-backticks
### Rendered as code
```
````""",
    )

    result = validator.validate_text(text, kind="pull-request")

    assert result.valid, result.findings


def test_contract_fields_rendered_inside_code_fence_are_not_accepted() -> None:
    fields = (FIXTURES / "valid-pull-request.md").read_text().split(
        "### Summary\n", 1
    )[1]
    text = """### Contract version
v1

````markdown
```not-a-close-for-four-backticks
### Summary
""" + fields + "\n````\n"

    result = validator.validate_text(text, kind="pull-request")

    assert not result.valid
    assert "heading-contract" in {item.code for item in result.findings}


def test_pull_request_url_does_not_satisfy_related_issue() -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text().replace(
        "Closes #194", "https://github.com/jhw7500/automation/pull/194"
    )

    result = validator.validate_text(text, kind="pull-request")

    assert "reference-required" in {item.code for item in result.findings}


def test_pull_request_url_is_valid_commit_reference() -> None:
    text = (FIXTURES / "valid-commit.md").read_text().replace(
        "Issue: #194", "PR: https://github.com/jhw7500/automation/pull/194"
    )

    result = validator.validate_text(text, kind="commit")

    assert result.valid, result.findings


@pytest.mark.parametrize("reference", ["PR: #194", "PR #194", "Pull request #194"])
def test_explicit_pr_label_does_not_satisfy_related_issue(reference: str) -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text().replace(
        "Closes #194", reference
    )

    result = validator.validate_text(text, kind="pull-request")

    assert "reference-required" in {item.code for item in result.findings}


def test_mixed_issue_and_pr_labels_satisfy_related_issue() -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text().replace(
        "Closes #194", "PR: #193\nIssue: #194"
    )

    result = validator.validate_text(text, kind="pull-request")

    assert result.valid, result.findings


def test_ordered_list_satisfies_changes_field() -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text()
    start = text.index("### Changes\n") + len("### Changes\n")
    end = text.find("\n### ", start)
    text = text[:start] + "1. Define the v1 headings and field rules.\n" + text[end:]

    result = validator.validate_text(text, kind="pull-request")

    assert result.valid, result.findings


@pytest.mark.parametrize(
    ("kind", "fixture", "field", "replacement", "code"),
    [
        (
            "pull-request",
            "valid-pull-request.md",
            "Changes",
            "```markdown\n- hidden change\n```",
            "bullet-required",
        ),
        (
            "pull-request",
            "valid-pull-request.md",
            "Changes",
            "    - indented code, not a visible list",
            "bullet-required",
        ),
        (
            "issue",
            "valid-issue.md",
            "Acceptance criteria",
            "```markdown\n- [ ] hidden criterion\n```",
            "checklist-required",
        ),
        (
            "pull-request",
            "valid-pull-request.md",
            "Related issue",
            "```markdown\nIssue: #194\n```",
            "reference-required",
        ),
        (
            "pull-request",
            "valid-pull-request.md",
            "Related issue",
            " \t#194",
            "reference-required",
        ),
    ],
)
def test_code_content_does_not_satisfy_visible_structure(
    kind: str, fixture: str, field: str, replacement: str, code: str
) -> None:
    text = (FIXTURES / fixture).read_text()
    start = text.index(f"### {field}\n") + len(f"### {field}\n")
    end = text.find("\n### ", start)
    if end == -1:
        end = len(text)
    text = text[:start] + replacement + "\n" + text[end:]

    result = validator.validate_text(text, kind=kind)

    assert code in {item.code for item in result.findings}


def test_cli_enforces_or_audits_the_same_invalid_document(tmp_path: Path) -> None:
    source = FIXTURES / "invalid-pull-request-reference.md"
    enforce = subprocess.run(
        [sys.executable, str(SCRIPT), "--kind", "pull-request", "--path", str(source), "--format", "json"],
        check=False,
        capture_output=True,
        text=True,
    )
    audit_output = tmp_path / "github-output"
    audit = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--kind",
            "pull-request",
            "--path",
            str(source),
            "--mode",
            "audit",
            "--format",
            "json",
            "--github-output",
            str(audit_output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert enforce.returncode == 1
    assert json.loads(enforce.stdout)["valid"] is False
    assert audit.returncode == 0
    assert json.loads(audit.stdout)["findings"] == json.loads(enforce.stdout)["findings"]
    assert "valid=false\n" in audit_output.read_text()


def test_github_outputs_cannot_be_injected_by_multiline_version(tmp_path: Path) -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text().replace(
        "### Contract version\nv1\n",
        "### Contract version\nv2\nvalid=true\nkind=commit\n",
    )
    result = validator.validate_text(text, kind="pull-request")
    output = tmp_path / "github-output"

    validator._write_github_output(output, result)

    records = output.read_text().splitlines()
    assert records[:3] == ["valid=false", "kind=pull-request", "version="]
    assert sum(item.startswith("valid=") for item in records) == 1
    assert sum(item.startswith("kind=") for item in records) == 1
    report = json.loads(records[3].split("=", 1)[1])
    assert report["version"] == "v2\nvalid=true\nkind=commit"
    assert report["valid"] is False


def test_workspace_mode_rejects_symlink_evidence(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "target.md"
    target.write_text((FIXTURES / "valid-issue.md").read_text())
    (workspace / "evidence.md").symlink_to(target)

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--kind",
            "issue",
            "--path",
            "evidence.md",
            "--workspace",
            str(workspace),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "must not contain symlinks" in completed.stderr


def test_composite_action_exposes_closed_inputs_and_outputs() -> None:
    action = yaml.load(
        (ROOT / ".github/actions/validate-change-evidence/action.yml").read_text(),
        Loader=yaml.BaseLoader,
    )

    assert set(action["inputs"]) == {"kind", "path", "contract-version", "mode"}
    assert set(action["outputs"]) == {"valid", "kind", "version", "report-json"}
    assert action["runs"]["using"] == "composite"
    command = action["runs"]["steps"][0]["run"]
    assert "$GITHUB_ACTION_PATH/../../../scripts/validate_change_evidence.py" in command
    assert "--workspace \"$GITHUB_WORKSPACE\"" in command


def test_issue_form_labels_match_issue_contract() -> None:
    form = yaml.safe_load(
        (ROOT / ".github/ISSUE_TEMPLATE/change-evidence.yml").read_text()
    )
    labels = tuple(item["attributes"]["label"] for item in form["body"])

    assert labels == validator.CONTRACTS["issue"].headings
    assert form["body"][0]["attributes"]["options"] == ["v1"]
    assert all(item["validations"]["required"] for item in form["body"])


def test_pull_request_template_has_exact_fields_and_requires_completion() -> None:
    template = (ROOT / ".github/pull_request_template.md").read_text()

    result = validator.validate_text(template, kind="pull-request")

    headings = tuple(section[0] for section in validator._parse_sections(template)[0])
    assert headings == validator.CONTRACTS["pull-request"].headings
    assert not result.valid
    assert {item.code for item in result.findings} == {"empty-field"}


def test_change_evidence_fixtures_trigger_fleet_tests() -> None:
    workflow = (ROOT / ".github/workflows/test-fleet-tools.yml").read_text()

    assert workflow.count("- 'tests/fixtures/change-evidence/**'") == 2
