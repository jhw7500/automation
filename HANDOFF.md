# Handoff — automation

_2026-09-05 · claude-code · main = d2832c5 (v1.67) · 작업 브랜치 `fix/133-request-notes` (v1.68, 전체 스위트 실행 중)_

## 체크포인트 — v1.68 두 갈래 구현 완료, 스위트 확인 중
- **완료·검증됨 (A: 요청자 질문 전달, 커밋 `36c64ef`)**: 전체 스위트 **3083 통과** ·
  되돌리면 7건 빨개짐 · actionlint 0건. `additional_context` 를 diff 와 같이 **파일로** 넘기고
  프롬프트는 파일명만 가리킨다(UNTRUSTED 헤더 + BEGIN/END + 4000B 상한 + 잘림 표시).
  **답글일 때 부모 코멘트 본문이 실리는데 그 작성자는 OWNER/MEMBER/COLLABORATOR 검사를 안 거친다** —
  비신뢰 취급의 실제 근거.
- **완료 (B: #143 폐기, 미커밋)**: 사용자 승인. 카탈로그 2항목 `retired`(롤아웃이 소비자 파일을 삭제),
  정본 파일 2개·설정 키 2개 삭제, `_verify_manual_gemini_output_contract` 를 v1.68 게이트,
  `restore_retired_manual_pr_review`(파일+카탈로그+설정 키 복원) + **전 체인 배선**
  (`prepare_v151~v167`, `restore_historical_v140_manual_outputs`, 번들 픽스처).
  릴리스 계약 테스트 2건(양방향). **검증기 601 통과 · actionlint 0건.**
- **정본 트리는 카탈로그와 정확히 일치해야 한다**(`test_canonical_tree_is_exactly_catalogued`) —
  파일을 남기고 `retired` 만 표시하는 방식은 불가함을 실측으로 확인했다.
- **중앙 `.github/workflows/gemini-review.yml` 은 남겼다** — 소비자에 배포되지 않고, 지우면 과거 태그
  픽스처까지 건드려 범위가 커진다. 후속 정리 대상.
- **다음 액션 1개**: PR #144 라운드 → 머지 →
  태그 v1.68 → 롤아웃(**이번 롤아웃은 소비자에서 파일 2개를 삭제한다**) → audit → 범프 PR.
- **제약**: `gh`·`git push` 모두 `env -u GITHUB_TOKEN`. 롤아웃은 `--actionlint /tmp/actionlint-v1.7.12/actionlint`.
  라벨은 `opened` 실행 종료 후. 호스트 메모리 빠듯 — BG 작업이 2회 강제종료된 적 있다.
- **열린 이슈**: #143(이 릴리스로 해소) · #133(잔여 1건 — `gemini-review.yml:378` 금지 문장, 중앙 파일과 함께 정리) ·
  #128 · #125 · #106 · #93 · #83.

## OpenCode 재현 규칙 (이번에 실측)
- **`gh run rerun --failed` 는 모델을 다시 부르지 않는다.** `opencode-review` 잡은 `REVIEW_OUTCOME=failure`
  를 출력해도 **잡 자체는 success** 라 재실행 대상이 아니다. 다시 도는 것은 `opencode-canonicalize` 뿐이고
  그것은 이전 출력을 읽으므로 같은 결과가 나온다 — "여러 번 실패했으니 결정적" 이라고 읽으면 오진이다.
- **무플래그 전체 재실행도 같은 head 에서는 막힌다.** 예산이 `duplicate_head` 로 거부한다(설계대로).
  같은 head 에서 모델을 다시 부를 방법은 없다. 재현하려면 **새 head** 가 필요하다.
- **원문 이벤트 스트림은 보존되지 않는다.** `opencode-rejected` 아티팩트는 후보가 *생긴 뒤* 거부될 때만
  채워진다. 후보 추출 자체가 실패하면 남는 것은 `jq` 오류 한 줄뿐이다(이슈 #145).

## #143 폐기 근거 (실측)
- `gemini-pr-review.yml` 실행 13건 vs `gemini-dispatch.yml` 3,154건(소비자 6곳 표본).
- **2026-01-22 이후 성공 0건.** 마지막 3건(8/29·8/31×2) 전부 실패.
- 원인은 자기 `PATH` 재정의(`gemini-review.yml:297`)가 safe-bin 래퍼를 앞에 두어,
  **액션이 자기 출력 임시파일을 읽는 것까지 거부**한 것:
  `ERROR: refusing to read non-text file: /home/runner/work/_temp/gemini-out.*`
  (`grep -rln safe-bin .github/workflows/` 결과는 이 파일 하나 — 정상 동작하는 3종은 PATH 를 안 건드린다.)
- 그 워크플로는 `pr_number` 를 받고도 범위·체크아웃에 쓰지 않아 기본 브랜치 마지막 커밋을 리뷰했다.

## (이전) v1.67 · v1.66 · v1.65 — 완료
- v1.67: 태그 `88a2a115…` → `fbcbb30b`, 범프 PR #142(`d2832c5`). 수동 `/review` 에 diff 공급 + 구조 계약.
- v1.66: 태그 `bfdff11b…` → `14f97fb4`, 범프 PR #139. 라벨 불일치를 거절로 + opencode 문구 동등화.
- v1.65: 태그 `35379e75…` → `3bd49095`, 범프 PR #137. skip 사유 오귀속 수정.
