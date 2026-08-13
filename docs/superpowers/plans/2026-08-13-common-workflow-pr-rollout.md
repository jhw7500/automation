# Common Workflow PR Rollout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a catalog-driven workflow fleet tool that renders the common AI callers, validates them, and opens independent repository pull requests without merging, reverting, force-pushing, or changing secrets.

**Architecture:** The tagged `automation` release contains the reusable workflows, one typed catalog, one typed 19-repository profile inventory, and one canonical consumer tree. Pure catalog/render/audit code computes managed-file changes; a small Git/GitHub adapter only clones, checks prerequisite names, creates a deterministic branch, pushes a new commit, and opens or reuses an exact PR. Repository owners retain all merge and recovery control through GitHub.

**Tech Stack:** Python 3.12 standard library, PyYAML `BaseLoader`, `unittest`/pytest, Bash, Git, GitHub CLI, GitHub Actions YAML, actionlint 1.7.12.

## Global Constraints

- Release from current `automation/main` as immutable annotated tag `v1.40`; consumer `uses:` values contain its resolved 40-character commit, while `.github/workflow-config.yml` records both `automation_ref: v1.40` and `automation_commit: <commit>`.
- `examples/baseline-workflows/.github/` is the only canonical consumer tree. The duplicate `examples/baseline-workflows/workflows/` tree is deleted.
- Manage only the 10 required callers, 4 profile-selected optional callers, `.github/workflow-config.yml`, and retired `bump-automation-ref.yml`; every other workflow remains byte-identical.
- `plan` and `audit` are read-only. `publish` may create a non-default branch, one commit, and one PR only.
- No code path may merge, enable auto-merge, update a PR branch, force-push, push a default branch, revert, or write a GitHub Actions secret/variable.
- Workflow rollout and `personal-ops/claude-token-sync` remain separate. Local provider values including `CLAUDE_CODE_OAUTH_TOKEN`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `ZHIPU_API_KEY`, and `APP_PRIVATE_KEY` are removed from Git/`gh` child environments and never enter argv, logs, commits, or reports.
- Caller secret mappings are same-name expressions and never `secrets: inherit`: Claude uses `CLAUDE_CODE_OAUTH_TOKEN`, Gemini uses `GEMINI_API_KEY`, and profiled OpenCode uses `ZHIPU_API_KEY`.
- Gemini model authentication has no `GOOGLE_API_KEY`, GCP/Vertex, Code Assist, or OIDC path. Repository-write authentication is exactly `github_app` or `github_token` from the profile.
- `github_app` callers map `app_id: ${{ vars.APP_ID }}` and `APP_PRIVATE_KEY`; `github_token` callers map neither and central workflows use `${{ github.token }}`.
- Preserve the `v1.39` OpenCode same-repository guard, exact permissions, private-repository checkout authentication, `github.token` use, and pinned CLI archive checks.
- Bootstrap is permitted only for `wpa-supplicant` and `cts-email-mcp-server`, requires a one-repository publish command plus `--bootstrap-repo`, and renders every common workflow disabled.
- Public plan statuses are exactly `current`, `planned`, `reusable`, and `blocked`; renderer-only `drift`/`bootstrap_required` never leak through the plan CLI, missing config without explicit bootstrap is `blocked`, and audit separately reports `current`, `drift`, or `blocked`.
- Closed PR history blocks reuse of the deterministic repository/release identity. Closing/deleting aborts that attempt; a corrected retry requires a new immutable release/ref.
- Use TDD, run the focused test red before implementation, run it green afterward, and make one reviewable commit per task. Prefix every shell command with `rtk`.

## File Responsibility Map

- `scripts/workflow-catalog.json`: sole managed-path and caller-contract catalog.
- `scripts/workflow-config.json`: sole 19-repository profile inventory and release defaults.
- `scripts/workflow_catalog.py`: typed catalog/profile loading and schema validation only.
- `scripts/prepare_workflow_rollout.py`: pure managed-file rendering plus guarded application to a disposable clone.
- `scripts/audit_workflow_fleet.py`: content-state classification (`current`, `drift`, `blocked`) only.
- `scripts/workflow_release_bundle.py`: verified tag resolution and extraction of catalog/config/canonical files.
- `scripts/workflow_fleet_git.py`: scrubbed subprocess execution, default-branch clone/fetch, name-only prerequisite inventory, branch inspection/push, and PR inspection/creation.
- `scripts/rollout_workflow_fleet.py`: `plan`/`publish` orchestration and JSON reporting; no secret or merge client.
- `scripts/verify_workflow_release.py`: immutable tag and central/canonical security contract gate.
- `tests/test_workflow_catalog.py`, `tests/test_prepare_workflow_rollout.py`, `tests/test_audit_workflow_fleet.py`, `tests/test_workflow_release_bundle.py`, `tests/test_rollout_workflow_fleet.py`: focused unit and integration boundaries.

---

### Task 1: Define the Typed Catalog and Fleet Profiles

**Files:**
- Create: `scripts/workflow-catalog.json`
- Create: `scripts/workflow_catalog.py`
- Create: `tests/test_workflow_catalog.py`
- Modify: `scripts/workflow-config.json`

**Interfaces:**
- Consumes: JSON files relative to an automation release root.
- Produces:
  - `CallerJobContract(name: str, permissions: tuple[tuple[str, str], ...], with_keys: tuple[str, ...], secrets: tuple[str, ...])`.
  - `CatalogEntry(path: PurePosixPath, kind: Literal["required", "optional", "config", "retired"], central_workflow: str | None, auth_family: Literal["claude", "gemini", "opencode", "none"], profile_axis: Literal["repo_write_auth"] | None, trigger: object, caller_jobs: tuple[CallerJobContract, ...])`.
  - `WorkflowCatalog(entries: tuple[CatalogEntry, ...])` with read-only properties `callers: tuple[CatalogEntry, ...]`, `managed_paths: frozenset[PurePosixPath]`, and `by_name: Mapping[str, CatalogEntry]`.
  - `RepoProfile(name: str, profile: str, optional_workflows: frozenset[str], repo_write_auth: Literal["github_app", "github_token"], bootstrap_allowed: bool)`
  - `FleetConfig(owner: str, automation_ref: str, canonical_dir: PurePosixPath, profiles: Mapping[str, RepoProfile])`
  - `load_catalog(root: Path) -> WorkflowCatalog`
  - `load_fleet_config(root: Path, catalog: WorkflowCatalog) -> FleetConfig`
  - `extract_caller_jobs(workflow: Mapping[str, object]) -> tuple[CallerJobContract, ...]`.
  - `expected_caller_jobs(entry: CatalogEntry, profile: RepoProfile) -> tuple[CallerJobContract, ...]`, which returns canonical jobs unchanged except that a Gemini `github_token` job retains `repo_write_auth`, removes `app_id`, and removes `APP_PRIVATE_KEY`.

- [ ] **Step 1: Add a failing schema/profile test**

Create `tests/test_workflow_catalog.py` with the complete policy snapshot, not a count-only assertion:

```python
EXPECTED = {
    "gstApp": ({"auto-rereview-request.yml", "gemini-chat.yml", "opencode.yml", "opencode-auto-review.yml"}, "github_app", False),
    "max9296": ({"auto-rereview-request.yml", "gemini-chat.yml", "opencode.yml", "opencode-auto-review.yml"}, "github_app", False),
    "wlan-driver": ({"auto-rereview-request.yml", "opencode.yml", "opencode-auto-review.yml"}, "github_token", False),
    "wlan-driver-v2": ({"auto-rereview-request.yml", "opencode.yml", "opencode-auto-review.yml"}, "github_app", False),
    "wlan-bridge": ({"gemini-chat.yml", "opencode.yml", "opencode-auto-review.yml"}, "github_app", False),
    "wlan-package": ({"opencode.yml", "opencode-auto-review.yml"}, "github_app", False),
    "pim-package-jhw": ({"opencode-auto-review.yml"}, "github_app", False),
    "wlan-opc": ({"opencode.yml"}, "github_token", False),
    "pcap-analyzer": (set(), "github_app", False),
    "wpa-supplicant": (set(), "github_token", True),
    "sc16is7xx": (set(), "github_app", False),
    "pim-check": (set(), "github_token", False),
    "redmine": (set(), "github_app", False),
    "jhw-notion": (set(), "github_app", False),
    "personal-ops": (set(), "github_app", False),
    "cts-email-mcp-server": (set(), "github_token", True),
    "cts-ta-mcp-server": (set(), "github_token", False),
    "cts-ta-webapp": (set(), "github_token", False),
    "claude-config": (set(), "github_token", False),
}

def test_catalog_and_profiles_are_closed() -> None:
    catalog = load_catalog(ROOT)
    config = load_fleet_config(ROOT, catalog)
    assert config.owner == "jhw7500"
    assert config.automation_ref == "v1.40"
    assert config.canonical_dir == PurePosixPath("examples/baseline-workflows/.github")
    assert set(config.profiles) == set(EXPECTED)
    for name, (optional, auth, bootstrap) in EXPECTED.items():
        profile = config.profiles[name]
        assert profile.profile == "common-ai-v1"
        assert profile.optional_workflows == frozenset(optional)
        assert profile.repo_write_auth == auth
        assert profile.bootstrap_allowed is bootstrap

    by_kind = {kind: {entry.path.name for entry in catalog.entries if entry.kind == kind}
               for kind in ("required", "optional", "config", "retired")}
    assert by_kind["required"] == {
        "claude.yml", "claude-code-review.yml", "gemini-auto-review.yml",
        "gemini-dispatch.yml", "gemini-invoke.yml", "gemini-issue-triage.yml",
        "gemini-pr-review.yml", "gemini-review.yml",
        "gemini-scheduled-triage.yml", "gemini-triage.yml",
    }
    assert by_kind["optional"] == {
        "auto-rereview-request.yml", "gemini-chat.yml",
        "opencode.yml", "opencode-auto-review.yml",
    }
    assert by_kind["config"] == {"workflow-config.yml"}
    assert by_kind["retired"] == {"bump-automation-ref.yml"}
```

