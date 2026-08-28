import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).parents[1]
ACTION_ROOT = ROOT / ".github/actions/review-invocation-budget"
ACTION = ACTION_ROOT / "action.yml"
HELPER = ACTION_ROOT / "review_invocation_budget.py"
HEAD_A = "a" * 40
HEAD_B = "b" * 40
HASH_1 = "1" * 64
HASH_2 = "2" * 64
CENTRAL_REF = "refs/tags/v1.47"
CENTRAL_SHA = "d" * 40
CENTRAL_PATH = (
    "jhw7500/automation/.github/workflows/claude-code-review.yml@" + CENTRAL_SHA
)

SPEC = importlib.util.spec_from_file_location("review_invocation_budget_action_helper", HELPER)
assert SPEC and SPEC.loader
budget = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = budget
SPEC.loader.exec_module(budget)


def _prior_comment() -> str:
    invocation = budget.Invocation(
        run_id=501,
        run_attempt=1,
        head_sha=HEAD_A,
        full_diff_sha256=HASH_1,
        caller_workflow_path=".github/workflows/claude-review-caller.yml",
        caller_event="pull_request",
        referenced_workflow_path=CENTRAL_PATH,
        referenced_workflow_ref=CENTRAL_REF,
        referenced_workflow_sha=CENTRAL_SHA,
        round_number=1,
        override_event_id=None,
        model_route=("route-v1",),
        effort="medium",
        call_unit="claude-code-action review session",
        call_count=1,
        estimated_input_tokens=20_002,
        elapsed_seconds=12,
        status="finalized",
        outcome="provider_failure",
        stop_reason="provider_failure",
        remaining_finding_ids=(),
    )
    state = budget.LedgerState.initial("example/repo", 52, "claude", invocations=(invocation,))
    return (
        f"{budget.MARKERS['claude']}\n"
        f"{budget.STATE_PREFIX}{budget.serialize_ledger(state)}{budget.STATE_SUFFIX}\n\nprior"
    )


def _claimed_comment() -> str:
    invocation = budget.Invocation(
        run_id=700,
        run_attempt=1,
        head_sha=HEAD_A,
        full_diff_sha256=HASH_1,
        caller_workflow_path=".github/workflows/claude-review-caller.yml",
        caller_event="pull_request",
        referenced_workflow_path=CENTRAL_PATH,
        referenced_workflow_ref=CENTRAL_REF,
        referenced_workflow_sha=CENTRAL_SHA,
        round_number=1,
        override_event_id=None,
        model_route=("route-v2",),
        effort="medium",
        call_unit="claude-code-action review session",
        call_count=0,
        estimated_input_tokens=20_002,
        elapsed_seconds=0,
        status="claimed",
        outcome=None,
        stop_reason="claimed",
        remaining_finding_ids=(),
    )
    state = budget.LedgerState.initial("example/repo", 52, "claude", invocations=(invocation,))
    return (
        f"{budget.MARKERS['claude']}\n"
        f"{budget.STATE_PREFIX}{budget.serialize_ledger(state)}{budget.STATE_SUFFIX}\n\nprior"
    )


def _bot_comment(body: str, comment_id: int = 80, login: str = "github-actions[bot]") -> dict:
    return {"id": comment_id, "body": body, "user": {"login": login}}


