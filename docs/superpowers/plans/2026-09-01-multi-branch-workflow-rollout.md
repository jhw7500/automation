# Multi-Branch Workflow Rollout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make fleet rollout and audit cover every configured active branch while preserving default-branch behavior and fail-closed publication.

**Architecture:** Fleet schema v2 adds per-repository `additional_branches`; schema v1 remains readable as default-only. The restricted Git adapter returns snapshots containing both repository default and selected base branches. Rollout and audit expand repositories into branch targets, isolate each target in a fresh clone, and bind all branch/PR/manifest identities to the selected base.

**Tech Stack:** Python 3.12, dataclasses, Git/GitHub CLI adapter, pytest, JSON, SHA-256, actionlint.

**Spec:** `docs/superpowers/specs/2026-09-01-multi-branch-workflow-rollout-design.md`

## Global Constraints

- Keep the current deterministic default rollout head unchanged.
- Preserve immutable automation SHA pinning and the restricted Git Data API boundary.
- Prevalidate every selected repository/branch target before the first remote mutation.
- Bootstrap only the repository default branch.
- Do not create a release tag or consumer pull request in this issue PR.
- Do not modify issue #52 or Notion.

---

### Task 1: Versioned Fleet Configuration

**Files:**
- Modify: `scripts/workflow_catalog.py`
- Modify: `scripts/workflow-config.json`
- Test: `tests/test_workflow_catalog.py`

**Interfaces:**
- Consumes: schema-1 and schema-2 `scripts/workflow-config.json` bytes.
- Produces: `RepoProfile.additional_branches: tuple[str, ...]` with schema-1 default `()`, plus
  `configured_branch_targets(profile) -> tuple[str | None, ...]` returning default sentinel `None`
  followed by configured additional branches.

- [ ] **Step 1: Write failing schema tests**

Add behavior tests proving the live `wlan-driver-v2` profile yields `("ported",)`, all other live
profiles yield `()`, a copied schema-1 config remains readable as default-only, and duplicate,
non-string, empty, or invalid Git branch names are rejected. Assert the shared target helper returns
`(None, "ported")` for `wlan-driver-v2` and `(None,)` for a default-only profile.

- [ ] **Step 2: Run the tests and verify RED**

Run: `python3 -m pytest -q tests/test_workflow_catalog.py`

Expected: failures because `RepoProfile` has no `additional_branches` and schema 2 is unsupported.

- [ ] **Step 3: Implement the minimal versioned parser**

Extend the profile record:

```python
@dataclass(frozen=True)
class RepoProfile:
    name: str
    profile: str
    optional_workflows: frozenset[str]
    repo_write_auth: Literal["github_app", "github_token"]
    bootstrap_allowed: bool
    additional_branches: tuple[str, ...]
```

Accept schema versions 1 and 2. Schema 1 requires the historical four profile keys and supplies an
empty tuple. Schema 2 requires those keys plus `additional_branches`, validates a unique ordered list
of valid non-empty Git branch names, and preserves that order. Add the pure shared target helper so
rollout and audit cannot diverge in expansion order. Change the live config to schema 2, set
`wlan-driver-v2.additional_branches` to `["ported"]`, and set every other repository to `[]`.

- [ ] **Step 4: Run the tests and verify GREEN**

Run: `python3 -m pytest -q tests/test_workflow_catalog.py tests/test_prepare_workflow_rollout.py`

- [ ] **Step 5: Commit the configuration unit**

```bash
git add scripts/workflow_catalog.py scripts/workflow-config.json tests/test_workflow_catalog.py
git commit -m "feat(fleet): configure additional rollout branches"
```

### Task 2: Branch-Aware Restricted Git Adapter

**Files:**
- Modify: `scripts/workflow_fleet_git.py`
- Test: `tests/test_workflow_fleet_git.py`

**Interfaces:**
- Consumes: optional requested base branch and the configured repository identity.
- Produces: `RepositorySnapshot.base_branch`, `clone_branch(...)`, and `refetch_branch(...)` while retaining default-only wrappers.

- [ ] **Step 1: Write failing adapter tests**

Add tests that clone `ported` with `git clone --single-branch --branch ported`, retain the reported
repository default `main`, refetch `ported`, reject malformed or missing branch metadata, and accept
only the expected SHA-256-suffixed rollout ref/commit identity for an additional base.

- [ ] **Step 2: Run the tests and verify RED**

Run: `python3 -m pytest -q tests/test_workflow_fleet_git.py`

Expected: failures because snapshots and adapter entry points are default-only.

- [ ] **Step 3: Implement the minimal adapter surface**

Add `base_branch` to `RepositorySnapshot`. Implement:

```python
def clone_branch(owner: str, repo: str, workspace: Path, branch: str | None) -> RepositorySnapshot:
    ...

def clone_default_branch(owner: str, repo: str, workspace: Path) -> RepositorySnapshot:
    return clone_branch(owner, repo, workspace, None)

def refetch_branch(snapshot: RepositorySnapshot) -> str:
    ...
```

Keep `refetch_default` as the compatibility wrapper for default snapshots. Extend the closed rollout
ref validator with an optional 64-hex digest suffix and validate that suffix against
`sha256(snapshot.base_branch.encode("utf-8"))` whenever the selected base differs from the default.
Bind the expected rollout commit message to the selected base branch.

- [ ] **Step 4: Run the tests and verify GREEN**

Run: `python3 -m pytest -q tests/test_workflow_fleet_git.py`

- [ ] **Step 5: Commit the adapter unit**

