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

## Independent-review fix round 2/5

Implementation fix commit: `074626e10e64e9ebda17d8fd3a7688733bddeb8c`

The remaining Finding 3 was closed structurally. Helper verification now compares
exact frozen-dataclass record shapes including field order, annotations, and defaults;
authenticates the Decision and Outcome type aliases; binds each claim predicate to its
ordered direct statement and refusal body; binds live finalization, stored-ledger cap,
provenance, override, bounded-finding, checkpoint, and CAS relationships to their AST
nodes; and rejects dead-code decoys. Reviewer workflow call caps are now verified in
the named live step: exact Claude metrics step mapping, parsed Gemini embedded-Python
function AST, and the anchored OpenCode `run_opencode` shell sequence. Raw helper
fragment/occurrence-count gates and workflow-wide call-cap fragment searches were
removed. Exact digest authentication remains after semantic validation and all inputs
continue to come from `VerifiedCommitTree`.

### Fix-round 2 RED evidence

- Command:
  `rtk python3 -m pytest tests/test_verify_workflow_release.py::test_v147_budget_helper_semantics_bind_live_ast_relationships tests/test_verify_workflow_release.py::test_v147_budget_workflow_semantics_bind_live_reviewer_call_caps -q --tb=short`
- Result: `9 failed in 0.89s`.
- All nine mutations escaped the prior semantic verifier: schema annotation drift,
  loss of the frozen record shape, a dead duplicate-head guard retaining its refusal,
  a weakened live final cap with the exact relation copied into dead code, weakened
  live RVW and provenance bounds with dead decoys, and Claude/Gemini/OpenCode live
  call-cap weakening while the former raw fragments remained elsewhere.

### Fix-round 2 GREEN and regression evidence

- The same nine structural mutations: `9 passed in 0.63s`; touched verifier/test
  `py_compile` passed in the same command.
- Existing structural/digest and findings 1/2/4 focus:
  `rtk python3 -m pytest tests/test_workflow_release_bundle.py::test_review_invocation_budget_action_has_exact_safe_contract tests/test_workflow_release_bundle.py::test_review_invocation_budget_capability_boundary_is_closed tests/test_verify_workflow_release.py::test_v147_requires_each_budget_file_as_one_regular_0644_blob tests/test_verify_workflow_release.py::test_v147_rejects_budget_helper_gate_removal tests/test_verify_workflow_release.py::test_v147_budget_helper_semantics_reject_authenticated_mutations tests/test_verify_workflow_release.py::test_v147_budget_helper_semantics_bind_live_ast_relationships tests/test_verify_workflow_release.py::test_v147_budget_workflow_semantics_reject_authenticated_mutations tests/test_verify_workflow_release.py::test_v147_budget_workflow_semantics_bind_live_reviewer_call_caps tests/test_verify_workflow_release.py::test_v147_accepts_current_budget_release_contract -q --tb=short`
  → `48 passed in 11.25s`.
- Release suite:
  `rtk python3 -m pytest tests/test_workflow_release_bundle.py tests/test_verify_workflow_release.py -q --tb=short`
  → `575 passed in 226.40s`.
- Full unfiltered suite: `rtk python3 -m pytest -q --tb=short`
  → `2665 passed, 48 subtests passed in 790.25s`.
- Static checks:
  `rtk git diff --check` and `rtk python3 -m py_compile scripts/workflow_release_inventory.py scripts/verify_workflow_release.py tests/test_workflow_release_bundle.py tests/test_verify_workflow_release.py`
  → exit 0. The implementation diff contains only the Task 7 verifier and verifier
  test module.

### Fix-round 2 committed-byte verification

After committing the implementation fix, no tag was created:

```text
rtk python3 -m scripts.verify_workflow_release --automation . --ref v1.47 --expected-commit "$(rtk git rev-parse HEAD)" --commit-only
PASS: v1.47 commit content is secure at 074626e10e64e9ebda17d8fd3a7688733bddeb8c
```

### Fix-round 2 self-review

- A copied expression or refusal in nested/dead code cannot satisfy claim validation:
  the verifier requires the exact 12-statement direct claim body, exact predicate
  positions, and matching direct/nested refusal bodies.
