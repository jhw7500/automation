# Review Finding Quality Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish only mechanically evidenced Claude and Gemini findings while preserving valid findings, authenticated re-review state, and prior successful checkpoints.

**Architecture:** A repository-owned Python composite action sits between each provider step and sticky-comment publication. `review_scope.py` extracts the existing OpenCode Git-object and added-line boundary, while `canonicalize_review.py` parses one shared grammar, assigns workflow-owned IDs, soft-filters individual candidates, and emits canonical Markdown plus bounded JSON metadata; Claude and Gemini then publish only that canonical file under schema-3 state.

**Tech Stack:** Python 3.12 standard library, Git 2.x plumbing, GitHub Actions composite YAML, `actions/github-script` JavaScript, pytest, PyYAML `BaseLoader`, actionlint 1.7.12

**Spec:** `docs/superpowers/specs/2026-08-23-review-finding-quality-gates-design.md`

## Global Constraints

- Add no provider/model call: one Claude or Gemini generation remains one provider request.
- Keep provider output at a hard 60,000-byte maximum and decode it with fatal UTF-8 handling.
- Hard reasons are exactly `candidate_missing`, `invalid_utf8`, `candidate_oversize`, `ambiguous_document`, `scope_invalid`, and `canonicalizer_error`.
- Soft reasons are exactly `invalid_anchor`, `invalid_trigger_evidence`, `invalid_severity`, `invalid_impact_class`, `missing_material_impact`, `unsupported_performance_basis`, `non_actionable_category`, `unknown_prior_id`, `duplicate_prior_binding`, and `missing_fix_anchor`.
- Allowed severities are exactly `CRITICAL`, `HIGH`, and `MEDIUM`; `LOW` is non-actionable.
- Allowed impact classes are exactly `runtime`, `security`, `data-integrity`, `user-visible`, and `performance`.
- The stable finding ID is `RVW-` plus the first 12 lowercase hexadecimal characters of SHA-256 over reviewer, changed path, changed line, severity, and normalized title.
- Claude and Gemini markers become `<!-- automation:claude-code-review:v3 -->` and `<!-- automation:gemini-auto-review:v3 -->`; OpenCode remains schema/marker v2.
- A v2 Claude/Gemini comment is only an in-place display target. It supplies no previous SHA, full hash, body, quality counters, or carryover identity.
- Schema-3 success uses `quality_schema: 1`; first schema-3 failure has null counters, while stale failure and unchanged success preserve counters authenticated by the previous schema-3 success.
- Publication reads reviewer-specific canonical Markdown only. Raw `claude-review.md` and `gemini_review.md` remain untrusted inputs to the action and are never read by the upsert step.
- Rejected candidate prose never enters the result JSON, workflow state, logs, or PR comment.
- Runtime code uses the Python standard library only. Test-only dependencies remain pytest and PyYAML.
- Every shell command in this repository is prefixed with `rtk`; `.omc/` and `HANDOFF.md` remain untouched.
- The release capability boundary is `v1.46`; historical `v1.45` and older release inventories must continue verifying against their original closed sets.
- Do not update `scripts/workflow-config.json` to `v1.46` until the annotated tag exists and the coordinated `/jhw:ship` schema-3 parser prerequisite is green.

---

## File Map

- `.github/actions/canonicalize-review/review_scope.py`: trusted manifest/Git identity, selected-range added-line, and current-head quotation validation; no Markdown parsing.
- `.github/actions/canonicalize-review/canonicalize_review.py`: candidate decoding, document/block parsing, finding/carryover decisions, stable IDs, canonical Markdown, JSON result, and CLI/output bridge.
- `.github/actions/canonicalize-review/action.yml`: exact composite-action interface; no GitHub mutation.
- `tests/test_review_scope.py`: OpenCode-equivalent Git boundary, rename/path, added-line, tracked-blob, symlink, and quotation tests.
- `tests/test_canonicalize_review.py`: parser, reason, ID, rendering, carryover, result-hygiene, and CLI tests.
- `tests/fixtures/review-finding-quality/*.md`: provider-independent PR #101-derived candidate corpus.
- `.github/workflows/claude-code-review.yml`: schema-3 collection, grammar prompt, shared action call, canonical-only upsert, migration, and preserved-state behavior.
- `.github/workflows/gemini-auto-review.yml`: the same schema-3 contract plus removal of the contradictory `Cannot verify` instruction.
- `tests/test_review_workflow_logic.py`: extracted Bash/JavaScript behavior tests and exact workflow wiring assertions for both reviewers.
- `scripts/workflow_release_inventory.py`: `v1.46+` closed inventory for all three canonicalizer action files.
- `scripts/workflow_release_bundle.py`: latest-capability defaults without changing historical extraction.
- `scripts/verify_workflow_release.py`: exact action contract, helper signatures/reasons, reviewer dependency graph, schema-3 publication boundary, and historical capability gates.
- `tests/test_workflow_release_bundle.py`: exact action metadata/argv bridge and release-bundle membership.
- `tests/test_verify_workflow_release.py`: `v1.45` historical acceptance and `v1.46` fail-closed inventory/wiring mutation tests.
- `tests/release_fixture_helpers.py` and `tests/fixtures/review-workflows-v1.45.2/`: Git-history-independent Claude/Gemini snapshots from immutable commit `abf5e65cf6188277d9984be062d0b069c82cf25f`.
- `docs/workflows/contracts.md`: consumer-visible hard/soft policy, candidate grammar, schema-3 state, counters, migration, and merge interpretation.

---

### Task 1: Extract the trusted Git scope validator

**Files:**
- Create: `.github/actions/canonicalize-review/review_scope.py`
- Create: `tests/test_review_scope.py`
- Reference: `.github/workflows/opencode-auto-review.yml:1540-1795`
- Reference: `.github/actions/prepare-review-diff/prepare_review_diff.py:123-277`

**Interfaces:**
- Produces: `SourceAnchor(path: str, line: int)` and `TriggerEvidence(path: str, line: int, quote: str)`.
- Produces: `ScopeValidationError(ValueError)` for an invalid trusted boundary.
- Produces: `load_review_scope(repository_root: Path, manifest_path: Path, selected_diff_path: Path, *, diff_mode: Literal["full", "delta"], previous_sha: str, expected_repository: str) -> ReviewScope`.
- Produces: `ReviewScope.validate_changed_anchor(anchor: SourceAnchor) -> bool`, `ReviewScope.validate_fix_anchor(anchor: SourceAnchor) -> bool`, and `ReviewScope.validate_trigger(evidence: TriggerEvidence) -> bool`.
- Test fixtures: `scoped_repo` exposes `root`, `manifest`, `selected_diff`, and `load()`; `delta_repo` additionally exposes `previous_sha`; `scope_attack_repo` commits regular, symlink, and invalid-UTF-8 blobs under one valid manifest.

- [ ] **Step 1: Write RED tests for exact manifest and added-line identity**

Build a temporary two-commit repository and assert that only an actual selected-range `+` line with the current manifest identity passes:

```python
def test_changed_anchor_requires_exact_manifest_record_and_added_line(scoped_repo):
    scope = load_review_scope(
        scoped_repo.root,
        scoped_repo.manifest,
        scoped_repo.selected_diff,
        diff_mode="full",
        previous_sha="",
        expected_repository="example/repo",
    )
    assert scope.validate_changed_anchor(SourceAnchor("src/runner.py", 3))
    assert not scope.validate_changed_anchor(SourceAnchor("src/runner.py", 2))
    assert not scope.validate_changed_anchor(SourceAnchor("src/missing.py", 3))


def test_delta_anchor_is_not_satisfied_by_an_older_full_range_addition(delta_repo):
    scope = load_review_scope(
        delta_repo.root,
        delta_repo.manifest,
        delta_repo.selected_diff,
        diff_mode="delta",
        previous_sha=delta_repo.previous_sha,
        expected_repository="example/repo",
    )
    assert scope.validate_changed_anchor(SourceAnchor("src/runner.py", 4))
    assert not scope.validate_changed_anchor(SourceAnchor("src/runner.py", 3))
```

