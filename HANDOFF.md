# Handoff — automation

_2026-09-05 · claude-code · main = 867e88e · **태그 v1.68 발행됨, 롤아웃 보류** · 브랜치 `fix/143-rollout-retired-caller` (v1.69 후보, 스위트 실행 중)_

## 체크포인트 — v1.68 태그까지 완료, 롤아웃은 v1.69 수정이 선행돼야 함
- **v1.68 머지·태그 완료**: PR #144 머지(`867e88e`), 태그 `v1.68`(tag object `f864e0b7…`),
  `verify_release` PASS, 선검증 v1.67 음성 대조 FAIL. 리뷰어 3종 CLEAN, CI success, 전체 스위트 3084 통과.
- **롤아웃은 하지 않았다** — plan 이 **17/17 BLOCKED** 였다:
  `unknown central caller path: .github/workflows/gemini-pr-review.yml`.
  `prepare_workflow_rollout.py:725` 가 소비자의 중앙 캘러를 `catalog.callers` 와 대조하는데
  그 뷰는 `central_workflow is None` 인 항목을 **제외**한다 — 폐기 항목이 정확히 그것이다.
  카탈로그가 관리하는 파일을 모르는 파일로 읽어, 그 파일을 지우려는 롤아웃 자체를 막았다.
- **수정 완료(커밋 `045cc04`, 브랜치 푸시됨)**: 비교 대상을 `catalog.managed_paths` 로 바꿨다.
  카탈로그가 선언한 적 없는 캘러를 잡는다는 원래 목적은 유지되고, 폐기 항목은 계획된 삭제로 넘어간다.
  **되돌리면 실패하는 테스트 추가** — 소비자가 폐기 캘러를 갖고 있는 픽스처는 지금까지 없었다
  (배포된 상태에서 무언가를 폐기한 적이 없어서). 수정 후 plan 재실행: **17/17 PLANNED, blocked=0**,
  `changed_paths` 에 두 파일 삭제 포함 확인.
- **다음 액션 1개**: 전체 스위트(BG `bqhl0xlvc`) 통과 확인 → PR → 라운드 → 머지 → **태그 v1.69** →
  롤아웃(**소비자에서 파일 2개 삭제**) → audit → 범프 PR(**v1.69 로 직행**, v1.68 은 롤아웃하지 않음).
- **제약**: 태그는 이동 불가라 v1.68 은 그대로 두고 v1.69 로 나간다.
  `gh`·`git push` 모두 `env -u GITHUB_TOKEN`. 롤아웃은 `--actionlint /tmp/actionlint-v1.7.12/actionlint`.
  라벨은 `opened` 실행 종료 후. **호스트 메모리 빠듯 — BG 작업이 이 세션에서 5회 강제종료됐다.**
- **열린 이슈**: #145(OpenCode 진단 가시성 — 아래) · #133(잔여: `gemini-review.yml:378` 금지 문장,
  중앙 파일 정리와 함께) · #128 · #125 · #106 · #93 · #83. #143 은 v1.68 로 해소.

## OpenCode 침묵 조사 결론 (이슈 #145)
- head `17e4f0b` 에서 한 번 침묵했고 **재현되지 않았다** — 새 head `8a0f9d0`(48KB, 더 큼)에서 정상 리뷰.
  프로브 2건으로 내용(1KB)과 33KB 크기를 각각 배제했다(PR #146·#147, 둘 다 정상 리뷰 후 닫음).
- **재현 규칙(중요)**: `gh run rerun --failed` 는 모델을 다시 부르지 않는다 — 모델 잡은 실패를 출력으로
  알리면서 **잡 수준에서는 success** 라 재실행 대상이 아니고, 다시 도는 `opencode-canonicalize` 는
  이전 출력을 읽어 항상 같은 결과를 낸다. 이것을 독립 시도로 세면 **한 번의 실패가 여러 번으로 보인다**
  (내가 그렇게 오판했다). 무플래그 전체 재실행은 모델을 부르지만 예산이 `duplicate_head` 로 거부한다.
  **재현에는 새 head 가 필요하다.**
- `candidate_contract_failed` 는 계약 위반이 아니라 **후보 추출 실패**일 수 있다. 원문 이벤트 스트림은
  보존되지 않아(`opencode-rejected` 는 후보가 생긴 뒤 거부될 때만) 진단이 `jq` 오류 한 줄에 의존한다.

## (이전) v1.67 · v1.66 · v1.65 — 완료
- v1.67: 태그 `88a2a115…` → `fbcbb30b`, 범프 PR #142. 수동 `/review` 에 diff 공급 + 구조 계약.
- v1.66: 태그 `bfdff11b…` → `14f97fb4`, 범프 PR #139. 라벨 불일치를 거절로 + opencode 문구 동등화.
- v1.65: 태그 `35379e75…` → `3bd49095`, 범프 PR #137. skip 사유 오귀속 수정.
