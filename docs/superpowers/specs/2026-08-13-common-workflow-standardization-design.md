# Common Workflow Standardization Design

Date: 2026-08-13
Status: revised after multiple independent security and architecture review rounds

## Decision Summary

The fleet will use one immutable release bundle, one typed machine-readable catalog,
one declarative repository profile, and one remote workflow writer.

- `automation@v1.40` will bundle the reusable workflows, a recursively locked declared
  Actions/container graph, caller catalog, fleet profiles, renderer, verifier, and tests.
- The supported rollout entrypoint executes only from a clean detached checkout of the
  verified release commit; working-tree tooling is not an accepted execution path.
- Consumer reusable-workflow calls will use the resolved 40-character `v1.40` commit,
  not the movable tag text.
- All 19 repositories registered in `scripts/workflow-config.json` are managed by the
  required common-AI profile. The two repositories that currently have no `.github`
  directory are explicit bootstrap targets rather than implicit skips.
- Optional workflow presence, Gemini model-auth mode, and GitHub App mode are declared
  in fleet configuration. They are never inferred from mutable repository contents.
- `v1.40` supports Gemini API-key model authentication only. It removes ambient GCP,
  Vertex AI, Code Assist, Google API-key, OIDC, and caller-selected CLI-version paths.
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
8. Provide one authoritative workflow-standardization CLI for read-only plan, confirmed
   publish, attested merge, and attested rollback, with an explicit one-repository
   bootstrap form.
9. Perform no secret value write during this standardization rollout.
10. Bind the executing renderer and every policy input to the verified release commit.
11. Lock the declared GitHub Actions/container dependency graph and the Gemini CLI
    installer artifact; live API responses and service behavior remain outside this
    reproducibility boundary.

## Non-goals

- Standardizing repository-specific build, test, hardware verification, release,
  packaging, deployment, ShellCheck, commit-lint, or repository synchronization logic.
- Enabling optional OpenCode, Gemini Chat, or auto-rereview workflows in repositories
  where the approved profile does not include them.
- Changing model provider, model name, prompts, secret values, repository variables, or
  Claude/Gemini/OpenCode enablement settings in the 17 existing consumers. Caller control
  of `GEMINI_CLI_VERSION` is intentionally removed as a runtime-integrity hardening change.
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
- the typed machine-readable catalog
- `scripts/workflow-config.json` with the reviewed fleet profiles
- the recursive action/container/runtime-artifact lock manifest
- `scripts/workflow-release-manifest.json`, which hashes every declared release-bundle
  file while excluding only itself to avoid a self-referential digest
- `scripts/workflow_release_bootstrap.py`, a standard-library-only verified loader
- `third_party/pyyaml/`, the reviewed pure-Python parser and its upstream license
- `scripts/prepare_workflow_rollout.py`
- `scripts/audit_workflow_fleet.py`
- `scripts/rollout_workflow_fleet.py`
- `scripts/verify_workflow_release.py`
- tests that enforce bundle invariants

The verifier has two closed phases. `prepush` requires an annotated local `v1.40` at the
expected merge commit and requires the remote tag to be absent. `remote` requires local
and remote tags to resolve to that same expected commit. Both archive the bundle from the
local tag and verify the archive rather than a development working tree. The release
manifest defines closed bundle roots and every tracked file below them must appear exactly
once; an undeclared file, missing file, duplicate path, symlink escape, special file, or
case-colliding path fails. Rollout accepts only evidence from the successful `remote`
phase.

Release refs use one shared grammar in the CLI, verifier, catalog, and tests:
`^v[0-9]+\.[0-9]+(?:\.[0-9]+)?$`. No component maintains its own narrower regex.

### 1.2 Verified execution context

The supported operator path resolves the phase-appropriate tag: `prepush` verifies the
annotated local tag while requiring remote absence; `remote` and every rollout command
verify the annotated remote/local equality. It then creates one disposable **detached** Git
worktree at that exact commit and invokes the tagged tool
with isolated, no-site, no-bytecode Python mode (`/usr/bin/python3 -I -S -B`). ApprovalPlan,
preview, EffectJournal, PublishResult, RevertPlan, and RevertResult artifacts are written
outside that worktree. Executing a renderer copied from another checkout is unsupported
and fails.

The bootstrap defines the host trust root explicitly: a trusted `/usr/bin/python3`
CPython 3.10 runtime/standard library, `/usr/bin/git`, `/usr/bin/gh`, the operating system,
and TLS. It requires `sys.implementation.name == cpython` and
`sys.version_info[:2] == (3, 10)` and records the full versions and executable digests;
release code and parsers are not taken from the host. Before project import it discards
the inherited environment completely and constructs the exact closed child environments
defined in Section 7.1. The operator's GitHub authentication credential is the sole
credential exception and is available only through the private credential-broker channel;
it is redacted from diagnostics and never written to any evidence object or preview. The
supported invocation is exactly `/usr/bin/python3 -I -S -B
scripts/workflow_release_bootstrap.py ...`. `-S` is mandatory so neither system/user
`site-packages`, `.pth`, `sitecustomize`, nor `usercustomize` executes before attestation.

At process start, before loading policy, importing a project/non-stdlib module, or touching
a consumer clone, the standard-library-only bootstrap requires all of the following:

- `HEAD == expected_release_commit == local_tag_commit`;
- phase-specific remote state: `prepush` requires remote tag absence, while `remote` and
  every rollout command require `remote_tag_commit == local_tag_commit`;
- detached HEAD, GitHub API-confirmed `jhw7500/automation` repository identity, and no
  submodule indirection;
- zero staged, unstaged, or untracked changes and no policy input loaded from an ignored
  path in the release worktree;
- its own path and the release manifest are tracked paths whose working bytes equal their
  release blobs, after which every manifest path and digest is verified;
- every subsequently imported project or non-stdlib module resolves to a verified tracked
  path beneath the detached release root; and
- the recomputed bundle digests equal the archived release metadata.

PyYAML is the only allowed non-stdlib runtime dependency. The release vendors the
pure-Python `yaml/` tree from PyYAML `6.0.3` sdist
`pyyaml-6.0.3.tar.gz` (SHA-256
`d76623373421df22fb4cf8817020cbb7ef15c725b9d5e45f17e189bfc384190f`) with its MIT license.
Only after the complete release manifest verifies does the bootstrap install a restrictive
import hook for the verified `scripts/` and `third_party/pyyaml/` roots and invoke the
tagged CLI. Import of `_yaml`, host PyYAML, any other site package, or an unmanifested
module fails. Project code never calls PyYAML's default/unsafe loader. A release-owned
`GitHubLoader` uses YAML 1.2 core scalar rules (`true`/`false` are booleans while
`on`/`off`/`yes`/`no` remain strings), preserves scalar node kind/style and source spans,
rejects duplicate keys and unsafe/unknown tags, and never constructs Python objects. The
config editor layers the stricter alias/anchor/merge policy described below. Contract
tests explicitly cover the GitHub `on:` key and boolean/number/string distinctions.

The tool records `tool_commit == release_commit`; this is enforced equality, not merely
informational metadata. A dirty tree, branch checkout, mismatched tool commit, outside-root
module, changed policy file, `sys.flags.no_site != 1`, unexpected preloaded module, or
unexpected `sys.path` entry blocks. A disposable-venv regression fixture installs an
executable `.pth` sentinel and proves the supported `-I -S -B` entrypoint cannot execute it;
negative tests omit `-S` and demonstrate the sentinel would be detected. The disposable
worktree is removed only after evidence is preserved.

### 1.3 Locked declared action and installer graph

Every remote `uses:` in released reusable workflows and composite actions must use a
40-character commit SHA. The lock manifest records each owner/repository/path/commit,
materialized action-directory tree digest, nested edge, container image digest, and
integrity-checked installer artifact.

The release verifier recursively materializes each remote action or reusable-workflow
node at its locked SHA. For composite actions it parses the fetched `action.yml`, follows
nested `uses:` edges, and repeats until no unvisited node remains. JavaScript and Docker
entrypoints must exist in the locked action tree. The verifier rejects a fetched tree or
nested edge whose digest is absent from the lock manifest.

