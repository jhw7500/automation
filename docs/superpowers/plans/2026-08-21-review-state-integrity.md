# Review State Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Claude, Gemini, and OpenCode review checkpoints provenance-bound, coverage-aware, sanitized before success evaluation, and immune to stale-run overwrites.

**Architecture:** Each workflow keeps its existing model integration but adopts the same v2 canonical comment envelope and state transition rules. The workflow—not the model—constructs reserved metadata, and every write is guarded by the captured PR head and monotonic `(run_id, run_attempt)` generation. This slice uses the existing diff preparation paths with explicit readiness flags; the second plan replaces those paths with the shared deterministic action.

**Tech Stack:** GitHub Actions YAML, Bash, jq, JavaScript in `actions/github-script`, Python pytest harnesses, Node.js workflow-script harness.

**Spec:** `docs/superpowers/specs/2026-08-21-review-state-scope-hardening-design.md`

## Global Constraints

- A successful checkpoint requires a prepared review input, successful model step, non-empty sanitized body, current captured head, and a strictly newer `(run_id, run_attempt)` generation.
- A validated previous successful checkpoint with an unchanged full-diff hash is the only model-free checkpoint path; that optimization belongs to the second plan.
- Legacy comments may be display targets but never trusted input state.
- Reserved header, marker, visible metadata, and JSON state lines are workflow-generated only.
- Invalid previous state falls back to a full PR review.
- Stale runs never mutate a PR comment.
- Do not change model/provider versions, authentication families, or unrelated workflows.

---

### Task 1: Lock the v2 envelope parser contract

**Files:**
- Modify: `tests/test_review_workflow_logic.py:1-185`
- Modify: `.github/workflows/claude-code-review.yml:119-191`

**Interfaces:**
- Consumes: issue comments returned by `gh api repos/$GITHUB_REPOSITORY/issues/$PR_NUM/comments --paginate`.
- Produces: a canonical v2 state line with fields `schema`, `reviewer`, `pr`, `run_id`, `run_attempt`, `attempt_head`, `successful_head`, `attempt_status`, `diff_mode`, and `full_diff_sha256`; validated previous body and successful SHA for later tasks.

- [ ] **Step 1: Add failing canonical-selection fixtures**

Add helpers that build the exact prefix and state line, then add tests for a valid v2 comment, a human marker quote, a foreign bot inline quote, a foreign reviewer envelope, a mismatched PR, malformed JSON, and two valid records whose `run_id` order differs from comment order.

```python
CLAUDE_HEADER = "## Claude Code Review (latest)"
CLAUDE_V2_MARKER = "<!-- automation:claude-code-review:v2 -->"

def _state_line(reviewer: str, pr: int, run_id: int, head: str, run_attempt: int = 1) -> str:
    state = {
        "schema": 2,
        "reviewer": reviewer,
        "pr": pr,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "attempt_head": head,
        "successful_head": head,
        "attempt_status": "success",
        "diff_mode": "full",
        "full_diff_sha256": "12" * 32,
    }
    return f"<!-- automation-state:{json.dumps(state, separators=(',', ':'))} -->"

def _v2_body(header: str, marker: str, state: str, body: str = "REAL REVIEW") -> str:
    return f"{header}\n{marker}\n{state}\n\n{body}"
```

- [ ] **Step 2: Run the focused tests and verify legacy selection fails**

Run: `python3 -m pytest tests/test_review_workflow_logic.py -k 'canonical or foreign_bot or run_id' -q`

Expected: new tests fail because collection still uses `Bot && contains(marker)` and legacy marker text.

- [ ] **Step 3: Implement strict Claude collection parsing**

Replace the broad jq selector with a jq program that requires exact first lines, parses the state JSON with `fromjson?`, validates all fields, and selects the largest lexicographic `(run_id, run_attempt)` generation. Export the validated state separately from sanitized review prose.

```jq
def parsed_state($header; $marker; $reviewer; $pr):
  (.body // "") as $body
  | ($body | split("\n")) as $lines
  | select(.user.type == "Bot")
  | select($lines[0] == $header and $lines[1] == $marker)
  | ($lines[2] | capture("^<!-- automation-state:(?<json>\\{.*\\}) -->$").json? | fromjson?) as $s
  | select($s.schema == 2 and $s.reviewer == $reviewer and $s.pr == ($pr | tonumber))
  | select(($s.run_id | type) == "number" and $s.run_id > 0)
  | select(($s.run_attempt | type) == "number" and $s.run_attempt > 0)
  | select(($s.attempt_head | test("^[0-9a-f]{40}$")))
  | {comment: ., state: $s};

[.[] | parsed_state($header; $marker; $reviewer; $pr)]
| sort_by(.state.run_id, .state.run_attempt)
| last // null
```

