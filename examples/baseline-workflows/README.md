# Canonical Common AI Caller Baseline

`examples/baseline-workflows/.github/` is the sole canonical consumer tree used by the
fleet renderer. Existing repositories should not copy these files manually; use the
reviewable plan/publish workflow described in
[`docs/workflow-fleet-rollout.md`](../../docs/workflow-fleet-rollout.md).

## Catalog ownership

The authoritative `scripts/workflow-catalog.json` describes **14 managed caller
workflows** (ten required and four optional), their triggers, inputs, permissions, central
targets, and exact secret mappings. `scripts/workflow-config.json` selects optional callers
and the Gemini repository-write mode for each of the 19 repositories. The baseline README
is explanatory and is not another policy source.

The canonical tree also contains `.github/workflow-config.yml`. Its
`__AUTOMATION_REF__` and `__AUTOMATION_COMMIT__` placeholders are renderer inputs, not
values to commit to a consumer. Rendered reusable calls use the verified 40-character
commit for the selected immutable tag.

## Authentication contracts

Credentials are mapped only by the same name:

```yaml
CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
ZHIPU_API_KEY: ${{ secrets.ZHIPU_API_KEY }}
```

Gemini callers always receive `GEMINI_API_KEY` and use one explicit repository-write
profile:

```yaml
# GitHub App profile
repo_write_auth: github_app
app_id: ${{ vars.APP_ID }}
APP_PRIVATE_KEY: ${{ secrets.APP_PRIVATE_KEY }}

# Built-in GitHub token profile
repo_write_auth: github_token
```

The built-in token profile omits both App fields. Bulk secret forwarding and ambient
provider authentication are not supported. OpenCode callers use `ZHIPU_API_KEY`, run only
against same-repository pull request heads, and retain their no-OIDC permission boundary.

## Consumer configuration

The bootstrap config disables every common caller. For an existing repository, the
renderer preserves comments, formatting, and all config keys except the release identity:

```yaml
automation_ref: v1.40.1
automation_commit: 0123456789abcdef0123456789abcdef01234567
```

Repository-specific build, verification, packaging, deployment, and release workflows
are not part of this baseline and must not be moved here.

## Validation and adoption

Before a managed branch is pushed, the renderer verifies catalog membership, YAML,
reusable caller contracts, permission and authentication boundaries, provider-environment
isolation, project-owned workflow preservation, and actionlint. Each repository receives
an independent PR. Review, CI, merge, and revert remain ordinary GitHub operations; a
read-only content audit confirms adoption after merge.
