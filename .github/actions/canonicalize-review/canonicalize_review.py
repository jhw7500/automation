#!/usr/bin/env python3
"""Fail-closed canonicalization for untrusted review finding documents."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Literal

from review_scope import SourceAnchor, ScopeValidationError, TriggerEvidence, load_review_scope


HARD_REASONS = frozenset({
    "candidate_missing", "invalid_utf8", "candidate_oversize",
    "ambiguous_document", "scope_invalid", "canonicalizer_error",
})
SOFT_REASONS = frozenset({
    "invalid_anchor", "invalid_trigger_evidence", "invalid_severity",
    "invalid_impact_class", "missing_material_impact",
    "unsupported_performance_basis", "non_actionable_category",
    "unknown_prior_id", "duplicate_prior_binding", "missing_fix_anchor",
})
SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM")
IMPACT_CLASSES = frozenset({"runtime", "security", "data-integrity", "user-visible", "performance"})
MAX_CANDIDATE_BYTES = 60_000
MAX_CANONICAL_BYTES = 64_000
MAX_PREVIOUS_CANONICAL_BYTES = 65_536
MAX_CANDIDATE_BLOCKS = 512
MAX_SAFE_INTEGER = (1 << 53) - 1
PROOF_DEFICIT = re.compile(
    r"\b(?:plausible(?:\s+but)?\s+unconfirmed|cannot\s+(?:confirm|verify)|"
    r"not\s+confirmed|pending\s+confirmation|unverified\s+external)\b",
    re.IGNORECASE,
)
PYTHON_EXCEPTION_HANDLER = re.compile(r"^\s*except(?:\s|:)")
WORKFLOW_OWNED = re.compile(
    r"(?:<!--.*automation:|^#{1,6}\s*(?:status|run|reviewed|validation)\b|"
    r"^\s*(?:-\s*)?(?:status|run|reviewed|validation)\s*:)", re.IGNORECASE,
)
SECTION_NAMES = ("New findings", "Still open", "Resolved", "Retracted")
NEW_FINDING_FIELDS = frozenset(
    {
        "Changed anchor",
        "Trigger evidence",
        "Impact class",
        "Material impact",
        "Performance basis",
    }
)
VISIBLE_METADATA = re.compile(
    r"^(?:-\s*)?(?:status|run|reviewed|validation)\s*:", re.IGNORECASE
)
VISIBLE_HEADING = re.compile(r"^#{1,6}(?:\s|$)")
STICKY_HEADERS = frozenset({"## Claude Code Review (latest)", "## 🔎 Gemini Code Review"})
AMBIGUITY_DIAGNOSTICS = frozenset(
    {
        "finding_before_section",
        "unknown_section_before_document",
        "unknown_section_after_document",
        "duplicate_section",
        "preamble",
        "missing_new_findings",
        "invalid_empty_new_findings",
        "content_without_finding",
        "none_with_finding",
        "section_preamble",
        "invalid_finding_heading",
        "too_many_blocks",
    }
)


@dataclass(frozen=True)
class CanonicalizationRequest:
    reviewer: Literal["claude", "gemini"]
    candidate_file: Path
    canonical_file: Path
    result_file: Path
    scope_manifest: Path
    selected_diff: Path
    repository_root: Path
    diff_mode: Literal["full", "delta"]
    previous_sha: str
    previous_review_file: Path | None
    expected_repository: str


@dataclass(frozen=True)
class CandidateReason:
    index: int
    section: Literal["New findings", "Still open", "Resolved", "Retracted"]
    outcome: Literal["filtered", "normalized"]
    reason: str
    claimed_severity: Literal["none", "MEDIUM", "HIGH", "CRITICAL"]

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index, "section": self.section, "outcome": self.outcome,
            "reason": self.reason, "claimed_severity": self.claimed_severity,
        }


@dataclass(frozen=True)
class CandidateValidation:
    attempt: Literal["initial"]
    sha256: str
    valid: Literal[False]
    rule: str
    line: int
    column: int

    def to_dict(self) -> dict[str, object]:
        return {
            "attempt": self.attempt,
            "sha256": self.sha256,
            "valid": self.valid,
            "rule": self.rule,
            "line": self.line,
            "column": self.column,
        }


@dataclass(frozen=True)
class CanonicalizationResult:
    document_valid: bool
    accepted_count: int
    filtered_count: int
    normalized_count: int
    filtered_max_severity: Literal["none", "MEDIUM", "HIGH", "CRITICAL"]
    failure_reason: str
    candidate_reasons: tuple[CandidateReason, ...]
    candidate_validations: tuple[CandidateValidation, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": 2,
            "document_valid": self.document_valid,
            "accepted_count": self.accepted_count,
            "filtered_count": self.filtered_count,
            "normalized_count": self.normalized_count,
            "filtered_max_severity": self.filtered_max_severity,
            "failure_reason": self.failure_reason,
            "candidate_reasons": [reason.to_dict() for reason in self.candidate_reasons],
            "candidate_validations": [
                validation.to_dict() for validation in self.candidate_validations
            ],
        }


@dataclass(frozen=True)
class _Block:
    index: int
    section: Literal["New findings", "Still open", "Resolved", "Retracted"]
    finding_id: str | None
    severity: Literal["none", "MEDIUM", "HIGH", "CRITICAL"]
    low_severity: bool
    title: str
    lines: tuple[str, ...]


class _CandidateSyntaxError(ValueError):
    def __init__(self, rule: str, line: int, column: int = 1):
        super().__init__(rule)
        self.rule = rule
        self.line = line
        self.column = column


@dataclass(frozen=True)
class _Finding:
    severity: Literal["MEDIUM", "HIGH", "CRITICAL"]
    title: str
    anchor: SourceAnchor
    evidence: tuple[TriggerEvidence, ...]
    impact_class: str
    material_impact: str
    prose: tuple[str, ...]
    performance_basis: tuple[str, TriggerEvidence] | None


@dataclass(frozen=True)
class _PriorFinding:
    finding_id: str
    finding: _Finding


@dataclass(frozen=True)
class _PriorHeading:
    finding_id: str
    severity: Literal["MEDIUM", "HIGH", "CRITICAL"]
    title: str


def _canonical_visible_text(value: str) -> str:
    """Render untrusted free text visibly without allowing workflow control syntax."""
    safe = value.replace("<!--", "&lt;!--").replace("-->", "--&gt;")
    offset = len(safe) - len(safe.lstrip())
    leading, body = safe[:offset], safe[offset:]
    if body in STICKY_HEADERS or VISIBLE_METADATA.match(body) or VISIBLE_HEADING.match(body):
        return leading + "\\" + body
    return safe


def normalize_title(title: str) -> str:
    return " ".join(title.split()).casefold()


def stable_finding_id(reviewer: str, anchor: SourceAnchor, severity: str, title: str) -> str:
    identity = "\0".join((
        reviewer, anchor.path, str(anchor.line), severity, normalize_title(_canonical_visible_text(title)),
    ))
    return "RVW-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]


def _hard(
    reason: str,
    candidate_validations: tuple[CandidateValidation, ...] = (),
) -> CanonicalizationResult:
    return CanonicalizationResult(
        False, 0, 0, 0, "none", reason, (), candidate_validations
    )


def _strict_object(value: str, keys: set[str]) -> dict[str, object] | None:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = item
        return result

    try:
        parsed = json.loads(value, object_pairs_hook=reject_duplicates)
    except (ValueError, json.JSONDecodeError, RecursionError):
        return None
    return parsed if isinstance(parsed, dict) and set(parsed) == keys else None


def _safe_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value <= MAX_SAFE_INTEGER


def _anchor(value: str) -> SourceAnchor | None:
    parsed = _strict_object(value, {"path", "line"})
    if parsed is None or not isinstance(parsed["path"], str) or not _safe_integer(parsed["line"]):
        return None
    return SourceAnchor(parsed["path"], parsed["line"])


def _trigger(value: str) -> TriggerEvidence | None:
    parsed = _strict_object(value, {"path", "line", "quote"})
    if (parsed is None or not isinstance(parsed["path"], str) or not _safe_integer(parsed["line"])
            or not isinstance(parsed["quote"], str)):
        return None
    return TriggerEvidence(parsed["path"], parsed["line"], parsed["quote"])


def _heading(line: str) -> tuple[str | None, Literal["none", "MEDIUM", "HIGH", "CRITICAL"], bool, str] | None:
    matched = re.fullmatch(r"#### (?:(RVW-[0-9a-f]{12}) )?\[(CRITICAL|HIGH|MEDIUM|LOW|[^\]]+)\] (.+)", line)
    if matched is None:
        return None
    finding_id, raw_severity, title = matched.groups()
    severity: Literal["none", "MEDIUM", "HIGH", "CRITICAL"]
    severity = raw_severity if raw_severity in SEVERITIES else "none"
    return finding_id, severity, raw_severity == "LOW", title


def _parse_document(text: str) -> dict[str, list[_Block]]:
    stripped = text.strip()
    if stripped in {"No blocking issues found.", "No validated blocking issues found."}:
        return {name: [] for name in SECTION_NAMES}
    lines = text.splitlines()
    sections: dict[str, list[tuple[int, str]]] = {}
    section_heading_lines: dict[str, int] = {}
    section_order: list[str] = []
    active: str | None = None
    for line_number, line in enumerate(lines, 1):
        if line.startswith("####"):
            if active is None:
                raise _CandidateSyntaxError("finding_before_section", line_number)
            sections[active].append((line_number, line))
        elif line.startswith("###"):
            if line not in {f"### {name}" for name in SECTION_NAMES}:
                raise _CandidateSyntaxError(
                    "unknown_section_after_document"
                    if sections
                    else "unknown_section_before_document",
                    line_number,
                )
            active = line[4:]
            if active in sections:
                raise _CandidateSyntaxError("duplicate_section", line_number)
            sections[active] = []
            section_heading_lines[active] = line_number
            section_order.append(active)
        elif active is not None:
            sections[active].append((line_number, line))
        elif line.strip():
            raise _CandidateSyntaxError("preamble", line_number)
    if "New findings" not in sections:
        raise _CandidateSyntaxError("missing_new_findings", 1)
    parsed: dict[str, list[_Block]] = {name: [] for name in SECTION_NAMES}
    index = 0
    for section in section_order:
        records = sections.get(section, [])
        contents = [line for _, line in records]
        heading_positions = [position for position, line in enumerate(contents) if line.startswith("####")]
        if not heading_positions:
            meaningful = [(line_number, line) for line_number, line in records if line.strip()]
            meaningful_lines = [line for _, line in meaningful]
            if section == "New findings" and meaningful_lines not in (
                ["None"],
                ["None", "No validated blocking issues found."],
            ):
                raise _CandidateSyntaxError(
                    "invalid_empty_new_findings",
                    meaningful[0][0] if meaningful else section_heading_lines[section],
                )
            if section != "New findings" and meaningful:
                raise _CandidateSyntaxError("content_without_finding", meaningful[0][0])
            continue
        if section == "New findings" and any(line.strip() == "None" for line in contents):
            none_position = next(
                position for position, line in enumerate(contents) if line.strip() == "None"
            )
            raise _CandidateSyntaxError("none_with_finding", records[none_position][0])
        if any(line.strip() for line in contents[:heading_positions[0]]):
            preamble_position = next(
                position
                for position, line in enumerate(contents[:heading_positions[0]])
                if line.strip()
            )
            raise _CandidateSyntaxError("section_preamble", records[preamble_position][0])
        for block_number, start in enumerate(heading_positions):
            parsed_heading = _heading(contents[start])
            if parsed_heading is None:
                raise _CandidateSyntaxError("invalid_finding_heading", records[start][0])
            end = heading_positions[block_number + 1] if block_number + 1 < len(heading_positions) else len(contents)
            finding_id, severity, low_severity, title = parsed_heading
            parsed[section].append(_Block(index, section, finding_id, severity, low_severity, title, tuple(contents[start + 1:end])))
            index += 1
            if index > MAX_CANDIDATE_BLOCKS:
                raise _CandidateSyntaxError("too_many_blocks", records[start][0])
    return parsed


def _field_values(lines: tuple[str, ...], name: str) -> list[str]:
    prefix = f"- {name}: "
    return [line[len(prefix):] for line in lines if line.startswith(prefix)]


def _finding_prose(lines: tuple[str, ...]) -> tuple[str, ...]:
    """Return visible prose while excluding only the canonical structured fields."""
    structured_prefixes = tuple(f"- {name}: " for name in NEW_FINDING_FIELDS)
    return tuple(
        _canonical_visible_text(line)
        for line in lines
        if line and not line.startswith(structured_prefixes)
    )


def _claim_reason(block: _Block, outcome: Literal["filtered", "normalized"], reason: str) -> CandidateReason:
    return CandidateReason(block.index, block.section, outcome, reason, block.severity)


def _exception_handler_has_only_self_evidence(
    anchor: SourceAnchor,
    evidence: tuple[TriggerEvidence | None, ...],
) -> bool:
    return (
        all(
            item is not None and item.path == anchor.path and item.line == anchor.line
            for item in evidence
        )
        and any(
            item is not None and PYTHON_EXCEPTION_HANDLER.match(item.quote)
            for item in evidence
        )
    )


def _validate_new(block: _Block, scope: object) -> tuple[_Finding | None, str | None]:
    if block.severity == "none":
        if block.low_severity:
            return None, "non_actionable_category"
        return None, "invalid_severity"
    anchors = _field_values(block.lines, "Changed anchor")
    anchor = _anchor(anchors[0]) if len(anchors) == 1 else None
    if anchor is None or not scope.validate_changed_anchor(anchor):
        return None, "invalid_anchor"
    trigger_values = _field_values(block.lines, "Trigger evidence")
    evidence = tuple(_trigger(value) for value in trigger_values)
    if not evidence or any(item is None or not scope.validate_trigger(item) for item in evidence):
        return None, "invalid_trigger_evidence"
    if _exception_handler_has_only_self_evidence(anchor, evidence):
        return None, "invalid_trigger_evidence"
    impact_values = _field_values(block.lines, "Impact class")
    impact = impact_values[0] if len(impact_values) == 1 else ""
    if impact in {"style", "maintainability", "cleanup"}:
        return None, "non_actionable_category"
    if impact not in IMPACT_CLASSES:
        return None, "invalid_impact_class"
    material_values = _field_values(block.lines, "Material impact")
    material = material_values[0].strip() if len(material_values) == 1 else ""
    extra_prose = _finding_prose(block.lines)
    material_text = " ".join((material, *extra_prose)).strip()
    if not material or PROOF_DEFICIT.search(material_text):
        return None, "missing_material_impact"
    performance_basis: tuple[str, TriggerEvidence] | None = None
    if impact == "performance":
        bases = _field_values(block.lines, "Performance basis")
        if len(bases) != 1:
            return None, "unsupported_performance_basis"
        basis = _strict_object(bases[0], {"kind", "path", "line", "quote"})
        if (basis is None or basis["kind"] not in {"measured", "unbounded-amplification"}
                or not isinstance(basis["path"], str) or not _safe_integer(basis["line"])
                or not isinstance(basis["quote"], str)):
            return None, "unsupported_performance_basis"
        basis_evidence = TriggerEvidence(basis["path"], basis["line"], basis["quote"])
        if not scope.validate_trigger(basis_evidence):
            return None, "unsupported_performance_basis"
        if basis["kind"] == "measured" and re.search(r"[0-9]", basis["quote"]) is None:
            return None, "unsupported_performance_basis"
        performance_basis = (basis["kind"], basis_evidence)
    return _Finding(
        block.severity, _canonical_visible_text(block.title), anchor, evidence, impact,
        _canonical_visible_text(material), extra_prose, performance_basis,
    ), None


def _candidate_json_line(value: dict[str, object]) -> str:
    """Serialize the exact literal JSON form accepted from a current candidate."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_line(value: dict[str, object]) -> str:
    return _candidate_json_line(value).translate(str.maketrans({
        "<": "\\u003c", ">": "\\u003e", "&": "\\u0026",
        "\x85": "\\u0085", "\u2028": "\\u2028", "\u2029": "\\u2029",
    }))


