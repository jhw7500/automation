# Review-mode Workflow Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every managed PR review workflow honor `review:request`, `review:skip`, draft state, and repository automatic-review configuration before any model invocation.

**Architecture:** A small release-owned composite action resolves PR metadata, labels, explicit mode, and existing YAML configuration into one fail-closed decision shared by Claude, Gemini, and OpenCode. Thin managed callers pass the label-derived mode and support `ready_for_review`; reusable workflows retain their existing provider, canonicalization, and invocation-budget paths. The feature ships as immutable automation release `v1.51`, followed by a separate fleet-default PR.

**Tech Stack:** GitHub Actions YAML, Python 3 standard library, Ruby stdlib YAML bridge on `ubuntu-latest`, GitHub CLI/API, pytest, actionlint.

**Spec:** `docs/superpowers/specs/2026-08-31-review-mode-command-control-design.md`

## Global Constraints

- Do not modify Notion databases, Project Control state, or issue #52.
- `--review` and `--no-review` are represented by exactly `review:request` and `review:skip`.
- Effective modes are exactly `auto`, `request`, `skip`, and `conflict`.
- Both labels or an input/label mismatch fail without invoking a model.
- Draft, skip, unsafe-fork, and closed PR decisions terminate successfully without invoking a model.
- `review:request` overrides automatic mode but never overrides `workflows.<name>.enabled`.
- Automatic precedence remains `workflows.<name>.auto -> review.auto -> true`.
- The baseline remains explicit `review.auto: false`.
- Keep existing review prompts, finding grammar, quality schemas, and blocking thresholds unchanged.
- Preserve historical releases; the new local action is required only for `v1.51+`.
- Do not move or recreate an immutable release tag.
- Every implementation change follows RED-GREEN-REFACTOR and ends in a focused commit.
- Every shell command in this environment starts with `rtk`.

## File map

| Path | Responsibility |
| --- | --- |
| `.github/actions/resolve-review-policy/action.yml` | Fetch current PR/config inputs and publish the shared decision |
| `.github/actions/resolve-review-policy/resolve_review_policy.py` | Pure validation and mode-resolution logic |
| `.github/workflows/{claude-code-review,gemini-auto-review,opencode-auto-review}.yml` | Consume the policy and gate existing provider jobs |
| `.github/workflows/_self-*-review.yml` | Dogfood label mode and ready transition locally |
| `examples/baseline-workflows/.github/workflows/*auto-review*.yml` | Managed consumer callers |
| `scripts/workflow-catalog.json` | Exact caller triggers, inputs, permissions, and `with` keys |
| `scripts/workflow_release_inventory.py` | `v1.51` release capability and immutable action roots |
| `scripts/verify_workflow_release.py` | Authenticate the action and all caller/reusable wiring |
| `tests/test_review_policy_action.py` | Pure policy behavior and action transport tests |
| `tests/test_review_workflow_logic.py` | Reusable workflow provider-gating behavior |
| `tests/test_canonical_workflow_tree.py` | Baseline/self caller exact shapes |
| `tests/test_workflow_catalog.py` | Catalog parity and fleet default |
| `tests/test_verify_workflow_release.py` | Historical and `v1.51` verifier mutation tests |
| `.github/workflow-config.yml` | Explicit automation dogfood default |
| `.gemini/config.yaml` | Disable only Gemini PR-open review for the automation repository |
| `docs/workflows/contracts.md` | Consumer, label, App, release, and rollback contract |

---

### Task 1: Add the deterministic review-policy resolver

**Files:**
- Create: `.github/actions/resolve-review-policy/action.yml`
- Create: `.github/actions/resolve-review-policy/resolve_review_policy.py`
- Create: `tests/test_review_policy_action.py`

**Interfaces:**
- Consumes: `PolicyRequest(workflow_name, review_mode, force_run, force_review, event_name, repository, pr, config)`.
- Produces: `PolicyDecision(run_review: bool, effective_mode: str, reason: str, head_sha: str)`.
- Raises: `PolicyError` for invalid input, both labels, non-boolean config, or event/input label mismatch.
- Action outputs: `run-review`, `effective-mode`, `reason`, and `head-sha`.

- [ ] **Step 1: Write RED unit tests for precedence and safety states**

Create `tests/test_review_policy_action.py` with a helper that loads the module and these table-driven cases:

```python
def base_pr() -> dict[str, object]:
    return {
        "state": "open",
        "draft": False,
        "head": {
            "sha": "a" * 40,
            "repo": {"full_name": "jhw7500/example", "fork": False},
        },
        "labels": [],
    }


def request(*, labels=None, mode="auto", config=None, pr=None, event="pull_request", force_review=False):
    payload = base_pr() if pr is None else pr
    payload["labels"] = [{"name": name} for name in (labels or [])]
    return PolicyRequest(
        workflow_name="claude-code-review",
        review_mode=mode,
        force_run=False,
        force_review=force_review,
        event_name=event,
        repository="jhw7500/example",
        pr=payload,
        config={} if config is None else config,
    )


@pytest.mark.parametrize(
    ("labels", "mode", "config", "expected"),
    [
        ([], "auto", {"review": {"auto": True}}, (True, "review_auto_true")),
        ([], "auto", {"review": {"auto": False}}, (False, "review_auto_false")),
        ([], "auto", {"workflows": {"claude-code-review": {"auto": False}}, "review": {"auto": True}}, (False, "workflow_auto_false")),
        (["review:request"], "request", {"review": {"auto": False}}, (True, "request")),
        (["review:skip"], "skip", {"review": {"auto": True}}, (False, "skip")),
    ],
)
def test_policy_precedence(labels, mode, config, expected):
    decision = resolve_policy(request(labels=labels, mode=mode, config=config))
    assert (decision.run_review, decision.reason) == expected


def test_both_labels_fail_closed():
    with pytest.raises(PolicyError, match="review_label_conflict"):
        resolve_policy(request(labels=["review:request", "review:skip"], mode="conflict"))


@pytest.mark.parametrize(("change", "reason"), [
    ({"draft": True}, "draft"),
    ({"state": "closed"}, "closed"),
    ({"head": {"repo": {"full_name": "fork/repo", "fork": True}}}, "unsafe_pr"),
])
def test_noneligible_pr_never_runs(change, reason):
    pr = base_pr() | change
    decision = resolve_policy(request(pr=pr))
    assert decision.run_review is False
    assert decision.reason == reason


def test_pull_request_mode_must_match_labels():
    with pytest.raises(PolicyError, match="review_mode_label_mismatch"):
        resolve_policy(request(labels=["review:skip"], mode="request"))


def test_manual_force_review_allows_request_without_label():
    decision = resolve_policy(request(mode="request", event="workflow_dispatch", force_review=True))
    assert decision.run_review is True
    assert decision.reason == "request"
```

- [ ] **Step 2: Run the new tests and confirm the missing module failure**

Run:

```bash
rtk python3 -m pytest tests/test_review_policy_action.py -q
```

Expected: collection fails because `.github/actions/resolve-review-policy/resolve_review_policy.py` does not exist.

- [ ] **Step 3: Implement the pure resolver**

Define these exact public types and resolution order:

```python
class PolicyError(ValueError):
    pass


@dataclass(frozen=True)
class PolicyRequest:
    workflow_name: str
    review_mode: str
    force_run: bool
    force_review: bool
    event_name: str
    repository: str
    pr: dict[str, object]
    config: dict[str, object]


@dataclass(frozen=True)
class PolicyDecision:
    run_review: bool
    effective_mode: str
    reason: str
    head_sha: str


def resolve_policy(request: PolicyRequest) -> PolicyDecision:
    labels = _label_names(request.pr)
    if {"review:request", "review:skip"} <= labels:
        raise PolicyError("review_label_conflict")
    label_mode = "request" if "review:request" in labels else "skip" if "review:skip" in labels else "auto"
    if request.review_mode not in {"auto", "request", "skip", "conflict"}:
        raise PolicyError("review_mode_invalid")
    if request.review_mode == "conflict":
        raise PolicyError("review_label_conflict")
    manual_request = request.event_name == "workflow_dispatch" and request.force_review
    if manual_request and request.review_mode != "request":
        raise PolicyError("force_review_mode_invalid")
    if not manual_request and request.review_mode != label_mode:
        raise PolicyError("review_mode_label_mismatch")
    head_sha = _validated_head(request.pr, request.repository)
    if request.pr.get("state") != "open":
        return PolicyDecision(False, request.review_mode, "closed", head_sha)
    if request.pr.get("draft") is True:
        return PolicyDecision(False, request.review_mode, "draft", head_sha)
    if head_sha == "":
        return PolicyDecision(False, request.review_mode, "unsafe_pr", "")
    if request.review_mode == "skip":
        return PolicyDecision(False, "skip", "skip", head_sha)
    if request.review_mode == "request" or request.force_run:
        return PolicyDecision(True, "request", "request", head_sha)
    return _automatic_decision(request, head_sha)
```

