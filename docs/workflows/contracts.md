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

## Adoption and recovery

Use `scripts/rollout_workflow_fleet.py` to plan and open managed PRs; do not copy the
baseline into existing repositories manually. Operators and repository owners then use
normal CI, review, and GitHub merge controls. After merge, verify default-branch content
with `scripts/audit_workflow_fleet.py`. See
[`docs/workflow-fleet-rollout.md`](../workflow-fleet-rollout.md) for exact commands,
canaries, bootstrap, and recovery.
