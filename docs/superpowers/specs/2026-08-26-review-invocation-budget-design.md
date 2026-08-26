# Review Invocation Budget and Handoff Design

Date: 2026-08-26
Status: approved in chat; implementation not started
Issue: https://github.com/jhw7500/automation/issues/52

## 1. Decision

Claude, Gemini, and OpenCode automated PR reviews will share one deterministic invocation-budget
state machine. Each reviewer will keep a separate, workflow-owned ledger comment so concurrent
reviewers cannot overwrite one another. The ledger will claim an invocation before any provider
process starts and finalize that claim after the provider and canonical review checkpoint finish.

The existing review comments remain the only authority for review findings and merge status:

- Claude and Gemini retain their exact schema-3 review envelopes and quality counters.
- OpenCode retains its existing schema-2 attestation and review publication contract.
- `/jhw:ship` continues to classify those review comments and CI signals without parsing the new
  budget ledger as review evidence.

The new ledger is authority only for whether another model invocation may start and for the
checkpoint needed to hand the work to another session. This separation avoids changing the exact
review-state schemas completed by issues #41 through #43.

## 2. Motivation and current gap

The v1.46.2 review workflows already skip a provider when an authenticated successful review has
the same full-diff hash. They also bound Gemini's provider requests to three and OpenCode's
format-only repair to one extra CLI invocation. They do not cover these cases:

1. A provider failure leaves no successful full-diff hash, so a manual rerun at the same head can
   call the same reviewer again.
2. Review rounds are not counted across distinct workflow runs, so repeated pushes can start an
   unbounded number of model sessions.
3. Claude and OpenCode do not expose a common wall-time, call-count, or estimated-input checkpoint.
4. Provider failures are classified after the call but are not durable input to a later invocation
   decision.
5. When a review budget is exhausted, the next session must reconstruct the state from Actions
   logs, review comments, and conversation history instead of one bounded checkpoint.

Shell or API polling does not itself consume model requests. The budget gate therefore controls
provider invocations, while `/jhw:ship` continues to perform deterministic polling and summarizes
the terminal signals once.

## 3. Goals

1. Never invoke the same reviewer twice for one PR head SHA, including after provider failure,
   timeout, cancellation, or invalid model output.
2. Never invoke a reviewer for a new head whose effective full-diff hash was already claimed by
   that reviewer.
3. Count only provider-approved, distinct effective diffs as review rounds and stop automatically
   after two rounds by default.
4. Enforce per-round model-call, wall-time, and estimated-input limits before or immediately after
   the relevant operation.
5. Record reviewer, model route, effort, call unit, call count, round, elapsed time, input estimate,
   outcome, and stop reason in a reproducible run artifact and durable PR checkpoint.
6. Prevent provider failure from opening an unbounded retry or cross-model fallback cascade.
7. Leave a bounded handoff containing the exact head, effective diff hash, consumed budget, and
   remaining authenticated finding IDs when no further automatic review may run.
8. Preserve every v1.46.2 review-input, finding-quality, publication, and merge-gate invariant.

## 4. Non-goals

- Replacing the semantic reviewer or proving that a model finding is correct.
- Combining Claude, Gemini, and OpenCode into a voting or fallback chain.
- Changing Codex or Gemini Assist GitHub App behavior.
- Letting a budget stop imply that a PR is clean or safe to merge.
- Replacing `/jhw:ship` review classification or deterministic polling.
- Charging ordinary CI, canonicalization, API polling, or deterministic diff preparation as model
  calls.
- Inferring exact billed tokens when a provider does not expose trusted usage metadata.
- Releasing or rolling out a new immutable fleet version as part of issue #52. Release and fleet
  rollout remain separate, explicit operations after the merged implementation is validated.

## 5. Definitions

### 5.1 Reviewer invocation

A reviewer invocation is one provider-facing unit:

| Reviewer | Call unit | Per-round hard cap |
|---|---|---:|
| Claude | one `claude-code-action` review session | 1 |
| Gemini | one `generate_content` API request | 3 |
| OpenCode | one `opencode run` CLI session | 2 |

Gemini primary retries and its configured fallback share the same three-request cap. OpenCode's
second call is permitted only for the existing substance-preserving format-only repair. No
provider failure starts a different reviewer; the three reviewers remain independent channels
triggered by the PR event.

### 5.2 Effective diff

The effective diff identity is the SHA-256 of the exact immutable `review-full.diff` prepared by
`prepare-review-diff`, regardless of whether Claude or Gemini later read the full or delta file.
The same 64-hex hash therefore means the same complete PR change set for one reviewer.

### 5.3 Round

