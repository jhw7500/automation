# OpenCode Finalization Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Authenticate a failed original OpenCode finalizer, complete its existing invocation idempotently with zero provider calls, and make the original review reusable through a distinct completed recovery receipt.

**Architecture:** Keep normal claim/finalize strict. Add a separate pure budget transition, a bounded evidence collector and serialized recovery publisher, and a distinct receipt verifier in both normal OpenCode collectors. Recompute original canonical output with the original approved canonicalizer in a read-only replay.

**Tech Stack:** Python standard library, Node standard library, GitHub Actions/REST, existing pytest and workflow harnesses.

**Spec:** ../specs/2026-09-10-opencode-finalization-recovery-design.md

## Global Constraints

- Worktree: existing task/2fb06be868a0-jhw7500-automation-179 at 6dc5934ecd6bd1c298e8b7fb869b412b0828ff7c; no new Claim or Registry operation.
- Every shell command starts with rtk. Edit source with apply_patch.
- No commit, push, PR, release, live recovery, override, provider execution or consumer mutation in this implementation pass.
- Preserve original run/attempt, caller and central SHA, head/diff, round and estimated usage. No additional provider calls or rounds.
- Same OpenCode per-PR concurrency group; cancel-in-progress false for recovery; bounded prewrite comparison, not an atomic external-writer guarantee.
- Original artifact expiry fails closed unless a completed recovery receipt already authenticates an identical no-op.
- Preserve normal failed-run distrust and v1.74/v1.75 release verification.
- No new dependencies. No consumer executable code or artifact executable payloads run.
- Tests first; meaningful behavioral and mutation checks. Existing Task start approval and approved design persist.

### Task 1: Idempotent original invocation transition

**Files:**
- Modify: .github/actions/review-invocation-budget/review_invocation_budget.py
- Test: tests/test_review_invocation_budget_recovery.py

**Interfaces:**
- Consumes existing LedgerState, Invocation, FinalizeRequest, RunProvenance and Transition.
- Produces recover_finalize(state: LedgerState, original_claim: Invocation, request: FinalizeRequest, provenances: Mapping[tuple[int,int], RunProvenance]) -> Transition.
- Caller is the authenticated recovery validator, never the ordinary claim/finalize CLI.

- [x] Write tests against real claimed_state("opencode") fixtures: original call_count 1 / elapsed_seconds 45 are finalized on the same invocation with no appended round.
- [x] Run the new test file and observe missing recover_finalize failure.
- [x] Validate full original claimed entry and live state/provenance; require selected invocation to be the latest relevant entry. Use existing finalize only after checks. For already-finalized state derive the expected entry from the original claim and authenticated request; exact equality permits an unchanged Transition with decision finalized, mutate_comment false, allow_invocation false.
- [x] Reject foreign reviewer, request run/attempt/head/diff drift, changed original immutable fields, newer generation, altered metrics/outcome on repeat and malformed state. Ordinary finalize repeat stays refused.
- [x] Run new tests and existing budget suite. Report tests, exact edited files, and verifier implications without committing.

Example required assertions:
```python
assert result.state.invocations[0].run_attempt == 1
assert result.state.invocations[0].call_count == 1
assert result.state.invocations[0].elapsed_seconds == 45
assert len(result.state.invocations) == 1
assert result.allow_invocation is False
assert repeat.state == result.state
assert repeat.mutate_comment is False
```

### Task 2: Authenticated evidence and read-only canonical replay

**Files:**
- Create: .github/actions/recover-opencode-review/evidence.py
- Create: .github/actions/recover-opencode-review/replay.js
- Test: tests/test_opencode_recovery_evidence.py
- Test fixtures: tests/fixtures/opencode-recovery/

**Interfaces:**
- RecoveryTarget(repository, pr, run_id, run_attempt, head_sha, base_sha).
- validate_evidence(target, bundle, workspace) returns validated original claim, FinalizeRequest, canonical binding and evidence digests.
- read_artifact(metadata, payload, expected_name, run_id) returns an exact bounded filename-to-bytes mapping.
- replay.js consumes a JSON fixture bundle on stdin and produces JSON with canonical body, state, outcome and finding IDs. All API methods are read-only evidence lookups; every unsupported call throws. No model executable is available.

- [x] Write a sanitized synthetic real-git fixture plus malformed artifact/job/check cases. Check missing implementation fails.
- [x] Reject malformed/duplicate JSON, unsafe ZIP entries/size, expired/missing artifacts and wrong API ID/name/digest/run.
- [x] Validate exact original failed run/PR bindings, sealed handoff/claim/candidate and successful publication/outcome steps followed by failed finalize.
- [x] Verify original attestation and canonical bytes, original scope and hermetic recomputed full diff.
- [x] Replay the approved original workflow canonicalizer up to its first mutation boundary in a tokenless Node subprocess; match canonical body byte-for-byte. Pin accepted replay source digest; unsupported source refuses.
- [x] Derive original metrics and findings only from successful validated original evidence, and test zero calls.
- [x] Run focused evidence tests and existing canonicalization behavior cases.

