# Common Workflow Standardization Design

Date: 2026-08-13
Status: revised after independent security and architecture review

## Decision Summary

The fleet will use one immutable release bundle, one machine-readable caller catalog,
one declarative repository profile, and one remote writer.

- `automation@v1.40` will bundle the reusable workflows, fully pinned transitive
  actions, caller catalog, renderer, verifier, and tests.
- Consumer reusable-workflow calls will use the resolved 40-character `v1.40` commit,
  not the movable tag text.
- All 19 repositories registered in `scripts/workflow-config.json` are managed by the
  required common-AI profile. The two repositories that currently have no `.github`
  directory are explicit bootstrap targets rather than implicit skips.
- Optional workflow presence and Gemini authentication mode are declared in fleet
  configuration. They are never inferred from mutable repository contents.
- Project-owned build, test, verification, release, packaging, deployment, lint, and
  synchronization workflows remain byte-for-byte outside the managed catalog.
- `rollout_workflow_fleet.py` is the only remote workflow writer. The legacy direct-copy
  setup path and consumer-side regex bump workflow are retired.
- This rollout does not write or refresh any secret value.

## Context and Problems to Solve

The previous `v1.39` fleet has 17 repositories with valid central callers and two
repositories with no callers. The 17 consumers satisfy the current ref and secret-name
contract, but their common caller files contain structural drift. The registered fleet
configuration nevertheless marks all 19 repositories as workflow-enabled, and both
caller-free repositories already contain the required Claude and Gemini secret names.

The repository currently has two overlapping template trees:

- `examples/baseline-workflows/workflows/`
- `examples/baseline-workflows/.github/workflows/`

Neither is a complete source of truth. The chosen `.github/workflows` tree is missing
`gemini-issue-triage.yml`, `gemini-pr-review.yml`, and
`opencode-auto-review.yml`. The other tree lacks several optional workflows. Template
refs and `scripts/workflow-config.json` also lag the deployed `v1.39` fleet.

`v1.39` fixes the known OpenCode write-escalation path, but its runtime dependency graph
is not fully immutable. Credential-sensitive workflows still reference internal and
external actions through tags such as `check-workflow-enabled@v1.1`,
`setup-gemini-auth@v1.1`, `run-gemini-cli@v0`, and `claude-code-action@v1`.

The existing fleet auditor validates reusable secret declarations but not the complete
`workflow_call.inputs` contract. The existing legacy setup script and
`bump-automation-ref.yml` are independent write paths that can bypass canonical
rendering and exact contract validation.

## Goals

1. Produce a reproducible fleet state from one verified immutable release bundle.
2. Standardize every managed common caller while retaining only explicitly declared
   optional presence and authentication differences.
3. Preserve project-owned workflows byte-for-byte.
4. Enforce complete reusable input and secret contracts before any remote write.
5. Eliminate ambient inventory as an authorization policy.
6. Make accidental caller deletion, optional-file deletion, and unmanaged central calls
   fail closed.
7. Keep each consumer change independently reviewable and reversible.
8. Provide one command for plan and one confirmed command for publish across the fleet.
9. Perform no secret value write during this standardization rollout.

## Non-goals

- Standardizing repository-specific build, test, hardware verification, release,
  packaging, deployment, ShellCheck, commit-lint, or repository synchronization logic.
- Enabling optional OpenCode, Gemini Chat, or auto-rereview workflows in repositories
  where the approved profile does not include them.
- Changing model provider, model name, prompts, secret values, repository variables, or
  Claude/Gemini/OpenCode enablement settings in the 17 existing consumers.
- Automatically enabling PR auto-review in the two bootstrap repositories.
- Moving or deleting an existing release tag.
- Retaining consumer-side regex replacement as a release-upgrade mechanism.

## 1. Immutable Release Bundle

### 1.1 New release identity

The implementation is merged to `automation/main` first. The verified merge commit is
then published as the new annotated tag `v1.40`; the tag does not exist at design time
and must never be moved or deleted after publication.

The release bundle contains at least:

- `.github/workflows/`
- `.github/actions/`
- `examples/baseline-workflows/.github/`
- the machine-readable catalog
- `scripts/prepare_workflow_rollout.py`
- `scripts/audit_workflow_fleet.py`
- `scripts/rollout_workflow_fleet.py`
- `scripts/verify_workflow_release.py`
- tests that enforce bundle invariants

