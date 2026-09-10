"""Execute recovery driver validation and caller routing with no GitHub mutations."""
import itertools
import json
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow(path):
    return yaml.load((ROOT / path).read_text(), Loader=yaml.BaseLoader)


def node(script, value):
    result = subprocess.run(["node", "-e", script], input=json.dumps(value), text=True,
                            capture_output=True, check=True, timeout=15)
    return json.loads(result.stdout)


@pytest.mark.parametrize("force,recover,partial", itertools.product([False, True], [False, True], range(16)))
def test_recovery_dispatch_excludes_every_provider_route(force, recover, partial):
    caller = workflow("examples/baseline-workflows/.github/workflows/opencode-auto-review.yml")
    names = ["recovery_original_run_id", "recovery_original_run_attempt",
             "recovery_expected_head_sha", "recovery_expected_base_sha"]
    inputs = {"force_review": force, "recover_finalization": recover}
    inputs.update({name: "present" if partial & (1 << index) else "" for index, name in enumerate(names)})
    conditions = {name: caller["jobs"][name]["if"] for name in
                  ["opencode-review", "opencode-recovery", "reject-conflicting-dispatch"]}
    selected = node("""
const f=JSON.parse(require('fs').readFileSync(0,'utf8'));
const github={event_name:'workflow_dispatch'};
const result=Object.entries(f.conditions).filter(([name,expression]) =>
  new Function('github','inputs', 'return Boolean('+expression+');')(github,f.inputs)).map(([name])=>name);
process.stdout.write(JSON.stringify(result));
""", {"conditions": conditions, "inputs": inputs})
    if recover or partial:
        assert selected == ["reject-conflicting-dispatch" if force else "opencode-recovery"]
    else:
        assert selected == (["opencode-review"] if force else [])


def driver_validation(**changes):
    document = workflow(".github/workflows/opencode-recover-finalization.yml")
    steps = document["jobs"]["opencode-recover-finalization"]["steps"]
    script = steps[0]["with"]["script"]
    env = {"RECOVERY_PR": "52", "ORIGINAL_RUN_ID": "700", "ORIGINAL_RUN_ATTEMPT": "1",
        "EXPECTED_HEAD_SHA": "a" * 40, "EXPECTED_BASE_SHA": "b" * 40,
        "GITHUB_RUN_ID": "800", "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_REPOSITORY": "example/repo", "GITHUB_SERVER_URL": "https://github.com"}
    run = {"id": 800, "run_attempt": 1, "status": "in_progress", "event": "workflow_dispatch",
        "repository": {"full_name": "example/repo"}, "head_sha": "c" * 40,
        "path": ".github/workflows/opencode-auto-review.yml",
        "referenced_workflows": [{"path": "jhw7500/automation/.github/workflows/"
            "opencode-recover-finalization.yml@" + "d" * 40, "sha": "d" * 40}]}
    env.update(changes.get("env", {}))
    run.update(changes.get("run", {}))
    return node("""
const f=JSON.parse(require('fs').readFileSync(0,'utf8')), outputs={}, calls=[];
Object.assign(process.env,f.env);
const github={rest:{actions:{getWorkflowRunAttempt:async(args)=>{calls.push(args);return {data:f.run}}}}};
const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;
new AsyncFunction('github','context','core',f.script)(github,{eventName:f.event,repo:{owner:'example',repo:'repo'}},
 {setOutput:(k,v)=>outputs[k]=v})
 .then(()=>process.stdout.write(JSON.stringify({outputs,calls})))
 .catch(()=>process.stdout.write(JSON.stringify({refused:true,outputs,calls})));
""", {"script": script, "env": env, "run": run, "event": changes.get("event", "workflow_dispatch")})


def test_driver_uses_only_exact_server_resolved_central_sha():
    result = driver_validation()
    assert result["outputs"] == {"central_sha": "d" * 40}
    assert result["calls"] == [{"owner": "example", "repo": "repo", "run_id": 800, "attempt_number": 1}]


@pytest.mark.parametrize("env", [
    {"RECOVERY_PR": "052"}, {"RECOVERY_PR": "1e2"}, {"ORIGINAL_RUN_ID": "0"},
    {"ORIGINAL_RUN_ATTEMPT": "-1"}, {"EXPECTED_HEAD_SHA": "a" * 39},
    {"EXPECTED_BASE_SHA": "A" * 40}, {"GITHUB_RUN_ATTEMPT": "2.0"},
])
def test_invalid_inputs_stop_before_server_lookup(env):
    result = driver_validation(env=env)
    assert result.get("refused") is True and not result["outputs"] and not result["calls"]


@pytest.mark.parametrize("run", [
    {"id": 801}, {"run_attempt": 2}, {"repository": {"full_name": "other/repo"}},
    {"event": "pull_request"}, {"head_sha": "invalid"}, {"path": "../review.yml"},
    {"referenced_workflows": []},
    {"referenced_workflows": [{"path": "jhw7500/automation/.github/workflows/"
      "opencode-recover-finalization.yml@main", "sha": "d" * 40}]},
])
def test_mismatched_server_driver_cannot_select_helper_checkout(run):
    result = driver_validation(run=run)
    assert result.get("refused") is True and not result["outputs"]


def test_workflow_keeps_writer_serialization_and_isolated_trusted_source():
    document = workflow(".github/workflows/opencode-recover-finalization.yml")
    assert set(document["on"]) == {"workflow_call"}
    assert document["concurrency"] == {
        "group": "automation-opencode-auto-review-${{ github.repository }}-${{ inputs.pr_number }}",
        "cancel-in-progress": "false"}
    job = document["jobs"]["opencode-recover-finalization"]
    assert job["permissions"] == {"actions": "read", "checks": "write", "contents": "read",
                                 "issues": "write", "pull-requests": "read"}
    trusted, target, recover = job["steps"][1:4]
    assert trusted["with"]["repository"] == "jhw7500/automation"
    assert trusted["with"]["ref"] == "${{ steps.driver.outputs.central_sha }}"
    assert target["with"]["ref"] == "${{ inputs.expected_head_sha }}"
    assert trusted["with"]["path"] != target["with"]["path"]
    assert trusted["with"]["persist-credentials"] == target["with"]["persist-credentials"] == "false"
    assert recover["working-directory"] == trusted["with"]["path"]
    assert "secrets" not in document["on"]["workflow_call"]
    caller = workflow("examples/baseline-workflows/.github/workflows/opencode-auto-review.yml")
    assert "secrets" not in caller["jobs"]["opencode-recovery"]
