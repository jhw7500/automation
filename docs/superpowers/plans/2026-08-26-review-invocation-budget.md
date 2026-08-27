# Review Invocation Budget and Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a durable pre-provider budget claim and post-review checkpoint to all three automatic reviewers so duplicate inputs use zero model calls, automatic review stops after two distinct diffs, and a new session can resume from one authenticated handoff.

**Architecture:** One release-owned composite action wraps a standard-library Python state machine and GitHub API compare-and-swap transport. Claude, Gemini, and OpenCode keep their existing review-state schemas and semantic-review paths; each workflow calls the budget action before its provider, records bounded provider metrics, finalizes after canonical publication, and uploads the resulting checkpoint.

**Tech Stack:** Python 3.12 standard library, GitHub Actions composite YAML, `gh api`, Bash, `jq`, pytest, PyYAML `BaseLoader`, actionlint 1.7.12

**Spec:** `docs/superpowers/specs/2026-08-26-review-invocation-budget-design.md`

## Global Constraints

- Keep Claude/Gemini review state at exact schema 3 and OpenCode review state/attestation at exact schema 2; the budget ledger cannot publish findings, report CLEAN, or authorize merge.
- Use exact ledger markers `<!-- automation:review-invocation-budget:claude:v1 -->`, `<!-- automation:review-invocation-budget:gemini:v1 -->`, and `<!-- automation:review-invocation-budget:opencode:v1 -->`, followed immediately by the compact JSON returned by `serialize_ledger` inside the `<!-- automation-budget-state:` / ` -->` delimiters.
- Accept a ledger comment only when its author login is exactly `github-actions[bot]`, its marker begins at byte zero, and exactly one comment matches the reviewer marker.
- Use SHA-256 of the immutable `review-full.diff` as the effective-diff identity, even when a provider consumes a delta.
- Same reviewer plus head SHA and same reviewer plus effective-diff hash are absolute zero-call gates and cannot be overridden.
- Allow exactly two automatic distinct-diff rounds; one `review-budget-override` label timeline-event ID may authorize exactly one additional round per PR/reviewer and must be consumed once.
- Fix estimated input to `ceil(sum(input_file_bytes) / 4) + 20_000`, with 200,000 tokens per round, 400,000 across automatic rounds, and 600,000 only after the one override.
- Fix provider wall time to 600 seconds per round and call caps to Claude 1 action session, Gemini 3 `generate_content` requests, and OpenCode 2 `opencode run` sessions.
- Persist a claim before provider execution. A cancelled, timed-out, provider-failed, quality-filtered, or unfinalized claim remains consumed.
- Validate historical run attempts against Actions API provenance; older claims require terminal runs, while the current run may be in progress.
- Refetch both PR head and prior comment immediately before mutation. Any mismatch records `state_invalid` with stop reason `compare_and_swap_failed`, performs no mutation, and returns `allow-invocation=false`.
- Deterministic diff preparation, classification, polling, ledger rendering, and handoff rendering use zero model calls.
- Ledger schema 1 has exactly `schema`, `repository`, `pr`, `reviewer`, `budgets`, `invocations`, `consumed_override_event_ids`, `last_decision`, and `handoff`; aggregate usage is derived from `invocations` and rejected when inconsistent.
- Every invocation records run ID/attempt, head/full-diff hash, round, override event, model route, effort, call unit/count, estimated input, elapsed seconds, status, outcome, stop reason, and at most eight active `RVW-<12hex>` IDs.
- Every handoff records repository/PR/reviewer, current head/hash/run, automatic and override rounds consumed, per-round calls/input/wall usage, current decision/outcome/stop reason, last authenticated successful review head/hash, and remaining authenticated finding IDs.
- Runtime Python uses only the standard library. Test-only dependencies remain pytest and PyYAML.
- Every new shell command added to repository documentation or workflows is prefixed with `rtk` only when it is an operator command; commands embedded in GitHub Actions continue using the runner's ordinary tools.
- Preserve `.omc/`, `.serena/`, `HANDOFF.md`, and unrelated changes in the original checkout.
- Do not create a release tag, advance `scripts/workflow-config.json`, or roll out consumer repositories in issue #52.

---

## File Map

- `.github/actions/review-invocation-budget/review_invocation_budget.py`: strict ledger schema, pure claim/finalize transitions, provenance checks, deterministic comments/checkpoints, and the file-based CLI used by the composite wrapper.
- `.github/actions/review-invocation-budget/action.yml`: exact action interface, bounded `gh api` reads, run-provenance collection, head/comment compare-and-swap, ledger mutation, outputs, and job summary.
- `tests/fixtures/review-invocation-budget/cases.json`: fixed same/new SHA, exhausted round, override, false-positive, provider-failure, and checkpoint-resume state vectors.
- `tests/test_review_invocation_budget.py`: pure parser, transition, budget, provenance, handoff, rendering, and CLI tests.
- `tests/test_review_invocation_budget_action.py`: composite metadata, inert argv/data transport, mocked GitHub API, compare-and-swap, and no-provider-on-refusal tests.
- `.github/workflows/claude-code-review.yml`: ten-minute job cap, claim before checkout/model, one-session metric, guarded canonicalization/publication, finalization, and checkpoint artifacts.
- `.github/workflows/gemini-auto-review.yml`: claim before dependency/provider work, durable pre-request counting across primary/retry/fallback, finalization, and checkpoint artifacts.
- `.github/workflows/opencode-auto-review.yml`: prepare-job claim, sealed budget identity, guarded ten-minute model job, two-session counter, canonicalize-job finalization, and checkpoint artifacts.
- `.github/actionlint.yaml`: one narrow local-action syntax exception for the new self action in each reviewer workflow.
- `tests/test_review_workflow_logic.py`: exact order, conditions, timeouts, call counters, outcome mapping, handoff sealing, and artifact assertions for all reviewers.
- `scripts/workflow_release_inventory.py`: `v1.47+` closed inventory for the budget action and helper without changing historical releases.
- `scripts/verify_workflow_release.py`: exact action contract, helper/state-machine invariants, reviewer wiring, and mutation-resistant budget gates.
- `tests/test_workflow_release_bundle.py`: exact action contract, safe quoted bridge, modes, and release-bundle membership.
- `tests/test_verify_workflow_release.py`: `v1.46.x` historical compatibility plus `v1.47` inventory and workflow/helper mutation gates.
- `docs/workflows/contracts.md`: consumer-visible invocation, refusal, override, failure, artifact, and handoff contract.

---

### Task 1: Build the strict ledger model and claim state machine

**Files:**
- Create: `.github/actions/review-invocation-budget/review_invocation_budget.py`
- Create: `tests/test_review_invocation_budget.py`
- Create: `tests/fixtures/review-invocation-budget/cases.json`

**Interfaces:**
- Produces: `Reviewer = Literal["claude", "gemini", "opencode"]`, `Outcome = Literal["success", "provider_failure", "quality_filtered", "checkpoint_failure", "wall_time_exhausted"]`, and `Decision = Literal["claimed", "finalized", "state_invalid", "diff_unavailable", "authenticated_reuse", "duplicate_head", "duplicate_effective_diff", "input_budget_exhausted", "round_budget_exhausted", "total_usage_budget_exhausted"]`.
- Produces: immutable records `BudgetPolicy`, `AuthenticatedReview`, `OverrideEvent`, `RunProvenance`, `Invocation`, `DecisionRecord`, `Handoff`, `LedgerState`, `ClaimRequest`, `FinalizeRequest`, and `Transition`.
- Produces: `BudgetPolicy.for_reviewer(reviewer: Reviewer) -> BudgetPolicy` with fixed calls `1/3/2` and fixed round/input/wall limits.
- Produces: `parse_ledger(body: str | None, *, repository: str, pr: int, reviewer: Reviewer) -> LedgerState | None` and `serialize_ledger(state: LedgerState) -> str`.
- Produces: `estimate_input_tokens(paths: Sequence[Path]) -> int` and `claim(state: LedgerState | None, request: ClaimRequest, provenances: Mapping[tuple[int, int], RunProvenance]) -> Transition`.
- `Transition` fields are exactly `state: LedgerState`, `allow_invocation: bool`, `decision: str`, `stop_reason: str`, `round_number: int | None`, `invocation_key: str | None`, and `mutate_comment: bool`.