FAKE_GH = r'''#!/usr/bin/python3
import json
import os
import sys
from pathlib import Path

config = json.loads(Path(os.environ["FAKE_GH_CONFIG"]).read_text())
state_path = Path(os.environ["FAKE_GH_STATE"])
state = json.loads(state_path.read_text()) if state_path.exists() else {"pr": 0, "comments": 0}
args = sys.argv[1:]
raw_output = None
with Path(os.environ["FAKE_GH_LOG"]).open("a") as stream:
    stream.write(json.dumps(args) + "\n")

endpoint = next((value for value in args if value.startswith("repos/")), "")
method = "GET"
if "--method" in args:
    method = args[args.index("--method") + 1]

if endpoint.endswith("/pulls/52"):
    state["pr"] += 1
    head = config["head"]
    if config["scenario"] == "pr-head-changed-before-patch" and state["pr"] > 1:
        head = "c" * 40
    response = {"number": 52, "head": {"sha": head}}
elif endpoint.endswith("/issues/52/comments?per_page=100"):
    state["comments"] += 1
    response = config["comments"]
    if config["scenario"] == "empty-cas-pages" and state["comments"] > 1:
        raw_output = " \n\t"
elif "/issues/comments/" in endpoint:
    comment = config["comments"][0]
    if config["scenario"] == "comment-body-changed-before-patch":
        comment = {**comment, "body": comment["body"] + "\nchanged"}
    response = comment
elif endpoint.endswith("/issues/52/timeline?per_page=100"):
    response = []
elif "/actions/runs/501/attempts/1" in endpoint:
    response = {
        "id": 501,
        "run_attempt": 1,
        "head_sha": HEAD_A if (HEAD_A := "a" * 40) else HEAD_A,
        "event": "pull_request",
        "path": ".github/workflows/claude-review-caller.yml",
        "status": "completed",
        "conclusion": "failure",
        "repository": {"full_name": "example/repo"},
        "pull_requests": [{"number": 52}],
        "referenced_workflows": [{
            "path": "jhw7500/automation/.github/workflows/claude-code-review.yml@" + "d" * 40,
            "ref": "refs/tags/v1.47",
            "sha": "d" * 40,
        }],
    }
    if config["scenario"] == "historical-run-head-mismatch":
        response["head_sha"] = "b" * 40
elif "/actions/runs/700/attempts/1" in endpoint:
    response = {
        "id": 700,
        "run_attempt": 1,
        "head_sha": config["head"],
        "event": "pull_request",
        "path": ".github/workflows/claude-review-caller.yml",
        "status": "in_progress",
        "conclusion": None,
        "repository": {"full_name": "example/repo"},
        "pull_requests": [{"number": 52}],
        "referenced_workflows": [{
            "path": "jhw7500/automation/.github/workflows/claude-code-review.yml@" + "d" * 40,
            "ref": "refs/tags/v1.47",
            "sha": "d" * 40,
        }],
    }
    if config["scenario"] == "current-run-reference-missing":
        response["referenced_workflows"] = []
    elif config["scenario"] == "current-run-reference-duplicate":
        response["referenced_workflows"].append(dict(response["referenced_workflows"][0]))
    elif config["scenario"] == "current-run-reference-wrong-path":
        response["referenced_workflows"][0]["path"] = (
            "other/repo/.github/workflows/claude-code-review.yml@" + "d" * 40
        )
    elif config["scenario"] == "current-run-reference-malformed-ref":
        response["referenced_workflows"][0]["ref"] = "bad ref"
    elif config["scenario"] == "current-run-reference-omits-ref":
        response["referenced_workflows"][0].pop("ref")
    elif config["scenario"] == "current-run-reference-wrong-sha":
        response["referenced_workflows"][0]["sha"] = "not-a-sha"
elif "/collaborators/" in endpoint and endpoint.endswith("/permission"):
    response = {"permission": "write", "user": {"login": "maintainer"}}
elif method in {"POST", "PATCH"}:
    input_path = Path(args[args.index("--input") + 1])
    response = {"id": 101 if method == "POST" else 80, "body": json.loads(input_path.read_text())["body"]}
else:
    raise SystemExit(f"unexpected gh call: {args!r}")

state_path.write_text(json.dumps(state))
sys.stdout.write(json.dumps(response) if raw_output is None else raw_output)
'''


PYTHON_WRAPPER = r'''#!/usr/bin/bash
/usr/bin/python3 -c 'import json, os, sys; open(os.environ["FAKE_PYTHON_ARGV"], "a").write(json.dumps(sys.argv[1:]) + "\n")' "$@"
exec /usr/bin/python3 "$@"
'''


@dataclass
class ActionResult:
    outputs: dict[str, str]
    checkpoint: dict
    calls: list[list[str]]
    provider_started: bool = False


