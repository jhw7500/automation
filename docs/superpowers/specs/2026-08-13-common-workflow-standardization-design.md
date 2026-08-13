# Common Workflow Standardization Design

Date: 2026-08-13
Status: revised after three independent security and architecture review rounds

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
8. Provide one authoritative workflow-standardization CLI form for read-only plan and
   confirmed publish across the fleet, with an explicit one-repository bootstrap form.
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

The supported operator path first resolves and verifies the annotated remote tag, creates
one disposable **detached** Git worktree at that exact commit, and invokes the tagged tool
with isolated, no-site, no-bytecode Python mode (`/usr/bin/python3 -I -S -B`). The
manifest and preview are written outside that worktree. Executing a renderer copied from another checkout is unsupported and fails.

The bootstrap defines the host trust root explicitly: a trusted `/usr/bin/python3`
CPython 3.10 runtime/standard library, `/usr/bin/git`, `/usr/bin/gh`, the operating system,
and TLS. It requires `sys.implementation.name == cpython` and
`sys.version_info[:2] == (3, 10)` and records the full versions and executable digests;
release code and parsers are not taken from the host. Before project import it constructs
a minimal child environment, removes model/provider credentials such as
`ZHIPU_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`, Claude tokens, and all unallowlisted
secret-like variables, and fixes command paths to `/usr/bin:/bin`. The operator's GitHub
authentication credential is the sole necessary credential exception: it is passed only
to `/usr/bin/gh`, redacted from diagnostics, and never written to a preview or manifest. The
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

The rollout manifest records:

- release ref and resolved release commit;
- renderer/tool commit, required to equal the release commit;
- release-manifest SHA-256;
- catalog SHA-256;
- fleet-profile configuration SHA-256;
- recursive action/container/runtime-artifact lock SHA-256;
- actionlint version and verified binary SHA-256;
- operation kind and secret-write policy;
- per-repository base and generated head commits.

### 1.5 Runtime provenance evidence

Every released reusable workflow begins with an unconditional, least-privileged
`provenance` job (`permissions: {}`, no checkout, no secret) that records only:

- caller `github.workflow_ref` and `github.workflow_sha`;
- called `job.workflow_ref`, `job.workflow_repository`, `job.workflow_file_path`, and
  `job.workflow_sha`.

All remaining jobs depend on that job. The catalog and release-owned static tests require
this exact job, property spellings, expression ASTs, and no-payload/no-secret structure;
caller/profile overrides are forbidden. Canary validation retrieves the log through the
Actions API and requires `job.workflow_repository == jhw7500/automation`,
`job.workflow_sha == verified_release_commit`, the expected reusable-workflow path, and a
caller SHA whose workflow file bytes match the approved rendered caller. This proves the
actual run, not merely the default branch after the fact, used the intended caller and
immutable reusable revision. Provenance contains no token, full context dump, or
attacker-controlled event payload.

GitHub.com documents these four `job.workflow_*` properties, but pinned actionlint 1.7.12
predates them and emits one schema false-positive for each exact property. The release
owns a structured compatibility allowlist containing only tuples of:

- diagnostic code/category `expression` with message shape
  `property "<exact-name>" is not defined in object type`;
- exact expression AST `job.<exact-name>` for one of `workflow_ref`,
  `workflow_repository`, `workflow_file_path`, or `workflow_sha`;
- a catalogued central reusable-workflow provenance field whose static provenance check
  has already passed.

The filter parses actionlint's machine-readable output and the YAML expression AST; it does
not use line-number, free-form path, substring, or blanket regex ignores. It requires
exactly the expected four diagnostics per canonical reusable workflow under actionlint
1.7.12 and rejects missing, duplicate, relocated, misspelled, or additional diagnostics.
The actionlint binary/hash and allowlist version are release-manifest fields. A tool upgrade
must first run a compatibility probe: once the schema accepts a property, that entry must
be removed rather than silently accepting zero-or-one. All other released/canonical
managed diagnostics remain forbidden.

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
    "github_app": true
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

GitHub App authentication for repository API writes is enabled for the following eleven
repositories because the current approved inventory contains both `APP_ID` and
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

It is disabled for the other eight repositories. Ambient addition or removal of a secret
or variable never changes the rendered mapping:

- a configured credential missing from inventory blocks the repository;
- an unconfigured credential present in inventory is ignored;
- `github_app: true` requires both `APP_ID` and `APP_PRIVATE_KEY`;
- `github_app: false` passes neither `app_id` nor `APP_PRIVATE_KEY`.

The new central release removes the legacy implicit `vars.APP_ID` fallback. GitHub App
mode is controlled only by the explicit `app_id` input, preventing an `APP_ID`-only
inventory from failing later inside the called workflow.

The Gemini GitHub App token is restricted exactly to `contents: read`, `issues: write`,
and `pull-requests: write`. It receives no workflow, secret, administration, OIDC, or
cross-repository permission. Tests inspect the pinned token action inputs in every job
that can mint the token.

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
content-addressed manifest without a remote write. After reviewing that diff, the operator
records the printed external digest. `workflows publish --bootstrap-repo <name>
--plan-manifest <path> --plan-sha256 <approved-64-hex-digest> --confirm` requires the exact
approved manifest bytes, preview bundle, release/profile/catalog hashes, and base SHA to
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
Normal plan always treats config and required callers as mandatory. After bootstrap,
their deletion remains blocked drift; recovery requires the same explicit preview and
single-repository bootstrap publish path again.

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
byte change blocks the repository. The durable proof is the recorded
`base_sha..generated_head_sha` diff: it must contain zero changes outside managed paths.
Final audit evaluates that recorded rollout diff, not an entire later default branch that
may legitimately contain unrelated project commits.

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
supported remote workflow writer and release-upgrade path.

- `setup-github-workflows.sh` and `sync-secrets.sh` become side-effect-free deprecation
  guards. `--help` prints the exact replacement; every former mutating invocation exits
  with status 2 without reading a secret or changing a repository.
- `bump-automation-ref.yml` is removed from consumers and retained only as a typed retired
  path.
- The `workflows` parser exposes only `plan` and `publish`; it has no secret-sync,
  secret-refresh, secret-value, or generic passthrough option. The release profile and
  manifest both require `operation: workflow-standardization` and `secret_writes: deny`.
- Any future fleet secret operation uses a separate first-class command path, separate
  confirmation, and code path that cannot write workflow files. It is not chained into
  this rollout.
- Read-only inventory may call only the repository Actions Secrets **list** GET and
  Actions Variables list GET, requesting/consuming names and pagination metadata. GitHub
  never supplies secret values on this path; any unexpected response field is ignored and
  no repository/model secret value-bearing local or environment source is consulted. The
  separately scoped operator GitHub authentication credential is used only for API access.
- Tests replace every secret mutation route/method (PUT, PATCH, POST, or DELETE under a
  secret scope) and `gh secret set/delete` with fail-on-call sentinels. They require zero
  mutation calls in plan and publish while separately proving paginated name-only GET is
  allowed.
- Documentation directs operators to profile changes plus fleet plan/publish rather than
  editing or deleting common wrapper files.

The rollout CLI separates target release identity from rollout identity:

```text
workflows plan|publish --ref v1.40 --rollout-id common-ai-v1-v140
```

The rollout ID is cosmetic metadata, never a security switch. Branches, commit messages,
PR titles, and manifests use it. They do not reuse the old secret-hardening branch name or
claim that repository-owned triggers and permissions were preserved when canonical
replacement intentionally changes them.

## 8. Validation and Failure Semantics

### 8.1 Plan

Plan mode is read-only for GitHub state. It fetches every default branch, renders into a
temporary preview, and collects outcomes for the full fleet. It may write only local
disposable clones, previews, and the requested local manifest outside the clean release
worktree.

Required plan gates:

- verified detached execution context, local/remote release identity, and bundle hashes;
- typed profile/catalog and closed operation-policy validation;
- recursive action/container/artifact lock verification;
- paginated secret/variable name prerequisites through the two allowlisted read-only
  list APIs, without value reads;
- authoritative static input-type and secret contract audit;
- exact managed-file comparison;
- retired and unmanaged central-caller checks;
- Gemini API-key-only and no-OIDC/no-GCP assertions;
- YAML parse;
- `git diff --check`;
- mandatory actionlint `1.7.12` using the official Linux AMD64 archive SHA-256
  `8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8`, with recorded
  version and fail-closed process status;
- exactly the release-owned structured actionlint compatibility diagnostics for the four
  statically verified `job.workflow_*` provenance expressions, zero other diagnostics in
  released/canonical managed workflows, and zero new diagnostics compared with the
  untouched project-owned baseline;
