# 커밋 형식 통일 — 계획 2: 커밋 시점 게이트 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Claude·Codex가 jhw7500 저장소에서 실행하는 `git commit`을 커밋 직전에 공유 규칙(계약 validator + 통일 제목 규칙)으로 검사해 위반을 막고, 사용자가 승인하면 그 커밋 1건만 통과시킨다.

**Architecture:** automation에 규칙의 단일 출처(제목 규칙 모듈 + 메시지 검사 실행기)를 만들고, claude-config에 새 `commit_gate` 패키지를 둔다. 게이트는 특정 automation 커밋의 파일 3개를 digest가 고정된 사본(vendor)으로 설치해 그대로 호출한다. 게이트는 tribunal과 별개 패키지·별개 훅 항목이며 `hooks/pre_pr_tribunal/**`를 수정하지 않는다. 승인은 `pr_override_grant`의 보증 수준을 따르되, 메시지·저장소·HEAD 바인딩과 TTL을 둔다.

**Tech Stack:** Python 3.10 stdlib(게이트 런타임 `/usr/bin/python3` 3.10.12), pytest. automation `scripts/`는 패키지가 아니다(`scripts/__init__.py` 없음). 모듈 간 로드는 `importlib.util.spec_from_file_location`으로 한다(`tests/test_change_evidence.py:19`와 같은 방식).

**Spec:** `docs/superpowers/specs/2026-10-19-commit-format-unification-design.md` §5-E, §8(판단 7·8), §11(2026-10-23 개정). 계획 1은 `docs/superpowers/plans/2026-10-19-commit-format-plan-1-authoring.md`, CI audit·정리는 계획 3(`2026-10-23-commit-format-plan-3-ci-audit.md`)이다.

## Global Constraints

- **개발 마찰 최소화**(설계 §12, 사용자 지시): 게이트는 확실히 판정할 수 있는 명백한 위반만 막는다. 아래 경우는 커밋을 통과시키고 stderr에 경고 한 줄만 남긴다. 놓친 것은 계획 3의 CI audit가 보여 준다.
  - 메시지를 알 수 없음(편집기, 해석하지 못한 동적 치환, 다른 셸 문자열 안의 커밋)
  - 게이트 내부 오류
  - 사본 digest 불일치
- **적용 저장소:** 커밋할 저장소의 `origin` URL이 jhw7500 GitHub 저장소일 때만 판정한다. 인정하는 형태는 `https://github.com/jhw7500/<repo>[.git]`, `git@github.com:jhw7500/<repo>[.git]`, `ssh://git@github.com/jhw7500/<repo>[.git]`이다. 그 밖의 저장소(origin 없음 포함)는 판정 없이 통과한다(설계 §8 판단 7).
- **규칙 출처:** automation의 `scripts/validate_change_evidence.py`, `scripts/commit_title_rules.py`, `scripts/check_commit_message.py`. 게이트는 같은 automation 커밋의 바이트를 수정 없이 쓴다. 판정 로직을 claude-config에 다시 구현하지 않는다.
- **런타임 하한:** 위 세 파일은 Python 3.10 stdlib만 쓴다.
- **제목 규칙**(§11):
  - 헤더 정규식은 `^(\w*)(?:\((.*)\))?!?: (.*)$`이며 `re.ASCII`로 컴파일한다.
  - type은 `feat|fix|chore|refactor|docs|test|build|ci|perf|style`이다.
  - 오류 코드는 `title-type-enum`, `title-type-case`, `title-type-empty`, `title-subject-empty`, `title-subject-full-stop`, `title-header-trim` 6종이다.
  - 72자 제한은 validator(`commit-title-too-long`)만 맡는다.
- **자동 메시지:** is-ignored 19.8.1의 기본 목록을 그대로 옮긴다. 정규식은 JS 원문과 같은 뜻으로 쓴다.
  - `^((Merge pull request)|(Merge (.*?) into (.*?)|(Merge branch (.*?)))(?:\r?\n)*$)` (multiline)
  - `^(Merge tag (.*?))(?:\r?\n)*$` (multiline)
  - `^(R|r)evert (.*)`
  - `^(R|r)eapply (.*)`
  - `^(amend|fixup|squash)!`
  - 첫 줄에서 `^chore(\([^)]+\))?:`를 떼고 trim한 값이 semver인 경우. 판정 기준은 npm `semver.valid()`, 곧 앞의 `v` 하나를 허용하는 SemVer 2.0.0 정식 문법(엄격 모드)이다.
  - `^(Merged (.*?)(in|into) (.*)|Merged PR (.*): (.*))`
  - `^Merge remote-tracking branch(\s*)(.*)`
  - `^Automatic merge(.*)`
  - `^Auto-merged (.*?) into (.*)`

  자동 메시지는 제목 규칙과 validator를 모두 건너뛴다.