OCI references have a conservative closed policy across the entire released graph. The
scanner covers workflow `container`/`services`, `docker://`, action `runs.image`, every
Dockerfile `FROM` stage, typed image inputs, JSON/Gemini settings command arrays, and shell
blocks or scripts invoking `docker`, `podman`, `nerdctl`, or `ctr`. `scratch` is the only
non-digest `FROM`; build arguments or variable interpolation in `FROM` are forbidden.
Every other image must contain `@sha256:` and map to one lock entry. Image-bearing inputs
have catalog type `locked_oci_image` and accept only the exact locked literal; concatenated,
environment-derived, tag-only, or free-form image values fail. Direct container-CLI
invocation is rejected except for catalogued canonical command arrays/blocks whose image
argument is that typed constant. Textual/AST tests cover the current `configure-gemini`
settings generator and the inline Gemini MCP settings, Dockerfiles, and dynamic/literal
`docker run` variants.

The current upstream `google-github-actions/run-gemini-cli` action is not acceptable as a
root pin by itself because its pinned commit still contains mutable nested actions,
container tags, and a `latest` CLI path. `v1.40` therefore vendors a reviewed hardened
derivative under `.github/actions/run-gemini-cli-hardened/`, preserving upstream notice
and license. The derivative removes the unused GCP-auth branch entirely. Its remaining
nested Actions use approved full SHAs, every image uses a digest, and its committed npm
lock fixes every transitive package version and integrity. Installation uses immutable
lockfile mode and exactly Gemini CLI `0.55.1`, whose npm integrity is
`sha512-leEv91V7J3YWhZdXqYIj4nTl0hXl8oNos5aVR0whPCFqVbRvoFPTzaQOHdI2UIT1wGgp+XdCi4qUrFDnUFN7RQ==`.
The central workflows do not pass `vars.GEMINI_CLI_VERSION`, `latest`, or `preview`.
Any other locked third-party composite action with a mutable descendant is likewise
hardened/vendorized or the release is blocked. A credential-bearing runtime installer
must use an exact artifact plus verified integrity or a committed frozen lockfile; an
unlocked package-manager or download command blocks release.

That rule is enforced across Dockerfile `RUN` and remote `ADD`, released workflow/action
shell blocks, referenced scripts, and every materialized composite action. Calls to
`apt`, `apk`, `dnf`, `yum`, `pip`, `npm`, `npx`, `yarn`, `pnpm`, `bun install`, `curl`,
`wget`, PowerShell web clients, or language installers are rejected unless their exact
catalogued form consumes a vendored/frozen lock or an immutable URL/version plus recorded
cryptographic digest and verifies it before execution. Dockerfile remote `ADD` is forbidden;
local `COPY` sources must be in the locked action tree. Tests include mutable Dockerfile
packages/downloads and composite shell/script installers. This does not claim to freeze
arbitrary network responses made by the already verified application at runtime; those
remain in the explicit non-hermetic boundary below.

An internal `jhw7500/automation/.github/actions/...@<SHA>` cannot self-pin to the final
commit. Its SHA must instead be a reviewed ancestor that already contains the finalized
action directory. The verifier requires that SHA to be an ancestor of `v1.40` and requires
the complete pinned action-directory Git tree to be byte-identical at the ancestor and at
the release commit. Any later action edit invalidates the pin and blocks the tag.

This guarantee covers the declarative GitHub Actions/reusable-workflow/container graph and
the explicitly locked CLI installer artifact. Live API responses, model output, runner
base-image evolution, and arbitrary service behavior are not claimed to be reproducible.
No `ratchet:exclude` or equivalent bypass is allowed for a credential-bearing or
write-capable node.

### 1.4 Consumer runtime pin

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

The operation has four deliberately separate evidence classes:

1. Immutable `ApprovalPlan` (canonical JSON, externally approved SHA-256) records the
   release/tool/policy/profile/catalog/lock/actionlint identities and a discriminated entry
   for every repository. All entries record repository/default-branch/base/profile/status
   evidence. Only a publishable `drift` entry additionally records deterministic generated
   tree and commit SHAs, fixed author/committer, message, timestamp/offset, planned
   path/blob/deletion set, preview digest, branch name, and PR metadata. `current` and
   `blocked` entries contain no synthetic generated commit. The generated commit object is
   computed locally but is not yet a remote effect. The plan contains no future remote
   branch/PR/merge IDs and is never mutated after approval.
2. Append-only `EffectJournal` records publish/merge/revert attempts and remote effects.
   Every event contains `root_approval_plan_sha256`, `change_plan_kind`
   (`approval` or `revert`), `change_plan_sha256`, repository, monotonic sequence,
   prior-event digest, timestamp, operation (`branch-created`, `pr-opened`, `merged`,
   `reverted`, or `blocked`), observed remote SHAs/IDs, and outcome. The tool writes each
   event under an exclusive `fcntl.flock` taken on the journal directory's fixed lock file.
   A mutating command holds that lock across the selected repository's complete
   read-validate-remote-effect-append cycle and revalidates the chain and remote state after
   acquisition. It writes a
   mode-0600 same-directory temporary file with `O_CREAT|O_EXCL`, fsyncs it, publishes the
   final sequence/digest filename through no-replace `os.link`, fsyncs the directory, and
   removes the temporary link. It never uses overwrite/`os.replace`, edits ApprovalPlan or
   RevertPlan bytes, or changes an existing event.
3. Immutable terminal merge evidence is always emitted once the merge API reports a remote
   commit. A successful `PublishResult` is derived from the ApprovalPlan plus its verified
   complete terminal journal chain and records the final branch head, PR URL/number, merge
   method, actual merge commit/tree, and evidence digests. If any post-merge assertion
   fails, the tool instead emits a discriminated `FailedMergeResult` with the same approved
   identities, the actual response/parents/tree/first-parent diff, failure reason, and
   journal digest. Neither form can be upgraded or overwritten; each external SHA-256 is
   recorded.
4. A rollback is a new immutable `RevertPlan`/`RevertResult` pair linked to the original
   PublishResult or FailedMergeResult digest. It uses the same journal, branch, PR,
   merge-attestation, and unmanaged-path rules; it is never an ad-hoc inverse commit.

Plan produces only object 1. Publish appends object-2 events but does not emit a final
result. After a merge API effect, merge always appends a terminal event and emits exactly
one object-3 success/failure result; no reported remote merge may be left without immutable
evidence. Rollback produces object 4 through the corresponding plan, publish, and merge
phases. An event/result is accepted only when its change-plan digest equals the externally
supplied digest, its root approval digest is correct, and the complete hash chain verifies.
Resume replays remote read-only state against that chain; it never rewrites prior evidence
or treats an EffectJournal as a new approval plan.

### 1.5 Runtime provenance evidence

Every released reusable workflow begins with an unconditional, least-privileged
`provenance` job (`permissions: {}`, no checkout, no secret). One constant shell step takes
caller `github.workflow_ref` and `github.workflow_sha` through two exact `env:` scalars and
takes the called job object only through `JOB_JSON: ${{ toJSON(job) }}`. A release-owned
stdlib-only Python parser is embedded literally in that step's quoted heredoc and reads
that environment; the job needs neither checkout nor a repository-local action. Static
tests hash the exact embedded parser bytes across all reusable workflows. The allowed raw
job-context keys are closed to
`status`, `check_run_id`, `container`, `services`, `workflow_ref`,
`workflow_repository`, `workflow_file_path`, and `workflow_sha`. The four `workflow_*`
keys are mandatory; the other four are optional GitHub platform fields. The parser validates
the documented type/size of every present field, rejects unknown keys, then discards all
non-identity fields. It validates identity length/character/40-hex SHA constraints and emits
one canonical JSON object containing only the two caller and four called identity values.
It never logs raw `JOB_JSON`.

Expressions are forbidden directly inside `run:` and are allowed only in those three exact
`env:` values. The constant shell invokes the embedded parser with
`python3 -I -S -B` and a single-quoted heredoc; Python reads the three environment fields as
data. There is no `eval`, command substitution, dynamic shell construction, or ref placed
in shell source. All remaining jobs depend on the provenance job. The catalog and
release-owned static tests require this exact
job, env expression ASTs, parser digest, constant shell, and no-payload/no-secret
structure; caller/profile overrides are forbidden. Canary validation retrieves the log
through the Actions API and requires `job.workflow_repository == jhw7500/automation`,
`job.workflow_sha == verified_release_commit`, the expected reusable-workflow path, and a
caller SHA whose workflow file bytes match the approved rendered caller. This proves the
actual run, not merely the default branch after the fact, used the intended caller and
immutable reusable revision. Provenance contains no token, full context dump, or
attacker-controlled event payload.

Pinned actionlint 1.7.12 accepts `toJSON(job)` but does not yet model direct
`job.workflow_*` properties. The exact-object parser therefore provides runtime validation
without a schema exception. Actionlint must report zero diagnostics for the provenance job;
there is no provenance allowlist, regex ignore, or compatibility suppression. A negative
fixture using direct `${{ job.workflow_sha }}` proves actionlint would fail and the catalog
also rejects that noncanonical form.