- [ ] **Step 1: Add fixed RED vectors for identity, duplicates, rounds, and override consumption**

Write `cases.json` as a JSON array whose records use exact 40-hex heads and 64-hex full hashes. Include these named vectors with their expected decisions:

```json
[
  {"name":"first-claim","prior":"empty","head":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","full_hash":"1111111111111111111111111111111111111111111111111111111111111111","expected":"claimed","allow":true},
  {"name":"same-head-success","prior":"one-success","head":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","full_hash":"1111111111111111111111111111111111111111111111111111111111111111","expected":"duplicate_head","allow":false},
  {"name":"same-head-provider-failure","prior":"one-provider-failure","head":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","full_hash":"1111111111111111111111111111111111111111111111111111111111111111","expected":"duplicate_head","allow":false},
  {"name":"same-head-unfinalized","prior":"one-claimed","head":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","full_hash":"1111111111111111111111111111111111111111111111111111111111111111","expected":"duplicate_head","allow":false},
  {"name":"new-head-same-diff","prior":"one-success","head":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","full_hash":"1111111111111111111111111111111111111111111111111111111111111111","expected":"duplicate_effective_diff","allow":false},
  {"name":"third-diff-no-override","prior":"two-successes","head":"cccccccccccccccccccccccccccccccccccccccc","full_hash":"3333333333333333333333333333333333333333333333333333333333333333","expected":"round_budget_exhausted","allow":false},
  {"name":"third-diff-one-override","prior":"two-successes","head":"cccccccccccccccccccccccccccccccccccccccc","full_hash":"3333333333333333333333333333333333333333333333333333333333333333","override_event":9001,"expected":"claimed","allow":true}
]
```

In `tests/test_review_invocation_budget.py`, load the helper with `importlib.util.spec_from_file_location`, build exact prior ledgers from the fixture's `prior` name, and assert decision, call allowance, round count, and consumed override event IDs.

- [ ] **Step 2: Run the focused tests and confirm the missing-helper failure**

Run: `rtk python3 -m pytest tests/test_review_invocation_budget.py -q`

Expected: collection fails because `.github/actions/review-invocation-budget/review_invocation_budget.py` does not exist.

- [ ] **Step 3: Implement fixed records, exact schema validation, and deterministic serialization**

Use these public record shapes and constants:

```python
SCHEMA = 1
STATE_PREFIX = "<!-- automation-budget-state:"
STATE_SUFFIX = " -->"
MARKERS = {
    "claude": "<!-- automation:review-invocation-budget:claude:v1 -->",
    "gemini": "<!-- automation:review-invocation-budget:gemini:v1 -->",
    "opencode": "<!-- automation:review-invocation-budget:opencode:v1 -->",
}
WORKFLOWS = {
    "claude": ".github/workflows/claude-code-review.yml",
    "gemini": ".github/workflows/gemini-auto-review.yml",
    "opencode": ".github/workflows/opencode-auto-review.yml",
}

@dataclass(frozen=True)
class BudgetPolicy:
    max_rounds: int = 2
    max_override_rounds: int = 1
    max_calls_per_round: int = 1
    max_wall_seconds_per_round: int = 600
    max_estimated_tokens_per_round: int = 200_000
    max_estimated_tokens_total: int = 400_000

    @classmethod
    def for_reviewer(cls, reviewer: Reviewer) -> "BudgetPolicy":
        return cls(max_calls_per_round={"claude": 1, "gemini": 3, "opencode": 2}[reviewer])

@dataclass(frozen=True)
class Transition:
    state: LedgerState
    allow_invocation: bool
    decision: str
    stop_reason: str
    round_number: int | None
    invocation_key: str | None
    mutate_comment: bool
```

Reject booleans where integers are required, unknown/missing keys, non-canonical lowercase hashes, duplicate run identities, duplicate heads/hashes, more than three invocations, inconsistent round numbers, mismatched reviewer budgets, impossible override positions, duplicate consumed event IDs, unknown outcomes/statuses, or finding IDs outside `RVW-[0-9a-f]{12}`. Render with `json.dumps(state.to_dict(), ensure_ascii=True, separators=(",", ":"), sort_keys=True)`.

- [ ] **Step 4: Run parser tests, confirm the claim vectors still fail, then implement claim order**

Run: `rtk python3 -m pytest tests/test_review_invocation_budget.py -q`

Expected: strict parser/serializer cases pass; vector cases fail because `claim` is absent.

Implement the exact decision order in one function:

```python
def estimate_input_tokens(paths: Sequence[Path]) -> int:
    total = 0
    for path in paths:
        stat_result = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(stat_result.st_mode):
            raise BudgetStateError("input_not_regular")
        total += stat_result.st_size
    return math.ceil(total / 4) + 20_000

def claim(state, request, provenances):
    validated = validate_or_initialize(state, request, provenances)
    if request.diff_mode == "unchanged" and request.authenticated_review.covers_hash(request.full_diff_sha256):
        return refuse(validated, request, "authenticated_reuse")
    if any(item.head_sha == request.head_sha for item in validated.invocations):
        return refuse(validated, request, "duplicate_head")
    if any(item.full_diff_sha256 == request.full_diff_sha256 for item in validated.invocations):
        return refuse(validated, request, "duplicate_effective_diff")
    if request.estimated_input_tokens > validated.budgets.max_estimated_tokens_per_round:
        return refuse(validated, request, "input_budget_exhausted")
    override = None
    if automatic_rounds(validated) >= validated.budgets.max_rounds:
        override = choose_override(validated, request.override_events)
        if override is None:
            return refuse(validated, request, "round_budget_exhausted")
    total_limit = 600_000 if override is not None else 400_000
    if estimated_total(validated) + request.estimated_input_tokens > total_limit:
        return refuse(validated, request, "total_usage_budget_exhausted")
    return append_claim(validated, request, override)
```

`choose_override` accepts only the latest unconsumed event whose event is `labeled`, label is exactly `review-budget-override`, actor permission is `admin`, `maintain`, or `write`, and whose positive integer ID has not been consumed. It returns no event after one override invocation already exists.

- [ ] **Step 5: Add and pass fail-closed provenance and authenticated-reuse tests**

Add tests proving current in-progress provenance is accepted, historical `completed` provenance is accepted even with `cancelled`/`timed_out` conclusion, historical in-progress/mismatched repository/PR/head/workflow/run-attempt is rejected, and unchanged reuse requires an authenticated successful state with the exact full hash. The authenticated successful head may be the current head or an earlier head whose immutable full diff is identical:

```python
@pytest.mark.parametrize("field", ["repository", "pr", "head_sha", "workflow_path", "run_attempt"])
def test_claim_fails_closed_when_historical_provenance_mismatches(field, two_round_state, claim_request):
    provenances = valid_provenances(two_round_state)
    provenances[(501, 1)] = replace(provenances[(501, 1)], **{field: mismatched(field)})
    result = claim(two_round_state, claim_request, provenances)
    assert not result.allow_invocation
    assert result.decision == "state_invalid"
    assert result.stop_reason == "provenance_mismatch"

def test_unchanged_requires_authenticated_exact_coverage(empty_state, unchanged_request):
    refused = claim(empty_state, unchanged_request, {})
    assert refused.decision == "state_invalid"
    assert refused.stop_reason == "unchanged_without_authenticated_review"

def test_new_head_with_authenticated_same_hash_reuses_without_a_call(empty_state, unchanged_request):
    request = replace(
        unchanged_request,
        head_sha="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        authenticated_review=authenticated_success(
            head="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            full_hash=unchanged_request.full_diff_sha256,
        ),
    )
    result = claim(empty_state, request, {})
    assert result.decision == "authenticated_reuse"
    assert not result.allow_invocation
```

Run: `rtk python3 -m pytest tests/test_review_invocation_budget.py -q`

Expected: all Task 1 tests pass.

- [ ] **Step 6: Commit the pure claim state machine**

```bash
rtk git add .github/actions/review-invocation-budget/review_invocation_budget.py tests/test_review_invocation_budget.py tests/fixtures/review-invocation-budget/cases.json
rtk git commit -m "feat(review): add invocation claim state machine"
```

---

### Task 2: Finalize invocations and render checkpoint-only handoffs

