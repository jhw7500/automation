# Claude Caller Rollout Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give an exact immutable workflow rollout pull request one authenticated, budgeted Claude review through the consumer's default-branch caller after the automatic reviewer refuses a changed caller with `workflow_validation_mismatch` before provider execution.

**Architecture:** Parse and authenticate one canonical issue-comment request in a release-owned composite action, route it from the existing default-branch `claude.yml` caller into the same immutable `claude-code-review.yml`, record the route in the shared Claude budget and canonical review state, and attest the complete automatic-failure-to-fallback chain with a read-only fleet verifier. Preserve the automatic failure and every native merge gate; only the later JHW command may consume the verifier receipt under a separately approved repository Task.

**Tech Stack:** Python standard library, Node scripts embedded in GitHub Actions, Bash, GitHub Actions reusable workflows and REST API, existing pytest/YAML harnesses, actionlint, and existing workflow fleet rendering modules.

**Spec:** ../specs/2026-09-11-claude-caller-rollout-fallback-design.md

## Main integration note (2026-09-11)

#183 published observational token estimates as immutable v1.77: annotated tag
`81f44fb6786bdfcc40f93161db74b1d9a9e3b7c5` peels to
`dd13f9dcc64540494c1c04bc3f9c7a4f2ef0ba19`. Integrate that exact main with a normal
merge, preserving the reviewed #182 Task 1-6 history. The fallback boundary was
published as immutable v1.78. Never republish or move v1.77 or v1.78.
Schema 2 preserves v1.77's observational token estimates and summary field while
retaining round, override, call-count, wall-time, checkpoint and provenance gates.
The v1.76 bootstrap evidence and immutable v1.76 identity below remain historical.

## Tribunal correction (2026-09-12)

The first `wlan-package` bootstrap candidate at v1.78 reached exact-head tribunal
review as PR #331. Reviewer A found that its caller-level `pull-requests: write`
and `id-token: write` ceiling also reached the unconditional `check-enabled` job,
where a mutable `check-workflow-enabled@v1.1` action ran before managed admission.
The public exact-head Codex review therefore supersedes the earlier zero-finding
comment and blocks that HEAD.

The bounded correction is a normal security patch. v1.78.1 gives
`check-enabled` only `contents: read`, gives `skipped` no permissions, and pins
the nested check action to its exact reviewed commit. The release verifier keeps
the immutable v1.78 contract accepted but requires this hardening at v1.78.1 and
later. PR #331 must be updated to v1.78.1 and pass a complete new review round
before any merge decision. The distinct release-owned-byte boundary canary moves
to v1.78.2 after v1.78.1 is installed on the consumer default branch.

## Global Constraints

- Work only in `/home/jhw/ai/opencode/worktrees/jhw-control/wt-7e615b91b0e8-jhw7500-automation-182` on `task/7e615b91b0e8-jhw7500-automation-182`, starting from design commit `b10503c` over immutable v1.76 source `444a7347aee169ed178aae80e8bd8d10eca52e02`.
- Keep Task `tsk-01a08b34-cc9e-777f-a99d-7e615b91b0e8` and its current supported Claim. Do not add, take over, delete, repair, or directly edit a Claim, lock, Registry record, budget ledger, secret, or Notion record.
- Every shell command starts with `rtk`; commands that need an unwrapped executable use `rtk proxy`. Apply source edits with `apply_patch`.
- Write a failing behavioral test before each implementation change. Add no dependency and execute no code, action, hook, filter, binary, or artifact supplied by a consumer pull request.
- Preserve v1.76, v1.77, and v1.78 bytes, tags, release acceptance, and historical fixtures. Own the fallback behind the v1.78 feature boundary and the pre-admission hardening behind v1.78.1.
- Authenticate only `workflow_validation_mismatch` with `review_execution=not_performed`, the Claude provider step skipped, and no budget claim. `workflow_validation_unavailable`, provider entry, any other failure, stale coordinates, ambiguity, pagination overflow, and transport uncertainty remain blocking.
- The fallback uses the existing Claude credential once and consumes one ordinary automatic review round for the exact pull-request HEAD/full diff. A retry reuses a valid request or finalized result and cannot add a second provider call.
- Do not make the original failed automatic check successful, hide it, waive a required status, weaken branch protection, or replace target tests, mergeability, exact HEAD/base, origin, or GitHub-generated merge verification.
- The consumer permission ceiling for `claude.yml` becomes `pull-requests: write`; the central interactive Claude job explicitly reduces itself to `pull-requests: read`; `check-enabled` is limited to `contents: read`, `skipped` has no permissions, and only the admitted nested managed job retains write permission for the canonical sticky comment. The pre-admission check action is pinned to an immutable commit.
- This plan changes only the automation repository. It ends with a versioned receipt schema and verifier CLI. Before editing `/home/jhw/ai/opencode/projects/jhw-notion-runtime`, run that repository's stateless Task nudge, obtain any required separate approval, and write a second implementation plan.
- Do not publish v1.78.1 or update the consumer candidate until the hardening branch passes local verification, native pre-PR tribunal, hosted review, and reviewed merge. Stop a canary if a required failed Claude check remains native-blocking.

---

## Contract Map

| Contract | Producer | Consumer | Exact version |
| --- | --- | --- | --- |
| Managed request marker | Later JHW command | `claude-rollout-fallback/contract.py` | `automation:claude-rollout-review-request:v1` |
| Admission JSON | `claude-rollout-fallback/action.yml` | central `claude.yml` and nested review | schema 1 |
| Budget ledger | `review-invocation-budget` | later review runs and verifier | schema 2, reads schema 1 |
| Claude sticky state | `claude-code-review.yml` | review context and fleet verifier | schema 3 with exact optional extensions |
| Fleet fallback receipt | `verify_claude_rollout_fallback.py` | later JHW command | schema 1 |
| Workflow release | reviewed automation merge | release verifier and consumers | v1.78 feature; v1.78.1 hardening |

The only accepted request body is the following two-line form, with one optional final LF and no other bytes:

```text
@claude managed rollout review for PR 109
<!-- automation:claude-rollout-review-request:v1 {"expected_base_sha":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","expected_head_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","managed_diff_sha256":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","nonce":"dddddddddddddddddddddddddddddddd","original_run_attempt":1,"original_run_id":34549275027,"pr":109,"release_commit":"444a7347aee169ed178aae80e8bd8d10eca52e02","repository":"jhw7500/gstApp"} -->
```

The parser sorts keys and uses compact UTF-8 JSON when it reconstructs this body. The JSON object has exactly the nine displayed keys. SHA values use lowercase hex, numeric values are positive safe integers, and the nonce is exactly 32 lowercase hex characters.

The nested review uses these eight all-or-none `workflow_call` inputs:

| Input | Type | Binding |
| --- | --- | --- |
| `fallback_request_comment_id` | string | fetched event comment ID |
| `fallback_request_nonce` | string | request marker nonce |
| `fallback_expected_head_sha` | string | current pull-request HEAD |
| `fallback_expected_base_sha` | string | current pull-request base object |
| `fallback_original_run_id` | string | failed automatic run |
| `fallback_original_run_attempt` | string | exact failed attempt |
| `fallback_release_commit` | string | immutable automation commit |
| `fallback_managed_diff_sha256` | string | exact fleet-rendered full diff |

---

### Task 1: Pure request and admission contract

**Files:**

- Create: `.github/actions/claude-rollout-fallback/contract.py`
- Create: `.github/actions/claude-rollout-fallback/action.yml`
- Create: `tests/test_claude_rollout_fallback.py`

**Interfaces:**

- `FallbackRequest(repository, pr, expected_head_sha, expected_base_sha, original_run_id, original_run_attempt, release_commit, managed_diff_sha256, nonce)` is the exact parsed request.
- `RequestClassification(route, reason, request)` has route `interactive`, `managed_candidate`, or `invalid`. A body containing neither the reserved visible prefix nor the hidden marker is interactive without network reads. Any body containing either reserved token is a managed candidate; malformed, displaced, quoted, duplicated, or unauthorized forms are invalid and cannot reach the interactive provider.
- `EvidenceBundle(event_name, event, comment, pull_request, original_run, original_jobs, issue_comments)` contains already-fetched JSON and performs no I/O.
- `FallbackAdmission(request_comment_id, request, automatic_comment_id, automatic_state_sha256)` is emitted only after every request, actor, pull request, run, job, and canonical-state check succeeds.
- `classify_event(event_name: object, event: object) -> RequestClassification`, `parse_request_body(body: object) -> FallbackRequest`, `canonical_request_body(request: FallbackRequest) -> str`, and `admit(bundle: EvidenceBundle) -> FallbackAdmission` are pure and fail with bounded reason codes.
- The private validators have fixed signatures: `event_text_fields(event_name: object, event: object) -> tuple[str, ...]`, `event_comment_body(event: object) -> str`, `positive_id(value: object, reason: str) -> int`, `require_managed_created_event(event_name: object, event: object) -> FallbackRequest`, `require_exact_comment(comment: object, event: object, request: FallbackRequest) -> None`, `require_exact_open_same_repository_pr(pull_request: object, request: FallbackRequest) -> None`, `require_exact_automatic_run(run: object, request: FallbackRequest) -> None`, `require_causal_order(run: object, comment: object) -> None`, `require_failed_before_provider(jobs: object) -> None`, `require_unique_request(comments: object, current_comment_id: int, request: FallbackRequest) -> None`, `require_automatic_failure_state(comments: object, request: FallbackRequest) -> tuple[int, bytes]`, and `require_no_successful_fallback(comments: object, request: FallbackRequest) -> None`. Each accepts decoded data only and raises `ContractError` with a bounded lowercase reason.
- `contract.py plan --event-name NAME --event-file PATH --plan-file PATH --github-output PATH` parses the event, writes a private evidence plan for managed candidates, and writes only route/reason for non-managed routes. `contract.py verify --plan-file PATH --evidence-directory PATH --admission-file PATH --github-output PATH` reads the completed fixed-name responses, writes one private admission JSON file, and appends regex-validated scalar outputs to `GITHUB_OUTPUT`.
- The composite makes only fixed GitHub REST reads: exact comment, exact pull request, exact run attempt, at most 100 jobs, and at most ten 100-comment pages. A full tenth page is `comment_horizon_exceeded`, rather than implicit completeness.
- `require_unique_request` counts canonical request comments for the same repository/PR/HEAD/base/original run/attempt/release/diff tuple while ignoring nonce differences; only the current comment may exist. A second matching tuple is `request_ambiguous`.
- `require_causal_order` accepts only canonical UTC GitHub timestamps and requires the automatic attempt's `updated_at` to be no later than the request comment's `created_at`; missing, malformed, or reversed time is `request_order_invalid`.
- Composite outputs are exactly `route`, `reason`, the eight nested inputs without their `fallback_` prefix, plus `automatic-comment-id` and `automatic-state-sha256`. Managed admission sets route `managed`, leaves reason empty, and fills all ten evidence outputs. Interactive and invalid classifications leave every evidence output empty; invalid alone emits a bounded reason.

Target type and key shape:

```python
class ContractError(ValueError):
    pass

VISIBLE_PREFIX = "@claude managed rollout review for PR "
HIDDEN_MARKER = "<!-- automation:claude-rollout-review-request:v1 "
REQUEST_BODY_RE = re.compile(
    r"\A@claude managed rollout review for PR (?P<visible_pr>[1-9][0-9]{0,15})\n"
    r"<!-- automation:claude-rollout-review-request:v1 (?P<json>\{[^\r\n]*\}) -->\n?\Z",
    re.ASCII,
)
REQUEST_KEYS = frozenset({
    "repository", "pr", "expected_head_sha", "expected_base_sha",
    "original_run_id", "original_run_attempt", "release_commit",
    "managed_diff_sha256", "nonce",
})
SHA_RE = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)
DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
NONCE_RE = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
REPOSITORY_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z", re.ASCII)
MAX_SAFE_INTEGER = 2**53 - 1

@dataclass(frozen=True)
class FallbackRequest:
    repository: str
    pr: int
    expected_head_sha: str
    expected_base_sha: str
    original_run_id: int
    original_run_attempt: int
    release_commit: str
    managed_diff_sha256: str
    nonce: str

    @classmethod
    def from_dict(cls, value: object) -> "FallbackRequest":
        if not isinstance(value, dict) or set(value) != REQUEST_KEYS:
            raise ContractError("request_invalid")
        integers = (value["pr"], value["original_run_id"], value["original_run_attempt"])
        if any(type(item) is not int or not 1 <= item <= MAX_SAFE_INTEGER for item in integers):
            raise ContractError("request_invalid")
        repository = value["repository"]
        shas = (value["expected_head_sha"], value["expected_base_sha"], value["release_commit"])
        if not isinstance(repository, str) or REPOSITORY_RE.fullmatch(repository) is None:
            raise ContractError("request_invalid")
        if any(not isinstance(item, str) or SHA_RE.fullmatch(item) is None for item in shas):
            raise ContractError("request_invalid")
        digest, nonce = value["managed_diff_sha256"], value["nonce"]
        if not isinstance(digest, str) or DIGEST_RE.fullmatch(digest) is None:
            raise ContractError("request_invalid")
        if not isinstance(nonce, str) or NONCE_RE.fullmatch(nonce) is None:
            raise ContractError("request_invalid")
        return cls(
            repository=repository,
            pr=value["pr"],
            expected_head_sha=value["expected_head_sha"],
            expected_base_sha=value["expected_base_sha"],
            original_run_id=value["original_run_id"],
            original_run_attempt=value["original_run_attempt"],
            release_commit=value["release_commit"],
            managed_diff_sha256=digest,
            nonce=nonce,
        )

    def to_dict(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in REQUEST_KEYS}

@dataclass(frozen=True)
class FallbackAdmission:
    request_comment_id: int
    request: FallbackRequest
    automatic_comment_id: int
    automatic_state_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "automatic_comment_id": self.automatic_comment_id,
            "automatic_state_sha256": self.automatic_state_sha256,
            "request": self.request.to_dict(),
            "request_comment_id": self.request_comment_id,
            "schema": 1,
        }
```