The fixture writes an exact schema-1 manifest with `repository`, positive `pr_number`, `merge_base_sha`, `head_sha`, and name-status-derived `files`; it obtains `review-full.diff` or `review-delta.diff` from the same immutable range.

- [ ] **Step 2: Run the focused tests and confirm the missing module failure**

Run: `rtk python3 -m pytest tests/test_review_scope.py -q`

Expected: collection fails because `.github/actions/canonicalize-review/review_scope.py` does not exist.

- [ ] **Step 3: Implement the exact public records and hardened Git runner**

Use these public definitions and fixed Git environment:

```python
@dataclass(frozen=True)
class SourceAnchor:
    path: str
    line: int


@dataclass(frozen=True)
class TriggerEvidence:
    path: str
    line: int
    quote: str


@dataclass(frozen=True)
class ScopeFile:
    status: str
    filename: str
    previous_filename: str | None = None


@dataclass(frozen=True)
class ScopeManifest:
    repository: str
    pr_number: int
    merge_base_sha: str
    head_sha: str
    files: tuple[ScopeFile, ...]


class ScopeValidationError(ValueError):
    """The trusted scope cannot be reconstructed exactly."""


GIT_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent/automation-review-scope/home",
    "XDG_CONFIG_HOME": "/nonexistent/automation-review-scope/xdg",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "/bin/false",
    "SSH_ASKPASS": "/bin/false",
    "GIT_EXTERNAL_DIFF": "",
}
```

Every Git invocation is an argv list beginning `/usr/bin/git --no-replace-objects --literal-pathspecs -c diff.external=`. Never invoke a shell, user alias, textconv, external diff, replacement object, or newline-delimited path transport.

- [ ] **Step 4: Port the OpenCode name-status and hunk parser without weakening it**

Implement private `parse_name_status(payload: bytes) -> tuple[ScopeFile, ...]` and `parse_added_lines(patch: str) -> dict[int, str]`, preserving the OpenCode rejection rules for NUL termination, strict UTF-8, status/score syntax, safe hunk coordinates, exact hunk exhaustion, EOF markers, monotonic ranges, and added-line text. Select the immutable left side exactly as follows:

```python
def selected_left(manifest: ScopeManifest, diff_mode: str, previous_sha: str) -> str:
    if diff_mode == "full":
        return manifest.merge_base_sha
    if diff_mode == "delta" and re.fullmatch(r"[0-9a-f]{40}", previous_sha):
        return previous_sha
    raise ScopeValidationError("invalid selected range")
```

`load_review_scope` must also require a regular, non-symlink selected diff; strict UTF-8; non-empty bytes; `git rev-parse HEAD == manifest.head_sha`; `manifest.repository == expected_repository`; an exact full-range name-status reconstruction; and an ancestor-valid delta left side. For a rename/copy, validate both literal old/current path arguments and accept only the current `filename` as an anchor identity.

- [ ] **Step 5: Write RED tests for current-head evidence and filesystem attacks**

```python
def test_trigger_reads_the_tracked_head_blob_not_the_worktree(scoped_repo):
    scope = scoped_repo.load()
    (scoped_repo.root / "src/runner.py").write_text("WORKTREE SECRET\n", encoding="utf-8")
    assert scope.validate_trigger(TriggerEvidence("src/runner.py", 3, "    return value + 1"))
    assert not scope.validate_trigger(TriggerEvidence("src/runner.py", 3, "WORKTREE SECRET"))


def test_trigger_rejects_symlink_missing_non_utf8_and_quote_mismatch(scope_attack_repo):
    scope = scope_attack_repo.load()
    assert not scope.validate_trigger(TriggerEvidence("link.py", 1, "target.py"))
    assert not scope.validate_trigger(TriggerEvidence("missing.py", 1, "x"))
    assert not scope.validate_trigger(TriggerEvidence("binary.dat", 1, "x"))
    assert not scope.validate_trigger(TriggerEvidence("src/runner.py", 3, "return value + 1"))
```

- [ ] **Step 6: Implement tracked-blob quotation and fix-anchor rules**

Resolve evidence with an argv-equivalent of `git ls-tree -z manifest.head_sha -- evidence.path`, require exactly one exact path record with mode `100644` or `100755` and type `blob`, read its authenticated object ID with `git cat-file blob`, decode UTF-8 strictly, and compare the complete requested line after removing only its `\n` or `\r\n` terminator. Reject unsafe integers, absolute/empty/parent-traversing paths, symlink mode `120000`, submodule mode `160000`, missing lines, and quote mismatch.

`validate_fix_anchor` returns false unless the selected mode is `delta`; when it is `delta`, it uses the same selected-range added-line map as `validate_changed_anchor`.

- [ ] **Step 7: Run the scope tests and the existing OpenCode anchor corpus**

Run: `rtk python3 -m pytest tests/test_review_scope.py tests/test_review_workflow_logic.py -q`

Expected: PASS, including existing OpenCode malformed hunk, rename, literal-path, and exact-line cases.

- [ ] **Step 8: Commit the extracted boundary**

```bash
rtk git add .github/actions/canonicalize-review/review_scope.py tests/test_review_scope.py
rtk git commit -m "feat(review): extract trusted finding scope validator"
```

---

### Task 2: Build the document canonicalizer and soft-filter policy

**Files:**
- Create: `.github/actions/canonicalize-review/canonicalize_review.py`
- Create: `tests/test_canonicalize_review.py`
- Consume: `.github/actions/canonicalize-review/review_scope.py`

**Interfaces:**
- Produces: `CanonicalizationRequest` with `reviewer: Literal["claude", "gemini"]`, six required `Path` fields for candidate/canonical/result/manifest/diff/repository root, `diff_mode: Literal["full", "delta"]`, `previous_sha: str`, `previous_review_file: Path | None`, and `expected_repository: str`.
- Produces: `CandidateReason(index: int, section: Literal["New findings", "Still open", "Resolved", "Retracted"], outcome: Literal["filtered", "normalized"], reason: str, claimed_severity: Literal["none", "MEDIUM", "HIGH", "CRITICAL"])`.
- Produces: `CanonicalizationResult(document_valid: bool, accepted_count: int, filtered_count: int, normalized_count: int, filtered_max_severity: Literal["none", "MEDIUM", "HIGH", "CRITICAL"], failure_reason: str, candidate_reasons: tuple[CandidateReason, ...])`.
- Produces: `canonicalize(request: CanonicalizationRequest) -> CanonicalizationResult` and `stable_finding_id(reviewer: str, anchor: SourceAnchor, severity: str, title: str) -> str`.
- CLI writes canonical Markdown only for a valid document, writes a schema-1 result of at most 131,072 UTF-8 bytes when it can do so safely, and mirrors scalar fields to `--github-output` when supplied.
- Test fixtures: `case_factory(payload: bytes | None)` creates a request over a valid synthetic scope and exposes `run() -> tuple[CanonicalizationResult, str | None]`; `scoped_case.run(text: str)` encodes the same valid scope for mixed-block tests.

- [ ] **Step 1: Write RED tests for document boundaries, hard reasons, and result hygiene**

