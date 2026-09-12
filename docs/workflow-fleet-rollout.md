# Workflow Fleet Rollout

The fleet tooling standardizes only catalogued common AI caller workflows. It renders a
managed diff, validates it, atomically creates a deterministic repository branch, and opens
a pull request. It never merges, reverts, forces, updates a default branch, or writes an
Actions secret or variable.

The release input is an immutable annotated automation tag. For `v1.40.1`, the local and
remote tag objects must agree and resolve to one verified commit before any consumer is
processed. Rendered callers pin that 40-character commit rather than the tag text.

`v1.40.1` is the immutable tooling patch for the initial `v1.40` release. The `v1.40`
tag remains unchanged, but its fleet publisher omitted the terminal newline required to
make GitHub's commit object match the locally computed SHA. Do not publish consumer refs
with the `v1.40` tool; use `v1.40.1` for plan, publish, and audit.

## Workflow PR rollout

### Prerequisites and workspace

Use a dedicated disposable directory. The first plan initializes its marker; subsequent
plan, publish, and audit commands reuse it. The scripts accept only repositories declared
by `scripts/workflow-config.json`. Consumer-repository Git and `gh` operations use the
operator's normal GitHub authentication, but provider credentials are removed from child
environments. Supply the locally installed, reviewed `actionlint` executable explicitly.

The historical `v1.40.1` release bundle uses schema v1 and is default-branch-only across
its 19 configured repositories. Its commands below must not be used to claim schema-v2
or multi-branch coverage.

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

### Publish the `v1.40.1` patch tag

After the patch PR is human-merged, publish `v1.40.1` from the new public `main`. The
historical `v1.40` direct and peeled identities are fixed below and must remain unchanged.
The patch ref must be absent before the first write. This procedure creates one annotated
tag object and then one create-only ref through the literal GitHub API; it never uses Git
push or an ambient remote for publication. Export `EXPECTED_PATCH_MERGE_SHA` from the
human-reviewed patch PR merge result; the procedure rejects the old `v1.40` commit and any
public `main` that does not equal that external review anchor. Authentication requires
exactly one existing `GH_TOKEN` or `GITHUB_TOKEN`. An isolated standard-library launcher
selects only that token and the reviewed merge anchor from the operator environment, passes
the token through a private file descriptor rather than an argument, and replaces itself
with a clean Bash process. The clean process uses a second isolated broker to replace itself
with the absolute GitHub CLI under an exact environment containing only that selected token
and fixed runtime variables. Parent shell tracing and exported functions therefore cannot
observe or intercept the release body.

