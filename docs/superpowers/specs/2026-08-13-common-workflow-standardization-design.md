# Common Workflow Standardization Design

Date: 2026-08-13  
Status: approved; implementation planned

## 1. Decision

The fleet will standardize only the common AI caller workflows. Automation will render
managed files, validate them, push a repository branch, and open a pull request. It will
not merge or revert a pull request. Repository CI, review, merge, and recovery remain
ordinary GitHub operations.

The supported lifecycle is deliberately small:

```text
automation release
  -> read-only fleet plan
  -> managed branch and PR creation
  -> repository CI and human review
  -> GitHub-native merge or revert
  -> read-only content audit
```

This replaces the earlier proposal for a custom merge controller, EffectJournal,
content-addressed rollout evidence, automatic rollback, and cross-repository transaction.
Those mechanisms are not required because repositories can adopt an immutable central
workflow revision independently.

## 2. Goals

1. Keep one canonical definition for every common AI caller.
2. Make allowed repository differences explicit rather than inferred from current files.
3. Preserve project-specific build, test, verification, release, packaging, deployment,
   lint, hardware, and synchronization workflows byte-for-byte.
4. Validate reusable workflow inputs, permissions, and secret mappings before creating a
   remote branch.
5. Create reviewable, independent PRs for all configured repositories.
6. Roll out through representative canaries before creating the remaining PRs.
7. Recover through normal GitHub PR closure or reviewed Git revert PRs.
8. Perform no secret or variable value write during workflow standardization.

## 3. Non-goals

- Automatically merging, rebasing, squashing, or reverting a consumer PR.
- Making the 19 repositories change atomically.
- Maintaining a custom rollout journal, merge-attestation state machine, or evidence hash
  graph.
- Replacing repository-specific CI or branch protection.
- Standardizing project-owned workflows.
- Reading, refreshing, or synchronizing repository/model secret values; the operator's
  GitHub credential is only transport authentication.
- Combining workflow rollout with `personal-ops/claude-token-sync` or another key writer.
- Automatically enabling common workflows in the two bootstrap repositories.

## 4. Source of Truth

### 4.1 Immutable central release

The implementation starts from the current `automation/main` and must preserve the
security fixes already shipped in `v1.39`. After its own tests and review, the final
automation commit is published as a new immutable annotated tag, `v1.40`. The tag is never
moved or deleted.

Consumer callers use the resolved 40-character commit, not tag text:

```yaml
uses: jhw7500/automation/.github/workflows/gemini-review.yml@<v1.40-commit>
```

The human-readable consumer config records both identities:

```yaml
automation_ref: v1.40
automation_commit: <v1.40-commit>
```

This permits gradual rollout: repositories that have not merged their PR continue to use
their prior immutable revision, while merged repositories use `v1.40`.

### 4.2 Canonical managed tree

`examples/baseline-workflows/.github/` is the only canonical consumer tree. The duplicate
top-level `examples/baseline-workflows/workflows/` source is retired after implementation
code and tests no longer use it.

The managed tree contains:

- canonical caller YAML under `.github/workflows/`;
- the bootstrap `.github/workflow-config.yml` template; and
- no project documentation or project-specific workflow.

### 4.3 Typed catalog

A single machine-readable catalog declares each managed path as one of:

- `required`: present in every configured repository;
- `optional`: present only when selected by that repository's profile;
- `config`: `.github/workflow-config.yml`, where only `automation_ref` and
  `automation_commit` may be changed in an existing repository; or
- `retired`: removed when present.

The catalog also defines each caller's central target, trigger shape, permissions, typed
inputs, and exact secret-name mappings. Python, shell, tests, and documentation must not
maintain independent managed-file lists.

## 5. Managed Workflow Set

### 5.1 Required callers

All 19 repositories have these ten managed callers:

- `claude.yml`
- `claude-code-review.yml`
- `gemini-auto-review.yml`
- `gemini-dispatch.yml`
- `gemini-invoke.yml`
- `gemini-issue-triage.yml`
- `gemini-pr-review.yml`
- `gemini-review.yml`
- `gemini-scheduled-triage.yml`
- `gemini-triage.yml`

