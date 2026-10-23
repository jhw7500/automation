# 커밋 형식 통일 — 계획 3: CI audit와 commitlint 정리 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** fleet 16개 저장소에 들어오는 새 커밋의 형식 위반을 CI step summary와 warning annotation으로 보이게 한다. 잡은 실패하지 않는다. 저장소별 commitlint 파일은 같은 롤아웃 PR에서 제거한다.

**Architecture:**
- automation에 재사용 워크플로 `commit-evidence-audit.yml`과 커밋 범위 수집·보고 스크립트를 둔다.
- 스크립트는 계획 2 Task 1의 `check_commit_message.check_message()`를 그대로 부른다. 그래서 커밋 시점 게이트와 판정이 같다.
- baseline caller는 catalog의 required 항목으로 추가한다.
- 소비자의 `commitlint.yml`·`commitlint.config.mjs`는 catalog `retired` 항목으로 등록한다. 그러면 fleet 렌더러가 caller를 넣는 같은 PR에서 두 파일을 지운다.
- 새 워크플로·caller·catalog 바이트는 기존 봉인 tree에 속하므로 새 릴리스 라인에 묶는다. 규칙 파일과 validator는 봉인하지 않는다(설계 §12).

**Tech Stack:** GitHub Actions 재사용 워크플로, Python 3 stdlib 스크립트, pytest, actionlint 1.7.12(`test-fleet-tools.yml`).

**Spec:** `docs/superpowers/specs/2026-10-19-commit-format-unification-design.md` §5-C, §5-D, §8(판단 3·5·6), §11. 선행 조건은 계획 2 Task 1이 automation main에 머지돼 있는 것이다(`scripts/commit_title_rules.py`, `scripts/check_commit_message.py`).

## Global Constraints

- **잡은 실패하지 않는다**(설계 §8 판단 5).
  - 위반은 step summary 표와 `::warning` annotation으로만 보인다.
  - 내부 오류(범위 계산 실패, 메시지 읽기 실패)도 warning과 summary 한 줄로 남기고 exit 0으로 끝낸다.
  - required check로 만들지 않는다.
- **대상 커밋**(§5-C-2)은 두 가지이고, 둘 다 merge 커밋(`--no-merges`)을 제외한다.
  - PR 이벤트: `pull_request.base.sha..pull_request.head.sha`
  - 기본 브랜치 push: `before..after`
- **판정:** `check_commit_message.check_message()` 결과를 그대로 쓴다. 워크플로나 스크립트에 규칙을 다시 쓰지 않는다.
- **출처 표시:** summary 머리에 다음 두 가지를 출력한다(critic 위험 3).
  - 중앙 automation 커밋 SHA
  - `check_message()`가 돌려준 `sources` digest
- **pin 관례:**
  - `actions/checkout`은 승인된 SHA(`tests/test_action_pins.py` `CHECKOUT_SHA`)로 참조한다.
  - `actions/setup-python`은 `test-fleet-tools.yml:62`와 같은 `5fda3b95a4ea91299a34e894583c3862153e4b97`로 참조한다.
  - `secrets: inherit`와 `secrets.GITHUB_TOKEN`은 쓰지 않는다(`tests/test_workflow_secret_contracts.py`).