```bash
/usr/bin/python3 -I -S -B -c '
import os
import re

token_keys = [key for key in ("GH_TOKEN", "GITHUB_TOKEN") if os.environ.get(key)]
if len(token_keys) != 1:
    os.write(2, b"ERROR: exactly one GitHub token variable is required\n")
    raise SystemExit(1)
token_key = token_keys[0]
try:
    token = os.environ[token_key].encode("ascii")
except UnicodeEncodeError:
    raise SystemExit(1) from None
if not 0 < len(token) <= 4096 or b"\n" in token or b"\r" in token:
    raise SystemExit(1)
expected = os.environ.get("EXPECTED_PATCH_MERGE_SHA", "")
if (
    re.fullmatch(r"[0-9a-f]{40}", expected) is None
    or expected == "3127d6a8e238bb426603d4b0feb5c7dd88299326"
):
    os.write(2, b"ERROR: reviewed patch merge identity is invalid\n")
    raise SystemExit(1)
read_fd, write_fd = os.pipe()
try:
    os.write(write_fd, token + b"\n")
finally:
    os.close(write_fd)
if read_fd == 3:
    os.set_inheritable(3, True)
else:
    os.dup2(read_fd, 3, inheritable=True)
    os.close(read_fd)
os.closerange(4, os.sysconf("SC_OPEN_MAX"))
environment = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent/automation-workflow-release/home",
    "XDG_CONFIG_HOME": "/nonexistent/automation-workflow-release/xdg",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "TOKEN_KEY": token_key,
    "EXPECTED_PATCH_MERGE_SHA": expected,
}
os.execve(
    "/bin/bash",
    ["/bin/bash", "--noprofile", "--norc", "-s"],
    environment,
)
' <<'PATCH_RELEASE_BASH'
set -euo pipefail
IFS= read -r RELEASE_GITHUB_TOKEN <&3
if IFS= read -r _ <&3; then
  printf '%s\n' 'ERROR: GitHub token must be a single line' >&2
  exit 1
fi
exec 3<&-
[[ -n "$RELEASE_GITHUB_TOKEN" && "$RELEASE_GITHUB_TOKEN" != *$'\r'* ]]
AUTOMATION_URL=https://github.com/jhw7500/automation.git
PATCH_CHECKOUT=/tmp/automation-v1.40.1-pretag
[[ "$EXPECTED_PATCH_MERGE_SHA" =~ ^[0-9a-f]{40}$ \
    && "$EXPECTED_PATCH_MERGE_SHA" != \
      3127d6a8e238bb426603d4b0feb5c7dd88299326 ]]
[[ ! -e "$PATCH_CHECKOUT" && ! -L "$PATCH_CHECKOUT" ]]

public_git() {
  /usr/bin/env -i PATH=/usr/bin:/bin \
    HOME=/nonexistent/automation-workflow-release/home \
    XDG_CONFIG_HOME=/nonexistent/automation-workflow-release/xdg \
    LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_SYSTEM=/dev/null \
    GIT_CONFIG_GLOBAL=/dev/null GIT_TERMINAL_PROMPT=0 \
    GIT_ASKPASS=/bin/false SSH_ASKPASS=/bin/false GCM_INTERACTIVE=Never \
    GIT_ALLOW_PROTOCOL=https GIT_PROTOCOL_FROM_USER=0 \
    GIT_CEILING_DIRECTORIES=/ \
    /usr/bin/git -C / "$@"
}

github_api() (
  /usr/bin/env -i /usr/bin/python3 -I -S -B -c '
import os
import sys

if len(sys.argv) < 2 or sys.argv[1] not in {"GH_TOKEN", "GITHUB_TOKEN"}:
    raise SystemExit(2)
with os.fdopen(3, "rb") as source:
    secret = source.read(4098)
os.closerange(3, os.sysconf("SC_OPEN_MAX"))
if (
    not 1 < len(secret) <= 4097
    or not secret.endswith(b"\n")
    or b"\n" in secret[:-1]
    or b"\r" in secret
):
    raise SystemExit(2)
try:
    token = secret[:-1].decode("ascii")
except UnicodeDecodeError:
    raise SystemExit(2)
args = ["/usr/bin/gh", "api", "--hostname", "github.com", *sys.argv[2:]]
environment = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent/automation-workflow-release/home",
    "XDG_CONFIG_HOME": "/nonexistent/automation-workflow-release/xdg",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    sys.argv[1]: token,
}
os.execve(args[0], args, environment)
' "$TOKEN_KEY" "$@" 3<<<"$RELEASE_GITHUB_TOKEN"
)

verify_annotated_tag() {
  local tag="$1" expected_direct="$2" expected_peeled="$3" lines="$4"
  local sha ref extra direct_count=0 peeled_count=0
  while IFS=$'\t' read -r sha ref extra; do
    [[ "$sha" =~ ^[0-9a-f]{40}$ && -z "${extra:-}" ]]
    if [[ "$ref" == "refs/tags/$tag" ]]; then
      [[ "$sha" == "$expected_direct" ]]
      direct_count=$((direct_count + 1))
    elif [[ "$ref" == "refs/tags/$tag^{}" ]]; then
      [[ "$sha" == "$expected_peeled" ]]
      peeled_count=$((peeled_count + 1))
    else
      return 1
    fi
  done <<< "$lines"
  [[ "$direct_count" -eq 1 && "$peeled_count" -eq 1 ]]
}

OLD_TAGS="$(public_git ls-remote --tags "$AUTOMATION_URL" \
  refs/tags/v1.40 'refs/tags/v1.40^{}')"
verify_annotated_tag v1.40 \
  9df0887ddfd43bb2dd96541a1b5d7147688e0471 \
  3127d6a8e238bb426603d4b0feb5c7dd88299326 "$OLD_TAGS"

PATCH_TAGS="$(public_git ls-remote --tags "$AUTOMATION_URL" \
  refs/tags/v1.40.1 'refs/tags/v1.40.1^{}')"
[[ -z "$PATCH_TAGS" ]]

REMOTE_MAIN="$(public_git ls-remote --heads "$AUTOMATION_URL" refs/heads/main)"
[[ -n "$REMOTE_MAIN" && "$REMOTE_MAIN" != *$'\n'* ]]
IFS=$'\t' read -r MERGE_SHA MAIN_REF MAIN_EXTRA <<< "$REMOTE_MAIN"
[[ "$MERGE_SHA" =~ ^[0-9a-f]{40}$ \
    && "$MAIN_REF" == refs/heads/main \
    && -z "${MAIN_EXTRA:-}" \
    && "$MERGE_SHA" == "$EXPECTED_PATCH_MERGE_SHA" ]]

public_git clone --no-recurse-submodules "$AUTOMATION_URL" "$PATCH_CHECKOUT"
patch_git() {
  /usr/bin/env -i PATH=/usr/bin:/bin \
    HOME=/nonexistent/automation-workflow-release/home \
    XDG_CONFIG_HOME=/nonexistent/automation-workflow-release/xdg \
    LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_SYSTEM=/dev/null \
    GIT_CONFIG_GLOBAL=/dev/null GIT_TERMINAL_PROMPT=0 \
    GIT_ASKPASS=/bin/false SSH_ASKPASS=/bin/false GCM_INTERACTIVE=Never \
    GIT_NO_REPLACE_OBJECTS=1 GIT_OPTIONAL_LOCKS=0 \
    /usr/bin/git -C "$PATCH_CHECKOUT" "$@"
}
[[ "$(patch_git rev-parse --is-shallow-repository)" == false ]]
[[ "$(patch_git rev-parse --verify refs/heads/main)" == \
  "$EXPECTED_PATCH_MERGE_SHA" ]]
[[ "$(patch_git rev-parse --verify refs/remotes/origin/main)" == \
  "$EXPECTED_PATCH_MERGE_SHA" ]]
(
  cd "$PATCH_CHECKOUT"
  /usr/bin/env -i PATH=/usr/bin:/bin \
    HOME=/nonexistent/automation-workflow-release/home \
    XDG_CONFIG_HOME=/nonexistent/automation-workflow-release/xdg \
    LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    /usr/bin/python3 -m scripts.verify_workflow_release \
    --automation "$PATCH_CHECKOUT" --ref v1.40.1 \
    --expected-commit "$EXPECTED_PATCH_MERGE_SHA" --commit-only
)

PREWRITE_MAIN="$(public_git ls-remote --heads \
  "$AUTOMATION_URL" refs/heads/main)"
[[ "$PREWRITE_MAIN" == "$REMOTE_MAIN" ]]
PREWRITE_PATCH_TAGS="$(public_git ls-remote --tags "$AUTOMATION_URL" \
  refs/tags/v1.40.1 'refs/tags/v1.40.1^{}')"
[[ -z "$PREWRITE_PATCH_TAGS" ]]

TAG_RESULT="$(
  github_api --method POST \
    repos/jhw7500/automation/git/tags \
    -f tag=v1.40.1 \
    -f message='automation workflow release v1.40.1' \
    -f object="$EXPECTED_PATCH_MERGE_SHA" \
    -f type=commit \
    --jq '[.sha, .tag, .object.sha, .object.type] | @tsv'
)"
IFS=$'\t' read -r TAG_SHA TAG_NAME TAG_COMMIT TAG_TYPE TAG_EXTRA <<< "$TAG_RESULT"
[[ "$TAG_SHA" =~ ^[0-9a-f]{40}$ \
    && "$TAG_NAME" == v1.40.1 \
    && "$TAG_COMMIT" == "$EXPECTED_PATCH_MERGE_SHA" \
    && "$TAG_TYPE" == commit \
    && -z "${TAG_EXTRA:-}" ]]

REF_RESULT="$(
  github_api --method POST \
    repos/jhw7500/automation/git/refs \
    -f ref=refs/tags/v1.40.1 \
    -f sha="$TAG_SHA" \
    --jq '[.ref, .object.sha] | @tsv'
)"
IFS=$'\t' read -r CREATED_REF CREATED_SHA REF_EXTRA <<< "$REF_RESULT"
[[ "$CREATED_REF" == refs/tags/v1.40.1 \
    && "$CREATED_SHA" == "$TAG_SHA" \
    && -z "${REF_EXTRA:-}" ]]

POST_PATCH_TAGS="$(public_git ls-remote --tags "$AUTOMATION_URL" \
  refs/tags/v1.40.1 'refs/tags/v1.40.1^{}')"
verify_annotated_tag v1.40.1 \
  "$TAG_SHA" "$EXPECTED_PATCH_MERGE_SHA" "$POST_PATCH_TAGS"
POST_OLD_TAGS="$(public_git ls-remote --tags "$AUTOMATION_URL" \
  refs/tags/v1.40 'refs/tags/v1.40^{}')"
verify_annotated_tag v1.40 \
  9df0887ddfd43bb2dd96541a1b5d7147688e0471 \
  3127d6a8e238bb426603d4b0feb5c7dd88299326 "$POST_OLD_TAGS"
PATCH_RELEASE_BASH
```