**Files:**
- Modify: `.github/actions/review-invocation-budget/review_invocation_budget.py`
- Modify: `tests/test_review_invocation_budget.py`
- Modify: `tests/fixtures/review-invocation-budget/cases.json`

**Interfaces:**
- Consumes: `LedgerState`, `FinalizeRequest`, and `Transition` from Task 1.
- Produces: `finalize(state: LedgerState, request: FinalizeRequest, provenances: Mapping[tuple[int, int], RunProvenance]) -> Transition`.
- Produces: `render_comment(state: LedgerState, *, server_url: str) -> str`, `render_checkpoint(state: LedgerState) -> bytes`, `load_checkpoint(payload: bytes) -> LedgerState`, and `render_summary(state: LedgerState) -> str`.
- `FinalizeRequest` fields are exact repository/PR/reviewer/run identity, head/hash, actual model route tuple, effort, call count, elapsed seconds, outcome, stop reason, authenticated review, and remaining finding-ID tuple.

- [ ] **Step 1: Add RED tests for final outcomes and hard call/wall limits**

Add fixed fixture vectors for Claude call count 2, Gemini call count 3/4, OpenCode count 2/3, elapsed 600/601, `quality_filtered`, `provider_failure`, and remaining IDs. Assert that exceeding a cap changes outcome to `checkpoint_failure` for calls or `wall_time_exhausted` for wall time and sets a non-empty stop reason.

```python
def test_gemini_primary_retry_and_fallback_share_one_three_request_cap(claimed_gemini):
    allowed = finalize(claimed_gemini, finalize_request(calls=3), current_provenance())
    refused = finalize(claimed_gemini, finalize_request(calls=4), current_provenance())
    assert allowed.state.invocations[-1].outcome == "success"
    assert refused.state.invocations[-1].outcome == "checkpoint_failure"
    assert refused.state.invocations[-1].stop_reason == "call_budget_exhausted"

def test_quality_filtered_is_terminal_and_duplicate_input_stays_blocked(claimed_claude):
    done = finalize(claimed_claude, finalize_request(outcome="quality_filtered"), current_provenance())
    again = claim(done.state, next_request_same_input(), completed_provenance(done.state))
    assert again.decision == "duplicate_head"
    assert not again.allow_invocation
```

- [ ] **Step 2: Run the new finalization cases and confirm failure**

Run: `rtk python3 -m pytest tests/test_review_invocation_budget.py -q`

Expected: finalization, hard-cap, and checkpoint tests fail because the functions are absent.

- [ ] **Step 3: Implement exact finalization matching and outcome normalization**

Match exactly one `claimed` entry by `(run_id, run_attempt, head_sha, full_diff_sha256)`. Reject a second finalization, identity drift, negative/boolean metrics, unknown model route, more than eight remaining IDs, duplicate IDs, or absent current provenance. Preserve previously stored remaining IDs on provider failure when no newer authenticated list is available.

```python
def finalize(state: LedgerState, request: FinalizeRequest, provenances):
    validated = validate_existing(state, request, provenances)
    index = matching_claim_index(validated, request)
    entry = validated.invocations[index]
    outcome = request.outcome
    stop_reason = request.stop_reason
    if request.call_count > validated.budgets.max_calls_per_round:
        outcome, stop_reason = "checkpoint_failure", "call_budget_exhausted"
    elif request.elapsed_seconds > validated.budgets.max_wall_seconds_per_round:
        outcome, stop_reason = "wall_time_exhausted", "wall_time_exhausted"
    completed = replace(
        entry,
        status="finalized",
        outcome=outcome,
        stop_reason=stop_reason,
        call_count=request.call_count,
        elapsed_seconds=request.elapsed_seconds,
        model_route=request.model_route,
        effort=request.effort,
        remaining_finding_ids=bounded_remaining_ids(validated, request),
    )
    return finalized_transition(validated, index, completed, request)
```

- [ ] **Step 4: Implement bounded handoff, exact comment body, summary, and checkpoint round-trip**

The comment begins with the exact marker and compact state lines returned by this expression, then renders only workflow-owned prose:

```python
state_lines = (
    f"{MARKERS[state.reviewer]}\n"
    f"{STATE_PREFIX}{serialize_ledger(state)}{STATE_SUFFIX}"
)
```

The visible suffix has this exact shape for a duplicate Claude run:

```text
## Claude review invocation budget
- Decision: duplicate_head
- Automatic rounds: 1/2
- Override rounds: 0/1
- Current run: https://github.com/example/repo/actions/runs/700
- Stop reason: duplicate_head

Budget exhaustion is not review approval. Use the authenticated review checkpoint and remaining finding IDs before merge.
```

`render_checkpoint` writes the compact, sorted encoding of `{"schema": 1, "ledger": state.to_dict(), "handoff": state.handoff.to_dict()}` plus one trailing newline. `load_checkpoint` must reproduce the same `LedgerState` without Actions logs, comments, or conversation context.

- [ ] **Step 5: Prove provider-failure and next-session behavior from checkpoint alone**

Add a round trip that serializes a provider-failed first round, loads only the checkpoint bytes, then tests same-head refusal and a distinct-head second-round claim:

```python
def test_checkpoint_alone_reconstructs_next_session(provider_failed_state, tmp_path):
    checkpoint = tmp_path / "budget.json"
    checkpoint.write_bytes(render_checkpoint(provider_failed_state))
    restored = load_checkpoint(checkpoint.read_bytes())
    assert claim(restored, same_input_request(), completed_provenance(restored)).decision == "duplicate_head"
    assert claim(restored, second_diff_request(), completed_provenance(restored)).allow_invocation
```

Run: `rtk python3 -m pytest tests/test_review_invocation_budget.py -q`

Expected: all pure state, false-positive, provider-failure, cap, handoff, and checkpoint-resume tests pass.

- [ ] **Step 6: Commit finalization and handoff rendering**

```bash
rtk git add .github/actions/review-invocation-budget/review_invocation_budget.py tests/test_review_invocation_budget.py tests/fixtures/review-invocation-budget/cases.json
rtk git commit -m "feat(review): finalize budget handoff checkpoints"
```

---

### Task 3: Wrap the state machine in a fail-closed composite action

**Files:**
- Create: `.github/actions/review-invocation-budget/action.yml`
- Create: `tests/test_review_invocation_budget_action.py`
- Modify: `.github/actions/review-invocation-budget/review_invocation_budget.py`

**Interfaces:**
- Consumes: Task 1/2 parser, transitions, comment/checkpoint renderers.
- Produces action inputs: required `github-token`, `mode`, `reviewer`, `pr-number`, `expected-head-sha`, `full-diff-sha256`, `diff-mode`, `input-files-json`, `authenticated-review-json`, `model-route-json`, `effort`, and `checkpoint-file`; optional `actual-call-count` default `0`, `elapsed-seconds` default `0`, `outcome` default `checkpoint_failure`, `stop-reason` default empty, and `remaining-finding-ids-json` default `[]`.
- Produces action outputs: `allow-invocation`, `decision`, `round`, `invocation-key`, `checkpoint-sha256`, and `comment-id` from step `budget`.
- Produces CLI operations `list-run-identities`, `claim`, `finalize`, and `cas-failed`; every operation accepts API payloads by file path and writes outputs only to explicit files.

- [ ] **Step 1: Add RED metadata and inert-data tests**

Parse `action.yml` with `yaml.BaseLoader` and assert the exact input/output names above, `runs.using == "composite"`, one shell step with `GH_TOKEN` passed through `env`, and no `${{ inputs.* }}` interpolation in the `run` text. Execute the extracted shell against a fake `gh` executable and hostile JSON strings containing spaces, quotes, command substitutions, newlines, and leading hyphens; assert no marker file is created and each value arrives only in an API response file or one quoted Python argv entry.

- [ ] **Step 2: Add RED API/CAS scenarios**

The fake `gh` records endpoints and returns deterministic JSON for:

```python
@pytest.mark.parametrize("scenario,expected", [
    ("first-comment-create", "claimed"),
    ("trusted-comment-update", "claimed"),
    ("duplicate-trusted-comments", "state_invalid"),
    ("foreign-author-marker", "state_invalid"),
    ("historical-run-head-mismatch", "state_invalid"),
    ("pr-head-changed-before-patch", "state_invalid"),
    ("comment-body-changed-before-patch", "state_invalid"),
])
def test_claim_transport_is_fail_closed(fake_github, scenario, expected):
    result = fake_github.run_action(mode="claim", scenario=scenario)
    assert result.outputs["decision"] == expected
    assert result.provider_started is False
```

