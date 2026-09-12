# v1.78.3 Managed Review Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Existing user authorization permits inline preparation and reviews; the concrete publication and merge boundaries below remain separate.

**Goal:** Prove one caller-changing rollout from the installed v1.78.2 driver to a distinct, reviewed v1.78.3 commit using the authenticated managed Claude route and its immutable verifier receipt.

**Architecture:** Preserve every v1.78.2 release-owned byte and create only documentation plus publication-guard tests on a distinct automation commit. Publish that reviewed commit through a create-only Git Data procedure, then use the existing fleet renderer and v1.78.2 verifier on one wlan-package rollout. The failed automatic check remains failed; the managed fallback consumes one ordinary automatic review invocation and produces a separately verified receipt.

**Tech Stack:** Python standard library, pytest, Git, GitHub REST, existing fleet renderer, release verifier, Claude fallback contract and installed pre-PR tribunal.

**Spec:** `../specs/2026-09-11-claude-caller-rollout-fallback-design.md`; `2026-09-11-claude-caller-rollout-fallback.md`, Task 8 Step M; `../../workflow-fleet-rollout.md`, v1.78.2 adoption.

## Global Constraints

- This follows the existing automation #182 Task and supported Claim. No new Task/Claim, takeover, Registry edit, secret change, quota increase or branch-rule change is part of this work.
- Preparation uses `/home/jhw/ai/opencode/projects/automation/.worktrees/v1.78.3-boundary-canary`, branch `docs/v1.78.3-boundary-canary`, from public main `74afc0057c4732993ecd76601590b8856e2a3bb3`. This supersedes only the old plan's implementation-worktree coordinates for this follow-up. Preserve its original worktree and untracked `.ai/handoff.md`.
- Every shell command starts with `rtk`. Source edits use `apply_patch`. No Yocto/BitBake task, build-cache mutation, consumer script execution or unrelated checkout cleanup.
- Driver: v1.78.2 annotated tag `edadc46ec59d0976e9fe5fd020aeb5a96685c3b7`, peeled commit `74afc0057c4732993ecd76601590b8856e2a3bb3`.
- Target: only the absent `v1.78.3` tag at a later separately reviewed public-main commit. Never repoint or delete any tag, and never blindly retry an uncertain POST.
- Authenticate direct and peeled identities of v1.76, v1.77, v1.78, v1.78.1 and v1.78.2 before each publication POST. Authenticate v1.78.2's annotation locally before importing its inventory. Require a distinct target, empty raw release-owned diff and a closed documentation/test change set.
- Canary: `jhw7500/wlan-package`, default branch `master`; initial observation `7fac9ff9f71a0e6f60d4653dc7de5eb6e639c0a9`. Reobserve before rendering. Abort publication on base/head drift; do not retarget an approved tuple silently.
- Keep the fleet-generated branch, title, description and managed paths exact. In particular, do not append review summaries or permission explanations to the generated canary PR body: the immutable receipt verifier authenticates that body.
- Native A/B/C receipts must preserve exact response bytes, current-user ownership, regular non-symlink type and mode 0600, including the immediate pre-final checks. A passed review binds only its exact HEAD/base/diff.
- Separate concrete approvals: automation reviewed merge; v1.78.3 tag creation; consumer branch/PR plus review label and Codex request; exact two-line managed Claude request; receipt-bound consumer merge. Earlier approvals do not cover a new tuple or later publication.
- Original automatic failure, branch protections, required checks, CI, exact head/base and GitHub merge-parent checks remain intact. Manual conversational Claude review is not evidence for this canary's managed-route success.

## Task 1: Reviewable create-only procedure and operational contract

**Files:**

- Create: `docs/workflows/v1.78.3-boundary-canary.md` — executable operator procedure and receipt-bound runbook.
- Create: `tests/test_v1783_publication_procedure.py` — execute the embedded pre-write guard and response writer against bounded fake Git/API transport; no network or real tag mutation.
- Modify: `docs/workflow-fleet-rollout.md` — link the new procedure without changing historical procedures.
- Create: this plan.

**Interfaces:** The operator procedure consumes `RELEASE_TAG=v1.78.3`, the separately approved full `EXPECTED_RELEASE_COMMIT` and an absent absolute `RELEASE_DIRECTORY`; the existing private-descriptor launcher supplies exactly one intended token. It emits one annotated-tag-object response and one create-only ref response, preserved exactly as mode-0600 files. The procedure's `guard_before_write()` is called by `post()` immediately before each write.

- [ ] Write behavior tests that refuse both POSTs on historical tag drift, default-branch drift, an existing target ref, release-owned changes, and an unapproved changed path. Test that the second write repeats the checks, successful responses retain exact bytes/mode 0600 under permissive umask, and an uncertain POST cannot be repeated into the same response path.
- [ ] Run `rtk python3 -m pytest -q tests/test_v1783_publication_procedure.py`; confirm failure because the new procedure is absent.
- [ ] Add the complete procedure and runbook. Pin the five observed historical identities. Use the authenticated v1.78.2 inventory and require an empty `git diff --raw` before each POST. Require the exact four files listed above as the complete change set; the candidate release verifier and all runtime modules therefore remain unchanged.
- [ ] Run the new tests, then `rtk python3 -m pytest -q tests/test_verify_workflow_release.py tests/test_verify_claude_rollout_fallback.py tests/test_claude_rollout_fallback.py tests/test_v1783_publication_procedure.py` and `rtk git diff --check`. After committing, also check the complete `74afc0057c4732993ecd76601590b8856e2a3bb3..HEAD` range; a clean worktree alone cannot prove committed whitespace is clean.
- [ ] Prove an empty inventory-owned diff, run the v1.78.3 commit-only verifier on the committed candidate, and run the normal full test suite once.
- [ ] Commit as `docs(release): define v1.78.3 managed boundary canary`. Complete the installed three-role tribunal, then publish one ordinary automation PR and collect configured hosted reviewers plus a current-head Codex request. Present the exact reviewed merge tuple for approval. Do not create v1.78.3 yet.