A round is consumed only after the budget action has durably claimed a previously unseen
`(reviewer, head_sha, full_diff_sha256)` tuple and returned `allow-invocation=true`. Diff
preparation failure, authenticated unchanged reuse, duplicate head, duplicate effective diff, and
budget refusal use zero model calls and do not append a new round.

### 5.4 Estimated input

Before a claim, the workflow provides the action with the workflow-owned input files that the
provider will consume. The estimate is:

```text
estimated_input_tokens = ceil(sum(input_file_bytes) / 4) + 20,000
```

The fixed reserve covers provider instructions, PR metadata, and bounded prior/human context. It
is intentionally conservative and is a budget unit, not a billing claim.

## 6. Default and hard budgets

Each reviewer has an independent PR ledger with these defaults:

| Budget | Default |
|---|---:|
| automatic rounds | 2 |
| estimated input per round | 200,000 tokens |
| estimated input across two automatic rounds | 400,000 tokens |
| provider wall time per round | 600 seconds |
| Claude calls per round | 1 session |
| Gemini calls per round | 3 requests |
| OpenCode calls per round | 2 sessions |

The same-head and same-effective-diff prohibitions are absolute and cannot be overridden. The
per-round input, call, and wall-time caps are also hard safety limits.

A repository collaborator may grant one additional distinct-diff round by applying the
`review-budget-override` PR label and manually rerunning the stopped reviewer. The action consumes
the latest unconsumed `labeled` timeline-event ID and records it in the ledger. A persistent label
cannot authorize repeated rounds, and one reviewer may consume at most one override round. The
override raises only the round and aggregate-input ceilings to three rounds and 600,000 estimated
tokens; all per-round hard caps remain unchanged.

## 7. Architecture

### 7.1 Shared action

Add a release-owned composite action:

```text
.github/actions/review-invocation-budget/action.yml
.github/actions/review-invocation-budget/review_invocation_budget.py
```

The Python helper owns validation, state transitions, deterministic JSON rendering, summaries,
and checkpoint files. The composite wrapper owns GitHub API transport through `gh api`. It passes
API responses as files to the helper and never evaluates PR-controlled text as shell code.

The action has two modes:

- `claim`: validate the prior ledger, calculate the input estimate, decide whether a provider may
  run, and persist the claim before returning `allow-invocation=true`.
- `finalize`: match the current run's existing claim, record actual calls and elapsed time, attach
  the review outcome and remaining finding IDs, and persist the final checkpoint.

Every mode writes a complete local JSON checkpoint even when it refuses a model invocation. Each
workflow uploads that file with a unique run-ID/run-attempt artifact name and copies the concise
visible summary to `GITHUB_STEP_SUMMARY`.

### 7.2 Per-reviewer ledger comments

The exact markers are:

```text
<!-- automation:review-invocation-budget:claude:v1 -->
<!-- automation:review-invocation-budget:gemini:v1 -->
<!-- automation:review-invocation-budget:opencode:v1 -->
```

The next line is one exact compact JSON state marker:

```text
<!-- automation-budget-state:{...} -->
```

Only an exact marker at the start of a `github-actions[bot]` PR comment is eligible. Zero matching
comments means first use. More than one, invalid JSON, unknown fields, inconsistent counters, an
untrusted author, or unverifiable historical run provenance fails closed before a provider call
and does not replace any comment.

The state contains:

```json
{
  "schema": 1,
  "repository": "owner/repo",
  "pr": 52,
  "reviewer": "claude",
  "budgets": {
    "max_rounds": 2,
    "max_override_rounds": 1,
    "max_calls_per_round": 1,
    "max_wall_seconds_per_round": 600,
    "max_estimated_tokens_per_round": 200000,
    "max_estimated_tokens_total": 400000
  },
  "invocations": [],
  "consumed_override_event_ids": [],
  "last_decision": {},
  "handoff": {}
}
```

`invocations` contains at most three entries: two automatic rounds plus one explicit override.
Each entry has exact run identity, head and full-diff hashes, round, override event if any, model
route, effort, call unit, call count, estimated input, elapsed seconds, status, outcome, stop
reason, and remaining finding IDs. `last_decision` records the current run even when it used zero
calls. Aggregate values are derived from entries and validated rather than stored independently.

### 7.3 Provenance and compare-and-swap

The helper validates historical invocation run IDs against the Actions run-attempt API. A record
must belong to the current repository and PR, use the expected reviewer workflow, and have the
recorded head SHA. A current `claim` may refer to its own in-progress run; older claims must refer
to a terminal run. Cancelled or timed-out historical runs remain consumed because a provider may
already have received the request.