## 2. Single Canonical Catalog

`examples/baseline-workflows/.github/` becomes the only canonical managed tree. The
duplicate top-level `workflows/` and `workflow-config.yml` are removed after all code,
tests, and documentation use the canonical tree.

A single typed machine-readable catalog, stored beside that tree, declares every managed
path. Its closed entry kinds are:

- `caller`: `presence` (`required` or `optional`), canonical YAML path, central target,
  config `enablement_key`, exact per-job permission map, exact-trigger policy, declared
  input/secret value schema, and the only profile-dependent substitutions;
- `config`: `.github/workflow-config.yml`, one bootstrap template, schema, and an
  existing-file mutation allowlist limited to `automation_ref` and `automation_commit`;
- `retired`: a path with `removal_policy: delete` and no canonical YAML.

The active/optional/retired path lists and permission policy are not duplicated in Python,
shell, tests, or fleet JSON. Canonical caller YAML must conform to the catalog's explicit
permission and mapping policy; editing both is a reviewed release change, never an ambient
consumer exception. Documentation such as `.github/README.md` is not fleet-managed.

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
file present without being profiled is drift and the renderer proposes its deletion;
it is never silently accepted.

### 2.3 Retired workflow

`bump-automation-ref.yml` is removed from the **active caller set**, its canonical YAML
is deleted, and consumer copies are removed. It cannot safely update input and secret
mappings when a reusable contract changes, and its GitHub App token path otherwise
requires high workflow-write permission. Future release upgrades are performed only
through the verified fleet renderer.

The typed catalog retains exactly one `retired` entry for the path so the renderer can
propose deletion and the auditor can reject reintroduction.

### 2.4 Catalog completeness invariants

Tests fail unless:

- every `caller` entry has exactly one canonical YAML and every canonical workflow YAML
  has exactly one `caller` entry;
- every `retired` entry has zero canonical files and is absent from the active caller set;
- the single `config` entry has exactly one canonical bootstrap template and its only
  mutable existing-file keys are `automation_ref` and `automation_commit`;
- each caller contains exactly one expected central reusable-workflow job;
- every central target exists in the release bundle;
- required and optional caller sets are disjoint;
- catalog permission maps, value schemas, and canonical callers agree exactly;
- no managed path uses both `.yml` and `.yaml` variants.

## 3. Declarative Fleet Profiles

`scripts/workflow-config.json` remains the fleet inventory but gains an explicit schema
version, a closed operation policy, and per-repository policy. It no longer treats current
file presence as intent. The release-level `workflow-standardization` operation declares
`secret_writes: deny`; repository entries cannot override it.

Each repository entry declares:

```json
{
  "profile": "common-ai-v1",
  "bootstrap_allowed": false,
  "optional_workflows": ["opencode.yml", "opencode-auto-review.yml"],
  "gemini": {
    "model_auth": "gemini_api_key",
    "model_secret": "GEMINI_API_KEY",
    "repo_write_auth": "github_app"
  }
}
```

`profile: common-ai-v1` means the required catalog is mandatory. Only
`cts-email-mcp-server` and `wpa-supplicant` declare `bootstrap_allowed: true`; this is an
authorization policy, not inferred or persisted lifecycle state. The other seventeen
declare false. A future opt-out must be an explicit reviewed configuration change, not an
empty workflow directory.

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

Gemini model authentication and repository-write authentication are separate policy axes.
All 19 profiles select `model_auth: gemini_api_key`. Every Gemini caller passes the exact
literal `gemini_auth_mode: api-key` and maps only
`GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}` for model authentication.

The `v1.40` central Gemini workflows accept only that mode. They remove the
`GOOGLE_API_KEY` reusable secret, never read or forward `GOOGLE_API_KEY`, and never read or
forward ambient `GCP_WIF_PROVIDER`, `GOOGLE_CLOUD_LOCATION`, `GOOGLE_CLOUD_PROJECT`,
`SERVICE_ACCOUNT_EMAIL`, `GOOGLE_GENAI_USE_VERTEXAI`, or `GOOGLE_GENAI_USE_GCA`. Their
Gemini jobs omit `id-token` permission entirely and pass none of the GCP, Vertex AI, or
Code Assist inputs to the hardened action. A future non-API-key mode requires a new typed
profile, explicit central input contract, threat review, tests, and immutable release.

Caller-controlled `GEMINI_CLI_VERSION` is also ignored; the hardened action owns the exact
version and integrity declared in the release lock. Model selection and non-auth runtime
settings remain unchanged where they do not create another authentication path.

Repository-write authentication is a second closed profile axis:
`repo_write_auth: github_app|github_token`. The following eleven repositories use
`github_app` because the current approved inventory contains both `APP_ID` and
`APP_PRIVATE_KEY`:

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

The other eight repositories use `github_token`: `wlan-driver`, `wlan-opc`,
`wpa-supplicant`, `pim-check`, `cts-email-mcp-server`, `cts-ta-mcp-server`,
`cts-ta-webapp`, and `claude-config`. Ambient addition or removal of a secret or variable
never changes the rendered mapping:

- a configured credential missing from inventory blocks the repository;
- an unconfigured credential present in inventory is ignored;
- `github_app` requires caller `app_id: ${{ vars.APP_ID }}` plus
  `APP_PRIVATE_KEY: ${{ secrets.APP_PRIVATE_KEY }}`;
- `github_token` passes neither App field, and the central authentication step selects the
  exact built-in `${{ github.token }}` without reading an App variable or secret.

The new central release removes the legacy implicit `vars.APP_ID` fallback and adds an
explicit required string input `repo_write_auth`. Its only literals are `github_app` and
`github_token`; the caller profile fixes the value. For App mode, the hardened setup action
mints one same-repository installation token. For built-in mode it returns exactly
`${{ github.token }}` and does not call the App-token action.

Both modes share the same caller reusable-invocation and central repository-write **job
permission ceiling**: exactly `contents: read`, `issues: write`, and
`pull-requests: write`, with no `id-token`, actions, workflow, packages, administration, or
cross-repository permission. Provenance, preparation, and disabled-check jobs retain their
stricter catalogued per-job maps rather than inheriting that ceiling. The selected token may
flow only to the locked Gemini action's `github_token` input, its exact `GITHUB_TOKEN`
environment field, the digest-pinned GitHub MCP container's
`GITHUB_PERSONAL_ACCESS_TOKEN`, and the fixed issue/PR comment/review steps. It may not
enter shell command text, artifact/cache output, telemetry, full context, or any other
step. Static taint/AST tests enumerate those sinks, require no App-token mint in
`github_token` mode, require the least-privileged App action inputs in `github_app` mode,
and reject every additional token consumer. Model authentication remains solely
`GEMINI_API_KEY` in both modes.

## 4. Bootstrap Policy for the Two Caller-Free Repositories

`cts-email-mcp-server` and `wpa-supplicant` are explicit bootstrap targets. Normal plan
emits `status: blocked, reason_code: bootstrap_required` only when the reviewed profile has
`bootstrap_allowed: true`, the config is absent, and zero active managed callers exist.
A partial managed tree is `blocked/inconsistent_managed_state`; a non-bootstrap profile
with missing config is `blocked/missing_required_config`. A missing individual caller in
an otherwise managed repository is ordinary drift. The tool does not classify absence as
skipped, silently create files, or persist a mutable "bootstrap state."

Bootstrap has a read-only preview and a separately confirmed publish form. Both require:

- an explicit `--bootstrap-repo <name>` argument;
- exactly one selected repository per invocation;
- the declared repository profile and required secret-name prerequisites;
- the same verified release/tool context as normal rollout.

`workflows plan --bootstrap-repo <name>` renders an exact temporary diff and
content-addressed ApprovalPlan without a remote write. After reviewing that diff, the operator
records the printed external digest. `workflows publish --bootstrap-repo <name>
--approval-plan <path> --plan-sha256 <approved-64-hex-digest> --effect-journal <dir>
--confirm` requires the exact
approved plan bytes, preview bundle, release/profile/catalog hashes, and base SHA to
remain unchanged, then opens one independent pull request. The workflows subcommand has
no secret-sync or secret-refresh options.

Bootstrap creates the required callers and a fail-closed config. Every required workflow
is explicitly disabled; enablement is a later reviewed repository config change:

```yaml
automation_ref: v1.40
automation_commit: <verified-commit>
review:
  auto: false
workflows:
  claude:
    enabled: false
  claude-code-review:
    enabled: false
  gemini-auto-review:
    enabled: false
  gemini-dispatch:
    enabled: false
  gemini-invoke:
    enabled: false
  gemini-review:
    enabled: false
  gemini-scheduled-triage:
    enabled: false
  gemini-triage:
    enabled: false
```

Catalog completeness tests require this bootstrap template to disable every distinct
`enablement_key` reached by a required caller (the manual issue/PR wrappers share
`gemini-triage`/`gemini-review`).
Normal plan always treats config and required callers as mandatory. After bootstrap, one
missing caller with a valid config is ordinary drift and normal plan restores it. Complete
loss of both config and all active callers may use the same explicit single-repository
bootstrap plan/publish path again. Partial caller loss with a missing config remains
`blocked/inconsistent_managed_state`: the renderer cannot reconstruct project-owned
enablement values. The operator must first restore the exact last-known-good config bytes
through a separate reviewed repository PR (from verified default-branch history or a
preserved PublishResult), after which normal plan may restore callers. The fleet tool never
guesses enablement state or overwrites the config wholesale.

## 5. Deterministic Renderer

For each repository the tagged renderer performs the following steps entirely in memory
or in a preview tree before writing the managed clone:

1. Prove the clean detached execution context and all release-bundle digests.
2. Load and validate the typed catalog, operation policy, and repository profile.
3. Resolve and verify the immutable release and recursive dependency lock.
4. Enumerate secret and variable **names** only; never read values.
5. Check declared authentication prerequisites without deriving policy from inventory.
6. Start every required and profiled optional caller from canonical bytes.
7. Substitute the verified release commit into the expected central `uses:` value.
8. Render the catalog's exact typed `with`/`secrets` mappings, including literal
   `gemini_auth_mode: api-key`, and no ambient authentication inputs.
9. Apply only dependency SHAs and digests present in the verified lock.
10. For the typed `config` entry, insert or replace only `automation_ref` and
    `automation_commit` scalar tokens in an existing file under the fail-closed edit
    grammar below, while preserving every other byte.
11. Propose deletion of typed `retired` paths.
12. Refuse to touch paths outside the typed managed set.
13. Validate the entire planned repository before performing any write.

The renderer is idempotent: applying the same verified bundle, catalog, profile, and
inventory to its own output produces zero changed files.

### 5.1 Consumer-config edit grammar

The vendored loader rejects duplicate mapping keys throughout the consumer config. For
the two managed top-level keys it also rejects aliases, anchors, merge keys, explicit YAML
tags, non-scalar values, multiline scalars, and multiple documents. Existing managed
values must be unquoted plain scalars; an inline comment is allowed and preserved.

Edits are token-span based, not a parse-and-reserialize round trip:

- if both keys exist once, replace only their scalar token bytes;
- if only `automation_ref` exists, insert `automation_commit` on the immediately following
  line;
- if only `automation_commit` exists, insert `automation_ref` immediately before it;
- if neither exists, insert the ordered pair after the optional `---` and leading comment
  block, before the first content key.

Both keys are top-level and always ordered ref then commit. Any other structure blocks
without writing. Tests cover each insertion case, CRLF/LF, inline comments, duplicate
keys, quoted/non-scalar values, anchors, aliases, merge keys, tags, and byte preservation
of all unmanaged content.

### 5.2 Project-owned boundary

Files outside the typed managed path set are hashed before and after preparation. Any
byte change blocks the repository. For each publishable drift entry, the initial proof is
the ApprovalPlan's recorded `base_sha..generated_commit_sha` diff:
it must contain zero changes outside managed paths. The final proof is the attested
`base_sha..actual_merge_commit` exact path/blob set in PublishResult. Final audit evaluates
both effect-bound diffs, not an entire later default branch that may legitimately contain
unrelated project commits.

An out-of-catalog file that calls `jhw7500/automation/.github/workflows` is not silently
treated as project-owned. It is an unmanaged central caller and blocks until the caller
is added to the catalog, renamed to a catalog entry, or explicitly removed through a
reviewed policy change.

## 6. Complete Reusable-Workflow Contract Audit

Contracts are loaded only from the verified release bundle. For every central target the
loader records:

- declared inputs, required inputs, and each `string`/`boolean`/`number` type and default;
- declared and required secrets;
- the catalog's allowed caller expressions and their expected result types.

The static contract checker, not actionlint, is authoritative for remote reusable
workflows. It preserves YAML scalar node types and directly validates:

- literal booleans are YAML booleans, numbers are YAML numbers, and strings are strings;
- an expression is allowed only when it exactly matches the catalog/canonical value
  schema and its declared source type is compatible with the callee input;
- a forwarding expression such as `${{ inputs.force_run }}` agrees with the caller's own
  typed trigger input; opaque alternative expressions are rejected;
- canonical templates themselves satisfy the archived callee contract before consumer
  rendering.

Every rendered and existing managed caller must also satisfy:

- target workflow exists and declares `workflow_call`;
- no unknown input or secret key and every required key is present;
- secret sources are exactly `${{ secrets.<same-name> }}`;
- `app_id`, when configured, is exactly `${{ vars.APP_ID }}`;
- `gemini_auth_mode` is the literal string `api-key` for every Gemini caller;
- the caller uses the verified release commit;
- caller job permissions equal the catalog's explicit per-job permission maps;
- triggers and all non-profile-dependent structure equal canonical output;
- no `secrets: inherit`;
- OpenCode callers contain no `id-token: write` and retain the read-only contents
  ceiling and same-repository caller guard;
- Gemini callers contain no `id-token`, Google API-key, GCP, Vertex AI, Code Assist, or
  caller-selected CLI-version path.

Actionlint remains mandatory for syntax, expressions it can understand, and local
workflow checks, but no gate assumes it downloads or type-checks an external reusable
workflow. Regression tests cover wrong boolean, number, and string literals; incompatible
forwarded expressions; unknown/missing inputs; and wrong secret sources.

## 7. One Writer and One Upgrade Path

The tagged `scripts/rollout_workflow_fleet.py workflows ...` subcommand is the only
supported remote writer for catalog-managed callers and the two managed config tokens, and
the only release-upgrade path. Repository owners may still change project-owned config
settings through ordinary reviewed repository PRs; that is not a fleet render or caller
write.

- `setup-github-workflows.sh` and `sync-secrets.sh` become side-effect-free deprecation
  guards. `--help` prints the exact replacement; every former mutating invocation exits
  with status 2 without reading a secret or changing a repository.
- `bump-automation-ref.yml` is removed from consumers and retained only as a typed retired
  path.
- The `workflows` parser exposes only `plan`, `publish`, `merge`, `revert-plan`, and
  `revert-publish`; it has no
  secret-sync, secret-refresh, secret-value, or generic passthrough option. The release profile and
  ApprovalPlan both require `operation: workflow-standardization` and `secret_writes: deny`.
- Any future fleet secret operation uses a separate first-class command path, separate
  confirmation, and code path that cannot write workflow files. It is not chained into
  this rollout.
- Read-only inventory may call only the repository Actions Secrets **list** GET and
  Actions Variables list GET. The secret endpoint supplies no value; although the variable
  endpoint includes non-secret values, the closed response parser projects only names and
  pagination metadata and discards values without branching or logging. No
  repository/model secret value-bearing local or environment source is consulted. The
  separately scoped operator GitHub authentication credential is used only for the
  allowlisted GitHub API calls and exact `github.com` Git HTTPS transport in Section 7.1.
- Tests replace every secret mutation route/method (PUT, PATCH, POST, or DELETE under a
  secret or variable scope) and `gh secret|variable set/delete` with fail-on-call
  sentinels. They require zero mutation calls in all five commands while separately proving
  paginated, projected-name-only GET is allowed in plan.
- Documentation directs operators to profile changes plus fleet
  plan/publish/merge/revert commands rather than editing or deleting common wrapper files.

The rollout CLI separates target release identity from rollout identity:

```text
workflows plan|publish|merge|revert-plan|revert-publish
  --ref v1.40 --rollout-id common-ai-v1-v140
```

The rollout ID is cosmetic metadata, never a security switch. Branches, commit messages,
PR titles, and evidence objects use it. They do not reuse the old secret-hardening branch
name or claim that repository-owned triggers and permissions were preserved when canonical
replacement intentionally changes them.

### 7.1 Closed child environments and isolated Git transport

