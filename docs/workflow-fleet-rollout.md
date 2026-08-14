# Workflow Fleet Rollout

The fleet tooling standardizes only catalogued common AI caller workflows. It renders a
managed diff, validates it, atomically creates a deterministic repository branch, and opens
a pull request. It never merges, reverts, forces, updates a default branch, or writes an
Actions secret or variable.

The release input is an immutable annotated automation tag. For `v1.40`, the local and
remote tag objects must agree and resolve to one verified commit before any consumer is
processed. Rendered callers pin that 40-character commit rather than the tag text.

## Workflow PR rollout

### Prerequisites and workspace

Use a dedicated disposable directory. The first plan initializes its marker; subsequent
plan, publish, and audit commands reuse it. The scripts accept only repositories declared
by `scripts/workflow-config.json`. Consumer-repository Git and `gh` operations use the
operator's normal GitHub authentication, but provider credentials are removed from child
environments. Supply the locally installed, reviewed `actionlint` executable explicitly.

Automation release verification has a narrower boundary. It discovers a normal repository
or linked worktree by reading the `.git` pointer and `commondir` itself, reads tag refs
directly, rejects alternates and promisor/shallow object stores, and creates an isolated
temporary Git directory. Raw object commands use absolute `/usr/bin/git`, point
`GIT_OBJECT_DIRECTORY` only at the discovered common object directory, set
`GIT_NO_REPLACE_OBJECTS=1`, and receive a nonexistent home/XDG root with all system and global Git configuration disabled, along with prompts, askpass, and the SSH agent. They never load source
`.git/config`, `.git/info/attributes`, filters, hooks/helpers, or replacement refs. Release
archives are constructed from exact raw tree/blob OIDs with fixed tar metadata rather than
`git archive`, so attributes cannot transform bytes or execute a filter.

Remote tag verification accepts only the local
remote name `origin`, requires that its directly configured URL be exactly the public
`jhw7500/automation` HTTPS URL, canonicalizes it to
`https://github.com/jhw7500/automation.git`, and runs a credential-free public HTTPS
`ls-remote` outside the checkout. It does not load host or repository credential helpers,
URL rewrites, includes, or SSH commands. A private or forked automation remote is not
supported by this release-verification path; supporting one requires a separately designed
explicit minimal credential channel rather than ambient host Git configuration. A linked
worktree sharing an ordinary complete SHA-1 object directory is supported; alternates and
promisor/shallow layouts fail closed rather than fetching missing objects.

CI pins and verifies actionlint itself, then runs its YAML schema and expression gate with
`-shellcheck= -pyflakes=`. Empty analyzer paths make this gate deterministic and independent
of optional host ShellCheck/Pyflakes installations; actionlint's own diagnostics remain
fail-closed. The rollout validator uses the same actionlint boundary for managed callers.

After the immutable tag is published, do not run from the pre-merge checkout, which has no
local `v1.40`. Materialize one full public clone from the literal canonical HTTPS URL in a
configuration-free, credential-free environment. The fixed clone and fleet paths must be
absent, including dangling symlinks; clear only a previously reviewed disposable path in a
separate operator step. The clone intentionally has no depth, filter, or single-branch flag:

