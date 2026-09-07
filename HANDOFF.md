# Handoff — automation

_2026-09-07 · **#128 Phase 2 머지 완료, #112 CLOSED** · main = `a5fbc88` · 다음: v1.71 릴리스_

> 이 문서는 **도구 비의존**으로 쓴다. 다음 세션이 Codex 든 Claude Code 든 이것만 읽고 이어갈 수 있어야 한다.
> Claude Code 전용 사항은 그렇게 표시한다.

## 체크포인트

- **완료·검증됨**: `a5fbc88` (PR #155 머지). OpenCode 리뷰어가 기각을 적용하고(#112 **CLOSED**),
  블록 하나 때문에 리뷰 전체가 버려지지 않는다. 전체 스위트 **3113 passed / 0 failed**, actionlint OK.
- **다음 액션 1개**: **v1.71 릴리스 + 17타깃 롤아웃 → `automation_ref` 범프 PR.**
  현재 태그는 `v1.70` 까지, `automation_ref = v1.70`. 절차는 아래 "릴리스 절차" 그대로.
- **열린 PR 없음.** 작업 트리 clean.

## 릴리스 절차 (v1.70 에서 그대로 반복)

```bash
cd /home/jhw/ai/opencode/projects/automation
MERGE=$(git rev-parse origin/main)                      # a5fbc88...
python3 -m scripts.verify_workflow_release --ref v1.71 --expected-commit "$MERGE" --commit-only
git tag -a v1.71 -m "v1.71: apply dismissals to OpenCode and stop discarding a review over one block (#128)" "$MERGE"
env -u GITHUB_TOKEN git push origin v1.71
python3 -m scripts.verify_workflow_release --ref v1.71 --expected-commit "$MERGE"
```

그 다음 `docs/workflow-fleet-rollout.md` 의 절차를 v1.71 로 치환해 수행한다 — 하드닝된
`public_git`/`release_git` 클론 검증 블록(문서 :308-380)을 건너뛰지 말 것.

```bash
export AUTOMATION_RELEASE_ROOT=/tmp/automation-v1.71-public
export FLEET_WORKSPACE=/tmp/automation-v1.71-fleet
export ACTIONLINT=/tmp/actionlint-v1.7.12/actionlint
# plan (읽기 전용) → blocked=0 확인 후 배치로 publish → 전 PR 머지 → audit
env -u GITHUB_TOKEN python3 "$AUTOMATION_RELEASE_ROOT/scripts/rollout_workflow_fleet.py" \
  --automation "$AUTOMATION_RELEASE_ROOT" --workspace "$FLEET_WORKSPACE" \
  --initialize-workspace --mode plan --ref v1.71 --actionlint "$ACTIONLINT"
```

마지막에 `scripts/workflow-config.json` 의 `automation_ref` 와
`tests/test_workflow_catalog.py` 의 단언을 **함께** v1.71 로 올리는 범프 PR 을 낸다(한쪽만 바꾸면
`test_catalog_and_profiles_are_closed` 가 실패한다 — 양쪽 arm 확인됨).

## 제약 (반드시 지킬 것)

- `gh` 와 `git push` 는 **항상** `env -u GITHUB_TOKEN` 으로.
- actionlint 는 `-shellcheck= -pyflakes=` 플래그로만.
- 스위트는 3분할: `tests/ --ignore=tests/test_verify_workflow_release.py --ignore=tests/test_review_workflow_logic.py`(832),
  `tests/test_review_workflow_logic.py`(1672, ~9분), `tests/test_verify_workflow_release.py`(609, ~4분).
- **워크플로나 예산 헬퍼를 1바이트라도 고치면** `scripts/verify_workflow_release.py` 의
  `EXPECTED_OPENCODE_DISMISSAL_WORKFLOW_SHA256["opencode"]` 와
  `EXPECTED_REVIEW_INVOCATION_BUDGET_HELPER_SHA256_V171` 을 `sha256sum` 으로 다시 맞춘다.
  그리고 `tests/release_fixture_helpers.py` 의 `PRE_V171_OPENCODE_DISMISSAL_HUNKS` 를 재생성해
  왕복이 v1.70 핀(`218292d6…`, `2123326a…`)을 재현하는지 확인한다.
- **BG 테스트가 읽는 파일을 실행 중에 편집하지 않는다.** 이 세션에서 두 번 오염됐다.
  `pytest ... | tail` 의 종료 코드는 tail 의 것이므로 판정은 요약 줄로 한다.
- **호스트 메모리 빠듯** — codex 프로세스들이 약 4GB 를 쓰고 있어 긴 BG 감시자가 이 세션에서 6회 강제종료됐다.
  긴 폴링 대신 짧은 확인을 반복한다.
- **(Claude Code 전용)** `gh pr create` 는 `pre-pr-tribunal` 훅이 막는다. 리뷰어 3인 트리뷰널을
  돌려 `finalize` 가 pass 해야 통과된다. 보고서 파일은 **`chmod 600`**(CLI 가 `st_mode & 0o077` 거부),
  텍스트 필드는 **한 줄**이어야 한다(스키마가 개행·탭 등 `Cc` 문자를 전부 거부 — 레퍼런스에 미기재).
  `.review/`·`.omc/`·`.serena/` 는 `.git/info/exclude` 에 넣어 워크트리를 clean 으로 유지했다.
  Codex 세션에는 이 훅이 없다.

## #128 을 열어 둔 이유

Phase 1(v1.70, ID 부여)과 Phase 2(v1.71, 기각 적용)가 모두 머지됐지만, 이슈 본문의 7단계 중
**2번(캐리오버 결속을 heading 문자열에서 ID 키로 전환)은 의도적으로 하지 않았다.**
실측 근거: ID 결속을 요구하는 변형에서 구형식 prior 를 가진 라운드가 `attempt_status: failure` 로
문서 전체가 실패했다. 소프트 정규화 프리미티브가 선행돼야 하는 목적지이고, Phase 2 가 그
프리미티브를 만들었으므로 이제 착수 가능하다.

## 트리뷰널이 남긴 후속 이슈 (전부 비블로킹, 실행 증거 있음)

- **#156** 기각이 닿지 않는 경로 2 — severity 없는 heading 은 영구 기각 불가(공유 정규화기는
  `invalid_severity` 로 거름), `unchanged` 재사용 라운드는 기각된 finding 을 재발행(다음 모델 라운드에 자가치유)
