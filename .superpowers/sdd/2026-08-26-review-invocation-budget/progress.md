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

Ruling: Task 5 preserves Gemini's exact schema-3 review-state authority and emits Task 3's exact four-key authenticated-review payload. It reuses Task 4's reviewed workspace-private selected-diff staging, immediate cleanup, and empty unavailable head/hash normalization instead of widening the shared action's path boundary — this keeps all reviewers on one transport/security contract — if wrong, Tasks 3-5 require a coordinated payload or filesystem boundary revision.

Ruling: A Gemini claim can fail before its first SDK request (dependency/auth/config failure), leaving truthful `call_count=0` and an empty attempted-route file, but Task 2 requires a nonempty final route whose first item matches the claimed route. In that zero-call case only, finalization retains the configured primary route already persisted by the claim; once any request is counted, finalization uses the actual attempted model route and keeps every primary retry/fallback under the shared three-call counter — this preserves truthful usage while allowing deterministic failure finalization — if wrong, the ledger schema needs a nullable attempted-route field separate from the required configured route.

Task 5: implementer complete (RED 12 expected failures/4 passes; GREEN 26 focused, 165 Gemini/actionlint, 1514 workflow-logic, and 2070 + 48 subtests non-Task7 full; digest-verified actionlint 1.7.12, YAML, changed-line Ruff, and diff-check green; report: task-5-report.md)

Task 5: self-review complete (schema-3 authority/exact four-key payload, workspace-private staging and immediate cleanup, GitHub `if` failure semantics, zero-call claimed route, shared three-call crash persistence, deterministic upsert/finalize outcomes, and no reviewer fallback verified)

Task 5: complete (commits 5ea5ce0..8e6b46a, independent review clean; Critical/Important 0; accepted intermediate red remains only Task 7 release inventory)

Ruling: Task 6 uses exact schema-1 `candidate.json` as the untrusted OpenCode metric/identity envelope. Canonicalization accepts it only after GitHub artifact API ID/digest validation, exact mode-aware regular-file inventory, exact envelope keys/types, repository/PR/run/attempt/head/full-diff/diff-mode identity, sealed prepare-claim checkpoint digest/identity, nonnegative count/elapsed values, exact fixed model route, and optional review hash validation. A failed candidate contains only `candidate.json`; a successful candidate additionally contains exact `review.md`. Metrics are initialized before download/cache/install and the always+allowed materialization path records truthful zero-call dependency failures as well as provider failures — this keeps schema-2 review publication authority in canonicalize and does not trust model-job outputs independently — if wrong, a new trusted cross-job transport would be required rather than weakening artifact validation.

Ruling: The OpenCode prepare job requires `issues: write`, while preserving its other least-privilege scopes, because the shared claim action performs issue-comment compare-and-swap. The tokenless `opencode-review` job retains exact `permissions: {}` and cannot receive a repository token — this grants mutation authority only to the prepare claim and privileged canonicalization/finalization boundaries — if wrong, the shared action would need a non-comment CAS backend before prepare can return to read-only permissions.

Ruling: Every OpenCode CLI session consumes the shared two-call allowance immediately before execution through a mode-0600 temporary file, file fsync, atomic rename, and parent-directory fsync. The optional format-only repair uses the same wrapper, and a third call is refused before CLI execution. Materialization reads the durable counter even after a raised provider request — this makes crash/exception accounting truthful without adding a cross-reviewer or model fallback — if wrong, the ledger schema needs an external provider-session receipt rather than a weaker in-process counter.

Task 6: implementer complete (RED 6 expected failures/1 pass; GREEN 9 focused; dynamic canonical harness 12 passed across success, provider failure, authenticated reuse, unavailable denial, and malformed candidate envelopes; 1192 OpenCode/actionlint; 1529 workflow-logic; 2085 + 48 subtests non-Task7 full; digest-verified actionlint 1.7.12, YAML, changed-line Ruff, and diff-check green; report: task-6-report.md)