Add negative tests for duplicate paths, unknown kinds, unknown optional names, an invalid auth mode, a third bootstrap repository, a path outside `.github/`, and a catalog caller without a central target.

- [ ] **Step 2: Run the focused test and confirm it is red**

Run: `rtk python3 -m pytest tests/test_workflow_catalog.py -q`

Expected: collection/import failure because `scripts.workflow_catalog` and `scripts/workflow-catalog.json` do not exist.

- [ ] **Step 3: Add the closed JSON schemas and loader**

Use this exact top-level fleet shape:

```json
{
  "schema_version": 1,
  "gh_owner": "jhw7500",
  "automation_ref": "v1.40",
  "canonical_dir": "examples/baseline-workflows/.github",
  "catalog": "scripts/workflow-catalog.json",
  "repos": {
    "wlan-package": {
      "profile": "common-ai-v1",
      "optional_workflows": ["opencode.yml", "opencode-auto-review.yml"],
      "repo_write_auth": "github_app",
      "bootstrap_allowed": false
    }
  }
}
```

Populate all 19 entries from `EXPECTED`. In `workflow-catalog.json`, use one entry per managed path. Each caller entry contains `path`, `kind`, `central_workflow`, `auth_family`, `profile_axis`, the exact `trigger` mapping, and `caller_jobs`; each caller job contains `name`, exact `permissions`, sorted `with` keys, and same-name `secrets` keys. Set `profile_axis` to `repo_write_auth` for every Gemini caller and `null` for all other entries. Use these auth families and targets:

| Caller | Kind | Target | Auth family |
| --- | --- | --- | --- |
| `claude.yml` | required | `claude.yml` | claude |
| `claude-code-review.yml` | required | `claude-code-review.yml` | claude |
| `gemini-auto-review.yml` | required | `gemini-auto-review.yml` | gemini |
| `gemini-dispatch.yml` | required | `gemini-dispatch.yml` | gemini |
| `gemini-invoke.yml` | required | `gemini-invoke.yml` | gemini |
| `gemini-issue-triage.yml` | required | `gemini-triage.yml` | gemini |
| `gemini-pr-review.yml` | required | `gemini-review.yml` | gemini |
| `gemini-review.yml` | required | `gemini-review.yml` | gemini |
| `gemini-scheduled-triage.yml` | required | `gemini-scheduled-triage.yml` | gemini |
| `gemini-triage.yml` | required | `gemini-triage.yml` | gemini |
| `auto-rereview-request.yml` | optional | `auto-rereview-request.yml` | none |
| `gemini-chat.yml` | optional | `gemini-chat.yml` | gemini |
| `opencode.yml` | optional | `opencode.yml` | opencode |
| `opencode-auto-review.yml` | optional | `opencode-auto-review.yml` | opencode |
| `workflow-config.yml` | config | null | none |
| `bump-automation-ref.yml` | retired | null | none |

Implement strict key checking so misspelled or additional JSON keys fail rather than being ignored:

```python
def _require_keys(value: dict[str, object], *, exact: set[str], where: str) -> None:
    actual = set(value)
    if actual != exact:
        missing = sorted(exact - actual)
        unknown = sorted(actual - exact)
        raise CatalogError(f"{where}: missing={missing}, unknown={unknown}")

def _managed_path(raw: str) -> PurePosixPath:
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts or path.parts[:1] != (".github",):
        raise CatalogError(f"managed path escapes .github: {raw}")
    return path

def extract_caller_jobs(workflow: Mapping[str, object]) -> tuple[CallerJobContract, ...]:
    contracts: list[CallerJobContract] = []
    jobs = workflow.get("jobs", {})
    if not isinstance(jobs, dict):
        raise CatalogError("workflow jobs must be a mapping")
    for name, raw_job in jobs.items():
        if not isinstance(raw_job, dict):
            continue
        uses = raw_job.get("uses")
        if not isinstance(uses, str) or "jhw7500/automation/.github/workflows/" not in uses:
            continue
        permissions = raw_job.get("permissions", {})
        with_values = raw_job.get("with", {})
        secrets = raw_job.get("secrets", {})
        if not all(isinstance(value, dict) for value in (permissions, with_values, secrets)):
            raise CatalogError(f"caller job {name} has a non-mapping contract")
        contracts.append(CallerJobContract(
            name=name,
            permissions=tuple(sorted(permissions.items())),
            with_keys=tuple(sorted(with_values)),
            secrets=tuple(sorted(secrets)),
        ))
    return tuple(contracts)

def expected_caller_jobs(entry: CatalogEntry, profile: RepoProfile) -> tuple[CallerJobContract, ...]:
    if entry.profile_axis != "repo_write_auth" or profile.repo_write_auth == "github_app":
        return entry.caller_jobs
    return tuple(dataclasses.replace(
        job,
        with_keys=tuple(key for key in job.with_keys if key != "app_id"),
        secrets=tuple(key for key in job.secrets if key != "APP_PRIVATE_KEY"),
    ) for job in entry.caller_jobs)
```

Reject duplicate catalog paths, duplicate repo names, optional selections that are not `optional`, auth values outside the two-value enum, any `profile` other than `common-ai-v1`, a profile axis on a non-Gemini entry, a Gemini caller without the `repo_write_auth` axis, and any bootstrap set other than exactly the approved two repositories.

- [ ] **Step 4: Run catalog tests green**

Run: `rtk python3 -m pytest tests/test_workflow_catalog.py -q`

Expected: all catalog/profile tests pass.

- [ ] **Step 5: Commit the typed policy source**

```bash
rtk git add scripts/workflow-catalog.json scripts/workflow-config.json scripts/workflow_catalog.py tests/test_workflow_catalog.py
rtk git commit -m "feat: define typed workflow fleet catalog"
```

---

### Task 2: Make Gemini Model and Repository Authentication Explicit

**Files:**
- Modify: `.github/workflows/gemini-auto-review.yml`
- Modify: `.github/workflows/gemini-chat.yml`
- Modify: `.github/workflows/gemini-dispatch.yml`
- Modify: `.github/workflows/gemini-invoke.yml`
- Modify: `.github/workflows/gemini-review.yml`
- Modify: `.github/workflows/gemini-scheduled-triage.yml`
- Modify: `.github/workflows/gemini-triage.yml`
- Modify: `tests/test_workflow_secret_contracts.py`

**Interfaces:**
- Consumes: reusable-workflow input `repo_write_auth`, optional input `app_id`, required secret `GEMINI_API_KEY`, optional secret `APP_PRIVATE_KEY`.
- Produces: a single repository token per write job from pinned `setup-gemini-auth` at commit `2254f13aab44585c78954d20749f4fb677a8c2f1`; `github_app` mints an App token and `github_token` selects `${{ github.token }}`.

- [ ] **Step 1: Replace the legacy-auth tests with failing explicit-mode tests**

In `tests/test_workflow_secret_contracts.py`, make all seven Gemini reusable workflows obey this contract:

```python
GEMINI_WORKFLOWS = {
    "gemini-auto-review.yml", "gemini-chat.yml", "gemini-dispatch.yml",
    "gemini-invoke.yml", "gemini-review.yml",
    "gemini-scheduled-triage.yml", "gemini-triage.yml",
}
SETUP_AUTH = (
    "jhw7500/automation/.github/actions/setup-gemini-auth@"
    "2254f13aab44585c78954d20749f4fb677a8c2f1"
)

def test_gemini_contract_is_api_key_only_and_mode_explicit() -> None:
    for filename in GEMINI_WORKFLOWS:
        workflow = load_workflow(WORKFLOWS / filename)
        call = workflow["on"]["workflow_call"]
        assert call["inputs"]["repo_write_auth"] == {
            "description": "Repository write authentication: github_app or github_token",
            "type": "string", "required": "true",
        }
        assert call["inputs"]["app_id"]["required"] == "false"
        assert set(call["secrets"]) == {"APP_PRIVATE_KEY", "GEMINI_API_KEY"}
        assert call["secrets"]["APP_PRIVATE_KEY"]["required"] == "false"
        assert call["secrets"]["GEMINI_API_KEY"]["required"] == "true"
        text = (WORKFLOWS / filename).read_text()
        assert "GOOGLE_API_KEY" not in text
        assert "vars.APP_ID" not in text
        assert "id-token:" not in text
        assert SETUP_AUTH in text
```

Add an AST assertion that every `setup-gemini-auth` step passes all three mode-controlled values exactly:

```python
assert step["with"] == {
    "app-id": "${{ inputs.repo_write_auth == 'github_app' && inputs.app_id || '' }}",
    "private-key": "${{ inputs.repo_write_auth == 'github_app' && secrets.APP_PRIVATE_KEY || '' }}",
    "fallback-token": "${{ inputs.repo_write_auth == 'github_token' && github.token || '' }}",
}
```

- [ ] **Step 2: Run the focused tests red**

Run: `rtk python3 -m pytest tests/test_workflow_secret_contracts.py -q`

Expected: failures report the existing Google fallback, `vars.APP_ID`, OIDC permission, tag-pinned auth action, and absent mode input.

- [ ] **Step 3: Change each reusable Gemini contract**

Use this exact declaration in every Gemini reusable workflow:

```yaml
on:
  workflow_call:
    inputs:
      repo_write_auth:
        description: 'Repository write authentication: github_app or github_token'
        type: string
        required: true
      app_id:
        description: 'GitHub App ID; used only when repo_write_auth is github_app'
        type: string
        required: false
        default: ''
    secrets:
      APP_PRIVATE_KEY:
        description: 'GitHub App private key; used only for github_app mode'
        required: false
      GEMINI_API_KEY:
        description: 'Gemini API key'
        required: true
```

Retain each workflow's non-auth inputs. Delete `GOOGLE_API_KEY` declarations and all Google/GCP/Vertex/Code Assist mappings. Remove `id-token` from every Gemini job permission block.

- [ ] **Step 4: Validate and resolve repository-write auth before each write path**

Place this validation immediately before the pinned setup action in each job that writes a comment, label, issue, or PR:

```yaml
- name: Validate repository-write auth
  shell: bash
  env:
    MODE: ${{ inputs.repo_write_auth }}
    APP_ID: ${{ inputs.app_id }}
    APP_PRIVATE_KEY: ${{ secrets.APP_PRIVATE_KEY }}
  run: |
    case "$MODE" in
      github_app)
        test -n "$APP_ID" && test -n "$APP_PRIVATE_KEY" || {
          echo 'github_app requires app_id and APP_PRIVATE_KEY' >&2
          exit 1
        }
        ;;
      github_token)
        test -z "$APP_ID" && test -z "$APP_PRIVATE_KEY" || {
          echo 'github_token forbids App credentials' >&2
          exit 1
        }
        ;;
      *)
        echo "invalid repo_write_auth: $MODE" >&2
        exit 1
        ;;
    esac

- name: Resolve repository-write token
  id: auth
  uses: jhw7500/automation/.github/actions/setup-gemini-auth@2254f13aab44585c78954d20749f4fb677a8c2f1
  with:
    app-id: ${{ inputs.repo_write_auth == 'github_app' && inputs.app_id || '' }}
    private-key: ${{ inputs.repo_write_auth == 'github_app' && secrets.APP_PRIVATE_KEY || '' }}
    fallback-token: ${{ inputs.repo_write_auth == 'github_token' && github.token || '' }}
```

Replace downstream GitHub token inputs/env values with `${{ steps.auth.outputs.token }}`. In workflows with separate model and label/comment jobs, resolve auth only in the job that needs repository write access; continue passing only `GEMINI_API_KEY` to the model runtime.

- [ ] **Step 5: Run the central workflow contract tests green**

Run: `rtk python3 -m pytest tests/test_workflow_secret_contracts.py -q`

Expected: all tests pass and OpenCode regression tests remain green.

- [ ] **Step 6: Parse all changed reusable workflows**

Run:

```bash
rtk python3 - <<'PY'
from pathlib import Path
import yaml
for path in Path('.github/workflows').glob('gemini*.yml'):
    assert isinstance(yaml.load(path.read_text(), Loader=yaml.BaseLoader), dict), path
print('PASS: Gemini YAML parsed')
PY
```

Expected: `PASS: Gemini YAML parsed`.

- [ ] **Step 7: Commit the central authentication contract**

```bash
rtk git add .github/workflows/gemini-*.yml tests/test_workflow_secret_contracts.py
rtk git commit -m "fix: make Gemini workflow authentication explicit"
```

---

### Task 3: Establish the Single Canonical Consumer Tree

**Files:**
- Modify: `examples/baseline-workflows/.github/workflows/*.yml`
- Create: `examples/baseline-workflows/.github/workflows/gemini-issue-triage.yml`
- Create: `examples/baseline-workflows/.github/workflows/gemini-pr-review.yml`
- Create: `examples/baseline-workflows/.github/workflows/opencode-auto-review.yml`
- Modify: `examples/baseline-workflows/.github/workflow-config.yml`
- Delete: `examples/baseline-workflows/.github/README.md`
- Delete: `examples/baseline-workflows/.github/workflows/bump-automation-ref.yml`
- Delete: `examples/baseline-workflows/workflow-config.yml`
- Delete: `examples/baseline-workflows/workflows/`
- Modify: `scripts/workflow-catalog.json`
- Create: `tests/test_canonical_workflow_tree.py`
- Modify: `tests/test_action_pins.py`

**Interfaces:**
- Consumes: Task 1 catalog and Task 2 central `workflow_call` declarations.
- Produces: valid canonical App-profile YAML containing `@__AUTOMATION_COMMIT__`; `render_caller()` in Task 4 changes only that commit placeholder and the declared Gemini auth lines.

- [ ] **Step 1: Add a failing canonical-tree contract test**

Test exact managed presence, catalog contract equality, and the absence of the duplicate tree:

```python
def load_yaml(path: Path) -> dict[str, object]:
    value = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(value, dict), path
    return value

def canonical_text(entry: CatalogEntry) -> str:
    return (CANONICAL / entry.path.relative_to(".github")).read_text(encoding="utf-8")

def caller_job_contracts(workflow: dict[str, object]) -> tuple[CallerJobContract, ...]:
    return extract_caller_jobs(workflow)

def central_accepts(entry: CatalogEntry, central_root: Path) -> bool:
    central = load_yaml(central_root / entry.central_workflow)
    call = central["on"]["workflow_call"]
    declared_inputs = set(call.get("inputs", {}))
    declared_secrets = call.get("secrets", {})
    required_secrets = {
        name for name, value in declared_secrets.items()
        if value.get("required", "false") == "true"
    }
    return all(
        set(job.with_keys) <= declared_inputs
        and set(job.secrets) <= set(declared_secrets)
        and required_secrets <= set(job.secrets)
        for job in entry.caller_jobs
    )

def test_canonical_tree_is_exactly_catalogued() -> None:
    catalog = load_catalog(ROOT)
    root = ROOT / "examples/baseline-workflows/.github"
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*") if path.is_file()
    }
    expected = {
        entry.path.relative_to(".github").as_posix()
        for entry in catalog.entries if entry.kind != "retired"
    }
    assert actual == expected
    assert not (ROOT / "examples/baseline-workflows/workflows").exists()
    assert not (ROOT / "examples/baseline-workflows/workflow-config.yml").exists()

def test_canonical_callers_match_catalog_and_central_contracts() -> None:
    for entry in load_catalog(ROOT).callers:
        workflow = load_yaml(CANONICAL / entry.path.relative_to('.github'))
        assert workflow["on"] == entry.trigger
        assert caller_job_contracts(workflow) == entry.caller_jobs
        assert "@__AUTOMATION_COMMIT__" in canonical_text(entry)
        assert central_accepts(entry, ROOT / ".github/workflows")
```