```python
@pytest.mark.parametrize(
    ("payload", "reason"),
    (
        (None, "candidate_missing"),
        (b"\xff", "invalid_utf8"),
        (b"x" * 60001, "candidate_oversize"),
        (b"### New findings\nNone\n\n#### [HIGH] contradictory", "ambiguous_document"),
        (b"### New findings\nNone\n\n### New findings\nNone", "ambiguous_document"),
    ),
)
def test_hard_document_failures_are_exact_and_write_no_canonical_body(case_factory, payload, reason):
    result, canonical = case_factory(payload).run()
    assert result.document_valid is False
    assert result.failure_reason == reason
    assert canonical is None


def test_result_json_never_repeats_rejected_model_text(case_factory):
    secret_claim = "INJECTED-REJECTED-CLAIM"
    candidate = f"""### New findings

#### [HIGH] {secret_claim}
- Changed anchor: {{"path":"missing.py","line":9}}
- Trigger evidence: {{"path":"review_cases.py","line":25,"quote":"        return int(value)"}}
- Impact class: runtime
- Material impact: The process records success after rejecting the operation.
""".encode("utf-8")
    result, _ = case_factory(candidate).run()
    encoded = json.dumps(result.to_dict(), sort_keys=True)
    assert secret_claim not in encoded
    assert result.candidate_reasons == (
        CandidateReason(index=0, section="New findings", outcome="filtered", reason="invalid_anchor", claimed_severity="HIGH"),
    )
```

- [ ] **Step 2: Run the focused test and confirm the missing canonicalizer failure**

Run: `rtk python3 -m pytest tests/test_canonicalize_review.py -q`

Expected: collection fails because `canonicalize_review.py` does not exist.

- [ ] **Step 3: Implement the closed result schema and stable ID algorithm**

```python
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


def normalize_title(title: str) -> str:
    return " ".join(title.split()).casefold()


def stable_finding_id(reviewer: str, anchor: SourceAnchor, severity: str, title: str) -> str:
    identity = "\0".join((reviewer, anchor.path, str(anchor.line), severity, normalize_title(title)))
    return "RVW-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
```

The result JSON keys are exactly `schema`, `document_valid`, `accepted_count`, `filtered_count`, `normalized_count`, `filtered_max_severity`, `failure_reason`, and `candidate_reasons`. Each candidate-reason object has exactly `index`, `section`, `outcome`, `reason`, and `claimed_severity`; all values are closed enums/integers, and no title, explanation, quote, path, or model substring is stored.

- [ ] **Step 4: Implement unambiguous section/block parsing**

Recognize exactly one `### New findings` section and at most one each of `### Still open`, `### Resolved`, and `### Retracted`. Plain summary prose is ignored, but an unknown `###` heading, duplicate actionable section, malformed `####` boundary, clean declaration mixed with a block, or response with neither an exact clean declaration nor a recognizable New-findings boundary returns `ambiguous_document`.

Cap the total number of `####` candidate blocks at 512 before allocating result entries; a larger document is `ambiguous_document`. This keeps the per-candidate result below 131,072 bytes under the 60,000-byte raw-input ceiling.

Accept the entire clean strings `No blocking issues found.` and `No validated blocking issues found.` and normalize each to:

```markdown
### New findings

None

No validated blocking issues found.
```

Within a block, parse JSON with an `object_pairs_hook` that rejects duplicate keys. Require exact key sets, strings for paths/quotes, positive Python integers that are at most `9007199254740991`, and no Boolean-as-integer acceptance.

- [ ] **Step 5: Write RED tests for one-invalid/one-valid soft filtering**

```python
def test_invalid_finding_is_filtered_without_discarding_valid_neighbor(scoped_case):
    result, canonical = scoped_case.run(
        """### New findings

#### [HIGH] Missing anchor
- Impact class: runtime
- Material impact: The process records success after rejecting the operation.

#### [MEDIUM] Broad ValueError catch hides invalid configuration
- Changed anchor: {"path":"review_cases.py","line":26}
- Trigger evidence: {"path":"review_cases.py","line":25,"quote":"        return int(value)"}
- Impact class: runtime
- Material impact: An invalid numeric configuration is converted into a normal result.

The caller cannot distinguish invalid input from a supported value.
"""
    )
    assert result.document_valid is True
    assert (result.accepted_count, result.filtered_count, result.normalized_count) == (1, 1, 0)
    assert result.filtered_max_severity == "HIGH"
    assert "Missing anchor" not in canonical
    assert "RVW-61d4cd9ac260 [MEDIUM] Broad ValueError catch" in canonical
```

- [ ] **Step 6: Implement finding validation and deterministic rendering**

For each `New findings` block, evaluate in this order so one candidate receives one deterministic reason: severity/category, changed anchor, trigger evidence, impact class, material-impact paragraph, then performance basis. `LOW` and the explicit impact values `style`, `maintainability`, and `cleanup` map to `non_actionable_category`; malformed or other severity values map to `invalid_severity`; other impact values map to `invalid_impact_class`.

`accepted_count` counts accepted New and Still-open actionable blocks only. `filtered_count` counts rejected New blocks and known-ID Still-open blocks that fail evidence/materiality checks. `normalized_count` counts first-round, unknown-ID, duplicate-ID, invalid Resolved, and invalid Retracted carryover blocks. Valid Resolved/Retracted blocks render but increment none of the three counters. A hard result uses zero for all three action-result counters, `filtered_max_severity: none`, an empty `candidate_reasons` array, and the closed `failure_reason`; the workflow converts those counters to null only when constructing a first-failure schema-3 state.

Reject material-impact text matching this bounded case-insensitive proof-deficit expression:

```python
PROOF_DEFICIT = re.compile(
    r"\b(?:plausible(?:\s+but)?\s+unconfirmed|cannot\s+(?:confirm|verify)|"
    r"not\s+confirmed|pending\s+confirmation|unverified\s+external)\b",
    re.IGNORECASE,
)
```

For `performance`, require one `Performance basis` object. `measured` must reuse tracked-blob quotation validation and contain at least one ASCII decimal digit in the quote. `unbounded-amplification` must reuse tracked-blob quotation validation. Any missing, duplicate, unsupported, or invalid basis maps to `unsupported_performance_basis`.

Render accepted blocks from parsed fields rather than copying reserved lines. Ignore model-supplied IDs on New findings, preserve input order, and strip lines matching workflow-owned markers, state, Status/Run/Reviewed/Validation metadata, and sticky headers. If no New/Still-open actionable block survives, include the exact sentence `No validated blocking issues found.`.

- [ ] **Step 7: Implement hard-failure containment and the CLI**

`canonicalize()` maps candidate I/O/decoding/size failures, document ambiguity, and `ScopeValidationError` to their exact hard reason. The CLI catches unexpected exceptions, emits a minimal `canonicalizer_error` result without exception/model text, removes any pre-existing canonical output, and exits zero after a valid failure result so the workflow upsert can stamp failure. An inability to atomically create the result or GitHub-output file exits nonzero, leaving the workflow to infer `canonicalizer_error` from action outcome.

Print one bounded summary line as `review-canonicalization: document_valid=... accepted=... filtered=... normalized=... filtered_max=... failure_reason=...`, followed by one line per rejected block as `candidate[index]: section=... outcome=... reason=... claimed_severity=...`. These lines contain only closed metadata from `CanonicalizationResult`, never paths, titles, quotes, explanations, or rejected prose.

- [ ] **Step 8: Run parser, scope, and result-contract tests**

Run: `rtk python3 -m pytest tests/test_canonicalize_review.py tests/test_review_scope.py -q`

Expected: PASS with exact IDs, reason codes, Markdown, and JSON keys.

- [ ] **Step 9: Commit the canonicalizer core**

```bash
rtk git add .github/actions/canonicalize-review/canonicalize_review.py tests/test_canonicalize_review.py
rtk git commit -m "feat(review): canonicalize evidenced findings"
```

---

### Task 3: Bind carryover identity and add the PR #101 regression corpus