For successful claim cases, additionally assert the comment mutation happens once before the test's simulated provider sentinel may run. For CAS failures, assert no POST/PATCH mutation occurs and the checkpoint stop reason is `compare_and_swap_failed`.

- [ ] **Step 3: Run action tests and confirm the missing action failure**

Run: `rtk python3 -m pytest tests/test_review_invocation_budget_action.py -q`

Expected: tests fail because `action.yml` and CLI transport operations do not exist.

- [ ] **Step 4: Implement the exact composite transport**

The shell step must:

1. create a mode-0700 temporary directory under `$RUNNER_TEMP`;
2. fetch PR head, issue comments, and timeline into mode-0600 files;
3. ask `list-run-identities` for at most three prior `(run_id, run_attempt)` pairs;
4. fetch each exact run attempt plus collaborator permission for eligible override actors;
5. execute `claim` or `finalize` into proposed-state/comment/output files;
6. refetch PR head and either the exact prior comment body or the zero-match comment set;
7. on mismatch, run `cas-failed`, skip mutation, and force `allow-invocation=false`;
8. otherwise create or patch exactly one ledger comment;
9. append `render_summary` output to `$GITHUB_STEP_SUMMARY`; and
10. write output keys to `$GITHUB_OUTPUT` after the mutation succeeds.

Use quoted environment-to-argv transport:

```yaml
runs:
  using: composite
  steps:
    - id: budget
      shell: bash
      env:
        GH_TOKEN: ${{ inputs.github-token }}
        BUDGET_MODE: ${{ inputs.mode }}
        REVIEWER: ${{ inputs.reviewer }}
        PR_NUMBER: ${{ inputs.pr-number }}
        EXPECTED_HEAD_SHA: ${{ inputs.expected-head-sha }}
        FULL_DIFF_SHA256: ${{ inputs.full-diff-sha256 }}
        DIFF_MODE: ${{ inputs.diff-mode }}
        INPUT_FILES_JSON: ${{ inputs.input-files-json }}
        AUTHENTICATED_REVIEW_JSON: ${{ inputs.authenticated-review-json }}
        MODEL_ROUTE_JSON: ${{ inputs.model-route-json }}
        EFFORT: ${{ inputs.effort }}
        ACTUAL_CALL_COUNT: ${{ inputs.actual-call-count }}
        ELAPSED_SECONDS: ${{ inputs.elapsed-seconds }}
        REVIEW_OUTCOME: ${{ inputs.outcome }}
        STOP_REASON: ${{ inputs.stop-reason }}
        REMAINING_FINDING_IDS_JSON: ${{ inputs.remaining-finding-ids-json }}
        CHECKPOINT_FILE: ${{ inputs.checkpoint-file }}
      run: |
        set -euo pipefail
        umask 077
        budget_dir="$(mktemp -d "$RUNNER_TEMP/review-budget.XXXXXX")"
        trap 'rm -rf -- "$budget_dir"' EXIT
        gh api "repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}" > "$budget_dir/pr.json"
        gh api --paginate "repos/${GITHUB_REPOSITORY}/issues/${PR_NUMBER}/comments?per_page=100" > "$budget_dir/comments.json"
        gh api --paginate -H 'Accept: application/vnd.github+json' "repos/${GITHUB_REPOSITORY}/issues/${PR_NUMBER}/timeline?per_page=100" > "$budget_dir/timeline.json"
```

No API payload or PR-controlled value may be sourced, evaluated, used as an option, or interpolated into executable shell text. Numeric IDs must pass a decimal regex before entering an endpoint string.

- [ ] **Step 5: Implement file-based CLI validation and mutation outputs**

Use `argparse` with `allow_abbrev=False`; open every JSON/input file directly; validate repository identity from `$GITHUB_REPOSITORY`; require input paths to resolve below `$GITHUB_WORKSPACE`, be regular non-symlink files, and exist only for a claim that could reach the input-budget decision. The CLI writes proposed comment body, checkpoint, summary, and a JSON output record atomically with mode 0600.

```python
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    commands = parser.add_subparsers(dest="operation", required=True)
    for operation in ("list-run-identities", "claim", "finalize", "cas-failed"):
        command = commands.add_parser(operation, allow_abbrev=False)
        command.add_argument("--request-file", type=Path, required=True)
        command.add_argument("--comments-file", type=Path, required=True)
        command.add_argument("--output-directory", type=Path, required=True)
    return parser

def write_private(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.new")
    temporary.write_bytes(payload)
    temporary.chmod(0o600)
    temporary.replace(path)
```

- [ ] **Step 6: Run state/action tests and security scans**

```bash
rtk python3 -m pytest tests/test_review_invocation_budget.py tests/test_review_invocation_budget_action.py -q
rtk python3 -m py_compile .github/actions/review-invocation-budget/review_invocation_budget.py
rtk rg -n '\$\{\{ inputs\.' .github/actions/review-invocation-budget/action.yml
```

Expected: pytest and compile pass; the final scan finds expressions only in the action step's `env` map and action output declarations, never in its `run` body.

- [ ] **Step 7: Commit the composite action**

```bash
rtk git add .github/actions/review-invocation-budget/action.yml .github/actions/review-invocation-budget/review_invocation_budget.py tests/test_review_invocation_budget_action.py
rtk git commit -m "feat(review): add durable invocation budget action"
```

---

### Task 4: Gate and checkpoint Claude reviews

**Files:**
- Modify: `.github/workflows/claude-code-review.yml`
- Modify: `.github/actionlint.yaml`
- Modify: `tests/test_review_workflow_logic.py`

**Interfaces:**
- Consumes action outputs `allow-invocation`, `decision`, `round`, and `checkpoint-sha256` from Task 3.
- Produces one authenticated-review JSON object from the existing schema-3 collector with exact keys `schema` (integer 1), `successful_head` (lowercase 40-hex string or null), `full_diff_sha256` (lowercase 64-hex string or null), and `remaining_finding_ids` (array of unique `RVW-[0-9a-f]{12}` strings).
- Produces `claude-call-count`, `claude-started-at`, and deterministic elapsed seconds for finalization.
- Keeps model route `[configured model]` or `["claude-code-action-default"]` and effort `final-review/default`.

- [ ] **Step 1: Add RED Claude workflow-contract tests**

Assert:

```python
def test_claude_claim_is_durable_before_provider_and_every_model_path_is_guarded():
    workflow = _load("claude-code-review.yml")
    steps = workflow["jobs"]["claude-review"]["steps"]
    names = [step.get("name") for step in steps]
    assert names.index("Claim Claude review budget") < names.index("Run Claude Code Review")
    assert "steps.review-budget-claim.outputs.allow-invocation == 'true'" in _step(steps, "Run Claude Code Review")["if"]
    assert "steps.review-budget-claim.outputs.allow-invocation == 'true'" in _step(steps, "Reset Claude review artifacts")["if"]
    assert workflow["jobs"]["claude-review"]["timeout-minutes"] == "10"

def test_claude_finalizes_after_review_state_upsert_and_uploads_both_checkpoints():
    steps = _load("claude-code-review.yml")["jobs"]["claude-review"]["steps"]
    names = [step.get("name") for step in steps]
    assert names.index("Upsert review comment") < names.index("Finalize Claude review budget")
    assert _step(steps, "Finalize Claude review budget")["if"] == "${{ always() && !cancelled() && steps.review-budget-claim.outputs.allow-invocation == 'true' }}"
```

Also require the authenticated unchanged path to use zero model calls, a duplicate failed/unfinalized claim not to enter the review-state success branch, and artifact names to contain reviewer, claim/final, run ID, and run attempt.

- [ ] **Step 2: Run focused Claude tests and confirm failure**

Run: `rtk python3 -m pytest tests/test_review_workflow_logic.py -q -k 'claude and (budget or timeout or invocation)'`

Expected: new tests fail because the claim/finalize steps and timeout are absent.

- [ ] **Step 3: Emit authenticated prior state and claim before provider work**