Required positive assertion:

```python
admission = admit(valid_bundle())
assert admission.request.original_run_id == 34549275027
assert admission.request_comment_id == 901
assert admission.automatic_comment_id == 887
assert canonical_request_body(admission.request) == REQUEST_BODY
```

- [ ] **Step 1: Write failing request parser and classifier cases.** Add the exact valid body above and this parameter table; each mutation must raise `ContractError("request_invalid")`, while a non-reserved body remains interactive.

```python
@pytest.mark.parametrize("change", [
    "duplicate_key", "extra_key", "missing_key", "uppercase_head",
    "zero_run", "unsafe_integer", "short_nonce", "second_marker",
    "quoted_marker", "control_character", "invalid_unicode", "oversize",
    "malformed_json",
])
def test_request_parser_rejects_noncanonical_bytes(change):
    with pytest.raises(ContractError, match="^request_invalid$"):
        parse_request_body(mutated_request_body(change))

def test_classifier_never_routes_reserved_invalid_text_to_interactive():
    bodies = (
        "@claude managed rollout review for PR 109",
        "> @claude managed rollout review for PR 109",
        "<!-- automation:claude-rollout-review-request:v1 {} -->",
    )
    for body in bodies:
        result = classify_event("issue_comment", issue_comment_event(body))
        assert (result.route, result.reason, result.request) == (
            "invalid", "request_invalid", None,
        )
```

- [ ] **Step 2: Run the parser cases and verify red.**

```bash
rtk pytest -q tests/test_claude_rollout_fallback.py -k 'request or classifier'
```

Expected: collection fails because `contract.py` or its public names do not exist.

- [ ] **Step 3: Implement the exact parser and classifier.** Use a duplicate-key hook, full-body regular expression, compact sorted JSON reconstruction, and the three closed routes.

```python
def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError("request_invalid")
        result[key] = value
    return result

def parse_request_body(body: object) -> FallbackRequest:
    if not isinstance(body, str):
        raise ContractError("request_invalid")
    try:
        encoded = body.encode("utf-8")
    except UnicodeEncodeError:
        raise ContractError("request_invalid") from None
    if len(encoded) > 4096:
        raise ContractError("request_invalid")
    match = REQUEST_BODY_RE.fullmatch(body)
    if match is None:
        raise ContractError("request_invalid")
    try:
        value = json.loads(match.group("json"), object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, ContractError):
        raise ContractError("request_invalid") from None
    request = FallbackRequest.from_dict(value)
    canonical = canonical_request_body(request)
    if int(match.group("visible_pr")) != request.pr or body not in {
        canonical, canonical + "\n",
    }:
        raise ContractError("request_invalid")
    return request

def canonical_request_body(request: FallbackRequest) -> str:
    payload = json.dumps(
        request.to_dict(), ensure_ascii=True, separators=(",", ":"), sort_keys=True,
    )
    return (
        f"{VISIBLE_PREFIX}{request.pr}\n"
        f"{HIDDEN_MARKER}{payload} -->"
    )

def classify_event(event_name: object, event: object) -> RequestClassification:
    texts = event_text_fields(event_name, event)
    reserved = any(
        VISIBLE_PREFIX in value or HIDDEN_MARKER in value for value in texts
    )
    if not reserved:
        return RequestClassification("interactive", "", None)
    if event_name != "issue_comment" or len(texts) != 1:
        return RequestClassification("invalid", "request_invalid", None)
    try:
        request = parse_request_body(texts[0])
    except ContractError as error:
        return RequestClassification("invalid", str(error), None)
    return RequestClassification("managed_candidate", "", request)
```

- [ ] **Step 4: Run the parser cases and verify green.**

```bash
rtk pytest -q tests/test_claude_rollout_fallback.py -k 'request or classifier'
```

Expected: all parser/classifier cases pass.

- [ ] **Step 5: Write failing admission and action-contract cases.** Use one valid bundle, then mutate one authenticated field at a time through the listed trust boundaries.

```python
def test_admission_binds_failure_before_provider_or_budget():
    admission = admit(valid_bundle())
    assert admission.request_comment_id == 901
    assert admission.automatic_comment_id == 887
    assert admission.request.original_run_id == 34549275027
    assert admission.automatic_state_sha256 == sha256(AUTOMATIC_STATE_BYTES).hexdigest()

@pytest.mark.parametrize("change", [
    "event_not_created", "comment_bytes_changed", "actor_not_collaborator",
    "pr_closed", "fork_head", "head_changed", "base_changed", "wrong_attempt",
    "request_before_failure",
    "wrong_release", "provider_entered", "budget_claimed", "duplicate_request",
    "duplicate_state",
    "existing_fallback", "job_overflow", "comment_horizon_exceeded",
])
def test_admission_fails_closed(change):
    with pytest.raises(ContractError, match="^[a-z_]+$"):
        admit(mutated_bundle(change))
```

- [ ] **Step 6: Run admission cases and verify red.**

```bash
rtk pytest -q tests/test_claude_rollout_fallback.py -k 'admission or action_contract'
```

Expected: `EvidenceBundle`, `admit`, or the action metadata is missing.

- [ ] **Step 7: Implement pure admission and the bounded transport.** `admit` must compare server objects before constructing its immutable result; the composite exposes only the twelve closed outputs.

```python
def admit(bundle: EvidenceBundle) -> FallbackAdmission:
    request = require_managed_created_event(bundle.event_name, bundle.event)
    require_exact_comment(bundle.comment, bundle.event, request)
    require_exact_open_same_repository_pr(bundle.pull_request, request)
    require_exact_automatic_run(bundle.original_run, request)
    require_causal_order(bundle.original_run, bundle.comment)
    require_failed_before_provider(bundle.original_jobs)
    request_comment_id = positive_id(
        bundle.comment["id"], "request_comment_id_invalid",
    )
    require_unique_request(bundle.issue_comments, request_comment_id, request)
    comment_id, state_bytes = require_automatic_failure_state(
        bundle.issue_comments, request,
    )
    require_no_successful_fallback(bundle.issue_comments, request)
    return FallbackAdmission(
        request_comment_id=request_comment_id,
        request=request,
        automatic_comment_id=comment_id,
        automatic_state_sha256=hashlib.sha256(state_bytes).hexdigest(),
    )
```

```yaml
inputs:
  github-token:
    required: true
outputs:
  route:
    value: ${{ steps.verify.outputs.route }}
  reason:
    value: ${{ steps.verify.outputs.reason }}
  request-comment-id:
    value: ${{ steps.verify.outputs.request-comment-id }}
  request-nonce:
    value: ${{ steps.verify.outputs.request-nonce }}
  expected-head-sha:
    value: ${{ steps.verify.outputs.expected-head-sha }}
  expected-base-sha:
    value: ${{ steps.verify.outputs.expected-base-sha }}
  original-run-id:
    value: ${{ steps.verify.outputs.original-run-id }}
  original-run-attempt:
    value: ${{ steps.verify.outputs.original-run-attempt }}
  release-commit:
    value: ${{ steps.verify.outputs.release-commit }}
  managed-diff-sha256:
    value: ${{ steps.verify.outputs.managed-diff-sha256 }}
  automatic-comment-id:
    value: ${{ steps.verify.outputs.automatic-comment-id }}
  automatic-state-sha256:
    value: ${{ steps.verify.outputs.automatic-state-sha256 }}
runs:
  using: composite
  steps:
    - id: collect
      shell: bash
    - id: verify
      shell: bash
```

The two shell steps use `umask 077`, a mode-0700 temporary directory, exact `contract.py plan` endpoints, ten explicit comment-page iterations, private regular response files, `contract.py verify`, and a cleanup trap. They never evaluate an API value as shell source.

- [ ] **Step 8: Run focused tests and verify green.**

```bash
rtk pytest -q tests/test_claude_rollout_fallback.py tests/test_action_pins.py
rtk git diff --check
```

Expected: both suites and the diff check pass.

- [ ] **Step 9: Commit the independently testable admission unit.**

```bash
rtk git add .github/actions/claude-rollout-fallback tests/test_claude_rollout_fallback.py
rtk git commit -m 'feat(claude): authenticate rollout fallback requests'
```

### Task 2: Typed fallback route in the shared Claude budget

**Files:**

- Modify: `.github/actions/review-invocation-budget/review_invocation_budget.py`
- Modify: `.github/actions/review-invocation-budget/action.yml`
- Modify: `tests/test_review_invocation_budget.py`
- Modify: `tests/test_review_invocation_budget_action.py`

**Interfaces:**

- Raise the serialized ledger to `SCHEMA = 2`; continue reading schema 1 and serialize schema 2 on the next legitimate mutation.
- Add `InvocationRoute(kind, request_comment_id, request_nonce, original_run_id, original_run_attempt, expected_base_sha, release_commit, managed_diff_sha256, automatic_comment_id, automatic_state_sha256)`.
- Exact `kind` values are `automatic`, `authorized_override`, and `default_branch_rollout_fallback`. Only the fallback kind carries the nine evidence fields; the other kinds serialize only their `kind` key.
- Schema-1 migration derives `automatic` from `pull_request` and `authorized_override` from `workflow_dispatch`; every other legacy event fails closed.
- Add optional action input `invocation-route-json` with default `{"kind":"automatic"}` for source compatibility during staged edits; every v1.78 workflow call passes an explicit route value.
- Add required persisted `route: InvocationRoute` to `Invocation`. Add `route` to `ClaimRequest` and `FinalizeRequest` with `field(default_factory=InvocationRoute.automatic)` so existing pure-Python callers remain automatic; every v1.78 action/workflow call still supplies explicit JSON. Claim and finalize require the same serialized route.
- For fallback claims only, the selected run event is `issue_comment`, caller path is `.github/workflows/claude.yml`, and the invocation HEAD is the freshly fetched pull-request HEAD rather than the default-branch run HEAD. Existing `referenced_workflow_sha` records the installed driver commit from the consumer default branch; route `release_commit` separately records the target commit being rolled out.
- The route still consumes an automatic round. `force_review` must be false, no override event may be consumed, and the existing duplicate-head/round/usage/finalization behavior is unchanged.

Target route serialization:

```python
@dataclass(frozen=True)
class InvocationRoute:
    kind: Literal[
        "automatic", "authorized_override", "default_branch_rollout_fallback"
    ]
    request_comment_id: int | None = None
    request_nonce: str | None = None
    original_run_id: int | None = None
    original_run_attempt: int | None = None
    expected_base_sha: str | None = None
    release_commit: str | None = None
    managed_diff_sha256: str | None = None
    automatic_comment_id: int | None = None
    automatic_state_sha256: str | None = None

    @classmethod
    def automatic(cls) -> "InvocationRoute":
        return cls(kind="automatic")
```

Required migration and accounting assertions:

```python
legacy = LedgerState.from_dict(schema_one_ledger())
assert [item.route.kind for item in legacy.invocations] == ["automatic"]
assert legacy.to_dict()["schema"] == 2

claimed = claim(empty_state(), fallback_claim(), fallback_provenances())
assert claimed.allow_invocation is True
assert claimed.state.invocations[-1].round_number == 1
assert claimed.state.invocations[-1].route.kind == "default_branch_rollout_fallback"
assert claimed.state.invocations[-1].caller_event == "issue_comment"
```

- [ ] **Step 1: Write failing schema migration and exact-route tests.** Cover both legal schema-1 events and exact schema-2 route key sets.

```python
@pytest.mark.parametrize(("event", "kind"), [
    ("pull_request", "automatic"),
    ("workflow_dispatch", "authorized_override"),
])
def test_schema_one_invocations_gain_typed_route(event, kind):
    raw = schema_one_ledger(caller_event=event)
    state = LedgerState.from_dict(raw)
    assert state.invocations[0].route.kind == kind
    assert state.to_dict()["schema"] == 2

def test_fallback_route_requires_every_evidence_field():
    raw = fallback_route_dict()
    raw.pop("automatic_state_sha256")
    with pytest.raises(BudgetStateError, match="^invocation_route_invalid$"):
        InvocationRoute.from_dict(raw)
```

- [ ] **Step 2: Run the schema cases and verify red.**

```bash
rtk pytest -q tests/test_review_invocation_budget.py -k 'schema_one or fallback_route'
```

Expected: schema 2 and `InvocationRoute` assertions fail.

- [ ] **Step 3: Implement exact route serialization and migration.** Pass the ledger schema into invocation decoding and always serialize the current schema.

```python
SCHEMA = 2

@classmethod
def from_legacy_event(cls, event: str) -> "InvocationRoute":
    kinds = {
        "pull_request": "automatic",
        "workflow_dispatch": "authorized_override",
    }
    if event not in kinds:
        raise BudgetStateError("invocation_route_invalid")
    return cls(kind=kinds[event])

def decode_invocation_route(
    value: object, *, schema: int, caller_event: str,
) -> InvocationRoute:
    if schema == 1:
        return InvocationRoute.from_legacy_event(caller_event)
    if schema == 2:
        return InvocationRoute.from_dict(value)
    raise BudgetStateError("schema_invalid")
```

Change `Invocation.from_dict(value, *, schema)` to use the current v1 key set when `schema == 1`, the same set plus `route` when `schema == 2`, and `decode_invocation_route` for the new field. `InvocationRoute.from_dict` accepts exactly `{"kind"}` for automatic/override and exactly the ten dataclass field names for fallback, validates positive IDs, 32-hex nonce, 40-hex SHAs, and 64-hex digests, and rejects non-null evidence on the two simple routes.