**Files:**
- Modify: `.github/actions/canonicalize-review/canonicalize_review.py`
- Modify: `tests/test_canonicalize_review.py`
- Create: `tests/fixtures/review-finding-quality/plan-global.md`
- Create: `tests/fixtures/review-finding-quality/duplicate-yaml.md`
- Create: `tests/fixtures/review-finding-quality/value-error.md`
- Create: `tests/fixtures/review-finding-quality/rejected-plan.md`

**Interfaces:**
- Consumes: schema-3 previous canonical Markdown containing workflow-owned `RVW-[0-9a-f]{12}` headings.
- Produces: prior active set from only `New findings` and `Still open`; Resolved/Retracted never re-enter the active set.
- Produces: exact canonical carryover sections and normalized decisions for unknown/duplicate/missing-fix identity.
- Test fixture: `review_quality_repo` exposes `run_fixture(name, reviewer="claude")`, `run_text(text)`, and `run_delta(previous_review, candidate)`; each returns `(CanonicalizationResult, canonical_markdown)` over the exact three-commit repository described below.

- [ ] **Step 1: Add the fixed PR #101-derived candidate files**

`plan-global.md`:

```markdown
### New findings

#### [HIGH] plan_global can bypass plan isolation
- Changed anchor: {"path":"review_cases.py","line":12}
- Trigger evidence: {"path":"review_cases.py","line":10,"quote":"    return execute_plan(plan, plan_global=GLOBAL_PLAN)"}
- Impact class: data-integrity
- Material impact: A non-local plan can be executed instead of the validated plan.
```

`duplicate-yaml.md`:

```markdown
### New findings

#### [MEDIUM] Profile YAML is loaded twice
- Changed anchor: {"path":"review_cases.py","line":13}
- Trigger evidence: {"path":"review_cases.py","line":14,"quote":"    second = load_profile(path)"}
- Impact class: performance
- Material impact: Each plan reads the local profile twice.
```

`value-error.md`:

```markdown
### New findings

#### [MEDIUM] Broad ValueError catch hides invalid configuration
- Changed anchor: {"path":"review_cases.py","line":26}
- Trigger evidence: {"path":"review_cases.py","line":25,"quote":"        return int(value)"}
- Impact class: runtime
- Material impact: Invalid numeric configuration is converted into a normal result.

The caller cannot distinguish invalid input from a supported value.
```

`rejected-plan.md`:

```markdown
### New findings

#### [HIGH] Rejected plan is rendered as successful completion
- Changed anchor: {"path":"review_cases.py","line":20}
- Trigger evidence: {"path":"review_cases.py","line":20,"quote":"        return \"completed 0/{}\".format(total)"}
- Impact class: user-visible
- Material impact: A rejected plan is displayed as a successful zero-item completion.
```

- [ ] **Step 2: Build the deterministic three-commit repository fixture**

The base and review head use one tracked `review_cases.py`. The head content is exactly:

```python
from pathlib import Path

def load_profile(path: Path) -> str:
    return path.read_text(encoding="utf-8")

def execute_plan(plan, plan_global=None):
    return plan_global

def call_plan(plan):
    return execute_plan(plan, plan_global=None)

def load_twice(path: Path) -> tuple[str, str]:
    first = load_profile(path)
    second = load_profile(path)
    return first, second


def render_progress(accepted: int, total: int) -> str:
    if accepted == 0:
        return "completed 0/{}".format(total)
    return f"{accepted}/{total}"

def classify(value: str) -> int | str:
    try:
        return int(value)
    except ValueError:
        return "invalid"
```

The base omits `load_twice`, uses only `return f"{accepted}/{total}"` in `render_progress`, and returns `"unknown"` from `classify`. The third commit replaces line 20 with `return "rejected"` to supply a delta fix anchor for carryover tests.

Lock the intended head coordinates in the repository fixture with `head_text.splitlines()[19] == '        return "completed 0/{}".format(total)'`, `head_text.splitlines()[24] == "        return int(value)"`, and `head_text.splitlines()[25] == "    except ValueError:"` before committing the head.

- [ ] **Step 3: Write RED corpus assertions**

```python
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
```

- [ ] **Step 4: Write RED carryover assertions**

```python
def test_first_round_resolved_is_normalized_not_a_hard_failure(review_quality_repo):
    result, canonical = review_quality_repo.run_text(first_round_false_resolved())
    assert result.document_valid is True
    assert result.normalized_count == 1
    assert result.candidate_reasons[0].reason == "unknown_prior_id"
    assert "### Resolved" not in canonical


def test_known_prior_resolution_requires_a_delta_fix_anchor(review_quality_repo):
    result, canonical = review_quality_repo.run_delta(
        previous_review=accepted_rejected_plan_review(),
        candidate=resolved_rejected_plan_candidate(),
    )
    assert result.document_valid is True
    assert result.normalized_count == 0
    assert "### Resolved" in canonical
    assert "RVW-3253866a28c6 [HIGH] Rejected plan" in canonical


def test_one_prior_id_cannot_bind_twice(review_quality_repo):
    result, canonical = review_quality_repo.run_delta(
        previous_review=accepted_rejected_plan_review(),
        candidate=duplicate_prior_binding_candidate(),
    )
    assert result.normalized_count == 2
    assert {item.reason for item in result.candidate_reasons} == {"duplicate_prior_binding"}
    assert "### Still open" not in canonical
    assert "### Resolved" not in canonical
```

Define the referenced test strings in the same file so their identity is literal and reusable:

```python
def accepted_rejected_plan_review() -> str:
    return """### New findings

#### RVW-3253866a28c6 [HIGH] Rejected plan is rendered as successful completion
- Changed anchor: {"path":"review_cases.py","line":20}
- Trigger evidence: {"path":"review_cases.py","line":20,"quote":"        return \\"completed 0/{}\\".format(total)"}
- Impact class: user-visible
- Material impact: A rejected plan is displayed as a successful zero-item completion.
"""


def first_round_false_resolved() -> str:
    return """### New findings
None

### Resolved
#### RVW-3253866a28c6 [HIGH] Rejected plan is rendered as successful completion
- Fix anchor: {"path":"review_cases.py","line":20}
- Resolution: The rejection is now explicit.
"""


def resolved_rejected_plan_candidate() -> str:
    return first_round_false_resolved()


def duplicate_prior_binding_candidate() -> str:
    return """### New findings
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
"""
```

- [ ] **Step 5: Implement prior parsing and section-specific validation**

Parse the optional previous file with the strict canonical renderer grammar, require unique `RVW-[0-9a-f]{12}` IDs, and build the active set from prior `New findings` plus `Still open` only. A malformed supplied previous file raises `ScopeValidationError`, which becomes `scope_invalid`; an absent previous file with empty `previous_sha` is first round.

For current blocks:

- `Still open` must bind one active ID once, uses the prior severity/title for rendering, and passes current changed-anchor plus trigger/materiality checks.
- `Resolved` must bind one active ID once, contain one valid delta `Fix anchor` and one non-empty single-line `Resolution`; otherwise normalize with `unknown_prior_id`, `duplicate_prior_binding`, or `missing_fix_anchor`.
- `Retracted` must bind one active ID once, contain at least one valid `Trigger evidence` and one non-empty single-line `Reason`; invalid/missing evidence or Reason uses `invalid_trigger_evidence` and counts as normalization.
- On first round, every carryover block normalizes out with `unknown_prior_id` without failing the document.
- A prior ID used by more than one current carryover block causes every conflicting block to normalize with `duplicate_prior_binding`.
- A prior ID cannot appear as a New finding because new model IDs are discarded and the workflow recomputes identity from the new anchor/title tuple.

- [ ] **Step 6: Assert exact Markdown, counters, and reason order**

Add literal expected Markdown for clean, accepted, Resolved, and Retracted results. Order sections as New findings, Still open, Resolved, Retracted; order candidate reasons by source block index; compute `filtered_max_severity` only from filtered New/Still-open candidates, never from normalized Resolved/Retracted blocks.

