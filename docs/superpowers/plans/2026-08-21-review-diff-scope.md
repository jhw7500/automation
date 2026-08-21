# Review Diff and Scope Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every automated reviewer an exact, fail-closed PR input while preserving unusual paths and requiring new findings to demonstrate PR causality.

**Architecture:** A focused composite action owned by the automation repository prepares full and incremental diffs plus a machine-readable scope manifest. Called workflows reference the action through GitHub's `$/` self-repository syntax, so the helper travels with the same immutable workflow commit while the working tree remains the consumer repository. Reviewer prompts consume only prepared artifacts; lookup failure falls back to the authoritative full PR diff, never an unrestricted commit range.

**Tech Stack:** GitHub composite actions, Python 3 standard library, Git/GitHub CLI, GitHub Actions YAML, pytest, repository release-verifier fixtures.

**Spec:** `docs/superpowers/specs/2026-08-21-review-state-scope-hardening-design.md`

## Final-audit amendment (normative)

This amendment supersedes any conflicting implementation detail in the task transcript below:

- `review-full.diff` is the unrestricted local Git diff of the exact verified
  `merge-base..captured-head` object graph. `review-scope.json` is parsed NUL-safely from the same
  range's local `--name-status -z --find-renames` output. Pulls Files and `gh pr diff` are not
  preparation inputs or fallbacks. The final metadata read is only an equality gate. Delta paths
  come from the immutable final manifest; an incremental/argv failure reuses the prepared full
  diff, while a local full/manifest/object failure is unavailable. All diffs override submodule
  ignore configuration and object-identity probes ignore replace refs. Tests cover ABA, 3,001
  files, restored-out delta paths, malformed paths, and non-UTF-8 fail-closed behavior.
- The OpenCode model job has empty permissions, no checkout or GitHub token, and runs pinned
  v1.18.17's generic `opencode run` in a fresh non-repository directory with the prompt on stdin,
  only the sealed full diff/scope attachments, pure/project-config-disabled mode, disabled sharing,
  and denied tools. Every non-empty stdout line must be a JSON object and the last completed text
  event becomes an untrusted candidate artifact.
- The clean canonicalizer accepts only the exact candidate artifact ID/digest/run/name, exact
  one-regular-file inventory, strict UTF-8, and 1..60,000 bytes. It validates the completed
  canonical body at no more than 65,536 UTF-8 bytes before mutation. Comment-shaped model-window
  bytes never supply the candidate, and cleanup remains bounded under exact-nonce floods.
- First reviews forbid `Still open`, `Resolved`, and `Retracted`. Rereview carryover headings must
  bind one-to-one to a unique active finding from authenticated prior state; `Still open` also
  needs a current changed anchor and an old active heading cannot be laundered into `New findings`.
- OpenCode anchors use the exact canonical one-line JSON form
  `- Changed anchor: {"path":"path/to/file","line":1}`. Strict canonical serialization, exact
  keys/types, manifest identity, and literal Git argv make all UTF-8 paths reversible while
  duplicate keys, reversed key order, and malformed/noncanonical encodings fail closed. The clean
  canonicalizer re-derives one exact NUL-delimited name-status record and its added hunks from the
  same immutable merge-base/head graph. Rename/copy checks use both old and current paths, and both
  queries force no replace objects, external diff/text conversion, 50% rename detection, and
  `--ignore-submodules=none`; the hunk query additionally forces zero inter-hunk context and no
  color. Canonicalization validates old/new counters through each complete unified hunk and records
  only actual `+` lines, never the hunk header's full span, context, deletion, or no-newline control
  records. A pure rename therefore contributes no reportable added line, and malformed/truncated
  patch output fails closed.
- The complete canonical state and worst-case fully wrapped 65,536-byte body are computed and
  checked before any cleanup or GitHub comment/Check mutation. An oversize prior body leaves even
  hostile marker comments untouched and fails closed.

The remaining checkbox steps document the original execution sequence. Where their Pulls Files,
numbered-diff, model-comment, or `path:line` instructions conflict with this amendment, they are
historical and must not be reintroduced.

## Global Constraints

