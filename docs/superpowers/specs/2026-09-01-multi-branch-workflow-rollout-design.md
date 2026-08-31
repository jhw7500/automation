# Multi-Branch Workflow Rollout Design

## Problem

The fleet rollout and audit commands currently inspect only each repository's default branch.
`wlan-driver-v2` also maintains the active `ported` branch. Because common workflow callers were
updated only on `main`, `ported` drifted until Claude's identical-default-workflow validation
prevented reviews on pull requests targeting that branch.

## Decision

Keep the default branch implicit and add a closed `additional_branches` list to each repository
profile. The live schema becomes version 2; historical schema-1 release bundles remain readable
and behave as default-branch-only bundles. Initially only `wlan-driver-v2` declares `ported`.

Both rollout and audit expand each selected repository into one default target plus its configured
additional targets. `--repo wlan-driver-v2` therefore covers both `main` and `ported` without a new
operator flag. `AuditResult`, CLI output, and manifests identify the exact resolved base branch;
summary totals count branch targets rather than repositories. Each audit target uses a fresh marked
single-branch clone, so a clone or fetch failure remains visible for that target without suppressing
the rest of the audit batch.

The existing default rollout head remains `automation/common-workflows-<ref>`. An additional target
uses `automation/common-workflows-<ref>-<sha256(base-branch)>`. The full digest avoids Git ref prefix
conflicts, preserves every valid Git branch name without lossy escaping, and gives each base branch
a deterministic one-to-one rollout identity. Pull-request title, body, commit identity, exact-PR
reuse checks, and attestation all bind the additional target's base branch.

The restricted Git adapter records both the repository default branch and the selected base branch.
Default-only wrapper functions remain for compatibility; branch-aware clone/refetch functions power
the new paths. Each branch target receives a fresh single-branch clone so switching or rendering one
target cannot contaminate another.

All branch targets complete read-only prevalidation before the first remote mutation. One blocked
target blocks publication for the entire selected batch, preserving the existing all-prevalidate
gate. Bootstrap remains default-branch-only; a missing configuration on an additional branch is a
normal blocked target.

## Rejected Alternatives

- An operator-only `--base-branch` flag is not durable and allows audits to silently omit an active
  branch.
- Making caller wrappers reference mutable tags weakens immutable release pinning and changes the
  security model far beyond this incident.
- Embedding raw base names in rollout heads creates ref-prefix and escaping collisions for valid Git
  branch names.

## Completion Conditions

- Schema-1 bundles remain default-only and schema-2 profiles validate `additional_branches`.
- `wlan-driver-v2` expands to default plus `ported`; all other repositories remain default-only.
- Plan, publish, reuse, and attestation bind the exact base branch and unique rollout head.
- A blocked additional target prevents every remote mutation in that batch.
- Audit reports every configured branch target and cannot call the fleet current while one is drifted.
- The current 16-repository fleet completes only at `current=17`, `drift=0`, and `blocked=0`.
- Existing default-branch behavior, immutable release verification, and restricted Git/GitHub
  boundaries remain green.
- No release tag or consumer rollout is created in this issue PR.
