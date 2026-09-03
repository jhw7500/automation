# Review-mode JHW Commands Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `/jhw:ship` with canonical `/jhw:pr`, add per-operation review selection, and add `/jhw:issue` with bounded supported-reviewer waiting and summaries.

**Architecture:** Keep the existing Markdown skill runtime: move the mature ship review-round contract into canonical `pr.md`, add small tested shell contracts for policy/mutation ordering and explicit App requests, and leave `ship.md` as an argument-preserving alias. `issue.md` reuses the same mode vocabulary and response classifications but has its own narrow creation/reviewer plan. Generated Codex skills continue to come only from `scripts/sync-codex-skills.mjs`.

**Tech Stack:** Markdown skill workflows, Bash snippets executed by agent tools, GitHub CLI/API, Node.js contract tests, canonical-to-Codex skill generator, shell install-safety tests.

**Spec:** `/home/jhw/ai/opencode/projects/automation/.worktrees/review-mode-command-control/docs/superpowers/specs/2026-08-31-review-mode-command-control-design.md`

## Global Constraints

- Target repository: `/home/jhw/ai/opencode/projects/jhw-notion-runtime`; implement from a fresh isolated worktree based on current `origin/main`.
- Before editing skill content, use `skill-creator` and `superpowers:writing-skills`; use `superpowers:test-driven-development` for every behavior change.
- Canonical sources are only `skills/claude/*.md`; never hand-edit generated `skills/codex/jhw-*` content.
- `/jhw:pr` keeps every current `/jhw:ship` option and adds exactly `--review` and `--no-review`.
- `/jhw:ship` remains a deprecated argument-compatible alias.
- `--review` and `--no-review` together fail before labels, push, PR, issue, comment, dispatch, or merge.
- Explicit modes use only `review:request` and `review:skip`; no option removes both overrides and follows configuration.
- New PR ordering is push -> draft PR -> label reconcile/verify -> ready -> App requests.
- Existing PR ordering is label reconcile/verify -> push -> App requests.
- `--no-review` invokes and awaits no AI reviewer; CI, target, head, mergeability, and explicit merge requirements remain.
- App requests are idempotent per reviewer and PR head.
- Issue review never edits/closes the issue or implements feedback automatically.
- Codex standalone-issue review is planned only after demonstrated connector/environment support; Gemini Assist and OpenCode are not issue reviewers.
- Do not modify Notion databases, Project Control state, or issue #52.
- Automation `v1.51` remote verification and the canary caller rollout are prerequisites.
- Every shell command in this environment starts with `rtk`.

## File map

All paths below are relative to the jhw-notion target repository.

| Path | Responsibility |
| --- | --- |
| `skills/claude/pr.md` | Canonical PR creation, review policy, waiting, repair, and merge workflow |
| `skills/claude/ship.md` | Deprecated alias that forwards to `pr.md` |
| `skills/claude/issue.md` | Issue creation and supported-reviewer workflow |
| `scripts/test-pr-skill-contract.mjs` | Executable PR option, ordering, trigger, and merge contracts |
| `scripts/test-issue-skill-contract.mjs` | Executable issue policy, reviewer-plan, and wait contracts |
| `scripts/test-install-safety.sh` | Runs both skill contracts in the existing install-safety suite |
| `skills/claude/AGENTS.md` | User-facing canonical/alias command inventory |
| `README.md` | Installation and command summary |
| `skills/codex/jhw-pr/*` | Generated Codex PR skill |
| `skills/codex/jhw-issue/*` | Generated Codex issue skill |
| `skills/codex/jhw-ship/*` | Regenerated deprecated alias skill |

---

### Task 1: Establish `/jhw:pr` as the canonical skill without behavior drift

**Files:**
- Create from rename: `skills/claude/pr.md`
- Create: `skills/claude/ship.md`
- Rename: `scripts/test-ship-skill-contract.mjs` -> `scripts/test-pr-skill-contract.mjs`
- Modify: `scripts/test-install-safety.sh`