Immediately before creating or updating a ledger comment, the wrapper refetches both the PR head
and the comment. It requires the head to equal the claimed head and the comment body to equal the
body from which the transition was computed. A mismatch is a compare-and-swap failure: no comment
is changed and no provider may start. Per-reviewer/per-PR workflow concurrency remains enabled as
an overlap reduction, not as the state authority.

### 7.4 Claim decisions

The claim state machine applies these decisions in order:

1. Invalid identity, state, provenance, PR head, or diff input: `state_invalid` or
   `diff_unavailable`; no call and fail closed.
2. `diff-mode=unchanged` backed by the existing authenticated successful review:
   `authenticated_reuse`; no call and the existing review workflow advances through its current
   unchanged-success path.
3. Existing invocation with the same head: `duplicate_head`; no call regardless of prior outcome.
4. Existing invocation with the same full-diff hash: `duplicate_effective_diff`; no call regardless
   of prior outcome.
5. Per-round estimated input above 200,000 tokens: `input_budget_exhausted`; no call.
6. Two rounds already consumed: consume one eligible override event or return
   `round_budget_exhausted`; no call.
7. Aggregate estimate above the applicable 400,000/600,000 cap: `total_usage_budget_exhausted`;
   no call.
8. Otherwise append a `claimed` entry and return `allow-invocation=true`.

A successful duplicate/reuse may finish the workflow successfully only when the existing
authenticated review state already proves coverage of the same effective diff. A duplicate whose
claimed round failed, timed out, or never finalized remains a failed review gate. Budget state
never manufactures a clean review.

### 7.5 Finalization and failure semantics

Finalization is an `always() && !cancelled()` workflow step. It requires the current run's claimed
entry and records:

- the actual provider-call count;
- the provider/model route and effort actually used;
- provider-step elapsed seconds;
- `success`, `provider_failure`, `quality_filtered`, `checkpoint_failure`, or
  `wall_time_exhausted` outcome;
- a bounded list of active `RVW-<12hex>` finding IDs from the authenticated canonical result; and
- the deterministic stop reason.

Actual calls above the reviewer cap or elapsed time above 600 seconds finalize as a budget-contract
failure and fail the workflow. A timeout at the job boundary may prevent finalization; the durable
pre-call `claimed` entry still blocks another invocation and its run artifact/log provides the
terminal evidence.

Provider failure never consumes another reviewer as fallback. A later distinct head may use the
next round; the same head or same effective diff may not. Gemini's eligible configured fallback
remains inside the same claimed round and shares the three-request counter.

### 7.6 Handoff

Every zero-call terminal refusal and every finalization renders a handoff object containing:

- repository, PR, reviewer, current head, and full-diff hash;
- current run ID and attempt;
- rounds and override rounds consumed;
- calls, estimated tokens, and wall time consumed per round;
- current decision, outcome, and stop reason;
- last authenticated successful review head/hash when available; and
- remaining authenticated finding IDs last observed by this reviewer.

The visible ledger comment explains that budget exhaustion is not approval and links to the
current Actions run. The JSON artifact is sufficient for a new session to decide whether it may
poll, request explicit override, fix remaining findings, or stop without replaying conversation
history.

## 8. Workflow integration

### 8.1 Claude

After `prepare-review-diff`, claim the Claude ledger before checkout/model execution. Condition the
Claude artifact reset, `claude-code-action`, and canonicalizer on `allow-invocation=true`, except
that the existing authenticated unchanged path remains available without a model. Add a 10-minute
job timeout. Record the resolved model or `claude-code-action-default`, effort
`final-review/default`, one session when the action starts, and its elapsed time. Finalize after the
review-state upsert so active canonical IDs and the review checkpoint result are known.

### 8.2 Gemini

Claim after deterministic diff preparation and before dependency installation/provider execution.
Count every call to `generate_content` before issuing it and persist the counter even when the
request raises. Record the primary model, any fallback model actually attempted, and the configured
thinking level. The current 450-second subprocess deadline, 200-second request deadline, and
10-minute job deadline remain; all primary retries and fallback requests share the hard cap of
three. Finalize after the schema-3 upsert.

### 8.3 OpenCode

Claim in `opencode-prepare` while the immutable full diff and context are available, before the
sealed handoff is uploaded. Add `allow_invocation` and budget-checkpoint identity to the sealed
handoff and job outputs. Give `opencode-review` a 10-minute timeout and condition CLI installation
and execution on the claim. Increment a durable counter immediately before each `opencode run`,
including the optional format-only repair. Finalize in `opencode-canonicalize`, after the existing
attestation and canonical review publication checks.

## 9. Model and effort routing