`_automatic_decision` must require real booleans when a key exists, choose the per-workflow value first, then global `review.auto`, and return `default_auto_true` only when both are absent.

- [ ] **Step 4: Add the JSON command boundary and composite action**

The Python entry point accepts `--request-file`, `--result-file`, and `--github-output`, writes a sorted compact JSON result, and appends these exact output keys with scalar values. The composite action declares inputs `workflow-name`, `pr-number`, `review-mode`, `force-run`, `force-review`, and `github-token`; it:

```yaml
runs:
  using: composite
  steps:
    - id: resolve
      shell: bash
      env:
        GH_TOKEN: ${{ inputs.github-token }}
        PR_NUMBER: ${{ inputs.pr-number }}
        REVIEW_MODE: ${{ inputs.review-mode }}
        WORKFLOW_NAME: ${{ inputs.workflow-name }}
        FORCE_RUN: ${{ inputs.force-run }}
        FORCE_REVIEW: ${{ inputs.force-review }}
      run: |
        set -euo pipefail
        policy_dir="$RUNNER_TEMP/review-policy-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
        install -d -m 0700 "$policy_dir"
        gh api "repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}" > "$policy_dir/pr.json"
        ruby -ryaml -rjson -e 'cfg = File.file?(ARGV[0]) ? (YAML.safe_load_file(ARGV[0], aliases: false) || {}) : {}; File.write(ARGV[1], JSON.generate(cfg))' \
          .github/workflow-config.yml "$policy_dir/config.json"
        python3 - "$policy_dir/pr.json" "$policy_dir/config.json" "$policy_dir/request.json" <<'PY'
        import json
        import os
        import sys
        from pathlib import Path

        def boolean(name: str) -> bool:
            value = os.environ[name]
            if value not in {"true", "false"}:
                raise SystemExit(f"{name.lower()}_invalid")
            return value == "true"

        pr_path, config_path, request_path = map(Path, sys.argv[1:])
        payload = {
            "workflow_name": os.environ["WORKFLOW_NAME"],
            "review_mode": os.environ["REVIEW_MODE"],
            "force_run": boolean("FORCE_RUN"),
            "force_review": boolean("FORCE_REVIEW"),
            "event_name": os.environ["GITHUB_EVENT_NAME"],
            "repository": os.environ["GITHUB_REPOSITORY"],
            "pr": json.loads(pr_path.read_text(encoding="utf-8")),
            "config": json.loads(config_path.read_text(encoding="utf-8")),
        }
        request_path.write_text(
            json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n",
            encoding="utf-8",
        )
        PY
        python3 "$GITHUB_ACTION_PATH/resolve_review_policy.py" \
          --request-file "$policy_dir/request.json" \
          --result-file "$policy_dir/result.json" \
          --github-output "$GITHUB_OUTPUT"
```

The transport passes scalars through environment variables and JSON through files; it never interpolates API JSON into shell code.

- [ ] **Step 5: Add transport and malformed-input tests**

Parse `action.yml` with `yaml.BaseLoader` and assert exact inputs, outputs, `using: composite`, `set -euo pipefail`, the two fixed API/config paths, and absence of `eval`. Add CLI tests proving malformed JSON, a non-object config, a non-boolean `review.auto`, invalid SHA, and invalid repository name exit nonzero without writing GitHub outputs.

- [ ] **Step 6: Run focused tests and commit**

Run:

```bash
rtk python3 -m pytest tests/test_review_policy_action.py -q
rtk git add .github/actions/resolve-review-policy tests/test_review_policy_action.py
rtk git commit -m "feat(review): add shared review policy resolver"
```

Expected: all policy tests pass and the commit contains only the new action and tests.

---

### Task 2: Gate Claude, Gemini, and OpenCode with the shared policy

**Files:**
- Modify: `.github/workflows/claude-code-review.yml`
- Modify: `.github/workflows/gemini-auto-review.yml`
- Modify: `.github/workflows/opencode-auto-review.yml`
- Modify: `tests/test_review_workflow_logic.py`

**Interfaces:**
- Consumes: Task 1 action outputs and existing `check-workflow-enabled` output.
- Produces: all provider jobs require `enabled == 'true' && policy_run == 'true'`.
- Adds reusable input: `review_mode` string, default `auto`, to all three workflows.
- Adds OpenCode reusable input: `force_review` boolean, default `false`.