- This plan starts only after `2026-08-21-review-state-integrity.md` passes.
- The helper has read-only repository/API behavior and no third-party Python dependency.
- Filenames are JSON-decoded and passed as `subprocess` argument elements; no newline-delimited path transport is permitted.
- A rename contributes both `previous_filename` and `filename` to path restriction.
- A failed PR-file lookup may use only the already prepared full PR diff.
- A failed authoritative full-diff preparation skips the model and cannot advance `Reviewed`.
- Claude keeps its ordinary context width; Gemini keeps `-U20`.
- New findings require a changed anchor or a concrete causal path from one.
- The action and workflows ship at one immutable automation commit.

---

### Task 1: Define the diff preparer's executable contract

**Files:**
- Create: `tests/test_prepare_review_diff.py`
- Create: `.github/actions/prepare-review-diff/prepare_review_diff.py`

**Interfaces:**
- CLI inputs: `--repository OWNER/REPO`, `--pr-number INT`, `--previous-sha SHA|''`, `--previous-full-hash HASH|''`, `--context-lines INT`, `--full-output PATH`, `--delta-output PATH`, `--manifest-output PATH`.
- Environment: `GH_TOKEN` for `gh`; current Git repository is the consumer checkout.
- JSON result: `diff_ready`, `diff_mode`, `head_sha`, `base_sha`, `full_diff_sha256`, `unchanged_since_previous`, and `warning`.
- Scope manifest: schema, repository, PR number, merge-base SHA, head SHA, and decoded file records with `status`, `filename`, and optional `previous_filename`.

- [ ] **Step 1: Write subprocess-backed unit tests**

Create local Git repositories and a PATH-shimmed `gh` fixture. Cover first-round full diff, valid incremental diff, previous non-ancestor fallback, PR-files API failure with numbered full-diff fallback, total preparation failure, equal metadata snapshots around mutable PR reads, and changed-head/base snapshots that fail closed.

```python
def run_prepare(repo: Path, gh_fixture: GhFixture, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repository", "o/r", "--pr-number", "7", *extra],
        cwd=repo,
        env=gh_fixture.env,
        text=True,
        capture_output=True,
    )

def test_pr_files_failure_uses_numbered_full_diff_not_commit_range(...):
    result = run_prepare(...)
    state = json.loads(result.stdout)
    assert state["diff_mode"] == "full"
    assert "OUT_OF_PR" not in full_output.read_text()
```

- [ ] **Step 2: Run tests and verify the helper is missing**

Run: `python3 -m pytest tests/test_prepare_review_diff.py -q`

Expected: tests fail because `prepare_review_diff.py` does not exist or exposes no CLI.

- [ ] **Step 3: Implement argument parsing and command boundaries**

Use a small immutable result type and a single subprocess boundary:

```python
@dataclass(frozen=True)
class PreparedReviewDiff:
    diff_ready: bool
    diff_mode: Literal["full", "delta", "unchanged", "unavailable"]
    head_sha: str
    base_sha: str
    full_diff_sha256: str
    unchanged_since_previous: bool
    warning: str = ""

def run(argv: Sequence[str], *, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(argv, cwd=cwd, check=check, capture_output=True)
```

Validate repository syntax, positive PR number, SHA/hash formats, non-negative context, and output paths before invoking Git or `gh`. Print one JSON object even on a controlled unavailable result; reserve a nonzero exit for invalid invocation or internal corruption.

- [ ] **Step 4: Implement metadata and PR-file retrieval**

Call `gh api repos/o/r/pulls/7` before and after the mutable PR inputs, and call `gh api repos/o/r/pulls/7/files --paginate --slurp` between those snapshots. Flatten page arrays, require string `filename`, include string `previous_filename` for renamed files, de-duplicate while preserving order, and retain decoded Python strings unchanged. Require both metadata responses to contain valid, identical base/head SHAs before returning any ready result; a changed or malformed snapshot removes staged outputs and returns `unavailable`.

- [ ] **Step 5: Implement full and incremental diff generation**

Fetch missing base/head/previous commits by literal SHA and determine `merge_base` with `git merge-base base head`. Invoke Git with an argument list:

```python
argv = [
    "git", "--literal-pathspecs", "diff", f"-U{context_lines}",
    f"{left}..{right}", "--", *paths,
]
```

