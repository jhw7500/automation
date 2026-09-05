# Shared Workflow Consumer Contract

Consumer repositories keep thin event/permission/authentication callers. Non-trivial AI
behavior remains in reusable workflows published by `jhw7500/automation`.

## Authoritative managed set

`scripts/workflow-catalog.json` is the only managed-path and caller-contract authority.
It defines **14 managed caller workflows**: ten required callers and four optional callers.
It also declares the managed config path and the retired
`.github/workflows/bump-automation-ref.yml` path. Repository membership and the only
allowed profile differences come from `scripts/workflow-config.json`.

The canonical source bytes live only under:

```text
examples/baseline-workflows/.github/
```

Do not maintain another filename list or copy caller files with an ad-hoc script.
Project-owned build, test, packaging, deployment, release, lint, hardware, and other
workflows are outside the catalog and remain byte-for-byte repository-owned.

## Immutable automation identity

Every reusable `uses:` target in a rendered caller ends with the verified
**40-character commit** resolved from an immutable annotated release tag:

```yaml
uses: jhw7500/automation/.github/workflows/gemini-review.yml@0123456789abcdef0123456789abcdef01234567
```

Tag text is retained only as human-readable identity in the consumer config:

```yaml
automation_ref: v1.40.1
automation_commit: 0123456789abcdef0123456789abcdef01234567
```

The renderer may update only these two scalars in an existing
`.github/workflow-config.yml`; all other keys, formatting, and comments are preserved.
An explicitly approved bootstrap creates the canonical disabled config. Never hand-edit a
caller to use a moving tag or branch.

## Same-name credential mappings

Model credential names are fixed by authentication family, and every mapping uses the
same repository secret name on both sides:

```yaml
# Claude callers
CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}

# Every Gemini caller
GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}

# Profiled OpenCode callers
ZHIPU_API_KEY: ${{ secrets.ZHIPU_API_KEY }}
```

Bulk or wildcard secret forwarding is forbidden. A caller maps only the names declared by
its catalog entry. Workflow rollout checks whether prerequisite names exist but never
reads, refreshes, or writes their values.

## Explicit Gemini repository-write modes

Gemini model authentication always uses `GEMINI_API_KEY`. Repository-write
authentication is an independent profile axis with exactly two modes.

### GitHub App mode

```yaml
with:
  repo_write_auth: github_app
  app_id: ${{ vars.APP_ID }}
  publisher_app_id: ${{ vars.APP_ID }}
secrets:
  APP_PRIVATE_KEY: ${{ secrets.APP_PRIVATE_KEY }}
  GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
```

Both `APP_ID` and `APP_PRIVATE_KEY` must exist. The App is used only for the central
workflow's declared repository write operations. Beginning with `v1.46`, the Gemini auto-review
workflow resolves the authentication action through `$/`, binding the helper and its outputs to
the same immutable automation commit as the reusable workflow. The authentication action exposes the
server-derived publisher login as `<app-slug>[bot]`; review-state collection and publication use
that exact login rather than trusting an arbitrary comment whose author type is merely `Bot`.
`publisher_app_id` is the same non-secret App ID and is retained in canonical callers so a later
switch to built-in-token mode can authenticate the existing App-authored sticky without retaining
the private key.
Actions run provenance is fetched separately with the job-scoped built-in token and
`actions: read`; the App installation does not need an added Actions permission.

### Built-in token mode

```yaml
with:
  repo_write_auth: github_token
  publisher_app_id: ${{ vars.APP_ID }}
secrets:
  GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
```

This mode passes neither the token-minting `app_id` nor `APP_PRIVATE_KEY`; the reusable workflow uses the
exact built-in `${{ github.token }}` path within its declared permissions. Ambient
authentication, OIDC model authentication, and alternate Gemini provider variables are
not supported. In this mode the current publisher login is exactly `github-actions[bot]`.
`publisher_app_id` is an optional identity-only hint: it is empty for repositories that never used
App mode, and otherwise names only the former publisher App. It never mints a token or changes
permissions.

Gemini sticky migration is deliberately narrower than accepting every bot. The current resolved
publisher always matches exactly. On `github_token → github_app`, only the official GitHub Actions
App (`id=15368`, slug `github-actions`) is accepted as the former publisher. On
`github_app → github_token`, only a comment whose `performed_via_github_app.id` equals the explicit
`publisher_app_id` and whose author login is exactly `<performed slug>[bot]` is accepted. The normal
schema and Actions-run provenance checks still apply before the comment contributes state or is
updated in place. An absent, malformed, mismatched, or arbitrary installed App remains untrusted.

## Triggers, inputs, and permissions

Each catalog entry owns the exact trigger shape, caller job name, typed `with` inputs,
permissions, and secret names. Required callers must exist in every configured repository.
An optional caller exists only when selected by that repository's declarative profile;
unexpected optional callers are removed by the managed PR.

OpenCode callers accept only same-repository pull request content and fail closed for
fork/external heads. Their caller ceiling is exactly `actions: read`, `checks: write`,
`contents: read`, `pull-requests: write`, and `issues: write`; they force the job-scoped
GitHub token and do not grant OIDC. The reusable workflow narrows each job below that ceiling:
the prepare job has read-only API access, the model job has empty permissions and produces only an
untrusted artifact, and only the clean canonicalizer can write a comment or durable Check receipt.
Central workflows own the pinned OpenCode CLI archive and action versions; consumers do not add
an installer.

Claude and Gemini callers retain only the catalogued permissions, including `actions: read` for
authenticating prior sticky-state run attempts. Repository-specific
trigger or permission changes require an explicit catalog/design change rather than an
in-place consumer exception.

## Repository config behavior

The shared review default remains:

```yaml
review:
  auto: false
```

For auto-review callers, `workflows.<name>.auto` takes precedence over `review.auto`.
Beginning with `v1.59`, a repository that sets neither key resolves to `default_auto_false`, so
the managed reviewers stay off until a pull request opts in. A repository that never adds the key
therefore behaves exactly like one that copied the baseline above. Releases through `v1.58`
resolved that same absent-key case to `default_auto_true`.
Manual mention/comment and manual-dispatch behavior remains available when the applicable
caller is enabled. The disabled bootstrap template uses `workflows.<name>.enabled: false`
for every common caller; enabling any of them is a later repository-owned PR.

## Review controls and external App operation

`review:request` and `review:skip` control only the managed Claude, Gemini, and OpenCode
workflows. They do not control GitHub App reviews. The resolved managed-workflow mode is, in
order: both labels are `conflict`; only `review:request` is `request`; only `review:skip` is
`skip`; and neither label is `auto`. Only with neither label may a `workflow_dispatch`
`force_review` change `auto` to `request`. `conflict` fails before a model is invoked. Draft
PRs, `skip`, unsafe forks, and closed PRs succeed without invoking a model. `request` never overrides
`workflows.<name>.enabled: false`, and `workflows.<name>.auto` still takes precedence over the
baseline `review.auto: false`. With neither label and neither configuration key, `auto` resolves
to `default_auto_false` and no model is invoked; `review:request` is the per-pull-request opt-in.

Beginning with `v1.51`, the closed release inventory also contains exactly these regular,
non-executable `100644` files:

```text
.github/actions/resolve-review-policy/action.yml
.github/actions/resolve-review-policy/resolve_review_policy.py
```

Release verification authenticates that exact composite-action interface and helper, requires each
of the Claude, Gemini, and OpenCode reusable workflows to call the resolver exactly once, and checks
the exact ready-for-review, draft-guard, label, dispatch, and configuration-precedence wiring in
their managed callers. These inventory and semantic checks apply only to `v1.51+`; `v1.50` and every
earlier release retain their existing closed inventories and historical caller contracts.

`/jhw:pr` posts `@codex review` and `/gemini review` after the final ready head, so manual
App review remains available. Operators must keep Codex Code review enabled while disabling
Automatic reviews in ChatGPT Codex settings. Gemini disables only PR-open automatic review:

```yaml
code_review:
  pull_request_opened:
    code_review: false
```

This uses only `pull_request_opened.code_review: false`; it does not set `code_review.disable`
and preserves unrelated existing Gemini configuration keys. `review:skip` cannot cancel an App
or managed-workflow review that has already started. Fleet activation stops unless an operator
confirms the Codex setting and the Gemini configuration is mechanically verified.

To roll back the external-App opt-in, restore only Gemini's
`pull_request_opened.code_review` value to `true` and re-enable Automatic reviews in ChatGPT
Codex settings; keep Codex Code review enabled. No label or managed-workflow policy changes are
needed for this rollback.

### Opt-in per review channel

Every review channel is off by default and requires an explicit opt-in:

| Channel | Default | Opt-in |
| --- | --- | --- |
| Managed Actions reviewers (Claude, Gemini, OpenCode) | off, from `default_auto_false` or the baseline `review.auto: false` | the `review:request` label, `workflows.<name>.auto: true`, or `review.auto: true` |
| Codex Code review | off, because Automatic reviews stays disabled in ChatGPT Codex settings | a `@codex review` pull-request comment |
| Gemini Code Assist App | off, from `pull_request_opened.code_review: false` | a `/gemini review` pull-request comment |

