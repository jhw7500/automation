# Review Finding Quality Gates Design

Date: 2026-08-23
Status: approved; issues #41, #42, and #43 registered; implementation not started

## 1. Decision

Claude and Gemini automated reviews will add a shared, deterministic finding-quality
canonicalization step between model generation and sticky-comment publication. The step will
separate two classes of failure that the current workflows treat too similarly:

1. **review integrity failures** make the whole review checkpoint fail closed; and
2. **individual finding-quality failures** remove only the unsupported finding while allowing the
   covered review checkpoint to succeed.

The model remains responsible for semantic analysis. Workflow code will not claim to prove that a
finding is true. It will instead require every actionable finding to provide repository evidence
that can be checked mechanically: an actual added-side PR anchor, an exact current-source trigger
quotation, an allowed impact class, and a concrete material-impact statement. Unsupported
candidates will not appear as actionable PR feedback.

This design extends the v1.45 immutable input and canonical state contracts. It does not weaken
head, diff, hash, generation, or prior-state validation.

## 2. Motivation and observed behavior

The live v1.45.2 run on `jhw7500/pim-check#101` demonstrated that input and re-review state
hardening work:

- the first Claude and Gemini rounds used a full prepared PR diff;
- the second rounds used a delta from the previous successful head;
- the sticky state advanced only to the final head; and
- Gemini consumed a human rebuttal and retracted its earlier `plan_global` claim.

The same run also demonstrated the remaining gap:

- Gemini initially reported a HIGH `plan_global` defect even though the preflight and execution
  paths both pass `plan_global=None` and `execute_plan` has no supported non-None input path;
- Gemini reported duplicate loading of small local YAML profiles as MEDIUM without material
  performance evidence; and
- Claude later placed a positively reviewed change under `Resolved` even though its authenticated
  previous review contained no corresponding actionable finding.

The current Claude publication gate accepts any non-empty sanitized model file after the covered
input and current-head checks pass. Gemini additionally drops explicitly named unverified sections,
but it does not validate the evidence or carryover identity of ordinary finding prose. Prompt rules
therefore express the intended policy but cannot enforce it.

## 3. Goals

1. Keep a model-authored false HIGH/MEDIUM from becoming actionable feedback when it cannot cite
   a mechanically verifiable changed anchor, trigger, and material impact.
2. Preserve valid findings from the same model response when a different candidate is unsupported.
3. Bind `Still open`, `Resolved`, and `Retracted` entries to unique findings from the authenticated
   previous successful review.
4. Keep malformed individual findings from failing an otherwise covered review round.
5. Continue to fail closed when the workflow cannot establish trustworthy input, document
   boundaries, current-head identity, or complete output.
6. Make filtering visible and authenticated without repeating the rejected claim in the PR
   discussion.
7. Add no provider/model request from canonicalization. The successful primary path remains one
   request; an eligible terminal provider, timeout, model-specific limit, empty-output, or
   truncated-output failure may use one configured fallback model. Authentication and
   unsupported-location failures do not trigger fallback. Primary retries and fallback share the
   existing three-request ceiling.
8. Use one implementation and one deterministic corpus for Claude and Gemini.

## 4. Non-goals

- Proving arbitrary semantic correctness with workflow code.
- Replacing Claude, Gemini, Codex, Gemini Assist, or OpenCode.
- Applying this contract to the external Codex or Gemini Assist GitHub Apps.
- Cross-model voting, deduplication, severity re-ranking, or automatic merge approval.
- Publishing the text of rejected candidates to the PR.
- Running model-backed quality tests in ordinary CI.
- Enabling OpenCode in repositories where it is not already deployed.
- Treating maintainability or style suggestions as actionable findings.

## 5. Policy boundary

### 5.1 Hard checkpoint failures

The whole attempt remains `failure` or `stale` under the existing v2 state rules when any of the
following occurs:

- deterministic diff preparation is unavailable;
- the captured head, full-diff hash, scope manifest, run generation, or current PR head is invalid;
- the provider step fails, returns empty output, returns invalid UTF-8, or truncates output;
- the raw review exceeds 60,000 UTF-8 bytes, its escaped canonical rendering exceeds 64,000 UTF-8
  bytes, or the final wrapped comment exceeds 65,536 UTF-8 bytes;
- the document has no recognizable clean result and no unambiguous finding-section boundary;
- duplicate top-level actionable sections make block ownership ambiguous; or
- the canonicalizer itself fails.

A hard failure never advances `successful_head`. An earlier valid body/head/hash is preserved and
the sticky shows `Status: stale` plus `Last attempt: failure`; a first-round hard failure has no
`Reviewed` line.