For the full diff use `merge_base..head`; for delta use `previous..head` only after ancestor validation and only with a successfully fetched PR path set. If local full preparation fails, invoke `gh pr diff 7` with the explicit PR number. The final metadata snapshot must occur after any numbered fallback so its mutable bytes are covered by the same equality check. Atomically replace output files only after command success and snapshot agreement.

- [ ] **Step 6: Implement hashes and unchanged behavior**

Hash the full diff bytes with SHA-256. If it equals the validated previous hash, remove any delta output and return `diff_mode="unchanged"`. If delta bytes are empty but the full hash differs, return the full input rather than unchanged.

- [ ] **Step 7: Run and commit the base helper**

Run: `python3 -m pytest tests/test_prepare_review_diff.py -q`

```bash
git add tests/test_prepare_review_diff.py .github/actions/prepare-review-diff/prepare_review_diff.py
git commit -m "feat(review): prepare deterministic PR-scoped diffs"
```

### Task 2: Preserve unusual paths and Git object changes

**Files:**
- Modify: `tests/test_prepare_review_diff.py`
- Modify: `.github/actions/prepare-review-diff/prepare_review_diff.py`

**Interfaces:**
- Consumes: decoded REST file objects and the Task 1 argv-safe Git runner.
- Produces: complete diffs for unusual paths without broadening scope.

- [ ] **Step 1: Add mixed-path regression tests**

Create one normal changed file plus each of these in the same incremental range:

```python
special_paths = [
    "pages/[id].tsx",
    "emoji-한글-😀.py",
    "line\nbreak.py",
    "leading-dash/-n.txt",
]
```

Assert every intended change appears and an out-of-PR decoy does not. Feed file objects as JSON, not pre-decoded newline text.

- [ ] **Step 2: Add rename and object-mode regression tests**

Cover rename with `previous_filename`, deletion, binary change, executable-mode change, symlink target change, and submodule pointer change. For rename assert the diff contains `rename from` and `rename to`, not `new file mode` for the destination.

- [ ] **Step 3: Run tests and observe current gaps**

Run: `python3 -m pytest tests/test_prepare_review_diff.py -k 'unicode or newline or rename or binary or submodule or symlink' -q`

- [ ] **Step 4: Complete path and size handling**

Pass paths directly in argv. Calculate encoded argv size against `os.sysconf("SC_ARG_MAX")` with a fixed environment allowance. If paths exceed the safe bound, use the already prepared full PR diff; never batch fragments in a way that loses cross-path rename detection.

- [ ] **Step 5: Run and commit**

Run: `python3 -m pytest tests/test_prepare_review_diff.py -q`

```bash
git add tests/test_prepare_review_diff.py .github/actions/prepare-review-diff/prepare_review_diff.py
git commit -m "fix(review): preserve unusual paths in review deltas"
```

### Task 3: Package the helper as a self-repository composite action

**Files:**
- Create: `.github/actions/prepare-review-diff/action.yml`
- Modify: `tests/test_workflow_release_bundle.py`
- Modify: `tests/test_verify_workflow_release.py`
- Modify: `scripts/verify_workflow_release.py`

**Interfaces:**
- Consumes: action inputs `github-token`, `pr-number`, `previous-sha`, `previous-full-hash`, `context-lines`, and output paths.
- Produces: action outputs matching `PreparedReviewDiff` plus files in `github.workspace`.

- [ ] **Step 1: Add failing action metadata and release-bundle tests**

Assert `action.yml` is composite, all inputs/outputs exist, the Python script is bundled, and the release verifier rejects a tag missing either action file.

- [ ] **Step 2: Run tests and verify the package is incomplete**

Run: `python3 -m pytest tests/test_workflow_release_bundle.py tests/test_verify_workflow_release.py -k 'prepare_review_diff' -q`

- [ ] **Step 3: Implement `action.yml`**

