"""Executable authentication tests for the recovery receipt trust boundary."""
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / ".github/actions/recover-opencode-review/receipt.js"


def compact(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def facts_fixture(pr=7, full_hash="12" * 32, pinned_original=True):
    head, base, central, driver_head = "ab" * 20, "bc" * 20, "75" * 20, "ef" * 20
    workflow = ".github/workflows/opencode-auto-review.yml"
    original_ref = "jhw7500/automation/" + workflow + "@" + (central if pinned_original else "refs/tags/v1.75")
    recovery_ref = "jhw7500/automation/.github/workflows/opencode-recover-finalization.yml@" + central
    state = dict(schema=2, reviewer="opencode", pr=pr, run_id=700, run_attempt=1,
                 attempt_head=head, successful_head=head, attempt_status="success",
                 diff_mode="full", full_diff_sha256=full_hash)
    state_text = compact(state)
    comment = dict(id=902, user=dict(type="Bot", login="github-actions[bot]"), body=(
        "## OpenCode Review (latest)\n<!-- automation:opencode-auto-review:v2 -->\n"
        f"<!-- automation-state:{state_text} -->\n\n- Attestation: 903\n"
        "- Run: https://github.com/example/repo/actions/runs/700\n\n### New findings\nNone"))
    attestation = dict(schema=1, repository="example/repo", workflow=workflow,
        caller_workflow_path=workflow, caller_event="pull_request",
        referenced_workflow_path=original_ref, referenced_workflow_sha=central,
        pr=pr, attempt_head=head, successful_head=head, workflow_head=head,
        run_id=700, run_attempt=1, prepared_run_attempt=1, comment_id=902,
        body_sha256=digest(comment["body"]), state_sha256=digest(state_text))
    canonical = dict(id=903, name="automation/opencode-canonical-review", head_sha=head,
        status="completed", conclusion="success", app=dict(id=15368, slug="github-actions"),
        external_id=f"automation-opencode-canonical:example/repo:pr:{pr}:run:700:1:comment:902",
        output=dict(text="<!-- automation-attestation:" + compact(attestation) + " -->"))
    entry = dict(run_id=700, run_attempt=1, head_sha=head, full_diff_sha256=full_hash,
        caller_workflow_path=workflow, caller_event="pull_request", referenced_workflow_path=original_ref,
        referenced_workflow_ref="refs/tags/v1.75", referenced_workflow_sha=central,
        round_number=1, override_event_id=None, model_route=["zai-coding-plan/glm-4.7"], effort="default",
        call_unit="provider", call_count=1, estimated_input_tokens=120, elapsed_seconds=10,
        status="finalized", outcome="success", stop_reason="", remaining_finding_ids=[])
    ledger = dict(schema=1, repository="example/repo", pr=pr, reviewer="opencode", budgets={},
        invocations=[entry], consumed_override_event_ids=[], last_decision={}, handoff={})
    ledger_comment = dict(id=904, user=dict(type="Bot", login="github-actions[bot]"), body=(
        "<!-- automation:review-invocation-budget:opencode:v1 -->\n"
        "<!-- automation-budget-state:" + compact(ledger) + " -->\n"))
    original = dict(run_id=700, run_attempt=1, head_sha=head, base_sha=base, workflow_head=head,
        full_diff_sha256=full_hash, caller_workflow_path=workflow, referenced_workflow_path=original_ref,
        referenced_workflow_sha=central, comment_id=902, attestation_id=903,
        body_sha256=digest(comment["body"]), state_sha256=digest(state_text),
        claim_checkpoint_sha256="34" * 32, finalized_invocation_sha256=digest(compact(entry)),
        artifacts={role: dict(id=101 + i, sha256=str(i + 1) * 64)
                   for i, role in enumerate(("handoff", "claim", "candidate"))})
    recovery = dict(run_id=800, run_attempt=1, workflow_head=driver_head, caller_workflow_path=workflow,
        referenced_workflow_path=recovery_ref, referenced_workflow_sha=central, provider_calls=0)
    receipt = dict(schema=1, repository="example/repo", pr=pr, original=original, recovery=recovery)
    check = dict(id=905, name="automation/opencode-finalization-recovery", head_sha=driver_head,
        status="completed", conclusion="success", app=dict(id=15368, slug="github-actions"),
        external_id=f"automation-opencode-recovery:example/repo:pr:{pr}:original:700:1:recovery:800:1",
        output=dict(text="<!-- automation-opencode-recovery:" + compact(receipt) + " -->"))
    def run(run_id, workflow_head, event, conclusion, ref):
        return dict(id=run_id, run_attempt=1, head_sha=workflow_head, event=event, status="completed",
            conclusion=conclusion, path=workflow, repository=dict(full_name="example/repo"),
            referenced_workflows=[dict(path=ref, sha=central)])
    original_run = run(700, head, "pull_request", "failure", original_ref)
    original_run["referenced_workflows"][0]["ref"] = "refs/tags/v1.75"
    original_run["pull_requests"] = [dict(number=pr, head=dict(sha=head), base=dict(sha=base))]
    def job(name, conclusion, steps):
        return dict(id=1001, run_id=700, run_attempt=1, name=name, status="completed", conclusion=conclusion,
                    steps=[dict(number=i + 1, name=n, status="completed", conclusion=c)
                           for i, (n,c) in enumerate(steps)])
    original_jobs = [
        job("opencode-prepare", "success", [(n, "success") for n in (
            "Claim OpenCode review budget", "Build sealed canonicalization handoff", "Upload sealed canonicalization handoff")]),
        job("opencode-review", "success", [(n, "success") for n in (
            "Run OpenCode PR review", "Materialize sealed OpenCode candidate", "Upload untrusted OpenCode candidate")]),
        job("opencode-canonicalize", "failure", [("Canonicalize OpenCode review", "success"),
            ("Resolve OpenCode budget outcome", "success"), ("Finalize OpenCode review budget", "failure")])]
    driver_jobs = [dict(id=1002, run_id=800, run_attempt=1, name="opencode-recover-finalization",
                        status="completed", conclusion="success")]
    return dict(receipt=receipt, check=check, driverRun=run(800, driver_head, "workflow_dispatch", "success", recovery_ref),
        driverJobs=driver_jobs, originalRun=original_run, originalJobs=original_jobs,
        canonicalCheck=canonical, comment=comment, ledgerComment=ledger_comment)


def node_validate(facts):
    result = subprocess.run(["node", str(HELPER)], input=json.dumps(facts), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["valid"]


def test_receipt_accepts_complete_exact_provenance():
    assert node_validate(facts_fixture())


@pytest.mark.parametrize("field,value", [
    ("driverRun.conclusion", "failure"), ("driverRun.conclusion", "cancelled"),
    ("driverRun.status", "in_progress"), ("driverRun.event", "pull_request"),
    ("driverRun.id", 801), ("driverRun.run_attempt", 2), ("driverRun.head_sha", "aa"*20),
    ("driverRun.repository.full_name", "evil/repo"), ("driverJobs.0.conclusion", "failure"),
    ("driverJobs.0.run_id", 801), ("driverJobs.0.run_attempt", 2),
    ("originalRun.conclusion", "success"), ("originalRun.event", "workflow_dispatch"),
    ("originalRun.run_attempt", 2), ("originalRun.pull_requests.0.base.sha", "aa"*20),
    ("originalJobs.2.steps.2.conclusion", "success"), ("originalJobs.1.conclusion", "failure"),
    ("check.app.id", 123), ("check.app.slug", "impostor"), ("check.conclusion", "failure"),
    ("check.external_id", "forged"), ("canonicalCheck.app.id", 1),
    ("canonicalCheck.id", 99), ("comment.id", 99), ("comment.user.type", "User"),
    ("comment.body", "forged"), ("ledgerComment.user.type", "User"),
    ("receipt.original.artifacts.claim.id", 0), ("receipt.recovery.provider_calls", 1),
    ("receipt.extra", True), ("receipt.original.head_sha", "AA"*20),
])
def test_receipt_rejects_broken_binding(field, value):
    facts = facts_fixture()
    cursor = facts
    parts = field.split(".")
    for part in parts[:-1]:
        cursor = cursor[int(part)] if isinstance(cursor, list) else cursor[part]
    cursor[parts[-1]] = value
    assert not node_validate(facts)


def test_receipt_binds_only_finalized_entry_and_allows_later_handoff():
    facts = facts_fixture()
    line = facts["ledgerComment"]["body"].splitlines()[1]
    ledger = json.loads(line[len("<!-- automation-budget-state:"):-4])
    ledger["handoff"] = {"reason": "later handoff 한글😀", "run_id": 999}
    prefix = facts["ledgerComment"]["body"].splitlines()[0] + "\n<!-- automation-budget-state:"
    facts["ledgerComment"]["body"] = prefix + compact(ledger) + " -->"
    assert node_validate(facts)
    ledger["invocations"][0]["elapsed_seconds"] += 1
    facts["ledgerComment"]["body"] = prefix + compact(ledger) + " -->"
    assert not node_validate(facts)


def test_duplicate_json_receipt_keys_are_rejected():
    facts = facts_fixture()
    facts["check"]["output"]["text"] = facts["check"]["output"]["text"].replace('"schema":1', '"schema":1,"schema":1')
    assert not node_validate(facts)


def test_python_canonical_json_matches_unicode_and_numeric_keys():
    value = {"z": "한글😀\u007f", "20": 1, "3": 2, "nested": {"b": 2, "a": 1}}
    result = subprocess.run(["node", "-e", "process.stdout.write(require(process.argv[1]).canonicalJson(JSON.parse(process.argv[2])))",
        str(HELPER), json.dumps(value)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == compact(value)


def test_quality_filtered_finalized_entry_is_valid_when_exactly_bound():
    facts = facts_fixture()
    prefix = '<!-- automation-budget-state:'
    ledger = json.loads(facts['ledgerComment']['body'].splitlines()[1][len(prefix):-4])
    ledger['invocations'][0]['outcome'] = 'quality_filtered'
    facts['ledgerComment']['body'] = '<!-- automation:review-invocation-budget:opencode:v1 -->\n' + prefix + compact(ledger) + ' -->'
    facts['receipt']['original']['finalized_invocation_sha256'] = digest(compact(ledger['invocations'][0]))
    facts['check']['output']['text'] = '<!-- automation-opencode-recovery:' + compact(facts['receipt']) + ' -->'
    assert node_validate(facts)


DISCOVER_SCRIPT = r'''
const fs = require('fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const f = input.facts, calls = [];
const reply = (name, params, data) => {
  calls.push([name, params]);
  if (input.fail === name) throw new Error('unavailable');
  return {data};
};
const github = {rest: {
  actions: {
    listWorkflowRunsForRepo: async (p) => reply('runs', p, {workflow_runs: input.runs || [f.driverRun]}),
    getWorkflowRunAttempt: async (p) => reply('attempt', p,
      p.run_id === 800 ? (input.driverAttempt || f.driverRun) : f.originalRun),
    listJobsForWorkflowRunAttempt: async (p) => {
      const jobs = p.run_id === 800 ? f.driverJobs : f.originalJobs;
      return reply('jobs', p, {total_count: input.jobsCount ?? jobs.length, jobs});
    },
  },
  checks: {listForRef: async (p) => {
    const checks = p.check_name === 'automation/opencode-finalization-recovery'
      ? (input.checks || [f.check]) : [f.canonicalCheck];
    return reply('checks', p, {total_count: input.checkCount ?? checks.length, check_runs: checks});
  }},
}};
(async () => {
  try {
    const records = await require(process.argv[1]).authenticateRecovery({github, repository:'example/repo', pr:7,
      comments: input.comments || [f.comment, f.ledgerComment]});
    process.stdout.write(JSON.stringify({records, calls}));
  } catch (error) { process.stdout.write(JSON.stringify({error: error.message, calls})); }
})();
'''


def discover(facts=None, **options):
    result = subprocess.run(['node', '-e', DISCOVER_SCRIPT, str(HELPER)],
        input=json.dumps(dict(facts=facts or facts_fixture(), **options)), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_server_discovery_authenticates_original_generation():
    result = discover()
    assert 'error' not in result, result
    assert len(result['records']) == 1
    record = result['records'][0]
    assert record['comment']['id'] == 902 and record['attestationId'] == 903
    assert record['state']['run_id'] == 700 and record['state']['run_attempt'] == 1
    assert result['calls'][0] == ['runs', dict(owner='example', repo='repo', event='workflow_dispatch', per_page=100, page=1)]
    assert [c[1]['run_id'] for c in result['calls'] if c[0] == 'attempt'] == [800, 700]


@pytest.mark.parametrize('kind', ['runs', 'checks', 'attempt', 'jobs'])
def test_server_api_uncertainty_fails_closed(kind):
    result = discover(fail=kind)
    assert 'error' in result and 'unavailable' in result['error']


def test_comments_never_seed_recovery_run_discovery():
    result = discover(runs=[])
    assert result.get('records') == [] and len(result['calls']) == 1


@pytest.mark.parametrize('conclusion', ['failure', 'cancelled'])
def test_completed_unsuccessful_exact_driver_is_untrusted(conclusion):
    run = facts_fixture()['driverRun']
    run['conclusion'] = conclusion
    assert discover(driverAttempt=run).get('records') == []


def test_pending_exact_driver_fails_closed():
    run = facts_fixture()['driverRun']
    run['status'] = 'in_progress'
    assert 'error' in discover(driverAttempt=run)


@pytest.mark.parametrize('option', ['jobsCount', 'checkCount'])
def test_server_pages_are_bounded(option):
    assert 'error' in discover(**{option: 101})


def test_server_discovery_rejects_ambiguous_ledgers():
    f = facts_fixture()
    other = dict(f['ledgerComment'], id=999)
    result = discover(f, comments=[f['comment'], f['ledgerComment'], other])
    assert 'error' in result


def test_server_discovery_ignores_unbound_receipts_before_original_lookup():
    f = facts_fixture()
    f['check']['app']['id'] = 99
    result = discover(f)
    assert result.get('records') == []
    assert not any(c[0] == 'attempt' for c in result['calls'])


def test_prepare_reuses_authenticated_recovered_comment(tmp_path):
    from test_review_workflow_logic import _run_opencode_ctx
    f = facts_fixture()
    output = _run_opencode_ctx(tmp_path, [f['comment'], f['ledgerComment']],
        check_runs=[f['canonicalCheck'], f['check']], workflow_runs=[f['originalRun'], f['driverRun']],
        run_jobs_by_attempt={'700:1': f['originalJobs'], '800:1': f['driverJobs']})
    assert 'previous_sha=' + f['receipt']['original']['head_sha'] in output
    assert 'previous_full_hash=' + f['receipt']['original']['full_diff_sha256'] in output


def test_prepare_failed_original_without_receipt_remains_untrusted(tmp_path):
    from test_review_workflow_logic import _run_opencode_ctx
    f = facts_fixture()
    output = _run_opencode_ctx(tmp_path, [f['comment'], f['ledgerComment']],
        check_runs=[f['canonicalCheck']], workflow_runs=[f['originalRun']],
        run_jobs_by_attempt={'700:1': f['originalJobs']})
    assert 'previous_sha=' + f['receipt']['original']['head_sha'] not in output


def test_live_canonicalizer_respects_recovered_original_generation(tmp_path):
    from test_review_workflow_logic import _run_opencode_canonicalize
    f = facts_fixture()
    calls = _run_opencode_canonicalize(tmp_path, [], [f['comment'], f['ledgerComment']],
        check_runs=[f['canonicalCheck'], f['check']], workflow_runs=[f['originalRun'], f['driverRun']],
        run_jobs_by_attempt={'700:1': f['originalJobs'], '800:1': f['driverJobs']}, run_id='42')
    assert not [c for c in calls if c[0] in {'create', 'update', 'delete', 'create-check', 'update-check'}]
    assert any(c[0] == 'list-runs' and c[1].get('event') == 'workflow_dispatch' for c in calls)


def loader_script():
    import yaml
    workflow = yaml.safe_load((ROOT / '.github/workflows/opencode-auto-review.yml').read_text())
    steps = workflow['jobs']['opencode-prepare']['steps']
    matches = [s for s in steps if s.get('id') == 'load-recovery-helper']
    assert len(matches) == 1, 'trusted recovery helper loader must exist'
    return matches[0]['with']['script']


def run_loader(tmp_path, **overrides):
    import base64
    f = facts_fixture()
    source = HELPER.read_bytes()
    data = dict(type='file', path='.github/actions/recover-opencode-review/receipt.js',
                encoding='base64', size=len(source), content=base64.b64encode(source).decode())
    data.update(overrides)
    script = r'''
const fs = require('fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const calls = [];
const context = {repo: {owner:'example', repo:'repo'}, runId:700};
const github = {rest: {
 actions: {getWorkflowRunAttempt: async p => { calls.push(['attempt',p]); return {data:input.run}; }},
 repos: {getContent: async p => { calls.push(['content',p]); return {data:input.content}; }},
}};
(async () => {
 try { await (new Function('github','context','require', 'return (async () => {' + input.script + '})();'))(github,context,require);
 process.stdout.write(JSON.stringify({calls}));
 } catch(e) { process.stdout.write(JSON.stringify({error:e.message,calls})); }
})();
'''
    import os
    result = subprocess.run(['node', '-e', script], input=json.dumps(dict(run=f['originalRun'], content=data, script=loader_script())),
        capture_output=True, text=True, env=dict(os.environ, RUNNER_TEMP=str(tmp_path), GITHUB_RUN_ATTEMPT='1'))
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_loader_fetches_exact_server_central_sha_and_writes_private_regular_file(tmp_path):
    result = run_loader(tmp_path)
    assert 'error' not in result, result
    target = tmp_path / 'opencode-recovery-receipt.js'
    assert target.read_bytes() == HELPER.read_bytes()
    assert target.stat().st_mode & 0o777 == 0o600
    assert result['calls'][1][1]['ref'] == '75' * 20
    assert result['calls'][1][1]['owner'] == 'jhw7500'


@pytest.mark.parametrize('overrides', [dict(type='symlink'), dict(path='evil.js'), dict(encoding='utf8'),
    dict(size=1000001), dict(content='!!!')])
def test_loader_rejects_untrusted_content(tmp_path, overrides):
    assert 'error' in run_loader(tmp_path, **overrides)
    assert not (tmp_path / 'opencode-recovery-receipt.js').exists()


def test_live_recovered_review_supports_zero_provider_call_reuse(tmp_path):
    from test_review_workflow_logic import _run_opencode_canonicalize, _single_mutation_body
    f = facts_fixture(full_hash=digest("TRUSTED FULL DIFF\n"))
    calls = _run_opencode_canonicalize(tmp_path, [f['comment'], f['ledgerComment']], [f['comment'], f['ledgerComment']],
        check_runs=[f['canonicalCheck'], f['check']], workflow_runs=[f['originalRun'], f['driverRun']],
        run_jobs_by_attempt={'700:1': f['originalJobs'], '800:1': f['driverJobs']},
        run_id='750', outcome='skipped', diff_mode='unchanged', unchanged_since_previous='true')
    body = _single_mutation_body(calls)
    state = json.loads(body.splitlines()[2][len('<!-- automation-state:'):-4])
    assert state['attempt_status'] == 'success' and state['diff_mode'] == 'unchanged'
    assert state['run_id'] == 750  # The later recovery driver (800) is never the review generation.
    assert '### New findings\nNone' in body
    assert ['output', 'publication_succeeded', 'true'] in calls


def test_exact_invocation_keys_cannot_be_folded_into_comma_key():
    f = facts_fixture()
    prefix = '<!-- automation-budget-state:'
    ledger = json.loads(f['ledgerComment']['body'].splitlines()[1][len(prefix):-4])
    e = ledger['invocations'][0]
    e['effort,elapsed_seconds'] = e.pop('effort')
    e.pop('elapsed_seconds')
    f['ledgerComment']['body'] = '<!-- automation:review-invocation-budget:opencode:v1 -->\n' + prefix + compact(ledger) + ' -->'
    f['receipt']['original']['finalized_invocation_sha256'] = digest(compact(e))
    f['check']['output']['text'] = '<!-- automation-opencode-recovery:' + compact(f['receipt']) + ' -->'
    assert not node_validate(f)


def test_missing_exact_driver_job_fails_closed():
    f = facts_fixture()
    f['driverJobs'] = []
    result = discover(f)
    assert 'error' in result and 'driver job' in result['error']


@pytest.mark.parametrize('record', ['driverRun', 'originalRun', 'driverJobs'])
@pytest.mark.parametrize('conclusion', [None, '', 'unknown-result'])
def test_unresolved_terminal_conclusion_fails_closed(record, conclusion):
    f = facts_fixture()
    value = f[record][0] if record == 'driverJobs' else f[record]
    value['conclusion'] = conclusion
    assert 'error' in discover(f)


@pytest.mark.parametrize('missing', [0, 1, 2])
@pytest.mark.parametrize('kind', ['job', 'step', 'job-null', 'job-empty', 'step-null', 'step-empty'])
def test_missing_original_job_prevents_live_mutations(tmp_path, missing, kind):
    from test_review_workflow_logic import _run_opencode_canonicalize
    f = facts_fixture()
    if kind == 'job':
        f['originalJobs'].pop(missing)
    elif kind == 'step':
        f['originalJobs'][missing]['steps'].pop(0)
    elif kind.startswith('job-'):
        f['originalJobs'][missing]['conclusion'] = None if kind.endswith('null') else ''
    else:
        f['originalJobs'][missing]['steps'][0]['conclusion'] = None if kind.endswith('null') else ''
    result = discover(f)
    assert 'original job' in result.get('error', '')
    calls = _run_opencode_canonicalize(tmp_path, [], [f['comment'], f['ledgerComment']],
        check_runs=[f['canonicalCheck'], f['check']], workflow_runs=[f['originalRun'], f['driverRun']],
        run_jobs_by_attempt={'700:1': f['originalJobs'], '800:1': f['driverJobs']},
        run_id='42', expect_error=True)
    assert not [c for c in calls if c[0] in {'create', 'update', 'delete', 'create-check', 'update-check'}]


def test_noncanonical_job_cannot_hide_a_second_failed_finalizer():
    f = facts_fixture()
    f['originalJobs'][0]['steps'].append(dict(name='Finalize OpenCode review budget', status='completed', conclusion='failure'))
    assert not node_validate(f)


@pytest.mark.parametrize('job', [0, 1, 2])
def test_receipt_requires_original_producer_step_order(job):
    f = facts_fixture()
    f['originalJobs'][job]['steps'].reverse()
    assert not node_validate(f)


def test_original_workflow_head_may_differ_from_reviewed_head():
    f = facts_fixture()
    workflow_head = '98' * 20
    f['receipt']['original']['workflow_head'] = workflow_head
    f['originalRun']['head_sha'] = workflow_head
    f['canonicalCheck']['head_sha'] = workflow_head
    a = json.loads(f['canonicalCheck']['output']['text'][len('<!-- automation-attestation:'):-4])
    a['workflow_head'] = workflow_head
    f['canonicalCheck']['output']['text'] = '<!-- automation-attestation:' + compact(a) + ' -->'
    f['check']['output']['text'] = '<!-- automation-opencode-recovery:' + compact(f['receipt']) + ' -->'
    assert node_validate(f)
    assert len(discover(f).get('records', [])) == 1


def test_matched_receipt_candidates_are_bounded():
    f = facts_fixture()
    checks = [dict(f['check'], id=10000+i) for i in range(41)]
    result = discover(f, checks=checks)
    assert 'candidate limit' in result.get('error', '')
    assert not any(c[0] == 'attempt' for c in result['calls'])


def test_recent_recovery_selection_is_bounded_to_twenty():
    f = facts_fixture()
    runs = [dict(f['driverRun'], id=800+i, head_sha=f'{i+1:040x}') for i in range(21)]
    result = discover(f, runs=runs)
    assert result.get('records') == []
    assert len([c for c in result['calls'] if c[0] == 'checks']) == 20


def test_clean_normal_prepare_does_not_query_recovery(tmp_path):
    from test_review_workflow_logic import _run_opencode_ctx
    f = facts_fixture()
    f['originalRun']['conclusion'] = 'success'
    f['originalJobs'][2]['conclusion'] = 'success'
    f['originalJobs'][2]['steps'][2]['conclusion'] = 'success'
    output = _run_opencode_ctx(tmp_path, [f['comment']], check_runs=[f['canonicalCheck']],
        workflow_runs=[f['originalRun']], run_jobs_by_attempt={'700:1': f['originalJobs']})
    assert 'previous_sha=' + f['receipt']['original']['head_sha'] in output
    assert 'event=workflow_dispatch' not in (tmp_path / 'gh-calls.log').read_text()


def test_unauthenticated_recovery_pending_driver_prevents_live_writes(tmp_path):
    from test_review_workflow_logic import _run_opencode_canonicalize
    f = facts_fixture()
    pending = dict(f['driverRun'], status='in_progress', conclusion=None)
    calls = _run_opencode_canonicalize(tmp_path, [], [f['comment'], f['ledgerComment']],
        check_runs=[f['canonicalCheck'], f['check']], workflow_runs=[f['originalRun'], f['driverRun']],
        workflow_run_attempts=[f['originalRun'], pending],
        run_jobs_by_attempt={'700:1': f['originalJobs'], '800:1': f['driverJobs']}, run_id='900', expect_error=True)
    assert not [c for c in calls if c[0] in {'create', 'update', 'delete', 'create-check', 'update-check'}]


@pytest.mark.parametrize('record', ['comment', 'ledgerComment'])
def test_foreign_bot_identity_is_rejected(record):
    f = facts_fixture()
    f[record]['user']['login'] = 'foreign-bot[bot]'
    assert not node_validate(f)


def test_recovery_cannot_bind_a_different_prepared_attempt():
    f = facts_fixture()
    o = f['receipt']['original']
    o['run_attempt'] = 2
    f['originalRun']['run_attempt'] = 2
    for job in f['originalJobs']:
        job['run_attempt'] = 2
    lines = f['comment']['body'].splitlines()
    state = json.loads(lines[2][len('<!-- automation-state:'):-4])
    state['run_attempt'] = 2
    lines[2] = '<!-- automation-state:' + compact(state) + ' -->'
    f['comment']['body'] = '\n'.join(lines)
    o['body_sha256'], o['state_sha256'] = digest(f['comment']['body']), digest(compact(state))
    a = json.loads(f['canonicalCheck']['output']['text'][len('<!-- automation-attestation:'):-4])
    a.update(run_attempt=2, body_sha256=o['body_sha256'], state_sha256=o['state_sha256'])
    f['canonicalCheck']['output']['text'] = '<!-- automation-attestation:' + compact(a) + ' -->'
    f['canonicalCheck']['external_id'] = f['canonicalCheck']['external_id'].replace(':700:1:', ':700:2:')
    ledger = json.loads(f['ledgerComment']['body'].splitlines()[1][len('<!-- automation-budget-state:'):-4])
    ledger['invocations'][0]['run_attempt'] = 2
    o['finalized_invocation_sha256'] = digest(compact(ledger['invocations'][0]))
    f['ledgerComment']['body'] = '<!-- automation:review-invocation-budget:opencode:v1 -->\n<!-- automation-budget-state:' + compact(ledger) + ' -->'
    f['check']['external_id'] = f['check']['external_id'].replace(':700:1:', ':700:2:')
    f['check']['output']['text'] = '<!-- automation-opencode-recovery:' + compact(f['receipt']) + ' -->'
    assert not node_validate(f)


def test_receipt_requires_compact_single_line_json():
    f = facts_fixture()
    f['check']['output']['text'] = '<!-- automation-opencode-recovery:' + json.dumps(f['receipt']) + ' -->'
    assert not node_validate(f)


def test_immutable_server_path_is_independent_from_named_server_ref():
    f = facts_fixture(pinned_original=True)
    assert node_validate(f)
    assert len(discover(f).get('records', [])) == 1


def test_original_tag_path_remains_exact_server_provenance():
    assert node_validate(facts_fixture(pinned_original=False))


def test_original_named_ref_mismatch_is_rejected():
    f = facts_fixture()
    f['originalRun']['referenced_workflows'][0]['ref'] = 'refs/tags/changed'
    assert not node_validate(f)
