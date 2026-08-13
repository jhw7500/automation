# Actions Runtime Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove deprecated checkout runtimes and make the OpenCode CLI artifact reproducible without weakening the existing GitHub-token security boundary.

**Architecture:** Central reusable workflows remain the only fleet integration point. Every managed checkout reference is pinned to the verified `actions/checkout` v7.0.1 commit, while the two OpenCode workflows download one tested Linux x64 release archive, verify its published SHA-256 digest, and invoke `opencode github run` with the same restricted token environment as today.

**Tech Stack:** GitHub Actions YAML, Python `unittest`/`pytest`, PyYAML, actionlint, GitHub release artifacts.

## Global Constraints

- Use `actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1` (`v7.0.1`) everywhere managed by this repository.
- Pin OpenCode CLI to `1.18.17` and verify archive SHA-256 `3f14a4c61c7f6b0d3b6d933d1d212e64e19683eba6fa453ad98e46303afe144a` before extraction.
- Keep `contents: read`, `pull-requests: write`, `issues: write`, no `id-token`, and `GITHUB_TOKEN: ${{ github.token }}` for both OpenCode execution jobs.
- Do not publish or move a release tag until the central PR is merged and the merge commit passes local release verification.
- Preserve unrelated changes in the original `automation`, `wlan-package`, and `personal-ops` checkouts.

---

### Task 1: Pin checkout v7.0.1

**Files:**
- Create: `tests/test_action_pins.py`
- Modify: `.github/workflows/*.yml`
- Modify: `examples/baseline-workflows/workflows/bump-automation-ref.yml`
- Modify: `examples/baseline-workflows/.github/workflows/bump-automation-ref.yml`

**Interfaces:**
- Consumes: the upstream v7.0.1 tag resolved to commit `3d3c42e5aac5ba805825da76410c181273ba90b1`.
- Produces: `test_all_managed_checkout_references_use_the_approved_sha`, a fleet-wide invariant over central and baseline workflow YAML.

- [ ] **Step 1: Add a failing test that reports every checkout reference not equal to the approved full SHA.**
- [ ] **Step 2: Run `python3 -m pytest -q tests/test_action_pins.py` and confirm it fails on the current v4/v5 references.**
- [ ] **Step 3: Replace all reported references with the approved SHA and retain an inline `# v7.0.1` version comment.**
- [ ] **Step 4: Run the focused test, full pytest suite, YAML parsing, and actionlint regression comparison.**
- [ ] **Step 5: Commit the checkout migration independently.**

### Task 2: Pin and verify the OpenCode CLI artifact

**Files:**
- Modify: `.github/workflows/opencode.yml`
- Modify: `.github/workflows/opencode-auto-review.yml`
- Modify: `scripts/verify_workflow_release.py`
- Modify: `tests/test_workflow_secret_contracts.py`
- Modify: `tests/test_verify_workflow_release.py`
- Modify: `tests/test_action_pins.py`

**Interfaces:**
- Consumes: GitHub release asset `v1.18.17/opencode-linux-x64.tar.gz` and its GitHub-published SHA-256 digest.
- Produces: identical `Install pinned OpenCode CLI` and `Run ...` step contracts in both reusable workflows; `verify_tag_content()` rejects version, digest, token, permission, or OIDC regressions.

- [ ] **Step 1: Add failing contract tests for the exact version, URL, digest, cache action SHA, checksum check, and `USE_GITHUB_TOKEN=true` runtime environment.**
- [ ] **Step 2: Run the focused tests and confirm failure against the dynamic upstream-action implementation.**
- [ ] **Step 3: Add a Node.js 24 cache step for the exact archive, download it only on cache miss, verify SHA-256 on every run, extract to a runner-temporary directory, and invoke `opencode github run` directly.**
- [ ] **Step 4: Update release verification and its fixtures so any unpinned version/digest or weakened GitHub-token contract fails closed.**
- [ ] **Step 5: Download the actual release asset, verify its SHA-256, extract it, and confirm `opencode --version` prints `1.18.17`.**
- [ ] **Step 6: Run full pytest, YAML parsing, actionlint regression comparison, `git diff --check`, and release-content verification against the candidate commit.**
- [ ] **Step 7: Commit the CLI pin separately and publish a PR only after every gate succeeds.**

### Task 3: Staged release and canary

**Files:**
- Modify after execution evidence: `docs/workflows/contracts.md` or rollout result documentation if operational notes change.

**Interfaces:**
- Consumes: the merged automation commit and the existing `verify_workflow_release.py` release gate.
- Produces: one new immutable automation tag and one same-repository OpenCode canary before any fleet ref bump.

- [ ] **Step 1: Merge the reviewed central PR and fetch only `origin/main` without overwriting existing local tags.**
- [ ] **Step 2: Re-run all local gates on the merge commit.**
- [ ] **Step 3: Create a new annotated tag on that exact merge commit and run local verification before pushing it.**
- [ ] **Step 4: Push the tag, run remote verification, then execute one enabled same-repository OpenCode canary.**
- [ ] **Step 5: Stop on any mismatch or canary failure; never move the tag. Roll back consumers by restoring their prior automation ref, and correct central code only through a newer release tag.**