- [ ] **Step 1: Write RED workflow-parity tests**

Add tests that load all three workflows and assert:

```python
REVIEW_WORKFLOWS = {
    name: _load(f"{name}.yml")
    for name in ("claude-code-review", "gemini-auto-review", "opencode-auto-review")
}


def _step(workflow, name):
    return next(
        step
        for step in workflow["jobs"]["check-enabled"]["steps"]
        if step.get("name") == name
    )


for workflow_name, workflow in REVIEW_WORKFLOWS.items():
    mode = workflow["on"]["workflow_call"]["inputs"]["review_mode"]
    assert mode == {"description": "Resolved PR review policy", "type": "string", "required": "false", "default": "auto"}
    policy = _step(workflow, "Resolve PR review policy")
    assert policy["uses"] == "$/.github/actions/resolve-review-policy"
    assert policy["with"]["review-mode"] == "${{ inputs.review_mode }}"


def test_every_provider_job_is_policy_gated():
    assert "needs.check-enabled.outputs.policy_run == 'true'" in claude["jobs"]["claude-review"]["if"]
    assert "needs.check-enabled.outputs.policy_run == 'true'" in gemini["jobs"]["gemini-review"]["if"]
    assert "needs.check-enabled.outputs.policy_run == 'true'" in opencode["jobs"]["opencode-prepare"]["if"]
```

Also assert the old three inline `Check auto review mode` steps are absent.

- [ ] **Step 2: Run focused tests and confirm the missing-input/action failures**

Run:

```bash
rtk python3 -m pytest tests/test_review_workflow_logic.py -q -k 'review_policy or force_review'
```

Expected: failures show missing `review_mode`, missing shared-action references, and missing OpenCode `force_review` plumbing.

- [ ] **Step 3: Replace duplicated automatic-mode scripts**

In every `check-enabled` job:

- add outputs `policy_run`, `policy_reason`, and `policy_head` from `steps.review_policy.outputs`;
- keep `Check workflow config` for `workflows.<name>.enabled`;
- remove `Check auto review mode`; and
- add `Resolve PR review policy` with exact workflow name, PR number, token, mode, force-run, and force-review values.

For OpenCode, retain its same-repository defense but make the shared action authoritative and remove the now-duplicate `Verify same-repository PR` step/output. Update the first provider-bearing job condition to use `policy_run` and remove `auto_enabled`/`safe_pr`.

- [ ] **Step 4: Add bounded OpenCode same-head review**

Add `force_review` to OpenCode and wire it exactly where Claude/Gemini already do:

```yaml
force_review:
  description: Perform one explicitly authorized review even when HEAD is unchanged
  type: boolean
  required: false
  default: false
```

Pass `force-full: ${{ inputs.force_review && 'true' || 'false' }}` to `prepare-review-diff`, pass `force-review: ${{ inputs.force_review && 'true' || 'false' }}` to the claim action, and add an enforcement step that fails only when an explicit force request was not authorized by the invocation budget. Do not alter ordinary authenticated reuse behavior.

- [ ] **Step 5: Prove skip/draft/conflict cannot reach providers**

Add static path tests that start from each provider action/CLI step and assert every job dependency includes the policy-gated first job. Assert `resolve-review-policy` appears exactly once per reusable workflow, before diff preparation, and that conflict cannot be converted to a skipped step via an `if` condition on the policy step.

- [ ] **Step 6: Run reviewer and invocation-budget suites**

Run:

```bash
rtk python3 -m pytest tests/test_review_workflow_logic.py tests/test_review_invocation_budget.py tests/test_review_invocation_budget_action.py -q
```

Expected: all tests pass; no finding/canonicalization snapshot changes.

- [ ] **Step 7: Commit reusable workflow integration**

```bash
rtk git add .github/workflows/claude-code-review.yml .github/workflows/gemini-auto-review.yml .github/workflows/opencode-auto-review.yml tests/test_review_workflow_logic.py
rtk git commit -m "feat(review): gate model invocation by PR policy"
```

---

### Task 3: Update managed callers, self-dogfood, and the exact catalog

