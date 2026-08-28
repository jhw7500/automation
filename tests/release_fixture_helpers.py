"""릴리즈 픽스처 공용 헬퍼.

test_verify_workflow_release.py 와 test_workflow_release_bundle.py 가 라이브 트리를
역사적 태그 픽스처로 되돌릴 때 공유한다. automation_ref 는 라이브 config 에서 읽어
치환하므로 버전 범프 때 이 파일을 손볼 필요가 없다(하드코딩 범프 체크리스트 제거).
"""

from __future__ import annotations

import json
from pathlib import Path


HISTORICAL_REVIEW_FIXTURE_ROOT = (
    Path(__file__).parent / "fixtures/review-workflows-v1.44"
)
HISTORICAL_REVIEW_WORKFLOWS = (
    "claude-code-review.yml",
    "gemini-auto-review.yml",
    "opencode-auto-review.yml",
)
V145_REVIEW_FIXTURE_ROOT = (
    Path(__file__).parent / "fixtures/review-workflows-v1.45.2"
)
V145_REVIEW_WORKFLOWS = (
    "claude-code-review.yml",
    "gemini-auto-review.yml",
)
PRE_V146_SETUP_AUTH_FIXTURE = (
    Path(__file__).parent / "fixtures/setup-gemini-auth-v1.45.2.yml"
)
V145_PREPARE_DIFF_FIXTURE = (
    Path(__file__).parent / "fixtures/prepare-review-diff-v1.45.2.yml"
)


def restore_pre_force_review_callers(repo: Path) -> None:
    """Remove the force-review caller surface from historical fixtures."""

    baseline_root = repo / "examples/baseline-workflows/.github/workflows"
    for filename in ("claude-code-review.yml", "gemini-auto-review.yml"):
        path = baseline_root / filename
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        dispatch = (
            "  workflow_dispatch:\n"
            "    inputs:\n"
            "      pr_number:\n"
            "        description: Pull request number\n"
            "        type: number\n"
            "        required: true\n"
            "      force_review:\n"
            "        description: Perform one authorized same-HEAD review\n"
            "        type: boolean\n"
            "        required: false\n"
            "        default: false\n"
        )
        forced_if = (
            "    if: >-\n"
            "      (github.event_name == 'pull_request' &&\n"
            "      github.event.pull_request.head.repo.fork == false &&\n"
            "      github.event.pull_request.head.repo.full_name == github.repository) ||\n"
            "      (github.event_name == 'workflow_dispatch' && inputs.force_review)\n"
        )
        assert text.count(dispatch) == 1
        assert text.count(forced_if) == 1
        assert text.count("      force_review: ${{ github.event_name == 'workflow_dispatch' && inputs.force_review }}\n") == 1
        text = text.replace(dispatch, "", 1).replace(
            forced_if,
            "    if: ${{ github.event.pull_request.head.repo.fork == false && github.event.pull_request.head.repo.full_name == github.repository }}\n",
            1,
        ).replace(
            "      pr_number: ${{ github.event.pull_request.number || inputs.pr_number }}\n",
            "      pr_number: ${{ github.event.pull_request.number }}\n",
            1,
        ).replace(
            "      force_review: ${{ github.event_name == 'workflow_dispatch' && inputs.force_review }}\n",
            "",
            1,
        )
        path.write_text(text, encoding="utf-8")

    catalog = repo / "scripts/workflow-catalog.json"
    if catalog.exists():
        text = catalog.read_text(encoding="utf-8")
        dispatch = (
            '        },\n'
            '        "workflow_dispatch": {\n'
            '          "inputs": {\n'
            '            "pr_number": {\n'
            '              "description": "Pull request number",\n'
            '              "type": "number",\n'
            '              "required": "true"\n'
            '            },\n'
            '            "force_review": {\n'
            '              "description": "Perform one authorized same-HEAD review",\n'
            '              "type": "boolean",\n'
            '              "required": "false",\n'
            '              "default": "false"\n'
            '            }\n'
            '          }\n'
            '        }\n'
        )
        assert text.count(dispatch) == 2
        assert text.count('            "force_review",\n') == 2
        text = text.replace(dispatch, "        }\n", 2).replace(
            '            "force_review",\n', "", 2
        )
        catalog.write_text(text, encoding="utf-8")


