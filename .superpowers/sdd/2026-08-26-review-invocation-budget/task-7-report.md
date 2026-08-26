# Task 7 report — release inventory, verifier, and consumer contract

Date: 2026-08-27
Implementation commit: `abc3cefd203014ed2295cac9efdb11f02ff1e84f`

## Result

The proposed v1.47 capability boundary now owns exactly the review invocation-budget
action and helper as mode `100644` blobs while preserving all v1.40–v1.46.2 release
inventories and the v1.46.2 fleet default. The authenticated release verifier closes
the action directory, parses the action with `yaml.BaseLoader`, authenticates exact
action/helper/workflow bytes, compiles and parses the helper, requires exact public
signatures/constants/policy gates, and verifies each reviewer workflow's ordered
claim/provider/finalize boundary, timeouts, counters, checkpoint artifacts, OpenCode
sealed handoff, and no cross-reviewer fallback. The consumer contract is documented in
`docs/workflows/contracts.md`. No tag, rollout, or config advancement was performed.

## TDD evidence

- RED command: `rtk python3 -m pytest tests/test_workflow_release_bundle.py tests/test_verify_workflow_release.py -q`
- RED result: `168 failed, 373 passed in 249.82s`. The new tests failed because
  v1.47 roots, action/helper authentication, and reviewer workflow contracts did not
  yet exist; the pre-Task-7 verifier also rejected the Task 4–6 budget references as
  unknown dependencies, which was the approved intermediate boundary.
- Focused GREEN: `rtk python3 -m pytest tests/test_workflow_release_bundle.py tests/test_verify_workflow_release.py -q -k 'review_invocation_budget or v147'`
- Focused GREEN result: `26 passed, 516 deselected in 15.11s`.
- Release/bundle GREEN: `rtk python3 -m pytest tests/test_workflow_release_bundle.py tests/test_verify_workflow_release.py -q`
- Release/bundle GREEN result: `542 passed in 266.42s`.
- Full unfiltered GREEN: `rtk python3 -m pytest -q`
- Full unfiltered GREEN result: `2632 passed, 48 subtests passed in 814.96s`.
- Static evidence: `rtk python3 -m py_compile` over both release scripts and both
  release test modules passed; `rtk git diff --check` passed.

## Committed-byte verification

After creating the implementation commit, the required command was run without a tag:

```text
rtk python3 -m scripts.verify_workflow_release --automation . --ref v1.47 --expected-commit "$(rtk git rev-parse HEAD)" --commit-only
PASS: v1.47 commit content is secure at abc3cefd203014ed2295cac9efdb11f02ff1e84f
```

## Self-review

- Historical capability predicates and fixtures remain ref-gated; current-source
  mutation tests use v1.47, while immutable historical commit/tag paths and the
  v1.46.2 default remain unchanged.
- The v1.47 inventory is closed to the exact action/helper paths and modes; additions,
  omissions, directory collisions, executable modes, symlinks, and gitlinks are
  covered by release tests.
- Exact action/helper/workflow digests authenticate only commit-tree bytes. Helper
  compilation, AST uniqueness, public signatures, policy constants, decision order,
  override/provenance/CAS/call/wall gates add explicit semantic checks rather than
  trusting the working tree.
- Existing v1.46 OpenCode runtime/publication digests remain accepted only on their
  historical ref path; the v1.47 OpenCode run, permissions, candidate envelope, and
  sealed budget handoff have separate exact contracts.
- Exhaustion remains a zero-call refusal, never approval or review evidence; provider
  failure cannot dispatch another reviewer. `/jhw:ship` remains outside ledger parsing.
- No Task 1–6 action/helper/workflow bytes, release tag, rollout state, or
  `scripts/workflow-config.json` were changed. No blockers remain.

## Independent-review fix round 1/5

Implementation fix commit: `0fb3a1d3b43bd03f125dbf1ae2e24970bec4024b`

All four Important findings were addressed without changing Task 1–6 production
bytes. The v1.46 mutation suite now uses workflow bytes restored from immutable
commit `d42c28ddd827554e6e46a2ab49dfe34c838c0425`, checks their fixed SHA-256 values,
and verifies them directly as `v1.46.2`; current-source v1.47 tests remain separate.
The expected action is now the complete `yaml.BaseLoader` mapping, including the
exact composite `runs`, `env`, and `run` document, and both verifier and bundle test
require full mapping equality. Helper/workflow semantic validation now precedes the
separate exact-byte authentication gate and directly verifies schema fields, AST
decision ordering, reviewer caps, bounded RVW identity, final cap relationships,
provider predicates, publication/finalization order, artifacts, and the OpenCode
cross-job sealed-claim handoff. Both new roots have explicit file-kind assertions and
missing/directory/executable/symlink/gitlink mutation coverage.