The release verifier resolves local and remote `v1.40` to the same expected merge
commit, archives the bundle from that tag, and verifies the archive rather than the
working tree.

Release refs use one shared grammar in the CLI, verifier, catalog, and tests:
`^v[0-9]+\.[0-9]+(?:\.[0-9]+)?$`. No component maintains its own narrower regex.

### 1.2 Transitive action pins

Every remote `uses:` in released reusable workflows and composite actions must use a
40-character commit SHA. This includes internal calls back to
`jhw7500/automation/.github/actions` and credential-sensitive third-party actions.

The release verifier recursively scans released workflow and action YAML and rejects:

- version tags, branches, or other mutable refs;
- unregistered `uses:` targets;
- a SHA that does not match the approved action-pin manifest;
- missing action-pin metadata or comments.

No general `ratchet:exclude` exception is allowed for an action that receives a token,
private key, API key, or repository write permission.

### 1.3 Consumer runtime pin

The renderer resolves `v1.40` to its verified 40-character commit and writes that commit
into every consumer reusable-workflow `uses:` value. Human-readable consumer config
records both values:

```yaml
automation_ref: v1.40
automation_commit: <verified-40-character-commit>
```

The auditor requires every managed central caller to use `automation_commit` exactly
and verifies that `automation_ref` still resolves to that commit remotely. A tag move
therefore fails audit and cannot alter an already deployed caller.

The rollout manifest records:

- release ref and resolved release commit;
- renderer/tool commit;
- catalog SHA-256;
- action-pin manifest SHA-256;
- actionlint version;
- per-repository base and generated head commits.

## 2. Single Canonical Catalog

`examples/baseline-workflows/.github/` becomes the only canonical caller tree. The
duplicate top-level `workflows/` and `workflow-config.yml` are removed after all code,
tests, and documentation use the canonical tree.

A single machine-readable catalog, stored beside the canonical tree, declares every
managed filename, class, central target, and removal policy. Lists are not duplicated in
Python, shell, tests, or JSON fleet configuration.

### 2.1 Required workflows

All 19 managed repositories contain the following ten common callers:

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

### 2.2 Optional workflows

Only repositories whose declared profile includes a filename contain that optional
caller:

- `auto-rereview-request.yml`
- `gemini-chat.yml`
- `opencode.yml`
- `opencode-auto-review.yml`

An optional file missing from a profile is absent by policy. A profiled optional file
missing from a repository is drift and is restored through a pull request. An optional
file present without being profiled is drift and is removed or blocked according to the
catalog policy; it is never silently accepted.

### 2.3 Retired workflow

`bump-automation-ref.yml` is removed from the canonical catalog and all consumers. It
cannot safely update input and secret mappings when a reusable contract changes, and
its GitHub App token path otherwise requires high workflow-write permission. Future
release upgrades are performed only through the verified fleet renderer.

The catalog records it as an explicitly retired managed filename so the renderer can
propose deletion and the auditor can reject reintroduction.

### 2.4 Catalog completeness invariants

Tests fail unless:

- every catalog entry has exactly one canonical YAML file;
- every canonical workflow YAML is registered;
- each caller contains exactly one expected central reusable-workflow job;
- the central target exists in the release bundle;
- required and optional sets are disjoint;
- retired filenames are absent from the canonical workflow directory;
- no catalog filename uses both `.yml` and `.yaml` variants.

## 3. Declarative Fleet Profiles

`scripts/workflow-config.json` remains the fleet inventory but gains an explicit schema
version and per-repository policy. It no longer treats current file presence as intent.

Each repository entry declares:

```json
{
  "profile": "common-ai-v1",
  "optional_workflows": ["opencode.yml", "opencode-auto-review.yml"],
  "gemini_auth": {
    "model_secret": "GEMINI_API_KEY",
    "github_app": true
  }
}
```

`profile: common-ai-v1` means the required catalog is mandatory. A future opt-out must
be an explicit reviewed configuration change, not an empty workflow directory.

### 3.1 Approved optional-presence snapshot

| Repositories | Optional workflows |
| --- | --- |
| `gstApp`, `max9296` | auto-rereview, Gemini Chat, OpenCode manual, OpenCode auto |
| `wlan-driver`, `wlan-driver-v2` | auto-rereview, OpenCode manual, OpenCode auto |
| `wlan-bridge` | Gemini Chat, OpenCode manual, OpenCode auto |
| `wlan-package` | OpenCode manual, OpenCode auto |
| `pim-package-jhw` | OpenCode auto |
| `wlan-opc` | OpenCode manual |
| all other registered repositories | none |

