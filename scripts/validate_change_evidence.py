#!/usr/bin/env python3
"""Validate Markdown against Change Evidence Contract v1."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import errno
import html
import json
import os
from pathlib import Path
import re
import stat
import sys
import unicodedata
from typing import Sequence


CONTRACT_VERSION = "v1"
MAX_BYTES = 64 * 1024
HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*$")
FENCE_RE = re.compile(r"^(?P<indent> {0,3})(?P<run>`{3,}|~{3,})(?P<rest>[^\r\n]*)$")
LINK_DEFINITION_RE = re.compile(
    r"^ {0,3}\[(?:\\.|[^\[\]\\])+\]:[ \t]*(?:\S.*)?$",
    re.DOTALL,
)
SETEXT_UNDERLINE_RE = re.compile(r"^ {0,3}(?:=+|-+)[ \t]*$")
PLACEHOLDER_VALUE_RE = re.compile(
    r"(?i)^(?:tbd|todo|fixme|n/?a|none|unknown|미정|추후|없음|\?{2,})[.。]?$"
)
ANGLE_PLACEHOLDER_RE = re.compile(
    r"(?i)^<(?:(?:fill|insert|describe|add)[^>]*|[^>]+ here)>$"
)
ISSUE_URL_RE = re.compile(
    r"(?i)https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/issues/[1-9][0-9]*\b"
)
BARE_REFERENCE_RE = re.compile(
    r"(?<![A-Za-z0-9_./?=&%+-])#[1-9][0-9]*\b"
)
PR_LABEL_RE = re.compile(r"(?i)\b(?:pr|pull request)\s*:?[ \t]*$")
CHANGE_REFERENCE_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_./?=&%+-])#[1-9][0-9]*\b|"
    r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/(?:issues|pull)/[1-9][0-9]*\b"
)
LIST_MARKER = r"(?:[-*+]|[0-9]{1,9}[.)])"
STRUCTURAL_LIST_ITEM_RE = re.compile(
    rf"^(?P<indent>[ \t]*)(?P<marker>{LIST_MARKER})(?P<spacing>[ \t]+)(?P<value>.*)$"
)
BLOCKQUOTE_RE = re.compile(r"^ {0,3}>")
BLOCKQUOTE_CONTENT_RE = re.compile(r"^ {0,3}>[ \t]?(?P<value>.*)$")
CHECKBOX_VALUE_RE = re.compile(r"^\[[ xX]\][ \t]+(?P<value>.*)$")
RAW_HTML_START_RE = re.compile(
    r"(?i)(?:</?[A-Za-z][A-Za-z0-9-]*(?=[\s/>])|<![A-Z]|<\?)"
)
ATX_HEADING_RE = re.compile(r"^#{1,6}(?:[ \t]+(?P<value>.*))?$")
THEMATIC_BREAK_RE = re.compile(
    r"^(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$"
)
LIST_ITEM_RE = re.compile(
    rf"^ {{0,3}}{LIST_MARKER}\s+(?:\[[ xX]\]\s+)?(?P<value>.*)$"
)
EMPTY_LIST_MARKER_RE = re.compile(rf"^ {{0,3}}{LIST_MARKER}[ \t]*$")
LIST_SENTINEL_RE = re.compile(
    rf"(?im)^ {{0,3}}{LIST_MARKER}\s+(?:\[[ xX]\]\s+)?"
    r"(?:unknown|not applicable|not run):"
)
ABSENCE_RE = re.compile(
    r"(?is)^\s*(?P<kind>unknown|not applicable|not run):\s*(?P<reason>.*?)\s*$"
)
GENERIC_COMMIT_TITLES = {
    "change",
    "changes",
    "fix",
    "misc",
    "update",
    "wip",
    "변경",
    "수정",
    "작업",
}


@dataclass(frozen=True)
class ContractDefinition:
    headings: tuple[str, ...]
    checklist_fields: frozenset[str] = frozenset()
    bullet_fields: frozenset[str] = frozenset()
    issue_reference_fields: frozenset[str] = frozenset()
    change_reference_fields: frozenset[str] = frozenset()
    absence_allowed_fields: frozenset[str] = frozenset()


CONTRACTS = {
    "issue": ContractDefinition(
        headings=(
            "Contract version",
            "Context and problem",
            "Goal",
            "Non-goals",
            "Acceptance criteria",
            "Constraints and impact",
        ),
        checklist_fields=frozenset({"Acceptance criteria"}),
        absence_allowed_fields=frozenset({"Non-goals", "Constraints and impact"}),
    ),
    "pull-request": ContractDefinition(
        headings=(
            "Contract version",
            "Summary",
            "Changes",
            "Validation",
            "Impact and risks",
            "Related issue",
        ),
        bullet_fields=frozenset({"Changes", "Validation"}),
        issue_reference_fields=frozenset({"Related issue"}),
        absence_allowed_fields=frozenset({"Impact and risks"}),
    ),
    "commit": ContractDefinition(
        headings=(
            "Contract version",
            "Why",
            "Changes",
            "Validation",
            "References",
        ),
        bullet_fields=frozenset({"Changes", "Validation"}),
        change_reference_fields=frozenset({"References"}),
    ),
}


@dataclass(frozen=True)
class Finding:
    code: str
    message: str
    line: int | None = None
    field: str | None = None


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    kind: str
    version: str | None
    findings: tuple[Finding, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "kind": self.kind,
            "version": self.version,
            "findings": [asdict(item) for item in self.findings],
        }


def _masked_line(line: str) -> str:
    return "".join("\n" if character == "\n" else " " for character in line)


def _indent_columns(line: str) -> int:
    columns = 0
    for character in line:
        if character == " ":
            columns += 1
        elif character == "\t":
            columns += 4 - (columns % 4)
        else:
            break
    return columns


def _column_width(value: str) -> int:
    columns = 0
    for character in value:
        if character == "\t":
            columns += 4 - (columns % 4)
        else:
            columns += 1
    return columns


def _blockquote_allows_lazy_continuation(value: str) -> bool:
    candidate = value.strip()
    if not candidate:
        return False
    if ATX_HEADING_RE.fullmatch(candidate) or THEMATIC_BREAK_RE.fullmatch(
        candidate
    ):
        return False
    return True


def _structural_lines(
    value: str,
) -> list[tuple[str, bool, re.Match[str] | None]]:
    """Classify visible lines without treating nested list content as top-level."""

    classified: list[tuple[str, bool, re.Match[str] | None]] = []
    list_content_indents: list[int] = []
    previous_line_blank = True
    blockquote_paragraph = False
    for line in value.splitlines():
        if not line.strip():
            classified.append((line, False, None))
            previous_line_blank = True
            blockquote_paragraph = False
            continue

        if BLOCKQUOTE_RE.match(line):
            classified.append((line, False, None))
            previous_line_blank = False
            blockquote = BLOCKQUOTE_CONTENT_RE.fullmatch(line)
            blockquote_paragraph = bool(
                blockquote
                and _blockquote_allows_lazy_continuation(
                    blockquote.group("value")
                )
            )
            continue

        indent = _indent_columns(line)
        list_match = STRUCTURAL_LIST_ITEM_RE.fullmatch(line)
        if list_match:
            blockquote_paragraph = False
            while list_content_indents and indent < list_content_indents[-1]:
                list_content_indents.pop()
            top_level = not list_content_indents
            classified.append((line, top_level, list_match))
            content_indent = _column_width(line[: list_match.start("value")])
            list_content_indents.append(content_indent)
            previous_line_blank = False
            continue

        if blockquote_paragraph:
            classified.append((line, False, None))
            previous_line_blank = False
            continue

        if previous_line_blank:
            while list_content_indents and indent < list_content_indents[-1]:
                list_content_indents.pop()
        classified.append((line, not list_content_indents, None))
        previous_line_blank = False
    return classified


def _mask_comment_spans(
    line: str,
    in_comment: bool,
    *,
    line_offset: int = 0,
    ambiguous_comment_offsets: list[int] | None = None,
) -> tuple[str, bool]:
    characters = list(line)
    cursor = 0
    while cursor < len(line):
        if in_comment:
            end = line.find("-->", cursor)
            stop = len(line) if end == -1 else end + 3
            for index in range(cursor, stop):
                if characters[index] not in {"\r", "\n"}:
                    characters[index] = " "
            if end == -1:
                return "".join(characters), True
            in_comment = False
            cursor = stop
            continue
        start = line.find("<!--", cursor)
        if start == -1:
            break
        if ambiguous_comment_offsets is not None and line[:start].strip():
            ambiguous_comment_offsets.append(line_offset + start)
        cursor = start
        in_comment = True
    return "".join(characters), in_comment


def _scan_markdown(
    value: str,
    *,
    mask_code: bool,
    mask_indented_code: bool | None = None,
    ambiguous_comment_offsets: list[int] | None = None,
) -> str:
    """Mask comments and optionally code using one ordered Markdown state machine."""

    if mask_indented_code is None:
        mask_indented_code = mask_code
    output: list[str] = []
    fence_character = ""
    fence_length = 0
    in_comment = False
    offset = 0
    for line in value.splitlines(keepends=True):
        line_without_ending = line.rstrip("\r\n")
        fence_match = FENCE_RE.match(line_without_ending)
        if fence_character:
            output.append(_masked_line(line) if mask_code else line)
            if fence_match:
                run = fence_match.group("run")
                if (
                    run[0] == fence_character
                    and len(run) >= fence_length
                    and not fence_match.group("rest").strip()
                ):
                    fence_character = ""
                    fence_length = 0
            offset += len(line)
            continue
        if in_comment:
            masked, in_comment = _mask_comment_spans(
                line,
                in_comment,
                line_offset=offset,
                ambiguous_comment_offsets=ambiguous_comment_offsets,
            )
            output.append(masked)
            offset += len(line)
            continue
        if fence_match:
            run = fence_match.group("run")
            rest = fence_match.group("rest")
            if run[0] != "`" or "`" not in rest:
                fence_character = run[0]
                fence_length = len(run)
                output.append(_masked_line(line) if mask_code else line)
                offset += len(line)
                continue
        if _indent_columns(line) >= 4:
            if mask_indented_code and ambiguous_comment_offsets is not None:
                cursor = 0
                while True:
                    comment_start = line.find("<!--", cursor)
                    if comment_start == -1:
                        break
                    ambiguous_comment_offsets.append(offset + comment_start)
                    cursor = comment_start + 4
            if mask_indented_code:
                output.append(_masked_line(line) if mask_code else line)
            else:
                masked, in_comment = _mask_comment_spans(
                    line,
                    False,
                    line_offset=offset,
                    ambiguous_comment_offsets=ambiguous_comment_offsets,
                )
                output.append(masked)
        else:
            masked, in_comment = _mask_comment_spans(
                line,
                False,
                line_offset=offset,
                ambiguous_comment_offsets=ambiguous_comment_offsets,
            )
            output.append(masked)
        offset += len(line)
    return "".join(output)


def _scan_visible_markdown(
    value: str, ambiguous_comment_offsets: list[int] | None = None
) -> str:
    return _scan_markdown(
        value,
        mask_code=True,
        ambiguous_comment_offsets=ambiguous_comment_offsets,
    )


def _link_definition_start_lines(value: str) -> list[int]:
    starts: list[int] = []
    start: int | None = None
    candidate = ""
    for index, line in enumerate(value.splitlines()):
        opener = bool(re.match(r"^ {0,3}\[", line))
        if start is None:
            if not opener:
                continue
            start = index
            candidate = line
        elif not line.strip():
            start = None
            candidate = ""
            continue
        elif opener:
            start = index
            candidate = line
        else:
            candidate += "\n" + line

        if LINK_DEFINITION_RE.fullmatch(candidate):
            starts.append(start + 1)
            start = None
            candidate = ""
        elif len(candidate) > 4096:
            start = None
            candidate = ""
    return starts


def _content_without_comments(value: str) -> str:
    return _scan_markdown(value, mask_code=False).strip()


def _mask_inline_code_spans(value: str) -> str:
    """Mask complete inline code spans while preserving line positions."""

    characters = list(value)
    cursor = 0
    while cursor < len(value):
        if value[cursor] != "`" or _backslash_escaped(value, cursor):
            cursor += 1
            continue
        opener_end = cursor
        while opener_end < len(value) and value[opener_end] == "`":
            opener_end += 1
        run_length = opener_end - cursor
        search = opener_end
        closer_end: int | None = None
        while search < len(value):
            next_tick = value.find("`", search)
            if next_tick == -1:
                break
            candidate_end = next_tick
            while candidate_end < len(value) and value[candidate_end] == "`":
                candidate_end += 1
            if candidate_end - next_tick == run_length:
                closer_end = candidate_end
                break
            search = candidate_end
        if closer_end is None:
            cursor = opener_end
            continue
        for index in range(cursor, closer_end):
            if characters[index] not in {"\r", "\n"}:
                characters[index] = " "
        cursor = closer_end
    return "".join(characters)


def _backslash_escaped(value: str, offset: int) -> bool:
    backslashes = 0
    cursor = offset - 1
    while cursor >= 0 and value[cursor] == "\\":
        backslashes += 1
        cursor -= 1
    return backslashes % 2 == 1


def _drop_indent_columns(value: str, columns: int) -> str:
    cursor = 0
    width = 0
    while cursor < len(value) and width < columns:
        character = value[cursor]
        if character == " ":
            width += 1
        elif character == "\t":
            width += 4 - (width % 4)
        else:
            break
        cursor += 1
    return value[cursor:]


def _mask_top_level_indented_code(value: str) -> str:
    """Mask indented code while retaining list-relative Markdown content."""

    output: list[str] = []
    list_content_indents: list[int] = []
    previous_line_blank = True
    in_indented_code = False
    fence_character = ""
    fence_length = 0
    for line in value.splitlines(keepends=True):
        line_without_ending = line.rstrip("\r\n")
        if not line_without_ending.strip():
            output.append(line)
            previous_line_blank = True
            continue

        indent = _indent_columns(line_without_ending)
        relative_line = _drop_indent_columns(
            line_without_ending,
            list_content_indents[-1] if list_content_indents else 0,
        )
        if fence_character:
            output.append(_masked_line(line))
            fence_match = FENCE_RE.match(relative_line)
            if fence_match:
                run = fence_match.group("run")
                if (
                    run[0] == fence_character
                    and len(run) >= fence_length
                    and not fence_match.group("rest").strip()
                ):
                    fence_character = ""
                    fence_length = 0
            previous_line_blank = False
            continue

        list_match = STRUCTURAL_LIST_ITEM_RE.fullmatch(line_without_ending)
        if list_match:
            in_indented_code = False
            while list_content_indents and indent < list_content_indents[-1]:
                list_content_indents.pop()
            list_content_indents.append(
                _column_width(line_without_ending[: list_match.start("value")])
            )
            fence_match = FENCE_RE.match(list_match.group("value"))
            if fence_match:
                run = fence_match.group("run")
                rest = fence_match.group("rest")
                if run[0] != "`" or "`" not in rest:
                    fence_character = run[0]
                    fence_length = len(run)
                    output.append(_masked_line(line))
                else:
                    output.append(line)
            else:
                output.append(line)
            previous_line_blank = False
            continue

        if previous_line_blank:
            while list_content_indents and indent < list_content_indents[-1]:
                list_content_indents.pop()
        relative_line = _drop_indent_columns(
            line_without_ending,
            list_content_indents[-1] if list_content_indents else 0,
        )
        fence_match = FENCE_RE.match(relative_line)
        if fence_match:
            run = fence_match.group("run")
            rest = fence_match.group("rest")
            if run[0] != "`" or "`" not in rest:
                fence_character = run[0]
                fence_length = len(run)
                output.append(_masked_line(line))
                previous_line_blank = False
                in_indented_code = False
                continue
        code_indent = (
            list_content_indents[-1] + 4 if list_content_indents else 4
        )
        if in_indented_code and indent < code_indent:
            in_indented_code = False
        if not in_indented_code and previous_line_blank and indent >= code_indent:
            in_indented_code = True
        output.append(_masked_line(line) if in_indented_code else line)
        previous_line_blank = False
    return "".join(output)


def _raw_html_offsets(value: str) -> list[int]:
    candidates = _scan_markdown(
        value,
        mask_code=True,
        mask_indented_code=False,
    )
    candidates = _mask_inline_code_spans(
        _mask_top_level_indented_code(candidates)
    )
    return [
        match.start()
        for match in RAW_HTML_START_RE.finditer(candidates)
        if not _backslash_escaped(candidates, match.start())
    ]


def _contains_placeholder_value(value: str) -> bool:
    for line in value.splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        list_match = LIST_ITEM_RE.fullmatch(line)
        if list_match:
            candidate = list_match.group("value").strip()
        if ANGLE_PLACEHOLDER_RE.fullmatch(candidate):
            return True
        candidate = _inline_visible_text(candidate).strip()
        if PLACEHOLDER_VALUE_RE.fullmatch(candidate) or ANGLE_PLACEHOLDER_RE.fullmatch(
            candidate
        ):
            return True
    return False


def _inline_visible_text(value: str) -> str:
    value = re.sub(r"!?\[([^\]\r\n]*)\]\([^\)\r\n]*\)", r"\1", value)
    value = re.sub(r"!?\[([^\]\r\n]*)\]\[[^\]\r\n]*\]", r"\1", value)
    value = re.sub(
        r"</?[A-Za-z][A-Za-z0-9-]*(?:[ \t][^>\r\n]*)?/?>",
        "",
        value,
    )
    value = re.sub(r"\\([!\"#$%&'()*+,\-./:;<=>?@\[\]^_`{|}~\\])", r"\1", value)
    value = value.translate(str.maketrans("", "", "`*_~[]()"))
    value = html.unescape(value)
    return "".join(
        character
        for character in value
        if unicodedata.category(character) not in {"Cc", "Cf", "Cs"}
        and not unicodedata.category(character).startswith("M")
    )


def _visible_evidence_text(value: str) -> str:
    visible: list[str] = []
    fence_character = ""
    fence_length = 0
    for line in value.splitlines():
        fence_match = FENCE_RE.match(line)
        if fence_character:
            if fence_match:
                run = fence_match.group("run")
                if (
                    run[0] == fence_character
                    and len(run) >= fence_length
                    and not fence_match.group("rest").strip()
                ):
                    fence_character = ""
                    fence_length = 0
                    continue
            visible.append(
                "".join(
                    character
                    for character in line
                    if unicodedata.category(character) not in {"Cc", "Cf", "Cs"}
                    and not unicodedata.category(character).startswith("M")
                )
            )
            continue
        if fence_match:
            run = fence_match.group("run")
            rest = fence_match.group("rest")
            if run[0] != "`" or "`" not in rest:
                fence_character = run[0]
                fence_length = len(run)
                continue

        candidate = line.strip()
        while candidate:
            if THEMATIC_BREAK_RE.fullmatch(candidate):
                candidate = ""
                break
            list_match = LIST_ITEM_RE.fullmatch(candidate)
            if list_match:
                candidate = list_match.group("value").strip()
                continue
            checkbox = CHECKBOX_VALUE_RE.fullmatch(candidate)
            if checkbox:
                candidate = checkbox.group("value").strip()
                continue
            blockquote = BLOCKQUOTE_CONTENT_RE.fullmatch(candidate)
            if blockquote:
                candidate = blockquote.group("value").strip()
                continue
            if EMPTY_LIST_MARKER_RE.fullmatch(candidate):
                candidate = ""
            break
        heading = ATX_HEADING_RE.fullmatch(candidate)
        if heading:
            candidate = (heading.group("value") or "").strip()
            candidate = re.sub(r"[ \t]+#+[ \t]*$", "", candidate)
            if re.fullmatch(r"#+", candidate):
                candidate = ""
        if THEMATIC_BREAK_RE.fullmatch(candidate):
            candidate = ""
        visible.append(_inline_visible_text(candidate))
    return "\n".join(visible).strip()


def _top_level_candidate(
    line: str, list_match: re.Match[str] | None
) -> str:
    return list_match.group("value") if list_match else line.strip()


def _top_level_reference_blocks(value: str) -> list[str]:
    blocks: list[str] = []
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append(" ".join(paragraph))
            paragraph.clear()

    for line, top_level, list_match in _structural_lines(value):
        if not line.strip() or not top_level:
            flush_paragraph()
            continue
        candidate = _top_level_candidate(line, list_match)
        if list_match:
            flush_paragraph()
            blocks.append(candidate)
        else:
            paragraph.append(candidate)
    flush_paragraph()
    return blocks


def _has_issue_reference(value: str) -> bool:
    for candidate in _top_level_reference_blocks(value):
        if ISSUE_URL_RE.search(candidate):
            return True
        visible_candidate = _inline_visible_text(candidate)
        for match in BARE_REFERENCE_RE.finditer(visible_candidate):
            prefix = visible_candidate[max(0, match.start() - 32) : match.start()]
            if not PR_LABEL_RE.search(prefix):
                return True
    return False


def _has_change_reference(value: str) -> bool:
    return any(
        CHANGE_REFERENCE_RE.search(_inline_visible_text(candidate))
        for candidate in _top_level_reference_blocks(value)
    )


def _has_top_level_checklist_item(value: str) -> bool:
    for _line, top_level, list_match in _structural_lines(value):
        if not top_level or list_match is None:
            continue
        checkbox = CHECKBOX_VALUE_RE.fullmatch(list_match.group("value"))
        if checkbox and _visible_evidence_text(checkbox.group("value")):
            return True
    return False


def _has_top_level_list_item(value: str) -> bool:
    return any(
        top_level
        and list_match is not None
        and bool(_visible_evidence_text(list_match.group("value")))
        for _line, top_level, list_match in _structural_lines(value)
    )


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _parse_sections(
    text: str,
) -> tuple[list[tuple[str, int, int, int]], list[Finding]]:
    """Return heading, line, body-start, body-end tuples for level-three fields."""

    scan_text = _scan_visible_markdown(text)
    headings: list[tuple[str, int, int, int]] = []
    findings: list[Finding] = []
    offset = 0
    for line in scan_text.splitlines(keepends=True):
        match = HEADING_RE.match(line.rstrip("\r\n"))
        if match:
            level = len(match.group(1))
            name = match.group(2)
            line_number = _line_number(text, offset)
            if level != 3:
                findings.append(
                    Finding(
                        "unexpected-heading-level",
                        "Contract fields must use level-three Markdown headings.",
                        line_number,
                        name,
                    )
                )
            else:
                headings.append((name, line_number, offset, offset + len(line)))
        offset += len(line)

    sections: list[tuple[str, int, int, int]] = []
    for index, (name, line_number, _heading_start, body_start) in enumerate(headings):
        body_end = headings[index + 1][2] if index + 1 < len(headings) else len(text)
        sections.append((name, line_number, body_start, body_end))
    return sections, findings


def validate_text(
    text: str, *, kind: str, expected_version: str = CONTRACT_VERSION
) -> ValidationResult:
    if kind not in CONTRACTS:
        raise ValueError(f"unsupported evidence kind: {kind}")
    if expected_version != CONTRACT_VERSION:
        raise ValueError(f"unsupported contract version: {expected_version}")

    findings: list[Finding] = []
    if "\x00" in text:
        findings.append(Finding("nul-byte", "NUL bytes are not valid Markdown evidence."))
    if not text.strip():
        findings.append(Finding("empty-document", "Evidence document is empty."))
        return ValidationResult(False, kind, None, tuple(findings))

    ambiguous_comment_offsets: list[int] = []
    visible_document = _scan_visible_markdown(text, ambiguous_comment_offsets)
    for offset in ambiguous_comment_offsets:
        findings.append(
            Finding(
                "ambiguous-comment-opener",
                "HTML comment openers must begin a line; use fenced code for literal '<!--'.",
                _line_number(text, offset),
            )
        )
    for offset in _raw_html_offsets(text):
        findings.append(
            Finding(
                "raw-html",
                "Raw HTML is not valid contract evidence; use Markdown or fenced code.",
                _line_number(text, offset),
            )
        )
    visible_lines = visible_document.splitlines()
    for line_number in _link_definition_start_lines(visible_document):
        findings.append(
            Finding(
                "non-rendered-link-definition",
                "Link reference definitions do not count as rendered field evidence; use an inline link.",
                line_number,
            )
        )
    for index, line in enumerate(visible_lines):
        if (
            index > 0
            and visible_lines[index - 1].strip()
            and SETEXT_UNDERLINE_RE.fullmatch(line)
        ):
            findings.append(
                Finding(
                    "setext-heading",
                    "Contract documents must not contain Setext-style Markdown headings.",
                    index + 1,
                )
            )

    definition = CONTRACTS[kind]
    if kind == "commit":
        title_source = _content_without_comments(text.splitlines()[0])
        title = _visible_evidence_text(title_source)
        if _contains_placeholder_value(title_source):
            findings.append(
                Finding(
                    "commit-title-placeholder",
                    "Commit title must replace the authoring placeholder with a delivered result.",
                    1,
                )
            )
        elif not title:
            findings.append(Finding("commit-title-empty", "Commit title is required.", 1))
        elif len(title) > 72:
            findings.append(
                Finding("commit-title-too-long", "Commit title must be 72 characters or fewer.", 1)
            )
        elif title.casefold().rstrip(".。") in GENERIC_COMMIT_TITLES:
            findings.append(
                Finding(
                    "commit-title-generic",
                    "Commit title must describe the result, not a generic activity.",
                    1,
                )
            )
        if len(text.splitlines()) < 2 or text.splitlines()[1].strip():
            findings.append(
                Finding("commit-title-separator", "Commit title must be followed by a blank line.", 2)
            )

    sections, heading_findings = _parse_sections(text)
    findings.extend(heading_findings)
    actual_headings = tuple(section[0] for section in sections)
    if actual_headings != definition.headings:
        findings.append(
            Finding(
                "heading-contract",
                "Expected headings in order: " + " | ".join(definition.headings),
            )
        )

    version_heading = re.search(
        r"(?m)^ {0,3}###[ \t]+Contract version[ \t]*$",
        _scan_visible_markdown(text),
    )
    if version_heading:
        preamble = text[: version_heading.start()]
        if kind == "commit":
            preamble_lines = preamble.splitlines()
            extra_preamble = "\n".join(preamble_lines[1:]) if preamble_lines else ""
        else:
            extra_preamble = preamble
        if _content_without_comments(extra_preamble):
            findings.append(
                Finding(
                    "unexpected-preamble",
                    "Only the commit title may appear before the first contract field.",
                )
            )

    seen: set[str] = set()
    version: str | None = None
    for field, line_number, body_start, body_end in sections:
        if field in seen:
            findings.append(
                Finding("duplicate-heading", "Contract heading appears more than once.", line_number, field)
            )
        seen.add(field)
        if field not in definition.headings:
            findings.append(
                Finding("unknown-heading", "Heading is not part of this contract kind.", line_number, field)
            )
            continue

        raw_body = text[body_start:body_end]
        body = _content_without_comments(raw_body)
        visible_body = _scan_visible_markdown(raw_body).strip()
        visible_evidence = _visible_evidence_text(body)
        visible_structure = _visible_evidence_text(visible_body)
        if not visible_evidence:
            findings.append(
                Finding("empty-field", "Contract field must contain evidence.", line_number, field)
            )
            continue
        if field == "Contract version":
            version = body
            if body != expected_version:
                findings.append(
                    Finding(
                        "version-mismatch",
                        f"Contract version must be exactly {expected_version}.",
                        line_number,
                        field,
                    )
                )
            continue

        if LIST_SENTINEL_RE.search(visible_body):
            findings.append(
                Finding(
                    "invalid-absence-syntax",
                    "Absence sentinels must be the complete field value, not a list item.",
                    line_number,
                    field,
                )
            )
            continue

        absence_match = ABSENCE_RE.fullmatch(visible_structure)
        absence_kind = absence_match.group("kind").casefold() if absence_match else None
        absence_reason = absence_match.group("reason").strip() if absence_match else ""
        allowed_absence_kinds = (
            {"unknown", "not applicable"}
            if field in definition.absence_allowed_fields
            else {"not run"}
            if field == "Validation"
            else set()
        )
        explicit_absence = (
            absence_kind in allowed_absence_kinds
            and bool(absence_reason)
            and not _contains_placeholder_value(absence_reason)
        )
        if absence_kind and not explicit_absence:
            code = (
                "absence-not-allowed"
                if absence_kind not in allowed_absence_kinds
                else "invalid-absence-reason"
            )
            findings.append(
                Finding(
                    code,
                    "Use an allowed sentinel with a concrete, non-placeholder reason.",
                    line_number,
                    field,
                )
            )
            continue
        if not absence_kind and _contains_placeholder_value(visible_body):
            findings.append(
                Finding(
                    "placeholder",
                    "Use concrete evidence or an allowed absence sentinel with a reason.",
                    line_number,
                    field,
                )
            )
        if (
            field in definition.checklist_fields
            and not _has_top_level_checklist_item(visible_body)
        ):
            findings.append(
                Finding(
                    "checklist-required",
                    "Field must contain at least one Markdown task-list item.",
                    line_number,
                    field,
                )
            )
        if field in definition.bullet_fields and not (
            _has_top_level_list_item(visible_body)
            or (field == "Validation" and explicit_absence)
        ):
            findings.append(
                Finding(
                    "bullet-required",
                    "Field must contain at least one Markdown list item.",
                    line_number,
                    field,
                )
            )
        reference_valid = (
            _has_issue_reference(visible_body)
            if field in definition.issue_reference_fields
            else _has_change_reference(visible_body)
            if field in definition.change_reference_fields
            else True
        )
        if not reference_valid:
            findings.append(
                Finding(
                    "reference-required",
                    "Field must contain an Issue or pull request reference.",
                    line_number,
                    field,
                )
            )

    return ValidationResult(not findings, kind, version, tuple(findings))


def _read_regular_descriptor(descriptor: int) -> str:
    item_stat = os.fstat(descriptor)
    if not stat.S_ISREG(item_stat.st_mode):
        raise ValueError("evidence path must be a non-symlink regular file")
    if item_stat.st_size > MAX_BYTES:
        raise ValueError(f"evidence file exceeds {MAX_BYTES} bytes")

    content = bytearray()
    while len(content) <= MAX_BYTES:
        chunk = os.read(descriptor, min(64 * 1024, MAX_BYTES + 1 - len(content)))
        if not chunk:
            break
        content.extend(chunk)
    if len(content) > MAX_BYTES:
        raise ValueError(f"evidence file exceeds {MAX_BYTES} bytes")
    return bytes(content).decode("utf-8")


def _open_no_follow(path: str | Path, flags: int, *, dir_fd: int | None = None) -> int:
    try:
        return os.open(path, flags, dir_fd=dir_fd)
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise ValueError("path must not contain symlinks") from error
        raise


def _open_regular_file(path: Path, workspace: Path | None) -> str:
    no_follow = getattr(os, "O_NOFOLLOW", None)
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if no_follow is None or directory_flag is None or os.open not in os.supports_dir_fd:
        raise ValueError("platform does not support secure workspace-relative file reads")

    nonblock = getattr(os, "O_NONBLOCK", None)
    if nonblock is None:
        raise ValueError("platform does not support nonblocking evidence-file reads")

    base_flags = os.O_RDONLY | no_follow | getattr(os, "O_CLOEXEC", 0)
    final_flags = base_flags | nonblock
    if workspace is None:
        item_stat = path.lstat()
        if stat.S_ISLNK(item_stat.st_mode):
            raise ValueError("path must not contain symlinks")
        descriptor = _open_no_follow(path, final_flags)
        try:
            return _read_regular_descriptor(descriptor)
        finally:
            os.close(descriptor)

    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("path must be a normalized workspace-relative path")
    workspace = workspace.resolve(strict=True)
    descriptor = _open_no_follow(workspace, base_flags | directory_flag)
    try:
        for index, part in enumerate(path.parts):
            component_stat = os.stat(part, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISLNK(component_stat.st_mode):
                raise ValueError("path must not contain symlinks")
            component_flags = final_flags
            if index < len(path.parts) - 1:
                component_flags = base_flags
                component_flags |= directory_flag
            next_descriptor = _open_no_follow(
                part,
                component_flags,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
        return _read_regular_descriptor(descriptor)
    finally:
        os.close(descriptor)


def _write_github_output(path: Path, result: ValidationResult) -> None:
    payload = json.dumps(result.to_dict(), ensure_ascii=False, separators=(",", ":"))
    safe_version = result.version if result.version == CONTRACT_VERSION else ""
    with path.open("a", encoding="utf-8") as stream:
        stream.write(f"valid={'true' if result.valid else 'false'}\n")
        stream.write(f"kind={result.kind}\n")
        stream.write(f"version={safe_version}\n")
        stream.write(f"report-json={payload}\n")


def _render_text(result: ValidationResult, source: Path) -> str:
    if result.valid:
        return f"PASS: {source} satisfies Change Evidence Contract {result.version} ({result.kind})"
    lines = [f"FAIL: {source} does not satisfy Change Evidence Contract {CONTRACT_VERSION} ({result.kind})"]
    for finding in result.findings:
        location = f"line {finding.line}: " if finding.line is not None else ""
        field = f"[{finding.field}] " if finding.field else ""
        lines.append(f"- {finding.code}: {location}{field}{finding.message}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--kind", required=True, choices=tuple(CONTRACTS))
    parser.add_argument("--path", required=True, type=Path)
    parser.add_argument("--expected-version", default=CONTRACT_VERSION)
    parser.add_argument("--mode", choices=("enforce", "audit"), default="enforce")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--github-output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        text = _open_regular_file(args.path, args.workspace)
        result = validate_text(text, kind=args.kind, expected_version=args.expected_version)
    except (OSError, UnicodeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    if args.github_output:
        _write_github_output(args.github_output, result)
    if args.format == "json":
        print(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True))
    else:
        print(_render_text(result, args.path))
    if result.valid or args.mode == "audit":
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