### 5.2 Soft finding filters

When the document boundary is valid, these errors affect only the candidate block:

- missing or malformed changed anchor;
- anchor path outside the sealed PR manifest;
- anchor line that is not an actual added-side line in the selected full/delta input;
- missing, untracked, non-regular, out-of-range, or quote-mismatched trigger evidence;
- missing or unsupported severity or impact class;
- missing concrete material-impact text;
- performance feedback without an allowed materiality basis;
- LOW, style, cleanup, or maintainability-only feedback;
- carryover that does not bind exactly once to an authenticated prior finding; or
- `Resolved` without a current fix anchor.

The canonicalizer omits the rejected block, increments deterministic counters, and continues with
the other blocks. If all candidates are filtered, the checkpoint may still succeed and the body
states `No validated blocking issues found.`

### 5.3 Why filtering is not a false CLEAN

Automated review is advisory and never proves that no defect exists. A model candidate that cannot
establish the required evidence has not become a finding under this contract. The published claim
is deliberately limited to "no validated blocking issues", and the body reports how many model
candidates were filtered. Other reviewers and required tests remain independent gates.

## 6. Model output contract

### 6.1 First-round clean result

The following canonical clean form is accepted:

```markdown
### New findings
None
```

For provider tolerance, an otherwise empty response containing exactly `No blocking issues found.`
or `No validated blocking issues found.` is normalized to the canonical form. A response that
contains a clean declaration and an actionable finding block is a hard ambiguous-document failure.

### 6.2 New finding candidate

Every candidate uses this shape:

```markdown
### New findings

#### [HIGH] Mixed-target rejection can look successful
- Changed anchor: {"path":"pim_check.py","line":410}
- Trigger evidence: {"path":"viewer_state.py","line":120,"quote":"...exact current source line..."}
- Impact class: user-visible
- Material impact: A rejected plan is rendered as a successful 0/N completion.

Explanation limited to the causal chain introduced by the changed anchor.
```

Rules:

- severity is exactly `CRITICAL`, `HIGH`, or `MEDIUM`;
- the changed anchor JSON contains only `path` and positive safe-integer `line`;
- one or more trigger-evidence lines may appear, each containing only `path`, `line`, and `quote`;
- canonical JSON escapes every Python `splitlines()` boundary, including U+0085, U+2028, and
  U+2029, while decoding back to the exact validated path or quotation;
- impact class is exactly one of `runtime`, `security`, `data-integrity`, `user-visible`, or
  `performance`;
- material impact is a non-empty single paragraph and cannot use uncertainty phrases as evidence;
- performance candidates additionally require one exact `Performance basis` JSON line in one of
  these forms:

  ```markdown
  - Performance basis: {"kind":"measured","path":"benchmarks/latest.txt","line":8,"quote":"p95_ms=240"}
  - Performance basis: {"kind":"unbounded-amplification","path":"runner.py","line":91,"quote":"for item in incoming_items:"}
  ```

  `measured` requires at least one decimal digit in the exact tracked-artifact quotation;
  `unbounded-amplification` requires an exact current-source quotation that establishes the repeated
  path. Both forms use the trigger-evidence path, line, regular-file, UTF-8, and exact-quote checks;
  and
- prose outside recognized candidate blocks is non-actionable summary text.

After validation, the workflow assigns a stable identifier derived from reviewer identity, changed
anchor, normalized severity, and normalized title:

```text
RVW-<first 12 lowercase hex characters of SHA-256>
```

The canonical posted heading becomes `#### RVW-... [SEVERITY] title`. Model-supplied identifiers on
new findings are ignored so the model cannot impersonate authenticated prior identity.

## 7. Evidence validation

### 7.1 Changed anchors

The canonicalizer receives the prepared scope manifest, selected review mode, selected diff path,
current head, and previous successful head when applicable. For every candidate it:

1. requires the current path identity to exist in the sealed manifest;
2. rejects removed files and old rename identities;
3. re-derives the exact manifest record from the immutable Git object graph;
4. re-derives zero-context hunks for the full or delta range with literal path arguments; and
5. accepts only a line consumed by an actual added-side `+` record.

A final head may be tree-equivalent to the merge base after all PR changes are reverted. That is a
valid clean-review scope only when the mode is `full`, the sealed selected diff is exactly zero
bytes, `files` is empty, and the immutable full-range Git reconstruction is also empty. Empty delta
scope, non-empty selected bytes, or hidden reconstructed records fail closed. No changed anchor can
validate in the accepted empty scope.