Also assert: no canonical file contains `secrets: inherit`, `GOOGLE_API_KEY`, a Gemini `id-token`, a tag-valued reusable `uses:`, or an unselected secret source.

- [ ] **Step 2: Run the canonical test red**

Run: `rtk python3 -m pytest tests/test_canonical_workflow_tree.py tests/test_action_pins.py -q`

Expected: missing three callers, duplicate tree present, retired bump file present, legacy Gemini mappings, and old release tags.

- [ ] **Step 3: Build the 14 canonical caller files**

Use the current approved trigger shapes, with these exact reusable caller permission ceilings:

| Caller family | Permissions |
| --- | --- |
| Claude command | `actions: read`, `contents: read`, `id-token: write`, `issues: read`, `pull-requests: read` |
| Claude auto review | `contents: read`, `id-token: write`, `issues: read`, `pull-requests: write` |
| Gemini Chat | `actions: read`, `contents: read`, `issues: write`, `pull-requests: write` |
| All other Gemini callers | `contents: read`, `issues: write`, `pull-requests: write` |
| Auto rereview | `contents: read`, `issues: write`, `pull-requests: write` |
| OpenCode manual/auto | `contents: read`, `issues: write`, `pull-requests: write` |

Every Gemini caller is stored as the canonical App profile:

```yaml
    uses: jhw7500/automation/.github/workflows/gemini-review.yml@__AUTOMATION_COMMIT__
    with:
      pr_number: ${{ inputs.pr_number }}
      issue_title: ${{ needs.prepare.outputs.pr_title }}
      issue_body: ${{ needs.prepare.outputs.pr_body }}
      additional_context: ${{ inputs.additional_context }}
      repo_write_auth: github_app
      app_id: ${{ vars.APP_ID }}
    secrets:
      APP_PRIVATE_KEY: ${{ secrets.APP_PRIVATE_KEY }}
      GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
```

Retain the input-specific `with` keys of each caller, but use exactly the two auth lines above. Claude maps only `CLAUDE_CODE_OAUTH_TOKEN`; OpenCode maps only `ZHIPU_API_KEY`. Create `opencode-auto-review.yml` as a thin `pull_request: [opened, synchronize]` caller with `pr_number`, exact OpenCode permissions, same-name `ZHIPU_API_KEY`, and no local checkout/check job because the central workflow owns enablement and same-repository enforcement.

- [ ] **Step 4: Make the bootstrap config explicit and disabled**

Use both identity placeholders and one `enabled: false` entry for all 14 caller names:

```yaml
automation_ref: __AUTOMATION_REF__
automation_commit: __AUTOMATION_COMMIT__
review:
  auto: false
workflows:
  auto-rereview-request:
    enabled: false
  claude:
    enabled: false
  claude-code-review:
    enabled: false
  gemini-auto-review:
    enabled: false
  gemini-chat:
    enabled: false
  gemini-dispatch:
    enabled: false
  gemini-invoke:
    enabled: false
  gemini-issue-triage:
    enabled: false
  gemini-pr-review:
    enabled: false
  gemini-review:
    enabled: false
  gemini-scheduled-triage:
    enabled: false
  gemini-triage:
    enabled: false
  opencode:
    enabled: false
  opencode-auto-review:
    enabled: false
```

- [ ] **Step 5: Populate catalog trigger/job contracts from the canonical files and remove duplicates**

Record the exact parsed `on` mapping and caller-job keys in `workflow-catalog.json`. Delete the duplicate tree, top-level duplicate config, canonical README, and both canonical bump copies. Update `tests/test_action_pins.py` so `MANAGED_WORKFLOW_ROOTS` contains only central `.github/workflows` and `examples/baseline-workflows/.github/workflows`.

- [ ] **Step 6: Run canonical and pin tests green**

Run: `rtk python3 -m pytest tests/test_canonical_workflow_tree.py tests/test_action_pins.py tests/test_workflow_secret_contracts.py -q`

Expected: all pass.

- [ ] **Step 7: Commit the canonical tree**

```bash
rtk git add -A examples/baseline-workflows scripts/workflow-catalog.json tests/test_canonical_workflow_tree.py tests/test_action_pins.py
rtk git commit -m "feat: establish canonical workflow caller tree"
```

---

### Task 4: Replace Line Editing with a Deterministic Managed-File Renderer

**Files:**
- Rewrite: `scripts/prepare_workflow_rollout.py`
- Rewrite: `tests/test_prepare_workflow_rollout.py`

**Interfaces:**
- Consumes: `WorkflowCatalog`, `RepoProfile`, canonical root, `release_ref`, 40-character `release_commit`, secret-name set, variable-name set, and `bootstrap: bool`.
- Produces:
  - `FileChange(path: PurePosixPath, before: bytes | None, after: bytes | None)` where `after=None` means deletion.
  - `RenderPlan(status: Literal["current", "drift", "bootstrap_required", "blocked"], reason: str, changes: tuple[FileChange, ...], required_secrets: frozenset[str], required_variables: frozenset[str])` with `after(path: str) -> bytes | None`, which returns the proposed bytes for one changed path and raises `KeyError` when the path is absent.
  - `render_repository(repo: Path, canonical: Path, catalog: WorkflowCatalog, profile: RepoProfile, release_ref: str, release_commit: str, secret_names: set[str], variable_names: set[str], *, bootstrap: bool = False) -> RenderPlan`.
  - `apply_render_plan(repo: Path, plan: RenderPlan) -> tuple[PurePosixPath, ...]`.

- [ ] **Step 1: Write failing renderer tests around the public API**

Cover these exact cases:

```python
ROOT = Path(__file__).resolve().parents[1]
CATALOG = load_catalog(ROOT)
FLEET = load_fleet_config(ROOT, CATALOG)
PROFILES = FLEET.profiles
CANONICAL = ROOT / FLEET.canonical_dir
COMMIT = "a" * 40

def make_existing_repo(root: Path, *, config: bytes = b"automation_ref: v1.39\n") -> Path:
    workflow_dir = root / ".github/workflows"
    workflow_dir.mkdir(parents=True)
    (root / ".github/workflow-config.yml").write_bytes(config)
    (workflow_dir / "project-build.yml").write_bytes(b"name: build\non: push\njobs: {}\n")
    return root

def render_fixture(root: Path, *, auth: str) -> RenderPlan:
    repo = make_existing_repo(root)
    profile = dataclasses.replace(PROFILES["wlan-package"], repo_write_auth=auth)
    return render_repository(
        repo, CANONICAL, CATALOG, profile, "v1.40", COMMIT,
        {"CLAUDE_CODE_OAUTH_TOKEN", "GEMINI_API_KEY", "ZHIPU_API_KEY", "APP_PRIVATE_KEY"},
        {"APP_ID"},
    )

def render_existing(root: Path, *, config: bytes) -> RenderPlan:
    repo = make_existing_repo(root, config=config)
    return render_repository(
        repo, CANONICAL, CATALOG, PROFILES["wlan-package"], "v1.40", COMMIT,
        {"CLAUDE_CODE_OAUTH_TOKEN", "GEMINI_API_KEY", "ZHIPU_API_KEY", "APP_PRIVATE_KEY"},
        {"APP_ID"},
    )

def test_app_and_token_profiles_differ_only_by_declared_auth_lines(tmp_path: Path) -> None:
    app = render_fixture(tmp_path / "app", auth="github_app")
    token = render_fixture(tmp_path / "token", auth="github_token")
    app_text = app.after(".github/workflows/gemini-review.yml").decode()
    token_text = token.after(".github/workflows/gemini-review.yml").decode()
    assert "repo_write_auth: github_app" in app_text
    assert "app_id: ${{ vars.APP_ID }}" in app_text
    assert "APP_PRIVATE_KEY: ${{ secrets.APP_PRIVATE_KEY }}" in app_text
    assert "repo_write_auth: github_token" in token_text
    assert "app_id:" not in token_text
    assert "APP_PRIVATE_KEY:" not in token_text
    assert "GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}" in token_text

def test_config_preserves_every_non_identity_byte(tmp_path: Path) -> None:
    original = b"# keep\nautomation_ref: v1.39 # keep comment\ncustom:\n  value: x\n"
    plan = render_existing(tmp_path, config=original)
    rendered = plan.after(".github/workflow-config.yml")
    assert rendered == (
        b"# keep\nautomation_ref: v1.40 # keep comment\n"
        + f"automation_commit: {COMMIT}\n".encode()
        + b"custom:\n  value: x\n"
    )
```

