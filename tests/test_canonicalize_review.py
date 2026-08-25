"""Behavioral tests for the review finding canonicalizer's Task 2 contract."""

from __future__ import annotations

import importlib.util
import json
from types import SimpleNamespace
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
ACTION_DIR = ROOT / ".github" / "actions" / "canonicalize-review"
SCOPE_PATH = ACTION_DIR / "review_scope.py"
MODULE_PATH = ACTION_DIR / "canonicalize_review.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


review_scope = _load("review_scope", SCOPE_PATH)
canonicalize_review = _load("canonicalize_review", MODULE_PATH)
CandidateReason = canonicalize_review.CandidateReason
CanonicalizationRequest = canonicalize_review.CanonicalizationRequest
canonicalize = canonicalize_review.canonicalize
stable_finding_id = canonicalize_review.stable_finding_id
SourceAnchor = review_scope.SourceAnchor


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _commit(root: Path, message: str) -> str:
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _scope_files(root: Path, left: str, head: str) -> list[dict[str, str]]:
    raw = subprocess.run(
        ["git", "diff", "--name-status", "-z", "--find-renames=50%", f"{left}..{head}"],
        cwd=root, check=True, capture_output=True,
    ).stdout
    fields = raw[:-1].split(b"\0")
    names = {"A": "added", "B": "changed", "C": "copied", "D": "removed", "M": "modified", "R": "renamed", "T": "changed", "U": "changed", "X": "changed"}
    records: list[dict[str, str]] = []
    index = 0
    while index < len(fields):
        status = fields[index].decode("ascii")
        index += 1
        paths = [fields[index].decode("utf-8")]
        index += 1
        if status[0] in {"C", "R"}:
            paths.append(fields[index].decode("utf-8"))
            index += 1
        record = {"status": names[status[0]], "filename": paths[-1]}
        if len(paths) == 2:
            record["previous_filename"] = paths[0]
        records.append(record)
    return records


class ReviewCase:
    def __init__(self, root: Path, payload: bytes | None):
        root.mkdir()
        self.root = root
        self.candidate = root / "candidate.md"
        if payload is not None:
            self.candidate.write_bytes(payload)
        base = root / "base"
        base.mkdir()
        (base / "review_cases.py").write_text("# base\n", encoding="utf-8")
        _git(base, "init")
        merge_base = _commit(base, "base")
        (base / "review_cases.py").write_text(
            "from pathlib import Path\n\n"
            "def load_profile(path: Path) -> str:\n"
            "    return path.read_text(encoding='utf-8')\n\n"
            "def execute_plan(plan, plan_global=None):\n"
            "    return plan_global\n\n"
            "def call_plan(plan):\n"
            "    return execute_plan(plan, plan_global=None)\n\n"
            "def load_twice(path: Path) -> tuple[str, str]:\n"
            "    first = load_profile(path)\n"
            "    second = load_profile(path)\n"
            "    return first, second\n\n\n"
            "def render_progress(accepted: int, total: int) -> str:\n"
            "    if accepted == 0:\n"
            "        return \"completed 0/{}\".format(total)\n"
            "    return f\"{accepted}/{total}\"\n\n"
            "def classify(value: str) -> int | str:\n"
            "    try:\n"
            "        return int(value)\n"
            "    except ValueError:\n"
            "        return 'invalid'\n"
            "\n"
            "def report(ok):\n"
            "    return 'success' if ok else 'failure'\n\n"
            "def render_verification_status() -> str:\n"
            "    return 'cannot verify'\n",
            encoding="utf-8",
        )
        head = _commit(base, "review change")
        manifest = root / "scope.json"
        manifest.write_text(json.dumps({
            "schema": 1, "repository": "example/repo", "pr_number": 1,
            "merge_base_sha": merge_base, "head_sha": head,
            "files": _scope_files(base, merge_base, head),
        }, separators=(",", ":")), encoding="utf-8")
        selected_diff = root / "selected.diff"
        selected_diff.write_bytes(subprocess.run(
            ["git", "diff", "--no-ext-diff", "--no-textconv", "-U0", f"{merge_base}..{head}"],
            cwd=base, check=True, capture_output=True,
        ).stdout)
        self.canonical = root / "canonical.md"
        self.result = root / "result.json"
        self.request = CanonicalizationRequest(
            reviewer="claude", candidate_file=self.candidate, canonical_file=self.canonical,
            result_file=self.result, scope_manifest=manifest, selected_diff=selected_diff,
            repository_root=base, diff_mode="full", previous_sha="",
            previous_review_file=None, expected_repository="example/repo",
        )

    def run(self) -> tuple[object, str | None]:
        result = canonicalize(self.request)
        return result, self.canonical.read_text(encoding="utf-8") if self.canonical.exists() else None


class ReviewQualityRepo:
    """The fixed three-commit PR #101 corpus used by carryover tests."""

    def __init__(self, root: Path):
        root.mkdir()
        self.root = root
        self.repository = root / "repository"
        self.repository.mkdir()
        self.candidate = root / "candidate.md"
        self.canonical = root / "canonical.md"
        self.result = root / "result.json"
        _git(self.repository, "init")
        self._write_base()
        self.merge_base = _commit(self.repository, "base")
        head_text = self._head_text()
        assert head_text.splitlines()[19] == '        return "completed 0/{}".format(total)'
        assert head_text.splitlines()[24] == "        return int(value)"
        assert head_text.splitlines()[25] == "    except ValueError:"
        (self.repository / "review_cases.py").write_text(head_text, encoding="utf-8")
        (self.repository / "evidence.py").write_text(
            "a < b > c & d\n", encoding="utf-8",
        )
        self.review_head = _commit(self.repository, "review change")
        (self.repository / "review_cases.py").write_text(
            head_text.replace('return "completed 0/{}".format(total)', 'return "rejected"'),
            encoding="utf-8",
        )
        special_path = self.repository / "dir" / "a&b<q>.py"
        special_path.parent.mkdir()
        special_path.write_text("FIXED = True\n", encoding="utf-8")
        self.fixed_head = _commit(self.repository, "render rejected plan")
        self.fixtures = ROOT / "tests" / "fixtures" / "review-finding-quality"

    def _write_base(self) -> None:
        (self.repository / "review_cases.py").write_text(
            "from pathlib import Path\n\n"
            "def load_profile(path: Path) -> str:\n"
            "    return path.read_text(encoding=\"utf-8\")\n\n"
            "def execute_plan(plan, plan_global=None):\n"
            "    return plan_global\n\n"
            "def call_plan(plan):\n"
            "    return execute_plan(plan, plan_global=None)\n\n"
            "def render_progress(accepted: int, total: int) -> str:\n"
            "    return f\"{accepted}/{total}\"\n\n"
            "def classify(value: str) -> int | str:\n"
            "    return \"unknown\"\n",
            encoding="utf-8",
        )

    @staticmethod
    def _head_text() -> str:
        return (
            "from pathlib import Path\n\n"
            "def load_profile(path: Path) -> str:\n"
            "    return path.read_text(encoding=\"utf-8\")\n\n"
            "def execute_plan(plan, plan_global=None):\n"
            "    return plan_global\n\n"
            "def call_plan(plan):\n"
            "    return execute_plan(plan, plan_global=None)\n\n"
            "def load_twice(path: Path) -> tuple[str, str]:\n"
            "    first = load_profile(path)\n"
            "    second = load_profile(path)\n"
            "    return first, second\n\n\n"
            "def render_progress(accepted: int, total: int) -> str:\n"
            "    if accepted == 0:\n"
            "        return \"completed 0/{}\".format(total)\n"
            "    return f\"{accepted}/{total}\"\n\n"
            "def classify(value: str) -> int | str:\n"
            "    try:\n"
            "        return int(value)\n"
            "    except ValueError:\n"
            "        return \"invalid\"\n"
        )

    def _request(
        self, text: str, *, reviewer: str, head: str, diff_mode: str = "full",
        previous_sha: str = "", previous_review: str | None = None,
    ) -> CanonicalizationRequest:
        _git(self.repository, "checkout", "--quiet", head)
        self.candidate.write_text(text, encoding="utf-8")
        manifest = self.root / "scope.json"
        manifest.write_text(json.dumps({
            "schema": 1, "repository": "example/repo", "pr_number": 101,
            "merge_base_sha": self.merge_base, "head_sha": head,
            "files": _scope_files(self.repository, self.merge_base, head),
        }, separators=(",", ":")), encoding="utf-8")
        selected_diff = self.root / "selected.diff"
        left = self.merge_base if diff_mode == "full" else previous_sha
        selected_diff.write_bytes(subprocess.run(
            ["git", "diff", "--no-ext-diff", "--no-textconv", "-U0", f"{left}..{head}"],
            cwd=self.repository, check=True, capture_output=True,
        ).stdout)
        previous_file = None
        if previous_review is not None:
            previous_file = self.root / "previous.md"
            previous_file.write_text(previous_review, encoding="utf-8")
        return CanonicalizationRequest(
            reviewer=reviewer, candidate_file=self.candidate, canonical_file=self.canonical,
            result_file=self.result, scope_manifest=manifest, selected_diff=selected_diff,
            repository_root=self.repository, diff_mode=diff_mode, previous_sha=previous_sha,
            previous_review_file=previous_file, expected_repository="example/repo",
        )

    def _run(self, request: CanonicalizationRequest) -> tuple[object, str]:
        result = canonicalize(request)
        return result, self.canonical.read_text(encoding="utf-8") if self.canonical.exists() else ""

    def run_fixture(self, name: str, reviewer: str = "claude") -> tuple[object, str]:
        return self._run(self._request(
            (self.fixtures / name).read_text(encoding="utf-8"), reviewer=reviewer,
            head=self.review_head,
        ))

    def run_text(self, text: str) -> tuple[object, str]:
        return self._run(self._request(text, reviewer="claude", head=self.review_head))

    def run_delta(self, previous_review: str, candidate: str) -> tuple[object, str]:
        return self._run(self._request(
            candidate, reviewer="gemini", head=self.fixed_head, diff_mode="delta",
            previous_sha=self.review_head, previous_review=previous_review,
        ))

    def run_carryover(self, previous_review: str, candidate: str) -> tuple[object, str]:
        return self._run(self._request(
            candidate, reviewer="gemini", head=self.review_head, previous_sha=self.review_head,
            previous_review=previous_review,
        ))


