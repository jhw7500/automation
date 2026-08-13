#!/usr/bin/env python3
"""Immutable reference checks for actions used by managed workflows."""

from pathlib import Path
import re
import unittest

import yaml

from scripts.prepare_workflow_rollout import CHECKOUT_SHA as FLEET_CHECKOUT_SHA
from scripts.verify_workflow_release import CHECKOUT_ACTION


ROOT = Path(__file__).resolve().parents[1]
CHECKOUT_SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"
CACHE_SHA = "55cc8345863c7cc4c66a329aec7e433d2d1c52a9"
OPENCODE_VERSION = "1.18.17"
OPENCODE_ARCHIVE_SHA256 = (
    "3f14a4c61c7f6b0d3b6d933d1d212e64e19683eba6fa453ad98e46303afe144a"
)
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
    def test_release_and_fleet_gates_use_the_source_checkout_pin(self) -> None:
        self.assertEqual(CHECKOUT_SHA, FLEET_CHECKOUT_SHA)
        self.assertEqual(f"actions/checkout@{CHECKOUT_SHA}", CHECKOUT_ACTION)

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

    def test_opencode_workflows_pin_cli_archive_and_cache_action(self) -> None:
        for filename, job_name, run_name in (
            ("opencode.yml", "opencode", "Run opencode"),
            ("opencode-auto-review.yml", "opencode-review", "Run OpenCode PR review"),
        ):
            with self.subTest(workflow=filename):
                path = ROOT / ".github" / "workflows" / filename
                text = path.read_text(encoding="utf-8")
                workflow = yaml.load(text, Loader=yaml.BaseLoader)
                job = workflow["jobs"][job_name]
                self.assertNotIn("anomalyco/opencode/github@", text)
                self.assertEqual(OPENCODE_VERSION, job["env"]["OPENCODE_VERSION"])
                self.assertEqual(
                    OPENCODE_ARCHIVE_SHA256,
                    job["env"]["OPENCODE_ARCHIVE_SHA256"],
                )
                cache_step = next(
                    step
                    for step in job["steps"]
                    if step.get("name") == "Cache pinned OpenCode CLI archive"
                )
                self.assertEqual(f"actions/cache@{CACHE_SHA}", cache_step["uses"])
                self.assertIn(
                    'releases/download/v${OPENCODE_VERSION}/opencode-linux-x64.tar.gz',
                    text,
                )
                self.assertIn("sha256sum --check -", text)
                self.assertIn('"$install_dir/opencode" --version', text)
                run_step = next(
                    step for step in job["steps"] if step.get("name") == run_name
                )
                self.assertEqual("opencode github run", run_step["run"])
                self.assertEqual("true", run_step["env"]["USE_GITHUB_TOKEN"])


if __name__ == "__main__":
    unittest.main()
