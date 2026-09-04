"""The operator script that creates the fleet's review labels (issue #115)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts import ensure_review_labels as ensure
from scripts import workflow_fleet_git


ROOT = Path(__file__).resolve().parents[1]
STANDARD = [
    {"name": "review:request", "color": "0E8A16", "description": "Explicitly request AI review"},
    {"name": "review:skip", "color": "BFDADC", "description": "Explicitly skip AI review"},
    {"name": "review-budget-override", "color": "D93F0B",
     "description": "Authorize one bounded reviewer override round"},
]


def completed(args: list[str], stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")


@pytest.fixture
def fake_github(monkeypatch: pytest.MonkeyPatch):
    """Serve `gh label list` per repository and record every mutation."""

    state: dict[str, object] = {"labels": {}, "mutations": []}

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert args[:2] == ["gh", "label"], args
        repo = args[args.index("-R") + 1]
        if args[2] == "list":
            assert args[3:] == ["-R", repo, "--json", "name,color,description", "--limit", "300"]
            return completed(args, json.dumps(state["labels"].get(repo, [])))
        state["mutations"].append(args)
        return completed(args)

    monkeypatch.setattr(workflow_fleet_git.subprocess, "run", fake_run)
    return state


def test_current_repository_needs_no_mutation(fake_github, capsys) -> None:
    fake_github["labels"]["jhw7500/gstApp"] = list(STANDARD)

    assert ensure.main(["--automation", str(ROOT), "--repo", "gstApp"]) == 0

    assert fake_github["mutations"] == []
    assert "CURRENT gstApp: 3 review labels current" in capsys.readouterr().out


def test_missing_labels_are_reported_and_fail_without_confirmation(fake_github, capsys) -> None:
    fake_github["labels"]["jhw7500/gstApp"] = [STANDARD[0]]

    assert ensure.main(["--automation", str(ROOT), "--repo", "gstApp"]) == 1

    out = capsys.readouterr().out
    assert "MISSING gstApp: review-budget-override, review:skip" in out
    assert "SUMMARY repos=1 missing=2 drift=0 created=0 normalized=0" in out
    assert fake_github["mutations"] == []


def test_confirmed_run_creates_only_the_missing_labels(fake_github, capsys) -> None:
    fake_github["labels"]["jhw7500/gstApp"] = [STANDARD[0]]

    assert ensure.main(["--automation", str(ROOT), "--repo", "gstApp", "--confirm"]) == 0

    assert fake_github["mutations"] == [
        ["gh", "label", "create", "review-budget-override", "-R", "jhw7500/gstApp",
         "--color", "D93F0B", "--description", "Authorize one bounded reviewer override round"],
        ["gh", "label", "create", "review:skip", "-R", "jhw7500/gstApp",
         "--color", "BFDADC", "--description", "Explicitly skip AI review"],
    ]
    assert "CREATED gstApp: review-budget-override, review:skip" in capsys.readouterr().out


def test_drift_is_reported_but_only_normalized_on_request(fake_github, capsys) -> None:
    drifted = dict(STANDARD[1], color="B60205")
    fake_github["labels"]["jhw7500/gstApp"] = [STANDARD[0], drifted, STANDARD[2]]

    assert ensure.main(["--automation", str(ROOT), "--repo", "gstApp", "--confirm"]) == 0
    assert fake_github["mutations"] == []
    assert "DRIFT gstApp: review:skip color=B60205 expected=BFDADC" in capsys.readouterr().out

    assert ensure.main(["--automation", str(ROOT), "--repo", "gstApp", "--confirm", "--normalize"]) == 0
    assert fake_github["mutations"] == [
        ["gh", "label", "edit", "review:skip", "-R", "jhw7500/gstApp",
         "--color", "BFDADC", "--description", "Explicitly skip AI review"],
    ]
    assert "NORMALIZED gstApp: review:skip" in capsys.readouterr().out


def test_every_configured_repository_is_checked_once_by_default(fake_github, capsys) -> None:
    fleet = ensure.configured_repositories(ROOT)
    for repo in fleet:
        fake_github["labels"][f"jhw7500/{repo}"] = list(STANDARD)

    assert ensure.main(["--automation", str(ROOT)]) == 0

    out = capsys.readouterr().out
    assert len(fleet) == 16
    assert out.count("CURRENT ") == 16
    assert "SUMMARY repos=16 missing=0 drift=0 created=0 normalized=0" in out


def test_unknown_repository_and_malformed_inventory_fail_closed(fake_github, monkeypatch, capsys) -> None:
    with pytest.raises(SystemExit):
        ensure.main(["--automation", str(ROOT), "--repo", "not-in-fleet"])

    def broken_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return completed(args, "not json")

    monkeypatch.setattr(workflow_fleet_git.subprocess, "run", broken_run)
    assert ensure.main(["--automation", str(ROOT), "--repo", "gstApp"]) == 1
    assert "ERROR gstApp:" in capsys.readouterr().out
