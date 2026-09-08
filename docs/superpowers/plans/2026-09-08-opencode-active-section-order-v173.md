# OpenCode Active Section Order v1.73 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve demoted OpenCode carryover findings ahead of inactive sections when the previous-review budget binds, while simplifying the historical release fixtures needed to authenticate v1.73.

**Architecture:** The OpenCode canonicalizer will synthesize `Still open` before the first inactive section and bind rendered blocks by section name instead of mutable array position. Historical release reconstruction will copy current files first and apply each restore chain once, then a new v1.73 capability rung will authenticate the changed workflow without changing the immutable v1.72 contract.

**Tech Stack:** GitHub Actions YAML, embedded JavaScript, Bash/AWK, Python 3, pytest, PyYAML, actionlint

**Spec:** `HANDOFF.md`; GitHub Issues [#165](https://github.com/jhw7500/automation/issues/165) and [#159](https://github.com/jhw7500/automation/issues/159)

## Global Constraints

- Preserve annotated release tags; never move or recreate v1.72.
- Keep the 20,000-character previous-review budget and finding-boundary clipping behavior unchanged.
- Preserve model-authored section order except when the workflow must synthesize `Still open`; insert that section before the first `Resolved` or `Retracted` section.
- Bind blocks to sections by the parsed section name so array insertion cannot silently reassign a block.
- Preserve every historical release verifier rung and add v1.73 as a new rung.
- Use behavior tests against the extracted workflow steps; do not assert source-text presence as the primary regression proof.

---

### Task 1: Make Historical Release Preparation Single-Pass

**Files:**
- Modify: `tests/test_verify_workflow_release.py:2031-2145`

**Interfaces:**
- Consumes: existing `restore_pre_v172_opencode_context_budget`, `restore_pre_v171_opencode_dismissals`, and `restore_pre_v170_opencode_finding_ids` helpers.
- Produces: `prepare_v163` through `prepare_v168` fixtures whose copy phase always precedes one ordered restore phase.

- [ ] **Step 1: Record the passing characterization baseline**

Run:

```bash
rtk python3 -m pytest -q tests/test_verify_workflow_release.py -k 'v163 or v164 or v165 or v166 or v167 or v168'
```

Expected: all selected historical release-verification tests pass before the refactor.

- [ ] **Step 2: Move the restore chain after all current-tree copies**

For each of `prepare_v163` through `prepare_v168`, retain the exact version-specific copies and retirements, then place the ordered restore chain once immediately before `commit(...)`:

```python
restore_pre_v172_opencode_context_budget(repo)
restore_pre_v171_opencode_dismissals(repo)
restore_pre_v170_opencode_finding_ids(repo)
```

Do not change `prepare_v162`: its `restore_pre_v163_finding_dismissal(repo, budget=True)` is a distinct historical boundary.

- [ ] **Step 3: Verify historical behavior stayed green**

Run the Step 1 command again.

Expected: the same selected historical releases pass with no digest or hunk-restoration failure.

### Task 2: Prove the Active Finding Loss

**Files:**
- Modify: `tests/test_review_workflow_logic.py:14863`

**Interfaces:**
- Consumes: `_run_opencode_canonicalize`, `_run_opencode_ctx`, `_opencode_v2_body`, `_state_line`, and `_bot`.
- Produces: two regression tests covering section assignment/order and the 20,000-character collector budget.

- [ ] **Step 1: Add a failing canonicalizer ordering test**

Create `test_opencode_invalid_anchor_synthesizes_still_open_before_inactive_sections`. Construct a prior active finding, then submit a candidate containing `New findings` and `Retracted` but no `Still open`; give the carried finding an out-of-scope current anchor so canonicalization demotes it.

Assert these hand-derived outcomes:

```python
assert body.index("### New findings") < body.index("### Still open")
assert body.index("### Still open") < body.index("### Retracted")
still_open = body.split("### Still open", 1)[1].split("### Retracted", 1)[0]
retracted = body.split("### Retracted", 1)[1]
assert "Persisting problem" in still_open
assert "Unrelated disproven problem" in retracted
assert "Unrelated disproven problem" not in still_open
```

The unrelated retracted block must carry valid evidence and bind to a second prior active heading, so the real canonicalizer retains it.

- [ ] **Step 2: Run the ordering test and observe RED**

Run:

```bash
rtk python3 -m pytest -q tests/test_review_workflow_logic.py::test_opencode_invalid_anchor_synthesizes_still_open_before_inactive_sections
```

Expected: FAIL because the current body renders `Retracted` before the synthesized `Still open`.

- [ ] **Step 3: Add a failing budget-integration test**

Create `test_opencode_demoted_carryover_survives_previous_review_budget`. Generate enough retained `Retracted` blocks to exceed 20,000 characters, obtain the published body from `_run_opencode_canonicalize`, and feed that body to `_run_opencode_ctx`.

Assert:

```python
assert "previous review truncated to fit the budget" in context
assert "Persisting problem" in context
assert "Retracted filler 59" not in context
```

- [ ] **Step 4: Run the budget test and observe RED**

Run:

```bash
rtk python3 -m pytest -q tests/test_review_workflow_logic.py::test_opencode_demoted_carryover_survives_previous_review_budget
```

Expected: FAIL because tail clipping removes the synthesized active block before the fix.

### Task 3: Render Synthesized Active Sections Safely

**Files:**
- Modify: `.github/workflows/opencode-auto-review.yml:4940-4965`
- Test: `tests/test_review_workflow_logic.py`

**Interfaces:**
- Consumes: parsed section records with unique names and parsed block records with `entry.section`.
- Produces: a canonical review whose synthesized `Still open` precedes inactive sections and whose block membership is invariant under section-array insertion.

- [ ] **Step 1: Insert a missing active section before inactive sections**

Replace append-only synthesis with:

```javascript
const firstInactiveIndex = parsedReview.sections.findIndex((item) =>
  item.name === 'Resolved' || item.name === 'Retracted');
stillOpenIndex = firstInactiveIndex === -1
  ? parsedReview.sections.length : firstInactiveIndex;
parsedReview.sections.splice(stillOpenIndex, 0, { name: 'Still open', lines: [] });
```

Keep `entry.section = 'Still open'`; no renderer behavior may depend on the shifted numeric index.

- [ ] **Step 2: Bind rendered blocks by section name**

Change the retained-block selection to:

```javascript
const retained = parsedReview.blocks.filter((entry) =>
  entry.section === section.name && !filteredEntries.has(entry));
```

Remove the now-unnecessary renderer `sectionIndex` callback parameter and the reassignment of `entry.sectionIndex` during demotion.

- [ ] **Step 3: Verify both regressions are GREEN**

Run:

```bash
rtk python3 -m pytest -q tests/test_review_workflow_logic.py \
  -k 'invalid_anchor_synthesizes or demoted_carryover_survives or out_of_scope_carryover_anchor'
```

Expected: all three tests pass.

### Task 4: Add the Immutable v1.73 Release Rung

**Files:**
- Modify: `scripts/workflow_release_inventory.py:145-265`
- Modify: `scripts/verify_workflow_release.py:817-830,5950-5975`
- Modify: `tests/release_fixture_helpers.py:760`
- Modify: `tests/test_verify_workflow_release.py:1-35,2140-2245`

**Interfaces:**
- Produces: `OPENCODE_ACTIVE_SECTION_ORDER_RELEASE = (1, 73)`, `release_supports_opencode_active_section_order(ref: str) -> bool`, `restore_pre_v173_opencode_active_section_order(repo: Path) -> None`, and `prepare_v173(repo: Path) -> str`.
- Preserves: the v1.72 OpenCode workflow digest and fixture bytes.

- [ ] **Step 1: Add failing v1.73 boundary tests**

Add these behavior checks:

```python
assert release_inventory.release_supports_opencode_active_section_order("v1.72") is False
assert release_inventory.release_supports_opencode_active_section_order("v1.73") is True
```

Add a `prepare_v173` fixture and tests proving:

- the current workflow passes the v1.73 verifier;
- the current workflow is rejected on the v1.72 release line;
- a workflow restored with `restore_pre_v173_opencode_active_section_order` is rejected on the v1.73 line;
- the restored workflow still passes the v1.72 line.

- [ ] **Step 2: Run the new release tests and observe RED**

Run:

```bash
rtk python3 -m pytest -q tests/test_verify_workflow_release.py -k 'v172 or v173'
```

Expected: FAIL because the v1.73 predicate, restore helper, and authenticated digest rung do not exist.

- [ ] **Step 3: Implement the capability and historical restore**

Add the v1.73 constant/predicate. Add one exact current-to-v1.72 workflow hunk for the insertion and name-based renderer change, applying it before the v1.72 restore. Import the predicate into the verifier and select a new `EXPECTED_OPENCODE_ACTIVE_SECTION_ORDER_WORKFLOW_SHA256` only for v1.73 and later.

- [ ] **Step 4: Compute and pin the authenticated current workflow digest**

Run:

```bash
rtk sha256sum .github/workflows/opencode-auto-review.yml
```

Copy the observed lowercase digest into the v1.73 OpenCode digest entry; retain the v1.72 digest constant unchanged.

- [ ] **Step 5: Verify v1.72 and v1.73 are both GREEN**

Run the Step 2 command again.

Expected: all selected v1.72/v1.73 release tests pass.

### Task 5: Update the Active Contract and Validate the Release Candidate

**Files:**
- Modify: `docs/workflows/contracts.md:590-617`
- Modify: `HANDOFF.md`

**Interfaces:**
- Produces: an operator-facing contract that distinguishes the remaining model-authored order freedom from the fixed workflow-synthesized order.

- [ ] **Step 1: Update the contract**

State that from v1.73 a synthesized `Still open` is inserted before `Resolved`/`Retracted`, rendering binds by section name, and the active block therefore precedes inactive tail blocks. Retain the documented limitation that a model-authored existing `Still open` may still appear in any grammar-valid relative order.

- [ ] **Step 2: Update the handoff**

Record #165 and #159 as implemented in the candidate, identify #166 as the separate pre-tag PR, and do not claim v1.73 released or rolled out before those external actions occur.

- [ ] **Step 3: Run focused validation**

```bash
rtk python3 -m pytest -q tests/test_review_workflow_logic.py tests/test_verify_workflow_release.py
```

Expected: all tests pass.

- [ ] **Step 4: Run repository validation**

```bash
rtk python3 -m pytest -q
rtk python3 -c 'from pathlib import Path; import yaml; [yaml.safe_load(path.read_text()) for path in Path(".github/workflows").glob("*.yml")]'
rtk /tmp/actionlint-v1.7.12/actionlint -shellcheck= -pyflakes= .github/workflows/*.yml examples/baseline-workflows/.github/workflows/*.yml
```

Expected: the full pytest summary reports zero failures, YAML parsing exits 0, and actionlint exits 0.

- [ ] **Step 5: Run the pre-PR tribunal and create the PR only after zero blockers**

Use the repository pre-PR tribunal workflow against the committed head. Any code change invalidates the verdict and requires a fresh round.
