# 커밋 형식 통일 설계

- 날짜: 2026-10-19
- 범위: 커밋 메시지 (Issue·PR은 후속, 리뷰는 보류)
- 정본 위치: 이 저장소(automation) — Change Evidence Contract v1과 fleet baseline이 여기 있다

## 1. 의도와 이유

저장소마다 커밋 모양이 달라서 사람이 읽기 어렵고, 여러 저장소의 커밋을 모아 만드는
redmine 주간보고도 같은 방식으로 읽기 어렵다. 어느 저장소든, Claude Code가 썼든 Codex가 썼든
커밋이 **같은 모양**이고 **한국어로 읽히게** 한다.

형식을 새로 만드는 일이 아니다. 계약 v1과 작성 스킬은 이미 있고 Claude·Codex가 같은 것을 쓴다.
빠진 것은 (1) 언어 규칙, (2) 저장소마다 갈라진 제목 규칙과 예외, (3) 스킬을 거치지 않은 위반 커밋을
막는 수단이다.

출발점은 automation#194다. 2026-09-30 주간보고 점검에서 git 근거만 있는 항목의 설명이 부족해 계약 v1을
만들었고, 그 Issue는 "기존 저장소에 대한 점진적 rollout 및 audit 방법"을 범위로 두었지만 CI 롤아웃까지는
이어지지 않았다. 이 설계는 그 후속이다(§10-11).

## 2. 목표 · 비목표 · 반목표

**목표**
- origin이 `github.com/jhw7500/*`인 저장소의 새 커밋이 계약 v1 구조 + conventional 제목 + 한국어 내용으로 작성된다.
- 언어 규칙이 저장소별 지침이 아니라 중앙(계약·작성 스킬)에 있다.
- Claude·Codex가 만드는 위반 커밋은 **커밋 시점에 막히고**, 사용자가 승인하면 그 1건만 통과한다.
- 훅이 보지 못하는 커밋(사람이 직접 한 커밋, 웹 커밋 등)을 포함해 fleet 저장소의 새 커밋에 대한 형식
  위반이 CI에서 **보인다**(audit).
- tribunal 수정 커밋도 같은 형식이다.

**비목표**
- CI에서 머지를 막는 것(required check·`mode: enforce`). 차단은 커밋 시점 한 곳에서만 한다.
- 과거 커밋 재작성.
- 언어를 기계로 검사하는 것.
- Claude/Codex 구분 트레일러 통일 (주간보고는 작성 도구를 쓰지 않는다).
- Issue·PR 형식(이 작업 뒤 같은 방식으로 후속), 리뷰 형식(보류).

**반목표** (기술적으로 돌아가도 실패로 보는 것)
- 계약 필드 제목이나 `type(scope)`까지 한국어로 바꿔 redmine 파서·validator·commitlint가 깨지는 것.
- audit 검사가 빨간 체크로 남아 사실상 차단처럼 작동하거나, 잡음 때문에 무시되는 것.
- 저장소마다 commitlint 설정 사본이 다시 갈라지는 것.
- 커밋 시점 게이트와 CI audit가 서로 다른 규칙으로 판정하는 것.
- 승인이 커밋 1건을 넘어 지속 허용으로 번지는 것.
- 게이트가 jhw7500 밖 저장소(외부 fork, 회사 GitLab 등)의 커밋까지 막는 것.

## 3. 현재 상태 (근거는 §10)

