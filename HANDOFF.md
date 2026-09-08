# Handoff — automation

_2026-09-08 · **v1.72 PR #167 열림, 머지 대기** · main = `6d8df7d` · `automation_ref = v1.71`_

> 도구 비의존으로 쓴다. 다음 세션이 Codex 든 Claude Code 든 이것만 읽고 이어갈 수 있어야 한다.

## 체크포인트

- **완료·검증됨**
  - 브랜치 `feat/172-opencode-context-budget`, HEAD `f544081` (push 완료).
    `27e35ba` 구현 → fix1~fix6 (`d202924` `b1a4cbd` `bc4ddd0` `d655c13` `b367212` `9fcfe29`)
    → `f544081` 문서 정정.
  - **PR #167** 생성 완료: https://github.com/jhw7500/automation/pull/167 (#162, #158 종료)
  - 트리뷰널 **5라운드** 완료, 최종 `9fcfe29` 에서 **pass / blocking_count 0**.
    라운드 기록은 `scratchpad/tribunal-*/` 에 보존(보고 15건 + 결정 9건).
  - 전체 스위트 **3122 passed, 48 subtests**. actionlint 0. run 블록 12개 `bash -n` 0오류.
  - 라운드트립: 현재 트리 → `restore_pre_v172_opencode_context_budget` → `v1.71` 바이트 일치, 멱등.
    hunk **10개** 전부 워크플로에 정확히 1회 출현.
  - v1.72 다이제스트 `9c7dfd5afc5ce5dea9fcaf51c1e3c0c362ef93ebca757621e82ba12c0bc12263`.
  - 신규 테스트 6건 양방향 실측(적용 시 통과 / 되돌리면 실패).
  - Notion 저장 6건 완료(KB 5 + DecisionLog 1).

- **다음 구체 액션 1개**
  PR #167 CI 확인 → 머지 → `v1.72` 태그 → 17타깃 플릿 롤아웃 → `automation_ref` 기본값 범프 PR.

- **제약**
  - `gh` / `git push` 는 **항상** `env -u GITHUB_TOKEN`.
  - actionlint 는 `-shellcheck= -pyflakes=` 로만. 바이너리 `/tmp/actionlint-v1.7.12/actionlint`.
  - **PR 게이트**: 커밋마다 `VERDICT_STALE`. 블로커 0이면 `--round 2` 는
    `ROUND_TRANSITION_INVALID` → 새 스냅샷에는 `begin --round 1`. **수정은 묶어서 한 번에**,
    CRITICAL/HIGH 가 아닌 지적은 고치지 말고 PR 본문에 기록해야 루프가 끊긴다.
    게이트는 `gh pr create` 에만 걸리므로 **PR 을 연 뒤 push 하면** 라운드 없이 반영 가능.
  - 호스트 부하 높음(load ~23, 가용 메모리 ~5GB). 전체 스위트 13~16분.
  - 파이프라인 끝 `$?` 는 pytest 가 아니라 `tail`/`tee` 의 exit. **요약 줄로 판정할 것.**
  - 리뷰어 프롬프트에 **예산 상한과 확인 항목**을 명시할 것(무제한이면 전체 스위트를 돌리려 한다).

- **열린 PR/이슈**: **PR #167**(이 작업). 닫을 이슈 #162, #158.
  이 작업에서 새로 생성: **#165, #166**.
  기타: #163 #159 #157 #156 #154 #152 #150 #145 #133 #128 #125 #106 #93 #83.

## Codex 가 이어받기 좋은 독립 작업 2건

### #165 — canonicalizer 가 `Still open` 을 섹션 끝에 붙인다
`opencode-auto-review.yml:4938-4943`. 앵커 검증 실패 + 모델이 `Still open` 미발행이면
섹션 배열 끝에 `push`. 직렬화는 배열 순서대로 렌더(`:4955`)하므로 발행 본문이
`New findings → Retracted → Still open` 이 되고, v1.72 의 꼬리 절단이 **active set 을 먼저 먹는다.**
바로 위 주석(`:4916`)이 그 경로를 "normal" 이라 부른다.
실측: 실제 canonicalizer 출력 순서 확인 + `clip_review` 통과 시 **open 블록 6개 중 0개 생존**.
**한 줄 수정 아님** — `entry.sectionIndex` 가 위치 인덱스이고 렌더링이 그 매칭에 의존해,
중간 `splice` 는 이후 모든 블록을 조용히 다른 섹션으로 민다. 이름 기반 렌더링 전환이 선호.

### #166 — `run` 블록 shell 문법 게이트 부재
`test-fleet-tools.yml:88` 이 `-shellcheck=` 로 shell 검사를 끈다(의도는 타당 — 호스트 의존 제거).
결과적으로 `run:` 의 shell 문법을 보는 게이트가 없다. 이번 작업에서 실제로 awk 프로그램
(작은따옴표 하나로 감싼 문자열) 안에 아포스트로피를 넣어 스텝 전체가 깨졌는데
**actionlint 는 exit 0** 이었고, 잡아낸 건 그 스텝을 실행하는 통합 테스트 하나뿐이었다.
제안: 모든 `run` 블록 추출 → `${{ }}` 중립 토큰 치환 → `bash -n`.

## 산출물 위치 (scratchpad)

| 파일 | 내용 |
|---|---|
| `pr172-body.md` | PR 본문 (측정치·정정 반영 완료) |
| `commit-round3.txt` / `commit-round4.txt` | fix3 / fix4 커밋 메시지 |
| `notion-candidates-v172.md` | Notion 저장 후보 6건 |
| `tribunal-b1a4cbd/`, `tribunal-bc4ddd0/` | 트리뷰널 기록 보존 |
| `issue-1.md`, `issue-2.md` | #165 / #166 본문 |
| `revertarm/` | revert 암 실측용 격리 사본 |

## 이 세션에서 확인된 방법론 (반복 실수 방지)

1. 파이프라인 끝 `$?` 로 통과 판정 금지 — 실제로 `1 failed` 인데 `EXIT=0` 이 나왔다.
2. 서브에이전트 최종 보고는 유실이 아니다. `tasks/<id>.output` 은 심볼릭 링크이고 원문은
   `~/.claude/projects/<proj>/subagents/agent-<id>.jsonl` 의 마지막 assistant 메시지.
3. 릴리스 사다리: 새 라인은 rung 추가. hunk 의 `current` 가 stale 하면 **조용한 no-op** 이 되고
   잡아내는 건 라운드트립 digest 체크뿐. hunk 경계에 주석을 넣으면 이웃 hunk 의 인접성이 깨진다.
4. 리뷰어 주장도 반증 확인 후 수용 — 이번에 A 의 전제는 옳았고(다른 워크플로 소속 코드였음),
   A-R1-003 은 반증에 성공해 기각했다(`reserved` 가 거부가 아니라 strip 필터라 앵커링하면
   near-miss 스푸핑이 열린다).
