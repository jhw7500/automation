"""Fail-closed validation of canonical review finding scope and evidence."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import Literal


SHA_RE = re.compile(r"[0-9a-f]{40}")
STATUS_NAMES = {
    "A": "added", "B": "changed", "C": "copied", "D": "removed",
    "M": "modified", "R": "renamed", "T": "changed", "U": "changed", "X": "changed",
}
GIT_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent/automation-review-scope/home",
    "XDG_CONFIG_HOME": "/nonexistent/automation-review-scope/xdg",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "/bin/false",
    "SSH_ASKPASS": "/bin/false",
    "GIT_EXTERNAL_DIFF": "",
}


@dataclass(frozen=True)
class SourceAnchor:
    path: str
    line: int


@dataclass(frozen=True)
class TriggerEvidence:
    path: str
    line: int
    quote: str


@dataclass(frozen=True)
class ScopeFile:
    status: str
    filename: str
    previous_filename: str | None = None


@dataclass(frozen=True)
class ScopeManifest:
    repository: str
    pr_number: int
    merge_base_sha: str
    head_sha: str
    files: tuple[ScopeFile, ...]


class ScopeValidationError(ValueError):
    """The trusted scope cannot be reconstructed exactly."""


def _safe_path(value: object) -> bool:
    if not isinstance(value, str) or not value or "\0" in value or value.startswith("/"):
        return False
    parts = value.split("/")
    return all(part not in {"", ".", ".."} for part in parts)


def _regular_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def _read_utf8_file(path: Path, description: str, *, nonempty: bool = False) -> str:
    if not _regular_file(path):
        raise ScopeValidationError(f"{description} must be a regular file")
    try:
        payload = path.read_bytes()
        if nonempty and not payload:
            raise ScopeValidationError(f"{description} is empty")
        return payload.decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ScopeValidationError(f"{description} is not strict UTF-8") from error


def parse_name_status(payload: bytes) -> tuple[ScopeFile, ...]:
    """Parse Git's NUL-delimited name-status output without normalization."""
    if not isinstance(payload, bytes) or not payload or not payload.endswith(b"\0"):
        raise ScopeValidationError("malformed name-status output")
    fields = payload[:-1].split(b"\0")
    records: list[ScopeFile] = []
    index = 0
    while index < len(fields):
        try:
            raw_status = fields[index].decode("ascii")
        except UnicodeDecodeError as error:
            raise ScopeValidationError("malformed name-status output") from error
        index += 1
        match = re.fullmatch(r"([ABCDMRTUX])([0-9]{1,3})?", raw_status)
        if match is None or (match[2] is not None and match[1] not in {"C", "R"}):
            raise ScopeValidationError("malformed name-status output")
        if match[2] is not None and int(match[2]) > 100:
            raise ScopeValidationError("malformed name-status output")
        path_count = 2 if match[1] in {"C", "R"} else 1
        if index + path_count > len(fields):
            raise ScopeValidationError("malformed name-status output")
        try:
            paths = tuple(field.decode("utf-8") for field in fields[index:index + path_count])
        except UnicodeDecodeError as error:
            raise ScopeValidationError("name-status has a non-UTF-8 path") from error
        index += path_count
        if any(not _safe_path(path) for path in paths):
            raise ScopeValidationError("name-status has an unsafe path")
        records.append(
            ScopeFile(STATUS_NAMES[match[1]], paths[-1], paths[0] if path_count == 2 else None)
        )
    return tuple(records)