- [ ] **Step 4: Run the schema cases and verify green.**

```bash
rtk pytest -q tests/test_review_invocation_budget.py -k 'schema or route'
```

Expected: migration and serialization cases pass without changing existing budget decisions.

- [ ] **Step 5: Write failing fallback provenance and accounting cases.** Require issue-comment provenance, separate installed/target commits, and automatic-round use.

```python
def test_fallback_claim_uses_pr_head_and_one_automatic_round():
    result = claim(empty_state(), fallback_claim(), fallback_provenances())
    invocation = result.state.invocations[-1]
    assert result.allow_invocation is True
    assert invocation.head_sha == FALLBACK_HEAD
    assert invocation.caller_event == "issue_comment"
    assert invocation.referenced_workflow_sha == DRIVER_COMMIT
    assert invocation.route.release_commit == TARGET_RELEASE_COMMIT
    assert invocation.round_number == 1
    assert invocation.override_event_id is None

@pytest.mark.parametrize("change", [
    "force_review", "wrong_caller", "wrong_pr_head", "wrong_base",
    "wrong_request", "wrong_target_release", "wrong_driver", "wrong_state_digest",
    "non_claude_reviewer",
])
def test_fallback_provenance_rejects_mismatches(change):
    transition = claim(empty_state(), fallback_claim(change), fallback_provenances())
    assert (transition.allow_invocation, transition.decision) == (False, "state_invalid")
```

- [ ] **Step 6: Run fallback provenance cases and verify red.**

```bash
rtk pytest -q tests/test_review_invocation_budget.py -k fallback
```

Expected: issue-comment provenance is refused before route-aware validation exists.

- [ ] **Step 7: Implement route-aware claim/finalize provenance.** Select the expected event from the typed route and bind fallback HEAD/base to the freshly fetched pull request.

```python
def expected_caller_event(request: ClaimRequest | FinalizeRequest) -> str:
    return {
        "automatic": "pull_request",
        "authorized_override": "workflow_dispatch",
        "default_branch_rollout_fallback": "issue_comment",
    }[request.route.kind]

def provenance_head(provenance: RunProvenance, request: ClaimRequest) -> str:
    if request.route.kind == "default_branch_rollout_fallback":
        if request.reviewer != "claude" or request.force_review:
            raise BudgetStateError("invocation_route_invalid")
        return request.head_sha
    return provenance.head_sha
```

Update `_validate_provenance_identity`, `_validate_one_provenance`, and `_run_provenances` to call these selectors. Require caller `.github/workflows/claude.yml` and nested review `referenced_workflow_sha == DRIVER_COMMIT`; compare route target release only with the admitted request, never with the driver commit.

- [ ] **Step 8: Write and implement the composite-action transport cases.** Add the new input and stage it as JSON data rather than shell source.

```yaml
inputs:
  invocation-route-json:
    required: false
    default: '{"kind":"automatic"}'
```

```python
payload["invocation_route_json"] = os.environ["INVOCATION_ROUTE_JSON"]
route = InvocationRoute.from_dict(json.loads(payload["invocation_route_json"]))
request = replace(request, route=route)
```

The harness must cover schema-1 comment migration, exact base fetch, duplicate-head reuse, one finalized call, malformed route JSON, and claim/finalize route drift.

- [ ] **Step 9: Run focused budget tests and verify green.**

```bash
rtk pytest -q tests/test_review_invocation_budget.py tests/test_review_invocation_budget_action.py
rtk git diff --check
```

Expected: both suites and diff validation pass.

- [ ] **Step 10: Commit the independently testable budget unit.**

```bash
rtk git add .github/actions/review-invocation-budget tests/test_review_invocation_budget.py tests/test_review_invocation_budget_action.py
rtk git commit -m 'feat(review-budget): record Claude fallback provenance'
```

### Task 3: Canonical Claude review fallback mode

**Files:**

- Modify: `.github/workflows/claude-code-review.yml`
- Modify: `tests/test_claude_workflow_validation.py`
- Modify: `tests/test_review_workflow_logic.py`

**Interfaces:**

- Declare the eight optional string inputs in the Contract Map with empty-string defaults. Define fallback mode as all eight nonempty and normal mode as all eight empty; every partial combination fails before diff preparation.
- In fallback mode, run the release-owned admission action again using `fallback_request_comment_id`, compare every admission scalar to the workflow input, and check the live pull-request HEAD/base both before `prepare-review-diff` and immediately before publication.
- Require `prepare-review-diff` to recompute a full diff whose SHA-256 exactly equals `fallback_managed_diff_sha256` before the budget claim.
- Caller validation remains mandatory and unchanged. The nested route succeeds because `github.workflow_ref` identifies the default-branch consumer `.github/workflows/claude.yml`; it receives no bypass flag.
- Pass the exact `default_branch_rollout_fallback` route JSON, including the freshly admitted automatic comment ID/state digest, to both budget claim and finalize. Normal and force-review paths pass `automatic` and `authorized_override` respectively.
- The central managed caller passes `review_mode: request`. The existing policy resolver must freshly observe the rollout PR's single `review:request` label and no `review:skip` label before the nested review job can reach admission, budget, or provider steps.
- Extend schema-3 state with one exact `failure_reason` field on new failure records and one exact `route` object on fallback success records. Normal success has neither field; legacy schema-3 key sets remain readable.
- The fallback route object has exactly `route`, `request_comment_id`, `original_failed_run_id`, `reviewed_base_sha`, `release_commit`, and `managed_diff_sha256`. Its route value is `default_branch_rollout_fallback`.

Target fallback state fragment:

```json
{
  "route": {
    "managed_diff_sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
    "original_failed_run_id": 34549275027,
    "release_commit": "444a7347aee169ed178aae80e8bd8d10eca52e02",
    "request_comment_id": 901,
    "reviewed_base_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "route": "default_branch_rollout_fallback"
  }
}
```

Required behavior assertions:

```python
state = _posted_state(fallback_success_body)
assert state["attempt_status"] == "success"
assert state["review_execution"] == "performed"
assert state["route"]["original_failed_run_id"] == 34549275027
assert state["route"]["reviewed_base_sha"] == "bb" * 20

failed = _posted_state(automatic_mismatch_body)
assert failed["failure_reason"] == "workflow_validation_mismatch"
assert failed["review_execution"] == "not_performed"
```

- [ ] **Step 1: Write failing input, admission, diff, and caller-validation cases.** Parameterize every partial input count and each immutable-coordinate mutation.

```python
@pytest.mark.parametrize("present", range(1, 8))
def test_partial_fallback_inputs_fail_before_diff(present):
    result = run_fallback_mode_step(FALLBACK_INPUTS[:present])
    assert result.returncode != 0
    assert not result.outputs

def test_fallback_requires_exact_recomputed_full_diff():
    result = run_prebudget_gate(
        diff_mode="full", computed_hash="12" * 32, requested_hash="34" * 32,
    )
    assert (result.returncode != 0, result.budget_entered) == (True, False)

def test_fallback_does_not_bypass_caller_validation():
    result = _preflight(tmp_path, currentBlob="11" * 20, defaultBlob="22" * 20)
    assert result["outputs"] == {
        "allowed": "false", "reason": "workflow_validation_mismatch",
    }

def test_fallback_still_requires_live_request_policy():
    workflow = _load("claude-code-review.yml")
    assert workflow["jobs"]["claude-review"]["if"] == (
        "needs.check-enabled.outputs.enabled == 'true' && "
        "needs.check-enabled.outputs.policy_run == 'true'"
    )
```

- [ ] **Step 2: Run route-entry cases and verify red.**

```bash
rtk pytest -q tests/test_claude_workflow_validation.py tests/test_review_workflow_logic.py -k fallback
```

Expected: fallback inputs and gates are absent.

- [ ] **Step 3: Add exact inputs, mode resolution, repeated admission, and prebudget gates.** The mode resolver writes only `normal` or `fallback`; partial input sets fail the job.

```yaml
fallback_request_comment_id:
  type: string
  required: false
  default: ''
fallback_request_nonce:
  type: string
  required: false
  default: ''
fallback_expected_head_sha:
  type: string
  required: false
  default: ''
fallback_expected_base_sha:
  type: string
  required: false
  default: ''
fallback_original_run_id:
  type: string
  required: false
  default: ''
fallback_original_run_attempt:
  type: string
  required: false
  default: ''
fallback_release_commit:
  type: string
  required: false
  default: ''
fallback_managed_diff_sha256:
  type: string
  required: false
  default: ''
```

```python
values = json.loads(os.environ["FALLBACK_INPUTS_JSON"])
count = sum(isinstance(value, str) and value != "" for value in values.values())
if count not in {0, 8}:
    raise SystemExit("fallback_inputs_partial")
mode = "fallback" if count == 8 else "normal"
with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as stream:
    stream.write(f"mode={mode}\n")
```

Change the existing diff action binding so an admitted fallback always computes the full review diff:

```yaml
force-full: ${{ (inputs.force_review || steps.fallback-mode.outputs.mode == 'fallback') && 'true' || 'false' }}
```

In fallback mode, invoke `$/.github/actions/claude-rollout-fallback`, require route `managed`, compare its eight request outputs with the eight workflow inputs, retain its two automatic-state outputs for the route JSON, fetch the live pull request, and require exact HEAD/base. After `prepare-review-diff`, require mode `full` and hash equality before caller validation and budget claim.

- [ ] **Step 4: Run route-entry cases and verify green.**

```bash
rtk pytest -q tests/test_claude_workflow_validation.py tests/test_review_workflow_logic.py -k 'fallback and not state'
```

Expected: partial/stale/diff/caller cases pass; existing caller-validation cases remain unchanged.

- [ ] **Step 5: Write failing budget-route and canonical-state cases.** Assert byte-identical claim/finalize route JSON and exact schema-3 key variants.

```python
def test_fallback_claim_and_finalize_use_identical_route_json():
    workflow = _load("claude-code-review.yml")
    claim = workflow_step(workflow, "Claim Claude review budget")
    finalize = workflow_step(workflow, "Finalize Claude review budget")
    assert claim["with"]["invocation-route-json"] == finalize["with"]["invocation-route-json"]

def test_fallback_success_publishes_authenticated_route():
    state = _posted_state(fallback_success_body())
    assert set(state["route"]) == {
        "managed_diff_sha256", "original_failed_run_id", "release_commit",
        "request_comment_id", "reviewed_base_sha", "route",
    }
    assert state["route"]["route"] == "default_branch_rollout_fallback"

def test_mismatch_failure_publishes_authenticated_reason():
    state = _posted_state(automatic_mismatch_body())
    assert (state["failure_reason"], state["review_execution"]) == (
        "workflow_validation_mismatch", "not_performed",
    )
```

- [ ] **Step 6: Run state/publication cases and verify red.**

```bash
rtk pytest -q tests/test_claude_workflow_validation.py tests/test_review_workflow_logic.py -k 'route_json or authenticated_route or authenticated_reason'
```

Expected: current schema-3 writers lack `failure_reason` and `route`.

- [ ] **Step 7: Implement route-aware budget calls and exact state variants.** Build route JSON once in a mode step and feed the same output to claim/finalize.

```javascript
const state = {
  schema: 3,
  reviewer: 'claude',
  pr: issueNumber,
  run_id: runId,
  run_attempt: runAttempt,
  attempt_head: attemptHead,
  successful_head: failed ? preservedHead : attemptHead,
  attempt_status: failed ? 'failure' : 'success',
  diff_mode: stateDiffMode,
  review_execution: execution,
  full_diff_sha256: stateDiffHash,
  quality_schema: 1,
  accepted_count: acceptedCount,
  filtered_count: filteredCount,
  normalized_count: normalizedCount,
  filtered_max_severity: filteredMaxSeverity,
};
if (failed) state.failure_reason = effectiveFailureReason;
if (!failed && fallbackMode) state.route = fallbackRoute;
```

Accept only legacy-without-execution, current normal, current failure, and current fallback exact key sets in both collect-context and upsert parsers. Require failure reason only on failure and route only on fallback success. Fetch and compare live HEAD/base again immediately before the sticky-comment mutation.

- [ ] **Step 8: Exercise success, failure, idempotency, and regressions.**

```bash
rtk pytest -q tests/test_claude_workflow_validation.py tests/test_review_workflow_logic.py tests/test_review_invocation_budget.py tests/test_review_invocation_budget_action.py
rtk git diff --check
```

Expected: one-call/finalized CLEAN, blocking findings, provider/candidate failures, finalized retry, automatic review, and force-review cases all pass.

- [ ] **Step 9: Commit the independently testable canonical-review unit.**

```bash
rtk git add .github/workflows/claude-code-review.yml tests/test_claude_workflow_validation.py tests/test_review_workflow_logic.py
rtk git commit -m 'feat(claude): publish authenticated fallback reviews'
```

### Task 4: Default-branch router and consumer permission contract

**Files:**

- Modify: `.github/workflows/claude.yml`
- Modify: `examples/baseline-workflows/.github/workflows/claude.yml`
- Modify: `scripts/workflow-catalog.json`
- Modify: `tests/test_canonical_workflow_tree.py`
- Modify: `tests/test_action_pins.py`

**Interfaces:**