Add tests for: all 19 profiles render deterministically; second render is `current`; selected optional presence; unselected optional deletion; retired bump deletion; required caller creation; unknown central caller block; malformed/duplicate identity scalar block; missing non-bootstrap config block; project-owned byte preservation; missing prerequisite names block for normal repos; disabled bootstrap succeeds only for the two allowed profiles and only when `bootstrap=True`; bootstrap with any existing central caller blocks; provider secret sentinel values never appear in `repr(plan)`, file bytes, or error text.

- [ ] **Step 2: Run renderer tests red**

Run: `rtk python3 -m pytest tests/test_prepare_workflow_rollout.py -q`

Expected: import/signature failures because the old line editor has no catalog/profile renderer.

- [ ] **Step 3: Implement closed-path rendering**

Use immutable dataclasses and validate inputs before constructing changes:

```python
SHA40 = re.compile(r"[0-9a-f]{40}")
CENTRAL_USE = re.compile(
    r"jhw7500/automation/\.github/workflows/(?P<name>[^@\s'\"]+)@(?P<ref>[^\s'\"]+)"
)

def selected_entries(catalog: WorkflowCatalog, profile: RepoProfile) -> tuple[CatalogEntry, ...]:
    return tuple(
        entry for entry in catalog.entries
        if entry.kind in {"required", "config"}
        or (entry.kind == "optional" and entry.path.name in profile.optional_workflows)
    )

def render_caller(template: bytes, entry: CatalogEntry, profile: RepoProfile,
                  release_commit: str) -> bytes:
    if SHA40.fullmatch(release_commit) is None:
        raise RolloutError("release commit must be 40 lowercase hex characters")
    text = template.decode("utf-8")
    if text.count("@__AUTOMATION_COMMIT__") != 1:
        raise RolloutError(f"{entry.path}: expected one commit placeholder")
    text = text.replace("@__AUTOMATION_COMMIT__", f"@{release_commit}")
    if entry.auth_family == "gemini" and profile.repo_write_auth == "github_token":
        text = replace_once(text, "repo_write_auth: github_app", "repo_write_auth: github_token")
        text = delete_line_once(text, "app_id: ${{ vars.APP_ID }}")
        text = delete_line_once(text, "APP_PRIVATE_KEY: ${{ secrets.APP_PRIVATE_KEY }}")
    return text.encode("utf-8")
```

Before returning, parse every proposed YAML document with `yaml.BaseLoader`, compare its trigger and `extract_caller_jobs()` result to `expected_caller_jobs(entry, profile)`, and reject any changed path outside `catalog.managed_paths`. Scan every repo workflow for `CENTRAL_USE`; if its path is not a catalog caller path, return `blocked` with the relative path.

- [ ] **Step 4: Implement identity-only config editing and prerequisite calculation**

For an existing non-bootstrap repository, require exactly one top-level `automation_ref`; update it in place and insert or update exactly one top-level `automation_commit` immediately after it. Preserve the matched line's spacing/comment and all other bytes. For an explicit caller-free bootstrap, copy the canonical config and replace exactly one `__AUTOMATION_REF__` and one `__AUTOMATION_COMMIT__`; never run the identity-only editor against a missing config. Prerequisites are derived from selected callers, never local environment values:

```python
required_secrets = {"CLAUDE_CODE_OAUTH_TOKEN", "GEMINI_API_KEY"}
if any(entry.auth_family == "opencode" for entry in selected):
    required_secrets.add("ZHIPU_API_KEY")
required_variables: set[str] = set()
if profile.repo_write_auth == "github_app":
    required_secrets.add("APP_PRIVATE_KEY")
    required_variables.add("APP_ID")
```

For a normal repo, missing names return `blocked` before file writes. For an explicit disabled bootstrap, record missing names in `reason` as non-blocking prerequisites and render only required callers plus the disabled config.

- [ ] **Step 5: Implement guarded application**

`apply_render_plan` accepts only `drift` or `bootstrap_required`, re-reads each current file and requires exact equality with `FileChange.before`, creates parents, writes through a same-directory temporary file plus `os.replace`, and unlinks only catalogued deletion paths. Refuse symlinks at every managed path and return the sorted paths actually changed.

- [ ] **Step 6: Run renderer tests green and regression tests**

Run:

```bash
rtk python3 -m pytest tests/test_prepare_workflow_rollout.py tests/test_workflow_catalog.py tests/test_canonical_workflow_tree.py -q
rtk git diff --check
```

Expected: all pass and `git diff --check` is silent.

- [ ] **Step 7: Commit the renderer rewrite**

```bash
rtk git add scripts/prepare_workflow_rollout.py tests/test_prepare_workflow_rollout.py
rtk git commit -m "feat: render managed workflow profiles deterministically"
```

---

### Task 5: Implement Content Audit and Verified Release Bundles

**Files:**
- Create: `scripts/workflow_release_bundle.py`
- Create: `tests/test_workflow_release_bundle.py`
- Rewrite: `scripts/audit_workflow_fleet.py`
- Rewrite: `tests/test_audit_workflow_fleet.py`
- Modify: `scripts/verify_workflow_release.py`
- Modify: `tests/test_verify_workflow_release.py`

**Interfaces:**
- Consumes: a local automation Git repository, release tag, optional remote, repo checkout, profile, and prerequisite name inventories.
- Produces:
  - `ReleaseBundle(root: Path, ref: str, commit: str, catalog: WorkflowCatalog, config: FleetConfig, canonical: Path)` as a context-managed temporary extraction.
  - `materialize_release_bundle(automation: Path, ref: str, *, remote: str | None) -> AbstractContextManager[ReleaseBundle]`.
  - `AuditResult(repo: str, status: Literal["current", "drift", "blocked"], detail: str, changed_paths: tuple[str, ...])`.
  - `audit_repository(repo: Path, bundle: ReleaseBundle, profile: RepoProfile, secret_names: set[str], variable_names: set[str]) -> AuditResult`.

- [ ] **Step 1: Write failing bundle and audit tests**

Create a temporary Git release containing the exact required paths and annotated `v1.40` tag. Assert that extraction reads the catalog/config/canonical tree from the tag rather than a newer working-tree commit. Add failures for a lightweight tag, local/remote tag mismatch, absent canonical path, profile inventory outside the tag, and path traversal in archive entries.

For audit, use renderer fixtures:

```python
ALL_SECRETS = {
    "CLAUDE_CODE_OAUTH_TOKEN", "GEMINI_API_KEY", "ZHIPU_API_KEY", "APP_PRIVATE_KEY",
}

def test_audit_classifies_content_not_history(repo, bundle, profile) -> None:
    result = audit_repository(repo, bundle, profile, ALL_SECRETS, {"APP_ID"})
    assert result.status == "drift"
    plan = render_repository(
        repo, bundle.canonical, bundle.catalog, profile,
        bundle.ref, bundle.commit, ALL_SECRETS, {"APP_ID"}, bootstrap=False,
    )
    apply_render_plan(repo, plan)
    assert audit_repository(repo, bundle, profile, ALL_SECRETS, {"APP_ID"}).status == "current"
    (repo / ".github/workflows/project-build.yml").write_text("on: push\n")
    assert audit_repository(repo, bundle, profile, ALL_SECRETS, {"APP_ID"}).status == "current"
```

Assert unknown central callers and malformed configs are `blocked`, while an ordinary managed byte mismatch is `drift`.

- [ ] **Step 2: Run bundle/audit tests red**

Run: `rtk python3 -m pytest tests/test_workflow_release_bundle.py tests/test_audit_workflow_fleet.py -q`

Expected: missing module/new API failures.

- [ ] **Step 3: Implement safe release extraction**

Resolve and verify an annotated tag, call `verify_tag_content(automation, ref)` before extraction, then archive only these release-owned paths:

```python
RELEASE_PATHS = (
    ".github/workflows",
    ".github/actions/setup-gemini-auth/action.yml",
    "examples/baseline-workflows/.github",
    "scripts/workflow-catalog.json",
    "scripts/workflow-config.json",
)
```

Use `git cat-file -t refs/tags/<ref>` to require `tag`, `git rev-parse <ref>^{commit}` for the 40-character commit, and `git ls-remote --tags` when `remote` is set. Extract with `git archive` into a fresh temporary directory; reject absolute paths, `..`, symlink/hardlink members, and unexpected top-level paths before writing. Load catalog/config from the extracted root.

- [ ] **Step 4: Rewrite audit as a renderer comparison**

Call `render_repository(repo, bundle.canonical, bundle.catalog, profile, bundle.ref, bundle.commit, secret_names, variable_names, bootstrap=False)` without applying it. Map `current` directly, map `drift` and `bootstrap_required` to audit status `drift` with sorted paths/reason, and map every render block to `blocked`. Audit reports no branch, PR, merge method, commit topology, or journal fields.

