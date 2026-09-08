# Handoff — automation

_2026-09-08 · **v1.72 릴리스 완료, 플릿 롤아웃 대기** · main = `b18f992` · `automation_ref = v1.71`_

> 도구 비의존으로 쓴다. 다음 세션이 Codex 든 Claude Code 든 이것만 읽고 이어갈 수 있어야 한다.

## 체크포인트

- **완료·검증됨**
  - PR **#167** 머지 (`b18f992`), 브랜치 삭제. #162·#158 종료.
  - 태그 **`v1.72`** 발행·검증: 리모트 `refs/tags/v1.72` → `a7b033cd`(annotated),
    peeled → `b18f99256bc2bd41e4fc70c3025805b7682217a0`.
    `python3 -m scripts.verify_workflow_release --ref v1.72 --expected-commit b18f9925…` → **PASS**.
  - CI 전체 통과(전체 스위트 + actionlint). 로컬 실측 3122 passed / 48 subtests.
  - 트리뷰널 5라운드, 최종 `pass / blocking_count 0`.
  - Notion 저장 6건(KB 5 + DecisionLog 1).

- **다음 구체 액션 1개**
  **17타깃 플릿 롤아웃.** 절차 전문은 `docs/workflow-fleet-rollout.md` — 그대로 따르면 된다.
  v1.72 고유 값만 바꾼다:
  ```
  export AUTOMATION_RELEASE_ROOT=/tmp/automation-v1.72-public
  export FLEET_WORKSPACE=/tmp/automation-v1.72-fleet
  export ACTIONLINT=/tmp/actionlint-v1.7.12/actionlint
  ```
  `--ref v1.72` 로 `--mode plan` → 매니페스트 확인 → `--mode publish`.
  이어서 `automation_ref` 기본값을 v1.71 → v1.72 로 올리는 PR.

- **먼저 결정할 것 (롤아웃 전)**

  v1.72 는 **알려진 결함 #165 를 안고 나간다.** 롤아웃하면 17개 저장소가 그 코드를 실행한다.
  두 갈래 중 하나를 고르고 시작할 것:

  1. **#165 를 먼저 고쳐 v1.73 으로 묶어 롤아웃** — 배포 1회로 끝나지만 릴리스가 늦어진다.
  2. **v1.72 를 지금 롤아웃하고 #165 는 v1.73 으로** — #162/#158 이익을 먼저 얻지만,
     예산이 바인딩되는 PR 에서는 active set 이 먼저 잘릴 수 있는 상태로 운영된다.

  판단 근거: #165 는 예산이 실제로 바인딩될 때만(이전 리뷰 20,000자 초과) 드러난다.
  빈도는 측정된 바 없다 — 리뷰어도 "메커니즘은 주장하되 빈도는 주장하지 않는다"고 명시했다.

## 이 작업에서 생성한 이슈 2건 (독립 착수 가능)

### #165 — canonicalizer 가 `Still open` 을 섹션 끝에 붙인다
`opencode-auto-review.yml` 의 앵커 검증 실패 경로에서, 모델이 `Still open` 을 발행하지 않았으면
섹션 배열 **끝에** `push` 한다. 직렬화가 배열 순서대로 렌더하므로 발행 본문이
`New findings → Retracted → Still open` 이 되고, v1.72 의 꼬리 절단이 **active set 을 먼저 먹는다.**
바로 위 주석이 그 경로를 "normal" 이라 부른다.
실측: 실제 canonicalizer 출력 순서 확인 + `clip_review` 통과 시 **open 블록 6개 중 0개 생존**.
**한 줄 수정 아님** — `entry.sectionIndex` 가 위치 인덱스이고 렌더링이 그 매칭에 의존해,
중간 `splice` 는 이후 모든 블록을 조용히 다른 섹션으로 민다. 이름 기반 렌더링 전환이 선호.

### #166 — `run` 블록 shell 문법 게이트 부재
`test-fleet-tools.yml` 이 `-shellcheck=` 로 shell 검사를 끈다(의도는 타당 — 호스트 의존 제거).
결과적으로 `run:` 의 shell 문법을 보는 게이트가 없다. 이번 작업에서 실제로 awk 프로그램
(작은따옴표 하나로 감싼 문자열) 안 아포스트로피 하나로 스텝 전체가 깨졌는데
**actionlint 는 exit 0** 이었고, 잡아낸 건 그 스텝을 실행하는 통합 테스트 하나뿐이었다.
제안: 모든 `run` 블록 추출 → `${{ }}` 중립 토큰 치환 → `bash -n`.

## 제약 (반복해서 발을 걸었던 것들)

- `gh` / `git push` 는 **항상** `env -u GITHUB_TOKEN`.
- actionlint 는 `-shellcheck= -pyflakes=` 로만. 바이너리 `/tmp/actionlint-v1.7.12/actionlint`.
- **파이프라인 끝 `$?` 는 pytest 가 아니라 `tail`/`tee` 의 exit.** 실제로 `1 failed` 인데
  `EXIT=0` 이 나온 적이 있다. **판정은 요약 줄(`N passed`)로 한다.**
  진행 관측이 필요하면 `| tail` 이 아니라 `| tee` (tail 은 끝날 때까지 아무것도 안 내보내
  행처럼 보인다).
- **릴리스 사다리**: 새 라인은 rung **추가**이지 이전 rung 핀 수정이 아니다. hunk 의 `current`
  가 stale 하면 **조용한 no-op** 이 되고, 잡아내는 건 라운드트립 digest 체크뿐.
  hunk 경계에 주석을 넣으면 이웃 hunk 의 인접성 컨텍스트가 깨진다.
- **pre-pr-tribunal 게이트**: 커밋마다 `VERDICT_STALE`. 블로커 0이면 `--round 2` 는
  `ROUND_TRANSITION_INVALID` → 새 스냅샷에는 `begin --round 1`.
  **수정은 묶어서 한 번에**, CRITICAL/HIGH 가 아닌 지적은 고치지 말고 PR 본문에 기록해야
  루프가 끊긴다(이번에 몰라서 5라운드를 돌았다).
  게이트는 `gh pr create` 에만 걸리므로 **PR 을 연 뒤 push 하면** 라운드 없이 반영 가능.
  리뷰어 프롬프트에는 **예산 상한과 확인 항목을 명시**할 것(무제한이면 전체 스위트를 돌린다).
- 호스트 부하 높음(load ~23, 가용 메모리 ~5GB). 전체 스위트 13~16분.
- 이 저장소의 자체 리뷰 워크플로는 **설정상 비활성**이다
  (`opencode-auto-review is disabled in .github/workflow-config.yml`, `ZHIPU_API_KEY` 미설정).
  PR 에 리뷰가 안 붙는 것은 고장이 아니다.

## 열린 이슈

**#165, #166**(이 작업에서 생성) · #163 #159 #157 #156 #154 #152 #150 #145 #133 #128 #125 #106 #93 #83