- **중앙 커밋 SHA:** `github.workflow_sha`를 쓰지 않는다. 재사용 워크플로에서 이 값은 caller 쪽 SHA이기 때문이다. 대신 `referenced_workflows`에서 찾는 기존 방식을 따른다(`.github/workflows/opencode-recover-finalization.yml:70-85`, 같은 job 권한).
- **릴리스:** 봉인 대상 파일의 새 바이트는 새 릴리스 라인에 묶는다(작성 시점 기준 다음 라인은 v1.87, #239가 먼저 쓰면 그다음 번호). 태그는 create-only다.
- **사용자 승인이 필요한 단계:** push, PR, 머지, 태그, fleet publish, `automation_ref` 범프는 각각 사용자 승인을 받는다. publish는 소비자 저장소를 바꾸므로 사용자가 요청한 저장소에만 실행한다.

## Review Focus

1. **기본 브랜치가 아닌 브랜치 push는 감사하지 않는다.** caller의 `push` 트리거에는 브랜치 필터가 없다. catalog의 `trigger`가 저장소 공통 JSON 하나라서 `master`/`main`을 고정할 수 없기 때문이다. 그래서 job 조건으로 `github.ref_name == github.event.repository.default_branch`일 때만 실행한다. → Task 1 `test_audit_job_skips_non_default_branch_push`
2. **새 브랜치·force-push는 `after` 1건만 감사한다.** `before`가 0으로만 된 SHA이거나 도달할 수 없는 force-push면 잡이 빨개지지 않고, summary에 그 사실을 적는다. → Task 1 `test_unreachable_before_audits_after_only`
3. **커밋이 많은 PR도 잡을 실패시키지 않는다.** 250건을 넘으면 앞 250건만 감사하고 생략 수를 적는다. → Task 1 `test_commit_limit_truncates_with_note`
4. **내부 오류도 실패가 아니다.** 메시지를 읽지 못하거나 범위가 잘못돼도 warning과 summary 한 줄로 끝내고 exit 0이다. → Task 1 `test_internal_error_is_warning_not_failure`
5. **commitlint 파일이 있는 5개 저장소만 삭제 변경을 받는다.** pim-package-jhw, pcap-analyzer, jhw-notion, personal-ops, claude-config가 해당한다. 나머지 11개는 caller 추가만 받는다. jhw-notion은 기본 브랜치가 `main`인데 파일에는 `[master]`가 적혀 있다. 그래도 같은 경로라 함께 지워진다. → Task 2 `test_retired_commitlint_paths_are_deleted_only_where_present`
   - 근거(2026-10-23): `gh api repos/jhw7500/<repo>/contents/<path>?ref=<기본 브랜치>`로 16개 저장소를 조회했다. 위 5곳은 두 파일(`.github/workflows/commitlint.yml`, `.github/commitlint.config.mjs`)이 모두 있다. pcap-analyzer는 `0c8e5276`/`5e27133b`다. 나머지 11곳은 두 파일 모두 404다.

---

### Task 1: 커밋 범위 수집·보고 스크립트와 재사용 워크플로

**Files:**
- Create: `scripts/audit_commits.py`
- Create: `.github/workflows/commit-evidence-audit.yml`
- Test: `tests/test_audit_commits.py`, `tests/test_commit_evidence_audit_workflow.py`

**Interfaces:**
- Consumes: `scripts/check_commit_message.py`의 `check_message(message: str) -> dict[str, object]`. 같은 디렉터리에서 `importlib.util.spec_from_file_location`으로 로드한다.
- Produces (`audit_commits.py`):
  - `@dataclass(frozen=True) class AuditRow: sha: str; title: str; auto: bool; valid: bool; title_findings: tuple[str, ...]; evidence_findings: tuple[str, ...]`
  - `@dataclass(frozen=True) class AuditReport: rows: tuple[AuditRow, ...]; notes: tuple[str, ...]; sources: dict[str, str]`
  - `def collect_range(repo_dir: Path, *, event_name: str, event: dict, limit: int = 250) -> tuple[list[str], list[str]]` — 반환값은 (커밋 SHA 목록, notes)다. PR은 base..head, push는 before..after다. before가 0으로만 된 SHA이거나 `git cat-file -e`에 실패하면 after만 본다. `git rev-list --no-merges --reverse`를 쓴다.
  - `def audit(repo_dir: Path, shas: list[str]) -> AuditReport` — 각 SHA를 `git log -1 --format=%B`로 읽어 `check_message()`에 넣는다.
  - `def render_summary(report: AuditReport, *, automation_commit: str) -> str`
    - 표 열: `SHA(7)`, `제목`, `제목 규칙`, `변경 증거`, `판정`
    - 판정 값: `ok` / `auto` / `위반`
  - `def render_annotations(report: AuditReport) -> list[str]` — 위반 행마다 `::warning title=commit-evidence-audit::<sha7> <codes>`다.
  - CLI: `python3 -I audit_commits.py --repo-dir <consumer checkout> --event-name <name> --event-path <file> --automation-commit <sha> --summary-path <file>`
    - annotation은 stdout으로 낸다.
    - 어떤 경우에도 exit 0이다. argparse 오류만 예외로 2다.
- Produces (`commit-evidence-audit.yml`, `on: workflow_call`):
  - job 1개, `runs-on: ubuntu-latest`
  - 조건: `github.event_name == 'pull_request' || github.ref_name == github.event.repository.default_branch`
  - 단계:
    1. 중앙 SHA 해석(precedent 방식)
    2. automation checkout: `repository: jhw7500/automation`, `ref: <central sha>`, `path: automation-audit`, `persist-credentials: false`
    3. 소비자 checkout: `path: audit-target`, `fetch-depth: 0`. PR이면 `ref: ${{ github.event.pull_request.head.sha }}`, `persist-credentials: false`
       - 별도 `path`가 필요한 이유: 경로 없이 workspace 루트에 받으면, pin된 `actions/checkout`이 `.git` 없는 대상의 내용을 지운다(`src/git-directory-helper.ts:28`, `:120`, Codex 리뷰 P2). 그러면 step 2의 `automation-audit`이 함께 지워진다.
       - 선례: `opencode-recover-finalization.yml`의 `review-target`
    4. `python3 -I automation-audit/scripts/audit_commits.py --repo-dir audit-target … --summary-path "$GITHUB_STEP_SUMMARY"`
  - 마지막 step에 `continue-on-error: true`를 둔다. 이중 안전장치이며, 스크립트 자체도 exit 0이다.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_audit_commits.py`는 tmp에 실제 git 저장소를 만들어 쓴다.
  - `test_pr_range_excludes_merges`
  - `test_push_range_before_after`
  - `test_unreachable_before_audits_after_only` — before 0-SHA와 존재하지 않는 SHA 두 경우를 본다. 각각 notes에 사유가 남는다.
  - `test_commit_limit_truncates_with_note`
  - `test_summary_has_source_digests_and_rows` — automation_commit과 `sources`의 값이 summary에 있다.
  - `test_annotations_only_for_violations`
  - `test_internal_error_is_warning_not_failure` — 존재하지 않는 repo-dir에서 CLI exit 0이고, stdout에 `::warning`이 있다.

  `tests/test_commit_evidence_audit_workflow.py`는 YAML을 파싱해 확인한다.
  - `test_audit_job_skips_non_default_branch_push` — job `if`의 정확한 문자열
  - `test_audit_never_fails` — 마지막 step의 `continue-on-error`
  - `test_audit_checkout_uses_central_sha_not_workflow_sha` — 파일에 `github.workflow_sha`가 없다.
  - `test_audit_does_not_persist_credentials`
  - `test_audit_checkouts_use_separate_paths` — 두 checkout step의 `path`가 각각 `automation-audit`·`audit-target`으로 서로 다르고, 실행 step의 `--repo-dir`가 `audit-target`이다
- [ ] **Step 2: 실패 확인** — `python3 -m pytest -q -p no:cacheprovider tests/test_audit_commits.py tests/test_commit_evidence_audit_workflow.py`
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** — 같은 명령. 이어서 `python3 scripts/check_workflow_shell_syntax.py`와 기존 pin·secret 테스트 `python3 -m pytest -q -p no:cacheprovider tests/test_action_pins.py tests/test_workflow_secret_contracts.py tests/test_workflow_shell_syntax.py`를 돌린다.
- [ ] **Step 5: 커밋** — `feat(workflows): 커밋 형식 감사 재사용 워크플로를 추가한다`

### Task 2: baseline caller, catalog, commitlint retired 항목

**Files:**
- Create: `examples/baseline-workflows/.github/workflows/commit-evidence-audit.yml`
  - `uses: jhw7500/automation/.github/workflows/commit-evidence-audit.yml@__AUTOMATION_COMMIT__`
  - 트리거: `pull_request`(`opened`, `synchronize`, `reopened`)와 `push`. push에는 브랜치 필터가 없다.
- Modify: `scripts/workflow-catalog.json` — 세 항목을 추가한다.
  - required 항목 1개: `central_workflow` 지정, `auth_family: "none"`, `profile_axis: null`, trigger·caller_jobs는 caller와 일치
  - retired 항목 2개:
    - `{"path": ".github/workflows/commitlint.yml", "kind": "retired", "central_workflow": null, "auth_family": "none", "profile_axis": null, "trigger": {}, "caller_jobs": []}`
    - `.github/commitlint.config.mjs`도 같은 모양

    선례는 `gemini-pr-review.yml` retired 항목이다.
- Modify: `tests/test_canonical_workflow_tree.py` — `EXPECTED_TRIGGERS`, `EXPECTED_CALLER_PERMISSIONS`에 새 caller를 넣는다.
- Test: `tests/test_workflow_catalog.py`, `tests/test_canonical_workflow_tree.py`, `tests/test_rollout_workflow_fleet.py`

`.github/workflow-config.yml`의 활성화 게이트(`check-workflow-enabled`)는 쓰지 않는다. 감사는 항상 켜 둔다(설계 §8 판단 3). `test_bootstrap_config_matches_the_approved_disabled_policy`가 새 이름을 요구하면, 그 테스트를 고치지 말고 멈춰서 보고한다.

- [ ] **Step 1: 실패하는 테스트 작성**
  - `test_rollout_workflow_fleet.py`에 `test_retired_commitlint_paths_are_deleted_only_where_present`를 추가한다. 기존 retired 삭제 테스트의 fixture 패턴을 쓴다. 파일이 있는 소비자는 삭제 변경을 받고, 없는 소비자는 변경이 없다.
  - `EXPECTED_TRIGGERS`·`EXPECTED_CALLER_PERMISSIONS`에 새 caller를 넣어 tree 테스트가 실패하게 한다.
- [ ] **Step 2: 실패 확인** — `python3 -m pytest -q -p no:cacheprovider tests/test_workflow_catalog.py tests/test_canonical_workflow_tree.py tests/test_rollout_workflow_fleet.py`
- [ ] **Step 3: caller와 catalog 항목 추가**
- [ ] **Step 4: 통과 확인** — 같은 명령. 이어서 `python3 scripts/rollout_workflow_fleet.py --mode plan …`(인자는 `docs/workflow-fleet-rollout.md`의 plan 절)을 돌린다. manifest에서 5개 저장소의 삭제 2건과 16개 저장소의 caller 추가를 센다.
- [ ] **Step 5: 커밋** — `feat(workflows): 커밋 감사 caller를 넣고 저장소별 commitlint를 퇴역시킨다`

### Task 3: 릴리스 라인과 Python 3.10 실측

Task 1·2가 봉인 tree(`.github/workflows`, `examples/baseline-workflows/.github`)와 catalog의 바이트를 바꾸므로 새 릴리스 라인에 묶는다. 규칙 파일·validator·`audit_commits.py`는 봉인 root에 넣지 않는다(설계 §12). 소비자가 커밋 SHA로 pin하므로 바이트는 고정되고, 봉인하면 이후 validator 수정마다 릴리스 라인이 필요해지기 때문이다.

**Files:**
- Modify: `scripts/workflow_release_inventory.py` — 새 라인 상수와 `def release_supports_commit_audit(ref: str) -> bool`을 추가한다. 선례는 `release_supports_claude_rollout_fallback`(`:337`)이다. 새 root는 추가하지 않는다.
- Modify: `scripts/verify_workflow_release.py` — 현재 seal(raw digest와 parsed seal)을 갱신한다. 이전 라인은 직전 태그에서 측정한 `V<NNN>_*` 상수로 기존 digest를 유지한다.
- Modify: `tests/release_fixture_helpers.py` — `restore_pre_v<NNN>` 함수를 추가한다.
- Modify: `.github/workflows/test-fleet-tools.yml` — job `pytest-py310`을 추가한다.
  - 기존 pytest job과 같은 checkout·setup-python pin을 쓰고 `python-version: '3.10'`
  - 실행: `pytest -q tests/test_commit_title_rules.py tests/test_check_commit_message.py tests/test_audit_commits.py tests/test_change_evidence.py`
- Modify: `docs/workflows/contracts.md` — 커밋 감사 절을 넣는다. 트리거, 대상 커밋, 실패하지 않음, summary 형식, 규칙 출처를 쓴다. 조건은 산문으로 다시 쓰지 말고 `audit_commits.py`의 함수를 가리킨다.
- Test: `tests/test_verify_workflow_release.py`

절차는 `AGENTS.md` 「워크플로·액션 바이트를 바꿀 때 같이 고칠 것」과 `git show --stat be8fb11`의 "Release binding" 항목을 그대로 따른다.

- [ ] **Step 1: 실패하는 테스트 작성** — `test_release_supports_commit_audit`: 새 라인 ref에서 True이고 직전 라인에서 False다.
- [ ] **Step 2: 실패 확인** — `python3 -m pytest -q -p no:cacheprovider tests/test_verify_workflow_release.py -k commit_audit`
- [ ] **Step 3: 구현과 seal 갱신**
- [ ] **Step 4: 통과 확인** — `python3 -m pytest -q -p no:cacheprovider tests/test_verify_workflow_release.py tests/test_workflow_catalog.py`. 전체 스위트는 PR CI(`test-fleet-tools.yml`)가 돌린다. 새 `pytest-py310` job이 실제로 3.10에서 돌았는지 CI 로그의 interpreter 버전 줄로 확인한다.
- [ ] **Step 5: 커밋** — `chore(workflows): 커밋 감사를 v<NNN> 릴리스 라인에 묶는다`

### Task 4: fleet 롤아웃 커밋 메시지를 계약 형식으로

fleet 롤아웃 PR은 merge commit으로 머지된다. 실측 결과 personal-ops #69, claude-config #209, pcap-analyzer #87의 머지 커밋은 모두 부모가 2개였다. 그래서 롤아웃 도구가 만든 커밋 자체가 기본 브랜치에 남는다. 지금 그 메시지는 `pr_title()` 한 줄뿐이다(`scripts/rollout_workflow_fleet.py:927`, `:950`). 감사가 켜지면 롤아웃마다 저장소 16곳에 위반이 1건씩 생긴다.

**Files:**
- Modify: `scripts/rollout_workflow_fleet.py` — `def commit_message(ref: str, base_branch: str | None = None, default_branch: str | None = None) -> str`를 추가하고 `:927`·`:950`에서 쓴다. `pr_title()`은 PR 제목용으로 그대로 둔다. PR 형식은 설계 §9의 후속이다.
- Test: `tests/test_rollout_workflow_fleet.py`

`commit_message()` 출력은 다음과 같다.
- 제목: `ci: 공통 자동화 워크플로를 <ref>로 맞춘다`. 추가 브랜치면 `ci: 공통 자동화 워크플로를 <ref>로 맞춘다 (base=<b>)`다. 72자를 넘지 않아야 한다.
- 본문은 계약 v1 필드이고, 필드 제목 앞에 공백 한 칸을 둔다.
  - `Contract version`: `v1`
  - `Why`: automation `<ref>` 롤아웃
  - `Changes`: 렌더링된 경로 목록. 호출부에서 넘길 수 없으면 `examples/baseline-workflows` 기준이라는 한 줄
  - `Validation`: `Not run:` 사유. 롤아웃 도구는 소비자 테스트를 돌리지 않는다.
  - `References`: automation 태그 URL

- [ ] **Step 1: 실패하는 테스트 작성**
  - `test_rollout_commit_message_passes_shared_rules` — `check_commit_message.check_message(commit_message("v1.87"))`가 `valid=True`다. base 변형도 확인한다.
  - `test_rollout_commit_uses_commit_message` — 기존 publish fixture에서 git commit stdin, 즉 `:927` 경로가 `commit_message()` 출력이다.
- [ ] **Step 2: 실패 확인** — `python3 -m pytest -q -p no:cacheprovider tests/test_rollout_workflow_fleet.py -k commit_message`
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** — `python3 -m pytest -q -p no:cacheprovider tests/test_rollout_workflow_fleet.py`
- [ ] **Step 5: 커밋** — `fix(fleet): 롤아웃 커밋 메시지를 계약 형식으로 만든다`

### Task 5: 통합 — PR·태그·롤아웃·범프

각 단계는 사용자 승인 뒤에만 실행한다. 절차의 정본은 `docs/workflow-fleet-rollout.md`다.

- [ ] **Step 1: automation PR** — Task 1~4는 floor가 높은 `.github/workflows/**`를 포함하므로 둘로 나눈다.
  - (a) Task 1·2·4: 워크플로·caller·롤아웃 메시지
  - (b) Task 3: 봉인·3.10 job·계약 문서

  각각 tribunal → PR → 리뷰(`review:request`, `@claude review`, `@codex review`) → 머지한다.
- [ ] **Step 2: 태그** — 새 라인 태그를 create-only로 만든다. 이어서 `python3 -m scripts.verify_workflow_release --ref <tag> --expected-commit <40-hex>`를 실행한다.
- [ ] **Step 3: 롤아웃**
  1. `--mode plan`
  2. canary 1곳에 publish. 대상은 personal-ops다. commitlint 두 파일이 있고 기본 브랜치가 `main`이다.
  3. canary PR에서 감사 잡이 돌았는지 확인한다. 확인 항목은 summary 표, 출처 SHA·digest 줄, 실패하지 않은 잡 상태다.
  4. 나머지 15곳에 publish한다.
  5. `audit_workflow_fleet.py`가 `current=16 drift=0 blocked=0`인지 확인한다.
- [ ] **Step 4: 기본값 범프** — `scripts/workflow-config.json` `automation_ref`를 새 태그로 올리고 `tests/test_workflow_catalog.py` 기대값을 고친다(선례: `chore(workflows): default rollout to v1.86`).
- [ ] **Step 5: 게이트 사본 갱신(claude-config)**
  1. `python3 scripts/sync-commit-gate-vendor.py --automation <checkout> --commit <태그 커밋>`을 실행한다.
  2. SOURCE.json의 파일 digest가 이전과 같으면 `automation_commit`만 바뀐 PR이 된다. 다르면 계획 2 Task 5의 테스트를 다시 돌린 뒤 PR을 만든다.
- [ ] **Step 6: 첫 기본 브랜치 push 확인** — 롤아웃 머지 뒤 아무 소비자 하나에서 기본 브랜치 push 이벤트로 감사 잡이 돌았는지 확인한다. 확인 항목은 두 가지다.
  - 롤아웃 커밋이 `ok`로 표시되는지(Task 4 효과)
  - 머지 커밋이 대상에서 빠졌는지

## 후속 (이 계획 밖)

- Issue·PR 형식 통일(설계 §9), 롤아웃 PR 제목(`pr_title()`)의 한국어화.
- fleet 밖 저장소(jhw-env, imx-hantro)의 편입 여부.