class FakeGitHub:
    def __init__(self, tmp_path: Path):
        self.tmp_path = tmp_path

    def run_action(
        self, *, mode: str, scenario: str, hostile: bool = False,
        diff_mode: str = "full", env_overrides: dict[str, str] | None = None,
    ) -> ActionResult:
        document = yaml.load(ACTION.read_text(), Loader=yaml.BaseLoader)
        run = document["runs"]["steps"][0]["run"]
        bin_dir = self.tmp_path / "bin"
        bin_dir.mkdir(exist_ok=True)
        gh = bin_dir / "gh"
        gh.write_text(FAKE_GH)
        gh.chmod(0o755)
        python = bin_dir / "python3"
        python.write_text(PYTHON_WRAPPER)
        python.chmod(0o755)

        workspace = self.tmp_path / "workspace"
        runner_temp = self.tmp_path / "runner-temp"
        workspace.mkdir(exist_ok=True)
        runner_temp.mkdir(exist_ok=True)
        marker = workspace / "PWNED"
        if hostile:
            input_path = workspace / '- input " $(touch PWNED)\n.diff'
            route = '--route " $(touch PWNED)\nvalue'
            effort = '--effort " $(touch PWNED)\nvalue'
        else:
            input_path = workspace / "review.diff"
            route = "route-v2"
            effort = "medium"
        input_path.write_bytes(b"abcde")

        prior = _prior_comment()
        if scenario in {
            "first-comment-create", "empty-cas-pages",
            "current-run-reference-omits-ref",
        }:
            comments = []
            head, full_hash = HEAD_A, HASH_1
        elif scenario == "finalize-trusted-comment":
            comments = [_bot_comment(_claimed_comment())]
            head, full_hash = HEAD_A, HASH_1
        else:
            comments = [_bot_comment(prior)]
            head, full_hash = HEAD_B, HASH_2
        if scenario == "duplicate-trusted-comments":
            comments.append(_bot_comment(prior, 81))
        elif scenario == "foreign-author-marker":
            comments = [_bot_comment(prior, login="octocat")]

        config = self.tmp_path / "config.json"
        config.write_text(json.dumps({"scenario": scenario, "head": head, "comments": comments}))
        output = self.tmp_path / "github-output"
        summary = self.tmp_path / "summary"
        checkpoint = workspace / "checkpoint.json"
        log = self.tmp_path / "gh.log"
        state = self.tmp_path / "gh-state.json"
        argv_log = self.tmp_path / "python-argv"
        request_values = {
            "GH_TOKEN": "token",
            "BUDGET_MODE": mode,
            "REVIEWER": "claude",
            "PR_NUMBER": "52",
            "EXPECTED_HEAD_SHA": head,
            "FULL_DIFF_SHA256": full_hash,
            "DIFF_MODE": diff_mode,
            "INPUT_FILES_JSON": json.dumps([str(input_path)]),
            "AUTHENTICATED_REVIEW_JSON": json.dumps({
                "success": False,
                "head_sha": None,
                "full_diff_sha256": None,
                "remaining_finding_ids": [],
            }),
            "MODEL_ROUTE_JSON": json.dumps([route]),
            "EFFORT": effort,
            "ACTUAL_CALL_COUNT": "1" if mode == "finalize" else "0",
            "ELAPSED_SECONDS": "12" if mode == "finalize" else "0",
            "REVIEW_OUTCOME": "success" if mode == "finalize" else "checkpoint_failure",
            "STOP_REASON": "success" if mode == "finalize" else "",
            "REMAINING_FINDING_IDS_JSON": "[]",
            "CHECKPOINT_FILE": str(checkpoint),
        }
        env = {
            **os.environ,
            **request_values,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "GITHUB_ACTION_PATH": str(ACTION_ROOT),
            "GITHUB_REPOSITORY": "example/repo",
            "GITHUB_RUN_ID": "700",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_WORKSPACE": str(workspace),
            "GITHUB_SERVER_URL": "https://github.com",
            "RUNNER_TEMP": str(runner_temp),
            "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": str(summary),
            "FAKE_GH_CONFIG": str(config),
            "FAKE_GH_STATE": str(state),
            "FAKE_GH_LOG": str(log),
            "FAKE_PYTHON_ARGV": str(argv_log),
        }
        env.update(env_overrides or {})
        subprocess.run(["bash", "-c", run], cwd=workspace, env=env, check=True)
        outputs = dict(line.split("=", 1) for line in output.read_text().splitlines())
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        assert not marker.exists()
        if hostile:
            argv = [json.loads(entry) for entry in argv_log.read_text().splitlines()]
            for name in ("INPUT_FILES_JSON", "MODEL_ROUTE_JSON", "EFFORT"):
                value = request_values[name]
                assert sum(part == value for entry in argv for part in entry) == 1, name
        return ActionResult(outputs, json.loads(checkpoint.read_text()), calls)