```yaml
name: Prepare review diff
description: Prepare a fail-closed full or incremental PR diff
inputs:
  github-token:
    required: true
  pr-number:
    required: true
  previous-sha:
    required: false
    default: ''
  previous-full-hash:
    required: false
    default: ''
  context-lines:
    required: false
    default: '3'
outputs:
  diff-ready:
    value: ${{ steps.prepare.outputs.diff_ready }}
  diff-mode:
    value: ${{ steps.prepare.outputs.diff_mode }}
  head-sha:
    value: ${{ steps.prepare.outputs.head_sha }}
  full-diff-sha256:
    value: ${{ steps.prepare.outputs.full_diff_sha256 }}
runs:
  using: composite
  steps:
    - id: prepare
      shell: bash
      env:
        GH_TOKEN: ${{ inputs.github-token }}
      run: >-
        python3 "$GITHUB_ACTION_PATH/prepare_review_diff.py"
        --repository "$GITHUB_REPOSITORY"
        --pr-number "${{ inputs.pr-number }}"
        --previous-sha "${{ inputs.previous-sha }}"
        --previous-full-hash "${{ inputs.previous-full-hash }}"
        --context-lines "${{ inputs.context-lines }}"
        --full-output "$GITHUB_WORKSPACE/review-full.diff"
        --delta-output "$GITHUB_WORKSPACE/review-delta.diff"
        --manifest-output "$GITHUB_WORKSPACE/review-scope.json"
        --github-output "$GITHUB_OUTPUT"
```

Use only environment or quoted action inputs; do not interpolate PR-controlled values into shell source.

- [ ] **Step 4: Extend the release verifier's required tree**

Add both action files with regular-file mode `100644` to the authoritative release bundle. Keep the manifest list centralized in the existing verifier structure rather than adding a second file list.

- [ ] **Step 5: Run and commit**

Run: `python3 -m pytest tests/test_workflow_release_bundle.py tests/test_verify_workflow_release.py -q`

```bash
git add .github/actions/prepare-review-diff/action.yml scripts/verify_workflow_release.py tests/test_workflow_release_bundle.py tests/test_verify_workflow_release.py
git commit -m "feat(review): package deterministic diff preparation"
```

### Task 4: Wire Claude and Gemini to the shared action

**Files:**
- Modify: `.github/workflows/claude-code-review.yml:119-300`
- Modify: `.github/workflows/gemini-auto-review.yml:110-310`
- Modify: `tests/test_review_workflow_logic.py`

**Interfaces:**
- Consumes: validated previous v2 state from the first plan and action outputs from Task 3.
- Produces: reviewer-specific model input chosen from `review-delta.diff` or `review-full.diff`; v2 state records action mode and full hash.

- [ ] **Step 1: Add failing workflow-wiring tests**

Assert each workflow invokes exactly `uses: $/.github/actions/prepare-review-diff`, passes its PR number and validated previous state, uses context `3` for Claude and `20` for Gemini, and removes `gh pr diff --name-only`, `xargs -d`, and unrestricted `PREV..HEAD` fallback.

- [ ] **Step 2: Run focused tests**

Run: `python3 -m pytest tests/test_review_workflow_logic.py -k 'shared_diff or diff_wiring' -q`

- [ ] **Step 3: Replace Claude's inline diff block**

Invoke the action after state collection. Update the prompt to read the shared filenames. Gate the model on `diff-ready == 'true'` and `diff-mode != 'unchanged'`.

On `unchanged`, skip the model and call upsert with `UNCHANGED_SINCE_PREVIOUS=true`; preserve the body and advance only after current-head/run-order checks.

- [ ] **Step 4: Replace Gemini's inline diff block**

Apply the same wiring with `context-lines: '20'`. Retain title/body/human-comment preparation and Gemini retry code. The model reads exactly one prepared diff file.

- [ ] **Step 5: Persist full hash and mode in v2 state**

Feed `steps.prepare-diff.outputs.diff-mode` and `full-diff-sha256` into both upserts. Reject an unchanged shortcut unless existing state has a successful SHA, non-empty previous body, and matching full hash.

- [ ] **Step 6: Run and commit**

Run: `python3 -m pytest tests/test_review_workflow_logic.py tests/test_prepare_review_diff.py -q`

```bash
git add .github/workflows/claude-code-review.yml .github/workflows/gemini-auto-review.yml tests/test_review_workflow_logic.py
git commit -m "refactor(review): use shared PR-scoped diff input"
```

### Task 5: Bind OpenCode to the prepared PR input and finding scope