Run: `rtk python3 -m pytest tests/test_canonicalize_review.py -q`

Expected: PASS for all nine design-corpus classes, including invalid-plus-valid preservation and ambiguous-document hard failure.

- [ ] **Step 7: Commit carryover and corpus behavior**

```bash
rtk git add .github/actions/canonicalize-review/canonicalize_review.py tests/test_canonicalize_review.py tests/fixtures/review-finding-quality
rtk git commit -m "test(review): lock finding quality regression corpus"
```

---

### Task 4: Publish the shared composite-action interface

**Files:**
- Create: `.github/actions/canonicalize-review/action.yml`
- Modify: `tests/test_workflow_release_bundle.py`
- Modify: `tests/test_canonicalize_review.py`

**Interfaces:**
- Consumes the nine spec inputs exactly.
- Exposes the six hyphenated outputs exactly, backed by underscore-named helper outputs.
- Performs no API request and no comment/check mutation.

- [ ] **Step 1: Write the exact action-document RED test**

Assert a `yaml.BaseLoader` document equal to this complete contract:

```yaml
name: Canonicalize review
description: Canonicalize evidenced Claude or Gemini review findings
inputs:
  reviewer:
    required: true
  candidate-file:
    required: true
  canonical-file:
    required: true
  result-file:
    required: true
  scope-manifest:
    required: true
  selected-diff:
    required: true
  diff-mode:
    required: true
  previous-sha:
    required: false
    default: ''
  previous-review-file:
    required: false
    default: ''
outputs:
  document-valid:
    value: ${{ steps.canonicalize.outputs.document_valid }}
  accepted-count:
    value: ${{ steps.canonicalize.outputs.accepted_count }}
  filtered-count:
    value: ${{ steps.canonicalize.outputs.filtered_count }}
  normalized-count:
    value: ${{ steps.canonicalize.outputs.normalized_count }}
  filtered-max-severity:
    value: ${{ steps.canonicalize.outputs.filtered_max_severity }}
  failure-reason:
    value: ${{ steps.canonicalize.outputs.failure_reason }}
runs:
  using: composite
  steps:
    - id: canonicalize
      shell: bash
      env:
        REVIEWER: ${{ inputs.reviewer }}
        CANDIDATE_FILE: ${{ inputs.candidate-file }}
        CANONICAL_FILE: ${{ inputs.canonical-file }}
        RESULT_FILE: ${{ inputs.result-file }}
        SCOPE_MANIFEST: ${{ inputs.scope-manifest }}
        SELECTED_DIFF: ${{ inputs.selected-diff }}
        DIFF_MODE: ${{ inputs.diff-mode }}
        PREVIOUS_SHA: ${{ inputs.previous-sha }}
        PREVIOUS_REVIEW_FILE: ${{ inputs.previous-review-file }}
      run: >-
        python3 "$GITHUB_ACTION_PATH/canonicalize_review.py"
        --reviewer "$REVIEWER"
        --candidate-file "$CANDIDATE_FILE"
        --canonical-file "$CANONICAL_FILE"
        --result-file "$RESULT_FILE"
        --scope-manifest "$SCOPE_MANIFEST"
        --selected-diff "$SELECTED_DIFF"
        --diff-mode "$DIFF_MODE"
        --previous-sha "$PREVIOUS_SHA"
        --previous-review-file "$PREVIOUS_REVIEW_FILE"
        --repository-root "$GITHUB_WORKSPACE"
        --expected-repository "$GITHUB_REPOSITORY"
        --github-output "$GITHUB_OUTPUT"
```

- [ ] **Step 2: Run the exact-action test and confirm it fails**

Run: `rtk python3 -m pytest tests/test_workflow_release_bundle.py -q -k canonicalize_review`

Expected: FAIL because the action metadata is absent.

- [ ] **Step 3: Add the action and argv-injection test**

Create the action from Step 1. Execute its `run` string in a temporary Bash harness with semicolons, Unicode, spaces, and leading dashes in every path-like environment value. Replace only the helper file in the temporary action directory with an argv recorder and assert each value arrives as one argument in the exact flag order above.

- [ ] **Step 4: Add a runner-style CLI output test**

Invoke the real helper with `--github-output`, then assert the file contains exactly:

```text
document_valid=true
accepted_count=1
filtered_count=0
normalized_count=0
filtered_max_severity=none
failure_reason=
```

Also assert `canonical-file` and `result-file` are newly created regular files beneath the temporary workspace and that pre-existing symlink destinations are rejected as `canonicalizer_error` without following them.

- [ ] **Step 5: Run the action and core tests**

Run: `rtk python3 -m pytest tests/test_workflow_release_bundle.py tests/test_canonicalize_review.py tests/test_review_scope.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the action surface**

```bash
rtk git add .github/actions/canonicalize-review/action.yml tests/test_workflow_release_bundle.py tests/test_canonicalize_review.py
rtk git commit -m "feat(review): expose shared canonicalizer action"
```

---

### Task 5: Integrate Claude with schema-3 canonical publication

**Files:**
- Modify: `.github/workflows/claude-code-review.yml`
- Modify: `tests/test_review_workflow_logic.py`

**Interfaces:**
- Consumes: `claude-review.md`, `review-scope.json`, the selected prepared diff, and optional authenticated `claude-previous-review.md`.
- Produces: `claude-review-canonical.md`, `claude-review-result.json`, and the exact schema-3 sticky envelope.
- Preserves: existing current-head/generation ordering, unchanged mode, failure/stale body preservation, and v2 display-target reuse without v2 trust.

- [ ] **Step 1: Write RED wiring and raw-output isolation tests**

```python
def test_claude_uses_one_shared_canonicalizer_and_upsert_reads_only_canonical_file():
    workflow = _load("claude-code-review.yml")
    job = workflow["jobs"]["claude-review"]
    steps = [step for step in job["steps"] if step.get("uses") == "$/.github/actions/canonicalize-review"]
    assert len(steps) == 1
    assert steps[0]["id"] == "canonicalize-review"
    assert steps[0]["with"]["reviewer"] == "claude"
    assert steps[0]["with"]["candidate-file"] == "${{ github.workspace }}/claude-review.md"
    upsert = _step(workflow, "claude-review", "Upsert review comment")
    assert "claude-review-canonical.md" in upsert["with"]["script"]
    assert "readFileSync('claude-review.md'" not in upsert["with"]["script"]