If parsing yields no candidate, leave previous context and previous SHA empty. Strip every reserved line from the body before writing `claude-review-context.md`.

- [ ] **Step 4: Run the complete collection tests**

Run: `python3 -m pytest tests/test_review_workflow_logic.py -k 'collect' -q`

Expected: all Claude collection tests pass after legacy expectations are updated to v2 fixtures.

- [ ] **Step 5: Commit the parser contract**

```bash
git add tests/test_review_workflow_logic.py .github/workflows/claude-code-review.yml
git commit -m "fix(review): require canonical Claude review state"
```

### Task 2: Make Claude success and failure transitions truthful

**Files:**
- Modify: `tests/test_review_workflow_logic.py:390-650`
- Modify: `.github/workflows/claude-code-review.yml:200-440`

**Interfaces:**
- Consumes: `REVIEW_OUTCOME`, `RUN_ID`, `RUN_URL`, `DIFF_READY`, captured head file, model output, and validated existing v2 state.
- Produces: one canonical v2 sticky comment or a no-op for stale runs.

- [ ] **Step 1: Add failing transition tests**

Cover these exact cases in the Node harness:

```python
@pytest.mark.parametrize(
    ("outcome", "diff_ready", "review", "expected_status"),
    [
        ("success", "false", "diff unavailable", "failure"),
        ("success", "true", "", "failure"),
        ("success", "true", "<!-- automation:x -->", "failure"),
        ("success", "true", "REAL FINDING", "success"),
    ],
)
def test_claude_checkpoint_requires_coverage_and_sanitized_body(...):
    ...
```

Also assert that infra-only output cannot erase an existing real body, a failure after success produces `Status: stale`, and success emits exactly one v2 state line.

- [ ] **Step 2: Run the tests and verify the two reproduced bugs fail**

Run: `python3 -m pytest tests/test_review_workflow_logic.py -k 'claude and (checkpoint or infra or stale)' -q`

Expected: unavailable-diff text and infra-only output are incorrectly successful before the fix.

- [ ] **Step 3: Emit explicit input readiness from the existing preparation step**

Initialize readiness before the full-diff fetch and set it only after a non-empty authoritative file exists.

```bash
printf 'false' > review_diff_ready.txt
if gh pr diff "$PR_NUM" > claude-review-full.diff 2>/dev/null \
   && [ -s claude-review-full.diff ]; then
  printf 'true' > review_diff_ready.txt
  sha256sum claude-review-full.diff | cut -d' ' -f1 > review_full_diff_sha256.txt
else
  rm -f claude-review-full.diff
  : > review_full_diff_sha256.txt
fi
```

Pass `DIFF_READY`, `RUN_ID`, and the captured attempt head into the upsert step. Remove the prompt instruction that turns missing diff into a normal review body; missing input is a workflow failure path.

- [ ] **Step 4: Sanitize before calculating failure and construct v2 state**

Use this ordering in the GitHub Script:

```javascript
review = review.split('\n').filter((line) => !infraLine.test(line)).join('\n').trim();
const diffReady = process.env.DIFF_READY === 'true';
const failed = !ok || !diffReady || !review;
```

On success, generate the envelope from trusted environment values. On failure with a prior success, preserve the prior body and successful SHA, replace only the envelope/visible attempt metadata, and use `Status: stale`. On failure without prior success, create a canonical failure comment without `Reviewed`.

- [ ] **Step 5: Run Claude workflow tests**

Run: `python3 -m pytest tests/test_review_workflow_logic.py -k 'claude or collect' -q`

Expected: all selected tests pass, including unavailable input and infra-only output.

- [ ] **Step 6: Commit truthful Claude transitions**

```bash
git add tests/test_review_workflow_logic.py .github/workflows/claude-code-review.yml
git commit -m "fix(review): gate Claude checkpoints on reviewed input"
```

### Task 3: Add current-head and generation compare-before-write

**Files:**
- Modify: `tests/test_review_workflow_logic.py:390-650`
- Modify: `.github/workflows/claude-code-review.yml:75-440`

**Interfaces:**
- Consumes: captured attempt head, `github.run_id`, `github.run_attempt`, current PR head from `pulls.get`, and the stored v2 generation tuple.
- Produces: comment update only when `currentHead == attemptHead` and the stored `(run_id, run_attempt)` is strictly older than the current tuple.

- [ ] **Step 1: Extend the Node harness with PR-head API behavior**

```javascript
rest: {
  issues: { /* existing stubs */ },
  pulls: {
    get: async () => ({ data: { head: { sha: fx.currentHead } } }),
  },
}
```

Add tests where the current head differs, where the existing state has a larger run ID, and
where the same run ID has an equal or larger attempt. Assert there are no `createComment`
or `updateComment` calls. Add the positive case where a larger `run_attempt` under the same
run ID is allowed.

