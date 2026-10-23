from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "scripts/commit_title_rules.py"
CHECKER = ROOT / "scripts/check_commit_message.py"
VALIDATOR = ROOT / "scripts/validate_change_evidence.py"
DOC = ROOT / "docs/commit-title-rules.md"

SPEC = importlib.util.spec_from_file_location("commit_title_rules", RULES)
assert SPEC and SPEC.loader
rules = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = rules
SPEC.loader.exec_module(rules)

ERROR_CODES = (
    "title-type-enum",
    "title-type-case",
    "title-type-empty",
    "title-subject-empty",
    "title-subject-full-stop",
    "title-header-trim",
)


def codes(message: str) -> set[str]:
    return {finding.code for finding in rules.check_title(message)}


@pytest.mark.parametrize(
    "title",
    [
        "feat(commit): 직접 커밋에도 변경 증거를 남긴다",
        "fix!: 깨진 링크를 고친다",
        "docs(a/b): 문서를 고친다",
    ],
)
def test_valid_titles_pass(title: str) -> None:
    assert rules.check_title(title) == ()


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Fix: 고친다", {"title-type-case", "title-type-enum"}),
        ("feature: 추가한다", {"title-type-enum"}),
        ("fix: ", {"title-subject-empty", "title-header-trim"}),
        ("fix: 고친다.", {"title-subject-full-stop"}),
        ("fix: 계속...", set()),
        ("fix: a..", {"title-subject-full-stop"}),
        (" fix: 고친다", {"title-header-trim", "title-type-empty", "title-subject-empty"}),
        ("고친다", {"title-type-empty", "title-subject-empty"}),
        ("고친다.", {"title-type-empty", "title-subject-empty", "title-subject-full-stop"}),
        ("기능(x): 추가", {"title-type-empty", "title-subject-empty"}),
    ],
)
def test_title_rule_codes(title: str, expected: set[str]) -> None:
    assert codes(title) == expected


def test_only_first_line_is_checked() -> None:
    assert rules.check_title("fix: 고친다\n\nnot a header.\n") == ()


@pytest.mark.parametrize(
    "message",
    [
        "Merge branch 'x'",
        "Merge pull request #1 from a/b",
        'Revert "feat: x"',
        "fixup! feat: x",
        "squash! x",
        "amend! x",
        "chore: 1.2.3",
        "chore(release): v1.2.3",
        "Merge remote-tracking branch 'origin/main'",
        "Automatic merge from x",
        "Auto-merged a into b",
        "1.2.3",
        "v1.2.3",
        "feat: x\n\nMerge branch 'main'",
        "feat: x\rMerge branch 'y'",
    ],
)
def test_auto_messages(message: str) -> None:
    assert rules.is_auto_message(message) is True


@pytest.mark.parametrize(
    "message",
    ["feat: merge branch", "chore: release 1.2", "chore: 1.2", "chore: 01.2.3",
     "=1.2.3", "vv1.2.3", "chore: =1.2.3", "chore: vv1.2.3", "Merged foo\ninto bar"],
)
def test_non_auto_messages(message: str) -> None:
    assert rules.is_auto_message(message) is False


def test_rules_doc_matches_module() -> None:
    doc = DOC.read_text(encoding="utf-8")
    assert "RULES_VERSION" in doc
    assert f'"{rules.RULES_VERSION}"' in doc
    for type_name in rules.TYPES:
        assert f"`{type_name}`" in doc
    for code in ERROR_CODES:
        assert code in doc


def test_rule_files_parse_as_python_310() -> None:
    for path in (RULES, CHECKER, VALIDATOR):
        tree = ast.parse(path.read_text(encoding="utf-8"), feature_version=(3, 10))
        for node in tree.body:
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            for name in names:
                assert name in sys.stdlib_module_names, (path.name, name)
