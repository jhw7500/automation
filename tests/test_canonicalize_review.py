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
            "    return 'success' if ok else 'failure'\n",
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


@pytest.mark.parametrize(
    ("payload", "reason"),
    (
        (None, "candidate_missing"),
        (b"\xff", "invalid_utf8"),
        (b"x" * 60001, "candidate_oversize"),
        (b"### New findings\nNone\n\n#### [HIGH] contradictory", "ambiguous_document"),
        (b"### New findings\nNone\n\n### New findings\nNone", "ambiguous_document"),
        (b"### Unknown\nNone", "ambiguous_document"),
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
        ("[HIGH] Style without anchor", "- Impact class: style\n- Material impact: cosmetic only.", "invalid_anchor"),
        ("[LOW] Low category", "- Changed anchor: {\"path\":\"review_cases.py\",\"line\":26}\n- Trigger evidence: {\"path\":\"review_cases.py\",\"line\":25,\"quote\":\"        return int(value)\"}\n- Impact class: runtime\n- Material impact: concrete.", "non_actionable_category"),
        ("[BOGUS] Invalid severity", "- Changed anchor: {\"path\":\"review_cases.py\",\"line\":26}\n- Trigger evidence: {\"path\":\"review_cases.py\",\"line\":25,\"quote\":\"        return int(value)\"}\n- Impact class: runtime\n- Material impact: concrete.", "invalid_severity"),
        ("[HIGH] Bad trigger", "- Changed anchor: {\"path\":\"review_cases.py\",\"line\":26}\n- Trigger evidence: {\"path\":\"review_cases.py\",\"line\":25,\"quote\":\"wrong\"}\n- Impact class: runtime\n- Material impact: concrete.", "invalid_trigger_evidence"),
        ("[HIGH] Bad impact", "- Changed anchor: {\"path\":\"review_cases.py\",\"line\":26}\n- Trigger evidence: {\"path\":\"review_cases.py\",\"line\":25,\"quote\":\"        return int(value)\"}\n- Impact class: invented\n- Material impact: concrete.", "invalid_impact_class"),
        ("[HIGH] Deficit", "- Changed anchor: {\"path\":\"review_cases.py\",\"line\":26}\n- Trigger evidence: {\"path\":\"review_cases.py\",\"line\":25,\"quote\":\"        return int(value)\"}\n- Impact class: runtime\n- Material impact: Cannot confirm the impact.", "missing_material_impact"),
        ("[MEDIUM] No performance basis", "- Changed anchor: {\"path\":\"review_cases.py\",\"line\":26}\n- Trigger evidence: {\"path\":\"review_cases.py\",\"line\":25,\"quote\":\"        return int(value)\"}\n- Impact class: performance\n- Material impact: Each request is expensive.", "unsupported_performance_basis"),
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