- Finalization requires the exact live call-first `if`/wall-time `elif` AST at its
  direct statement position; stored ledger cap exceptions are checked as one exact
  live loop rather than independent strings.
- Record checks include `@dataclass(frozen=True)`, exact ordered annotations, and
  required/default expressions, closing same-name/wrong-type and mutable-record drift.
- Bounded RVW checks bind list/tuple type, maximum eight, uniqueness, and the exact
  `_FINDING.fullmatch` relation in the live deserializers and request validators.
- Gemini caps bind the parsed `counted_generate_content` body; Claude binds the exact
  live metrics step; OpenCode binds the unique counter-read anchor and following
  refusal/increment sequence inside `run_opencode`. Decoys elsewhere cannot satisfy
  these checks.
- Findings 1, 2, and 4 remain covered and unchanged. No Task 1–6 action/helper/workflow
  bytes, inventory default, config, tag, release, rollout, GitHub, or Notion state was
  changed. No blockers or remaining concerns were found.

## Independent-review fix round 3/5

Implementation fix commit: `1bc6f7924b987f3b944966cf011e2caa486bbefb`

The remaining OpenCode reachability finding was closed by replacing normalized-line
matching and occurrence counting with an authenticated shell command/control parser.
The parser joins logical commands, excludes heredoc payloads, records executable
control nesting, and requires the complete `run_opencode` command sequence exactly.
The live top-level counter read, numeric validation, two-call refusal group, durable
increment, and sole OpenCode CLI invocation must therefore occur in their required
order and control context; a complete copy below `if false` cannot satisfy the
contract. Semantic validation still precedes exact digest authentication, and both
operate only on bytes read from `VerifiedCommitTree`.

### Fix-round 3 RED evidence

- Added
  `test_v147_opencode_call_cap_rejects_complete_unreachable_sequence_decoy`, which
  replaces the live counter read with `count="$(printf 0)"`, moves the complete prior
  seven-line counter/cap/increment sequence below `if false; then`, commits the
  mutation, authenticates the mutated workflow SHA-256 in the expected digest map,
  and asserts that the semantic contract rejects the same verified tree bytes.
- Command:
  `rtk python3 -m pytest tests/test_verify_workflow_release.py::test_v147_opencode_call_cap_rejects_complete_unreachable_sequence_decoy -q --tb=short`
- Result before the production fix: `1 failed in 0.26s` with `DID NOT RAISE`; the
  explicit tree-byte digest assertion passed, proving that the prior semantic gate
  accepted the authenticated unreachable-sequence bypass.

### Fix-round 3 GREEN and regression evidence

- Authentic workflow plus the exact bypass mutation, with touched-file `py_compile`:
  `rtk python3 -m py_compile scripts/verify_workflow_release.py tests/test_verify_workflow_release.py && rtk python3 -m pytest tests/test_verify_workflow_release.py::test_v147_accepts_current_budget_release_contract tests/test_verify_workflow_release.py::test_v147_opencode_call_cap_rejects_complete_unreachable_sequence_decoy -q --tb=short`
  → `2 passed in 0.76s`.
- The prior 48 finding-regression cases plus the new bypass mutation:
  `rtk python3 -m pytest tests/test_workflow_release_bundle.py::test_review_invocation_budget_action_has_exact_safe_contract tests/test_workflow_release_bundle.py::test_review_invocation_budget_capability_boundary_is_closed tests/test_verify_workflow_release.py::test_v147_requires_each_budget_file_as_one_regular_0644_blob tests/test_verify_workflow_release.py::test_v147_rejects_budget_helper_gate_removal tests/test_verify_workflow_release.py::test_v147_budget_helper_semantics_reject_authenticated_mutations tests/test_verify_workflow_release.py::test_v147_budget_helper_semantics_bind_live_ast_relationships tests/test_verify_workflow_release.py::test_v147_budget_workflow_semantics_reject_authenticated_mutations tests/test_verify_workflow_release.py::test_v147_budget_workflow_semantics_bind_live_reviewer_call_caps tests/test_verify_workflow_release.py::test_v147_opencode_call_cap_rejects_complete_unreachable_sequence_decoy tests/test_verify_workflow_release.py::test_v147_accepts_current_budget_release_contract -q --tb=short`
  → `49 passed in 12.16s`.