**Files:**
- Modify: `.github/workflows/_self-claude-review.yml`
- Modify: `.github/workflows/_self-gemini-auto-review.yml`
- Modify: `.github/workflows/_self-opencode-auto-review.yml`
- Modify: `examples/baseline-workflows/.github/workflows/claude-code-review.yml`
- Modify: `examples/baseline-workflows/.github/workflows/gemini-auto-review.yml`
- Modify: `examples/baseline-workflows/.github/workflows/opencode-auto-review.yml`
- Modify: `examples/baseline-workflows/.github/workflow-config.yml`
- Modify: `.github/workflow-config.yml`
- Modify: `scripts/workflow-catalog.json`
- Modify: `tests/test_canonical_workflow_tree.py`
- Modify: `tests/test_workflow_catalog.py`

**Interfaces:**
- Produces: event label expression -> reusable `review_mode`.
- Produces: `ready_for_review` trigger for all six PR callers.
- Produces: manual `force_review` dispatch for all three baseline reviewers.

- [ ] **Step 1: Write RED exact-caller tests**

Extend canonical-tree tests with this exact trigger and input contract:

```python
assert caller["on"]["pull_request"]["types"] == ["opened", "synchronize", "ready_for_review"]
assert caller["jobs"][job]["with"]["review_mode"] == REVIEW_MODE_EXPRESSION
assert "github.event.pull_request.draft == false" in caller["jobs"][job]["if"]
```

Define `REVIEW_MODE_EXPRESSION` once as the whitespace-normalized expression whose precedence is workflow dispatch request, both-label conflict, request, skip, then auto. Add OpenCode dispatch assertions matching Claude/Gemini's `pr_number` and `force_review` shapes.

- [ ] **Step 2: Run caller/catalog tests and confirm RED**

```bash
rtk python3 -m pytest tests/test_canonical_workflow_tree.py tests/test_workflow_catalog.py -q
```

Expected: all six callers lack `ready_for_review`/`review_mode`; OpenCode lacks dispatch.

- [ ] **Step 3: Implement the label-derived expression in baseline callers**

Use this semantic ordering in each baseline caller:

```yaml
review_mode: >-
  ${{
    github.event_name == 'workflow_dispatch' && inputs.force_review && 'request' ||
    contains(github.event.pull_request.labels.*.name, 'review:request') &&
    contains(github.event.pull_request.labels.*.name, 'review:skip') && 'conflict' ||
    contains(github.event.pull_request.labels.*.name, 'review:request') && 'request' ||
    contains(github.event.pull_request.labels.*.name, 'review:skip') && 'skip' ||
    'auto'
  }}
```

The pull-request arm of every job `if` must require same-repository, non-fork, and `draft == false`. The workflow-dispatch arm remains authorized only when `force_review` is true.

- [ ] **Step 4: Update self callers without adding manual dispatch**

Add `ready_for_review`, the non-draft condition, and the same label expression without the dispatch arm. Pass `force_review: false` where the reusable declares it. Preserve existing secret-presence preflight jobs for Gemini and OpenCode.

- [ ] **Step 5: Update the catalog and explicit defaults**

Change only the three auto-review entries:

- add `ready_for_review` to pull-request types;
- add `review_mode` to each caller `with` list;
- add OpenCode `workflow_dispatch` inputs identical to Claude/Gemini; and
- add OpenCode `force_review` to its caller `with` list.

Keep baseline `review.auto: false`. Add this explicit section to automation's root config to replace compatibility inference:

```yaml
review:
  auto: false
```

- [ ] **Step 6: Run exact tree, catalog, and YAML suites**

```bash
rtk python3 -m pytest tests/test_canonical_workflow_tree.py tests/test_workflow_catalog.py -q
rtk python3 -c 'from pathlib import Path; import yaml; paths=sorted(Path(".github/workflows").glob("*.yml"))+sorted(Path("examples/baseline-workflows/.github/workflows").glob("*.yml")); [(_ for _ in ()).throw(AssertionError(p)) for p in paths if not isinstance(yaml.load(p.read_text(), Loader=yaml.BaseLoader), dict)]; print(len(paths))'
```

Expected: both commands exit zero.

- [ ] **Step 7: Commit caller and catalog parity**

```bash
rtk git add .github/workflows/_self-claude-review.yml .github/workflows/_self-gemini-auto-review.yml .github/workflows/_self-opencode-auto-review.yml .github/workflow-config.yml examples/baseline-workflows/.github scripts/workflow-catalog.json tests/test_canonical_workflow_tree.py tests/test_workflow_catalog.py
rtk git commit -m "feat(review): pass label policy through managed callers"
```

---

### Task 4: Configure external App opt-in behavior and document operations

