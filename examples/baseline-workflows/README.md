# Baseline Consumer Workflows

This folder contains a minimal, copy-paste-friendly set of workflow wrappers for repositories
that want to consume shared reusable workflows from `jhw7500/automation`.

Goal: keep consumer repos thin (event triggers + `uses:`) and keep logic centralized in
`jhw7500/automation`.

## How to use

1) Copy the `.github/` directory from this folder into your target repository.

2) Update the pinned version in each workflow file:

- Search for `jhw7500/automation/.github/workflows/...@v1.21` and bump to the desired tag.
- Or update `automation_ref` in `.github/workflow-config.yml` and use `bump-automation-ref.yml`
  to auto-create a PR that rewrites all refs.

3) Configure `review.auto` in `.github/workflow-config.yml`:

- `review.auto: false` (recommended default) disables automatic PR reviews.
- Manual commands remain available.

## Required secrets / variables

See `docs/workflows/contracts.md` for the full list.

Quick minimum:

- Secret: `GEMINI_API_KEY`
- Variable: `GEMINI_MODEL`

If you enable Claude workflows:

- Secret: `CLAUDE_CODE_OAUTH_TOKEN`

If you enable opencode (z.ai GLM-4.7):

- Secret: `ZHIPU_API_KEY`