```

- [ ] **Step 2: Write RED migration and state-semantic tests**

Extend the extracted collector/upsert harness with `CLAUDE_V3_MARKER` and a schema-3 state helper. Assert:

- a valid v2 sticky yields empty `previous_sha`, empty `previous_full_hash`, no prior canonical file, and full preparation;
- a valid v3 success yields its SHA/hash plus a prior file containing only canonical body bytes;
- a v2 sticky is patched in place by first v3 success but contributes no old prose;
- first v3 failure has `quality_schema: 1` and null `accepted_count`, `filtered_count`, `normalized_count`, and `filtered_max_severity`;
- stale v3 failure preserves all four prior values and prior body/head/hash;
- unchanged v3 success advances the successful head while preserving prior body/hash/counters;
- a prospective successful sticky over 65,536 UTF-8 bytes becomes a hard `candidate_oversize` attempt and preserves prior success instead of truncating the canonical body;
- wrong reviewer, wrong schema, extra/missing key, negative count, or invalid max severity is not authenticated.

- [ ] **Step 3: Change collection to authenticate schema 3 only**

Use the exact sorted state-key set:

```text
accepted_count, attempt_head, attempt_status, diff_mode, filtered_count,
filtered_max_severity, full_diff_sha256, normalized_count, pr, quality_schema,
reviewer, run_attempt, run_id, schema, successful_head
```

Set `MARKER` to `<!-- automation:claude-code-review:v3 -->`. On authenticated prior success, write the sanitized canonical body to `claude-previous-review.md` separately from `claude-review-context.md`; export SHA/hash only from this record. Continue putting bounded human comments in the context file, but never pass them to the canonicalizer.

- [ ] **Step 4: Replace the Claude prompt shape with the shared grammar**

Require exactly the fields and section names from the spec, JSON one-line anchors/evidence, allowed severity/impact values, performance basis, workflow-owned prior IDs for carryover, `Fix anchor` for Resolved, and evidence plus Reason for Retracted. State that the canonicalizer may omit invalid blocks and that the model must not emit workflow state, markers, validation counters, or `Cannot verify` sections.

Keep the existing trust boundary: selected prepared diff is the exclusive change set, surrounding repository code is evidence only, and `claude-review.md` is the sole writable model path.

- [ ] **Step 5: Insert the action before upsert**

Use this exact dataflow:

```yaml
      - name: Canonicalize Claude review
        id: canonicalize-review
        if: ${{ always() && steps.prepare-diff.outputs.diff-ready == 'true' && steps.prepare-diff.outputs.diff-mode != 'unchanged' }}
        uses: $/.github/actions/canonicalize-review
        with:
          reviewer: claude
          candidate-file: ${{ github.workspace }}/claude-review.md
          canonical-file: ${{ github.workspace }}/claude-review-canonical.md
          result-file: ${{ github.workspace }}/claude-review-result.json
          scope-manifest: ${{ github.workspace }}/review-scope.json
          selected-diff: ${{ steps.prepare-diff.outputs.diff-mode == 'delta' && format('{0}/review-delta.diff', github.workspace) || format('{0}/review-full.diff', github.workspace) }}
          diff-mode: ${{ steps.prepare-diff.outputs.diff-mode }}
          previous-sha: ${{ steps.collect-context.outputs.previous_sha }}
          previous-review-file: ${{ github.workspace }}/claude-previous-review.md
```

Use the actual collector step ID already present in the workflow when resolving `previous_sha`; the test must assert the expression from that ID exactly.

- [ ] **Step 6: Make upsert canonical-only and build schema-3 state**

Read `claude-review-canonical.md` only when the action outcome is success and `document-valid == true`. Set `modelFailed` when provider/diff/canonical action/document validity fails. Preserve provider/diff reasons first; otherwise use the action's closed hard reason, falling back to `canonicalizer_error`.

Construct the visible line only from trusted action outputs or authenticated prior v3 state:

```javascript
const validationLine = `- Validation: accepted=${quality.accepted_count}; filtered=${quality.filtered_count}; normalized=${quality.normalized_count}; filtered_max=${quality.filtered_max_severity}`;
```

Strip any model-authored Validation line before wrapping. The new state uses `schema: 3`, `quality_schema: 1`, exact non-negative safe integers, and max severity in `none|MEDIUM|HIGH|CRITICAL`. Use the v3 record as preservation source; search exact v2-marker bot comments only as a display target after no newer v3 target exists.

Before publishing a success body, require `Buffer.byteLength(body, 'utf8') <= 65536`. A larger body changes the current attempt to failure reason `candidate_oversize`; it never slices canonical Markdown. Failure/stale envelopes are rebuilt from bounded workflow-owned metadata and any already authenticated prior v3 body.

- [ ] **Step 7: Run Claude behavior tests**

Run: `rtk python3 -m pytest tests/test_review_workflow_logic.py -q -k 'claude or shared_diff or canonical'`

Expected: PASS, including canonical-only publication, first-v3 full migration, stale preservation, unchanged preservation, and exact quality metadata.

- [ ] **Step 8: Commit Claude integration**

```bash
rtk git add .github/workflows/claude-code-review.yml tests/test_review_workflow_logic.py
rtk git commit -m "feat(review): gate Claude findings before publication"
```

---

### Task 6: Integrate Gemini with the same schema-3 contract

**Files:**
- Modify: `.github/workflows/gemini-auto-review.yml`
- Modify: `tests/test_review_workflow_logic.py`

**Interfaces:**
- Consumes: `gemini_review.md`, `review-scope.json`, selected prepared diff, and optional authenticated `prev_review.txt` renamed to `gemini-previous-review.md`.
- Produces: `gemini-review-canonical.md`, `gemini-review-result.json`, and the same schema-3 quality state as Claude.
- Preserves: 429/daily-quota/provider diagnostics and retries; the canonicalizer adds no request or retry.

- [ ] **Step 1: Write RED parity and prompt-consistency tests**

```python
def test_gemini_uses_the_same_canonicalizer_contract_as_claude():
    workflow = _load("gemini-auto-review.yml")
    action = next(
        step for step in workflow["jobs"]["gemini-review"]["steps"]
        if step.get("uses") == "$/.github/actions/canonicalize-review"
    )
    assert action["with"]["reviewer"] == "gemini"
    assert action["with"]["canonical-file"] == "${{ github.workspace }}/gemini-review-canonical.md"
    python = _extract_gemini_python()
    assert "Cannot verify (outside provided diff)" not in python
    assert "Never emit a `Cannot verify`" in python
    assert "Trigger evidence" in python
    assert "Material impact" in python
    assert "Performance basis" in python
```

Add the same migration/state/upsert cases as Claude with `GEMINI_V3_MARKER`, plus a provider-failure test proving `quota_exhausted` remains the visible failure reason and no canonicalization retry occurs.
Also assert the 65,536-byte final-comment gate uses UTF-8 byte length and preserves prior success rather than truncating a canonical block.

- [ ] **Step 2: Run Gemini-focused tests and confirm they fail**

Run: `rtk python3 -m pytest tests/test_review_workflow_logic.py -q -k gemini`

Expected: FAIL on missing shared-action wiring, v2 marker, raw-file publication, and contradictory prompt text.

- [ ] **Step 3: Authenticate only Gemini schema-3 prior state**

Use the same exact key/semantic validator as Task 5 with reviewer `gemini`, marker `<!-- automation:gemini-auto-review:v3 -->`, and dedicated `gemini-previous-review.md`. Continue sanitizing bounded human comments into their separate prompt input. A v2 record can be selected only later as a display target.

- [ ] **Step 4: Replace both contradictory re-review instructions with one grammar**

Delete the instruction that says disputed items outside the diff belong under `Cannot verify`. Retain the single rule that unverifiable items are omitted. Emit the same New/Still open/Resolved/Retracted grammar, exact JSON fields, performance basis, prior IDs, and proof requirements as Claude.

- [ ] **Step 5: Insert the shared action with Gemini paths**

Use `if: always()` under ready/non-unchanged diff, reviewer `gemini`, raw `gemini_review.md`, canonical `gemini-review-canonical.md`, result `gemini-review-result.json`, selected full/delta expression, the collector's authenticated previous SHA, and `gemini-previous-review.md`.

- [ ] **Step 6: Replace the section-name heuristic with canonical-only upsert**

Remove `dropNonActionableSections`; the shared canonicalizer now owns block filtering. Keep provider failure classification and diff-truncation gates. Read only `gemini-review-canonical.md`, build schema-3 state and the workflow-owned Validation line from action outputs, preserve prior v3 success on stale/unchanged attempts, and use v2 only as an overwrite target.

- [ ] **Step 7: Run cross-reviewer behavior and exact-wiring tests**

Run: `rtk python3 -m pytest tests/test_review_workflow_logic.py -q`

Expected: PASS. The static test must prove Claude and Gemini each reference canonicalize-review exactly once, OpenCode references it zero times, and neither upsert script contains a raw candidate `readFileSync` call.

- [ ] **Step 8: Commit Gemini parity**

```bash
rtk git add .github/workflows/gemini-auto-review.yml tests/test_review_workflow_logic.py
rtk git commit -m "feat(review): gate Gemini findings before publication"
```

---

### Task 7: Close the v1.46 release inventory and verifier boundary

**Files:**
- Modify: `scripts/workflow_release_inventory.py`
- Modify: `scripts/workflow_release_bundle.py`
- Modify: `scripts/verify_workflow_release.py`
- Modify: `tests/release_fixture_helpers.py`
- Create: `tests/fixtures/review-workflows-v1.45.2/claude-code-review.yml`
- Create: `tests/fixtures/review-workflows-v1.45.2/gemini-auto-review.yml`
- Modify: `tests/test_workflow_release_bundle.py`
- Modify: `tests/test_verify_workflow_release.py`
- Modify: `docs/workflows/contracts.md`

**Interfaces:**
- Produces: `release_supports_canonicalize_review(ref: str) -> bool` with boundary `(1, 46)`.
- Produces: three exact `100644` canonicalizer release roots for `v1.46+` only.
- Verifies: Claude/Gemini use prepare-review-diff once and canonicalize-review once; OpenCode uses prepare-review-diff once and canonicalize-review zero times.

- [ ] **Step 1: Write RED historical/capability inventory tests**

```python
def test_canonicalizer_capability_boundary_is_closed():
    assert release_inventory.release_supports_canonicalize_review("v1.45.2") is False
    assert release_inventory.release_supports_canonicalize_review("v1.46") is True
    paths = {root.path.as_posix() for root in release_inventory.release_roots_for("v1.46")}
    assert {
        ".github/actions/canonicalize-review/action.yml",
        ".github/actions/canonicalize-review/canonicalize_review.py",
        ".github/actions/canonicalize-review/review_scope.py",
    } <= paths


