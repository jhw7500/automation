# Review State and Scope Hardening Design

Date: 2026-08-21  
Status: approved in chat; implementation pending

## 1. Decision

The automated reviewers will stop treating model-authored prose and an arbitrary bot
comment as authoritative review state. Claude, Gemini, and OpenCode will share two
machine-enforced contracts:

1. a review checkpoint is valid only when the workflow prepared the exact PR-scoped input,
   the model step succeeded, the sanitized body is non-empty, and the reviewed head is still
   the current PR head; and
2. previous-review context is accepted only from a canonical, reviewer-specific state
   envelope generated and validated by workflow code.

Delivery is split into two reviewable slices. The first establishes trustworthy state and
completion ordering. The second centralizes PR diff preparation and closes remaining scope
and unusual-path gaps. Both slices are required before fleet rollout because fixing only
the state bugs would leave the observed out-of-diff OpenCode finding possible.

## 2. Goals

1. Never write `Reviewed: <sha>` unless a machine-prepared input covering that PR head was
   actually reviewed.
2. Never let sanitization turn an otherwise successful run into an empty successful review.
3. Never let an older run overwrite or stamp the state of a newer PR head.
4. Never accept a foreign bot's marker quotation as a reviewer's previous review.
5. Keep incremental diffs inside the PR file set without losing Unicode, newline, glob-like,
   deleted, binary, submodule, or renamed paths.
6. Never broaden a failed filename lookup into an unrestricted `previous..head` review.
7. Require every new finding to be tied to a changed hunk or to a concrete causal path from
   a changed hunk.
8. Preserve a prior successful review when a later attempt fails, while making the failed
   attempt and its head visible.
9. Preserve the existing immutable central-workflow release and fleet rollout model.

## 3. Non-goals

- Replacing Claude, Gemini, OpenCode, or their model versions.
- Building a general-purpose review database or service.
- Creating one GitHub App identity per reviewer.
- Combining the three reviewer workflows into one large reusable workflow.
- Automatically proving that a model's semantic judgment is correct.
- Adding automatic severity ranking, cross-model deduplication, merge approval, or merge
  blocking policy.
- Changing unrelated fleet composition, authentication, manual-review, or triage workflows.

## 4. Core Invariants

The implementation must enforce these invariants in code rather than prompt prose:

```text
checkpoint_advance =
  captured_head_is_current
  AND current_run_is_not_older_than_stored_state
  AND (
    (diff_ready AND model_step_succeeded AND sanitized_review_nonempty)
    OR
    (validated_previous_success AND full_diff_hash_is_unchanged)
  )
```

- `Reviewed` is emitted only when `checkpoint_success` is true.
- A failed or stale attempt cannot change the previous successful review body or successful
  checkpoint.
- A stale attempt may write only a workflow notice; it may not mutate the PR comment.
- Comment text is presentation. Structured envelope fields, not free-form body text, drive
  previous-SHA and re-review decisions.
- Failure to validate previous state degrades to a full PR review, never to an unrestricted
  commit-range review.
- Failure to prepare the full PR input fails closed and skips the model.

## 5. Canonical Review State Envelope

### 5.1 Schema

Every sticky review begins with a deterministic header and a single-line JSON envelope
generated after model output sanitization. Example:

```markdown
## Claude Code Review (latest)
<!-- automation:claude-code-review:v2 -->
<!-- automation-state:{"schema":2,"reviewer":"claude-code-review","pr":34,"run_id":1234,"run_attempt":2,"attempt_head":"<sha>","successful_head":"<sha>","attempt_status":"success","diff_mode":"delta","full_diff_sha256":"<sha256>"} -->
```

The model never generates or edits these lines. Output sanitization removes every reserved
header, marker, and state line before the workflow constructs the final comment.

On a failed attempt after an earlier success, `successful_head` and the previous body stay
unchanged, `attempt_head` records the failed head, and `attempt_status` becomes `failure`.
The visible metadata uses `Status: stale` plus a `Last attempt: failure` line so readers do
not mistake the preserved body for coverage of the latest head. If no successful review
exists, the status is `failure` and `successful_head` is null.

### 5.2 Validation

A previous-state candidate is accepted only if all of the following hold:

- the body starts with the exact reviewer header, v2 marker, and state line;
- the JSON contains only the supported schema and field types;
- `reviewer` and `pr` match the running workflow;
- SHA and hash fields have exact hexadecimal lengths;
- `run_id` and `run_attempt` are positive safe integers and the visible run URL matches the current repository;
- the visible run URL contains the same `run_id`; and
- the comment author is a bot.

Candidates are ordered lexicographically by `(run_id, run_attempt)`, not comment list order
or `updated_at`. GitHub manual reruns retain their `run_id` and increment `run_attempt`, so
the pair is the review generation. The exact-prefix requirement is paired with output
sanitization in every reviewer, so another reviewer's model body cannot accidentally
manufacture a candidate. If validation cannot be completed, the workflow ignores the
candidate and performs a full review. A marker appearing anywhere else in a body has no
state meaning.

This is a provenance boundary against accidental or model-induced cross-reviewer marker
echoes. A dedicated App identity remains a possible later defense-in-depth improvement, not
a dependency of this project.

### 5.3 Legacy migration

Legacy comments may be reused as the display comment only when their exact legacy header
and marker match. Their body and `Reviewed` line are not trusted as input state. The first
v2 run therefore performs one full review and rewrites the display comment with a v2
envelope. OpenCode's historical unstructured comments are ignored and a new canonical
record is established.

## 6. Completion Ordering

Each reviewer job uses a repository/reviewer/PR-specific concurrency group with
`cancel-in-progress: true`. The group includes a fixed reviewer identifier so Claude,
Gemini, and OpenCode do not cancel one another.

Concurrency is an optimization, not the correctness boundary. Immediately before any
comment mutation the workflow must:

1. refetch the PR head SHA;
2. compare it with the head captured during input preparation;
3. parse the current canonical state; and
4. refuse the update unless the stored `(run_id, run_attempt)` generation is strictly older
   than the current generation.

This compare-before-write rule handles cancellation races and jobs already beyond a
non-cancellable external API call. The head read and comment mutation are separate GitHub
resources, so this remains an optimistic guard; eliminating the final push race would
require an atomic conditional comment API or a different persistence design.

## 7. Deterministic PR Diff Preparation

### 7.1 Shared action

A focused composite action is added at:

```text
.github/actions/prepare-review-diff/
  action.yml
  prepare_review_diff.py
```

Reusable workflows invoke it with the `$/` self-repository action syntax. GitHub documents
that `$/` resolves against the repository and commit containing the called reusable
workflow, rather than the consumer checkout. This permits the workflow and helper to ship
at the same immutable automation commit without duplicating the script into consumers:

https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/find-and-customize-actions

The action has no write permission. It receives the PR number, previous validated state,
output filenames, and context-line count. It writes:

- the full PR diff;
- an incremental diff when safe and non-empty;
- a JSON scope manifest containing schema, repository, PR number, merge-base SHA, head SHA,
  and decoded file records (`status`, `filename`, and optional `previous_filename`);
- the captured head SHA; and
- outputs for `diff_ready`, `diff_mode`, `head_sha`, `full_diff_sha256`, and
  `unchanged_since_previous`.

### 7.2 Full PR input

The helper fetches PR base/head metadata before and after every mutable PR API input needed
for preparation, including the paginated Pulls Files response and any numbered server-diff
fallback. It produces a ready result only when both validated metadata snapshots have the
same base and head. JSON decoding produces real path strings; filenames are never
transported through a newline-delimited shell file. It fetches the required commits,
verifies the head object, and prepares a merge-base-to-head diff. The local full diff is
restricted to the API's PR file set, including both `filename` and `previous_filename` for
renames.

If local preparation is unavailable, an explicitly numbered `gh pr diff <pr>` is the only
fallback. If both mechanisms fail, `diff_ready=false`; the model does not run and no
checkpoint advances.

### 7.3 Incremental input

An incremental diff is permitted only when:

- the previous state is valid and has a successful SHA;
- that SHA and the captured head are available commits;
- the previous SHA is an ancestor of the captured head; and
- the PR file list was fetched successfully.

