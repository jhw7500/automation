#!/usr/bin/env python3
"""Create or normalize the fleet's review labels in configured repositories (issue #115).

The rollout and audit tools only inspect label names and block a repository whose
opt-in labels are missing; they never create anything but Git objects. This script is
the explicit, idempotent operator step that creates the missing labels and, with
``--normalize``, also repairs color/description drift. Without ``--confirm`` it only
reports.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import workflow_fleet_git as fleet_git  # noqa: E402
from scripts.prepare_workflow_rollout import REQUIRED_REVIEW_LABELS  # noqa: E402
from scripts.workflow_catalog import CatalogError, load_catalog, load_fleet_config  # noqa: E402

LIST_LIMIT = "300"
_ALLOWED_GH = (["gh", "label", "list"], ["gh", "label", "create"], ["gh", "label", "edit"])


def _gh_label(args: list[str]) -> str:
    """Run one label command; nothing but list/create/edit can leave this script."""
    if args[:3] not in _ALLOWED_GH:
        raise ValueError("label operation is not permitted")
    return fleet_git.run(args)


class LabelInventoryError(ValueError):
    """GitHub returned a label inventory that cannot be trusted."""


@dataclass(frozen=True)
class LabelOutcome:
    repo: str
    missing: tuple[str, ...]
    drift: tuple[str, ...]
    created: tuple[str, ...]
    normalized: tuple[str, ...]
    error: str | None = None


def configured_repositories(root: Path) -> tuple[str, ...]:
    config = load_fleet_config(root, load_catalog(root))
    return tuple(sorted(config.profiles))


def fleet_owner(root: Path) -> str:
    return load_fleet_config(root, load_catalog(root)).owner


def label_inventory(owner: str, repo: str) -> dict[str, tuple[str, str]]:
    raw = _gh_label([
        "gh", "label", "list", "-R", f"{owner}/{repo}",
        "--json", "name,color,description", "--limit", LIST_LIMIT,
    ])
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise LabelInventoryError("GitHub returned malformed label inventory") from exc
    if not isinstance(data, list):
        raise LabelInventoryError("GitHub returned malformed label inventory")
    inventory: dict[str, tuple[str, str]] = {}
    for item in data:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("name"), str)
            or not item["name"]
        ):
            raise LabelInventoryError("GitHub returned malformed label inventory")
        color = item.get("color")
        description = item.get("description")
        inventory[item["name"]] = (
            color.upper() if isinstance(color, str) else "",
            description if isinstance(description, str) else "",
        )
    return inventory


def ensure_repository(
    owner: str, repo: str, *, confirm: bool, normalize: bool
) -> LabelOutcome:
    try:
        inventory = label_inventory(owner, repo)
    except (fleet_git.FleetGitError, LabelInventoryError) as exc:
        return LabelOutcome(repo, (), (), (), (), str(exc))
    missing: list[str] = []
    drift: list[str] = []
    drifted = []
    for label in REQUIRED_REVIEW_LABELS:
        if label.name not in inventory:
            missing.append(label.name)
            continue
        color, description = inventory[label.name]
        fields = []
        if color != label.color:
            fields.append(f"color={color} expected={label.color}")
        if description != label.description:
            fields.append(f"description={description!r} expected={label.description!r}")
        if fields:
            drift.append(f"{label.name} " + " ".join(fields))
            drifted.append(label)
    created: list[str] = []
    normalized: list[str] = []
    try:
        if confirm:
            for label in sorted(
                (item for item in REQUIRED_REVIEW_LABELS if item.name in missing),
                key=lambda item: item.name,
            ):
                _gh_label([
                    "gh", "label", "create", label.name, "-R", f"{owner}/{repo}",
                    "--color", label.color, "--description", label.description,
                ])
                created.append(label.name)
            if normalize:
                for label in drifted:
                    _gh_label([
                        "gh", "label", "edit", label.name, "-R", f"{owner}/{repo}",
                        "--color", label.color, "--description", label.description,
                    ])
                    normalized.append(label.name)
    except fleet_git.FleetGitError as exc:
        return LabelOutcome(
            repo, tuple(sorted(missing)), tuple(drift), tuple(created), tuple(normalized),
            str(exc),
        )
    return LabelOutcome(
        repo, tuple(sorted(missing)), tuple(drift), tuple(created), tuple(normalized)
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--automation", type=Path, default=ROOT)
    parser.add_argument("--repo", action="append", default=[])
    parser.add_argument("--confirm", action="store_true", help="create missing labels")
    parser.add_argument(
        "--normalize",
        action="store_true",
        help="with --confirm, also repair color/description drift",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        fleet = configured_repositories(args.automation)
        owner = fleet_owner(args.automation)
    except (CatalogError, OSError, ValueError) as exc:
        parser.error(f"fleet configuration is invalid: {exc}")
    selected = tuple(args.repo) if args.repo else fleet
    unknown = sorted(set(selected) - set(fleet))
    if unknown:
        parser.error(f"repository is not configured: {', '.join(unknown)}")
    totals = {"missing": 0, "drift": 0, "created": 0, "normalized": 0}
    failed = False
    for repo in selected:
        outcome = ensure_repository(
            owner, repo, confirm=args.confirm, normalize=args.normalize
        )
        if outcome.error is not None:
            failed = True
            print(f"ERROR {repo}: {outcome.error}")
        remaining = tuple(name for name in outcome.missing if name not in outcome.created)
        unresolved = tuple(
            line for line in outcome.drift
            if line.split(" ", 1)[0] not in outcome.normalized
        )
        if outcome.created:
            print(f"CREATED {repo}: {', '.join(outcome.created)}")
        if outcome.normalized:
            print(f"NORMALIZED {repo}: {', '.join(outcome.normalized)}")
        if remaining:
            failed = True
            print(f"MISSING {repo}: {', '.join(remaining)}")
        for line in unresolved:
            print(f"DRIFT {repo}: {line}")
        quiet = not (remaining or unresolved or outcome.created or outcome.normalized)
        if outcome.error is None and quiet:
            print(f"CURRENT {repo}: {len(REQUIRED_REVIEW_LABELS)} review labels current")
        totals["missing"] += len(remaining)
        totals["drift"] += len(unresolved)
        totals["created"] += len(outcome.created)
        totals["normalized"] += len(outcome.normalized)
    print(
        f"SUMMARY repos={len(selected)} missing={totals['missing']} "
        f"drift={totals['drift']} created={totals['created']} "
        f"normalized={totals['normalized']}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
