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
workflow's declared repository write operations.

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
not supported.

## Triggers, inputs, and permissions

Each catalog entry owns the exact trigger shape, caller job name, typed `with` inputs,
permissions, and secret names. Required callers must exist in every configured repository.
An optional caller exists only when selected by that repository's declarative profile;
unexpected optional callers are removed by the managed PR.

OpenCode callers accept only same-repository pull request content and fail closed for
fork/external heads. Their jobs grant exactly `contents: read`, `pull-requests: write`, and
`issues: write`, force the job-scoped GitHub token, and do not grant OIDC. Central
workflows own the pinned OpenCode CLI archive and action versions; consumers do not add an
installer.

Claude and Gemini callers retain only the catalogued permissions. Repository-specific
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

## Canonical automated-review state (v2)

Claude, Gemini, and OpenCode publish review state in a workflow-generated v2 envelope.
Only a bot comment whose first three lines are the reviewer's exact header, its exact v2
marker, and one exact `<!-- automation-state:{...} -->` line is a state candidate. A marker
quoted later in prose, a different reviewer or PR, malformed JSON, or an invalid field is not
state. The highest lexicographic `(run_id, run_attempt)` candidate wins; comment ordering and
timestamps do not. `run_id` and `run_attempt` are positive safe integers, so a manual rerun
of the same run is newer when its attempt is larger.

The envelope fields are `schema` (always `2`), `reviewer`, `pr`, `run_id`, `run_attempt`,
`attempt_head`, `successful_head`, `attempt_status`, `diff_mode`, and
`full_diff_sha256`. `attempt_head` is the head this attempt prepared. The successful pair is
atomic in meaning: `successful_head` is a 40-hex reviewed head only when
`full_diff_sha256` is its 64-hex full-input hash; otherwise both are `null`. The status is
`success` or `failure`, and `diff_mode` is `full`, `delta`, `unchanged`, or `unavailable`
(Slice 1 only advances a successful checkpoint from covered `full` or `delta` input).
A `success` state requires the non-null successful pair and
`successful_head == attempt_head`; a `failure` may carry either a null pair or a valid retained
pair from an earlier success.

The v2 contract requires the visible `- Run:` line to be the exact URL-only value
`${{ github.server_url }}/${{ github.repository }}/actions/runs/<state.run_id>`; malformed,
foreign-repository, or mismatched-run URLs are not state. Free-form review prose is untrusted
presentation/comparison data only: reserved header, marker, state, and status lines are
sanitized from model output and are constructed by the workflow, never treated as model
authority.

Legacy comments may be reused only as an exact legacy display target. Their marker, body, and
`Reviewed` text never supply input state. The first v2 run performs a full review and
establishes canonical v2 state; historical unstructured OpenCode comments are ignored.

A successful checkpoint requires a prepared covered input, a successful model step, non-empty
sanitized prose, and a valid current write gate. A failure preserves a prior successful body
and successful pair only when both remain valid, records the failed `attempt_head`, and shows
`Status: stale` plus `Last attempt: failure`; without prior success it is `Status: failure`
with no `Reviewed` checkpoint. A stale run, missing/invalid input, empty sanitized output, or
invalid prior state cannot advance coverage (invalid prior state falls back to a full review).

Immediately before comment mutation, each reviewer refetches the PR head and requires it to
equal `attempt_head`; it also refuses to write unless stored `(run_id, run_attempt)` is
strictly older. Per-reviewer/per-PR concurrency with cancellation reduces overlap. OpenCode
adds fresh generation and head checks before repair, before comment creation, and immediately
before its receipt becomes successful.

OpenCode uses three jobs. A read-only prepare job captures prior comments and provenance,
prepares the diff, and uploads one immutable handoff. The handoff is selected by server-issued
artifact ID, with the upload's raw SHA-256 output, the REST `sha256:` digest, repository/run
identity, an exact conditional file inventory, and per-file hashes all checked. The model job
has no Actions, Checks, OIDC, or contents-write permission. The pinned CLI is expected to emit
one legacy raw comment containing the sealed per-attempt candidate nonce exactly once; every
marker-bearing model-window mutation remains untrusted and subject to quarantine. A clean
privileged job re-downloads the exact artifact ID, validates it, checks out the sealed PR head, and uses
`/usr/bin/git` with a closed provider-free environment for changed-anchor validation.

The canonicalizer treats every model-window marker-bearing new or changed comment as
untrusted. It restores the newest previously attested fallback, quarantines forgeries, admits
exactly one new-ID nonce-bound raw candidate, and creates a new canonical comment. It then
completes a dedicated Check Run only after exact-byte refetch. The receipt binds repository,
workflow, PR, attempt head, successful head, run ID/attempt, comment ID, body/state digests,
the actual caller workflow path/event, and the referenced central workflow path/SHA. A later
collector also requires that bound run attempt and its reusable canonicalizer job to have
completed successfully; cancelled runs therefore leave no trusted state. Older attested
comments may be marker-free tombstoned, but the newest prior fallback is retained until a
future completed run can authenticate the successor.

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