A concurrent patch-ref creation makes the ref POST fail without moving it. The tag object
created immediately before that failure is content-addressed and harmless. Never move or
delete either release ref.

After the immutable tag is published, do not run from the pre-merge checkout, which has no
local `v1.40.1`. Materialize one full public clone from the literal canonical HTTPS URL in a
configuration-free, credential-free environment. The fixed clone and fleet paths must be
absent, including dangling symlinks; clear only a previously reviewed disposable path in a
separate operator step. The clone intentionally has no depth, filter, or single-branch flag:

```bash
set -euo pipefail
export AUTOMATION_RELEASE_ROOT=/tmp/automation-v1.40.1-public
export FLEET_WORKSPACE=/tmp/automation-v1.40.1-fleet
export ACTIONLINT=/tmp/actionlint-v1.7.12/actionlint
[[ ! -e "$AUTOMATION_RELEASE_ROOT" && ! -L "$AUTOMATION_RELEASE_ROOT" ]]
[[ ! -e "$FLEET_WORKSPACE" && ! -L "$FLEET_WORKSPACE" ]]

public_git() {
  /usr/bin/env -i PATH=/usr/bin:/bin \
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
  /usr/bin/env -i PATH=/usr/bin:/bin \
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
  refs/tags/v1.40.1 'refs/tags/v1.40.1^{}')"
EXPECTED_TAG=
EXPECTED_PEELED=
DIRECT_COUNT=0
PEELED_COUNT=0
while IFS=$'\t' read -r SHA REF EXTRA; do
  [[ "$SHA" =~ ^[0-9a-f]{40}$ && -z "${EXTRA:-}" ]]
  case "$REF" in
    refs/tags/v1.40.1)
      EXPECTED_TAG="$SHA"
      DIRECT_COUNT=$((DIRECT_COUNT + 1))
      ;;
    refs/tags/v1.40.1^\{\})
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
[[ "$(release_git rev-parse --verify refs/tags/v1.40.1)" == "$EXPECTED_TAG" ]]
[[ "$(release_git rev-parse --verify 'refs/tags/v1.40.1^{}')" == "$EXPECTED_PEELED" ]]
(cd "$AUTOMATION_RELEASE_ROOT" && python3 -m scripts.verify_workflow_release \
  --automation "$AUTOMATION_RELEASE_ROOT" --ref v1.40.1 \
  --expected-commit "$EXPECTED_MAIN")
```