**Files:**
- Create: `.gemini/config.yaml`
- Modify: `docs/workflows/contracts.md`
- Modify: `tests/test_canonical_workflow_tree.py`

**Interfaces:**
- Produces: Gemini Code Assist does not review on PR open in automation; `/gemini review` remains available.
- Records: Codex Code review enabled + Automatic reviews disabled is a required operator confirmation.

- [ ] **Step 1: Write RED repository-config tests**

Add:

```python
def test_automation_gemini_app_is_manual_review_only():
    config = yaml.safe_load((ROOT / ".gemini/config.yaml").read_text())
    assert config == {"code_review": {"pull_request_opened": {"code_review": False}}}
    assert config["code_review"].get("disable") is None
```

- [ ] **Step 2: Run the test and confirm the missing file failure**

```bash
rtk python3 -m pytest tests/test_canonical_workflow_tree.py -q -k gemini_app
```

Expected: failure reports missing `.gemini/config.yaml`.

- [ ] **Step 3: Add the minimal Gemini configuration**

Create exactly:

```yaml
code_review:
  pull_request_opened:
    code_review: false
```

Do not set `code_review.disable` and do not add summary/help/ignore defaults.

- [ ] **Step 4: Document operator and command boundaries**

In `docs/workflows/contracts.md`, add sections that state:

- labels control only managed Claude/Gemini/OpenCode workflows;
- `/jhw:pr` posts `@codex review` and `/gemini review` after the final ready head;
- Codex Code review remains enabled while Automatic reviews is disabled in ChatGPT Codex settings;

> **Superseded 2026-09-03:** Codex Automatic reviews is now **enabled**, so Codex reviews every
> pull request alongside the managed reviewers. The operator confirmation described below is no
> longer required. See `docs/workflows/contracts.md` for the current policy.
- Gemini uses only `pull_request_opened.code_review: false` and preserves all unrelated existing keys;
- `review:skip` cannot cancel an already-started App or workflow review; and
- fleet activation stops without Codex operator confirmation and mechanically verified Gemini config.

- [ ] **Step 5: Run docs/config tests and commit**

```bash
rtk python3 -m pytest tests/test_canonical_workflow_tree.py -q
rtk git add .gemini/config.yaml docs/workflows/contracts.md tests/test_canonical_workflow_tree.py
rtk git commit -m "docs(review): define manual GitHub App review mode"
```

---

### Task 5: Close the immutable `v1.51` release boundary

**Files:**
- Modify: `scripts/workflow_release_inventory.py`
- Modify: `scripts/workflow_release_bundle.py`
- Modify: `scripts/verify_workflow_release.py`
- Modify: `tests/test_workflow_release_bundle.py`
- Modify: `tests/test_verify_workflow_release.py`
- Modify: `docs/workflows/contracts.md`

**Interfaces:**
- Produces: `release_supports_review_policy(ref: str) -> bool` with boundary `(1, 51)`.
- Produces: exact `100644` roots for policy `action.yml` and `resolve_review_policy.py` in `v1.51+`.
- Verifies: each auto-review reusable uses the policy action once; each caller passes the mode and ready trigger exactly.

- [ ] **Step 1: Write RED inventory capability tests**

```python
def test_review_policy_release_boundary():
    assert release_supports_review_policy("v1.50") is False
    assert release_supports_review_policy("v1.51") is True
    paths = {root.path.as_posix() for root in release_roots_for("v1.51")}
    assert {
        ".github/actions/resolve-review-policy/action.yml",
        ".github/actions/resolve-review-policy/resolve_review_policy.py",
    } <= paths
```

Add a historical fake-tree test that removes both new files and verifies `v1.50`, plus a `v1.51` test that rejects either missing file, wrong mode, or an extra file under the action directory.

- [ ] **Step 2: Add release inventory roots and update bundle defaults**

Define `REVIEW_POLICY_RELEASE = (1, 51)`, two exact roots, `REVIEW_POLICY_ROOTS`, and append them only when `release_supports_review_policy(ref)` is true. Change `_build_git_archive`, `_git_archive`, `_safe_member`, and `_extract_archive` convenience defaults from `v1.46` to `v1.51`; explicit historical callers continue passing their own ref.

- [ ] **Step 3: Write RED verifier mutation tests**

For commit-only `v1.51`, mutate one property at a time and assert rejection for:

- missing/extra/reordered action input or output;
- executable or symlink policy files;
- missing `set -euo pipefail`, fixed PR API fetch, or JSON-file transport;
- a reusable missing/duplicating the action;
- a provider job not depending on `policy_run`;
- a caller missing `ready_for_review`, draft guard, or `review_mode`;
- changed label spelling or expression precedence; and
- OpenCode manual dispatch not matching the Claude/Gemini shape.

- [ ] **Step 4: Implement exact action and workflow verification**

Add `EXPECTED_REVIEW_POLICY_ACTION` as the `yaml.BaseLoader` representation and authenticate the Python blob from the commit tree. Require these literals/signatures in the helper:

```text
PolicyError
PolicyRequest
PolicyDecision
resolve_policy
review:request
review:skip
review_label_conflict
review_mode_label_mismatch
workflow_auto_false
review_auto_false
default_auto_true
```

Compile the helper, require the exact public dataclass fields, and add the policy action to the approved local-action list only for `v1.51+`.

- [ ] **Step 5: Run inventory and verifier suites**

```bash
rtk python3 -m pytest tests/test_workflow_release_bundle.py tests/test_verify_workflow_release.py -q
```

Expected: all historical release cases remain green and all `v1.51` mutations fail closed.

- [ ] **Step 6: Commit release enforcement**

```bash
rtk git add scripts/workflow_release_inventory.py scripts/workflow_release_bundle.py scripts/verify_workflow_release.py tests/test_workflow_release_bundle.py tests/test_verify_workflow_release.py docs/workflows/contracts.md
rtk git commit -m "feat(review): close v1.51 policy release contract"
```

---

### Task 6: Verify, review, merge, and tag the automation implementation

**Files:**
- Modify only when a failing verification proves a defect in Tasks 1-5.
- Preserve all unrelated root worktree files and branches.

**Interfaces:**
- Produces: merged automation feature PR and immutable annotated `v1.51` tag.
- Canary A: `review:request` overrides automation's explicit auto-off setting.

- [ ] **Step 1: Run focused syntax and contract suites**

```bash
rtk python3 -m py_compile .github/actions/resolve-review-policy/resolve_review_policy.py
rtk python3 -m pytest tests/test_review_policy_action.py tests/test_review_workflow_logic.py tests/test_canonical_workflow_tree.py tests/test_workflow_catalog.py tests/test_workflow_release_bundle.py tests/test_verify_workflow_release.py -q
```

Expected: both commands exit zero.

- [ ] **Step 2: Run the complete repository verification**

```bash
rtk python3 -m pytest -q
rtk actionlint -shellcheck= -pyflakes= .github/workflows/*.yml examples/baseline-workflows/.github/workflows/*.yml
rtk git diff --check origin/main...HEAD
```

Expected: full pytest and actionlint pass; no whitespace errors.

- [ ] **Step 3: Verify proposed `v1.51` content against the literal head SHA**

Pass the exact current 40-character commit directly to the verifier:

```bash
rtk python3 -m scripts.verify_workflow_release --automation . --ref v1.51 --expected-commit "$(rtk git rev-parse HEAD)" --commit-only
```

Expected output starts `PASS: v1.51 commit content is secure at` and ends with the SHA printed by `rtk git rev-parse HEAD`.

- [ ] **Step 4: Confirm App preconditions before opening the ready PR**

Mechanically verify `.gemini/config.yaml` and ask the operator for one explicit confirmation that Codex Code review is enabled and Automatic reviews is disabled for `jhw7500/automation`. Stop before `gh pr ready` if confirmation is unavailable.

- [ ] **Step 5: Open the implementation PR as draft and apply request before ready**

```bash
rtk git push -u origin feat/review-mode-command-control
rtk gh pr create --repo jhw7500/automation --base main --head feat/review-mode-command-control --draft --title "feat(review): add command-controlled review mode" --body "Adds the review label policy, draft-safe callers, external App opt-in configuration, and the immutable v1.51 release boundary.\n\nSpec: docs/superpowers/specs/2026-08-31-review-mode-command-control-design.md"
rtk gh label list --repo jhw7500/automation --limit 100 --json name,description,color
```

Run `rtk gh label create review:request --repo jhw7500/automation --color 0E8A16 --description "Explicitly request AI review"` only when that label is absent, and the corresponding `review:skip` command with color `BFDADC` and description `Explicitly skip AI review` only when it is absent. Add only `review:request` to the draft, verify labels/head through `gh pr view --json labels,headRefOid,isDraft`, then run:

```bash
rtk gh pr ready "$(rtk gh pr view --repo jhw7500/automation --json number -q .number)" --repo jhw7500/automation
```

