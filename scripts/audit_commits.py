#!/usr/bin/env python3
"""Audit commit messages without turning findings or audit errors into failures."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import html
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Sequence


_DIR = Path(__file__).resolve().parent
_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def _load_checker():
    path = _DIR / "check_commit_message.py"
    name = "_audit_check_commit_message"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_checker = None


def _checker_module():
    global _checker
    if _checker is None:
        _checker = _load_checker()
    return _checker


@dataclass(frozen=True)
class AuditRow:
    sha: str
    title: str
    auto: bool
    valid: bool
    title_findings: tuple[str, ...]
    evidence_findings: tuple[str, ...]


@dataclass(frozen=True)
class AuditReport:
    rows: tuple[AuditRow, ...]
    notes: tuple[str, ...]
    sources: dict[str, str]


def _validated_sha(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase 40- or 64-hex SHA")
    return value


def _run_git(repo_dir: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo_dir), *args],
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {detail or result.returncode}")
    return result.stdout


def _commit_exists(repo_dir: Path, sha: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo_dir), "cat-file", "-e", f"{sha}^{{commit}}"],
        capture_output=True,
        check=False,
        timeout=30,
    )
    return result.returncode == 0


def _is_ancestor(repo_dir: Path, before: str, after: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo_dir), "merge-base", "--is-ancestor", before, after],
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode not in (0, 1):
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git merge-base --is-ancestor failed: {detail or result.returncode}")
    return result.returncode == 0


def _rev_list(repo_dir: Path, revision: str, *, limit: int) -> tuple[list[str], int]:
    count_text = _run_git(
        repo_dir, "rev-list", "--count", "--no-merges", revision
    ).decode("ascii").strip()
    total = int(count_text)
    # rev-list applies --max-count before --reverse. Skip the newest surplus
    # commits first so the bounded output remains the oldest part of the range.
    skip = max(total - limit, 0)
    output = _run_git(
        repo_dir,
        "rev-list",
        "--no-merges",
        "--reverse",
        f"--skip={skip}",
        f"--max-count={limit}",
        revision,
    ).decode("ascii")
    shas = [line for line in output.splitlines() if line]
    if any(_SHA.fullmatch(sha) is None for sha in shas):
        raise RuntimeError("git rev-list returned an invalid SHA")
    return shas, total


def collect_range(
    repo_dir: Path,
    *,
    event_name: str,
    event: dict,
    limit: int = 250,
) -> tuple[list[str], list[str]]:
    """Return non-merge commits for one supported GitHub event, oldest first."""
    if not isinstance(event, dict):
        raise ValueError("event must be an object")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise ValueError("limit must be a positive integer")

    notes: list[str] = []
    if event_name == "pull_request":
        try:
            pull_request = event["pull_request"]
            before_value = pull_request["base"]["sha"]
            after_value = pull_request["head"]["sha"]
        except (KeyError, TypeError) as error:
            raise ValueError("pull_request event is missing base/head SHA") from error
        before = _validated_sha(before_value, "pull_request.base.sha")
        after = _validated_sha(after_value, "pull_request.head.sha")
        revision = f"{before}..{after}"
    elif event_name == "push":
        before = _validated_sha(event.get("before"), "before")
        after = _validated_sha(event.get("after"), "after")
        before_unusable = set(before) == {"0"} or not _commit_exists(repo_dir, before)
        if before_unusable:
            reason = "0-SHA" if set(before) == {"0"} else "도달할 수 없는 before"
            notes.append(f"{reason}이므로 after 커밋 1건만 감사했다.")
            revision = f"{after}^!"
        elif not _is_ancestor(repo_dir, before, after):
            notes.append("before가 after의 조상이 아니므로 after 커밋 1건만 감사했다.")
            revision = f"{after}^!"
        else:
            revision = f"{before}..{after}"
    else:
        raise ValueError(f"unsupported event name: {event_name}")

    shas, total = _rev_list(repo_dir, revision, limit=limit)
    omitted = total - len(shas)
    if omitted:
        notes.append(f"커밋 {omitted}개를 제한 {limit}개 이후 생략했다.")
    return shas, notes


def audit(repo_dir: Path, shas: list[str]) -> AuditReport:
    checker = _checker_module()
    rows: list[AuditRow] = []
    sources: dict[str, str] | None = None
    for index, raw_sha in enumerate(shas):
        sha = _validated_sha(raw_sha, f"shas[{index}]")
        message = _run_git(repo_dir, "log", "-1", "--format=%B", sha, "--").decode("utf-8")
        checked = checker.check_message(message)
        current_sources = dict(checked["sources"])
        if sources is None:
            sources = current_sources
        elif sources != current_sources:
            raise RuntimeError("checker source digests changed during the audit")
        title = message.splitlines()[0] if message.splitlines() else ""
        rows.append(
            AuditRow(
                sha=sha,
                title=title,
                auto=bool(checked["auto"]),
                valid=bool(checked["valid"]),
                title_findings=tuple(str(code) for code in checked["title_findings"]),
                evidence_findings=tuple(str(code) for code in checked["evidence_findings"]),
            )
        )
    if sources is None:
        sources = dict(checker.source_digests())
    return AuditReport(tuple(rows), (), sources)


def _markdown_cell(value: object) -> str:
    text = str(value)
    text = "".join(
        f"\\x{ord(character):02x}" if ord(character) < 32 or ord(character) == 127 else character
        for character in text
    )
    return html.escape(text, quote=False).replace("|", "&#124;").replace("`", "&#96;")


def render_summary(report: AuditReport, *, automation_commit: str) -> str:
    lines = [
        "# Commit evidence audit",
        "",
        f"- automation commit: `{_markdown_cell(automation_commit)}`",
        "- rule sources:",
    ]
    for name, digest in sorted(report.sources.items()):
        lines.append(f"  - `{_markdown_cell(name)}`: `{_markdown_cell(digest)}`")
    if report.notes:
        lines.extend(("", "## Notes", ""))
        lines.extend(f"- {_markdown_cell(note)}" for note in report.notes)
    lines.extend(
        (
            "",
            "## Results",
            "",
            "| SHA(7) | 제목 | 제목 규칙 | 변경 증거 | 판정 |",
            "| --- | --- | --- | --- | --- |",
        )
    )
    for row in report.rows:
        title_findings = ", ".join(row.title_findings) or "-"
        evidence_findings = ", ".join(row.evidence_findings) or "-"
        verdict = "auto" if row.auto else "ok" if row.valid else "위반"
        lines.append(
            "| "
            + " | ".join(
                _markdown_cell(value)
                for value in (
                    row.sha[:7], row.title, title_findings, evidence_findings, verdict
                )
            )
            + " |"
        )
    if not report.rows:
        lines.append("| - | 감사할 커밋 없음 | - | - | ok |")
    return "\n".join(lines) + "\n"


def _command_message(value: object) -> str:
    return str(value).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def render_annotations(report: AuditReport) -> list[str]:
    annotations = []
    for row in report.rows:
        if row.valid or row.auto:
            continue
        codes = ",".join((*row.title_findings, *row.evidence_findings))
        annotations.append(
            f"::warning title=commit-evidence-audit::{row.sha[:7]} "
            f"{_command_message(codes)}"
        )
    return annotations


def _error_report(error: Exception) -> AuditReport:
    return AuditReport(
        (),
        (f"감사 내부 오류: {type(error).__name__}: {error}",),
        {},
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit commit evidence without failing CI")
    parser.add_argument("--repo-dir", type=Path, required=True)
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--event-path", type=Path, required=True)
    parser.add_argument("--automation-commit", required=True)
    parser.add_argument("--summary-path", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        automation_commit = _validated_sha(args.automation_commit, "automation commit")
        event = json.loads(args.event_path.read_text(encoding="utf-8"))
        shas, notes = collect_range(
            args.repo_dir, event_name=args.event_name, event=event
        )
        audited = audit(args.repo_dir, shas)
        report = AuditReport(audited.rows, tuple(notes), audited.sources)
    except Exception as error:  # noqa: BLE001 - audit is deliberately fail-open
        automation_commit = args.automation_commit
        report = _error_report(error)
        print(
            "::warning title=commit-evidence-audit::"
            + _command_message(f"internal error: {type(error).__name__}: {error}")
        )

    for annotation in render_annotations(report):
        print(annotation)
    try:
        args.summary_path.write_text(
            render_summary(report, automation_commit=automation_commit), encoding="utf-8"
        )
    except Exception as error:  # noqa: BLE001 - summary output cannot fail the job
        print(
            "::warning title=commit-evidence-audit::"
            + _command_message(f"cannot write summary: {type(error).__name__}: {error}")
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
