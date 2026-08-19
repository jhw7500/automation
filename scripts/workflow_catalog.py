"""Typed, closed policy source for the common workflow fleet."""
from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, Mapping


class CatalogError(ValueError):
    pass


_CANONICAL_DIR = PurePosixPath("examples/baseline-workflows/.github")
_CATALOG_PATH = "scripts/workflow-catalog.json"
# 승인된 플릿 구성 세대: (repository set, bootstrap-allowed set) 쌍.
# 릴리즈 검증기는 역사적 태그의 config도 현재 코드로 검증하므로, 구성 변경은 새 세대를
# 추가하고 이전 세대를 보존한다 — 각 세대 안에서는 정확 일치(닫힌 집합)를 유지한다.
_FLEET_GENERATIONS = (
    # v1.40 ~ v1.43 (19 repos)
    (
        frozenset({
            "gstApp", "max9296", "wlan-driver", "wlan-driver-v2", "wlan-bridge",
            "wlan-package", "pim-package-jhw", "wlan-opc", "pcap-analyzer",
            "wpa-supplicant", "sc16is7xx", "pim-check", "redmine", "jhw-notion",
            "personal-ops", "cts-email-mcp-server", "cts-ta-mcp-server",
            "cts-ta-webapp", "claude-config",
        }),
        frozenset({"wpa-supplicant", "cts-email-mcp-server"}),
    ),
    # v1.44+ (2026-08-19: wlan-driver 레거시 제외, cts-* 3종 미사용 제외, imx-vpu 추가)
    (
        frozenset({
            "gstApp", "max9296", "imx-vpu", "wlan-driver-v2", "wlan-bridge",
            "wlan-package", "pim-package-jhw", "wlan-opc", "pcap-analyzer",
            "wpa-supplicant", "sc16is7xx", "pim-check", "redmine", "jhw-notion",
            "personal-ops", "claude-config",
        }),
        frozenset({"wpa-supplicant"}),
    ),
)


@dataclass(frozen=True)
class CallerJobContract:
    name: str
    permissions: tuple[tuple[str, str], ...]
    with_keys: tuple[str, ...]
    secrets: tuple[str, ...]


@dataclass(frozen=True)
class CatalogEntry:
    path: PurePosixPath
    kind: Literal["required", "optional", "config", "retired"]
    central_workflow: str | None
    auth_family: Literal["claude", "gemini", "opencode", "none"]
    profile_axis: Literal["repo_write_auth"] | None
    trigger: object
    caller_jobs: tuple[CallerJobContract, ...]


@dataclass(frozen=True)
class WorkflowCatalog:
    entries: tuple[CatalogEntry, ...]

    @property
    def callers(self) -> tuple[CatalogEntry, ...]:
        return tuple(entry for entry in self.entries if entry.central_workflow is not None)

    @property
    def managed_paths(self) -> frozenset[PurePosixPath]:
        return frozenset(entry.path for entry in self.entries)

    @property
    def by_name(self) -> Mapping[str, CatalogEntry]:
        return {entry.path.name: entry for entry in self.entries}


@dataclass(frozen=True)
class RepoProfile:
    name: str
    profile: str
    optional_workflows: frozenset[str]
    repo_write_auth: Literal["github_app", "github_token"]
    bootstrap_allowed: bool


@dataclass(frozen=True)
class FleetConfig:
    owner: str
    automation_ref: str
    canonical_dir: PurePosixPath
    profiles: Mapping[str, RepoProfile]


def _require_keys(value: dict[str, object], *, exact: set[str], where: str) -> None:
    actual = set(value)
    if actual != exact:
        missing = sorted(exact - actual)
        unknown = sorted(actual - exact)
        raise CatalogError(f"{where}: missing={missing}, unknown={unknown}")


def _managed_path(raw: str) -> PurePosixPath:
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts or path.parts[:1] != (".github",):
        raise CatalogError(f"managed path escapes .github: {raw}")
    return path