**Interfaces:**
- Preserves: all current v3 Claude/Gemini, OpenCode, Codex, timeout, severity, target, and auto-fix behavior.
- Produces: canonical markers and state paths prefixed `jhw-pr`.
- Produces: alias file containing no duplicate review implementation.

- [ ] **Step 1: Write the RED canonical/alias assertions**

Rename the test file, then change its source paths:

```bash
rtk git mv scripts/test-ship-skill-contract.mjs scripts/test-pr-skill-contract.mjs
```

```javascript
const canonicalPr = join(repoRoot, "skills", "claude", "pr.md");
const shipAlias = join(repoRoot, "skills", "claude", "ship.md");
const prText = readFileSync(canonicalPr, "utf8");
const aliasText = readFileSync(shipAlias, "utf8");

assert.match(prText, /^# \/jhw:pr — PR 생성/m);
assert.match(prText, /<!-- jhw-pr:codex-review round=/);
assert.doesNotMatch(prText, /<!-- jhw-ship:codex-review/);
assert.match(aliasText, /deprecated/i);
assert.match(aliasText, /\/jhw:pr/);
assert.doesNotMatch(aliasText, /ship-round-contract: trigger-and-scope:begin/);
```

Keep every existing executable round test, but make it extract the block from `prText` and expect `jhw-pr` marker/state names.

- [ ] **Step 2: Run the renamed test and confirm RED**

```bash
rtk node scripts/test-pr-skill-contract.mjs
```

Expected: failure reports missing `skills/claude/pr.md`.

- [ ] **Step 3: Move the mature implementation and create a thin alias**

Use Git rename so history follows the implementation:

```bash
rtk git mv skills/claude/ship.md skills/claude/pr.md
```

Change the canonical frontmatter/title/examples and these internal names only:

```text
/jhw:ship                         -> /jhw:pr
jhw-ship:codex-review             -> jhw-pr:codex-review
jhw-ship.${PR}.round.${ROUND}     -> jhw-pr.${PR}.round.${ROUND}
```

Create `skills/claude/ship.md` with this complete alias behavior:

```markdown
---
description: "(deprecated) /jhw:pr 사용 — 모든 인자를 변경 없이 전달"
argument-hint: "[same arguments as /jhw:pr]"
---

# /jhw:ship (deprecated)

이 명령은 `/jhw:pr`의 호환 alias다.

1. 같은 canonical 디렉터리의 `pr.md`를 읽는다.
2. 사용자가 `/jhw:ship` 뒤에 준 모든 인자를 순서와 값 변경 없이 `/jhw:pr` 인자로 해석한다.
3. 실행 시작 시 `/jhw:ship`이 deprecated이며 `/jhw:pr`로 대체되었다고 한 줄 알린다.
4. 이후에는 `pr.md`의 승인점·안전 규칙·테스트·리뷰·머지 절차만 실행한다.

별도 PR·리뷰·머지 로직을 이 alias에 복제하지 않는다.
```

- [ ] **Step 4: Point install safety at the canonical test**

Replace only:

```bash
node "$REPO_ROOT/scripts/test-ship-skill-contract.mjs"
```

with:

```bash
node "$REPO_ROOT/scripts/test-pr-skill-contract.mjs"
```

- [ ] **Step 5: Run preserved behavior tests**

```bash
rtk node scripts/test-pr-skill-contract.mjs
```

Expected: `pr skill contract: ok`; every pre-existing round/polling assertion remains present.

- [ ] **Step 6: Commit the canonical rename**

```bash
rtk git add skills/claude/pr.md skills/claude/ship.md scripts/test-pr-skill-contract.mjs scripts/test-install-safety.sh
rtk git commit -m "refactor(skills): make jhw pr the canonical ship workflow"
```

---

### Task 2: Add PR review-mode parsing, labels, and race-free mutation order

**Files:**
- Modify: `skills/claude/pr.md`
- Modify: `scripts/test-pr-skill-contract.mjs`

