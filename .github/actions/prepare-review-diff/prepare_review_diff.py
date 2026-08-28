#!/usr/bin/env python3
"""Prepare fail-closed, deterministic PR review inputs."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Literal, Sequence


SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
ARG_MAX_ENVIRONMENT_ALLOWANCE = 128 * 1024
GIT_STATUS_NAMES = {
    "A": "added",
    "B": "changed",
    "C": "copied",
    "D": "removed",
    "M": "modified",
    "R": "renamed",
    "T": "changed",
    "U": "changed",
    "X": "changed",
}


@dataclass(frozen=True)
class PreparedReviewDiff:
    diff_ready: bool
    diff_mode: Literal["full", "delta", "unchanged", "unavailable"]
    head_sha: str
    base_sha: str
    full_diff_sha256: str
    unchanged_since_previous: bool
    warning: str = ""


class PreparationUnavailable(Exception):
    """An expected external or mutable-input failure that must fail closed."""


def run(
    argv: Sequence[str], *, cwd: Path, check: bool = True
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(argv, cwd=cwd, check=check, capture_output=True)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pr-number", required=True, type=int)
    parser.add_argument("--previous-sha", required=True)
    parser.add_argument("--previous-full-hash", required=True)
    parser.add_argument("--force-full", action="store_true")
    parser.add_argument("--context-lines", required=True, type=int)
    parser.add_argument("--full-output", required=True)
    parser.add_argument("--delta-output", required=True)
    parser.add_argument("--manifest-output", required=True)
    parser.add_argument("--github-output")
    args = parser.parse_args(argv)
    if not REPOSITORY_RE.fullmatch(args.repository):
        parser.error("--repository must be OWNER/REPO")
    if args.pr_number <= 0:
        parser.error("--pr-number must be positive")
    if args.previous_sha and not SHA_RE.fullmatch(args.previous_sha):
        parser.error("--previous-sha must be a 40-character SHA or empty")
    if args.previous_full_hash and not HASH_RE.fullmatch(args.previous_full_hash):
        parser.error("--previous-full-hash must be a SHA-256 hash or empty")
    if args.context_lines < 0:
        parser.error("--context-lines must be non-negative")
    args.full_output = validate_output_path(parser, args.full_output)
    args.delta_output = validate_output_path(parser, args.delta_output)
    args.manifest_output = validate_output_path(parser, args.manifest_output)
    if len({args.full_output, args.delta_output, args.manifest_output}) != 3:
        parser.error("output paths must be distinct")
    return args


def validate_output_path(parser: argparse.ArgumentParser, value: str) -> Path:
    if not value:
        parser.error("output paths must not be empty")
    path = Path(value).resolve()
    if not path.parent.is_dir() or path.is_dir():
        parser.error(f"invalid output path: {value}")
    return path


def decode_json(output: bytes, description: str) -> object:
    try:
        return json.loads(output.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PreparationUnavailable(f"invalid {description} response") from error


def metadata(repository: str, pr_number: int, cwd: Path) -> tuple[str, str]:
    try:
        payload = decode_json(
            run(["gh", "api", f"repos/{repository}/pulls/{pr_number}"], cwd=cwd).stdout,
            "PR metadata",
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise PreparationUnavailable("PR metadata is unavailable") from error
    if not isinstance(payload, dict):
        raise PreparationUnavailable("malformed PR metadata")
    base = payload.get("base")
    head = payload.get("head")
    base_sha = base.get("sha") if isinstance(base, dict) else None
    head_sha = head.get("sha") if isinstance(head, dict) else None
    if not isinstance(base_sha, str) or not isinstance(head_sha, str):
        raise PreparationUnavailable("malformed PR metadata")
    if not SHA_RE.fullmatch(base_sha) or not SHA_RE.fullmatch(head_sha):
        raise PreparationUnavailable("malformed PR metadata")
    return base_sha, head_sha


def parse_name_status(output: bytes) -> list[dict[str, str]]:
    """Parse Git's NUL-delimited name-status format without path normalization."""
    try:
        fields = output.split(b"\0")
        if not fields or fields[-1] != b"":
            raise PreparationUnavailable("malformed local Git scope manifest")
        fields.pop()
    except (AttributeError, TypeError) as error:
        raise PreparationUnavailable("malformed local Git scope manifest") from error
    records: list[dict[str, str]] = []
    index = 0
    while index < len(fields):
        try:
            raw_status = fields[index].decode("ascii")
        except UnicodeDecodeError as error:
            raise PreparationUnavailable("malformed local Git scope manifest") from error
        index += 1
        match = re.fullmatch(r"([ABCDMRTUX])([0-9]{1,3})?", raw_status)
        if not match or (match[2] is not None and match[1] not in {"C", "R"}):
            raise PreparationUnavailable("malformed local Git scope manifest")
        if match[2] is not None and int(match[2]) > 100:
            raise PreparationUnavailable("malformed local Git scope manifest")
        path_count = 2 if match[1] in {"C", "R"} else 1
        if index + path_count > len(fields):
            raise PreparationUnavailable("malformed local Git scope manifest")
        try:
            paths = [field.decode("utf-8") for field in fields[index : index + path_count]]
        except UnicodeDecodeError as error:
            raise PreparationUnavailable("local Git scope contains a non-UTF-8 path") from error
        index += path_count
        if any(not path for path in paths):
            raise PreparationUnavailable("malformed local Git scope manifest")
        record = {"status": GIT_STATUS_NAMES[match[1]], "filename": paths[-1]}
        if path_count == 2:
            record["previous_filename"] = paths[0]
        records.append(record)
    return records