The bootstrap inherits **zero** environment variables into project code. The launcher
accepts the GitHub credential only on an already-open file descriptor named by the
`--github-token-fd <integer>` CLI option; the descriptor number is non-secret. No token
environment variable is accepted from the operator. The verified tagged bootstrap/writer
is the trusted credential broker: it retains the bytes only in broker process memory and
creates a fresh one-shot pipe or exact child environment for each allowed use. It builds
all child environments from constants only. The common project/Git environment is exactly:

- `PATH=/usr/bin:/bin`, `LANG=C.UTF-8`, `LC_ALL=C.UTF-8`, `TZ=UTC`;
- a newly created mode-0700 `HOME` and `TMPDIR` outside the release worktree;
- `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=/dev/null`,
  `GIT_TERMINAL_PROMPT=0`, `GIT_ASKPASS=/bin/false`, `SSH_ASKPASS=/bin/false`,
  `GIT_SSH_COMMAND=/bin/false`, `GIT_PAGER=cat`, and `PAGER=cat`.

Project Python receives only that common environment, with no token descriptor, token
scalar, or gh-config path. The exact `/usr/bin/gh` child environment adds only
`GH_TOKEN=<broker credential>`, `GH_HOST=github.com`,
`GH_CONFIG_DIR=<private mode-0700 directory outside HOME/TMPDIR>`,
`GH_PROMPT_DISABLED=1`, `GH_NO_UPDATE_NOTIFIER=1`,
`GH_NO_EXTENSION_UPDATE_NOTIFIER=1`, and `GH_TELEMETRY=false`. The trusted `/usr/bin/git`
transport child and the helper it launches instead receive only
`GITHUB_TOKEN_FD=<fresh one-shot descriptor>` in addition to the Git environment; the
descriptor is passed to Git solely so its verified helper can inherit/read it, and Git is
part of the declared host trust root. Neither receives `GH_TOKEN` or `GH_CONFIG_DIR`. Unit
tests assert exact key/value equality for the Python, Git, gh, and helper environments, not
merely absence of known secret names.

No proxy, Python, shell-startup, Git, package-manager, provider/model, or arbitrary
operator environment variable is inherited. The tool never calls `gh auth login`, never
consults stored host credentials, and never persists the token. Exact `/usr/bin/gh api`
children authenticate from their child-only `GH_TOKEN`, which supports fine-grained PAT,
classic PAT, and OAuth token forms without the `--with-token` classic-PAT ambiguity. They
first probe the viewer identity, every selected repository, and each phase's safe read
endpoint; the expected owner is `jhw7500`. The private `GH_CONFIG_DIR` must remain free of a
credential-bearing file and is recursively deleted on normal exit and signals. Child
stdout/stderr is captured, structurally parsed, and secret-redacted before any diagnostic.
The credential is never placed in argv, Git URL, Git config, project/Git environment, log,
journal, plan, preview, or result. The trusted broker and exact gh child environment are
the two declared in-memory exceptions; this is not an isolation claim against unrelated
same-UID host processes.

The documented fine-grained-token capability ceiling is repository access limited to the
19 configured repositories, with Metadata read, Contents write, Pull requests write,
Workflows write, Actions read, Checks read, Commit statuses read, Secrets read, and
Variables read. Plan uses only the read subset; branch/PR publish needs Contents,
Workflows, and Pull requests write; merge needs Contents write; canary evidence needs
Actions read. A classic token requires only the corresponding `repo` and `workflow`
scopes, but fine-grained is preferred. The controller requests active branch rules through
`GET /repos/{owner}/{repo}/rules/branches/{branch}` (Metadata read), not the
Administration-protected branch-protection endpoint. A 200 response is parsed normally.
The one typed exception is GitHub's exact private-repository feature-unavailable 403 for a
verified `jhw7500`-owned private repository on a plan that cannot support private rules;
that records `active_rules: unavailable_by_plan` rather than pretending the call
succeeded. Any other 401/403, changed error body, owner/visibility/plan mismatch, or partial
response blocks. Repository/PR/check-run/review state is still inspected, and the
exact-head merge API remains the final atomic policy enforcement. Because GitHub exposes no
reliable non-mutating proof of every fine-grained write grant, plan proves all read grants
up front; the first exact approved write fails closed if a declared write grant is absent
and performs no fallback or privilege escalation.

Repository data transport uses `/usr/bin/git` HTTPS with an explicit one-process credential
broker. Git invokes a release-owned, verified helper by absolute path via command-scoped
`-c credential.helper=` and
`-c credential.https://github.com.helper=!<verified-helper>`. The helper receives a
duplicated private descriptor named only by a numeric
`GITHUB_TOKEN_FD` in that helper child's environment, obtains the credential bytes from it,
answers only Git's `get` request
for `protocol=https` and `host=github.com`, emits `username=x-access-token` plus password to
Git's credential pipe, rejects store/erase/other hosts, and never logs. No host/system,
global, or cloned local credential helper is honored.

Every Git command uses `/usr/bin/git --no-optional-locks` plus command-scoped controls:

- `GIT_CONFIG_NOSYSTEM=1`, throwaway `HOME`, `GIT_CONFIG_GLOBAL=/dev/null`, and
  `-c include.path=/dev/null` with all include/includeIf entries rejected after clone;
- `-c core.hooksPath=<verified-empty-directory>`, `-c protocol.file.allow=never`,
  `-c protocol.ext.allow=never`, and only HTTPS GitHub remotes whose owner/repository match
  the fleet profile;
- clone is exactly non-recursive `--no-checkout --no-recurse-submodules`; no submodule
  operation, LFS, sparse external command, pager, editor, clean/smudge/process filter, or
  arbitrary protocol. After clone, repository config and
  `.gitattributes` are parsed; any hook, include, alternate object store, external diff,
  credential, URL rewrite, filter driver, or submodule setting blocks before checkout/add;
- `git -c filter.lfs.smudge= -c filter.lfs.required=false` is not used as a bypass: a
  managed path with a filter attribute or any required filter causes a block.

Fetch/push receive the credential only through that broker. Commit construction uses Git
plumbing, not `git commit`: write the approved preview blobs, construct exactly the planned
tree, then `git commit-tree` with parent=`base_sha`, fixed author/committer identity,
fixed UTF-8 message and fixed author/committer timestamp/offset stored in the
ApprovalPlan. The timestamp is the UTC second when the plan begins, captured once before
rendering and represented as canonical RFC 3339 plus its Git integer/`+0000` form; it does
not depend on per-repository processing time. Plan performs the same pure object
calculation without writing remote state, so it
records both `generated_tree_sha` and deterministic `generated_commit_sha`. Publish requires
both calculations to match, signs neither implicitly, and writes no hooks. Tests run with
malicious system/global/local config, helpers, hooks, filters, includes, URL rewrites,
proxy/env, and a token-only private-repository fixture; only the verified broker path may
authenticate.

## 8. Validation and Failure Semantics

### 8.1 Plan

Plan mode is read-only for GitHub state. It fetches every default branch, renders into a
temporary preview, and collects outcomes for the full fleet. It may write only local
disposable clones, previews, and the requested local ApprovalPlan outside the clean release
worktree.

Required plan gates:

- verified detached execution context, local/remote release identity, and bundle hashes;
- typed profile/catalog and closed operation-policy validation;
- recursive action/container/artifact lock verification;
- paginated secret/variable name prerequisites through the two allowlisted read-only list
  APIs, projecting names without consulting returned variable values;
- repository identity/default branch is writable, not archived/disabled, allows merge
  commits, and its current applicable rules/readability state is recorded;
- authoritative static input-type and secret contract audit;
- exact managed-file comparison;
- retired and unmanaged central-caller checks;
- Gemini API-key-only and no-OIDC/no-GCP assertions;
- YAML parse;
- `git diff --check`;
- mandatory actionlint `1.7.12` using the official Linux AMD64 archive SHA-256
  `8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8`, with recorded
  version and fail-closed process status;
- zero actionlint diagnostics in released/canonical managed workflows and zero new
  diagnostics compared with the untouched project-owned baseline;
- a zero unmanaged-path diff between each publishable drift entry's recorded base and
  generated tree/commit.

Every ApprovalPlan repository entry uses `status: current|drift|blocked` plus a stable
reason code. Only a `drift` entry that passes all gates is publishable and carries
`generated_tree_sha`, `generated_commit_sha`, preview/path/blob/deletion data, and branch/PR
metadata. A `current` entry records the observed managed blobs at its base, while a
`blocked` entry records only the evidence needed for its reason; neither invents a commit
that cannot be published.
`bootstrap_required` is a failure reason, not persistent policy state. Plan exits 0 only
when there is no blocked outcome, exits 3 when it produces a complete ApprovalPlan containing
one or more blocked repositories, and exits 2 when it cannot produce a trustworthy full
ApprovalPlan. It never hides a missing catalog behind `skipped`.

