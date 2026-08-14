# Manual Gemini Output Delimiter Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the two canonical manual Gemini callers preserve arbitrary issue and pull-request title/body text in `$GITHUB_OUTPUT` without allowing a content line to terminate the output record.

**Architecture:** Keep the change local to each existing fetch step: a Bash `write_output` helper derives a collision-free delimiter from the finite value, then emits the value only through quoted `printf`. Execute the actual YAML-embedded scripts in regression tests, and make the authenticated release verifier require the exact hardened fetch-step contract only for `v1.40.2` and later so immutable `v1.40`/`v1.40.1` remain verifiable.

**Tech Stack:** GitHub Actions YAML, Bash, Python 3, pytest, PyYAML, actionlint 1.7.12.

## Global Constraints

- Modify only the two canonical manual Gemini caller workflows, their tests, and the release verifier; do not edit consumer repositories or existing `v1.40.1` rollout PRs.
- Do not create or move a release tag, mutate secrets or variables, merge a PR, or enable auto-merge.
- Treat issue and pull-request title/body as untrusted data and never interpolate them into Bash source.
- Preserve `v1.40` and `v1.40.1` as immutable historical artifacts; apply the new static release contract only to `v1.40.2` and later.
- Use strict RED→GREEN TDD and retain all existing release-object authentication boundaries.

---

### Task 1: Execute the Canonical Fetch Steps Against Hostile Content

**Files:**
- Modify: `tests/test_legacy_workflow_writers.py`
- Modify: `examples/baseline-workflows/.github/workflows/gemini-issue-triage.yml:29-45`
- Modify: `examples/baseline-workflows/.github/workflows/gemini-pr-review.yml:33-49`

**Interfaces:**
- Consumes: `_workflow_step(path: Path, name: str) -> dict` and `_parse_github_outputs(text: str) -> dict[str, str]` from `tests/test_legacy_workflow_writers.py`.
- Produces: both fetch steps expose exactly `title` and `body` outputs whose values are byte-for-byte equal after Bash command-substitution newline semantics; each step contains a local `write_output(name, value)` Bash function.

- [ ] **Step 1: Add the real-step hostile-content regression test**

Add a canonical root constant and a parametrized test that executes both embedded `run` scripts through `/bin/bash -eu -o pipefail`. The fake `gh` executable must select `HOSTILE_TITLE` or `HOSTILE_BODY` from the `--json title|body` argument and print it with `printf '%s'`:

```python
CANONICAL_WORKFLOWS = (
    ROOT / "examples" / "baseline-workflows" / ".github" / "workflows"
)


@pytest.mark.parametrize(
    ("filename", "step_name"),
    (
        ("gemini-issue-triage.yml", "Fetch issue"),
        ("gemini-pr-review.yml", "Fetch PR"),
    ),
)
def test_manual_gemini_fetch_preserves_hostile_multiline_outputs(
    tmp_path: Path, filename: str, step_name: str
) -> None:
    step = _workflow_step(CANONICAL_WORKFLOWS / filename, step_name)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        "while (($#)); do\n"
        "  if [[ $1 == --json ]]; then\n"
        "    case $2 in\n"
        "      title) printf '%s' \"$HOSTILE_TITLE\" ;;\n"
        "      body) printf '%s' \"$HOSTILE_BODY\" ;;\n"
        "      *) exit 96 ;;\n"
        "    esac\n"
        "    exit 0\n"
        "  fi\n"
        "  shift\n"
        "done\n"
        "exit 97\n",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    title = (
        "title-before\nEOF\ninjected_title=value\n"
        "__AUTOMATION_OUTPUT__\n__AUTOMATION_OUTPUT___X\ntitle-after"
    )
    body = (
        "body-before\nEOF\ninjected_body=value\n"
        "__AUTOMATION_OUTPUT__\n__AUTOMATION_OUTPUT___X\nbody-after"
    )
    output = tmp_path / "github-output"
    completed = subprocess.run(
        ["/bin/bash", "-eu", "-o", "pipefail", "-c", step["run"]],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "GITHUB_OUTPUT": str(output),
            "HOSTILE_TITLE": title,
            "HOSTILE_BODY": body,
            "GH_TOKEN": "sentinel-token",
            "REPO": "jhw7500/example",
            "ISSUE_NUMBER": "17",
            "PR_NUMBER": "19",
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert _parse_github_outputs(output.read_text(encoding="utf-8")) == {
        "title": title,
        "body": body,
    }
```

- [ ] **Step 2: Run the regression test and capture RED**

Run:

```bash
rtk python3 -m pytest -q tests/test_legacy_workflow_writers.py::test_manual_gemini_fetch_preserves_hostile_multiline_outputs
```

Expected: both parametrized cases fail against the fixed `EOF` implementation because the hostile value terminates the first record and leaves extra output-file commands.