- [ ] **Step 2: Run stale-write tests and verify they fail**

Run: `python3 -m pytest tests/test_review_workflow_logic.py -k 'stale_head or newer_run' -q`

Expected: current code mutates the comment.

- [ ] **Step 3: Implement compare-before-write**

Before choosing the success/failure mutation path:

```javascript
const { data: pr } = await github.rest.pulls.get({ owner, repo, pull_number: issueNumber });
if (pr.head?.sha !== attemptHead) {
  core.notice(`Discarding stale review for ${attemptHead}; current head is ${pr.head?.sha || 'unknown'}`);
  return;
}
if (existingState && (existingState.run_id > runId
  || (existingState.run_id === runId && existingState.run_attempt >= runAttempt))) {
  core.notice(`Discarding stale generation (${runId}, ${runAttempt})`);
  return;
}
```

Reject a missing/malformed captured head, run ID, or run attempt rather than posting success.

- [ ] **Step 4: Add per-reviewer/PR job concurrency**

At the `claude-review` job level:

```yaml
concurrency:
  group: automation-claude-review-${{ github.repository }}-${{ inputs.pr_number || github.event.pull_request.number }}
  cancel-in-progress: true
```

Add a static test asserting the group includes the reviewer identifier, repository, and PR number.

- [ ] **Step 5: Run and commit**

Run: `python3 -m pytest tests/test_review_workflow_logic.py -k 'claude or concurrency' -q`

```bash
git add tests/test_review_workflow_logic.py .github/workflows/claude-code-review.yml
git commit -m "fix(review): reject stale Claude review writes"
```

### Task 4: Apply the same state machine to Gemini

**Files:**
- Modify: `tests/test_review_workflow_logic.py:300-900`
- Modify: `.github/workflows/gemini-auto-review.yml:100-580`

**Interfaces:**
- Consumes: the Task 1 envelope schema and Task 3 compare-before-write rules.
- Produces: Gemini v2 state with identical field meanings, including `(run_id, run_attempt)` ordering; Gemini-specific header, marker, model, and `-U20` diff mode remain unchanged.

- [ ] **Step 1: Add Gemini parity tests**

Parameterize the canonical-state and transition assertions across Claude and Gemini wherever the workflow shape permits. Add explicit Gemini tests for foreign bot marker quotation, malformed v2 state, unavailable `pr_diff.txt`, sanitized-empty output, stale head, newer run, higher manual-rerun attempt, equal/lower generation rejection, and an older rate-limited run finishing after a newer success.

- [ ] **Step 2: Verify current Gemini behavior fails the new contract**

Run: `python3 -m pytest tests/test_review_workflow_logic.py -k 'gemini and (canonical or coverage or stale or ordering)' -q`

- [ ] **Step 3: Implement Gemini collection and readiness**

Replace `gh pr diff ... || echo "No diff available" > pr_diff.txt` with an explicit readiness file and SHA-256 file. Parse only the exact Gemini v2 prefix and state schema. Keep the existing 429 retry loop and `-U20` context.

Read and validate the PR head immediately before and after `gh pr diff`. Mark the input
ready and bind `attempt_head` only when both 40-hex values match; otherwise remove the diff,
leave its hash empty, and skip the model. Add executable positive and negative preparation
fixtures for stable, changed, and malformed heads. A `DIFF_LIMIT`-truncated prompt is partial
coverage and must take the failure/stale transition rather than advancing a checkpoint.

```bash
if gh pr diff "$PR_NUMBER" > pr_diff.txt 2>/dev/null && [ -s pr_diff.txt ]; then
  printf 'true' > review_diff_ready.txt
  sha256sum pr_diff.txt | cut -d' ' -f1 > review_full_diff_sha256.txt
else
  rm -f pr_diff.txt
  printf 'false' > review_diff_ready.txt
  : > review_full_diff_sha256.txt
fi
```

- [ ] **Step 4: Implement Gemini sanitize/gate/CAS/upsert parity**

Calculate failure only after `gemini_review.md` sanitization, generate the same state fields, preserve an existing successful body as stale on failure, and perform the current-head/run-order checks before any mutation.

- [ ] **Step 5: Add Gemini job concurrency**

```yaml
concurrency:
  group: automation-gemini-auto-review-${{ github.repository }}-${{ inputs.pr_number || github.event.pull_request.number }}
  cancel-in-progress: true
```

- [ ] **Step 6: Run and commit**

Run: `python3 -m pytest tests/test_review_workflow_logic.py -k 'gemini or concurrency' -q`

```bash
git add tests/test_review_workflow_logic.py .github/workflows/gemini-auto-review.yml
git commit -m "fix(review): make Gemini state head-bound and monotonic"
```

