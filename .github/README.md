# GitHub Actions Configuration

This directory contains GitHub Actions workflows for automated code review and quality assurance using Claude Code, Gemini, and OpenCode.

## Workflows

### Claude Code Workflows

#### 1. `claude.yml` - Interactive Claude Code Assistant
Triggers when `@claude` is mentioned in:
- Issue comments
- Pull request review comments
- Issue descriptions
- Pull request reviews

**Required Secret:**
- `CLAUDE_CODE_OAUTH_TOKEN` - OAuth token from https://claude.com/code/oauth

**Usage:**
```
@claude Please review this code
@claude Explain the changes in this PR
@claude Fix the bug described in this issue
```

#### 2. `claude-code-review.yml` - Automatic PR Review
Automatically reviews pull requests when opened or updated.

**Required Secret:**
- `CLAUDE_CODE_OAUTH_TOKEN`

**Features:**
- Code quality analysis
- Bug detection
- Performance considerations
- Security concerns
- Test coverage assessment

### Gemini Workflows

#### 1. `gemini-auto-review.yml` - Automatic PR Review ⭐ NEW
**Automatically reviews pull requests when opened or updated** (similar to Claude Code Review).

**Required Secret:**
- `GEMINI_API_KEY` - API key from Google AI Studio

**Features:**
- Overall code assessment
- Critical issue detection (security, bugs, breaking changes)
- Code quality suggestions
- Performance considerations
- Uses the Gemini model set by the `GEMINI_MODEL` repo/org variable (default: `gemini-3-flash-preview`)
- Bounds provider waits with a 7-minute SDK timeout, 7.5-minute process watchdog, and 10-minute job ceiling

**Triggers:** Automatically on PR opened or synchronized (new commits pushed)

#### 2. `gemini-dispatch.yml` - Gemini Request Dispatcher
Central dispatcher for routing Gemini-related requests (requires `@gemini-cli` mention).

#### 3. `gemini-invoke.yml` - Direct Gemini Invocation
Handles direct Gemini CLI invocations for code analysis.

#### 4. `gemini-review.yml` - Gemini PR Review
Provides Gemini-powered code reviews on pull requests (called by dispatch).

#### 5. `gemini-triage.yml` - Issue Triage
Automatically triages and categorizes issues using Gemini.

#### 6. `gemini-scheduled-triage.yml` - Scheduled Issue Analysis
Runs periodic analysis of repository issues.

**Required Secrets:**
- `GEMINI_API_KEY` - API key from Google AI Studio
- `GCP_PROJECT_ID` - Google Cloud Project ID (if using Vertex AI)
- `GCP_LOCATION` - GCP region (e.g., `us-central1`)

**Required Variables:**
- Additional configuration may be required in workflow files

### Automatic Review Input

Claude, Gemini, and OpenCode automatic reviews use the shared
`$/.github/actions/prepare-review-diff` action. It prepares a PR-scoped full diff, a safe
incremental diff when available, and a scope manifest from the same immutable automation commit
as the reusable workflow. The authoritative full diff and manifest come only from the exact local
`merge-base..captured-head` object graph—never Pulls Files or a numbered server diff—so ABA views
and the 3,000-file API ceiling cannot narrow review scope. Missing authoritative input skips the
model and cannot advance the review checkpoint. Claude and Gemini consume the selected
full/incremental artifact. OpenCode consumes the sealed full diff through a tokenless generic run,
returns an exact-ID/digest untrusted artifact to the clean canonicalizer, and machine-validates
canonical one-line JSON changed anchors plus authenticated carryover identity.

