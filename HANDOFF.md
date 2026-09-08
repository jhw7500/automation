# Handoff — automation

_2026-09-08 · **v1.72 구현 완료, PR 대기** · main = `affdad0` · `automation_ref = v1.71`_

> 이 문서는 **도구 비의존**으로 쓴다. 다음 세션이 Codex 든 Claude Code 든 이것만 읽고 이어갈 수 있어야 한다.
> Claude Code 전용 사항은 그렇게 표시한다.

## 체크포인트

- **완료·검증됨**
  - v1.71 태그 = `b24074e` (annotated, 리모트 확인: `refs/tags/v1.71` → `1d8a1dc8`, peeled → `b24074e`).
  - 플릿 17/17 PR 머지, 감사 **`current=17 drift=0 blocked=0`**.
  - `automation_ref` 범프 PR #160 머지 (`deaf4d8` → main `04813c0`). 트리뷰널 라운드 1 `pass`, 블로커 0.
  - 전체 스위트 3113 passed(832+609+1672). umask 의존이던 픽스처 모드 단언은 #161 로 제거돼,
    이제 어떤 umask 의 체크아웃에서도 같은 수가 나온다.
  - **#157 종결**: 선택지 2(계약 문구 정직화) 채택, `contracts.md` 수정 머지(`98d64bb`). 불변식 자체는
    회복되지 않았고 재개 조건 4가지를 이슈에 남겼다. **#128 범위 2번은 superseded** 로 판정(이슈 코멘트).
  - **#161 완료** (PR #164, `5f6a69c`). 단언을 약화하지 않고 **삭제**했다 — `path.stat()` 은
    인덱스가 아니라 작업 트리를 읽으므로 `& 0o111 == 0` 로 바꿔도 `core.fileMode=false` 에서
    같은 결함이 되살아난다. 잃는 것은 "바이트는 맞는데 `stat()` 권한 비트가 0644가 아닌 경로"뿐이고,
    그 탐지는 원래 우연적·타깃 의존적이었다(심링크는 타깃 모드에 따라 잡히거나 안 잡혔다).
  - **v1.72 (#158 + #162) 구현 완료** — 브랜치 `feat/172-opencode-context-budget` (`e166292`),
    전체 스위트 **3119 passed**, actionlint clean. 세 변경을 각각 되돌려 자기 테스트만 빨개지는 것까지 확인.
- **다음 액션 1개**: **`feat/172-opencode-context-budget` 트리뷰널 → PR → 머지 → v1.72 태그 → 17타깃 롤아웃
  → `automation_ref` 범프.** 릴리스 절차는 아래 그대로.
- **그다음 후보**: #156(severity 없는 heading 은 영구적으로 ID 없음 → 기각 불가),
  #157(생략에 의한 은퇴 — v1.72 가 빈도를 낮췄을 뿐 경로는 열려 있다), #159, #163.
- **열린 PR 없음.** 작업 트리 clean.

## 릴리스 절차 (v1.71 에서 그대로 반복)

```bash
cd /home/jhw/ai/opencode/projects/automation
MERGE=$(git rev-parse origin/main)
python3 -m scripts.verify_workflow_release --ref vX.YZ --expected-commit "$MERGE" --commit-only
git tag -a vX.YZ -m "vX.YZ: <요약>" "$MERGE"
env -u GITHUB_TOKEN git push origin vX.YZ
python3 -m scripts.verify_workflow_release --ref vX.YZ --expected-commit "$MERGE"
```

그 다음 `docs/workflow-fleet-rollout.md` 를 vX.YZ 로 치환해 수행한다 — 하드닝된
`public_git`/`release_git` 클론 검증 블록(문서 :308-380)을 건너뛰지 말 것.

```bash
export AUTOMATION_RELEASE_ROOT=/tmp/automation-vX.YZ-public
export FLEET_WORKSPACE=/tmp/automation-vX.YZ-fleet
export ACTIONLINT=/tmp/actionlint-v1.7.12/actionlint
# plan(읽기 전용, blocked=0 확인) → publish 를 4개씩 배치로 → 전 PR 머지 → audit
env -u GITHUB_TOKEN python3 "$AUTOMATION_RELEASE_ROOT/scripts/rollout_workflow_fleet.py" \
  --automation "$AUTOMATION_RELEASE_ROOT" --workspace "$FLEET_WORKSPACE" \
  --initialize-workspace --mode plan --ref vX.YZ --actionlint "$ACTIONLINT"
# audit 는 별도 스크립트다 (rollout 에 --mode audit 는 없다)
env -u GITHUB_TOKEN python3 "$AUTOMATION_RELEASE_ROOT/scripts/audit_workflow_fleet.py" \
  --automation "$AUTOMATION_RELEASE_ROOT" --workspace "$FLEET_WORKSPACE" --ref vX.YZ
```

마지막에 `scripts/workflow-config.json` 의 `automation_ref` 와 `tests/test_workflow_catalog.py:42`
의 단언을 **함께** 올리는 범프 PR 을 낸다. 한쪽만 바꾸면 `test_catalog_and_profiles_are_closed`
가 실패한다 — v1.71 에서 arm 4종(both/config-only/test-only/base) 실측으로 재확인했다.
플릿은 16 저장소 / **17 브랜치 타깃**이다(`wlan-driver-v2` 가 `main`·`ported` 둘 다).

## 제약 (반드시 지킬 것)

- `gh` 와 `git push` 는 **항상** `env -u GITHUB_TOKEN` 으로.
- actionlint 는 `-shellcheck= -pyflakes=` 플래그로만.
- 스위트는 3분할: `tests/ --ignore=tests/test_verify_workflow_release.py --ignore=tests/test_review_workflow_logic.py`(832),
  `tests/test_verify_workflow_release.py`(609, ~4분), `tests/test_review_workflow_logic.py`(1672, ~9분).
- **`test_verify_workflow_release.py` 는 진짜 `.git` 이 있는 체크아웃을 요구한다.** `git archive`
  로 뽑은 트리에서는 469건이 `unsupported Git repository layout`(`verify_workflow_release.py:1814`)로
  setup 단계에서 죽는다. 스위트 검증은 클론이나 워크트리에서 하라.
- **`"v1.70"` ↔ `"v1.71"` 은 바이트 길이가 같다.** 같은 초에 sed 로 갈아끼우고 재실행하면 Python 이
  mtime+size 기반 `.pyc` 캐시를 재사용해 **한 단계 전 상태를 채점한다.** 되돌림 검증을 할 때는
  `PYTHONDONTWRITEBYTECODE=1` + `-p no:cacheprovider` + `__pycache__` 삭제를 붙여라.
  (이 세션에서 실제로 arm 결과가 한 칸씩 밀려 나왔다.)
- **새 릴리스 라인은 핀을 *교체*하지 말고 사다리에 *추가*한다.** 기존 상수를 덮어쓰면 그 릴리스
  자신의 바이트가 `verify_opencode_runtime` 의 두 allowlist(`:2503`, `:2539`)에서 빠져 이전 라인
  테스트가 무더기로 깨진다(이번에 29건). 한 칸을 올릴 때 손대는 곳: 새 `EXPECTED_*_SHA256` 상수,
  두 allowlist, `workflow_digests` 사다리 top, `workflow_release_inventory.py` 의 `*_RELEASE` +
  술어, `PRE_V17X_*_HUNKS` + 복원 함수, `prepare_v17X`, 그리고 **직전 `prepare_v17(X-1)` 에
  복사 *뒤* 복원 추가**(복사가 먼저 오면 복원이 조용히 지워진다 — 이 저장소가 세 번 밟았다).
  hunk 를 만든 뒤에는 **왕복이 직전 릴리스의 핀 다이제스트를 재현하는지** 반드시 확인한다.
- **워크플로나 예산 헬퍼를 1바이트라도 고치면** `scripts/verify_workflow_release.py` 의
  `EXPECTED_OPENCODE_DISMISSAL_WORKFLOW_SHA256["opencode"]` 와
  `EXPECTED_REVIEW_INVOCATION_BUDGET_HELPER_SHA256_V171` 을 `sha256sum` 으로 다시 맞춘다.
  그리고 `tests/release_fixture_helpers.py` 의 `PRE_V171_OPENCODE_DISMISSAL_HUNKS` 를 재생성해
  왕복이 v1.70 핀(`218292d6…`, `2123326a…`)을 재현하는지 확인한다.
- **BG 테스트가 읽는 파일을 실행 중에 편집하지 않는다.** `pytest ... | tail` 의 종료 코드는
  tail 의 것이므로 판정은 요약 줄로 한다.
- **(Claude Code 전용)** `gh pr create` 는 `pre-pr-tribunal` 훅이 막는다. 리뷰어 3인을 detached
  워크트리에서 돌려 `finalize` 가 pass 해야 한다. 2026-09-07 기준 실측한 운영 제약:
  - **`COMMAND_AMBIGUOUS` 는 원인을 구분해 주지 않는다.** 최소 세 가지가 같은 코드로 뭉뚱그려진다.
    소거법으로 한 겹씩 벗겨야 한다 — `evaluate_gate(cwd, cmd)` 를 직접 호출해 형태별로 비교하는 게 가장 빠르다.
    ① 환경 검사: `GIT_` 접두 이름이 `_SAFE_GIT_ENV_NAMES` 9개에 없으면 차단. harness 가 넣는
       `GIT_EDITOR` 가 여기 걸린다(claude-config #116, 배포할 때마다 재발).
    ② `--repo` / `-R` / `--head` / `-H` 는 **거부 대상**이다. `--base` 는 **필수**. 대상은 현재 체크아웃에서 바인딩된다.
    ③ **복합 명령이면 무조건 거부.** `cd && gh`, heredoc + gh, `echo; gh` 전부 막힌다.
       훅은 Bash 호출 문자열 전체를 본다. **`gh pr create` 를 단독 호출로 분리**하고 본문은 미리 `--body-file` 로 준비한다.
  - 통과하는 형태: `env -u GITHUB_TOKEN gh pr create --base main --title "..." --body-file <path>` (단독 호출).
  - 보고서 파일은 **`chmod 600`**, `.review/inbox/round-N` **디렉터리는 `chmod 700`**(`rm -rf` 후
    `mkdir` 하면 umask 0002 로 775 가 되어 `ROUND_DIRECTORY_UNSAFE`). 텍스트 필드는 **한 줄**
    (`Cc` 문자 전부 거부), **severity 는 대문자**(`LOW` 소문자면 `FINDING_SCHEMA_INVALID` 로 라운드 전체 폐기).
  - `diff_sha256` 는 **`git diff --full-index BASE HEAD`** 기준이다. 리뷰어 프롬프트에 이 명령을 명시하지
    않으면 재현 실패를 결함으로 보고한다.
  - **트리가 안 바뀐 라운드에 전체 스위트를 다시 돌리게 하지 말 것.** 커밋 메시지만 고친 라운드에서
    B 에게 3113건을 재실행시켰다가 90분 초과로 죽였다. 범위를 좁히면 같은 리뷰가 2~8분에 끝난다.
  - 서브에이전트에 `name` 을 붙이면 최종 보고 텍스트가 유실되니 붙이지 말 것.
  - `.review/`·`.omc/`·`.serena/` 는 `.git/info/exclude` 에 있다. Codex 세션에는 이 훅이 없다.

## #128 을 열어 둔 이유

Phase 1(v1.70, ID 부여)과 Phase 2(v1.71, 기각 적용)가 모두 머지됐지만, 이슈 본문의 7단계 중
**2번(캐리오버 결속을 heading 문자열에서 ID 키로 전환)은 의도적으로 하지 않았다.**
실측 근거: ID 결속을 요구하는 변형에서 구형식 prior 를 가진 라운드가 `attempt_status: failure` 로
문서 전체가 실패했다. 소프트 정규화 프리미티브가 선행돼야 하는 목적지이고, Phase 2 가 그
프리미티브를 만들었으므로 이제 착수 가능하다.

## 열린 후속 이슈

- **#159** 릴리스 픽스처의 "복사 뒤 복원 재호출" 규칙 제거 — 이 함정을 두 번 밟았다
- **#158** 캐리오버 블록별 검증이 저장소 전체 `git diff` 를 블록 수만큼 반복 — 600초 예산 잠식
- **#157** 발행 본문이 정직한 active 집합이 아니다 — 캐리오버 **생략**만으로 finding 이 은퇴
- **#156** 기각이 닿지 않는 경로 2 — severity 없는 heading, `unchanged` 재사용 라운드
- **#152** 크기 가설은 **반증됐다**(92KB/358초 성공). 남는 것은 진단 가능성

## 확정된 사실 (재사용할 것)

- **발행 본문이 곧 active 집합이다.** `priorActiveHeadings` 는 매 라운드 `previousBody` 에서 재도출되고
  `remaining_finding_ids` 는 발행 본문을 스크레이핑한다. 블록을 빼면 finding 이 은퇴한다 — 소프트 경로에서
  드롭이 아니라 **이월/강등**을 택한 이유다.
- **기각은 캐리오버만으로 부족하다.** 라운드 N 의 제거가 라운드 N+1 의 prior 에서 ID 를 없애므로,
  New finding 의 **파생 ID** 도 기각 목록과 대조해야 한다.
- **severity 는 읽되 요구하지 않는다.** 문법(`/^#### \S.*$/`)은 불변. 못 읽으면 heading 원문 그대로,
  ID 없음 — 오늘 발행되는 finding 과 바이트 동일. 대가는 #156.
- **범프 PR 은 태그 뒤에 온다.** 태그 vX 의 트리는 `automation_ref = v(X-1)` 을 담는다
  (v1.67→v1.66, v1.70→v1.69, v1.71→v1.70). 이건 시차가 아니라 확립된 순서다.
- **HANDOFF 는 릴리스마다 별도 `docs(handoff)` 커밋으로 닫는다** (`45edd1e` v1.69, `2e0d2b2` v1.70, 이 커밋 v1.71).
- **OpenCode 재시도에는 새 head 가 필요하다.** override 라운드는 Claude·Gemini 전용이고 v1.62 부터
  OpenCode 는 거절된다(`contracts.md:249-255`). `gh run rerun --failed` 는 모델을 다시 부르지 않는다.
- **Codex 는 전역 설정이 아니라 저장소별 등록**이고 자동 리뷰는 의도적으로 꺼져 있다.
  트리거는 `@codex review` 코멘트. 연동은 살아 있다(2026-09-07 실측).

## 완료된 릴리스
- v1.71 (#128 Phase 2: OpenCode 기각 적용, #112 CLOSED) · v1.70 (#128 Phase 1) · v1.69 · v1.68(태그만) · v1.67 · v1.66 · v1.65.