The original Task 9 block records the completed `v1.40` procedure. For this patch release,
use the `v1.40.1` clone and verification block above instead. Every command below executes
the released script from this non-shallow clone, passes the same directory as `--automation`,
and uses only the marked `FLEET_WORKSPACE`.

Do not place unrelated files or working repositories in `FLEET_WORKSPACE`.

### 1. Read-only plan

Run the complete fleet plan before creating any branch:

```bash
python3 "$AUTOMATION_RELEASE_ROOT/scripts/rollout_workflow_fleet.py" \
  --automation "$AUTOMATION_RELEASE_ROOT" \
  --workspace "$FLEET_WORKSPACE" \
  --initialize-workspace \
  --mode plan \
  --ref v1.40.1 \
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
each configured target.

The manifest and operator output identify the default target and include
the observed base SHA, release commit, required secret, variable, and (from `v1.64`) review-label
**names**, and managed diff paths. The manifest is a convenience report, not an approval token; publish always
refetches and recomputes.

### 2. Publish independent PRs

Publish requires explicit repositories and confirmation:

```bash
python3 "$AUTOMATION_RELEASE_ROOT/scripts/rollout_workflow_fleet.py" \
  --automation "$AUTOMATION_RELEASE_ROOT" \
  --workspace "$FLEET_WORKSPACE" \
  --mode publish \
  --ref v1.40.1 \
  --repo wlan-package \
  --confirm \
  --actionlint "$ACTIONLINT"
```

For `v1.40.1`, the default target keeps the deterministic branch
`automation/common-workflows-v1.40.1`. Publish computes exact blob, tree, and commit SHA-1
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
exact expected commit; otherwise publication blocks. Detached blobs, trees, or commits left
unreachable by any validation failure before ref creation, including the initial `v1.40`
commit-response mismatch, are harmless. A `v1.40.1` retry reuses matching
content-addressed blobs and trees but creates its own release-bound commit identity; no
cleanup ref is created. There is no ordinary Git
branch push, force option, merge, auto-merge, update-branch, default-branch write,
secret-write, variable-write, or revert operation.

All selected repositories pass read-only prevalidation before the first remote effect.
Publication then refetches and recomputes each repository immediately before its branch
is created or reused. The reuse rules are fail-closed:

- an absent rollout branch may be created from the freshly fetched selected base branch;
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
repository's default target. Its new config disables
every common workflow:

```bash
python3 "$AUTOMATION_RELEASE_ROOT/scripts/rollout_workflow_fleet.py" \
  --automation "$AUTOMATION_RELEASE_ROOT" \
  --workspace "$FLEET_WORKSPACE" \
  --mode publish \
  --ref v1.40.1 \
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
  --ref v1.40.1 \
  --repo wpa-supplicant \
  --bootstrap-repo wpa-supplicant \
  --confirm \
  --actionlint "$ACTIONLINT"
```

### 4. Read-only audit

Audit current content on every configured repository default after reviewed merges:

```bash
python3 "$AUTOMATION_RELEASE_ROOT/scripts/audit_workflow_fleet.py" \
  --automation "$AUTOMATION_RELEASE_ROOT" \
  --workspace "$FLEET_WORKSPACE" \
  --ref v1.40.1
```

Repeated `--repo NAME` arguments narrow the audit. Output identifies the repository default.
Audit reports `current`, `drift`, or
`blocked` from managed bytes, tracked Git entry metadata, and contracts; it ignores
project-owned workflow differences. A mode-only drift is `drift`, appears in
`changed_paths`, and is repaired to an exact `100644 blob`. Commit construction and final
attestation cover the complete managed set: every required or selected optional/config
file that is present has canonical bytes and mode/type, unselected optional and retired
paths are absent, and no project-owned path is changed. During rollout, an unmerged
repository legitimately remains `drift`. The historical fleet completion condition is
`current=19`, `drift=0`, and `blocked=0`.

## Post-merge multi-branch contract

Use this section only with the first immutable automation release containing #76 after it
is merged; no release tag is assigned here. That release carries schema v2. It treats the
repository default as an implicit target and expands each profile's ordered
`additional_branches`. The current 16-repository configuration therefore has 17 targets:
`wlan-driver-v2` expands to its discovered default plus `ported`; every other profile has
only its default target.

If the implicit default target cannot be cloned far enough to discover its remote name,
its blocked result is labeled `default:unresolved`; the colon makes it invalid as a Git branch.

For that post-merge release, `--repo wlan-driver-v2` plans, publishes, and audits both
targets without another flag. The default rollout head remains
`automation/common-workflows-<ref>`; an additional target uses the full lower-case
SHA-256 digest suffix of its resolved base branch. PR title/body, commit identity, reuse,
attestation, manifest, and output bind the exact base branch.

All expanded targets must complete read-only prevalidation before the first remote
publication. Metadata must resolve to one repository default, unique base branches, and
unique rollout heads; an additional target cannot equal that default. Any inconsistency
blocks that repository's targets and prevents publication for the selected batch. Audit
keeps every configured target visible and blocked on the same inconsistency, rather than
reporting duplicate targets as current. Bootstrap remains default-only. Completion for the
current schema-v2 fleet is `current=17`, `drift=0`, and `blocked=0`.

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

## Review labels

From `v1.64` the plan, publish, and audit tools also read each repository's label names and
block a repository that lacks `review:request`, `review:skip`, or `review-budget-override`
(`missing labels: ...`), because the shipped review policy is opt-in through those labels. The
tools never create labels. Create or repair them with the explicit operator step, which is
read-only without `--confirm`:

```bash
python3 "$AUTOMATION_RELEASE_ROOT/scripts/ensure_review_labels.py" \
  --automation "$AUTOMATION_RELEASE_ROOT"                        # report per repository