def local_scope(merge_base_sha: str, head_sha: str, cwd: Path) -> list[dict[str, str]]:
    try:
        output = run(
            [
                "git",
                "--no-replace-objects",
                "--literal-pathspecs",
                "-c",
                "diff.external=",
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--name-status",
                "-z",
                "--find-renames=50%",
                "--ignore-submodules=none",
                f"{merge_base_sha}..{head_sha}",
            ],
            cwd=cwd,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise PreparationUnavailable("local Git scope manifest is unavailable") from error
    return parse_name_status(output)


def scope_paths(records: Sequence[dict[str, str]]) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for record in records:
        for path in (record.get("previous_filename"), record["filename"]):
            if path is not None and path not in seen:
                seen.add(path)
                paths.append(path)
    return paths


def ensure_commit(sha: str, cwd: Path) -> None:
    present = run(
        ["git", "--no-replace-objects", "cat-file", "-e", f"{sha}^{{commit}}"],
        cwd=cwd,
        check=False,
    )
    if present.returncode == 0:
        return
    fetched = run(
        ["git", "--no-replace-objects", "fetch", "--no-tags", "origin", sha],
        cwd=cwd,
        check=False,
    )
    if fetched.returncode != 0:
        raise PreparationUnavailable(f"commit {sha} is unavailable locally")
    verified = run(
        ["git", "--no-replace-objects", "cat-file", "-e", f"{sha}^{{commit}}"],
        cwd=cwd,
        check=False,
    )
    if verified.returncode != 0:
        raise PreparationUnavailable(f"commit {sha} is unavailable locally")


def git_diff(left: str, right: str, context_lines: int, paths: Sequence[str], cwd: Path) -> bytes:
    if not paths:
        return b""
    argv = [
        "git",
        "--no-replace-objects",
        "--literal-pathspecs",
        "-c",
        "diff.external=",
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--find-renames=50%",
        "--ignore-submodules=none",
        f"-U{context_lines}",
        f"{left}..{right}",
        "--",
        *paths,
    ]
    try:
        arg_max = os.sysconf("SC_ARG_MAX")
        encoded_size = sum(len(os.fsencode(argument)) + 1 for argument in argv)
    except (AttributeError, OSError, ValueError, UnicodeError) as error:
        raise PreparationUnavailable("local PR-scoped diff argument limit is unavailable") from error
    if encoded_size > arg_max - ARG_MAX_ENVIRONMENT_ALLOWANCE:
        raise PreparationUnavailable("local PR-scoped diff exceeds executable argument limit")
    try:
        return run(argv, cwd=cwd).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise PreparationUnavailable("local PR-scoped diff is unavailable") from error


def git_full_diff(left: str, right: str, context_lines: int, cwd: Path) -> bytes:
    try:
        return run(
            [
                "git",
                "--no-replace-objects",
                "--literal-pathspecs",
                "-c",
                "diff.external=",
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--find-renames=50%",
                "--ignore-submodules=none",
                f"-U{context_lines}",
                f"{left}..{right}",
            ],
            cwd=cwd,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise PreparationUnavailable("local full diff is unavailable") from error


def merge_base(base_sha: str, head_sha: str, cwd: Path) -> str:
    try:
        value = run(
            ["git", "--no-replace-objects", "merge-base", base_sha, head_sha],
            cwd=cwd,
        ).stdout.decode("ascii").strip()
    except (OSError, UnicodeDecodeError, subprocess.CalledProcessError) as error:
        raise PreparationUnavailable("merge base is unavailable") from error
    if not SHA_RE.fullmatch(value):
        raise PreparationUnavailable("merge base is unavailable")
    return value


def stage(path: Path, data: bytes) -> Path:
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
        return Path(output.name)


def discard(paths: Sequence[Path]) -> None:
    for path in paths:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def publish(staged: Sequence[tuple[Path, Path]], remove: Sequence[Path]) -> None:
    try:
        for temporary, destination in staged:
            os.replace(temporary, destination)
    finally:
        discard([temporary for temporary, _ in staged])
    discard(remove)


def unavailable(
    *, head_sha: str, base_sha: str, warning: str, outputs: Sequence[Path]
) -> PreparedReviewDiff:
    discard(outputs)
    return PreparedReviewDiff(False, "unavailable", head_sha, base_sha, "", False, warning)


def write_github_output(path: str, result: PreparedReviewDiff) -> None:
    """Append the scalar subset GitHub Actions may expose as composite outputs."""
    values = (
        ("diff_ready", str(result.diff_ready).lower()),
        ("diff_mode", result.diff_mode),
        ("head_sha", result.head_sha),
        ("full_diff_sha256", result.full_diff_sha256),
        ("unchanged_since_previous", str(result.unchanged_since_previous).lower()),
    )
    with Path(path).open("a", encoding="utf-8") as output:
        for name, value in values:
            output.write(f"{name}={value}\n")


def prepare(args: argparse.Namespace, cwd: Path) -> PreparedReviewDiff:
    base_sha = ""
    head_sha = ""
    output_paths = (args.full_output, args.delta_output, args.manifest_output)
    try:
        base_sha, head_sha = metadata(args.repository, args.pr_number, cwd)
        ensure_commit(base_sha, cwd)
        ensure_commit(head_sha, cwd)
        merge_base_sha = merge_base(base_sha, head_sha, cwd)
        full_diff = git_full_diff(merge_base_sha, head_sha, args.context_lines, cwd)
        records = local_scope(merge_base_sha, head_sha, cwd)
        warnings: list[str] = []

        final_base, final_head = metadata(args.repository, args.pr_number, cwd)
        if (base_sha, head_sha) != (final_base, final_head):
            return unavailable(
                head_sha=head_sha,
                base_sha=base_sha,
                warning="PR base or head changed during diff preparation",
                outputs=output_paths,
            )

        full_hash = hashlib.sha256(full_diff).hexdigest()
        mode: Literal["full", "delta", "unchanged"] = "full"
        delta_diff: bytes | None = None
        if (
            not args.force_full
            and args.previous_full_hash
            and full_hash.lower() == args.previous_full_hash.lower()
        ):
            mode = "unchanged"
        elif not args.force_full and args.previous_sha:
            try:
                ensure_commit(args.previous_sha, cwd)
                ancestor = run(
                    [
                        "git",
                        "--no-replace-objects",
                        "merge-base",
                        "--is-ancestor",
                        args.previous_sha,
                        head_sha,
                    ],
                    cwd=cwd,
                    check=False,
                )
                if ancestor.returncode == 0:
                    candidate = git_diff(args.previous_sha, head_sha, args.context_lines, scope_paths(records), cwd)
                    if candidate:
                        mode = "delta"
                        delta_diff = candidate
            except PreparationUnavailable:
                warnings.append("incremental diff unavailable; used full PR diff")

        manifest = {
            "schema": 1,
            "repository": args.repository,
            "pr_number": args.pr_number,
            "merge_base_sha": merge_base_sha,
            "head_sha": head_sha,
            "files": records,
        }
        staged = [
            (stage(args.full_output, full_diff), args.full_output),
            (stage(args.manifest_output, (json.dumps(manifest, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")), args.manifest_output),
        ]
        if delta_diff is not None:
            staged.append((stage(args.delta_output, delta_diff), args.delta_output))
        publish(staged, [] if delta_diff is not None else [args.delta_output])
        return PreparedReviewDiff(
            True,
            mode,
            head_sha,
            base_sha,
            full_hash,
            mode == "unchanged",
            "; ".join(warnings),
        )
    except PreparationUnavailable as error:
        return unavailable(
            head_sha=head_sha,
            base_sha=base_sha,
            warning=str(error),
            outputs=output_paths,
        )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    result = prepare(args, Path.cwd())
    if args.github_output is not None:
        write_github_output(args.github_output, result)
    print(json.dumps(asdict(result), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