```bash
set -euo pipefail
export AUTOMATION_RELEASE_ROOT=/tmp/automation-v1.40-public
export FLEET_WORKSPACE=/tmp/automation-v1.40-fleet
export ACTIONLINT=/tmp/actionlint-v1.7.12/actionlint
[[ ! -e "$AUTOMATION_RELEASE_ROOT" && ! -L "$AUTOMATION_RELEASE_ROOT" ]]
[[ ! -e "$FLEET_WORKSPACE" && ! -L "$FLEET_WORKSPACE" ]]

public_git() {
  env -i PATH=/usr/bin:/bin \
    HOME=/nonexistent/automation-workflow-release/home \
    XDG_CONFIG_HOME=/nonexistent/automation-workflow-release/xdg \
    LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_SYSTEM=/dev/null \
    GIT_CONFIG_GLOBAL=/dev/null GIT_TERMINAL_PROMPT=0 \
    GIT_ASKPASS=/bin/false SSH_ASKPASS=/bin/false GCM_INTERACTIVE=Never \
    GIT_ALLOW_PROTOCOL=https GIT_PROTOCOL_FROM_USER=0 \
    /usr/bin/git -C / "$@"
}
public_git clone --no-recurse-submodules \
  https://github.com/jhw7500/automation.git "$AUTOMATION_RELEASE_ROOT"

release_git() {
  env -i PATH=/usr/bin:/bin \
    HOME=/nonexistent/automation-workflow-release/home \
    XDG_CONFIG_HOME=/nonexistent/automation-workflow-release/xdg \
    LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_SYSTEM=/dev/null \
    GIT_CONFIG_GLOBAL=/dev/null GIT_TERMINAL_PROMPT=0 \
    GIT_ASKPASS=/bin/false SSH_ASKPASS=/bin/false GCM_INTERACTIVE=Never \
    GIT_NO_REPLACE_OBJECTS=1 GIT_OPTIONAL_LOCKS=0 \
    /usr/bin/git -C "$AUTOMATION_RELEASE_ROOT" "$@"
}
[[ "$(release_git rev-parse --is-shallow-repository)" == false ]]
[[ "$(release_git remote get-url --all origin)" == \
  https://github.com/jhw7500/automation.git ]]
[[ "$(release_git remote get-url --push --all origin)" == \
  https://github.com/jhw7500/automation.git ]]

REMOTE_MAIN="$(public_git ls-remote --heads \
  https://github.com/jhw7500/automation.git refs/heads/main)"
[[ -n "$REMOTE_MAIN" && "$REMOTE_MAIN" != *$'\n'* ]]
IFS=$'\t' read -r EXPECTED_MAIN MAIN_REF MAIN_EXTRA <<< "$REMOTE_MAIN"
[[ "$EXPECTED_MAIN" =~ ^[0-9a-f]{40}$ \
    && "$MAIN_REF" == refs/heads/main && -z "${MAIN_EXTRA:-}" ]]
REMOTE_TAGS="$(public_git ls-remote --tags \
  https://github.com/jhw7500/automation.git \
  refs/tags/v1.40 'refs/tags/v1.40^{}')"
EXPECTED_TAG=
EXPECTED_PEELED=
DIRECT_COUNT=0
PEELED_COUNT=0
while IFS=$'\t' read -r SHA REF EXTRA; do
  [[ "$SHA" =~ ^[0-9a-f]{40}$ && -z "${EXTRA:-}" ]]
  case "$REF" in
    refs/tags/v1.40)
      EXPECTED_TAG="$SHA"
      DIRECT_COUNT=$((DIRECT_COUNT + 1))
      ;;
    refs/tags/v1.40^\{\})
      EXPECTED_PEELED="$SHA"
      PEELED_COUNT=$((PEELED_COUNT + 1))
      ;;
    *) exit 1 ;;
  esac
done <<< "$REMOTE_TAGS"
[[ "$DIRECT_COUNT" -eq 1 && "$PEELED_COUNT" -eq 1 \
    && "$EXPECTED_PEELED" == "$EXPECTED_MAIN" ]]
[[ "$(release_git rev-parse --verify refs/heads/main)" == "$EXPECTED_MAIN" ]]
[[ "$(release_git rev-parse --verify refs/remotes/origin/main)" == "$EXPECTED_MAIN" ]]
[[ "$(release_git rev-parse --verify refs/tags/v1.40)" == "$EXPECTED_TAG" ]]
[[ "$(release_git rev-parse --verify 'refs/tags/v1.40^{}')" == "$EXPECTED_PEELED" ]]
(cd "$AUTOMATION_RELEASE_ROOT" && python3 -m scripts.verify_workflow_release \
  --automation "$AUTOMATION_RELEASE_ROOT" --ref v1.40 \
  --expected-commit "$EXPECTED_MAIN")
```

The normative Task 9 block contains the same exact sequence. Every command below executes
the released script from this non-shallow clone, passes the same directory as
`--automation`, and uses only the marked `FLEET_WORKSPACE`.

Do not place unrelated files or working repositories in `FLEET_WORKSPACE`.

### 1. Read-only plan

Run the complete fleet plan before creating any branch:

```bash
python3 "$AUTOMATION_RELEASE_ROOT/scripts/rollout_workflow_fleet.py" \
  --automation "$AUTOMATION_RELEASE_ROOT" \
  --workspace "$FLEET_WORKSPACE" \
  --initialize-workspace \
  --mode plan \
  --ref v1.40 \
  --actionlint "$ACTIONLINT"
```

Use repeated `--repo NAME` arguments to narrow a later read-only plan. Plan clones and
reads GitHub state, renders locally, validates YAML/catalog contracts, and writes
`$FLEET_WORKSPACE/rollout-manifest.json`; it performs no remote mutation.

The public plan statuses are exactly:

- `current`: managed content already matches the release and no stale rollout branch exists;
- `planned`: validated managed changes can create a branch/PR, or an exact branch can
  receive its missing PR;
- `reusable`: one exact open PR and its branch can be reused; or
- `blocked`: repository, config, inventory, contract, prerequisites, or remote state is
  unsafe.

The pure renderer's renderer-only content classifications are `current`, `drift`,
`bootstrap_required`, and `blocked`. They are not the public plan statuses: after remote
inspection, safe `drift` and an explicitly requested safe `bootstrap_required` become
`planned`. A missing config without explicit bootstrap is `blocked`; it is never presented
as an implicit bootstrap opportunity. Audit reports `current`, `drift`, or `blocked` for
default-branch content.