python3 "$AUTOMATION_RELEASE_ROOT/scripts/ensure_review_labels.py" \
  --automation "$AUTOMATION_RELEASE_ROOT" --confirm              # create the missing labels
python3 "$AUTOMATION_RELEASE_ROOT/scripts/ensure_review_labels.py" \
  --automation "$AUTOMATION_RELEASE_ROOT" --confirm --normalize  # also repair color/description
```

Use repeated `--repo NAME` arguments to narrow it. Labels are per repository, so the operator
step checks a multi-branch repository such as `wlan-driver-v2` once, while plan and audit read
the names once per branch target. Color or description drift is reported but never blocks a
rollout; only a missing name does.

## Token synchronization

Workflow PR rollout and credential lifecycle are separate operations. The workflow tools
may inspect required secret, variable, and review-label **names** as preconditions, but they
never read a provider value, consume a local provider file, or call a secret/variable/label
mutation API. Missing names block the affected repository until an independently reviewed
credential process (or the label step above) resolves them.

Claude token rotation remains owned by `personal-ops/claude-token-sync`, including its
inventory, locking, health checks, and deployment lifecycle. Other provider-key changes
must use their separately reviewed owner process. There is intentionally no command that
combines workflow PR publication with token synchronization.

## Create-only v1.78.1 and v1.78.2 tag publication

This is an operator procedure for a separately approved publication, not an action
performed by implementing the fallback. The Git Data sequence used for the
immutable v1.76 boundary remains create-only. Never publish with ordinary Git push,
move a tag or delete a tag. The older v1.40.1 example above is historical.

For each release separately, obtain the exact reviewed merge commit from the
approved public-main change. Set `RELEASE_TAG` to `v1.78.1` or `v1.78.2`,
`EXPECTED_RELEASE_COMMIT` to that 40-character commit and `RELEASE_DIRECTORY` to
an absolute, absent disposable directory under a trusted parent. Exactly one of
`GH_TOKEN` and `GITHUB_TOKEN` must contain the intended publication token. Never
put its value in an argument, trace or response report. The launcher passes it
through a private descriptor to an isolated process; only the GitHub API child
receives it in an exact environment. The public Git process receives no token.

The following complete procedure verifies absent direct and peeled refs twice,
verifies public main against the review anchor, validates the exact candidate,
then makes one annotated-tag-object POST and one tag-ref POST. Every raw response
is created as a current-user-owned non-symlink regular file and independently set
to 0600. It computes the intended tag OID before the first POST and verifies both
response OIDs, then verifies the local and public direct/peeled identities.

Both paths authenticate the existing annotated v1.78 tag object
`7efe562b49c7b5f9fdad6855ff8c2a73b090a122` and peeled commit
`a08141d644ab1036cadd8167d48158e827dcd978`. v1.78.1 is the reviewed security
patch that narrows the managed command router's pre-admission permissions and
pins its nested check-workflow-enabled action to an immutable commit. v1.78.2
extends that boundary to the automatic Claude review workflow. Before either
POST, its procedure authenticates the existing annotated v1.78.1 tag and peeled
commit, requires a distinct reviewed candidate, and loads the complete release
inventory from the authenticated v1.78.1 checkout. Exactly the central Claude
review workflow and its release verifier may differ; every other inventory-owned
path must remain unchanged, and the candidate verifier still authenticates all
release paths and modes. First publication of v1.78.1 has no prior-v1.78.1 check.
Both paths also authenticate immutable v1.77 tag object
`81f44fb6786bdfcc40f93161db74b1d9a9e3b7c5`, peeled commit
`dd13f9dcc64540494c1c04bc3f9c7a4f2ef0ba19`, and the unchanged v1.76 identity
before any publication write. v1.77 is #183's observational-token release;
#182's fallback begins in v1.78, its router is hardened in v1.78.1, and its
automatic review pre-admission path is hardened in v1.78.2.

```bash
rtk proxy /usr/bin/python3 -I -S -B -c '
import os
import re
keys = [key for key in ("GH_TOKEN", "GITHUB_TOKEN") if os.environ.get(key)]
if len(keys) != 1:
    raise SystemExit("exactly one intended token is required")
key = keys[0]
token = os.environ[key].encode("ascii")
if not 0 < len(token) <= 4096 or b"\n" in token or b"\r" in token:
    raise SystemExit("invalid token channel")
tag = os.environ.get("RELEASE_TAG", "")
commit = os.environ.get("EXPECTED_RELEASE_COMMIT", "")
directory = os.environ.get("RELEASE_DIRECTORY", "")
if tag not in {"v1.78.1", "v1.78.2"} or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
    raise SystemExit("invalid reviewed release identity")
if not directory.startswith("/"):
    raise SystemExit("absolute disposable directory required")