@pytest.fixture
def case_factory(tmp_path: Path):
    def make(payload: bytes | None) -> ReviewCase:
        return ReviewCase(tmp_path / f"case-{len(list(tmp_path.iterdir()))}", payload)
    return make


@pytest.fixture
def scoped_case(case_factory):
    class ScopedCase:
        def run(self, text: str) -> tuple[object, str | None]:
            return case_factory(text.encode("utf-8")).run()
    return ScopedCase()


@pytest.fixture
def review_quality_repo(tmp_path: Path) -> ReviewQualityRepo:
    return ReviewQualityRepo(tmp_path / "review-quality")


@pytest.mark.parametrize(
    ("payload", "reason"),
    (
        (None, "candidate_missing"),
        (b"\xff", "invalid_utf8"),
        (b"x" * 60001, "candidate_oversize"),
        (b"### New findings\nNone\n\n#### [HIGH] contradictory", "ambiguous_document"),
        (b"### New findings\nNone\n\n### New findings\nNone", "ambiguous_document"),
        (b"### Unknown\nNone", "ambiguous_document"),
        (b"provider failed\n### New findings\nNone", "ambiguous_document"),
        (b"### New findings\nNone\nunreviewed tail", "ambiguous_document"),
        (
            b"### New findings\nNone\n### Still open\nunreviewed tail",
            "ambiguous_document",
        ),
    ),
)
def test_hard_document_failures_are_exact_and_write_no_canonical_body(case_factory, payload, reason):
    """Removing a document-boundary check must fail with its closed reason."""
    result, canonical = case_factory(payload).run()
    assert result.document_valid is False
    assert result.failure_reason == reason
    assert canonical is None
    assert (result.accepted_count, result.filtered_count, result.normalized_count) == (0, 0, 0)
    assert result.filtered_max_severity == "none"
    assert result.candidate_reasons == ()


def test_result_json_never_repeats_rejected_model_text(case_factory):
    """Leaking rejected prose into the result would expose untrusted model text."""
    secret_claim = "INJECTED-REJECTED-CLAIM"
    candidate = f'''### New findings

#### [HIGH] {secret_claim}
- Changed anchor: {{"path":"missing.py","line":9}}
- Trigger evidence: {{"path":"review_cases.py","line":25,"quote":"        return int(value)"}}
- Impact class: runtime
- Material impact: The process records success after rejecting the operation.
'''.encode("utf-8")
    result, _ = case_factory(candidate).run()
    encoded = json.dumps(result.to_dict(), sort_keys=True)
    assert secret_claim not in encoded
    assert result.candidate_reasons == (
        CandidateReason(0, "New findings", "filtered", "invalid_anchor", "HIGH"),
    )
    assert set(result.to_dict()) == {"schema", "document_valid", "accepted_count", "filtered_count", "normalized_count", "filtered_max_severity", "failure_reason", "candidate_reasons"}
    assert set(result.to_dict()["candidate_reasons"][0]) == {"index", "section", "outcome", "reason", "claimed_severity"}


def test_invalid_finding_is_filtered_without_discarding_valid_neighbor(scoped_case):
    """A filter branch must not cause a valid sibling block to disappear."""
    result, canonical = scoped_case.run('''### New findings

#### [HIGH] Missing anchor
- Impact class: runtime
- Material impact: The process records success after rejecting the operation.

#### [MEDIUM] Broad ValueError catch hides invalid configuration
- Changed anchor: {"path":"review_cases.py","line":26}
- Trigger evidence: {"path":"review_cases.py","line":25,"quote":"        return int(value)"}
- Impact class: runtime
- Material impact: An invalid numeric configuration is converted into a normal result.

The caller cannot distinguish invalid input from a supported value.
''')
    assert result.document_valid is True
    assert (result.accepted_count, result.filtered_count, result.normalized_count) == (1, 1, 0)
    assert result.filtered_max_severity == "HIGH"
    assert "Missing anchor" not in canonical
    assert "RVW-61d4cd9ac260 [MEDIUM] Broad ValueError catch" in canonical


@pytest.mark.parametrize(
    ("heading", "fields", "reason"),
    (
        (
            "[HIGH] Style without anchor",
            "- Impact class: style\n- Material impact: cosmetic only.",
            "invalid_anchor",
        ),
        (
            "[LOW] Low category",
            '- Changed anchor: {"path":"review_cases.py","line":26}\n- Trigger evidence: {"path":"review_cases.py","line":25,"quote":"        return int(value)"}\n- Impact class: runtime\n- Material impact: concrete.',
            "non_actionable_category",
        ),
        (
            "[BOGUS] Invalid severity",
            '- Changed anchor: {"path":"review_cases.py","line":26}\n- Trigger evidence: {"path":"review_cases.py","line":25,"quote":"        return int(value)"}\n- Impact class: runtime\n- Material impact: concrete.',
            "invalid_severity",
        ),
        (
            "[HIGH] Bad trigger",
            '- Changed anchor: {"path":"review_cases.py","line":26}\n- Trigger evidence: {"path":"review_cases.py","line":25,"quote":"wrong"}\n- Impact class: runtime\n- Material impact: concrete.',
            "invalid_trigger_evidence",
        ),
        (
            "[HIGH] Bad impact",
            '- Changed anchor: {"path":"review_cases.py","line":26}\n- Trigger evidence: {"path":"review_cases.py","line":25,"quote":"        return int(value)"}\n- Impact class: invented\n- Material impact: concrete.',
            "invalid_impact_class",
        ),
        (
            "[HIGH] Deficit",
            '- Changed anchor: {"path":"review_cases.py","line":26}\n- Trigger evidence: {"path":"review_cases.py","line":25,"quote":"        return int(value)"}\n- Impact class: runtime\n- Material impact: Cannot confirm the impact.',
            "missing_material_impact",
        ),
        (
            "[HIGH] Hidden deficit bullet",
            '- Changed anchor: {"path":"review_cases.py","line":26}\n- Trigger evidence: {"path":"review_cases.py","line":25,"quote":"        return int(value)"}\n- Impact class: runtime\n- Material impact: Invalid input is recorded as a supported value.\n- Caveat: Cannot confirm this path executes.',
            "missing_material_impact",
        ),
        (
            "[MEDIUM] No performance basis",
            '- Changed anchor: {"path":"review_cases.py","line":26}\n- Trigger evidence: {"path":"review_cases.py","line":25,"quote":"        return int(value)"}\n- Impact class: performance\n- Material impact: Each request is expensive.',
            "unsupported_performance_basis",
        ),
    ),
)
def test_soft_filter_reasons_follow_the_closed_validation_order(scoped_case, heading, fields, reason):
    """Changing a soft-policy branch must retain its first deterministic reason."""
    result, canonical = scoped_case.run(f"### New findings\n\n#### {heading}\n{fields}\n")
    assert result.document_valid is True
    assert result.accepted_count == 0
    assert result.filtered_count == 1
    assert result.candidate_reasons[0].reason == reason
    assert canonical == "### New findings\n\nNone\n\nNo validated blocking issues found.\n"