Diff identity, duplicate detection, round classification, provider-failure classification, and
handoff construction are deterministic and use no model. The provider invocation is therefore the
only `final-review` phase:

- Claude records the configured/default model and `final-review/default` effort.
- Gemini records primary/fallback model usage and the exact low/medium/high thinking level.
- OpenCode records `zai-coding-plan/glm-4.7` and `final-review/default` effort.

No second model is introduced for exploration or classification. This separates cheap,
deterministic routing from the expensive semantic judgment without creating a new fallback path.

## 10. Tests and release contract

### 10.1 Pure state-machine fixtures

Add fixed tests for:

- first valid claim and successful finalization;
- same SHA after success, provider failure, and unfinalized cancellation;
- new SHA with the same full-diff hash;
- two distinct automatic rounds followed by deterministic exhaustion;
- one collaborator-label override event consumed exactly once;
- hard per-round input, call, and wall-time limits;
- Gemini's primary retry plus fallback sharing three requests;
- OpenCode's format-only repair sharing two sessions;
- a quality-filtered false positive finalizing successfully without opening another round;
- provider failure preserving remaining finding IDs without cross-model fallback;
- invalid, duplicate, oversized, stale-head, or provenance-mismatched ledger state failing closed;
  and
- checkpoint JSON alone reconstructing the next-session decision.

Every production behavior is introduced through a failing test first.

### 10.2 Workflow and release verification

Workflow-logic tests require all three provider steps to depend on the claim output, require
pre-call claim ordering, check counters and timeouts, and prove finalization runs on ordinary
success/failure paths. Existing unchanged-review, stale-state, quality-corpus, and OpenCode
attestation tests remain unchanged and green.

The release inventory gains the new action directory as an exact owned dependency. The release
verifier rejects missing/extra files, mutable action references, weakened action inputs/outputs,
and mutations that remove duplicate-head, duplicate-diff, round, input, call, wall-time,
provenance, or compare-and-swap gates.

Validation before PR creation consists of focused red/green tests, all affected workflow suites,
Ruff, YAML parsing, actionlint, release-verifier mutation tests, and the complete Python suite.

## 11. Operational behavior

| Situation | Model calls | Review-state effect | Budget/handoff effect |
|---|---:|---|---|
| authenticated unchanged success | 0 | existing success may advance to current head | records authenticated reuse |
| same reviewer and head already claimed | 0 | preserves prior success/failure meaning | `duplicate_head` |
| new head, identical effective diff already claimed | 0 | preserves prior success/failure meaning | `duplicate_effective_diff` |
| first or second distinct diff within limits | provider cap | ordinary full/delta review | claims and finalizes next round |
| provider failure | calls already made | failure/stale under existing review contract | finalizes failure; same input cannot retry |
| false-positive candidates filtered | calls already made | successful canonical review with counters | finalizes `quality_filtered` |
| third distinct diff without override | 0 | no clean claim; merge remains blocked | `round_budget_exhausted` handoff |
| one unconsumed override-label event | provider cap | ordinary review | consumes one audited override round |
| budget exhausted after override | 0 | merge remains blocked | terminal handoff; no further auto review |

## 12. Security and compatibility invariants

1. PR title, body, code, comments, labels, model output, and prior free-form prose remain untrusted.
2. No untrusted value is interpolated into executable shell; files and JSON arguments carry data.
3. A claim is persisted before the provider call, so cancellation cannot reopen the same input.
4. Comment updates are head-bound and compare-and-swap protected.
5. Invalid ledger state fails closed and cannot downgrade to a legacy budget schema.
6. Budget reuse requires the existing authenticated review checkpoint; ledger state alone cannot
   report success, resolve a finding, or authorize merge.
7. Existing schema-3/schema-2 review comments, quality counters, immutable diff preparation,
   canonicalization, run-generation checks, and publication limits remain unchanged.
8. The action and helper are immutable release dependencies when a future release includes them.

## 13. Acceptance mapping

| Issue #52 completion condition | Design mechanism |
|---|---|
| same head does not call a reviewer twice | absolute pre-call `duplicate_head` gate |
| wait loop with no new diff uses zero model calls | deterministic polling plus `duplicate_effective_diff`/reuse decisions |
| automatic review stops after two rounds | two claimed distinct-diff rounds per reviewer |
| provider error does not create unlimited fallback | claimed input plus provider-specific hard call cap; no cross-reviewer cascade |
| run reviewer/model/effort/calls/round/stop reason is reproducible | validated ledger entry, artifact, and job summary |
| next session continues from checkpoint | bounded handoff object and visible ledger summary |
| fixed same/new SHA, false-positive, provider-failure paths | pure fixtures plus workflow integration tests |