- [ ] **Step 5: Extend the release verifier**

`verify_release()` must require `git cat-file -t refs/tags/<ref>` to return `tag`, so lightweight tags fail. `verify_tag_content()` must load the catalog and canonical tree from the tag and run the same contract checks as Tasks 1–3. Add central checks that all Gemini reusable workflows expose the explicit mode, declare only `APP_PRIVATE_KEY`/`GEMINI_API_KEY`, contain no Google/GCP/OIDC/ambient App fallback, and pin `setup-gemini-auth` to `2254f13aab44585c78954d20749f4fb677a8c2f1`. Keep all existing OpenCode checks unchanged. Add negative tagged-release fixtures for `GOOGLE_API_KEY`, `id-token: write`, `vars.APP_ID`, an unpinned setup action, and a missing `repo_write_auth` input.

- [ ] **Step 6: Run focused and full release tests green**

Run:

```bash
rtk python3 -m pytest tests/test_workflow_release_bundle.py tests/test_audit_workflow_fleet.py tests/test_verify_workflow_release.py tests/test_workflow_secret_contracts.py -q
rtk git diff --check
```

Expected: all pass.

- [ ] **Step 7: Commit bundle and audit support**

```bash
rtk git add scripts/workflow_release_bundle.py scripts/audit_workflow_fleet.py scripts/verify_workflow_release.py tests/test_workflow_release_bundle.py tests/test_audit_workflow_fleet.py tests/test_verify_workflow_release.py
rtk git commit -m "feat: audit tagged workflow release content"
```

---

### Task 6: Add the Restricted Git and GitHub Adapter

**Files:**
- Create: `scripts/workflow_fleet_git.py`
- Create: `tests/test_workflow_fleet_git.py`

**Interfaces:**
- Consumes: owner `jhw7500`, configured repository name, disposable workspace, release branch name, and ordinary operator Git/`gh` authentication.
- Produces:
  - `RepositorySnapshot(path: Path, default_branch: str, base_sha: str, secret_names: frozenset[str], variable_names: frozenset[str])`.
  - `PullRequest(number: int, url: str, state: str, base: str, head: str, title: str, body: str)`.
  - `clone_default_branch(owner: str, repo: str, workspace: Path) -> RepositorySnapshot`.
  - `refetch_default(snapshot: RepositorySnapshot) -> str`.
  - `remote_branch_sha(snapshot: RepositorySnapshot, branch: str) -> str | None`.
  - `push_new_branch(snapshot: RepositorySnapshot, branch: str) -> str`.
  - `list_rollout_prs(owner: str, repo: str, branch: str) -> tuple[PullRequest, ...]`.
  - `create_pull_request(owner: str, repo: str, base: str, head: str, title: str, body: str) -> PullRequest`.

- [ ] **Step 1: Write failing child-boundary tests**

Patch `subprocess.run` and seed all provider values with sentinels. Assert every child environment excludes them while retaining normal operator authentication:

```python
PROVIDER_KEYS = {
    "CLAUDE_CODE_OAUTH_TOKEN", "GEMINI_API_KEY", "GOOGLE_API_KEY",
    "ZHIPU_API_KEY", "APP_PRIVATE_KEY",
}

def test_child_env_scrubs_provider_credentials(monkeypatch) -> None:
    calls = []
    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout="{}\n", stderr="")
    monkeypatch.setattr(workflow_fleet_git.subprocess, "run", fake_run)
    for key in PROVIDER_KEYS:
        monkeypatch.setenv(key, f"sentinel-{key}")
    monkeypatch.setenv("GH_TOKEN", "operator-github-token")
    run(["gh", "repo", "view", "jhw7500/wlan-package"])
    env = calls[0][1]["env"]
    assert PROVIDER_KEYS.isdisjoint(env)
    assert env["GH_TOKEN"] == "operator-github-token"
```

Add command tests proving clone uses `--no-recurse-submodules`, every Git invocation disables hooks and recursive submodules, the origin is exactly `https://github.com/jhw7500/<configured-name>.git` or its SSH equivalent reported by `gh`, secret/variable calls are list-only, push has no force option and no default-branch refspec, and no method constructs merge/revert/secret-set/variable-set requests.

- [ ] **Step 2: Run adapter tests red**

Run: `rtk python3 -m pytest tests/test_workflow_fleet_git.py -q`

Expected: module import failure.

- [ ] **Step 3: Implement scrubbed process execution and repository reads**

Use a fixed provider-key denylist and never include an input value in an error message:

```python
def child_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key not in PROVIDER_KEYS}

def run(args: Sequence[str], *, cwd: Path | None = None, stdin: str | None = None) -> str:
    completed = subprocess.run(
        list(args), cwd=cwd, input=stdin, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=child_env(),
    )
    if completed.returncode:
        raise FleetGitError(f"command failed ({Path(args[0]).name}, rc={completed.returncode})")
    return completed.stdout.strip()
```

Use `gh repo view --json defaultBranchRef,url` and `gh secret list`/`gh variable list --json name` only. Clone into a workspace that contains `.automation-fleet-workspace`, with `git clone --no-recurse-submodules --single-branch --branch <default>`. Set `core.hooksPath=/dev/null` and `submodule.recurse=false`, verify origin owner/name, and reject symlinked clone roots.

- [ ] **Step 4: Implement branch/PR reads and creation-only writes**

`push_new_branch` must first prove the remote branch is absent, create a local branch from the freshly fetched base, and run only:

```text
git push --set-upstream origin HEAD:refs/heads/automation/common-workflows-v1.40
```

Do not accept a force parameter. Query all PR states for the exact head branch with `gh pr list --state all --json number,url,state,baseRefName,headRefName,title,body,isDraft,mergedAt`. `create_pull_request` uses only `gh pr create --base ... --head ... --title ... --body-file <0600 temp file>` so the body is not placed in argv.

- [ ] **Step 5: Run adapter tests green**

Run: `rtk python3 -m pytest tests/test_workflow_fleet_git.py -q`

Expected: all pass.

- [ ] **Step 6: Commit the adapter**

```bash
rtk git add scripts/workflow_fleet_git.py tests/test_workflow_fleet_git.py
rtk git commit -m "feat: add restricted workflow fleet git adapter"
```

---

### Task 7: Rewrite Fleet Orchestration as Plan and PR-Only Publish

**Files:**
- Rewrite: `scripts/rollout_workflow_fleet.py`
- Rewrite: `tests/test_rollout_workflow_fleet.py`
- Modify: `scripts/audit_workflow_fleet.py`
- Modify: `tests/test_audit_workflow_fleet.py`

**Interfaces:**
- Consumes: Tasks 4–6 APIs plus CLI `--mode plan|publish --ref v1.40 --repo NAME ... --workspace PATH`, `--initialize-workspace`, `--confirm`, optional `--bootstrap-repo NAME`, optional `--manifest PATH`, and optional `--actionlint PATH`.
- Produces: `RepoOutcome(repo, status, detail, base_sha="", head_sha="", pr_url="", changed_paths=())`, convenience JSON report, and exit code 1 iff any selected repository is `blocked`.

- [ ] **Step 1: Replace secret-sync tests with failing PR-only behavior tests**

Delete tests for `secret_source`, `sync_missing`, `refresh_secrets`, and `prepare` mode. Add parser tests proving all removed flags fail before any command:

```python
@pytest.mark.parametrize("flag", [
    "--sync-missing-secrets", "--allow-personal-oauth-fanout",
    "--allow-env-secret", "--refresh-secret",
])
def test_legacy_secret_flags_are_rejected(flag: str) -> None:
    with mock.patch("scripts.workflow_fleet_git.subprocess.run") as child:
        with pytest.raises(SystemExit):
            main(["--mode", "publish", "--workspace", str(WORK), flag])
        child.assert_not_called()
```

Add tests for: plan has no remote mutation; publish requires `--confirm` and an explicit `--repo`; multi-repo publish prevalidates all selected repos; each selected repo is refetched before write; exact absent branch creates branch/commit/PR; matching branch with no PR creates only the PR; one exact open PR is reused; mismatched content/base/title/body, multiple PRs, or closed/merged PR with branch present blocks; no force/default push; partial success is reported and rerunnable; bootstrap requires exactly one allowed repo and `--bootstrap-repo`; and every forbidden command sentinel (`merge`, `auto-merge`, `update-branch`, `secret set`, `variable set`) fails the test.

- [ ] **Step 2: Run orchestration tests red**

Run: `rtk python3 -m pytest tests/test_rollout_workflow_fleet.py tests/test_audit_workflow_fleet.py -q`

Expected: old secret/prepare behavior and force-with-lease assertions fail.

