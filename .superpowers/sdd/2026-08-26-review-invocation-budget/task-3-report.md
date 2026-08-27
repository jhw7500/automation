# Task 3 report: fail-closed composite action and CAS transport

## Status

COMPLETE pending task review. The original Task 3 implementer produced and staged the implementation, then the user status turn ended that subagent before it could commit or write this report. The controller preserved the exact staged files, reran all focused/static/full validation, performed the security self-review below, and committed the recovered task without changing the staged implementation.

## Files

- `.github/actions/review-invocation-budget/action.yml`
- `.github/actions/review-invocation-budget/review_invocation_budget.py`
- `tests/test_review_invocation_budget_action.py`

No reviewer workflow, release inventory, contract documentation, approved spec, plan, or unrelated path was changed.

## Implemented contract

- Added one composite action exposing required claim/finalize inputs and six deterministic outputs through one inert environment bridge.
- Added file-only CLI operations for provenance identity discovery, claim, finalize, and compare-and-swap failure recording.
- Added strict repository/PR/run/reviewer/head/hash/request validation, bounded regular-file input paths under the actual runner workspace, canonical embedded JSON parsing, and private atomic outputs.
- Added exact `github-actions[bot]` reviewer-ledger selection with duplicate/foreign-author fail-closed behavior.
- Added bounded Actions run-attempt provenance fetches and deduplicated collaborator permission lookups for override events.
- Added PR-head plus existing-comment-body or zero-comment compare-and-swap checks immediately before POST/PATCH mutation.
- A CAS mismatch performs no mutation, records `state_invalid/compare_and_swap_failed`, and forces `allow-invocation=false`.
- Outputs are written only after a successful mutation or deterministic refusal, and the complete checkpoint plus concise summary is written for the run.

## RED evidence

The original implementer reported this exact command before production implementation:

```text
rtk python3 -m pytest tests/test_review_invocation_budget_action.py -q
```

Result: all initial 10 tests failed. The failures were the expected missing `.github/actions/review-invocation-budget/action.yml` and missing `build_parser`/file-only CLI transport interfaces. Three additional action cases were added while completing GREEN coverage, bringing the final action module to 13 tests. The status turn removed the subagent before its detailed terminal transcript could be saved, so this report does not invent individual initial failure lines.

## GREEN evidence

Focused Tasks 1-3 suite, rerun by the controller against the exact staged implementation:

```text
rtk python3 -m pytest tests/test_review_invocation_budget.py tests/test_review_invocation_budget_action.py -q
.............................................................            [100%]
61 passed in 4.02s
```

Complete repository suite on the same staged state:

```text
rtk python3 -m pytest -q
........................................................................ [ 99%]
............                                                             [100%]
2530 passed, 48 subtests passed in 559.92s (0:09:19)
```

Static and serialization validation:

```text
rtk ruff check .github/actions/review-invocation-budget/review_invocation_budget.py tests/test_review_invocation_budget.py tests/test_review_invocation_budget_action.py
[]

rtk python3 -m py_compile .github/actions/review-invocation-budget/review_invocation_budget.py tests/test_review_invocation_budget.py tests/test_review_invocation_budget_action.py
# exit 0, no output

rtk python3 -c 'from pathlib import Path; import yaml; path=Path(".github/actions/review-invocation-budget/action.yml"); data=yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader); assert isinstance(data, dict); print("PASS action yaml")'
PASS action yaml

rtk git diff --cached --check
# exit 0, no output
```

Staged scope:

```text
.github/actions/review-invocation-budget/action.yml          | 290 lines added
.github/actions/review-invocation-budget/review_invocation_budget.py | 535 lines added
tests/test_review_invocation_budget_action.py                 | 408 lines added
```

## Security self-review