def parse_added_lines(patch: str) -> dict[int, str]:
    """Return exact added new-side lines after strictly parsing a unified patch."""
    if not isinstance(patch, str):
        raise ScopeValidationError("patch is not text")
    if not patch:
        return {}
    if not patch.endswith("\n"):
        raise ScopeValidationError("patch does not end in a newline")
    lines = patch[:-1].split("\n")
    added: dict[int, str] = {}
    in_hunk = False
    old_line = new_line = old_remaining = new_remaining = 0
    previous_old_start: int | None = None
    previous_new_start: int | None = None
    previous_old_end: int | None = None
    previous_new_end: int | None = None
    last_body_prefix: str | None = None
    old_eof_marked = new_eof_marked = False
    maximum = (1 << 53) - 1

    for line in lines:
        if line.startswith("@@"):
            if in_hunk and (old_remaining != 0 or new_remaining != 0):
                raise ScopeValidationError("incomplete patch hunk")
            if in_hunk and (old_eof_marked or new_eof_marked):
                raise ScopeValidationError("content follows an EOF marker")
            match = re.fullmatch(
                r"@@ -([0-9]+)(?:,([0-9]+))? \+([0-9]+)(?:,([0-9]+))? @@(?: .*)?", line
            )
            if match is None:
                raise ScopeValidationError("malformed patch hunk")
            old_start = int(match[1])
            old_count = int(match[2]) if match[2] is not None else 1
            new_start = int(match[3])
            new_count = int(match[4]) if match[4] is not None else 1
            old_end, new_end = old_start + old_count, new_start + new_count
            values = (old_start, old_count, new_start, new_count, old_end, new_end)
            if (any(value > maximum for value in values)
                    or (old_count > 0 and old_start < 1)
                    or (new_count > 0 and new_start < 1)
                    or (old_count == 0 and new_count == 0)
                    or (previous_old_end is not None and (
                        old_start < previous_old_end or new_start < previous_new_end
                        or old_start == previous_old_start or new_start == previous_new_start
                    ))):
                raise ScopeValidationError("unsafe patch hunk coordinates")
            in_hunk = True
            old_line, new_line = old_start, new_start
            old_remaining, new_remaining = old_count, new_count
            previous_old_start, previous_new_start = old_start, new_start
            previous_old_end, previous_new_end = old_end, new_end
            last_body_prefix = None
            continue
        if line == "\\ No newline at end of file":
            if (not in_hunk or last_body_prefix is None
                    or (last_body_prefix == "+" and new_remaining != 0)
                    or (last_body_prefix == "-" and old_remaining != 0)
                    or (last_body_prefix == " " and (old_remaining != 0 or new_remaining != 0))):
                raise ScopeValidationError("misplaced EOF marker")
            if last_body_prefix in {"+", " "}:
                new_eof_marked = True
            if last_body_prefix in {"-", " "}:
                old_eof_marked = True
            last_body_prefix = None
            continue
        if not in_hunk:
            continue
        if old_remaining == 0 and new_remaining == 0:
            raise ScopeValidationError("content follows a completed patch hunk")
        if line.startswith(" "):
            if old_remaining < 1 or new_remaining < 1:
                raise ScopeValidationError("invalid patch context")
            old_line, new_line = old_line + 1, new_line + 1
            old_remaining, new_remaining = old_remaining - 1, new_remaining - 1
        elif line.startswith("-"):
            if old_remaining < 1:
                raise ScopeValidationError("invalid removed patch line")
            old_line, old_remaining = old_line + 1, old_remaining - 1
        elif line.startswith("+"):
            if new_remaining < 1:
                raise ScopeValidationError("invalid added patch line")
            added[new_line] = line[1:]
            new_line, new_remaining = new_line + 1, new_remaining - 1
        else:
            raise ScopeValidationError("invalid patch line")
        last_body_prefix = line[0]
        if old_line > maximum or new_line > maximum:
            raise ScopeValidationError("unsafe patch line number")
    if in_hunk and (old_remaining != 0 or new_remaining != 0):
        raise ScopeValidationError("incomplete patch hunk")
    return added


def selected_left(manifest: ScopeManifest, diff_mode: str, previous_sha: str) -> str:
    if diff_mode == "full":
        return manifest.merge_base_sha
    if diff_mode == "delta" and re.fullmatch(r"[0-9a-f]{40}", previous_sha):
        return previous_sha
    raise ScopeValidationError("invalid selected range")