def test_v145_inventory_does_not_require_future_canonicalizer_files(current_release_repo):
    repo, current = current_release_repo
    restore_v145_review_workflows(repo)
    for relative in (
        ".github/actions/canonicalize-review/action.yml",
        ".github/actions/canonicalize-review/canonicalize_review.py",
        ".github/actions/canonicalize-review/review_scope.py",
    ):
        (repo / relative).unlink()
    historical = commit(repo, "v1.45 historical inventory")
    assert release_verifier.verify_commit_content(repo, "v1.45", historical) == historical
```

Copy the two fixture files byte-for-byte from immutable v1.45.2 commit `abf5e65cf6188277d9984be062d0b069c82cf25f`, and add `restore_v145_review_workflows(repo)` to `tests/release_fixture_helpers.py`. The helper reads only those checked-in bytes and never invokes Git. Update tests that mean “the current implementation contract” from ref `v1.45` to `v1.46`; retain `v1.45`/`v1.45.2` only in explicit historical-capability assertions.

- [ ] **Step 2: Add the inventory roots and update latest bundle defaults**

Define `CANONICALIZE_REVIEW_ACTION_ROOT`, `CANONICALIZE_REVIEW_HELPER_ROOT`, and `REVIEW_SCOPE_HELPER_ROOT`, group them in `CANONICALIZE_REVIEW_ROOTS`, and compose roots by capability rather than making `v1.45` inherit new files. Change only convenience defaults in `workflow_release_bundle.py` from `v1.45` to `v1.46`; every caller that supplies an older ref still receives `release_roots_for(ref)`.

- [ ] **Step 3: Write RED verifier mutation tests**

For commit-only `v1.46`, assert rejection when:

- any one of the three action files is absent, non-regular, executable, or outside the closed inventory;
- action YAML input/output/run/env/argv structure differs from Task 4;
- either Claude or Gemini omits/duplicates/near-matches the canonicalizer reference;
- OpenCode gains the new reference;
- either reviewer upsert reads its raw candidate file;
- marker/schema/quality keys or Validation spelling changes;
- the helper omits any hard/soft reason or required public signature;
- only one reviewer contains Trigger evidence, Material impact, Performance basis, or carryover ID prompt rules.

For `v1.45`, assert any canonicalizer local dependency is rejected whether or not future action files happen to exist in the tree.

- [ ] **Step 4: Implement exact action and dependency verification**

Add `EXPECTED_CANONICALIZE_REVIEW_ACTION` as the BaseLoader form of Task 4. Replace the one-action local-reference assertion with a versioned exact map:

```python
def expected_review_actions(ref: str, workflow: str) -> list[str]:
    actions = [PREPARE_REVIEW_DIFF_ACTION] if release_supports_prepare_review_diff(ref) else []
    if release_supports_canonicalize_review(ref) and workflow in {
        "claude-code-review.yml", "gemini-auto-review.yml"
    }:
        actions.append(CANONICALIZE_REVIEW_ACTION)
    return actions