| 사실 | 근거 |
|---|---|
| 계약 v1 커밋 필드: 제목 ≤72자, `Contract version`→`Why`→`Changes`→`Validation`→`References` | `docs/change-evidence-contract-v1.md` "Commit fields" |
| 계약·작성 스킬 어디에도 언어 규칙이 없다. 저장소 지침이 각자 정한다 (`max9296/AGENTS.md:90` 한국어, jhw-notion `AGENTS.md` "관례는 영문 `type(scope): 결과`") | §10-1 |
| Claude(224건)와 Codex 확정분(19건)의 필드 포함·validator 통과율은 거의 같다. 갈리는 것은 트레일러(Codex는 없음)뿐 | §10-2 |
| 도구와 무관한 이탈: jhw-env 한국어 서술형 제목(conventional 0%), `review-fix round N:` 제목 11건, pim-package-jhw 도입 후 필드 없는 커밋 14건 | §10-2 |
| commitlint 설정은 5개 저장소에 있고 3가지 변형. 실제로 도는 곳은 claude-config·personal-ops·pim-package-jhw 3곳. jhw-notion은 `branches: [master]` + `COMMITLINT_ENABLED` 미설정으로 실행 이력 0건 | §10-3 |
| claude-config#151은 같은 결함(변수 부재로 commitlint가 skip)을 claude-config에서만 고쳤고 jhw-notion에는 남아 있다. 그 수정(PR #157)이 넣은 `review-fix` 예외는 이 설계의 결정 3이 걷어낸다 | §10-11 |
| 그 3곳 모두 branch protection·ruleset이 없다. 기존 commitlint는 required check가 아니어서 머지를 막지 않지만, 실패하면 빨간 체크가 남는다 | §10-8 |
| 커밋 언어를 정한 저장소 지침은 jhw-notion `AGENTS.md:101`, automation `AGENTS.md:83`, max9296 `AGENTS.md:90` 세 곳뿐이다 | §10-9 |
| `git commit`을 검사하는 훅은 아직 없다. 막은 뒤 일회성 사람 승인으로 통과시키는 구조(`pr_override_grant.py`)와 Codex PreToolUse 어댑터(`codex_hook.py`)는 claude-config tribunal 훅에 이미 있다 | §10-10 |
| `validate-change-evidence` action(`mode: audit` 지원)은 있지만 이 저장소의 워크플로와 baseline 트리 어디에서도 쓰지 않는다 | §10-4 |
| redmine 주간보고는 `parseChangeEvidence("commit", subject+body)`로 필드를 꺼낸다. ` ### `와 `### ` 둘 다 읽고, 필드 없는 커밋은 필드 0개 | §10-5 |
| tribunal 스킬은 "Create one `review-fix round N` commit"이라고만 한다. hooks·scripts에 이 제목 형식에 의존하는 코드는 없다 | §10-6 |
| fleet 대상은 `scripts/workflow-config.json`의 16개 저장소. jhw-env·imx-hantro·jhw-project-registry는 fleet 밖 | §10-7 |

## 4. 사용자가 정한 것

1. **언어**: 사람이 읽는 내용은 한국어, 기계가 읽는 부분은 영문.
   - 한국어: 제목의 결과문(콜론 뒤), 각 필드의 내용.
   - 영문 유지: `type`, `scope`, 필드 제목(`### Why` 등), 트레일러, 명령·경로·식별자·오류 코드.
2. **적용 범위**: origin이 `github.com/jhw7500/*`인 저장소 모두(공개 저장소 포함)에 예외 없이 같은 규칙.
   외부 upstream 저장소는 그 저장소의 언어 관례를 따른다(2026-10-19 계획 1 리뷰 뒤 사용자 결정).
3. **tribunal 수정 커밋**: 일반 형식으로 흡수한다. 제목은 conventional `type(scope): 한국어 결과`(2026-10-19 사용자 결정: `fix`로 한정하지 않음), `review-fix round N`과
   finding ID는 본문(`Why`)에 남긴다. commitlint 예외를 두지 않는다.
4. **강제 수준**: Claude·Codex가 `git commit`을 실행하는 시점에 막고, 사용자가 승인하면 그 커밋 1건만
   통과한다. CI는 audit(보고)만 하고 머지를 막지 않는다. (처음에는 "차단 없음"으로 정했다가
   "커밋 시점 차단 + 승인 통과"로 바꿨다.)
5. **제목 규칙 엔진** (2026-10-23 사용자 결정, §11): commitlint 대신 automation의 Python 공유 모듈 하나로
   제목을 검사하고, 게이트와 CI가 같은 automation 커밋의 모듈을 쓴다. 현재 commitlint가 쓰는 오류 규칙과
   기본 무시 목록은 그대로 옮긴다.

예시:

```text
fix(deploy): 일시적 프로세스가 검증된 배포를 막지 않게 한다

 ### Contract version
v1

 ### Why
검증 직후 inventory 샘플 1회가 호스트 PID 변동에 걸려
정상 배포가 recovery_blocked로 끝났다.

 ### Changes
- reacquire()가 샘플 창 안에서 재시도한다.

 ### Validation
- node --test scripts/test-runtime-deploy.mjs: (실행 결과)

 ### References
- Refs #206
```

