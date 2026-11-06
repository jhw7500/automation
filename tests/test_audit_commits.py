from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/audit_commits.py"

SPEC = importlib.util.spec_from_file_location("audit_commits", SCRIPT)
assert SPEC and SPEC.loader
auditor = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = auditor
SPEC.loader.exec_module(auditor)


def git(repo: Path, *args: str, input_text: str | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        input=input_text,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def init_repo(path: Path) -> Path:
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    git(path, "config", "user.name", "Audit Test")
    git(path, "config", "user.email", "audit@example.invalid")
    return path


def commit(repo: Path, subject: str, *, parents: tuple[str, ...] = ()) -> str:
    if parents:
        tree = git(repo, "show", "-s", "--format=%T", parents[0])
        args = ["commit-tree", tree, "-m", subject]
        for parent in parents:
            args.extend(("-p", parent))
        return git(repo, *args)
    return git(repo, "commit", "--allow-empty", "-q", "-m", subject) or git(repo, "rev-parse", "HEAD")


def linear_commits(repo: Path, count: int) -> list[str]:
    result = []
    for index in range(count):
        commit(repo, f"test: commit {index}")
        result.append(git(repo, "rev-parse", "HEAD"))
    return result


def test_pr_range_excludes_merges(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    base, first, second = linear_commits(repo, 3)
    merge = commit(repo, "Merge branch 'topic'", parents=(second, first))

    shas, notes = auditor.collect_range(
        repo,
        event_name="pull_request",
        event={"pull_request": {"base": {"sha": base}, "head": {"sha": merge}}},
    )

    assert shas == [first, second]
    assert notes == []


def test_push_range_before_after(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    before, middle, after = linear_commits(repo, 3)

    shas, notes = auditor.collect_range(
        repo, event_name="push", event={"before": before, "after": after}
    )

    assert shas == [middle, after]
    assert notes == []


def test_unreachable_before_audits_after_only(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    linear_commits(repo, 2)
    after = git(repo, "rev-parse", "HEAD")

    for before in ("0" * 40, "f" * 40):
        shas, notes = auditor.collect_range(
            repo, event_name="push", event={"before": before, "after": after}
        )
        assert shas == [after]
        assert len(notes) == 1
        assert "after" in notes[0]


def test_non_ancestor_before_audits_after_only(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    before = linear_commits(repo, 1)[0]
    tree = git(repo, "show", "-s", "--format=%T", before)
    after = git(repo, "commit-tree", tree, "-m", "test: rewritten history")

    shas, notes = auditor.collect_range(
        repo, event_name="push", event={"before": before, "after": after}
    )

    assert shas == [after]
    assert notes == ["before가 after의 조상이 아니므로 after 커밋 1건만 감사했다."]


def test_after_only_excludes_a_merge_commit(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    first, second = linear_commits(repo, 2)
    merge = commit(repo, "Merge branch 'topic'", parents=(second, first))

    shas, notes = auditor.collect_range(
        repo, event_name="push", event={"before": "0" * 40, "after": merge}
    )

    assert shas == []
    assert notes


def test_commit_limit_truncates_with_note(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    commits = linear_commits(repo, 5)

    shas, notes = auditor.collect_range(
        repo,
        event_name="push",
        event={"before": commits[0], "after": commits[-1]},
        limit=2,
    )

    assert shas == commits[1:3]
    assert notes == ["커밋 2개를 제한 2개 이후 생략했다."]


def test_summary_has_source_digests_and_rows() -> None:
    row = auditor.AuditRow(
        sha="a" * 40,
        title="test: <제목>|행\t끝",
        auto=False,
        valid=False,
        title_findings=("title-type-enum",),
        evidence_findings=("heading-contract",),
    )
    report = auditor.AuditReport(
        rows=(row,), notes=("범위 메모",), sources={"checker.py": "b" * 64}
    )

    summary = auditor.render_summary(report, automation_commit="c" * 40)

    assert "c" * 40 in summary
    assert "checker.py" in summary and "b" * 64 in summary
    assert "aaaaaaa" in summary
    assert "&lt;제목&gt;&#124;행\\x09끝" in summary
    assert "title-type-enum" in summary and "heading-contract" in summary
    assert "범위 메모" in summary


def test_annotations_only_for_violations_and_escape_commands() -> None:
    valid = auditor.AuditRow("a" * 40, "ok", False, True, (), ())
    auto = auditor.AuditRow("b" * 40, "auto", True, True, (), ())
    invalid = auditor.AuditRow(
        "c" * 40, "bad", False, False, ("bad%code\n::error::pwn",), ("evidence",)
    )

    annotations = auditor.render_annotations(
        auditor.AuditReport((valid, auto, invalid), (), {})
    )

    assert annotations == [
        "::warning title=commit-evidence-audit::ccccccc "
        "bad%25code%0A::error::pwn,evidence"
    ]


def test_rejects_untrusted_sha_before_git(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    with __import__("pytest").raises(ValueError, match="SHA"):
        auditor.collect_range(
            repo,
            event_name="push",
            event={"before": "0" * 40, "after": "--help"},
        )


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_internal_error_is_warning_not_failure(tmp_path: Path) -> None:
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps({"before": "0" * 40, "after": "a" * 40}))
    summary = tmp_path / "summary.md"

    result = run_cli(
        "--repo-dir", str(tmp_path / "missing"),
        "--event-name", "push",
        "--event-path", str(event_path),
        "--automation-commit", "b" * 40,
        "--summary-path", str(summary),
    )

    assert result.returncode == 0
    assert "::warning title=commit-evidence-audit::" in result.stdout
    assert summary.is_file()
    assert "감사 내부 오류" in summary.read_text()


def test_missing_checker_is_warning_not_import_failure(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    after = linear_commits(repo, 1)[0]
    isolated_script = tmp_path / "isolated" / "audit_commits.py"
    isolated_script.parent.mkdir()
    isolated_script.write_bytes(SCRIPT.read_bytes())
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps({"before": "0" * 40, "after": after}))
    summary = tmp_path / "summary.md"

    result = subprocess.run(
        [
            sys.executable, "-I", str(isolated_script),
            "--repo-dir", str(repo),
            "--event-name", "push",
            "--event-path", str(event_path),
            "--automation-commit", "b" * 40,
            "--summary-path", str(summary),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "::warning title=commit-evidence-audit::" in result.stdout
    assert "FileNotFoundError" in summary.read_text()


def test_non_utf8_commit_message_is_warning_not_failure(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    tree = git(repo, "mktree", input_text="")
    raw_commit = (
        f"tree {tree}\n"
        "author Audit <a@example.invalid> 1700000000 +0000\n"
        "committer Audit <a@example.invalid> 1700000000 +0000\n\n"
    ).encode() + b"test: \xff\n"
    created = subprocess.run(
        ["git", "-C", str(repo), "hash-object", "-t", "commit", "-w", "--stdin"],
        input=raw_commit,
        capture_output=True,
        check=True,
    ).stdout.decode().strip()
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps({"before": "0" * 40, "after": created}))
    summary = tmp_path / "summary.md"

    result = run_cli(
        "--repo-dir", str(repo),
        "--event-name", "push",
        "--event-path", str(event_path),
        "--automation-commit", "b" * 40,
        "--summary-path", str(summary),
    )

    assert result.returncode == 0
    assert "::warning title=commit-evidence-audit::" in result.stdout
    assert "UnicodeDecodeError" in summary.read_text()