- [ ] **Step 3: Replace the fixed delimiters with the minimal helper in both workflows**

Keep each existing pair of `gh ... --json title|body` commands, then use this exact helper and calls:

```bash
write_output() {
  local name="$1"
  local value="$2"
  local delimiter='__AUTOMATION_OUTPUT__'
  while [[ "$value" == *"$delimiter"* ]]; do
    delimiter="${delimiter}_X"
  done
  {
    printf '%s<<%s\n' "$name" "$delimiter"
    printf '%s\n' "$value"
    printf '%s\n' "$delimiter"
  } >> "$GITHUB_OUTPUT"
}

write_output title "$title"
write_output body "$body"
```

- [ ] **Step 4: Run the focused behavioral and canonical-contract tests**

Run:

```bash
rtk python3 -m pytest -q \
  tests/test_legacy_workflow_writers.py::test_manual_gemini_fetch_preserves_hostile_multiline_outputs \
  tests/test_canonical_workflow_tree.py
```

Expected: all tests pass; parsed outputs contain only `title` and `body` with the exact hostile values.

- [ ] **Step 5: Commit the behavioral fix**

```bash
rtk git add tests/test_legacy_workflow_writers.py \
  examples/baseline-workflows/.github/workflows/gemini-issue-triage.yml \
  examples/baseline-workflows/.github/workflows/gemini-pr-review.yml
rtk git commit -m "fix: harden manual Gemini outputs"
```

---

### Task 2: Fail Closed on Future Release Regressions

**Files:**
- Modify: `tests/test_verify_workflow_release.py`
- Modify: `scripts/verify_workflow_release.py`

**Interfaces:**
- Consumes: authenticated `VerifiedCommitTree`, `_release_version(ref)`, and YAML loaded with `yaml.BaseLoader`.
- Produces: `_verify_manual_gemini_output_contract(tree: VerifiedCommitTree, ref: str) -> None`, called by `_verify_commit_content`; it accepts historical refs below `v1.40.2` and requires the exact two hardened fetch-step mappings for `v1.40.2` and later.

- [ ] **Step 1: Add negative release tests before changing the verifier**

Keep the existing `release_repo` fixture historically accurate by replacing the newly
hardened physical block with the exact pre-patch fixed-`EOF` block immediately after it
copies `RELEASE_PATHS`. This is test-only reconstruction of the immutable `v1.40` policy
snapshot; the production verifier must still reject that pattern for `v1.40.2` and later:

```python
HARDENED_MANUAL_OUTPUT_BLOCK = """          write_output() {
            local name="$1"
            local value="$2"
            local delimiter='__AUTOMATION_OUTPUT__'
            while [[ "$value" == *"$delimiter"* ]]; do
              delimiter="${delimiter}_X"
            done
            {
              printf '%s<<%s\\n' "$name" "$delimiter"
              printf '%s\\n' "$value"
              printf '%s\\n' "$delimiter"
            } >> "$GITHUB_OUTPUT"
          }

          write_output title "$title"
          write_output body "$body"
"""
LEGACY_MANUAL_OUTPUT_BLOCK = """          echo "title<<EOF" >> "$GITHUB_OUTPUT"
          echo "$title" >> "$GITHUB_OUTPUT"
          echo "EOF" >> "$GITHUB_OUTPUT"

          echo "body<<EOF" >> "$GITHUB_OUTPUT"
          echo "$body" >> "$GITHUB_OUTPUT"
          echo "EOF" >> "$GITHUB_OUTPUT"
"""


def restore_historical_v140_manual_outputs(repo: Path) -> None:
    root = repo / "examples/baseline-workflows/.github/workflows"
    for filename in ("gemini-issue-triage.yml", "gemini-pr-review.yml"):
        replace(
            root / filename,
            HARDENED_MANUAL_OUTPUT_BLOCK,
            LEGACY_MANUAL_OUTPUT_BLOCK,
            count=1,
        )
```

Call `restore_historical_v140_manual_outputs(repo)` before the existing fixture's first
commit. Then create a separate current-content fixture and two mutations:

```python
@pytest.fixture
def current_release_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "current-automation"
    repo.mkdir()
    for relative in RELEASE_PATHS:
        source = ROOT / relative
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    return repo, commit(repo, "current release")
```

