# AGENTS.md — automation

Claude Code와 Codex가 공용으로 읽는 저장소 지침이다. 루트 `CLAUDE.md`는 이 파일을
`@AGENTS.md`로 import하는 셔임이며 내용은 여기에만 둔다. 일반 규칙(응답 언어, 검증 위생 등)은
각 도구의 전역 설정에 있으므로 여기에는 **이 저장소에서만 성립하는 사실**만 적는다. 사실의
정본은 아래 「정본 문서」이고, 어긋나면 그 문서를 따르고 이 파일을 고친다.

## 무엇인가

여러 소비자 저장소가 공유하는 GitHub Actions 자산의 중앙 저장소다.

- `.github/workflows/` — 재사용 워크플로. Claude(`claude.yml`, `claude-code-review.yml`),
  Gemini(`gemini-*.yml`), OpenCode(`opencode*.yml`) 리뷰·대화와 `auto-rereview-request.yml`.
  `_self-*.yml`은 이 저장소 자신을 위한 로컬 caller이고, `test-fleet-tools.yml`은 CI다.
- `.github/actions/` — 공용 composite action. 리뷰 예산 원장(`review-invocation-budget`),
  변경 증거 검증(`validate-change-evidence`), 리뷰 정책·diff 준비·OpenCode 복구 등.
- `examples/baseline-workflows/.github/` — 소비자 caller의 유일한 정본 트리. fleet 도구가
  이 트리를 렌더링해 소비자에 PR로 넣는다. 수동 복사하지 않는다 (`examples/baseline-workflows/README.md`).
- `scripts/` — fleet 롤아웃·감사(`rollout_workflow_fleet.py`, `audit_workflow_fleet.py`),
  릴리스 검증(`verify_workflow_release.py`, `workflow_release_inventory.py`), 증거 검증기
  (`validate_change_evidence.py`), 카탈로그(`workflow-catalog.json`)와 fleet 설정(`workflow-config.json`).
- 소비자는 릴리스 태그가 가리키는 40자리 커밋에 pin한다. 태그 텍스트는 사람이 읽는 식별자일 뿐이다.

## 명령

모든 명령은 저장소 루트에서 실행한다.

```bash
python3 -m pytest -q -p no:cacheprovider tests/test_change_evidence.py   # 대상 테스트 (예)
python3 -m pytest -q -p no:cacheprovider                                 # 전체 스위트
```

- 로컬에서는 **대상 테스트와 되돌리기 확인만** 돌린다. 전체 스위트(약 20~25분, 2026-10-06 실측 19~24분)는
  PR CI `.github/workflows/test-fleet-tools.yml`의 `pytest -q`가 돌린다. 같은 job이 워크플로 YAML
  파싱, `scripts/check_workflow_shell_syntax.py`, digest를 검증한 actionlint 1.7.12
  (`-shellcheck= -pyflakes=`)도 실행한다. actionlint 버전·digest의 정본은 그 워크플로다.
- 변경 증거 검증 (종료코드 0 통과, 1 위반, 2 입출력·버전 오류):

```bash
python3 scripts/validate_change_evidence.py --kind commit --expected-version v1 --path <msg-file>
python3 scripts/validate_change_evidence.py --kind pull-request --expected-version v1 --path <body-file>
python3 scripts/validate_change_evidence.py --kind issue --expected-version v1 --path <issue-file>
```

- 릴리스 검증 — 태그와 기대 커밋이 일치하는지 본다 (루트에서 모듈로 실행):

```bash
python3 -m scripts.verify_workflow_release --ref <tag> --expected-commit <40-char-sha>
```

- fleet 도구 — 대상 저장소는 `scripts/workflow-config.json`에 선언된 것만 받는다.
  - `rollout_workflow_fleet.py --mode plan` — 읽기 전용. 렌더링·검증 후 manifest를 쓴다.
  - `rollout_workflow_fleet.py --mode publish --repo <name> --confirm` — 소비자 PR을 만든다.
  - `audit_workflow_fleet.py` — 읽기 전용. 저장소별 `current`·`drift`·`blocked`를 보고한다.
  - 공통으로 `--automation`, `--workspace`, `--ref`를 받고 rollout은 `--actionlint`를 받는다.
    publish는 소비자 저장소를 바꾸므로 사용자가 요청한 경우에만 실행한다. 절차 전체는
    `docs/workflow-fleet-rollout.md`.

## 워크플로·액션 바이트를 바꿀 때 같이 고칠 것

릴리스된 태그의 바이트는 검증기에 봉인되어 있다. 새 바이트는 새 릴리스 라인에 묶는다.
예시는 `git show --stat be8fb11` (v1.83 라인 추가)와 그 커밋 메시지의 "Release binding" 항목이다.

- `scripts/workflow_release_inventory.py` — 새 릴리스 상수와 `release_supports_*` 게이트를 추가한다.
- `scripts/verify_workflow_release.py` — 현재 seal(raw digest와 parsed seal)을 갱신하고, 이전
  라인은 직전 태그에서 측정한 `V<NNN>_*` 상수로 기존 digest를 유지한다.
- `tests/release_fixture_helpers.py` — 과거 릴리스 fixture가 옛 바이트를 복원하는 `restore_pre_v*`
  함수를 추가한다.
- 소비자 caller가 바뀌면 `examples/baseline-workflows/`와 `scripts/workflow-catalog.json`도
  같이 움직인다. 동작 계약이 바뀌면 `docs/workflows/contracts.md`를 고친다.

