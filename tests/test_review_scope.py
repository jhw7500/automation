"""Behavioral tests for the trusted canonical-review Git scope boundary."""

from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / ".github" / "actions" / "canonicalize-review" / "review_scope.py"
SPEC = importlib.util.spec_from_file_location("review_scope", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
review_scope = importlib.util.module_from_spec(SPEC)
sys.modules["review_scope"] = review_scope
SPEC.loader.exec_module(review_scope)

SourceAnchor = review_scope.SourceAnchor
TriggerEvidence = review_scope.TriggerEvidence
load_review_scope = review_scope.load_review_scope


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _commit(root: Path, message: str) -> str:
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=Scope Test", "-c", "user.email=scope@example.test", "commit", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _manifest(root: Path, merge_base: str, head: str, files: list[dict[str, str]]) -> Path:
    path = root / "review-scope.json"
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "repository": "example/repo",
                "pr_number": 7,
                "merge_base_sha": merge_base,
                "head_sha": head,
                "files": files,
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return path


def _scope_files(root: Path, left: str, head: str) -> list[dict[str, str]]:
    raw = subprocess.run(
        ["git", "diff", "--name-status", "-z", "--find-renames=50%", f"{left}..{head}"],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    fields = raw[:-1].split(b"\0")
    status_names = {
        "A": "added", "B": "changed", "C": "copied", "D": "removed",
        "M": "modified", "R": "renamed", "T": "changed", "U": "changed", "X": "changed",
    }
    records: list[dict[str, str]] = []
    index = 0
    while index < len(fields):
        status = fields[index].decode("ascii")
        index += 1
        kind = status[0]
        paths = [fields[index].decode("utf-8")]
        index += 1
        if kind in {"C", "R"}:
            paths.append(fields[index].decode("utf-8"))
            index += 1
        record = {"status": status_names[kind], "filename": paths[-1]}
        if len(paths) == 2:
            record["previous_filename"] = paths[0]
        records.append(record)
    return records


@dataclass(frozen=True)
class ScopedRepo:
    root: Path
    manifest: Path
    selected_diff: Path
    merge_base: str
    head: str

    def load(self):
        return load_review_scope(
            self.root, self.manifest, self.selected_diff,
            diff_mode="full", previous_sha="", expected_repository="example/repo",
        )


@dataclass(frozen=True)
class DeltaRepo(ScopedRepo):
    previous_sha: str

    def load(self):
        return load_review_scope(
            self.root, self.manifest, self.selected_diff,
            diff_mode="delta", previous_sha=self.previous_sha, expected_repository="example/repo",
        )


@pytest.fixture
def scoped_repo(tmp_path: Path) -> ScopedRepo:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    (root / "src").mkdir()
    (root / "src" / "runner.py").write_text("def run():\n    value = 1\n", encoding="utf-8")
    merge_base = _commit(root, "base")
    (root / "src" / "runner.py").write_text(
        "def run():\n    value = 1\n    return value + 1\n", encoding="utf-8"
    )
    head = _commit(root, "add return")
    manifest = _manifest(root, merge_base, head, _scope_files(root, merge_base, head))
    selected_diff = root / "review-full.diff"
    selected_diff.write_bytes(
        subprocess.run(
            ["git", "diff", "--no-ext-diff", "--no-textconv", "-U0", f"{merge_base}..{head}", "--", "src/runner.py"],
            cwd=root, check=True, capture_output=True,
        ).stdout
    )
    return ScopedRepo(root, manifest, selected_diff, merge_base, head)


@pytest.fixture
def delta_repo(tmp_path: Path) -> DeltaRepo:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    (root / "src").mkdir()
    (root / "src" / "runner.py").write_text("def run():\n    value = 1\n", encoding="utf-8")
    merge_base = _commit(root, "base")
    (root / "src" / "runner.py").write_text(
        "def run():\n    value = 1\n    return value + 1\n", encoding="utf-8"
    )
    previous_sha = _commit(root, "first review change")
    (root / "src" / "runner.py").write_text(
        "def run():\n    value = 1\n    return value + 1\n    print('done')\n", encoding="utf-8"
    )
    head = _commit(root, "delta review change")
    manifest = _manifest(root, merge_base, head, _scope_files(root, merge_base, head))
    selected_diff = root / "review-delta.diff"
    selected_diff.write_bytes(
        subprocess.run(
            ["git", "diff", "--no-ext-diff", "--no-textconv", "-U0", f"{previous_sha}..{head}"],
            cwd=root, check=True, capture_output=True,
        ).stdout
    )
    return DeltaRepo(root, manifest, selected_diff, merge_base, head, previous_sha)


@pytest.fixture
def scope_attack_repo(tmp_path: Path) -> ScopedRepo:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    (root / "src").mkdir()
    (root / "src" / "runner.py").write_text("def run():\n    value = 1\n", encoding="utf-8")
    merge_base = _commit(root, "base")
    (root / "src" / "runner.py").write_text(
        "def run():\n    value = 1\n    return value + 1\n", encoding="utf-8"
    )
    (root / "target.py").write_text("target.py\n", encoding="utf-8")
    (root / "link.py").symlink_to("target.py")
    (root / "binary.dat").write_bytes(b"\xff\n")
    head = _commit(root, "add hostile entries")
    manifest = _manifest(root, merge_base, head, _scope_files(root, merge_base, head))
    selected_diff = root / "review-full.diff"
    selected_diff.write_bytes(
        subprocess.run(
            ["git", "diff", "--no-ext-diff", "--no-textconv", "-U0", f"{merge_base}..{head}", "--", "src/runner.py"],
            cwd=root, check=True, capture_output=True,
        ).stdout
    )
    return ScopedRepo(root, manifest, selected_diff, merge_base, head)


def test_changed_anchor_requires_exact_manifest_record_and_added_line(scoped_repo: ScopedRepo):
    scope = load_review_scope(
        scoped_repo.root, scoped_repo.manifest, scoped_repo.selected_diff,
        diff_mode="full", previous_sha="", expected_repository="example/repo",
    )
    assert scope.validate_changed_anchor(SourceAnchor("src/runner.py", 3))
    assert not scope.validate_changed_anchor(SourceAnchor("src/runner.py", 2))
    assert not scope.validate_changed_anchor(SourceAnchor("src/missing.py", 3))


def test_delta_anchor_is_not_satisfied_by_an_older_full_range_addition(delta_repo: DeltaRepo):
    scope = load_review_scope(
        delta_repo.root, delta_repo.manifest, delta_repo.selected_diff,
        diff_mode="delta", previous_sha=delta_repo.previous_sha, expected_repository="example/repo",
    )
    assert scope.validate_changed_anchor(SourceAnchor("src/runner.py", 4))
    assert not scope.validate_changed_anchor(SourceAnchor("src/runner.py", 3))
    assert scope.validate_fix_anchor(SourceAnchor("src/runner.py", 4))


def test_trigger_reads_the_tracked_head_blob_not_the_worktree(scoped_repo: ScopedRepo):
    scope = scoped_repo.load()
    (scoped_repo.root / "src" / "runner.py").write_text("WORKTREE SECRET\n", encoding="utf-8")
    assert scope.validate_trigger(TriggerEvidence("src/runner.py", 3, "    return value + 1"))
    assert not scope.validate_trigger(TriggerEvidence("src/runner.py", 3, "WORKTREE SECRET"))


def test_trigger_rejects_symlink_missing_non_utf8_and_quote_mismatch(scope_attack_repo: ScopedRepo):
    scope = scope_attack_repo.load()
    assert not scope.validate_trigger(TriggerEvidence("link.py", 1, "target.py"))
    assert not scope.validate_trigger(TriggerEvidence("missing.py", 1, "x"))
    assert not scope.validate_trigger(TriggerEvidence("binary.dat", 1, "x"))
    assert not scope.validate_trigger(TriggerEvidence("src/runner.py", 3, "return value + 1"))


def test_trigger_rejects_line_one_of_an_empty_tracked_blob(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    (root / "empty.py").write_text("was present\n", encoding="utf-8")
    merge_base = _commit(root, "base")
    (root / "empty.py").write_bytes(b"")
    head = _commit(root, "empty tracked blob")
    manifest = _manifest(root, merge_base, head, _scope_files(root, merge_base, head))
    selected_diff = root / "review-full.diff"
    selected_diff.write_bytes(
        subprocess.run(
            ["git", "diff", "--no-ext-diff", "--no-textconv", "-U0", f"{merge_base}..{head}"],
            cwd=root, check=True, capture_output=True,
        ).stdout
    )
    scope = load_review_scope(
        root, manifest, selected_diff,
        diff_mode="full", previous_sha="", expected_repository="example/repo",
    )
    assert not scope.validate_trigger(TriggerEvidence("empty.py", 1, ""))
