'use strict';

// This module is loaded only from the current run's server-resolved central SHA.
// Receipt bytes are claims until the independent server run/job/Check facts agree.
const crypto = require('crypto');
const WORKFLOW = '.github/workflows/opencode-auto-review.yml';
const CENTRAL = `jhw7500/automation/${WORKFLOW}@`;
const RECOVERY = 'jhw7500/automation/.github/workflows/opencode-recover-finalization.yml@';
const CHECK = 'automation/opencode-finalization-recovery';
const LEDGER = '<!-- automation:review-invocation-budget:opencode:v1 -->';
const positive = (v) => Number.isSafeInteger(v) && v > 0;
const terminalConclusion = (value) => ['success', 'failure', 'cancelled', 'skipped',
  'timed_out', 'neutral', 'action_required', 'stale', 'startup_failure'].includes(value);
const head = (v) => typeof v === 'string' && /^[0-9a-f]{40}$/.test(v);
const hash = (v) => typeof v === 'string' && /^[0-9a-f]{64}$/.test(v);
const sha = (v) => crypto.createHash('sha256').update(v).digest('hex');
const exact = (v, keys) => v !== null && typeof v === 'object' && !Array.isArray(v)
  && JSON.stringify(Object.keys(v).sort()) === JSON.stringify([...keys].sort());
const ORIGINAL_KEYS = ['run_id', 'run_attempt', 'head_sha', 'base_sha', 'workflow_head',
  'full_diff_sha256', 'caller_workflow_path', 'referenced_workflow_path', 'referenced_workflow_sha',
  'comment_id', 'attestation_id', 'body_sha256', 'state_sha256', 'claim_checkpoint_sha256',
  'finalized_invocation_sha256', 'artifacts'];
const RECOVERY_KEYS = ['run_id', 'run_attempt', 'workflow_head', 'caller_workflow_path',
  'referenced_workflow_path', 'referenced_workflow_sha', 'provider_calls'];
const STATE_KEYS = ['schema', 'reviewer', 'pr', 'run_id', 'run_attempt', 'attempt_head',
  'successful_head', 'attempt_status', 'diff_mode', 'full_diff_sha256'];
const ATTESTATION_KEYS = ['schema', 'repository', 'workflow', 'caller_workflow_path', 'caller_event',
  'referenced_workflow_path', 'referenced_workflow_sha', 'pr', 'attempt_head', 'workflow_head',
  'successful_head', 'run_id', 'run_attempt', 'prepared_run_attempt', 'comment_id', 'body_sha256', 'state_sha256'];
const INVOCATION_KEYS_V1 = ['run_id', 'run_attempt', 'head_sha', 'full_diff_sha256', 'caller_workflow_path',
  'caller_event', 'referenced_workflow_path', 'referenced_workflow_ref', 'referenced_workflow_sha',
  'round_number', 'override_event_id', 'model_route', 'effort', 'call_unit', 'call_count',
  'estimated_input_tokens', 'elapsed_seconds', 'status', 'outcome', 'stop_reason', 'remaining_finding_ids'];
const INVOCATION_KEYS_V2 = [...INVOCATION_KEYS_V1, 'route'];