def test_proof_deficit_in_exact_source_quote_does_not_filter_material_impact(
    scoped_case,
):
    result, canonical = scoped_case.run('''### New findings

#### [MEDIUM] Completed requests expose the fallback verification status
- Changed anchor: {"path":"review_cases.py","line":33}
- Trigger evidence: {"path":"review_cases.py","line":33,"quote":"    return 'cannot verify'"}
- Impact class: user-visible
- Material impact: Completed requests return the fallback status to callers instead of their final result.
''')

    assert result.document_valid is True
    assert result.accepted_count == 1
    assert result.filtered_count == 0
    assert "Completed requests expose the fallback verification status" in canonical


def test_clean_declarations_normalize_and_stable_id_is_literal(scoped_case):
    """Changing either clean normalization or identity components must alter observable output."""
    result, canonical = scoped_case.run("No blocking issues found.")
    assert result.document_valid is True
    assert canonical == "### New findings\n\nNone\n\nNo validated blocking issues found.\n"
    assert stable_finding_id("claude", SourceAnchor("review_cases.py", 26), "MEDIUM", " Broad   ValueError CATCH hides invalid configuration ") == "RVW-61d4cd9ac260"


def test_new_findings_discard_model_supplied_ids(scoped_case):
    """Trusting a model-supplied ID would let it impersonate authenticated carryover state."""
    result, canonical = scoped_case.run('''### New findings

#### RVW-deadbeefcafe [MEDIUM] Broad ValueError catch hides invalid configuration
- Changed anchor: {"path":"review_cases.py","line":26}
- Trigger evidence: {"path":"review_cases.py","line":25,"quote":"        return int(value)"}
- Impact class: runtime
- Material impact: Invalid numeric configuration is converted into a normal result.
''')
    assert result.document_valid is True
    assert result.accepted_count == 1
    assert "RVW-deadbeefcafe" not in canonical
    assert "RVW-61d4cd9ac260" in canonical


def test_preexisting_canonical_symlink_is_never_followed(case_factory):
    """Following an output symlink would let untrusted output overwrite another file."""
    candidate = b'''### New findings

#### [MEDIUM] Broad ValueError catch hides invalid configuration
- Changed anchor: {"path":"review_cases.py","line":26}
- Trigger evidence: {"path":"review_cases.py","line":25,"quote":"        return int(value)"}
- Impact class: runtime
- Material impact: Invalid numeric configuration is converted into a normal result.
'''
    case = case_factory(candidate)
    target = case.root / "must-not-change.md"
    target.write_text("preserve", encoding="utf-8")
    case.canonical.symlink_to(target)
    result, canonical = case.run()
    assert result == canonicalize_review.CanonicalizationResult(False, 0, 0, 0, "none", "canonicalizer_error", ())
    assert canonical is None
    assert target.read_text(encoding="utf-8") == "preserve"


@pytest.mark.parametrize(
    ("kind", "basis", "expected_basis"),
    (
        (
            "measured",
            '{"kind":"measured","path":"review_cases.py","line":20,"quote":"        return \\"completed 0/{}\\".format(total)"}',
            '- Performance basis: {"kind":"measured","path":"review_cases.py","line":20,"quote":"        return \\\"completed 0/{}\\\".format(total)"}',
        ),
        (
            "unbounded-amplification",
            '{"kind":"unbounded-amplification","path":"review_cases.py","line":25,"quote":"        return int(value)"}',
            '- Performance basis: {"kind":"unbounded-amplification","path":"review_cases.py","line":25,"quote":"        return int(value)"}',
        ),
    ),
)
def test_performance_basis_is_canonicalized_from_validated_fields(scoped_case, kind, basis, expected_basis):
    """Dropping a validated performance basis would make the canonical finding unverifiable."""
    result, canonical = scoped_case.run(f'''### New findings

#### [MEDIUM] Measured performance regression
- Changed anchor: {{"path":"review_cases.py","line":26}}
- Trigger evidence: {{"path":"review_cases.py","line":25,"quote":"        return int(value)"}}
- Impact class: performance
- Material impact: The changed exception path is exercised for every invalid request.
- Performance basis: {basis}
''')
    assert result == canonicalize_review.CanonicalizationResult(True, 1, 0, 0, "none", "", ())
    assert expected_basis in canonical
    assert canonical == f'''### New findings

#### RVW-ce6c91192e93 [MEDIUM] Measured performance regression
- Changed anchor: {{"path":"review_cases.py","line":26}}
- Trigger evidence: {{"path":"review_cases.py","line":25,"quote":"        return int(value)"}}
- Impact class: performance
- Material impact: The changed exception path is exercised for every invalid request.
{expected_basis}
'''


def test_deep_anchor_json_is_filtered_without_discarding_valid_neighbor(scoped_case):
    """A JSON recursion limit must be a candidate filter, never a document-wide failure."""
    nested = "[" * 1_500 + "]" * 1_500
    result, canonical = scoped_case.run(f'''### New findings

#### [HIGH] Deep malformed anchor
- Changed anchor: {nested}
- Trigger evidence: {{"path":"review_cases.py","line":25,"quote":"        return int(value)"}}
- Impact class: runtime
- Material impact: This must not escape candidate validation.

#### [MEDIUM] Broad ValueError catch hides invalid configuration
- Changed anchor: {{"path":"review_cases.py","line":26}}
- Trigger evidence: {{"path":"review_cases.py","line":25,"quote":"        return int(value)"}}
- Impact class: runtime
- Material impact: An invalid numeric configuration is converted into a normal result.
''')
    assert result.document_valid is True
    assert (result.accepted_count, result.filtered_count) == (1, 1)
    assert result.candidate_reasons == (
        CandidateReason(0, "New findings", "filtered", "invalid_anchor", "HIGH"),
    )
    assert "Deep malformed anchor" not in canonical
    assert "RVW-61d4cd9ac260" in canonical


def test_candidate_read_uses_a_bounded_os_read_after_a_size_race(case_factory, monkeypatch):
    """Replacing bounded descriptor reads with Path.read_bytes would allocate an unbounded candidate."""
    case = case_factory(b"x" * (canonicalize_review.MAX_CANDIDATE_BYTES + 1))
    original_read = canonicalize_review.os.read
    original_fstat = canonicalize_review.os.fstat
    read_sizes: list[int] = []
    monkeypatch.setattr(
        canonicalize_review.os, "fstat",
        lambda descriptor: SimpleNamespace(st_size=1, st_mode=original_fstat(descriptor).st_mode),
    )

    def bounded_read(descriptor: int, size: int) -> bytes:
        read_sizes.append(size)
        return original_read(descriptor, size)

    monkeypatch.setattr(canonicalize_review.os, "read", bounded_read)
    result, canonical = case.run()
    assert result.failure_reason == "candidate_oversize"
    assert canonical is None
    assert read_sizes and max(read_sizes) <= canonicalize_review.MAX_CANDIDATE_BYTES + 1


def test_candidate_short_read_does_not_hide_remaining_oversize_bytes(case_factory, monkeypatch):
    """A one-shot short read must not turn an oversized candidate prefix into a clean result."""
    clean_prefix = b"No blocking issues found."
    payload = clean_prefix + b"x" * (canonicalize_review.MAX_CANDIDATE_BYTES + 1 - len(clean_prefix))
    case = case_factory(payload)
    original_read = canonicalize_review.os.read
    original_fstat = canonicalize_review.os.fstat
    requested: list[int] = []
    retained: list[int] = []
    monkeypatch.setattr(
        canonicalize_review.os, "fstat",
        lambda descriptor: SimpleNamespace(
            st_size=len(clean_prefix), st_mode=original_fstat(descriptor).st_mode,
        ),
    )

    def short_then_remaining(descriptor: int, size: int) -> bytes:
        requested.append(size)
        if len(requested) == 1:
            chunk = original_read(descriptor, len(clean_prefix))
        else:
            chunk = original_read(descriptor, size)
        retained.append(len(chunk))
        return chunk

    monkeypatch.setattr(canonicalize_review.os, "read", short_then_remaining)
    result, canonical = case.run()
    assert result.failure_reason == "candidate_oversize"
    assert canonical is None
    assert len(requested) == 2
    assert max(requested) <= canonicalize_review.MAX_CANDIDATE_BYTES + 1
    assert sum(retained) <= canonicalize_review.MAX_CANDIDATE_BYTES + 1


def test_candidate_symlink_is_not_followed(case_factory):
    """A candidate symlink must not become a privileged input alias."""
    case = case_factory(None)
    target = case.root / "model-output.md"
    target.write_text("No blocking issues found.", encoding="utf-8")
    case.candidate.symlink_to(target)
    result, canonical = case.run()
    assert result.failure_reason == "candidate_missing"
    assert canonical is None


def test_clean_canonical_output_round_trips_with_summary_prose(case_factory):
    """Canonical clean output must remain a valid clean declaration when fed back as input."""
    first, canonical = case_factory(b"No blocking issues found.").run()
    second, round_trip = case_factory(canonical.encode("utf-8")).run()
    assert first.document_valid is True
    assert second.document_valid is True
    assert round_trip == canonical