Extend the existing authenticated schema-3 collector to output compact `authenticated_review_json`; derive remaining IDs only from its authenticated canonical body. Resolve the configured/default model through `jq --arg` so quotes can never produce malformed JSON, then add `Claim Claude review budget` immediately after `Prepare review diff`; give it an `always()` condition so a failed diff step still records `diff_unavailable`:

```yaml
- name: Resolve Claude budget metadata
  id: claude-budget-config
  shell: bash
  env:
    CONFIGURED_MODEL: ${{ needs.check-enabled.outputs.model }}
  run: |
    set -euo pipefail
    model="${CONFIGURED_MODEL:-claude-code-action-default}"
    printf 'model_route_json=%s\n' "$(jq -cn --arg model "$model" '[$model]')" >> "$GITHUB_OUTPUT"

- name: Claim Claude review budget
  id: review-budget-claim
  if: ${{ always() && steps.prepare-review-input.outcome == 'success' }}
  uses: $/.github/actions/review-invocation-budget
  with:
    github-token: ${{ github.token }}
    mode: claim
    reviewer: claude
    pr-number: ${{ inputs.pr_number || github.event.pull_request.number }}
    expected-head-sha: ${{ steps.prepare-diff.outputs.head-sha }}
    full-diff-sha256: ${{ steps.prepare-diff.outputs.full-diff-sha256 }}
    diff-mode: ${{ steps.prepare-diff.outputs.diff-mode }}
    input-files-json: ${{ steps.prepare-diff.outputs.diff-mode == 'delta' && format('[\"{0}/review-delta.diff\"]', runner.temp) || format('[\"{0}/review-full.diff\"]', runner.temp) }}
    authenticated-review-json: ${{ steps.prepare-review-input.outputs.authenticated_review_json }}
    model-route-json: ${{ steps.claude-budget-config.outputs.model_route_json }}
    effort: final-review/default
    checkpoint-file: ${{ runner.temp }}/claude-review-budget-claim.json
```

If diff preparation fails, pass empty canonical identities and let the action record `diff_unavailable`; do not call the provider.

- [ ] **Step 4: Guard Claude execution and record one action session**

Add `timeout-minutes: 10`. Require `allow-invocation == 'true'` on checkout, reset, provider, and canonicalizer paths that operate on a new diff. Immediately before the provider, write one call and epoch to step outputs; after it, compute non-negative integer elapsed time. The counter step itself also requires allow true, so a wait/rerun with no new diff records zero.

```yaml
- name: Start Claude review metrics
  id: claude-budget-metrics
  if: ${{ steps.review-budget-claim.outputs.allow-invocation == 'true' }}
  shell: bash
  run: |
    set -euo pipefail
    printf 'call_count=1\n' >> "$GITHUB_OUTPUT"
    printf 'started_at=%s\n' "$(date +%s)" >> "$GITHUB_OUTPUT"

- name: Capture Claude elapsed time
  id: claude-budget-elapsed
  if: ${{ always() && steps.claude-budget-metrics.outcome == 'success' }}
  shell: bash
  env:
    STARTED_AT: ${{ steps.claude-budget-metrics.outputs.started_at }}
  run: |
    set -euo pipefail
    now="$(date +%s)"
    (( now >= STARTED_AT ))
    printf 'elapsed_seconds=%s\n' "$((now - STARTED_AT))" >> "$GITHUB_OUTPUT"
```

- [ ] **Step 5: Finalize after review-state upsert and upload checkpoints**

Map canonical valid with accepted findings to `success`, canonical valid with zero accepted and positive filtered to `quality_filtered`, provider failure to `provider_failure`, invalid canonical/publication to `checkpoint_failure`, and elapsed above 600 to `wall_time_exhausted`. Finalize under `always() && !cancelled() && allow-invocation`, then upload claim checkpoint for every run and final checkpoint when it exists using pinned `actions/upload-artifact`.

```yaml
- name: Finalize Claude review budget
  id: review-budget-finalize
  if: ${{ always() && !cancelled() && steps.review-budget-claim.outputs.allow-invocation == 'true' }}
  uses: $/.github/actions/review-invocation-budget
  with:
    github-token: ${{ github.token }}
    mode: finalize
    reviewer: claude
    pr-number: ${{ inputs.pr_number || github.event.pull_request.number }}
    expected-head-sha: ${{ steps.prepare-diff.outputs.head-sha }}
    full-diff-sha256: ${{ steps.prepare-diff.outputs.full-diff-sha256 }}
    diff-mode: ${{ steps.prepare-diff.outputs.diff-mode }}
    input-files-json: '[]'
    authenticated-review-json: ${{ steps.prepare-review-input.outputs.authenticated_review_json }}
    model-route-json: ${{ steps.claude-budget-config.outputs.model_route_json }}
    effort: final-review/default
    actual-call-count: ${{ steps.claude-budget-metrics.outputs.call_count || '0' }}
    elapsed-seconds: ${{ steps.claude-budget-elapsed.outputs.elapsed_seconds || '0' }}
    outcome: ${{ steps.review-budget-outcome.outputs.outcome }}
    stop-reason: ${{ steps.review-budget-outcome.outputs.stop_reason }}
    remaining-finding-ids-json: ${{ steps.review-budget-outcome.outputs.remaining_finding_ids_json }}
    checkpoint-file: ${{ runner.temp }}/claude-review-budget-final.json
```

- [ ] **Step 6: Add the exact actionlint exception and run Claude regressions**

Add only the new `$/.github/actions/review-invocation-budget` missing-ref exception under the Claude workflow. Run:

```bash
rtk python3 -m pytest tests/test_review_workflow_logic.py -q -k 'claude or actionlint'
rtk actionlint -shellcheck= -pyflakes= .github/workflows/claude-code-review.yml
```

Expected: all Claude logic tests pass and actionlint emits no diagnostics.

- [ ] **Step 7: Commit Claude integration**

```bash
rtk git add .github/workflows/claude-code-review.yml .github/actionlint.yaml tests/test_review_workflow_logic.py
rtk git commit -m "feat(review): bound Claude review invocations"
```

---

### Task 5: Share one bounded Gemini request counter

**Files:**
- Modify: `.github/workflows/gemini-auto-review.yml`
- Modify: `.github/actionlint.yaml`
- Modify: `tests/test_review_workflow_logic.py`

**Interfaces:**
- Consumes Task 3 action with reviewer `gemini` and call cap 3.
- Produces `gemini_call_count.txt`, `gemini_started_at.txt`, `gemini_elapsed_seconds.txt`, and `gemini_model_route.json` from the provider subprocess.
- Keeps primary model `gemini-3.7-flash`, fallback model `gemini-3.6-flash`, configured thinking level, 200-second request deadline, 450-second subprocess watchdog, and ten-minute job timeout.

- [ ] **Step 1: Add RED tests for claim ordering and one shared request budget**

Extract the embedded provider Python and assert every `generate_content(prompt, model)` call is reached only through this wrapper:

```python
def counted_generate_content(prompt, model):
    count = read_call_count()
    if count >= 3:
        raise ProviderFailure("call_budget_exhausted")
    write_call_count(count + 1)
    append_model_route(model)
    return generate_content(prompt, model)
```

Test primary success records 1, two primary attempts plus fallback records 3, a fourth attempt is rejected before the API method, and a raised request still persists its increment. Workflow tests require `Claim Gemini review budget` after `Prepare review diff` but before dependency installation and provider execution.

- [ ] **Step 2: Run Gemini budget tests and confirm failure**

Run: `rtk python3 -m pytest tests/test_review_workflow_logic.py -q -k 'gemini and (budget or request_count or invocation)'`

Expected: new tests fail because Gemini does not yet claim or persist its call counter.

- [ ] **Step 3: Add authenticated state and pre-provider claim**

Emit the same schema-1 authenticated-review JSON shape from the Gemini schema-3 collector. Resolve the primary model and thinking level with a quoted `jq --arg` bridge, call the shared action with reviewer `gemini`, the selected full/delta input path, and `gemini-review-budget-claim.json`, then guard dependency installation, provider generation, canonicalization, and new-diff publication on allow true while retaining authenticated unchanged reuse.