def _load_json(path: Path) -> object:
    def pairs_hook(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise CatalogError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs_hook)
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogError(f"cannot read {path}: {exc}") from exc


def _mapping(value: object, where: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise CatalogError(f"{where} must be a mapping")
    return value


def _string(value: object, where: str) -> str:
    if not isinstance(value, str):
        raise CatalogError(f"{where} must be a string")
    return value


def _jobs(value: object, where: str) -> tuple[CallerJobContract, ...]:
    if not isinstance(value, list):
        raise CatalogError(f"{where} must be a list")
    result: list[CallerJobContract] = []
    for index, raw in enumerate(value):
        job = _mapping(raw, f"{where}[{index}]")
        _require_keys(job, exact={"name", "permissions", "with", "secrets"}, where=f"{where}[{index}]")
        name = _string(job["name"], f"{where}[{index}].name")
        permissions = _mapping(job["permissions"], f"{where}[{index}].permissions")
        keys = job["with"]
        secrets = job["secrets"]
        if not isinstance(keys, list) or not isinstance(secrets, list) or not all(isinstance(key, str) for key in keys + secrets):
            raise CatalogError(f"{where}[{index}] with and secrets must be string lists")
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise CatalogError(f"{where}[{index}].with must be sorted and unique")
        if len(secrets) != len(set(secrets)):
            raise CatalogError(f"{where}[{index}].secrets must be unique")
        if not all(isinstance(key, str) and isinstance(item, str) for key, item in permissions.items()):
            raise CatalogError(f"{where}[{index}].permissions must be string pairs")
        result.append(CallerJobContract(name, tuple(sorted(permissions.items())), tuple(keys), tuple(sorted(secrets))))
    return tuple(result)


def load_catalog(root: Path) -> WorkflowCatalog:
    raw = _mapping(_load_json(root / "scripts/workflow-catalog.json"), "catalog")
    _require_keys(raw, exact={"schema_version", "entries"}, where="catalog")
    if raw["schema_version"] != 1 or not isinstance(raw["entries"], list):
        raise CatalogError("unsupported catalog schema")
    entries: list[CatalogEntry] = []
    paths: set[PurePosixPath] = set()
    for index, value in enumerate(raw["entries"]):
        entry = _mapping(value, f"catalog.entries[{index}]")
        _require_keys(entry, exact={"path", "kind", "central_workflow", "auth_family", "profile_axis", "trigger", "caller_jobs"}, where=f"catalog.entries[{index}]")
        path = _managed_path(_string(entry["path"], f"catalog.entries[{index}].path"))
        if path in paths:
            raise CatalogError(f"duplicate catalog path: {path}")
        paths.add(path)
        kind = entry["kind"]
        auth = entry["auth_family"]
        axis = entry["profile_axis"]
        central = entry["central_workflow"]
        if kind not in {"required", "optional", "config", "retired"}:
            raise CatalogError(f"unknown catalog kind: {kind}")
        if auth not in {"claude", "gemini", "opencode", "none"}:
            raise CatalogError(f"unknown auth family: {auth}")
        if axis not in {None, "repo_write_auth"}:
            raise CatalogError(f"unknown profile axis: {axis}")
        if (auth == "gemini") != (axis == "repo_write_auth"):
            raise CatalogError("Gemini callers must use repo_write_auth and no other caller may use it")
        if central is not None and not isinstance(central, str):
            raise CatalogError("central_workflow must be a string or null")
        if kind in {"required", "optional"} and central is None:
            raise CatalogError(f"catalog caller without a central target: {path}")
        entries.append(CatalogEntry(path, kind, central, auth, axis, entry["trigger"], _jobs(entry["caller_jobs"], f"catalog.entries[{index}].caller_jobs")))
    return WorkflowCatalog(tuple(entries))


def load_fleet_config(root: Path, catalog: WorkflowCatalog) -> FleetConfig:
    raw = _mapping(_load_json(root / "scripts/workflow-config.json"), "fleet config")
    _require_keys(raw, exact={"schema_version", "gh_owner", "automation_ref", "canonical_dir", "catalog", "repos"}, where="fleet config")
    if raw["schema_version"] != 1:
        raise CatalogError("unsupported fleet config schema")
    canonical_dir = PurePosixPath(_string(raw["canonical_dir"], "canonical_dir"))
    if canonical_dir != _CANONICAL_DIR:
        raise CatalogError(f"invalid canonical_dir: {canonical_dir}")
    if _string(raw["catalog"], "catalog") != _CATALOG_PATH:
        raise CatalogError(f"invalid catalog path: {raw['catalog']}")
    repos = _mapping(raw["repos"], "repos")
    generation = next(
        (entry for entry in _FLEET_GENERATIONS if set(repos) == entry[0]), None
    )
    if generation is None:
        raise CatalogError(f"invalid repository set: {sorted(repos)}")
    optional_names = {entry.path.name for entry in catalog.entries if entry.kind == "optional"}
    profiles: dict[str, RepoProfile] = {}
    bootstrap: set[str] = set()
    for name, value in repos.items():
        profile = _mapping(value, f"repos.{name}")
        _require_keys(profile, exact={"profile", "optional_workflows", "repo_write_auth", "bootstrap_allowed"}, where=f"repos.{name}")
        optional = profile["optional_workflows"]
        auth = profile["repo_write_auth"]
        allowed = profile["bootstrap_allowed"]
        if profile["profile"] != "common-ai-v1":
            raise CatalogError(f"repos.{name}: unsupported profile")
        if not isinstance(optional, list) or not all(isinstance(item, str) for item in optional):
            raise CatalogError(f"repos.{name}.optional_workflows must be a string list")
        if len(optional) != len(set(optional)) or not set(optional) <= optional_names:
            raise CatalogError(f"repos.{name}: unknown optional workflow")
        if auth not in {"github_app", "github_token"}:
            raise CatalogError(f"repos.{name}: invalid repo_write_auth")
        if not isinstance(allowed, bool):
            raise CatalogError(f"repos.{name}.bootstrap_allowed must be boolean")
        if allowed:
            bootstrap.add(name)
        profiles[name] = RepoProfile(name, "common-ai-v1", frozenset(optional), auth, allowed)
    if bootstrap != generation[1]:
        raise CatalogError(f"invalid bootstrap repositories: {sorted(bootstrap)}")
    return FleetConfig(_string(raw["gh_owner"], "gh_owner"), _string(raw["automation_ref"], "automation_ref"), canonical_dir, profiles)


def extract_caller_jobs(workflow: Mapping[str, object]) -> tuple[CallerJobContract, ...]:
    contracts: list[CallerJobContract] = []
    jobs = workflow.get("jobs", {})
    if not isinstance(jobs, dict):
        raise CatalogError("workflow jobs must be a mapping")
    for name, raw_job in jobs.items():
        if not isinstance(raw_job, dict):
            continue
        uses = raw_job.get("uses")
        if not isinstance(uses, str) or "jhw7500/automation/.github/workflows/" not in uses:
            continue
        permissions = raw_job.get("permissions", {})
        with_values = raw_job.get("with", {})
        secrets = raw_job.get("secrets", {})
        if not all(isinstance(value, dict) for value in (permissions, with_values, secrets)):
            raise CatalogError(f"caller job {name} has a non-mapping contract")
        contracts.append(CallerJobContract(name=name, permissions=tuple(sorted(permissions.items())), with_keys=tuple(sorted(with_values)), secrets=tuple(sorted(secrets))))
    return tuple(contracts)


def expected_caller_jobs(entry: CatalogEntry, profile: RepoProfile) -> tuple[CallerJobContract, ...]:
    if entry.profile_axis != "repo_write_auth" or profile.repo_write_auth == "github_app":
        return entry.caller_jobs
    return tuple(dataclasses.replace(job, with_keys=tuple(key for key in job.with_keys if key != "app_id"), secrets=tuple(key for key in job.secrets if key != "APP_PRIVATE_KEY")) for job in entry.caller_jobs)
