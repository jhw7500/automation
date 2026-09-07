# Handoff — automation

_2026-09-07 · claude-code · 브랜치 `feat/128-opencode-finding-ids` · **#128 Phase 1 구현 완료, 검증 중**_

## 체크포인트 — #128 Phase 1, PR #151 리뷰 라운드 종료
- **완료·검증됨**: 커밋 `7b19fc7`, PR **#151**. 전체 스위트 **3105 passed / 0 failed**
  (part1 832 · part2 1667 · part3 606), actionlint OK. 신규 테스트 20건 전부 양쪽 arm 검증.
  리뷰 라운드: **Claude·Gemini 지적 0건**, OpenCode 는 `wall_time_exhausted` 로 실패.
- **OpenCode 실패는 이 변경과 무관**: 후보 아티팩트가 `elapsed_seconds: 610`(잡 상한 10분),
  `review_sha256: null`, `candidate_validations: []` — 모델이 62KB diff 에서 잘렸고 후보 자체가
  없었다. 내 변경은 후보가 생긴 뒤의 렌더 경로다. 실패 경로는 프로덕션에서 정상 동작 확인됨
  (`provider_failed` 스티키를 내 수정본이 게시). **아직 못 본 것은 성공 경로의 ID 렌더링뿐이다.**
- **같은 head 재시도는 설계상 불가**: `contracts.md:249-255` — override 라운드는 Claude·Gemini
  전용이고 v1.62 부터 OpenCode 는 거절된다(dispatch 라운드는 발행할 수 없으므로). override 는
  소비되지 않았다(0/1). 재시도하려면 **새 head** 가 필요하다(#145 와 같은 규칙).
- **다음 액션 1개**: PR #151 머지 → **v1.70 릴리스 + 17타깃 롤아웃** → `automation_ref` 범프 PR.
  ID 렌더링 실기 확인은 범프 PR(작은 diff)에서 자연히 이뤄진다.
- **제약**: `gh`·`git push` 는 `env -u GITHUB_TOKEN`. actionlint 는 `-shellcheck= -pyflakes=`.
  스위트 3분할. 워크플로를 1바이트라도 고치면
  `EXPECTED_OPENCODE_FINDING_ID_WORKFLOW_SHA256["opencode"]`(`verify_workflow_release.py:800`) 재계산.
- **열린 이슈**: #128(작업 중) · #150 · #145 · #133 잔여 · #125 · #106 · #93 · #83. #112 는 Phase 2 에서 닫힌다.
  **신규 관측**: 62KB diff 에서 600초 벽시계 한도가 부족하다 — #145(77초 빈 스트림)와 증상이 달라
  별도 기록 대상이다.

## #128 설계 결정 (접근 검증 리뷰 판정 C 반영)
- **이슈의 7단계 중 2번(캐리오버 결속을 ID 키로 전환)은 연기**한다 — 영구 기각이 아니라
  소프트 정규화 프리미티브가 선행돼야 하는 목적지다. 결속은 heading **원문 문자열**을 유지한다.
  실측: ID 결속을 요구하는 변형에서 구형식 prior 를 가진 라운드가 `attempt_status: failure` 로
  **문서 전체 실패**했다. 문자열 결속만이 롤아웃 시점의 진행 중 PR 을 흡수한다.
- **severity 는 문법에 도입하되 강제하지 않는다.** `:4196`(`/^#### \S.*$/`) 불변. 렌더러가
  `[CRITICAL|HIGH|MEDIUM]` 를 선택적으로 읽고, 없거나 미인식이면 heading 원문 그대로 ID 없이
  발행한다 — 오늘 통과하는 finding 과 **바이트 동일**. 실측 표본(플릿 17repo/509PR/스티키 91건)에서
  finding heading 은 6건뿐이고 전부 `[MEDIUM]` 이었다. `[CRITICAL]`/`[HIGH]` 표본은 0건이다.
- **ID 는 한 번만 부여하고 캐리오버는 승계**한다(재파생 금지). 재파생하면 앵커가 움직일 때마다
  ID 가 바뀌어 이미 제출된 기각이 무효가 된다.
- **Phase 2 (다음)**: 기각 배선 — 봉인 handoff 6지점, `choose_dismissals` 의 opencode 가드 해제,
  `dismissed_prior_id`. **주의: 기각으로 캐리오버 섹션이 비면 `splitSections` 가 다음 라운드에
  문서를 `null` 처리해 그 PR 의 리뷰가 영구 실패한다** — retained 0 인 캐리오버 섹션은 드롭할 것.

## 재사용할 사실 (이전 사이클)
- **폐기·삭제를 처음 하는 경로는 스위트 통과로 검증되지 않는다** — 반드시 실제 plan 을 돌려 본다.
- **정본 트리는 카탈로그와 정확히 일치해야 한다** — 파일·카탈로그 항목·설정 키를 함께 지운다.
- **OpenCode 실패 재현에는 새 head 가 필요하다** — `gh run rerun --failed` 는 모델을 다시 부르지 않는다.
- **`candidate_contract_failed` 는 계약 위반이 아닐 수 있다** — 후보 추출 실패에도 같은 이름이 붙는다.
- **BG 테스트를 돌리는 동안 그 테스트가 읽는 파일을 편집하지 않는다** — 이번 세션에서 릴리스 검증
  4건이 그렇게 오염됐고, 실패 이름이 무관해 보여 기존 결함으로 오분류될 뻔했다. 또한
  `pytest ... | tail` 의 종료 코드는 **tail 의 것**이다 — 판정은 요약 줄로 한다.

## 완료된 릴리스
- v1.69 (플릿 17/17, 기본 `automation_ref`) · v1.68(태그만) · v1.67 · v1.66 · v1.65.