See [`docs/workflows/contracts.md`](../docs/workflows/contracts.md#deterministic-automated-review-input)
for exact modes, state transitions, unusual-path handling, and fail-closed behavior.

## Setup Instructions

### 1. Claude Code Setup

1. Go to https://claude.com/code/oauth
2. Generate an OAuth token for your repository
3. Add the token as a repository secret:
   - Go to repository **Settings** → **Secrets and variables** → **Actions**
   - Click **New repository secret**
   - Name: `CLAUDE_CODE_OAUTH_TOKEN`
   - Value: (paste your token)

### 2. Gemini Auto Review Setup (Recommended)

For automatic PR reviews with Gemini (similar to Claude):

1. Get a Gemini API key:
   - Visit https://aistudio.google.com/app/apikey
   - Create a new API key or use existing one

2. Add the API key as a repository secret:
   - Go to repository **Settings** → **Secrets and variables** → **Actions**
   - Click **New repository secret**
   - Name: `GEMINI_API_KEY`
   - Value: (paste your API key)

3. That's it! The `gemini-auto-review.yml` workflow will now automatically review PRs.

### 3. Advanced Gemini Setup (Optional)

For advanced Gemini features (dispatch, triage, scheduled reviews):

1. Get a Gemini API key (same as above)

2. (Optional) Set up Google Cloud Project for Vertex AI:
   - Create a GCP project at https://console.cloud.google.com
   - Enable Vertex AI API
   - Note your project ID and preferred region

3. Add additional secrets to your repository:
   - `GEMINI_API_KEY` - Your Gemini API key (required)
   - `GCP_PROJECT_ID` - (if using Vertex AI)
   - `GCP_LOCATION` - (if using Vertex AI, e.g., `us-central1`)

### 4. Verify Setup

After adding secrets, create a test PR or mention `@claude` in an issue to verify the workflows are working.

**Note:** Both Claude and Gemini auto-review workflows will run automatically on new PRs once their respective secrets are configured.

## Workflow Permissions

Permissions are catalogued per caller; there is no universal permission set. The automatic
review caller ceilings are:

- Claude: `contents: read`, `pull-requests: write`, `issues: read`, `id-token: write`.
- Gemini: `contents: read`, `pull-requests: write`, `issues: write`.
- OpenCode: `actions: read`, `checks: write`, `contents: read`, `pull-requests: write`,
  `issues: write`; no OIDC. Its reusable workflow narrows permissions by job so only the clean
  canonicalizer can write comments or the durable Check receipt; the model job has empty
  permissions, no checkout, and no GitHub token.

Interactive and triage workflows have their own catalogued permissions. See
[`docs/workflows/contracts.md`](../docs/workflows/contracts.md#triggers-inputs-and-permissions)
for the authoritative caller contract.

## Disabling Workflows

To disable a workflow:
1. Go to **Actions** tab in your repository
2. Select the workflow you want to disable
3. Click the **⋯** menu
4. Select **Disable workflow**

Or delete/rename the workflow file in `.github/workflows/`.

## Customization

### Adjusting Claude Prompts

Edit the `prompt` parameter in `claude-code-review.yml`:

```yaml
prompt: |
  Please review this pull request focusing on:
  - Your custom criteria here
  - Additional focus areas
```

### Changing Claude Model

The Claude model used by `claude-code-review` is read from the consumer
repository's `.github/workflow-config.yml`. No workflow file edit is required.

Two scopes are supported (per-workflow wins over shared):

```yaml
# Shared default for all Claude workflows
claude:
  model: "claude-sonnet-4-6"

# Or per-workflow override
workflows:
  claude-code-review:
    model: "claude-opus-4-7"
```

Supported model IDs:

- `claude-opus-4-7` — Opus 4.7 (deepest reasoning, highest cost)
- `claude-sonnet-4-6` — Sonnet 4.6 (balanced)
- `claude-haiku-4-5-20251001` — Haiku 4.5 (fastest, cheapest)

If both keys are empty or omitted, the underlying `anthropics/claude-code-action`
default is used. Available from automation `v1.29` onward — older trampoline
pins (`@v1.28` and earlier) ignore the setting.

### Changing Gemini Model

The Gemini model is read from the `GEMINI_MODEL` repository/organization Actions
**variable** (not a secret). If unset, it defaults to `gemini-3-flash-preview`.
A separate `GEMINI_FALLBACK_MODEL` variable (default `gemini-3-flash-preview`) is
used if the primary model call fails. No workflow file edit is required.

### Filtering Claude Reviews by Author

Uncomment and modify the `if` condition in `claude-code-review.yml`:

```yaml
if: |
  github.event.pull_request.user.login == 'external-contributor' ||
  github.event.pull_request.author_association == 'FIRST_TIME_CONTRIBUTOR'
```

### Adjusting Gemini Configuration

Edit the respective `gemini-*.yml` files to modify:
- Trigger conditions
- API endpoints
- Analysis parameters
- Output formats

## Troubleshooting

### Claude workflows are skipped
- Verify `CLAUDE_CODE_OAUTH_TOKEN` is set correctly
- Check if the trigger condition matches (e.g., `@claude` mention)
- Review workflow run logs in the **Actions** tab

### Gemini workflows fail
- Verify `GEMINI_API_KEY` is valid and not expired
- Check API quota limits at https://aistudio.google.com
- A quota response with provider retry guidance receives bounded backoff; a final `Reason: quota_exhausted` means all allowed attempts failed or the provider supplied no retry guidance
- Ensure GCP credentials are correct (if using Vertex AI)
- A sticky `Reason: provider_timeout` means the provider request exceeded its finite review deadline; inspect the linked run before rerunning

### Workflows don't trigger
- Check branch protection rules
- Verify workflow file syntax (YAML formatting)
- Ensure required permissions are granted

## Cost Considerations

- **Claude Code**: Usage is metered through your Anthropic account
- **Gemini**: Free tier available, check https://ai.google.dev/pricing for limits
- Review usage regularly to avoid unexpected costs

## Security Notes

- Never commit secrets directly to workflow files
- Use repository secrets for all sensitive data
- Regularly rotate API keys and tokens
- Review workflow logs for sensitive data exposure
- Limit workflow permissions to minimum required

## Support

- **Claude Code**: https://code.claude.com/docs
- **Gemini**: https://ai.google.dev/docs
- **GitHub Actions**: https://docs.github.com/actions

## Version History

- **2026-01-14**: Initial workflow setup with Claude and Gemini integration