- **tribunal 비간섭:** `hooks/pre_pr_tribunal/**`와 `scripts/install-pre-pr-tribunal.py`의 동작을 바꾸지 않는다. 공유 설치 라이브러리 `scripts/lib/runtime_hook_installer.py`는 호출만 한다.
- **승인 보증 수준:** "friction against accidental bypass, not cryptographic proof of human presence" (`hooks/pre_pr_tribunal/pr_override_grant.py:3-4`와 같다). 문서와 CLI 도움말에 이보다 강하게 쓰지 않는다.
- **이 작업의 커밋 형식:** 새 규칙을 따른다. 커밋은 `git commit --cleanup=verbatim --file <msg>`로 하고, 커밋 전에 validator `--kind commit --expected-version v1`를 통과해야 한다. References에는 `https://github.com/jhw7500/automation/issues/237`을 쓴다.
- **사용자 승인이 필요한 단계:** push, PR 생성, 머지, `install.sh` 실행, Codex `/hooks` trust는 각각 실행 직전 사용자 승인을 받는다. 서브에이전트 프롬프트에는 push·PR·머지·브랜치 전환 금지를 적는다.
- **PR 분할과 작업 위치:** claude-config의 `hooks/**`·`skills/**`는 tribunal floor 100(iterative)이다(`hooks/pre_pr_tribunal/policy.py:102,108`). 그래서 PR을 작게 나눈다(Task 7). claude-config 작업 브랜치는 공유 main 작업본이 아니라 `~/ai/opencode/worktrees/` 아래 worktree에서 만든다(계획 1 ledger Ruling: claude-config `.worktrees/`는 gitignore되지 않음).

## Review Focus

1. **Claude Code의 기본 커밋 형태는 그대로 쓸 수 있어야 한다.** `git commit -m "$(cat <<'EOF' … EOF)"`처럼 구분자를 따옴표로 감싼 heredoc은 정적 텍스트다. 꺼내서 검사하고, 유효하면 통과한다. 해석하지 못한 다른 동적 형태는 막지 않고 경고만 남긴다. → Task 3 `test_claude_heredoc_message_is_literal`, Task 5 `test_unknown_message_passes_with_warning`
2. **판정 대상은 실제로 커밋할 저장소다.** `cd <다른 저장소> && git commit -F m.md`나 `git -C <path> commit -F m.md`에서는 훅의 cwd가 아니라 커밋할 저장소의 origin과 HEAD로 판정한다. → Task 3 `test_cd_and_dash_c_resolve_commit_directory`, Task 5 `test_gate_uses_commit_directory_origin`
3. **승인은 메시지가 바뀌면 무효가 된다.** 승인 뒤 메시지 파일을 고치면 막혀야 한다. 같은 승인으로 두 번째 커밋도 막혀야 한다. → Task 4 `test_grant_binds_message_hash`, `test_grant_is_consumed_once`
4. **jhw7500 밖은 판정하지 않는다.** 외부 fork, origin이 없는 저장소, GitLab 저장소는 메시지가 어떻든 통과한다. → Task 5 `test_non_jhw7500_origin_passes`
5. **한 명령의 커밋 여러 건은 각각 검사한다.** `git commit -F a.md && git commit -F b.md`에서 하나라도 위반이면 명령 전체를 막고, 위반한 쪽을 사유에 적는다. → Task 3 `test_multiple_commits_in_one_command`, Task 5 `test_one_violation_blocks_compound_command`

---

### Task 1: automation — 공유 제목 규칙 모듈과 메시지 검사 실행기

**Files:**
- Create: `scripts/commit_title_rules.py`
- Create: `scripts/check_commit_message.py`
- Create: `docs/commit-title-rules.md` — 정본 산문. 규칙 목록·자동 메시지 목록·`RULES_VERSION`·1차 출처(§11 근거)를 담는다.
- Test: `tests/test_commit_title_rules.py`, `tests/test_check_commit_message.py`

이 파일들은 릴리스 인벤토리에 넣지 않는다(설계 §12). 따라서 새 릴리스 라인이 필요 없다. `.github/workflows/test-fleet-tools.yml`은 봉인 tree(`CENTRAL_WORKFLOW_ROOT = ReleaseRoot(PurePosixPath(".github/workflows"), "tree")`, `scripts/workflow_release_inventory.py:41`)에 속하므로 이 Task에서 고치지 않는다.

**Interfaces:**
- Produces (`commit_title_rules.py`):
  - `RULES_VERSION: str = "1"`
  - `TYPES: tuple[str, ...]`
  - `@dataclass(frozen=True) class TitleFinding: code: str; message: str`
  - `def is_auto_message(message: str) -> bool`
  - `def check_title(message: str) -> tuple[TitleFinding, ...]` — 첫 줄만 본다. 자동 메시지 여부는 보지 않는다. 그 판정은 실행기가 한다.