@pytest.fixture
def fake_github(tmp_path):
    return FakeGitHub(tmp_path)


def test_action_metadata_has_one_inert_environment_bridge():
    document = yaml.load(ACTION.read_text(), Loader=yaml.BaseLoader)
    assert set(document["inputs"]) == {
        "github-token", "mode", "reviewer", "pr-number", "expected-head-sha",
        "full-diff-sha256", "diff-mode", "input-files-json", "authenticated-review-json",
        "model-route-json", "effort", "checkpoint-file", "actual-call-count",
        "elapsed-seconds", "outcome", "stop-reason", "remaining-finding-ids-json",
    }
    assert {name for name, value in document["inputs"].items() if value["required"] == "true"} == {
        "github-token", "mode", "reviewer", "pr-number", "expected-head-sha",
        "full-diff-sha256", "diff-mode", "input-files-json", "authenticated-review-json",
        "model-route-json", "effort", "checkpoint-file",
    }
    assert {name: value["default"] for name, value in document["inputs"].items() if "default" in value} == {
        "actual-call-count": "0",
        "elapsed-seconds": "0",
        "outcome": "checkpoint_failure",
        "stop-reason": "",
        "remaining-finding-ids-json": "[]",
    }
    assert set(document["outputs"]) == {
        "allow-invocation", "decision", "round", "invocation-key", "checkpoint-sha256",
        "comment-id",
    }
    assert document["runs"]["using"] == "composite"
    assert len(document["runs"]["steps"]) == 1
    step = document["runs"]["steps"][0]
    assert step["id"] == "budget"
    assert step["shell"] == "bash"
    assert step["env"]["GH_TOKEN"] == "${{ inputs.github-token }}"
    assert "${{ inputs." not in step["run"]
    assert step["run"].index('cat -- "$budget_dir/summary.md" >> "$GITHUB_STEP_SUMMARY"') < step[
        "run"
    ].index('python3 - "$budget_dir/output.json" "$mutation_response" "$GITHUB_OUTPUT"')


def test_hostile_json_inputs_remain_single_python_arguments(fake_github):
    result = fake_github.run_action(mode="claim", scenario="first-comment-create", hostile=True)
    invocation = result.checkpoint["ledger"]["invocations"][0]
    assert invocation["model_route"] == ['--route " $(touch PWNED)\nvalue']
    assert invocation["effort"] == '--effort " $(touch PWNED)\nvalue'


@pytest.mark.parametrize("diff_mode", ["full", "delta"])
def test_public_changed_diff_modes_map_to_a_claim(fake_github, diff_mode):
    result = fake_github.run_action(
        mode="claim", scenario="first-comment-create", diff_mode=diff_mode,
    )
    assert result.outputs["decision"] == "claimed"
    assert result.outputs["allow-invocation"] == "true"
    assert result.checkpoint["ledger"]["invocations"][0]["full_diff_sha256"] == HASH_1


@pytest.mark.parametrize("diff_mode", ["full", "delta"])
def test_changed_claim_rejects_empty_staged_input_without_consuming_round(
    fake_github, diff_mode,
):
    result = fake_github.run_action(
        mode="claim",
        scenario="first-comment-create",
        diff_mode=diff_mode,
        env_overrides={"INPUT_FILES_JSON": "[]"},
    )
    mutations = [call for call in result.calls if "--method" in call]
    assert result.outputs["decision"] == "state_invalid"
    assert result.outputs["allow-invocation"] == "false"
    assert mutations == []
    assert result.checkpoint["ledger"]["invocations"] == []
    assert result.checkpoint["handoff"]["stop_reason"] == "input_files_empty"