**Interfaces:**
- Produces shell functions: `jhw_pr_review_mode_from_args`, `jhw_pr_global_auto_enabled`, `jhw_pr_ensure_review_labels`, `jhw_pr_reconcile_review_labels`, `jhw_pr_verify_remote_policy`.
- Produces scalar mode: `request`, `skip`, or `auto`.
- Consumes: `REPO_NWO`, optional `PR`, and original command arguments.

- [ ] **Step 1: Write RED executable mode tests**

Extract a new `<!-- pr-review-mode-contract:begin -->` Bash block and execute it with the existing fake-command test harness. Assert:

```javascript
const runMode = (args) => run(baseState(), `jhw_pr_review_mode_from_args ${args}`);

assert.equal((await runMode("--review")).stdout.trim(), "request");
assert.equal((await runMode("--no-review")).stdout.trim(), "skip");
assert.equal((await runMode("--merge --target")).stdout.trim(), "auto");
assert.notEqual((await runMode("--review --no-review")).code, 0);
```

Add global-config cases proving `review.auto: true`, `review.auto: false`, and missing config resolve to `true`, `false`, and compatibility `true`; a non-boolean value must fail before mutation.

Add a mutation-log fake `gh` and assert exact order for new and existing PRs:

```javascript
assert.deepEqual(newPrLog, ["ensure-labels", "push", "create-draft", "set-request", "verify", "ready"]);
assert.deepEqual(existingPrLog, ["ensure-labels", "set-skip", "verify", "push"]);
assert.deepEqual(autoExistingLog, ["ensure-labels", "remove-request", "remove-skip", "verify", "push"]);
```

- [ ] **Step 2: Run the focused mode tests and confirm RED**

```bash
rtk node scripts/test-pr-skill-contract.mjs
```

Expected: missing contract marker/function failures.

- [ ] **Step 3: Implement option resolution before all mutations**

Add the exact parser:

```bash
jhw_pr_review_mode_from_args() {
  local arg saw_review=0 saw_no_review=0
  for arg in "$@"; do
    case "$arg" in
      --review) saw_review=1 ;;
      --no-review) saw_no_review=1 ;;
    esac
  done
  (( saw_review == 0 || saw_no_review == 0 )) || {
    echo "--review and --no-review are mutually exclusive" >&2
    return 2
  }
  (( saw_review == 1 )) && { printf 'request\n'; return; }
  (( saw_no_review == 1 )) && { printf 'skip\n'; return; }
  printf 'auto\n'
}
```

Call it during the first preflight, before dirty-tree repair or any GitHub mutation. Add both options to frontmatter, the option table, examples, and merge rules.

Implement `jhw_pr_global_auto_enabled` with Ruby's standard YAML library, reading only global `review.auto`. It prints `true` or `false`, defaults to `true` only when the file/key is absent, and exits nonzero when the present value is not a Boolean. Per-workflow automatic overrides remain owned by the managed workflows and are not reimplemented in the skill.

- [ ] **Step 4: Implement fixed-label preflight and reconciliation**

`jhw_pr_ensure_review_labels` validates `REPO_NWO`, reads labels first, and creates only a missing label with fixed descriptions/colors. `jhw_pr_reconcile_review_labels` uses `gh pr edit` to remove the opposite label before adding the selected label; `auto` removes both. `jhw_pr_verify_remote_policy` reads back `.labels[].name` and fails on both labels, a missing explicit label, or a remaining override in auto mode.

Before label creation, query `gh repo view --json viewerPermission -q .viewerPermission` and require `ADMIN`, `MAINTAIN`, or `WRITE`. The test must prove a read-only permission exits before label creation, push, or PR creation. Missing labels may then be created as the single reported prerequisite mutation.

The contract must use these exact names and never infer by prefix:

```bash
JHW_REVIEW_REQUEST_LABEL='review:request'
JHW_REVIEW_SKIP_LABEL='review:skip'
```

- [ ] **Step 5: Encode the new/existing PR state machines**

Document and test these exact transitions:

```text
new:      label-definition preflight -> push -> gh pr create --draft -> reconcile -> verify head/labels/draft -> gh pr ready
existing: label-definition preflight -> reconcile -> verify labels/current remote head -> push -> verify new remote head
```