def _cli_args(case: ReviewCase, github_output: Path | None = None) -> list[str]:
    args = [
        sys.executable, str(MODULE_PATH), "--reviewer", "claude",
        "--candidate-file", str(case.candidate), "--canonical-file", str(case.canonical),
        "--result-file", str(case.result), "--scope-manifest", str(case.request.scope_manifest),
        "--selected-diff", str(case.request.selected_diff), "--repository-root", str(case.request.repository_root),
        "--diff-mode", "full", "--expected-repository", "example/repo",
    ]
    if github_output is not None:
        args.extend(("--github-output", str(github_output)))
    return args


def test_cli_writes_bounded_schema_result_scalar_outputs_and_metadata_only_logs(case_factory):
    """CLI output must be consumable without leaking model prose into logs or result JSON."""
    secret = "CLI-REJECTED-SECRET"
    case = case_factory(f'''### New findings

#### [HIGH] {secret}
- Changed anchor: {{"path":"missing.py","line":9}}
- Trigger evidence: {{"path":"review_cases.py","line":25,"quote":"        return int(value)"}}
- Impact class: runtime
- Material impact: Invalid state is reported as success.
'''.encode("utf-8"))
    github_output = case.root / "github-output.txt"
    completed = subprocess.run(_cli_args(case, github_output), cwd=ACTION_DIR, capture_output=True, text=True)
    assert completed.returncode == 0
    result = json.loads(case.result.read_text(encoding="utf-8"))
    assert set(result) == {"schema", "document_valid", "accepted_count", "filtered_count", "normalized_count", "filtered_max_severity", "failure_reason", "candidate_reasons"}
    assert len(case.result.read_bytes()) <= 131_072
    assert secret not in case.result.read_text(encoding="utf-8")
    assert secret not in completed.stdout
    assert completed.stdout == "review-canonicalization: document_valid=true accepted=0 filtered=1 normalized=0 filtered_max=HIGH failure_reason=\ncandidate[0]: section=New findings outcome=filtered reason=invalid_anchor claimed_severity=HIGH\n"
    assert github_output.read_text(encoding="utf-8") == "document_valid=true\naccepted_count=0\nfiltered_count=1\nnormalized_count=0\nfiltered_max_severity=HIGH\nfailure_reason=\n"
    assert case.canonical.read_text(encoding="utf-8") == "### New findings\n\nNone\n\nNo validated blocking issues found.\n"


def test_cli_logs_only_a_fixed_ambiguity_code_for_untrusted_preamble(case_factory):
    secret = "UNTRUSTED-PREAMBLE-SECRET"
    case = case_factory(
        f"{secret}\n### New findings\n\nNone\n".encode("utf-8")
    )

    completed = subprocess.run(
        _cli_args(case), cwd=ACTION_DIR, capture_output=True, text=True
    )

    assert completed.returncode == 0
    assert secret not in completed.stdout
    assert secret not in completed.stderr
    assert completed.stderr == "review-canonicalization-diagnostic: preamble\n"
    assert json.loads(case.result.read_text(encoding="utf-8"))["failure_reason"] == (
        "ambiguous_document"
    )


@pytest.mark.parametrize(
    ("candidate", "diagnostic"),
    (
        (
            "### UNTRUSTED-SECTION-SECRET\n### New findings\n\nNone\n",
            "unknown_section_before_document",
        ),
        (
            "### New findings\n\nNone\n### UNTRUSTED-SECTION-SECRET\n",
            "unknown_section_after_document",
        ),
    ),
)
def test_cli_classifies_unknown_section_position_without_logging_its_heading(
    case_factory, candidate, diagnostic
):
    case = case_factory(candidate.encode("utf-8"))

    completed = subprocess.run(
        _cli_args(case), cwd=ACTION_DIR, capture_output=True, text=True
    )

    assert completed.returncode == 0
    assert "UNTRUSTED-SECTION-SECRET" not in completed.stdout
    assert "UNTRUSTED-SECTION-SECRET" not in completed.stderr
    assert completed.stderr == f"review-canonicalization-diagnostic: {diagnostic}\n"
    assert json.loads(case.result.read_text(encoding="utf-8"))["failure_reason"] == (
        "ambiguous_document"
    )


def test_cli_action_first_round_empty_previous_review_file_writes_runner_outputs(case_factory):
    """The composite action's empty optional value denotes no previous review."""
    case = case_factory(accepted_rejected_plan_review().encode("utf-8"))
    github_output = case.root / "github-output.txt"
    command = _cli_args(case, github_output)
    command.extend(("--previous-review-file", ""))

    completed = subprocess.run(command, cwd=ACTION_DIR, capture_output=True, text=True)

    assert completed.returncode == 0, completed.stderr
    assert github_output.read_text(encoding="utf-8") == (
        "document_valid=true\n"
        "accepted_count=1\n"
        "filtered_count=0\n"
        "normalized_count=0\n"
        "filtered_max_severity=none\n"
        "failure_reason=\n"
    )
    assert case.canonical.is_file() and not case.canonical.is_symlink()
    assert case.result.is_file() and not case.result.is_symlink()
    assert case.canonical.is_relative_to(case.root)
    assert case.result.is_relative_to(case.root)


@pytest.mark.parametrize("destination", ("result", "github"))
def test_cli_refuses_symlink_result_and_github_output_destinations(case_factory, destination):
    """Atomic CLI output failures must be nonzero and never follow a symlink."""
    case = case_factory(b"No blocking issues found.")
    target = case.root / f"{destination}-target.txt"
    target.write_text("preserve", encoding="utf-8")
    if destination == "result":
        case.result.symlink_to(target)
        command = _cli_args(case)
    else:
        github_output = case.root / "github-output.txt"
        github_output.symlink_to(target)
        command = _cli_args(case, github_output)
    completed = subprocess.run(command, cwd=ACTION_DIR, capture_output=True, text=True)
    assert completed.returncode != 0
    assert target.read_text(encoding="utf-8") == "preserve"


def test_cli_canonical_symlink_becomes_closed_canonicalizer_error(case_factory):
    """Canonical write refusal is a valid failure result rather than a partial output success."""
    case = case_factory(b"No blocking issues found.")
    target = case.root / "canonical-target.md"
    target.write_text("preserve", encoding="utf-8")
    case.canonical.symlink_to(target)
    completed = subprocess.run(_cli_args(case), cwd=ACTION_DIR, capture_output=True, text=True)
    assert completed.returncode == 0
    assert json.loads(case.result.read_text(encoding="utf-8")) == {
        "schema": 1, "document_valid": False, "accepted_count": 0,
        "filtered_count": 0, "normalized_count": 0, "filtered_max_severity": "none",
        "failure_reason": "canonicalizer_error", "candidate_reasons": [],
    }
    assert not case.canonical.exists()
    assert target.read_text(encoding="utf-8") == "preserve"


def test_parser_rejects_duplicate_json_keys_and_too_many_blocks(scoped_case):
    """Removing JSON uniqueness or block caps would admit ambiguous model input."""
    duplicate = '''### New findings

#### [HIGH] Duplicate JSON
- Changed anchor: {"path":"review_cases.py","path":"missing.py","line":26}
- Trigger evidence: {"path":"review_cases.py","line":25,"quote":"        return int(value)"}
- Impact class: runtime
- Material impact: concrete.
'''
    result, _ = scoped_case.run(duplicate)
    assert result.candidate_reasons[0].reason == "invalid_anchor"
    massive = "### New findings\n\n" + "\n\n".join("#### [HIGH] x" for _ in range(513))
    result, canonical = scoped_case.run(massive)
    assert result.failure_reason == "ambiguous_document"
    assert canonical is None


def accepted_rejected_plan_review() -> str:
    return '''### New findings

#### RVW-3253866a28c6 [HIGH] Rejected plan is rendered as successful completion
- Changed anchor: {"path":"review_cases.py","line":20}
- Trigger evidence: {"path":"review_cases.py","line":20,"quote":"        return \\"completed 0/{}\\".format(total)"}
- Impact class: user-visible
- Material impact: A rejected plan is displayed as a successful zero-item completion.
'''


def first_round_false_resolved() -> str:
    return '''### New findings
None

### Resolved
#### RVW-3253866a28c6 [HIGH] Rejected plan is rendered as successful completion
- Fix anchor: {"path":"review_cases.py","line":20}
- Resolution: The rejection is now explicit.
'''


def resolved_rejected_plan_candidate() -> str:
    return first_round_false_resolved()