def test_unavailable_diff_refuses_locally_with_diagnostic_artifacts(fake_github):
    result = fake_github.run_action(
        mode="claim",
        scenario="first-comment-create",
        diff_mode="unavailable",
        env_overrides={"EXPECTED_HEAD_SHA": "", "FULL_DIFF_SHA256": ""},
    )
    assert result.calls == []
    assert result.outputs["allow-invocation"] == "false"
    assert result.outputs["decision"] == "diff_unavailable"
    assert result.checkpoint == {
        "handoff": {
            "current_full_diff_sha256": None,
            "current_head_sha": None,
            "current_run_attempt": 1,
            "current_run_id": 700,
            "decision": "diff_unavailable",
            "outcome": "checkpoint_failure",
            "pr": 52,
            "repository": "example/repo",
            "reviewer": "claude",
            "stop_reason": "diff_unavailable",
        },
        "ledger": None,
        "schema": 1,
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"BUDGET_MODE": "bogus"},
        {"REVIEWER": "bogus"},
        {"GITHUB_REPOSITORY": "not a repository"},
        {"PR_NUMBER": "not-decimal"},
        {"GITHUB_RUN_ID": "not-decimal"},
        {"GITHUB_RUN_ATTEMPT": "0"},
        {"EXPECTED_HEAD_SHA": "not-a-head"},
        {"FULL_DIFF_SHA256": "not-a-hash"},
        {"DIFF_MODE": "changed"},
    ],
)
def test_invalid_transport_refuses_before_any_github_api_call(fake_github, overrides):
    result = fake_github.run_action(
        mode="claim", scenario="first-comment-create", env_overrides=overrides,
    )
    assert result.calls == []
    assert result.outputs["allow-invocation"] == "false"
    assert result.outputs["decision"] == "state_invalid"
    assert result.checkpoint["schema"] == 1
    assert result.checkpoint["ledger"] is None
    assert result.checkpoint["handoff"]["decision"] == "state_invalid"
    assert result.checkpoint["handoff"]["outcome"] == "checkpoint_failure"
    assert result.checkpoint["handoff"]["stop_reason"].endswith("_invalid") or result.checkpoint[
        "handoff"
    ]["stop_reason"] == "repository_identity_mismatch"


@pytest.mark.parametrize(
    "scenario,expected",
    [
        ("first-comment-create", "claimed"),
        ("trusted-comment-update", "claimed"),
        ("duplicate-trusted-comments", "state_invalid"),
        ("foreign-author-marker", "state_invalid"),
        ("historical-run-head-mismatch", "state_invalid"),
        ("pr-head-changed-before-patch", "state_invalid"),
        ("comment-body-changed-before-patch", "state_invalid"),
    ],
)
def test_claim_transport_is_fail_closed(fake_github, scenario, expected):
    result = fake_github.run_action(mode="claim", scenario=scenario)
    assert result.outputs["decision"] == expected
    assert result.provider_started is False
    mutations = [call for call in result.calls if "--method" in call and call[call.index("--method") + 1] in {"POST", "PATCH"}]
    if expected == "claimed":
        assert result.outputs["allow-invocation"] == "true"
        assert len(mutations) == 1
    else:
        assert result.outputs["allow-invocation"] == "false"
        assert mutations == []
    if scenario in {"pr-head-changed-before-patch", "comment-body-changed-before-patch"}:
        assert mutations == []
        assert result.checkpoint["handoff"]["decision"] == "state_invalid"
        assert result.checkpoint["handoff"]["stop_reason"] == "compare_and_swap_failed"


def test_first_claim_authenticates_and_stores_reusable_workflow_provenance(fake_github):
    result = fake_github.run_action(mode="claim", scenario="first-comment-create")
    assert any("/actions/runs/700/attempts/1" in " ".join(call) for call in result.calls)
    invocation = result.checkpoint["ledger"]["invocations"][0]
    assert invocation["caller_workflow_path"] == ".github/workflows/claude-review-caller.yml"
    assert invocation["caller_event"] == "pull_request"
    assert invocation["referenced_workflow_path"] == CENTRAL_PATH
    assert invocation["referenced_workflow_ref"] == CENTRAL_REF
    assert invocation["referenced_workflow_sha"] == CENTRAL_SHA