## Task 2: Publish the immutable target after separate tag approval

**Consumes:** Approved merged automation commit and the procedure in Task 1.
**Produces:** Authenticated v1.78.3 direct/peeled refs and exact mode-0600 API responses.

- [ ] Read the GitHub-confirmed automation merge SHA and its tree; compare public main, authenticate all five old tags, prove absent v1.78.3 direct/peeled refs and empty release-owned diff. Capture the commit-only verifier output and release inventory digest manifest.
- [ ] Present those concrete objects and request approval to create that one tag. The eventual merge SHA is a runtime output, never a guessed or pre-authorized value.
- [ ] After approval, invoke the complete procedure with the approved SHA. Make one tag-object POST and one tag-ref POST, each guarded immediately beforehand. Any failed, mismatching or uncertain response stops for read-only reconciliation; preserve an orphan tag object if the ref write cannot be confirmed.
- [ ] Authenticate the final direct/peeled refs through public Git and the release verifier. Preserve all old tags unchanged.

## Task 3: One managed canary and immutable receipt

**Consumes:** Published v1.78.3 target; installed v1.78.2 driver; one fresh wlan-package snapshot.
**Produces:** One native-reviewed fleet PR, automatic failure evidence, one canonical managed request, one provider invocation, finalized budget and schema-1 verifier receipt.

- [ ] Use a fresh private fleet workspace and the authenticated target checkout. Execute the runbook's preparation with the released renderer and deterministic commit constructor. Preserve that single candidate, complete diff and mode-0600 manifest with its digest. Confirm the eleven managed paths, prior driver pin, new target pin and deterministic branch. Stop on unmanaged drift or an existing rollout branch/PR.
- [ ] Run the installed A/B/C review on that committed candidate without changing its tree/title/body. Preserve the review binding and exact candidate tree. Present the concrete repository/branch/head/base/tree, full diff, manifest digest, label and Codex request for approval.
- [ ] Publish that retained commit once through the runbook's guarded create-only adapters after verifying the approved manifest digest. Do not use stock CLI publish or rerender after approval: stock publish recomputes a fresh candidate and has no approved-tuple input. Test base/head/tree mismatch with zero remote writes and base drift between object uploads with no subsequent ref/PR write. Reobserve before each write and after PR creation; a race can leave exact approved objects, requiring read-only reconciliation. Require one attested PR before applying the existing `review:request` label once and requesting Codex once at the exact current head/base.
- [ ] Collect the exact current-head automatic Claude run, attempt, jobs, annotations and canonical state. Only `workflow_validation_mismatch`, `review_execution=not_performed`, skipped provider and no automatic budget claim qualify. Any other result or native required-check blocker ends this canary without substitution.
- [ ] Generate the exact two-line managed request with the authenticated v1.78.2 `FallbackRequest` and `canonical_request_body`, one 16-byte nonce and the published manifest's complete coordinates. Present the full body and obtain its separate posting approval. Reuse one existing exact request rather than posting a duplicate; ambiguous or uncertain writes stop for read-only reconciliation.
- [ ] Observe one default-branch `claude.yml` run, classifier route `managed`, the nested review at the v1.78.2 driver, exactly one provider entry, one finalized ordinary automatic round and a canonical clean schema-3 fallback result. Do not retry the provider or replace this proof with an interactive review.
- [ ] Execute the runbook's verifier command from a pristine v1.78.2 checkout with output outside that checkout. Require `effective_status=CLEAN`, `verifier_commit == fallback.driver_commit == 74afc0057c4732993ecd76601590b8856e2a3bb3`, and distinct `release_commit` equal to the authenticated v1.78.3 commit.
- [ ] Re-read receipt-bound live head/base, branch identity, exact canonical request/state, run/attempt, budget, required checks, CI, Codex and native review. Present the exact consumer merge tuple for approval. Merge only after approval and reconfirm all bindings; verify GitHub MERGED before deleting its feature branch.
- [ ] Audit wlan-package's final default-branch pins, preserve the receipt and original failed run, and record the complete chain. Only then assess existing Task completion readiness through supported Project Control; no Registry or Claim edits are permitted by this plan.

## Stop and recovery rules

Stop before the next mutation on moved coordinates, unsafe report/response type or mode, changed release-owned bytes, historical tag drift, a second provider entry, ambiguous request/run, non-finalized budget, noncanonical PR metadata, a required failed check or a blocking review finding. Reconcile existing remote objects read-only before any retry. An operator interruption preserves all evidence and owned views; it does not authorize duplicate dispatches, a new tag, a new request or a Claim takeover.