- Release suite:
  `rtk python3 -m pytest tests/test_workflow_release_bundle.py tests/test_verify_workflow_release.py -q --tb=short`
  → `576 passed in 259.90s (0:04:19)`.
- Full unfiltered suite: `rtk python3 -m pytest -q --tb=short`
  → `2666 passed, 48 subtests passed in 818.79s (0:13:38)`.
- Static checks:
  `rtk git diff --check` and
  `rtk python3 -m py_compile scripts/workflow_release_inventory.py scripts/verify_workflow_release.py tests/test_workflow_release_bundle.py tests/test_verify_workflow_release.py`
  → exit 0. The implementation diff contained only the Task 7 verifier and verifier
  test module.

### Fix-round 3 committed-byte verification

After committing the implementation fix, no tag was created:

```text
rtk python3 -m scripts.verify_workflow_release --automation . --ref v1.47 --expected-commit "$(rtk git rev-parse HEAD)" --commit-only
PASS: v1.47 commit content is secure at 1bc6f7924b987f3b944966cf011e2caa486bbefb
```

### Fix-round 3 self-review

- OpenCode call accounting is now an exact ordered tuple of parsed shell commands
  and their control paths, not a raw fragment, normalized-line slice, or occurrence
  count. The counter/cap/increment commands and the only provider invocation must be
  top-level in `run_opencode`; the refusal statements must be nested only beneath the
  exact two-call cap group.
- Heredoc bodies are excluded from executable commands, unsupported conditional/loop
  forms inside the authenticated function fail closed, and extra or ambiguous command
  sequences fail exact equality. The authentic current workflow remains accepted.
- Findings 1, 2, and 4 and the broader round-2 structural gates remain covered by the
  49-case focused regression and complete release/full suites. No Task 1–6
  action/helper/workflow bytes, inventory default, config, tag, release, rollout,
  GitHub, or Notion state changed. No blockers or remaining concerns were found.

## Independent-review fix round 4/5

Implementation fix commit: `2391487b764226630a0a47f3a1851a531e8bb87d`

The residual declaration-binding bypass is closed structurally. The shell analyzer
now parses the complete authenticated provider program from its first logical command,
tracks conditional/group/loop/case/function nesting across every declaration, and
recognizes both POSIX and Bash `function` declaration forms, including whitespace and
split-opening-brace variants. It requires exactly one canonical, reachable, top-level
`run_opencode() {` definition, rejects conditional/alternate/duplicate definitions,
compares the accepted function's exact live cap/durable-increment/CLI body, and binds
the two exact later invocation commands and their control paths to that definition.
Heredoc payload exclusion, semantic-before-digest ordering, and VerifiedCommitTree-only
input bytes remain unchanged.

### Fix-round 4 RED evidence

- Added
  `test_v147_opencode_call_cap_rejects_dead_canonical_function_decoy`, which
  authenticates mutated workflow bytes containing a weakened live
  `function run_opencode { ... }` definition while placing the complete expected
  `run_opencode() { ... }` body beneath a top-level `if false; then`. The later real
  invocations therefore bind to the weakened alternate definition.
- Exact command:
  `rtk python3 -m pytest tests/test_verify_workflow_release.py::test_v147_opencode_call_cap_rejects_dead_canonical_function_decoy -q --tb=short`
- Result before the production fix: `1 failed in 0.36s` with
  `Failed: DID NOT RAISE ReleaseVerificationError`; the explicit SHA-256 assertion
  proved that the verifier consumed the same authenticated mutated tree bytes.
- Adjacent ambiguity command:
  `rtk python3 -m pytest tests/test_verify_workflow_release.py::test_v147_opencode_call_cap_rejects_ambiguous_function_binding -q --tb=short`
  initially produced `3 failed in 0.68s`, each with `DID NOT RAISE`, for a conditional
  definition, later alternate redefinition, and pre-definition invocation. A second
  TDD edge added spaced POSIX and split-line Bash declarations; the split-line Bash
  case failed with `DID NOT RAISE` while the already recognized spaced form passed.

### Fix-round 4 GREEN and regression evidence

- Exact GREEN command:
  `rtk python3 -m pytest tests/test_verify_workflow_release.py::test_v147_opencode_call_cap_rejects_dead_canonical_function_decoy -q --tb=short`
  → `1 passed in 0.22s`.