Do not rely on `gh pr create --label` ordering. After any push, refresh `SHA` from `git rev-parse HEAD` and require `gh pr view --json headRefOid -q .headRefOid` to equal it.

- [ ] **Step 6: Add explicit skip merge semantics**

The skill must reject implicit review waiver. Only the literal combination `--no-review --merge` waives the AI gate, and the final receipt must include:

```text
AI review: explicitly skipped (--no-review; review:skip)
```

It must still require required CI, target PASS when requested, current head, mergeability, and supported merge method.

- [ ] **Step 7: Run tests and commit**

```bash
rtk node scripts/test-pr-skill-contract.mjs
rtk git add skills/claude/pr.md scripts/test-pr-skill-contract.mjs
rtk git commit -m "feat(skills): add PR review policy options"
```

---

### Task 3: Request every PR reviewer exactly once and preserve wait semantics

**Files:**
- Modify: `skills/claude/pr.md`
- Modify: `scripts/test-pr-skill-contract.mjs`

**Interfaces:**
- Produces: `jhw_pr_request_app_review(reviewer, command, head)`.
- Produces: `jhw_pr_dispatch_same_head(workflow_file, workflow_name, head)`.
- Produces: `jhw_pr_wait_required_checks(pr, head)` independent of the AI-review mode.
- Reuses: existing reviewer classifications and head-scoped polling.

- [ ] **Step 1: Write RED request/deduplication tests**

Add fake GitHub responses proving:

- Codex posts exactly `@codex review` plus a hidden marker whose `head=` value matches `[a-f0-9]{40}`;
- Gemini Assist posts exactly `/gemini review` plus reviewer/head marker;
- one existing actor-owned matching marker is reused;
- duplicate matching markers are `TRIGGER_FAILED`;
- a marker for an old head does not suppress the current request; and
- `skip` produces zero comments, zero workflow dispatches, and zero AI waits while still invoking the required-CI and optional target gates.

- [ ] **Step 2: Run request tests and confirm RED**

```bash
rtk node scripts/test-pr-skill-contract.mjs
```

Expected: Gemini request helper and generic head marker are absent.

- [ ] **Step 3: Generalize the current Codex trigger helper**

Replace the round-specific Codex posting path with a generic helper that validates reviewer, command, actor, PR, and 40-character head. It searches actor-owned issue comments for the exact hidden marker before posting. Accepted pairs are closed:

```text
codex         -> @codex review
gemini-assist -> /gemini review
```

Keep the current trigger grace, acknowledgment, bot identity discovery, and response classification. Auto-fix rounds call the same helper after every successful push.

- [ ] **Step 4: Add central same-head dispatch with deduplication**

For explicit `--review` on an unchanged remote head, query Actions runs by exact `head_sha` and workflow name. Reuse an existing queued/in-progress/completed current-head `workflow_dispatch` run; otherwise run exactly one:

```bash
gh workflow run claude-code-review.yml --repo "$REPO_NWO" -f pr_number="$PR" -f force_review=true
gh workflow run gemini-auto-review.yml --repo "$REPO_NWO" -f pr_number="$PR" -f force_review=true
gh workflow run opencode-auto-review.yml --repo "$REPO_NWO" -f pr_number="$PR" -f force_review=true
```

Dispatch only installed/enabled reviewers. A missing caller is `UNAVAILABLE`; an API rejection is `TRIGGER_FAILED`; an accepted run that exceeds the review timeout is `TIMEOUT`.

- [ ] **Step 5: Define mode-to-wait behavior**

Add a single table to the skill and assertions to the test:

| Effective command policy | Managed workflows | Apps | AI wait |
| --- | --- | --- | --- |
| request | event run or same-head dispatch | explicit head-scoped request | planned reviewers |
| skip | policy-only terminal checks | none | none |
| auto=true | ordinary event runs | explicit head-scoped request | planned reviewers |
| auto=false | no provider runs | none | none |

`--reviewers` remains a user-selected waiting subset; it does not change the repository-wide label consumed by managed workflows. State this limitation plainly so it is not mistaken for a per-provider enable switch.