Task 6: self-review complete (exact schema-2 publication/attestation authority preserved; sealed claim and candidate identities fail closed; durable two-session counter precedes both CLI calls; denied claims cannot manufacture schema-2 state; authenticated unchanged reuse remains zero-call; fixed OpenCode route only; no Task 7 files changed)

Task 6: fix round 1/5 (1 open — finalization used raw model-job count/elapsed outputs with zero fallbacks, so timeout or candidate-artifact loss after a durable CLI call could falsely finalize the claim as zero-call; base commit fbefb66)

Ruling: OpenCode budget metrics become privileged only inside canonicalization after the candidate artifact's API identity/digest, exact mode-aware regular-file inventory, sealed claim identity, exact envelope keys/types, nonnegative bounded count/elapsed, fixed route, job-output metric cross-check, and success/failure review hash/null mode all validate. Canonicalization initializes only `budget_metrics_valid=false`; validated count, elapsed, route, outcome, and failure reason outputs do not exist until the complete candidate contract succeeds. Outcome resolution and finalization require `budget_metrics_valid=true` and consume only these canonical outputs without zero fallbacks. Missing/malformed artifacts, upload/materialization loss, or timeout therefore leave the durable claim unfinalized, while valid zero-call and provider/contract failure envelopes finalize truthfully — this preserves consumed-claim evidence instead of inventing usage — if wrong, the claim action needs a separate privileged metrics recovery artifact outside the timed model job.

Task 6: fix round 1/5 (1 addressed, 0 open — RED 6 failed/8 passed, GREEN 14 focused and 5 dynamic canonical metric cases; 1197 OpenCode/actionlint, 1534 workflow-logic, 2090 + 48 subtests non-Task7 full; digest-verified actionlint 1.7.12, YAML, changed-line Ruff, and diff-check green; base commit fbefb66)

Task 6: complete (commits 7e8cd82..042cf6e, independent re-review clean after fix round 1/5; Critical/Important 0; accepted intermediate red remains only Task 7 release inventory)

Task 7: RED complete (`tests/test_workflow_release_bundle.py` + `tests/test_verify_workflow_release.py`: 168 failed, 373 passed in 249.82s; failures proved the absent v1.47 inventory/verifier boundary and the approved Task 4–6 unknown-dependency gap)

Task 7: GREEN complete (v1.47 focused 26 passed; release/bundle 542 passed in 266.42s; full unfiltered 2632 passed + 48 subtests in 814.96s; py_compile and diff-check green)

Ruling: Task 7 keeps the immutable v1.40–v1.46.2 capability inventories and v1.46.2 fleet default unchanged, while current live workflow mutation tests verify the v1.47 capability. The verifier first preserves established reviewer-specific diagnostics and then authenticates the exact complete v1.47 budget workflow bytes, so historical contracts remain inspectable without weakening the new closed boundary — if wrong, dedicated immutable v1.46 workflow fixtures must replace the current-source tests before a later release.

Task 7: self-review complete (exact BaseLoader action bytes/surface, helper compile + strict signatures/constants/AST/source gates, reviewer claim/provider/finalize ordering and guards, timeout/counter/artifact/OpenCode handoff/no-fallback contracts, historical digest branches, exhaustion-not-approval docs, and no Task 1–6/config/tag/rollout changes verified)

Task 7: complete (implementation commit abc3cefd203014ed2295cac9efdb11f02ff1e84f; post-commit `v1.47 --commit-only` PASS against the same authenticated HEAD; report: task-7-report.md; blockers: none)

Task 7: fix round 1/5 RED complete (finding 2/4 focus 1 failed + 11 passed; authenticated helper semantics 4 failed; original-text workflow semantics 10 failed + 3 passed; failures directly demonstrated incomplete full-action equality and missing schema/AST/cap/RVW/provider/order/artifact/OpenCode-handoff semantic gates)