def test_first_claim_uses_resolved_sha_when_github_omits_reusable_workflow_ref(fake_github):
    result = fake_github.run_action(
        mode="claim", scenario="current-run-reference-omits-ref",
    )

    assert result.outputs["decision"] == "claimed"
    assert result.outputs["allow-invocation"] == "true"
    invocation = result.checkpoint["ledger"]["invocations"][0]
    assert invocation["referenced_workflow_ref"] == CENTRAL_SHA


@pytest.mark.parametrize(
    "scenario",
    [
        "current-run-reference-missing",
        "current-run-reference-duplicate",
        "current-run-reference-wrong-path",
        "current-run-reference-malformed-ref",
        "current-run-reference-wrong-sha",
    ],
)
def test_first_claim_rejects_malformed_reusable_workflow_provenance(fake_github, scenario):
    result = fake_github.run_action(mode="claim", scenario=scenario)
    mutations = [call for call in result.calls if "--method" in call]
    assert result.outputs["decision"] == "state_invalid"
    assert result.outputs["allow-invocation"] == "false"
    assert mutations == []
    assert result.checkpoint["handoff"]["stop_reason"] == "provenance_mismatch"


def test_empty_cas_comment_pages_fail_closed_without_mutation(fake_github):
    result = fake_github.run_action(mode="claim", scenario="empty-cas-pages")
    mutations = [
        call for call in result.calls
        if "--method" in call and call[call.index("--method") + 1] in {"POST", "PATCH"}
    ]
    assert mutations == []
    assert result.outputs["allow-invocation"] == "false"
    assert result.outputs["decision"] == "state_invalid"
    assert result.checkpoint["handoff"]["stop_reason"] == "compare_and_swap_failed"


def test_cli_uses_only_file_based_transport_operations(tmp_path):
    parser = budget.build_parser()
    for operation in ("preflight", "list-run-identities", "claim", "finalize", "cas-failed"):
        parsed = parser.parse_args([
            operation,
            "--request-file", str(tmp_path / "request.json"),
            "--comments-file", str(tmp_path / "comments.json"),
            "--output-directory", str(tmp_path / "out"),
        ])
        assert parsed.operation == operation
    with pytest.raises(SystemExit):
        parser.parse_args(["claim", "--request", "{}"])