- [ ] **Step 3: Implement the narrowed CLI and report model**

Use only `plan` and `publish` modes. Derive the branch with a strict version-tag parser:

```python
VERSION_REF = re.compile(r"v[0-9]+(?:\.[0-9]+)+")

def rollout_branch(ref: str) -> str:
    if VERSION_REF.fullmatch(ref) is None:
        raise CommandError(f"invalid release ref: {ref}")
    return f"automation/common-workflows-{ref}"
```

Load catalog/profile/canonical data only from `materialize_release_bundle(args.automation, args.ref, remote="origin")`. Remove `--config`, `--mode prepare`, all secret flags/functions, and `synced_secrets`. Initialize only a marked disposable workspace. `plan` writes release commit, observed base, status, reason, required name sets, and managed diff paths; it is explicitly non-authoritative and is never accepted by publish as an approval token.

- [ ] **Step 4: Implement exact branch and PR idempotency**

Use constants derived solely from ref/commit:

```python
def pr_title(ref: str) -> str:
    return f"ci: adopt common automation workflows ({ref})"

def pr_body(ref: str, commit: str, changed_paths: Sequence[str]) -> str:
    paths = "\n".join(f"- `{path}`" for path in sorted(changed_paths))
    return (
        "Standardize only the catalogued common AI workflow callers.\n\n"
        f"- automation tag: `{ref}`\n- automation commit: `{commit}`\n"
        f"- managed paths:\n{paths}\n\n"
        "Project-specific workflows are unchanged. This PR does not modify secrets. "
        "Merge and recovery use this repository's normal GitHub controls.\n"
    )
```

For an existing branch, fetch it and require: its single parent equals the freshly observed base; its changed-path set exactly equals the render plan; every managed blob/deletion equals the plan; and it changes no project-owned path. Reuse exactly one open PR only when base, head, title, and body match. Never update or replace a mismatched branch.

- [ ] **Step 5: Implement prevalidation, per-repo refetch, and partial outcome reporting**

First clone/render/validate every selected repo and collect blocks without writes. If any selected repo is blocked, return non-zero with zero new branches/PRs. Otherwise process in requested order: refetch one repo, recompute render/inventory, then create or reuse its branch/PR. A network failure after earlier PRs records those earlier outcomes and a blocked current outcome; remaining repos continue. Rerun reuses exact branches/PRs.

Before push run YAML parse, catalog audit, `git diff --check`, and actionlint against only the managed result. Fail closed when actionlint is absent or returns diagnostics.

- [ ] **Step 6: Make audit's fleet CLI use the same release/profile/read adapter**

Support:

```text
audit_workflow_fleet.py --automation PATH --workspace PATH --ref v1.40 [--repo NAME ...]
```

With no repo, audit all 19. It clones/fetches default branches, lists prerequisite names, calls `audit_repository`, prints a count summary, writes no remote object, and returns non-zero for `blocked` but not for `drift`.

- [ ] **Step 7: Run orchestration and audit tests green**

Run:

```bash
rtk python3 -m pytest tests/test_rollout_workflow_fleet.py tests/test_workflow_fleet_git.py tests/test_audit_workflow_fleet.py -q
rtk git diff --check
```

Expected: all pass; no test observes merge, force, default-branch, secret-write, or variable-write behavior.

- [ ] **Step 8: Commit PR-only orchestration**

```bash
rtk git add scripts/rollout_workflow_fleet.py scripts/audit_workflow_fleet.py tests/test_rollout_workflow_fleet.py tests/test_audit_workflow_fleet.py
rtk git commit -m "feat: publish workflow rollout pull requests only"
```

---

### Task 8: Retire Legacy Writers and Align Documentation and CI

**Files:**
- Rewrite: `scripts/setup-github-workflows.sh`
- Rewrite: `scripts/sync-secrets.sh`
- Rewrite: `docs/workflow-fleet-rollout.md`
- Modify: `docs/workflows/contracts.md`
- Modify: `examples/baseline-workflows/README.md`
- Modify: `docs/superpowers/specs/2026-08-13-common-workflow-standardization-design.md`
- Modify: `.github/workflows/test-fleet-tools.yml`
- Create: `tests/test_legacy_workflow_writers.py`

**Interfaces:**
- Consumes: new `plan`, `publish`, and `audit` commands.
- Produces: side-effect-free migration guards, accurate operator commands, and CI that runs the complete fleet/release gates.

- [ ] **Step 1: Add failing legacy-writer and documentation tests**

Assert both legacy shell scripts exit non-zero without invoking `git`, `gh`, `cp`, or reading environment/provider files. Scan source and docs for removed options and stale behavior:

```python
FORBIDDEN = {
    "--sync-missing-secrets", "--refresh-secret", "--allow-env-secret",
    "--allow-personal-oauth-fanout", "--mode prepare", "--force-with-lease",
}

def test_retired_scripts_are_side_effect_free_guards() -> None:
    for script in ("setup-github-workflows.sh", "sync-secrets.sh"):
        result = subprocess.run(["bash", f"scripts/{script}"], text=True, capture_output=True)
        assert result.returncode == 2
        assert "rollout_workflow_fleet.py --mode plan" in result.stderr or "personal-ops/claude-token-sync" in result.stderr
```

Add assertions that rollout docs contain separate sections for workflow PRs and token synchronization, contain all three canaries, and explicitly say merge/revert are GitHub-native.

- [ ] **Step 2: Run the guard/docs tests red**

Run: `rtk python3 -m pytest tests/test_legacy_workflow_writers.py -q`

Expected: both old scripts still mutate workflows/secrets and stale docs contain removed flags.

- [ ] **Step 3: Replace legacy scripts with migration guards**

Use this complete behavior; do not forward arguments or execute another command:

```bash
#!/usr/bin/env bash
set -euo pipefail
cat >&2 <<'EOF'
This writer is retired and performs no changes.
Use scripts/rollout_workflow_fleet.py --mode plan for workflow PR planning.
Workflow rollout never synchronizes credentials; Claude rotation remains in personal-ops/claude-token-sync.
EOF
exit 2
```

`setup-github-workflows.sh` points to plan/publish; `sync-secrets.sh` points only to the separately reviewed `personal-ops/claude-token-sync` lifecycle.

- [ ] **Step 4: Rewrite the operator and consumer documentation**

Document exact plan/publish/audit commands, deterministic branches, statuses, exact-PR reuse/block rules, bootstrap syntax, partial-success semantics, GitHub-native review/merge/revert, and the separate key-sync boundary. Update caller contracts to commit pins, explicit Gemini modes, same-name secret mappings, and the 14-file catalog. Mark the design `Status: approved; implementation planned`.

- [ ] **Step 5: Strengthen fleet CI without adding another policy source**

Update path filters for catalog JSON, canonical YAML, scripts, tests, and workflow docs. Run the full Python suite, parse all YAML, and install actionlint 1.7.12 from the official Linux AMD64 archive only after verifying SHA-256 `8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8`. Run actionlint on central workflows and canonical caller workflows; no `curl | sh`, floating version, or silent skip is allowed.

- [ ] **Step 6: Run docs/CI and full local verification**

Run:

```bash
rtk python3 -m pytest -q
rtk bash -n scripts/setup-github-workflows.sh scripts/sync-secrets.sh
rtk python3 - <<'PY'
from pathlib import Path
import yaml
for root in (Path('.github/workflows'), Path('examples/baseline-workflows/.github/workflows')):
    for path in root.glob('*.y*ml'):
        assert isinstance(yaml.load(path.read_text(), Loader=yaml.BaseLoader), dict), path
print('PASS: all workflow YAML parsed')
PY
rtk git diff --check
```

Expected: full suite passes, shell syntax passes, YAML parse prints PASS, and diff check is silent.

- [ ] **Step 7: Commit migration guards and documentation**

```bash
rtk git add scripts/setup-github-workflows.sh scripts/sync-secrets.sh docs/workflow-fleet-rollout.md docs/workflows/contracts.md examples/baseline-workflows/README.md docs/superpowers/specs/2026-08-13-common-workflow-standardization-design.md .github/workflows/test-fleet-tools.yml tests/test_legacy_workflow_writers.py
rtk git commit -m "docs: adopt pull-request-only workflow rollout"
```

---

### Task 9: Final Security Gate, Automation PR, and Staged Release Handoff

**Files:**
- Modify only when a verification failure identifies a concrete defect in a prior task.
- Record verification output in the automation PR description; do not create a custom rollout journal.

**Interfaces:**
- Consumes: complete implementation, normal GitHub PR review, and post-merge automation commit.
- Produces: reviewed automation PR; after merge, verified immutable `v1.40`; then canary PRs only. This task never merges a consumer PR.

