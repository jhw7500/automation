#!/usr/bin/env python3
"""Immutable reference checks for actions used by managed workflows."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
CHECKOUT_SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"
MANAGED_WORKFLOW_ROOTS = (
    ROOT / ".github" / "workflows",
    ROOT / "examples" / "baseline-workflows" / "workflows",
    ROOT / "examples" / "baseline-workflows" / ".github" / "workflows",
)


def workflow_paths() -> list[Path]:
    return sorted(
        path
        for directory in MANAGED_WORKFLOW_ROOTS
        for path in directory.glob("*.y*ml")
    )


class ActionPinsTest(unittest.TestCase):
    def test_all_managed_checkout_references_use_the_approved_sha(self) -> None:
        references: list[tuple[Path, int, str]] = []
        offenders: list[str] = []
        pattern = re.compile(r"uses:\s*['\"]?actions/checkout@([^'\"\s]+)")

        for path in workflow_paths():
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                match = pattern.search(line)
                if match is None:
                    continue
                ref = match.group(1)
                references.append((path, lineno, ref))
                if ref != CHECKOUT_SHA:
                    relative = path.relative_to(ROOT)
                    offenders.append(f"{relative}:{lineno}: {ref}")

        self.assertGreater(len(references), 0, "no managed checkout references found")
        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()
