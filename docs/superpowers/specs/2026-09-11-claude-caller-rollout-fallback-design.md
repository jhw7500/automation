# #182 Claude caller self-update review fallback - design for review

Status: design approved by the user's `go` on 2026-09-11.
Date: 2026-09-11. Source tree: 444a7347aee169ed178aae80e8bd8d10eca52e02.
Issue: https://github.com/jhw7500/automation/issues/182

Integration note (2026-09-11): #183 published observational token estimates as
immutable v1.77, tag object `81f44fb6786bdfcc40f93161db74b1d9a9e3b7c5` at exact
commit `dd13f9dcc64540494c1c04bc3f9c7a4f2ef0ba19`. The #182 fallback release is
therefore v1.78, with its distinct byte-identical boundary canary at v1.78.1.
The normal main merge preserves reviewed Task 1-6 history. Both future publication
paths authenticate v1.77 and unchanged v1.76 before any write; v1.78.1 additionally
authenticates v1.78 and compares all release-owned paths using that baseline's
inventory. Schema 2 keeps token estimates observational and preserves the v1.77
summary field, round, override, call-count, wall-time, checkpoint and provenance
gates. v1.77 continues to validate its authentic schema-1 helper. The v1.76 source
and bootstrap evidence below are historical and remain unchanged.

This is an advisory design record. Task and Claim ownership continue to come only
from the supported Project Control lifecycle. The active #182 Claim remains the
authority for the current canary and rollout work.

## Result to deliver

Give exact managed workflow rollout PRs one authenticated Claude review route when
their automatic `claude-code-review.yml` caller is intentionally different from the
default branch. Keep the caller identity check intact, invoke Claude exactly once
through a workflow that exists on the default branch, publish the ordinary canonical
Claude review for the exact PR HEAD/base, and let the JHW merge gate accept that
result only after it authenticates both the failed automatic attempt and the fallback
attempt.

Ordinary caller mismatch, unavailable validation, arbitrary workflow edits, stale
HEAD/base, ambiguous runs, malformed responses and provider failures remain blocking.
The fallback is a review route, not a general exception to required CI, branch
protection, target tests, mergeability or atomic head/base verification.

## Why this is recurring

The Claude reusable workflow predicts the Anthropic action's caller validation before
budget admission. It obtains `github.workflow_ref` and `github.workflow_sha`, reads
that caller at the running workflow SHA, reads the same path at the repository's
current default-branch SHA, and reports `workflow_validation_mismatch` when the blobs
differ. The upstream action applies the same default-branch identity rule, so removing
only the local prediction would delay the same refusal until the provider step.

A fleet release changes the immutable automation commit in the consumer
`.github/workflows/claude-code-review.yml`. The rollout PR therefore differs from its
default branch by construction. This happens once per adopting branch on every
release that changes the pin, rather than once for v1.76 as a whole. Normal feature
PRs work after the rollout is merged because their default-branch caller and running
caller are then identical, but the next pin rollout recreates the boundary.

The v1.76 full-profile evidence reproduced the same outcome independently:

| Repository / PR | Automatic Claude | Provider performed | Default-branch Claude |
| --- | --- | --- | --- |
| jhw7500/gstApp #109 | run 34549275027, mismatch | no | run 34549532475, CLEAN |
| jhw7500/max9296 #74 | run 34549486780, mismatch | no | run 34549560339, CLEAN |

Both fallback runs used the existing `issue_comment` caller from the default branch,
reviewed the exact requested HEAD/base, made no repository change and reported no
actionable finding. These runs prove feasibility. Their free-form responses and lack
of managed invocation metrics are bootstrap evidence only; they are not the durable
machine contract proposed below.

## Approaches considered

| Approach | Decision | Reason |
| --- | --- | --- |
| Default-branch request that nests the existing canonical reviewer | Recommended | Preserves caller validation and reuses diff, budget, canonicalization and publication contracts |
| Two alternating automatic caller files | Reject | Permanently doubles callers and required-check ambiguity while still needing a bootstrap rollout |
| Per-PR free-form `@claude review` substitution | Bootstrap only | Works today but requires bespoke parsing, has no managed meter and repeats operator judgment |
| Relax or remove caller validation | Reject | The upstream action still refuses the changed caller and the security boundary is weakened |

## Trust boundaries and non-goals

The security authority is a GitHub-authenticated request comment plus fresh server
state, not text found in the PR body, diff, a local transcript or a locally saved
response. The request must be created by an OWNER, MEMBER or COLLABORATOR and must
match one exact grammar. The target PR must be open, same-repository and at the exact
40-character HEAD and base recorded in the request.