def duplicate_prior_binding_candidate() -> str:
    return '''### New findings
None

### Still open
#### RVW-3253866a28c6 [HIGH] Rejected plan is rendered as successful completion
- Changed anchor: {"path":"review_cases.py","line":20}
- Trigger evidence: {"path":"review_cases.py","line":20,"quote":"        return \\"rejected\\""}
- Impact class: user-visible
- Material impact: The rejected plan still appears successful.

### Resolved
#### RVW-3253866a28c6 [HIGH] Rejected plan is rendered as successful completion
- Fix anchor: {"path":"review_cases.py","line":20}
- Resolution: The rejection is now explicit.
'''


def retracted_rejected_plan_candidate() -> str:
    return '''### New findings
None

### Retracted
#### RVW-3253866a28c6 [HIGH] Rejected plan is rendered as successful completion
- Trigger evidence: {"path":"review_cases.py","line":20,"quote":"        return \\"completed 0/{}\\".format(total)"}
- Reason: The rendered result is intentional product behavior.
'''


@pytest.mark.parametrize(
    ("fixture", "reason", "accepted"),
    (
        ("plan-global.md", "invalid_trigger_evidence", 0),
        ("duplicate-yaml.md", "unsupported_performance_basis", 0),
        ("value-error.md", "", 1),
        ("rejected-plan.md", "", 1),
    ),
)
def test_pr101_quality_corpus(review_quality_repo, fixture, reason, accepted):
    result, canonical = review_quality_repo.run_fixture(fixture)
    assert result.accepted_count == accepted
    assert result.document_valid is True
    assert [item.reason for item in result.candidate_reasons] == ([reason] if reason else [])
    assert ("No validated blocking issues found." in canonical) is (accepted == 0)


def test_corpus_stable_ids_are_literal(review_quality_repo):
    assert "RVW-61d4cd9ac260" in review_quality_repo.run_fixture("value-error.md")[1]
    assert "RVW-3253866a28c6" in review_quality_repo.run_fixture("rejected-plan.md", reviewer="gemini")[1]


def test_duplicate_generated_new_id_is_normalized_before_rendering(review_quality_repo):
    candidate = (review_quality_repo.fixtures / "value-error.md").read_text(encoding="utf-8")
    block = candidate.split("### New findings\n\n", 1)[1].strip()
    result, canonical = review_quality_repo.run_text(
        f"### New findings\n\n{block}\n\n{block}\n"
    )

    assert result == canonicalize_review.CanonicalizationResult(
        True, 1, 0, 1, "none", "",
        (canonicalize_review.CandidateReason(
            1, "New findings", "normalized", "duplicate_prior_binding", "MEDIUM",
        ),),
    )
    assert canonical.count("#### RVW-61d4cd9ac260 [MEDIUM]") == 1
    followup, _ = review_quality_repo._run(review_quality_repo._request(
        "### New findings\n\nNone\n", reviewer="claude",
        head=review_quality_repo.review_head, previous_sha=review_quality_repo.review_head,
        previous_review=canonical,
    ))
    assert followup.document_valid is True


def test_new_finding_cannot_duplicate_a_carried_active_id(review_quality_repo):
    candidate = '''### New findings

#### [HIGH] Rejected plan is rendered as successful completion
- Changed anchor: {"path":"review_cases.py","line":20}
- Trigger evidence: {"path":"review_cases.py","line":20,"quote":"        return \\"rejected\\""}
- Impact class: user-visible
- Material impact: A rejected plan is displayed as a successful zero-item completion.

### Still open
#### RVW-3253866a28c6 [HIGH] ignored candidate title
- Changed anchor: {"path":"review_cases.py","line":20}
- Trigger evidence: {"path":"review_cases.py","line":20,"quote":"        return \\"rejected\\""}
- Impact class: user-visible
- Material impact: The rejected plan still affects completion messaging.
'''
    result, canonical = review_quality_repo.run_delta(
        accepted_rejected_plan_review(), candidate,
    )

    assert result == canonicalize_review.CanonicalizationResult(
        True, 1, 0, 1, "none", "",
        (canonicalize_review.CandidateReason(
            0, "New findings", "normalized", "duplicate_prior_binding", "HIGH",
        ),),
    )
    assert canonical.count("#### RVW-3253866a28c6 [HIGH]") == 1
    assert "### Still open" in canonical


def test_first_round_resolved_is_normalized_not_a_hard_failure(review_quality_repo):
    result, canonical = review_quality_repo.run_text(first_round_false_resolved())
    assert result.document_valid is True
    assert result.normalized_count == 1
    assert result.candidate_reasons[0].reason == "unknown_prior_id"
    assert "### Resolved" not in canonical
    assert canonical == "### New findings\n\nNone\n\nNo validated blocking issues found.\n"


def test_known_prior_resolution_requires_a_delta_fix_anchor(review_quality_repo):
    result, canonical = review_quality_repo.run_delta(
        previous_review=accepted_rejected_plan_review(),
        candidate=resolved_rejected_plan_candidate(),
    )
    assert result.document_valid is True
    assert result.normalized_count == 0
    assert "### Resolved" in canonical
    assert "RVW-3253866a28c6 [HIGH] Rejected plan" in canonical
    assert canonical == '''### New findings

None

### Resolved

#### RVW-3253866a28c6 [HIGH] Rejected plan is rendered as successful completion
- Fix anchor: {"path":"review_cases.py","line":20}
- Resolution: The rejection is now explicit.
'''


def test_one_prior_id_cannot_bind_twice(review_quality_repo):
    result, canonical = review_quality_repo.run_delta(
        previous_review=accepted_rejected_plan_review(),
        candidate=duplicate_prior_binding_candidate(),
    )
    assert result.normalized_count == 2
    assert {item.reason for item in result.candidate_reasons} == {"duplicate_prior_binding"}
    assert "### Still open" not in canonical
    assert "### Resolved" not in canonical
    assert canonical == "### New findings\n\nNone\n\nNo validated blocking issues found.\n"


def test_retracted_prior_renders_canonical_reason(review_quality_repo):
    result, canonical = review_quality_repo.run_carryover(
        accepted_rejected_plan_review(), retracted_rejected_plan_candidate(),
    )
    assert result == canonicalize_review.CanonicalizationResult(True, 0, 0, 0, "none", "", ())
    assert canonical == '''### New findings

None

### Retracted

#### RVW-3253866a28c6 [HIGH] Rejected plan is rendered as successful completion
- Trigger evidence: {"path":"review_cases.py","line":20,"quote":"        return \\"completed 0/{}\\".format(total)"}
- Reason: The rendered result is intentional product behavior.
'''


def test_malformed_previous_canonical_file_is_scope_invalid(review_quality_repo):
    result, canonical = review_quality_repo.run_carryover(
        "### New findings\n\n#### [HIGH] unauthenticated heading\n", "### New findings\nNone\n",
    )
    assert result.document_valid is False
    assert result.failure_reason == "scope_invalid"
    assert canonical == ""


def valid_still_open_candidate() -> str:
    return '''### New findings
None

### Still open
#### RVW-3253866a28c6 [MEDIUM] ignored candidate title
- Changed anchor: {"path":"review_cases.py","line":20}
- Trigger evidence: {"path":"review_cases.py","line":20,"quote":"        return \\"rejected\\""}
- Impact class: user-visible
- Material impact: The rejected plan still affects completion messaging.
'''


def test_known_prior_still_open_renders_prior_identity_and_exact_markdown(review_quality_repo):
    result, canonical = review_quality_repo.run_delta(
        accepted_rejected_plan_review(), valid_still_open_candidate(),
    )
    assert result == canonicalize_review.CanonicalizationResult(True, 1, 0, 0, "none", "", ())
    assert canonical == '''### New findings

None

### Still open

#### RVW-3253866a28c6 [HIGH] Rejected plan is rendered as successful completion
- Changed anchor: {"path":"review_cases.py","line":20}
- Trigger evidence: {"path":"review_cases.py","line":20,"quote":"        return \\"rejected\\""}
- Impact class: user-visible
- Material impact: The rejected plan still affects completion messaging.
'''


@pytest.mark.parametrize(
    ("replacement", "replaced", "reason"),
    (
        ('return \\"rejected\\"', 'return \\"wrong\\"', "invalid_trigger_evidence"),
        ("The rejected plan still affects completion messaging.", "", "missing_material_impact"),
    ),
)
def test_invalid_still_open_is_filtered_with_prior_severity(review_quality_repo, replacement, replaced, reason):
    candidate = valid_still_open_candidate().replace(replacement, replaced)
    result, canonical = review_quality_repo.run_delta(accepted_rejected_plan_review(), candidate)
    assert result.accepted_count == 0
    assert result.filtered_count == 1
    assert result.normalized_count == 0
    assert result.filtered_max_severity == "HIGH"
    assert result.candidate_reasons == (
        CandidateReason(0, "Still open", "filtered", reason, "MEDIUM"),
    )
    assert canonical == "### New findings\n\nNone\n\nNo validated blocking issues found.\n"