- Confirmed every action input expression appears only in the step `env` mapping; no `${{ inputs.* }}` expression appears in the shell body.
- Confirmed PR-controlled strings are stored in JSON files or passed as single quoted argv values; they are never sourced or evaluated.
- Confirmed repository, PR, run ID, run attempt, comment ID, and encoded actor values pass explicit formats before entering API endpoint strings.
- Confirmed API reads precede transition computation and a second head/comment read precedes mutation.
- Confirmed create-CAS refuses if any exact marker comment appears during the second read; patch-CAS requires exact comment ID, body, and bot login.
- Confirmed invalid, duplicate, untrusted, provenance-mismatched, stale-head, stale-comment, and workspace-escape cases make zero comment mutations and return no provider allowance.
- Confirmed temporary transport files use a mode-0700 directory and mode-0600 writes, with cleanup scoped to the validated `mktemp` directory.
- Confirmed input paths resolve under the actual `GITHUB_WORKSPACE` environment and reject symlinks/non-regular files.
- Confirmed Task 1 claim and Task 2 finalization/checkpoint suites remain green and no workflow/provider path was added in this task.

## Concerns

- Evidence limitation: the original implementer's detailed RED transcript was lost when the status turn ended its subagent. The initial command, 10/10 failure result, and expected missing interfaces came from its delivered status message; all GREEN/static/full evidence above was independently rerun and captured by the controller.

## Fix round 1: transport boundary and local diagnostics

### Changed contract

- The public `diff-mode` transport now accepts exactly the producer vocabulary
  `full|delta|unchanged|unavailable`; only `full` and `delta` map to the pure state
  machine's internal `changed` value.
- `unavailable` accepts empty head/hash identities and terminates locally with no GitHub
  API or comment mutation. It writes `allow-invocation=false`,
  `decision=diff_unavailable`, a deterministic summary, and a canonical newline-terminated
  diagnostic checkpoint with `schema=1`, `ledger=null`, and nullable safely parsed identity
  fields in `handoff`.
- Invalid mode, reviewer, repository, PR, run identity, head, hash, or diff mode now takes
  the same zero-API local refusal path with `decision=state_invalid`. The helper's `main`
  catches failures raised before `_transport_request` completes and writes the canonical
  diagnostic artifacts instead of exiting without evidence. An internal file-only
  `preflight` CLI operation performs this gate; the action's public modes remain only
  `claim` and `finalize`.
- Both the helper pagination reader and the inline create-CAS page decoder now require at
  least one decoded JSON array. The literal `[]` remains valid evidence; empty and
  whitespace-only files fail closed.
- The composite appends the summary to `GITHUB_STEP_SUMMARY` before it opens
  `GITHUB_OUTPUT` for action outputs.

### TDD evidence

RED, after adding the fix-round regression tests and before changing production code:

```text
rtk python3 -m pytest tests/test_review_invocation_budget_action.py -q
26 failed, 4 passed in 3.80s
```

The expected failures covered the old summary/output order, rejection of public `full` and
`delta`, missing local diagnostic artifacts for unavailable/invalid requests, direct helper
exit before refusal evidence, acceptance of empty pagination, and the empty create-CAS page
being treated as valid.

GREEN focused state/action gate:

```text
rtk python3 -m pytest tests/test_review_invocation_budget.py tests/test_review_invocation_budget_action.py -q
78 passed in 8.80s
```

GREEN full repository gate, run once against the final source/test implementation:

```text
rtk python3 -m pytest -q
2547 passed, 48 subtests passed in 573.37s (0:09:33)
```

Static and serialization gates:

```text
rtk python3 -m py_compile .github/actions/review-invocation-budget/review_invocation_budget.py tests/test_review_invocation_budget_action.py
# exit 0, no output

rtk ruff check .github/actions/review-invocation-budget/review_invocation_budget.py tests/test_review_invocation_budget_action.py
[]

rtk python3 -c 'from pathlib import Path; import yaml; path=Path(".github/actions/review-invocation-budget/action.yml"); data=yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader); assert isinstance(data, dict); print("PASS action yaml")'
PASS action yaml

rtk git diff --check
# exit 0, no output
```

The exact producer action and its `PreparedReviewDiff` result type were inspected; they
confirm the public four-value mode set and that `unavailable` may publish an empty full-diff
hash.

### Fix-round files changed