- Produces (`check_commit_message.py`):
  - `def check_message(message: str) -> dict[str, object]` — 반환 키:
    - `auto: bool`
    - `valid: bool`
    - `title_findings: list[str]`
    - `evidence_findings: list[str]`
    - `rules_version: str`
    - `sources: dict[str, str]` (파일명 → sha256)

    `auto`이면 두 findings는 비어 있고 `valid`는 True다.
  - `def source_digests() -> dict[str, str]` — 자기 디렉터리의 세 파일 바이트에 대한 sha256이다.
  - CLI `python3 -I check_commit_message.py --path <file> [--format text|json]`. 종료코드는 0(valid 또는 auto), 1(위반), 2(I/O)다. 파일은 UTF-8로 읽고, 바이트 그대로 검사한다(cleanup 흉내 없음).
  - validator 호출은 `validate_text(text, kind="commit", expected_version="v1")`다(`scripts/validate_change_evidence.py:2614`). findings 코드는 `ValidationResult.findings`의 `code`로 꺼낸다.

- [ ] **Step 1: 실패하는 테스트 작성**
  - `tests/test_commit_title_rules.py`
    - `test_valid_titles_pass` — `feat(commit): 직접 커밋에도 변경 증거를 남긴다`, `fix!: 깨진 링크를 고친다`, `docs(a/b): 문서를 고친다`는 `()`를 반환한다.
    - `test_title_rule_codes` — 각 입력과 기대 코드 집합은 다음과 같다.
      - `Fix: 고친다` → `{title-type-case, title-type-enum}`
      - `feature: 추가한다` → `{title-type-enum}`
      - `fix: ` → `{title-subject-empty, title-header-trim}` (끝 공백)
      - `fix: 고친다.` → `{title-subject-full-stop}`
      - ` fix: 고친다` → `{title-header-trim, title-type-empty, title-subject-empty}`
      - `고친다` → `{title-type-empty, title-subject-empty}`
      - `기능(x): 추가` → `{title-type-empty, title-subject-empty}` (ASCII `\w` 확인)
    - `test_auto_messages` — 아래 입력은 `is_auto_message`가 True를 반환한다.
      - `Merge branch 'x'`
      - `Merge pull request #1 from a/b`
      - `Revert "feat: x"`
      - `fixup! feat: x`
      - `squash! x`
      - `amend! x`
      - `chore: 1.2.3`
      - `chore(release): v1.2.3`
      - `Merge remote-tracking branch 'origin/main'`
      - `Automatic merge from x`
      - `Auto-merged a into b`

      `feat: merge branch`와 `chore: release 1.2`는 False를 반환한다.
    - `test_rules_doc_matches_module` — `docs/commit-title-rules.md`에 `RULES_VERSION`, `TYPES`의 각 값, 오류 코드 6개가 모두 있다.
    - `test_rule_files_parse_as_python_310` — 두 새 파일과 validator를 `ast.parse(src, feature_version=(3, 10))`로 파싱한다. 그리고 모든 최상위 import가 `sys.stdlib_module_names`에 있다.
  - `tests/test_check_commit_message.py`
    - `test_contract_valid_message_passes` — `examples/change-evidence/commit-message.md` 형식의 한국어 메시지를 쓴다. 기대값은 `valid=True`, 두 findings가 비어 있음이다.
    - `test_missing_fields_reported_as_evidence_findings` — 기대값은 `evidence_findings`가 비어 있지 않고 `valid=False`다.
    - `test_title_and_evidence_findings_combined` — 제목 위반과 필드 누락이 함께 보고된다.
    - `test_auto_message_skips_both` — `Revert "x"` 본문만 있는 메시지에서 `auto=True`, `valid=True`다.
    - `test_title_over_72_is_validator_only` — 73자 제목에서 `commit-title-too-long`은 `evidence_findings`에만 있고 `title_findings`에는 없다.
    - `test_cli_exit_codes` — 각각 0, 1, 2(없는 경로)를 확인한다.
    - `test_sources_digest_matches_files` — `sources`의 값이 실제 파일 sha256과 같다.

- [ ] **Step 2: 실패 확인** — `python3 -m pytest -q -p no:cacheprovider tests/test_commit_title_rules.py tests/test_check_commit_message.py`. 모듈이 없으니 수집 단계에서 실패해야 한다.

- [ ] **Step 3: 구현** — 위 Interfaces 그대로 구현한다. `check_title`은 헤더 정규식이 맞지 않으면 type·subject를 None으로 본다. commitlint는 이 경우 `type-empty`·`subject-empty`를 낸다. `type-case`는 `type != type.lower()`일 때, `type-enum`은 비어 있지 않은 type이 `TYPES`에 없을 때 낸다(대소문자 구분).

- [ ] **Step 4: 통과 확인 + 보존 검사** — 같은 명령으로 전부 PASS여야 한다. 이어서 `python3 -m pytest -q -p no:cacheprovider tests/test_change_evidence.py`를 돌려 기존 수와 같은 PASS인지 본다(validator 미변경 보존 검사).

