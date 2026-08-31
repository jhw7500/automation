from __future__ import annotations

import json
from pathlib import Path, PurePosixPath

import pytest

from scripts.workflow_catalog import CatalogError, load_catalog, load_fleet_config

ROOT = Path(__file__).resolve().parents[1]

EXPECTED = {
    "gstApp": ({"auto-rereview-request.yml", "gemini-chat.yml", "opencode.yml", "opencode-auto-review.yml"}, "github_app", False),
    "max9296": ({"auto-rereview-request.yml", "gemini-chat.yml", "opencode.yml", "opencode-auto-review.yml"}, "github_app", False),
    "imx-vpu": ({"auto-rereview-request.yml", "gemini-chat.yml", "opencode.yml", "opencode-auto-review.yml"}, "github_token", False),
    "wlan-driver-v2": ({"auto-rereview-request.yml", "opencode.yml", "opencode-auto-review.yml"}, "github_app", False),
    "wlan-bridge": ({"gemini-chat.yml", "opencode.yml", "opencode-auto-review.yml"}, "github_app", False),
    "wlan-package": ({"opencode.yml", "opencode-auto-review.yml"}, "github_app", False),
    "pim-package-jhw": ({"opencode-auto-review.yml"}, "github_app", False),
    "wlan-opc": ({"opencode.yml"}, "github_token", False),
    "pcap-analyzer": (set(), "github_app", False),
    "wpa-supplicant": (set(), "github_token", True),
    "sc16is7xx": (set(), "github_app", False),
    "pim-check": (set(), "github_token", False),
    "redmine": (set(), "github_app", False),
    "jhw-notion": (set(), "github_app", False),
    "personal-ops": (set(), "github_app", False),
    "claude-config": (set(), "github_token", False),
}


def test_catalog_and_profiles_are_closed() -> None:
    catalog = load_catalog(ROOT)
    config = load_fleet_config(ROOT, catalog)
    assert config.owner == "jhw7500"
    assert config.automation_ref == "v1.51"
    assert config.canonical_dir == PurePosixPath("examples/baseline-workflows/.github")
    assert set(config.profiles) == set(EXPECTED)
    for name, (optional, auth, bootstrap) in EXPECTED.items():
        profile = config.profiles[name]
        assert profile.profile == "common-ai-v1"
        assert profile.optional_workflows == frozenset(optional)
        assert profile.repo_write_auth == auth
        assert profile.bootstrap_allowed is bootstrap

    by_kind = {kind: {entry.path.name for entry in catalog.entries if entry.kind == kind}
               for kind in ("required", "optional", "config", "retired")}
    assert by_kind["required"] == {
        "claude.yml", "claude-code-review.yml", "gemini-auto-review.yml",
        "gemini-dispatch.yml", "gemini-invoke.yml", "gemini-issue-triage.yml",
        "gemini-pr-review.yml", "gemini-review.yml",
        "gemini-scheduled-triage.yml", "gemini-triage.yml",
    }
    assert by_kind["optional"] == {
        "auto-rereview-request.yml", "gemini-chat.yml",
        "opencode.yml", "opencode-auto-review.yml",
    }
    assert by_kind["config"] == {"workflow-config.yml"}
    assert by_kind["retired"] == {"bump-automation-ref.yml"}

    callers = {
        entry.path.name: entry
        for entry in catalog.callers
        if entry.path.name in {
            "claude-code-review.yml",
            "gemini-auto-review.yml",
            "opencode-auto-review.yml",
        }
    }
    assert set(callers) == {
        "claude-code-review.yml",
        "gemini-auto-review.yml",
        "opencode-auto-review.yml",
    }
    for entry in callers.values():
        assert entry.trigger["pull_request"]["types"] == [
            "opened", "synchronize", "ready_for_review",
        ]
        assert "review_mode" in entry.caller_jobs[0].with_keys


def _write(root: Path, catalog: list[dict], repos: dict[str, dict]) -> None:
    (root / "scripts").mkdir()
    (root / "scripts/workflow-catalog.json").write_text(json.dumps({"schema_version": 1, "entries": catalog}))
    (root / "scripts/workflow-config.json").write_text(json.dumps({
        "schema_version": 1, "gh_owner": "jhw7500", "automation_ref": "v1.40",
        "canonical_dir": "examples/baseline-workflows/.github", "catalog": "scripts/workflow-catalog.json", "repos": repos,
    }))


def _entry(**changes: object) -> dict:
    value = {"path": ".github/workflows/gemini.yml", "kind": "required", "central_workflow": "gemini.yml", "auth_family": "gemini", "profile_axis": "repo_write_auth", "trigger": {"workflow_dispatch": {}}, "caller_jobs": []}
    value.update(changes)
    return value


@pytest.mark.parametrize("entries", [
    [_entry(), _entry()],
    [_entry(kind="bogus")],
    [_entry(path="outside.yml")],
    [_entry(central_workflow=None)],
])
def test_catalog_rejects_invalid_entries(tmp_path: Path, entries: list[dict]) -> None:
    _write(tmp_path, entries, {})
    with pytest.raises(CatalogError):
        load_catalog(tmp_path)


@pytest.mark.parametrize(("change", "message"), [
    (
        lambda config: config["repos"]["wlan-package"].__setitem__("optional_workflows", ["missing.yml"]),
        "repos.wlan-package: unknown optional workflow",
    ),
    (
        lambda config: config["repos"]["wlan-package"].__setitem__("repo_write_auth", "invalid"),
        "repos.wlan-package: invalid repo_write_auth",
    ),
    (
        lambda config: config["repos"]["wlan-package"].__setitem__("bootstrap_allowed", True),
        "invalid bootstrap repositories: [\'wlan-package\', \'wpa-supplicant\']",
    ),
])
def test_fleet_rejects_invalid_profiles(tmp_path: Path, change: object, message: str) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for filename in ("workflow-catalog.json", "workflow-config.json"):
        (scripts / filename).write_text((ROOT / "scripts" / filename).read_text())
    config_path = scripts / "workflow-config.json"
    config = json.loads(config_path.read_text())
    change(config)
    config_path.write_text(json.dumps(config))
    with pytest.raises(CatalogError) as error:
        load_fleet_config(tmp_path, load_catalog(tmp_path))
    assert str(error.value) == message

@pytest.mark.parametrize("change", [
    lambda config: config.__setitem__("canonical_dir", "other/.github"),
    lambda config: config.__setitem__("catalog", "scripts/not-the-catalog.json"),
    lambda config: config["repos"].__setitem__(
        "unexpected-repository",
        {"profile": "common-ai-v1", "optional_workflows": [], "repo_write_auth": "github_app", "bootstrap_allowed": False},
    ),
])
def test_fleet_rejects_noncanonical_policy_shape(tmp_path: Path, change: object) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for filename in ("workflow-catalog.json", "workflow-config.json"):
        (scripts / filename).write_text((ROOT / "scripts" / filename).read_text())
    config_path = scripts / "workflow-config.json"
    config = json.loads(config_path.read_text())
    change(config)
    config_path.write_text(json.dumps(config))
    with pytest.raises(CatalogError):
        load_fleet_config(tmp_path, load_catalog(tmp_path))
