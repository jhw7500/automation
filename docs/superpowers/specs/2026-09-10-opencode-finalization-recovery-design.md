# #179 OpenCode original-attempt finalization recovery — design for review

Status: implementation design approved by the user's go on 2026-09-10.
Date: 2026-09-10. Source tree: 6dc5934ecd6bd1c298e8b7fb869b412b0828ff7c.
Issue: https://github.com/jhw7500/automation/issues/179

This is an advisory work record. Task ownership comes only from the supported lifecycle.
The approved #169 finish and #179 start both succeeded; see the private .review/issue-179/task-switch.json in the main checkout.

## Result to deliver

Recover a review whose provider, candidate validation, canonical publication and outcome
resolution succeeded, but whose budget finalization failed. Authenticate the original
execution, finalize its existing invocation once, and publish a separately authenticated
recovery receipt. Recovery consumes zero provider calls and zero new review rounds.

The original invocation keeps its run ID, attempt, caller/central-workflow provenance,
HEAD/diff identity, estimated input usage and round number. Actual usage comes from its
authenticated candidate. The recovery driver has its own run/attempt and never appears
as a provider invocation. The original failed Actions run remains failed.

## Evidence and reproduced cause

The archived 21-file evidence manifest passed size/SHA-256 verification. The three
OpenCode ZIP digests also match their API metadata; the canonical comment bytes match
the original attestation. A fresh read at 2026-09-10T07:52:45Z confirmed the original
attempt remains failed, the PR remains open/unmerged at its original HEAD/base, and
the artifacts are not expired.

| Field | Value |
| --- | --- |
| Consumer | jhw7500/wlan-package PR 326 |
| Original run / attempt | 34421303503 / 1 |
| Original workflow SHA | eb460b7e547a85f831820f8f25d30f134b2c6254 |
| PR HEAD | 8afe40e1194dd8c748abd7b6f6e504f7f633bde8 |
| PR base / sealed merge base | 9abf6b5650e020ee56d1bf2e9d073893ffa9cd83 |
| Full diff SHA-256 | 7cf2f1ba122d6c7cc00c0209f2924cfb3bf7aabef1c8c06b0dccc8551bbe8092 |
| Budget comment | 5610734298 |
| Canonical comment | 5610756061 |
| Original attestation Check | 102697694955 |
| Original provider usage | 1 call / 45 seconds |
| Handoff artifact | 10130993045 |
| Claim artifact | 10130993536 |
| Candidate artifact | 10131032389 |

The original job evidence reports success for canonical publication and outcome
resolution, then failure for Finalize OpenCode review budget. Its log records an
API GET timeout. The ledger remains claimed.

Pure-function reproduction on the new #179 worktree, without API/provider calls:

| Operation | Observed result |
| --- | --- |
| Finalize original claim as attempt 2 | state_invalid / invocation_not_claimed |
| Whole rerun without an authenticated receipt | duplicate_head |
| Finalize original attempt 1 with original metrics | finalized |
| Repeat that identical finalization after the write | state_invalid / invocation_not_claimed |

These are diagnosis assertions, not tests of an implemented recovery feature.

Relevant current boundaries:
- Budget helper finalize() matches the exact invocation tuple and rejects an already
  finalized entry. Its provenance validation does not itself require run success.
- action.yml constructs identity from the current GITHUB_RUN_ID/GITHUB_RUN_ATTEMPT.
- OpenCode's normal receipt collectors additionally require successful original
  run/canonicalizer-job completion; a successful Check alone cannot bypass that.
- The existing ledger CAS is a prewrite read-and-compare followed by an ordinary
  PATCH. It is not an atomic GitHub conditional-write primitive.

## Approaches considered

| Approach | Decision | Reason |
| --- | --- | --- |
| Separate recovery workflow and receipt | Recommended | Explicit original/driver identities; no provider credentials |
| Relax failed-run reuse / substitute current attempt | Reject | Loses authenticated execution identity |
| Local operator ledger-edit command | Reject | Bypasses Actions serialization and canonical receipt trust |

## Proposed architecture

A central reusable opencode-recover-finalization.yml runs only from an explicitly
selected workflow_dispatch recovery path in the existing consumer caller. It receives
PR number, original run ID/attempt, expected HEAD and expected base. Recovery inputs
and force_review are mutually exclusive; malformed recovery inputs fail before any
normal provider job is eligible. The recovery job has actions:read, contents:read,
issues:write, pull-requests:read and checks:write, with no model secret or model CLI.

