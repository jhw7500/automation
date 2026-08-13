# Common Workflow Standardization Design

Date: 2026-08-13

## Context

The 19 repositories registered in `scripts/workflow-config.json` currently contain
17 repositories with reusable-workflow callers and two repositories without central
callers. The 17 active consumers all satisfy the `automation@v1.39` ref and secret
contract, but their common caller files are not all generated from one canonical
source. Project-owned build, test, verification, release, and deployment workflows are
expected to differ and are outside this design.

The repository also has two overlapping baseline layouts:

- `examples/baseline-workflows/workflows/`
- `examples/baseline-workflows/.github/workflows/`

They contain different file sets and release refs. This makes it possible for setup,
audit, and rollout paths to disagree even when each path succeeds independently.

## Goals

1. Make every installed common AI caller and `bump-automation-ref.yml` derive from one
   canonical catalog.
2. Allow differences only for:
   - whether an optional common workflow is installed;
   - repository-owned settings in `.github/workflow-config.yml`;
   - explicit secret and variable mappings selected from the repository inventory.
3. Preserve every project-owned workflow byte-for-byte.
4. Detect structural drift before a fleet change is published.
5. Keep workflow delivery reversible through independent repository pull requests.
6. Perform no secret writes during this standardization rollout.

## Non-goals

- Standardizing build, test, hardware verification, packaging, release, deployment,
  ShellCheck, commit-lint, or repository-specific synchronization workflows.
- Installing optional AI workflows into repositories where those files do not already
  exist.
- Changing secret values, repository variables, or the central OpenCode provider and
  model.
- Moving an existing immutable release tag.
- Replacing per-repository workflow enablement settings with one fleet-wide value.

## Canonical Catalog

`examples/baseline-workflows/.github/` becomes the only canonical tree. The duplicate
top-level `examples/baseline-workflows/workflows/` and
`examples/baseline-workflows/workflow-config.yml` are removed after all scripts, tests,
and documentation point at the canonical tree.

The catalog records two classes.

### Required common files

Every repository that already has at least one central caller must contain these files:

- `bump-automation-ref.yml`
- `claude.yml`
- `claude-code-review.yml`
- `gemini-auto-review.yml`
- `gemini-dispatch.yml`
- `gemini-invoke.yml`
- `gemini-issue-triage.yml`
- `gemini-pr-review.yml`
- `gemini-review.yml`
- `gemini-scheduled-triage.yml`
- `gemini-triage.yml`

Missing required files are reported as blocked rather than silently installed. This
keeps an intentional removal from being re-enabled without review. The current 17
consumer repositories already contain the complete required set.

### Optional common files

These files are normalized only when the same filename already exists in the consumer:

- `auto-rereview-request.yml`
- `gemini-chat.yml`
- `opencode.yml`
- `opencode-auto-review.yml`

Their presence set is preserved exactly. Absence is not drift.

## Rendering Rules

For every managed file, the renderer starts from the canonical file rather than editing
the consumer file in place. It then applies only the following repository-derived
values:

1. Replace reusable workflow release refs with the requested verified ref, initially
   `v1.39`.
2. Rebuild explicit secret mappings from the corresponding release contract and the
   repository secret-name inventory.
3. Forward `app_id: ${{ vars.APP_ID }}` and `APP_PRIVATE_KEY` only when both the variable
   and secret exist and the central contract supports the input.
4. Use the approved full `actions/checkout` SHA in every managed file.
5. Update only the `automation_ref` scalar in an existing
   `.github/workflow-config.yml`; preserve all other repository settings and text.

No `secrets: inherit` form is generated. Secret values are never read. Secret names are
obtained from GitHub metadata and values are referenced only through GitHub expression
syntax.

## Wrapper Responsibilities

Caller wrappers own only GitHub event triggers, the reusable-job permission ceiling,
explicit inputs, and explicit secret mappings. Runtime behavior belongs to the central
reusable workflow.

In particular, the `v1.39` OpenCode reusable workflows already enforce:

- `.github/workflow-config.yml` enablement;
- automatic-review enablement;
- same-repository and non-fork pull request scope;
- trusted comment-author association;
- read-only repository contents permission for OpenCode execution.

