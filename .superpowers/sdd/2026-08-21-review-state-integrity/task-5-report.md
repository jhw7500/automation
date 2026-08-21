# Task 5 report — OpenCode review state integrity

## Result

Implemented canonical OpenCode v2 review state, head-bound full-diff preparation,
strict post-CLI candidate handling, and v1/v2 informational marker extraction.

## RED → GREEN

- RED: `python3 -m pytest tests/test_review_workflow_logic.py -k 'opencode and (canonical or candidate or foreign)' -q` initially failed 8 tests: broad marker selection, missing preparation outputs, and absent canonicalization step.
- GREEN: focused OpenCode/rereview suite passed **22** tests (`22 passed, 91 deselected`).
- Added executable regressions for exact v2 prefix/schema selection and tuple ordering; legacy and foreign/preamble marker text; stable/changed/malformed preparation heads; snapshot fetch failure; zero/multiple/unchanged candidate rejection; stale head; forged v2 CLI output; output sanitization; success/failure/stale transitions; and OpenCode's real fresh-comment two-round path (canonical update before transient raw-comment deletion).

## Verification

- `python3 -m pytest tests/test_review_workflow_logic.py -q` — **113 passed**.
- All test modules run individually (equivalent full suite): **581 passed**, plus **48 subtests passed**.
- `python3 -m compileall -q tests` — passed.
- `git diff --check` — passed.

## Key behavior

- The comments snapshot is mandatory: a comments API failure exits before the CLI can run.
- Previous context accepts only exact, schema-complete OpenCode v2 bot envelopes and strips reserved workflow text.
- The explicitly numbered full PR diff is accepted only after equal validated head reads before and after fetching it.
- A changed CLI comment is untrusted prose. The workflow requires exactly one changed marker-bearing bot candidate, sanitizes it, and generates the v2 envelope itself.
- OpenCode creates a fresh raw comment each round. Where a canonical comment already exists, the workflow updates that canonical ID and then deletes the transient candidate; this preserves one canonical comment. The tuple guard and current-head read run before either mutation.
- Candidate count failures throw and perform no mutations. The state baseline is the pre-run snapshot only, so a forged v2-looking CLI body cannot affect CAS selection.

## Concern / accepted limitation

The final head read and comment update/delete are separate GitHub API operations, so this remains an optimistic guard. The reviewer/repository/PR concurrency group and pre-run canonical tuple baseline reduce overlap, but GitHub offers no atomic cross-resource CAS. This matches the approved design's documented platform limitation.

## Commit

`fix(review): canonicalize OpenCode review provenance` (the final SHA is reported in the task handoff).
