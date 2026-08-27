# Task 8 local report — close verifier breaker and run Steps 1–4

Date: 2026-08-27
Starting HEAD: `fc7f1b3ce6e82c970f53d9c8e3e1bcf2ee1a42e3`
Implementation commit: `8fa6bd6e1c62f92c787e212f8fd0876789ef3139`
Whitespace-gate correction commit: `0c63098a804a625bcce262cd0faed7115484a02c`

## Result

The carried load-bearing verifier finding is closed locally. The authenticated
OpenCode shell analyzer now recognizes heredocs from quote-aware executable `<<`
operator tokens rather than a raw regular-expression match, so quoted data such as
`"<<'true'"` cannot hide later executable statements. It also fails closed when
literal assignment/append statements construct `eval`, `source`, `.`, or
`run_opencode`, and when an indirect shell expansion occupies command position.
The exact computed-executor/target redefinition is therefore rejected without
interpreting untrusted shell code.

No Task 1–6 action, helper, or workflow byte was changed. The release inventory,
workflow contract, fleet config/default, and authenticated workflow digests remain
unchanged. No push, PR, merge, tag, release, rollout, GitHub mutation, or Notion
mutation was performed; the controller owns Task 8 Steps 5–9.

## TDD evidence

### RED

Two tests first committed bash-syntax-valid mutated workflow bytes into the release
fixture repository, updated the expected OpenCode SHA-256 to those exact bytes,
reopened them through `VerifiedCommitTree`, asserted the tree digest, and required the
semantic verifier to reject them:

- quoted here-doc-like data followed by a compact live `run_opencode` redefinition
  and the later quoted-data value as a normal `true` command;
- `e=e; e+=val; n=run_open; n+=code; "$e" "${n}(){…; }"`, which computes both
  the namespace executor and target name before redefining the function.

Command:

```text
rtk python3 -m pytest tests/test_verify_workflow_release.py::test_v147_opencode_call_cap_rejects_redefinition_after_quoted_heredoc_data tests/test_verify_workflow_release.py::test_v147_opencode_call_cap_rejects_computed_executor_redefinition -q --tb=short
```

Result before production changes: `2 failed in 0.50s`; both failures were
`Failed: DID NOT RAISE ReleaseVerificationError`. Both embedded `bash --noprofile
--norc -n -c` checks and both authenticated tree-digest assertions had already
passed, proving the semantic bypass rather than a malformed fixture or digest-only
failure.

### GREEN and focused regression

- The exact RED command after the structural correction: `2 passed in 0.39s`.
- `rtk python3 -m py_compile scripts/verify_workflow_release.py tests/test_verify_workflow_release.py`
  exited 0.
- Authentic workflow plus the complete prior OpenCode reachability/declaration/
  compact/dynamic parser matrix and both new cases: `17 passed in 3.41s`.
- The unchanged prior structural regression command covering exact action/boundary,
  release-root kinds, helper gates/live AST, workflow semantics/live reviewer caps,
  the unreachable-sequence mutation, and positive v1.47 candidate:
  `49 passed in 12.25s`.
- The previous 589 release/bundle cases plus the two new authenticated regressions:
  `591 passed in 270.89s (0:04:30)`.

## Task 8 Step 1 — helper syntax and five focused files

Final implementation HEAD `0c63098a804a625bcce262cd0faed7115484a02c`:

```text
rtk python3 -m py_compile .github/actions/review-invocation-budget/review_invocation_budget.py
```

Result: exit 0.

```text
rtk python3 -m pytest tests/test_review_invocation_budget.py tests/test_review_invocation_budget_action.py tests/test_review_workflow_logic.py tests/test_workflow_release_bundle.py tests/test_verify_workflow_release.py -q
```

Result: `2206 passed in 886.51s (0:14:46)`.

The same Step 1 commands had first passed on implementation commit `8fa6bd6e…`
(`2206 passed in 787.19s`); they were rerun from scratch after the Step 4
whitespace-only commit changed HEAD.

## Task 8 Step 2 — full Python, YAML, and actionlint