def _finding_lines(finding_id: str, finding: _Finding) -> list[str]:
    lines = [
        f"#### {finding_id} [{finding.severity}] {_canonical_visible_text(finding.title)}",
        "- Changed anchor: " + _json_line({"path": finding.anchor.path, "line": finding.anchor.line}),
    ]
    for evidence in finding.evidence:
        lines.append("- Trigger evidence: " + _json_line({
            "path": evidence.path, "line": evidence.line, "quote": evidence.quote,
        }))
    lines.extend((
        f"- Impact class: {finding.impact_class}",
        f"- Material impact: {_canonical_visible_text(finding.material_impact)}",
    ))
    if finding.performance_basis is not None:
        kind, basis = finding.performance_basis
        lines.append("- Performance basis: " + _json_line({
            "kind": kind, "path": basis.path, "line": basis.line, "quote": basis.quote,
        }))
    lines.extend(_canonical_visible_text(line) for line in finding.prose)
    return lines


def _render_active_section(name: str, findings: list[_PriorFinding]) -> str:
    lines = [f"### {name}", ""]
    if not findings:
        lines.append("None")
    for position, prior in enumerate(findings):
        if position:
            lines.append("")
        lines.extend(_finding_lines(prior.finding_id, prior.finding))
    return "\n".join(lines)