// Python's ensure_ascii=True and recursive code-point key ordering, including
// numeric-looking keys (which JSON.stringify on a reconstructed object reorders).
function canonicalJson(value) {
  if (Array.isArray(value)) return '[' + value.map(canonicalJson).join(',') + ']';
  if (value && typeof value === 'object') {
    const compare = (a, b) => {
      const aa = Array.from(a, (c) => c.codePointAt(0)), bb = Array.from(b, (c) => c.codePointAt(0));
      for (let i = 0; i < Math.min(aa.length, bb.length); i++) if (aa[i] !== bb[i]) return aa[i] - bb[i];
      return aa.length - bb.length;
    };
    return '{' + Object.keys(value).sort(compare).map((k) => canonicalJson(k) + ':' + canonicalJson(value[k])).join(',') + '}';
  }
  const encoded = JSON.stringify(value);
  if (encoded === undefined || (typeof value === 'number' && !Number.isSafeInteger(value))) {
    throw new Error('unsupported canonical JSON value');
  }
  return encoded.replace(/[\u007f-\uffff]/g, (c) => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0'));
}

// JSON.parse alone silently accepts duplicate object keys. Tokenize validated JSON
// first so duplicate escaped keys are rejected at every nesting level.
function strictJson(text) {
  if (typeof text !== 'string' || text.length > 1000000) throw new Error('JSON bound exceeded');
  const parsed = JSON.parse(text);
  const tokens = text.match(/"(?:[^"\\]|\\.)*"|[{}\[\]:,]|[^\s{}\[\]:,]+/g) || [];
  let at = 0;
  function walk() {
    const token = tokens[at++];
    if (token === '{') {
      const keys = new Set();
      while (tokens[at] !== '}') {
        const key = JSON.parse(tokens[at++]);
        if (keys.has(key)) throw new Error('duplicate JSON key');
        keys.add(key);
        at++; // colon; syntax has already been validated
        walk();
        if (tokens[at] !== ',') break;
        at++;
      }
      at++;
    } else if (token === '[') {
      while (tokens[at] !== ']') {
        walk();
        if (tokens[at] !== ',') break;
        at++;
      }
      at++;
    }
  }
  walk();
  return parsed;
}
function envelope(text, marker) {
  if (typeof text !== 'string' || text.includes('\n') || !text.startsWith(`<!-- ${marker}:`)
    || !text.endsWith(' -->')) throw new Error('invalid envelope');
  const parsed = strictJson(text.slice(marker.length + 6, -4));
  if (marker === 'automation-opencode-recovery'
    && text !== `<!-- ${marker}:${JSON.stringify(parsed)} -->`) throw new Error('noncompact recovery receipt');
  return parsed;
}
function receiptShape(r) {
  if (!exact(r, ['schema', 'repository', 'pr', 'original', 'recovery']) || r.schema !== 1
    || typeof r.repository !== 'string' || !/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(r.repository)
    || !positive(r.pr) || !exact(r.original, ORIGINAL_KEYS) || !exact(r.recovery, RECOVERY_KEYS)) return false;
  const o = r.original, d = r.recovery;
  return [o.run_id, o.run_attempt, o.comment_id, o.attestation_id, d.run_id, d.run_attempt].every(positive)
    && o.run_id !== d.run_id
    && [o.head_sha, o.base_sha, o.workflow_head, o.referenced_workflow_sha, d.workflow_head, d.referenced_workflow_sha].every(head)
    && [o.full_diff_sha256, o.body_sha256, o.state_sha256, o.claim_checkpoint_sha256, o.finalized_invocation_sha256].every(hash)
    && [o.caller_workflow_path, d.caller_workflow_path].every((p) => typeof p === 'string'
      && /^\.github\/workflows\/[^/]+\.ya?ml$/.test(p))
    && typeof o.referenced_workflow_path === 'string' && o.referenced_workflow_path.startsWith(CENTRAL)
    && o.referenced_workflow_path.length > CENTRAL.length
    && d.referenced_workflow_path === RECOVERY + d.referenced_workflow_sha && d.provider_calls === 0
    && exact(o.artifacts, ['handoff', 'claim', 'candidate'])
    && Object.values(o.artifacts).every((a) => exact(a, ['id', 'sha256']) && positive(a.id) && hash(a.sha256))
    && new Set(Object.values(o.artifacts).map((a) => a.id)).size === 3;
}
function invocationShape(entry, schema) {
  if (schema === 1) return exact(entry, INVOCATION_KEYS_V1);
  return schema === 2 && exact(entry, INVOCATION_KEYS_V2)
    && exact(entry.route, ['kind']) && entry.route.kind === 'automatic';
}
function invocationDigestMatches(entry, schema, expected) {
  if (sha(canonicalJson(entry)) === expected) return true;
  if (schema !== 2 || entry.route.kind !== 'automatic') return false;
  const historical = Object.fromEntries(INVOCATION_KEYS_V1.map((key) => [key, entry[key]]));
  return sha(canonicalJson(historical)) === expected;
}
function runMatches(run, binding, repository, event) {
  return run?.id === binding.run_id && run.run_attempt === binding.run_attempt
    && run.repository?.full_name === repository && run.head_sha === binding.workflow_head
    && run.event === event && run.path === binding.caller_workflow_path
    && Array.isArray(run.referenced_workflows)
    && run.referenced_workflows.filter((r) => r.path.startsWith(event === 'pull_request' ? CENTRAL : RECOVERY)).length === 1
    && run.referenced_workflows.some((r) => r.path === binding.referenced_workflow_path && r.sha === binding.referenced_workflow_sha);
}
function successfulCheck(check, name, workflowHead) {
  return positive(check?.id) && check.name === name && check.head_sha === workflowHead
    && check.status === 'completed' && check.conclusion === 'success'
    && check.app?.id === 15368 && check.app.slug === 'github-actions';
}
function jobMatches(jobs, name, binding, conclusion, requiredSteps) {
  if (!Array.isArray(jobs) || jobs.length > 100) return false;
  const matched = jobs.filter((j) => new RegExp(`(?:^|\\s/\\s)${name}$`).test(j?.name || ''));
  if (matched.length !== 1) return false;
  const j = matched[0];
  if (!positive(j.id) || j.run_id !== binding.run_id || ('run_attempt' in j && j.run_attempt !== binding.run_attempt)
    || j.status !== 'completed' || j.conclusion !== conclusion) return false;
  if (!requiredSteps) return true;
  if (!Array.isArray(j.steps) || j.steps.length > 100) return false;
  if (j.steps.some((step, index) => !positive(step.number)
    || (index > 0 && step.number <= j.steps[index - 1].number))) return false;
  let previousNumber = 0;
  return Object.entries(requiredSteps).every(([name, conclusion]) => {
    const found = j.steps.filter((s) => s.name === name);
    if (found.length !== 1 || found[0].status !== 'completed' || found[0].conclusion !== conclusion
      || found[0].number <= previousNumber) return false;
    previousNumber = found[0].number;
    return true;
  }) && j.steps.filter((s) => !['success', 'skipped'].includes(s.conclusion))
    .every((s) => conclusion === 'failure' && s.name === 'Finalize OpenCode review budget' && s.conclusion === 'failure');
}
function validateReceipt(facts) {
  try {
    const {receipt: r, check, driverRun, driverJobs, originalRun, originalJobs, canonicalCheck, comment, ledgerComment} = facts;
    if (!receiptShape(r) || canonicalJson(envelope(check?.output?.text, 'automation-opencode-recovery')) !== canonicalJson(r)) return false;
    const o = r.original, d = r.recovery;
    if (!successfulCheck(check, CHECK, d.workflow_head)
      || check.external_id !== `automation-opencode-recovery:${r.repository}:pr:${r.pr}:original:${o.run_id}:${o.run_attempt}:recovery:${d.run_id}:${d.run_attempt}`
      || !runMatches(driverRun, d, r.repository, 'workflow_dispatch')
      || driverRun.status !== 'completed' || driverRun.conclusion !== 'success'
      || !jobMatches(driverJobs, 'opencode-recover-finalization', d, 'success')
      || !runMatches(originalRun, o, r.repository, 'pull_request')
      || originalRun.status !== 'completed' || originalRun.conclusion !== 'failure'
      || !Array.isArray(originalRun.pull_requests) || originalRun.pull_requests.length !== 1) return false;
    const pr = originalRun.pull_requests[0];
    if (pr.number !== r.pr || pr.head?.sha !== o.head_sha || pr.base?.sha !== o.base_sha) return false;
    if (!jobMatches(originalJobs, 'opencode-prepare', o, 'success', {
      'Claim OpenCode review budget': 'success', 'Build sealed canonicalization handoff': 'success',
      'Upload sealed canonicalization handoff': 'success'})
      || !jobMatches(originalJobs, 'opencode-review', o, 'success', {
        'Run OpenCode PR review': 'success', 'Materialize sealed OpenCode candidate': 'success',
        'Upload untrusted OpenCode candidate': 'success'})
      || !jobMatches(originalJobs, 'opencode-canonicalize', o, 'failure', {
        'Canonicalize OpenCode review': 'success', 'Resolve OpenCode budget outcome': 'success',
        'Finalize OpenCode review budget': 'failure'})) return false;
    if (comment?.id !== o.comment_id || comment.user?.type !== 'Bot' || comment.user.login !== 'github-actions[bot]' || typeof comment.body !== 'string'
      || sha(comment.body) !== o.body_sha256) return false;
    const lines = comment.body.split('\n');
    if (lines[0] !== '## OpenCode Review (latest)' || lines[1] !== '<!-- automation:opencode-auto-review:v2 -->'
      || lines.filter((l) => /^- Attestation: /.test(l)).length !== 1
      || !lines.includes(`- Attestation: ${o.attestation_id}`)
      || !lines.includes(`- Run: https://github.com/${r.repository}/actions/runs/${o.run_id}`)) return false;
    const state = envelope(lines[2], 'automation-state');
    const stateText = lines[2].slice('<!-- automation-state:'.length, -4);
    if (!exact(state, STATE_KEYS) || state.schema !== 2 || state.reviewer !== 'opencode' || state.pr !== r.pr
      || state.run_id !== o.run_id || state.run_attempt !== o.run_attempt || state.attempt_head !== o.head_sha
      || state.successful_head !== o.head_sha || state.attempt_status !== 'success'
      || !['full', 'delta'].includes(state.diff_mode) || state.full_diff_sha256 !== o.full_diff_sha256
      || sha(stateText) !== o.state_sha256) return false;
    const a = envelope(canonicalCheck?.output?.text, 'automation-attestation');
    if (!successfulCheck(canonicalCheck, 'automation/opencode-canonical-review', o.workflow_head)
      || canonicalCheck.id !== o.attestation_id
      || canonicalCheck.external_id !== `automation-opencode-canonical:${r.repository}:pr:${r.pr}:run:${o.run_id}:${o.run_attempt}:comment:${o.comment_id}`
      || !exact(a, ATTESTATION_KEYS) || a.schema !== 1 || a.repository !== r.repository || a.pr !== r.pr
      || a.workflow !== WORKFLOW || a.caller_event !== 'pull_request'
      || a.attempt_head !== o.head_sha || a.successful_head !== o.head_sha
      || !positive(a.prepared_run_attempt) || a.prepared_run_attempt !== o.run_attempt
      || ['caller_workflow_path', 'referenced_workflow_path', 'referenced_workflow_sha', 'workflow_head',
        'run_id', 'run_attempt', 'comment_id', 'body_sha256', 'state_sha256'].some((key) => a[key] !== o[key])) return false;
    if (!positive(ledgerComment?.id) || ledgerComment.user?.type !== 'Bot' || ledgerComment.user.login !== 'github-actions[bot]') return false;
    const ledgerLines = (ledgerComment.body || '').split('\n');
    if (ledgerLines[0] !== LEDGER) return false;
    const ledger = envelope(ledgerLines[1], 'automation-budget-state');
    if (![1, 2].includes(ledger.schema) || ledger.repository !== r.repository || ledger.pr !== r.pr || ledger.reviewer !== 'opencode'
      || !Array.isArray(ledger.invocations) || ledger.invocations.length > 100
      || ledgerLines[1] !== `<!-- automation-budget-state:${canonicalJson(ledger)} -->`) return false;
    const entries = ledger.invocations.filter((e) => e.run_id === o.run_id && e.run_attempt === o.run_attempt);
    if (entries.length !== 1) return false;
    const entry = entries[0];
    return invocationShape(entry, ledger.schema) && entry.status === 'finalized' && ['success', 'quality_filtered'].includes(entry.outcome)
      && entry.caller_event === 'pull_request'
      && ['head_sha', 'full_diff_sha256', 'caller_workflow_path', 'referenced_workflow_path', 'referenced_workflow_sha']
        .every((key) => entry[key] === o[key])
      && originalRun.referenced_workflows.some((ref) => ref.path === o.referenced_workflow_path
        && ref.sha === o.referenced_workflow_sha
        && entry.referenced_workflow_ref === ('ref' in ref ? ref.ref : ref.sha))
      && invocationDigestMatches(entry, ledger.schema, o.finalized_invocation_sha256);
  } catch { return false; }
}