```text
rtk python3 -m pytest -q
```

Final-HEAD result: `2681 passed, 48 subtests passed in 703.88s (0:11:43)`.

```text
rtk python3 -c 'from pathlib import Path; import yaml; paths=sorted(Path(".github/workflows").glob("*.y*ml"))+sorted(Path("examples/baseline-workflows/.github/workflows").glob("*.y*ml"))+sorted(Path(".github/actions").glob("**/*.y*ml")); assert all(isinstance(yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader), dict) for path in paths); print(f"PASS: {len(paths)} YAML documents")'
```

Result: `PASS: 36 YAML documents`.

Actionlint 1.7.12 was downloaded from its upstream release archive. The archive
digest was checked twice and exactly matched the repository-pinned value
`8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8`;
`actionlint -version` reported `1.7.12`.

```text
rtk /tmp/actionlint-v1.7.12-task8.E6oGEB/actionlint -shellcheck= -pyflakes= .github/workflows/*.yml examples/baseline-workflows/.github/workflows/*.yml
```

Result: exit 0 with no diagnostics.

The full suite had first passed on `8fa6bd6e…` as `2681 passed, 48 subtests passed
in 819.01s`; the final-HEAD run above supersedes it.

## Task 8 Step 3 — mutation gate and committed-tree v1.47 verification

```text
rtk python3 -m pytest tests/test_verify_workflow_release.py -q -k 'v147 or invocation_budget or mutation'
```

Final-HEAD result: `73 passed, 450 deselected in 16.42s`.

```text
rtk python3 -m scripts.verify_workflow_release --automation . --ref v1.47 --expected-commit "$(rtk git rev-parse HEAD)" --commit-only
```

Result:

```text
PASS: v1.47 commit content is secure at 0c63098a804a625bcce262cd0faed7115484a02c
```

No tag was created or pushed.

## Task 8 Step 4 — exact scope and whitespace audit

The initial `rtk git diff --check origin/main...HEAD` on implementation commit
`8fa6bd6e…` correctly returned exit 2 for one pre-existing extra blank line at EOF
in the approved issue-52 design spec. Removing only that empty line was committed as
`0c63098 docs(spec): remove trailing blank line`; no prose or approved decision
changed. All Steps 1–4 were then rerun against that final implementation HEAD.

- `rtk git diff --check origin/main...HEAD` → exit 0, no diagnostics.
- `rtk git status --short --branch` → clean branch,
  `feat/52-review-invocation-budget...origin/main [ahead 33]`.
- `rtk git diff --stat origin/main...HEAD` → 21 files, 12,468 insertions, 246
  deletions before this Task 8 evidence file was added.
- `rtk git log --oneline origin/main..HEAD` → 33 issue-52 commits, ending at
  `0c63098 docs(spec): remove trailing blank line`; the Task 8 implementation is
  `8fa6bd6 fix(verifier): close shell parser redefinition bypasses`.

The exact name audit contains only the approved spec/plan; invocation-budget
action/helper/fixtures/tests; Claude, Gemini, and OpenCode central review workflows;
actionlint config; release inventory/verifier/tests; workflow contract; and this
plan's SDD evidence. It contains no consumer rollout, release tag/default/config
advance, issue-43 file outside this scope, or user-owned original-checkout path.

## Self-review

- The heredoc scanner uses `shlex` punctuation tokens with shell comments and POSIX
  quote removal. Only a standalone unquoted `<<` token followed by the admitted
  identifier delimiter opens payload skipping; quoted here-doc-like words remain
  normal command data, and unsupported delimiter grammar fails closed.
- Multiple real heredoc operators are consumed in shell order. Existing authentic
  Python and contract-tool heredocs remain accepted by the positive v1.47 tests.
- Static assignment tracking operates only on already parsed simple literal
  assignment/append words. Unknown expansions clear tracked knowledge; exact
  construction of the namespace executors or target fails closed. A `$` or backtick
  in executable command position is rejected as an indirect command.
