# Change Evidence Contract v1

Status: stable

Identifier: `v1`

Canonical validator: `scripts/validate_change_evidence.py`

Change Evidence Contract v1 defines one model-independent Markdown structure for
Issues, pull requests, and commit messages. Its purpose is to preserve why a change
was needed, what result it produced, how it was verified, and which tracker records
connect to it. Claude, Codex, a human author, or any other producer may choose the
wording, but they must emit the same fields and satisfy the same validation rules.

The words MUST, MUST NOT, SHOULD, and MAY in this document are normative.

## Common envelope

- Documents MUST be UTF-8 Markdown, no larger than 64 KiB, with no NUL byte.
- Contract fields MUST use exact level-three headings (`###`) in the documented order.
- Every heading MUST appear exactly once. Unknown contract headings are invalid.
- Field bodies MUST contain evidence after HTML comments are removed.
- An HTML comment opener (`<!--`) MUST begin a line with at most three leading spaces.
  Literal or inline uses of that token MUST be placed inside a fenced code block.
- `Contract version` MUST be exactly `v1`.
- Placeholder-only values such as `TBD`, `TODO`, `N/A`, `unknown`, `미정`, `추후`,
  or angle-bracket fill instructions are invalid.
- Prose, list items, fenced code blocks, and links MAY appear inside a field. Additional
  Markdown headings MUST NOT be used because they create an ambiguous field boundary.
- Link reference definitions are invalid because they render no field evidence. Use
  inline links when a field needs a URL.
- A checklist, ordered/unordered list item, or reference counts only when rendered as a
  top-level Markdown structure with zero to three leading spaces. Nested structures and
  text inside fenced or indented code do not satisfy a structural field requirement.
- Commit messages MUST begin with a result-oriented title of at most 72 characters,
  followed by a blank line. Generic titles such as `Update`, `Fix`, `WIP`, `수정`, or
  `작업` are invalid. The validator catches these obvious placeholders; authors and
  reviewers remain responsible for whether a less obvious title actually describes a
  result.

## Issue fields

| Order | Heading | Requirement |
| --- | --- | --- |
| 1 | `Contract version` | Exact value `v1`. |
| 2 | `Context and problem` | Observed problem and the evidence that makes it actionable. |
| 3 | `Goal` | Result to achieve, not an implementation activity. |
| 4 | `Non-goals` | Explicit exclusions, or a reasoned `Unknown:`/`Not applicable:` statement. |
| 5 | `Acceptance criteria` | At least one Markdown task-list item. |
| 6 | `Constraints and impact` | Compatibility, affected systems, rollout constraints, or a reasoned `Unknown:`/`Not applicable:` statement. |

The canonical GitHub Issue Form is
`.github/ISSUE_TEMPLATE/change-evidence.yml`. GitHub converts form responses into
Markdown headings, so the form labels are byte-for-byte equal to the headings above.

## Pull request fields

| Order | Heading | Requirement |
| --- | --- | --- |
| 1 | `Contract version` | Exact value `v1`. |
| 2 | `Summary` | Delivered result. |
| 3 | `Changes` | At least one Markdown list item describing a material change. |
| 4 | `Validation` | At least one Markdown list item with an actual command/result, or `Not run: <reason>`. |
| 5 | `Impact and risks` | Compatibility, rollout, remaining risk, or a reasoned `Unknown:`/`Not applicable:` statement. |
| 6 | `Related issue` | An Issue reference such as `Closes #194` or a full GitHub Issue URL. A pull request URL alone is invalid. |

The canonical template is `.github/pull_request_template.md`. Its HTML comments are
instructions only; submitting the unchanged template is invalid because comments do not
count as evidence.

## Commit fields

The first line is the result-oriented title. After the required blank line, these fields
follow:

| Order | Heading | Requirement |
| --- | --- | --- |
| 1 | `Contract version` | Exact value `v1`. |
| 2 | `Why` | Reason the change was needed. |
| 3 | `Changes` | At least one Markdown list item describing a material change. |
| 4 | `Validation` | At least one Markdown list item with an actual command/result, or `Not run: <reason>`. |
| 5 | `References` | At least one Issue or pull request reference. |

`examples/change-evidence/commit-message.md` is the canonical authoring template. A
repository MAY copy it and configure it with `git config commit.template <path>`.

## Missing information and non-applicable fields

Authors MUST NOT infer commands, results, impact, or tracker relationships that they did
not observe. A field that permits absence uses exactly one of these forms with a concrete
reason:

```text
Unknown: the device test owner has not reported a result yet.
Not applicable: this documentation change has no runtime compatibility surface.
Not run: documentation-only change with no executable behavior.
```