- Authentic workflow, prior unreachable-sequence decoy, exact bypass, and all five
  adjacent binding mutations:
  `rtk python3 -m pytest tests/test_verify_workflow_release.py::test_v147_accepts_current_budget_release_contract tests/test_verify_workflow_release.py::test_v147_opencode_call_cap_rejects_complete_unreachable_sequence_decoy tests/test_verify_workflow_release.py::test_v147_opencode_call_cap_rejects_dead_canonical_function_decoy tests/test_verify_workflow_release.py::test_v147_opencode_call_cap_rejects_ambiguous_function_binding -q --tb=short`
  → `8 passed in 2.04s`.
- The unchanged prior 49-case focused regression command from fix round 3
  (exact action/boundary, root kinds, helper gates/live AST, workflow semantics/live
  caps, unreachable-sequence decoy, and positive v1.47 candidate)
  → `49 passed in 12.16s`.
- Both release suites:
  `rtk python3 -m pytest tests/test_workflow_release_bundle.py tests/test_verify_workflow_release.py -q --tb=short`
  → `582 passed in 270.94s (0:04:30)`.
- Full unfiltered suite: `rtk python3 -m pytest -q --tb=short`
  → `2672 passed, 48 subtests passed in 804.09s (0:13:24)`.
- Static checks:
  `rtk git diff --check` and
  `rtk python3 -m py_compile scripts/workflow_release_inventory.py scripts/verify_workflow_release.py tests/test_workflow_release_bundle.py tests/test_verify_workflow_release.py`
  → exit 0. The implementation diff contains only the Task 7 verifier and verifier
  test module.

### Fix-round 4 committed-byte verification

After committing the implementation fix, no tag was created:

```text
rtk python3 -m scripts.verify_workflow_release --automation . --ref v1.47 --expected-commit "$(rtk git rev-parse HEAD)" --commit-only
PASS: v1.47 commit content is secure at 2391487b764226630a0a47f3a1851a531e8bb87d
```

### Fix-round 4 self-review

- Whole-program nesting is established before a target declaration is evaluated;
  a canonical body inside `if false`, `if true`, a group, another function, or any
  duplicate declaration cannot be treated as the live top-level implementation.
- Both `name() {`/spaced POSIX and `function name {`/optional-parentheses Bash forms,
  including a following-line opening brace, enter the same structural definition
  inventory. Only the one exact canonical production declaration is accepted.
- The exact two normalized invocations must follow the accepted definition and retain
  their expected top-level/format-repair conditional paths; an earlier invocation or
  redefinition cannot borrow the accepted body's proof.
- The accepted body still requires the exact counter file/mode/read, numeric guard,
  two-call refusal group, fsync-backed increment heredoc command, and sole CLI command.
  Heredoc contents remain data rather than executable parser input.
- Semantic checks still precede exact workflow SHA-256 authentication over the same
  VerifiedCommitTree bytes. No Task 1–6 action/helper/workflow bytes, inventory/docs
  contract, fleet default, config, tag, release, rollout, GitHub, or Notion state was
  changed. No blockers or remaining concerns were found.

## Independent-review fix round 5/5

Implementation fix commit: `a9ccc1fa9eec4c91a53f1cdf33f5125e80b98995`

The final compact-declaration bypass is closed structurally. The whole-program shell
analyzer now tokenizes unquoted semicolon lists while preserving quoted content,
command/parameter substitutions, continuations, control openers, and authenticated
heredoc payload boundaries. It separates a function declaration's opening brace from
same-line commands, recognizes POSIX `name()` and Bash `function name` forms with
compact or split braces, inventories every parsed `run_opencode` definition, and
requires exactly one canonical live top-level definition before the two exact bound
calls. Target-bearing syntax outside the declaration/invocation grammar and dynamic
namespace execution through direct, wrapped, or assignment-prefixed `eval`, `source`,
or `.` commands fail closed. No raw substring or occurrence-count gate was added.

### Fix-round 5 RED evidence

- Added authenticated compact redefinitions after the accepted canonical function;
  both retained the later production calls, replaced the live binding with an
  unbounded usable body, and passed `bash -n`:
  `function run_opencode { ...; }` and `run_opencode(){ ...; }`.