read_fd, write_fd = os.pipe()
os.write(write_fd, token + b"\n")
os.close(write_fd)
if read_fd != 3:
    os.dup2(read_fd, 3, inheritable=True)
    os.close(read_fd)
os.set_inheritable(3, True)
os.closerange(4, os.sysconf("SC_OPEN_MAX"))
environment = {
    "PATH": "/usr/bin:/bin", "HOME": "/nonexistent/automation-release/home",
    "XDG_CONFIG_HOME": "/nonexistent/automation-release/xdg",
    "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TOKEN_KEY": key,
    "RELEASE_TAG": tag, "EXPECTED_RELEASE_COMMIT": commit,
    "RELEASE_DIRECTORY": directory,
}
os.execve("/usr/bin/python3", ["/usr/bin/python3", "-I", "-S", "-B", "-"], environment)
' <<'CLAUDE_RELEASE_PY'
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess

def require(condition, reason):
    if not condition:
        raise SystemExit(reason)

with os.fdopen(3, "rb") as channel:
    secret = channel.read(4098)
require(1 < len(secret) <= 4097 and secret.endswith(b"\n")
        and b"\n" not in secret[:-1] and b"\r" not in secret, "invalid token channel")
token = secret[:-1].decode("ascii")
tag = os.environ["RELEASE_TAG"]
commit = os.environ["EXPECTED_RELEASE_COMMIT"]
root = Path(os.environ["RELEASE_DIRECTORY"])
require(not root.exists() and not root.is_symlink(), "release directory already exists")
root.mkdir(mode=0o700)
os.chmod(root, 0o700)
checkout = root / "automation"
runtime = {key: os.environ[key] for key in
           ("PATH", "HOME", "XDG_CONFIG_HOME", "LANG", "LC_ALL")}
git_env = {**runtime, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_SYSTEM": "/dev/null",
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0",
           "GIT_ASKPASS": "/bin/false", "SSH_ASKPASS": "/bin/false",
           "GCM_INTERACTIVE": "Never", "GIT_ALLOW_PROTOCOL": "https",
           "GIT_PROTOCOL_FROM_USER": "0", "GIT_NO_REPLACE_OBJECTS": "1",
           "GIT_CEILING_DIRECTORIES": "/"}
url = "https://github.com/jhw7500/automation.git"

def git(*args, cwd=Path("/")):
    return subprocess.run(["/usr/bin/git", *args], cwd=cwd, env=git_env,
                          check=True, capture_output=True, text=True).stdout.strip()

def public_tags(name):
    return git("ls-remote", "--tags", url, "refs/tags/" + name, "refs/tags/" + name + "^{}")

def tag_pairs(name, direct, peeled):
    return {direct + "\trefs/tags/" + name, peeled + "\trefs/tags/" + name + "^{}"}

v176_commit = "444a7347aee169ed178aae80e8bd8d10eca52e02"
v176_tag = "09af80682619129fcf343091b78413d2686a4579"
v177_commit = "dd13f9dcc64540494c1c04bc3f9c7a4f2ef0ba19"
v177_tag = "81f44fb6786bdfcc40f93161db74b1d9a9e3b7c5"
v178_commit = "a08141d644ab1036cadd8167d48158e827dcd978"
v178_tag = "7efe562b49c7b5f9fdad6855ff8c2a73b090a122"
require(set(public_tags("v1.77").splitlines()) == tag_pairs("v1.77", v177_tag, v177_commit),
        "v1.77 identity changed")
require(set(public_tags("v1.76").splitlines()) == tag_pairs("v1.76", v176_tag, v176_commit),
        "v1.76 identity changed")
require(set(public_tags("v1.78").splitlines()) == tag_pairs("v1.78", v178_tag, v178_commit),
        "v1.78 identity changed")
require(public_tags(tag) == "", "release direct or peeled ref already exists")
main = git("ls-remote", "--heads", url, "refs/heads/main")
require(main == commit + "\trefs/heads/main", "public main differs from reviewed commit")
git("clone", "--no-recurse-submodules", url, str(checkout))
require(git("rev-parse", "--is-shallow-repository", cwd=checkout) == "false", "incomplete clone")
require(git("rev-parse", "refs/remotes/origin/main", cwd=checkout) == commit, "main moved")
git("checkout", "--detach", commit, cwd=checkout)
require(git("status", "--porcelain", cwd=checkout) == "", "dirty release checkout")
for historical_tag, direct, peeled in (
    ("v1.76", v176_tag, v176_commit), ("v1.77", v177_tag, v177_commit),
    ("v1.78", v178_tag, v178_commit),
):
    require(git("rev-parse", "refs/tags/" + historical_tag, cwd=checkout) == direct
            and git("rev-parse", "refs/tags/" + historical_tag + "^{}", cwd=checkout) == peeled
            and git("cat-file", "-t", direct, cwd=checkout) == "tag",
            historical_tag + " annotation or peeled commit differs")
require(commit != v178_commit, "patch release requires a distinct reviewed commit")

def verify_release(*extra):
    subprocess.run(["/usr/bin/python3", "-B", "-m", "scripts.verify_workflow_release",
                    "--automation", str(checkout), "--ref", tag,
                    "--expected-commit", commit, *extra], cwd=checkout, env=runtime, check=True)