### Task 3: Recovery receipt and ordinary collector integration

**Files:**
- Create: .github/actions/recover-opencode-review/receipt.js
- Modify: .github/workflows/opencode-auto-review.yml
- Test: tests/test_opencode_recovery_receipts.py
- Extend if necessary: tests/test_review_workflow_logic.py

**Interfaces:**
- Receipt schema 1 carries repository/pr; original run/attempt/head/base/full_diff and immutable provenance; canonical comment/check IDs and body/state digests; original artifact/claim digests; finalized invocation digest; recovery driver run/attempt/head/caller/central workflow SHA.
- Named Check: automation/opencode-finalization-recovery.
- Named reusable recovery workflow: jhw7500/automation/.github/workflows/opencode-recover-finalization.yml.
- Named recovery job: opencode-recover-finalization.
- Normal verifier returns trusted original canonical IDs only for exact completed successful driver run/job and matching original provenance/comment/ledger entry; generation ordering remains original run/attempt.

- [x] Define exact receipt keys once and test fabricated, failed, cancelled and mismatched driver/original/comment bindings.
- [x] Implement bounded server-first recovery run/Check discovery, exact attempts/jobs, and immutable ledger invocation digest validation.
- [x] Integrate verification into prepare and live canonicalization, retaining the old path's checks and unresolved-evidence fail-closed behavior.
- [x] Verify normal failed run is still rejected without a valid receipt and valid recovered review supports zero-call reuse.
- [x] Verify later unrelated ledger handoff changes do not break a receipt; changed original invocation does.
- [x] Run receipt and affected OpenCode behavioral suites.

### Task 4: Serialized recovery transport and workflow entrypoint

**Files:**
- Create: .github/actions/recover-opencode-review/transport.py
- Create: .github/workflows/opencode-recover-finalization.yml
- Modify: examples/baseline-workflows/.github/workflows/opencode-auto-review.yml
- Test: tests/test_opencode_recovery_transport.py

**Interfaces:**
- Transport CLI reads validated driver env plus PR/original run/attempt/expected HEAD/base inputs; only supported GitHub REST reads and bounded ledger/Check writes.
- Uses validate_evidence then budget.recover_finalize.
- Outcomes: finalized, already_recovered, or bounded refusal with zero provider calls.

- [x] Write HTTP-fake tests for PATCH-before-timeout, committed-PATCH-response-loss, unchanged/conflicting/unknown readback and receipt response loss.
- [x] Collect bounded fresh evidence and dismissal permissions; pin trusted helper checkout to the actual referenced central SHA; never execute PR source.
- [x] Recheck live PR/canonical/ledger immediately before mutation. PATCH at most once; on uncertainty reread once. Exact already-finalized target performs no PATCH.
- [x] Publish exact recovery receipt only after ledger readback; verify readback; completed run/job required for later trust. Verify an existing completed matching receipt before requiring expired artifacts on no-op.
- [x] Add mutually exclusive caller dispatch route with strict recovery inputs and no model job/credentials.
- [x] Use existing per-PR concurrency group and cancel-in-progress false. Add private checkpoint artifact and bounded failure output.
- [x] Run transport/workflow gate tests and actionlint/Bash syntax.

### Task 5: Release contract, documentation and whole-change validation

**Files:**
- Modify: scripts/workflow_release_inventory.py
- Modify: scripts/verify_workflow_release.py
- Modify: tests/test_verify_workflow_release.py
- Modify relevant release inventory/bundle/catalog tests as necessary
- Modify: docs/workflows/contracts.md

**Interfaces:**
- v1.76 enables and seals recovery artifacts/workflows/caller interface; v1.74/v1.75 immutable acceptance stays unchanged.
- All new executable files are owned by the closed release inventory.

- [x] Add v1.76 positive acceptance and mutation tests for recovery gates, helper bytes, new permissions, normal receipt trust and caller mutual exclusion.
- [x] Update inventory/verifier without accepting recovery content as old-release content.
- [x] Document original vs driver provenance, zero calls, idempotency, CAS limits, expiry and caller adoption.
- [x] Run related suites, full pytest, YAML/actionlint, Bash syntax and git diff --check.
- [x] Independent review of spec compliance and code quality; address blocking findings and rerun affected tests.
- [x] Report review readiness with exact test evidence and remaining live-release/canary boundary. Do not commit or publish.