```bash
git add scripts/workflow_fleet_git.py tests/test_workflow_fleet_git.py
git commit -m "feat(fleet): inspect explicit base branches"
```

### Task 3: Multi-Branch Rollout Planning and Publication

**Files:**
- Modify: `scripts/rollout_workflow_fleet.py`
- Test: `tests/test_rollout_workflow_fleet.py`

**Interfaces:**
- Consumes: `RepoProfile.additional_branches`, branch-aware snapshots, and existing release bundles.
- Produces: branch-bound `RepoOutcome`, `PreparedRepo`, rollout head/title/body, exact PR reuse, publication, and manifest records.

- [ ] **Step 1: Write failing identity and expansion tests**

Add tests proving default identity text is byte-for-byte unchanged; `ported` receives a literal
hand-derived digest-suffixed head, base-bound title/body, and exact PR base; `--repo wlan-driver-v2`
prevalidates and publishes both targets; and a blocked `ported` prevalidation prevents publication
of `main` and every other selected target.

- [ ] **Step 2: Run the tests and verify RED**

Run: `python3 -m pytest -q tests/test_rollout_workflow_fleet.py`

Expected: failures because result models and loops contain one target per repository.

- [ ] **Step 3: Implement branch-bound identities**

Add `base_branch` to public outcomes and prepared records. Make `rollout_branch`, `pr_title`, and
`pr_body` accept selected/default branch identity while preserving their current output for the
default branch. Pass the exact identity through commit construction, inspection, reuse, creation,
and attestation.

- [ ] **Step 4: Implement target expansion and isolation**

Expand each selected profile through `configured_branch_targets(profile)`, where `None` means the
discovered default. Give each target its own marked temporary clone, prevalidate the full expanded
batch before mutation, publish the exact prepared targets only after the gate passes, and reject
bootstrap on additional targets.

- [x] **Step 5: Run the tests and verify GREEN**

Run: `python3 -m pytest -q tests/test_rollout_workflow_fleet.py tests/test_workflow_fleet_git.py`

- [ ] **Step 6: Commit the rollout unit**

```bash
git add scripts/rollout_workflow_fleet.py tests/test_rollout_workflow_fleet.py
git commit -m "feat(fleet): publish per active branch"
```

### Task 4: Multi-Branch Audit and Operator Contract

**Files:**
- Modify: `scripts/audit_workflow_fleet.py`
- Modify: `docs/workflow-fleet-rollout.md`
- Test: `tests/test_audit_workflow_fleet.py`

**Interfaces:**
- Consumes: the same branch target expansion as rollout.
- Produces: one branch-identified `AuditResult` per target and summary totals over targets.

- [x] **Step 1: Write failing audit tests**

Add tests proving the live 16-repository config audits 17 branch targets, output distinguishes
`wlan-driver-v2[main]` and `wlan-driver-v2[ported]`, `ported` drift prevents an all-current result,
and clone/fetch failures block only their exact target while remaining visible in the summary.

- [x] **Step 2: Run the tests and verify RED**

Run: `python3 -m pytest -q tests/test_audit_workflow_fleet.py`

Expected: failures because audit currently clones and reports only repository defaults.

- [x] **Step 3: Implement branch-aware audit**

Add `base_branch` to `AuditResult`, expand selected repositories exactly as rollout does, use a fresh
marked clone workspace for each target, and print target-qualified results. Count branch targets in
the final summary.

- [x] **Step 4: Update the operator contract**

Document schema v2, default-plus-additional expansion, target-qualified manifest/output, unique head
identity, all-target prevalidation, default-only bootstrap, and a 17-target completion condition for
the current fleet.

- [ ] **Step 5: Run the tests and verify GREEN**

Run: `python3 -m pytest -q tests/test_audit_workflow_fleet.py tests/test_rollout_workflow_fleet.py`

- [x] **Step 6: Commit the audit/documentation unit**

```bash
git add scripts/audit_workflow_fleet.py docs/workflow-fleet-rollout.md tests/test_audit_workflow_fleet.py docs/superpowers/specs/2026-09-01-multi-branch-workflow-rollout-design.md docs/superpowers/plans/2026-09-01-multi-branch-workflow-rollout.md
git commit -m "docs(fleet): define multi-branch rollout contract"
```

### Task 5: Release Contract and Final Verification

**Files:**
- Modify if required by authenticated bytes: `scripts/verify_workflow_release.py`
- Test: `tests/test_verify_workflow_release.py`
- Test: `tests/test_workflow_release_bundle.py`

**Interfaces:**
- Consumes: completed schema/adapter/rollout/audit implementation.
- Produces: a release-verifiable, review-ready branch without creating a release.

- [ ] **Step 1: Run release-focused verification**

Run: `python3 -m pytest -q tests/test_verify_workflow_release.py tests/test_workflow_release_bundle.py`

If authenticated byte checks fail, recalculate only the exact live/historical transformed digests
required by those failures, update the verifier, and rerun the failing tests.

- [ ] **Step 2: Run workflow validation**

Run CI's YAML parser over `.github/workflows` and `examples/baseline-workflows/.github/workflows`,
then run digest-verified actionlint 1.7.12 with `-shellcheck= -pyflakes=` over both directories.

- [ ] **Step 3: Run the full suite**

Run: `python3 -m pytest -q`

Expected: every test and subtest passes with zero failures.

- [ ] **Step 4: Review and integrate**

Run an independent read-only review over `origin/main..HEAD`, address every Critical or Important
finding with a new RED→GREEN cycle, push `feat/76-multi-branch-rollout`, open a PR containing
`Fixes #76`, wait for required CI, and merge with the repository's merge-commit convention.