- Central jobs are `check-enabled`, `classify-request`, `claude`, `managed-rollout-review`, and `skipped`.
- `classify-request` has `actions: read`, `contents: read`, `issues: read`, and `pull-requests: read`; it calls `$/.github/actions/claude-rollout-fallback`.
- Classifier output `interactive` permits only the existing interactive conditions, `managed` requires a complete admission, and `invalid` runs neither provider path while surfacing one bounded diagnostic.
- `claude` keeps the current provider and read-only permission set, and its condition explicitly excludes the exact managed marker prefix even if the body contains `@claude`.
- `managed-rollout-review` is mutually exclusive with `claude`, has `actions: read`, `contents: read`, `issues: read`, `pull-requests: write`, and `id-token: write`, and calls `$/.github/workflows/claude-code-review.yml` with `pr_number` from the issue event, all eight admission outputs, and the existing Claude secret.
- The managed nested call fixes `review_mode` to `request`; the existing policy resolver therefore requires the live `review:request` label and refuses `review:skip`, conflict, draft, closed, unsafe-head, or disabled-workflow states before provider access.
- The baseline consumer caller raises only its ceiling from `pull-requests: read` to `pull-requests: write`; it still points to an immutable `__AUTOMATION_COMMIT__` and passes only `CLAUDE_CODE_OAUTH_TOKEN`.
- The catalog records the changed caller permission contract without adding a second consumer workflow or trigger.

Target mutual-exclusion assertions:

```python
central = load_yaml(ROOT / ".github/workflows/claude.yml")
assert central["jobs"]["managed-rollout-review"]["uses"] == (
    "$/.github/workflows/claude-code-review.yml"
)
assert central["jobs"]["claude"]["permissions"]["pull-requests"] == "read"
assert central["jobs"]["managed-rollout-review"]["permissions"]["pull-requests"] == "write"
assert baseline["jobs"]["claude"]["permissions"]["pull-requests"] == "write"
```

- [ ] **Step 1: Write failing router, permission, and mutual-exclusion tests.** Assert the exact job set and evaluate representative classifications.

```python
def test_claude_router_has_closed_jobs_and_permissions():
    central = load_yaml(ROOT / ".github/workflows/claude.yml")
    assert set(central["jobs"]) == {
        "check-enabled", "classify-request", "claude",
        "managed-rollout-review", "skipped",
    }
    assert set(central["jobs"]["claude"]["needs"]) == {
        "check-enabled", "classify-request",
    }
    assert central["jobs"]["claude"]["permissions"] == CLAUDE_INTERACTIVE_PERMISSIONS
    assert central["jobs"]["managed-rollout-review"]["permissions"] == CLAUDE_REVIEW_PERMISSIONS
    assert central["jobs"]["managed-rollout-review"]["with"]["review_mode"] == "request"
    assert central["jobs"]["managed-rollout-review"]["with"]["pr_number"] == (
        "${{ github.event.issue.number }}"
    )

@pytest.mark.parametrize(("classification", "interactive", "managed"), [
    ("interactive", True, False),
    ("managed", False, True),
    ("invalid", False, False),
])
def test_claude_routes_are_mutually_exclusive(classification, interactive, managed):
    assert route_jobs(classification) == {
        "claude": interactive,
        "managed-rollout-review": managed,
    }
```

- [ ] **Step 2: Run router cases and verify red.**

```bash
rtk pytest -q tests/test_canonical_workflow_tree.py -k 'claude and (router or mutually or permission)'
```

Expected: central workflow lacks classifier/managed jobs and the consumer ceiling is read-only.

- [ ] **Step 3: Implement the central router.** Preserve `check-enabled`; classify only when enabled; expose all ten admitted evidence outputs, feed the eight request fields into the nested workflow, and let that workflow's repeated admission derive the two automatic-state fields for its budget route.

```yaml
classify-request:
  needs: check-enabled
  if: needs.check-enabled.outputs.enabled == 'true'
  runs-on: ubuntu-latest
  permissions:
    actions: read
    contents: read
    issues: read
    pull-requests: read
  outputs:
    route: ${{ steps.classify.outputs.route }}
    reason: ${{ steps.classify.outputs.reason }}
    request-comment-id: ${{ steps.classify.outputs.request-comment-id }}
    request-nonce: ${{ steps.classify.outputs.request-nonce }}
    expected-head-sha: ${{ steps.classify.outputs.expected-head-sha }}
    expected-base-sha: ${{ steps.classify.outputs.expected-base-sha }}
    original-run-id: ${{ steps.classify.outputs.original-run-id }}
    original-run-attempt: ${{ steps.classify.outputs.original-run-attempt }}
    release-commit: ${{ steps.classify.outputs.release-commit }}
    managed-diff-sha256: ${{ steps.classify.outputs.managed-diff-sha256 }}
    automatic-comment-id: ${{ steps.classify.outputs.automatic-comment-id }}
    automatic-state-sha256: ${{ steps.classify.outputs.automatic-state-sha256 }}
  steps:
    - id: classify
      uses: $/.github/actions/claude-rollout-fallback
      with:
        github-token: ${{ github.token }}
    - name: Report invalid managed request
      if: steps.classify.outputs.route == 'invalid'
      shell: bash
      env:
        REASON: ${{ steps.classify.outputs.reason }}
      run: |
        set -euo pipefail
        [[ "$REASON" =~ ^[a-z_]+$ ]]
        printf '## Claude request declined\n\nReason: `%s`\n' "$REASON" >> "$GITHUB_STEP_SUMMARY"

claude:
  needs: [check-enabled, classify-request]
  if: |
    needs.classify-request.outputs.route == 'interactive' && (
      (github.event_name == 'issue_comment' &&
        contains(fromJson('["OWNER","MEMBER","COLLABORATOR"]'), github.event.comment.author_association) &&
        contains(github.event.comment.body, '@claude')) ||
      (github.event_name == 'pull_request_review_comment' &&
        contains(fromJson('["OWNER","MEMBER","COLLABORATOR"]'), github.event.comment.author_association) &&
        contains(github.event.comment.body, '@claude')) ||
      (github.event_name == 'pull_request_review' &&
        contains(fromJson('["OWNER","MEMBER","COLLABORATOR"]'), github.event.review.author_association) &&
        contains(github.event.review.body, '@claude')) ||
      (github.event_name == 'issues' &&
        contains(fromJson('["OWNER","MEMBER","COLLABORATOR"]'), github.event.issue.author_association) &&
        (contains(github.event.issue.body, '@claude') || contains(github.event.issue.title, '@claude')))
    )

managed-rollout-review:
  needs: [check-enabled, classify-request]
  if: needs.classify-request.outputs.route == 'managed'
  permissions:
    actions: read
    contents: read
    id-token: write
    issues: read
    pull-requests: write
  uses: $/.github/workflows/claude-code-review.yml
  with:
    pr_number: ${{ github.event.issue.number }}
    review_mode: request
    fallback_request_comment_id: ${{ needs.classify-request.outputs.request-comment-id }}
    fallback_request_nonce: ${{ needs.classify-request.outputs.request-nonce }}
    fallback_expected_head_sha: ${{ needs.classify-request.outputs.expected-head-sha }}
    fallback_expected_base_sha: ${{ needs.classify-request.outputs.expected-base-sha }}
    fallback_original_run_id: ${{ needs.classify-request.outputs.original-run-id }}
    fallback_original_run_attempt: ${{ needs.classify-request.outputs.original-run-attempt }}
    fallback_release_commit: ${{ needs.classify-request.outputs.release-commit }}
    fallback_managed_diff_sha256: ${{ needs.classify-request.outputs.managed-diff-sha256 }}
  secrets:
    CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
```

Route `invalid` writes its bounded reason to the run summary and reaches no credentialed job. Tests require the displayed twelve-output map exactly, the interactive job's two `needs`, and the full existing actor/event clauses, so a future action/job name drift fails before rollout.

- [ ] **Step 4: Raise the consumer ceiling and update the catalog.** Change only the Claude consumer job's pull-request permission and the matching parsed catalog contract.

```yaml
jobs:
  claude:
    permissions:
      actions: read
      contents: read
      id-token: write
      issues: read
      pull-requests: write
```

```python
CLAUDE_COMMAND_PERMISSIONS = {
    "actions": "read",
    "contents": "read",
    "id-token": "write",
    "issues": "read",
    "pull-requests": "write",
}
CLAUDE_INTERACTIVE_PERMISSIONS = {
    **CLAUDE_COMMAND_PERMISSIONS,
    "pull-requests": "read",
}
```

Use `CLAUDE_INTERACTIVE_PERMISSIONS` for the central `claude` job assertion and `CLAUDE_COMMAND_PERMISSIONS` for the baseline caller ceiling. Keep the managed job on the existing `CLAUDE_REVIEW_PERMISSIONS` set.

- [ ] **Step 5: Run router and source-pin checks.**

```bash
rtk pytest -q tests/test_canonical_workflow_tree.py tests/test_action_pins.py tests/test_claude_rollout_fallback.py
```

Expected: exact triggers, local `$/` references, third-party full pins, input/secret mappings, and route exclusivity pass.

- [ ] **Step 6: Run actionlint and diff validation.**

```bash
rtk proxy /home/jhw/ai/opencode/projects/automation/.review/releases/v1.74/tools/actionlint-1.7.12 .github/workflows/claude.yml .github/workflows/claude-code-review.yml examples/baseline-workflows/.github/workflows/claude.yml
rtk git diff --check
```

Expected: actionlint and diff validation pass.

- [ ] **Step 7: Commit the independently testable router unit.**

```bash
rtk git add .github/workflows/claude.yml examples/baseline-workflows/.github/workflows/claude.yml scripts/workflow-catalog.json tests/test_canonical_workflow_tree.py tests/test_action_pins.py
rtk git commit -m 'feat(claude): route managed reviews from default branch'
```

### Task 5: Read-only fleet verifier and private receipt

**Files:**

- Create: `scripts/verify_claude_rollout_fallback.py`
- Create: `tests/test_verify_claude_rollout_fallback.py`
- Modify: `scripts/rollout_workflow_fleet.py`
- Modify: `tests/test_rollout_workflow_fleet.py`

**Interfaces:**

- Promote only pure reusable fleet helpers needed by the verifier: `render_rollout_plan(snapshot: RepositorySnapshot, bundle: ReleaseBundle, repo: str, *, bootstrap: bool) -> RenderPlan`, `validate_commit_tree(snapshot: RepositorySnapshot, expected_head: str, expected_base: str, plan: RenderPlan) -> None`, and `attest_pull_request(snapshot: RepositorySnapshot, release_ref: str, release_commit: str, expected_head: str, changed_paths: tuple[str, ...], request: VerificationRequest) -> PullRequest`. Existing rollout command behavior remains byte-for-byte compatible outside symbol naming.
- `VerificationRequest(automation_root, release_ref, remote, repository, pr, expected_head, expected_base, output)` is the CLI request.
- `verify(request: VerificationRequest, evidence_provider: EvidenceProvider) -> dict[str, object]` materializes the exact release, renders the selected consumer profile at the expected base, validates the single-child commit tree and exact open pull request, then validates request, automatic run/jobs/state, fallback run/jobs/state, budget ledger, and current required checks.
- The target `release_commit` comes from the rendered review branch. The fallback `driver_commit` comes from the consumer default-branch caller pin and must match the nested fallback run's referenced workflow SHA. These commits are expected to differ on the real caller-changing canary.
- `EvidenceProvider` performs bounded GitHub REST and trusted-origin Git reads. Tests use a deterministic fake implementing the same methods; the verifier never imports or executes consumer files.
- Remote enumeration limits are exact: at most ten 100-item workflow-run pages, one 100-job page per selected attempt, ten 100-annotation pages per selected check run, ten 100-comment pages, ten 100-check-run pages, and ten 100-item required-context/ruleset pages. A full final permitted page is an overflow error because completeness was not proven.
- CLI arguments are exactly `--automation-root`, `--release-ref`, `--remote`, `--repository`, `--pr`, `--expected-head`, `--expected-base`, and `--output`; the output path must be outside `automation_root`.
- Production invocation uses Python isolated/no-site/no-bytecode flags `-I -S -B`. The script performs only standard-library and fixed-tool reads until the driver checkout passes exact-root verification, then explicitly inserts that verified root for its delayed project imports.
- Output is canonical compact JSON plus LF. Require a current-user-owned, non-symlink mode-0700 parent outside `automation_root`; reject every pre-existing destination including a broken symlink; write and fsync an adjacent O_EXCL temporary regular file, chmod it 0600 independently of umask, atomically publish it with a same-directory no-replace hard link, then lstat and verify current UID/mode before success.
- The fallback updates the existing sticky comment, so the pre-fallback failure body is no longer live afterward. The receipt labels its digest `admitted_state_sha256`; the verifier binds that digest through the finalized budget route and independently proves the mismatch from the original failed job's exact error annotation and skipped provider step.
- `automation_root` must be a clean exact checkout at the installed `driver_commit` derived from the consumer default-branch caller. The verifier imports local rollout modules only after `/usr/bin/git` verifies HEAD, tracked bytes, untracked-file absence, regular-file modes, and required module paths against that commit.
- Receipt `verifier_commit` equals that verified `driver_commit`; top-level `release_commit` remains the distinct target rollout commit. The real-boundary canary rejects equality between those two fields.
- Private verifier helpers have fixed signatures: `parse_default_caller_pin(document: object) -> str`, `load_verified_modules(root: Path) -> VerifiedModules`, `verify_fleet_request(request: VerificationRequest, evidence: EvidenceProvider, modules: VerifiedModules) -> dict[str, object]`, `verify_automatic_failure(request: VerificationRequest, evidence: EvidenceProvider) -> dict[str, object]`, `verify_fallback_success(request: VerificationRequest, evidence: EvidenceProvider, automatic: dict[str, object], driver_commit: str) -> dict[str, object]`, `require_required_checks_clean(checks: tuple[dict[str, object], ...]) -> None`, and `build_receipt(request: VerificationRequest, fleet: dict[str, object], automatic: dict[str, object], fallback: dict[str, object], driver_commit: str) -> dict[str, object]`. `VerifiedModules` is a frozen dataclass holding the imported release-bundle, inventory, and promoted rollout helper modules.
- Filesystem helpers are `git_stdout(root: Path, *args: str) -> str`, `canonical_json_bytes(payload: dict[str, object]) -> bytes`, `require_private_output_parent(path: Path) -> None`, and `require_private_regular_owned(path: Path) -> None`; `git_stdout` uses absolute `/usr/bin/git`, `GIT_OPTIONAL_LOCKS=0`, no prompts/askpass, no replacement objects, and disabled system/global configuration. Every failure becomes a bounded `VerificationError` without remote body text.

