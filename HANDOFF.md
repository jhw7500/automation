# Handoff — automation

_2026-09-07 · claude-code · main = `dbd2a6e` · **v1.70 릴리스 완료** (#128 Phase 1)_

## 체크포인트 — v1.70 완료, 다음은 #128 Phase 2
- **완료·검증됨**: 태그 `v1.70`(tag `ed154b6f…` → commit `0989944`), 플릿 **17/17**,
  기본 `automation_ref` = v1.70(범프 PR #153, `dbd2a6e`). 마지막 감사는 `--ref` 없이
  `total=17 current=17 drift=0 blocked=0`. 로컬 스위트 **3105 passed / 0 failed**.
- **다음 액션 1개**: **#128 Phase 2 — 기각 배선**. `dismissed-finding-ids` 를 봉인 handoff 로
  OpenCode 캐노니컬라이저까지 나르고, `review_invocation_budget.py:755-758` 의 opencode 가드를
  해제하고, `dismissed_prior_id` 정규화를 넣는다. **#112 는 이때 닫힌다.**
- **제약**: `gh`·`git push` 는 `env -u GITHUB_TOKEN`. actionlint 는 `-shellcheck= -pyflakes=`.
  스위트 3분할. 워크플로를 1바이트라도 고치면
  `EXPECTED_OPENCODE_FINDING_ID_WORKFLOW_SHA256["opencode"]`(`verify_workflow_release.py:800`) 재계산.
  **호스트 메모리 빠듯 — BG 감시자가 이 세션에서 5회 강제종료됐다**(codex 프로세스 4개가 약 4.1GB).
  긴 BG 폴링 대신 직접 조회로 확인할 것.
- **열린 이슈**: #152(신규) · #150 · #145 · #133 잔여 · #125 · #106 · #93 · #83. #112 는 Phase 2 대기.

## #128 Phase 1 에서 확정된 것 (Phase 2 에서 재사용)
- **캐리오버 결속은 heading 원문 문자열이다 — 바꾸지 말 것.** 이슈 #128 의 2번(ID 키 전환)을
  변형으로 시험했더니 구형식 prior 를 가진 라운드가 `attempt_status: failure` 로 **문서 전체 실패**했다.
  문자열 결속만이 롤아웃을 가로지르는 PR 을 흡수한다. 2번은 소프트 정규화 프리미티브가 선행돼야 하는
  **목적지**이지 영구 기각이 아니다.
- **severity 는 읽되 요구하지 않는다.** `:4196`(`/^#### \S.*$/`) 불변. 못 읽으면 heading 원문 그대로,
  ID 없음 — 오늘 발행되는 finding 과 바이트 동일.
- **ID 는 한 번 부여하고 캐리오버가 승계한다**(재파생 금지). 재파생하면 앵커 이동 때마다 ID 가 바뀌어
  이미 제출된 기각이 무효가 된다.
- **Phase 2 의 함정**: 기각으로 캐리오버 섹션의 잔존 블록이 0 이 되면 빈 섹션이 발행되고,
  다음 라운드에 `splitSections`(`:4183-4185`)가 문서를 `null` 처리해 **그 PR 의 OpenCode 리뷰가 영구 실패**한다.
  잔존 0 인 캐리오버 섹션은 드롭할 것. `### New findings` 만 `None` 으로 유지한다.
- **실기 확인 상태**: 성공 경로(재렌더)는 PR #153 에서 확인됨(`attempt_status: success`).
  **ID 렌더링 자체는 아직 미관측** — OpenCode 가 지적을 실제로 내는 PR 이 나와야 확인된다.

## 재사용할 사실 (이전 사이클)
- **폐기·삭제를 처음 하는 경로는 스위트 통과로 검증되지 않는다** — 반드시 실제 plan 을 돌려 본다.
- **정본 트리는 카탈로그와 정확히 일치해야 한다** — 파일·카탈로그 항목·설정 키를 함께 지운다.
- **OpenCode 재시도에는 새 head 가 필요하다** — override 라운드는 Claude·Gemini 전용이고
  v1.62 부터 OpenCode 는 거절된다(`contracts.md:249-255`). `gh run rerun --failed` 도 모델을 다시 부르지 않는다.
- **`candidate_contract_failed` 는 계약 위반이 아닐 수 있다** — 후보 아티팩트의 `candidate_validations`
  (빈 배열이면 검사 미실행)와 `review_sha256`(null)로 판별한다.
- **BG 테스트가 읽는 파일을 실행 중에 편집하지 않는다** — 이번 세션에서 릴리스 검증 4건이 그렇게 오염됐고,
  실패 이름이 무관해 보여 기존 결함으로 오분류될 뻔했다. `pytest ... | tail` 의 종료 코드는 tail 의 것이다.

## 완료된 릴리스
- v1.70 (#128 Phase 1: OpenCode finding ID) · v1.69 · v1.68(태그만) · v1.67 · v1.66 · v1.65.