function boundedPage(data, key) {
  if (!Array.isArray(data?.[key]) || !Number.isSafeInteger(data.total_count)
    || data.total_count < 0 || data.total_count > 100 || data[key].length !== data.total_count) {
    throw new Error(`unbounded or incomplete recovery ${key} page`);
  }
  return data[key];
}
async function authenticateRecovery({github, repository, pr, comments}) {
  if (typeof repository !== 'string' || !/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository)
    || !positive(pr) || !Array.isArray(comments)) throw new Error('invalid recovery lookup scope');
  const [owner, repo] = repository.split('/');
  const scope = {owner, repo};
  const {data: recent} = await github.rest.actions.listWorkflowRunsForRepo({
    ...scope, event: 'workflow_dispatch', per_page: 100, page: 1,
  });
  if (!Array.isArray(recent?.workflow_runs) || recent.workflow_runs.length > 100) {
    throw new Error('invalid recent recovery run page');
  }
  const runs = recent.workflow_runs.filter((run) => positive(run?.id) && positive(run.run_attempt)
    && head(run.head_sha) && run.event === 'workflow_dispatch'
    && Array.isArray(run.referenced_workflows)
    && run.referenced_workflows.filter((r) => typeof r.path === 'string' && head(r.sha)
      && r.path === RECOVERY + r.sha).length === 1).slice(0, 20);
  const candidates = [], seen = new Set();
  for (const run of runs) {
    const {data} = await github.rest.checks.listForRef({...scope, ref: run.head_sha,
      check_name: CHECK, status: 'completed', filter: 'all', per_page: 100, page: 1});
    for (const check of boundedPage(data, 'check_runs')) {
      if (seen.has(check.id) || !successfulCheck(check, CHECK, run.head_sha)) continue;
      let r;
      try { r = envelope(check.output?.text, 'automation-opencode-recovery'); } catch { continue; }
      if (!receiptShape(r) || r.repository !== repository || r.pr !== pr) continue;
      const d = r.recovery, o = r.original;
      if (d.run_id !== run.id || d.run_attempt > run.run_attempt || d.workflow_head !== run.head_sha
        || d.caller_workflow_path !== run.path || run.repository?.full_name !== repository
        || !run.referenced_workflows.some((ref) => ref.path === d.referenced_workflow_path && ref.sha === d.referenced_workflow_sha)
        || check.external_id !== `automation-opencode-recovery:${repository}:pr:${pr}:original:${o.run_id}:${o.run_attempt}:recovery:${d.run_id}:${d.run_attempt}`) continue;
      const comment = comments.find((c) => c.id === o.comment_id && c.user?.type === 'Bot'
        && typeof c.body === 'string' && sha(c.body) === o.body_sha256);
      if (!comment) continue;
      seen.add(check.id);
      candidates.push({receipt: r, check, comment});
      if (candidates.length > 40) throw new Error('recovery receipt candidate limit exceeded');
    }
  }
  const records = [];
  for (const facts of candidates) {
    const r = facts.receipt, d = r.recovery, o = r.original;
    const {data: driverRun} = await github.rest.actions.getWorkflowRunAttempt({
      ...scope, run_id: d.run_id, attempt_number: d.run_attempt});
    if (!runMatches(driverRun, d, repository, 'workflow_dispatch') || driverRun.status !== 'completed'
      || !terminalConclusion(driverRun.conclusion)) {
      throw new Error('unresolved exact recovery driver attempt');
    }
    if (driverRun.conclusion !== 'success') continue;
    const {data: driverPage} = await github.rest.actions.listJobsForWorkflowRunAttempt({
      ...scope, run_id: d.run_id, attempt_number: d.run_attempt, per_page: 100, page: 1});
    const driverJobs = boundedPage(driverPage, 'jobs');
    const matchedJobs = driverJobs.filter((j) => /(?:^|\s\/\s)opencode-recover-finalization$/.test(j?.name || ''));
    if (matchedJobs.length !== 1 || matchedJobs[0].status !== 'completed'
      || !terminalConclusion(matchedJobs[0].conclusion)
      || matchedJobs[0].run_id !== d.run_id || !positive(matchedJobs[0].id)
      || ('run_attempt' in matchedJobs[0] && matchedJobs[0].run_attempt !== d.run_attempt)) {
      throw new Error('unresolved exact recovery driver job');
    }
    if (matchedJobs[0].conclusion !== 'success') continue;
    const {data: originalRun} = await github.rest.actions.getWorkflowRunAttempt({
      ...scope, run_id: o.run_id, attempt_number: o.run_attempt});
    if (!runMatches(originalRun, o, repository, 'pull_request') || originalRun.status !== 'completed'
      || !terminalConclusion(originalRun.conclusion)) {
      throw new Error('unresolved exact recovery original attempt');
    }
    const {data: originalPage} = await github.rest.actions.listJobsForWorkflowRunAttempt({
      ...scope, run_id: o.run_id, attempt_number: o.run_attempt, per_page: 100, page: 1});
    const originalJobs = boundedPage(originalPage, 'jobs');
    const requiredOriginalSteps = {
      'opencode-prepare': ['Claim OpenCode review budget', 'Build sealed canonicalization handoff',
        'Upload sealed canonicalization handoff'],
      'opencode-review': ['Run OpenCode PR review', 'Materialize sealed OpenCode candidate',
        'Upload untrusted OpenCode candidate'],
      'opencode-canonicalize': ['Canonicalize OpenCode review', 'Resolve OpenCode budget outcome',
        'Finalize OpenCode review budget'],
    };
    for (const [suffix, steps] of Object.entries(requiredOriginalSteps)) {
      const matched = originalJobs.filter((j) => new RegExp(`(?:^|\\s/\\s)${suffix}$`).test(j?.name || ''));
      if (matched.length !== 1 || !positive(matched[0].id) || matched[0].run_id !== o.run_id
        || matched[0].status !== 'completed' || !terminalConclusion(matched[0].conclusion)
        || !Array.isArray(matched[0].steps)
        || ('run_attempt' in matched[0] && matched[0].run_attempt !== o.run_attempt)) {
        throw new Error('unresolved exact recovery original job');
      }
      if (matched[0].steps.some((step, index) => !positive(step.number)
        || (index > 0 && step.number <= matched[0].steps[index - 1].number))) {
        throw new Error('unresolved exact recovery original job order');
      }
      let previousNumber = 0;
      for (const name of steps) {
        const found = matched[0].steps.filter((s) => s.name === name);
        if (found.length !== 1 || found[0].status !== 'completed' || !terminalConclusion(found[0].conclusion)) {
          throw new Error('unresolved exact recovery original job step');
        }
        if (found[0].number <= previousNumber) throw new Error('unresolved exact recovery original job order');
        previousNumber = found[0].number;
      }
    }
    const {data: checksPage} = await github.rest.checks.listForRef({...scope, ref: originalRun.head_sha,
      check_name: 'automation/opencode-canonical-review', status: 'completed', filter: 'all', per_page: 100, page: 1});
    const canonicalChecks = boundedPage(checksPage, 'check_runs').filter((c) => c.id === o.attestation_id);
    const ledgers = comments.filter((c) => c.user?.type === 'Bot' && c.body?.startsWith(LEDGER + '\n'));
    if (ledgers.length !== 1) throw new Error('unresolved or ambiguous recovery ledger');
    if (canonicalChecks.length !== 1) throw new Error('unresolved original canonical Check');
    Object.assign(facts, {driverRun, driverJobs, originalRun, originalJobs,
      canonicalCheck: canonicalChecks[0], ledgerComment: ledgers[0]});
    if (validateReceipt(facts)) records.push({comment: facts.comment,
      state: envelope(facts.comment.body.split('\n')[2], 'automation-state'), attestationId: o.attestation_id});
  }
  return records.sort((a, b) => a.state.run_id - b.state.run_id || a.state.run_attempt - b.state.run_attempt);
}
module.exports = {validateReceipt, authenticateRecovery, canonicalJson};
if (require.main === module) {
  let valid = false;
  try { valid = validateReceipt(strictJson(require('fs').readFileSync(0, 'utf8'))); } catch { /* invalid facts */ }
  process.stdout.write(JSON.stringify({valid}));
}