@pytest.mark.parametrize(
    "candidate",
    (
        '''### New findings
None

### Resolved
#### RVW-3253866a28c6 [HIGH] Rejected plan is rendered as successful completion
- Resolution: explicit now.
''',
        '''### New findings
None

### Resolved
#### RVW-3253866a28c6 [HIGH] Rejected plan is rendered as successful completion
- Fix anchor: {"path":"review_cases.py","line":20}
- Resolution:\x20
''',
    ),
)
def test_invalid_resolved_normalizes_with_closed_reason(review_quality_repo, candidate):
    result, canonical = review_quality_repo.run_delta(accepted_rejected_plan_review(), candidate)
    assert result.normalized_count == 1
    assert result.candidate_reasons == (
        CandidateReason(0, "Resolved", "normalized", "missing_fix_anchor", "HIGH"),
    )
    assert canonical == "### New findings\n\nNone\n\nNo validated blocking issues found.\n"


@pytest.mark.parametrize(
    "candidate",
    (
        '''### New findings
None

### Retracted
#### RVW-3253866a28c6 [HIGH] Rejected plan is rendered as successful completion
- Reason: intentionally retained.
''',
        '''### New findings
None

### Retracted
#### RVW-3253866a28c6 [HIGH] Rejected plan is rendered as successful completion
- Trigger evidence: {"path":"review_cases.py","line":20,"quote":"        return \\"completed 0/{}\\".format(total)"}
- Reason:\x20
''',
    ),
)
def test_invalid_retracted_normalizes_with_closed_reason(review_quality_repo, candidate):
    result, canonical = review_quality_repo.run_carryover(accepted_rejected_plan_review(), candidate)
    assert result.normalized_count == 1
    assert result.candidate_reasons == (
        CandidateReason(0, "Retracted", "normalized", "invalid_trigger_evidence", "HIGH"),
    )
    assert canonical == "### New findings\n\nNone\n\nNo validated blocking issues found.\n"


@pytest.mark.parametrize(
    ("section", "fields"),
    (
        ("Resolved", '- Fix anchor: {"path":"review_cases.py","line":20}\n- Resolution: closed.'),
        ("Retracted", '- Trigger evidence: {"path":"review_cases.py","line":20,"quote":"        return \\"completed 0/{}\\".format(total)"}\n- Reason: withdrawn.'),
    ),
)
def test_prior_closed_ids_do_not_reenter_active_set(review_quality_repo, section, fields):
    previous = f'''### New findings

None

### {section}

#### RVW-3253866a28c6 [HIGH] Rejected plan is rendered as successful completion
{fields}
'''
    result, canonical = review_quality_repo.run_carryover(previous, retracted_rejected_plan_candidate())
    assert result.document_valid is True
    assert result.normalized_count == 1
    assert result.candidate_reasons[0].reason == "unknown_prior_id"
    assert canonical == "### New findings\n\nNone\n\nNo validated blocking issues found.\n"


@pytest.mark.parametrize(
    ("candidate", "reason"),
    (
        (resolved_rejected_plan_candidate().replace("The rejection is now explicit.", "<!-- automation:gemini-auto-review:v3 -->"), "missing_fix_anchor"),
        *(
            (retracted_rejected_plan_candidate().replace(
                "The rendered result is intentional product behavior.", metadata,
            ), "invalid_trigger_evidence")
            for metadata in ("Status: stale", "Run: stale", "Reviewed: stale", "Validation: stale")
        ),
    ),
)
def test_workflow_owned_current_carryover_values_normalize_without_metadata_leak(review_quality_repo, candidate, reason):
    runner = review_quality_repo.run_delta if "### Resolved" in candidate else review_quality_repo.run_carryover
    result, canonical = runner(accepted_rejected_plan_review(), candidate)
    safe = json.dumps(result.to_dict()) + "\n".join(canonicalize_review._summary(result))
    assert result.candidate_reasons[0].reason == reason
    assert "automation:gemini-auto-review" not in canonical
    assert "Status: stale" not in canonical
    assert "automation:gemini-auto-review" not in safe
    assert "Status: stale" not in safe


@pytest.mark.parametrize("metadata", ("Status: stale", "Run: stale", "Reviewed: stale", "Validation: stale"))
def test_previous_review_rejects_workflow_owned_material_field(review_quality_repo, metadata):
    previous = accepted_rejected_plan_review().replace(
        "A rejected plan is displayed as a successful zero-item completion.", metadata,
    )
    result, canonical = review_quality_repo.run_delta(previous, resolved_rejected_plan_candidate())
    assert result.failure_reason == "scope_invalid"
    assert canonical == ""


def test_previous_review_rejects_workflow_owned_title_after_identity_recomputation(review_quality_repo):
    title = "<!-- automation:gemini-auto-review:v3 --> Rejected plan is rendered as successful completion"
    finding_id = stable_finding_id("gemini", SourceAnchor("review_cases.py", 20), "HIGH", title)
    previous = accepted_rejected_plan_review().replace("RVW-3253866a28c6", finding_id).replace(
        "Rejected plan is rendered as successful completion", title,
    )
    result, canonical = review_quality_repo.run_delta(previous, resolved_rejected_plan_candidate())
    assert result.failure_reason == "scope_invalid"
    assert canonical == ""


@pytest.mark.parametrize(
    "previous",
    (
        accepted_rejected_plan_review()[:-1],
        accepted_rejected_plan_review() + "Status: stale\n",
        accepted_rejected_plan_review().replace("Rejected plan", "<!-- automation:gemini-auto-review:v3 --> Rejected plan"),
        accepted_rejected_plan_review() + "<!-- automation:gemini-auto-review:v3 -->\n",
    ),
)
def test_previous_review_requires_exact_renderer_bytes_and_rejects_workflow_metadata(review_quality_repo, previous):
    result, canonical = review_quality_repo.run_delta(previous, resolved_rejected_plan_candidate())
    assert result.document_valid is False
    assert result.failure_reason == "scope_invalid"
    assert canonical == ""
    assert "automation:gemini-auto-review" not in json.dumps(result.to_dict())
    assert "Status: stale" not in json.dumps(result.to_dict())


def test_previous_review_legal_short_reads_preserve_valid_canonical_input(review_quality_repo, monkeypatch):
    original_read = canonicalize_review.os.read
    requested: list[int] = []

    def one_byte_reads(descriptor: int, size: int) -> bytes:
        requested.append(size)
        return original_read(descriptor, min(size, 1))

    monkeypatch.setattr(canonicalize_review.os, "read", one_byte_reads)
    result, canonical = review_quality_repo.run_delta(
        accepted_rejected_plan_review(), resolved_rejected_plan_candidate(),
    )
    assert result.document_valid is True
    assert "### Resolved" in canonical
    assert requested and max(requested) <= canonicalize_review.MAX_PREVIOUS_CANONICAL_BYTES + 1


def test_previous_reader_accepts_exact_canonical_ceiling_across_short_reads(tmp_path, monkeypatch):
    assert canonicalize_review.MAX_PREVIOUS_CANONICAL_BYTES == 65_536
    payload = b"x" * canonicalize_review.MAX_PREVIOUS_CANONICAL_BYTES
    previous = tmp_path / "previous.md"
    previous.write_bytes(payload)
    original_read = canonicalize_review.os.read
    requested: list[int] = []
    retained: list[int] = []

    def short_reads(descriptor: int, size: int) -> bytes:
        requested.append(size)
        chunk = original_read(descriptor, min(size, 4_096))
        retained.append(len(chunk))
        return chunk

    monkeypatch.setattr(canonicalize_review.os, "read", short_reads)
    assert canonicalize_review._read_previous(previous).encode("utf-8") == payload
    assert max(requested) <= canonicalize_review.MAX_PREVIOUS_CANONICAL_BYTES + 1
    assert sum(retained) == canonicalize_review.MAX_PREVIOUS_CANONICAL_BYTES


def test_previous_reader_rejects_one_byte_over_ceiling_after_size_race(tmp_path, monkeypatch):
    assert canonicalize_review.MAX_PREVIOUS_CANONICAL_BYTES == 65_536
    payload = b"x" * (canonicalize_review.MAX_PREVIOUS_CANONICAL_BYTES + 1)
    previous = tmp_path / "previous.md"
    previous.write_bytes(payload)
    original_fstat = canonicalize_review.os.fstat
    original_read = canonicalize_review.os.read
    requested: list[int] = []
    retained: list[int] = []
    monkeypatch.setattr(
        canonicalize_review.os,
        "fstat",
        lambda descriptor: SimpleNamespace(
            st_size=1, st_mode=original_fstat(descriptor).st_mode,
        ),
    )

    def short_then_remaining(descriptor: int, size: int) -> bytes:
        requested.append(size)
        chunk = original_read(descriptor, 1 if len(requested) == 1 else size)
        retained.append(len(chunk))
        return chunk

    monkeypatch.setattr(canonicalize_review.os, "read", short_then_remaining)
    with pytest.raises(review_scope.ScopeValidationError):
        canonicalize_review._read_previous(previous)
    assert len(requested) == 2
    assert max(requested) <= canonicalize_review.MAX_PREVIOUS_CANONICAL_BYTES + 1
    assert sum(retained) == canonicalize_review.MAX_PREVIOUS_CANONICAL_BYTES + 1