```yaml
- name: Resolve Gemini budget metadata
  id: gemini-budget-config
  shell: bash
  env:
    PRIMARY_MODEL: ${{ vars.GEMINI_MODEL || 'gemini-3.7-flash' }}
    THINKING_LEVEL: ${{ vars.GEMINI_THINKING_LEVEL || 'medium' }}
  run: |
    set -euo pipefail
    printf 'model_route_json=%s\n' "$(jq -cn --arg model "$PRIMARY_MODEL" '[$model]')" >> "$GITHUB_OUTPUT"
    printf 'effort=%s\n' "$THINKING_LEVEL" >> "$GITHUB_OUTPUT"

- name: Claim Gemini review budget
  id: review-budget-claim
  if: ${{ always() && steps.pr-details.outcome == 'success' }}
  uses: $/.github/actions/review-invocation-budget
  with:
    github-token: ${{ github.token }}
    mode: claim
    reviewer: gemini
    pr-number: ${{ inputs.pr_number || github.event.pull_request.number }}
    expected-head-sha: ${{ steps.prepare-diff.outputs.head-sha }}
    full-diff-sha256: ${{ steps.prepare-diff.outputs.full-diff-sha256 }}
    diff-mode: ${{ steps.prepare-diff.outputs.diff-mode }}
    input-files-json: ${{ steps.prepare-diff.outputs.diff-mode == 'delta' && format('[\"{0}/review-delta.diff\"]', runner.temp) || format('[\"{0}/review-full.diff\"]', runner.temp) }}
    authenticated-review-json: ${{ steps.pr-details.outputs.authenticated_review_json }}
    model-route-json: ${{ steps.gemini-budget-config.outputs.model_route_json }}
    effort: ${{ steps.gemini-budget-config.outputs.effort }}
    checkpoint-file: ${{ runner.temp }}/gemini-review-budget-claim.json
```

- [ ] **Step 4: Increment before every request and persist actual routing**

Create the metric files before the subprocess starts. Replace direct API calls with `counted_generate_content`; write the increment atomically before calling the SDK, append each actually attempted model once, and leave the existing primary retry/fallback classification unchanged. Primary retries and fallback consume the same three-request file.

```python
def counted_generate_content(prompt: str, model: str):
    count = int(call_count_path.read_text(encoding="ascii"))
    if count >= 3:
        raise ProviderFailure("call_budget_exhausted")
    call_count_path.write_text(f"{count + 1}\n", encoding="ascii")
    route = json.loads(model_route_path.read_text(encoding="utf-8"))
    route.append(model)
    model_route_path.write_text(
        json.dumps(route, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    return generate_content(prompt, model)
```

- [ ] **Step 5: Finalize after schema-3 upsert and upload checkpoints**

Read the metric files even when the subprocess exits nonzero, map the existing failure reason deterministically, finalize under `always() && !cancelled() && allow-invocation`, and upload claim/final checkpoint artifacts with unique run ID/attempt names. A provider error must not dispatch Claude or OpenCode and a same-input rerun must stop at the ledger.

```yaml
- name: Finalize Gemini review budget
  id: review-budget-finalize
  if: ${{ always() && !cancelled() && steps.review-budget-claim.outputs.allow-invocation == 'true' }}
  uses: $/.github/actions/review-invocation-budget
  with:
    github-token: ${{ github.token }}
    mode: finalize
    reviewer: gemini
    pr-number: ${{ inputs.pr_number || github.event.pull_request.number }}
    expected-head-sha: ${{ steps.prepare-diff.outputs.head-sha }}
    full-diff-sha256: ${{ steps.prepare-diff.outputs.full-diff-sha256 }}
    diff-mode: ${{ steps.prepare-diff.outputs.diff-mode }}
    input-files-json: '[]'
    authenticated-review-json: ${{ steps.pr-details.outputs.authenticated_review_json }}
    model-route-json: ${{ steps.gemini-budget-metrics.outputs.model_route_json }}
    effort: ${{ steps.gemini-budget-config.outputs.effort }}
    actual-call-count: ${{ steps.gemini-budget-metrics.outputs.call_count }}
    elapsed-seconds: ${{ steps.gemini-budget-metrics.outputs.elapsed_seconds }}
    outcome: ${{ steps.review-budget-outcome.outputs.outcome }}
    stop-reason: ${{ steps.review-budget-outcome.outputs.stop_reason }}
    remaining-finding-ids-json: ${{ steps.review-budget-outcome.outputs.remaining_finding_ids_json }}
    checkpoint-file: ${{ runner.temp }}/gemini-review-budget-final.json
```

- [ ] **Step 6: Run Gemini deadline, fallback, quality, and lint regressions**

```bash
rtk python3 -m pytest tests/test_review_workflow_logic.py -q -k 'gemini or actionlint'
rtk actionlint -shellcheck= -pyflakes= .github/workflows/gemini-auto-review.yml
```

Expected: existing timeout/fallback classifications stay green; new counter, guard, finalization, and zero-call duplicate tests pass.

- [ ] **Step 7: Commit Gemini integration**

```bash
rtk git add .github/workflows/gemini-auto-review.yml .github/actionlint.yaml tests/test_review_workflow_logic.py
rtk git commit -m "feat(review): bound Gemini review requests"
```

---

### Task 6: Carry the OpenCode claim across the sealed three-job boundary

**Files:**
- Modify: `.github/workflows/opencode-auto-review.yml`
- Modify: `.github/actionlint.yaml`
- Modify: `tests/test_review_workflow_logic.py`

**Interfaces:**
- Consumes Task 3 action with reviewer `opencode` and call cap 2.
- Produces prepare-job outputs `allow_invocation`, `budget_decision`, `budget_checkpoint_sha256`, and a sealed `review-budget-claim.json` file/hash in `handoff.json`.
- Produces model-job outputs `review_call_count`, `review_elapsed_seconds`, and actual model route `["zai-coding-plan/glm-4.7"]` inside the sealed candidate artifact.
- Produces canonicalize-job final checkpoint after existing schema-2 attestation and canonical publication checks.

- [ ] **Step 1: Add RED three-job contract tests**

Require:

```python
def test_opencode_claim_is_sealed_before_tokenless_model_job():
    workflow = _load("opencode-auto-review.yml")
    prepare = workflow["jobs"]["opencode-prepare"]
    review = workflow["jobs"]["opencode-review"]
    assert prepare["outputs"]["allow_invocation"] == "${{ steps.review-budget-claim.outputs.allow-invocation }}"
    assert "needs.opencode-prepare.outputs.allow_invocation == 'true'" in review["if"]
    assert review["timeout-minutes"] == "10"
```

Also assert `review-budget-claim.json` and its hash are in the handoff allowlist, the model job has empty permissions, each `run_opencode` call increments the same file before CLI execution, the repair cannot become a third session, and canonicalize finalizes only the exact claim identity from the sealed handoff.

- [ ] **Step 2: Run OpenCode budget tests and confirm failure**

Run: `rtk python3 -m pytest tests/test_review_workflow_logic.py -q -k 'opencode and (budget or invocation or timeout or handoff)'`

Expected: new tests fail because the budget claim and cross-job metrics are absent.

- [ ] **Step 3: Claim in `opencode-prepare` and seal the checkpoint**

After `Prepare review diff`, call the action with reviewer `opencode`, `review-full.diff` and `review-scope.json` as input files, model route `["zai-coding-plan/glm-4.7"]`, and effort `final-review/default`. Add the allow/decision/checkpoint outputs to the job. Copy the claim checkpoint into the handoff and extend `handoff.json.files` with its SHA-256. The sealed handoff validator must require exactly the added filename and verify its digest.

```yaml
- name: Claim OpenCode review budget
  id: review-budget-claim
  if: ${{ always() && steps.ctx.outcome == 'success' }}
  uses: $/.github/actions/review-invocation-budget
  with:
    github-token: ${{ github.token }}
    mode: claim
    reviewer: opencode
    pr-number: ${{ inputs.pr_number || github.event.pull_request.number || github.event.issue.number }}
    expected-head-sha: ${{ steps.prepare-diff.outputs.head-sha }}
    full-diff-sha256: ${{ steps.prepare-diff.outputs.full-diff-sha256 }}
    diff-mode: ${{ steps.prepare-diff.outputs.diff-mode }}
    input-files-json: ${{ format('[\"{0}/review-full.diff\",\"{0}/review-scope.json\"]', github.workspace) }}
    authenticated-review-json: ${{ steps.ctx.outputs.authenticated_review_json }}
    model-route-json: '["zai-coding-plan/glm-4.7"]'
    effort: final-review/default
    checkpoint-file: ${{ runner.temp }}/opencode-review-budget-claim.json
```