def restore_pre_v146_review_contracts(repo: Path) -> None:
    """Restore shared auth identity and Claude/Gemini caller ceilings."""

    restore_pre_force_review_callers(repo)
    setup = repo / ".github/actions/setup-gemini-auth/action.yml"
    setup.parent.mkdir(parents=True, exist_ok=True)
    setup.write_bytes(PRE_V146_SETUP_AUTH_FIXTURE.read_bytes())

    baseline_root = repo / "examples/baseline-workflows/.github/workflows"
    for filename in ("claude-code-review.yml", "gemini-auto-review.yml"):
        path = baseline_root / filename
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        needle = "    permissions:\n      actions: read\n"
        assert text.count(needle) == 1
        text = text.replace(needle, "    permissions:\n", 1)
        if filename == "gemini-auto-review.yml":
            publisher_input = "      publisher_app_id: ${{ vars.APP_ID }}\n"
            assert text.count(publisher_input) == 1
            text = text.replace(publisher_input, "", 1)
        path.write_text(text, encoding="utf-8")

    catalog = repo / "scripts/workflow-catalog.json"
    if catalog.exists():
        text = catalog.read_text(encoding="utf-8")
        for caller in ("claude-review", "gemini-review"):
            needle = (
                f'          "name": "{caller}",\n'
                '          "permissions": {\n'
                '            "actions": "read",\n'
            )
            replacement = (
                f'          "name": "{caller}",\n          "permissions": {{\n'
            )
            assert text.count(needle) == 1
            text = text.replace(needle, replacement, 1)
        publisher_input = '            "publisher_app_id",\n'
        assert text.count(publisher_input) == 1
        text = text.replace(publisher_input, "", 1)
        catalog.write_text(text, encoding="utf-8")


def restore_historical_automation_ref(repo: Path, historical_ref: str) -> None:
    """픽스처 트리의 automation_ref 를 라이브 값에서 역사적 값으로 되돌린다."""
    config_path = repo / "scripts/workflow-config.json"
    config_text = config_path.read_text(encoding="utf-8")
    live_ref = json.loads(config_text)["automation_ref"]
    needle = f'"automation_ref": "{live_ref}"'
    assert config_text.count(needle) == 1, (
        f"workflow-config.json 에서 {needle} 를 정확히 1회 찾지 못했습니다 "
        f"(count={config_text.count(needle)}) — config 포맷이 바뀌면 이 헬퍼를 갱신하세요"
    )
    config_path.write_text(
        config_text.replace(needle, f'"automation_ref": "{historical_ref}"', 1),
        encoding="utf-8",
    )


def restore_historical_review_workflows(
    repo: Path,
    filenames: tuple[str, ...] = HISTORICAL_REVIEW_WORKFLOWS,
) -> None:
    """Restore genuine v1.44 central review bytes into a historical fixture.

    The snapshots come from immutable v1.44 commit 5ec427c540619d6fbd80ea758de8d8e0bf00d987.
    Keeping them in the test tree makes historical fixtures independent of Git history.
    """

    for filename in filenames:
        relative = f".github/workflows/{filename}"
        (repo / relative).write_bytes(
            (HISTORICAL_REVIEW_FIXTURE_ROOT / filename).read_bytes()
        )

    restore_pre_v146_review_contracts(repo)

    # v1.44 callers predate the Task 5 Actions/Checks ceiling. Restore those two
    # release-policy files without altering the immutable central workflow fixtures.
    baseline = repo / "examples/baseline-workflows/.github/workflows/opencode-auto-review.yml"
    if baseline.exists():
        text = baseline.read_text(encoding="utf-8")
        text = text.replace("      actions: read\n      checks: write\n", "", 1)
        baseline.write_text(text, encoding="utf-8")
    catalog = repo / "scripts/workflow-catalog.json"
    if catalog.exists():
        text = catalog.read_text(encoding="utf-8")
        text = text.replace(
            '          "permissions": {\n'
            '            "actions": "read",\n'
            '            "checks": "write",\n'
            '            "contents": "read",\n'
            '            "issues": "write",\n'
            '            "pull-requests": "write"\n'
            "          },",
            '          "permissions": {\n'
            '            "contents": "read",\n'
            '            "issues": "write",\n'
            '            "pull-requests": "write"\n'
            "          },",
            1,
        )
        catalog.write_text(text, encoding="utf-8")


def restore_v145_review_workflows(repo: Path) -> None:
    """Restore immutable v1.45.2 Claude/Gemini workflow bytes.

    These fixtures are the exact blobs published by commit
    abf5e65cf6188277d9984be062d0b069c82cf25f.  Reading only checked-in
    fixtures keeps historical verification available in shallow repositories.
    """

    for filename in V145_REVIEW_WORKFLOWS:
        relative = f".github/workflows/{filename}"
        (repo / relative).write_bytes(
            (V145_REVIEW_FIXTURE_ROOT / filename).read_bytes()
        )

    restore_pre_v146_review_contracts(repo)
    prepare = repo / ".github/actions/prepare-review-diff/action.yml"
    prepare.parent.mkdir(parents=True, exist_ok=True)
    prepare.write_bytes(V145_PREPARE_DIFF_FIXTURE.read_bytes())