def _prior_severity(prior: _PriorFinding | _PriorHeading) -> str:
    return prior.finding.severity if isinstance(prior, _PriorFinding) else prior.severity


def _prior_title(prior: _PriorFinding | _PriorHeading) -> str:
    return _canonical_visible_text(prior.finding.title if isinstance(prior, _PriorFinding) else prior.title)


def _render_resolved_section(resolved: list[tuple[_PriorFinding | _PriorHeading, SourceAnchor, str]]) -> str:
    lines = ["### Resolved", ""]
    for position, (prior, anchor, resolution) in enumerate(resolved):
        if position:
            lines.append("")
        lines.extend((
            f"#### {prior.finding_id} [{_prior_severity(prior)}] {_prior_title(prior)}",
            "- Fix anchor: " + _json_line({"path": anchor.path, "line": anchor.line}),
            f"- Resolution: {_canonical_visible_text(resolution)}",
        ))
    return "\n".join(lines)


def _render_retracted_section(retracted: list[tuple[_PriorFinding | _PriorHeading, tuple[TriggerEvidence, ...], str]]) -> str:
    lines = ["### Retracted", ""]
    for position, (prior, evidence, reason) in enumerate(retracted):
        if position:
            lines.append("")
        lines.append(f"#### {prior.finding_id} [{_prior_severity(prior)}] {_prior_title(prior)}")
        lines.extend("- Trigger evidence: " + _json_line({
            "path": item.path, "line": item.line, "quote": item.quote,
        }) for item in evidence)
        lines.append(f"- Reason: {_canonical_visible_text(reason)}")
    return "\n".join(lines)