```

Reject every `$/.github/actions/` or `./.github/actions/` reference not present in that exact ordered list. Add the canonicalizer action to the approved local-action set only for `v1.46+`.

- [ ] **Step 5: Verify helper and workflow publication contracts**

Read both Python helpers from authenticated commit objects, compile them, and require the exact constants/signatures/reason literals. Inspect parsed YAML and embedded script text to require one shared action per reviewer, v3 markers, schema/quality keys, canonical filename reads, and absence of raw filename reads in Upsert steps. Keep OpenCode's existing three-job/verifier boundary unchanged.

- [ ] **Step 6: Update the consumer contract documentation**

Document:

- v1.46's three new immutable action files and exact composite inputs/outputs;
- hard checkpoint failure versus per-finding soft filtering;
- the candidate/carryover grammar and workflow-owned ID derivation;
- exact `- Validation: accepted=N; filtered=N; normalized=N; filtered_max=LEVEL` interpretation;
- all-filtered success wording and why it is not a CLEAN proof;
- schema-3 success/failure/stale/unchanged semantics;
- v2 display-only migration and first-v3 forced full review;
- OpenCode v2 coexistence;
- merge tooling treats only canonical bracketed severities as blocking and reports filtered counters as warnings.

- [ ] **Step 7: Run release inventory/verifier tests**

Run: `rtk python3 -m pytest tests/test_workflow_release_bundle.py tests/test_verify_workflow_release.py -q`

Expected: PASS for historical `v1.40` through `v1.45.2` fixtures and the new `v1.46` boundary.

- [ ] **Step 8: Commit the release contract**

```bash
rtk git add scripts/workflow_release_inventory.py scripts/workflow_release_bundle.py scripts/verify_workflow_release.py tests/test_workflow_release_bundle.py tests/test_verify_workflow_release.py docs/workflows/contracts.md
rtk git commit -m "feat(review): close v1.46 quality gate release contract"
```

---

### Task 8: Run repository-wide verification and open the implementation PR

**Files:**
- Modify only if a verification failure demonstrates a defect in Tasks 1-7.
- Preserve: `.omc/`, `HANDOFF.md`, and unrelated working-tree changes.

**Interfaces:**
- Consumes all implementation commits.
- Produces one reviewable automation PR linked to issues #41, #42, and #43.

- [ ] **Step 1: Run syntax and focused quality suites**

```bash
rtk python3 -m py_compile .github/actions/canonicalize-review/review_scope.py .github/actions/canonicalize-review/canonicalize_review.py
rtk python3 -m pytest tests/test_review_scope.py tests/test_canonicalize_review.py tests/test_review_workflow_logic.py tests/test_workflow_release_bundle.py tests/test_verify_workflow_release.py -q
```

Expected: both commands exit 0.

- [ ] **Step 2: Run the complete Python and YAML parse suites**

```bash
rtk python3 -m pytest -q
rtk python3 -c 'from pathlib import Path; import yaml; paths=sorted(Path(".github/workflows").glob("*.y*ml"))+sorted(Path("examples/baseline-workflows/.github/workflows").glob("*.y*ml"))+sorted(Path(".github/actions").glob("**/*.y*ml")); [(_ for _ in ()).throw(AssertionError(p)) for p in paths if not isinstance(yaml.load(p.read_text(encoding="utf-8"), Loader=yaml.BaseLoader), dict)]; print(f"PASS: {len(paths)} YAML documents")'
```

Expected: complete pytest PASS and all YAML documents parse as mappings.

- [ ] **Step 3: Run the same actionlint boundary as CI**

Use the locally installed verified binary when present:

```bash
rtk actionlint -shellcheck= -pyflakes= .github/workflows/*.yml examples/baseline-workflows/.github/workflows/*.yml
```

If `rtk actionlint --version` is unavailable, install actionlint 1.7.12 using the digest and commands already pinned in `.github/workflows/test-fleet-tools.yml`, then rerun the exact lint command. Expected: no diagnostics and exit 0.

- [ ] **Step 4: Verify proposed v1.46 commit content before tagging**

Resolve the proposed commit first and copy the exact 40-character output. Replace the placeholder
in the second command with that literal output before running it; do not execute the placeholder
text itself.

```bash
rtk git rev-parse HEAD
rtk python3 -m scripts.verify_workflow_release --automation . --ref v1.46 --expected-commit <40-character-HEAD-SHA> --commit-only
```

Expected: `PASS: v1.46 commit content is secure at` followed by the copied exact HEAD SHA. Symbolic
`HEAD` is intentionally rejected by the verifier because `--expected-commit` is a raw-OID pin.

- [ ] **Step 5: Audit the final diff and workspace ownership**

```bash
rtk git diff --check origin/main...HEAD
rtk git status --short --branch
rtk git diff --stat origin/main...HEAD
```

Expected: no whitespace error; only planned tracked paths differ; `.omc/` and `HANDOFF.md` remain untracked and unchanged.

- [ ] **Step 6: Push and open one implementation PR**

```bash
rtk git push origin task/41-review-finding-quality-gates
rtk gh pr create --repo jhw7500/automation --base main --head task/41-review-finding-quality-gates --title "feat(review): add finding quality gates" --body "Implements #41 and #42. Adds the provider-independent regression corpus and release gates for #43.\n\nThe shared canonicalizer soft-filters unsupported individual findings, binds carryover to workflow IDs, migrates Claude/Gemini to v3 state, and preserves the existing fail-closed input/state boundary."
```

If an open PR already exists for the branch, use `rtk gh pr view task/41-review-finding-quality-gates --repo jhw7500/automation` and continue with that PR instead of creating a duplicate.

- [ ] **Step 7: Obtain review and repair before merge**

Run the configured automation reviewers, inspect every actionable finding against current code, fix validated issues with a new RED/GREEN commit, rerun Steps 1-5, and request re-review. Stop before merge while any required check is failing, any `must-fix` finding remains open, or schema/release verification is stale against the current head.

- [ ] **Step 8: Merge only the verified current head**

Use `$jhw-ship --merge --auto-fix --target='rtk python3 -m pytest -q'` after all checks and required reviews are green. Confirm the merged commit equals the last verified PR head, close #41 and #42 from the implementation PR, and leave #43 open until the release plus full/delta canary evidence is attached.

---

### Task 9: Release v1.46 and canary it on pim-check

**Files:**
- Modify after tag creation: `scripts/workflow-config.json`
- Modify after tag creation: `tests/test_workflow_catalog.py`
- Consumer change: `jhw7500/pim-check` managed workflow config/callers generated by the rollout tool.

**Interfaces:**
- Consumes: merged automation main commit, annotated tag `v1.46`, verified `/jhw:ship` schema-3 parsing, and actionlint.
- Produces: an immutable pim-check caller pin and one first/full plus one delta live-review acceptance record.

- [ ] **Step 1: Verify the installed ship parser prerequisite before consumer pinning**

Run:

```bash
rtk rg -n "quality_schema|filtered_max_severity|automation:claude-code-review:v3|automation:gemini-auto-review:v3" /home/jhw/.codex/skills/jhw-ship
```

Expected: the parser authenticates schema 3 for Claude/Gemini, preserves OpenCode schema 2, derives blocking status only from canonical bracketed findings, and renders filtered counts as warnings. If any element is absent, stop consumer pinning and complete the separate jhw-ship skill update/review first; the automation release itself may remain tagged.

- [ ] **Step 2: Create and verify the immutable annotated release**

From updated `main`, rerun Task 8 Steps 1-5. Resolve merged `main` and copy the exact 40-character
output. Replace the placeholder below with that literal output before running the verification
command; do not execute the placeholder text itself. Then create annotated tag `v1.46`, push the
tag, and verify both local and remote identities in this order:

```bash
rtk git rev-parse main
rtk git tag -a v1.46 -m "automation workflows v1.46"
rtk git push origin refs/tags/v1.46
rtk python3 -m scripts.verify_workflow_release --automation . --ref v1.46 --expected-commit <40-character-merged-main-SHA> --remote origin
```

Expected: local annotated tag, peeled commit, remote tag, and authenticated release content all
resolve to the copied exact merged-main commit. Symbolic `main` is intentionally rejected by the
raw-OID pin contract. Never move or recreate the tag.

- [ ] **Step 3: Advance the fleet default only after tag verification**

Change exactly `scripts/workflow-config.json` `automation_ref` from its current value to `v1.46`, update the literal expectation in `tests/test_workflow_catalog.py`, run `rtk python3 -m pytest tests/test_workflow_catalog.py -q`, and commit/push through a normal reviewed PR. This later default change is not part of the immutable v1.46 tag bytes.

- [ ] **Step 4: Plan and publish only the pim-check rollout**

Use a fresh narrow workspace created with `rtk mktemp -d`, then execute the released rollout tool from the verified release bundle:

```bash
pim_rollout_workspace="$(rtk mktemp -d /tmp/automation-v146-pim-check.XXXXXX)"
actionlint_path="$(rtk which actionlint)"
rtk python3 scripts/rollout_workflow_fleet.py --automation . --workspace "$pim_rollout_workspace" --initialize-workspace --mode plan --ref v1.46 --repo pim-check --actionlint "$actionlint_path"
rtk python3 scripts/rollout_workflow_fleet.py --automation . --workspace "$pim_rollout_workspace" --mode publish --ref v1.46 --repo pim-check --confirm --actionlint "$actionlint_path"
```

Expected: plan status `planned` or `reusable`, only managed workflow/config paths change, and the generated caller `uses:` values pin the 40-character v1.46 commit rather than tag text.

- [ ] **Step 5: Validate the first schema-3 full round**

On the rollout PR or a dedicated normal pim-check implementation PR, confirm:

- both collectors ignore any old v2 state and prepare `diff-mode=full`;
- Claude/Gemini each make one provider request;
- the shared action reports `document-valid=true`;
- unsupported `plan_global`/basis-free performance candidates do not appear as actionable headings;
- valid corpus-style findings remain if emitted;
- the sticky marker is v3, state `successful_head` equals live PR head, `quality_schema` is 1, counters match action logs, and the visible Validation line matches state exactly;
- a first-round model-authored Resolved block is normalized out rather than posted or treated as hard failure.

- [ ] **Step 6: Validate one delta re-review**

Push one bounded follow-up commit that fixes or changes an anchored canary case. Confirm `diff-mode=delta`, prior IDs come only from the authenticated v3 body, a valid Fix anchor can produce Resolved, unknown/duplicate IDs normalize out, current `Reviewed` equals the new head, and no stale prior body is mislabeled as current.

- [ ] **Step 7: Record acceptance and merge the rollout**

Attach run URLs, exact heads, state/counter excerpts, provider request counts, and review disposition to issue #43. Merge the pim-check rollout only after required tests and reviewers are green. If live behavior violates any Task 9 acceptance point, leave the rollout PR open, revert no immutable tag, fix automation under a new release version, and rerun the full/delta canary.