- a zero unmanaged-path diff between each recorded base and generated head.

Every repository outcome uses `status: current|drift|blocked` plus a stable reason code.
`bootstrap_required` is a failure reason, not persistent policy state. Plan exits 0 only
when there is no blocked outcome, exits 3 when it produces a complete manifest containing
one or more blocked repositories, and exits 2 when it cannot produce a trustworthy full
manifest. It never hides a missing catalog behind `skipped`.

A successful complete plan emits canonical UTF-8 JSON plus a deterministic preview archive.
The manifest contains the preview archive SHA-256 and every planned file/deletion digest;
its own SHA-256 is external and is printed as
`APPROVAL_PLAN_SHA256=<64 lowercase hex>`. It is also copied to an operator-controlled,
content-addressed evidence path `<sha256>.json`. The human/controller reviews the exact
manifest and preview, then preserves that digest in an approval record independent of the
manifest. Publish must receive it through `--plan-sha256`; it never trusts a digest stored
inside the manifest and never silently computes-and-accepts a replacement digest.

### 8.2 Publish

Publish requires both `--plan-manifest` and the independently preserved
`--plan-sha256 <approved digest>`. Before GitHub access it hashes the exact manifest bytes,
requires equality with that external input/content-addressed filename, then verifies every
release, tool, policy, profile, catalog, lock, preview-archive, planned-file, and base
identity. A missing/malformed digest or mismatch is a parser/gate failure, never an
interactive auto-approval. It then retains `--confirm` and fail-fast behavior. A repository
is fully validated before any push. Immediately before commit/push, the tool refetches the
remote default branch and requires it to equal the recorded base SHA. A mismatch blocks
without pushing and must be replanned.

Each repository receives an independent branch and pull request. The manifest records
completed remote effects. Resumption uses explicit `--repo` selections and never replays
already merged repositories implicitly.

The workflow-standardization operation structurally has no secret option or secret-write
code path, regardless of rollout ID. Every manifest outcome must contain
`synced_secrets: []`, and any attempt to supply a secret-related flag is a parser error
before GitHub access.

### 8.3 Tool failure

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
- sentinel local provider/model credentials (including `ZHIPU_API_KEY`) are absent from the
  project/child environment and from every log, preview, and manifest; GitHub auth remains
  scoped to the `gh` child and redacted;
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
- API-key-only and GitHub-App profiles differ only by approved fields;
- adding any ambient `GOOGLE_API_KEY`, App, GCP/WIF, Vertex AI, Code Assist, or CLI-version
  variable does not expand a profile;
- every Gemini caller and central Gemini job remains no-OIDC and API-key-only;
- partial configured App inventory blocks;
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
- actionlint's exact four known `job.workflow_*` schema diagnostics per central workflow
  are structurally matched only after the static provenance AST passes; missing, typoed,
  relocated, extra, or blanket-ignored diagnostics fail;
- `base_sha..generated_head_sha` contains zero project-owned changes;
- retired bump files are deleted;
- a second render produces no changes;
- no file is written when any planned YAML is invalid.

### Orchestration

- plan performs no remote write and reports all repositories with stable status/reason;
- only an explicitly bootstrap-allowed, fully caller-free/config-free repository yields
  `blocked/bootstrap_required`; partial and unauthorized absence use distinct blocks;
- bootstrap plan is read-only and bootstrap publish requires one repository, an external
  `--plan-sha256` matching the reviewed content-addressed manifest, and confirmation;
- missing, malformed, manifest-self-supplied, or mismatched plan digest blocks before
  GitHub access; exact external digest plus unchanged preview succeeds;
- publish requires actionlint and fails on tool execution errors;
- publish refetches and blocks a stale default branch or stale preview;
- publish is fail-fast and resume is explicit;
- rollout branch and PR text use rollout ID as metadata rather than a security switch;
- secret-related flags are parser errors, name-only inventory GET remains allowed, and
  all secret-mutation sentinels remain untouched;
- allowlisted paginated secret-name GET succeeds, while every mutation method/CLI
  sentinel fails the test if called;
- canary evidence rejects a caller SHA with wrong bytes or a called `job.workflow_sha`
  other than the verified release;
- all standardization manifests contain empty `synced_secrets`.

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