def test_max_candidate_renderer_output_is_valid_authenticated_previous_input(review_quality_repo):
    assert canonicalize_review.MAX_CANDIDATE_BYTES == 60_000
    assert canonicalize_review.MAX_PREVIOUS_CANONICAL_BYTES == 65_536
    blocks = []
    for index in range(200):
        blocks.append(f'''#### [HIGH] Finding {index:03d}
- Changed anchor: {{"path":"review_cases.py","line":26}}
- Trigger evidence: {{"path":"review_cases.py","line":25,"quote":"        return int(value)"}}
- Impact class: runtime
- Material impact: Concrete runtime impact {"PADDING" if index == 0 else "confirmed"}.
''')
    candidate = "### New findings\n\n" + "\n".join(blocks)
    padding = canonicalize_review.MAX_CANDIDATE_BYTES - len(candidate.encode("utf-8"))
    assert padding > 0
    candidate = candidate.replace("PADDING", "x" * (len("PADDING") + padding), 1)
    candidate_bytes = candidate.encode("utf-8")
    assert len(candidate_bytes) == canonicalize_review.MAX_CANDIDATE_BYTES

    initial, previous = review_quality_repo._run(review_quality_repo._request(
        candidate, reviewer="gemini", head=review_quality_repo.review_head,
    ))
    previous_bytes = previous.encode("utf-8")
    assert initial == canonicalize_review.CanonicalizationResult(True, 200, 0, 0, "none", "", ())
    assert len(previous_bytes) == canonicalize_review.MAX_CANDIDATE_BYTES + (17 * 200)
    assert (
        canonicalize_review.MAX_CANDIDATE_BYTES
        < len(previous_bytes)
        <= canonicalize_review.MAX_PREVIOUS_CANONICAL_BYTES
    )

    request = review_quality_repo._request(
        "### New findings\n\nNone\n", reviewer="gemini", head=review_quality_repo.review_head,
        previous_sha=review_quality_repo.review_head, previous_review=previous,
    )
    assert request.previous_review_file is not None
    assert request.previous_review_file.read_bytes() == previous_bytes
    followup, canonical = review_quality_repo._run(request)
    assert followup == canonicalize_review.CanonicalizationResult(True, 0, 0, 0, "none", "", ())
    assert canonical == "### New findings\n\nNone\n\nNo validated blocking issues found.\n"


def test_candidate_reason_indexes_follow_physical_source_block_order(review_quality_repo):
    candidate = '''### Resolved
#### RVW-3253866a28c6 [HIGH] Rejected plan is rendered as successful completion
- Resolution: missing fix anchor.

### New findings

#### [HIGH] Invalid new anchor
- Changed anchor: {"path":"missing.py","line":1}
- Trigger evidence: {"path":"review_cases.py","line":20,"quote":"        return \\"rejected\\""}
- Impact class: runtime
- Material impact: Invalid anchor remains actionable.
'''
    result, canonical = review_quality_repo.run_delta(accepted_rejected_plan_review(), candidate)
    assert result.candidate_reasons == (
        CandidateReason(0, "Resolved", "normalized", "missing_fix_anchor", "HIGH"),
        CandidateReason(1, "New findings", "filtered", "invalid_anchor", "HIGH"),
    )
    assert canonical == "### New findings\n\nNone\n\nNo validated blocking issues found.\n"


@pytest.mark.parametrize("metadata", ("Status", "Run", "Reviewed", "Validation"))
@pytest.mark.parametrize(
    ("candidate_factory", "expected_reason"),
    (
        (
            lambda metadata: resolved_rejected_plan_candidate().replace(
                "The rejection is now explicit.", f"- {metadata}: stale",
            ),
            "missing_fix_anchor",
        ),
        (
            lambda metadata: retracted_rejected_plan_candidate().replace(
                "The rendered result is intentional product behavior.", f"- {metadata}: stale",
            ),
            "invalid_trigger_evidence",
        ),
    ),
)
def test_bulleted_workflow_metadata_in_current_carryover_values_is_never_rendered(
    review_quality_repo, metadata, candidate_factory, expected_reason,
):
    candidate = candidate_factory(metadata)
    runner = review_quality_repo.run_delta if "### Resolved" in candidate else review_quality_repo.run_carryover
    result, canonical = runner(accepted_rejected_plan_review(), candidate)
    safe = json.dumps(result.to_dict()) + "\n".join(canonicalize_review._summary(result))
    assert result.candidate_reasons[0].reason == expected_reason
    assert f"- {metadata}: stale" not in canonical
    assert f"- {metadata}: stale" not in safe


@pytest.mark.parametrize("metadata", ("Status", "Run", "Reviewed", "Validation"))
def test_bulleted_workflow_metadata_in_prior_active_material_is_scope_invalid(review_quality_repo, metadata):
    previous = accepted_rejected_plan_review().replace(
        "A rejected plan is displayed as a successful zero-item completion.", f"- {metadata}: stale",
    )
    result, canonical = review_quality_repo.run_delta(previous, resolved_rejected_plan_candidate())
    assert result.failure_reason == "scope_invalid"
    assert canonical == ""


@pytest.mark.parametrize("metadata", ("Status", "Run", "Reviewed", "Validation"))
@pytest.mark.parametrize(
    "section", ("Resolved", "Retracted"),
)
def test_bulleted_workflow_metadata_in_prior_closed_fields_is_scope_invalid(review_quality_repo, metadata, section):
    field = (
        f'- Fix anchor: {{"path":"review_cases.py","line":20}}\n- Resolution: - {metadata}: stale'
        if section == "Resolved" else
        f'- Trigger evidence: {{"path":"review_cases.py","line":20,"quote":"        return \\"completed 0/{{}}\\".format(total)"}}\n- Reason: - {metadata}: stale'
    )
    previous = f'''### New findings

#### RVW-3253866a28c6 [HIGH] Rejected plan is rendered as successful completion
- Changed anchor: {{"path":"review_cases.py","line":20}}
- Trigger evidence: {{"path":"review_cases.py","line":20,"quote":"        return \\"completed 0/{{}}\\".format(total)"}}
- Impact class: user-visible
- Material impact: A rejected plan is displayed as a successful zero-item completion.

### {section}

#### RVW-deadbeefcafe [HIGH] Historical closure
{field}
'''
    result, canonical = review_quality_repo.run_delta(previous, resolved_rejected_plan_candidate())
    assert result.failure_reason == "scope_invalid"
    assert canonical == ""


def _new_free_text_candidate(title: str, material: str, prose: str = "") -> str:
    return f'''### New findings

#### [HIGH] {title}
- Changed anchor: {{"path":"review_cases.py","line":26}}
- Trigger evidence: {{"path":"review_cases.py","line":25,"quote":"        return int(value)"}}
- Impact class: runtime
- Material impact: {material}
{prose}
'''


@pytest.mark.parametrize(
    ("title", "material", "prose", "expected"),
    (
        (
            "<!-- automation:claude-code-review:v3 --> title",
            "A concrete runtime impact.", "",
            "&lt;!-- automation:claude-code-review:v3 --&gt; title",
        ),
        (
            "Safe title", "<!-- automation:gemini-auto-review:v3 --> material impact.", "",
            "&lt;!-- automation:gemini-auto-review:v3 --&gt; material impact.",
        ),
        (
            "Safe title", "A concrete runtime impact.",
            "<!-- automation-state:{\"schema\":3} -->",
            "&lt;!-- automation-state:{\"schema\":3} --&gt;",
        ),
        ("Status: visible title", "A concrete runtime impact.", "", r"\Status: visible title"),
        ("## Claude Code Review (latest)", "A concrete runtime impact.", "", r"\## Claude Code Review (latest)"),
        ("## 🔎 Gemini Code Review", "A concrete runtime impact.", "", r"\## 🔎 Gemini Code Review"),
        ("### human heading", "A concrete runtime impact.", "", r"\### human heading"),
    ),
)
def test_accepted_new_free_text_is_visible_safe_and_uses_canonical_title_identity(
    scoped_case, title, material, prose, expected,
):
    """Accepted free text must not create a marker, metadata line, or sticky Markdown heading."""
    result, canonical = scoped_case.run(_new_free_text_candidate(title, material, prose))
    assert result == canonicalize_review.CanonicalizationResult(True, 1, 0, 0, "none", "", ())
    assert expected in canonical
    assert "<!-- automation:" not in canonical
    assert "<!-- automation-state:" not in canonical
    assert "\n## Claude Code Review (latest)" not in canonical
    assert "\n## 🔎 Gemini Code Review" not in canonical
    if title != "Safe title":
        expected_id = stable_finding_id("claude", SourceAnchor("review_cases.py", 26), "HIGH", expected)
        assert f"#### {expected_id} [HIGH] {expected}" in canonical