### 5.2 Optional callers

The only optional callers are:

- `auto-rereview-request.yml`
- `gemini-chat.yml`
- `opencode.yml`
- `opencode-auto-review.yml`

An optional caller selected by a profile but missing from the repository is drift. An
optional caller present without profile selection is also drift and is proposed for
deletion in its PR.

### 5.3 Retired caller

`bump-automation-ref.yml` is retired and removed from consumers. It is no longer a second
workflow writer. Future upgrades use the same fleet PR generator.

## 6. Declarative Fleet Profiles

`scripts/workflow-config.json` becomes a typed inventory for the 19 repositories. Each
entry declares only policy that is allowed to vary:

```json
{
  "profile": "common-ai-v1",
  "optional_workflows": ["opencode.yml", "opencode-auto-review.yml"],
  "repo_write_auth": "github_app",
  "bootstrap_allowed": false
}
```

The approved profile snapshot is:

| Repository | Optional callers | Repo-write auth | Bootstrap |
| --- | --- | --- | --- |
| `gstApp` | auto-rereview, Gemini Chat, OpenCode manual/auto | App | no |
| `max9296` | auto-rereview, Gemini Chat, OpenCode manual/auto | App | no |
| `wlan-driver` | auto-rereview, OpenCode manual/auto | GitHub token | no |
| `wlan-driver-v2` | auto-rereview, OpenCode manual/auto | App | no |
| `wlan-bridge` | Gemini Chat, OpenCode manual/auto | App | no |
| `wlan-package` | OpenCode manual/auto | App | no |
| `pim-package-jhw` | OpenCode auto | App | no |
| `wlan-opc` | OpenCode manual | GitHub token | no |
| `pcap-analyzer` | none | App | no |
| `wpa-supplicant` | none | GitHub token | yes |
| `sc16is7xx` | none | App | no |
| `pim-check` | none | GitHub token | no |
| `redmine` | none | App | no |
| `jhw-notion` | none | App | no |
| `personal-ops` | none | App | no |
| `cts-email-mcp-server` | none | GitHub token | yes |
| `cts-ta-mcp-server` | none | GitHub token | no |
| `cts-ta-webapp` | none | GitHub token | no |
| `claude-config` | none | GitHub token | no |

The machine-readable configuration is authoritative; this table is a review snapshot.
File presence, ambient environment variables, and secret inventory never select a profile.

## 7. Authentication Contract

Model credentials and repository-write credentials remain separate.

- Claude callers map only `CLAUDE_CODE_OAUTH_TOKEN` where required.
- Gemini callers use `GEMINI_API_KEY` for model access.
- Profiled OpenCode callers map `ZHIPU_API_KEY`.
- No caller uses `secrets: inherit`.
- Secret sources are exactly `${{ secrets.<same-name> }}`.

For Gemini repository writes:

- `github_app` callers pass `app_id: ${{ vars.APP_ID }}` and
  `APP_PRIVATE_KEY: ${{ secrets.APP_PRIVATE_KEY }}`;
- `github_token` callers pass neither App field and the central workflow uses the exact
  built-in `${{ github.token }}` path; and
- caller and reusable-job permissions stay at the catalogued minimum and contain no
  unnecessary `id-token: write`.

The `v1.40` central contract removes ambiguous ambient authentication paths and retains
the known OpenCode same-repository and no-write-escalation protections from `v1.39`.
Specifically, Gemini model authentication accepts `GEMINI_API_KEY`, not
`GOOGLE_API_KEY`, GCP/Vertex, Code Assist, or OIDC credentials. Central release tests,
rather than the fleet rollout tool, own Action pinning, container pinning, CLI version
integrity, and reusable-workflow security checks.

## 8. Renderer and Safety Boundary

For each repository, the renderer:

1. loads the verified central release, catalog, and repository profile;
2. resolves `v1.40` to its commit;
3. reads the repository default branch;
4. checks required secret and variable names without reading values;
5. renders required and profiled optional callers from canonical bytes;
6. applies only declared authentication substitutions;
7. updates only the two automation identity scalars in an existing config;
8. creates a disabled bootstrap config only in an explicitly allowed bootstrap repository;
9. proposes deletion of unprofiled optional and retired callers;
10. rejects any proposed change outside the managed path set; and
11. validates the complete result before any push.

An out-of-catalog file that calls a reusable workflow under
`jhw7500/automation/.github/workflows/` is blocked for operator review. The tool neither
silently accepts it as project-owned nor deletes it without a catalog change.

Existing config comments, formatting, and every key other than `automation_ref` and
`automation_commit` are preserved. A malformed or ambiguous config blocks rather than
being rewritten wholesale.

Project-owned workflow bytes are captured before rendering and required to remain
identical afterward.

## 9. Operator Commands

The implementation keeps the existing repository scripts but narrows their public
behavior to three operations: plan, publish, and audit. Command spelling may follow the
existing argparse layout, but the following semantics are normative.

The commands use the operator's normal authenticated Git and `gh` installation. No custom
credential broker or rollout credential store is introduced. A credential is never placed
in a remote URL, command argument, commit, report, or log. Publish requires only repository
access needed to create workflow-file commits, branches, and PRs; its code contains no
merge or secret-mutation client. Temporary clones disable repository hooks, do not recurse
into submodules, and accept only the configured `github.com/jhw7500/<repo>` remote.

The central release verifier is intentionally separate from those authenticated consumer
operations. It discovers normal and linked-worktree metadata without Git, rejects
alternates and promisor/shallow object stores, reads version tag refs directly, and creates
an isolated temporary Git directory. Local raw object subprocesses use absolute
`/usr/bin/git`, `GIT_OBJECT_DIRECTORY` for only the complete common object store,
`GIT_NO_REPLACE_OBJECTS=1`, and a fixed nonexistent home/XDG root with system/global config,
prompts, askpass, and SSH agent disabled. Source `.git/config`, `.git/info/attributes`,
filters, hooks/helpers, and replacement refs do not enter object resolution. Archive bytes
come from exact raw tree/blob OIDs in a deterministic Python-built tar, not `git archive`.

For remote tag attestation it reads only the
direct, no-include local `remote.origin.url`, accepts exactly the public automation HTTPS
identity, canonicalizes it to `https://github.com/jhw7500/automation.git`, and performs
credential-free public HTTPS outside the repository with only the HTTPS transport allowed.
Host/repository URL rewrites, SSH commands, includes, and credential helpers never enter the
transport subprocess. A private or forked automation remote is outside this contract until
an explicit, minimal release-verification credential channel is separately designed and
reviewed.

### 9.1 Plan

```text
rollout_workflow_fleet.py --mode plan --ref v1.40 [--repo NAME ...]
```

Plan is read-only for GitHub. With no `--repo`, it evaluates all 19 profiles and writes a
convenience JSON report containing the release commit, observed base SHA, status, reason,
and proposed managed-file diff for every repository.

The public plan statuses are:

- `current`: managed content already matches and no stale rollout branch exists;
- `planned`: validated managed changes can create a branch/PR, or an exact branch can
  receive its missing PR;
- `reusable`: one exact open PR and branch match the complete rollout identity; or
- `blocked`: input, contract, config, inventory, prerequisites, or repository state is
  unsafe.

The pure renderer retains renderer-only classifications `current`, `drift`,
`bootstrap_required`, and `blocked`. Orchestration maps safe `drift` and an explicitly
requested safe `bootstrap_required` to public `planned` only after exact remote-state
inspection. A missing config without explicit bootstrap is `blocked`. Audit remains a
separate content classifier and reports only `current`, `drift`, or `blocked`.

The report is not an approval token or transaction journal. Publish always refetches and
recomputes the selected repository before writing.

### 9.2 Publish

```text
rollout_workflow_fleet.py --mode publish --ref v1.40
  --repo NAME [--repo NAME ...] --confirm
```

Publish performs only these remote effects:

1. create a repository branch from the freshly fetched default branch;
2. commit the validated managed-file diff;
3. push that branch; and
4. open a pull request.