Receipt schema 1 has these exact top-level keys:

```python
RECEIPT_KEYS = frozenset({
    "automatic", "base_sha", "effective_status", "fallback", "fleet",
    "head_sha", "managed_diff_sha256", "pr", "release_commit",
    "repository", "schema", "verifier_commit",
})
AUTOMATIC_KEYS = frozenset({
    "admitted_state_sha256", "canonical_comment_id", "reason",
    "review_execution", "run_attempt", "run_id", "status",
})
FALLBACK_KEYS = frozenset({
    "budget_status", "canonical_comment_id", "canonical_state_sha256",
    "driver_commit", "filtered_max_severity", "request_comment_id",
    "request_nonce", "review_execution", "route", "run_attempt", "run_id",
    "status",
})
FLEET_KEYS = frozenset({
    "base_branch", "changed_paths", "head_repository", "pull_request_url",
    "rollout_branch",
})
```

The outcome subrecords are exact:

```json
{
  "automatic": {
    "canonical_comment_id": 887,
    "admitted_state_sha256": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
    "reason": "workflow_validation_mismatch",
    "review_execution": "not_performed",
    "run_attempt": 1,
    "run_id": 34549275027,
    "status": "FAILED"
  },
  "effective_status": "CLEAN",
  "fallback": {
    "budget_status": "finalized",
    "canonical_comment_id": 887,
    "canonical_state_sha256": "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
    "driver_commit": "9999999999999999999999999999999999999999",
    "filtered_max_severity": "none",
    "request_comment_id": 901,
    "request_nonce": "dddddddddddddddddddddddddddddddd",
    "review_execution": "performed",
    "route": "default_branch_rollout_fallback",
    "run_attempt": 1,
    "run_id": 34550000000,
    "status": "CLEAN"
  }
}
```

- [ ] **Step 1: Write failing CLI, root-trust, and private-output tests.** Cover exact arguments and every local filesystem trust boundary.

```python
@pytest.mark.parametrize("change", [
    "bad_repository", "zero_pr", "uppercase_head", "bad_release_ref",
    "output_inside_root", "wrong_head", "dirty_tracked", "untracked_shadow",
    "symlinked_module", "wrong_driver_pin",
])
def test_local_verifier_boundary_fails_closed(change, verifier_fixture):
    result = verifier_fixture.run(change=change)
    assert result.returncode != 0
    assert not verifier_fixture.output.exists()

@pytest.mark.parametrize("change", [
    "existing_output", "broken_output_symlink", "symlinked_output_parent",
])
def test_output_path_rejection_preserves_original_node(change, verifier_fixture):
    before = verifier_fixture.install_output_guard(change)
    result = verifier_fixture.run()
    assert result.returncode != 0
    assert verifier_fixture.output_guard() == before

@pytest.mark.parametrize("mask", [0o000, 0o077, 0o777])
def test_receipt_is_private_regular_and_owned(mask, verifier_fixture):
    verifier_fixture.run_valid(umask=mask)
    observed = verifier_fixture.output.lstat()
    assert stat.S_ISREG(observed.st_mode)
    assert not verifier_fixture.output.is_symlink()
    assert observed.st_uid == os.getuid()
    assert stat.S_IMODE(observed.st_mode) == 0o600
```

- [ ] **Step 2: Run local-boundary cases and verify red.**

```bash
rtk pytest -q tests/test_verify_claude_rollout_fallback.py -k 'local or receipt_is_private'
```

Expected: verifier module and CLI are absent.

- [ ] **Step 3: Implement CLI parsing, exact-root verification, and atomic output.** Delay project imports until the standard-library root check returns `driver_commit`.

```python
def verify_source_root(root: Path, driver_commit: str) -> None:
    head = git_stdout(root, "rev-parse", "HEAD")
    status = git_stdout(root, "status", "--porcelain=v1", "--untracked-files=all")
    if head != driver_commit or status != "":
        raise VerificationError("verifier_root_invalid")
    for relative in REQUIRED_MODULE_PATHS:
        record = git_stdout(root, "ls-tree", driver_commit, "--", relative)
        if not record.startswith("100644 blob "):
            raise VerificationError("verifier_root_invalid")

def write_receipt(path: Path, payload: dict[str, object]) -> None:
    require_private_output_parent(path.parent)
    try:
        path.lstat()
    except FileNotFoundError:
        pass
    else:
        raise VerificationError("receipt_path_exists")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(canonical_json_bytes(payload))
            os.fchmod(stream.fileno(), 0o600)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            raise VerificationError("receipt_path_exists") from None
    finally:
        temporary.unlink(missing_ok=True)
    require_private_regular_owned(path)
```

The CLI first reads the default caller pin with its standard-library transport, verifies the source root at that driver commit, adds that root to `sys.path`, imports the existing rollout/release modules, and writes no output until verification is complete.

- [ ] **Step 4: Run local-boundary cases and verify green.**

```bash
rtk pytest -q tests/test_verify_claude_rollout_fallback.py -k 'local or receipt_is_private'
```

Expected: valid root/output cases pass and every mutation leaves no receipt.

- [ ] **Step 5: Write failing exact-fleet tests.** Use a real temporary Git repository and mutate one renderer-owned invariant per case.

```python
@pytest.mark.parametrize("change", [
    "extra_path", "changed_blob", "executable_mode", "merge_commit",
    "wrong_parent", "wrong_profile", "wrong_target_pin", "moved_branch",
    "duplicate_pr", "fork_head", "stale_head", "changed_pr_body",
])
def test_fleet_attestation_rejects_mutation(change, exact_rollout_fixture):
    fixture = exact_rollout_fixture.mutate(change)
    with pytest.raises(VerificationError, match="^fleet_attestation_failed$"):
        verify(fixture.request, fixture.provider)
```

- [ ] **Step 6: Promote and use the pure fleet helpers.** Rename `_render` to `render_rollout_plan`, keep the current caller as a thin wrapper, and use the existing tree/pull-request checks from the verifier.

```python
def render_rollout_plan(
    snapshot: RepositorySnapshot,
    bundle: ReleaseBundle,
    repo: str,
    *,
    bootstrap: bool,
) -> RenderPlan:
    return render_repository(
        snapshot.path, bundle.canonical, bundle.catalog,
        bundle.config.profiles[repo], bundle.ref, bundle.commit,
        set(snapshot.secret_names), set(snapshot.variable_names),
        label_names=set(snapshot.label_names), bootstrap=bootstrap,
        observed_revision=snapshot.base_sha,
    )
```

Call `validate_commit_tree(snapshot, expected_head, expected_base, plan)` and `attest_pull_request(snapshot, bundle.ref, bundle.commit, expected_head, changed_paths, request)` on freshly fetched state. Convert their bounded `CommandError` to `VerificationError("fleet_attestation_failed")` without including remote response text.

- [ ] **Step 7: Run fleet cases and verify green.**

```bash
rtk pytest -q tests/test_verify_claude_rollout_fallback.py -k fleet
rtk pytest -q tests/test_rollout_workflow_fleet.py
```

Expected: exact rendered rollout passes; all mutations and existing rollout regressions pass.

- [ ] **Step 8: Write failing evidence-chain and native-check cases.** Require one unambiguous chain and explicit failure when the automatic job is required.

```python
def test_exact_chain_emits_effective_clean_receipt(exact_evidence):
    receipt = verify(exact_evidence.request, exact_evidence.provider)
    assert receipt["automatic"]["reason"] == "workflow_validation_mismatch"
    assert receipt["automatic"]["review_execution"] == "not_performed"
    assert receipt["fallback"]["review_execution"] == "performed"
    assert receipt["fallback"]["budget_status"] == "finalized"
    assert receipt["fallback"]["driver_commit"] == DRIVER_COMMIT
    assert receipt["verifier_commit"] == DRIVER_COMMIT
    assert receipt["release_commit"] == TARGET_RELEASE_COMMIT
    assert receipt["verifier_commit"] != receipt["release_commit"]
    assert receipt["effective_status"] == "CLEAN"

def test_required_failed_automatic_check_blocks_receipt(exact_evidence):
    exact_evidence.provider.required.add("Claude Code Review / claude-review")
    with pytest.raises(VerificationError, match="^required_check_failed$"):
        verify(exact_evidence.request, exact_evidence.provider)
```

Parameterize missing/duplicate evidence, stale coordinates, provider entry, missing or altered mismatch annotation, call count zero/two, claimed ledger, wrong driver, wrong target release/diff/base, active blocking finding, and unrelated App/free-form comments.

- [ ] **Step 9: Implement the bounded provider and exact evidence verifier.** Keep network transport separate from pure evidence predicates.

```python
class EvidenceProvider(Protocol):
    def default_caller(self) -> dict[str, object]:
        raise NotImplementedError
    def pull_request(self, number: int) -> dict[str, object]:
        raise NotImplementedError
    def issue_comments(self, number: int) -> tuple[dict[str, object], ...]:
        raise NotImplementedError
    def run_attempt(self, run_id: int, attempt: int) -> dict[str, object]:
        raise NotImplementedError
    def run_jobs(self, run_id: int, attempt: int) -> tuple[dict[str, object], ...]:
        raise NotImplementedError
    def check_annotations(self, check_run_id: int) -> tuple[dict[str, object], ...]:
        raise NotImplementedError
    def required_checks(self, head_sha: str) -> tuple[dict[str, object], ...]:
        raise NotImplementedError

def verify(request: VerificationRequest, evidence: EvidenceProvider) -> dict[str, object]:
    driver_commit = parse_default_caller_pin(evidence.default_caller())
    verify_source_root(request.automation_root, driver_commit)
    modules = load_verified_modules(request.automation_root)
    fleet = verify_fleet_request(request, evidence, modules)
    automatic = verify_automatic_failure(request, evidence)
    fallback = verify_fallback_success(request, evidence, automatic, driver_commit)
    require_required_checks_clean(evidence.required_checks(request.expected_head))
    return build_receipt(request, fleet, automatic, fallback, driver_commit)
```

Set `GITHUB_CLI = Path("/usr/bin") / ("g" + "h")` and invoke it with argument arrays. Enforce the exact interface limits before accepting completeness, check every response's exact JSON type, and never log response bodies. `verify_fallback_success` loads Task 1's exact request parser and Task 2's schema-2 ledger parser from the verified driver checkout.

- [ ] **Step 10: Run verifier and rollout suites and verify green.**

```bash
rtk pytest -q tests/test_verify_claude_rollout_fallback.py tests/test_rollout_workflow_fleet.py
rtk git diff --check
```

Expected: exact chain emits the declared schema-1 receipt; every mutation fails without output.

- [ ] **Step 11: Commit the independently testable verifier unit.**

```bash
rtk git add scripts/verify_claude_rollout_fallback.py scripts/rollout_workflow_fleet.py tests/test_verify_claude_rollout_fallback.py tests/test_rollout_workflow_fleet.py
rtk git commit -m 'feat(rollout): attest Claude fallback evidence'
```

### Task 6: v1.78 release boundary and operator documentation

**Files:**

- Modify: `scripts/workflow_release_inventory.py`
- Modify: `scripts/verify_workflow_release.py`
- Modify: `tests/test_verify_workflow_release.py`
- Modify: `tests/release_fixture_helpers.py`
- Modify: `scripts/workflow-catalog.json`
- Modify: `tests/test_canonical_workflow_tree.py`
- Modify: `docs/workflows/contracts.md`
- Modify: `docs/workflow-fleet-rollout.md`

**Interfaces:**

- Add `CLAUDE_ROLLOUT_FALLBACK_RELEASE = (1, 78)` and `release_supports_claude_rollout_fallback(ref)`.
- Add both new action files and the verifier script as exact 100644 release roots for v1.78 only. The verifier's imported rollout modules remain ordinary repository source, but delayed imports require a clean checkout at receipt `verifier_commit`; the later JHW plan must execute from that exact commit.
- v1.78 validation seals request keys, bounded evidence reads, mutual exclusion, permission reduction, nested `$/` path, eight all-or-none inputs, ledger schema migration, exact canonical route, receipt keys, and no required-check waiver.
- v1.76 continues to validate at `444a7347aee169ed178aae80e8bd8d10eca52e02`; v1.77 continues to validate its authentic observational schema-1 helper at `dd13f9dcc64540494c1c04bc3f9c7a4f2ef0ba19`. v1.74/v1.75 historical fixtures retain their current accepted trees.
- Release-contract helpers have fixed signatures: `require_claude_fallback_permissions(caller: object, router: object, review: object) -> None`, `require_claude_fallback_routes(router: object) -> None`, `require_claude_fallback_inputs(review: object) -> None`, `require_budget_schema_two(root: Path) -> None`, and `require_request_and_receipt_contracts(root: Path) -> None`. They raise `ReleaseVerificationError` and emit no partial acceptance.
- The v1.78 operator section adapts the existing v1.76 create-only GitHub Git Data procedure. It requires an absent direct/peeled ref, exact reviewed public-main commit, one intended token passed by private file descriptor into an isolated environment, one annotated-tag-object POST followed by one tag-ref POST, exact response OIDs, raw response records at mode 0600, and final local/public remote verification. It never publishes through an ordinary Git push or moves/deletes a tag.

Target release gate:

```python
CLAUDE_ROLLOUT_FALLBACK_RELEASE = (1, 78)

def release_supports_claude_rollout_fallback(ref: str) -> bool:
    """Return whether the release owns the managed Claude rollout fallback."""
    return _release_version(ref) >= CLAUDE_ROLLOUT_FALLBACK_RELEASE
```

- [ ] **Step 1: Write failing v1.78 inventory and historical-acceptance tests.** Assert exact path ownership and unchanged older boundaries.

```python
def test_v178_adds_only_claude_rollout_fallback_roots():
    v177 = set(release_inventory.release_paths_for("v1.77"))
    v178 = set(release_inventory.release_paths_for("v1.78"))
    assert v178 - v177 == {
        ".github/actions/claude-rollout-fallback/action.yml",
        ".github/actions/claude-rollout-fallback/contract.py",
        "scripts/verify_claude_rollout_fallback.py",
    }

def test_v176_candidate_remains_accepted():
    assert verify_commit_content(repo, "v1.76", V176_COMMIT) == V176_COMMIT
```

- [ ] **Step 2: Run inventory cases and verify red.**

```bash
rtk pytest -q tests/test_verify_workflow_release.py -k 'v178 or v176_candidate'
```

Expected: v1.78 feature boundary and fixture do not exist.

- [ ] **Step 3: Implement the versioned roots and fixture downgrade.** Append only the three exact regular files when the ref supports the feature.

```python
CLAUDE_ROLLOUT_FALLBACK_RELEASE = (1, 78)
CLAUDE_ROLLOUT_FALLBACK_ROOTS = (
    ReleaseRoot(PurePosixPath(".github/actions/claude-rollout-fallback/action.yml"), "file", "100644"),
    ReleaseRoot(PurePosixPath(".github/actions/claude-rollout-fallback/contract.py"), "file", "100644"),
    ReleaseRoot(PurePosixPath("scripts/verify_claude_rollout_fallback.py"), "file", "100644"),
)

def release_supports_claude_rollout_fallback(ref: str) -> bool:
    return _release_version(ref) >= CLAUDE_ROLLOUT_FALLBACK_RELEASE

def _with_claude_rollout_fallback_roots(
    ref: str, roots: tuple[ReleaseRoot, ...],
) -> tuple[ReleaseRoot, ...]:
    if release_supports_claude_rollout_fallback(ref):
        return roots + CLAUDE_ROLLOUT_FALLBACK_ROOTS
    return roots
```

Return `_with_claude_rollout_fallback_roots(ref, roots)` at the end of the existing `release_roots_for` branch sequence. In the test fixture helper, downgrade versions below v1.78 by deleting these three paths and restoring every #182-modified release-owned file from authenticated v1.77 commit `dd13f9dcc64540494c1c04bc3f9c7a4f2ef0ba19` before older downgrade chains. Preserve deliberate mutations and repeated-call idempotency. Keep the authentic v1.77 schema-1 verifier and seals; seal the combined schema-2 observational helper only at v1.78.

- [ ] **Step 4: Write failing v1.78 mutation tests.** Use one named mutation for every security seal.

```python
@pytest.mark.parametrize("mutation", [
    "request_keys", "actor_set", "comment_page_bound", "provider_skipped",
    "nested_reference", "interactive_exclusion", "consumer_permission",
    "interactive_permission", "route_json", "ledger_migration",
    "canonical_route", "receipt_keys", "receipt_mode", "required_check",
])
def test_v178_rejects_fallback_contract_mutation(repo, mutation):
    mutate_v178_contract(repo, mutation)
    bad = commit(repo, f"mutate {mutation}")
    with pytest.raises(ReleaseVerificationError):
        verify_commit_content(repo, "v1.78", bad)
```

- [ ] **Step 5: Implement parsed and literal release seals.** Add one feature-gated verifier function and call it from the existing commit-content pipeline.

```python
def verify_claude_rollout_fallback_contract(root: Path) -> None:
    caller = load_workflow(root / "examples/baseline-workflows/.github/workflows/claude.yml")
    router = load_workflow(root / ".github/workflows/claude.yml")
    review = load_workflow(root / ".github/workflows/claude-code-review.yml")
    require_claude_fallback_permissions(caller, router, review)
    require_claude_fallback_routes(router)
    require_claude_fallback_inputs(review)
    require_budget_schema_two(root)
    require_request_and_receipt_contracts(root)

if release_supports_claude_rollout_fallback(ref):
    verify_claude_rollout_fallback_contract(candidate_root)
```

Use parsed YAML/Python AST checks for job/input/permission/schema structure and exact literal seals for embedded JavaScript key sets, error reasons, bounded page counts, and publication gates.

- [ ] **Step 6: Write the operator contracts.** Add these exact sections and tables, using the names already fixed in this plan.

```text
docs/workflows/contracts.md
  Claude rollout request v1
  Admission schema 1
  Invocation route schema 2 migration
  Claude schema-3 failure and fallback variants
  Fleet fallback receipt schema 1

docs/workflow-fleet-rollout.md
  Create-only v1.78 and v1.78.1 tag publication
  v1.76 bootstrap evidence
  v1.78 route adoption
  v1.78.1 real-boundary canary
  Required-check stop conditions
```

- [ ] **Step 7: Run release, catalog, and pin suites and verify green.**

```bash
rtk pytest -q tests/test_verify_workflow_release.py tests/test_canonical_workflow_tree.py tests/test_action_pins.py
rtk git diff --check
```

Expected: v1.78 positive/mutation cases and v1.74-v1.77 historical cases pass.

- [ ] **Step 8: Commit the independently testable release unit.**

```bash
rtk git add scripts/workflow_release_inventory.py scripts/verify_workflow_release.py tests/test_verify_workflow_release.py tests/release_fixture_helpers.py scripts/workflow-catalog.json tests/test_canonical_workflow_tree.py docs/workflows/contracts.md docs/workflow-fleet-rollout.md
rtk git commit -m 'feat(release): define v1.78 Claude fallback contract'
```

- [ ] **Step 9: Verify the exact committed object.** Resolve HEAD inside Python and pass the resulting 40-hex object to the existing commit-only CLI.

```bash
rtk python3 - <<'PY'
from pathlib import Path
import subprocess
import sys

root = Path.cwd()
head = subprocess.run(
    ["/usr/bin/git", "rev-parse", "HEAD"],
    cwd=root,
    check=True,
    capture_output=True,
    text=True,
).stdout.strip()
subprocess.run([
    sys.executable,
    "scripts/verify_workflow_release.py",
    "--automation", str(root),
    "--ref", "v1.78",
    "--expected-commit", head,
    "--commit-only",
], cwd=root, check=True)
PY
```

Expected: `PASS: v1.78 commit content is secure` names the exact HEAD object.

### Task 7: Whole-change verification and adversarial review

**Files:**

- Modify only files implicated by a reproduced test or reviewer finding.
- Add private tribunal reports under `/home/jhw/ai/opencode/projects/automation/.review/issue-182/tribunal/`; do not commit them.

**Interfaces:**

- Local acceptance requires focused suites, full pytest, actionlint for every release-managed workflow, Python compilation, YAML parsing, and clean diff checks.
- Native pre-PR tribunal controls the adversarial review with exactly three independent read-only roles A, B, and C. Each reviewer response is preserved byte-for-byte in a current-user-owned, non-symlink regular file with mode 0600; type, owner, and mode are checked independently immediately before `finalize`.
- Hosted pull-request review is required before merge. Any must-fix finding returns to the smallest owning task, followed by affected tests, full verification, and another review round.

- [ ] **Step 1: Reassert ownership and snapshot preconditions.** Fetch may happen only before tribunal `begin`; require clean status and enabled native hooks/multi-agent features.

```bash
rtk python3 /home/jhw/ai/opencode/projects/automation/.review/releases/v1.76-canary-next-mxyr4iya/pr328-review-r1/assert-owner.py
rtk git fetch origin main
rtk git status --short
rtk proxy codex features list
```

Expected: ownership succeeds, status is empty, and `hooks` plus `multi_agent` are enabled. Stop before tribunal state mutation on any failure.

- [ ] **Step 2: Run the focused acceptance suite.**

```bash
rtk pytest -q tests/test_claude_rollout_fallback.py tests/test_claude_workflow_validation.py tests/test_review_invocation_budget.py tests/test_review_invocation_budget_action.py tests/test_review_workflow_logic.py tests/test_canonical_workflow_tree.py tests/test_rollout_workflow_fleet.py tests/test_verify_claude_rollout_fallback.py tests/test_verify_workflow_release.py tests/test_workflow_release_bundle.py tests/test_action_pins.py
```

Expected: all focused tests pass.

- [ ] **Step 3: Run repository-wide static and dynamic checks.**

```bash
rtk pytest -q
rtk python3 -m compileall -q .github/actions/claude-rollout-fallback .github/actions/review-invocation-budget scripts/verify_claude_rollout_fallback.py scripts/workflow_release_inventory.py scripts/verify_workflow_release.py
rtk proxy /home/jhw/ai/opencode/projects/automation/.review/releases/v1.74/tools/actionlint-1.7.12 .github/workflows/*.yml examples/baseline-workflows/.github/workflows/*.yml
rtk git diff --check
rtk git status --short
```

Expected: every command passes and the implementation worktree is clean.

- [ ] **Step 4: Read the installed tribunal role contracts and begin round 1.** Read reviewer A/B/C and report schema completely, then bind the clean named branch against explicit base `origin/main`.

```bash
rtk cat /home/jhw/.codex/skills/pre-pr-tribunal/references/reviewer-a.md
rtk cat /home/jhw/.codex/skills/pre-pr-tribunal/references/reviewer-b.md
rtk cat /home/jhw/.codex/skills/pre-pr-tribunal/references/reviewer-c.md
rtk cat /home/jhw/.codex/skills/pre-pr-tribunal/references/report-schema.md
rtk proxy /usr/bin/python3 /home/jhw/.local/share/claude-config/pre_pr_tribunal/cli.py begin --base origin/main --runtime codex --round 1
```

Expected: `begin` returns one active telemetry run ID plus immutable HEAD/diff/contract bindings.

- [ ] **Step 5: Generate isolated A/B/C contexts.** Preserve each command's exact JSON stdout separately and never show one projection to another role.

```bash
rtk proxy /usr/bin/python3 /home/jhw/.local/share/claude-config/pre_pr_tribunal/cli.py context --reviewer A
rtk proxy /usr/bin/python3 /home/jhw/.local/share/claude-config/pre_pr_tribunal/cli.py context --reviewer B
rtk proxy /usr/bin/python3 /home/jhw/.local/share/claude-config/pre_pr_tribunal/cli.py context --reviewer C
```

- [ ] **Step 6: Dispatch exactly three native read-only reviewers.** Make a mode-0700 temporary view root, add one detached clean worktree per role at the bound HEAD, require absent `.review`, then use three parallel native `collaboration.spawn_agent` calls with `fork_turns="none"`. Each self-contained prompt names its dedicated worktree, its own role reference, the report schema, bound snapshot, and only its own projection. Track every view and handle immediately; do not start a fourth role.

- [ ] **Step 7: Preserve and submit each terminal response independently.** For A, B, and C, write the exact response bytes through O_EXCL/no-follow creation, chmod 0600, verify current UID/regular/non-symlink/mode/digest, submit the file bytes on stdin, compare returned `raw_sha256`, then validate the stored report.

```bash
rtk proxy /usr/bin/python3 /home/jhw/.local/share/claude-config/pre_pr_tribunal/cli.py status
rtk proxy /usr/bin/python3 /home/jhw/.local/share/claude-config/pre_pr_tribunal/cli.py validate-report --reviewer A --source stored
rtk proxy /usr/bin/python3 /home/jhw/.local/share/claude-config/pre_pr_tribunal/cli.py validate-report --reviewer B --source stored
rtk proxy /usr/bin/python3 /home/jhw/.local/share/claude-config/pre_pr_tribunal/cli.py validate-report --reviewer C --source stored
```

Use the installed skill's telemetry spans around dispatch, storage, validation, cleanup, and every terminal exit. A format rejection gets a format-only retry for the same role; a supported terminal failure gets at most three controller-local attempts for that role. Never repair or reserialize reviewer bytes.

- [ ] **Step 8: Enforce the local 0600 check immediately before finalization.** Require exactly the three controller-private response paths and compare their bytes to the sealed receipt digests.

```bash
rtk python3 - <<'PY'
from hashlib import sha256
import json
import os
from pathlib import Path
import stat

root = Path("/home/jhw/ai/opencode/projects/automation/.review/issue-182/tribunal")
for role in ("A", "B", "C"):
    response = root / f"reviewer-{role}.report"
    receipt = json.loads((root / f"reviewer-{role}.receipt.json").read_text())
    observed = response.lstat()
    assert stat.S_ISREG(observed.st_mode)
    assert not response.is_symlink()
    assert observed.st_uid == os.getuid()
    assert stat.S_IMODE(observed.st_mode) == 0o600
    assert sha256(response.read_bytes()).hexdigest() == receipt["raw_sha256"]
PY
```

Stop before `finalize` if this command or any immediately repeated stored-report validation fails.

- [ ] **Step 9: Finalize only an all-sealed round.** Re-run the three stored validations, compare them with sealed receipts and the bound snapshot, then invoke the pathless finalizer.

```bash
rtk proxy /usr/bin/python3 /home/jhw/.local/share/claude-config/pre_pr_tribunal/cli.py finalize
```

Close the telemetry run on every exit. A PASS records bound HEAD/diff. CRITICAL/HIGH findings require one decision each, test-first fixes or evidence-backed rebuttals, one `review-fix round N` commit, and a complete next A/B/C round; round 3 is the hard stop.

- [ ] **Step 10: Open the hosted review only after tribunal PASS.** Use the repository's `jhw-pr` reviewed-merge workflow against base `main`, bind it to the tribunal HEAD, request hosted reviewers, and wait for required checks. Do not enable merge before a clean hosted verdict and live exact-head/base validation.

- [ ] **Step 11: Merge and verify the remote result.** Merge only the reviewed HEAD through the supported method, then compare the API-returned merge commit and the new remote `main` object with the expected GitHub-generated commit. Any drift stops before release publication.

### Task 8: Immutable release, real-boundary canary, and bootstrap batch

#### Automatic-review security amendment (2026-09-12)

This amendment supersedes the v1.78.2 docs-only and real-boundary-canary steps in
the earlier amendment below. Its v1.78.1 evidence and completed validation remain
historical inputs. A consumer tribunal found that the automatic
`claude-code-review.yml` still let `check-enabled` and `skipped` inherit the
caller's pull-request write and OIDC ceiling, and its config check still used the
mutable `check-workflow-enabled@v1.1` tag. The same omission existed in the release
verifier, which protected the command router but not the automatic review gate.

| Boundary | Current evidence | Required next state |
| --- | --- | --- |
| v1.78.1 | Published immutable router-hardening release | Preserve exact tag and historical verifier acceptance |
| v1.78.2 | Unpublished | Automatic-review security patch with exact release-owned changes limited to the central Claude review workflow and verifier |
| Consumer updates | wlan-package #331 and the local tossApp candidate target v1.78.1 | Hold merge; repin both to the reviewed v1.78.2 release and rerun their exact-HEAD reviews |

- [ ] **Step I: Complete the v1.78.2 automatic-review hardening test-first.** Give
  `check-enabled` only contents/pull-requests read, give `skipped` an empty
  permission map, and pin the config check to the immutable v1.78 commit. Add
  mutation tests proving the release verifier rejects inherited authority and a
  movable action reference while retaining exact v1.78.1 acceptance.
- [ ] **Step J: Review and merge the central security patch.** Run focused and
  full verification, actionlint, Python compilation and `git diff --check`, then
  complete the three-role tribunal and hosted reviews on one exact HEAD. Obtain
  the separate exact-tuple merge approval required by this plan.
- [ ] **Step K: Publish v1.78.2 only after separate tag approval.** Use the
  create-only procedure, authenticate the v1.78.1 direct and peeled identities,
  and require the release-owned diff to contain exactly the central Claude review
  workflow and its verifier. Never move, delete, or blindly retry a tag write.
- [ ] **Step L: Repin and rereview both consumer candidates.** Update only the
  managed config and active caller paths to v1.78.2, preserve prior evidence, and
  rerun local and hosted exact-HEAD checks. Merge each repository only after its
  separate reviewed tuple is approved.
- [ ] **Step M: Defer the real managed-boundary canary.** After a default branch
  installs v1.78.2, select a later distinct release and add a separately reviewed
  create-only publication procedure and receipt-bound canary plan.

#### Prior execution amendment (2026-09-12; superseded where conflicting)

This amendment supersedes the original numbered Task 8 procedure below. The old
sequence is retained only as historical design provenance and must not be used for
further mutations.

| Boundary | Current evidence | Required next state |
| --- | --- | --- |
| v1.78 | Annotated tag `7efe562b49c7b5f9fdad6855ff8c2a73b090a122`, peeled commit `a08141d644ab1036cadd8167d48158e827dcd978` | Preserve unchanged |
| Bootstrap PR | `jhw7500/wlan-package#331`, reviewed HEAD `8ed6b45e8c5df68391941fbd181cfb79f0380178`, base `a35064638d4881c06d7589cd6bfa08a2bffbaff0` | Blocked by tribunal finding A-R1-001; update to v1.78.1 and review the new HEAD |
| v1.78.1 | Security patch candidate | Narrow pre-admission permissions and pin `check-workflow-enabled` to the immutable v1.78 commit `a08141d644ab1036cadd8167d48158e827dcd978`, preserving description-before-enabled fallback parsing |
| v1.78.2 | Not yet created | Distinct docs-only release after v1.78.1 adoption, with every v1.78.1 release-owned path byte-identical |

- [ ] **Step A: Complete the v1.78.1 hardening patch test-first.** Require
  `check-enabled.permissions == {contents: read}`,
  `skipped.permissions == {}`, and the exact immutable nested action pin. Keep
  v1.78 accepted only at its existing authenticated bytes; require hardening for
  v1.78.1 and later. Update release documentation and tests for the new sequence.
- [ ] **Step B: Review and merge the automation patch.** Run focused and full
  verification, actionlint, Python compilation and `git diff --check`; then run a
  complete three-role native tribunal and hosted review on one exact automation
  HEAD. Resolve every HIGH/CRITICAL finding with a decision and a new complete
  round. Merge only after a clean verdict and an explicit reviewed-merge decision.
- [ ] **Step C: Publish v1.78.1 only after concrete approval.** Present the exact
  reviewed main commit, tree, verifier result, release-root digest, and proof that
  both direct and peeled v1.78.1 refs are absent. Create the annotated tag once
  through the create-only procedure in `docs/workflow-fleet-rollout.md`; never move,
  delete, or retry an uncertain ref creation.
- [ ] **Step D: Update the existing bootstrap PR.** Re-render only the managed
  workflow paths in PR #331 from v1.78 to v1.78.1. Bind the new commit, tree, base,
  full-diff digest and released central commit before the single remote update.
  Preserve the original automatic mismatch and Round 1 tribunal evidence.
- [ ] **Step E: Rereview and merge the bootstrap only if clean.** Record a formal
  decision for A-R1-001, run all three tribunal roles as Round 2 against the new
  exact HEAD, and rerun target, Gemini, OpenCode, shell, source and Codex gates.
  A new finding or coordinate drift blocks merge. Present the live exact-head/base
  merge tuple and obtain separate merge approval.
- [ ] **Step F: Create and publish the v1.78.2 boundary candidate.** After v1.78.1
  is installed on the consumer default branch, add only
  `docs/workflows/v1.78.2-boundary-canary.md` on a distinct automation commit.
  Prove an empty raw Git diff over the authenticated v1.78.1 release inventory,
  run complete automation review, merge, and obtain separate tag approval.
- [ ] **Step G: Exercise the real pin boundary.** Create one managed rollout PR
  from the installed v1.78.1 caller to v1.78.2. Require the expected automatic
  `workflow_validation_mismatch`, one separately authorized canonical fallback
  request, exactly one provider entry, a finalized budget, canonical clean state,
  and a receipt verified from the exact v1.78.1 driver commit. Merge only after
  independent review and separate approval.
- [ ] **Step H: Close the operational handoff.** Record both new tag objects and
  peeled commits, PR/run/comment/receipt tuples, review decisions, provider count,
  fleet audits, remaining blockers and untouched evidence digests. Complete the
  supported Task lifecycle only after every requested rollout result is verified.

#### Archived original Task 8 sequence (superseded)

**Files:**

- Create after the v1.78 bootstrap merge: `docs/workflows/v1.78.1-boundary-canary.md`
- Write evidence only beneath a new `/home/jhw/ai/opencode/projects/automation/.review/releases/v1.78-*` directory; do not commit generated evidence.
- Reuse the current v1.76 batch evidence at `/home/jhw/ai/opencode/projects/automation/.review/releases/v1.76-canary-next-mxyr4iya/full-profile-batch-nOaszzvB` without rewriting its source records.

**Interfaces:**

- Publish v1.78 only from the exact reviewed merge commit with an annotated immutable tag and the repository's existing release verification flow. Both v1.78 and v1.78.1 publication paths authenticate immutable v1.77 tag `81f44fb6786bdfcc40f93161db74b1d9a9e3b7c5` / commit `dd13f9dcc64540494c1c04bc3f9c7a4f2ef0ba19` and unchanged v1.76 identity before either create-only POST. Allowed new tags are exactly v1.78 and v1.78.1; first publication has no prior-v1.78 requirement.
- The first consumer adoption is a bootstrap because its default branch lacks the route. After that merge, publish a separately reviewed v1.78.1 validation release from a distinct automation commit with identical release-owned v1.78 bytes. The exact v1.78-to-v1.78.1 pin change on the same canary must exercise automatic mismatch, structured request, nested fallback, one provider call, finalized budget, canonical result, and verifier receipt.
- On that canary, `driver_commit` is the installed v1.78 commit and target `release_commit` is the distinct v1.78.1 commit. Equality is an attestation failure because it would not test the caller-changing boundary.
- Current gstApp #109 and max9296 #74 stay separate from the durable route. Their existing default-branch Claude reviews are accepted only through the already hashed bootstrap proposal after live exact-head/base revalidation and a concrete authorization record.

- [ ] **Step 1: Seal the reviewed v1.78 merge candidate.** From a clean checkout whose `origin/main` is the hosted-review merge returned by Task 7, create a mode-0700 evidence directory whose suffix is the first 12 hexadecimal characters of that merge, under `/home/jhw/ai/opencode/projects/automation/.review/releases/`. Record the exact merge SHA, commit tree, `release_paths_for("v1.78")`, focused/full test logs, actionlint log, and SHA-256 for every recorded file. Run the commit-only verifier against the resolved 40-hex object:

```bash
release_commit="$(rtk git rev-parse origin/main)"
rtk python3 scripts/verify_workflow_release.py --automation . --ref v1.78 --expected-commit "$release_commit" --commit-only
```

Expected: `PASS: v1.78 commit content is secure` names `release_commit`; the evidence writer independently chmods every regular record to 0600 and rejects an existing path or symlink.

- [ ] **Step 2: Present the concrete v1.78 publication boundary.** Show the user the reviewed merge SHA, tree SHA, release verifier result, release-root digest manifest, and proof that local and remote `refs/tags/v1.78` are absent. Obtain explicit approval for creation of that one immutable tag. Stop without creating a local tag when approval is absent or does not bind the displayed commit.

- [ ] **Step 3: Publish and remotely verify v1.78.** Use the exact release procedure written in Task 6: create one annotated `v1.78` tag on the approved commit, create the remote tag ref once, then run the tag and remote verifier. The final verification command resolves the commit before invocation:

```bash
release_commit="$(rtk git rev-parse origin/main)"
rtk python3 scripts/verify_workflow_release.py --automation . --ref v1.78 --expected-commit "$release_commit" --remote origin
```

Expected: the local tag object and the public remote tag object are annotated, agree, and peel to the approved commit. A partially created or conflicting tag stops rollout; never move, delete, or recreate it.

- [ ] **Step 4: Render the read-only v1.78 bootstrap canary for wlan-package.** Reuse `wlan-package`, whose v1.76 automatic runtime canary already completed, and initialize a new private rollout workspace. Pass the released ref and an explicit manifest path:

```bash
canary_workspace="$(rtk mktemp -d /tmp/automation-v178-wlan-package.XXXXXX)"
rtk python3 scripts/rollout_workflow_fleet.py --automation . --workspace "$canary_workspace" --initialize-workspace --mode plan --ref v1.78 --repo wlan-package --manifest "$canary_workspace/rollout-plan.json" --actionlint /home/jhw/ai/opencode/projects/automation/.review/releases/v1.74/tools/actionlint-1.7.12
```

Expected: one `planned` or exact `reusable` default-branch target, no remote mutation, and rendered callers pinned to the peeled v1.78 commit. Copy the manifest and its digest into the v1.78 evidence directory.

- [ ] **Step 5: Obtain approval for the exact bootstrap PR publication.** Present repository `jhw7500/wlan-package`, observed base SHA, deterministic branch, expected commit/tree, changed paths, full diff digest, and the existing `review:request` label that will trigger review after publication. Obtain explicit user approval for that one branch/PR creation and label application. This approval does not authorize a merge or a later v1.78.1 publication.

- [ ] **Step 6: Publish or reuse the exact v1.78 bootstrap PR.** In the same marked workspace, let the released fleet publisher refetch and recompute before its single remote write:

```bash
rtk python3 scripts/rollout_workflow_fleet.py --automation . --workspace "$canary_workspace" --mode publish --ref v1.78 --repo wlan-package --confirm --manifest "$canary_workspace/rollout-publish.json" --actionlint /home/jhw/ai/opencode/projects/automation/.review/releases/v1.74/tools/actionlint-1.7.12
```

Expected: exactly one `published` or `reused` PR whose live head, base, title, body, branch and managed tree match the manifest. Stop on `current`, `blocked`, an unexpected prior branch/PR, or any second candidate.

- [ ] **Step 7: Trigger review and capture the expected bootstrap failure without retrying it.** Apply the already-existing `review:request` label once, then fetch the resulting automatic Claude run, its exact attempt, jobs, check-run annotations, budget comment, and canonical sticky comment. Require `workflow_validation_mismatch`, `review_execution=not_performed`, a skipped provider step, and no new Claude budget invocation. Preserve the raw responses as private evidence; another reason, provider entry, or ambiguous run stops the bootstrap.

- [ ] **Step 8: Complete the v1.78 bootstrap reviews.** Run the consumer repository's exact three-role tribunal, required tests, Gemini/OpenCode/Codex gates, and a separately requested default-branch manual Claude read-only review bound to the same HEAD/base/full diff. Record all exact-byte responses and current checks. A reviewer rerun must bind the same current HEAD; a new commit starts a new review round.

- [ ] **Step 9: Present the v1.78 bootstrap merge tuple.** Re-read the pull request, head repository, head SHA, base SHA, mergeability, required checks and every reviewer state. Present those concrete values plus the manual-Claude substitution evidence to the user and obtain explicit authorization for this one merge.

- [ ] **Step 10: Merge and confirm the v1.78 bootstrap once.** Invoke the consumer repository's supported reviewed-merge helper with the approved HEAD/base and reviewer map. Re-read the pull request and default branch until GitHub reports the returned merge commit. If the response is uncertain, reconcile read-only and do not invoke merge again.