**Files:**
- Modify: `.github/workflows/opencode-auto-review.yml:75-280`
- Modify: `tests/test_review_workflow_logic.py:900-1040`

**Interfaces:**
- Consumes: prepared full diff and scope manifest from Task 3; canonical OpenCode state from the first plan.
- Produces: no OpenCode invocation without a ready PR input; changed-anchor-constrained new findings.

- [ ] **Step 1: Add failing OpenCode input and prompt-contract tests**

Assert the workflow uses the shared action, gates the CLI on `diff-ready`, supplies the selected prepared filename, and includes these semantic requirements:

```text
Every new finding must identify a Changed anchor at path:line from the prepared diff.
An unchanged line is supporting evidence only and requires a concrete causal explanation.
A disproven previous finding is Retracted, not Resolved.
```

Add a canonicalization fixture whose `### New findings` section lacks `Changed anchor:` and assert it cannot become a successful canonical review.

- [ ] **Step 2: Run focused tests**

Run: `python3 -m pytest tests/test_review_workflow_logic.py -k 'opencode and (scope or changed_anchor or diff_ready)' -q`

- [ ] **Step 3: Wire the shared action before the CLI**

Use context width `3`. Skip `opencode github run` when input is unavailable. Include the selected prepared diff filename and `review-scope.json` in the trusted prompt suffix, after all untrusted previous context.

- [ ] **Step 4: Strengthen and validate the output contract**

Require each non-`None` item under `### New findings` to include a line matching:

```regex
^\s*- Changed anchor: `?(?<location>.+:\d+)`?\s*$
```

Split `location` at its final `:<decimal line>` delimiter so colons remain legal inside the
path. Canonicalization checks that the decoded path exists in `review-scope.json`, then
derives zero-context added-side hunk ranges from the manifest's merge-base/head and verifies
the cited line is changed. Supporting unchanged evidence remains allowed only when the
finding also contains a valid changed anchor. Invalid new-finding output is recorded as a
failed attempt and cannot advance state.

- [ ] **Step 5: Run and commit**

Run: `python3 -m pytest tests/test_review_workflow_logic.py -k 'opencode' -q`

```bash
git add .github/workflows/opencode-auto-review.yml tests/test_review_workflow_logic.py
git commit -m "fix(review): bind OpenCode findings to PR changes"
```

### Task 6: Document, verify, and prepare canary evidence

**Files:**
- Modify: `docs/workflows/contracts.md`
- Modify: `.github/README.md` only if its reviewer-input description is stale
- Test: `tests/`

**Interfaces:**
- Consumes: Tasks 1–5 and the completed state-integrity plan.
- Produces: repository documentation, release-integrity evidence, and a canary checklist; no fleet mutation.

- [ ] **Step 1: Document deterministic diff semantics**

Describe full/delta/unchanged/unavailable modes, JSON path transport, rename handling, fail-closed behavior, the changed-anchor contract, and that `$/` resolves the helper from the immutable automation workflow commit.

- [ ] **Step 2: Run targeted tests**

```bash
python3 -m pytest tests/test_prepare_review_diff.py -q
python3 -m pytest tests/test_review_workflow_logic.py -q
python3 -m pytest tests/test_verify_workflow_release.py tests/test_workflow_release_bundle.py -q
```

- [ ] **Step 3: Run full verification**

```bash
python3 -m pytest tests/ -q
git diff --check
python3 -m compileall -q .github/actions/prepare-review-diff scripts tests
```

Run `actionlint` when available. If unavailable, state that in the handoff rather than claiming it passed.

- [ ] **Step 4: Independently review the completed range**

Request correctness and architecture reviews. Block release on any path that advances an uncovered checkpoint, accepts foreign state, writes from a stale run, broadens beyond PR scope, or loses a PR path.

- [ ] **Step 5: Commit documentation**

```bash
git add docs/workflows/contracts.md .github/README.md
git commit -m "docs(review): define deterministic review scope"
```

- [ ] **Step 6: Stop before external rollout**

Prepare—but do not execute without separate authorization—the dogfood PR, immutable tag, canary rollout, and fleet rollout. External PR creation, tag publication, and consumer-repository mutation are outside this local implementation step.