The recovery workflow uses the existing exact per-repository/per-PR OpenCode
concurrency group and cancel-in-progress:false. It adds no Project Control Claim,
Registry lock, or operator lock. Participating normal review and recovery workflows
therefore serialize. A synchronize event can still cancel a run through the existing
normal workflow policy; every incomplete recovery remains safe to inspect and retry.

Executable helpers come from the exact API-verified referenced central workflow
commit in a separate trusted checkout. The target PR checkout and downloaded
artifacts are data only; no PR hook, action, or script is executed. Run provenance,
input shape, caller identity and central SHA are checked before privileged writes.

Split responsibilities:
1. Bounded API/artifact collector: fresh GitHub facts and exact downloaded bytes.
2. Pure recovery validator/transition: evidence validation and deterministic mutation.
3. Serialized publisher: recheck, write, readback and recovery attestation.
4. Normal receipt collectors: authenticate the distinct recovery receipt when present.

The shared budget helper gains a separate recovery transition. Its normal claim and
finalize request identity rules remain intact. Recovery does not synthesize a claim
or append an invocation.

## Required evidence before any write

- Exact original run and attempt from GitHub, completed with failure; original
  repository, PR association, caller path/event, server workflow head and referenced
  central workflow SHA must agree with the sealed handoff.
- Exact bounded job/step evidence: successful prepare, model, canonical publication
  and outcome resolution; finalization is the failed functional step. No cancelled,
  in-progress, missing or ambiguously duplicated producer/canonicalizer job.
- Exact original run PR bindings must include the expected PR, repository, HEAD
  and base; missing or conflicting server bindings refuse recovery. Live open PR
  HEAD and base must also equal those explicitly expected values. Recompute
  the merge base and full diff under the existing hermetic diff contract; match the
  sealed merge base, review scope and full-diff bytes/hash. The base guard is checked
  against the server run binding; it is not falsely described as a field sealed in
  the original handoff, which contains only the merge base.
- Unique non-expired original artifacts, exact names/IDs/run binding and API digest.
  ZIP bytes must match those digests. Reject duplicate entries, paths, symlinks,
  extra files and excessive compressed/uncompressed sizes before reading payloads.
- Exact handoff schema and file hashes, claim checkpoint, allowed original claim,
  candidate nonce/contract validation, candidate claim-checkpoint digest, identity,
  review digest, successful outcome and bounded original metrics.
- Server-discovered original canonical Check and exact canonical comment bytes:
  bot author, check app/name/external ID, comment/state digests, original attempt,
  original central SHA, successful HEAD and full-diff hash all match.
- Recompute the candidate-to-canonical validation using the existing canonicalization
  contract and sealed scope/context. Neither candidate-reported success nor job
  success alone authenticates a final review.
- Fresh budget comment parses under the existing schema. The selected invocation
  must match the original claim's immutable fields and be the current relevant
  generation. Conflicting/newer work, changed canonical bytes or unresolved newer
  provenance stops recovery. Refresh dismissal provenance using the existing rules.

## Write, response loss and repeat behavior

```text
original claim + authenticated original review
                     |
                     v
       validate original evidence + live state
                     |
                     v
       recheck PR / canonical / ledger bytes
                     |
                     v
       finalize original invocation once
                     |
                     v
           read back exact result
                     |
                     v
     separate recovery Check + driver completion
                     |
                     v
          eligible authenticated reuse
```

For claimed state, use the original candidate metrics and original invocation tuple
to construct the expected finalized entry. Preserve all unrelated ledger records.
An identical already-finalized target is a successful no-op only after the same
original evidence checks and original immutable/finalized fields match. Any other
finalized value is a conflict.

Immediately before mutation, reread PR HEAD/base, canonical comment/Check and budget
comment identity/body. Any drift refuses without writing. Use Actions serialization
plus this existing comparison discipline; do not claim protection against arbitrary
uncoordinated external writers.

If PATCH times out, reread once to distinguish the exact committed result from
unchanged/conflicting/unknown state. Do not blindly issue another PATCH. A later
explicit recovery run can validate and continue idempotently. If the ledger write
succeeded but receipt publication failed, the next recovery performs no ledger write
and completes the missing receipt stage after validation.

The separate recovery receipt binds the original invocation and evidence digests,
canonical comment/Check and unchanged original review identity, plus the actual
recovery driver run/attempt/caller/central SHA. A receipt is reusable only when its
exact recovery run and recovery job have completed successfully. Receipt publication
response loss uses bounded server discovery and exact payload matching, with no
duplicate ambiguous receipt accepted.