- No raw target substring count or textual declaration-count workaround was added.
  Whole-program control/declaration/invocation binding from the previous five rounds
  remains unchanged.
- Semantic validation still precedes exact SHA-256 authentication and both consume
  only `VerifiedCommitTree` bytes. Both new tests independently authenticate their
  mutated tree bytes before exercising the semantic contract.
- No Task 1–6 production byte, inventory/contract/config/default, tag, release,
  rollout, GitHub state, or Notion state changed. No blocker or remaining local
  correctness concern was found.

## Ruling outcomes

- The Task 7 breaker ruling is satisfied locally: both carried authenticated bypasses
  are RED-proven and now rejected, the authentic workflow and all prior release cases
  remain green, and the local merge-prohibition condition for this finding is cleared.
  Independent review and all external Steps 5–9 remain controller-owned.
- Ruling: remove the one extra EOF blank line from the already-approved spec because
  the exact mandatory Step 4 command demonstrated a real whitespace defect; preserve
  every prose byte and decision — if wrong, the cost is a reversible one-byte
  documentation-only correction with no runtime or release-boundary effect.

Blockers: none. Remaining concerns: none locally; independent controller review is
still required before any push or PR action.

## Independent-review fix round 1/5

Both Important findings are addressed in the tracked fix that contains this report.
The exact immutable commit SHA and all post-commit Steps 1–4 evidence are recorded in
the intentionally git-ignored `task-8-final-head-evidence.md`; no tracked commit is
made after that evidence boundary.

### RED evidence

```text
rtk python3 -m pytest tests/test_verify_workflow_release.py::test_v147_opencode_call_cap_rejects_alias_executor_redefinition -q --tb=short
```

Result before the fix: `1 failed in 0.32s` with `Failed: DID NOT RAISE
ReleaseVerificationError`. The test's Bash syntax check, Bash namespace-execution
proof, authenticated mutated-tree commit, expected digest update, and
`VerifiedCommitTree` digest assertion all passed first.

The isolated parsed alias-state controls initially produced `3 failed, 5 passed in
1.30s`, each failure `DID NOT RAISE`. Self-review added the direct Bash alias-array
namespace form before finalizing production code; its focused RED was `1 failed in
0.32s`, also `DID NOT RAISE`.

### GREEN and covering regression evidence

- Exact execution-proven bypass: `1 passed in 0.21s`.
- Exact bypass plus all isolated target/alias controls:
  `rtk python3 -m pytest tests/test_verify_workflow_release.py::test_v147_opencode_call_cap_rejects_alias_executor_redefinition tests/test_verify_workflow_release.py::test_v147_opencode_call_cap_rejects_unparsed_target_affecting_syntax -q --tb=short`
  → `10 passed in 1.78s`.
- Authentic v1.47 candidate plus the prior 17 OpenCode parser cases and all new
  controls → `22 passed in 3.99s`.
- Unchanged prior structural regression command → `49 passed in 9.00s`.
- `rtk python3 -m py_compile scripts/verify_workflow_release.py tests/test_verify_workflow_release.py`
  → exit 0.
- `rtk python3 -m pytest tests/test_workflow_release_bundle.py tests/test_verify_workflow_release.py -q --tb=short`
  → `596 passed in 201.92s (0:03:21)`.

### Fix self-review

- Parsed command words are checked after assignment-prefix and `command`/`builtin`
  wrapper removal; alias declaration/removal, the `expand_aliases` shell option, and
  the Bash alias namespace array fail closed. The authentic workflow contains none.
- The change adds no substring occurrence/count proof and does not interpret alias
  bodies. Whole-program target declaration/invocation analysis remains unchanged.
- The regression test executes the real Bash namespace transition before testing the
  authenticated release verifier, so it does not assert only source text or syntax.
- Finding 2 uses one tracked final-review commit followed by a new ignored evidence
  artifact. This prevents evidence from changing the reviewed SHA while preserving
  exact post-commit commands and results.
- No Task 1–6 production byte, inventory/contract/config/default, tag, release,
  rollout, GitHub, Notion, or other external state changed. Blockers: none.
