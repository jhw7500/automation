'use strict';
// Read-only replay of a digest-approved original canonicalizer, stopped before its
// first mutation. No credentials, network transport, model CLI or write API exists.
const fs = require('node:fs');
const crypto = require('node:crypto');
const childProcess = require('node:child_process');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const approved = new Set(['b8804d0c387e4f7e554443a1d0edd5862d9c4b1c1435de4140c621c241a25df5',
  '3915f9bc32985745996d2246017cff9122460d25a071ca72568ac73c75339371']);
if (!approved.has(crypto.createHash('sha256').update(input.source).digest('hex'))) {
  throw new Error('original_replay_unsupported');
}
const outputs = {};
const target = input.target;
const [owner, repo] = target.repository.split('/');
const history = input.history;
const validateArgs = (args) => {
  if (args.owner !== owner || args.repo !== repo) throw new Error('replay_repository_mismatch');
};
const getAttempt = (args) => {
  const value = history.attempts[`${args.run_id}:${args.attempt_number}`];
  if (!value) throw new Error('replay_attempt_unavailable');
  return value;
};
const rest = {
  actions: {
    getArtifact: async (args) => {
      validateArgs(args);
      const data = input.artifacts.find((item) => item.id === args.artifact_id);
      if (!data) throw new Error('replay_artifact_unavailable');
      return { data };
    },
    getWorkflowRunAttempt: async (args) => {
      validateArgs(args);
      return { data: getAttempt(args).run };
    },
    listWorkflowRunsForRepo: async (args) => {
      validateArgs(args);
      const runs = history.runs.filter((item) => item.event === args.event);
      if (runs.length > 100 || args.page !== 1 || args.per_page !== 100) {
        throw new Error('replay_runs_unbounded');
      }
      return { data: { total_count: runs.length, workflow_runs: runs } };
    },
    listJobsForWorkflowRunAttempt: async (args) => {
      validateArgs(args);
      const jobs = getAttempt(args).jobs;
      if (jobs.length > 100 || args.page !== 1 || args.per_page !== 100) {
        throw new Error('replay_jobs_unbounded');
      }
      return { data: { total_count: jobs.length, jobs } };
    },
  },
  checks: {
    listForRef: async (args) => {
      validateArgs(args);
      const checks = history.checks.filter((item) => item.head_sha === args.ref
        && (!args.check_name || item.name === args.check_name));
      if (checks.length > 100) throw new Error('replay_checks_unbounded');
      return { data: { total_count: checks.length, check_runs: checks } };
    },
  },
  issues: { listComments: Symbol('read-comments') },
  pulls: {
    get: async (args) => {
      validateArgs(args);
      if (args.pull_number !== target.pr) throw new Error('replay_pr_mismatch');
      return { data: input.pr };
    },
  },
};
const github = {
  rest,
  paginate: async (method, args) => {
    validateArgs(args);
    if (method !== rest.issues.listComments || args.issue_number !== target.pr
        || history.comments.length > 300) throw new Error('replay_comments_unavailable');
    return history.comments;
  },
};
const readonlyRequire = (name) => {
  // This is the recovery checkout's sealed verifier, never an artifact/PR path.
  if (name === require('node:path').join(__dirname, 'receipt.js')) return require('./receipt.js');
  if (name === 'fs') return {
    readFileSync: fs.readFileSync, realpathSync: fs.realpathSync,
    readdirSync: fs.readdirSync, lstatSync: fs.lstatSync,
  };
  if (name === 'crypto') return crypto;
  if (name === 'path') return require('node:path');
  if (name === 'child_process') return {
    spawnSync: (command, args, options) => {
      if (command !== '/usr/bin/git' || options.cwd !== input.env.TRUSTED_WORKSPACE) {
        throw new Error('replay_command_refused');
      }
      return childProcess.spawnSync(command, args, { ...options, timeout: 30000, maxBuffer: 8000000 });
    },
  };
  throw new Error('replay_module_refused');
};
for (const key of Object.keys(process.env)) delete process.env[key];
Object.assign(process.env, input.env, { PATH: '/usr/bin:/bin', LANG: 'C.UTF-8', LC_ALL: 'C.UTF-8' });
const suffix = `
const remainingFindingIds = [];
let activeFindingSection = false;
for (const line of displayBody.split('\\n')) {
  if (line === '### New findings' || line === '### Still open') activeFindingSection = true;
  else if (/^### /.test(line)) activeFindingSection = false;
  else if (activeFindingSection) {
    const match = line.match(/^#### (RVW-[0-9a-f]{12}) \\[(?:CRITICAL|HIGH|MEDIUM)\\] .+$/);
    if (match && !remainingFindingIds.includes(match[1]) && remainingFindingIds.length < 8)
      remainingFindingIds.push(match[1]);
  }
}
return {body:bodyFor(recoveryCheckId), state, succeeded, quality_filtered:qualityFiltered,
  remaining_finding_ids:remainingFindingIds};
`;
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
if (!Number.isSafeInteger(input.check_id) || input.check_id < 1) throw new Error('replay_check_invalid');
new AsyncFunction('require', 'github', 'context', 'core', 'recoveryCheckId', input.source + suffix)(
  readonlyRequire, github, { repo: { owner, repo } }, {
    notice: () => {}, warning: () => {},
    setOutput: (key, value) => { outputs[key] = value; },
  }, input.check_id,
).then((result) => {
  if (!result || outputs.budget_metrics_valid !== 'true') throw new Error('canonical_replay_refused');
  process.stdout.write(JSON.stringify(result));
}).catch((error) => {
  process.stderr.write('canonical_replay_refused\n');
  process.exitCode = 1;
});