verify_release("--commit-only")
if tag == "v1.78.2":
    pairs = [line.split("\t") for line in public_tags("v1.78.1").splitlines()]
    require(len(pairs) == 2 and all(len(pair) == 2 for pair in pairs),
            "missing or ambiguous v1.78.1 direct/peeled identity")
    refs = {ref: oid for oid, ref in pairs}
    require(set(refs) == {"refs/tags/v1.78.1", "refs/tags/v1.78.1^{}"}
            and all(re.fullmatch("[0-9a-f]{40}", oid) for oid in refs.values()),
            "invalid v1.78.1 direct/peeled identity")
    v1781_tag = refs["refs/tags/v1.78.1"]
    v1781_commit = refs["refs/tags/v1.78.1^{}"]
    require(commit != v1781_commit, "boundary canary requires a distinct reviewed commit")
    git("fetch", "--no-tags", url, "refs/tags/v1.78.1:refs/tags/v1.78.1", cwd=checkout)
    require(git("rev-parse", "refs/tags/v1.78.1", cwd=checkout) == v1781_tag
            and git("rev-parse", "refs/tags/v1.78.1^{}", cwd=checkout) == v1781_commit
            and git("cat-file", "-t", v1781_tag, cwd=checkout) == "tag",
            "fetched v1.78.1 annotation or peeled commit differs")
    baseline = root / "v1781-baseline"
    git("worktree", "add", "--detach", str(baseline), v1781_commit, cwd=checkout)
    subprocess.run(["/usr/bin/python3", "-B", "-m", "scripts.verify_workflow_release",
                    "--automation", str(baseline), "--ref", "v1.78.1",
                    "--expected-commit", v1781_commit, "--remote", "origin"],
                   cwd=baseline, env=runtime, check=True)
    inventory = subprocess.run(["/usr/bin/python3", "-B", "-c",
        'import json; from scripts.workflow_release_inventory import release_paths_for; '
        'print(json.dumps(release_paths_for("v1.78.1")))'],
        cwd=baseline, env=runtime, check=True, capture_output=True, text=True)
    owned = json.loads(inventory.stdout)
    require(isinstance(owned, list) and owned and all(isinstance(path, str) for path in owned),
            "invalid authenticated v1.78.1 inventory")
    expected_owned_changes = {
        ".github/workflows/claude-code-review.yml",
        "scripts/verify_workflow_release.py",
    }
    diff_scope = sorted(set(owned) | expected_owned_changes)
    changed = git("diff", "--name-only", "--no-ext-diff", "--no-textconv",
                  v1781_commit, commit, "--", *diff_scope, cwd=checkout).splitlines()
    require(len(changed) == len(expected_owned_changes)
            and set(changed) == expected_owned_changes,
            "v1.78.2 release-owned patch scope differs")
    require(set(public_tags("v1.78.1").splitlines()) == tag_pairs("v1.78.1", v1781_tag, v1781_commit),
            "v1.78.1 identity moved before write")
require(set(public_tags("v1.76").splitlines()) == tag_pairs("v1.76", v176_tag, v176_commit),
        "v1.76 identity moved before write")
require(set(public_tags("v1.77").splitlines()) == tag_pairs("v1.77", v177_tag, v177_commit),
        "v1.77 identity moved before write")
require(set(public_tags("v1.78").splitlines()) == tag_pairs("v1.78", v178_tag, v178_commit),
        "v1.78 identity moved before write")
require(git("ls-remote", "--heads", url, "refs/heads/main") == main, "main moved before write")
require(public_tags(tag) == "", "release ref appeared before write")
now = datetime.now(timezone.utc).replace(microsecond=0)
tagger = {"name": "Automation workflow release", "email": "noreply@github.com",
          "date": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
message = "automation workflow release " + tag + "\n"
tag_bytes = ("object " + commit + "\ntype commit\ntag " + tag + "\ntagger "
             + tagger["name"] + " <" + tagger["email"] + "> "
             + str(int(now.timestamp())) + " +0000\n\n" + message).encode("utf-8")
tag_oid = hashlib.sha1(b"tag " + str(len(tag_bytes)).encode("ascii") + b"\0" + tag_bytes).hexdigest()

def post(endpoint, payload, filename):
    path = root / filename
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        os.fchmod(output.fileno(), 0o600)
        result = subprocess.run(["/usr/bin/gh", "api", "--hostname", "github.com",
            "--method", "POST", "-H", "Accept: application/vnd.github+json",
            "-H", "X-GitHub-Api-Version: 2022-11-28", endpoint, "--input", "-"],
            env={**runtime, os.environ["TOKEN_KEY"]: token}, input=json.dumps(payload).encode(),
            stdout=output, stderr=subprocess.PIPE)
        output.flush()
        os.fsync(output.fileno())
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o600, "unsafe raw response file")
    require(result.returncode == 0, "POST failed: stop and reconcile read-only; never retry blindly")
    return json.loads(path.read_bytes())

created = post("repos/jhw7500/automation/git/tags",
    {"tag": tag, "message": message, "object": commit, "type": "commit", "tagger": tagger},
    "tag-object-response.json")
require(created.get("sha") == tag_oid and created.get("tag") == tag
        and created.get("message") == message and created.get("tagger") == tagger
        and created.get("object", {}).get("sha") == commit
        and created.get("object", {}).get("type") == "commit", "tag object response differs")
