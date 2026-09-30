from __future__ import annotations

import importlib.util
import json
import os
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


def test_comment_only_commit_title_is_rejected() -> None:
    text = (FIXTURES / "valid-commit.md").read_text()
    text = "<!-- hidden title -->" + text[text.index("\n") :]

    result = validator.validate_text(text, kind="commit")

    assert not result.valid
    assert "commit-title-empty" in {item.code for item in result.findings}


def test_canonical_commit_template_title_is_rejected() -> None:
    text = (FIXTURES / "valid-commit.md").read_text()
    template_title = (
        ROOT / "examples/change-evidence/commit-message.md"
    ).read_text().splitlines()[0]
    text = template_title + text[text.index("\n") :]

    result = validator.validate_text(text, kind="commit")

    assert not result.valid
    assert "commit-title-placeholder" in {item.code for item in result.findings}


@pytest.mark.parametrize(
    "title",
    ["**Update**", "[Fix](https://example.invalid)", "`WIP`"],
)
def test_formatted_generic_commit_title_is_rejected(title: str) -> None:
    text = (FIXTURES / "valid-commit.md").read_text()
    text = title + text[text.index("\n") :]

    result = validator.validate_text(text, kind="commit")

    assert not result.valid
    assert "commit-title-generic" in {item.code for item in result.findings}


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


@pytest.mark.parametrize(
    "value",
    [
        "- **Not run:** no tests were executed.",
        "- [ ] *Not run:* no tests were executed.",
        "> **Not run:** no tests were executed.",
    ],
)
def test_formatted_container_wrapped_absence_sentinels_are_rejected(
    value: str,
) -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text()
    start = text.index("### Validation\n") + len("### Validation\n")
    end = text.find("\n### ", start)
    text = text[:start] + value + "\n" + text[end:]

    result = validator.validate_text(text, kind="pull-request")

    assert not result.valid
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


@pytest.mark.parametrize("value", ["**TBD**", "`TODO`", "[N/A](https://example.invalid)"])
def test_formatted_placeholder_only_field_is_rejected(value: str) -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text().replace(
        "Repositories must adopt the template before enabling enforcement.", value
    )

    result = validator.validate_text(text, kind="pull-request")

    assert not result.valid
    assert "placeholder" in {item.code for item in result.findings}


def test_empty_html_markup_does_not_satisfy_required_field() -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text().replace(
        "Adds deterministic validation for structured change evidence.",
        "<span></span>",
    )

    result = validator.validate_text(text, kind="pull-request")

    assert not result.valid
    assert "raw-html" in {item.code for item in result.findings}


@pytest.mark.parametrize("field", ["Summary", "Impact and risks"])
def test_default_ignorable_unicode_does_not_satisfy_required_field(
    field: str,
) -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text()
    start = text.index(f"### {field}\n") + len(f"### {field}\n")
    end = text.find("\n### ", start)
    text = text[:start] + "\u200b\n" + text[end:]

    result = validator.validate_text(text, kind="pull-request")

    assert not result.valid
    assert "empty-field" in {item.code for item in result.findings}


@pytest.mark.parametrize("value", ["\u034f", "\ufe00"])
def test_combining_or_variation_character_does_not_satisfy_field(
    value: str,
) -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text().replace(
        "Adds deterministic validation for structured change evidence.", value
    )

    result = validator.validate_text(text, kind="pull-request")

    assert not result.valid
    assert "empty-field" in {item.code for item in result.findings}


def test_combining_characters_do_not_satisfy_structural_evidence() -> None:
    pull_request = (FIXTURES / "valid-pull-request.md").read_text()
    pull_request = pull_request.replace(
        "- Define the v1 headings and field rules.\n"
        "- Add a dependency-free validator and reusable action.",
        "- \u034f",
    ).replace(
        "- `pytest -q tests/test_change_evidence.py` passed.",
        "- \ufe00",
    )
    issue = (FIXTURES / "valid-issue.md").read_text()
    start = issue.index("### Acceptance criteria\n") + len(
        "### Acceptance criteria\n"
    )
    end = issue.find("\n### ", start)
    issue = issue[:start] + "- [ ] \u034f\n" + issue[end:]

    pull_request_result = validator.validate_text(
        pull_request, kind="pull-request"
    )
    issue_result = validator.validate_text(issue, kind="issue")

    assert not pull_request_result.valid
    assert not issue_result.valid
    assert {item.code for item in pull_request_result.findings} & {
        "empty-field",
        "bullet-required",
    }
    assert {item.code for item in issue_result.findings} & {
        "empty-field",
        "checklist-required",
    }


def test_variation_character_does_not_satisfy_commit_title() -> None:
    text = (FIXTURES / "valid-commit.md").read_text()
    text = "\ufe00" + text[text.index("\n") :]

    result = validator.validate_text(text, kind="commit")

    assert not result.valid
    assert "commit-title-empty" in {item.code for item in result.findings}


@pytest.mark.parametrize("field", ["Changes", "Validation"])
def test_syntax_only_list_item_does_not_satisfy_bullet_field(field: str) -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text()
    start = text.index(f"### {field}\n") + len(f"### {field}\n")
    end = text.find("\n### ", start)
    text = text[:start] + "- []()\n" + text[end:]

    result = validator.validate_text(text, kind="pull-request")

    assert not result.valid
    assert {item.code for item in result.findings} & {
        "empty-field",
        "bullet-required",
    }


def test_syntax_only_task_item_does_not_satisfy_acceptance_criteria() -> None:
    text = (FIXTURES / "valid-issue.md").read_text()
    start = text.index("### Acceptance criteria\n") + len(
        "### Acceptance criteria\n"
    )
    end = text.find("\n### ", start)
    text = text[:start] + "- [ ] []()\n" + text[end:]

    result = validator.validate_text(text, kind="issue")

    assert not result.valid
    assert {item.code for item in result.findings} & {
        "empty-field",
        "checklist-required",
    }


@pytest.mark.parametrize(
    ("kind", "fixture", "field", "replacement"),
    [
        (
            "pull-request",
            "valid-pull-request.md",
            "Summary",
            '<span\nclass="empty"></span>',
        ),
        (
            "pull-request",
            "valid-pull-request.md",
            "Changes",
            "<div>\n- hidden change\n</div>",
        ),
        (
            "issue",
            "valid-issue.md",
            "Acceptance criteria",
            "<div>\n- [ ] hidden criterion\n</div>",
        ),
        (
            "pull-request",
            "valid-pull-request.md",
            "Related issue",
            "<div>\nCloses #194\n</div>",
        ),
    ],
)
def test_raw_html_cannot_supply_contract_evidence(
    kind: str, fixture: str, field: str, replacement: str
) -> None:
    text = (FIXTURES / fixture).read_text()
    start = text.index(f"### {field}\n") + len(f"### {field}\n")
    end = text.find("\n### ", start)
    if end == -1:
        end = len(text)
    text = text[:start] + replacement + "\n" + text[end:]

    result = validator.validate_text(text, kind=kind)

    assert not result.valid
    assert "raw-html" in {item.code for item in result.findings}


@pytest.mark.parametrize(
    ("replacement", "raw_html"),
    [
        (r"\<span>escaped literal\</span>", False),
        (r"\\<img src=x onerror=alert(1)>", True),
        ("Delivered result.\n\n- parent\n    <span>raw</span>", True),
        ("Delivered result.\n    <span>raw</span>", True),
        (
            "Delivered result.\n\n- parent\n    ```html\n"
            "    <span>literal</span>\n    ```",
            False,
        ),
    ],
)
def test_raw_html_respects_escapes_and_list_relative_indentation(
    replacement: str, raw_html: bool
) -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text().replace(
        "Adds deterministic validation for structured change evidence.",
        replacement,
    )

    result = validator.validate_text(text, kind="pull-request")
    codes = {item.code for item in result.findings}

    assert ("raw-html" in codes) is raw_html
    assert result.valid is not raw_html


def test_markdown_autolink_issue_reference_remains_valid() -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text().replace(
        "Closes #194", "<https://github.com/jhw7500/automation/issues/194>"
    )

    result = validator.validate_text(text, kind="pull-request")

    assert result.valid, result.findings


@pytest.mark.parametrize(
    "replacement",
    [
        "Documents literal `<span>` syntax.",
        "```html\n<span>literal example</span>\n```",
    ],
)
def test_html_literal_in_code_is_not_raw_html(replacement: str) -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text().replace(
        "Adds deterministic validation for structured change evidence.",
        replacement,
    )

    result = validator.validate_text(text, kind="pull-request")

    assert result.valid, result.findings


def test_fenced_code_content_satisfies_prose_field() -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text().replace(
        "Adds deterministic validation for structured change evidence.",
        "```text\nobserved validator output\n```",
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


@pytest.mark.parametrize("underline", ["-------------", "============="])
def test_setext_heading_is_rejected(underline: str) -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text().replace(
        "Adds deterministic validation for structured change evidence.",
        f"Adds deterministic validation for structured change evidence.\n\nUnexpected heading\n{underline}",
    )

    result = validator.validate_text(text, kind="pull-request")

    assert not result.valid
    assert "setext-heading" in {item.code for item in result.findings}


def test_link_reference_definition_does_not_satisfy_related_issue() -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text().replace(
        "Closes #194", "[hidden]: https://example.invalid/#194"
    )

    result = validator.validate_text(text, kind="pull-request")

    assert not result.valid
    assert "non-rendered-link-definition" in {
        item.code for item in result.findings
    }


def test_multiline_link_reference_definition_does_not_satisfy_related_issue() -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text().replace(
        "Closes #194",
        "[hidden]:\n  https://github.com/example/repo/issues/194",
    )

    result = validator.validate_text(text, kind="pull-request")

    assert not result.valid
    assert "non-rendered-link-definition" in {
        item.code for item in result.findings
    }


def test_multiline_label_link_definition_does_not_satisfy_related_issue() -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text().replace(
        "Closes #194",
        "[hidden\nlabel]: https://github.com/example/repo/issues/194",
    )

    result = validator.validate_text(text, kind="pull-request")

    assert not result.valid
    assert "non-rendered-link-definition" in {
        item.code for item in result.findings
    }


def test_escaped_label_link_definition_does_not_satisfy_related_issue() -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text().replace(
        "Closes #194",
        r"[hidden\]]: https://github.com/example/repo/issues/194",
    )

    result = validator.validate_text(text, kind="pull-request")

    assert not result.valid
    assert "non-rendered-link-definition" in {
        item.code for item in result.findings
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


@pytest.mark.parametrize(
    "reference",
    [
        "PR: **#194**",
        "PR: <strong>#194</strong>",
        "PR: [#194](https://example.invalid/reference)",
    ],
)
def test_formatted_pr_label_does_not_satisfy_related_issue(reference: str) -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text().replace(
        "Closes #194", reference
    )

    result = validator.validate_text(text, kind="pull-request")

    assert "reference-required" in {item.code for item in result.findings}


def test_softbreak_pr_label_does_not_satisfy_related_issue() -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text().replace(
        "Closes #194", "PR:\n#194"
    )

    result = validator.validate_text(text, kind="pull-request")

    assert "reference-required" in {item.code for item in result.findings}


@pytest.mark.parametrize("label", ["PR:", "Pull request:"])
def test_long_spaced_pr_label_does_not_satisfy_related_issue(label: str) -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text().replace(
        "Closes #194", label + " " * 40 + "#194"
    )

    result = validator.validate_text(text, kind="pull-request")

    assert "reference-required" in {item.code for item in result.findings}


@pytest.mark.parametrize(
    "reference",
    [
        'Context [documentation](https://example.invalid "https://github.com/example/repo/issues/194")',
        "Context ![](https://github.com/example/repo/issues/194)",
    ],
)
def test_hidden_issue_url_does_not_satisfy_related_issue(reference: str) -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text().replace(
        "Closes #194", reference
    )

    result = validator.validate_text(text, kind="pull-request")

    assert "reference-required" in {item.code for item in result.findings}


@pytest.mark.parametrize(
    "reference",
    [
        "https://github.com/example/repo/issues/194",
        "<https://github.com/example/repo/issues/194>",
        "[Issue](https://github.com/example/repo/issues/194)",
    ],
)
def test_rendered_or_navigable_issue_url_satisfies_related_issue(
    reference: str,
) -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text().replace(
        "Closes #194", reference
    )

    result = validator.validate_text(text, kind="pull-request")

    assert result.valid, result.findings


@pytest.mark.parametrize(
    "reference",
    [
        "[Issue](https://github.com/example/repo/issues/194)",
        "[Pull request](https://github.com/example/repo/pull/194)",
    ],
)
def test_navigable_change_url_satisfies_commit_reference(reference: str) -> None:
    text = (FIXTURES / "valid-commit.md").read_text().replace(
        "Issue: #194", reference
    )

    result = validator.validate_text(text, kind="commit")

    assert result.valid, result.findings


@pytest.mark.parametrize(
    "reference",
    [
        "Context ![](https://github.com/example/repo/issues/194)",
        '[documentation](https://example.invalid "https://github.com/example/repo/issues/194")',
    ],
)
def test_hidden_change_url_does_not_satisfy_commit_reference(
    reference: str,
) -> None:
    text = (FIXTURES / "valid-commit.md").read_text().replace(
        "Issue: #194", reference
    )

    result = validator.validate_text(text, kind="commit")

    assert "reference-required" in {item.code for item in result.findings}


def test_nested_issue_reference_does_not_satisfy_related_issue() -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text().replace(
        "Closes #194", "- Parent item\n  - Closes #194"
    )

    result = validator.validate_text(text, kind="pull-request")

    assert "reference-required" in {item.code for item in result.findings}


def test_blockquoted_issue_reference_does_not_satisfy_related_issue() -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text().replace(
        "Closes #194", "> Closes #194"
    )

    result = validator.validate_text(text, kind="pull-request")

    assert "reference-required" in {item.code for item in result.findings}


def test_lazy_blockquote_issue_reference_does_not_satisfy_related_issue() -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text().replace(
        "Closes #194", "> context\nCloses #194"
    )

    result = validator.validate_text(text, kind="pull-request")

    assert "reference-required" in {item.code for item in result.findings}


def test_empty_blockquote_does_not_hide_following_issue_reference() -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text().replace(
        "Closes #194", ">\nCloses #194"
    )

    result = validator.validate_text(text, kind="pull-request")

    assert result.valid, result.findings


def test_empty_link_paragraph_keeps_lazy_blockquote_continuation() -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text().replace(
        "Closes #194", "> []()\nCloses #194"
    )

    result = validator.validate_text(text, kind="pull-request")

    assert "reference-required" in {item.code for item in result.findings}


def test_block_only_markdown_does_not_satisfy_required_evidence() -> None:
    commit = (FIXTURES / "valid-commit.md").read_text()
    commit = "#" + commit[commit.index("\n") :]
    pull_request = (FIXTURES / "valid-pull-request.md").read_text()
    pull_request = pull_request.replace(
        "- Define the v1 headings and field rules.\n"
        "- Add a dependency-free validator and reusable action.",
        "- #",
    ).replace(
        "- `pytest -q tests/test_change_evidence.py` passed.",
        "- ---",
    )

    commit_result = validator.validate_text(commit, kind="commit")
    pull_request_result = validator.validate_text(
        pull_request, kind="pull-request"
    )

    assert not commit_result.valid
    assert "commit-title-empty" in {item.code for item in commit_result.findings}
    assert not pull_request_result.valid
    assert "empty-field" in {
        item.code for item in pull_request_result.findings
    }


@pytest.mark.parametrize("value", ["-", "- -", "- - -", "- 1.", "1."])
def test_empty_list_markers_do_not_satisfy_required_evidence(value: str) -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text().replace(
        "Adds deterministic validation for structured change evidence.", value
    )

    result = validator.validate_text(text, kind="pull-request")

    assert not result.valid
    assert "empty-field" in {item.code for item in result.findings}


def test_inline_link_issue_label_satisfies_related_issue() -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text().replace(
        "Closes #194", "Issue: [#194](https://example.invalid/reference)"
    )

    result = validator.validate_text(text, kind="pull-request")

    assert result.valid, result.findings


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


@pytest.mark.parametrize("marker", ["1.", "1)"])
def test_ordered_task_list_satisfies_acceptance_criteria(marker: str) -> None:
    text = (FIXTURES / "valid-issue.md").read_text().replace(
        "- [ ] Validator accepts every valid v1 fixture.",
        f"{marker} [ ] Validator accepts every valid v1 fixture.",
    )

    result = validator.validate_text(text, kind="issue")

    assert result.valid, result.findings


def test_nested_task_list_does_not_satisfy_acceptance_criteria() -> None:
    text = (FIXTURES / "valid-issue.md").read_text()
    start = text.index("### Acceptance criteria\n") + len(
        "### Acceptance criteria\n"
    )
    end = text.find("\n### ", start)
    text = text[:start] + "- Parent item\n  - [ ] Nested criterion\n" + text[end:]

    result = validator.validate_text(text, kind="issue")

    assert "checklist-required" in {item.code for item in result.findings}


def test_contract_headings_nested_in_list_are_rejected() -> None:
    text = """### Contract version
v1

### Summary
Delivered result.

- parent
  ### Changes
  - hidden change
  ### Validation
  - hidden validation
  ### Impact and risks
  Not applicable: no runtime impact.
  ### Related issue
  Closes #194
"""

    result = validator.validate_text(text, kind="pull-request")

    assert not result.valid
    assert "nested-heading" in {item.code for item in result.findings}


@pytest.mark.parametrize(
    ("kind", "fixture"),
    [
        ("issue", "valid-issue.md"),
        ("pull-request", "valid-pull-request.md"),
        ("commit", "valid-commit.md"),
    ],
)
def test_top_level_heading_may_immediately_follow_list_field(
    kind: str, fixture: str
) -> None:
    text = (FIXTURES / fixture).read_text()
    contract_start = text.index("### Contract version")
    text = text[:contract_start] + text[contract_start:].replace(
        "\n\n### ", "\n### "
    )

    result = validator.validate_text(text, kind=kind)

    assert result.valid, result.findings


@pytest.mark.parametrize(
    "separator",
    ["\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"],
)
def test_non_markdown_line_separator_cannot_create_list_item(
    separator: str,
) -> None:
    text = (FIXTURES / "valid-pull-request.md").read_text()
    start = text.index("### Changes\n") + len("### Changes\n")
    end = text.find("\n### ", start)
    text = text[:start] + f"Intro{separator}- hidden change\n" + text[end:]

    result = validator.validate_text(text, kind="pull-request")

    assert not result.valid
    assert "unsupported-line-separator" in {
        item.code for item in result.findings
    }


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


def test_maximum_input_findings_and_github_outputs_are_bounded(
    tmp_path: Path,
) -> None:
    source = tmp_path / "evidence.md"
    source.write_text("<a>" * (validator.MAX_BYTES // 3))
    reports: list[dict[str, object]] = []

    for mode, expected_code in (("enforce", 1), ("audit", 0)):
        github_output = tmp_path / f"github-output-{mode}"
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--kind",
                "issue",
                "--path",
                str(source),
                "--mode",
                mode,
                "--format",
                "json",
                "--github-output",
                str(github_output),
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        assert completed.returncode == expected_code, completed.stderr
        report = json.loads(completed.stdout)
        reports.append(report)
        assert len(report["findings"]) <= validator.MAX_FINDINGS
        assert report["findings"][-1]["code"] == "findings-truncated"
        assert (
            len(github_output.read_text().encode("utf-16-le"))
            < 1024 * 1024
        )

    assert reports[0] == reports[1]


def test_github_output_io_failure_returns_operational_error(
    tmp_path: Path,
) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--kind",
            "issue",
            "--path",
            str(FIXTURES / "valid-issue.md"),
            "--github-output",
            str(tmp_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "Is a directory" in completed.stderr
    assert "Traceback" not in completed.stderr


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


@pytest.mark.parametrize("workspace_mode", [False, True])
def test_fifo_evidence_returns_promptly(
    tmp_path: Path, workspace_mode: bool
) -> None:
    fifo = tmp_path / "evidence.md"
    os.mkfifo(fifo)
    path = "evidence.md" if workspace_mode else str(fifo)
    command = [
        sys.executable,
        str(SCRIPT),
        "--kind",
        "issue",
        "--path",
        path,
    ]
    if workspace_mode:
        command.extend(["--workspace", str(tmp_path)])

    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=2,
    )

    assert completed.returncode == 2
    assert "non-symlink regular file" in completed.stderr


def test_workspace_read_rejects_symlink_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    evidence = workspace / "evidence.md"
    evidence.write_text((FIXTURES / "valid-issue.md").read_text())
    outside = tmp_path / "outside.md"
    outside.write_text("OUTSIDE")
    replacement = workspace / "replacement"
    replacement.symlink_to(outside)

    original_open = validator._open_no_follow
    swapped = False

    def swap_before_open(
        path: str | os.PathLike[str],
        flags: int,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        if path == "evidence.md" and dir_fd is not None and not swapped:
            os.replace(replacement, evidence)
            swapped = True
        return original_open(path, flags, dir_fd=dir_fd)

    monkeypatch.setattr(validator, "_open_no_follow", swap_before_open)

    with pytest.raises(ValueError, match="must not contain symlinks"):
        validator._open_regular_file(Path("evidence.md"), workspace)

    assert swapped


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