Ruling: Task 7 historical review mutation coverage uses the exact three workflow blobs from immutable commit `d42c28ddd827554e6e46a2ab49dfe34c838c0425`, guarded by fixed SHA-256 constants, and verifies those synthetic commits as `v1.46.2`; v1.47 current-source tests remain distinct. This avoids using mutable candidate bytes as historical fixtures while staying within the Task 7 test-file boundary — if wrong, the same authenticated blobs can be materialized as checked-in fixture files in a separately approved file-scope expansion.

Ruling: Invocation-budget semantic validation runs before a distinct exact-byte authentication pass so mutation tests prove each parser/AST/order/artifact gate rather than only digest rejection. Both passes consume only `VerifiedCommitTree` bytes from the same authenticated commit object; full BaseLoader action equality and final raw digests remain mandatory — if wrong, the verifier would need a diagnostic-only semantic entry point while retaining the same commit-tree caller ordering.

Task 7: fix round 1/5 GREEN complete (semantic/inventory focus 29 passed; immutable v1.46 suite 166 passed; final release suite 566 passed in 270.50s; full unfiltered 2656 passed + 48 subtests in 813.70s; py_compile and diff-check green)

Task 7: fix round 1/5 self-review complete (all four Important findings closed; immutable v1.46.2 direct coverage, exact complete BaseLoader action equality, separate semantic/authentication gates, strict helper and reviewer workflow relationships, full new-root kind mutations, no Task 1–6/config/tag/release/rollout/external-state changes; blockers and remaining concerns: none)

Task 7: fix round 1/5 complete (implementation fix commit 0fb3a1d3b43bd03f125dbf1ae2e24970bec4024b; post-commit `v1.47 --commit-only` PASS against the same authenticated HEAD; report: task-7-report.md)

Task 7: fix round 2/5 RED complete (9 failed: schema annotation, frozen record shape, dead claim refusal, dead final-cap relation, dead RVW/provenance decoys, and three reviewer live call-cap decoys all escaped the prior semantic checks)

Ruling: Helper semantic verification is an exact live-AST contract, not a source-fragment approximation. Record decorators/annotations/defaults, type aliases, direct claim statement order and predicate-to-refusal bodies, final/stored caps, provenance, override, RVW bounds, checkpoint canonicality, and CAS refusal are bound to their executable AST locations before exact-byte authentication — if wrong, a future schema version must introduce a separately versioned AST contract instead of weakening these v1.47 relationships.

Ruling: Reviewer call accounting is bound to the named provider/metrics step and its live program: exact Claude metrics step mapping, Gemini embedded Python AST, and the unique anchored OpenCode shell counter/refusal/increment sequence. Workflow-wide raw fragment presence is not evidence of a live cap — if wrong, the embedded scripts should be extracted into separately authenticated helpers in a future release boundary.

Task 7: fix round 2/5 GREEN complete (structural focus 9 passed; combined findings 1-4 focus 48 passed; release suite 575 passed in 226.40s; full unfiltered 2665 passed + 48 subtests in 790.25s; py_compile and diff-check green)

Task 7: fix round 2/5 self-review complete (all Finding 3 decoy classes structurally rejected; authentication remains after semantics over the same VerifiedCommitTree bytes; findings 1/2/4 preserved; no Task 1-6/config/tag/release/rollout/external-state changes; blockers and remaining concerns: none)

Task 7: fix round 2/5 complete (implementation fix commit 074626e10e64e9ebda17d8fd3a7688733bddeb8c; post-commit `v1.47 --commit-only` PASS against the same authenticated HEAD; report: task-7-report.md)

Task 7: fix round 3/5 RED complete (the exact authenticated OpenCode bypass moved the complete seven-line counter/cap/durable-increment sequence below `if false`, weakened the live counter read, and escaped the prior normalized-line semantic gate: 1 failed with DID NOT RAISE in 0.26s)

