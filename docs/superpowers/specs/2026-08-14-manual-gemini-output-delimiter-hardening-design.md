# Manual Gemini Output Delimiter Hardening Design

**Status:** implemented; review pending
**Date:** 2026-08-14
**Scope:** `automation` canonical consumer callers only

## Problem

The canonical manual Gemini callers currently write an issue or pull-request title and body
to `$GITHUB_OUTPUT` with the fixed multiline delimiter `EOF`:

- `examples/baseline-workflows/.github/workflows/gemini-issue-triage.yml`
- `examples/baseline-workflows/.github/workflows/gemini-pr-review.yml`

Those values come from repository issue or pull-request content. A body containing a line
equal to `EOF` terminates the output early. The remaining content can be interpreted as
additional output-file commands or can make the prepare job fail. This is a real
availability and output-integrity defect in a manual-dispatch path.

The pattern predates `v1.40.1`; immutable release tags and already-open consumer PRs must not
be moved or rewritten.

## Boundaries

This patch will:

1. change only the two canonical manual Gemini caller files;
2. add behavioral regression tests for hostile multiline content;
3. extend release verification so a future fixed-delimiter regression fails closed; and
4. create a normal reviewable `automation` pull request.

This patch will not:

- edit any consumer repository or existing `v1.40.1` rollout PR;
- create or move an automation release tag;
- modify project-specific build or ShellCheck code;
- read or write a secret, variable, or provider key; or
- merge, revert, or enable auto-merge anywhere.

## Design

Each affected `Fetch issue` or `Fetch PR` step will define the same small Bash helper:

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
```

The step will call `write_output title "$title"` and `write_output body "$body"`.

The delimiter is derived without an external random-number or package dependency. Because
the input is finite and the suffix grows until it is absent from the value, the chosen
delimiter cannot collide with the emitted content. Quoted `printf` preserves the complete
shell-variable value without shell evaluation. Empty values remain representable as valid
multiline outputs.

The helper remains local to each workflow rather than introducing a composite action: two
call sites do not justify another release artifact or runtime dependency.

Both the validation step and fetch step declare `shell: bash`, so workflow or job defaults
cannot reinterpret Bash-specific `[[ ... ]]` syntax. For `v1.40.2` and later, the release
verifier binds the complete `prepare` job: runner, permissions, output mappings, exactly the
validation and safe fetch steps, and the downstream reusable-job title/body expressions.
An alternate writer, output rewiring, or loss of the explicit Bash execution context is a
release-gate failure even if an unused copy of the safe step remains present.

## Validation

Tests will execute the real embedded workflow steps with a controlled `gh` stub and a
temporary `$GITHUB_OUTPUT`. For both callers, hostile title/body fixtures will contain:

- a line exactly equal to `EOF`;
- `name=value` text after that line;
- the initial `__AUTOMATION_OUTPUT__` delimiter; and
- one or more `_X` delimiter variants.

The test parser must reconstruct exactly two outputs whose values equal the controlled `gh`
stub results and must not observe an injected output name. The test must fail against the
current fixed-`EOF` implementation before production files change.

Release-verifier coverage will reject restoration of the fixed delimiter, removal of the
collision check, alternate-writer/output rewiring, downstream value rewiring, or loss of
the explicit Bash context. Existing YAML parsing, actionlint 1.7.12, focused tests, the full
Python suite, Ruff, `py_compile`, and `git diff --check` remain required gates.

## Delivery and Recovery

The implementation is delivered as one `automation` PR based on current `main`. Existing
`v1.40.1` tags and consumer PRs remain unchanged. After human review and merge, publishing a
new immutable patch release such as `v1.40.2` and deciding whether to re-roll the fleet is a
separate operator decision.

Before merge, closing the automation PR abandons the patch with no consumer effect. After
merge, correction uses another normal automation PR and a new immutable patch tag; existing
tags are never moved.