Python passes decoded paths as an argument vector to `git --literal-pathspecs diff`, so
Unicode and embedded-newline names remain single path arguments. Renames include both old
and new names. If filename retrieval fails, the helper uses the already prepared full PR
diff; it never runs an unrestricted `previous..head` diff.

If the full PR diff hash equals the prior successful state's hash, the run is
`unchanged_since_previous`: the model is skipped, the previous body is preserved, and the
checkpoint may advance to the current head after the ordinary current-head/run-order
checks. If a computed delta is empty but the full hash changed, the helper fails back to a
full review rather than claiming no change.

## 8. Reviewer-Specific Flow

### 8.1 Claude

- Collect only validated v2 state and sanitized human discussion.
- Prepare full/delta input through the shared action.
- Remove model-side unnumbered or repository-wide diff fallback.
- Sanitize model output before computing success.
- Upsert only after diff, output, head, and generation checks pass.
- Preserve and mark the prior review as stale on a genuine latest-head failure.

### 8.2 Gemini

- Use the same state collector, preparation outputs, and compare-before-write rules.
- Retain `-U20` context because Gemini cannot inspect surrounding repository files.
- Retain bounded 429 retry behavior, but cancellation and generation checks prevent a
  delayed retry from overwriting a newer result.
- Sanitize before success evaluation exactly as Claude does.
- Bind the inline Slice 1 full diff to equal validated head reads before and after fetch;
  Slice 2 moves that invariant into the shared helper.
- A prompt truncated by Gemini's bounded input limit is partial coverage: its model body
  cannot advance `successful_head` or the stored full-diff hash.

### 8.3 OpenCode

- Prepare an authoritative full PR diff before invoking the CLI; a missing input skips the
  CLI and fails closed.
- Tell the reviewer to treat that file as the exclusive set of changes under review.
- Require every new finding to provide a changed anchor, formatted as `path:line`, and allow
  unchanged lines only as supporting evidence with an explicit causal explanation.
- Require disproven prior findings to be reported as `Retracted`, not `Resolved`.
- Snapshot comments before the CLI call. Pinned OpenCode 1.18.17 creates a fresh working
  comment per run, so after the call identify exactly one marker-bearing bot comment whose ID
  is absent from that snapshot. Strip reserved lines and wrap it in the machine-generated v2
  envelope. Zero, multiple, reused, or unverified candidates fail closed and produce no
  trusted state. If a future pinned CLI changes to update an existing comment, this deliberate
  compatibility cost is a fail-closed review until the lifecycle contract is revalidated and
  revised.
- Previous context is drawn only from that canonical envelope; arbitrary marker-containing
  comments are ignored.

## 9. Finding Scope Contract

All three prompts use the same semantic rule:

- A new finding must cite at least one changed line from the prepared PR input.
- An unchanged line may be cited only as supporting evidence after identifying the changed
  anchor and explaining the concrete data/control-flow impact.
- A real current line without PR causality is insufficient.
- A previous finding that no longer applies because the original claim was wrong is
  `Retracted`; `Resolved` requires a code change that fixes the finding.
- If a changed anchor cannot be supplied, omit the finding.

Prompt-contract tests assert these requirements. The project does not attempt to prove the
semantic explanation automatically, but OpenCode's canonicalization step rejects a new
finding section that lacks the required changed-anchor form. It parses the final
`:<decimal line>` delimiter so legal colons in Git paths remain supported, verifies the path
against the scope manifest, and verifies the line against an added-side hunk derived from
the recorded merge-base and head. Claude and Gemini remain bounded by their prepared diff
input and receive the same instruction.

## 10. Failure Behavior

| Condition | Model runs | Comment mutation | Checkpoint |
| --- | --- | --- | --- |
| Full PR input unavailable | no | latest-head failure/stale stamp only | unchanged |
| Model/action failure | attempted | latest-head failure/stale stamp only | unchanged |
| Sanitized output empty | attempted | latest-head failure/stale stamp only | unchanged |
| PR head changed during input preparation | no | none | unchanged |
| PR head changed after preparation | result discarded | none | unchanged |
| Stored run is newer | result discarded | none | unchanged |
| Previous state invalid | yes, full review | success only after normal gates | current head |
| Full diff hash unchanged | no | preserve body, advance canonical state | current head |
| Delta empty but full hash changed | yes, full review | normal success/failure path | gated |