The canonical OpenCode callers therefore do not copy the local
`check-workflow-enabled` action or duplicate its checkout job. The automatic caller
retains a cheap caller-level same-repository guard as defense in depth, while the
central workflow remains authoritative and fail-closed. Existing local copies of the
action remain untouched because project-owned build or lint workflows may use them.

`bump-automation-ref.yml` is rendered identically everywhere. Its canonical version:

- uses the approved checkout SHA;
- rewrites reusable workflow refs only;
- fails when an `automation_ref` change rewrites zero reusable refs;
- uses a GitHub App token when configured and otherwise preserves the existing explicit
  fallback behavior.

## Audit and Rollout Behavior

The official fleet path remains `scripts/rollout_workflow_fleet.py`.

`plan` mode:

1. Fetches each remote default branch into a disposable managed workspace.
2. Verifies the requested immutable central release.
3. Reads repository secret and variable names.
4. Renders the expected managed files in a preview copy.
5. Reports required-file absence, exact managed-file drift, contract failures, YAML
   failures, and new actionlint diagnostics.
6. Writes no repository secret and pushes no Git reference.

`publish` mode retains the existing explicit `--confirm` gate and creates one branch and
one pull request per repository. A failure in one repository is recorded as blocked and
does not combine that repository's changes with another repository.

The legacy direct-push setup script is not the fleet rollout authority. It is updated to
consume the same canonical directory and clearly directs fleet operations to the Python
rollout path, preventing a second template source from surviving.

## Tests

Implementation follows test-driven development. Regression coverage must prove:

1. Required managed files are rendered from the canonical source.
2. A missing required file blocks instead of being silently added.
3. Existing optional files are normalized and absent optional files remain absent.
4. Project-owned files are byte-identical before and after rendering.
5. API-key-only and GitHub-App-enabled Gemini repositories produce only the allowed
   mapping differences.
6. OpenCode caller permission, trigger, same-repository guard, input, and secret drift is
   detected.
7. `bump-automation-ref.yml` drift is detected.
8. Rendering is idempotent.
9. Invalid YAML and an unsafe or uneditable generated caller fail before any write.
10. The full existing test suite and actionlint regression gate pass.

## Rollout and Stop Conditions

1. Create and verify the automation change in an isolated worktree.
2. Merge the automation pull request before using the new renderer. No new central
   workflow tag is required because the `v1.39` reusable artifacts are unchanged.
3. Run a full fleet `plan` with secret sync disabled.
4. Use `pim-package-jhw` as the behavioral canary because it currently has the largest
   relevant OpenCode caller drift and OpenCode auto-review is enabled there.
5. Validate its PR with YAML parsing, contract audit, exact managed-file audit,
   `git diff --check`, and actionlint.
6. Merge the canary, open a harmless temporary PR, and require a successful OpenCode
   automatic-review run.
7. Publish independent PRs for the remaining repositories only after the canary passes.
8. Re-run fleet `plan`; success is 17 current, two intentionally skipped, and zero
   blocked.

Stop immediately before the next stage if release verification, secret prerequisites,
YAML parsing, contract audit, exact drift audit, actionlint regression, canary execution,
or default-branch freshness fails.

## Recovery

- The starting automation commit is
  `2254f13aab44585c78954d20749f4fb677a8c2f1`.
- Development occurs on `codex/standardize-common-workflows` in an isolated worktree.
- Before merge, deleting the branch and worktree restores the original state.
- After merge, revert the automation pull request to restore the previous renderer and
  templates.
- Consumer changes are independent pull requests and can be reverted independently.
- No release tag is moved or deleted.
- No secret value is written, so this rollout requires no secret-value rollback.

## Acceptance Criteria

- All 17 repositories with central callers have the complete required catalog.
- Every installed managed file equals the renderer output for `v1.39` and its repository
  authentication inventory.
- Optional-file presence is unchanged.
- Project-owned workflow hashes are unchanged by the rollout.
- All managed callers use `v1.39`, contain no `secrets: inherit`, and satisfy the central
  secret contracts.
- All managed checkout references use the approved v7.0.1 full SHA.
- Final fleet status is `current=17`, `skipped=2`, `blocked=0`.
- The canary OpenCode automatic-review run succeeds after merge.