- [ ] **Step 5: 판정 동등성 확인(1회성, 결과는 PR 본문에 기록)** — critic 위험 1 대응이다.
  - 대상은 commitlint가 실제로 돈 claude-config·personal-ops·pim-package-jhw의 기본 브랜치다.
  - 각 저장소에서 `gh run list --workflow=commitlint.yml --status success`의 run이 본 커밋 제목을 모은다. 실패 run의 제목도 따로 모은다.
  - 둘 다 `check_title`에 넣는다.
  - 기대값은 둘이다.
    - 성공 run의 제목은 모두 `()`다. 단, 72자 초과 제목(이제 validator 담당)과 `review-fix round N` 제목(예외 삭제)은 예외로 따로 센다.
    - 실패 run의 제목은 모두 1개 이상의 코드를 낸다. 단, 100자 초과와 본문 줄 길이 실패는 예외로 따로 센다.
  - 불일치가 있으면 원인을 적고 멈춘다.

- [ ] **Step 6: 커밋** — `feat(evidence): 커밋 제목 규칙과 메시지 검사기를 추가한다`.

### Task 2: claude-config — 규칙 사본(vendor)과 동기화 도구

**Files:**
- Create: `hooks/commit_gate/vendor/validate_change_evidence.py`, `hooks/commit_gate/vendor/commit_title_rules.py`, `hooks/commit_gate/vendor/check_commit_message.py` — Task 1이 머지된 automation 커밋의 바이트 그대로다.
- Create: `hooks/commit_gate/vendor/SOURCE.json` — `{"automation_commit": "<40-hex>", "rules_version": "1", "files": {"<name>": "<sha256>", …}}`
- Create: `scripts/sync-commit-gate-vendor.py`
- Test: `tests/commit_gate/test_vendor.py`

**Interfaces:**
- Consumes: Task 1 파일 3개와 `check_message()`
- Produces:
  - `sync-commit-gate-vendor.py --automation <checkout> --commit <40-hex>` — `git -C <checkout> show <commit>:scripts/<name>`로 세 파일을 쓰고 SOURCE.json을 갱신한다.
  - `sync-commit-gate-vendor.py --check` — 파일 digest가 SOURCE.json과 다르면 exit 1이다.
  - 후속 Task는 `hooks/commit_gate/vendor/check_commit_message.py`를 경로로 로드해 `check_message()`를 부른다.

- [ ] **Step 1: 실패하는 테스트 작성**
  - `test_vendor_digests_match_source` — SOURCE.json의 파일 3개 digest가 실제와 같다.
  - `test_vendor_runs_under_this_interpreter` — vendored `check_message()`가 유효 메시지에 valid=True, 필드 없는 메시지에 False를 낸다. claude-config CI는 Python 3.10에서 돌기 때문에, 이 테스트가 automation 파일의 3.10 실행을 실측하는 지점이 된다.
  - `test_sync_check_detects_drift` — tmp 사본의 한 바이트를 바꾸면 `--check`가 exit 1이다.
- [ ] **Step 2: 실패 확인** — `python3 -m pytest -q tests/commit_gate/test_vendor.py`
- [ ] **Step 3: 구현과 사본 생성** — 로컬 automation checkout(`~/ai/opencode/projects/automation`, main을 Task 1 머지 커밋으로 맞춘 상태)에서 sync를 실행한다.
- [ ] **Step 4: 통과 확인** — 같은 명령으로 PASS
- [ ] **Step 5: 커밋** — `feat(commit-gate): 커밋 규칙 사본과 동기화 도구를 추가한다`

### Task 3: claude-config — `git commit` 명령 해석기

**Files:**
- Create: `hooks/commit_gate/commit_scan.py`
- Test: `tests/commit_gate/test_commit_scan.py`

`hooks/pre_pr_tribunal/shell_scan.py`는 공개 API가 `scan_pr_create()`뿐이다. 내부 파서도 gh 전용이다(`_scan_simple_command`, `:721`). 그래서 재사용하지 않고 범위를 좁힌 해석기를 새로 만든다. 확실히 아는 형태만 `file`·`literal`·`auto`로 분류하고 나머지는 `unknown`으로 둔다. `unknown`은 게이트가 통과시킨다(설계 §12).

**Interfaces:**
- Produces:
  - `MessageSource = Literal["file", "literal", "auto", "unknown"]`
  - `@dataclass(frozen=True) class CommitInvocation: directory: Path; source: MessageSource; message_path: Path | None; message_text: str | None; reason: str`
  - `@dataclass(frozen=True) class ScanResult: invocations: tuple[CommitInvocation, ...]`
  - `def scan_commits(command: str, *, cwd: Path) -> ScanResult` — `git commit`이 없으면 `invocations=()`다.

해석 규칙(이것이 테스트 대상이다):
- `shlex.shlex(command, posix=True, punctuation_chars=True)`로 토큰화하고 `&&`, `||`, `;`, `|`, 줄바꿈으로 단순 명령을 나눈다. `cd <리터럴 경로>`는 같은 체인의 뒤 명령에 쓸 디렉터리를 바꾼다.
- 앞쪽의 `NAME=value`와 `env`·`command`를 건너뛴 뒤 argv[0]의 basename이 `git`이고 첫 하위 명령이 `commit`이면 커밋으로 본다.
  - `git` 전역 옵션 `-C <path>`는 누적 적용한다.
  - `-c k=v`는 건너뛴다.
  - `--git-dir`·`--work-tree`가 있으면 `unknown`이다.