It has no merge, auto-merge, update-branch, force-push, default-branch push, secret write,
variable write, or revert code path. It never calls a GitHub merge endpoint.

The branch name is deterministic per repository and release, for example
`automation/common-workflows-v1.40`. For each repository:

- an absent branch is created;
- an existing branch whose base and managed path/blob diff exactly match the fresh render
  may receive its missing PR;
- an existing branch and one open PR are reused only when that same content plus PR base,
  title, and body match; and
- a content mismatch, multiple PRs, or any closed/merged PR history for the deterministic
  head blocks that repository without a force-push, whether or not its branch remains.

Multi-repository publish first validates every selected repository, then creates PRs one
at a time, refetching that repository immediately before branch creation. Network or
permission failure may therefore leave a valid subset of PRs open.
That is an accepted outcome, not a transaction failure: the report lists created,
existing, and blocked repositories, returns non-zero when any repository is blocked, and
a later invocation safely reuses exact existing PRs.

Bootstrap publish requires an additional explicit `--bootstrap-repo NAME` and exactly one
repository. Its generated config disables every common workflow. Enabling them is a later
ordinary repository PR.

### 9.3 Audit

```text
audit_workflow_fleet.py --ref v1.40 [--repo NAME ...]
```

Audit reads current default-branch content and reports `current`, `drift`, or `blocked`.
It validates managed bytes and contracts, not merge history. It ignores project-owned
workflow differences by design.

Audit does not require a rollout journal or prove which merge method was used. GitHub
commits, PR reviews, checks, and merge history are the operational record.

## 10. GitHub Review and Merge

After publish, repository owners use their existing GitHub process:

1. inspect the PR diff;
2. run repository-specific CI and validation;
3. obtain any required review;
4. merge through the repository's normal GitHub controls; and
5. run audit against the resulting default branch.

The fleet tool never grants itself an exception to branch protection and never performs
the merge. Merge commit, squash, or rebase policy may remain repository-specific because
final audit checks content rather than a custom commit topology.

## 11. Rollout Sequence

### Gate 1: automation release

1. Implement central workflow, canonical template, catalog, profile, renderer, and audit
   changes in bounded automation PRs.
2. Run automation unit tests, workflow contract tests, YAML parsing, actionlint, release
   security regression tests, and `git diff --check`.
3. Merge to current `automation/main`, create immutable `v1.40`, and verify the tag and
   release commit.

No consumer PR is created before this gate succeeds.

### Gate 2: full read-only plan

Run plan across all 19 repositories. Review every proposed managed diff and every blocked
reason. Unexpected project-owned changes, permission expansion, unknown callers, or secret
mapping changes stop the rollout.

### Gate 3: canaries

Create independent PRs for:

1. `wlan-package`: App-auth Gemini plus automatic/manual OpenCode and representative
   Claude behavior;
2. `wlan-driver`: built-in GitHub-token Gemini behavior without App credentials; and
3. `cts-email-mcp-server`: explicit disabled bootstrap behavior.

Repository owners review and merge each PR normally. After merge, use harmless test PRs or
manual dispatch to verify the expected central revision and behavior. Any failure stops
further PR creation. An unmerged canary is closed to abort that repository/release attempt;
its closed PR history blocks reuse, so a corrected retry uses a new immutable release/ref.
Merged canaries are reverted through an ordinary reviewed revert PR when rollback is
preferable to roll-forward.

### Gate 4: remaining PRs

After canaries succeed, create independent PRs for the remaining repositories.
`wpa-supplicant` uses its own explicit disabled bootstrap PR. Each repository merges on its
own schedule after its normal checks and reviews.

### Gate 5: final audit

Repeat audit until all 19 repositories report `current` and none report `blocked`.
Repositories with an open, not-yet-merged PR legitimately remain `drift` during rollout.

## 12. Recovery

### Before merge

- Close the PR and optionally delete the rollout branch to abort the current attempt.
- Treat its closed PR history as permanently blocking reuse of that deterministic
  repository/release identity.
- Correct the central template or profile, publish a new immutable release/ref, and plan a
  new deterministic branch and PR.