Human-comment API failure removes discussion context but does not broaden code scope.
PR-metadata or head-validation failure cannot produce a successful checkpoint. PR-file,
commit, or local-diff failure may continue only when the explicitly numbered server-side
full PR diff fallback succeeds.

## 11. Implementation Slices

### Slice 1: trustworthy state

Modify:

- `.github/workflows/claude-code-review.yml`
- `.github/workflows/gemini-auto-review.yml`
- `.github/workflows/opencode-auto-review.yml`
- `tests/test_review_workflow_logic.py`

Implement v2 envelopes, sanitization-before-status, current-head/run-order checks,
reviewer/PR concurrency, OpenCode canonicalization, and legacy migration.

### Slice 2: exact diff and finding scope

Add or modify:

- `.github/actions/prepare-review-diff/action.yml`
- `.github/actions/prepare-review-diff/prepare_review_diff.py`
- the three auto-review workflows;
- release verifier and release-bundle tests so the new action is shipped atomically;
- prompt-contract and executable diff-preparation tests; and
- workflow contract documentation.

Implement argv-safe PR file handling, rename preservation, fail-closed fallback, full-diff
hashing, unchanged-input handling, and changed-anchor prompt requirements.

## 12. Test Strategy

Tests execute the production parser/helper paths rather than string-only stand-ins.

### State and ordering

- foreign bot quotes each reviewer marker;
- malformed, mismatched-reviewer, mismatched-PR, and invalid-run envelopes;
- legacy comment migration;
- infra-only model output with and without an existing review;
- diff-unavailable text cannot become success;
- H1/H2 success/failure completion in every ordering;
- head changes immediately before upsert;
- head changes during diff preparation, plus the stable-head positive preparation path;
- failed latest attempt preserves body and successful SHA but records stale state.

### Diff preparation

- first-round full review and valid incremental review;
- shallow clone recovery and force-push/non-ancestor fallback;
- PR-files API failure and full-diff failure;
- different valid PR metadata snapshots around mutable API reads fail closed, while equal
  snapshots bind the exact head and full hash;
- mixed ASCII plus Unicode and embedded-newline filenames;
- glob-like paths such as `[id].tsx`;
- rename, deletion, binary, executable-mode, symlink, and submodule changes;
- unchanged full-diff hash and empty-delta/hash-changed behavior;
- sufficiently many paths to exercise argument-size handling or its explicit bound.

### Prompt and integration

- each reviewer requires a changed anchor or explicit causal chain;
- OpenCode `Resolved` versus `Retracted` wording;
- prepared diff filenames and modes reach the correct reviewer;
- full repository suite, release verifier, release bundle, catalog, and fleet audit tests;
- `actionlint` when available, plus `git diff --check`.

## 13. Rollout and Stop Conditions

1. Land Slice 1 and dogfood sequential and overlapping pushes in the automation repository.
2. Land Slice 2 and dogfood an ordinary PR plus a synthetic unusual-path PR.
3. Publish a new immutable annotated automation tag only after both slices pass tests and
   independent code review.
4. Roll out to a small representative canary set.
5. Audit canaries before fleet-wide rollout.

Stop and do not publish when any run can advance `Reviewed` without prepared coverage, any
stale run can mutate newer state, any foreign marker is accepted, or any PR path is silently
lost. Dedicated reviewer Apps, a general review service, and model changes remain follow-up
work outside this design.

## 14. Risks and Mitigations

- **`$/` runner compatibility:** exercise the self workflows before release and keep the
  existing inline implementation available for immediate rollback. The fleet runs on
  GitHub-hosted cloud Actions, where `$/` is documented.
- **One forced full review during migration:** accepted as a safe, bounded cost; legacy
  metadata is not trusted.
- **OpenCode comment-shape variance:** snapshot and post-run candidate identification must
  require exactly one candidate and fail closed otherwise.
- **Additional API calls:** paginate PR files once per run and clip human context as today;
  exact-prefix and schema checks remain local after the existing comment fetch.
- **Prompt non-compliance:** machine gates own state and scope; malformed OpenCode new-finding
  output is not accepted as a trusted successful review.