def _parse_manifest(path: Path) -> ScopeManifest:
    text = _read_utf8_file(path, "scope manifest")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise ScopeValidationError("invalid scope manifest") from error
    if not isinstance(data, dict) or set(data) != {
        "schema", "repository", "pr_number", "merge_base_sha", "head_sha", "files"
    } or data["schema"] != 1:
        raise ScopeValidationError("invalid scope manifest")
    repository, pr_number = data["repository"], data["pr_number"]
    merge_base_sha, head_sha, files = data["merge_base_sha"], data["head_sha"], data["files"]
    if (not isinstance(repository, str) or not isinstance(pr_number, int) or isinstance(pr_number, bool)
            or pr_number <= 0 or not isinstance(merge_base_sha, str) or not isinstance(head_sha, str)
            or not SHA_RE.fullmatch(merge_base_sha) or not SHA_RE.fullmatch(head_sha)
            or not isinstance(files, list)):
        raise ScopeValidationError("invalid scope manifest")
    parsed_files: list[ScopeFile] = []
    for file in files:
        if not isinstance(file, dict) or set(file) not in ({"status", "filename"}, {"status", "filename", "previous_filename"}):
            raise ScopeValidationError("invalid scope manifest file")
        status, filename = file.get("status"), file.get("filename")
        previous = file.get("previous_filename")
        if status not in set(STATUS_NAMES.values()) or not _safe_path(filename):
            raise ScopeValidationError("invalid scope manifest file")
        if status in {"renamed", "copied"}:
            if not _safe_path(previous):
                raise ScopeValidationError("invalid scope manifest rename")
        elif previous is not None or "previous_filename" in file:
            raise ScopeValidationError("invalid scope manifest file")
        parsed_files.append(ScopeFile(status, filename, previous))
    if not parsed_files or len({file.filename for file in parsed_files}) != len(parsed_files):
        raise ScopeValidationError("invalid scope manifest file identity")
    return ScopeManifest(repository, pr_number, merge_base_sha, head_sha, tuple(parsed_files))


def _git(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["/usr/bin/git", "--no-replace-objects", "--literal-pathspecs", "-c", "diff.external=", *args],
        cwd=root, env=GIT_ENV, check=False, capture_output=True,
    )
    if result.returncode != 0:
        raise ScopeValidationError("trusted Git reconstruction failed")
    return result.stdout


def _exact_record(root: Path, manifest: ScopeManifest, file: ScopeFile) -> None:
    paths = [file.filename] if file.previous_filename is None else [file.previous_filename, file.filename]
    records = parse_name_status(_git(
        root, "diff", "--no-ext-diff", "--no-textconv", "--name-status", "-z",
        "--find-renames=50%", "--ignore-submodules=none",
        f"{manifest.merge_base_sha}..{manifest.head_sha}", "--", *paths,
    ))
    if records != (file,):
        raise ScopeValidationError("manifest file identity cannot be reconstructed")


def _added_lines(root: Path, left: str, head: str, file: ScopeFile) -> dict[int, str]:
    paths = [file.filename] if file.previous_filename is None else [file.previous_filename, file.filename]
    try:
        patch = _git(
            root, "diff", "--no-ext-diff", "--no-textconv", "--find-renames=50%",
            "--ignore-submodules=none", "--inter-hunk-context=0", "--no-color", "-U0",
            f"{left}..{head}", "--", *paths,
        ).decode("utf-8")
    except UnicodeDecodeError:
        return {}
    return parse_added_lines(patch)