- 토큰화 전에 `-m "$(cat <<'DELIM'` … `DELIM` … `)"` 형태를 먼저 찾는다. 구분자는 `'DELIM'` 또는 `"DELIM"`처럼 따옴표로 감싼 것이다. 찾으면 heredoc 본문을 그대로 `literal` 메시지로 쓰고, 그 부분을 자리표시 토큰으로 바꾼 뒤 나머지를 해석한다. 따옴표 없는 구분자는 셸 확장이 일어나므로 이 규칙에서 제외한다.
- 그 밖의 동적 구성(원문의 `$`, 백틱, `<<`, `eval`, `bash -c`, `sh -c`)이 있는 명령에서 감지된 커밋은 모두 `unknown`이다. 판정하지 않으며, 게이트는 통과시킨다(설계 §12).
- 어떤 토큰 안에 `\bgit\b(?:\s+(?:-C|-c)\s+\S+|\s+--?[A-Za-z][\w-]*(?:=\S+)?)*\s+commit\b`가 맞는 문자열이 있으면, cwd 기준 `unknown` 1건을 만든다. 이런 토큰은 `bash -c`·`eval`의 인자처럼 다른 셸에 넘기는 문자열이다. 이 정규식은 `commit`이 git 하위 명령 자리, 즉 전역 옵션 뒤에 올 때만 맞는다. `git log --grep=commit`은 최상위에서든 `bash -c` 안에서든 커밋이 아니다.
- 메시지 출처는 다음과 같다.
  - `-F/--file <p>`, `--file=<p>`, `-F<p>`이면 `file`이다. `<p>`가 `-`이면 `unknown`이다.
  - `-m/--message`가 1개 이상이면 `literal`이다. 값이 여러 개면 `"\n\n"`로 잇는다(git 동작).
  - `--fixup`, `--squash`, `--amend --no-edit`, 그리고 git dir에 `MERGE_HEAD`·`CHERRY_PICK_HEAD`·`REVERT_HEAD`가 있을 때의 `--no-edit`은 `auto`다.
  - `-e/--edit`, `-t/--template`, `-C/-c`(reuse/reedit), 메시지 옵션이 없는 경우는 `unknown`이다.

- [ ] **Step 1: 실패하는 테스트 작성**
  - `test_no_commit_returns_empty` — `git status && gh pr list`
  - `test_file_message` — `git commit --cleanup=verbatim --file /tmp/m.md` → `file`, 경로 `/tmp/m.md`
  - `test_literal_messages_joined` — `git commit -m "a" -m "b"` → `literal`, `"a\n\nb"`
  - `test_claude_heredoc_message_is_literal` — `git commit -m "$(cat <<'EOF'\nfeat: x\n\n ### Why\n…\nEOF\n)"` → `literal`. 본문은 바이트 그대로이되, 명령 치환이 끝 줄바꿈을 모두 지우므로 끝 줄바꿈은 제거한다. `<<"EOF"`도 같다.
  - `test_unquoted_heredoc_is_unknown` — `<<EOF`(따옴표 없음)와 `-m "$MSG"` → `unknown`
  - `test_editor_is_unknown` — `git commit`, `git commit -e -F m.md`, `git commit -t t.md`
  - `test_cd_and_dash_c_resolve_commit_directory` — `cd /x && git commit -F m.md` → directory `/x`. `git -C /x -C y commit -F m.md` → `/x/y`
  - `test_multiple_commits_in_one_command` — 2건이 각자 directory·출처를 가진다
  - `test_auto_sources` — `--fixup HEAD`, `--amend --no-edit`, MERGE_HEAD가 있는 repo의 `--no-edit`
  - `test_env_prefix_and_wrappers` — `GIT_AUTHOR_NAME=x env git commit -F m.md` → `file`
  - `test_nested_shell_commit_is_unknown` — `bash -c "git commit -F m.md"`는 cwd 기준 `unknown` 1건이다
  - `test_git_log_grep_commit_is_not_a_commit` — `git log --grep=commit`, `git show HEAD -- commit.md`, `bash -c "git log --grep=commit"`는 `()`다
- [ ] **Step 2: 실패 확인** — `python3 -m pytest -q tests/commit_gate/test_commit_scan.py`
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인**
- [ ] **Step 5: 커밋** — `feat(commit-gate): git commit 명령 해석기를 추가한다`

### Task 4: claude-config — 일회성 승인 저장소와 CLI

**Files:**
- Create: `hooks/commit_gate/grant.py`, `hooks/commit_gate/cli.py`
- Test: `tests/commit_gate/test_grant.py`

`pr_override_grant.py`는 저장소의 `.review/`에 저장하고 TTL이 없다(`created_at`만 기록, `:161`). 커밋 승인은 저장소 밖 상태 디렉터리에 두고 TTL을 건다.