- [ ] **Step 11: Create the distinct v1.78.1 validation commit.** From the new public automation `main`, create only `docs/workflows/v1.78.1-boundary-canary.md` with this complete content, then commit it on a dedicated branch:

```text
# v1.78.1 Boundary Canary

This validation release intentionally preserves every v1.78 release-owned byte.
Its distinct commit exists only to exercise a real immutable caller-pin change from
v1.78 to v1.78.1 after the default branch has installed the fallback router.
```

Use `apply_patch`, run `rtk git diff --check`, and commit with `docs(release): define v1.78.1 boundary canary`. Run focused/full tests, the exact A/B/C tribunal procedure from Task 7, hosted review, and reviewed merge before treating the resulting main commit as a release candidate.

- [ ] **Step 12: Prove v1.78.1 changes no release-owned byte.** Authenticate the existing annotated v1.78 tag locally and publicly, require a distinct reviewed v1.78.1 candidate, load inventory from a clean checkout of that authenticated baseline, and require an empty raw Git diff before commit-only verification:

```bash
rtk python3 - <<'PY'
import json
from pathlib import Path
import subprocess
import sys

root = Path.cwd()
def git(*args):
    return subprocess.run(["/usr/bin/git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()

direct = git("rev-parse", "refs/tags/v1.78")
old = git("rev-parse", "refs/tags/v1.78^{}")
new = git("rev-parse", "origin/main")
if git("cat-file", "-t", direct) != "tag" or old == new:
    raise SystemExit("annotated baseline and distinct candidate required")
subprocess.run([
    sys.executable, "-B", "-m", "scripts.verify_workflow_release",
    "--automation", str(root), "--ref", "v1.78",
    "--expected-commit", old, "--remote", "origin",
], cwd=root, check=True)
baseline = root / ".review" / ("v178-boundary-baseline-" + old)
if baseline.exists() or baseline.is_symlink():
    raise SystemExit("new baseline checkout required")
git("worktree", "add", "--detach", str(baseline), old)
if subprocess.run(["/usr/bin/git", "status", "--porcelain"], cwd=baseline,
                  check=True, capture_output=True, text=True).stdout:
    raise SystemExit("dirty baseline checkout")
subprocess.run([
    sys.executable, "-B", "-m", "scripts.verify_workflow_release",
    "--automation", str(baseline), "--ref", "v1.78",
    "--expected-commit", old, "--remote", "origin",
], cwd=baseline, check=True)
inventory = subprocess.run([
    sys.executable, "-B", "-c",
    'import json; from scripts.workflow_release_inventory import release_paths_for; '
    'print(json.dumps(release_paths_for("v1.78")))',
], cwd=baseline, check=True, capture_output=True, text=True)
owned = json.loads(inventory.stdout)
if not isinstance(owned, list) or not owned or not all(isinstance(path, str) for path in owned):
    raise SystemExit("invalid authenticated baseline inventory")
if git("diff", "--raw", "--no-ext-diff", "--no-textconv", "--exit-code",
       old, new, "--", *owned):
    raise SystemExit("release-owned boundary difference")
if git("rev-parse", "refs/tags/v1.78") != direct or git("rev-parse", "refs/tags/v1.78^{}") != old:
    raise SystemExit("baseline identity moved")
subprocess.run([
    sys.executable, "scripts/verify_workflow_release.py",
    "--automation", str(root), "--ref", "v1.78.1",
    "--expected-commit", new, "--commit-only",
], cwd=root, check=True)
PY
```

Expected: the owned-path diff is empty and v1.78.1 commit content passes. Any release-owned change requires a normal patch release design and invalidates this boundary canary. The create-only operator procedure repeats baseline authentication and the raw diff immediately before either POST, and authenticates unchanged v1.76 and v1.77 for both new release paths.

- [ ] **Step 13: Present the concrete v1.78.1 publication boundary.** Show the distinct reviewed commit, tree, empty release-owned diff, commit-only verifier output, digest manifest, and proof that local and remote `refs/tags/v1.78.1` are absent. Obtain explicit approval for creation of that immutable tag.

- [ ] **Step 14: Publish and verify v1.78.1.** Create the annotated tag once and verify it remotely:

```bash
validation_commit="$(rtk git rev-parse origin/main)"
rtk python3 scripts/verify_workflow_release.py --automation . --ref v1.78.1 --expected-commit "$validation_commit" --remote origin
```

Expected: v1.78 and v1.78.1 remain two immutable annotated tags that peel to two different commits while their v1.78 release-owned paths are byte-identical.

- [ ] **Step 15: Render the real pin-change canary without mutation.** Use a second new workspace and the same `wlan-package` repository. Require the default branch to pin the v1.78 driver commit and the rendered rollout head to pin the distinct v1.78.1 target commit.

```bash
boundary_workspace="$(rtk mktemp -d /tmp/automation-v1781-wlan-package.XXXXXX)"
rtk python3 scripts/rollout_workflow_fleet.py --automation . --workspace "$boundary_workspace" --initialize-workspace --mode plan --ref v1.78.1 --repo wlan-package --manifest "$boundary_workspace/rollout-plan.json" --actionlint /home/jhw/ai/opencode/projects/automation/.review/releases/v1.74/tools/actionlint-1.7.12
```

- [ ] **Step 16: Present the real pin-change publication boundary.** Show the fresh plan's repository, observed base, deterministic branch, expected head/tree, changed paths, target release, full diff digest, and the existing `review:request` label that will trigger review. Obtain explicit approval for this one branch/PR creation and label application.

- [ ] **Step 17: Publish the real pin-change canary.** Reuse the Step 15 workspace and let the released publisher refetch and recompute before mutation:

```bash
rtk python3 scripts/rollout_workflow_fleet.py --automation . --workspace "$boundary_workspace" --mode publish --ref v1.78.1 --repo wlan-package --confirm --manifest "$boundary_workspace/rollout-publish.json" --actionlint /home/jhw/ai/opencode/projects/automation/.review/releases/v1.74/tools/actionlint-1.7.12
```

Expected: one exact `published` or `reused` PR and no other remote change.

- [ ] **Step 18: Trigger review and capture the real-boundary automatic failure.** Apply the already-existing `review:request` label once, then preserve the automatic run, attempt, jobs, annotations, budget comment and canonical comment. Require `workflow_validation_mismatch`, `review_execution=not_performed`, skipped provider, and no budget invocation at the exact published HEAD/base. Stop on ambiguity or any other result.

- [ ] **Step 19: Build one canonical request record.** Generate a fresh 16-byte nonce with `secrets.token_hex(16)`, construct `FallbackRequest` from the Step 18 run/attempt plus the published repository/PR/HEAD/base/v1.78.1 release/full-diff coordinates, and render it with v1.78's `canonical_request_body`. Write the exact body and coordinates with O_EXCL/no-follow creation, chmod 0600, and immediately lstat current owner/type/mode. If one exact live request already exists, record its comment ID and reuse it instead of generating a second request.

- [ ] **Step 20: Authorize the exact request comment.** Present the complete two-line body and all bound coordinates to the user. Obtain explicit authorization to post that exact GitHub issue comment; an approval for the PR or release tag does not authorize this message.

- [ ] **Step 21: Post or reuse the request exactly once.** Immediately re-read HEAD/base and the failed run before the write. Post the authorized body once through the fixed GitHub REST endpoint, or reuse the single exact existing request. Save the returned comment object byte-for-byte and never retry an uncertain write until a read proves that no matching comment exists.

- [ ] **Step 22: Prove the managed fallback ran exactly once.** Wait for one default-branch `claude.yml` issue-comment run. Require classifier route `managed`, nested `claude-code-review.yml` identity at the installed v1.78 driver commit, one provider entry, one finalized automatic budget round, and one schema-3 fallback state whose route binds the request comment, failed run, base, v1.78.1 target commit and full-diff hash. A second request/run/provider call, partial route, changed coordinate, or required failed automatic check stops before merge.

- [ ] **Step 23: Generate and validate the immutable fallback receipt.** Create a clean detached checkout at the v1.78 driver commit, keep the output outside that checkout, derive PR/head/base from the single authoritative publish manifest, and invoke the verifier:

```bash
driver_commit="$(rtk git rev-parse 'refs/tags/v1.78^{}')"
driver_checkout="/tmp/automation-v178-driver-$driver_commit"
boundary_evidence="$(rtk mktemp -d /home/jhw/ai/opencode/projects/automation/.review/releases/v1.78.1-wlan-package.XXXXXX)"
rtk git worktree add --detach "$driver_checkout" "$driver_commit"
boundary_pr="$(rtk python3 -c 'import json,sys; value=json.load(open(sys.argv[1])); assert len(value)==1; print(value[0]["pr_url"].rsplit("/",1)[1])' "$boundary_workspace/rollout-publish.json")"
boundary_head="$(rtk python3 -c 'import json,sys; value=json.load(open(sys.argv[1])); assert len(value)==1; print(value[0]["head_sha"])' "$boundary_workspace/rollout-publish.json")"
boundary_base="$(rtk python3 -c 'import json,sys; value=json.load(open(sys.argv[1])); assert len(value)==1; print(value[0]["base_sha"])' "$boundary_workspace/rollout-publish.json")"
rtk python3 -I -S -B "$driver_checkout/scripts/verify_claude_rollout_fallback.py" --automation-root "$driver_checkout" --release-ref v1.78.1 --remote origin --repository jhw7500/wlan-package --pr "$boundary_pr" --expected-head "$boundary_head" --expected-base "$boundary_base" --output "$boundary_evidence/fallback-receipt.json"
```

Immediately lstat the receipt and require current UID, regular/non-symlink type, mode 0600, schema 1, `effective_status=CLEAN`, `fallback.driver_commit` equal to the v1.78 peeled commit, and `release_commit` equal to the distinct v1.78.1 peeled commit. Independently re-read the live pull request, checks, budget, comment, and runs after receipt creation; any drift invalidates the receipt.

- [ ] **Step 24: Complete the real-boundary reviews.** Run the remaining independent reviewers and target checks on the receipt-bound HEAD. Require native policy to show that the failed automatic Claude check is not a required blocker and require the fallback canonical state to contain no blocking finding.

- [ ] **Step 25: Present the real-boundary merge tuple.** Re-read the receipt, exact head/base/origin, mergeability, required checks and all review states. Present the concrete merge tuple and obtain explicit authorization for this one merge.

- [ ] **Step 26: Merge and audit the real-boundary canary.** Use the supported reviewed-merge helper once, verify the GitHub-generated merge commit, and run the released fleet audit at `v1.78.1` for `wlan-package`. Remove the detached verifier worktree with a non-force removal only after its clean status is proven.

- [ ] **Step 27: Revalidate the existing two-PR v1.76 proposal without altering evidence.** Require SHA-256 `916c58c9a9062d74a0740d8fb51a184fe979dd37c2872c328f21bb6e12c5170d` for `/home/jhw/ai/opencode/projects/automation/.review/releases/v1.76-canary-next-mxyr4iya/full-profile-batch-nOaszzvB/merge-substitution-proposal.json`. Freshly require gstApp #109 head `52dbdedbc090dcdb50bdc93172985a67bc5df683` / base `6d8200e4562da0b7aff01db7b86cb565390eb56d` and max9296 #74 head `00db418bd1520ab36d866961b70179d53a01453e` / base `621f6605e1ec16251a1d086e8f3fc4e4ce36f212`. Re-run the existing read-only policy/evidence preflights; stop if either tuple, reviewer state, required check, mergeability, origin, release tag or proposal digest differs.

- [ ] **Step 28: Bind the exact two-PR approval.** Present the unchanged proposal hash and both exact tuples. If the user's explicit approval binds them, create `manual-substitution-authorization.json` once with O_EXCL/no-follow mode 0600, the exact user response, `authorized_by_user=true`, the proposal SHA-256, and scope `v176_full_profile_two_pr_manual_claude_substitution`. Immediately lstat and verify current owner/type/mode, then run the existing `verify-approval.py`.

- [ ] **Step 29: Merge gstApp and max9296 sequentially.** Run the existing `merge-one-approved.sh` for gstApp, require its private merge receipt and API-confirmed GitHub merge commit, then do the same for max9296. An uncertain or partial result is read-only reconciliation, never a repeated merge.

- [ ] **Step 30: Close the operational handoff.** Record v1.78/v1.78.1 tag objects and peeled commits, the bootstrap and boundary PR/run/comment/receipt coordinates, provider call count, fleet audits, the two v1.76 merge receipts, remaining blockers, and untouched evidence digests in the existing Task handoff. Release Claim `clm-01a08b35-4a07-75ba-8a22-a8ca96029ade` only through the supported Task completion path after every requested rollout result is verified; do not edit the Registry directly.

## Final Verification Matrix

| Failure or race | Expected result |
| --- | --- |
| Automatic mismatch, provider skipped, exact rollout | One admitted fallback path |
| Automatic mismatch after provider entry | Blocking; no fallback |
| Validation unavailable or another failure reason | Blocking; no fallback |
| Stale HEAD/base/release/diff at any read | Blocking; new tuple required |
| Repeated identical request | Reuse; zero extra provider calls |
| Claimed but unfinalized fallback | Existing bounded failure policy; no interactive replacement |
| Two requests, runs, states, ledgers, or rollout pull requests | Ambiguous and blocking |
| Fallback CLEAN with blocking canonical finding | Blocking at configured threshold |
| Automatic Claude check required and failed | Native required-check failure remains blocking |
| Valid receipt and all independent gates clean | Later JHW gate may map effective Claude status to CLEAN |

The automation implementation is complete only when Tasks 1-7 are merged after review. Operational rollout is complete only when Task 8 proves the v1.78-to-v1.78.1 pin-change boundary with one provider call and no required-check bypass. JHW command consumption is outside this plan and starts only after its separate Task decision.
