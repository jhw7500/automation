#!/usr/bin/env python3
"""Render the closed common-workflow policy into a repository.

Rendering is side-effect free.  Only :func:`apply_render_plan` writes, and it first
proves that every path still has the exact bytes observed by the renderer.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from typing import Literal

import yaml

from scripts.workflow_catalog import (
    CatalogEntry,
    CatalogError,
    RepoProfile,
    WorkflowCatalog,
    expected_caller_jobs,
    extract_caller_jobs,
    load_catalog,
)


# Kept as the fleet-wide approved checkout pin consumed by test_action_pins.py.
CHECKOUT_SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"
SHA40 = re.compile(r"[0-9a-f]{40}")
CENTRAL_USE = re.compile(
    r"jhw7500/automation/\.github/workflows/(?P<name>[^@\s'\"]+)@(?P<ref>[^\s'\"]+)"
)
_IDENTITY_KEYS = ("automation_ref", "automation_commit")


class RolloutError(RuntimeError):
    """The rollout cannot be rendered or safely applied."""


class SecretPrerequisiteError(RolloutError):
    """Compatibility error for the rollout orchestrator pending its renderer move."""

    def __init__(self, message: str, missing_secrets: set[str]) -> None:
        super().__init__(message)
        self.missing_secrets = frozenset(missing_secrets)


@dataclass(frozen=True)
class RolloutResult:
    """Compatibility result for callers that have not moved to RenderPlan yet."""

    callers: int
    changed_files: tuple[Path, ...]
    required_secrets: frozenset[str]


@dataclass(frozen=True)
class FileChange:
    path: PurePosixPath
    before: bytes | None
    after: bytes | None


@dataclass(frozen=True)
class RenderPlan:
    status: Literal["current", "drift", "bootstrap_required", "blocked"]
    reason: str
    changes: tuple[FileChange, ...]
    required_secrets: frozenset[str]
    required_variables: frozenset[str]

    def after(self, path: str) -> bytes | None:
        requested = PurePosixPath(path)
        for change in self.changes:
            if change.path == requested:
                return change.after
        raise KeyError(path)


def selected_entries(
    catalog: WorkflowCatalog, profile: RepoProfile
) -> tuple[CatalogEntry, ...]:
    """Return the catalog entries owned by a normal profile render."""

    return tuple(
        entry
        for entry in catalog.entries
        if entry.kind in {"required", "config"}
        or (
            entry.kind == "optional"
            and entry.path.name in profile.optional_workflows
        )
    )


def replace_once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise RolloutError(f"expected exactly one {old!r}")
    return text.replace(old, new, 1)


def delete_line_once(text: str, line: str) -> str:
    pattern = re.compile(
        rf"(?m)^[ \t]*{re.escape(line)}[ \t]*(?:\r?\n|$)"
    )
    matches = tuple(pattern.finditer(text))
    if len(matches) != 1:
        raise RolloutError(f"expected exactly one line {line!r}")
    match = matches[0]
    return text[: match.start()] + text[match.end() :]


def render_caller(
    template: bytes,
    entry: CatalogEntry,
    profile: RepoProfile,
    release_commit: str,
) -> bytes:
    if SHA40.fullmatch(release_commit) is None:
        raise RolloutError("release commit must be 40 lowercase hex characters")
    try:
        text = template.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RolloutError(f"{entry.path}: canonical caller is not UTF-8") from exc
    if text.count("@__AUTOMATION_COMMIT__") != 1:
        raise RolloutError(f"{entry.path}: expected one commit placeholder")
    text = text.replace("@__AUTOMATION_COMMIT__", f"@{release_commit}")
    if entry.auth_family == "gemini" and profile.repo_write_auth == "github_token":
        text = replace_once(
            text,
            "repo_write_auth: github_app",
            "repo_write_auth: github_token",
        )
        text = delete_line_once(text, "app_id: ${{ vars.APP_ID }}")
        text = delete_line_once(
            text, "APP_PRIVATE_KEY: ${{ secrets.APP_PRIVATE_KEY }}"
        )
    return text.encode("utf-8")


def _required_names(
    selected: tuple[CatalogEntry, ...], profile: RepoProfile
) -> tuple[frozenset[str], frozenset[str]]:
    required_secrets = {"CLAUDE_CODE_OAUTH_TOKEN", "GEMINI_API_KEY"}
    if any(entry.auth_family == "opencode" for entry in selected):
        required_secrets.add("ZHIPU_API_KEY")
    required_variables: set[str] = set()
    if profile.repo_write_auth == "github_app":
        required_secrets.add("APP_PRIVATE_KEY")
        required_variables.add("APP_ID")
    return frozenset(required_secrets), frozenset(required_variables)


def _blocked(
    reason: str,
    required_secrets: frozenset[str],
    required_variables: frozenset[str],
) -> RenderPlan:
    return RenderPlan(
        "blocked", reason, (), required_secrets, required_variables
    )


def _safe_relative(path: PurePosixPath) -> bool:
    return (
        not path.is_absolute()
        and ".." not in path.parts
        and path.parts[:1] == (".github",)
    )


def _symlink_component(repo: Path, relative: PurePosixPath) -> Path | None:
    current = repo
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return current
    return None


def _read_before(repo: Path, relative: PurePosixPath) -> bytes | None:
    path = repo / relative
    if not path.exists():
        return None
    if not path.is_file():
        raise RolloutError(f"{relative}: managed path is not a regular file")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise RolloutError(f"{relative}: cannot read managed file: {exc}") from exc


def _canonical_bytes(canonical: Path, entry: CatalogEntry) -> bytes:
    try:
        relative = entry.path.relative_to(".github")
    except ValueError as exc:
        raise RolloutError(f"{entry.path}: managed path is outside .github") from exc
    components = (canonical,) + tuple(
        canonical.joinpath(*relative.parts[:index])
        for index in range(1, len(relative.parts) + 1)
    )
    for index, component in enumerate(components):
        try:
            metadata = component.lstat()
        except OSError as exc:
            raise RolloutError(
                f"{entry.path}: canonical path is missing or unsafe"
            ) from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise RolloutError(
                f"{entry.path}: canonical path contains a symlink"
            )
        if index < len(components) - 1 and not stat.S_ISDIR(metadata.st_mode):
            raise RolloutError(
                f"{entry.path}: canonical path component is not a directory"
            )
        if index == len(components) - 1 and not stat.S_ISREG(metadata.st_mode):
            raise RolloutError(
                f"{entry.path}: canonical path is not a regular file"
            )
    path = components[-1]
    try:
        return path.read_bytes()
    except OSError as exc:
        raise RolloutError(f"{entry.path}: cannot read canonical file: {exc}") from exc


def _validate_caller(
    rendered: bytes, entry: CatalogEntry, profile: RepoProfile
) -> None:
    try:
        value = yaml.load(rendered.decode("utf-8"), Loader=yaml.BaseLoader)
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise RolloutError(f"{entry.path}: rendered caller is invalid YAML") from exc
    if not isinstance(value, dict):
        raise RolloutError(f"{entry.path}: rendered caller must be a mapping")
    if value.get("on") != entry.trigger:
        raise RolloutError(f"{entry.path}: rendered trigger violates the catalog")
    try:
        actual_jobs = extract_caller_jobs(value)
    except CatalogError as exc:
        raise RolloutError(f"{entry.path}: invalid rendered caller jobs: {exc}") from exc
    if actual_jobs != expected_caller_jobs(entry, profile):
        raise RolloutError(f"{entry.path}: rendered caller jobs violate the catalog")
    uses = tuple(CENTRAL_USE.finditer(rendered.decode("utf-8")))
    if (
        len(uses) != 1
        or uses[0].group("name") != entry.central_workflow
    ):
        raise RolloutError(f"{entry.path}: rendered central target violates the catalog")


def _scan_central_callers(
    repo: Path, catalog: WorkflowCatalog
) -> tuple[tuple[PurePosixPath, str], ...]:
    workflow_root = repo / ".github/workflows"
    if not workflow_root.exists() or not workflow_root.is_dir():
        return ()
    callers: list[tuple[PurePosixPath, str]] = []
    try:
        candidates = sorted(
            path
            for path in workflow_root.rglob("*")
            if path.is_file() and path.suffix in {".yml", ".yaml"}
        )
    except OSError as exc:
        raise RolloutError(f"cannot scan repository workflows: {exc}") from exc
    for path in candidates:
        try:
            text = path.read_bytes().decode("utf-8", errors="ignore")
        except OSError as exc:
            raise RolloutError(f"cannot scan {path.relative_to(repo)}: {exc}") from exc
        relative = PurePosixPath(path.relative_to(repo).as_posix())
        callers.extend((relative, match.group("name")) for match in CENTRAL_USE.finditer(text))
    return tuple(callers)


def _identity_nodes(
    text: str,
) -> dict[str, tuple[yaml.nodes.ScalarNode, yaml.nodes.ScalarNode]]:
    try:
        document = yaml.compose(text, Loader=yaml.BaseLoader)
    except yaml.YAMLError as exc:
        raise RolloutError("rendered workflow config identity is malformed") from exc
    if not isinstance(document, yaml.nodes.MappingNode):
        raise RolloutError("rendered workflow config identity is malformed")
    found: dict[str, list[tuple[yaml.nodes.ScalarNode, yaml.nodes.ScalarNode]]] = {
        key: [] for key in _IDENTITY_KEYS
    }
    for key_node, value_node in document.value:
        if not isinstance(key_node, yaml.nodes.ScalarNode):
            raise RolloutError("workflow config identity has a non-scalar key")
        if key_node.value == "<<" and key_node.style is None:
            raise RolloutError("workflow config identity does not allow YAML merges")
        if key_node.value not in found:
            continue
        if not isinstance(value_node, yaml.nodes.ScalarNode):
            raise RolloutError(
                f"workflow config identity {key_node.value} must be a scalar"
            )
        found[key_node.value].append((key_node, value_node))
    if len(found["automation_ref"]) != 1:
        raise RolloutError(
            "workflow config identity requires exactly one top-level automation_ref"
        )
    if len(found["automation_commit"]) > 1:
        raise RolloutError(
            "workflow config identity allows at most one top-level automation_commit"
        )
    return {key: values[0] for key, values in found.items() if values}


def _scalar_replacement(text: str, node: yaml.nodes.ScalarNode, value: str) -> str:
    original = text[node.start_mark.index : node.end_mark.index]
    if node.style is None and original == node.value:
        return value
    if node.style == "'" and original.startswith("'") and original.endswith("'"):
        return "'" + value.replace("'", "''") + "'"
    if node.style == '"' and original.startswith('"') and original.endswith('"'):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    raise RolloutError("workflow config identity scalar uses unsupported YAML syntax")


def _identity_line_span(
    text: str,
    key: yaml.nodes.ScalarNode,
    value: yaml.nodes.ScalarNode,
) -> tuple[int, int]:
    start = text.rfind("\n", 0, key.start_mark.index) + 1
    newline = text.find("\n", value.end_mark.index)
    end = len(text) if newline < 0 else newline + 1
    if (
        key.start_mark.index != start
        or key.start_mark.line != value.start_mark.line
        or value.start_mark.line != value.end_mark.line
        or value.start_mark.index <= key.end_mark.index
    ):
        raise RolloutError("workflow config identity must use one-line scalar entries")
    key_source = text[key.start_mark.index : key.end_mark.index]
    if key.style is None:
        valid_key = key_source == key.value
    elif key.style == "'":
        valid_key = key_source.startswith("'") and key_source.endswith("'")
    elif key.style == '"':
        valid_key = key_source.startswith('"') and key_source.endswith('"')
    else:
        valid_key = False
    if not valid_key:
        raise RolloutError("workflow config identity key uses unsupported YAML syntax")
    return start, end


def _replace_node_in_line(
    text: str,
    span: tuple[int, int],
    node: yaml.nodes.ScalarNode,
    replacement: str,
) -> str:
    start, end = span
    return (
        text[start : node.start_mark.index]
        + replacement
        + text[node.end_mark.index : end]
    )


def _render_existing_config(
    original: bytes, release_ref: str, release_commit: str
) -> bytes:
    try:
        text = original.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RolloutError("rendered workflow config identity is malformed") from exc
    identity = _identity_nodes(text)
    ref_key, ref_value = identity["automation_ref"]
    ref_span = _identity_line_span(text, ref_key, ref_value)
    ref_line = _replace_node_in_line(
        text,
        ref_span,
        ref_value,
        _scalar_replacement(text, ref_value, release_ref),
    )
    newline = "\r\n" if ref_line.endswith("\r\n") else "\n"
    if not ref_line.endswith(("\n", "\r")):
        ref_line += newline

    commit_entry = identity.get("automation_commit")
    spans = [ref_span]
    if commit_entry is None:
        commit_line = f"automation_commit: {release_commit}{newline}"
    else:
        commit_key, commit_value = commit_entry
        commit_span = _identity_line_span(text, commit_key, commit_value)
        spans.append(commit_span)
        commit_line = _replace_node_in_line(
            text,
            commit_span,
            commit_value,
            _scalar_replacement(text, commit_value, release_commit),
        )
        if not commit_line.endswith(("\n", "\r")):
            commit_line += newline

    insertion = ref_span[0] - sum(
        end - start for start, end in spans if start < ref_span[0]
    )
    retained = text
    for start, end in sorted(spans, reverse=True):
        retained = retained[:start] + retained[end:]
    rendered_text = retained[:insertion] + ref_line + commit_line + retained[insertion:]
    proposed = _identity_nodes(rendered_text)
    if (
        proposed["automation_ref"][1].value != release_ref
        or proposed.get("automation_commit") is None
        or proposed["automation_commit"][1].value != release_commit
    ):
        raise RolloutError("rendered workflow config has invalid identity values")
    return rendered_text.encode("utf-8")


def _render_bootstrap_config(
    template: bytes, release_ref: str, release_commit: str
) -> bytes:
    try:
        text = template.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RolloutError("canonical workflow config is not UTF-8") from exc
    text = replace_once(text, "__AUTOMATION_REF__", release_ref)
    text = replace_once(text, "__AUTOMATION_COMMIT__", release_commit)
    try:
        document = yaml.load(text, Loader=yaml.BaseLoader)
    except yaml.YAMLError as exc:
        raise RolloutError("rendered bootstrap config is invalid YAML") from exc
    if not isinstance(document, dict):
        raise RolloutError("rendered bootstrap config must be a mapping")
    if document.get("automation_ref") != release_ref:
        raise RolloutError("rendered bootstrap config has the wrong automation_ref")
    if document.get("automation_commit") != release_commit:
        raise RolloutError("rendered bootstrap config has the wrong automation_commit")
    return text.encode("utf-8")


def _prerequisite_reason(
    required_secrets: frozenset[str],
    required_variables: frozenset[str],
    secret_names: set[str],
    variable_names: set[str],
) -> str:
    parts: list[str] = []
    missing_secrets = sorted(required_secrets - secret_names)
    missing_variables = sorted(required_variables - variable_names)
    if missing_secrets:
        parts.append(f"missing secrets: {', '.join(missing_secrets)}")
    if missing_variables:
        parts.append(f"missing variables: {', '.join(missing_variables)}")
    return "; ".join(parts)


def render_repository(
    repo: Path,
    canonical: Path,
    catalog: WorkflowCatalog,
    profile: RepoProfile,
    release_ref: str,
    release_commit: str,
    secret_names: set[str],
    variable_names: set[str],
    *,
    bootstrap: bool = False,
) -> RenderPlan:
    """Return the complete deterministic change plan for one repository."""

    if SHA40.fullmatch(release_commit) is None:
        raise RolloutError("release commit must be 40 lowercase hex characters")
    if not release_ref or any(character.isspace() for character in release_ref):
        raise RolloutError("release ref must be one non-whitespace value")

    normal_selected = selected_entries(catalog, profile)
    selected = (
        tuple(
            entry
            for entry in catalog.entries
            if entry.kind in {"required", "config"}
        )
        if bootstrap
        else normal_selected
    )
    required_secrets, required_variables = _required_names(selected, profile)

    for managed in catalog.managed_paths:
        if not _safe_relative(managed):
            raise RolloutError(f"{managed}: catalog path is unsafe")
        symlink = _symlink_component(repo, managed)
        if symlink is not None:
            relative = symlink.relative_to(repo)
            return _blocked(
                f"managed path contains symlink: {relative}",
                required_secrets,
                required_variables,
            )

    try:
        central_callers = _scan_central_callers(repo, catalog)
    except RolloutError as exc:
        return _blocked(str(exc), required_secrets, required_variables)
    catalog_caller_paths = frozenset(entry.path for entry in catalog.callers)
    unknown = tuple(
        path for path, _ in central_callers if path not in catalog_caller_paths
    )
    if unknown:
        return _blocked(
            f"unknown central caller path: {unknown[0]}",
            required_secrets,
            required_variables,
        )

    if bootstrap:
        if not profile.bootstrap_allowed:
            return _blocked(
                f"disabled bootstrap is not allowed for profile {profile.name}",
                required_secrets,
                required_variables,
            )
        if central_callers:
            return _blocked(
                f"bootstrap refuses existing central caller: {central_callers[0][0]}",
                required_secrets,
                required_variables,
            )
    else:
        config_path = PurePosixPath(".github/workflow-config.yml")
        config_file = repo / config_path
        if not config_file.is_file():
            return _blocked(
                "workflow config is missing; explicit bootstrap is required",
                required_secrets,
                required_variables,
            )
        missing = _prerequisite_reason(
            required_secrets,
            required_variables,
            secret_names,
            variable_names,
        )
        if missing:
            return _blocked(
                missing, required_secrets, required_variables
            )

    selected_paths = frozenset(entry.path for entry in selected)
    changes: list[FileChange] = []
    try:
        for entry in catalog.entries:
            before = _read_before(repo, entry.path)
            after: bytes | None
            if entry.kind in {"required", "optional"} and entry.path in selected_paths:
                template = _canonical_bytes(canonical, entry)
                after = render_caller(template, entry, profile, release_commit)
                _validate_caller(after, entry, profile)
            elif entry.kind == "config" and entry.path in selected_paths:
                template = _canonical_bytes(canonical, entry)
                after = (
                    _render_bootstrap_config(template, release_ref, release_commit)
                    if bootstrap
                    else _render_existing_config(
                        before if before is not None else b"",
                        release_ref,
                        release_commit,
                    )
                )
            elif bootstrap:
                continue
            elif entry.kind in {"optional", "retired"}:
                after = None
            else:
                continue
            if before != after:
                changes.append(FileChange(entry.path, before, after))
    except RolloutError as exc:
        return _blocked(str(exc), required_secrets, required_variables)

    if any(change.path not in catalog.managed_paths for change in changes):
        raise RolloutError("renderer proposed a path outside the workflow catalog")
    change_tuple = tuple(changes)
    if not change_tuple:
        return RenderPlan(
            "current",
            "managed files are current",
            (),
            required_secrets,
            required_variables,
        )
    if bootstrap:
        prerequisites = _prerequisite_reason(
            required_secrets,
            required_variables,
            secret_names,
            variable_names,
        )
        reason = "disabled bootstrap required"
        if prerequisites:
            reason += f"; non-blocking prerequisites: {prerequisites}"
        status: Literal["drift", "bootstrap_required"] = "bootstrap_required"
    else:
        reason = f"{len(change_tuple)} managed file(s) differ"
        status = "drift"
    return RenderPlan(
        status,
        reason,
        change_tuple,
        required_secrets,
        required_variables,
    )


def apply_render_plan(
    repo: Path, plan: RenderPlan
) -> tuple[PurePosixPath, ...]:
    """Apply a renderer-owned plan after validating every observed byte."""

    if plan.status not in {"drift", "bootstrap_required"}:
        raise RolloutError(f"render plan is not actionable: {plan.status}")
    if not plan.changes:
        raise RolloutError("render plan is not actionable: no changes")

    try:
        application_catalog = load_catalog(Path(__file__).resolve().parents[1])
    except CatalogError as exc:
        raise RolloutError(f"cannot load the workflow catalog: {exc}") from exc
    for managed in application_catalog.managed_paths:
        symlink = _symlink_component(repo, managed)
        if symlink is not None:
            raise RolloutError(
                f"managed path contains symlink: {symlink.relative_to(repo)}"
            )

    seen: set[PurePosixPath] = set()
    current: dict[PurePosixPath, bytes | None] = {}
    modes: dict[PurePosixPath, int] = {}
    entries_by_path = {
        entry.path: entry for entry in application_catalog.entries
    }
    for change in plan.changes:
        if change.path in seen:
            raise RolloutError(f"duplicate render path: {change.path}")
        seen.add(change.path)
        if not _safe_relative(change.path):
            raise RolloutError(f"unsafe render path: {change.path}")
        if change.path not in application_catalog.managed_paths:
            raise RolloutError(
                f"render path is outside the workflow catalog: {change.path}"
            )
        if (
            change.after is None
            and entries_by_path[change.path].kind not in {"optional", "retired"}
        ):
            raise RolloutError(
                f"render path is not catalogued for deletion: {change.path}"
            )
        symlink = _symlink_component(repo, change.path)
        if symlink is not None:
            raise RolloutError(
                f"managed path contains symlink: {symlink.relative_to(repo)}"
            )
        path = repo / change.path
        if path.exists():
            if not path.is_file():
                raise RolloutError(f"{change.path}: managed path is not a regular file")
            observed = path.read_bytes()
            modes[change.path] = stat.S_IMODE(path.stat().st_mode)
        else:
            observed = None
        current[change.path] = observed
        if observed != change.before:
            raise RolloutError(f"{change.path}: changed since rendering")

    changed: list[PurePosixPath] = []
    for change in sorted(plan.changes, key=lambda item: item.path):
        if current[change.path] == change.after:
            continue
        path = repo / change.path
        if change.after is None:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(change.after)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temporary, modes.get(change.path, 0o644))
                os.replace(temporary, path)
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        changed.append(change.path)
    return tuple(changed)


def prepare_repository(*args: object, **kwargs: object) -> RolloutResult:
    """Reject the retired in-place editor while older orchestrator code is migrated."""

    del args, kwargs
    raise RolloutError("prepare_repository was replaced by render_repository")