```python
@pytest.mark.parametrize(
    ("old", "new"),
    (
        (
            "            while [[ \"$value\" == *\"$delimiter\"* ]]; do\n"
            "              delimiter=\"${delimiter}_X\"\n"
            "            done\n",
            "",
        ),
        (
            "          write_output title \"$title\"\n",
            "          printf 'title<<EOF\\n%s\\nEOF\\n' \"$title\" "
            ">> \"$GITHUB_OUTPUT\"\n",
        ),
    ),
    ids=("missing-collision-loop", "fixed-eof-restored"),
)
def test_commit_gate_rejects_unsafe_manual_gemini_output_writer(
    current_release_repo: tuple[Path, str], old: str, new: str
) -> None:
    repo, _ = current_release_repo
    replace(
        repo / "examples/baseline-workflows/.github/workflows/gemini-pr-review.yml",
        old,
        new,
        count=1,
    )
    bad_commit = commit(repo, "weaken manual Gemini output writer")
    with pytest.raises(ReleaseVerificationError, match="manual Gemini output"):
        release_verifier.verify_commit_content(repo, "v1.40.2", bad_commit)
```

The fixture must initialize a local Git repository, copy exactly `RELEASE_PATHS`, commit them, and return `(repo, commit_oid)`. Also change `test_actual_current_commit_only_uses_authenticated_objects` to verify current `HEAD` as `v1.40.2`; the historical `release_repo` tests remain on `v1.40`/`v1.40.1` and must still pass.

- [ ] **Step 2: Run the new release tests and capture RED**

Run:

```bash
rtk python3 -m pytest -q \
  tests/test_verify_workflow_release.py::test_commit_gate_rejects_unsafe_manual_gemini_output_writer \
  tests/test_verify_workflow_release.py::test_actual_current_commit_only_uses_authenticated_objects
```

Expected: the two mutation cases fail because the verifier has no manual-output contract yet; the current-commit case passes only if called with `v1.40.2` after Task 1.

- [ ] **Step 3: Add an exact authenticated caller-step contract**

In `scripts/verify_workflow_release.py`, define the expected fetch steps as ordinary Python mappings. Each expected mapping must include the current exact `name`, `id`, `env`, and full `run` string. The only differences are:

```python
MANUAL_GEMINI_FETCH_STEPS = {
    "gemini-issue-triage.yml": {
        "step_name": "Fetch issue",
        "step_id": "issue",
        "number_env": ("ISSUE_NUMBER", "${{ inputs.issue_number }}"),
        "fetch": "gh issue view",
    },
    "gemini-pr-review.yml": {
        "step_name": "Fetch PR",
        "step_id": "pr",
        "number_env": ("PR_NUMBER", "${{ inputs.pr_number }}"),
        "fetch": "gh pr view",
    },
}
```

Build the complete expected step from only those closed values and this exact function:

```python
def _expected_manual_fetch_step(contract: dict[str, object]) -> dict[str, object]:
    number_name, number_expression = contract["number_env"]
    assert isinstance(number_name, str)
    assert isinstance(number_expression, str)
    command = contract["fetch"]
    assert isinstance(command, str)
    number_reference = f"${number_name}"
    run = (
        f'title="$({command} "{number_reference}" --repo "$REPO" '
        '--json title --jq .title)"\n'
        f'body="$({command} "{number_reference}" --repo "$REPO" '
        '--json body --jq .body)"\n\n'
        "write_output() {\n"
        '  local name="$1"\n'
        '  local value="$2"\n'
        "  local delimiter='__AUTOMATION_OUTPUT__'\n"
        '  while [[ "$value" == *"$delimiter"* ]]; do\n'
        '    delimiter="${delimiter}_X"\n'
        "  done\n"
        "  {\n"
        "    printf '%s<<%s\\n' \"$name\" \"$delimiter\"\n"
        "    printf '%s\\n' \"$value\"\n"
        "    printf '%s\\n' \"$delimiter\"\n"
        '  } >> "$GITHUB_OUTPUT"\n'
        "}\n\n"
        'write_output title "$title"\n'
        'write_output body "$body"\n'
    )
    return {
        "name": contract["step_name"],
        "id": contract["step_id"],
        "env": {
            "GH_TOKEN": "${{ github.token }}",
            "REPO": "${{ github.repository }}",
            number_name: number_expression,
        },
        "run": run,
    }
```

Implement the verifier with the following closed control flow:

```python
def _verify_manual_gemini_output_contract(
    tree: VerifiedCommitTree, ref: str
) -> None:
    if _release_version(ref) < (1, 40, 2):
        return
    root = "examples/baseline-workflows/.github/workflows"
    for filename, contract in MANUAL_GEMINI_FETCH_STEPS.items():
        path = f"{root}/{filename}"
        try:
            document = yaml.load(tree.read_text(path), Loader=yaml.BaseLoader)
            steps = document["jobs"]["prepare"]["steps"]
        except (ReleaseVerificationError, yaml.YAMLError, KeyError, TypeError):
            raise ReleaseVerificationError(
                f"{path} manual Gemini output contract is invalid"
            ) from None
        matches = [step for step in steps if step.get("id") == contract["step_id"]]
        if len(matches) != 1 or matches[0] != expected_manual_fetch_step(contract):
            raise ReleaseVerificationError(
                f"{path} manual Gemini output contract is invalid"
            )
```