This is the same security boundary as the existing OpenCode added-line validator. The reusable
implementation must share or extract that validator rather than create a weaker path parser.

### 7.2 Trigger evidence

Trigger evidence may cite unchanged current repository code because it explains the effect of an
anchored PR change. Each evidence record must:

- name a tracked path relative to the repository root;
- identify a positive line within the current head;
- refer to a regular UTF-8 file without following a symlink; and
- match the complete current source line after removing only the line terminator.

Whitespace inside the quoted line remains significant. Evidence paths and quotes are transported as
JSON strings so Unicode, spaces, colons, and backticks are not reinterpreted by a shell.

This check would reject the `plan_global` HIGH candidate from PR #101 unless the model could cite an
actual supported non-None caller. It cannot determine whether a real quoted line is semantically
relevant; that residual semantic risk is explicit.

### 7.3 Materiality

The canonicalizer validates the presence and allowed class of material-impact evidence. It does not
re-rank severity. For performance findings, the extra basis rule prevents a repeated local file read
from becoming MEDIUM solely because duplicate calls exist. A model may still make an incorrect causal
argument using real evidence; the fixed corpus and live canary measure this residual behavior.

## 8. Carryover contract

The previous successful canonical body is parsed separately from human discussion. Only its
workflow-assigned `RVW-...` identifiers form the prior active set.

### 8.1 Still open

A `Still open` block must name one active prior identifier exactly once and include a current changed
anchor plus trigger evidence. It remains actionable only when all evidence validates. An active prior
finding cannot also appear as a new finding in the same round.

### 8.2 Resolved

A `Resolved` block must name one active prior identifier and include:

```markdown
- Fix anchor: {"path":"pim_check.py","line":410}
- Resolution: The rejection now emits a plan failure before run_end.
```

The fix anchor must be an actual added-side line in the selected delta. An unknown identifier or
missing/invalid fix anchor is omitted and counted as normalized carryover, not a hard failure.

### 8.3 Retracted

A `Retracted` block must name one active prior identifier and include at least one exact current-source
evidence quote plus a non-empty reason. It does not require a code delta. An unknown identifier is
omitted and counted as normalized carryover.

### 8.4 First round

A first-round response has no authenticated prior active set. Every model-authored `Still open`,
`Resolved`, or `Retracted` block is therefore normalized out. This directly prevents the PR #101
Claude classification error without failing the entire review.

The Gemini re-review prompt will no longer instruct the model to create a `Cannot verify` section.
Unverifiable candidates are omitted by the model or filtered by the canonicalizer; prompt and output
contracts will have one rule.

## 9. Shared canonicalizer action

Create one repository-owned composite action:

```text
.github/actions/canonicalize-review/
├── action.yml
├── canonicalize_review.py
└── review_scope.py
```

Responsibilities are separated as follows:

- `review_scope.py` validates manifest identities, Git records, added-side anchors, and current-source
  quotations without understanding Markdown;
- `canonicalize_review.py` parses the review grammar, assigns stable IDs, applies hard/soft policy,
  and writes canonical Markdown plus a JSON result; and
- `action.yml` validates paths, invokes the Python entry point, exposes scalar outputs, and never
  posts to GitHub.

Inputs:

| Input | Meaning |
| --- | --- |
| `reviewer` | `claude` or `gemini` |
| `candidate-file` | raw model output path |
| `canonical-file` | destination Markdown path |
| `result-file` | destination JSON path |
| `scope-manifest` | trusted `review-scope.json` |
| `selected-diff` | trusted full or delta artifact |
| `diff-mode` | `full` or `delta` |
| `previous-sha` | prior successful head for delta; empty on full |
| `previous-review-file` | authenticated previous canonical body; optional |

Outputs:

| Output | Values |
| --- | --- |
| `document-valid` | `true` or `false` |
| `accepted-count` | non-negative integer |
| `filtered-count` | non-negative integer |
| `normalized-count` | non-negative integer |
| `filtered-max-severity` | `none`, `MEDIUM`, `HIGH`, or `CRITICAL` |
| `failure-reason` | fixed machine reason or empty |

The JSON result contains the same fields plus per-candidate reason codes for logs and tests. It must
not contain rejected candidate prose, repository secrets, or arbitrary model text.

Canonical Markdown is emitted only when its final encoded body is at most 64,000 UTF-8 bytes. This
post-render bound includes JSON and HTML-safe escaping growth and reserves 1,536 bytes for the
workflow-owned v3 sticky envelope. The publisher retains its independent 65,536-byte preflight over
the complete comment.