- [ ] **Step 6: Execute Canary A and obtain review**

Confirm the ready event creates exactly one eligible current-head run for each enabled managed reviewer even though `review.auto` is false. Post exactly one `@codex review` and one `/gemini review` comment with head-scoped hidden markers. Inspect every result; repair only validated findings with RED/GREEN commits and repeat current-head verification.

- [ ] **Step 7: Merge only the verified current head**

When required CI, target verification, managed reviewers, Codex, and Gemini Assist are terminal and no blocking finding remains, verify the PR head equals the last locally verified SHA and merge using the repository-supported merge method. Confirm `origin/main` contains that exact head ancestry.

- [ ] **Step 8: Create and verify immutable `v1.51`**

From a clean checkout of updated main:

```bash
rtk git tag -a v1.51 -m "automation workflows v1.51"
rtk git push origin refs/tags/v1.51
```

Run the remote verifier against the exact merged main:

```bash
rtk python3 -m scripts.verify_workflow_release --automation . --ref v1.51 --expected-commit "$(rtk git rev-parse origin/main)" --remote origin
```

Confirm local annotated tag, peeled commit, remote tag, and authenticated content all match. Never recreate the tag.

---

### Task 7: Validate skip behavior and advance the fleet default

**Files:**
- Modify after the immutable tag: `scripts/workflow-config.json`
- Modify after the immutable tag: `tests/test_workflow_catalog.py`
- Operational canary: one disposable automation PR carrying `review:skip`

**Interfaces:**
- Canary B: `review:skip` overrides an auto-on PR head and invokes zero AI providers/Apps.
- Produces: reviewed default-advance PR from `v1.50` to `v1.51`.

- [ ] **Step 1: Run the skip canary on a disposable branch**

Create a fresh branch from tagged/merged main, make one canary-only config commit that sets `review.auto: true`, push it, create a draft PR, apply only `review:skip`, verify it, and mark ready. Do not post App mentions and do not merge this temporary config change.

- [ ] **Step 2: Prove zero invocation**

For each self caller, record the policy job URL and `reason=skip`; verify no provider job started and no new Claude/Gemini/OpenCode/Codex/Gemini Assist review artifact exists for the canary SHA. Required policy checks must reach a successful terminal state. Close the disposable PR and delete only its explicit canary branch after attaching the evidence to the implementation PR or release notes.

- [ ] **Step 3: Write RED fleet-default assertion**

Change the catalog test expectation to:

```python
assert config.automation_ref == "v1.51"
```

Run `rtk python3 -m pytest tests/test_workflow_catalog.py -q` and confirm it fails while config remains `v1.50`.

- [ ] **Step 4: Advance only the fleet default**

Change exactly `scripts/workflow-config.json` `automation_ref` to `v1.51`, run the catalog test and complete pytest, then commit:

```bash
rtk git add scripts/workflow-config.json tests/test_workflow_catalog.py
rtk git commit -m "chore(workflows): default rollout to v1.51"
```

- [ ] **Step 5: Open, review, and merge the default PR**

Push a dedicated `chore/workflow-default-v1.51` branch, open a normal PR, and require the same current-head CI/review verification. The immutable `v1.51` tag must continue pointing to the feature merge, not this later default commit.

- [ ] **Step 6: Prepare jhw-notion as the first pinned consumer**

If Gemini Code Assist is installed on `jhw7500/jhw-notion`, open and merge one setup PR that creates or merge-preserves `.gemini/config.yaml` while changing only `code_review.pull_request_opened.code_review` to false. Record operator confirmation for the jhw-notion Codex Automatic reviews setting when Codex is installed.

From a new `rtk mktemp -d /tmp/automation-v151-jhw-notion.XXXXXX` directory, run `scripts/rollout_workflow_fleet.py` first with `--mode plan --ref v1.51 --repo jhw-notion --initialize-workspace`, inspect the exact managed diff, then rerun the same workspace with `--mode publish --ref v1.51 --repo jhw-notion --confirm`. Review and merge that rollout PR only after actionlint, immutable pins, auth profile, `ready_for_review`, and `review_mode` are verified.

- [ ] **Step 7: Hand off to the JHW command plan**

Record the exact `v1.51` peeled commit, implementation PR URL, default PR URL, request canary run URLs, skip canary policy URLs, jhw-notion setup/rollout PR URLs, and Codex/Gemini setting evidence. The next plan may start only when these coordinates are available and the tag verifier passes remotely.