The default branch is unchanged, so no rollback transaction is needed.

### After a consumer merge

Use GitHub's normal revert-PR capability when available, or create a branch containing
`git revert` of the relevant merge/squash commit and open a normal reviewed PR. The fleet
tool does not automate this operation or overwrite intervening changes.

### After a bad central release

Never move or delete `v1.40`. Prefer roll-forward:

1. fix automation;
2. publish a new immutable tag such as `v1.41`; and
3. generate new consumer PRs.

An urgent repository may instead use a reviewed PR restoring its previous known-good
automation commit.

## 13. Secret and Token Synchronization

Workflow rollout and credential synchronization remain separate operations.

- The workflow tools may list required secret or variable names for prerequisite checks.
- They never request secret values, consume local provider keys, or call secret/variable
  mutation endpoints.
- Local values such as `ZHIPU_API_KEY` are scrubbed from child environments and logs.
- `personal-ops/claude-token-sync` retains its own repository inventory, locking, health,
  and deployment lifecycle.

The legacy secret-write flags and paths in `rollout_workflow_fleet.py` and
`sync-secrets.sh` are not part of this workflow command. They are removed or changed into
side-effect-free migration guards before fleet PR creation. `setup-github-workflows.sh`
also stops directly copying to consumer repositories and points operators to plan/publish.
Missing required secret names must be resolved through the separately reviewed credential
management path before the affected workflow PR is merged.

A future read-only report may compare workflow membership, required secret names, and token
sync membership, but there is no combined "workflow plus key mutation" command.

## 14. Test Strategy

Implementation follows test-driven development and covers:

### Catalog and rendering

- every required, optional, config, and retired path has exactly one catalog entry;
- canonical caller contracts agree with central `workflow_call` inputs and secrets;
- all 19 profiles render deterministically;
- only declared profile substitutions differ;
- a second render is byte-identical;
- project-owned workflow bytes never change; and
- malformed config or an unmanaged proposed path blocks before writing.

### Authentication and security

- `github_app` and `github_token` render only their approved mappings;
- Claude, Gemini, and OpenCode secret sources use the same-name secret expression;
- `secrets: inherit`, unknown secret inputs, permission expansion, and OpenCode OIDC/write
  escalation fail;
- provider secrets from the local environment never reach renderer, Git, logs, or reports;
- central reusable calls use the verified commit; and
- current `v1.39` security fixes remain regression tested in `v1.40`.

### Commands

- plan and audit issue no remote mutation;
- publish calls only branch, push, and PR creation paths;
- any merge, auto-merge, force-push, default-branch push, secret mutation, or variable
  mutation sentinel fails the test;
- exact existing PRs are reusable and mismatched branches/PRs block;
- a partial multi-repository publish reports all completed and failed repositories and is
  safe to rerun; and
- bootstrap requires one explicitly allowed repository and renders all common workflows
  disabled.

### Live validation

- automation release verification passes from the tagged commit;
- the three canaries exercise App, built-in GitHub token, OpenCode, Claude, and disabled
  bootstrap paths; and
- final audit reports `current=19`, `drift=0`, and `blocked=0` after all reviewed merges.

No custom filesystem crash matrix, merge-intent replay, or rollout-journal fault injection
is required because the tool does not merge or revert default-branch content.

## 15. Acceptance Criteria

- `v1.40` is an immutable verified automation release based on current `main`.
- The catalog and fleet profile are the only policy sources for managed callers.
- Every consumer change is an independent reviewable PR.
- The fleet tool never merges, reverts, force-pushes, or writes a default branch.
- Project-owned workflows are unchanged.
- Required and profiled optional callers match canonical content after rollout.
- All caller references use the verified central commit.
- Authentication differences match only the declared profiles.
- `bump-automation-ref.yml` is absent from consumers.
- No repository Actions secret/variable value or local provider value is read or written
  by workflow rollout; only the separately scoped operator GitHub credential is used for
  repository access.
- Canary runtime behavior succeeds before remaining PR creation.
- Final content audit reports all 19 repositories current with no blocked repository.