Ruling: OpenCode live call accounting is an exact structural shell-function contract over logical commands and executable control nesting. The unique top-level counter read, numeric guard, two-call refusal group, durable increment, and sole CLI invocation must appear in exact order inside `run_opencode`; heredoc payloads, unreachable or conditional copies, alternate reads, and ambiguous extra sequences cannot authenticate live behavior — if wrong, the embedded shell should be extracted into a separately authenticated helper in a future release boundary rather than reverting to raw text/count checks.

Task 7: fix round 3/5 GREEN complete (authentic-plus-bypass focus 2 passed; prior 48 plus residual regression 49 passed; release suite 576 passed in 259.90s; full unfiltered 2666 passed + 48 subtests in 818.79s; py_compile and diff-check green)

Task 7: fix round 3/5 self-review complete (exact parsed command/control equality replaces OpenCode normalized fragments and occurrence counts; semantic-before-digest and VerifiedCommitTree-only bytes preserved; findings 1/2/4 and round-2 relationships retained; no Task 1–6/config/tag/release/rollout/external-state changes; blockers and remaining concerns: none)

Task 7: fix round 3/5 complete (implementation fix commit 1bc6f7924b987f3b944966cf011e2caa486bbefb; post-commit `v1.47 --commit-only` PASS against the same authenticated HEAD; report: task-7-report.md)

Task 7: fix round 4/5 RED complete (the exact authenticated bypass placed the complete canonical `run_opencode() { ... }` under top-level `if false`, installed a weakened live `function run_opencode { ... }`, and escaped the prior declaration-anchored parser: 1 failed with DID NOT RAISE in 0.36s; conditional/duplicate/predefinition adjacency also escaped)

Ruling: OpenCode function verification parses the complete authenticated shell program before evaluating declarations, recognizes POSIX and Bash function forms including whitespace/split braces, and requires exactly one exact canonical reachable top-level definition whose exact two invocation sites occur later with the approved control paths. Conditional, alternate, duplicate, or pre-invocation bindings fail structurally before exact digest authentication — if wrong, the embedded shell must move into a separately authenticated helper rather than selecting a declaration by textual spelling.

Task 7: fix round 4/5 GREEN complete (exact bypass 1 passed; authentic/prior-decoy/exact/adjacent focus 8 passed; unchanged prior regression 49 passed; release suites 582 passed in 270.94s; full unfiltered 2672 passed + 48 subtests in 804.09s; four-file py_compile and diff-check green)

Task 7: fix round 4/5 self-review complete (whole-program control nesting, both declaration syntaxes and split forms, single canonical top-level definition, exact later invocation binding, exact body cap/increment/CLI structure, heredoc exclusion, semantic-before-digest and VerifiedCommitTree-only bytes preserved; no Task 1–6/inventory/docs-contract/config/tag/release/rollout/external-state changes; blockers and remaining concerns: none)

Task 7: fix round 4/5 complete (implementation fix commit 2391487b764226630a0a47f3a1851a531e8bb87d; post-commit `v1.47 --commit-only` PASS against the same authenticated HEAD; report: task-7-report.md)

Task 7: fix round 5/5 RED complete (both legal compact `function run_opencode { …; }` and `run_opencode(){ …; }` authenticated redefinitions escaped with `DID NOT RAISE`: 2 failed; inline conditional/dynamic/hidden target syntax and wrapped/assignment-prefixed eval adjacency also escaped before their structural fixes)

Ruling: OpenCode whole-program verification tokenizes executable semicolon lists and compact/split brace declarations before control analysis, inventories every recognized target definition, and permits only one exact canonical reachable top-level binding before the two exact later calls. Target-bearing unrecognized shell words plus direct, wrapper-mediated, or assignment-prefixed dynamic namespace commands fail closed; quoted/substitution/control/heredoc data boundaries remain preserved, and no raw substring/count gate is evidence — if wrong, the embedded provider must move to a separately authenticated helper rather than widening the accepted shell grammar.

Task 7: fix round 5/5 GREEN complete (new/positive focus 8 passed; unchanged round-4 focus 8 passed; unchanged prior regression 49 passed; final release suites 589 passed in 277.11s; full unfiltered 2679 passed + 48 subtests in 814.39s; four-file py_compile and diff-check green)