Normal prepare and live canonicalization discover these receipts from bounded,
server-authored recovery workflow runs. They verify both provenance chains. They
continue to reject ordinary failed-run review receipts without a valid recovery
receipt. Ordering uses the original provider generation, not the later recovery
run ID. Later ledger handoff updates must not invalidate a receipt solely because
the entire ledger body changed; bind the original finalized invocation instead.

Checks retain the existing trust ceiling: they are not signatures against an
unrelated workflow independently granted checks:write.

## Expiration policy and this canary

Initial recovery requires live, non-expired original artifacts and matching downloads.
Archived ZIP/API responses alone are not authorization after expiry. If evidence
has expired or disappeared, return a bounded artifact-expired/unavailable refusal,
perform no writes, and retain the original consumed claim. There is no upload of an
old ZIP as a substitute and no refund/override fallback.

An already completed, authenticated recovery receipt can support later normal reuse
after original artifact expiry, within the normal bounded discovery policy. That is
a completed trust receipt, not a late attempt to create one from local archives.
An explicit repeat with that same fully verified completed receipt returns an
already-recovered no-op; it cannot authorize a different target or any new write.

Current API expiry times:
- Handoff: 2026-09-11 09:27:07 KST.
- Candidate: 2026-09-11 09:28:38 KST.
- Claim checkpoint: 2026-09-17 09:27:08 KST.

The consumer default branch is master and already contains the caller path with
workflow_dispatch. A reviewed operator branch at that same path can select the new
immutable recovery workflow while keeping PR 326 HEAD and base untouched. GitHub
documents selecting a branch/ref for a manual run; live deployment must still
validate that exact caller, inputs and central pin before dispatch. Creating that
branch, publishing the recovery release and dispatching are later concrete actions,
not performed or implied by this diagnosis.

## Implementation and verification scope

- New central recovery workflow and a focused recovery helper/transport.
- Separate recovery transition in review_invocation_budget.py and narrow action
  integration if needed; no broad weakening of the ordinary transport.
- Existing OpenCode caller template: explicit mutually exclusive recovery path.
- Existing OpenCode prepare/live receipt collectors: recovery receipt support.
- Workflow release inventory/verifier and mutation tests: seal the new contract in
  the next release (proposed v1.76), preserve immutable v1.74/v1.75 verification.
- docs/workflows/contracts.md: identity, retry, serialization, receipts, expiry and
  operator entrypoint contract.

Tests must exercise outcomes, with mocked API/provider transport and zero provider
calls asserted:
- Timeout before PATCH, after committed PATCH, and during receipt publication.
- Identical repeat, failed-job rerun identity, concurrent queued recovery and changed
  state between observation and write.
- Wrong original attempt/claim, HEAD/base/diff drift and superseding generations.
- Forged or missing jobs/attestation/comment/artifact, digest mismatch, malformed ZIP,
  unverified success, expired artifacts and unavailable API.
- Recovery driver failure/cancellation cannot authorize reuse; successful recovery
  permits reuse without relabeling provider provenance or spending a round.
- Receipt ordering/discovery limits and old successful/failed-run trust behavior.
- Caller mode separation and zero model credentials/jobs in recovery mode.
- Release acceptance plus security mutation tests; YAML/actionlint and Bash checks.
- Relevant existing budget/action/OpenCode suites; full repository suite before
  reporting the implementation ready for review.

## Current stop boundary

The user authorized Task transition and #179 work; that transition is complete.
The implementation design adds a new authenticated workflow/receipt interface and
received design approval in the following user go; implementation may proceed.
No code, commit, PR, provider call, canary rerun, budget mutation or rollout has been
performed in this diagnosis. Keep the active #179 Claim; do not recreate it.

After this design is approved: turn it into the required implementation plan and
implement/test in the existing #179 worktree. Do not ask again to start the Task.

## Sources

Local code: .github/actions/review-invocation-budget/{action.yml,review_invocation_budget.py},
.github/workflows/opencode-auto-review.yml, tests/test_review_invocation_budget.py,
examples/baseline-workflows/.github/workflows/opencode-auto-review.yml,
docs/workflows/contracts.md and scripts/verify_workflow_release.py.

GitHub concurrency:
https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency

GitHub manual branch/ref dispatch:
https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow

GitHub exact attempt and artifact APIs:
https://docs.github.com/en/rest/actions/workflow-runs
https://docs.github.com/en/rest/actions/artifacts