created_ref = post("repos/jhw7500/automation/git/refs",
    {"ref": "refs/tags/" + tag, "sha": tag_oid}, "tag-ref-response.json")
require(created_ref.get("ref") == "refs/tags/" + tag
        and created_ref.get("object", {}).get("sha") == tag_oid
        and created_ref.get("object", {}).get("type") == "tag", "tag ref response differs")
require(set(public_tags(tag).splitlines()) == tag_pairs(tag, tag_oid, commit), "public tag differs")
require(set(public_tags("v1.77").splitlines()) == tag_pairs("v1.77", v177_tag, v177_commit),
        "v1.77 identity changed")
require(set(public_tags("v1.76").splitlines()) == tag_pairs("v1.76", v176_tag, v176_commit),
        "v1.76 identity changed")
require(set(public_tags("v1.78").splitlines()) == tag_pairs("v1.78", v178_tag, v178_commit),
        "v1.78 identity changed")
if tag == "v1.78.2":
    require(set(public_tags("v1.78.1").splitlines())
            == tag_pairs("v1.78.1", v1781_tag, v1781_commit),
            "v1.78.1 identity changed")
git("fetch", "--no-tags", url, "refs/tags/" + tag + ":refs/tags/" + tag, cwd=checkout)
require(git("rev-parse", "refs/tags/" + tag, cwd=checkout) == tag_oid, "local direct ref differs")
require(git("rev-parse", "refs/tags/" + tag + "^{}", cwd=checkout) == commit, "local peeled ref differs")
verify_release("--remote", "origin")
print(tag + " verified: tag=" + tag_oid + " commit=" + commit)
CLAUDE_RELEASE_PY
```

A failed or uncertain POST stops the procedure. Preserve both raw response files
that exist and reconcile with read-only API/public Git reads. An orphan annotated
object is harmless; a concurrent ref creation must never be repaired by moving
or deleting the tag. Repeat this procedure for v1.78.2 only after its separate
reviewed security change and publication authorization.

## v1.76 bootstrap evidence

The immutable bootstrap boundary is commit
`444a7347aee169ed178aae80e8bd8d10eca52e02`, annotated tag object
`09af80682619129fcf343091b78413d2686a4579`. Preserve original fleet manifests,
PR HEAD/base tuples, automatic run IDs/attempts, canonical failure states and
evidence digests. A default branch still pinned to v1.76 cannot authenticate the
new managed route. Its original automatic caller-validation failure is bootstrap
evidence; neither a new comment nor a receipt can retroactively enable v1.78 code.
Use a separately reviewed adoption/merge decision to establish the new default
caller. Never rewrite the original failure as a successful run.

## v1.78.1 hardened route adoption

After create-only publication and release verification, render consumer caller
changes with the existing fleet plan/publish/audit workflow, explicitly selecting
the immutable release ref and approved consumer/base branch. Review the generated
managed diff and adoption PR before a separately authorized merge. Verify the
default-branch `claude.yml` pins the peeled v1.78.1 commit and has the catalogued
write ceiling. Keep the ordinary automatic reviewer on the reviewed release pin.
An open v1.78 bootstrap PR must be updated to v1.78.1 and reviewed again at its
new exact HEAD; do not merge the superseded v1.78 candidate.

Record the new default caller SHA and its central commit as the future fallback
driver. A v1.78.1 adoption PR alone is not the real boundary canary: until merged,
the default branch still runs the older caller. Request/admission/ledger/comment
and receipt schemas are defined in [the consumer contract](workflows/contracts.md).

## v1.78.2 automatic-review hardening adoption

Publish v1.78.2 only from the separately reviewed security patch described above.
Update bootstrap consumers directly to its immutable peeled commit, preserving
the expanded v1.78.1 review budgets. A caller-changing PR can still produce the
expected workflow-validation mismatch while its default branch runs an older
caller; preserve that result as bootstrap evidence and require the repository's
other review gates before a separately authorized merge.

After a consumer default branch installs v1.78.2, use a later distinct reviewed
release for the real managed-boundary canary. That release must have its own
create-only publication procedure, exact driver and target commits, and the same
request, invocation, finalized-budget, canonical-result and mode-0600 receipt
proofs. Do not reuse the former docs-only v1.78.2 assumption.

The next concrete validation release is described in
[the v1.78.3 boundary canary procedure](workflows/v1.78.3-boundary-canary.md).
It preserves all v1.78.2 release-owned bytes and tests one wlan-package rollout with
the installed v1.78.2 driver, a distinct target commit, one managed provider
invocation and the immutable fallback receipt. Tag publication, the consumer
PR, its exact managed request and both merges retain their separate approvals.

## Required-check stop conditions

Stop on changed HEAD/base, caller or release identity, noncanonical managed diff,
missing/expired/ambiguous evidence, provider entry in the original failed attempt,
partial fallback inputs, an unfinalized budget, non-clean fallback quality, or
uncertain required-check discovery. Required checks must be completed with an
accepted conclusion (`success`, `neutral`, or `skipped`); any failure, cancellation
or pending check prevents the receipt. In particular, if the original failed
automatic job is a required check, fallback evidence cannot waive it. Stop and
resolve the repository's required-check policy through its own approved process.
Never remove a required check or fabricate a successful Check to make rollout pass.