Task 7: fix round 5/5 self-review complete (compact POSIX/Bash declarations and semicolon bodies parsed structurally; duplicate/conditional/dead/dynamic/hidden binding ambiguity rejected; exact later invocation binding and heredoc/control preservation retained; semantic-before-digest and VerifiedCommitTree-only bytes preserved; analyzer admits only the authenticated shell subset and refuses computed namespace execution; no Task 1–6/inventory/contracts/config/tag/release/rollout/external-state changes; blockers and remaining known concerns: none)

Task 7: fix round 5/5 complete (implementation fix commit a9ccc1fa9eec4c91a53f1cdf33f5125e80b98995; post-commit `v1.47 --commit-only` PASS against the same authenticated HEAD; report: task-7-report.md)

Task 7: breaker adjudication (1 load-bearing Important remains after round 5/5 — the shell lexer can mistake quoted here-document-like data for a real here-document and can miss a dynamically assembled namespace executor, allowing a compact live `run_opencode` redefinition to escape semantic inventory)

Ruling: The round-5 residual is real and load-bearing, not parked as harmless: Task 8 explicitly permits corrections demonstrated by validated review findings, so Task 8 must add authenticated RED cases for false-heredoc hiding and a computed executor name, then make executable shell tokenization and target-bearing dynamic execution fail closed before any PR is opened. Task 8 stops without push/PR/merge if either bypass remains or if the exact tested head changes — if wrong, the proposed v1.47 verifier could authenticate a weakened live OpenCode budget path, so merge is prohibited rather than accepting this risk.

Task 7: complete (commits 0b59828..c802c60, breaker reached after fix round 5/5; findings 1/2/4 addressed; 1 real load-bearing verifier finding carried into Task 8 by ruling)

Task 8: carried-breaker RED complete (two authenticated, bash-syntax-valid OpenCode workflow mutations both escaped with `DID NOT RAISE`: quoted here-doc-like data hid a compact redefinition, and concatenated executor/target variables performed an indirect dynamic redefinition; 2 failed in 0.50s)

Ruling: Parse heredocs only from quote-aware executable `<<` operator tokens and fail closed when parsed literal assignment/append words compute `eval`, `source`, `.`, or `run_opencode`, or when shell expansion occupies command position. Keep the accepted authentic shell subset, whole-program binding, semantic-before-digest ordering, and VerifiedCommitTree bytes unchanged — if wrong, the verifier could either accept a computed live budget bypass or falsely reject the authenticated OpenCode workflow, so both exact REDs, the positive candidate, prior parser matrix, release suite, and full suite must all be green before handoff.

Task 8: carried-breaker GREEN complete (exact new cases 2 passed; authentic/prior OpenCode parser matrix 17 passed; unchanged structural regression 49 passed; release/bundle suite 591 passed; no Task 1–6 action/helper/workflow byte changed; implementation commit 8fa6bd6e1c62f92c787e212f8fd0876789ef3139)

Ruling: The exact Step 4 diff-check exposed one extra blank line at EOF in the approved design spec. Remove only that empty line, preserve all prose/decisions, commit it separately, and rerun Steps 1–4 on the changed HEAD — if wrong, the cost is a reversible one-byte documentation-only correction with no runtime or release-boundary effect.

Task 8: local Steps 1–4 complete on 0c63098a804a625bcce262cd0faed7115484a02c (helper py_compile exit 0; five-file suite 2206 passed; full suite 2681 passed + 48 subtests; 36 YAML mappings; digest-pinned actionlint 1.7.12 clean; mutation focus 73 passed; v1.47 commit-only PASS; diff-check/status/stat/log scope audit clean; report: task-8-report.md)

Task 8: breaker ruling outcome — addressed locally with no remaining load-bearing verifier finding; Task 8 Steps 5–9, independent review, push, PR, merge, tag, release, rollout, GitHub, and Notion changes were not performed and remain controller-owned.