## 5. 설계

### A. 형식 규칙 — 정본 (automation)

- `docs/change-evidence-contract-v1.md`의 "Commit fields"에 **검증하지 않는 작성 관례**로 언어 규칙을
  추가한다(SHOULD). validator 동작·필드·순서·requiredness는 바꾸지 않으므로 v1 범위의 산문 보강으로
  본다(§8 판단 1).

### B. 작성 경로 — 형식을 지키게 하는 주 수단

형식은 작성 시점에 결정된다. 게이트(E)에 막히기 전에 처음부터 맞게 쓰도록 Claude·Codex가 공유하는
경로를 모두 고친다.

1. **jhw-notion `skills/claude/commit.md`**: 언어 규칙과 한국어 예시를 넣는다.
   `node scripts/sync-codex-skills.mjs`로 `skills/codex/jhw-commit/`을 같은 커밋에 갱신한다.
2. **저장소별 언어 지침** (§10-9 열거 결과): 새 규칙과 어긋나는 문장은 두 곳이다.
   - jhw-notion `AGENTS.md:101` "관례는 영문 `type(scope): 결과`다."
   - automation `AGENTS.md:83` "저장소 관례는 영문 `type(scope): 결과` 또는 `type: 결과`이고 …"
   - max9296 `AGENTS.md:90` "커밋 메시지 … 한국어로 쓴다"는 새 규칙과 맞으므로 두고, 다른 fleet
     저장소는 고칠 문장이 없다.
3. **claude-config tribunal 스킬** (`skills/pre-pr-tribunal/SKILL.md:33`): "Create one `review-fix round N`
   commit"을 "conventional 제목 + 계약 필드로 커밋하고 `review-fix round N`과 finding ID를 `Why`에
   남긴다"로 바꾼다. 전역 지침의 `git log --grep='review-fix round'` 집계는 본문도 검색하므로 그대로
   동작한다.