- Exact command:
  `rtk python3 -m pytest tests/test_verify_workflow_release.py::test_v147_opencode_call_cap_rejects_compact_function_redefinition -q --tb=short`
- Result before the production fix: `2 failed in 0.48s`, both with
  `DID NOT RAISE ReleaseVerificationError`. Each explicit SHA-256 assertion proved
  the verifier consumed the same authenticated mutated workflow bytes.
- Adjacent authenticated ambiguity probes for an inline conditional declaration,
  direct dynamic redefinition, and a hidden conditional call initially produced
  `3 failed in 0.67s`, all with `DID NOT RAISE`. Self-review then added wrapped
  `command builtin eval` and computed-name, assignment-prefixed `eval` probes; each
  independently failed with `DID NOT RAISE` before its structural fix (`0.28s` and
  `0.34s`, respectively).

### Fix-round 5 GREEN and regression evidence

- Final new/positive focus with touched-file compilation:
  `rtk python3 -m py_compile scripts/verify_workflow_release.py tests/test_verify_workflow_release.py && rtk python3 -m pytest tests/test_verify_workflow_release.py::test_v147_opencode_call_cap_rejects_compact_function_redefinition tests/test_verify_workflow_release.py::test_v147_opencode_call_cap_rejects_unparsed_target_affecting_syntax tests/test_verify_workflow_release.py::test_v147_accepts_current_budget_release_contract -q --tb=short`
  → `8 passed in 1.59s`.
- Unchanged round-4 authentic/prior-decoy/dead-canonical/five-ambiguity focus:
  `8 passed in 1.93s`.
- Unchanged prior 49-case focused regression command from fix round 3:
  `49 passed in 11.61s`.
- Final release suites on the implementation commit:
  `rtk python3 -m pytest tests/test_workflow_release_bundle.py tests/test_verify_workflow_release.py -q --tb=short`
  → `589 passed in 277.11s (0:04:37)`.
- Full unfiltered suite on the final implementation:
  `rtk python3 -m pytest -q --tb=short`
  → `2679 passed, 48 subtests passed in 814.39s (0:13:34)`.
- Static checks:
  `rtk python3 -m py_compile scripts/workflow_release_inventory.py scripts/verify_workflow_release.py tests/test_workflow_release_bundle.py tests/test_verify_workflow_release.py`
  and `rtk git diff --check` → exit 0. The implementation diff contains only the
  Task 7 verifier and verifier test module.

### Fix-round 5 committed-byte verification

After committing the implementation fix, no tag was created:

```text
rtk python3 -m scripts.verify_workflow_release --automation . --ref v1.47 --expected-commit "$(rtk git rev-parse HEAD)" --commit-only
PASS: v1.47 commit content is secure at a9ccc1fa9eec4c91a53f1cdf33f5125e80b98995
```

### Fix-round 5 self-review

- Both legal compact brace declaration spellings are tokenized into a declaration,
  ordered body commands, and an exact closure rather than being accepted as opaque
  top-level text. Spacing, split-opening-brace, compact-body, duplicate, conditional,
  dead, alternate, and pre-invocation variants share one definition inventory.
- Semicolons in quoted data, substitutions, control `; then`/`; do` openers, and
  authenticated heredoc bodies are not mistaken for executable top-level boundaries;
  the authentic provider program and all prior reachability cases remain green.
- Later calls are recorded only as parsed `run_opencode` command statements and retain
  their exact program order/control paths. A target identifier in any other parsed
  shell word is rejected, and dynamic namespace commands are structurally unwrapped
  across assignment prefixes plus `command`/`builtin` wrappers before rejection.
- The analyzer intentionally accepts only the authenticated provider's shell subset;
  it is not claimed to be a general Bash AST. Computed shell code is refused at the
  namespace-execution boundary rather than interpreted, so no unproved alternate
  declaration grammar is admitted into the accepted contract.
- Semantic validation still precedes exact SHA-256 authentication over the same
  `VerifiedCommitTree` bytes. No Task 1–6 production byte, inventory/contracts/config
  default, tag, release, rollout, GitHub, Notion, or other external state changed.
  No blockers or remaining known concerns were found.