The table is encoded once in fleet configuration; it is descriptive here, not a second
machine source.

### 3.2 Authentication profiles

All 19 profiles explicitly select `GEMINI_API_KEY` as the model credential. The
renderer never also exposes `GOOGLE_API_KEY` merely because that secret later appears.

GitHub App authentication is enabled for the following eleven repositories because the
current approved inventory contains both `APP_ID` and `APP_PRIVATE_KEY`:

- `gstApp`
- `jhw-notion`
- `max9296`
- `pcap-analyzer`
- `personal-ops`
- `pim-package-jhw`
- `redmine`
- `sc16is7xx`
- `wlan-bridge`
- `wlan-driver-v2`
- `wlan-package`

It is disabled for the other eight repositories. Ambient addition or removal of a
secret or variable never changes the rendered mapping:

- a configured credential missing from inventory blocks the repository;
- an unconfigured credential present in inventory is ignored;
- `github_app: true` requires both `APP_ID` and `APP_PRIVATE_KEY`;
- `github_app: false` passes neither `app_id` nor `APP_PRIVATE_KEY`.

The new central release removes the legacy implicit `vars.APP_ID` fallback. GitHub App
mode is controlled only by the explicit `app_id` input, preventing an `APP_ID`-only
inventory from failing later inside the called workflow.

The Gemini GitHub App token is restricted exactly to `contents: read`, `issues: write`,
and `pull-requests: write`. It receives no workflow, secret, administration, or
cross-repository permission. Tests inspect the pinned token action inputs in every job
that can mint the token.

## 4. Bootstrap Policy for the Two Caller-Free Repositories

`cts-email-mcp-server` and `wpa-supplicant` are explicit bootstrap targets. Normal
`plan` reports `bootstrap-required` while their config or required catalog is absent; it
does not classify them as skipped or silently create files.

Bootstrap requires all of:

- an explicit `--bootstrap-repo <name>` argument;
- exactly one selected repository per invocation;
- `--mode publish --confirm`;
- the declared repository profile and required secret-name prerequisites;
- zero secret-sync or secret-refresh flags;
- an independent pull request.

Bootstrap creates the required catalog and a minimal config with automatic review off:

```yaml
automation_ref: v1.40
automation_commit: <verified-commit>
review:
  auto: false
workflows:
  gemini-scheduled-triage:
    enabled: false
```

After bootstrap merges, normal plan treats config and required callers as mandatory;
their deletion becomes blocked drift rather than another bootstrap opportunity unless
the explicit bootstrap flag is supplied again.

## 5. Deterministic Renderer

For each repository the renderer performs the following steps entirely in memory or in
a preview tree before writing the managed clone:

1. Load and validate the catalog and repository profile.
2. Resolve and verify the immutable release bundle.
3. Enumerate secret and variable names only; never read values.
4. Check declared authentication prerequisites without deriving policy from inventory.
5. Start every required and profiled optional file from canonical bytes.
6. Substitute the verified release commit into the expected central `uses:` value.
7. Render the exact declared `with` and `secrets` mappings.
8. Apply the approved full action SHAs in managed callers.
9. Update only `automation_ref` and `automation_commit` in an existing consumer config;
   preserve all other bytes.
10. Propose deletion of catalog-declared retired files.
11. Refuse to touch files outside the managed catalog.
12. Validate the entire planned repository before performing any write.

The renderer is idempotent: applying the same verified bundle, catalog, profile, and
inventory to its own output produces zero changed files.

### 5.1 Project-owned boundary

Files outside the managed catalog are hashed before and after preparation. Any byte
change blocks the repository.

An out-of-catalog file that calls `jhw7500/automation/.github/workflows` is not silently
treated as project-owned. It is an unmanaged central caller and blocks until the caller
is added to the catalog, renamed to a catalog entry, or explicitly removed through a
reviewed policy change.

## 6. Complete Reusable-Workflow Contract Audit

Contracts are loaded only from the verified release bundle. For every central target the
loader records:

- declared inputs;
- required inputs;
- input types and defaults;
- declared secrets;
- required secrets.

Every rendered and existing managed caller must satisfy:

