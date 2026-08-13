# Workflow Fleet Rollout

The fleet tooling standardizes only catalogued common AI caller workflows. It renders a
managed diff, validates it, pushes a deterministic repository branch, and opens a pull
request. It never merges, reverts, force-pushes, updates a default branch, or writes an
Actions secret or variable.

The release input is an immutable annotated automation tag. For `v1.40`, the local and
remote tag objects must agree and resolve to one verified commit before any consumer is
processed. Rendered callers pin that 40-character commit rather than the tag text.

## Workflow PR rollout

### Prerequisites and workspace

Use a dedicated disposable directory. The first plan initializes its marker; subsequent
plan, publish, and audit commands reuse it. The scripts accept only repositories declared
by `scripts/workflow-config.json`. Git and `gh` use the operator's normal GitHub
authentication, but provider credentials are removed from child environments. Supply the
locally installed, reviewed `actionlint` executable explicitly.

CI pins and verifies actionlint itself, then runs its YAML schema and expression gate with
`-shellcheck= -pyflakes=`. Empty analyzer paths make this gate deterministic and independent
of optional host ShellCheck/Pyflakes installations; actionlint's own diagnostics remain
fail-closed. The rollout validator uses the same actionlint boundary for managed callers.

```bash
FLEET_WORKSPACE=/path/to/disposable-workflow-fleet
ACTIONLINT=/path/to/actionlint
```

Do not place unrelated files or working repositories in `FLEET_WORKSPACE`.

### 1. Read-only plan

Run the complete fleet plan before creating any branch:

```bash
python3 scripts/rollout_workflow_fleet.py \
  --workspace "$FLEET_WORKSPACE" \
  --initialize-workspace \
  --mode plan \
  --ref v1.40 \
  --actionlint "$ACTIONLINT"
```

Use repeated `--repo NAME` arguments to narrow a later read-only plan. Plan clones and
reads GitHub state, renders locally, validates YAML/catalog contracts, and writes
`$FLEET_WORKSPACE/rollout-manifest.json`; it performs no remote mutation.

The renderer classifies content as:

- `current`: managed content already matches the release;
- `drift`: a normal managed PR can converge it;
- `bootstrap_required`: an allowed caller-free repository needs explicit bootstrap; or
- `blocked`: repository, inventory, contract, or remote state is unsafe.

After checking the deterministic branch and PR state, plan reports actionable drift as
`planned`, an exact reusable PR as `reusable`, and otherwise reports `current` or
`blocked`. The manifest includes the observed base SHA, release commit, required secret
and variable **names**, and managed diff paths. It is a convenience report, not an
approval token; publish always refetches and recomputes.

### 2. Publish independent PRs

Publish requires explicit repositories and confirmation:

```bash
python3 scripts/rollout_workflow_fleet.py \
  --workspace "$FLEET_WORKSPACE" \
  --mode publish \
  --ref v1.40 \
  --repo wlan-package \
  --confirm \
  --actionlint "$ACTIONLINT"
```

For `v1.40`, every repository uses the deterministic branch
`automation/common-workflows-v1.40`. Publish may only create a local managed commit, push
that non-default branch, and create a PR. It has no merge, auto-merge, update-branch,
default-branch push, secret-write, variable-write, or revert operation.

All selected repositories pass read-only prevalidation before the first remote effect.
Publication then refetches and recomputes each repository immediately before its branch
is created or reused. The reuse rules are fail-closed:

- an absent rollout branch may be created from the freshly fetched default branch;
- a matching branch may receive its missing PR only when its base, release commit, and
  managed path/blob diff exactly match the fresh render;
- one exact open PR is reusable only when its base branch, head repository, head branch,
  head object ID, title, body, and remote branch object ID all match;
- a mismatched branch or PR, multiple PRs, or a closed or merged PR whose rollout branch
  remains present blocks that repository; and
- no mismatch is repaired with a force-push.

Publish reports `published`, `reused`, `current`, or `blocked`. A network or permission
failure after earlier repositories were published is **partial success**: valid PRs remain
open, every observed result is recorded, and the process returns non-zero when any
repository is blocked. It does not roll back successful repositories. Correct the cause
and rerun the same command; exact branches and PRs are safely reused.

Bootstrap is deliberately separate and accepts exactly one matching, bootstrap-allowed
repository. Its new config disables every common workflow:

```bash
python3 scripts/rollout_workflow_fleet.py \
  --workspace "$FLEET_WORKSPACE" \
  --mode publish \
  --ref v1.40 \
  --repo cts-email-mcp-server \
  --bootstrap-repo cts-email-mcp-server \
  --confirm \
  --actionlint "$ACTIONLINT"
```

Enabling callers in a bootstrapped repository is a later, ordinary repository PR.

### 3. Canary sequence

Do not create the remaining fleet PRs until repository owners have reviewed, merged, and
runtime-tested these three independent canaries in order:

1. `wlan-package` — App-auth Gemini plus Claude and manual/automatic OpenCode;
2. `wlan-driver` — built-in GitHub-token Gemini plus OpenCode; and
3. `cts-email-mcp-server` — an explicitly disabled bootstrap.

Use the publish command above for `wlan-package`, then repeat it with
`--repo wlan-driver`. Use the explicit bootstrap command for
`cts-email-mcp-server`. Stop on any failed PR check or runtime canary.

After all three succeed, publish the remaining non-bootstrap repositories with repeated
`--repo NAME` arguments. Bootstrap `wpa-supplicant` separately:

```bash
python3 scripts/rollout_workflow_fleet.py \
  --workspace "$FLEET_WORKSPACE" \
  --mode publish \
  --ref v1.40 \
  --repo wpa-supplicant \
  --bootstrap-repo wpa-supplicant \
  --confirm \
  --actionlint "$ACTIONLINT"
```

### 4. Read-only audit

Audit current default-branch content after reviewed merges:

```bash
python3 scripts/audit_workflow_fleet.py \
  --workspace "$FLEET_WORKSPACE" \
  --ref v1.40
```

Repeated `--repo NAME` arguments narrow the audit. Audit reports `current`, `drift`, or
`blocked` from managed bytes and contracts; it ignores project-owned workflow
differences. During rollout, an unmerged repository legitimately remains `drift`. The
completion condition is `current=19`, `drift=0`, and `blocked=0`.

### Review, merge, and recovery

Repository owners inspect each diff, run project-specific CI, obtain their normal reviews,
and use a **GitHub-native merge**. Merge commit, squash, or rebase policy remains local to
the repository because audit validates final content.

Before merge, close the PR and delete its rollout branch if recovery is needed. After
merge, use a **GitHub-native revert** PR (or a normal reviewed PR containing `git revert`).
Never move an immutable automation tag. Repair a bad central release with a new tag and
new consumer PRs.

## Token synchronization

Workflow PR rollout and credential lifecycle are separate operations. The workflow tools
may inspect required secret and variable **names** as preconditions, but they never read a
provider value, consume a local provider file, or call a secret/variable mutation API.
Missing names block the affected repository until an independently reviewed credential
process resolves them.

Claude token rotation remains owned by `personal-ops/claude-token-sync`, including its
inventory, locking, health checks, and deployment lifecycle. Other provider-key changes
must use their separately reviewed owner process. There is intentionally no command that
combines workflow PR publication with token synchronization.