- **#157** 발행 본문이 정직한 active 집합이 아니다 — 캐리오버를 **생략**하는 것만으로 finding 이 은퇴.
  base 와 head 에서 동일(기존 성질). 소프트 경로는 "잘못된 블록" 경로만 막았다
- **#158** 캐리오버 블록별 검증이 저장소 전체 `git diff` 를 블록 수만큼 반복(이전엔 1회). 600초 예산 잠식
- **#159** 릴리스 픽스처의 "복사 뒤 복원 재호출" 규칙 제거 — 이 세션에서 두 번 밟은 함정. C 가 재배치
  대안으로 v163·v168·v170·v171 통과를 확인했다
- **#152** 크기 가설은 **반증됐다** — 92KB/358초 성공. 남는 것은 진단 가능성(repair 산출물 미보존,
  정규화만 한 라운드의 원문 포인터 부재)

## 확정된 사실 (재사용할 것)

- **발행 본문이 곧 active 집합이다.** `priorActiveHeadings` 는 매 라운드 `previousBody` 에서 재도출되고
  `remaining_finding_ids` 는 발행 본문을 스크레이핑한다. 블록을 빼면 finding 이 은퇴한다 — 소프트 경로에서
  드롭이 아니라 **이월/강등**을 택한 이유다.
- **기각은 캐리오버만으로 부족하다.** 라운드 N 의 제거가 라운드 N+1 의 prior 에서 ID 를 없애므로,
  New finding 의 **파생 ID** 도 기각 목록과 대조해야 한다. 이것이 트리뷰널이 잡은 HIGH 였다.
- **severity 는 읽되 요구하지 않는다.** 문법(`/^#### \S.*$/`)은 불변. 못 읽으면 heading 원문 그대로,
  ID 없음 — 오늘 발행되는 finding 과 바이트 동일. 대가는 #156.
- **OpenCode 재시도에는 새 head 가 필요하다.** override 라운드는 Claude·Gemini 전용이고 v1.62 부터
  OpenCode 는 거절된다(`contracts.md:249-255`). `gh run rerun --failed` 는 모델을 다시 부르지 않는다.
- **Codex 는 전역 설정이 아니라 저장소별 등록**이고 자동 리뷰는 의도적으로 꺼져 있다.
  트리거는 `@codex review` 코멘트(`/jhw:pr` 이 자동 게시). 연동은 살아 있다(2026-09-07 실측).
  플릿 스캔 원자료: `scratchpad/codex-findings.md`.

## 완료된 릴리스
- v1.70 (#128 Phase 1: OpenCode finding ID) · v1.69 · v1.68(태그만) · v1.67 · v1.66 · v1.65.