**Interfaces:**
- Produces (`grant.py`):
  - `GRANT_TTL_SECONDS = 600`
  - `def state_dir(home: Path) -> Path` — `home/.local/state/claude-config/commit-gate`, 0700
  - `def make_binding(*, repo_root: Path, head: str, message_sha256: str, runtime: str) -> dict[str, str]` — head가 없으면(unborn) `"UNBORN"`이다.
  - `def record_grant(home: Path, *, binding: dict[str, str], reason: str, channel: Literal["tty", "relayed"], now: float) -> Path`
    - 파일명은 정렬된 binding JSON의 sha256이다.
    - 내용은 binding, reason(최대 512자), channel, created_at이다.
    - 0600, 최대 16 KiB, 원자적 교체로 쓴다.
  - `def consume_grant(home: Path, *, binding: dict[str, str], now: float) -> bool` — 다음 순서로 동작한다.
    1. 디렉터리 `flock(LOCK_EX)`을 잡는다.
    2. 같은 파일명이 있는지, 만료 전(`now - created_at <= GRANT_TTL_SECONDS`)인지, binding이 같은지 확인한다.
    3. unlink와 fsync를 한 뒤 True를 반환한다.

    만료된 파일은 지우고 False를 반환한다.
- Produces (`cli.py`):
  - `grant --runtime claude|codex --repo <path> (--message-file <path> | --message-sha256 <64-hex>) --reason "<사용자 답>" [--relayed]`
    - repo root, HEAD, 메시지 sha256으로 binding을 만든다. 메시지 sha256은 파일 바이트에서 계산하거나 직접 받는다. 직접 받는 형태는 `-m` 메시지용이며, 게이트의 차단 사유가 그 값을 알려 준다.
    - `--relayed`가 없으면 터미널에서 `commit`을 입력받아 확인한다.
    - 기록한 binding 요약을 출력한다.
  - `revoke --repo <path>` — 그 repo의 승인을 모두 지운다.

- [ ] **Step 1: 실패하는 테스트 작성**
  - `test_grant_allows_exactly_one_consume`
  - `test_grant_is_consumed_once` — 두 번째 consume은 False다
  - `test_grant_binds_message_hash` — 다른 message_sha256이면 False다
  - `test_grant_binds_head_and_runtime`
  - `test_grant_expires` — `now + 601`
  - `test_concurrent_consumers_one_winner` — 패턴 출처는 claude-config `tests/pre_pr_tribunal/test_gate_adapters.py:672`
  - `test_state_dir_and_file_modes` — 0700/0600
  - `test_cli_relayed_records_and_revoke`
- [ ] **Step 2: 실패 확인** — `python3 -m pytest -q tests/commit_gate/test_grant.py`
- [ ] **Step 3: 구현** — 모듈 docstring에 Global Constraints의 보증 수준 문장을 그대로 넣는다.
- [ ] **Step 4: 통과 확인**
- [ ] **Step 5: 커밋** — `feat(commit-gate): 커밋 1건용 일회성 승인을 추가한다`

### Task 5: claude-config — 게이트 판정과 Claude·Codex 훅 어댑터

**Files:**
- Create: `hooks/commit_gate/gate.py`, `hooks/commit_gate/hook_io.py`, `hooks/commit_gate/claude_hook.py`, `hooks/commit_gate/codex_hook.py`
- Test: `tests/commit_gate/test_gate.py`, `tests/commit_gate/test_hook_adapters.py`

`hook_io.py`는 `hooks/pre_pr_tribunal/hook_common.py`를 import하지 않는다. 같은 입출력 형식을 따로 구현해 두 게이트를 독립시킨다. 입력과 출력 형식은 다음과 같다.
- 입력: `hook_event_name`/`tool_name`/`tool_input.command`/`cwd`이며 snake_case와 camelCase를 모두 받는다. Codex의 tool name은 `exec_command`다.
- 출력: deny는 stdout `{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":…}}`과 exit 0이다. allow는 출력 없이 exit 0이다. 출처는 `hook_common.py:98-102`다.

**Interfaces:**
- Consumes: Task 2 `check_message()`, Task 3 `scan_commits()`, Task 4 `make_binding()`·`consume_grant()`
- Produces:
  - `@dataclass(frozen=True) class Decision: allow: bool; reason: str`
  - `def evaluate(command: str, *, cwd: Path, runtime: str, home: Path, now: float) -> Decision`
  - `def adapter_main(runtime: str) -> int` (`hook_io.py`)

