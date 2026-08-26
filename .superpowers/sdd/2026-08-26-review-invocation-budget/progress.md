# SDD ledger — plan: docs/superpowers/plans/2026-08-26-review-invocation-budget.md

Started: 2026-08-26
Branch base: d42c28ddd827554e6e46a2ab49dfe34c838c0425
Approved spec: docs/superpowers/specs/2026-08-26-review-invocation-budget-design.md

## Preflight interface/conflict scan

| Tasks | Shared file/interface | Finding |
|---|---|---|
| 1 | Self-consistency | Fixture decisions match the ordered claim gates. `FinalizeRequest` is declared in Task 1 as a record shape and receives behavior only in Task 2. |
| 2 | Self-consistency | Finalization caps, bounded IDs, deterministic rendering, and checkpoint-only resume tests agree. Resume still revalidates Actions provenance, as the spec requires. |
| 3 | Self-consistency | The composite exposes only claim/finalize publicly; helper-only CLI operations support provenance discovery and CAS failure without adding provider modes. The action never starts a provider, so the fake provider sentinel remains outside the composite invocation. |
| 4 | Self-consistency | Claude claim follows immutable diff preparation and precedes checkout/model; authenticated unchanged reuse bypasses the model while review schema 3 remains authoritative. |
| 5 | Self-consistency | Gemini keeps its existing 10-minute/450-second/200-second deadlines; the new durable counter wraps all primary/retry/fallback requests before each SDK call. |
| 6 | Self-consistency | OpenCode claim is sealed in prepare, the tokenless model job consumes at most two CLI sessions, and canonicalize owns finalization after schema-2 publication. Required cross-job outputs must be added explicitly. |
| 7 | Self-consistency | Historical release roots remain unchanged; the new capability is modeled as v1.47 without creating a tag or advancing the fleet default. Step 7's commit-only verification ordering conflicts with Step 8's commit placement. |
| 8 | Self-consistency | Verification, independent review, PR creation, deterministic monitoring, merge, and issue closure match the requested terminal outcome; release/rollout remains excluded. |
| 1 → 2 | Helper records, parser, tests, fixture corpus | Task 2 extends Task 1's strict types and state without changing claim semantics. |
| 1 → 3 | `claim`, parser, policy, provenance types | Task 3 transports files/API data only and delegates all state decisions to the Task 1 helper. |
| 2 → 3 | `finalize`, renderers, checkpoint | Task 3 persists the exact transition and exposes only derived outputs. |
| 1 ↔ 2 | `review_invocation_budget.py`, pure tests, cases fixture | Sequential edits are intentional; Task 2 must preserve every Task 1 RED/GREEN vector. |
| 1/2 → 4 | Reviewer policy and authenticated-review shape | Claude supplies reviewer `claude`, one-session model route, exact head/hash, and schema-3 authenticated coverage. |
| 1/2 → 5 | Reviewer policy and authenticated-review shape | Gemini supplies reviewer `gemini`, actual attempted model route, thinking level, and the shared three-request count. |
| 1/2 → 6 | Reviewer policy and authenticated-review shape | OpenCode supplies reviewer `opencode`, fixed model/effort, two-session count, and schema-2 authenticated IDs. |
| 3 → 4 | Composite inputs/outputs and checkpoint files | Claude conditions every new-diff provider path on the durable claim and finalizes after upsert. |
| 3 → 5 | Composite inputs/outputs and checkpoint files | Gemini claims before its embedded dependency/provider step and finalizes after upsert. |
| 3 → 6 | Composite inputs/outputs and checkpoint files | OpenCode carries the action's claim identity across the sealed three-job boundary. |
| 4 ↔ 5 | `.github/actionlint.yaml`, `tests/test_review_workflow_logic.py` | Sequential additions must preserve exact prior exceptions/tests; no reviewer shares a ledger marker or provider fallback. |
| 4 ↔ 6 | `.github/actionlint.yaml`, `tests/test_review_workflow_logic.py` | Sequential additions are independent by reviewer; shared test helpers may be refactored only without weakening existing cases. |
| 5 ↔ 6 | `.github/actionlint.yaml`, `tests/test_review_workflow_logic.py` | Gemini request counters and OpenCode CLI counters use different artifacts and must not be combined. |
| 3 → 7 | Action/helper authenticated bytes | The v1.47 verifier owns exact metadata, syntax, state-machine literals/AST, directory closure, and immutable modes. |
| 4 → 7 | Claude workflow bytes | Verifier requires claim-before-provider, allow guards, 10-minute cap, finalization, and artifacts. |
| 5 → 7 | Gemini workflow bytes | Verifier requires the pre-request shared counter and unchanged existing deadlines/fallback class. |
| 6 → 7 | OpenCode workflow bytes | Verifier requires sealed claim identity, tokenless guard, two-call cap, canonicalize finalization, and artifacts. |
| 1-7 → 8 | Complete branch diff and validation commands | Task 8 reviews and ships exactly the accumulated implementation; no implementation interface is added there unless a verified failure requires a fix. |

Ruling: Treat `FinalizeRequest` in Task 1 as a data-only record and implement its transition in Task 2 — this preserves the task boundary while keeping type names stable — if wrong, Task 2 may require a small type relocation with no public workflow impact.

Ruling: Use `REVIEW_INVOCATION_BUDGET_RELEASE = (1, 47)` only as the next verifier capability boundary; do not create/push a v1.47 tag or change the v1.46.2 fleet default — this lets future releases authenticate the new files while respecting #52's non-goal — if release numbering changes, the predicate/tests must be renamed before that release.

Ruling: In Task 7, run RED release tests first, implement the inventory/verifier, commit the Task 7 files, then run commit-only verification against that new HEAD; if verification finds a defect, fix it in a follow-up Task 7 commit before review — verifying the pre-commit HEAD would omit the worktree changes — if wrong, this creates an extra correction commit but does not weaken verification.

Task 1: reviewer ⚠️ resolved — the pure state machine validates canonical hash shape; Tasks 3-6 must bind that value to `prepare-review-diff`'s immutable `review-full.diff` output, so this is a downstream integration check rather than a Task 1 gap.

Task 1: fix round 1/5 (2 addressed, 0 open — stored invocation caps and boundary coverage; commit 1ae24aa..ec17f73)

Task 1: complete (commits b88bb88..ec17f73, review clean)

Ruling: Task 2 must persist the actual over-cap call count or elapsed seconds rather than normalize evidence. The strict ledger accepts a stored call-count violation only when the entry is `finalized` with `outcome=checkpoint_failure` and `stop_reason=call_budget_exhausted`, and accepts a wall violation only when `finalized` with `outcome=wall_time_exhausted` and `stop_reason=wall_time_exhausted`; claimed/success/other mismatches remain invalid, and input/aggregate overages remain impossible — this reconciles truthful finalization with fail-closed parsing — if wrong, downstream checkpoint consumers may need a separate observed-versus-budget field in a schema revision.

Ruling: When actual calls and elapsed time both exceed their caps, preserve both actual metrics and follow the plan's call-first normalization: `outcome=checkpoint_failure`, `stop_reason=call_budget_exhausted`. The parser may accept an over-cap wall value under that outcome only when the same entry's call count is also over cap; a wall-only violation still requires `wall_time_exhausted` — this avoids losing a dual violation while keeping every permissive state mechanically tied to observed evidence — if wrong, the schema needs a dedicated dual-overage stop reason before release.

Task 2: fix round 1/5 (4 addressed, 0 open — authoritative empty findings, refusal handoff, exact checkpoint identity, final-head full suite; commit 9c0399b..c3b3415)

Task 2: complete (commits ec17f73..c3b3415, review clean)

Ruling: Task 3's public transport accepts exactly `full|delta|unchanged|unavailable`, matching `prepare-review-diff`; it maps only `full|delta` to the pure claim machine's internal `changed` value. `unavailable` with empty canonical identities and any malformed transport request must finish locally with zero API mutation/provider allowance and atomically write a deterministic diagnostic checkpoint, summary, and outputs. When invalid or absent identity fields cannot form a schema-valid `LedgerState`, the diagnostic checkpoint uses the canonical outer schema with `ledger:null` and a bounded handoff carrying `decision=state_invalid` or `diff_unavailable`; strict ledger checkpoints remain unchanged for valid transitions, so any resume consumer fails closed rather than inventing identity — this reconciles the required always-checkpoint behavior with the ledger's non-null authenticated identity invariants — if wrong, schema 2 must introduce an explicit nullable pre-identity handoff record before workflow integration.

Task 3: fix round 1/5 (5 open — prepare-review-diff vocabulary, local transport refusal checkpoint, nonempty paginated evidence, summary/output ordering, regression coverage; base commit 1862945)

Task 3: fix round 1/5 (5 addressed, 0 open — exact producer diff vocabulary, canonical zero-API local refusals, nonempty pagination/CAS evidence, summary-before-output ordering, regression coverage; base commit 1862945)

Task 3: fix round 2/5 (1 open — malformed request JSON raises before the local diagnostic boundary; write output-directory refusal artifacts with all unavailable identities null and write the requested external checkpoint only when its path was safely decoded; base commit 0cef9fc)

Task 3: fix round 2/5 (1 addressed, 0 open — malformed, non-object, and undecodable requests now write canonical output-directory refusals with null identities and no invented external checkpoint path; base commit 0cef9fc)

Task 3: complete (commits c3b3415..862ef0f, review clean; focused 81 passed, full 2550 passed + 48 subtests)

Ruling: Task 4 preserves the authoritative Claude review comment at exact schema 3 and derives the budget action input as Task 3's already approved exact four-key object `{"success":bool,"head_sha":str|null,"full_diff_sha256":str|null,"remaining_finding_ids":list}`. The Task 4 interface sentence naming `schema`/`successful_head` describes the upstream authenticated collector, not a second incompatible action payload — this avoids weakening `_authenticated_review` exact-key validation or changing existing review-state authority — if wrong, the action transport and all three workflow tasks would require a coordinated schema revision before integration.

Ruling: Task 4 must preserve Task 3's reviewed rule that estimated-input files resolve below `$GITHUB_WORKSPACE`. Because `prepare-review-diff` intentionally writes provider artifacts under `$RUNNER_TEMP`, add a pre-claim staging step that copies only the selected immutable full/delta diff byte-for-byte into a mode-0700 workspace subdirectory, builds the action's JSON path with `jq`, and an `always()` post-claim cleanup before checkout/provider execution; `unavailable` passes `[]` and creates no staged input. Do not widen the composite action to trust all of runner temp — this keeps the approved path boundary and lets the provider continue reading its original runner-temp artifact — if wrong, the Task 3 security contract must be reopened for all reviewers rather than relaxed only in Claude.

Ruling: Tasks 4-6 intentionally make the unfiltered full suite fail only in Task 7-owned `test_workflow_release_bundle.py` / `test_verify_workflow_release.py` until the v1.47 inventory and verifier are implemented; intermediate tasks require all focused/workflow tests plus the full non-Task7 suite green, and Tasks 7-8 must restore the unfiltered full suite to green — this preserves the approved task/file boundaries without weakening release tests — if wrong, the release inventory work must move into each reviewer-integration task.

Task 4: implementer complete (RED 6 expected failures; GREEN 15 focused, 133 Claude/actionlint, 1493 workflow-logic, and 2049 + 48 subtests non-Task7 full; unfiltered full 2422 + 48 subtests with 143 Task7-boundary failures; report: task-4-report.md)

Task 4: fix round 1/5 (1 open — graceful `diff-mode=unavailable` preserves a producer head SHA; normalize both canonical head/hash claim inputs to empty while retaining exact identities for full/delta/unchanged; base commit 26f8afe)

Task 4: fix round 1/5 (1 addressed, 0 open — unavailable producer identities are normalized empty before the Task 3 claim; RED 4 failed/12 passed, GREEN 16 focused, 134 Claude/actionlint, and 2050 + 48 subtests non-Task7 full; base commit 26f8afe)

Task 4: complete (commits 8e98272..d0aeedb, review clean; accepted intermediate red remains only Task 7 release inventory)