def _render_document(
    new: list[_PriorFinding], still_open: list[_PriorFinding],
    resolved: list[tuple[_PriorFinding | _PriorHeading, SourceAnchor, str]],
    retracted: list[tuple[_PriorFinding | _PriorHeading, tuple[TriggerEvidence, ...], str]],
) -> str:
    sections = [_render_active_section("New findings", new)]
    if still_open:
        sections.append(_render_active_section("Still open", still_open))
    if resolved:
        sections.append(_render_resolved_section(resolved))
    if retracted:
        sections.append(_render_retracted_section(retracted))
    if len(sections) == 1 and not new:
        return sections[0] + "\n\nNo validated blocking issues found.\n"
    return "\n\n".join(sections) + "\n"


def _render_new(reviewer: str, findings: list[_Finding]) -> str:
    return _render_document(
        [_PriorFinding(stable_finding_id(reviewer, finding.anchor, finding.severity, finding.title), finding)
         for finding in findings],
        [], [], [],
    )


def _trim_section_padding(lines: tuple[str, ...]) -> tuple[str, ...]:
    end = len(lines)
    while end and not lines[end - 1]:
        end -= 1
    return lines[:end]


def _prior_heading(block: _Block) -> None:
    if (block.finding_id is None or block.severity == "none" or block.low_severity
            or WORKFLOW_OWNED.search(block.title)):
        raise ScopeValidationError("previous canonical heading is invalid")


