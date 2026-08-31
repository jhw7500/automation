# Review-mode command control design

Date: 2026-08-31
Status: approved; implementation plans written

## 1. Decision

Add one explicit review policy to pull-request and issue creation commands without replacing the
existing repository configuration:

- `--review` requests AI review;
- `--no-review` suppresses AI review for the affected PR head or new issue; and
- no option follows the repository's existing review configuration.

The canonical pull-request command becomes `/jhw:pr`. `/jhw:ship` remains as a deprecated,
argument-compatible alias. A new `/jhw:issue` command creates GitHub issues and can request and wait
for supported issue reviewers.

Repository-managed PR workflows use two GitHub labels as the durable override contract:

| Labels on the PR | Effective workflow policy |
| --- | --- |
| `review:request` only | Run enabled Claude, Gemini, and OpenCode review workflows |
| `review:skip` only | Do not invoke any repository-managed AI reviewer |
| neither | Use each workflow's existing `workflows.<name>.auto`, then `review.auto`, then compatibility default |
| both | Configuration error; fail closed without invoking a model |

The labels control repository-managed workflows. `/jhw:pr` applies the same effective policy to
external GitHub Apps by posting their documented manual commands when review is requested. App
automatic-review settings are disabled separately so PR-open events do not bypass the command
policy.

## 2. Goals

1. Let a user decide per PR creation or commit push whether AI review runs.
2. Apply that decision before the event that starts review, avoiding an opened/synchronize race.
3. Control the existing Claude, Gemini, and OpenCode workflows plus Codex and Gemini Code Assist.
4. Preserve current repository defaults when neither option is given.
5. Let issue creation use the same options and, when requested, wait for supported issue-review
   responses and summarize them.
6. Keep `/jhw:ship` users working while moving the primary name to `/jhw:pr`.
7. Keep review advisory: review results never edit or close an issue automatically.

## 3. Non-goals

- Replacing workflow-specific `enabled` settings, required CI, branch protection, or human approval.
- Turning GitHub labels into general-purpose commands for external Apps; Apps are invoked by the
  JHW command because they do not consume these labels.
- Cancelling or deleting a review that started before `review:skip` was applied.
- Making unsupported standalone-issue behavior a required capability of a PR-only GitHub App.
- Changing reviewer prompts, finding grammar, severity policy, or review quality gates.
- Changing Notion databases, Project Control state, or issue #52.
- Adding a new executable CLI or service when the existing JHW skill workflow and `gh` are enough.

## 4. Command interface

### 4.1 `/jhw:pr`

`/jhw:pr` keeps the current `/jhw:ship` options and behavior, including `--merge`, `--target`,
`--auto-fix`, `--base`, reviewer selection, timeout, round limits, and blocking threshold. It adds:

```text
/jhw:pr [existing options] [--review | --no-review]
```

`--review` and `--no-review` are mutually exclusive. Supplying both stops before any label, push,
PR, comment, or merge mutation.

The option changes review policy only. It does not imply `--merge`, weaken CI, or change the target
test. `--no-review --merge` is allowed only when both were explicitly supplied; the final receipt
and durable `review:skip` label state that AI review was explicitly skipped.

`/jhw:ship` is retained as a thin deprecated alias to `/jhw:pr` and forwards every argument. Its
output identifies the replacement command but otherwise follows the same state machine.

### 4.2 `/jhw:issue`

`/jhw:issue` creates an issue in the current repository from the user-supplied or current-task title
and body:

```text
/jhw:issue [issue content] [--review | --no-review] [--timeout <duration>]
```

If the title or body cannot be derived unambiguously, the command obtains them before mutation. The
new command is intentionally narrow; ordinary assignee, milestone, project, and bulk-issue
management remain outside this feature.

- `--review`: create the issue, request every preflight-supported issue reviewer once, wait up to
  the bounded timeout, apply `review:request`, and summarize responses.
- `--no-review`: apply `review:skip` and create the issue without reviewer mentions or review
  waiting.
- no option: apply neither override, read `review.auto`, and request and wait when true; otherwise
  create only.

An issue-review result never rewrites the issue body, closes the issue, changes its milestone, or
implements the feedback automatically.

## 5. Policy resolution and compatibility

The command resolves its mode before mutation:

1. reject mutually exclusive options;
2. explicit `--review` selects `request`;
3. explicit `--no-review` selects `skip`; and
4. otherwise select `auto` and read the checked-out target repository configuration.

For external App requests and issue review, `auto` uses global `review.auto`. A missing value keeps
the existing compatibility default (`true`); the baseline file continues to declare `false`
explicitly so managed repositories are not surprised. Repository-managed PR workflows additionally
preserve their existing per-workflow override precedence:

```text
workflows.<review-workflow>.auto -> review.auto -> true
```

An explicit label overrides only automatic mode, not `workflows.<name>.enabled`. A disabled or
unconfigured reviewer remains unavailable under `review:request`; the command reports that rather
than treating it as a successful review.

When `/jhw:pr` operates on an existing PR, it removes the opposite override before applying the
selected one. With no option it removes both override labels, restoring configuration-driven
behavior. If both labels are observed after reconciliation, execution stops as a policy conflict.

## 6. PR event ordering

### 6.1 New PR

To prevent `opened` from running reviewers before the override exists:

1. preflight permissions, workflow capability, labels, and App prerequisites;
2. push the branch if needed;
3. create a draft PR;
4. reconcile the selected override label while the PR is still draft;
5. verify the remote PR labels and head SHA;
6. mark the PR ready for review; and
7. request external App reviews for that exact ready head when effective policy is `request`.

The managed callers add `ready_for_review` and refuse model invocation while the PR is a draft.
Thus correctness does not depend on whether `gh pr create --label` applies a label before or after
GitHub emits `opened`: the draft event is harmless and the ready event is the first eligible review
event.

### 6.2 Existing PR with a new head

Before push, the command reconciles the override labels and verifies them through the GitHub API.
The subsequent `synchronize` event sees the intended mode. External App comments are posted only
after the remote PR head equals the pushed head.

### 6.3 Existing PR with an unchanged head

`--review` uses each installed caller's authorized manual dispatch path for one same-head central
review and posts each external App request once for the same SHA. OpenCode gains the same bounded
manual same-head contract already used by Claude and Gemini. No-option automatic mode does not force
a duplicate same-head review.

Requests are idempotent per reviewer and PR head. Command-owned App comments include a hidden
reviewer/SHA marker, and central workflows retain their authenticated same-head invocation budget.
Re-running the command for the same reviewer and SHA observes the existing request instead of
posting or dispatching another.

### 6.4 `--no-review`

The skip label is verified before a push or before a draft becomes ready. Repository-managed callers
complete their policy gate without a model invocation. The command posts no App mention and does not
wait for AI review. It still waits for required CI and an explicitly requested target test. It does
not cancel a run that began before the label was applied.

## 7. Workflow contract

Claude, Gemini, and OpenCode reusable review workflows accept a validated `review_mode` input with
exact values `auto`, `request`, `skip`, or `conflict`:

- `auto` preserves current workflow/global/default precedence;
- `request` enables review when that workflow is installed and enabled;
- `skip` returns a successful policy result without provider invocation; and
- `conflict` fails the policy result with an explicit diagnostic and no provider invocation.

`force_review` remains a separate authorized same-head mechanism. A caller may set it only for a
manual dispatch that represents `request`; it forces a full current-head review subject to the
existing invocation budget. It never legitimizes `skip` or `conflict`.

Managed callers:

- listen to `opened`, `synchronize`, and `ready_for_review`;
- derive `review_mode` from the PR labels for event-driven calls;
- pass `request` for an authorized `force_review` dispatch;
- keep same-repository/fork checks; and
- pass every mode, including `skip` and `conflict`, to the reusable policy gate so required checks
  get a terminal result.

The exact trigger/input/permission changes are recorded in `scripts/workflow-catalog.json`, baseline
callers, release inventory/verifiers, and `docs/workflows/contracts.md`. Self-review callers follow
the same contract so automation dogfoods the behavior before fleet rollout.

Implementation reuses the existing workflow/config gates and invocation-budget machinery. It does
not add a persistent service, database, or general command runtime; a shared policy helper is added
only if the test-first implementation shows that exact behavior cannot remain consistent in the
three workflows without it.

## 8. Labels and permissions

Every managed target repository uses these fixed labels for PRs and issues:

- `review:request`: explicitly request AI review for the current operation;
- `review:skip`: explicitly skip AI review for the current operation.