- target workflow exists and declares `workflow_call`;
- no unknown input or secret key;
- every required input and secret is present;
- secret sources are exactly `${{ secrets.<same-name> }}`;
- `app_id`, when configured, is exactly `${{ vars.APP_ID }}`;
- the caller uses the verified release commit;
- caller job permissions equal the catalog permission allowlist;
- triggers and all non-profile-dependent structure equal canonical output;
- no `secrets: inherit`;
- OpenCode callers contain no `id-token: write` and retain the read-only contents
  ceiling and same-repository caller guard.

Static audit checks key presence and canonical expressions. Actionlint supplies GitHub
expression and input type validation. Missing/unknown input and secret tests must fail
for the intended reason before production code is added.

## 7. One Writer and One Upgrade Path

`scripts/rollout_workflow_fleet.py` is the only supported remote workflow writer and the
only release-upgrade path.

- `setup-github-workflows.sh` loses raw copy, direct commit/push, and secret-write
  behavior. It either becomes a deprecation shim that invokes the Python tool or exits
  with the exact replacement command.
- `bump-automation-ref.yml` is removed from consumers and the catalog.
- `sync-secrets.sh` is not used by this rollout. Future secret operations use the
  Python tool's explicit allowlisted flags and remain a separately confirmed phase.
- Documentation must direct operators to profile changes plus fleet plan/publish rather
  than editing or deleting common wrapper files.

The rollout CLI separates target release identity from rollout identity:

```text
--ref v1.40
--rollout-id common-ai-v1-v140
```

Branches, commit messages, PR titles, and manifests use the rollout ID. They do not reuse
the old secret-hardening branch name or claim that repository-owned triggers and
permissions were preserved when canonical replacement intentionally changes them.

## 8. Validation and Failure Semantics

### 8.1 Plan

Plan mode is read-only for GitHub state. It fetches every default branch, renders into a
temporary preview, and collects outcomes for the full fleet. It may write only local
disposable clones and the requested local manifest.

Required plan gates:

- verified local/remote release bundle and hashes;
- profile/catalog schema validation;
- secret/variable name prerequisites;
- complete input and secret contract audit;
- exact managed-file comparison;
- retired and unmanaged central-caller checks;
- YAML parse;
- `git diff --check`;
- mandatory actionlint `1.7.12` using the official Linux AMD64 archive SHA-256
  `8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8`, with recorded
  version and fail-closed process status;
- zero new actionlint diagnostics compared with untouched project-owned baseline;
- confirmation that project-owned file hashes are unchanged.

Plan returns non-zero for any `blocked` or `bootstrap-required` outcome. It does not hide
a missing catalog behind `skipped`.

### 8.2 Publish

Publish retains `--confirm` and is fail-fast by default. A repository is prepared and
fully validated before any push. Immediately before commit/push, the tool refetches the
remote default branch and requires it to equal the recorded base SHA. A mismatch blocks
without pushing and must be replanned.

Each repository receives an independent branch and pull request. The manifest records
completed remote effects. Resumption uses explicit `--repo` selections and never
replays already merged repositories implicitly.

The tool rejects every secret-sync and refresh option for this rollout ID. Every
manifest outcome must contain `synced_secrets: []`.

### 8.3 Tool failure

Actionlint absence, non-zero execution without parseable diagnostics, malformed GitHub
metadata, release/tag mismatch, stale default branch, invalid YAML, incomplete
inventory, or a catalog inconsistency is blocked rather than treated as an empty clean
result.

## 9. Test Strategy

Implementation follows test-driven development. Each behavior is first demonstrated by
a failing regression test.

### Release and catalog

- local and remote tag mismatch fails;
- catalog or action-pin digest mismatch fails;
- any released remote action tag/branch ref fails;
- all catalog files exist and no unregistered managed YAML exists;
- missing required and profiled optional files are drift;
- unprofiled optional and retired files are reported according to policy.

### Rendering and contracts

- required caller output equals canonical rendering;
- API-key-only and GitHub-App profiles differ only by approved fields;
- ambient `GOOGLE_API_KEY` or `APP_ID` does not expand a profile;
- partial configured App inventory blocks;
- missing and unknown input keys fail;
- missing, unknown, inherited, and wrong-source secrets fail;
- caller permissions, trigger, input, and same-repository guard drift fail;
- out-of-catalog central callers fail;
- project-owned hashes remain identical;
- retired bump files are deleted;
- a second render produces no changes;
- no file is written when any planned YAML is invalid.

### Orchestration