def _parse_prior_active(block: _Block, reviewer: str) -> _PriorFinding:
    _prior_heading(block)
    anchors = _field_values(block.lines, "Changed anchor")
    trigger_values = _field_values(block.lines, "Trigger evidence")
    impact_values = _field_values(block.lines, "Impact class")
    material_values = _field_values(block.lines, "Material impact")
    anchor = _anchor(anchors[0]) if len(anchors) == 1 else None
    evidence = tuple(_trigger(value) for value in trigger_values)
    impact = impact_values[0] if len(impact_values) == 1 else ""
    material = material_values[0].strip() if len(material_values) == 1 else ""
    if (anchor is None or not evidence or any(item is None for item in evidence)
            or impact not in IMPACT_CLASSES or not material or WORKFLOW_OWNED.search(material)):
        raise ScopeValidationError("previous canonical finding is invalid")
    basis: tuple[str, TriggerEvidence] | None = None
    bases = _field_values(block.lines, "Performance basis")
    if impact == "performance":
        raw_basis = _strict_object(bases[0], {"kind", "path", "line", "quote"}) if len(bases) == 1 else None
        if (raw_basis is None or raw_basis["kind"] not in {"measured", "unbounded-amplification"}
                or not isinstance(raw_basis["path"], str) or not _safe_integer(raw_basis["line"])
                or not isinstance(raw_basis["quote"], str)):
            raise ScopeValidationError("previous performance basis is invalid")
        basis = (raw_basis["kind"], TriggerEvidence(
            raw_basis["path"], raw_basis["line"], raw_basis["quote"],
        ))
    elif bases:
        raise ScopeValidationError("previous canonical finding is invalid")
    prose = _finding_prose(block.lines)
    if any(WORKFLOW_OWNED.search(line) for line in prose):
        raise ScopeValidationError("previous canonical prose is workflow-owned")
    finding = _Finding(block.severity, block.title, anchor, tuple(evidence), impact, material, prose, basis)
    assert block.finding_id is not None
    # New IDs are assigned from their original anchor. A workflow-rendered
    # Still-open finding keeps that authenticated ID while its current anchor
    # is refreshed on later deltas.
    if (block.section == "New findings"
            and block.finding_id != stable_finding_id(
                reviewer, anchor, block.severity, block.title,
            )):
        raise ScopeValidationError("previous canonical identity is invalid")
    if _trim_section_padding(block.lines) != tuple(_finding_lines(block.finding_id, finding)[1:]):
        raise ScopeValidationError("previous canonical finding is not rendered")
    return _PriorFinding(block.finding_id, finding)


def _validate_prior_closed(
    block: _Block,
) -> tuple[_PriorHeading, SourceAnchor | tuple[TriggerEvidence, ...], str]:
    _prior_heading(block)
    assert block.finding_id is not None
    if block.section == "Resolved":
        anchors = _field_values(block.lines, "Fix anchor")
        resolutions = _field_values(block.lines, "Resolution")
        anchor = _anchor(anchors[0]) if len(anchors) == 1 else None
        resolution = resolutions[0].strip() if len(resolutions) == 1 else ""
        expected = () if anchor is None or not resolution or WORKFLOW_OWNED.search(resolution) else (
            "- Fix anchor: " + _json_line({"path": anchor.path, "line": anchor.line}),
            f"- Resolution: {resolution}",
        )
    else:
        triggers = tuple(_trigger(value) for value in _field_values(block.lines, "Trigger evidence"))
        reasons = _field_values(block.lines, "Reason")
        reason = reasons[0].strip() if len(reasons) == 1 else ""
        expected = () if (not triggers or any(item is None for item in triggers) or not reason
                          or WORKFLOW_OWNED.search(reason)) else tuple(
            "- Trigger evidence: " + _json_line({"path": item.path, "line": item.line, "quote": item.quote})
            for item in triggers if item is not None
        ) + (f"- Reason: {reason}",)
    if not expected or _trim_section_padding(block.lines) != expected:
        raise ScopeValidationError("previous canonical closed finding is invalid")
    heading = _PriorHeading(block.finding_id, block.severity, block.title)
    if block.section == "Resolved":
        assert anchor is not None
        return heading, anchor, resolution
    return heading, tuple(item for item in triggers if item is not None), reason


def _read_previous(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except (FileNotFoundError, IsADirectoryError, OSError) as error:
        raise ScopeValidationError("previous review is unavailable") from error
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_PREVIOUS_CANONICAL_BYTES:
            raise ScopeValidationError("previous review is unsafe")
        chunks: list[bytes] = []
        remaining = MAX_PREVIOUS_CANONICAL_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        if remaining == 0:
            raise ScopeValidationError("previous review is oversized")
    finally:
        os.close(descriptor)
    try:
        return b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError as error:
        raise ScopeValidationError("previous review is not UTF-8") from error


def _validate_previous_layout(text: str, parsed: dict[str, list[_Block]]) -> None:
    lines = text.splitlines()
    raw: dict[str, list[str]] = {}
    order: list[str] = []
    active: str | None = None
    for line in lines:
        if line.startswith("####"):
            if active is None:
                raise ScopeValidationError("previous canonical preamble is invalid")
            raw[active].append(line)
        elif line.startswith("###"):
            if line not in {f"### {name}" for name in SECTION_NAMES}:
                raise ScopeValidationError("previous canonical section is invalid")
            active = line[4:]
            if active in raw:
                raise ScopeValidationError("previous canonical section is duplicated")
            raw[active] = []
            order.append(active)
        elif active is None:
            raise ScopeValidationError("previous canonical preamble is invalid")
        else:
            raw[active].append(line)
    if order != [name for name in SECTION_NAMES if name in raw] or "New findings" not in raw:
        raise ScopeValidationError("previous canonical section order is invalid")
    for section in SECTION_NAMES:
        body = tuple(_trim_section_padding(tuple(raw.get(section, []))))
        if section == "New findings" and not parsed[section]:
            expected = ("", "None")
            if len(order) == 1:
                expected += ("", "No validated blocking issues found.")
            if body != expected:
                raise ScopeValidationError("previous canonical clean result is invalid")
        elif section != "New findings" and section in raw and not parsed[section]:
            raise ScopeValidationError("previous canonical empty section is invalid")
        elif parsed[section] and (not body or body[0]):
            raise ScopeValidationError("previous canonical section padding is invalid")


def _load_prior_active(request: CanonicalizationRequest) -> dict[str, _PriorFinding]:
    if request.previous_review_file is None:
        if request.previous_sha:
            raise ScopeValidationError("missing previous review")
        return {}
    if not request.previous_sha:
        raise ScopeValidationError("missing previous SHA")
    text = _read_previous(request.previous_review_file)
    try:
        sections = _parse_document(text)
    except ValueError as error:
        raise ScopeValidationError("previous canonical document is invalid") from error
    _validate_previous_layout(text, sections)
    prior: dict[str, _PriorFinding] = {}
    prior_new: list[_PriorFinding] = []
    prior_still_open: list[_PriorFinding] = []
    prior_resolved: list[tuple[_PriorHeading, SourceAnchor, str]] = []
    prior_retracted: list[tuple[_PriorHeading, tuple[TriggerEvidence, ...], str]] = []
    seen: set[str] = set()
    for section in SECTION_NAMES:
        for block in sections[section]:
            _prior_heading(block)
            assert block.finding_id is not None
            if block.finding_id in seen:
                raise ScopeValidationError("duplicate previous canonical identity")
            seen.add(block.finding_id)
            if section in {"New findings", "Still open"}:
                finding = _parse_prior_active(block, request.reviewer)
                prior[block.finding_id] = finding
                if section == "New findings":
                    prior_new.append(finding)
                else:
                    prior_still_open.append(finding)
            else:
                heading, value, text_value = _validate_prior_closed(block)
                if section == "Resolved":
                    assert isinstance(value, SourceAnchor)
                    prior_resolved.append((heading, value, text_value))
                else:
                    assert isinstance(value, tuple)
                    prior_retracted.append((heading, value, text_value))
    if text != _render_document(prior_new, prior_still_open, prior_resolved, prior_retracted):
        raise ScopeValidationError("previous canonical bytes are not renderer output")
    return prior


def _remove_canonical(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _read_candidate(path: Path) -> tuple[str, bytes | None]:
    """Read at most one byte beyond the candidate ceiling from one regular descriptor."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except (FileNotFoundError, IsADirectoryError, OSError):
        return "candidate_missing", None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return "candidate_missing", None
        if os.fstat(descriptor).st_size > MAX_CANDIDATE_BYTES:
            return "candidate_oversize", None
        chunks: list[bytes] = []
        remaining = MAX_CANDIDATE_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    finally:
        os.close(descriptor)
    if remaining == 0:
        return "candidate_oversize", None
    return "", b"".join(chunks)


def _validate_still_open(block: _Block, prior: _PriorFinding, scope: object) -> tuple[_Finding | None, str | None]:
    inherited = _Block(
        block.index, block.section, block.finding_id, prior.finding.severity, False,
        prior.finding.title, block.lines,
    )
    return _validate_new(inherited, scope)


def _validate_resolved(block: _Block, scope: object) -> tuple[SourceAnchor | None, str | None]:
    anchors = _field_values(block.lines, "Fix anchor")
    resolutions = _field_values(block.lines, "Resolution")
    anchor = _anchor(anchors[0]) if len(anchors) == 1 else None
    resolution = resolutions[0].strip() if len(resolutions) == 1 else ""
    if (anchor is None or not scope.validate_fix_anchor(anchor) or not resolution
            or WORKFLOW_OWNED.search(resolution)):
        return None, None
    if _trim_section_padding(block.lines) != (
        "- Fix anchor: " + _candidate_json_line({"path": anchor.path, "line": anchor.line}),
        f"- Resolution: {resolution}",
    ):
        return None, None
    return anchor, _canonical_visible_text(resolution)


def _validate_retracted(block: _Block, scope: object) -> tuple[tuple[TriggerEvidence, ...] | None, str | None]:
    evidence = tuple(_trigger(value) for value in _field_values(block.lines, "Trigger evidence"))
    reasons = _field_values(block.lines, "Reason")
    reason = reasons[0].strip() if len(reasons) == 1 else ""
    if (not evidence or any(item is None or not scope.validate_trigger(item) for item in evidence)
            or not reason or WORKFLOW_OWNED.search(reason)):
        return None, None
    parsed = tuple(item for item in evidence if item is not None)
    expected = tuple(
        "- Trigger evidence: " + _candidate_json_line({
            "path": item.path, "line": item.line, "quote": item.quote,
        })
        for item in parsed
    ) + (f"- Reason: {reason}",)
    if _trim_section_padding(block.lines) != expected:
        return None, None
    return parsed, _canonical_visible_text(reason)


def canonicalize(request: CanonicalizationRequest) -> CanonicalizationResult:
    """Validate an untrusted candidate and create canonical Markdown only on success."""
    try:
        candidate_reason, payload = _read_candidate(request.candidate_file)
        if candidate_reason:
            result = _hard(candidate_reason)
        else:
            assert payload is not None
            try:
                text = payload.decode("utf-8")
            except UnicodeDecodeError:
                result = _hard("invalid_utf8")
            else:
                try:
                    sections = _parse_document(text)
                    scope = load_review_scope(
                        request.repository_root, request.scope_manifest, request.selected_diff,
                        diff_mode=request.diff_mode, previous_sha=request.previous_sha,
                        expected_repository=request.expected_repository,
                    )
                    prior_active = _load_prior_active(request)
                except ScopeValidationError:
                    result = _hard("scope_invalid")
                except ValueError as error:
                    diagnostic = str(error)
                    if diagnostic not in AMBIGUITY_DIAGNOSTICS:
                        diagnostic = "unclassified"
                    print(
                        f"review-canonicalization-diagnostic: {diagnostic}",
                        file=sys.stderr,
                    )
                    line = error.line if isinstance(error, _CandidateSyntaxError) else 1
                    column = error.column if isinstance(error, _CandidateSyntaxError) else 1
                    result = _hard(
                        "ambiguous_document",
                        (
                            CandidateValidation(
                                "initial",
                                hashlib.sha256(payload).hexdigest(),
                                False,
                                diagnostic,
                                line,
                                column,
                            ),
                        ),
                    )
                else:
                    accepted: list[tuple[_Block, _Finding]] = []
                    still_open: list[_PriorFinding] = []
                    resolved: list[tuple[_PriorFinding, SourceAnchor, str]] = []
                    retracted: list[tuple[_PriorFinding, tuple[TriggerEvidence, ...], str]] = []
                    reasons: list[CandidateReason] = []
                    filtered_severities: list[str] = []
                    filtered = normalized = 0
                    for block in sections["New findings"]:
                        finding, reason = _validate_new(block, scope)
                        if reason is not None:
                            filtered += 1
                            filtered_severities.append(block.severity)
                            reasons.append(_claim_reason(block, "filtered", reason))
                        else:
                            assert finding is not None
                            accepted.append((block, finding))
                    bound: dict[str, list[_Block]] = {}
                    for section in ("Still open", "Resolved", "Retracted"):
                        for block in sections[section]:
                            if block.finding_id in prior_active:
                                assert block.finding_id is not None
                                bound.setdefault(block.finding_id, []).append(block)
                    duplicated = {finding_id for finding_id, blocks in bound.items() if len(blocks) > 1}
                    for section in ("Still open", "Resolved", "Retracted"):
                        for block in sections[section]:
                            prior = prior_active.get(block.finding_id or "")
                            if prior is None:
                                normalized += 1
                                reasons.append(_claim_reason(block, "normalized", "unknown_prior_id"))
                                continue
                            if prior.finding_id in duplicated:
                                normalized += 1
                                reasons.append(_claim_reason(block, "normalized", "duplicate_prior_binding"))
                                continue
                            if section == "Still open":
                                finding, reason = _validate_still_open(block, prior, scope)
                                if reason is not None:
                                    filtered += 1
                                    filtered_severities.append(prior.finding.severity)
                                    reasons.append(_claim_reason(block, "filtered", reason))
                                else:
                                    assert finding is not None
                                    still_open.append(_PriorFinding(prior.finding_id, finding))
                            elif section == "Resolved":
                                anchor, resolution = _validate_resolved(block, scope)
                                if anchor is None or resolution is None:
                                    normalized += 1
                                    reasons.append(_claim_reason(block, "normalized", "missing_fix_anchor"))
                                else:
                                    resolved.append((prior, anchor, resolution))
                            else:
                                evidence, reason = _validate_retracted(block, scope)
                                if evidence is None or reason is None:
                                    normalized += 1
                                    reasons.append(_claim_reason(block, "normalized", "invalid_trigger_evidence"))
                                else:
                                    retracted.append((prior, evidence, reason))
                    canonical_new: list[_PriorFinding] = []
                    active_ids = set(prior_active)
                    for block, finding in accepted:
                        finding_id = stable_finding_id(
                            request.reviewer, finding.anchor, finding.severity, finding.title,
                        )
                        if finding_id in active_ids:
                            normalized += 1
                            reasons.append(_claim_reason(
                                block, "normalized", "duplicate_prior_binding",
                            ))
                            continue
                        active_ids.add(finding_id)
                        canonical_new.append(_PriorFinding(finding_id, finding))
                    rank = {"none": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
                    maximum = max(filtered_severities, key=lambda item: rank[item], default="none")
                    result = CanonicalizationResult(
                        True, len(canonical_new) + len(still_open), filtered, normalized, maximum, "",
                        tuple(sorted(reasons, key=lambda item: item.index)),
                        (),
                    )
                    canonical_payload = _render_document(
                        canonical_new,
                        still_open, resolved, retracted,
                    ).encode("utf-8")
                    if len(canonical_payload) > MAX_CANONICAL_BYTES:
                        result = _hard("candidate_oversize")
                    else:
                        _write_atomic(
                            request.canonical_file,
                            canonical_payload,
                        )
    except (OSError, TypeError, AttributeError):
        result = _hard("canonicalizer_error")
    if not result.document_valid:
        try:
            _remove_canonical(request.canonical_file)
        except OSError:
            return _hard("canonicalizer_error")
    return result


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise OSError("unsafe destination")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _summary(result: CanonicalizationResult) -> list[str]:
    lines = [
        "review-canonicalization: "
        f"document_valid={str(result.document_valid).lower()} accepted={result.accepted_count} "
        f"filtered={result.filtered_count} normalized={result.normalized_count} "
        f"filtered_max={result.filtered_max_severity} failure_reason={result.failure_reason}"
    ]
    lines.extend(
        f"candidate[{item.index}]: section={item.section} outcome={item.outcome} "
        f"reason={item.reason} claimed_severity={item.claimed_severity}"
        for item in result.candidate_reasons
    )
    lines.extend(
        "candidate-validation: "
        f"attempt={item.attempt} sha256={item.sha256} valid=false "
        f"rule={item.rule} line={item.line} column={item.column}"
        for item in result.candidate_validations
    )
    return lines


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reviewer", required=True, choices=("claude", "gemini"))
    parser.add_argument("--candidate-file", required=True, type=Path)
    parser.add_argument("--canonical-file", required=True, type=Path)
    parser.add_argument("--result-file", required=True, type=Path)
    parser.add_argument("--scope-manifest", required=True, type=Path)
    parser.add_argument("--selected-diff", required=True, type=Path)
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--diff-mode", required=True, choices=("full", "delta"))
    parser.add_argument("--previous-sha", default="")
    parser.add_argument(
        "--previous-review-file", type=lambda value: None if value == "" else Path(value)
    )
    parser.add_argument("--expected-repository", required=True)
    parser.add_argument("--github-output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    request = CanonicalizationRequest(
        args.reviewer, args.candidate_file, args.canonical_file, args.result_file,
        args.scope_manifest, args.selected_diff, args.repository_root, args.diff_mode,
        args.previous_sha, args.previous_review_file, args.expected_repository,
    )
    try:
        result = canonicalize(request)
    except BaseException:
        result = _hard("canonicalizer_error")
        try:
            _remove_canonical(request.canonical_file)
        except OSError:
            return 1
    payload = json.dumps(result.to_dict(), ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    if len(payload) > 131_072:
        return 1
    try:
        _write_atomic(args.result_file, payload)
        if args.github_output is not None:
            scalar = result.to_dict()
            # Reason codes come from a fixed canonicalizer vocabulary, so surfacing them
            # carries no untrusted text and lets a reader see why a finding was dropped
            # without opening the run log.
            filtered_reasons = ",".join(sorted({
                item.reason for item in result.candidate_reasons if item.outcome == "filtered"
            }))
            output = ("".join(
                f"{name}={str(scalar[name]).lower() if isinstance(scalar[name], bool) else scalar[name]}\n"
                for name in ("document_valid", "accepted_count", "filtered_count", "normalized_count", "filtered_max_severity", "failure_reason")
            ) + f"filtered_reasons={filtered_reasons}\n").encode("utf-8")
            _write_atomic(args.github_output, output)
    except OSError:
        return 1
    print("\n".join(_summary(result)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