4. **전역 지침** (Claude `~/.claude/global-guidance.md`, Codex `~/.codex/AGENTS.md`의 "Change Evidence
   작성 활성화" 절): 스킬을 거치지 않은 커밋도 언어 규칙을 따르도록 한 줄 추가한다(§8 판단 2).
   두 파일의 정본 저장소는 claude-config로 추정하며, 빌더가 확인한다.

### C. 감사 경로 — 위반을 보이게 (automation → fleet)

1. **재사용 워크플로** (예: `.github/workflows/commit-evidence.yml`): 대상 커밋마다
   - 제목을 **automation이 가진 공유 규칙 모듈**로 검사한다(호출 시 pin된 automation 커밋에서 모듈을
     읽는다). 소비자 저장소 설정 사본을 쓰지 않는다. (2026-10-23 개정: commitlint 대신 Python 모듈, §11)
   - `validate-change-evidence`를 `kind: commit`, `mode: audit`로 돌린다.
   - 결과를 커밋별 표(SHA, 제목, commitlint 결과, validator finding code)로 step summary에 남긴다.
   - **잡은 실패하지 않는다**(차단은 E에서만). 위반은 summary와 warning annotation으로만 보인다.
2. **대상 커밋**: PR의 커밋들, 그리고 기본 브랜치로 직접 push된 커밋. merge 커밋은 제외.
3. **통일 제목 규칙** (2026-10-23 개정, §11): 현재 type 목록(`feat|fix|chore|refactor|docs|test|build|ci|perf|style`)
   유지, 제목 길이는 계약과 같은 72자(validator의 `commit-title-too-long`이 맡는다), 본문·footer 줄 길이 제한
   해제(Validation의 명령을 줄바꿈하지 않기 위해 — claude-config에서 이미 이 이유로 해제했다),
   `subject-case` 해제(한국어), `review-fix` 예외 없음. config-conventional에서 상속하던 오류 규칙
   (`type-case`, `type-empty`, `subject-empty`, `subject-full-stop`, `header-trim`)과 commitlint 기본 무시 목록은
   유지한다.
4. **baseline caller와 catalog**: `examples/baseline-workflows/.github/workflows/`에 caller를 추가하고
   `scripts/workflow-catalog.json`에 등록한다. 모든 fleet 저장소에 같은 규칙이 목적이므로 required
   caller로 둔다(§8 판단 3).
5. **릴리스·롤아웃**: 기존 fleet 절차(`docs/workflow-fleet-rollout.md`의 plan → PR → canary → audit)를
   그대로 따른다.

### D. 정리

- 소비자 저장소의 저장소별 `commitlint.yml`·`commitlint.config.mjs`는 **새 caller가 들어가는 시점에 함께
  제거**한다. 한 저장소에서 기존 commitlint(실패 시 빨간 체크, `review-fix` 예외 등 다른 설정)와 새
  audit가 동시에 도는 기간을 만들지 않는다(§8 판단 6). fleet 렌더러가 비관리 파일을 지울 수 없으면
  caller 롤아웃 직전에 저장소별 PR로 지운다. 방법은 빌더가 정한다.
- 순서: B(작성 경로) → E(커밋 시점 게이트) → C+D(감사 배포와 기존 commitlint 제거). 작성 경로가 먼저
  고쳐져야 게이트가 정상 커밋까지 막지 않고, audit도 처음부터 잡음으로 덮이지 않는다.

### E. 커밋 시점 게이트 — 위반을 막고 승인으로 통과 (claude-config)

1. **가로채는 지점**: Claude Code와 Codex의 PreToolUse(Bash) 훅이 `git commit` 실행을 가로챈다. tribunal
   훅의 셸 명령 해석(`hooks/pre_pr_tribunal/shell_scan.py`)과 Codex 어댑터(`codex_hook.py`) 방식을
   재사용한다.
2. **적용 저장소**: origin이 `github.com/jhw7500/*`인 저장소만. 그 밖의 저장소는 그대로 통과한다
   (§8 판단 7).
3. **검사 규칙**: CI audit(C)와 **같은 규칙**으로 검사한다 — 계약 validator(`kind: commit`)와 통일
   제목 규칙 모듈(§11). 게이트와 CI가 같은 출처(pin된 automation 커밋)의 규칙·validator를 쓰게 해
   판정이 갈라지지 않게 한다. 언어는 검사하지 않는다.
4. **메시지를 확정할 수 없는 커밋**: 편집기를 여는 `git commit`처럼 메시지를 미리 알 수 없는 명령은
   ~~막고 `--file` 사용을 안내한다~~ 통과시키고 경고만 남긴다. CI audit가 결과를 보여 준다(2026-10-23 개정,
   §12). merge·revert처럼 Git이 메시지를 만드는 경우는 통과시킨다.
5. **승인**: 위반이면 막고 finding을 보여 준다. 사용자가 승인하면 그 메시지·저장소·HEAD에 묶인
   **일회성 승인**으로 그 커밋 1건만 통과하고, 승인은 쓰는 순간 소비된다. 승인 기록 방식과 보증
   수준은 `pr_override_grant.py`를 따른다.
6. **보지 못하는 커밋**: 사람이 터미널에서 직접 한 커밋, 프로그램 내부에서 실행되는 커밋(jhw-control
   registry 등), GitHub 웹 커밋은 훅을 거치지 않는다. 이것들은 CI audit(C)가 보여 준다.

## 6. 제약

- 직접 고치는 저장소는 3곳(automation, jhw-notion, claude-config)이고, 감사 caller는 fleet 롤아웃으로
  16개 저장소에 들어간다. 저장소별 언어 지침 수정은 이 3곳 안에서 끝난다(§10-9). 각 저장소의 PR·
  tribunal·리뷰 절차를 따른다. automation의 `.github/workflows/**`와 claude-config의 `skills/**`·`hooks/**`는
  tribunal floor 100(iterative)이므로 PR을 작게 나눈다.
- 계약 `v1`의 field names/order, requiredness, sentinel, finding 의미를 바꾸지 않는다(바꾸면 v2).
- redmine 주간보고 파서(`lib/change-evidence.js`)가 그대로 읽을 수 있어야 한다.
- 커밋 시점 게이트(E)는 기존 tribunal 훅의 `gh pr create` 판정을 바꾸지 않는다. 훅은 claude-config의
  설치 경로로 배포되며, 배포 승인은 별도다.
- jhw-notion 스킬 변경이 live에 반영되는 경로(심링크 즉시 반영 vs release 배포)를 확인한다.
  배포가 필요하면 배포 승인은 별도다.
- 서브에이전트 구현 시 push·PR·머지·브랜치 전환 금지를 프롬프트에 적는다.

## 7. 빌더에게 맡기는 것

- 재사용 워크플로의 이름, 커밋 범위 계산 방식, summary 표 모양.
- ~~commitlint 실행 방식~~ — 2026-10-23 개정으로 commitlint를 쓰지 않는다(§11). 제목 규칙 모듈의
  내부 구조는 빌더가 정한다.
- 계약 문서에 넣을 언어 규칙 문장의 정확한 위치와 문구.
- 저장소별 commitlint 사본 제거 방법.
- 게이트가 validator·commitlint 규칙을 공급받는 방식(§5-E-3의 "같은 출처" 조건 안에서).
- 게이트가 통과시킬 Git 자동 메시지 목록, 승인 요청 문구.

## 8. 내가 내린 판단 (사용자 확인 대상)

1. 언어 규칙을 계약 v1의 **검증하지 않는 산문 보강**으로 넣는다. "requiredness 변경 = v2" 규칙에
   걸리지 않는다고 봤다. 리뷰에서 v2 사안으로 판정되면 별도 작성 관례 문서로 옮긴다.
2. 게이트(E)는 언어를 검사하지 않으므로, 스킬을 거치지 않는 커밋도 한국어로 쓰도록 전역 지침 두 곳에
   언어 규칙 한 줄을 넣는다.
3. 새 caller를 optional이 아니라 required로 둔다.
4. fleet 밖 저장소(jhw-env, imx-hantro)는 audit 대상이 아니다. 작성 경로(B)는 적용된다.
   jhw-project-registry의 `registry: register …` 자동 커밋은 대상에서 뺀다(fleet 밖이라 자연히 제외).
5. CI audit 잡은 실패하지 않는다. 차단은 커밋 시점 게이트(E) 한 곳에서만 한다.
6. 기존 저장소별 commitlint는 새 caller와 같은 시점에 제거한다. 지금은 required check가 아니라
   머지를 막지 않지만, 실패 시 빨간 체크를 남기고 설정이 통일 규칙과 다르다.
7. 게이트는 origin이 `github.com/jhw7500/*`인 저장소에만 건다. 외부 fork나 회사 GitLab 저장소까지 계약
   형식을 강제하지 않는다.
8. ~~메시지를 미리 알 수 없는 `git commit`(편집기 사용 등)은 통과가 아니라 차단하고 `--file`을 안내한다.~~
   2026-10-23 개정(§12): 통과시키고 경고만 남긴다. 게이트가 확실히 판정할 수 있는 위반만 막는다.

## 9. 후속

- Issue·PR 형식: 계약 v1에 이미 필드가 있고 템플릿(`.github/ISSUE_TEMPLATE/change-evidence.yml`,
  `.github/pull_request_template.md`)도 있다. 같은 방식(언어 규칙 + audit)으로 이어서 한다.
- 리뷰 형식: 보류.
- fleet 밖 저장소의 fleet 편입 여부.

## 10. 근거 목록

1. 언어 규칙 검색: `grep -n -iE '영문|한국어|language|언어|English|Korean' jhw-notion/skills/claude/commit.md automation/docs/change-evidence-contract-v1.md` → 0건.
   `max9296/AGENTS.md:90` "커밋 메시지, 문서, 스크립트·Makefile 주석은 한국어로 쓴다".
2. 커밋 조사(2026-10-19, 12개 저장소, 2026-10-02 이후 non-merge, 저장소당 최대 100건): Claude 표지는
   `^Co-Authored-By: Claude`/`^Claude-Session:` 트레일러, Codex 확정은 `~/.codex/sessions`의 commit 출력
   대조. validator: `python3 -I scripts/validate_change_evidence.py --kind commit --expected-version v1 --format json --path <msg>`.
   원자료와 스크립트는 세션 scratchpad(영속 아님).
   직접 재확인: wlan-package `b13ed63`(Codex, 트레일러 없음, 비conventional 제목, 필드 있음),
   pim-package-jhw `6d5f2d3`(Claude, 2026-10-08, 필드 없음), redmine `0438287`(`###` 공백 없음, validator valid).
3. commitlint 실행: `gh run list -R jhw7500/<repo> --workflow=commitlint.yml --limit 50` — claude-config
   success 34/failure 16, personal-ops 34/1, pim-package-jhw 49/1, jhw-notion 0건, max9296 워크플로 없음.
   `gh api repos/jhw7500/jhw-notion/actions/variables/COMMITLINT_ENABLED` → 404.
4. `git grep -n -l 'validate-change-evidence' -- .github/workflows examples` → 0건 (이 저장소, `bd7ceb8`).
5. redmine `lib/collector.js:888` `parseChangeEvidence("commit", `${subject}\n\n${body}`)`. 위 세 커밋을
   파서에 넣은 결과: `b13ed63`·`0438287` 필드 5개, `6d5f2d3` 필드 0개.
6. claude-config `skills/pre-pr-tribunal/SKILL.md:33` "Create one `review-fix round N` commit only if the new
   request includes committing." / `git grep -n 'review-fix round' -- hooks scripts` → 0건
   (같은 grep이 `.github`에서는 4건).
7. `scripts/workflow-config.json` `repos`: gstApp, max9296, imx-vpu, wlan-driver-v2, wlan-bridge, wlan-package,
   pim-package-jhw, wlan-opc, pcap-analyzer, wpa-supplicant, sc16is7xx, pim-check, redmine, jhw-notion,
   personal-ops, claude-config.
8. `gh api repos/jhw7500/<r>/branches/<default>/protection` → claude-config·personal-ops·pim-package-jhw 모두
   "Branch not protected". `gh api repos/jhw7500/<r>/rules/branches/<default>` → 규칙 없음. 같은 ruleset
   조회가 automation `main`에서는 `deletion,non_fast_forward,pull_request`를 반환했다(조회 능력 확인).
9. fleet 16개 저장소의 GitHub 기본 브랜치 `AGENTS.md`/`CLAUDE.md`와 로컬 `*/AGENTS.md`·`*/CLAUDE.md`
   (`/home/jhw/ai/opencode/projects` 1단계)를 `영문|영어|English|한국어로|Korean` ∧ `커밋|commit|제목|메시지|type(scope)`
   로 검색. 하위 문서·다른 브랜치는 검색하지 않았다.
10. claude-config `git grep -n -I -lE 'git commit|commit-msg' -- hooks` → 0건.
    `hooks/pre_pr_tribunal/pr_override_grant.py:1` "One-shot, snapshot-bound human approval for a terminal
    non-pass verdict." / `hooks/pre_pr_tribunal/codex_hook.py:2` "Codex PreToolUse adapter for tribunal PR
    gating."
11. 기존 Issue 열거: automation·jhw-notion·claude-config에서 `gh issue list --state all --search` 로
    "commitlint", "change evidence", "commit message", "커밋"을 검색. 같은 주제의 열린 Issue는 없다.
    관련 닫힌 Issue: automation#194(계약 v1 정의, 주간보고 점검이 계기, rollout·audit를 범위에 둠),
    claude-config#151(commitlint skip 결함, PR #157로 `review-fix` 예외 추가), claude-config#175(전역
    activation), jhw-notion#165(작성 스킬 통합).