def test_direct_main_invalid_request_writes_canonical_refusal(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/repo")
    checkpoint = tmp_path / "checkpoint.json"
    request = {
        "actual_call_count": "0",
        "authenticated_review_json": '{"success":false,"head_sha":null,"full_diff_sha256":null,"remaining_finding_ids":[]}',
        "checkpoint_file": str(checkpoint),
        "diff_mode": "changed",
        "effort": "medium",
        "elapsed_seconds": "0",
        "full_diff_sha256": HASH_1,
        "github_workspace": str(tmp_path),
        "head_sha": HEAD_A,
        "input_files_json": "[]",
        "model_route_json": '["route-v1"]',
        "operation": "claim",
        "outcome": "checkpoint_failure",
        "pr": "52",
        "remaining_finding_ids_json": "[]",
        "repository": "example/repo",
        "reviewer": "claude",
        "run_attempt": "1",
        "run_id": "700",
        "server_url": "https://github.com",
        "stop_reason": "",
    }
    request_file = tmp_path / "request.json"
    comments_file = tmp_path / "comments.json"
    request_file.write_text(json.dumps(request))
    comments_file.write_text("[]")

    assert budget.main([
        "claim", "--request-file", str(request_file), "--comments-file", str(comments_file),
        "--output-directory", str(tmp_path),
    ]) == 0
    payload = checkpoint.read_bytes()
    assert payload.endswith(b"\n")
    assert payload == json.dumps(
        json.loads(payload), ensure_ascii=True, separators=(",", ":"), sort_keys=True,
    ).encode("ascii") + b"\n"
    assert json.loads(payload)["ledger"] is None
    assert json.loads((tmp_path / "output.json").read_text())["decision"] == "state_invalid"


@pytest.mark.parametrize("request_bytes", [b"{", b"[]", b"\xff"])
def test_direct_main_malformed_request_writes_local_refusal_only(
    tmp_path, monkeypatch, request_bytes,
):
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/repo")
    request_file = tmp_path / "request.json"
    comments_file = tmp_path / "comments.json"
    output_directory = tmp_path / "outputs"
    request_file.write_bytes(request_bytes)
    comments_file.write_text("[]")

    assert budget.main([
        "claim", "--request-file", str(request_file), "--comments-file", str(comments_file),
        "--output-directory", str(output_directory),
    ]) == 0
    assert {path.name for path in output_directory.iterdir()} == {
        "checkpoint.json", "output.json", "preflight.json", "summary.md",
    }
    checkpoint = (output_directory / "checkpoint.json").read_bytes()
    assert checkpoint == json.dumps(
        json.loads(checkpoint), ensure_ascii=True, separators=(",", ":"), sort_keys=True,
    ).encode("ascii") + b"\n"
    assert json.loads(checkpoint) == {
        "handoff": {
            "current_full_diff_sha256": None,
            "current_head_sha": None,
            "current_run_attempt": None,
            "current_run_id": None,
            "decision": "state_invalid",
            "outcome": "checkpoint_failure",
            "pr": None,
            "repository": None,
            "reviewer": None,
            "stop_reason": "request_invalid",
        },
        "ledger": None,
        "schema": 1,
    }
    assert json.loads((output_directory / "output.json").read_text())[
        "allow-invocation"
    ] == "false"
    assert json.loads((output_directory / "output.json").read_text())["decision"] == "state_invalid"
    assert json.loads((output_directory / "preflight.json").read_text()) == {"continue": False}
    assert "No provider or GitHub API action was attempted." in (
        output_directory / "summary.md"
    ).read_text()


@pytest.mark.parametrize("contents", ["", " \n\t"])
def test_paginated_reader_rejects_missing_json_array(tmp_path, contents):
    pages = tmp_path / "pages.json"
    pages.write_text(contents)
    with pytest.raises(budget.TransportError, match="comments_invalid"):
        budget._read_pages(pages, "comments")


def test_paginated_reader_accepts_an_empty_json_array(tmp_path):
    pages = tmp_path / "pages.json"
    pages.write_text("[]")
    assert budget._read_pages(pages, "comments") == []


def test_finalize_transport_persists_actual_usage_before_outputs(fake_github):
    result = fake_github.run_action(mode="finalize", scenario="finalize-trusted-comment")
    invocation = result.checkpoint["ledger"]["invocations"][0]
    assert result.outputs["decision"] == "finalized"
    assert result.outputs["allow-invocation"] == "false"
    assert invocation["status"] == "finalized"
    assert invocation["call_count"] == 1
    assert invocation["elapsed_seconds"] == 12
    assert invocation["outcome"] == "success"


def test_override_actor_permission_lookup_is_deduplicated(tmp_path):
    (tmp_path / "timeline.json").write_text(json.dumps([
        {
            "id": 9001,
            "event": "labeled",
            "label": {"name": "review-budget-override"},
            "actor": {"login": "maintainer"},
        },
        {
            "id": 9002,
            "event": "labeled",
            "label": {"name": "review-budget-override"},
            "actor": {"login": "maintainer"},
        },
    ]))
    assert budget._timeline_permission_actors(tmp_path) == [
        {"index": 0, "login": "maintainer", "encoded_login": "maintainer"},
    ]


def test_input_paths_are_bounded_by_the_runner_workspace_environment(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.diff"
    outside.write_text("diff")
    monkeypatch.setenv("GITHUB_WORKSPACE", str(workspace))
    request = {
        "github_workspace": str(tmp_path),
        "input_files_json": json.dumps([str(outside)]),
    }
    with pytest.raises(budget.TransportError, match="workspace_identity_mismatch"):
        budget._validated_input_paths(request)