판정 순서(`evaluate`)는 커밋마다 다음과 같다.
1. 디렉터리에서 `git rev-parse --show-toplevel`로 저장소 root를 구한다. 실패하면 통과한다(git이 스스로 실패한다).
2. origin이 jhw7500이 아니면 통과한다.
3. `unknown`이면 통과하고 stderr에 경고 한 줄을 남긴다(설계 §12). 메시지 파일을 읽지 못한 경우도 같다. git이 스스로 실패한다.
4. `auto`이면 통과한다.
5. `file`이면 파일을, `literal`이면 문자열을 `check_message()`에 넣는다. valid이면 통과한다.
6. 위반이면, 메시지 sha256으로 만든 binding에 대해 `consume_grant()`가 True일 때 통과한다. `file`과 `literal` 모두 해당한다. 그렇지 않으면 deny한다. 사유는 짧게 쓰고 다음을 넣는다.
   - finding 코드와 각 코드의 고침 방법 한 줄. 예: `heading-contract`(필드 누락·순서 오류, validator 실측 코드) → 필요한 필드 제목 5개를 순서대로, `title-type-enum` → 허용 type 목록.
   - `automation_commit`과 규칙 digest(SOURCE.json)
   - 승인 절차: 사용자에게 물은 뒤 `cli.py grant … --message-sha256 <값> --relayed`. 메시지 sha256 값을 사유에 그대로 넣는다.

명령 하나에 커밋이 여러 건이면 모두 판정한다. 하나라도 deny면 deny이고, 승인은 해당 커밋 것만 소비한다. 단, 다른 커밋이 deny이면 어떤 승인도 소비하지 않는다(판정을 먼저 끝내고 소비는 마지막에 한다). 게이트 내부 예외와 사본 digest 불일치는 통과 + stderr 경고다(fail-open, 설계 §12).

- [ ] **Step 1: 실패하는 테스트 작성** — `git_repo` fixture 패턴을 쓴다(claude-config `tests/pre_pr_tribunal/conftest.py:37`, tmp에 실제 `git init`). origin은 `git remote add origin https://github.com/jhw7500/demo.git`처럼 네트워크 없이 설정한다.
  - `test_valid_file_commit_passes`
  - `test_missing_fields_denied_with_codes_and_source` — 사유에 finding 코드와 `automation_commit` 값이 있다.
  - `test_unknown_message_passes_with_warning` — 편집기 커밋과 `-m "$MSG"`에서 allow이고, stderr 경고가 있다
  - `test_claude_heredoc_violation_denied_valid_passes` — 따옴표 heredoc 메시지가 실제로 검사된다
  - `test_auto_message_passes`
  - `test_non_jhw7500_origin_passes` — 외부 GitHub, GitLab, origin 없음
  - `test_gate_uses_commit_directory_origin` — cwd는 jhw7500 밖, `-C` 대상은 jhw7500
  - `test_grant_allows_one_then_denies`
  - `test_one_violation_blocks_compound_command` — 승인은 소비되지 않는다
  - `test_literal_violation_can_be_granted` — deny 사유의 sha256으로 승인하면 같은 `-m` 메시지가 1회 통과한다
  - `test_deny_reason_has_fix_hint` — 사유에 finding 코드별 고침 줄이 있다
  - `test_internal_error_fails_open` — `check_message` monkeypatch로 예외를 내면 allow이고 stderr 경고가 있다
  - `test_hook_adapters_emit_deny_shape` — Claude(`Bash`)·Codex(`exec_command`) payload를 stdin으로 넣고 stdout JSON 형식과 exit 0을 확인한다
- [ ] **Step 2: 실패 확인** — `python3 -m pytest -q tests/commit_gate/test_gate.py tests/commit_gate/test_hook_adapters.py`
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** — 이어서 `python3 -m pytest -q tests/pre_pr_tribunal`이 변경 전과 같은 결과인지 본다(tribunal 비간섭 보존 검사).
- [ ] **Step 5: 커밋** — `feat(commit-gate): 커밋 시점 게이트 판정과 훅 어댑터를 추가한다`

### Task 6: claude-config — 설치기와 지침 문장

**Files:**
- Create: `scripts/install-commit-gate.py`
- Modify: `install.sh:356-360` 다음 — tribunal 설치 뒤에 새 설치기를 호출하고 Codex `/hooks` trust 안내를 출력한다
- Modify: `claude-md/global-guidance.md:47`, `scripts/install-task-nudge.py:213`, `tests/task_nudge/test_change_evidence_guidance.py:21`(`SHARED_RULES`), `README.md:39`
- Test: `tests/commit_gate/test_installer.py`, `tests/task_nudge/test_change_evidence_guidance.py`

**Interfaces:**
- Consumes:
  - `scripts/lib/runtime_hook_installer.py`의 `merge_pre_tool_hook(original, *, matcher, command, legacy_commands, home)` (`:248`)
  - `apply_transaction` (`:1317`)
  - `read_python_source_group` (`:428`)
- Produces:
  - 설치 경로: `~/.local/share/claude-config/commit_gate/` (+ `vendor/`)
  - Claude 훅: `CLAUDE_COMMAND = "/usr/bin/python3 $HOME/.local/share/claude-config/commit_gate/claude_hook.py"`, matcher `"Bash"`
  - Codex 훅: `CODEX_COMMAND`는 `codex_hook.py`이며 matcher는 None이다. tribunal Codex 항목과 같은 방식이다(`~/.codex/hooks.json`).