Hard failure reasons are exactly `candidate_missing`, `invalid_utf8`, `candidate_oversize`,
`ambiguous_document`, `scope_invalid`, and `canonicalizer_error`. Soft filter/normalization reasons
are exactly `invalid_anchor`, `invalid_trigger_evidence`, `invalid_severity`,
`invalid_impact_class`, `missing_material_impact`, `unsupported_performance_basis`,
`non_actionable_category`, `unknown_prior_id`, `duplicate_prior_binding`, and
`missing_fix_anchor`. Adding or renaming a reason is an output-contract change and requires tests and
release-verifier updates.

## 10. Workflow integration

Claude and Gemini retain their existing provider actions, diff preparation, v2 state envelope,
head/generation checks, and sticky upsert ownership.

Each workflow changes as follows:

1. collect the authenticated previous canonical body into a dedicated file separate from human
   discussion;
2. update the model prompt to emit the shared grammar;
3. run the model into its existing raw output file;
4. run `$/.github/actions/canonicalize-review` into a reviewer-specific canonical file;
5. make canonicalizer document failure part of `modelFailed`;
6. read only the canonical file for successful publication; and
7. add one workflow-owned visible metadata line:

```text
- Validation: accepted=1; filtered=2; normalized=0; filtered_max=HIGH
```

The rejected claim text is available neither in the sticky body nor in the JSON result. GitHub step
notices contain only counts and fixed reason codes. Claude and Gemini advance to canonical review
state schema 3 so validation counters cannot be spoofed by model prose. The visible validation line
is generated from that state; it is not parsed back as authority.

The validation line is workflow-owned infrastructure. Claude and Gemini input/output sanitizers must
remove it from previous prose before model context is built, just as they remove `Status`, `Run`, and
`Reviewed`; model-authored validation lines are discarded before the workflow generates the real
line.

### 10.1 Canonical quality state and migration

Claude and Gemini use the exact markers `<!-- automation:claude-code-review:v3 -->` and
`<!-- automation:gemini-auto-review:v3 -->`, followed by this exact state shape:

```json
{"schema":3,"reviewer":"gemini","pr":101,"run_id":32628135199,"run_attempt":1,"attempt_head":"<sha>","successful_head":"<sha>","attempt_status":"success","diff_mode":"delta","full_diff_sha256":"<sha256>","quality_schema":1,"accepted_count":1,"filtered_count":2,"normalized_count":0,"filtered_max_severity":"HIGH"}
```

On success, `quality_schema` is exactly `1`, counts are non-negative safe integers, and
`filtered_max_severity` is `none`, `MEDIUM`, `HIGH`, or `CRITICAL`. On failure without a prior v3
success, the four count/severity fields are `null`; on a stale failure that preserves a prior v3
success, they preserve the prior successful body's values together with its head and full-diff hash.
They never describe a failed candidate body.

A v2 Claude/Gemini comment may be reused only as the display target. Its state, body, previous SHA,
and full hash are not eligible for v3 re-review context. The first v3 run therefore performs a full
review, establishes workflow-assigned finding IDs and quality counters, and replaces the display
envelope in place. This avoids treating a model-authored historical `Validation` line as trusted and
prevents an ID-less v2 body from silently losing active findings in a delta round.

Prior-state candidates are checked newest-first during both context collection and publication, with
the larger comment ID breaking a duplicate-generation tie, and lookup stops after the highest
candidate authenticates. HTTP 404 for an exact run attempt is definitive absence and may be ignored;
every other lookup failure encountered before selection (including a timeout, 403, 429, or 5xx)
aborts before model generation and publication. Failure to fetch the issue-comment snapshot is also
uncertain rather than equivalent to an empty snapshot. The publication step is gated on a successful
collector outcome, so no comment is created, updated, or deleted after collection failure. This
prevents a transient lookup failure for the newest state from dropping or resurrecting findings,
updating an older sticky, or creating a duplicate while still allowing a definitive 404 to fall back
to older authenticated state.

Gemini publisher identity survives supported repository-write mode changes without weakening the
bot boundary. The current resolver login remains the primary exact identity. A switch from the
built-in token to App mode may additionally reuse only the official GitHub Actions App identity
(`id=15368`); a switch from App mode to the built-in token may reuse only the explicit non-secret
`publisher_app_id`. In both cases `performed_via_github_app.id`, App slug, and bot login must agree,
and the existing schema/run provenance authentication still applies. Canonical callers retain the
App ID expression in a separate `publisher_app_id` input even when token minting `app_id` and the
private key are removed. This lets the workflow update the old sticky in place while rejecting an
arbitrary Bot or unrelated installed App.