`/jhw:pr --review` applies the label and posts both mentions, so opting one pull request into all
three channels is a single command.

The labels themselves are a fleet precondition. Every configured repository must define
`review:request` (`0E8A16`, "Explicitly request AI review"), `review:skip` (`BFDADC`,
"Explicitly skip AI review"), and `review-budget-override` (`D93F0B`, "Authorize one bounded
reviewer override round"). From `v1.64` the rollout plan, publish, and audit tools read the
repository's label names next to its secret and variable names and report a repository whose
names are missing as `blocked` with `missing labels: ...`; the tools never create labels. The
explicit, idempotent operator step is `scripts/ensure_review_labels.py`, which reports missing
labels and color/description drift, creates the missing ones with `--confirm`, and repairs drift
with `--confirm --normalize`.

From `v1.64` the managed callers also subscribe to `labeled`, guarded so that only the
`review:request` label starts a run: `github.event.action != 'labeled' ||
github.event.label.name == 'review:request'`. Adding `review:request` to an already-open,
non-draft pull request therefore starts one review of its current head without a new commit,
a ready transition, or a dispatch, provided that workflow is enabled in
`.github/workflow-config.yml`; the `labeled` payload carries the updated label list, so the
caller's `review_mode` resolves to `request`. Unrelated labels start nothing, and the event is
never a cancellation trigger. Three consequences follow from contracts that did not change:
adding the label while another run is still resolving fails that run closed with
`review_mode_label_mismatch` — so labeling a non-draft pull request right after opening it
leaves one red `opened` run beside the `labeled` run that reviews, which is the expected
steady state of that sequence; adding it to a pull request that already carries `review:skip`
starts a run that fails closed with `review_label_conflict` before any model is invoked, so
remove `review:skip` first and re-add `review:request`; and a head that a reviewer already
reviewed is refused with `duplicate_head` — the label reviews each head once, and a same-head
re-review still needs the override round below. On draft pull requests the label starts nothing
until the pull request is marked ready. Before `v1.64` the callers subscribed to `opened`,
`synchronize`, and `ready_for_review` only, so the label had to be applied before the final
ready head — `/jhw:pr --review` labels the draft and then marks it ready — or an already-open
pull request needed a `workflow_dispatch` carrying `force_review: true`.

A bounded override round (`review-budget-override` plus a `workflow_dispatch` carrying
`force_review: true`) is available for Claude and Gemini only; from `v1.64` the callers'
`force_review` input says so ("Perform one authorized same-HEAD override round; requires the
review-budget-override label"), because a dispatch without the label is refused as
`round_budget_exhausted` even on a first review. From `v1.62` the budget refuses it
for OpenCode, because that canonicalizer authenticates `pull_request` provenance throughout and a
dispatch round can never publish: spending the override there consumed it and lost the verdict.

`review:skip` and `review:request` steer only the managed Actions reviewers. They never reach
Codex, whose automatic review is decided entirely in ChatGPT Codex settings, so neither label
changes Codex behavior. Codex does require the repository to be registered under Codex code-review
settings; a custom cloud environment is not required, because the default `universal` image serves
review. An unregistered repository either answers a mention with "create an environment for this
repo" or stays silent, and no review appears.

### When to add the label

`review:request` may be added to an already-open pull request from `v1.64`. A label that moves
while the `opened` runs are still resolving still disagrees with them: those runs were triggered
without the label, so `REVIEW_MODE` is `auto` for them, and the resolver reads the pull request
afterwards.

From `v1.66` that disagreement **declines** the run rather than failing it. The resolver returns
`run-review=false` with reason `review_mode_label_mismatch`, and the `skipped` job says the label
changed after the run was triggered. Nothing is reviewed under either outcome, so failing only
reported a broken reviewer for an ordinary opt-in race, and the run started by the label carries
the verdict. Before `v1.66` all three reviewers failed together and a re-run reproduced it,
because the replayed payload still carries the original mode.

**A manual dispatch still fails.** `workflow_dispatch` with `force_review` has no follow-up run,
so declining would drop an explicit request; a label that contradicts it is a `PolicyError`.

Labelling after the `opened` runs conclude, or draft → label → ready, avoids the extra runs
entirely. The `labeled` run is unaffected either way: it is triggered with the label present.

### Why a managed reviewer did not run

From `v1.65` the `skipped` job of `claude-code-review.yml` and `gemini-auto-review.yml` names the
reason instead of always citing `workflow-config.yml`. It reads the `enabled` and `policy_reason`
outputs of `check-enabled` through the environment, rejects anything outside the resolver's fixed
`^[a-z_]*$` vocabulary, and reports:

| Condition | Reported as |
| --- | --- |
| `enabled` is not `true` | disabled in `.github/workflow-config.yml` |
| `default_auto_false`, `workflow_auto_false`, `review_auto_false` | automatic review is off; add the `review:request` label |
| `skip` | the `review:skip` label is present |
| `draft` | the pull request is a draft |
| `closed` | the pull request is not open |
| `unsafe_pr` | the head is not in this repository |
| `review_mode_label_mismatch` | the label changed after the run was triggered; the labelled run carries the verdict |
| empty | no reason was produced; read the `Check if enabled` job |
| anything else | the reason verbatim |

Since reviews became opt-in a decline is the ordinary path, so the notice is the first place a
person looks. Before `v1.65` every one of these said the workflow was disabled in
`workflow-config.yml`, which sent them to a file whose contents were already correct.

From `v1.66` `opencode-auto-review.yml` reports the same way. It previously named the
cross-repository case in a fixed sentence, which the shared vocabulary keeps as `unsafe_pr`;
leaving it behind would have let it answer a declined label change with three false causes.

The `skipped` job's `if:` condition is unchanged: it still covers both causes, and the release
verifier requires it to keep testing `policy_run != 'true'`.


### What a manual `/review` sees

`@gemini-cli /review` is handled by the `review` job of `gemini-dispatch.yml`. Until `v1.67` that
job had no diff: it checked out the reviewed commit and told the model to look beyond the change,
so the model could only answer with general architecture advice and never named anything the pull
request had touched.

The job now builds the diff itself, in a plain step rather than through the shared
`prepare-review-diff` action. That action resolves the live head, which would contradict
`/review commit=<sha>`, and it deletes its output while still exiting zero, so a failure would be
indistinguishable from having no diff at all.

| Input | Source |
| --- | --- |
| head | `reviewed_sha`, so `commit=<sha>` is honoured |
| base | `base_sha`, taken from the pull request the dispatch job already fetched |
| incremental base | `last_success_sha`, when `incremental=true` and it is an ancestor of the head |

The `issue_comment` event that carries `/review` has no `pull_request` object, so the base cannot
be read from the payload. It comes from the `pulls.get` call the dispatch job already makes.

The range is `base...head`, so `commit=<sha>` reviews everything the pull request accumulated up
to that commit rather than that commit alone. `incremental=true` is how a single round is asked
for, and it falls back to the pull request base with a notice when the recorded head is not an
ancestor.

The diff is written into the workspace and the prompt names the file, the range, and an inline
`git diff --stat`. The summary is there so that a model which cannot read the file still knows
what changed, rather than reproducing the failure this contract removes.

**A diff that cannot be produced stops the round.** The step reports `diff_ready=false` with one of
`reviewed_sha_invalid`, `base_sha_invalid`, `max_bytes_invalid`, `base_commit_unavailable`, or
`diff_too_large`, writes no diff behind it, and the model steps do not run. The reason reaches the
sticky comment, because a silent skip would look like the original defect. The checkout carries no
credentials, so a base object missing from the local history is reported rather than fetched.

`GEMINI_MAX_READ_BYTES` bounds both the diff written here and what the model may read, so the job
cannot leave behind a file too large to be read. Measured pull requests across two repositories sit
at a median near 9 KB, with about 7% above the 200,000-byte default.


## Deterministic automated-review input

Claude, Gemini, and OpenCode call the same composite action:

```yaml
uses: $/.github/actions/prepare-review-diff
```

In a called reusable workflow, `$/` resolves the local action from the repository and commit
that contain the called workflow, not from the consumer checkout. The workflow and helper
therefore travel together at the immutable automation commit selected by the caller. Release
verification requires both regular `100644` action files for `v1.45+` and rejects a workflow
dependency without that inventory; the historical `v1.44` inventory remains unchanged.

Beginning with `v1.46`, Claude and Gemini then call the shared canonicalizer exactly once:

```yaml
uses: $/.github/actions/canonicalize-review
```

OpenCode continues to use only `prepare-review-diff`; adding `canonicalize-review` to OpenCode is a
release-contract violation. The `v1.46+` closed release inventory adds exactly these regular,
non-executable `100644` files:

```text
.github/actions/canonicalize-review/action.yml
.github/actions/canonicalize-review/canonicalize_review.py
.github/actions/canonicalize-review/review_scope.py
```

As with diff preparation, the `$/` reference binds the action and both helpers to the authenticated
automation commit. A `v1.45` or `v1.45.2` release neither requires those future files nor permits a
workflow dependency on them, even if an unrelated tree happens to contain paths with those names.

The composite interface has exactly nine inputs:

| Input | Contract |
| --- | --- |
| `reviewer` | Required; exactly `claude` or `gemini`. |
| `candidate-file` | Required raw provider-output path; untrusted input. |
| `canonical-file` | Required destination for canonical Markdown. |
| `result-file` | Required destination for bounded schema-1 result JSON. |
| `scope-manifest` | Required authenticated `review-scope.json` path. |
| `selected-diff` | Required authenticated full or delta diff path. |
| `diff-mode` | Required; exactly `full` or `delta`. |
| `previous-sha` | Optional authenticated prior successful head; default empty on a first round, but it may remain set when delta safely falls back to full. |
| `previous-review-file` | Optional authenticated prior canonical body; default empty. |

It exposes exactly seven scalar outputs: `document-valid`, `accepted-count`, `filtered-count`,
`normalized-count`, `filtered-max-severity`, `failure-reason`, and, from `v1.62`,
`filtered-reasons`. The first is `true` or `false`; the three counts are non-negative integers;
`filtered-max-severity` is `none`, `MEDIUM`, `HIGH`, or `CRITICAL`; `failure-reason` is empty on a
document-valid result or one fixed hard reason; and `filtered-reasons` is the sorted, comma-joined
set of fixed reason codes for the filtered blocks, empty when none were filtered.

The action captures validated PR base/head metadata, prepares `review-full.diff`, optionally
prepares `review-delta.diff`, and writes `review-scope.json`. Its composite outputs are
`diff-ready`, `diff-mode`, `head-sha`, `full-diff-sha256`, and
`unchanged-since-previous`. The hash is SHA-256 over the exact `review-full.diff` bytes. A ready
manifest has schema `1`, repository, PR number, merge-base SHA, head SHA, and file records with
`status`, `filename`, and optional `previous_filename`.
The file list may be empty only for a tree-equivalent head whose authenticated full diff and local
full-range name-status reconstruction are both empty.
`force-full=true` is reserved for the authenticated force-review entrypoint. It ignores only the
previous SHA/hash optimization and therefore produces the same immutable full PR diff and manifest;
it does not relax PR-head, scope, or output validation.

`prepare-review-diff` requires an explicit `output-directory`; Claude and Gemini bind it to
`${{ runner.temp }}`. Their prior canonical body and context files use the same runner-temporary
boundary. These inputs are therefore outside `${{ github.workspace }}` before the final
`actions/checkout --force` of the captured PR head and cannot be replaced by a PR-controlled
tracked file or symlink. Provider output, canonical output, and result JSON remain fixed workspace
paths, but the workflow unlinks them and creates them only after that final checkout.
OpenCode binds the directory explicitly to `${{ github.workspace }}` because its prepare job has no
later checkout; it immediately copies the atomically replaced regular files into its sealed
`${{ runner.temp }}` handoff before the no-checkout model job starts.

The underlying CLI prints one JSON object with `diff_ready`, `diff_mode`, `head_sha`, `base_sha`,
`full_diff_sha256`, `unchanged_since_previous`, and `warning`. The composite output bridge exposes
the five workflow-facing scalars above without changing stdout. `warning` records a safe
incremental fallback to the already prepared immutable full diff; it does not by itself make a
ready result unavailable. `unchanged_since_previous` is true only in `unchanged` mode.

| Mode | Artifacts and selection | Reviewer/checkpoint behavior |
| --- | --- | --- |
| `full` | `diff-ready=true`; the unrestricted local `merge-base..captured-head` full diff and local manifest exist; delta is absent. This covers a first review, an unusable/non-ancestor previous SHA, an empty delta whose full hash changed, an incremental preparation/argv failure, or a head whose final tree equals the merge-base tree. The last case has an exact zero-byte full diff and `files: []`. | Claude and Gemini read the full diff. OpenCode always reads the sealed full diff. A model result can advance only after the ordinary output, head, and generation gates. An authenticated empty full scope can produce only a clean canonical result because no changed anchor can validate. |
| `delta` | `diff-ready=true`; full diff, non-empty ancestor-to-head delta, and manifest all exist. The previous successful SHA is an available ancestor, and the delta is restricted to paths present in the immutable final full-range manifest. | Claude and Gemini read the delta as their exclusive changed set. OpenCode still reads the full diff. The stored hash is always the full-diff hash. |
| `unchanged` | `diff-ready=true`; full diff and manifest exist, delta is absent, and the full-diff hash equals the validated previous full hash. | No model runs. The prior non-empty body is preserved, and the successful head/hash may advance only when authenticated prior success, exact hash equality, current-head, and run-generation checks all pass. |
| `unavailable` | `diff-ready=false`; the mode is `unavailable`, the full hash is empty, and staged full/delta/manifest outputs are removed. | No model runs and no `Reviewed` checkpoint advances. A latest-head failure/stale record may be written only through the normal head/generation gate; a head that changed during preparation causes the later head gate to reject comment mutation. |

Expected external or mutable-input failures return a controlled `unavailable` result so workflows
can record fail-closed state; an invalid CLI invocation or internal corruption is nonzero. A
successful full/delta model attempt additionally requires sanitized, non-empty output (and Gemini
must not have truncated the prepared diff). Failure preserves a valid earlier successful
body/head/hash but does not claim coverage of the attempted head.

### Full and incremental preparation

The helper captures PR base/head metadata, fetches and verifies those exact commit objects,
computes the merge base, and prepares both authoritative artifacts only from the local immutable
`merge-base..captured-head` graph. The full diff is unrestricted; the manifest is parsed from
`git diff --name-status -z --find-renames` without a record ceiling. Neither Pulls Files nor
`gh pr diff` supplies, repairs, or restricts checkpoint scope. Consequently ABA-shaped server
views and PRs beyond the Pulls Files 3,000-file limit cannot omit a locally changed path. A final
metadata read is only a base/head equality gate; a different or malformed snapshot removes all
outputs and returns `unavailable`. Every diff forces `--ignore-submodules=none`, and commit,
merge-base, ancestor, and diff identity operations use `--no-replace-objects`, so local Git
configuration cannot hide a submodule pointer or replace an authoritative object.

An incremental `previous..captured-head` diff is attempted only when the previous commit is a
local ancestor and is restricted to the old/current path identities in the immutable final
manifest. Thus a path changed after the previous checkpoint but restored to merge-base content by
the captured head is excluded. If incremental preparation is unavailable—including an unsafe
argument-vector size—the helper uses the already prepared immutable full diff. If exact objects,
the local full diff, or the local manifest cannot be prepared, the result is unavailable; there is
no mutable server-diff fallback.

Git's NUL-delimited records are decoded strictly as UTF-8 and transported directly as Python
strings. Each delta filename remains one subprocess argument to `git --literal-pathspecs diff`, so
Unicode, embedded newlines, glob-like characters, leading dashes, and other legal UTF-8 path
strings are not reinterpreted through a newline-delimited file or shell. Rename/copy records retain
both old and current names in one manifest record and both are de-duplicated into the literal path
restriction. Malformed or non-UTF-8 records fail closed. Deletions, binary changes, executable
modes, symlink targets, submodule pointers, and rename metadata remain part of the prepared input.

### Changed-anchor contract

All three reviewers are instructed that every new finding needs a changed path-and-line anchor.
Unchanged surrounding code is supporting evidence only after a concrete causal explanation from
that anchor. A real current line without PR causality is insufficient; a disproven prior claim is
`Retracted`, while `Resolved` requires a code change.

Claude and Gemini enforce this as a prompt contract over their exclusive prepared artifact.
The Claude command and review model steps use `anthropics/claude-code-action` v1.0.204 at the
immutable commit `6bcfb8263aca9b0eab0aba20d96dddd74de2875f`. Moving major tags are not accepted:
the release gate for v1.45.3 and later requires both exact action references so an upstream CLI
bump cannot change runner behavior between otherwise identical workflow attempts.
OpenCode additionally enforces it in the clean canonicalizer. A candidate must contain exactly one
`### New findings` section whose body is exactly `None` or one or more `####` finding blocks. Each
`New findings`, `Still open`, and `Retracted` block needs exactly one canonical one-line JSON anchor,
`- Changed anchor: {"path":"path/to/file","line":1}`, and exactly one matching JSON source line,
`- Current line: "exact complete added-side source line"`. A `Resolved` block normally uses that
same pair. If the exact authenticated prior evidence line was deleted in the current round, a
`Resolved` block may instead copy it exactly as `- Removed anchor: {"path":"path/to/file","line":1}`
and `- Removed line: "exact previous source line"`; no other section may use that alternative, and
current and removed pairs cannot be mixed. JSON string escaping makes every UTF-8 path reversible,
including embedded newlines, backticks, colons, Unicode, and leading dashes. Duplicate keys, extra
keys, alternate/noncanonical serialization, malformed JSON, empty paths, non-positive or unsafe
line integers, and evidence-like noncanonical field labels invalidate that finding block. Invalid
`New findings` blocks are filtered as described below; invalid authenticated carryover or
disposition blocks fail closed. Canonical JSON rendering
also escapes every character that Python `splitlines()` recognizes as a
line boundary, including U+0085, U+2028, and U+2029, so a validated escaped path or quotation cannot
poison the next round's strict prior-document layout.
Markdown link/image, HTML tag/comment, and HTML entity wrappers are normalized only for
reserved-label detection.
Markdown destinations are consumed with balanced delimiters and quoted-title state rather than a
greedy match. The complete semicolon-terminated HTML5 alias set whose decoded value consists only
of whitespace, invisible format characters, supported decorators, or a colon is normalized;
raw and numeric-entity Unicode format controls are normalized to spaces on the same detection-only
path. Unrelated and unknown named entities remain literal. Wrapper syntax therefore cannot
disguise a second unverified evidence field without turning benign prose into a reserved label. The
parser then:

1. verifies the sealed full diff and manifest hashes and their repository, PR, merge-base, and head
   identity;
2. matches the anchor against the current `filename` in the manifest (not
   `previous_filename`) and rejects removed files;
3. re-derives exactly one NUL-delimited local name-status record from the manifest's immutable
   `merge-base..head` graph, using both old and current literal path arguments for rename/copy
   records, and requires its status and path identities to equal the sealed record; and
4. derives zero-context hunks for that same record and graph with `/usr/bin/git`,
   `--no-replace-objects`, `--no-ext-diff`, `--no-textconv`, `--find-renames=50%`, and
   `--ignore-submodules=none`. It also forces `--inter-hunk-context=0` and `--no-color`, then
   validates every hunk's old/new counters through the complete patch body and accepts only line
   numbers consumed by actual `+` records. Context, deletions, and the no-newline control record
   never become anchors. Hunk coordinates must advance without overlap on both sides, and an empty
   old/new hunk is invalid. A no-newline control must immediately follow a body record that exhausts
   its relevant side; once that side is EOF-marked, a later hunk cannot reopen it. Malformed or
   truncated hunk bodies, counters, coordinates, and controls fail closed; diff prelude metadata is
   not treated as hunk-body evidence.

After the top-level document and section grammar validates, OpenCode checks each `New findings`
block independently. A block with malformed, missing, mixed, or noncanonical evidence fields is
omitted with reason `finding_grammar_invalid`. A syntactically valid block whose path is absent or
removed, whose line is not an actual added-side line, or whose quoted current line does not match is
omitted with reason `anchor_out_of_scope`. Valid blocks—including `[HIGH]` blocks—remain unchanged.
If every new block is omitted, the canonical section becomes exactly `### New findings` followed by
`None`; the successful attested comment reports `filtered_invalid_new_findings=N` and the sorted
set of filtering reasons on its visible validation line. Whenever that count is nonzero, the next
line identifies the untrusted source as artifact `opencode-candidate-<run_id>-<run_attempt>` and
file `review.md`; the adjacent run URL opens the Actions run that owns the artifact. The artifact is
retained for one day, and this workflow-owned location line is excluded from later review context.
Git invocation or parsing failures, sealed
manifest inconsistencies, malformed document or section grammar, and invalid carryover or
disposition evidence still fail the checkpoint without advancing `successful_head`.

The sole zero-record case is an exact empty full review: the sealed manifest has `files: []`, the
selected full diff is a zero-byte regular file, and local full-range name-status reconstruction is
also empty. An empty delta, non-empty selected diff, or non-empty Git reconstruction remains
`scope_invalid`. The resulting scope has no changed anchors, so only a structurally valid clean
canonical result can be published; claimed findings are filtered because none can bind a changed
anchor.

For the `Resolved`-only removed-evidence alternative, the canonicalizer first requires an exact
path, old line number, and line-content match against the unique authenticated active prior block.
It then requires that prior successful HEAD to be an ancestor of the current attempt HEAD and
re-derives their literal-path, zero-context diff with the same hardened Git options. The exact old
line must be consumed by a real `-` record, the prior path must have one non-rename/non-copy status
record, and the same content must not be re-added anywhere in the authenticated prior-to-current
diff. This permits deletion-only fixes even when the file disappears from the base-to-current
manifest, without treating a pure rename as a fix or allowing an unsupported `Resolved` or
`Retracted` disposition to advance the checkpoint.

Rename preparation consequently transports both old and current identities, but a reportable
anchor names a changed added-side line in the current filename. A pure 100% rename has no such
line, while a renamed file with a real addition can cite that addition. The explicit submodule
override keeps a changed gitlink reportable even when tracked or local configuration says to ignore
submodules. Exact-record matching prevents another path's hunk from satisfying the anchor. The
workflow does not infer additions from a hunk header's whole new-side span, so repository-local
inter-hunk merging cannot turn an unchanged bridge line into a valid anchor. It machine-checks
anchor form and changed-line membership; the causal explanation for supporting unchanged evidence
remains a semantic review requirement.

### Candidate and carryover grammar

A Claude or Gemini candidate is one bounded UTF-8 document with exactly one `### New findings`
section. Its body is exactly `None` or one or more `####` blocks. A new block uses
`#### [SEVERITY] title`, where `SEVERITY` is exactly `CRITICAL`, `HIGH`, or `MEDIUM`, and contains
exactly one canonical `Changed anchor`, one or more canonical `Trigger evidence` objects, one
allowed `Impact class`, and one concrete `Material impact`. The allowed impact classes are
`runtime`, `security`, `data-integrity`, `user-visible`, and `performance`. A performance claim
also has exactly one `Performance basis` object whose kind is `measured` or
`unbounded-amplification` and whose quoted source is validated. `LOW`, style, maintainability, and
cleanup claims are non-actionable rather than blocking findings.

When the changed anchor is a Python exception-handler line, at least one trigger-evidence object
must cite a different throwing or calling line. Repeating only the handler coordinate is
`invalid_trigger_evidence`: the handler itself cannot prove which exception reaches it. The handler
may remain as additional evidence when a distinct trigger is also present. Other direct single-line
findings are unaffected.

The document boundary is closed: non-blank prose before the first allowed section, after the last
section, or in place of a declared carryover section is `ambiguous_document`. A no-finding section
accepts only the closed workflow-owned no-findings form; `None` cannot terminate parsing and hide a
later provider error or caveat. Unknown bullets inside a finding are not silently discarded. They
remain candidate prose and participate in proof-deficit checks, so wording such as “cannot verify”
cannot evade filtering merely by using an unrecognized field label. Accepted supplemental bullets
remain byte-stable canonical prose when the next delta round authenticates and reconstructs the
prior finding; only the closed set of structured field prefixes is excluded from prose.

Carryover sections are `### Still open`, `### Resolved`, and `### Retracted`. Every carryover
heading has the exact form `#### RVW-<12 lowercase hex> [SEVERITY] title` and binds exactly one
authenticated active prior finding. `Still open` must repeat current changed-anchor, trigger, impact,
and material-impact proof. `Resolved` requires a selected-range `Fix anchor` and a resolution;
`Retracted` requires current trigger evidence and a reason disproving the earlier claim. A first
round has no authenticated prior active set, so model-authored carryovers are normalized out instead
of becoming findings.

The model never assigns authoritative IDs. For each accepted new finding the workflow derives
`RVW-` plus the first 12 lowercase hexadecimal characters of SHA-256 over the NUL-separated
reviewer, changed path, changed line, severity, and whitespace-normalized, case-folded title. That
derivation makes later carryover bindings reproducible while keeping them under workflow ownership.

From `v1.63` the canonicalizer also receives the finding IDs a collaborator dismissed through the
review invocation budget (see [Dismissing a finding](#dismissing-a-finding)). A dismissed ID
leaves the authenticated active set before binding: a `Still open`, `Resolved`, or `Retracted`
block naming it is normalized with `dismissed_prior_id`, and a new finding whose derived ID is
dismissed — the model repeating the same claim verbatim — is normalized the same way instead of
re-entering the document. The finding therefore disappears from the canonical Markdown, from
`accepted-count`, and from the remaining finding IDs for as long as the dismissal stands. The
audit trail is the budget ledger, not the review document. OpenCode derives no `RVW-` IDs — its
canonicalizer binds carryover by exact heading text — so a dismissal cannot name an OpenCode
finding until that reviewer assigns IDs.

### Hard checkpoints and soft finding filters

The canonicalizer fails the whole document for exactly these hard reasons: `candidate_missing`,
`invalid_utf8`, `candidate_oversize`, `ambiguous_document`, `scope_invalid`, and
`canonicalizer_error`. A hard failure sets `document-valid=false`, makes the provider attempt a
failed checkpoint, publishes no candidate prose, and cannot advance the successful head or hash.
`candidate_oversize` covers both a raw candidate above 60,000 UTF-8 bytes and a rendered canonical
body above 64,000 UTF-8 bytes. The second bound is checked after canonical JSON and HTML-safe
escaping, before the canonical file is published, and reserves 1,536 bytes for the authenticated
v3 sticky envelope under GitHub's 65,536-byte comment limit.
After an attempted Claude or Gemini canonicalization that does not produce
`document-valid=true`, the workflow uploads the bounded schema-1
`<reviewer>-review-result.json` as a uniquely named, non-overwriting diagnostic artifact for one
day. Each reviewer additionally uploads the rejected raw candidate as
`<reviewer>-candidate-<run>-<attempt>` under that same rejection-only condition, only when the
candidate file actually exists, and with the same one-day, non-overwriting bound, because the
structural code alone cannot separate a prompt/format regression from an over-strict validator.
The upload is bounded to rejected rounds, whose text is by definition never published,
and it stays untrusted provider output that no program reads: the upsert program never names or
opens the raw file, and the failure comment cites only the artifact name, and only when that
upload step reported success. The workspace path is safe because the reset step removes it with
`rm -f --` before generation and the canonicalizer runs only after that reset succeeds, so no
checkout-seeded symlink target survives. A missing result is ignored.

A Gemini provider failure or process-deadline timeout never writes into the candidate file.
Writing it there made the canonicalizer read provider text as a document and report
`ambiguous_document`/`preamble`, so a provider outage looked like a model format regression.
The reason still travels through `gemini_failure_reason.txt` to the sticky comment, and the
provider text is uploaded as its own one-day, non-overwriting `gemini-provider-error-<run>-<attempt>`
artifact — never merged into the canonicalizer-owned diagnostic, which stays bounded schema-1
output. With no candidate file the canonicalizer reports `candidate_missing`, which is what
actually happened.

OpenCode keeps its rejected candidate too. A contract failure previously deleted `review.md`
from the sealed candidate directory, so only the envelope's rule name and sha256 survived. The
raw document is now copied to `$RUNNER_TEMP/opencode-rejected` before that deletion — after the
same regular-file, non-symlink and 60,000-byte checks the success path applies — and uploaded as
its own one-day, non-overwriting `opencode-rejected-<run>-<attempt>` artifact. The sealed handoff
artifact keeps its exact inventory, so the clean job's verification is unchanged. The diagnostic is not authority: neither the upsert program nor later review state or
carryover reads it. For `ambiguous_document`, logs expose only a fixed structural diagnostic code
such as `preamble`, `unknown_section_before_document`,
`unknown_section_after_document`, or `invalid_finding_heading`; candidate text is never copied into
that message. The two unknown-section codes reveal only whether an allowed section had already
started, never the untrusted heading text.

Once the document boundary and trusted scope are valid, a bad individual block does not discard
valid siblings. It is filtered or normalized with exactly one of `invalid_anchor`,
`invalid_trigger_evidence`, `invalid_severity`, `invalid_impact_class`,
`missing_material_impact`, `unsupported_performance_basis`, `non_actionable_category`,
`unknown_prior_id`, `duplicate_prior_binding`, `missing_fix_anchor`, or (from `v1.63`)
`dismissed_prior_id`. Rejected prose is absent
from canonical Markdown, result JSON, workflow state, and the PR comment; only bounded counts,
claimed maximum severity, and fixed reason codes are observable. From `v1.62` the Claude and
Gemini workflows write those reason codes to the run summary whenever `filtered-count` is not zero,
so a reader can see why a block was dropped without opening the run log. The step validates the
codes against the fixed vocabulary and fails closed on anything else. The sticky comment is
unchanged; it still carries counts only. `accepted-count` counts accepted
new and still-open actionable blocks, `filtered-count` counts soft-rejected blocks, and
`normalized-count` counts carryover blocks omitted without becoming actionable, including an
unknown or duplicate prior binding or missing transition proof.

## Canonical automated-review state

Claude and Gemini publish workflow-generated schema-3 state under the exact markers
`<!-- automation:claude-code-review:v3 -->` and
`<!-- automation:gemini-auto-review:v3 -->`. OpenCode deliberately retains its existing v2 marker
and schema. Only a bot comment whose first three lines are the reviewer's exact header, the marker
for that reviewer and schema, and one exact `<!-- automation-state:{...} -->` line is a state
candidate. Claude/Gemini v2 is never accepted as v3, and OpenCode never accepts v3. A marker quoted
later in prose, a different reviewer or PR, malformed JSON, or an extra, missing, or invalid field
is not state. The author must also match the current exact publisher or the explicitly bounded
Gemini mode-migration identity above. From the newest
20 syntactically valid records, the workflow queries the claimed Actions run attempt and retains
only a completed success/failure whose repository, `pull_request` event, PR association number,
immutable run `head_sha`, run ID/attempt, and referenced central reusable-workflow path plus SHA all
agree. The association's `pull_requests[].head.sha` is deliberately not trusted because GitHub
updates it to the PR's latest head after the historical run completes. The
highest authenticated `(run_id, run_attempt)` then wins; a foreign bot or a forged large run ID is
ignored rather than poisoning carryover or the stale-generation guard. Comment ordering and
timestamps do not decide state. Both values are positive safe integers, so a manual rerun of one
authenticated run is newer when its attempt is larger.

Collection and publication each try candidates newest-first; duplicate authenticated generations
prefer the larger comment ID. Once the highest candidate authenticates, older records are irrelevant
and are not queried. An exact-attempt provenance lookup returning HTTP 404 is definitive absence, so
that candidate is ignored and older authenticated state may still be used. Any other lookup error
encountered before selection—including timeout, authorization, rate-limit, or server failure—makes
prior-state selection uncertain. A missing or unreadable issue-comment snapshot is uncertain too;
it is never treated as an empty first-review context. Claude and Gemini then fail closed before model
generation, and the publication step is explicitly gated on successful collection. They create,
update, and delete no review comment, leaving the existing sticky state byte-for-byte unchanged for a
later rerun.

Every envelope has the common fields `schema`, `reviewer`, `pr`, `run_id`, `run_attempt`,
`attempt_head`, `successful_head`, `attempt_status`, `diff_mode`, and `full_diff_sha256`.
`attempt_head` is the head this attempt prepared. The successful pair is atomic:
`successful_head` is a 40-hex reviewed head only when `full_diff_sha256` is its 64-hex full-input
hash; otherwise both are `null`. Status is `success` or `failure`; mode is `full`, `delta`,
`unchanged`, or `unavailable`, and unavailable is never successful. Success requires a non-null
pair with `successful_head == attempt_head`; failure may carry either a null pair or a valid pair
retained from an earlier success.

Claude/Gemini schema 3 additionally emits `review_execution` and the quality fields
`quality_schema`, `accepted_count`, `filtered_count`, `normalized_count`, and
`filtered_max_severity`. `review_execution` is `performed` when this attempt entered the model
step, `reused` only for authenticated unchanged reuse, and `not_performed` when the attempt failed
before a model call. Existing schema-3 records without this additive field remain readable.
The same value appears in the trusted `- Execution:` metadata line, so a reused success cannot be
presented as a new model review. `quality_schema` is always `1`. On full or delta
success the counts are non-negative safe integers and the maximum is `none`, `MEDIUM`, `HIGH`, or
`CRITICAL`. A first schema-3 failure has all four count/severity values `null`. A stale failure that
preserves a prior schema-3 success also preserves that success's four values with its body, head,
and full-diff hash; the values never describe the failed candidate. An authenticated unchanged
success likewise preserves the prior canonical body and all four quality values while advancing
only the permitted checkpoint identity. OpenCode schema 2 has only the ten common fields and no
quality fields.

Both schema contracts require the visible `- Run:` line to be the exact URL-only value
`${{ github.server_url }}/${{ github.repository }}/actions/runs/<state.run_id>`; malformed,
foreign-repository, or mismatched-run URLs are not state. Free-form review prose is untrusted
presentation/comparison data only: reserved header, marker, state, and status lines are
sanitized from model output and are constructed by the workflow, never treated as model
authority.

A v2 Claude/Gemini comment may be reused only as the exact in-place display target. Its state,
body, previous SHA, full hash, finding IDs, validation text, and quality counters supply no v3
authority or re-review context. The first v3 run is therefore forced to full mode, assigns the
first workflow-owned IDs, establishes canonical quality counters, and replaces that display
envelope in place. OpenCode continues its v2 collection and publication path; its historical
unstructured comments remain ignored.

A successful `full` or `delta` checkpoint requires a prepared covered input, a successful model
step, a document-valid canonicalizer result, canonical publication content, and a valid current
write gate. The workflow reads only `claude-review-canonical.md` or
`gemini-review-canonical.md` for successful Claude/Gemini publication; it never reads the raw
candidate in the upsert step. A successful `unchanged` checkpoint instead requires the exact full
hash to match an authenticated prior success and preserves its non-empty canonical body and quality
counters while skipping both provider and canonicalizer.

Every successful Claude/Gemini comment contains the exact workflow-owned line
`- Validation: accepted=N; filtered=N; normalized=N; filtered_max=LEVEL`. `N` values come from the
schema-3 state and `LEVEL` is `none`, `MEDIUM`, `HIGH`, or `CRITICAL`; the visible line is not parsed
back as authority. When no actionable block is accepted—including when every submitted block was
soft-filtered—the canonical body says `No validated blocking issues found.`. That sentence proves
only that this attempt produced zero mechanically validated actionable findings. It is not proof
that the code is clean: filtered candidates can represent unsupported, malformed, or
non-actionable claims, so monitoring reports their count and maximum claimed severity as a warning.
Filtered severity is never rendered as a bracketed actionable label.

Only accepted canonical headings with a bracketed severity (`[CRITICAL]`, `[HIGH]`, or `[MEDIUM]`)
are eligible to block in merge tooling; the configured severity threshold decides which of them
actually blocks. Tooling does not block on `filtered_max`, reason codes, raw provider prose, or a
model-claimed severity that was filtered. A monitoring summary may therefore say, for example,
`Gemini: CLEAN (0 validated blocking findings; 2 candidates filtered, max claimed HIGH)`, but the
word `CLEAN` remains a display classification rather than stronger evidence about the code.

A failure preserves a prior successful body, successful pair, and (for schema 3) quality counters
only when all remain valid. It records the failed `attempt_head` and shows `Status: stale` plus
`Last attempt: failure`; without prior success it is `Status: failure` with no `Reviewed`
checkpoint and null quality values. A stale run, missing/invalid input, empty canonical output, or
invalid prior state cannot advance coverage; an invalid prior state falls back to a full review.

Gemini auto-review has nested finite deadlines so a provider or transport stall cannot occupy a
review round indefinitely: the current SDK request timeout is 200,000 ms, the review subprocess
watchdog is 450 seconds (plus a 15-second hard-kill grace), and the job timeout is 10 minutes. This
reserves 135 seconds of the job budget outside the watchdog window for setup overhead, cleanup, and
sticky publication. The watchdog measures elapsed time and normalizes a hard-kill status (`137`) to
the timeout status only after the configured process deadline has elapsed; an earlier signal exit
remains a generic provider failure. An SDK timeout, a Google API `499 CANCELLED` deadline response,
or the subprocess deadline records `provider_timeout`; the non-cancelled upsert path publishes that
reason as a failed/stale attempt without advancing `Reviewed`. The job timeout is the last-resort
ceiling and remains below the 12-minute `/jhw:ship` review-round deadline.

The successful Gemini path makes one provider request. After an eligible terminal provider,
timeout, quota/rate-limit, empty-output, or truncated-output failure, it may try one configured
fallback model. Primary retries and fallback share the existing three-request ceiling. An
authentication failure, an unsupported caller location, or a canonical-format failure never
triggers the model fallback because changing models cannot repair that failure class. An
unsupported caller location is published as `unsupported_location` rather than collapsed into
`provider_failed`.

A provider-side HTTP `500`, `502`, `503`, or `504` failure receives at most one same-model retry
after a short bounded delay inside the already-claimed review round. If that retry also fails, the
configured fallback may use the final request; without a distinct fallback the failure stops after
two calls instead of spending the full three-call allowance. The retry is counted normally and
must fit within the existing process watchdog. It does not refund the claimed automatic round or
relax duplicate-head admission. Authentication, unsupported-location, invalid-input, and other
non-transient failures are not retried.

Gemini 429 handling separates retry eligibility from the final failure classification. A positive
provider `RetryInfo`/`Please retry in` delay remains authoritative even when the same response
contains a requests-per-day quota ID. Second and millisecond guidance both use the existing
three-attempt bounded backoff, and an eligible retry never waits less than the provider delay.
A requests-per-day response without positive guidance is terminal for that job. For every 429,
the computed backoff (including provider floor and jitter) must also fit inside the remaining
process watchdog; otherwise the response remains terminal with its original `quota_exhausted` or
`rate_limited` classification. Retrying does not weaken the sticky failure state or extend any of
the nested deadlines above.

Immediately before comment mutation, each reviewer refetches the PR head and requires it to
equal `attempt_head`; it also refuses to write unless stored `(run_id, run_attempt)` is
strictly older. Per-reviewer/per-PR concurrency with cancellation reduces overlap. OpenCode
adds fresh generation and head checks before repair, before comment creation, and immediately
before its receipt becomes successful.

OpenCode uses three jobs. A read-only prepare job captures prior comments and provenance,
prepares the diff, and uploads one immutable handoff. The handoff is selected by server-issued
artifact ID, with the upload's raw SHA-256 output, the REST `sha256:` digest, repository/run
identity, an exact conditional file inventory, and per-file hashes all checked. The model job
has empty job permissions, no repository checkout, and no `GITHUB_TOKEN`, `GH_TOKEN`, or
`USE_GITHUB_TOKEN`. It runs pinned OpenCode 1.18.17's generic `opencode run` in a fresh
non-repository directory, sends the prompt on stdin, attaches only `review-full.diff` and
`review-scope.json`, and enables pure/project-config-disabled mode with sharing and all tools
denied. Every non-empty stdout line must be a JSON object; the last completed text event becomes
an untrusted `review.md` candidate, while malformed JSONL or no text event fails closed. Before
upload, a strict outer-format preflight requires the exact review marker, the handoff-bound nonce
on the next line, `New findings` as the first and only required section, unique allowed sections,
and non-empty `None`/finding-block bodies. A valid first candidate incurs no extra model call. If
only this outer framing is malformed, the model job makes exactly one format-only call in the same
tokenless, tool-denied environment, with no repository attachments and with the original candidate
JSON-encoded and explicitly labeled as untrusted data. The repair prompt forbids changing finding
substance. The model job then derives a deterministic signature from each candidate's exact finding
blocks grouped by section plus the `New findings: None` meaning. It accepts only a repaired candidate
with the same signature; dropping, adding, moving, reclassifying, rewording, or changing an anchor is
terminal. Outside the signed sections, only complete literal blocks, structural Markdown
decoration, and exact members of the closed benign wrapper vocabulary may be removed. Any other
nonblank free-form line is ambiguous substance and makes repair terminal, whether it appears before
the first section, after an enclosing fence, in a blockquote, through a link/reference or HTML
entity spelling, or behind an unmatched escaped delimiter. The guard is intentionally independent
of finding keywords: generic prose can describe a real defect just as readily as a severity-labeled
heading. Attribute-free inline presentation tags may be removed only from a closed tag-name set;
arbitrary custom element names remain source text so a finding cannot be hidden in a tag name. One
matching enclosing CommonMark fence, required marker/nonce framing, empty carryover
sections, and section ordering are also excluded from the signature comparison. Signature generation canonicalizes ASCII case-only
variants of the four allowed section headings, while the final outer validator still requires their
exact spelling. Any CommonMark list item outside the signed document is treated as ambiguous
substance and cannot be removed by repair unless its title exactly matches the closed benign
wrapper vocabulary or it contains only a complete fenced code block. Prefix matches with added
substance remain terminal, including indented and CommonMark lazy continuation lines. The same
guard covers an empty marker with an indented continuation. The one exception is a single `-` that
closes an already-open benign paragraph as a CommonMark Setext underline; a following complete
indented-code or HTML literal block is wrapper data, not list-item substance. `+`, `*`, and ordered
empty markers do not receive that exception. A standalone empty marker remains removable but never
opens a paragraph, so a later `-` cannot manufacture this Setext exception. After a blank line, a quoted item must
replay its quote containers; missing markers end the item instead of inheriting root indentation.
For these block decisions, only an empty line or ASCII spaces/tabs are CommonMark blank; Unicode
spaces and Python-only control whitespace remain paragraph content. A standalone empty item or
thematic break remains repairable. When more than one root fence contains an allowed section
heading, the signature parser selects only the unique fence containing this run's exact adjacent
marker/nonce pair. Multiple nonce-bound fences, or multiple unbound candidate fences, fail closed
instead of letting an earlier example shadow the actual review. A lone unbound fence remains
eligible for the one format-only repair. An enclosing fence may use
backticks or tildes with
any valid info string; its closing run must use the same character and be at least as long as the
opening run, and only ASCII spaces or tabs may follow a closing run. An incomplete fence, list
fence, or HTML block with an
explicit terminator fails closed on its first scan so maximum-size untrusted candidates remain
bounded. Empty optional carryover sections are equivalent to omission; an empty `New findings`
section remains terminal. Fences
inside a finding remain signed substance. A finding heading with an explicit bracketed or
colon-delimited `CRITICAL`, `HIGH`, `MEDIUM`, `LOW`, or `P0`–`P3` marker remains unsafe to drop.
The same applies when that exact marker is separated from its title by `-` or `–` with horizontal
space on both sides, or by `—` with or without surrounding horizontal space. This recognizer does not
classify hyphenated prose or ranges such as `Medium-term`, `P1-Review`, or `P1–P3` as severity
headings; the independent free-prose guard still rejects them unless the complete line is in the
closed benign vocabulary. Emphasis or code-span
delimiters may close immediately before any separator. An exact `P0`–`P3` marker followed by a
period and at least one horizontal space is also protected; this intentionally excludes word
severities followed by a period, `P4`/`P10`, decimals such as `P1.2`, and unspaced forms such as
`P1.Review`. Exact singular/plural defect labels (`Finding`, `Bug`, `Defect`, `Issue`,
`Vulnerability`, `Regression`, `Problem`, `Risk`, `Concern`, `Flaw`, or `Error`) are protected when
bracketed with optional emphasis/code decoration, used as a standalone heading, or followed by an
optional numeric identifier and one of the same colon/dash separators. Longer lookalikes such as
`Bugfix`, `Finding aid`, `Issues reviewed`,
`Risk assessment`, and `No findings` are not defect-label matches. They are removable only in an
exact allowlisted benign form such as the documented `...: Review complete` variants. Separator and field-colon boundaries
accept only the closed Unicode horizontal-space
set: ASCII space/tab, no-break and Ogham spaces, U+2000–U+200A spaces, narrow no-break space, medium
mathematical space, and ideographic space. An exact `Changed anchor` or `Current line` field before
the first section is also protected, including
optional H1–H6, emphasis, or code-span decoration and horizontal space before its colon. The field
may be bare, introduced by `-`, `+`, or `*`, or reached inside nested Markdown quote, ordered-list,
or task-list containers. Lookalikes such as `Changed anchors:`, `Current lines:`, and
`Unchanged anchor:`, and generic labels such as `Summary:`, `Medium-term:`, `P4:`, and `P10:` are
not reserved evidence fields, but arbitrary text using those labels is still rejected. Only their
exact closed benign forms are removable. Unlabeled ATX headings are not assumed to be harmless: an H1–H6 heading before
the first review section or after an enclosing fence makes repair terminal unless an H1–H3 title
exactly matches the closed generic wrapper vocabulary (`Review`, an optional automated/OpenCode and
code/PR/pull-request qualifier, an optional summary/overview/results/report/complete suffix, or the
standalone `Summary`/`Overview`). The same vocabulary may follow an exact `[Note]`, `[Info]`, or
`[Context]` tag; the documented plural evidence-field lookalikes are removable only in the exact
`...: Review complete` title form. Matching is case-insensitive and may carry whole-title
emphasis/code decoration or an ATX closing sequence, but prefix matches such as
`Review: Authentication bypass`, `Security review`, and `Summary of failures` remain protected.
H4–H6 headings always remain finding-like because they overlap the canonical finding-block syntax.
CommonMark ATX syntax itself requires ASCII space or tab after the opening hashes; the repair guard
also treats the closed Unicode horizontal-space set in that position as a heading-like adversarial
lookalike. Exact benign titles remain repairable under that safety superset, while an unknown title
cannot bypass signing with an Ogham, no-break, or other enumerated space. One-line and multiline
CommonMark Setext headings receive the same closed-vocabulary treatment, including up to three
spaces of indentation, source-ordered nesting of blockquotes and list items, lazy paragraph
continuation text inside those containers, and an explicitly contained underline. The underline
itself is never treated as a lazy continuation. A standalone thematic break, an outside-list
thematic break, an empty list item, an internally spaced underline, fenced or indented code, and a
CommonMark HTML block are not promoted to a Setext heading. A valid single-line link-reference
definition and its one-line destination or title continuation are likewise not Setext headings,
but remain ambiguous free prose outside the signed document and therefore make repair terminal.
Inline HTML, autolinks, invalid closing tags, and lowercase `<!...>` lookalikes remain paragraph
content and do not bypass either guard.
The same closed-vocabulary rule applies to a whole wrapper line enclosed by matching Markdown
emphasis, strong-emphasis, strikethrough, or code-span delimiters: `**Review complete**` remains
repairable, while an unlabeled title such as `**Authentication bypass**` is protected. This check
also follows nested blockquote and list containers, ignores trailing normalized whitespace, and applies
after an enclosing fence. Delimiter runs are paired with bounded linear scans rather than a
backtracking regular expression. Emphasis and strikethrough runs must have CommonMark-whitespace
flanking semantics (TAB/LF/FF/CR plus Unicode `Zs`) and an unescaped closer; code spans use their
matching backtick-run rule. Delimiter-only
CommonMark thematic breaks and whitespace-flanked delimiter lookalikes remain wrapper syntax. Raw
Markdown syntax and normalized syntax are evaluated independently and combined conservatively, so
an HTML entity, Unicode format control, or NFKC-compatible backslash cannot manufacture an escape or
whitespace edge that makes a source-level decorated title appear harmless after normalization.
Finding-like content or an allowed review section after an enclosing fence is likewise preserved
rather than treated as wrapper text. An
unsafe-to-sign or malformed repair is terminal; there is no third call and no candidate upload. The
model job reports `model_job_failed` for setup/infrastructure failure, `provider_failed` only when a
model process fails, and `candidate_contract_failed` when post-response preflight rejects the
candidate. The candidate is limited to 60,000 UTF-8 bytes and uploaded as a separate exact-name
artifact. This preflight never replaces or relaxes the clean canonicalizer's semantic, scope, and
provenance checks.

For modern releases, release verification also authenticates the exact Git blob of the complete
OpenCode auto-review workflow. Changes to installation, step topology, workflow/job environment,
shell selection, or review execution therefore require an explicit reviewed digest update. The only
legacy generic-runtime exceptions are the exact approved tag-and-peeled-commit identities.

A clean privileged job downloads that artifact by its exact server-issued ID and verifies the
reported and REST digest, repository/run identity, exact run-scoped name, one-file inventory,
regular-file/no-symlink type, 1..60,000-byte size, and strict UTF-8 decoding before parsing it.
It separately re-downloads the sealed handoff, checks out the sealed PR head, and uses
`/usr/bin/git` with a closed provider-free environment for changed-anchor validation. Before any
comment or Check mutation the shared canonicalizer first limits the fully rendered canonical body
to 64,000 UTF-8 bytes. The publisher then computes the complete canonical state and worst-case
fully wrapped body and requires at most 65,536 UTF-8 bytes. Either oversize failure therefore
performs no cleanup, comment creation/update/deletion, or Check creation, matching the repository's
GitHub comment-publication contract.

The canonicalizer treats every model-window marker-bearing new or changed comment as untrusted
cleanup material; none can supply the model result. It restores the newest previously attested
fallback, bounds cleanup to 20 hostile comments (with the historical single-candidate allowance),
and creates a canonical comment only from the validated candidate artifact. It then completes a
dedicated Check Run only after exact-byte refetch. The receipt binds repository,
workflow, PR, attempt head, the server-authored Actions workflow-run head, successful head,
run ID/attempt, comment ID, body/state digests,
the actual caller workflow path/event, and the referenced central workflow path/SHA. A later
collector also requires that bound run attempt and its reusable canonicalizer job to have
completed successfully; cancelled runs therefore leave no trusted state. Older attested
comments may be marker-free tombstoned, but the newest prior fallback is retained until a
future completed run can authenticate the successor.

Receipt discovery never starts from a comment-provided Check ID. Both prepare and live
canonicalization first query a bounded horizon of recent `pull_request` Actions run IDs and
server-authored heads without filtering on the latest attempt status. They retain only runs with
the exact central reusable-workflow path and SHA, then list the exact named Check Runs on each
selected head. A strict receipt/comment digest intersection identifies historical attempts, and
the exact attempt and its jobs must independently report a successful completed canonicalizer.
This preserves an older successful attempt while a rerun of the same run ID is in progress,
failed, or cancelled. Comment receipt IDs are used only for equality after server discovery.
An authentic receipt outside the horizon safely causes one full review; its next completed
canonicalizer receipt returns the state to the horizon. The API page is bounded at 100 recent PR
runs, central reusable-workflow identity is filtered before the newest 20 central runs are
selected, and each selected head/job query is single-page bounded. More than 40 strict matched
historical candidates fails closed before exact-attempt calls or comment repair; no candidate is
silently dropped before cleanup or publication. Live CAS applies this discovery to every strict
canonical record, including unchanged records absent from the prepared evidence snapshot.
Completed exact-attempt evidence is cached across repeated CAS checks; queued or in-progress
evidence is never cached. Once a strict server Check/comment intersection yields unresolved
exact-attempt evidence, that bounded `(run_id, run_attempt)` identity remains fail-closed for the
whole canonicalization transaction. A later run/Check discovery omission, exact-attempt 404,
API uncertainty, or another non-completed response cannot clear it or permit a write. Only a
rediscovered exact completed attempt plus bounded jobs evidence resolves it: matching successful
canonicalizer provenance is authenticated, while completed non-success or a valid jobs response
without the successful canonicalizer resolves the receipt as untrusted.

A partial GitHub rerun may reuse the immutable handoff produced by an earlier attempt of the
same run. The handoff's producer attempt is retained as `prepared_run_attempt`; the clean job
accepts it only when it is no newer than the current attempt and the current server run still
matches the sealed workflow head, caller path/event, and central reusable workflow identity.
State, generation ordering, the completed canonicalizer job, and the receipt's `run_attempt`
always bind the current rerun attempt.

Physical cleanup is also bounded: the exact single nonce candidate has one reserved cleanup
slot, while at most 20 other untrusted marker-bearing comments are tombstoned per run; multiple
exact-nonce lookalikes share that same 20-comment ceiling. Overflow
remains unattested and therefore ignored by collectors; later runs can drain it without allowing
comment volume to drive unbounded privileged writes.

Checks are a canonicalizer-only receipt under this workflow's fixed least-privilege caller
ceiling, not a universal signature against an unrelated workflow independently granted
`checks: write`. This OpenCode path targets GitHub-hosted Actions: the pinned artifact v4
actions are not GHES-compatible, and the pinned upstream OpenCode command is itself tied to
github.com.

## Review invocation budget and handoff

Claude, Gemini, and OpenCode each own one schema-1 budget ledger comment. Its
reviewer-specific marker must begin at byte zero and must be followed immediately by
one compact `<!-- automation-budget-state:{...} -->` JSON marker:

```text
<!-- automation:review-invocation-budget:claude:v1 -->
<!-- automation:review-invocation-budget:gemini:v1 -->
<!-- automation:review-invocation-budget:opencode:v1 -->
```

Only a comment authored by exactly `github-actions[bot]` is eligible, and exactly one
comment may match a reviewer marker. Invalid, duplicate, or provenance-unverifiable
state fails closed. The effective-diff identity is the SHA-256 of the immutable
`review-full.diff`, including when a provider consumes a delta. A normal invocation by
the same reviewer with the same head SHA or full-diff hash is an absolute zero-call
gate. Only the explicit force-review contract below may consume the one override round
to review that input again.

A round is one distinct effective diff claimed by one reviewer. Each reviewer has two
automatic rounds. One `review-budget-override` label timeline-event ID can authorize
one additional round for that PR/reviewer and is consumed exactly once. Estimated
input is `ceil(sum(input_file_bytes) / 4) + 20_000`, capped at 200,000 tokens per
round, 400,000 across automatic rounds, and 600,000 only after the override. Every
round has a 600-second provider wall-time cap. Call units and caps are one Claude
action session, three Gemini `generate_content` requests across primary, retries, and
configured same-reviewer fallback, and two OpenCode `opencode run` sessions including
format repair.

Claude and Gemini baseline callers expose a `workflow_dispatch` input pair:
`pr_number` (required) and `force_review` (boolean, default `false`). A force claim is accepted only
when the run event is `workflow_dispatch` and the PR timeline contains an unconsumed
`review-budget-override` label event created by an actor with write, maintain, or admin permission.
It prepares the current full PR diff, binds publication and budget state to the fetched current
head plus the exact run ID/attempt, consumes the override immediately, and is terminal for that
reviewer budget. A label alone never bypasses the normal same-head zero-call gate.

`/jhw:ship` or an operator uses the stable sequence below, substituting the repository, PR, and
caller filename. The Gemini caller uses `gemini-auto-review.yml` with the same inputs.

```bash
gh pr edit 26 --repo OWNER/REPO --add-label review-budget-override
gh workflow run claude-code-review.yml --repo OWNER/REPO \
  -f pr_number=26 -f force_review=true
```

The caller job is skipped when `force_review` is omitted or false. A missing, unauthorized,
already-consumed, or otherwise unavailable override returns `round_budget_exhausted` and performs
no model call. A non-dispatch force request or a run/PR/head/ref mismatch returns `state_invalid`;
diff preparation and provider/canonicalization failures retain their existing fail-closed outcomes.
Neither a failed force run nor budget exhaustion is merge approval, and orchestration must require
`review_execution=performed` on a current-head successful state before treating the force request
as satisfied.

Claim decisions are applied in this order: invalid state/provenance/head/diff;
authenticated unchanged reuse; normal duplicate head; normal duplicate effective diff; per-round
input exhaustion; force authorization or automatic-round exhaustion and eligible override consumption;
aggregate usage exhaustion; then `claimed` with `allow-invocation=true`. The claim is
persisted before provider execution. Cancelled, timed-out, provider-failed,
quality-filtered, and unfinalized claims remain consumed. Immediately before ledger
mutation, the action refetches both PR head and prior comment; any mismatch returns
`state_invalid` with `compare_and_swap_failed`, performs no mutation, and permits no
provider call.

Finalization runs after canonical publication under `always() && !cancelled()` and
records the actual model route, effort, call unit/count, elapsed seconds, outcome,
stop reason, and at most eight active `RVW-<12hex>` IDs. Claude records its
configured/default model with `final-review/default`; Gemini records every attempted
primary/fallback model and its configured thinking level; OpenCode records
`zai-coding-plan/glm-4.7` with `final-review/default`. Provider failure never falls
back to a different reviewer.

Every claim/refusal and finalization writes a deterministic checkpoint. Artifact names
are `claude-review-budget-{claim|final}-${run_id}-${run_attempt}`,
`gemini-review-budget-{claim|final}-${run_id}-${run_attempt}`, and
`opencode-review-budget-{claim|final}-${run_id}-${run_attempt}`, each containing the
corresponding `*-review-budget-{claim|final}.json`. The handoff records repository,
PR, reviewer, head and full-diff hash, run ID/attempt, automatic and override rounds,
per-round calls/input/wall usage, current decision/outcome/stop reason, the last
authenticated successful review head/hash, and remaining authenticated finding IDs.
OpenCode additionally seals the claim checkpoint hash and decision into its prepared
handoff and validates them before model execution and canonical finalization.

Budget exhaustion is not approval: the ledger cannot publish findings, report CLEAN,
or authorize merge. `/jhw:ship` polls existing review comments and CI signals
deterministically and does not parse this budget ledger as review evidence.

### Dismissing a finding

From `v1.63` a collaborator can retire a false-positive finding without spending a round and
without a model retraction. The trust path is the one the override label already uses: the
budget action reads the PR timeline, uses the fixed grammar below only to decide whose
repository permission to fetch through the collaborators API, and lets a comment count only
after that permission has been verified. At most sixteen distinct label or comment actors are
looked up per timeline; more fails closed with `permission_actors_exceeded`, because anyone who
can comment can add an actor. GitHub serializes a `commented` timeline event with both `actor`
and `user` naming the comment author; the action reads `actor`, the same field the label path
already trusts.

A dismissal is one issue comment on the pull request whose whole body is exactly

```text
dismiss RVW-<12 lowercase hex> <reason>
```

— the word `dismiss`, one space, the finding ID as printed in the review comment, one space, and
a non-empty single-line reason. Trailing whitespace is ignored; any other leading text, casing,
spacing, or a second line makes the comment inert. The reason is for human readers and is never
copied into the ledger or the bot comment: the ledger records only the finding ID and the comment
ID, and the summary links to the comment, so the audit trail lives in the collaborator's own
words on the pull request.

Only comments whose author holds `write`, `maintain`, or `admin` permission count. Each `claim`
and `finalize` replaces the ledger's dismissal snapshot with the current timeline, binding every
dismissed ID to its earliest authorizing comment, sorted by ID and bounded at sixteen entries.
More than sixteen distinct dismissed IDs fails the whole round closed with
`dismissed_findings_invalid` and records no snapshot at all, so every dismissal on that pull
request stops applying until enough comments are deleted; the bound is twice the eight-ID
remaining-finding cap and is not expected to be reached in normal use. Deleting a comment
revokes its dismissal on the next run; editing it re-targets it. A refused claim — including
`round_budget_exhausted` after every round is spent — still records the snapshot and rewrites the
ledger comment, so a dismissal takes effect without a new model round. The OpenCode ledger
ignores dismissal comments entirely and keeps the pre-`v1.63` summary text, because that
reviewer assigns no `RVW-` IDs for a dismissal to name.

The ledger key `dismissed_findings` is written only when at least one dismissal exists; a ledger
without one keeps its exact pre-`v1.63` bytes, so every open pull request stays valid across the
upgrade. A dismissed ID is never listed in the handoff's `remaining_finding_ids`, and a round
finalized after the dismissal records its remaining IDs without it; a ledger whose handoff
violates this fails closed with `handoff_mismatch`. Rounds finalized before the dismissal keep
their historical lists. The action exposes the snapshot as the comma-separated `dismissed-finding-ids` output, which the
Claude and Gemini workflows hand to the shared canonicalizer so the next round cannot carry the
finding over or re-emit it (see [Candidate and carryover grammar](#candidate-and-carryover-grammar)).
The bot comment lists each dismissal as `- Dismissed findings:` with a link to the comment and
documents the grammar in its closing guidance.

A dismissal is not approval either: it removes one finding from the active set and nothing else.
The known limit is that finding IDs derive from the anchor line and title, so a reworded or
re-anchored repeat of the same claim receives a new ID and must be dismissed again.

## Adoption and recovery

Use `scripts/rollout_workflow_fleet.py` to plan and open managed PRs; do not copy the
baseline into existing repositories manually. Operators and repository owners then use
normal CI, review, and GitHub merge controls. After merge, verify default-branch content
with `scripts/audit_workflow_fleet.py`. See
[`docs/workflow-fleet-rollout.md`](../workflow-fleet-rollout.md) for exact commands,
canaries, bootstrap, and recovery.
