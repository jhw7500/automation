"""릴리즈 픽스처 공용 헬퍼.

test_verify_workflow_release.py 와 test_workflow_release_bundle.py 가 라이브 트리를
역사적 태그 픽스처로 되돌릴 때 공유한다. automation_ref 는 라이브 config 에서 읽어
치환하므로 버전 범프 때 이 파일을 손볼 필요가 없다(하드코딩 범프 체크리스트 제거).
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess


HISTORICAL_REVIEW_WORKFLOW_COMMIT = "5ec427c540619d6fbd80ea758de8d8e0bf00d987"
HISTORICAL_REVIEW_WORKFLOWS = (
    "claude-code-review.yml",
    "gemini-auto-review.yml",
)


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
    source_repo: Path,
    filenames: tuple[str, ...] = HISTORICAL_REVIEW_WORKFLOWS,
) -> None:
    """Restore genuine v1.44 central review bytes into a historical fixture.

    The immutable commit is the target of tag v1.44. Using its committed bytes keeps
    pre-v1.45 fixtures honest when the live workflows gain release-owned dependencies.
    """

    for filename in filenames:
        relative = f".github/workflows/{filename}"
        payload = subprocess.run(
            [
                "/usr/bin/git",
                "-C",
                str(source_repo),
                "show",
                f"{HISTORICAL_REVIEW_WORKFLOW_COMMIT}:{relative}",
            ],
            check=True,
            stdout=subprocess.PIPE,
        ).stdout
        (repo / relative).write_bytes(payload)