OpenCode remains on its existing v2 state because it already has a separate strict canonicalizer and
is outside issues #41 and #42. Shared collectors and documentation must explicitly support Claude and
Gemini v3 alongside OpenCode v2; neither schema is accepted for the wrong reviewer.

If there are no accepted actionable findings, the canonical body contains
`No validated blocking issues found.` plus the validation line. A filtered claimed severity is not an
actionable severity and must never be rendered in bracket form such as `[HIGH]`.

## 11. Monitoring and ship interpretation

The review run remains successful when document validation succeeds, even if one or more candidates
were soft-filtered. Existing merge logic continues to consider only canonical actionable severity
labels. Monitoring should additionally report the validation counters as a warning:

```text
Gemini: CLEAN (0 validated blocking findings; 2 candidates filtered, max claimed HIGH)
```

Filtering does not block the default merge gate. A future repository-specific strict mode is outside
this implementation and can be added only with an explicit policy request.

Because `/jhw:ship` is distributed outside this repository, this project guarantees the exact v3
state and visible validation metadata line and documents their interpretation. Updating the installed
ship parser to recognize reviewer v3 state is a coordinated rollout prerequisite before a consumer
repository is pinned to the new release; it is not implemented by this repository's workflow bundle.

## 12. Deterministic regression corpus

Provider-independent fixtures will exercise the shared canonicalizer directly:

| Fixture | Expected result |
| --- | --- |
| `plan_global` HIGH without a real non-None caller | filtered |
| duplicate small local YAML reads with no performance basis | filtered |
| broad `ValueError` catch with exact runtime misclassification evidence | accepted |
| plan rejection rendered as successful 0/N completion | accepted |
| first-round `Resolved` with no prior ID | normalized |
| valid prior finding plus delta fix anchor | resolved and preserved |
| invalid block beside a valid finding | invalid block filtered; valid finding accepted |
| ambiguous document boundary | hard failure |
| invalid attempt after earlier success | old body/head/hash preserved as stale |

Tests must assert exact canonical Markdown, counters, stable IDs, reason codes, and state publication
behavior. Workflow wiring tests must prove both reviewers use the same action and that publication
cannot read the raw model file after canonicalization.

`verify_workflow_release.py`, the release inventory, and fleet rollout tests must include every new
action file and reject a release where only one reviewer is wired to the contract.

## 13. Release and live validation

The change alters reviewer output behavior and the reusable workflow inventory, so it requires a new
minor shared-workflow release rather than a patch-only fleet edit.

Release sequence:

1. implement issues #41 and #42 with red-green tests;
2. add the issue #43 corpus and release-verifier coverage;
3. run the full local pytest suite, release verification, actionlint, and repository workflow checks;
4. obtain independent code review on the implementation PR;
5. tag the new automation release only after all required checks are green;
6. prepare a normal fleet rollout PR for `pim-check` pinned to the immutable release commit; and
7. validate one first-round and one delta re-review using the PR #101-derived cases.

Live acceptance requires:

- no unsupported candidate appears as actionable feedback;
- validation counters match the run log;
- valid findings survive filtering;
- a first-round false `Resolved` does not appear;
- final `Reviewed` equals the live head; and
- no additional provider request is made by canonicalization.

## 14. Alternatives considered

### Prompt-only tightening

Rejected as the sole control. v1.45.2 already contains explicit scope, materiality, and uncertainty
instructions, yet PR #101 produced the target false positives.

### Fail the whole round for any invalid candidate

Rejected as too strict for an advisory stochastic reviewer. One malformed or unsupported block would
discard valid findings and create avoidable reruns.

### Silently delete invalid candidates

Rejected because it makes review behavior unauditable. The selected design publishes deterministic
counts and highest claimed severity without repeating rejected prose.

### Add a second model/critic call

Rejected for this iteration because it consumes daily quota, adds provider latency and failure modes,
and still cannot create a deterministic correctness boundary. This does not prohibit the bounded
availability fallback above: the fallback replaces a failed primary result and is never used as a
second opinion or after canonical-format rejection.

## 15. Residual risks

- A model can cite real but semantically irrelevant source lines. Exact quote validation reduces
  hallucinated evidence but cannot prove causal relevance.
- A real finding can be filtered when the model fails the grammar. The validation count makes this
  visible, and independent reviewers reduce the single-model miss risk.
- Performance materiality remains partly semantic even with measured/unbounded basis fields.
- External GitHub App reviewers do not inherit this contract.
- One live PR cannot quantify the long-term false-positive rate; the fixed corpus proves contract
  behavior, while post-rollout PR sampling measures model behavior.