@dataclass(frozen=True)
class ReviewScope:
    repository_root: Path
    manifest: ScopeManifest
    diff_mode: Literal["full", "delta"]
    added_lines_by_path: dict[str, dict[int, str]]

    def validate_changed_anchor(self, anchor: SourceAnchor) -> bool:
        if not isinstance(anchor, SourceAnchor) or not _safe_path(anchor.path) or not _safe_line(anchor.line):
            return False
        return anchor.line in self.added_lines_by_path.get(anchor.path, {})

    def validate_fix_anchor(self, anchor: SourceAnchor) -> bool:
        return self.diff_mode == "delta" and self.validate_changed_anchor(anchor)

    def validate_trigger(self, evidence: TriggerEvidence) -> bool:
        if (not isinstance(evidence, TriggerEvidence) or not _safe_path(evidence.path)
                or not _safe_line(evidence.line) or not isinstance(evidence.quote, str)):
            return False
        try:
            record = _git(
                self.repository_root, "ls-tree", "-z", self.manifest.head_sha, "--", evidence.path
            )
            fields = record[:-1].split(b"\0") if record.endswith(b"\0") else []
            if len(fields) != 1:
                return False
            metadata, raw_path = fields[0].split(b"\t", 1)
            mode, kind, object_id = metadata.decode("ascii").split(" ")
            path = raw_path.decode("utf-8")
            if (mode not in {"100644", "100755"} or kind != "blob"
                    or not SHA_RE.fullmatch(object_id) or path != evidence.path):
                return False
            blob = _git(self.repository_root, "cat-file", "blob", object_id).decode("utf-8")
        except (ScopeValidationError, UnicodeDecodeError, ValueError):
            return False
        quoted = _line_at(blob, evidence.line)
        return quoted is not None and quoted == evidence.quote


def _safe_line(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value <= (1 << 53) - 1


def _line_at(text: str, line: int) -> str | None:
    if not text:
        return None
    segments = text.split("\n")
    terminated = text.endswith("\n")
    if terminated:
        segments.pop()
    if line > len(segments):
        return None
    value = segments[line - 1]
    if line <= text.count("\n") and value.endswith("\r"):
        return value[:-1]
    return value


def load_review_scope(
    repository_root: Path,
    manifest_path: Path,
    selected_diff_path: Path,
    *,
    diff_mode: Literal["full", "delta"],
    previous_sha: str,
    expected_repository: str,
) -> ReviewScope:
    if not isinstance(repository_root, Path) or not repository_root.is_dir():
        raise ScopeValidationError("repository root is invalid")
    if diff_mode not in {"full", "delta"}:
        raise ScopeValidationError("invalid selected range")
    manifest = _parse_manifest(manifest_path)
    _read_utf8_file(selected_diff_path, "selected diff", nonempty=True)
    if manifest.repository != expected_repository:
        raise ScopeValidationError("scope repository does not match")
    try:
        head = _git(repository_root, "rev-parse", "HEAD").decode("ascii").strip()
    except UnicodeDecodeError as error:
        raise ScopeValidationError("Git HEAD is invalid") from error
    if head != manifest.head_sha:
        raise ScopeValidationError("Git HEAD does not match scope manifest")
    reconstructed = parse_name_status(_git(
        repository_root, "diff", "--no-ext-diff", "--no-textconv", "--name-status", "-z",
        "--find-renames=50%", "--ignore-submodules=none",
        f"{manifest.merge_base_sha}..{manifest.head_sha}",
    ))
    if reconstructed != manifest.files:
        raise ScopeValidationError("scope manifest cannot be reconstructed")
    left = selected_left(manifest, diff_mode, previous_sha)
    if diff_mode == "delta" and _git(
        repository_root, "merge-base", "--is-ancestor", left, manifest.head_sha
    ) != b"":
        raise ScopeValidationError("invalid selected range")
    added_lines_by_path: dict[str, dict[int, str]] = {}
    for file in manifest.files:
        _exact_record(repository_root, manifest, file)
        if file.status != "removed":
            added_lines_by_path[file.filename] = _added_lines(
                repository_root, left, manifest.head_sha, file
            )
    return ReviewScope(repository_root, manifest, diff_mode, added_lines_by_path)