설치기 구조는 `scripts/install-pre-pr-tribunal.py`를 따른다. 그 구성은 `PACKAGE_NAMES` 닫힌 목록, `read_validated_sources`, `merge_runtime_configs`, `build_plan`, `apply_transaction`, `main(--repo, --home)`이다. 설치 전에 `sync-commit-gate-vendor.py --check`를 실행하고, 실패하면 설치를 중단한다.

지침 문장 교체(모든 사본에 같은 바이트):
- 기존: `일반 git 명령을 추가로 차단하거나 Tribunal 재심사를 호출하지 않는다.`
- 새 문장: `jhw7500 저장소의 커밋 형식 위반은 커밋 시점 게이트가 막고, 사용자가 승인하면 그 커밋 1건만 통과한다. 그 밖의 git 명령을 차단하거나 Tribunal 재심사를 호출하지 않는다.`
- `README.md:39`의 "새 훅은 없으며" 부분은 게이트 훅을 언급하도록 고친다.

- [ ] **Step 1: 실패하는 테스트 작성**
  - `test_installer.py`
    - `test_install_copies_package_and_vendor`
    - `test_install_merges_claude_and_codex_hooks_idempotently` — 두 번 설치해도 항목은 1개다
    - `test_install_aborts_on_vendor_drift`
    - `test_tribunal_entries_untouched` — 기존 tribunal 항목 바이트가 보존된다
  - `SHARED_RULES`의 해당 항목을 새 문장으로 교체한다. 그러면 기존 `test_claude_and_codex_share_bounded_activation`이 문서 수정 전까지 실패한다.
- [ ] **Step 2: 실패 확인** — `python3 -m pytest -q tests/commit_gate/test_installer.py tests/task_nudge/test_change_evidence_guidance.py`
- [ ] **Step 3: 구현과 문장 교체**
- [ ] **Step 4: 통과 확인** — 같은 명령. 이어서 저장소 전체 `python3 -m pytest -q --continue-on-collection-errors`를 돌린다. 변경 전 기준선과 실패 집합이 같아야 한다. 기준선의 `slack_bolt` 수집 오류는 계획 1 ledger에 기록돼 있다.
- [ ] **Step 5: 커밋** — `feat(commit-gate): 게이트 설치기와 전역 지침 문장을 추가한다`

### Task 7: 통합 — PR·머지·배포·실환경 확인

각 단계는 사용자 승인 뒤에만 실행한다.

- [ ] **Step 1: automation PR** (Task 1)
  - `/pre-pr-tribunal` → PASS → canonical `gh pr create`
  - 리뷰 요청: `review:request` 라벨, `@claude review`, `@codex review`
  - 머지 → main 커밋 SHA를 기록한다(= vendor 출처)
- [ ] **Step 2: claude-config PR 2개** — floor 100 경로라 나눈다.
  - (a) Task 2·3·4: 사본·해석기·승인. 훅 연결이 없어 동작이 바뀌지 않는다.
  - (b) Task 5·6: 판정·어댑터·설치기·지침
  - 각각 tribunal → PR → 리뷰 → 머지
- [ ] **Step 3: 배포**
  1. claude-config main을 머지 커밋으로 맞춘다.
  2. 사용자가 `./install.sh`를 실행한다.
  3. 사용자가 Codex `/hooks`에서 새 항목을 trust한다.
- [ ] **Step 4: 실환경 확인** — 새 세션에서 tmp 저장소를 만든다(`git init`, `git remote add origin https://github.com/jhw7500/demo.git`). Claude Bash 도구로 다음을 실행해 기대 결과를 기록한다.
  - ① 필드 없는 메시지 `git commit -F bad.md` → deny(사유에 코드와 automation 커밋)
  - ② 계약 메시지 → 커밋 생성
  - ③ ①을 승인 CLI로 승인 → 1건 커밋 → 같은 메시지 재시도 → deny
  - ④ `git commit -m "$(cat <<'EOF' …)"` → 계약 메시지는 커밋되고, 필드 없는 메시지는 deny
  - ④' `git commit`(편집기) → 막지 않음, stderr 경고
  - ⑤ origin을 `https://gitlab.com/x/y.git`로 바꾼 뒤 ① → 통과

  Codex에서는 ①·②만 반복한다.
- [ ] **Step 5: 설치본 확인** — 아래 셋을 확인한다.
  - `~/.local/share/claude-config/commit_gate/vendor/SOURCE.json`의 `automation_commit`이 Step 1의 SHA와 같다
  - `python3 scripts/sync-commit-gate-vendor.py --check`가 exit 0이다
  - `grep -c '커밋 시점 게이트가 막고' ~/.claude/global-guidance.md ~/.codex/AGENTS.md`가 각 1이다

## 계획 3으로 넘기는 것

- automation CI의 Python 3.10 실측은 계획 3 Task 3에서 한다(봉인은 하지 않음, 설계 §12).
- fleet `automation_ref`가 새 릴리스로 오르면 Task 2의 sync를 그 커밋으로 다시 실행한다. 판정 동등성은 SHA가 아니라 SOURCE.json의 파일 digest로 비교한다. digest가 같으면 `automation_commit`만 갱신된다.