- `.github/actions/review-invocation-budget/action.yml`
- `.github/actions/review-invocation-budget/review_invocation_budget.py`
- `tests/test_review_invocation_budget_action.py`
- `.superpowers/sdd/2026-08-26-review-invocation-budget/task-3-report.md`
- `.superpowers/sdd/2026-08-26-review-invocation-budget/progress.md`

### Self-review and concerns

- Confirmed all endpoint-bearing shell occurs after the local helper writes
  `{"continue":true}` and that invalid/unavailable regression scenarios record zero fake
  `gh` calls.
- Confirmed no input expression moved into executable shell text and hostile strings still
  arrive as one quoted Python argument only.
- Confirmed valid transitions retain the strict `render_checkpoint`/`load_checkpoint`
  contract; only pre-identity diagnostics use `ledger:null`, which strict resume parsing
  rejects fail closed.
- No remaining implementation concern. A missing or unwritable `checkpoint-file` remains
  the unavoidable configuration-fatal case permitted by the controller ruling.

## Fix round 2: malformed request decode boundary

### Changed contract

- `main()` now catches request-file decoding and top-level-shape failures before attempting
  `_transport_request`.
- Malformed JSON, non-object JSON, and undecodable request bytes return success from the
  local helper after writing `checkpoint.json`, `summary.md`, `output.json`, and
  `preflight.json` in the explicit output directory.
- The diagnostic checkpoint is compact sorted-ASCII JSON with one trailing newline,
  `schema=1`, `ledger=null`, `decision=state_invalid`, and every unavailable identity field
  set to null.
- Because malformed bytes cannot safely provide a `checkpoint_file`, this branch does not
  invent or write an external checkpoint path. Safely decoded dictionaries retain the
  round-1 requirement that their configured external checkpoint be written, with a missing
  or unwritable path remaining configuration-fatal.
- Strict valid ledger transitions and `load_checkpoint` behavior are unchanged.

### TDD evidence

RED before the helper boundary fix:

```text
rtk python3 -m pytest tests/test_review_invocation_budget_action.py -q
3 failed, 30 passed in 7.43s
```

All three new cases raised `request_invalid` from `_raw_request` before writing artifacts:
malformed JSON, a JSON array instead of an object, and undecodable bytes.

GREEN action gate:

```text
rtk python3 -m pytest tests/test_review_invocation_budget_action.py -q
33 passed in 7.36s
```

GREEN focused Tasks 1-3 gate:

```text
rtk python3 -m pytest tests/test_review_invocation_budget.py tests/test_review_invocation_budget_action.py -q
81 passed in 7.29s
```

GREEN full repository gate, run once on the final code:

```text
rtk python3 -m pytest -q
2550 passed, 48 subtests passed in 528.57s (0:08:48)
```

Static and serialization gates:

```text
rtk python3 -m py_compile .github/actions/review-invocation-budget/review_invocation_budget.py tests/test_review_invocation_budget_action.py
# exit 0, no output

rtk ruff check .github/actions/review-invocation-budget/review_invocation_budget.py tests/test_review_invocation_budget_action.py
[]

rtk python3 -c 'from pathlib import Path; import yaml; path=Path(".github/actions/review-invocation-budget/action.yml"); data=yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader); assert isinstance(data, dict); print("PASS action yaml")'
PASS action yaml

rtk git diff --check
# exit 0, no output
```

### Fix-round files changed

- `.github/actions/review-invocation-budget/review_invocation_budget.py`
- `tests/test_review_invocation_budget_action.py`
- `.superpowers/sdd/2026-08-26-review-invocation-budget/task-3-report.md`
- `.superpowers/sdd/2026-08-26-review-invocation-budget/progress.md`

### Self-review and concerns

- The new optional external-write flag is used only when the request dictionary itself
  could not be recovered; all decoded-dictionary refusals keep the prior external
  checkpoint behavior.
- The malformed branch supplies an empty mapping to the bounded diagnostic renderer, so it
  cannot invent repository, reviewer, PR, run, head, or diff identities.
- No remaining concern.