A complete plan emits canonical UTF-8 ApprovalPlan JSON plus a deterministic preview
archive. The ApprovalPlan contains the preview archive SHA-256 and every planned
file/deletion digest; its own SHA-256 is external and is printed as
`APPROVAL_PLAN_SHA256=<64 lowercase hex>`. It is also copied to an operator-controlled,
content-addressed evidence path `<sha256>.json`. The human/controller reviews the exact
ApprovalPlan and preview, then preserves that digest in an approval record independent
of the plan. Publish must receive it through `--plan-sha256`; it never trusts a digest
stored inside the plan and never silently computes-and-accepts a replacement digest.

### 8.2 Publish

The exact normal form is:

```text
workflows publish --approval-plan <path> --plan-sha256 <digest>
  --effect-journal <new-or-existing-plan-bound-dir> --repo <one-name> --confirm
```

Publish requires both `--approval-plan` and the independently preserved
`--plan-sha256 <approved digest>`. Before GitHub access it hashes the exact ApprovalPlan bytes,
requires equality with that external input/content-addressed filename, then verifies every
release, tool, policy, profile, catalog, lock, preview-archive, planned-file, generated
object, and base identity. A missing/malformed digest or mismatch is a parser/gate failure, never an
interactive auto-approval. It then retains `--confirm` and fail-fast behavior. A repository
is fully validated before any push. Immediately before commit/push, the tool refetches the
remote default branch and requires it to equal the recorded base SHA. A mismatch blocks
without pushing and must be replanned.

Each repository receives an independent branch and pull request. The immutable
ApprovalPlan is never changed; remote observations are appended only to the EffectJournal.
A journal directory contains an immutable root binding to exactly one ApprovalPlan digest;
an empty unbound directory is initialized once, while a directory bound to any other plan
or containing a second root blocks. A completed publish records the exact open-PR state but
produces no PublishResult; only an
attested merge can produce that terminal object. Resumption requires the same external plan
digest plus explicit `--repo` selections, validates the journal hash chain and live remote
state, and never replays an already verified effect implicitly.

For a selected repository, publish is the only branch writer and never force-pushes an
unknown branch. The allowed states are:

- remote rollout branch absent and no prior branch effect: create exactly
  `generated_commit_sha`;
- remote branch equals that SHA and one open PR matches the approved base/head/title/body:
  record/reuse the idempotent effect;
- any other branch SHA, disappearance after a recorded effect, multiple/mismatched PR, or a
  closed/merged PR in publish mode: block. An unmerged conflict requires a new
  ApprovalPlan/rollout ID; an exact already-merged PR is recoverable only through merge
  resume below.

Crash reconciliation is closed and evidence-preserving. After taking the journal lock, a
resume may append a missing branch/PR event with `outcome: observed_after_crash` only when
the live SHA and every approved PR field match uniquely. If the exact-head merge API
succeeded but the controller crashed before its terminal event, `workflows merge` refetches
the one recorded PR and merge commit, reruns the complete post-merge attestation, then
appends the missing terminal event and creates the result. No other unjournaled remote
effect is adopted; ambiguity or any mismatch appends `blocked` and stops.

Changes to non-selected repositories after the full-fleet plan do not alter the immutable
plan hash and do not block publishing a selected entry. Each selected entry still requires
its own remote default branch to equal its recorded base immediately before branch creation.
After any first repository publish, stale remaining entries must be individually replanned
before their later publish if their own base changed; the new per-repository ApprovalPlan
references the original fleet-plan digest for traceability.

The workflow-standardization operation structurally has no secret option or secret-write
code path, regardless of rollout ID. Every ApprovalPlan, PublishResult, RevertPlan, and
RevertResult repository entry has `synced_secrets: []`, and any attempt to supply a
secret-related flag is a parser error before GitHub access.

### 8.3 Merge attestation

The same tagged writer exposes the only two typed merge forms:

```text
workflows merge --approval-plan <path> --plan-sha256 <digest> --effect-journal <dir>
  --repo <one-name> --confirm-merge
workflows merge --revert-plan <path> --revert-plan-sha256 <digest> --effect-journal <dir>
  --repo <one-name> --confirm-merge
```

The two flag pairs are mutually exclusive. The parser validates the selected typed plan's
external digest, root ApprovalPlan link, and complete EffectJournal before any API
mutation; one invocation selects exactly one open PR/repository. With an ApprovalPlan it
can emit only PublishResult; with a RevertPlan it can emit only RevertResult. The `merge`
parser has no arbitrary ref/direct-push operation, secret option, generic PR-number
override, merge-method override, or admin-bypass option. UI/manual, auto, queue, and other
merge paths are unsupported and make attestation fail.

The tagged writer permits only GitHub **merge-commit** merges through an exact-head API
operation; squash, rebase, update-branch, auto-merge, queue, and force-push are disabled for
rollout PRs. Immediately before merge it refetches and requires:

- remote PR head and rollout branch both equal the selected plan's
  `generated_commit_sha`;
- PR base ref is the approved default branch and its base SHA still equals the approved
  `base_sha` (no base advance is accepted; replan instead);
- PR changed-path set, blob/deletion set, generated tree, title, and body equal the
  selected ApprovalPlan or RevertPlan exactly; and
- checks/reviews required by repository policy are complete.

The API request supplies the exact expected head SHA. After merge, the controller fetches
the returned merge commit and requires exactly two parents `[base_sha,
generated_commit_sha]`, no octopus/rewrite, and a tree equal to the selected plan's
`generated_tree_sha`.
It recomputes `base_sha..merge_commit` and requires zero unmanaged paths and exact approved
managed blobs/deletions. Only then does an ApprovalPlan merge append `merged` and emit
PublishResult, or a RevertPlan merge append `reverted` and emit RevertResult. If the API
reports a merge but any postcondition differs, the controller appends `blocked`, emits the
immutable FailedMergeResult before returning failure, quarantines that repository, stops
the fleet, and triggers the reviewed recovery policy.

Final audit validates every repository's ApprovalPlan digest, complete EffectJournal hash
chain, approved head, PR identity, merge method/commit/tree, and exact managed-only diff.
A repository can be `current` by content without historical rollout evidence only if it
was already current in the original ApprovalPlan; every drift/bootstrap repository requires
a verified PublishResult. Thus an unrelated commit on a later default branch cannot hide an
unapproved file in the rollout PR.

#### 8.3.1 Attested rollback

Rollback uses the same tagged writer, never GitHub's UI revert button:

```text
workflows revert-plan --publish-result <path> --result-sha256 <digest> --repo <one-name>
workflows revert-plan --failed-merge-result <path> --result-sha256 <digest>
  --repo <one-name>
workflows revert-publish --revert-plan <path> --revert-plan-sha256 <digest>
  --effect-journal <dir> --repo <one-name> --confirm
```

The two `revert-plan` source flags are mutually exclusive. From PublishResult, revert-plan
refetches the current default base and constructs the exact inverse of only the original
verified managed blob/deletion set. From FailedMergeResult, it first validates the actual
merge commit and its first-parent diff independently of the failed expected assertion; it
may construct an inverse only when the actual changed-path set equals the selected
ApprovalPlan's approved managed path set and every first-parent before-image equals the
approved base blob/deletion state. It blocks if any affected managed path has changed since
the recorded merge or inverse application would touch a later independent change. It emits
a new externally reviewed
content-addressed RevertPlan with deterministic tree/commit, branch, PR metadata, and
exactly one of `reverts_publish_result_sha256` or
`reverts_failed_merge_result_sha256`.

If a FailedMergeResult contains an unmanaged/unknown path, an extra or missing managed
path, or an unverified before-image, the fleet writer must not silently reverse those
bytes. It records `quarantine_requires_break_glass` and performs no mutation. The stop
condition then requires repository-owner incident review
and a separate manually reviewed recovery PR whose exact first-parent restoration is
preserved with the FailedMergeResult; normal fleet rollout cannot resume until a fresh plan
proves the repository managed tree and all project-owned bytes are coherent. This narrowly
declared break-glass exception is safer than granting the workflow writer authority over
unmanaged files.

`revert-publish` opens the revert PR under the same absent/exact-head branch rules and
appends effects to the same hash-chained journal. Its PR must then use the typed
`workflows merge --revert-plan ... --revert-plan-sha256 ... --confirm-merge` form; merge
attestation emits immutable RevertResult and appends `reverted`. Multi-repository recovery
plans and merges in reverse original merge order, one repository at a time, refetching and
replanning each new base. All five command parsers reject secret/mutation passthroughs.
Tests cover intervening managed/unmanaged changes, wrong result/plan digest, forward-order
attempt, UI/manual merge, partial journal, and exact successful recovery.