The workflow route itself is safe for an authenticated collaborator to request on a
same-repository PR because it reads and reviews a fixed diff without executing code
from the PR. The JHW merge substitution is narrower: it additionally requires the
remote PR to be an exact `rollout_workflow_fleet.py` result for an immutable automation
release. A fallback review on another PR does not become a rollout substitution.

This design does not:

- allow forks, contributor-authored commands or PR-controlled workflow code to gain
  a token or secret;
- execute a script, action, hook or binary from the PR checkout;
- accept a Claude App comment without a corresponding authenticated workflow run;
- refund a failed review, bypass the invocation budget or add a second reviewer
  identity;
- turn `workflow_validation_unavailable` or another infrastructure failure into an
  eligible fallback;
- make a failed required status check successful. A repository that requires the
  failed automatic Claude check remains blocked until a separately reviewed
  head-bound status design exists or its native policy changes explicitly.

## Request contract

The JHW rollout-review driver posts one command-owned issue comment after it has
observed the exact automatic failure. The visible line identifies the operation; one
hidden canonical JSON marker carries the machine fields:

```text
@claude managed rollout review for PR <number>
<!-- automation:claude-rollout-review-request:v1 {canonical JSON} -->
```

The schema contains exactly:

- repository and PR number;
- expected PR HEAD and base SHA;
- original automatic run ID and run attempt;
- immutable automation release commit and managed full-diff SHA-256;
- a random request nonce with at least 128 bits of entropy.

The driver creates the comment from a 0600 non-symlink request file, reads the created
comment back, and records its GitHub ID, author and creation time. The nonce is unique
per repository/PR/HEAD/base. Before posting, the driver searches the bounded comment
horizon for that exact tuple. An existing valid request is reused; a conflicting or
ambiguous marker stops without posting.

No request field is trusted by itself. The workflow admission job parses the marker
with an exact schema, fetches the comment and PR from GitHub, and confirms:

1. the event is `issue_comment.created` for that exact comment ID;
2. the comment author association is OWNER, MEMBER or COLLABORATOR;
3. the issue is an open same-repository pull request at the requested HEAD/base;
4. the named automatic run is a current-HEAD `Claude Code Review` run and is the
   unique selected run for its number/attempt;
5. its canonical failure state says `workflow_validation_mismatch` and
   `review_execution=not_performed`;
6. the provider job evidence agrees that the Claude provider step was skipped; and
7. no successful fallback already exists for the same review tuple.

Any pagination overflow, missing field, API failure, duplicate candidate or mismatch
fails before a provider credential is available.

## Workflow architecture

The existing consumer `.github/workflows/claude.yml` remains the top-level
`issue_comment` caller. GitHub only triggers this event when the workflow file exists
on the default branch. Once a repository has adopted the new caller, a later rollout
may change the PR copy while the previous default-branch copy remains the trusted
entrypoint.

The central `.github/workflows/claude.yml` adds three mutually exclusive paths:

1. A permission-minimal classifier handles the exact managed rollout request grammar
   and produces only validated scalar outputs.
2. The existing interactive Claude job handles ordinary authenticated `@claude`
   comments and explicitly excludes managed rollout markers.
3. The managed rollout job calls
   `$/.github/workflows/claude-code-review.yml` from the same immutable automation
   commit, passing the exact PR and fallback evidence inputs.

GitHub documents that a called reusable workflow keeps the caller's `github` context.
The nested review therefore sees the consumer default-branch `claude.yml` as its
caller, so the existing caller comparison succeeds without an exception. The `$/`
reference resolves within the automation repository at the running reusable
workflow's exact commit; it does not select code from the consumer checkout.

The consumer caller grants `pull-requests: write` as the permission ceiling required
for the canonical sticky comment. The central interactive job continues to reduce
its own permissions to read-only. Only the admitted managed rollout job retains the
write permission used by the existing canonical publication path. Both jobs keep
`contents: read`, `actions: read`, `issues: read` and `id-token: write` at the minimum
already required by their provider paths.

The nested review receives new optional, all-or-none fallback inputs:

- fallback request comment ID and nonce;
- expected HEAD and base;
- original automatic run ID and attempt; and
- immutable release commit and managed diff SHA-256.

Supplying some but not all inputs is an input error. Normal PR and workflow-dispatch
callers supply none and retain their current behavior. Fallback admission revalidates
the live PR coordinates immediately before diff preparation and again before
publication. The existing prepare-review-diff action remains the authority for the
actual numbered diff, merge base and full-diff hash.