## 커밋과 PR

- 커밋·PR·이슈 본문은 Change Evidence Contract v1(`docs/change-evidence-contract-v1.md`)을 따른다.
  - 커밋: 결과를 말하는 제목(72자 이하) → 빈 줄 → `Contract version`(값 `v1`), `Why`, `Changes`,
    `Validation`, `References`. 템플릿은 `examples/change-evidence/commit-message.md`.
  - 필드 제목 `### `는 **앞에 공백 한 칸**을 둔다. 기본 cleanup이 0열 `#` 줄을 지우기 때문이다.
    메시지 파일로 커밋한다: `git commit --cleanup=verbatim --file <msg-file>`.
  - PR 본문: `Contract version`, `Summary`, `Changes`, `Validation`, `Impact and risks`,
    `Related issue`. 템플릿은 `.github/pull_request_template.md`.
  - 커밋 제목은 영문 `type(scope): 결과` 형식이다. 최근 `git log`가 예다.
- PR 생성은 pre-PR tribunal 훅을 통과해야 한다. 브랜치 체크아웃 안에서 아래 형식 그대로 실행한다.
  `cd … &&` 결합, `--head`, `--repo`는 쓰지 않는다. PR이 생긴 뒤의 push는 게이트되지 않는다.

```bash
PATH=/usr/bin:/bin /usr/bin/gh pr create --base main --title "<title>" --body-file <body-file>
```

- `main`은 ruleset `protect-main`으로 보호된다(PR 필수, force push 금지). main에 push하지 않는다.

## 리뷰

- 관리형 리뷰(Claude·Gemini·OpenCode)는 `.github/workflow-config.yml`의 `review.auto`가 `false`라
  자동으로 돌지 않는다. 보통은 PR에 `review:request` 라벨을 붙여 요청한다. 다른 요청 경로(수동
  dispatch, 예산 override)와 라벨 조합 규칙은 `docs/workflows/contracts.md`
  「Review controls and external App operation」이 정본이다.
- `@claude` 멘션은 `.github/workflows/_self-claude.yml`이 받아 대화형 Claude 리뷰를 돌린다.
- `@codex review`는 사람 계정이 단 코멘트여야 한다. 봇이 단 멘션은 거부된다.
- 결과는 sticky 코멘트로 제자리 갱신된다. 머리의 `automation-state` JSON을 읽는다. 공통 필드는
  `attempt_status`·`attempt_head`·`successful_head` 등이다. Claude·Gemini(schema 3)는
  `review_execution`과 `accepted_count`·`filtered_count` 같은 품질 필드를 더 내고, OpenCode는
  schema 2를 유지해 이 필드들이 없다. 각 필드가 가질 수 있는 값과 의미는 여기 옮기지 않는다 —
  `docs/workflows/contracts.md`의 state 절이 정본이다. job success만으로 리뷰가 돌았다고
  판정하지 않는다.
- OpenCode는 현재 구독이 없어 `quota_exhausted`로 실패한다. 이것을 리뷰 결과로 읽지 않는다.

## 릴리스와 롤아웃

- 태그는 릴리스 절차로 한 번 만들고 옮기지 않는다. 소비자는 태그가 가리키는 커밋 SHA에 pin한다.
- fleet 롤아웃이 끝나면 `scripts/workflow-config.json`의 `automation_ref`를 올리고
  `tests/test_workflow_catalog.py`의 기대값도 같이 고친다 (예: `chore(workflows): default rollout to v1.83`).
- 롤아웃 후에도 열려 있던 작업 브랜치는 옛 caller pin을 가진다. 그 브랜치 ref로 수동 dispatch하지
  않는다 (`docs/workflows/contracts.md` 「Open work branches after a fleet rollout」).

## 하지 않는 것

- `secrets: inherit`, `secrets.GITHUB_TOKEN` 사용 — `tests/test_workflow_secret_contracts.py`가 막는다.
  기본 토큰은 `github.token` 컨텍스트로 쓴다.
- pin하지 않은 action 참조 — `tests/test_action_pins.py`가 승인된 SHA를 강제한다.
- 태그 이동·재생성, 예산 원장 코멘트의 편집·절단·삭제 (복구도 원장을 그대로 둔다).
- 세션 체크포인트 `HANDOFF.<세션>.md`와 도구 상태 디렉터리(`.omc`, `.omx`, `.serena`, `.review`,
  `.code-review-graph`, `.codex`, `.agents`)를 커밋하지 않는다. `.gitignore`가 이들을 막지 않으므로
  스테이징 전에 `git status`로 직접 확인한다. 루트 `HANDOFF.md`는 이미 추적 중인 파일이다
  (`git log -- HANDOFF.md`).

## 정본 문서

| 주제 | 정본 |
|---|---|
| 소비자 계약·리뷰 상태·예산 원장 | `docs/workflows/contracts.md` |
| 커밋·PR·이슈 증거 형식 | `docs/change-evidence-contract-v1.md` |
| fleet 롤아웃·감사·태그 발행 | `docs/workflow-fleet-rollout.md` |
| 소비자 caller 트리 | `examples/baseline-workflows/README.md` |
| 과거 설계·계획 (이력) | `docs/superpowers/specs/`, `docs/superpowers/plans/` |

`docs/superpowers/`는 당시의 설계 기록이다. 현재 동작은 위의 다른 문서와 테스트로 판단한다.