@pytest.mark.parametrize(
    ("candidate", "raw", "expected"),
    (
        (
            resolved_rejected_plan_candidate().replace(
                "The rejection is now explicit.", "## Claude Code Review (latest)",
            ),
            "## Claude Code Review (latest)", r"\## Claude Code Review (latest)",
        ),
        (
            retracted_rejected_plan_candidate().replace(
                "The rendered result is intentional product behavior.", "## 🔎 Gemini Code Review",
            ),
            "## 🔎 Gemini Code Review", r"\## 🔎 Gemini Code Review",
        ),
        (
            resolved_rejected_plan_candidate().replace(
                "The rejection is now explicit.", "### human closure heading",
            ),
            "### human closure heading", r"\### human closure heading",
        ),
    ),
)
def test_accepted_resolution_and_reason_are_visible_safe_without_counter_changes(
    review_quality_repo, candidate, raw, expected,
):
    """Renderer-only escaping must preserve accepted carryover classification and counts."""
    runner = review_quality_repo.run_delta if "### Resolved" in candidate else review_quality_repo.run_carryover
    result, canonical = runner(accepted_rejected_plan_review(), candidate)
    assert result == canonicalize_review.CanonicalizationResult(True, 0, 0, 0, "none", "", ())
    assert expected in canonical
    assert f": {raw}" not in canonical
    assert "<!-- automation:" not in canonical
    assert "<!-- automation-state:" not in canonical


def test_json_renderer_escapes_control_bytes_without_changing_decoded_trigger_or_basis():
    """Trusted decoded JSON must round-trip exactly even though Markdown never contains raw controls."""
    marker = "<!-- automation:trusted --> &"
    evidence = canonicalize_review.TriggerEvidence("review_cases.py", 20, marker)
    finding = canonicalize_review._Finding(
        "HIGH", "Safe title", SourceAnchor("review_cases.py", 20), (evidence,), "performance",
        "A concrete performance impact.", (), ("unbounded-amplification", evidence),
    )
    lines = canonicalize_review._finding_lines("RVW-deadbeefcafe", finding)
    trigger = next(line for line in lines if line.startswith("- Trigger evidence: "))
    basis = next(line for line in lines if line.startswith("- Performance basis: "))
    for line in (trigger, basis):
        encoded = line.split(": ", 1)[1]
        assert "<" not in encoded and ">" not in encoded and "&" not in encoded
        assert "\\u003c" in encoded and "\\u003e" in encoded and "\\u0026" in encoded
        assert json.loads(encoded)["quote"] == marker


def test_current_resolved_fix_anchor_accepts_literal_json_controls_and_authenticates_output(
    review_quality_repo,
):
    """Candidate structure accepts literal path data while canonical prior bytes stay escaped."""
    path = "dir/a&b<q>.py"
    candidate = resolved_rejected_plan_candidate().replace(
        '"path":"review_cases.py","line":20', f'"path":"{path}","line":1',
    )
    result, canonical = review_quality_repo.run_delta(accepted_rejected_plan_review(), candidate)

    assert result == canonicalize_review.CanonicalizationResult(True, 0, 0, 0, "none", "", ())
    encoded = next(
        line.removeprefix("- Fix anchor: ")
        for line in canonical.splitlines()
        if line.startswith("- Fix anchor: ")
    )
    assert all(character not in encoded for character in "<>&")
    assert "\\u003c" in encoded and "\\u003e" in encoded and "\\u0026" in encoded
    assert json.loads(encoded) == {"path": path, "line": 1}

    followup, _ = review_quality_repo._run(review_quality_repo._request(
        "### New findings\n\nNone\n", reviewer="gemini", head=review_quality_repo.fixed_head,
        previous_sha=review_quality_repo.fixed_head, previous_review=canonical,
    ))
    assert followup == canonicalize_review.CanonicalizationResult(True, 0, 0, 0, "none", "", ())


def test_current_retracted_trigger_accepts_literal_json_controls_and_authenticates_output(
    review_quality_repo,
):
    """Candidate structure accepts literal quote data while canonical prior bytes stay escaped."""
    quote = "a < b > c & d"
    candidate = retracted_rejected_plan_candidate().replace(
        '"path":"review_cases.py","line":20,"quote":"        return \\"completed 0/{}\\".format(total)"',
        f'"path":"evidence.py","line":1,"quote":"{quote}"',
    )
    result, canonical = review_quality_repo.run_carryover(accepted_rejected_plan_review(), candidate)

    assert result == canonicalize_review.CanonicalizationResult(True, 0, 0, 0, "none", "", ())
    encoded = next(
        line.removeprefix("- Trigger evidence: ")
        for line in canonical.splitlines()
        if line.startswith("- Trigger evidence: ")
    )
    assert all(character not in encoded for character in "<>&")
    assert "\\u003c" in encoded and "\\u003e" in encoded and "\\u0026" in encoded
    assert json.loads(encoded) == {"path": "evidence.py", "line": 1, "quote": quote}

    followup, _ = review_quality_repo.run_carryover(
        canonical, "### New findings\n\nNone\n",
    )
    assert followup == canonicalize_review.CanonicalizationResult(True, 0, 0, 0, "none", "", ())


def test_canonicalized_marker_title_is_strict_byte_stable_authenticated_prior_input(review_quality_repo):
    """Only renderer bytes with canonical free text may enter the authenticated prior-round parser."""
    raw_title = "<!-- automation:gemini-auto-review:v3 --> prior title"
    canonical_title = "&lt;!-- automation:gemini-auto-review:v3 --&gt; prior title"
    initial = _new_free_text_candidate(raw_title, "A concrete runtime impact.", "<!-- automation-state:{} -->")
    prior_result, previous = review_quality_repo._run(review_quality_repo._request(
        initial, reviewer="gemini", head=review_quality_repo.review_head,
    ))
    expected_id = stable_finding_id("gemini", SourceAnchor("review_cases.py", 26), "HIGH", canonical_title)
    assert prior_result == canonicalize_review.CanonicalizationResult(True, 1, 0, 0, "none", "", ())
    assert f"#### {expected_id} [HIGH] {canonical_title}" in previous
    request = review_quality_repo._request(
        "### New findings\n\nNone\n", reviewer="gemini", head=review_quality_repo.review_head,
        previous_sha=review_quality_repo.review_head, previous_review=previous,
    )
    loaded = canonicalize_review._load_prior_active(request)
    assert previous == canonicalize_review._render_document([loaded[expected_id]], [], [], [])
    still_open = f'''### New findings
None

### Still open
#### {expected_id} [HIGH] ignored model title
- Changed anchor: {{"path":"review_cases.py","line":20}}
- Trigger evidence: {{"path":"review_cases.py","line":20,"quote":"        return \\"rejected\\""}}
- Impact class: runtime
- Material impact: The rejection remains visible to callers.
'''
    result, canonical = review_quality_repo.run_delta(previous, still_open)
    assert result == canonicalize_review.CanonicalizationResult(True, 1, 0, 0, "none", "", ())
    assert canonical_title in canonical
    assert "<!-- automation:" not in canonical
    assert "<!-- automation-state:" not in canonical

    followup_request = review_quality_repo._request(
        "### New findings\n\nNone\n", reviewer="gemini", head=review_quality_repo.fixed_head,
        previous_sha=review_quality_repo.fixed_head, previous_review=canonical,
    )
    loaded_followup = canonicalize_review._load_prior_active(followup_request)
    assert loaded_followup[expected_id].finding.anchor == SourceAnchor("review_cases.py", 20)
    followup, followup_canonical = review_quality_repo._run(followup_request)
    assert followup == canonicalize_review.CanonicalizationResult(True, 0, 0, 0, "none", "", ())
    assert followup_canonical == "### New findings\n\nNone\n\nNo validated blocking issues found.\n"


@pytest.mark.parametrize(
    ("raw", "expected"),
    (
        ("<!-- automation:claude-code-review:v3 -->", "&lt;!-- automation:claude-code-review:v3 --&gt;"),
        ("Status: visible", r"\Status: visible"),
        ("- Validation: visible", r"\- Validation: visible"),
        ("## Claude Code Review (latest)", r"\## Claude Code Review (latest)"),
    ),
)
def test_visible_text_canonicalization_is_idempotent(raw, expected):
    """A renderer retry or authenticated prior round must not double-escape visible text."""
    once = canonicalize_review._canonical_visible_text(raw)
    assert once == expected
    assert canonicalize_review._canonical_visible_text(once) == expected