### Task 5: Canonicalize OpenCode output and previous context

**Files:**
- Modify: `tests/test_review_workflow_logic.py:900-1040`
- Modify: `.github/workflows/opencode-auto-review.yml:75-250`

**Interfaces:**
- Consumes: before/after issue-comment snapshots, current run ID and attempt, captured head, an explicitly numbered full PR diff, and OpenCode marker-bearing output.
- Produces: exactly one canonical OpenCode v2 comment; previous context only from validated OpenCode v2 state.

- [ ] **Step 1: Add failing OpenCode provenance and candidate tests**

Add cases for a genuine canonical state followed by a newer Claude/Gemini bot quote, a legacy OpenCode marker, a model preamble before the marker, zero current-run candidates, and two current-run candidates. The collection test must select the canonical OpenCode record by `(run_id, run_attempt)`; the canonicalization test must fail closed unless exactly one comment changed during the run.

- [ ] **Step 2: Run the focused tests**

Run: `python3 -m pytest tests/test_review_workflow_logic.py -k 'opencode and (canonical or candidate or foreign)' -q`

Expected: foreign bot quote still displaces the genuine review before implementation.

- [ ] **Step 3: Snapshot comments and prepare the attempt input**

During context collection, save the fetched comment IDs and update timestamps to
`opencode-comments-before.json`. Fetch and validate the PR head before and after preparing
`opencode-review-full.diff` with an explicitly numbered `gh pr diff`; write
`opencode-attempt-head.txt`, readiness, and SHA-256 only when the two heads match. Skip the
CLI and leave the attempt identity untrusted if either head is malformed or changed.
Previous context uses only a validated v2 envelope. The second plan replaces this temporary
inline preparation with the shared action.

- [ ] **Step 4: Add post-run canonicalization**

After `opencode github run`, refetch comments and select bot comments containing the
OpenCode marker whose ID is new or whose `updated_at` differs from the snapshot. Require
exactly one candidate. Strip any model-generated reserved lines, prepend the canonical
header/marker/state, and update that candidate through the issue-comments API.

```javascript
const candidates = after.filter((comment) => {
  const beforeComment = beforeById.get(comment.id);
  const changed = !beforeComment || beforeComment.updated_at !== comment.updated_at;
  return changed && comment.user?.type === 'Bot' && (comment.body || '').includes(legacyMarker);
});
if (candidates.length !== 1) throw new Error(`expected one OpenCode output, got ${candidates.length}`);
```

Before updating, apply the same current-head and run-order checks as Tasks 3–4.

- [ ] **Step 5: Add OpenCode job concurrency**

```yaml
concurrency:
  group: automation-opencode-auto-review-${{ github.repository }}-${{ inputs.pr_number || github.event.pull_request.number || github.event.issue.number }}
  cancel-in-progress: true
```

- [ ] **Step 6: Update auto-rereview marker extraction for v1 and v2**

Modify `.github/workflows/auto-rereview-request.yml` only if its existing marker regex does
not recognize `:v2`. Keep reviewer notification informational; do not make it a state source.
Add an executable test covering both marker generations.

- [ ] **Step 7: Run and commit**

Run: `python3 -m pytest tests/test_review_workflow_logic.py -k 'opencode or rereview' -q`

```bash
git add tests/test_review_workflow_logic.py .github/workflows/opencode-auto-review.yml .github/workflows/auto-rereview-request.yml
git commit -m "fix(review): canonicalize OpenCode review provenance"
```

### Task 6: Slice 1 verification and documentation

**Files:**
- Modify: `docs/workflows/contracts.md`
- Test: `tests/test_review_workflow_logic.py`

**Interfaces:**
- Consumes: completed state implementation from Tasks 1–5.
- Produces: documented v2 state semantics and a verified, independently reviewable Slice 1 commit set.

- [ ] **Step 1: Document state semantics and migration**

Add a concise section describing exact-prefix selection, the v2 fields, `Status: stale`,
legacy full-review migration, and current-head/run-order write gates. Explicitly state that
free-form comment bodies are untrusted presentation data.

- [ ] **Step 2: Run targeted workflow logic tests**

Run: `python3 -m pytest tests/test_review_workflow_logic.py -q`

Expected: all tests pass.

- [ ] **Step 3: Run the full repository suite**

Run: `python3 -m pytest tests/ -q`

Expected: all tests and subtests pass.

- [ ] **Step 4: Run static integrity checks**

```bash
git diff --check HEAD~5..HEAD
python3 -m compileall -q scripts tests
```

If `actionlint` is installed, run `actionlint`; otherwise record that it was unavailable and rely on YAML loading plus workflow-contract tests.

- [ ] **Step 5: Commit documentation**

```bash
git add docs/workflows/contracts.md
git commit -m "docs(review): document canonical review state"
```