- [ ] **Step 6: Add the AI-independent required-CI gate**

For every mode, start a current-PR required-check wait using `gh pr checks "$PR" --required --watch --interval 10`. Verify the PR head before and after the wait; a changed head restarts policy resolution. Treat a failed/cancelled required check or an inability to read required checks as a failed merge gate. Run the existing optional target gate in parallel. In `skip`/auto-false mode, completion of CI and target ends the wait without inspecting AI artifacts.

- [ ] **Step 7: Preserve auto-fix and current-head merge gates**

Update every old `jhw-ship` state/marker expectation to `jhw-pr`, request Codex and Gemini Assist after each new head, and retain the rule that no new push occurs while any planned reviewer is pending/failed/timed out. A later push invalidates every prior App response and workflow result.

- [ ] **Step 8: Run the complete PR skill contract and commit**

```bash
rtk node scripts/test-pr-skill-contract.mjs
rtk git add skills/claude/pr.md scripts/test-pr-skill-contract.mjs
rtk git commit -m "feat(skills): request head-scoped PR reviews"
```

---

### Task 4: Generate and document `/jhw:pr` plus the ship alias

**Files:**
- Modify: `skills/claude/AGENTS.md`
- Modify: `README.md`
- Generate: `skills/codex/jhw-pr/SKILL.md`
- Generate: `skills/codex/jhw-pr/references/pr.md`
- Modify generated: `skills/codex/jhw-ship/SKILL.md`
- Preserve generated link: `skills/codex/jhw-ship/references/ship.md`

**Interfaces:**
- Consumes: canonical `pr.md` and alias `ship.md`.
- Produces: discoverable `$jhw-pr`; keeps `$jhw-ship` discoverable but deprecated.

- [ ] **Step 1: Add RED sync/inventory assertions**

Extend `scripts/test-pr-skill-contract.mjs` to require `skills/claude/AGENTS.md` lists `pr.md` under custom skills and `ship.md` under deprecated aliases. Require README mentions `/jhw:pr --review`, `/jhw:pr --no-review`, and `/jhw:ship` replacement.

- [ ] **Step 2: Update canonical documentation**

Move the current ship custom-skill row/pattern to `pr.md`, list all existing options plus the two new flags, and add `ship.md -> /jhw:pr` to the deprecated table. Update README's command summary without changing Notion/MCP sections.

- [ ] **Step 3: Generate Codex skills from canonical sources**

```bash
rtk node scripts/sync-codex-skills.mjs
rtk node scripts/sync-codex-skills.mjs --check
```

Expected: `jhw-pr` is created, `jhw-ship` description is regenerated from the alias, and every reference is a relative symlink to its canonical Claude file.

- [ ] **Step 4: Run PR and install-safety tests**

```bash
rtk node scripts/test-pr-skill-contract.mjs
rtk bash scripts/test-install-safety.sh
```

Expected: both exit zero.

- [ ] **Step 5: Commit canonical and generated PR skills**

```bash
rtk git add skills/claude/AGENTS.md README.md skills/codex/jhw-pr skills/codex/jhw-ship
rtk git commit -m "docs(skills): publish jhw pr and deprecate ship"
```

---

### Task 5: Add `/jhw:issue` creation and reviewer-plan policy

**Files:**
- Create: `skills/claude/issue.md`
- Create: `scripts/test-issue-skill-contract.mjs`
- Modify: `scripts/test-install-safety.sh`

**Interfaces:**
- Produces: modes `request`, `skip`, `auto` with the same mutual exclusion as PR.
- Produces: planned issue reviewer set from enabled Claude, enabled central Gemini, and capability-proven Codex.
- Produces: issue labels and one hidden request marker per planned reviewer.

- [ ] **Step 1: Write RED issue option and no-mutation tests**

Create the test with a fake `gh` command and mutation log. Assert:

```javascript
const mode = (args) => runIssueContract(
  `jhw_issue_review_mode_from_args ${args}`,
  { viewerPermission: "WRITE", workflows: [] },
);

assert.equal((await mode("--review")).stdout.trim(), "request");
assert.equal((await mode("--no-review")).stdout.trim(), "skip");
assert.equal((await mode("")).stdout.trim(), "auto");
assert.notEqual((await mode("--review --no-review")).code, 0);
assert.deepEqual(mutatingCalls, []);
```

Add cases proving `--review` with zero eligible reviewers fails before `gh issue create`, while a later reviewer failure never invokes issue delete/close/edit.

- [ ] **Step 2: Run the new test and confirm missing skill failure**

```bash
rtk node scripts/test-issue-skill-contract.mjs
```

Expected: failure reports missing `skills/claude/issue.md`.

- [ ] **Step 3: Create the narrow issue skill interface**

Use exact frontmatter:

```yaml
---
description: "GitHub 이슈 생성 · --review 지원 리뷰어 요청·대기·요약 · --no-review 리뷰 생략 · --timeout 대기한도"
argument-hint: "[title/body] [--review|--no-review] [--timeout <min>]"
---
```

The first phase resolves title/body, repository, mode, timeout, permission, labels, and reviewer plan. It must not expose assignee/milestone/project/bulk-management options.

- [ ] **Step 4: Implement reviewer discovery without secret guessing**

Claude is eligible only when `.github/workflows/claude.yml` exists and `workflows.claude.enabled` is true. Central Gemini is eligible only when `gemini-chat.yml` or its documented managed mention route exists and the corresponding config is enabled. Codex is eligible only when repository-local capability evidence named in the skill exists or the operator explicitly confirms a successful issue canary for that repository. GitHub secret values are never claimed during preflight.

If mode is `auto`, read global `review.auto`, default true only when missing, and turn the reviewer plan on/off accordingly. `skip` plans no reviewer. `request` requires at least one eligible reviewer.

- [ ] **Step 5: Implement create/label/request ordering**

Use this exact order:

```text
validate options/content -> ensure label definitions -> discover reviewers -> create issue -> apply one explicit label or neither -> verify -> post planned mentions
```

Apply `review:request` for request, `review:skip` for skip, and neither for auto. Verify the issue labels by reading the created issue back before posting any mention. Auto mode uses global `review.auto` to decide whether the already-created issue receives reviewer requests; it still carries neither override label.

Request bodies and markers are:

```text
@claude 이 이슈의 요구사항·누락 조건·구현 위험을 검토해 주세요.
<!-- jhw-issue:review-request reviewer=claude -->

@gemini 이 이슈의 요구사항·누락 조건·구현 위험을 검토해 주세요.
<!-- jhw-issue:review-request reviewer=gemini -->

@codex 이 이슈의 요구사항·누락 조건·구현 위험을 검토해 주세요.
<!-- jhw-issue:review-request reviewer=codex -->
```

Post each in its own issue comment. On resume, reuse exactly one actor-owned matching marker; multiple markers are `FAILED`.

- [ ] **Step 6: Add the issue contract to install safety**

Append exactly:

```bash
node "$REPO_ROOT/scripts/test-issue-skill-contract.mjs"
```

beside the PR skill contract invocation.

- [ ] **Step 7: Run issue tests and commit**

```bash
rtk node scripts/test-issue-skill-contract.mjs
rtk git add skills/claude/issue.md scripts/test-issue-skill-contract.mjs scripts/test-install-safety.sh
rtk git commit -m "feat(skills): add review-aware issue creation"
```

---

### Task 6: Add bounded issue waiting and response summaries

**Files:**
- Modify: `skills/claude/issue.md`
- Modify: `scripts/test-issue-skill-contract.mjs`

**Interfaces:**
- Produces reviewer states: `PENDING`, `CLEAN`, `FEEDBACK`, `FAILED`, `TIMEOUT`, `UNAVAILABLE`.
- Produces final fields: issue URL, requested reviewers, unavailable reviewers, response links, highest disposition, diagnostics.

- [ ] **Step 1: Write RED response-classification tests**

Use fixtures for comments, reactions, and Actions runs to prove:

- acknowledged mention plus substantive no-problem response -> `CLEAN`;
- response containing actionable requirements/risks -> `FEEDBACK`;
- workflow conclusion failure or explicit connector rejection -> `FAILED`;
- no trigger acknowledgment within the short trigger window -> `FAILED`, not `TIMEOUT`;
- acknowledged trigger with no terminal response by `--timeout` -> `TIMEOUT`;
- preflight unsupported channel -> `UNAVAILABLE` and no wait; and
- a response/request from before issue creation or wrong bot identity is ignored.

- [ ] **Step 2: Run classification tests and confirm RED**

```bash
rtk node scripts/test-issue-skill-contract.mjs
```

Expected: missing wait-contract marker and classifier functions.

- [ ] **Step 3: Implement a bounded poll loop**

Add `<!-- issue-review-wait-contract:begin -->` with functions that poll issue comments, reactions on each request comment, and relevant Actions runs at approximately 60-second intervals. Default timeout is 20 minutes and must be a positive integer. Capture issue creation time and request comment IDs so old signals cannot finish the run.

The loop terminates when every requested reviewer is terminal or the deadline is reached. It never calls issue edit/delete/close endpoints.

- [ ] **Step 4: Implement the summary and exit policy**

Render one compact table:

```text
Reviewer | Status | Response | Diagnostic
```

Highest disposition order is `FAILED/TIMEOUT > FEEDBACK > CLEAN`; `UNAVAILABLE` is listed separately because it was never requested. Return the issue URL even on partial failure. Do not convert review feedback into body edits or implementation work.

- [ ] **Step 5: Run the complete issue contract**

```bash
rtk node scripts/test-issue-skill-contract.mjs
```

Expected: all issue states and preservation behavior pass. The combined install suite runs after generated issue output exists in Task 7.

- [ ] **Step 6: Commit waiting and reporting**

```bash
rtk git add skills/claude/issue.md scripts/test-issue-skill-contract.mjs
rtk git commit -m "feat(skills): wait for issue review responses"
```

---

### Task 7: Publish generated issue skill and verify the jhw-notion PR

**Files:**
- Modify: `skills/claude/AGENTS.md`
- Modify: `README.md`
- Generate: `skills/codex/jhw-issue/SKILL.md`
- Generate: `skills/codex/jhw-issue/references/issue.md`
- Modify only on demonstrated defects: prior Task files.

**Interfaces:**
- Produces: reviewed and merged jhw-notion skill PR.
- Consumes: remotely verified automation `v1.51` and jhw-notion canary callers.

- [ ] **Step 1: Document the issue skill and generate Codex output**

Add `issue.md` to the custom skill table and README examples for `--review`, `--no-review`, and timeout. Then run:

```bash
rtk node scripts/sync-codex-skills.mjs
rtk node scripts/sync-codex-skills.mjs --check
```

- [ ] **Step 2: Run complete repository verification**

```bash
rtk node scripts/test-pr-skill-contract.mjs
rtk node scripts/test-issue-skill-contract.mjs
rtk bash scripts/test-install-safety.sh
rtk npm test --prefix mcp-server
rtk git diff --check origin/main...HEAD
```

Expected: every command exits zero and only planned skill/docs/test/generated paths differ.

- [ ] **Step 3: Inspect generated ownership and symlinks**

Run `rtk node scripts/sync-codex-skills.mjs --check` again after tests and verify `jhw-pr`, `jhw-ship`, and `jhw-issue` each contain only `SKILL.md` plus one relative reference symlink. Confirm the installed `/home/jhw/.codex/skills/jhw-*` targets remain repository-owned and no foreign target was replaced.

- [ ] **Step 4: Open the skill PR with explicit review request**

Push the isolated branch, use the branch's `pr.md` workflow to create a draft PR, apply/verify `review:request`, make it ready, and request installed external Apps exactly once for the final head. The managed v1.51 caller policy must report `request` while the repository default remains auto-off.

- [ ] **Step 5: Review, repair, and reverify**

Inspect every central/App finding against current skill contracts. Apply only validated changes with a failing regression assertion first, regenerate Codex skills, and rerun Step 2 after every final-head change. Do not accept duplicate, stale-head, or unsupported issue-App findings as blockers without evidence.

