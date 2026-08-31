import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / ".github/actions/resolve-review-policy/resolve_review_policy.py"
ACTION = HELPER.with_name("action.yml")
SPEC = importlib.util.spec_from_file_location("resolve_review_policy", HELPER)
assert SPEC is not None and SPEC.loader is not None
review_policy = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = review_policy
SPEC.loader.exec_module(review_policy)

PolicyError = review_policy.PolicyError
PolicyRequest = review_policy.PolicyRequest
resolve_policy = review_policy.resolve_policy


def base_pr() -> dict[str, object]:
    return {
        "state": "open",
        "draft": False,
        "head": {
            "sha": "a" * 40,
            "repo": {"full_name": "jhw7500/example", "fork": False},
        },
        "labels": [],
    }


def request(*, labels=None, mode="auto", config=None, pr=None, event="pull_request", force_review=False):
    payload = base_pr() if pr is None else pr
    payload["labels"] = [{"name": name} for name in (labels or [])]
    return PolicyRequest(
        workflow_name="claude-code-review",
        review_mode=mode,
        force_run=False,
        force_review=force_review,
        event_name=event,
        repository="jhw7500/example",
        pr=payload,
        config={} if config is None else config,
    )


@pytest.mark.parametrize(
    ("labels", "mode", "config", "expected"),
    [
        ([], "auto", {"review": {"auto": True}}, (True, "review_auto_true")),
        ([], "auto", {"review": {"auto": False}}, (False, "review_auto_false")),
        ([], "auto", {"workflows": {"claude-code-review": {"auto": False}}, "review": {"auto": True}}, (False, "workflow_auto_false")),
        (["review:request"], "request", {"review": {"auto": False}}, (True, "request")),
        (["review:skip"], "skip", {"review": {"auto": True}}, (False, "skip")),
    ],
)
def test_policy_precedence(labels, mode, config, expected):
    decision = resolve_policy(request(labels=labels, mode=mode, config=config))
    assert (decision.run_review, decision.reason) == expected


def test_both_labels_fail_closed():
    with pytest.raises(PolicyError, match="review_label_conflict"):
        resolve_policy(request(labels=["review:request", "review:skip"], mode="conflict"))


@pytest.mark.parametrize(("change", "reason"), [
    ({"draft": True}, "draft"),
    ({"state": "closed"}, "closed"),
    ({"head": {"repo": {"full_name": "fork/repo", "fork": True}}}, "unsafe_pr"),
])
def test_noneligible_pr_never_runs(change, reason):
    pr = base_pr() | change
    decision = resolve_policy(request(pr=pr))
    assert decision.run_review is False
    assert decision.reason == reason


def test_pull_request_mode_must_match_labels():
    with pytest.raises(PolicyError, match="review_mode_label_mismatch"):
        resolve_policy(request(labels=["review:skip"], mode="request"))


def test_manual_force_review_allows_request_without_label():
    decision = resolve_policy(request(mode="request", event="workflow_dispatch", force_review=True))
    assert decision.run_review is True
    assert decision.reason == "request"


def test_manual_force_review_rejects_skip_label_mismatch():
    with pytest.raises(PolicyError, match="review_mode_label_mismatch"):
        resolve_policy(
            request(
                labels=["review:skip"],
                mode="request",
                event="workflow_dispatch",
                force_review=True,
            )
        )


def test_request_does_not_override_disabled_workflow():
    decision = resolve_policy(
        request(
            labels=["review:request"],
            mode="request",
            config={"workflows": {"claude-code-review": {"enabled": False}}},
        )
    )
    assert (decision.run_review, decision.reason) == (False, "workflow_disabled")


def test_automatic_mode_defaults_to_true_when_not_configured():
    decision = resolve_policy(request())
    assert (decision.run_review, decision.reason) == (True, "default_auto_true")


def test_action_uses_file_transport_with_expected_interface():
    document = yaml.load(ACTION.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)

    assert set(document["inputs"]) == {
        "workflow-name",
        "pr-number",
        "review-mode",
        "force-run",
        "force-review",
        "github-token",
    }
    assert set(document["outputs"]) == {
        "run-review",
        "effective-mode",
        "reason",
        "head-sha",
    }
    assert document["runs"]["using"] == "composite"

    script = document["runs"]["steps"][0]["run"]
    assert "set -euo pipefail" in script
    assert 'gh api "repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}"' in script
    assert ".github/workflow-config.yml \"$policy_dir/config.json\"" in script
    assert "eval" not in script


def _cli_payload() -> dict[str, object]:
    return {
        "workflow_name": "claude-code-review",
        "review_mode": "auto",
        "force_run": False,
        "force_review": False,
        "event_name": "pull_request",
        "repository": "jhw7500/example",
        "pr": base_pr(),
        "config": {},
    }


def _run_cli(tmp_path: Path, payload: str) -> subprocess.CompletedProcess[str]:
    request_path = tmp_path / "request.json"
    result_path = tmp_path / "result.json"
    output_path = tmp_path / "github-output"
    request_path.write_text(payload, encoding="utf-8")
    output_path.write_text("", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(HELPER),
            "--request-file",
            str(request_path),
            "--result-file",
            str(result_path),
            "--github-output",
            str(output_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert output_path.read_text(encoding="utf-8") == ""
    return result


@pytest.mark.parametrize(
    "payload",
    [
        "{",
        json.dumps({**_cli_payload(), "config": []}),
        json.dumps({**_cli_payload(), "config": {"review": {"auto": "true"}}}),
        json.dumps(
            {
                **_cli_payload(),
                "pr": {**base_pr(), "head": {"sha": "invalid", "repo": {"full_name": "jhw7500/example", "fork": False}}},
            }
        ),
        json.dumps({**_cli_payload(), "repository": "invalid-repository"}),
    ],
)
def test_cli_rejects_malformed_input_without_writing_github_output(tmp_path, payload):
    assert _run_cli(tmp_path, payload).returncode != 0