## 11. 개정 (2026-10-23) — 제목 규칙 엔진

**결정**: 통일 제목 규칙을 commitlint 설정이 아니라 automation의 Python 공유 모듈 하나로 구현한다. 계약
validator와 이 모듈을 함께 부르는 실행기를 CI audit와 커밋 시점 게이트가 같은 automation 커밋에서 쓴다.
fleet의 commitlint 파일은 걷어낸다. (설계 메모 → critic 판정 "조건부 수용" → 사용자 승인)

**이유**: 게이트는 `/usr/bin/python3` 3.10 훅이고, claude-config 훅과 automation `.github`·`examples` 어디에도
Node·commitlint 사용 이력이 없다. commitlint를 쓰면 게이트와 CI 양쪽에 Node 런타임과 npm 패키지 고정을 새로
들여와야 하고, JS 설정과 Python validator 두 엔진의 판정을 따로 맞춰야 한다(반목표 "게이트와 CI가 서로 다른
규칙"). 제목 규칙을 validator에 넣는 안은 계약 v1의 requiredness 변경(= v2)이라 택하지 않았다.

**규칙** (현재 commitlint가 실제로 쓰는 것을 옮긴다):
- 헤더 문법: conventionalcommits preset 7.0.2 `headerPattern` `^(\w*)(?:\((.*)\))?!?: (.*)$` (JS `\w`는 ASCII).
- 오류 규칙: `type-enum`(위 10종), `type-case`(소문자), `type-empty`, `subject-empty`, `subject-full-stop`(`.`),
  `header-trim`. 72자 제한은 validator의 `commit-title-too-long`만 맡는다(중복 finding 방지).
- 뺀 것: `header-max-length` 100(→ validator 72), `body/footer-max-line-length`(해제), `subject-case`(해제),
  경고 등급 `body-leading-blank`·`footer-leading-blank`, `review-fix` 예외.
- 자동 메시지(commitlint `@commitlint/is-ignored` 19.8.1 기본 목록): 제목 규칙과 validator를 모두 건너뛴다.
  목록은 모듈 안에 두어 게이트와 CI가 같은 목록을 쓴다.

**critic이 낸 위험과 대응** (계획 2·3에 반영):
1. 규칙 집합이 말없이 줄어든다 → 위 규칙 목록을 1차 출처에서 옮기고, commitlint가 실제로 판정한 3개
   저장소(claude-config·personal-ops·pim-package-jhw)의 커밋 이력으로 판정 동등성을 확인한다.
2. 규칙의 소유·버전이 없다 → 모듈은 접두 문법과 위 규칙만 맡고 `RULES_VERSION`을 둔다. 정본 산문은
   `docs/commit-title-rules.md`. ~~모듈·실행기·validator를 릴리스 인벤토리에 넣는다~~ 넣지 않는다(§12).
   소비자는 커밋 SHA로 pin하므로 바이트는 고정되고, 봉인하면 이후 validator 수정마다 릴리스 라인이 필요해진다.
3. pin 어긋남과 인터프리터 하한 → 게이트 사본은 automation 커밋 SHA가 아니라 파일 digest로 비교한다.
   게이트 차단 메시지와 CI summary가 automation SHA와 규칙 digest를 출력한다. automation CI에 Python 3.10
   대상 테스트를 넣는다(계획 3).

**범위 밖**: redmine `lib/collector.js`의 커밋 type 파싱(다른 type 목록, 대소문자 무시)은 주간보고 분류용
세 번째 소비자로, 이 규칙과 맞추지 않는다.

**근거**: config-conventional 19.1.0 `lib/index.js` 규칙 목록, is-ignored 19.8.1 `lib/defaults.js`,
conventional-changelog-conventionalcommits 7.0.2 `parserOpts.js:7` (wagoid/commitlint-github-action v6.2.1
`package.json`의 `^19.1.0` 의존성에서 출발, 2026-10-23 npm 레지스트리·unpkg에서 받음).

## 12. 개정 (2026-10-23) — 개발 마찰 최소화

**사용자 지시**: "너무 깐깐하게 해서 실제 개발을 어렵게 만들지는 마."

**원칙**: 커밋 시점 게이트는 **확실히 판정할 수 있는 명백한 위반만** 막는다. 판정할 수 없거나 게이트 자체가
실패하면 커밋을 통과시키고 경고만 남긴다. 놓친 것은 CI audit가 보여 준다(차단 없는 안전망). 승인으로 1건만
통과시키는 규칙(§4-4)은 그대로 둔다.

**바뀐 것**:
- 메시지를 알 수 없는 커밋(편집기, 해석하지 못한 동적 치환, `bash -c` 안의 커밋)은 통과 + 경고(§5-E-4, §8 판단 8).
- Claude Code의 기본 형태 `git commit -m "$(cat <<'EOF' … EOF)"`(따옴표로 감싼 heredoc 구분자)는 정적 텍스트로
  꺼내 검사한다. 이 형태 때문에 `--file`로 다시 쓰게 하지 않는다.
- `-m` 메시지 위반도 승인할 수 있다(메시지 해시로 바인딩). 파일로 다시 쓰게 하지 않는다.
- 게이트 내부 오류는 통과 + 경고(fail-open).
- 차단 사유에는 무엇이 빠졌는지와 바로 쓸 수 있는 고침 방법을 짧게 준다.
- 규칙 파일·validator를 릴리스 인벤토리에 봉인하지 않는다(§11 대응 2 철회).
