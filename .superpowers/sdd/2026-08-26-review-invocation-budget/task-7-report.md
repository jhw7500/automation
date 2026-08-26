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
