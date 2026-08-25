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
Actions run provenance is fetched separately with the job-scoped built-in token and
`actions: read`; the App installation does not need an added Actions permission.

### Built-in token mode

```yaml
with:
  repo_write_auth: github_token
secrets:
  GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
```

This mode passes neither `app_id` nor `APP_PRIVATE_KEY`; the reusable workflow uses the
exact built-in `${{ github.token }}` path within its declared permissions. Ambient
authentication, OIDC model authentication, and alternate Gemini provider variables are
not supported. In this mode the publisher login is exactly `github-actions[bot]`.

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
Manual mention/comment and manual-dispatch behavior remains available when the applicable
caller is enabled. The disabled bootstrap template uses `workflows.<name>.enabled: false`
for every common caller; enabling any of them is a later repository-owned PR.

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

It exposes exactly six scalar outputs: `document-valid`, `accepted-count`, `filtered-count`,
`normalized-count`, `filtered-max-severity`, and `failure-reason`. The first is `true` or `false`;
the three counts are non-negative integers; `filtered-max-severity` is `none`, `MEDIUM`, `HIGH`, or
`CRITICAL`; and `failure-reason` is empty on a document-valid result or one fixed hard reason.

The action captures validated PR base/head metadata, prepares `review-full.diff`, optionally
prepares `review-delta.diff`, and writes `review-scope.json`. Its composite outputs are
`diff-ready`, `diff-mode`, `head-sha`, `full-diff-sha256`, and
`unchanged-since-previous`. The hash is SHA-256 over the exact `review-full.diff` bytes. A ready
manifest has schema `1`, repository, PR number, merge-base SHA, head SHA, and file records with
`status`, `filename`, and optional `previous_filename`.

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
| `full` | `diff-ready=true`; the unrestricted local `merge-base..captured-head` full diff and local manifest exist; delta is absent. This covers a first review, an unusable/non-ancestor previous SHA, an empty delta whose full hash changed, or an incremental preparation/argv failure. | Claude and Gemini read the full diff. OpenCode always reads the sealed full diff. A model result can advance only after the ordinary output, head, and generation gates. |
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
line integers, and evidence-like noncanonical field labels fail closed. Markdown link/image,
HTML tag/comment, and HTML entity wrappers are normalized only for reserved-label detection.
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

The document boundary is closed: non-blank prose before the first allowed section, after the last
section, or in place of a declared carryover section is `ambiguous_document`. A no-finding section
accepts only the closed workflow-owned no-findings form; `None` cannot terminate parsing and hide a
later provider error or caveat. Unknown bullets inside a finding are not silently discarded. They
remain candidate prose and participate in proof-deficit checks, so wording such as “cannot verify”
cannot evade filtering merely by using an unrecognized field label.

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

### Hard checkpoints and soft finding filters

The canonicalizer fails the whole document for exactly these hard reasons: `candidate_missing`,
`invalid_utf8`, `candidate_oversize`, `ambiguous_document`, `scope_invalid`, and
`canonicalizer_error`. A hard failure sets `document-valid=false`, makes the provider attempt a
failed checkpoint, publishes no candidate prose, and cannot advance the successful head or hash.
After an attempted Claude or Gemini canonicalization that does not produce
`document-valid=true`, the workflow uploads only the bounded schema-1
`<reviewer>-review-result.json` as a uniquely named, non-overwriting diagnostic artifact for one
day. The untrusted raw candidate is never uploaded: it may contain provider-echoed secrets and a
workspace path could otherwise expose a checkout-seeded symlink target. A missing result is
ignored. The diagnostic is not authority: neither the upsert program nor later review state or
carryover reads it.

Once the document boundary and trusted scope are valid, a bad individual block does not discard
valid siblings. It is filtered or normalized with exactly one of `invalid_anchor`,
`invalid_trigger_evidence`, `invalid_severity`, `invalid_impact_class`,
`missing_material_impact`, `unsupported_performance_basis`, `non_actionable_category`,
`unknown_prior_id`, `duplicate_prior_binding`, or `missing_fix_anchor`. Rejected prose is absent
from canonical Markdown, result JSON, workflow state, and the PR comment; only bounded counts,
claimed maximum severity, and fixed reason codes are observable. `accepted-count` counts accepted
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
is not state. The author login must also equal the token's exact publisher login. From the newest
20 syntactically valid records, the workflow queries the claimed Actions run attempt and retains
only a completed success/failure whose repository, `pull_request` event, PR number, attempt head,
run ID/attempt, and referenced central reusable-workflow path plus immutable SHA all agree. The
highest authenticated `(run_id, run_attempt)` then wins; a foreign bot or a forged large run ID is
ignored rather than poisoning carryover or the stale-generation guard. Comment ordering and
timestamps do not decide state. Both values are positive safe integers, so a manual rerun of one
authenticated run is newer when its attempt is larger.

Every envelope has the common fields `schema`, `reviewer`, `pr`, `run_id`, `run_attempt`,
`attempt_head`, `successful_head`, `attempt_status`, `diff_mode`, and `full_diff_sha256`.
`attempt_head` is the head this attempt prepared. The successful pair is atomic:
`successful_head` is a 40-hex reviewed head only when `full_diff_sha256` is its 64-hex full-input
hash; otherwise both are `null`. Status is `success` or `failure`; mode is `full`, `delta`,
`unchanged`, or `unavailable`, and unavailable is never successful. Success requires a non-null
pair with `successful_head == attempt_head`; failure may carry either a null pair or a valid pair
retained from an earlier success.

Claude/Gemini schema 3 adds exactly `quality_schema`, `accepted_count`, `filtered_count`,
`normalized_count`, and `filtered_max_severity`. `quality_schema` is always `1`. On full or delta
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
review round indefinitely: the current SDK request timeout is 420,000 ms, the review subprocess
watchdog is 450 seconds (plus a 15-second hard-kill grace), and the job timeout is 10 minutes. This
reserves 135 seconds of the job budget outside the watchdog window for setup overhead, cleanup, and
sticky publication. The watchdog measures elapsed time and normalizes a hard-kill status (`137`) to
the timeout status only after the configured process deadline has elapsed; an earlier signal exit
remains a generic provider failure. An SDK timeout or subprocess deadline records
`provider_timeout`; the non-cancelled upsert path publishes that reason as a failed/stale attempt
without advancing `Reviewed`. The job timeout is the last-resort ceiling and remains below the
12-minute `/jhw:ship` review-round deadline.

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
comment or Check mutation it computes the complete canonical state and worst-case fully wrapped
body, then requires at most 65,536 UTF-8 bytes. Oversize failure therefore performs no cleanup,
comment creation/update/deletion, or Check creation, matching the repository's GitHub
comment-publication contract.

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

## Adoption and recovery

Use `scripts/rollout_workflow_fleet.py` to plan and open managed PRs; do not copy the
baseline into existing repositories manually. Operators and repository owners then use
normal CI, review, and GitHub merge controls. After merge, verify default-branch content
with `scripts/audit_workflow_fleet.py`. See
[`docs/workflow-fleet-rollout.md`](../workflow-fleet-rollout.md) for exact commands,
canaries, bootstrap, and recovery.