- [ ] **Step 6: Merge the verified current head**

Merge only when required CI, PR/issue skill tests, sync check, install safety, requested reviewers, and current-head identity are green. Confirm the merged main includes the exact verified PR head and the live repository-backed Codex skill symlinks expose `$jhw-pr` and `$jhw-issue`.

---

### Task 8: Run live issue canary and bounded fleet activation

**Files:**
- Consumer configuration changes only in repositories where the corresponding App is installed.
- Preserve all unrelated `.gemini/config.yaml` keys.
- No Notion/Project Control writes.

**Interfaces:**
- Produces: one live `/jhw:issue --review` evidence set.
- Produces: fleet callers pinned to verified `v1.51` and App automatic-review prerequisites applied.

- [ ] **Step 1: Run a standalone issue canary in jhw-notion**

Create one clearly marked review canary issue using `/jhw:issue --review --timeout 20`. Require Claude and central Gemini when enabled. Include Codex only if the repository's connector/environment was proven during preflight. Record request comment IDs, acknowledgments, response URLs, terminal classifications, and the final summary.

- [ ] **Step 2: Verify preservation behavior**

Confirm the command did not edit the issue body, close it, change milestone/project, or implement feedback. After evidence is attached to the rollout record, the operator may close the canary issue as explicit cleanup; the command itself must not do so.

- [ ] **Step 3: Plan the managed workflow fleet from immutable `v1.51`**

From a clean automation checkout, first require an unused task-specific workspace, then run the fleet tool in plan mode for every repository in `scripts/workflow-config.json`:

```bash
rtk python3 -c 'from pathlib import Path; p=Path("/tmp/automation-v151-fleet-plan"); assert not p.exists(), f"workspace already exists: {p}"'
rtk python3 scripts/rollout_workflow_fleet.py --automation . --workspace /tmp/automation-v151-fleet-plan --initialize-workspace --mode plan --ref v1.51
```

Require the release verifier to identify the remote peeled `v1.51` commit and inspect each proposed diff for only catalog-owned workflow/config paths.

- [ ] **Step 4: Prepare App settings only where installed**

For repositories with Gemini Code Assist, create a normal merge-preserving setup PR that sets only:

```yaml
code_review:
  pull_request_opened:
    code_review: false
```

For repositories with Codex Code review, record operator confirmation that Code review remains enabled and Automatic reviews is disabled. Do not install a new App merely to satisfy this feature. Repositories without an App mark that channel `UNAVAILABLE`.

> **Superseded 2026-09-03:** Codex Automatic reviews is now **enabled**, so Codex reviews every
> pull request alongside the managed reviewers. The operator confirmation described below is no
> longer required. See `docs/workflows/contracts.md` for the current policy.

- [ ] **Step 5: Publish in bounded batches**

Publish the fleet plan first to the already-proven jhw-notion canary, then batches of at most four repositories. For each rollout PR, verify immutable caller pins, `ready_for_review`, `review_mode`, unchanged auth profile, actionlint, and config preservation before merge. Stop the batch on any duplicate App review, provider invocation under skip/draft/conflict, label race, or verifier mismatch.

For each approved batch, rerun the same workspace with `--mode publish --ref v1.51 --confirm` and explicit `--repo` arguments for only the repositories in that batch. Never use publish without reviewing the immediately preceding plan output.

- [ ] **Step 6: Run one request and one skip smoke check per batch**

Use an existing normal PR when safe; otherwise a bounded disposable docs PR. `review:request` on an auto-off repository must produce the planned reviewers. `review:skip` on an auto-on test head must produce successful policy checks and zero providers/Apps. Close/delete only explicitly created disposable smoke branches after evidence capture.

- [ ] **Step 7: Record final activation evidence**

Produce a repository table containing automation pin, request/skip support, Codex setting, Gemini config, available issue reviewers, rollout PR, and smoke evidence. A repository remains not activated when any required cell is missing; this does not roll back already verified repositories or move `v1.51`.