### 8.4 Tool failure

Dirty or mismatched release tooling, actionlint absence, non-zero execution without
parseable diagnostics, malformed GitHub metadata, nested dependency-lock drift,
release/tag mismatch, stale default branch, invalid YAML, incompatible input types,
ambient Gemini authentication paths, incomplete inventory, or a catalog inconsistency is
blocked rather than treated as an empty clean result.

## 9. Test Strategy

Implementation follows test-driven development. Each behavior is first demonstrated by
a failing regression test.

### Release and catalog

- local/remote tag mismatch, non-detached HEAD, dirty/untracked release tree, tool-commit
  mismatch, and outside-root project/non-stdlib import each fail;
- the supported process has `no_site=1`, only verified roots plus stdlib on `sys.path`, and
  loads vendored pure-Python PyYAML 6.0.3; a disposable executable `.pth` sentinel cannot
  run under `-I -S -B`;
- sentinel local provider/model credentials (including `ZHIPU_API_KEY`) are absent from
  every project/child environment and every log, preview, and evidence object; GitHub auth
  appears only in the exact gh child's transient `GH_TOKEN` or one-shot Git-helper pipe,
  never in the empty ephemeral gh config, and remains redacted;
- the release-owned YAML 1.2 loader retains `on` as a key string, preserves true
  boolean/number/string nodes, rejects duplicate/unsafe tags, and never constructs objects;
- catalog, fleet-profile, release-manifest, or recursive lock digest mismatch fails;
- a root or nested action tag/branch, mutable container tag, missing fetched tree, or
  unlocked reusable-workflow edge fails;
- Dockerfile `FROM`, workflow/service/action images, typed image inputs, JSON/settings
  arrays, and container-CLI shell blocks accept only lock-mapped digests; dynamic and
  untyped image paths fail;
- an internal action pin that is not an ancestor or whose full directory tree differs at
  release fails;
- the hardened Gemini action uses only locked nested dependencies and the exact CLI
  version/integrity; `latest`, `preview`, and caller version input fail;
- every typed caller/config/retired entry satisfies its class-specific cardinality and no
  unregistered managed YAML exists;
- missing required and profiled optional files are drift;
- unprofiled optional and retired files produce their declared deletion drift.

### Rendering and contracts

- required caller output equals canonical rendering;
- `github_app` and `github_token` repository-write profiles differ only by approved App
  mappings versus exact built-in-token fallback; model auth stays API-key-only;
- adding any ambient `GOOGLE_API_KEY`, App, GCP/WIF, Vertex AI, Code Assist, or CLI-version
  variable does not expand a profile;
- every Gemini caller and central Gemini job remains no-OIDC and API-key-only;
- partial configured App inventory blocks;
- `github_app` mode mints only through the pinned action with three permissions and
  `github_token` mode never mints; both token flows reach only the enumerated sinks;
- missing/unknown inputs and wrong boolean, number, string, or forwarded-expression types
  fail in the static checker even when actionlint returns 0;
- missing, unknown, inherited, and wrong-source secrets fail;
- caller permissions, trigger, input, and same-repository guard drift fail;
- out-of-catalog central callers fail;
- config insertion/replacement follows the fixed byte-span grammar; duplicate keys,
  quotes/non-scalars, aliases, anchors, merges, tags, and multiple documents block;
- only the two allowed existing-config scalar tokens change;
- every canonical reusable workflow has the exact no-secret/no-permission provenance job
  and all execution jobs depend on it;
- provenance uses only the exact two caller `env:` expressions plus `toJSON(job)`, never
  an expression in `run:`; the parser requires four identity keys, accepts/type-checks
  only the four documented optional raw keys, and emits only six canonical identity values;
  an unknown key, missing identity, direct `job.workflow_*`, or raw-object logging fails;
- every publishable drift entry's `base_sha..generated_commit_sha` contains zero
  project-owned changes and its tree/path set equals the ApprovalPlan;
- retired bump files are deleted;
- a second render produces no changes;
- no file is written when any planned YAML is invalid.

### Orchestration

- plan performs no remote write and reports all repositories with stable status/reason;
- only an explicitly bootstrap-allowed, fully caller-free/config-free repository yields
  `blocked/bootstrap_required`; partial and unauthorized absence use distinct blocks;
- complete managed-tree loss can re-enter bootstrap, one missing caller with valid config
  is normal drift, and config loss with partial callers remains blocked until a reviewed
  exact last-known-good config restoration;
- bootstrap plan is read-only and bootstrap publish requires one repository, an external
  `--plan-sha256` matching the reviewed content-addressed ApprovalPlan, one plan-bound
  EffectJournal directory, and confirmation;
- missing, malformed, plan-self-supplied, or mismatched plan digest blocks before
  GitHub access; exact external digest plus unchanged preview succeeds;
- publish rejects an omitted journal, a journal with multiple/mismatched root bindings, or
  a non-directory/symlink evidence path;
- publish requires actionlint and fails on tool execution errors;
- project/child environments equal their exact closed schemas; hostile Git config,
  credential helper, hook, filter, protocol, URL rewrite, proxy, and provider variables
  cannot execute or leak, while the fd broker clones/fetches/pushes a private token-only
  fixture;
- gh accepts a fine-grained token only in its exact child environment, leaves no stored
  credential, and rejects wrong viewer/repository access; active-rules tests distinguish
  public `200 []`, the exact verified private-plan feature-unavailable 403, and a true
  permission/authentication 403 that must block;
- archived/disabled/non-writable repositories or `allow_merge_commit: false` block at plan;
- deterministic plan/publish object construction yields the same tree and commit SHA;
- ApprovalPlan bytes never mutate; EffectJournal events use exclusive revalidation,
  create-only atomic publication, and a hash chain; concurrent append, existing-filename,
  broken-chain, or mismatched-plan tests block, and only attested merge emits a separate
  immutable PublishResult/RevertResult while every post-merge mismatch emits an immutable
  FailedMergeResult;
- publish refetches and blocks a stale selected base or stale preview while unrelated
  non-selected repository advances do not invalidate the full-fleet plan;
- branch absent/create and exact-head/open-PR reuse are idempotent; unknown head,
  mismatched/closed PR, and any force-push path block;
- crash after exact branch creation, PR creation, or merge is reconciled only from a unique
  byte/SHA-identical remote effect; missing-after-journal and ambiguous effects block;
- merge requires one explicit repository, exactly one typed ApprovalPlan/RevertPlan flag
  pair and external digest, a verified journal, and `--confirm-merge`;
  UI/manual/admin/generic PR overrides and mixed plan flags block;
- merge attestation requires exact PR head/path/blob set, merge-commit two-parent tree,
  and zero unmanaged actual-merge diff; squash/rebase/update/queue and base advance block;
- publish is fail-fast and resume is explicit;
- rollout branch and PR text use rollout ID as metadata rather than a security switch;
- secret-related flags are parser errors in all five commands, projected-name-only
  inventory GET remains allowed only in plan prerequisite checks, and all secret/variable
  mutation sentinels remain untouched in every command;
- allowlisted paginated secret/variable list GET succeeds and projects only names, while
  every mutation method/CLI sentinel fails the test if called;
- RevertPlan/Result is linked to a PublishResult or FailedMergeResult, contains only a
  conflict-free exact inverse managed patch, publishes/merges through the same
  attestations, and rejects wrong order, intervening changes, manual/UI action, or broken
  journal; a failed merge with any unmanaged actual effect is quarantined for documented
  repository-owner break-glass rather than auto-reverted;
- canary evidence rejects a caller SHA with wrong bytes or a called workflow provenance
  SHA other than the verified release;
- all ApprovalPlan/PublishResult/FailedMergeResult/RevertPlan/RevertResult entries contain empty
  `synced_secrets`.

## 10. Rollout Sequence and Stop Conditions

### Gate 1: automation implementation and release

1. Implement in isolated worktrees and use three bounded, non-squashed PRs so an internal
   action can be pinned to a real ancestor on `main`:
   - action payload: finalized hardened internal action trees, upstream notices, locked
     nested Actions/images, and exact Gemini CLI installer integrity;
   - runtime workflows: central workflows pinned to the merged action-payload commit,
     API-key-only Gemini mode, explicit App mode, dependency lock, and release verifier;
   - delivery tooling: typed catalog, profiles, static contract checker,
     renderer/auditor, writer retirement, and orchestration tests.