`Unknown:` and `Not applicable:` are allowed only in fields whose tables explicitly
permit them. `Not run:` is allowed only for `Validation`. A sentinel MUST be the complete
field value, not a list item, and MUST include a non-placeholder reason. Acceptance
criteria and tracker references always require positive evidence.

## Validator contract

Local validation:

```bash
python3 scripts/validate_change_evidence.py \
  --kind pull-request \
  --path tests/fixtures/change-evidence/valid-pull-request.md \
  --expected-version v1
```

Kinds are exactly `issue`, `pull-request`, and `commit`. Valid evidence exits `0`.
Invalid evidence exits `1`; an I/O, encoding, path, or unsupported-version error exits
`2`. `--format json` emits a stable object with `valid`, `kind`, `version`, and ordered
`findings`. Finding entries expose `code`, `message`, `line`, and `field`.

`--mode audit` preserves the same findings and outputs but exits `0` for structurally
invalid evidence. It does not turn invalid evidence into v1 evidence. In GitHub Actions,
the action additionally rejects absolute paths, traversal, symlink components, non-regular
files, and files outside `github.workspace`.

Valid and invalid examples live under `tests/fixtures/change-evidence/`. Tests bind the
fixtures, exact form labels, action inputs/outputs, path boundary, and PR template to the
same Python definitions.

## Reusable GitHub Action

Consumers SHOULD pin the action to a verified 40-character automation commit, not a
moving branch or major tag:

```yaml
- uses: actions/checkout@<verified-40-character-commit>

- name: Materialize pull request evidence
  shell: bash
  env:
    EVIDENCE_BODY: ${{ github.event.pull_request.body }}
  run: printf '%s\n' "$EVIDENCE_BODY" > .change-evidence.md

- id: change-evidence
  uses: jhw7500/automation/.github/actions/validate-change-evidence@<verified-40-character-automation-commit>
  with:
    kind: pull-request
    path: .change-evidence.md
    contract-version: v1
    mode: enforce
```

For an Issue event, use `github.event.issue.body` and `kind: issue`. For a commit,
materialize the full commit message (for example, `git log -1 --format=%B`) and use
`kind: commit`. The action outputs `valid`, `kind`, `version`, and single-line
`report-json`. `version` is `v1` only when the parsed value is the supported token; it is
empty for malformed or unsupported values. The JSON report retains the parsed value for
audit without exposing it to GitHub's line-delimited output framing. Treat event bodies as
untrusted data: pass them through an environment variable or file, never interpolate them
into a shell program.

## Version compatibility and updates

`v1` is an exact major contract, not a floating schema range. Under the `v1` identifier,
maintainers MAY clarify prose, add fixtures, or fix a validator bug that makes the code
match this document. Changing field names/order, requiredness, sentinel syntax, accepted
document kinds, or the meaning of an existing finding requires `v2`.

A consumer pins two independent coordinates:

1. `contract-version: v1`, which selects the document contract.
2. The action's verified automation commit SHA, which selects validator implementation
   bytes.

To update, open a consumer pull request that changes only the automation SHA, run the
consumer's current valid/invalid fixtures against the candidate, review any finding delta,
then merge. Never silently change `contract-version`; a major-contract migration needs new
templates and an explicit rollout.

## Progressive rollout and audit

Adopt the contract per repository in four reviewable phases:

1. Pin the action and add the Issue/PR/commit templates without a required check.
2. Run `mode: audit` on newly created or edited records and collect finding codes.
3. Fix authoring integrations, then make `mode: enforce` required for new records only.
4. Audit historical records separately; do not block unrelated changes on rewriting them.

For each repository, record the pinned contract version, automation SHA, rollout mode,
template presence, and counts grouped by `kind` and finding `code`. An audit is reproducible
only when it records the repository/ref and validator SHA used.

## Legacy and fallback policy

A record is one of:

- `v1-valid`: exact v1 structure and all field rules pass.
- `v1-invalid`: it declares v1 or resembles the v1 fields but fails validation.
- `legacy-unstructured`: it has no exact v1 version field and heading set.

Only `v1-valid` may be consumed as structured Change Evidence Contract data. Validators,
agents, and reporting systems MUST NOT repair or invent missing legacy fields. A downstream
report MAY fall back to existing sources such as an approved changelog, linked tracker
record, PR title, or commit subject, but it MUST label the source and must not represent the
fallback as v1 evidence. Historical migration creates a new auditable record; it does not
rewrite old commits in place.

A bare `#194` is accepted as an Issue reference because offline validation cannot resolve
its remote type. Consumers that require stronger identity SHOULD materialize a full Issue
URL before validation.