### Fix-round RED evidence

- `rtk python3 -m pytest tests/test_workflow_release_bundle.py::test_review_invocation_budget_action_has_exact_safe_contract tests/test_workflow_release_bundle.py::test_review_invocation_budget_capability_boundary_is_closed tests/test_verify_workflow_release.py::test_v147_requires_each_budget_file_as_one_regular_0644_blob -q`
  → `1 failed, 11 passed in 2.15s`; the incomplete expected action mapping omitted
  `runs` while all newly added inventory-kind mutations were already rejected.
- `rtk python3 -m pytest tests/test_verify_workflow_release.py::test_v147_budget_helper_semantics_reject_authenticated_mutations tests/test_verify_workflow_release.py::test_v147_budget_workflow_semantics_reject_authenticated_mutations -q`
  → `4 failed, 13 passed`; all four authenticated helper semantic mutations escaped.
  The initial YAML reserialization workflow harness was then replaced with exact
  original-text block mutations so formatting could not cause a digest-only result.
- `rtk python3 -m pytest tests/test_verify_workflow_release.py::test_v147_budget_workflow_semantics_reject_authenticated_mutations -q --tb=short`
  → `10 failed, 3 passed in 1.98s`; exact provider predicates (3), publication order
  (3), OpenCode cross-job claim order (1), and claim artifact paths (3) escaped the
  semantic verifier, while the three existing call-cap gates rejected their mutations.

### Fix-round GREEN and regression evidence

- Amended finding 2/4 plus helper/workflow semantic focus:
  `rtk python3 -m pytest tests/test_workflow_release_bundle.py::test_review_invocation_budget_action_has_exact_safe_contract tests/test_workflow_release_bundle.py::test_review_invocation_budget_capability_boundary_is_closed tests/test_verify_workflow_release.py::test_v147_requires_each_budget_file_as_one_regular_0644_blob tests/test_verify_workflow_release.py::test_v147_budget_helper_semantics_reject_authenticated_mutations tests/test_verify_workflow_release.py::test_v147_budget_workflow_semantics_reject_authenticated_mutations -q --tb=short`
  → `29 passed in 3.76s`.
- Immutable historical suite:
  `rtk python3 -m pytest tests/test_verify_workflow_release.py -q -k 'v146' --tb=short`
  → `166 passed, 332 deselected in 97.22s`.
- Positive-current regression after correcting the tuple-target form emitted by
  `ast.unparse`: three current/v1.47 positive cases → `3 passed in 2.61s`.
- Existing helper gate-removal mutations plus positive v1.47 candidate after adding
  exact policy occurrence semantics → `10 passed in 6.96s`.
- Final release suite:
  `rtk python3 -m pytest tests/test_workflow_release_bundle.py tests/test_verify_workflow_release.py -q --tb=short`
  → `566 passed in 270.50s`.
- Static checks:
  `rtk git diff --check` and `rtk python3 -m py_compile scripts/workflow_release_inventory.py scripts/verify_workflow_release.py tests/test_workflow_release_bundle.py tests/test_verify_workflow_release.py`
  → exit 0.
- Full unfiltered suite: `rtk python3 -m pytest -q --tb=short`
  → `2656 passed, 48 subtests passed in 813.70s`.

### Fix-round committed-byte verification

After committing the implementation fix, no tag was created:

```text
rtk python3 -m scripts.verify_workflow_release --automation . --ref v1.47 --expected-commit "$(rtk git rev-parse HEAD)" --commit-only
PASS: v1.47 commit content is secure at 0fb3a1d3b43bd03f125dbf1ae2e24970bec4024b
```

### Fix-round self-review

- The fixed v1.46.2 commit and three workflow SHA-256 values are constants; no
  historical test reads workflow bytes from the candidate worktree, and all 32
  historical mutation calls use `ref="v1.46.2"`.
- The expected action mapping is independently embedded and parsed with
  `yaml.BaseLoader`; verifier equality covers the entire document before the raw
  action digest is authenticated.
- Semantic helper/workflow functions contain no digest short-circuit. They operate
  only on bytes supplied by `VerifiedCommitTree`; the caller subsequently requires
  the exact action, helper, and three workflow digests from the same tree.
- Workflow checks bind exact per-reviewer allow/finalize predicates, timeout and call
  caps, checkpoint upload names/paths/policies, order, and OpenCode handoff identity;
  cross-reviewer fallback remains forbidden.
- Changed production/test scope is exactly the Task 7 verifier and two release test
  files. No inventory default, workflow, action, helper, config, tag, release, rollout,
  GitHub, or Notion state changed. No blockers or remaining concerns were found.