The manifest includes the observed base SHA, release commit, required secret and variable
**names**, and managed diff paths. It is a convenience report, not an approval token;
publish always refetches and recomputes.

### 2. Publish independent PRs

Publish requires explicit repositories and confirmation:

```bash
python3 "$AUTOMATION_RELEASE_ROOT/scripts/rollout_workflow_fleet.py" \
  --automation "$AUTOMATION_RELEASE_ROOT" \
  --workspace "$FLEET_WORKSPACE" \
  --mode publish \
  --ref v1.40 \
  --repo wlan-package \
  --confirm \
  --actionlint "$ACTIONLINT"
```

For `v1.40`, every repository uses the deterministic branch
`automation/common-workflows-v1.40`. Publish computes exact blob, tree, and commit SHA-1
identities locally with fixed author, committer, timestamp, and message fields. JSON sent
through stdin creates those detached objects only at the literal GitHub Git Data API
endpoints `repos/jhw7500/<catalog-repo>/git/blobs`, `trees`, and `commits`; the final
`POST .../git/refs` atomically creates the exact non-default ref. Every response must match
the locally computed identity, and the branch is re-read afterward. GitHub children receive
only fixed runtime/config variables plus at most one intended GitHub token (`GH_TOKEN`
preferred, otherwise `GITHUB_TOKEN`); provider credentials and unrelated operator variables
do not cross the boundary.

A concurrent ref creation makes `POST .../git/refs` fail without advancing or replacing
the branch. A lost response is reconciled read-only only if the branch already equals the
exact expected commit; otherwise publication blocks. Detached objects left unreachable by
a failed ref creation are harmless, and no cleanup ref is created. There is no ordinary Git
branch push, force option, merge, auto-merge, update-branch, default-branch write,
secret-write, variable-write, or revert operation.

All selected repositories pass read-only prevalidation before the first remote effect.
Publication then refetches and recomputes each repository immediately before its branch
is created or reused. The reuse rules are fail-closed:

- an absent rollout branch may be created from the freshly fetched default branch;
- a matching branch may receive its missing PR only when its base, release commit, and
  managed path/mode/blob diff exactly match the fresh render;
- one exact open PR is reusable only when its base branch, head repository, head branch,
  head object ID, title, body, and remote branch object ID all match;
- a mismatched branch or PR, multiple PRs, or any closed or merged PR history for the
  deterministic head blocks that repository, whether or not the branch remains; and
- no mismatch is repaired or overwritten.

Publish reports `published`, `reused`, `current`, or `blocked`. A network or permission
failure after earlier repositories were published is **partial success**: valid PRs remain
open, every observed result is recorded, and the process returns non-zero when any
repository is blocked. It does not roll back successful repositories. Correct the cause
and rerun the same command; exact branches and PRs are safely reused.

Bootstrap is deliberately separate and accepts exactly one matching, bootstrap-allowed
repository. Its new config disables every common workflow:

```bash
python3 "$AUTOMATION_RELEASE_ROOT/scripts/rollout_workflow_fleet.py" \
  --automation "$AUTOMATION_RELEASE_ROOT" \
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
python3 "$AUTOMATION_RELEASE_ROOT/scripts/rollout_workflow_fleet.py" \
  --automation "$AUTOMATION_RELEASE_ROOT" \
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
python3 "$AUTOMATION_RELEASE_ROOT/scripts/audit_workflow_fleet.py" \
  --automation "$AUTOMATION_RELEASE_ROOT" \
  --workspace "$FLEET_WORKSPACE" \
  --ref v1.40
```

Repeated `--repo NAME` arguments narrow the audit. Audit reports `current`, `drift`, or
`blocked` from managed bytes, tracked Git entry metadata, and contracts; it ignores
project-owned workflow differences. A mode-only drift is `drift`, appears in
`changed_paths`, and is repaired to an exact `100644 blob`. Commit construction and final
attestation cover the complete managed set: every required or selected optional/config
file that is present has canonical bytes and mode/type, unselected optional and retired
paths are absent, and no project-owned path is changed. During rollout, an unmerged
repository legitimately remains `drift`. The completion condition is `current=19`,
`drift=0`, and `blocked=0`.

### Review, merge, and recovery

Repository owners inspect each diff, run project-specific CI, obtain their normal reviews,
and use a **GitHub-native merge**. Merge commit, squash, or rebase policy remains local to
the repository because audit validates final content.

Before merge, closing the PR (and optionally deleting its branch) safely aborts that
repository/release attempt without changing the default branch. Closed PR history blocks
reuse of the deterministic identity, so the same repository/release attempt cannot be
recreated. Correct the source and use a new immutable release/ref and its new deterministic
branch for another attempt.

After merge, use a **GitHub-native revert** PR (or a normal reviewed PR containing
`git revert`). Never move an immutable automation tag. Repair a bad central release with a
new immutable release and new consumer PRs.

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
