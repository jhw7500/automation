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
MAX_CANDIDATE_BLOCKS = 512
MAX_SAFE_INTEGER = (1 << 53) - 1
PROOF_DEFICIT = re.compile(
    r"\b(?:plausible(?:\s+but)?\s+unconfirmed|cannot\s+(?:confirm|verify)|"
    r"not\s+confirmed|pending\s+confirmation|unverified\s+external)\b",
    re.IGNORECASE,
)
WORKFLOW_OWNED = re.compile(
    r"(?:<!--.*automation:|^#{1,6}\s*(?:status|run|reviewed|validation)\b|"
    r"^\s*(?:status|run|reviewed|validation)\s*:)", re.IGNORECASE,
)
SECTION_NAMES = ("New findings", "Still open", "Resolved", "Retracted")


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
class CanonicalizationResult:
    document_valid: bool
    accepted_count: int
    filtered_count: int
    normalized_count: int
    filtered_max_severity: Literal["none", "MEDIUM", "HIGH", "CRITICAL"]
    failure_reason: str
    candidate_reasons: tuple[CandidateReason, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": 1,
            "document_valid": self.document_valid,
            "accepted_count": self.accepted_count,
            "filtered_count": self.filtered_count,
            "normalized_count": self.normalized_count,
            "filtered_max_severity": self.filtered_max_severity,
            "failure_reason": self.failure_reason,
            "candidate_reasons": [reason.to_dict() for reason in self.candidate_reasons],
        }


@dataclass(frozen=True)
class _Block:
    index: int
    section: Literal["New findings", "Still open", "Resolved", "Retracted"]
    severity: Literal["none", "MEDIUM", "HIGH", "CRITICAL"]
    low_severity: bool
    title: str
    lines: tuple[str, ...]


@dataclass(frozen=True)
class _Finding:
    severity: Literal["MEDIUM", "HIGH", "CRITICAL"]
    title: str
    anchor: SourceAnchor
    evidence: tuple[TriggerEvidence, ...]
    impact_class: str
    material_impact: str
    prose: tuple[str, ...]


def normalize_title(title: str) -> str:
    return " ".join(title.split()).casefold()


def stable_finding_id(reviewer: str, anchor: SourceAnchor, severity: str, title: str) -> str:
    identity = "\0".join((reviewer, anchor.path, str(anchor.line), severity, normalize_title(title)))
    return "RVW-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]


def _hard(reason: str) -> CanonicalizationResult:
    return CanonicalizationResult(False, 0, 0, 0, "none", reason, ())


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
    except (ValueError, json.JSONDecodeError):
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


def _heading(line: str) -> tuple[Literal["none", "MEDIUM", "HIGH", "CRITICAL"], bool, str] | None:
    matched = re.fullmatch(r"#### (?:RVW-[0-9a-f]{12} )?\[(CRITICAL|HIGH|MEDIUM|LOW|[^\]]+)\] (.+)", line)
    if matched is None:
        return None
    raw_severity, title = matched.groups()
    severity: Literal["none", "MEDIUM", "HIGH", "CRITICAL"]
    severity = raw_severity if raw_severity in SEVERITIES else "none"
    return severity, raw_severity == "LOW", title


def _parse_document(text: str) -> dict[str, list[_Block]]:
    stripped = text.strip()
    if stripped in {"No blocking issues found.", "No validated blocking issues found."}:
        return {name: [] for name in SECTION_NAMES}
    lines = text.splitlines()
    sections: dict[str, list[str]] = {}
    active: str | None = None
    for line in lines:
        if line.startswith("####"):
            if active is None:
                raise ValueError("ambiguous")
            sections[active].append(line)
        elif line.startswith("###"):
            if line not in {f"### {name}" for name in SECTION_NAMES}:
                raise ValueError("ambiguous")
            active = line[4:]
            if active in sections:
                raise ValueError("ambiguous")
            sections[active] = []
        elif active is not None:
            sections[active].append(line)
    if "New findings" not in sections:
        raise ValueError("ambiguous")
    parsed: dict[str, list[_Block]] = {name: [] for name in SECTION_NAMES}
    index = 0
    for section in SECTION_NAMES:
        contents = sections.get(section, [])
        heading_positions = [position for position, line in enumerate(contents) if line.startswith("####")]
        if not heading_positions:
            meaningful = [line for line in contents if line.strip()]
            if section == "New findings" and meaningful != ["None"]:
                raise ValueError("ambiguous")
            continue
        if section == "New findings" and any(line.strip() == "None" for line in contents):
            raise ValueError("ambiguous")
        if any(line.strip() for line in contents[:heading_positions[0]]):
            raise ValueError("ambiguous")
        for block_number, start in enumerate(heading_positions):
            parsed_heading = _heading(contents[start])
            if parsed_heading is None:
                raise ValueError("ambiguous")
            end = heading_positions[block_number + 1] if block_number + 1 < len(heading_positions) else len(contents)
            severity, low_severity, title = parsed_heading
            parsed[section].append(_Block(index, section, severity, low_severity, title, tuple(contents[start + 1:end])))
            index += 1
            if index > MAX_CANDIDATE_BLOCKS:
                raise ValueError("ambiguous")
    return parsed


def _field_values(lines: tuple[str, ...], name: str) -> list[str]:
    prefix = f"- {name}: "
    return [line[len(prefix):] for line in lines if line.startswith(prefix)]


def _claim_reason(block: _Block, outcome: Literal["filtered", "normalized"], reason: str) -> CandidateReason:
    return CandidateReason(block.index, block.section, outcome, reason, block.severity)


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
    impact_values = _field_values(block.lines, "Impact class")
    impact = impact_values[0] if len(impact_values) == 1 else ""
    if impact in {"style", "maintainability", "cleanup"}:
        return None, "non_actionable_category"
    if impact not in IMPACT_CLASSES:
        return None, "invalid_impact_class"
    material_values = _field_values(block.lines, "Material impact")
    material = material_values[0].strip() if len(material_values) == 1 else ""
    extra_prose = tuple(
        line for line in block.lines
        if line and not line.startswith("- ") and not WORKFLOW_OWNED.search(line)
    )
    material_text = " ".join((material, *extra_prose)).strip()
    if not material or PROOF_DEFICIT.search(material_text):
        return None, "missing_material_impact"
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
    return _Finding(block.severity, block.title, anchor, evidence, impact, material, extra_prose), None


def _json_line(value: dict[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _render_new(reviewer: str, findings: list[_Finding]) -> str:
    lines = ["### New findings", ""]
    if not findings:
        lines.extend(("None", "", "No validated blocking issues found."))
        return "\n".join(lines) + "\n"
    for position, finding in enumerate(findings):
        if position:
            lines.append("")
        finding_id = stable_finding_id(reviewer, finding.anchor, finding.severity, finding.title)
        lines.extend((
            f"#### {finding_id} [{finding.severity}] {finding.title}",
            "- Changed anchor: " + _json_line({"path": finding.anchor.path, "line": finding.anchor.line}),
        ))
        for evidence in finding.evidence:
            lines.append("- Trigger evidence: " + _json_line({
                "path": evidence.path, "line": evidence.line, "quote": evidence.quote,
            }))
        lines.extend((
            f"- Impact class: {finding.impact_class}",
            f"- Material impact: {finding.material_impact}",
        ))
        lines.extend(finding.prose)
    return "\n".join(lines) + "\n"


def _remove_canonical(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def canonicalize(request: CanonicalizationRequest) -> CanonicalizationResult:
    """Validate an untrusted candidate and create canonical Markdown only on success."""
    try:
        if not request.candidate_file.is_file():
            result = _hard("candidate_missing")
        else:
            payload = request.candidate_file.read_bytes()
            if len(payload) > MAX_CANDIDATE_BYTES:
                result = _hard("candidate_oversize")
            else:
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
                    except ScopeValidationError:
                        result = _hard("scope_invalid")
                    except ValueError:
                        result = _hard("ambiguous_document")
                    else:
                        accepted: list[_Finding] = []
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
                                accepted.append(finding)
                        # Task 2 recognizes carryover grammar, but authenticated prior binding is Task 3.
                        for section in ("Still open", "Resolved", "Retracted"):
                            for block in sections[section]:
                                normalized += 1
                                reasons.append(_claim_reason(block, "normalized", "unknown_prior_id"))
                        rank = {"none": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
                        maximum = max(filtered_severities, key=lambda item: rank[item], default="none")
                        result = CanonicalizationResult(
                            True, len(accepted), filtered, normalized, maximum, "", tuple(reasons)
                        )
                        _write_atomic(
                            request.canonical_file,
                            _render_new(request.reviewer, accepted).encode("utf-8"),
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
    parser.add_argument("--previous-review-file", type=Path)
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
            output = "".join(
                f"{name}={str(scalar[name]).lower() if isinstance(scalar[name], bool) else scalar[name]}\n"
                for name in ("document_valid", "accepted_count", "filtered_count", "normalized_count", "filtered_max_severity", "failure_reason")
            ).encode("utf-8")
            _write_atomic(args.github_output, output)
    except OSError:
        return 1
    print("\n".join(_summary(result)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