The handoff builder copies that exact file and adds it to the existing hash map with key `review-budget-claim.json`; the validator's sorted filename assertion adds exactly that basename.

- [ ] **Step 4: Guard the model job and count both allowed CLI sessions**

Add `timeout-minutes: 10` to `opencode-review`; require allow true in the job condition and in cache/install/run steps. Implement one wrapper:

```bash
run_opencode() {
  local prompt_path="$1"
  local output_path="$2"
  shift 2
  local count
  count="$(cat "$call_count_file")"
  (( count < 2 )) || { review_failure_reason=call_budget_exhausted; return 1; }
  printf '%s\n' "$((count + 1))" > "$call_count_file"
  env -i PATH="$PATH" HOME="$isolated_home" XDG_CONFIG_HOME="$isolated_xdg" \
    XDG_DATA_HOME="$isolated_xdg/data" XDG_CACHE_HOME="$isolated_xdg/cache" \
    ZHIPU_API_KEY="$ZHIPU_API_KEY" OPENCODE_PURE="$OPENCODE_PURE" \
    OPENCODE_DISABLE_PROJECT_CONFIG="$OPENCODE_DISABLE_PROJECT_CONFIG" \
    OPENCODE_CONFIG_CONTENT="$OPENCODE_CONFIG_CONTENT" \
    opencode run --model zai-coding-plan/glm-4.7 --format json "$@" \
    < "$prompt_path" > "$output_path"
}
```

Write call count, elapsed seconds, and model route into the candidate artifact even on provider failure. Keep the second invocation limited to the existing format-only repair predicate.

- [ ] **Step 5: Finalize in `opencode-canonicalize`**

Download and validate the claim checkpoint and metric files, perform the existing clean attestation/publication logic, derive remaining authenticated `RVW-*` IDs, then call finalization under `always() && !cancelled()` only for an allowed claim. Upload the final checkpoint. A refused claim uploads the prepare checkpoint and skips the tokenless model job without converting the schema-2 review state to success.

```yaml
- name: Finalize OpenCode review budget
  id: review-budget-finalize
  if: ${{ always() && !cancelled() && needs.opencode-prepare.outputs.allow_invocation == 'true' }}
  uses: $/.github/actions/review-invocation-budget
  with:
    github-token: ${{ github.token }}
    mode: finalize
    reviewer: opencode
    pr-number: ${{ inputs.pr_number || github.event.pull_request.number || github.event.issue.number }}
    expected-head-sha: ${{ needs.opencode-prepare.outputs.attempt_head }}
    full-diff-sha256: ${{ needs.opencode-prepare.outputs.full_diff_sha256 }}
    diff-mode: ${{ needs.opencode-prepare.outputs.diff_mode }}
    input-files-json: '[]'
    authenticated-review-json: ${{ steps.opencode-budget-outcome.outputs.authenticated_review_json }}
    model-route-json: '["zai-coding-plan/glm-4.7"]'
    effort: final-review/default
    actual-call-count: ${{ needs.opencode-review.outputs.review_call_count || '0' }}
    elapsed-seconds: ${{ needs.opencode-review.outputs.review_elapsed_seconds || '0' }}
    outcome: ${{ steps.opencode-budget-outcome.outputs.outcome }}
    stop-reason: ${{ steps.opencode-budget-outcome.outputs.stop_reason }}
    remaining-finding-ids-json: ${{ steps.opencode-budget-outcome.outputs.remaining_finding_ids_json }}
    checkpoint-file: ${{ runner.temp }}/opencode-review-budget-final.json
```

- [ ] **Step 6: Run OpenCode attestation, repair, boundary, and lint regressions**

```bash
rtk python3 -m pytest tests/test_review_workflow_logic.py -q -k 'opencode or actionlint'
rtk actionlint -shellcheck= -pyflakes= .github/workflows/opencode-auto-review.yml
```

Expected: all prior attestation and format-repair tests remain green; new timeout, two-call, sealed-checkpoint, finalization, and duplicate-refusal tests pass.

- [ ] **Step 7: Commit OpenCode integration**

```bash
rtk git add .github/workflows/opencode-auto-review.yml .github/actionlint.yaml tests/test_review_workflow_logic.py
rtk git commit -m "feat(review): bound OpenCode review sessions"
```

---

### Task 7: Close the release inventory, verifier, and consumer contract

**Files:**
- Modify: `scripts/workflow_release_inventory.py`
- Modify: `scripts/verify_workflow_release.py`
- Modify: `tests/test_workflow_release_bundle.py`
- Modify: `tests/test_verify_workflow_release.py`
- Modify: `docs/workflows/contracts.md`

**Interfaces:**
- Consumes exact action/helper/workflow bytes from Tasks 1-6.
- Produces `REVIEW_INVOCATION_BUDGET_ACTION_ROOT`, `REVIEW_INVOCATION_BUDGET_HELPER_ROOT`, `REVIEW_INVOCATION_BUDGET_ROOTS`, `REVIEW_INVOCATION_BUDGET_RELEASE = (1, 47)`, and `release_supports_review_invocation_budget(ref: str) -> bool`.
- Produces `EXPECTED_REVIEW_INVOCATION_BUDGET_ACTION` in the verifier and exact expected local-action lists for Claude, Gemini, and OpenCode at `v1.47+`.
- Produces `REVIEWER_WORKFLOWS`, `require_budget_helper_contract(source: str) -> None`, and `require_budget_workflow_contract(tree: VerifiedCommitTree, workflow: str, reviewer: str) -> None` in the verifier.
- Preserves the v1.46.x inventory, current `v1.46.2` fleet default, and every historical immutable fixture.

- [ ] **Step 1: Add RED inventory and exact-action tests**

In bundle tests, assert both files are regular 0644 release members for `v1.47`, absent for `v1.46.2`, and that the parsed action document exactly equals its declared inputs, outputs, one composite step, quoted env bridge, and modes. In verifier tests, synthesize a v1.47 tree and require rejection when either new file is missing, extra files appear under an owned tree, or mode bits change.

- [ ] **Step 2: Add RED mutation tests for every safety gate**

Parametrize authenticated blob mutations that remove or weaken each required literal/AST relationship:

```python
@pytest.mark.parametrize("needle", [
    "duplicate_head",
    "duplicate_effective_diff",
    "round_budget_exhausted",
    "input_budget_exhausted",
    "total_usage_budget_exhausted",
    "call_budget_exhausted",
    "wall_time_exhausted",
    "provenance_mismatch",
    "compare_and_swap_failed",
])
def test_v147_rejects_budget_helper_gate_removal(release_repo, needle):
    mutate_owned_helper(release_repo, lambda text: text.replace(needle, "weakened", 1))
    with pytest.raises(ReleaseVerificationError, match="invocation-budget helper contract"):
        verify_current_release(release_repo, ref="v1.47")
```

Add workflow mutations that move claim after provider, remove allow conditions, raise timeouts/call caps, omit finalization, remove checkpoint artifacts, break OpenCode handoff hashes, or make provider failure dispatch another reviewer.

- [ ] **Step 3: Run release tests and confirm the unsupported-capability failures**

Run: `rtk python3 -m pytest tests/test_workflow_release_bundle.py tests/test_verify_workflow_release.py -q`

Expected: new v1.47 tests fail because inventory and verifier support are absent; historical cases remain green.

- [ ] **Step 4: Add the v1.47 closed inventory without changing old roots**

Add two exact 0644 roots and gate them only from `v1.47`:

```python
REVIEW_INVOCATION_BUDGET_RELEASE = (1, 47)
REVIEW_INVOCATION_BUDGET_ACTION_ROOT = ReleaseRoot(
    PurePosixPath(".github/actions/review-invocation-budget/action.yml"), "file", "100644"
)
REVIEW_INVOCATION_BUDGET_HELPER_ROOT = ReleaseRoot(
    PurePosixPath(".github/actions/review-invocation-budget/review_invocation_budget.py"), "file", "100644"
)
REVIEW_INVOCATION_BUDGET_ROOTS = (
    REVIEW_INVOCATION_BUDGET_ACTION_ROOT,
    REVIEW_INVOCATION_BUDGET_HELPER_ROOT,
)

def release_supports_review_invocation_budget(ref: str) -> bool:
    return _release_version(ref) >= REVIEW_INVOCATION_BUDGET_RELEASE
```