## Budget and canonical result

The failed automatic run stops before budget claim, so it contributes zero provider
calls. The default-branch fallback claims the ordinary Claude reviewer budget for the
same repository, PR and HEAD. The budget action gains a typed
`default_branch_rollout_fallback` route whose provenance includes the request comment
and original failed run, while its duplicate-head, request ceiling, elapsed-time and
finalization rules stay unchanged.

The route performs at most one provider call sequence allowed by the existing Claude
budget. A repeated command observes the existing request or the existing finalized
HEAD and performs no additional provider call. A claimed-but-unfinalized fallback is
handled by the existing bounded failure policy; it is never replaced by an unmetered
interactive response.

The ordinary Claude canonicalizer and sticky-comment publisher remain the only
authority for findings. Schema 3 adds an optional authenticated route block:

```text
route = default_branch_rollout_fallback
request_comment_id = <id>
original_failed_run_id = <id>
reviewed_base_sha = <40 hex>
release_commit = <40 hex>
managed_diff_sha256 = <64 hex>
```

For normal reviews the block is absent. The fallback success state still includes the
ordinary reviewed HEAD, full-diff hash, run ID/attempt, canonical counts, provider
execution and finalized budget evidence. Display fields must agree with the hidden
state, and active findings keep the existing severity and section grammar.

The original failed run remains failed and discoverable. The fallback does not edit
its check, rewrite its logs or describe it as successful.

## Fleet attestation and JHW merge policy

One pure verifier in the automation repository owns the substitution rules. It takes
repository, PR, expected HEAD/base and immutable release coordinates, obtains fresh
GitHub evidence, and returns a machine-readable receipt. It reuses the fleet renderer
and PR attestation logic rather than trusting branch names, PR titles or local
manifests.

The verifier requires:

- the remote commit tree and diff exactly match the selected release rendering for
  the selected base branch;
- every changed path is managed by the release catalog and the caller pin points to
  the exact immutable release commit;
- the automatic Claude failure, request comment, fallback run, canonical comment,
  budget receipt and live PR coordinates form one unambiguous chain;
- Gemini, OpenCode, Codex, required CI and any requested target result remain governed
  by their existing independent gates; and
- the fallback canonical review has no active finding at or above the configured
  blocking threshold.

The receipt records both Claude outcomes:

```text
automatic = FAILED(workflow_validation_mismatch, not_performed)
fallback  = CLEAN(default_branch_rollout_fallback, performed, finalized)
effective Claude reviewer status = CLEAN
```

The source JHW PR command in `jhw-notion-runtime/skills/claude/pr.md` invokes that
verifier only for this exact failure reason and exact rollout class. It does not
duplicate the trust logic. A valid receipt maps the single planned Claude reviewer to
`CLEAN` and reports the original failure plus fallback route in the merge receipt.
Invalid or unavailable evidence leaves Claude `FAILED`; no App result, tribunal result
or user prose is silently substituted.

Immediately before merge, the existing JHW helper still rechecks the PR HEAD, base
OID, required checks, mergeability, merge method, origin identity and exact
GitHub-generated merge commit. Native required checks are not filtered or waived.

## Bootstrap and rollout order

Repositories whose default branch still points to v1.77 or earlier do not yet have
the structured route. Their first adoption cannot use code that exists only in the PR
copy. The already completed gstApp #109 and max9296 #74 free-form reviews are therefore
a one-time bootstrap exception, covered by the user's approval of this design and the
existing hashed exact-evidence proposal. Any additional pre-route repository uses the
same separately evidenced default-branch review procedure; responses are grouped into
bounded batch proposals rather than treated as the durable verifier output.

The durable rollout order is:

```text
implement and review fallback release
                  |
                  v
bootstrap one canary consumer with existing default-branch review
                  |
                  v
open a later exact pin-update PR on that canary
                  |
                  v
observe automatic refusal -> structured fallback -> canonical CLEAN
                  |
                  v
verify JHW gate and zero duplicate provider calls
                  |
                  v
resume fleet rollout; each repository gains the route after its bootstrap merge
```

The canary must prove the route on a real caller-changing PR; a normal feature PR does
not exercise the failure boundary. Fleet expansion stops if the nested workflow sees
the PR caller instead of the default-branch caller, permissions cannot reach the
canonical publisher, the budget creates a second invocation, the JHW verifier cannot
bind the base, or native required checks remain unsatisfied.

