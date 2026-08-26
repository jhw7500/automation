# Task 6 implementation report

Status: DONE

Commit subject: `feat(review): bound OpenCode review sessions`

## Implemented

- Preserved OpenCode's exact schema-2 review comment and Check Run attestation authority. Budget finalization derives only from the real canonical publication/attestation result.
- Added the durable claim after immutable full-diff preparation with reviewer `opencode`, fixed route `zai-coding-plan/glm-4.7`, effort `final-review/default`, and the exact four-key authenticated-review object containing at most eight active canonical `RVW-<12hex>` IDs.
- Granted `issues: write` only to `opencode-prepare` because the shared claim action performs issue-comment CAS; retained the tokenless model job's exact `permissions: {}` and 10-minute timeout.
- Sealed the claim checkpoint and SHA-256 into the existing prepare handoff allowlist, exposed the required prepare outputs, and validated exact claim identity in both the tokenless model job and privileged canonicalizer.
- Initialized private metrics before handoff download, cache, install, or provider work. Every OpenCode CLI session durably increments the shared mode-0600 counter immediately before execution using an exclusive temporary file, file fsync, atomic rename, and parent-directory fsync. The optional format-only repair shares the same two-call cap; a third call is refused before CLI execution.
- Added an always+allowed materialization path that emits exact schema-1 `candidate.json` for zero-call dependency failure, provider failure, contract failure, or success. It records truthful count, elapsed seconds, fixed actual route, outcome/reason, full claim identity, and review SHA/null; successful mode adds `review.md`, while failure mode contains only the envelope.
- Made canonicalization treat the candidate artifact as untrusted until GitHub API artifact ID/digest, exact regular-file inventory, exact keys/types/identity/metrics/route, sealed claim digest, and mode-aware review hash all validate. Symlinks, extras, malformed data/modes, mismatched claim identity, and bad metrics fail closed.
- Preserved authenticated unchanged reuse as a zero-call schema-2 success. Duplicate or otherwise denied claims upload only the prepare checkpoint, skip the model job, and cannot manufacture a schema-2 publication.
- Derived the deterministic `success`, `quality_filtered`, `provider_failure`, `checkpoint_failure`, and `wall_time_exhausted` budget outcomes after canonical publication, then finalized the exact allowed claim and uploaded the final checkpoint. No cross-reviewer or alternate-model fallback was added.
- Added only the exact actionlint exception required for the local budget action reference.

## TDD evidence

RED:

```text
rtk python3 -m pytest tests/test_review_workflow_logic.py -q -k 'opencode and (budget or invocation or timeout or handoff)'
6 failed, 1 passed, 1515 deselected
```

The failures were the expected missing claim/output/handoff contracts, tokenless allow guards and timeout, durable shared counter, exact candidate envelope, and post-publication finalization behavior.

GREEN (final focused run):

```text
rtk python3 -m pytest tests/test_review_workflow_logic.py -q -k 'opencode and (budget or invocation or timeout or handoff)'
9 passed, 1520 deselected in 1.22s
```

Dynamic canonical harness coverage:

```text
rtk python3 -m pytest tests/test_review_workflow_logic.py -q -k 'opencode and (checkpoint_requires_cli_success or failure_preserves_prior_success or unchanged_advances or unavailable_budget_refusal or candidate_metrics_and_claim_identity)'
12 passed, 1517 deselected in 3.19s
```

This executes sanitized success, provider failure with prior-state preservation, authenticated unchanged reuse, unavailable denied/no-publication behavior, and six malformed envelope type/range/route/claim/mode/extra-key cases.

## Verification

- Four expectation-only regressions after the first broad run: `4 passed, 1525 deselected in 0.93s` after updating assertions for the required allow guard, `issues: write`, and directory candidate upload.
- OpenCode/actionlint regression: `1192 passed, 337 deselected in 449.67s`.
- Full workflow logic: `1529 passed in 495.47s`.
- Exact required non-Task7 suite: `2085 passed, 48 subtests passed in 524.15s`, excluding only `tests/test_workflow_release_bundle.py` and `tests/test_verify_workflow_release.py`.
- Digest-verified actionlint 1.7.12: archive SHA-256 `8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8` verified; the full CI workflow set produced no diagnostics.
- YAML parse: all 16 workflow YAML files parsed successfully.
- Ruff changed-line gate: 439 changed Python lines, zero Ruff diagnostics. The file's eight whole-file diagnostics are pre-existing and outside added lines.
- `git diff --check`: exited 0.

## Files changed

- `.github/workflows/opencode-auto-review.yml`
- `.github/actionlint.yaml`
- `tests/test_review_workflow_logic.py`
- `.superpowers/sdd/2026-08-26-review-invocation-budget/progress.md`
- `.superpowers/sdd/2026-08-26-review-invocation-budget/task-6-report.md`

## Self-review

- Security: the model job remains tokenless; all handoff/candidate inputs require exact API-backed artifact identity, bounded regular files, exact inventory, and sealed digest/identity matches before use.
- Accounting: both possible CLI calls pass one durable counter wrapper, persist before provider execution, refuse a third call, and retain count after raised requests. Pre-run dependency failures finalize with zero calls.
- Publication: schema-2 success still requires the existing canonical comment and exact Check Run attestation path. Failed allowed claims publish only truthful failure state; denied claims publish nothing; authenticated unchanged reuse remains the sole no-model success path.
- Scope: no release inventory, verifier, tag, fleet default, contracts documentation, Task 7 scripts, or Task 7 tests changed.

Concerns: none. The eight whole-file Ruff findings predate Task 6; all changed lines are clean.
