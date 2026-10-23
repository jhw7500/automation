from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/check_commit_message.py"

SPEC = importlib.util.spec_from_file_location("check_commit_message", SCRIPT)
assert SPEC and SPEC.loader
checker = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = checker
SPEC.loader.exec_module(checker)

VALID = """feat(commit): 직접 커밋에도 변경 증거를 남긴다

 ### Contract version
v1

 ### Why
직접 커밋은 PR 본문 검증을 거치지 않아 변경 이유가 남지 않았다.

 ### Changes
- 커밋 제목 규칙과 메시지 검사기를 추가한다.
- 검사기가 계약 validator를 함께 호출한다.

 ### Validation
- `python3 -m pytest -q tests/test_check_commit_message.py` 통과.

 ### References
- https://github.com/jhw7500/automation/issues/237
"""


def test_contract_valid_message_passes() -> None:
    result = checker.check_message(VALID)
    assert result["valid"] is True
    assert result["auto"] is False
    assert result["title_findings"] == []
    assert result["evidence_findings"] == []
    assert result["rules_version"] == "1"


def test_missing_fields_reported_as_evidence_findings() -> None:
    result = checker.check_message("feat: 필드가 없다\n\n본문만 있다.\n")
    assert result["title_findings"] == []
    assert result["evidence_findings"]
    assert result["valid"] is False


def test_title_and_evidence_findings_combined() -> None:
    result = checker.check_message("Fix: 고친다.\n\n본문만 있다.\n")
    assert set(result["title_findings"]) == {
        "title-type-case",
        "title-type-enum",
        "title-subject-full-stop",
    }
    assert result["evidence_findings"]
    assert result["valid"] is False


def test_auto_message_skips_both() -> None:
    result = checker.check_message('Revert "x"\n\nThis reverts commit abc.\n')
    assert result["auto"] is True
    assert result["valid"] is True
    assert result["title_findings"] == []
    assert result["evidence_findings"] == []


def test_title_over_72_is_validator_only() -> None:
    title = "feat: " + "가" * 67
    assert len(title) == 73
    result = checker.check_message(VALID.replace(VALID.splitlines()[0], title, 1))
    assert "commit-title-too-long" in result["evidence_findings"]
    assert result["title_findings"] == []
    assert result["valid"] is False


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_exit_codes(tmp_path: Path) -> None:
    good = tmp_path / "good.txt"
    good.write_text(VALID, encoding="utf-8")
    bad = tmp_path / "bad.txt"
    bad.write_text("Fix: 고친다.\n", encoding="utf-8")
    auto = tmp_path / "auto.txt"
    auto.write_text("Merge branch 'x'\n", encoding="utf-8")

    assert run_cli("--path", str(good)).returncode == 0
    assert run_cli("--path", str(auto)).returncode == 0
    failed = run_cli("--path", str(bad), "--format", "json")
    assert failed.returncode == 1
    assert json.loads(failed.stdout)["valid"] is False
    assert run_cli("--path", str(tmp_path / "missing.txt")).returncode == 2


def test_cli_non_utf8_is_io_error(tmp_path: Path) -> None:
    path = tmp_path / "latin.txt"
    path.write_bytes(b"feat: \xff\xfe\n")
    assert run_cli("--path", str(path)).returncode == 2


def test_sources_digest_matches_files() -> None:
    result = checker.check_message(VALID)
    expected = {
        name: hashlib.sha256((ROOT / "scripts" / name).read_bytes()).hexdigest()
        for name in (
            "validate_change_evidence.py",
            "commit_title_rules.py",
            "check_commit_message.py",
        )
    }
    assert result["sources"] == expected
    assert checker.source_digests() == expected


def _padded(total_bytes: int) -> str:
    base = VALID
    pad = total_bytes - len(base.encode("utf-8"))
    assert pad >= 0
    return base + "a" * pad


def test_oversize_message_is_reported_not_validated() -> None:
    limit = checker._validator.MAX_BYTES
    over = checker.check_message(_padded(limit + 1))
    assert over["valid"] is False
    assert over["auto"] is False
    assert "document-too-large" in over["evidence_findings"]
    exact = checker.check_message(_padded(limit))
    assert "document-too-large" not in exact["evidence_findings"]


def test_oversize_auto_looking_message_is_not_auto() -> None:
    limit = checker._validator.MAX_BYTES
    result = checker.check_message("Merge branch 'x'\n" + "a" * limit)
    assert result["auto"] is False
    assert result["valid"] is False
    assert result["evidence_findings"] == ["document-too-large"]


def test_cli_oversize_exit_code(tmp_path: Path) -> None:
    path = tmp_path / "big.txt"
    path.write_text(_padded(checker._validator.MAX_BYTES + 1), encoding="utf-8")
    assert run_cli("--path", str(path)).returncode == 1


def test_cli_unexpected_exception_exits_2(tmp_path: Path, monkeypatch, capsys) -> None:
    path = tmp_path / "m.txt"
    path.write_text(VALID, encoding="utf-8")

    def boom(_message: str):
        raise RuntimeError("boom")

    monkeypatch.setattr(checker, "check_message", boom)
    assert checker.main(["--path", str(path)]) == 2
    assert "check_commit_message: internal error: RuntimeError: boom" in capsys.readouterr().err


def test_cr_only_line_endings_use_the_validator_title_boundary() -> None:
    cr_valid = VALID.replace("\n", "\r")
    ok = checker.check_message(cr_valid)
    assert ok["valid"] is True
    assert ok["title_findings"] == []
    bad = checker.check_message(cr_valid.replace("남긴다\r", "남긴다.\r", 1))
    assert bad["valid"] is False
    assert bad["title_findings"] == ["title-subject-full-stop"]