## Failure, race and retry behavior

- HEAD or base changes at any admission, review, publication, receipt or merge check:
  stop and require a new tuple.
- The automatic run performed any provider call: do not request fallback; report a
  normal Claude failure.
- Missing or conflicting request marker, run, job, state, budget or release evidence:
  fail closed without inference.
- Two eligible fallback runs or two canonical comments for one tuple: ambiguous and
  blocking until resolved through the normal reviewed cleanup path.
- Provider or canonicalization failure in fallback: keep it failed and consume budget
  according to the ordinary rules; do not use the free-form interactive job.
- Comment publication response loss: discover by exact nonce, author, tuple and
  creation horizon; never post blindly a second request.
- Workflow dispatch or API timeout: poll bounded server state, retain the request and
  return a retryable diagnostic without changing the PR.
- Existing successful finalized fallback: reuse only when every immutable coordinate
  and response byte still agrees.

No raw Registry edit, takeover, stale Claim deletion, secret mutation or budget-ledger
repair is part of this design.

## Implementation boundaries

Automation repository:

- `.github/workflows/claude.yml`: classifier, interactive exclusion and nested route;
- `.github/workflows/claude-code-review.yml`: typed fallback inputs, admission,
  provenance and state publication;
- `.github/actions/review-invocation-budget`: typed fallback caller provenance using
  the existing Claude ledger;
- focused admission/attestation helper or composite action;
- fleet fallback verifier and exact receipt schema;
- baseline callers, workflow catalog, release verifier, contracts and runbook; and
- tests and mutation checks for every new trust branch.

JHW command source repository:

- `skills/claude/pr.md`: detect only the eligible automatic outcome, request/poll one
  fallback, invoke the automation verifier and render the effective receipt; and
- its command tests: forged, stale, ambiguous, duplicate and valid fallback evidence.

The JHW repository remains a separate Project Control decision. Before its first
substantive edit, run its stateless Task nudge and follow the returned registered-task
policy. The current automation Claim does not imply a second repository Task.

## Verification and canary gates

Implementation tests must cover:

- exact request grammar, author association and same-repository PR checks;
- malformed/partial fields, forks, stale HEAD/base, wrong run/attempt and pagination
  overflow;
- automatic mismatch with provider skipped versus every ineligible failure shape;
- interactive and managed routes are mutually exclusive and never double-call;
- nested caller identity, `$/` resolution and permissions cannot elevate beyond the
  consumer ceiling;
- ordinary reviews and old callers remain backward compatible;
- one shared Claude budget invocation, duplicate request reuse and finalized receipt;
- canonical state/display agreement, active finding thresholds and route provenance;
- exact fleet rendering and immutable release attestation;
- JHW selection of fallback evidence without accepting another App or a free-form
  bot comment; and
- required CI, target, current-head/base and atomic merge gates remain unchanged.

Run the focused workflow, budget, canonicalization, rollout and release-verifier
suites; actionlint all release-managed workflows; then run the full repository suite.
The JHW source repository runs its own focused and full command tests independently.

The canary evidence must include exact request/comment bytes, run/job JSON, canonical
state, budget ledger/metrics, PR/head/base/diff/release attestation, check runs and
provider call count. Native pre-PR tribunal and hosted reviewers review the
implementation before merge. Immutable release publication and consumer rollout
remain separate reviewed actions.

## Current stop boundary

This design approval permits the design document and subsequent implementation plan.
It does not by itself publish a new immutable release or mutate another repository's
Project Control state. The current gstApp and max9296 PRs remain open until their
existing merge proposal is revalidated at exact current HEAD/base and the approved
bootstrap substitution is recorded.

After written-spec review, create the implementation plan in the existing #182
worktree. Preserve the active Claim; do not recreate it. Implement the automation
side first, then obtain any separately required JHW repository Task authorization
before changing its command source.

## Sources

Local code:

- `.github/workflows/claude-code-review.yml`
- `.github/workflows/claude.yml`
- `.github/actions/prepare-review-diff/action.yml`
- `.github/actions/canonicalize-review/action.yml`
- `.github/actions/review-invocation-budget/`
- `scripts/rollout_workflow_fleet.py`
- `scripts/verify_workflow_release.py`
- `docs/workflows/contracts.md`
- `/home/jhw/ai/opencode/projects/jhw-notion-runtime/skills/claude/pr.md`

GitHub reusable workflow and event behavior:

- https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations
- https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows
- https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows
