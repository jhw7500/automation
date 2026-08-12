# Shared Workflow Consumer Contract

This document defines the consumer-facing contract for repositories that use the reusable
workflows in `jhw7500/automation`.

The intent:

- Consumer repos keep only thin workflow wrappers (event triggers + `uses:`).
- All non-trivial logic lives in `jhw7500/automation`.

## Baseline copy

Start by copying the baseline wrappers:

- `examples/baseline-workflows/.github/`

Copy that folder into your consumer repo's `.github/`.

## Repo config file: `.github/workflow-config.yml`

Workflows read `.github/workflow-config.yml` for repo-level behavior.

### `review.auto`

```yaml
review:
  auto: false
```

Semantics:

- `review.auto: true`: enable automatic PR reviews (e.g. on PR opened/synchronize).
- `review.auto: false`: disable automatic PR reviews.
- Manual triggers must continue to work regardless of `review.auto`.
  - Example manual trigger: comment `@gemini-cli /review ...`.

> **Precedence:** the auto-review workflows first read the per-workflow key
> `workflows.<name>.auto` (e.g. `workflows.gemini-auto-review.auto`,
> `workflows.claude-code-review.auto`), then fall back to `review.auto`, then to
> `true` if both are unset. A repo that pins the per-workflow keys must change
> *those* to disable auto review — a global `review.auto: false` is silently
> ignored when a per-workflow `auto` is present.

## Secrets (consumer repository)

Required secrets depend on which workflows you enable.

- `GEMINI_API_KEY`
  - Required for Gemini workflows (review/triage/invoke/dispatch).
- `CLAUDE_CODE_OAUTH_TOKEN`
  - Required for Claude workflows.
- `ZHIPU_API_KEY`
  - Required for OpenCode workflows.

### OpenCode PR boundary

OpenCode automatic review and the manual `/oc` command run only for pull requests whose
head branch belongs to the same repository. Fork/external PRs fail closed and produce a
skipped workflow summary. This restriction lets private repositories retain the read-only
checkout credential required by OpenCode's internal branch fetch without exposing it while
processing external contributor content.

Both OpenCode workflows force the job-scoped `github.token`; `id-token: write` is forbidden,
so the action cannot exchange OIDC for an App token outside the declared job permissions.

## Variables (consumer repository)

These are configured as GitHub Actions Variables.

### Gemini runtime

- `GEMINI_CLI_VERSION`
  - Example: `preview`
- `GEMINI_MODEL`
  - Recommended default: `gemini-3-flash-preview`
- `GEMINI_FALLBACK_MODEL`
  - Recommended default: `gemini-3-flash-preview`
- `GEMINI_DEBUG`
  - Set to `true` to enable verbose logging.
- `UPLOAD_ARTIFACTS`
  - Set to `true` to upload run artifacts (logs/reports) for debugging.
  - Recommended default: `false`.

### Gemini guardrails

- `GEMINI_MAX_READ_BYTES`
  - Hard cap for file reads through the safe wrapper layer.
  - Default: `200000` (bytes) if unset.

- `GEMINI_SPARSE_CHECKOUT`
- `GEMINI_SPARSE_CHECKOUT_PATTERNS`

Sparse checkout is optional. If you enable it, you must provide patterns.

Example:

```text
GEMINI_SPARSE_CHECKOUT=true
GEMINI_SPARSE_CHECKOUT_PATTERNS=\
.github/\
src/\
scripts/\
README.md
```

Notes:

- Keep patterns tight to reduce checkout size and reduce the chance of context bloat.
- Do not include large generated artifacts or release outputs.

## Wrapper workflow expectations

Consumer repos should:

- Pin reusable workflow versions (e.g. `@v1.15`) in wrapper `uses:` lines.
- Keep wrappers portable (no `main`/`master` assumptions).
- Avoid storing documentation under `.github/workflows/` in consumer repos.
  - Put docs in `docs/` or keep them in `jhw7500/automation`.