Append these roots in `release_roots_for` only when the capability predicate is true.

- [ ] **Step 5: Implement exact verifier contracts**

Require the exact BaseLoader action document, compile the helper from authenticated bytes, verify its public signatures/constants/fixed policy values, and inspect AST/control-flow source for strict schema, ordered duplicate/input/round/total gates, one-use override, current/historical provenance distinction, hard finalization caps, bounded IDs, deterministic checkpoint, and CAS failure. Parsed workflow checks must require exactly one claim/finalize action pair per reviewer, claim-before-provider ordering, allow conditions, timeouts, counters, and artifacts. Keep verification based only on authenticated commit objects.

```python
def _verify_review_invocation_budget(tree: VerifiedCommitTree, ref: str) -> None:
    if not release_supports_review_invocation_budget(ref):
        return
    action = yaml.load(
        tree.read_text(".github/actions/review-invocation-budget/action.yml"),
        Loader=yaml.BaseLoader,
    )
    if action != EXPECTED_REVIEW_INVOCATION_BUDGET_ACTION:
        raise ReleaseVerificationError("invocation-budget action contract is invalid")
    helper = tree.read_text(
        ".github/actions/review-invocation-budget/review_invocation_budget.py"
    )
    compile(helper, "review_invocation_budget.py", "exec")
    require_budget_helper_contract(helper)
    for reviewer, workflow in REVIEWER_WORKFLOWS.items():
        require_budget_workflow_contract(tree, workflow, reviewer)
```

- [ ] **Step 6: Document the operational contract**

Add a `Review invocation budget and handoff` section to `docs/workflows/contracts.md` containing exact markers, budgets, round definition, call units, decision order, override-label event consumption, failure semantics, model/effort recording, checkpoint artifact names, handoff fields, and the statement that exhaustion is not approval. Document that `/jhw:ship` polls deterministically and does not parse the budget ledger as review evidence.

- [ ] **Step 7: Run release/bundle tests and proposed v1.47 commit verification**

```bash
rtk python3 -m pytest tests/test_workflow_release_bundle.py tests/test_verify_workflow_release.py -q
rtk python3 -m scripts.verify_workflow_release --automation . --ref v1.47 --expected-commit "$(rtk git rev-parse HEAD)" --commit-only
```

Expected: historical v1.40-v1.46.2 fixtures pass, v1.47 includes exactly both budget files and hardened workflows, and commit-only verification reports PASS. No `v1.47` tag is created.

- [ ] **Step 8: Commit the release contract and documentation**

```bash
rtk git add scripts/workflow_release_inventory.py scripts/verify_workflow_release.py tests/test_workflow_release_bundle.py tests/test_verify_workflow_release.py docs/workflows/contracts.md
rtk git commit -m "feat(review): close invocation budget release contract"
```

---

### Task 8: Verify, review, open PR, and merge issue #52

**Files:**
- Modify only if a failing verification or validated review finding demonstrates a defect in Tasks 1-7.
- Preserve the approved spec and this implementation plan.

**Interfaces:**
- Consumes all issue #52 implementation commits.
- Produces one independently reviewed PR that closes GitHub issue #52 and is merged into the then-current `main`.
- Stops without merge if the verified PR head changes unexpectedly, a required check is failing, a valid blocking finding remains, or the current branch no longer contains the tested commits.

- [ ] **Step 1: Run helper syntax and focused suites**

```bash
rtk python3 -m py_compile .github/actions/review-invocation-budget/review_invocation_budget.py
rtk python3 -m pytest tests/test_review_invocation_budget.py tests/test_review_invocation_budget_action.py tests/test_review_workflow_logic.py tests/test_workflow_release_bundle.py tests/test_verify_workflow_release.py -q
```

Expected: both commands exit 0.

- [ ] **Step 2: Run complete Python, YAML, and actionlint verification**

```bash
rtk python3 -m pytest -q
rtk python3 -c 'from pathlib import Path; import yaml; paths=sorted(Path(".github/workflows").glob("*.y*ml"))+sorted(Path("examples/baseline-workflows/.github/workflows").glob("*.y*ml"))+sorted(Path(".github/actions").glob("**/*.y*ml")); assert all(isinstance(yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader), dict) for path in paths); print(f"PASS: {len(paths)} YAML documents")'
rtk actionlint -shellcheck= -pyflakes= .github/workflows/*.yml examples/baseline-workflows/.github/workflows/*.yml
```

Expected: complete pytest passes, every YAML document parses as a mapping, and actionlint emits no diagnostics.

- [ ] **Step 3: Run release verifier and mutation-focused gates**

```bash
rtk python3 -m pytest tests/test_verify_workflow_release.py -q -k 'v147 or invocation_budget or mutation'
rtk python3 -m scripts.verify_workflow_release --automation . --ref v1.47 --expected-commit "$(rtk git rev-parse HEAD)" --commit-only
```

Expected: all budget mutations are rejected and proposed v1.47 content verification passes without creating or pushing a tag.

- [ ] **Step 4: Audit exact scope and whitespace**

```bash
rtk git diff --check origin/main...HEAD
rtk git status --short --branch
rtk git diff --stat origin/main...HEAD
rtk git log --oneline origin/main..HEAD
```

Expected: only the approved spec, plan, budget action/helper/tests, three central review workflows, actionlint config, release inventory/verifier/tests, and workflow contract differ. No consumer rollout, release tag/default, issue #43 files outside this scope, or user-owned workspace path changes appear.

- [ ] **Step 5: Request independent two-stage review and repair only validated findings**

Use `superpowers:requesting-code-review` to dispatch a read-only reviewer against `origin/main...HEAD`. First verify spec compliance and issue acceptance mapping; then review correctness, security, failure semantics, workflow expression behavior, fixture quality, and test gaps. For every valid finding, add a focused RED test, implement the smallest correction, rerun Steps 1-4, and commit with a finding-specific message. Reject unsupported findings with concrete file/test evidence.

- [ ] **Step 6: Push the branch and create one issue-closing PR**

```bash
rtk git push --set-upstream origin feat/52-review-invocation-budget
rtk gh pr create --repo jhw7500/automation --base main --head feat/52-review-invocation-budget --title "feat(review): bound automated review invocations" --body $'Closes #52.\n\nAdds durable per-reviewer claims, duplicate head/effective-diff gates, two-round budgets with one audited override, provider call/wall/input caps, and checkpoint-only handoff across Claude, Gemini, and OpenCode. Release/tag rollout remains separate.'
```

If that branch already has an open PR, inspect it with `rtk gh pr view feat/52-review-invocation-budget --repo jhw7500/automation` and continue the existing PR.

- [ ] **Step 7: Monitor deterministic checks and reviewer comments**

Use `$jhw-ship --merge --auto-fix --target='rtk python3 -m pytest -q' --base=main`. Poll Actions and PR state deterministically, summarize terminal signals once, and do not request the same automated reviewer again for an unchanged head/effective diff. If a provider reports a terminal quota/service error, preserve that failed checkpoint and evaluate only the repository's documented exception path; do not open a fallback cascade or force merge.

- [ ] **Step 8: Rebase or merge current main only when required, then reverify the exact head**

If main advanced, incorporate it non-destructively, rerun Steps 1-4, push the new head, and wait for checks on that exact SHA. Confirm the PR head equals `rtk git rev-parse HEAD` before merge.

- [ ] **Step 9: Merge and verify closure**

After required checks/reviews are terminal and acceptable, let `$jhw-ship --merge` merge the verified current head. Then run:

```bash
rtk gh pr view --repo jhw7500/automation --json number,state,mergedAt,mergeCommit,url
rtk gh issue view 52 --repo jhw7500/automation --json number,state,url
rtk git fetch origin main
rtk git log -1 --oneline origin/main
```

Expected: PR state is `MERGED`, issue #52 is `CLOSED`, and `origin/main` contains the merged implementation. Leave release creation and fleet rollout for a separate explicitly authorized task.