The command preflights repository write access and ensures missing labels exist before creating the
PR/issue or pushing a review-triggering commit. Lack of permission stops the operation with no
branch push, PR, issue, or reviewer comment. Label creation itself is the only allowed prerequisite
mutation and is reported in the receipt.

Label application is verified by reading the PR or issue back from the API. Workflows independently
reject the both-label state; they never trust command-side validation alone.

## 9. External GitHub Apps

External Apps must not retain PR-open automatic review while command-level selection is active.

### 9.1 Codex

Keep repository Code review enabled, turn off **Automatic reviews** in Codex repository settings,
and use the documented `@codex review` PR comment for explicit requests. This separates App access
from automatic invocation. The JHW command cannot safely mutate this account-level setting, so
activation requires a one-time operator confirmation.

### 9.2 Gemini Code Assist

Keep Gemini Code Assist installed and disable only PR-open code review in repository configuration:

```yaml
code_review:
  pull_request_opened:
    code_review: false
```

Do not use `code_review.disable: true`, because that disables Gemini acting on pull requests rather
than merely disabling the opened-event review. Explicit requests use the documented `/gemini review`
PR comment. Existing unrelated `.gemini/config.yaml` settings must be preserved.

Fleet preflight verifies the Gemini repository file mechanically and records a one-time operator
confirmation for the Codex setting, which GitHub does not expose as repository content. If either
cannot be confirmed, the implementation and canary PRs may remain reviewable, but fleet activation
stops before broad rollout. Updating an existing `.gemini/config.yaml` is a merge-preserving,
separately reviewed setup change; unrelated keys are never regenerated or removed.

## 10. Issue-review channels

`/jhw:issue --review` builds a reviewer plan before creating the issue:

- Claude is eligible when the managed issue-mention caller is installed and enabled.
- Central Gemini is eligible when the managed issue chat/dispatch caller is installed, enabled, and
  configured.
- Codex issue mention is eligible only for repositories where the connector/environment has been
  explicitly configured and a canary has demonstrated standalone-issue response. Official Codex
  code-review documentation guarantees PR review, so issue support is never inferred from PR setup.
- Gemini Code Assist `/gemini review` is PR-only and is not requested or awaited on standalone
  issues.
- OpenCode's current safe-review contract is PR-only and is not requested or awaited on standalone
  issues.

If no issue reviewer is eligible, `--review` stops before issue creation. Otherwise the command
creates the issue, applies `review:request`, posts one command-owned request per planned reviewer,
and records hidden request markers for retry idempotence.

GitHub does not reveal secret values during preflight. A caller that is present and enabled can
therefore still fail authentication at runtime; that result is `FAILED` and is reported with its
Actions URL rather than being mislabeled as a timeout.

The wait loop observes reviewer comments, reviews where applicable, reactions that prove a trigger
was accepted, and relevant Actions runs. Each planned reviewer finishes as `CLEAN`, `FEEDBACK`,
`FAILED`, or `TIMEOUT`; an unavailable channel is reported as `UNAVAILABLE` during preflight and is
not waited on. Trigger rejection or workflow failure is `FAILED`, not `TIMEOUT`.

The final issue summary contains the issue URL, requested and unavailable reviewers, response links,
the highest actionable disposition, and timeout/failure diagnostics. A timeout never deletes the
created issue.

## 11. PR review waiting and merge behavior

For effective `request`, `/jhw:pr` waits for the exact current head across:

- Claude, Gemini, and OpenCode managed review results that were planned;
- Codex and Gemini Code Assist responses that were requested;
- required GitHub checks; and
- the optional target command.

Each reviewer is requested once and classified using existing `/jhw:ship` semantics: `CLEAN`,
`FEEDBACK`, `FAILED`, or `TIMEOUT`. The command distinguishes a missing/failed trigger from an
accepted request that exceeded the timeout. A later push invalidates the prior wait result and
starts a new head-scoped round.

Merge remains blocked while required CI fails, the target fails, a planned reviewer has not reached
an allowed terminal state, or a finding at/above `--block-on` remains unresolved. Under explicit
`--no-review --merge`, only the AI-review gate is waived; CI, target, current-head, and mergeability
checks are unchanged.

## 12. Repository ownership

### 12.1 `jhw7500/automation`

Owns:

- label-to-`review_mode` workflow behavior;
- draft and `ready_for_review` gates;
- Claude/Gemini/OpenCode reusable and caller parity;
- baseline workflow/config templates;
- catalog, release inventory, immutable release verification, rollout checks, and contracts; and
- deterministic tests for policy precedence, conflict handling, provider non-invocation, and
  same-head authorization.

### 12.2 `jhw7500/jhw-notion`

Owns:

- canonical `skills/claude/pr.md`;
- deprecated `skills/claude/ship.md` alias;
- canonical `skills/claude/issue.md`;
- generated Codex skill synchronization;
- command documentation and skill tests; and
- the waiting, summary, idempotence, and mutation-order instructions used by the commands.

Only skill files and their documentation/tests change in this repository. Notion database and
Project Control records are excluded.

## 13. Error handling and recovery

- Invalid option combination: fail before mutation.
- Missing permission or required label: fail before push/PR/issue creation, except reported creation
  of an absent label during preflight.
- Both labels: workflow policy failure, no model request.
- Draft PR: no model request; ready transition retries policy normally.
- Push succeeds but later request fails: keep the branch/PR, report exact failed channel, and allow
  idempotent resume at the same head.
- Issue creation succeeds but a reviewer fails or times out: keep the issue and return partial
  results.
- App trigger not acknowledged: classify `FAILED` after the trigger window rather than waiting the
  full review timeout.
- New commit during a wait: discard stale terminal results and resolve policy for the new head.
- Missing deployed workflow capability: stop before using a review override; do not pretend an old
  caller can honor `--no-review`.

## 14. Verification

Automation tests cover at least:

1. `review.auto=true` and `review.auto=false` with no labels;
2. `review:request` overriding false;
3. `review:skip` overriding true;
4. both labels failing without any provider step;
5. draft `opened` producing zero reviews and `ready_for_review` producing exactly one;
6. label verification preceding a review-triggering push;
7. authorized same-head dispatch and per-reviewer/SHA deduplication;
8. disabled or missing reviewers reported unavailable rather than successful;
9. exact catalog, baseline, permission, release inventory, and local-action contracts; and
10. complete pytest, YAML parsing, and actionlint suites.

JHW skill tests cover at least:

1. mutual exclusion before mutation;
2. new-PR draft/label/ready order;
3. existing-PR label-before-push order;
4. no-option override removal and config fallback;
5. external App command text and hidden-marker deduplication;
6. `--no-review` issuing zero AI requests while retaining CI/target gates;
7. explicit skip merge receipt;
8. issue reviewer discovery, zero-reviewer preflight failure, waiting, and summary;
9. timeout/failure preserving the created issue; and
10. canonical-to-generated skill sync plus install-safety tests.

## 15. Release and rollout

1. Implement and review the automation contract on an isolated branch.
2. Run repository-wide tests and dogfood the PR with explicit request/skip cases.
3. Merge only a verified current head and create the next immutable automation release; never move a
   release tag.
4. Roll out to two bounded canary scenarios: `request` overriding an auto-off repository and `skip`
   overriding an auto-on repository. Each canary verifies draft ordering, exact provider counts,
   App mentions, same-head deduplication, and current-head results.
5. Implement, test, review, and merge the jhw-notion skill changes against the verified canary
   contract, then run the canonical-to-Codex sync.
6. Confirm Codex Automatic reviews are off and apply the merge-preserving Gemini PR-open
   configuration change before fleet activation.
7. Use the existing fleet planner and publisher for managed callers/config, inspect every proposed
   repository diff, and roll out in bounded batches.
8. Stop rollout on duplicate App reviews, a label/push race, a non-terminal required check, a
   provider invocation under skip/conflict/draft, or any release-verifier mismatch.

Rollback uses a new automation release that restores the prior caller behavior and a normal skill
revert PR. Immutable tags are not rewritten. Labels may remain harmlessly installed if the feature
is rolled back.

## 16. References

- Codex documents separate repository Code review access, manual `@codex review`, and the Automatic
  reviews setting: <https://developers.openai.com/codex/integrations/github>
- Gemini documents repository `.gemini/config.yaml`, `pull_request_opened.code_review`, and
  `code_review.disable`: <https://docs.cloud.google.com/gemini/docs/code-review/customize-repo-review>
- Gemini documents manual `/gemini review` on PR issue comments:
  <https://docs.cloud.google.com/gemini/docs/code-review/use-code-assist-github>