Run normal read-only plan for all 19 profiles. The expected manifest is exactly 17
`status: drift`, exactly two `status: blocked, reason_code: bootstrap_required`
(`cts-email-mcp-server` and `wpa-supplicant`), zero other blocked reasons, and no skip.
Exit 3 is expected for this discovery gate. The controller may advance only after parsing
that exact complete manifest, reviewing its preview, and preserving the printed
`APPROVAL_PLAN_SHA256` outside the manifest; any other count, reason, exit code, incomplete
result, or secret mutation call stops. This is the sole gate-specific exception to the
normal "any blocked stops" rule. It permits canary publish of explicitly selected
non-blocked entries from that complete manifest; blocked entries remain ineligible for
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
   For both runs require caller file bytes at `github.workflow_sha` to match the approved
   caller and called `job.workflow_sha` to equal the verified `v1.40` commit.
2. `wlan-driver`: publish and merge its approved API-key-only entry, create a harmless
   same-repository PR, and invoke the manual Gemini PR-review `workflow_dispatch` from the
   default branch for that PR. Require provenance equality, a successful API-key model
   path, no App-token mint, no `APP_PRIVATE_KEY` mapping, and no OIDC/GCP/Vertex/Code
   Assist path.
3. `cts-email-mcp-server`: approve a separate explicit bootstrap plan/digest, publish and
   merge the bootstrap PR, and verify the default-branch caller/config bytes. Invoke the
   newly available `gemini-scheduled-triage` `workflow_dispatch`; require the central
   provenance job to run from `v1.40` and the execution job to skip because the bootstrap
   config explicitly disables it. No model or App credential may be consumed.

The controller records run IDs, events, caller/called refs and SHAs, conclusions, and
review/comment evidence before removing harmless test PRs and branches. If any live or
provenance assertion fails, fleet rollout stops and all Gate 3 managed consumer merges are
reverted in reverse order through independently reviewed revert PRs before retry; the
immutable automation tag is never moved.

### Gate 4: fleet publish

The three successful managed canary PRs are already part of the target state. Publish
independent PRs for the remaining sixteen repositories from their externally approved,
unchanged plan manifests. `wpa-supplicant` receives its own explicit bootstrap plan and
confirmed publish; all of its common callers remain disabled until a separate reviewed
enablement change.

### Gate 5: final audit

The final read-only plan must report:

- `current=19`;
- `skipped=0`;
- `blocked=0`;
- `synced_secrets=[]` for every repository;
- all consumer callers pinned to the verified release commit;
- each recorded `base_sha..generated_head_sha` changes zero unmanaged paths.

Except for the exact Gate 2 discovery condition above, any release/tool verification,
dependency-lock, catalog/profile, contract/type, Gemini-auth, YAML, actionlint,
default-branch freshness, unmanaged-path, secret-mutation, or live-canary failure stops
the next stage.

## 11. Recovery

- Starting automation base: `2254f13aab44585c78954d20749f4fb677a8c2f1`.
- Initial design commit: `7ea29d2e5d5c7b1673cce378c40c8c24deb3df81`.
- Development remains isolated on `codex/standardize-common-workflows`.
- Before merge, delete the isolated worktree and branch to abandon the change.
- After merge, revert the three automation PRs in reverse order; never move or delete `v1.40`.
- If released behavior is unsafe, fix forward with a new immutable tag.
- Consumer changes are independent PRs and are reverted independently in reverse merge
  order.
- Reverting a bootstrap PR removes only the newly added managed `.github` content.
- Removing `bump-automation-ref.yml` is restored by reverting that repository's PR if
  emergency rollback requires the previous mechanism.
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
- Every recorded rollout `base_sha..generated_head_sha` contains zero unmanaged-path
  changes.
- Mandatory actionlint passes with only the exact release-owned structured
  `job.workflow_*` schema-compatibility diagnostics, and all automated tests pass with
  recorded versions/evidence.
- Publish requires an externally approved `--plan-sha256`, consumes the byte-identical
  content-addressed manifest/preview, and the remote default branch still equals its
  planned base SHA for each repository.
- `wlan-package` automatic and manual OpenCode canaries succeed.
- API-key-only and fail-closed bootstrap canaries satisfy their declared profiles.
- Final fleet state is `current=19`, `skipped=0`, `blocked=0`.
- The operation policy is `secret_writes: deny`; allowlisted name-only inventory GETs
  may occur, all secret-mutation sentinels remain unused, and every manifest entry has
  `synced_secrets: []`.