Call `_verify_manual_gemini_output_contract(tree, ref)` in `_verify_commit_content` after the authenticated inventory/setup checks and before returning the tree. Do not weaken `_verify_approved_v140_policy`; `v1.40` and `v1.40.1` must continue to use their existing immutable digest.

- [ ] **Step 4: Run the release-verifier RED tests and historical compatibility tests**

Run:

```bash
rtk python3 -m pytest -q \
  tests/test_verify_workflow_release.py::test_commit_gate_rejects_unsafe_manual_gemini_output_writer \
  tests/test_verify_workflow_release.py::test_actual_current_commit_only_uses_authenticated_objects \
  tests/test_verify_workflow_release.py::test_patch_release_must_preserve_the_approved_v140_policy \
  tests/test_verify_workflow_release.py::test_release_verifier_preserves_pre_inventory_v139_contract
```

Expected: all tests pass; both unsafe `v1.40.2` mutations fail closed internally, while the tests themselves pass and historical releases retain their existing verification behavior.

- [ ] **Step 5: Commit the release gate**

```bash
rtk git add scripts/verify_workflow_release.py tests/test_verify_workflow_release.py
rtk git commit -m "test: gate manual Gemini output safety"
```

---

### Task 3: Verify the Patch and Prepare the Reviewable Automation PR

**Files:**
- Verify: all files changed in Tasks 1-2
- Do not modify: consumer repositories, tags, secrets, variables, or project-specific build files

**Interfaces:**
- Consumes: the two task commits and the approved design at `docs/superpowers/specs/2026-08-14-manual-gemini-output-delimiter-hardening-design.md`.
- Produces: a clean automation feature branch whose diff is limited to the approved patch, with local verification evidence ready for a normal GitHub PR.

- [ ] **Step 1: Run focused and full Python tests**

```bash
rtk python3 -m pytest -q tests/test_legacy_workflow_writers.py \
  tests/test_canonical_workflow_tree.py \
  tests/test_verify_workflow_release.py
rtk python3 -m pytest -q
```

Expected: zero failures and zero errors.

- [ ] **Step 2: Run the pinned actionlint gate**

Run exactly:

```bash
rtk rm -rf /tmp/actionlint-v1.7.12
rtk mkdir -p /tmp/actionlint-v1.7.12
rtk curl -fsSL -o /tmp/actionlint-v1.7.12/actionlint.tar.gz \
  https://github.com/rhysd/actionlint/releases/download/v1.7.12/actionlint_1.7.12_linux_amd64.tar.gz
rtk bash -lc "printf '%s  %s\\n' \
  8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8 \
  /tmp/actionlint-v1.7.12/actionlint.tar.gz | rtk sha256sum -c -"
rtk tar -xzf /tmp/actionlint-v1.7.12/actionlint.tar.gz \
  -C /tmp/actionlint-v1.7.12 actionlint
rtk /tmp/actionlint-v1.7.12/actionlint -shellcheck= -pyflakes= \
  .github/workflows/*.yml \
  examples/baseline-workflows/.github/workflows/*.yml
```

Expected: actionlint reports zero diagnostics.

- [ ] **Step 3: Run static format and syntax gates**

```bash
rtk python3 -m py_compile scripts/verify_workflow_release.py \
  tests/test_legacy_workflow_writers.py tests/test_verify_workflow_release.py
rtk python3 -m ruff check scripts/verify_workflow_release.py \
  tests/test_legacy_workflow_writers.py tests/test_verify_workflow_release.py
rtk python3 - <<'PY'
from pathlib import Path
import yaml
for path in Path('.').glob('examples/baseline-workflows/.github/workflows/*.yml'):
    yaml.safe_load(path.read_text(encoding='utf-8'))
PY
rtk git diff --check origin/main...HEAD
```

Expected: every command exits zero.

- [ ] **Step 4: Review the complete branch diff and stop on scope drift**

```bash
rtk git diff --stat origin/main...HEAD
rtk git diff --name-status origin/main...HEAD
rtk git status --short --branch
```

Expected: only the approved design/plan, two canonical workflows, their tests, and `scripts/verify_workflow_release.py` appear; the tracked worktree is clean. If any consumer, tag, secret, variable, or project build file appears, stop without pushing.

- [ ] **Step 5: Publish only the automation branch and open a non-draft PR**

After all gates pass, push `codex/fix-manual-gemini-output-delimiter` without force, open one PR against `jhw7500/automation:main`, and verify through the GitHub API that the PR is open, non-draft, has `auto_merge: null`, has the expected base/head repository and branch, and contains only the approved changed paths.

Expected: one automation feature branch and one open PR; no tag, merge, consumer, secret, or variable mutation.