2. After the action-payload PR merges, record its exact mainline merge commit. The next
   PR pins that commit; squash or history rewriting that would destroy ancestry is not
   allowed.
3. Run the full unit, YAML, archived-bundle, recursive-lock, static-contract, actionlint,
   and diff gates after each bounded PR.
4. Merge in dependency order. The later PRs must not modify the pinned internal action
   trees. Fetch the final exact merge commit and rerun all pre-release gates from a clean
   detached checkout of that commit.
5. Create annotated local `v1.40` only at the final commit. Run verifier `prepush`, which
   requires the remote tag to still be absent; any unexpected remote tag stops release.
6. Push the tag once, create a new clean detached checkout, and run verifier `remote` to
   require local/remote equality. Only that evidence unlocks rollout.

Do not create a consumer PR before the remote release verifier succeeds.

### Gate 2: full read-only fleet plan

Run normal read-only plan for all 19 profiles. The expected ApprovalPlan is exactly 17
`status: drift`, exactly two `status: blocked, reason_code: bootstrap_required`
(`cts-email-mcp-server` and `wpa-supplicant`), zero other blocked reasons, and no skip.
Exit 3 is expected for this discovery gate. The controller may advance only after parsing
that exact complete ApprovalPlan, reviewing its preview, and preserving the printed
`APPROVAL_PLAN_SHA256` outside the ApprovalPlan; any other count, reason, exit code, incomplete
result, or secret/variable mutation call stops. This is the sole gate-specific exception to the
normal "any blocked stops" rule. It permits canary publish of explicitly selected
non-blocked entries from that complete ApprovalPlan; blocked entries remain ineligible for
the normal publish path.

### Gate 3: merged behavioral and bootstrap canaries

A workflow-changing pull request is not treated as live evidence: several relevant events
load the workflow from the base/default branch. Each canary therefore follows
**publish reviewed plan → merge managed PR → verify default-branch bytes → create a separate
harmless same-repository PR → trigger → verify runtime provenance**. The managed canary PR
remains merged on success; only the harmless test PR/branch is removed.

1. `wlan-package`: publish the selected non-blocked entry using its externally approved
   Gate 2 plan digest and merge it. Verify the default branch contains the exact rendered
   OpenCode callers. Create a harmless same-repository PR based after that merge; observe
   automatic OpenCode review, then post `/opencode` as a normal PR `issue_comment`.
   Because the rollout caller is already on the default branch, both events exercise it.
   Next invoke manual Gemini PR review for the harmless PR. Require the `github_app` path
   to consume the mapped private key, call the pinned App-token action with exactly the
   three allowed permissions, avoid the built-in fallback, and pass the resulting token
   only to the approved Gemini/MCP/comment sinks. Finally run one representative manual
   Claude invocation to validate its newly pinned action graph. For every run require
   caller file bytes at `github.workflow_sha` to match the approved caller and called
   workflow provenance SHA to equal the verified `v1.40` commit.
2. `wlan-driver`: publish and merge its approved API-key-only entry, create a harmless
   same-repository PR, and invoke the manual Gemini PR-review `workflow_dispatch` from the
   default branch for that PR. Require provenance equality, a successful API-key model
   path, no App-token mint, no `APP_PRIVATE_KEY` mapping, exact built-in
   `${{ github.token }}` fallback to only the approved sinks, and no OIDC/GCP/Vertex/Code
   Assist path.
3. `cts-email-mcp-server`: approve a separate explicit bootstrap plan/digest, publish and
   merge the bootstrap PR, and verify the default-branch caller/config bytes. Invoke the
   newly available `gemini-scheduled-triage` `workflow_dispatch`; require the central
   provenance job to run from `v1.40` and the execution job to skip because the bootstrap
   config explicitly disables it. No model or App credential may be consumed.

The controller records run IDs, events, caller/called refs and SHAs, conclusions, and
review/comment evidence before removing harmless test PRs and branches. If any live or
provenance assertion fails, fleet rollout stops and all Gate 3 managed consumer merges are
reverted in reverse order through the attested `revert-plan`/`revert-publish`/`merge`
flow before retry; the immutable automation tag is never moved.

### Gate 4: fleet publish

The three successful managed canary PRs are already part of the target state. Publish
independent PRs for the remaining sixteen repositories from their externally approved,
unchanged ApprovalPlans. `wpa-supplicant` receives its own explicit bootstrap plan and
confirmed publish; all of its common callers remain disabled until a separate reviewed
enablement change.

### Gate 5: final audit

The final read-only plan must report:

- `current=19`;
- `skipped=0`;
- `blocked=0`;
- zero unresolved FailedMergeResult or quarantine;
- `synced_secrets=[]` for every repository;
- all consumer callers pinned to the verified release commit;
- each PublishResult links its immutable ApprovalPlan and a complete EffectJournal chain,
  and its planned and actual-merge diffs change zero unmanaged paths.

Except for the exact Gate 2 discovery condition above, any release/tool verification,
dependency-lock, catalog/profile, contract/type, Gemini-auth, YAML, actionlint,
default-branch freshness, unmanaged-path, secret/variable-mutation, or live-canary failure stops
the next stage.

## 11. Recovery

- Starting automation base: `2254f13aab44585c78954d20749f4fb677a8c2f1`.
- Initial design commit: `7ea29d2e5d5c7b1673cce378c40c8c24deb3df81`.
- Development remains isolated on `codex/standardize-common-workflows`.
- Before merge, delete the isolated worktree and branch to abandon the change.
- Before `v1.40` publication, the three automation PRs can be reverted in reverse order.
  After publication, never move/delete the tag: correct automation behavior through new
  reviewed commits and a new immutable tag.
- Consumer rollback uses only `workflows revert-plan` → reviewed RevertPlan →
  `revert-publish` → `merge`, one repository in reverse merge order. UI/manual revert is
  not accepted evidence.
- A bootstrap RevertPlan removes only the managed `.github` paths that its PublishResult
  added and blocks if any were independently modified.
- Restoring `bump-automation-ref.yml` requires a separate reviewed policy/release change;
  rollback does not silently revive the retired writer.
- No secret value is changed, so secret-value rollback is not part of this rollout.

## 12. Acceptance Criteria

- `v1.40` local and remote tags resolve to the reviewed final merge commit.
- Rollout executes only from a clean detached checkout whose tool commit equals that
  release commit; mismatch, dirty tree, and outside-root import tests pass.
- The archived release-bundle verifier passes and all recorded
  release-manifest/profile/catalog/lock digests match.
- Every root and nested remote action/reusable-workflow reference is an approved full SHA;
  every remote image is digest-pinned; internal ancestor action trees equal release trees.
- The hardened Gemini action uses exactly CLI `0.55.1` with the recorded npm integrity and
  no caller-controlled/latest/preview path.
- The typed catalog is complete, class-consistent, unique, and digest-recorded.
- All 19 repository profiles are explicit; no management, optional, or authentication
  state is inferred from ambient inventory or file presence.
- All 19 repositories contain the required callers and exactly their profiled optional
  callers.
- Every central caller uses the verified release commit and satisfies authoritative input
  type, secret, permission, trigger, and canonical-expression contracts.
- Gemini is API-key-only with no OIDC/GCP/Vertex/Code Assist/Google API-key path; GitHub
  App mode remains separately explicit and least-privileged.
- No managed caller uses `secrets: inherit` or an undeclared ambient credential.
- `bump-automation-ref.yml` is absent from all consumers.
- Every publishable ApprovalPlan drift entry's `base_sha..generated_commit_sha` and every
  PublishResult `base_sha..actual_merge_commit` contains zero unmanaged-path changes and
  the exact approved managed blobs/deletions.
- Mandatory actionlint has zero managed-workflow diagnostics, and all automated tests
  pass with recorded versions/evidence.
- Publish requires an externally approved `--plan-sha256`, consumes the byte-identical
  content-addressed ApprovalPlan/preview, and the remote default branch still equals its
  planned base SHA for each repository.
- `wlan-package` automatic/manual OpenCode, GitHub-App Gemini, and representative Claude
  canaries succeed with verified provenance and token sinks.
- Built-in-token API-key-only and fail-closed bootstrap canaries satisfy their declared
  profiles.
- Final fleet state is `current=19`, `skipped=0`, `blocked=0`.
- The operation policy is `secret_writes: deny`; allowlisted list responses are projected
  to names only, all secret/variable-mutation sentinels remain unused, and every
  ApprovalPlan/PublishResult/FailedMergeResult/RevertPlan/RevertResult entry has
  `synced_secrets: []`.