- plan performs no remote write and reports all repositories;
- normal mode does not infer bootstrap from absence;
- bootstrap requires one explicit repository and confirmation;
- publish requires actionlint and fails on tool execution errors;
- publish refetches and blocks a stale default branch;
- publish is fail-fast and resume is explicit;
- rollout branch and PR text use rollout ID rather than release-only identity;
- all standardization manifests contain empty `synced_secrets`.

## 10. Rollout Sequence and Stop Conditions

### Gate 1: automation implementation and release

1. Implement in the isolated `codex/standardize-common-workflows` worktree.
2. Review the automation changes as two bounded commits or pull requests:
   - runtime hardening: transitive action pins, explicit App mode, and release verifier;
   - delivery tooling: catalog, profiles, renderer/auditor, writer retirement, and tests.
3. Run the full unit, YAML, release-bundle, action-pin, actionlint, and diff gates after
   each bounded change.
4. Merge both in dependency order, fetch the final exact merge commit, and rerun all
   gates from that commit.
5. Create annotated `v1.40` only at the final commit and verify locally before push.
6. Push the tag and rerun the verifier against remote.

Do not create a consumer PR before the remote release verifier succeeds.

### Gate 2: full read-only fleet plan

Run all 19 profiles with secret sync disabled. Expected initial outcomes are drift for
the 17 existing consumers and explicit `bootstrap-required` for the two caller-free
repositories, with no ordinary skip.

### Gate 3: behavioral and bootstrap canaries

1. `wlan-package`: validate canonical replacement of its local OpenCode check jobs,
   GitHub-App profile, OpenCode automatic review, and manual `/opencode` path.
2. `wlan-driver`: validate the API-key-only profile produces no App input or private-key
   mapping.
3. `cts-email-mcp-server`: bootstrap the required catalog with automatic review disabled
   and verify workflow discovery/skip behavior on a harmless pull request.

Temporary canary pull requests and branches are closed or deleted after evidence is
captured. Any canary failure stops the rollout.

### Gate 4: fleet publish

Publish independent PRs for the remaining repositories. `wpa-supplicant` uses the same
explicit bootstrap path and remains automatic-review-disabled initially.

### Gate 5: final audit

The final read-only plan must report:

- `current=19`;
- `skipped=0`;
- `bootstrap-required=0`;
- `blocked=0`;
- `synced_secrets=[]` for every repository;
- all consumer callers pinned to the verified release commit;
- project-owned hashes unchanged from each PR base.

At every gate, release verification, catalog/profile validation, contract audit, YAML,
actionlint, default-branch freshness, project-file hashes, or live canary failure stops
the next stage.

## 11. Recovery

- Starting automation base: `2254f13aab44585c78954d20749f4fb677a8c2f1`.
- Initial design commit: `7ea29d2e5d5c7b1673cce378c40c8c24deb3df81`.
- Development remains isolated on `codex/standardize-common-workflows`.
- Before merge, delete the isolated worktree and branch to abandon the change.
- After merge, revert the automation PR; never move or delete `v1.40`.
- If released behavior is unsafe, fix forward with a new immutable tag.
- Consumer changes are independent PRs and are reverted independently in reverse merge
  order.
- Reverting a bootstrap PR removes only the newly added managed `.github` content.
- Removing `bump-automation-ref.yml` is restored by reverting that repository's PR if
  emergency rollback requires the previous mechanism.
- No secret value is changed, so secret-value rollback is not part of this rollout.

## 12. Acceptance Criteria

- `v1.40` local and remote tags resolve to the reviewed merge commit.
- The release bundle verifier passes against the archived tag content.
- Every released remote action reference is an approved full commit SHA.
- The canonical catalog is complete, unique, and digest-recorded.
- All 19 repository profiles are explicit; no management or optional state is inferred
  from file presence.
- All 19 repositories contain the required catalog and exactly their profiled optional
  callers.
- Every central caller uses the verified release commit and satisfies complete input,
  secret, permission, and trigger contracts.
- No managed caller uses `secrets: inherit` or an undeclared ambient credential.
- `bump-automation-ref.yml` is absent from all consumers.
- Every project-owned workflow hash equals its pre-rollout base hash.
- Mandatory actionlint and all automated tests pass with recorded versions/evidence.
- Publish-time default branch SHA equals its planned base SHA for each repository.
- `wlan-package` automatic and manual OpenCode canaries succeed.
- API-key-only and bootstrap canaries satisfy their declared profiles.
- Final fleet state is `current=19`, `skipped=0`, `bootstrap-required=0`, `blocked=0`.
- Every manifest entry has `synced_secrets: []`.