- [ ] **Step 1: Run the complete local gate from a clean tree**

```bash
rtk python3 -m pytest -q
rtk bash -n scripts/setup-github-workflows.sh scripts/sync-secrets.sh
rtk git diff --check
rtk git status --short
```

Expected: tests pass, syntax/diff checks pass, and status is empty. If a test fails, fix it in the owning task's files and rerun that focused test before repeating this gate.

- [ ] **Step 2: Run the actionlint gate exactly as CI does**

Download `actionlint_1.7.12_linux_amd64.tar.gz` into `/tmp/actionlint-v1.7.12`, verify the exact SHA-256 from Task 8, extract `/tmp/actionlint-v1.7.12/actionlint`, and run its deterministic schema/expression gate with `-shellcheck= -pyflakes=` over `.github/workflows/*.yml` and `examples/baseline-workflows/.github/workflows/*.yml`. These empty analyzer paths make the gate independent of optional host ShellCheck/Pyflakes installations; actionlint's own diagnostics remain fail-closed. Keep that verified executable for the plan/publish commands below.

```bash
rtk rm -rf /tmp/actionlint-v1.7.12
rtk mkdir -p /tmp/actionlint-v1.7.12
rtk curl -fsSL -o /tmp/actionlint-v1.7.12/actionlint.tar.gz \
  https://github.com/rhysd/actionlint/releases/download/v1.7.12/actionlint_1.7.12_linux_amd64.tar.gz
rtk bash -lc "printf '%s  %s\n' \
  8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8 \
  /tmp/actionlint-v1.7.12/actionlint.tar.gz | rtk sha256sum -c -"
rtk tar -xzf /tmp/actionlint-v1.7.12/actionlint.tar.gz -C /tmp/actionlint-v1.7.12 actionlint
rtk /tmp/actionlint-v1.7.12/actionlint -shellcheck= -pyflakes= \
  .github/workflows/*.yml examples/baseline-workflows/.github/workflows/*.yml
```

Expected: zero diagnostics and exit code 0.

- [ ] **Step 3: Create the automation implementation PR and stop before release tagging**

Push the feature branch normally and open a PR describing: central contract changes, canonical/catalog/profile sources, renderer/audit behavior, PR-only mutation boundary, removed secret writer, test evidence, and the three canaries. Do not enable auto-merge and do not tag the feature-branch commit.

- [ ] **Step 4: After human merge, verify and publish the immutable central tag**

From a clean automation checkout after fetching `origin/main` and tags:

```bash
rtk bash -lc '
  set -euo pipefail
  MERGE_SHA="$(rtk git rev-parse origin/main)"
  if rtk git show-ref --verify --quiet refs/tags/v1.40; then
    printf "%s\n" "ERROR: local v1.40 already exists; stop without changing it" >&2
    exit 1
  fi
  REMOTE_TAGS="$(rtk git ls-remote --tags origin refs/tags/v1.40 "refs/tags/v1.40^{}")"
  if [ -n "$REMOTE_TAGS" ]; then
    printf "%s\n" "ERROR: remote v1.40 already exists; stop without changing it" >&2
    exit 1
  fi
  rtk git tag -a v1.40 "$MERGE_SHA" -m "automation workflow release v1.40"
  test "$(rtk git cat-file -t refs/tags/v1.40)" = tag
  test "$(rtk git rev-parse refs/tags/v1.40^{commit})" = "$MERGE_SHA"
  rtk python3 scripts/verify_workflow_release.py --automation . --ref v1.40 --expected-commit "$MERGE_SHA"
  rtk git push origin refs/tags/v1.40
  rtk python3 scripts/verify_workflow_release.py --automation . --ref v1.40 --expected-commit "$MERGE_SHA" --remote origin
'
```

Expected: both absence checks pass before creation, the annotated local object and commit
are verified before push, and remote verification passes afterward. Any local/remote
`v1.40` presence or verification failure stops the shell under `set -euo pipefail`; never
move/delete the tag.

- [ ] **Step 5: Run the full read-only fleet plan**

```bash
rtk python3 scripts/rollout_workflow_fleet.py \
  --automation . --workspace /tmp/automation-v1.40-fleet \
  --initialize-workspace --mode plan --ref v1.40 \
  --actionlint /tmp/actionlint-v1.7.12/actionlint
```

Expected: exactly 19 outcomes with only the public plan statuses `current`, `planned`,
`reusable`, or reviewed `blocked`; no remote branch, PR, secret, or variable changes.
Renderer-only `drift`/`bootstrap_required` are not public plan statuses, and missing config
without explicit bootstrap is `blocked`. Stop on any unexplained block or project-owned
path.

- [ ] **Step 6: Create and approve the `wlan-package` canary first**

```bash
rtk python3 scripts/rollout_workflow_fleet.py \
  --automation . --workspace /tmp/automation-v1.40-fleet \
  --mode publish --ref v1.40 \
  --repo wlan-package --confirm \
  --actionlint /tmp/actionlint-v1.7.12/actionlint
```

Expected: one independent App-auth branch/PR outcome. Stop without merging; repository
owners review, run CI, and merge through GitHub. After human merge, use harmless repository
PRs/manual dispatches to record the `wlan-package` Gemini App comment, representative
Claude invocation, OpenCode automatic review, and OpenCode manual command. Audit only
`wlan-package` and require `current` before approving the next canary.

- [ ] **Step 7: Create and approve the `wlan-driver` canary second**

```bash
rtk python3 scripts/rollout_workflow_fleet.py \
  --automation . --workspace /tmp/automation-v1.40-fleet \
  --mode publish --ref v1.40 \
  --repo wlan-driver --confirm \
  --actionlint /tmp/actionlint-v1.7.12/actionlint
```

Expected: one independent built-in GitHub-token branch/PR outcome. Stop without merging.
After human review and merge, record a `wlan-driver` Gemini comment through the built-in
token with no App credentials, audit only `wlan-driver`, and require `current` before the
bootstrap canary is approved.

- [ ] **Step 8: Create the disabled bootstrap canary PR third**

```bash
rtk python3 scripts/rollout_workflow_fleet.py \
  --automation . --workspace /tmp/automation-v1.40-fleet \
  --mode publish --ref v1.40 \
  --repo cts-email-mcp-server --bootstrap-repo cts-email-mcp-server --confirm \
  --actionlint /tmp/actionlint-v1.7.12/actionlint
```

Expected: one PR containing required callers plus a config that disables every common workflow. Stop without enabling or merging it automatically.

- [ ] **Step 9: Handoff the remaining rollout to the approved GitHub process**

After the three canaries are human-reviewed, merged, runtime-checked, and audited `current`, create the remaining 15 ordinary PRs:

```bash
rtk python3 scripts/rollout_workflow_fleet.py \
  --automation . --workspace /tmp/automation-v1.40-fleet \
  --mode publish --ref v1.40 --confirm \
  --repo gstApp --repo max9296 --repo wlan-driver-v2 \
  --repo wlan-bridge --repo wlan-opc --repo pcap-analyzer \
  --repo pim-package-jhw --repo sc16is7xx --repo pim-check \
  --repo redmine --repo jhw-notion --repo personal-ops \
  --repo cts-ta-mcp-server --repo cts-ta-webapp --repo claude-config \
  --actionlint /tmp/actionlint-v1.7.12/actionlint
```

Create `wpa-supplicant` separately as disabled bootstrap:

```bash
rtk python3 scripts/rollout_workflow_fleet.py \
  --automation . --workspace /tmp/automation-v1.40-fleet \
  --mode publish --ref v1.40 \
  --repo wpa-supplicant --bootstrap-repo wpa-supplicant --confirm \
  --actionlint /tmp/actionlint-v1.7.12/actionlint
```

Repository owners merge on their own schedules. Repeat the read-only fleet audit:

```bash
rtk python3 scripts/audit_workflow_fleet.py \
  --automation . --workspace /tmp/automation-v1.40-fleet --ref v1.40
```

Stop only when it reports `current=19`, `drift=0`, `blocked=0`. Before merge, closing the
PR and optionally deleting its branch aborts that repository/release attempt; closed PR
history prevents reuse, so a corrected retry uses a new immutable release/ref. After
merge, recovery is a normal reviewed GitHub revert PR or a new immutable roll-forward
release.

## Final Stop Conditions

- Stop implementation if any test reveals a change outside catalogued managed paths.
- Stop release if local or remote `v1.40` verification fails.
- Stop consumer rollout if plan reports unexplained `blocked`, unknown central callers, permission expansion, secret mapping drift, or project-owned changes.
- Stop after opening PRs. The tool and this plan never merge or revert a consumer PR.
