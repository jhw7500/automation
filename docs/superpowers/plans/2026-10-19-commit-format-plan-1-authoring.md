# 커밋 형식 통일 — 계획 1: 작성 경로 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Claude·Codex가 쓰는 모든 커밋 작성 경로(계약 산문, 작성 스킬, 저장소 지침, 전역 지침, tribunal 수정 커밋 지시)에 "기계가 읽는 부분은 원문, 내용은 한국어" 규칙을 넣고 live에 배포한다.

**Architecture:** 세 저장소에 저장소당 PR 하나씩. 규칙의 정본 문장은 계약 문서(영문 산문)에 두고, 작성 스킬과 전역 지침에는 같은 뜻의 한국어 문장을 둔다. 각 문장의 존재는 그 저장소의 기존 계약 테스트에 단언을 추가해 고정한다. 검증 동작(validator, 필드, pin)은 바꾸지 않는다.

**Tech Stack:** Markdown 문서·스킬, Node `node:assert` 스크립트 테스트(jhw-notion), pytest(automation, claude-config).

**Spec:** `docs/superpowers/specs/2026-10-19-commit-format-unification-design.md` (이 브랜치, 커밋 `716573b`) — §5-A, §5-B 구현. 게이트(§5-E)는 계획 2, CI audit·정리(§5-C, D)는 계획 3.

## Global Constraints

- 언어 규칙: 한국어 = 제목의 결과문(`type(scope): ` 뒤)과 각 필드의 내용. 원문 유지 = `type`, `scope`, 필드 제목(`### Why` 등), 트레일러, 명령·경로·식별자·오류 코드.
- 모든 저장소에 예외 없이 같은 규칙(공개 저장소 포함).
- 계약 v1의 field names/order, requiredness, sentinel, finding 의미와 validator 코드를 바꾸지 않는다.
- 계약 pin `0d97a63891ba4473a3a189eae643f8059b76eb56`을 바꾸지 않는다(스킬, 전역 지침, installer, README, 테스트 모두). 이번 변경은 validator 바이트가 아니라 산문이다.
- 이 작업의 커밋도 새 규칙을 따른다: `type(scope): 한국어 결과`, 필드 내용 한국어, `git commit --cleanup=verbatim --file <msg>`, 커밋 전 validator `--kind commit --expected-version v1` 통과.
- push·PR 생성·머지·배포는 각각 실행 직전 사용자 승인. 구현을 서브에이전트에 맡기면 프롬프트에 push·PR·머지·브랜치 전환 금지를 적는다.
- PR은 저장소당 1개(총 3개), pre-PR tribunal canonical command(`PATH=/usr/bin:/bin /usr/bin/gh pr create --base <base> --title … --body-file …`)로만 만든다.
- 작업 브랜치는 각 저장소의 `.worktrees/` 아래 worktree에서 만든다. 공유 main 작업본에서 브랜치를 바꾸지 않는다.

## Prerequisite — 추적 Issue (사용자 승인 필요)

각 커밋·PR의 `References`/`Related issue`에 쓸 Issue가 필요하다. 권장: automation에 이 작업(계획 1~3)의
추적 Issue를 `/jhw:issue`로 만들고 설계 문서를 링크한다. 승인 전에는 만들지 않는다. 승인되지 않으면
`https://github.com/jhw7500/automation/issues/194`(계약 v1 정의, rollout을 범위에 둠)를 `Refs`로 쓴다.
이하 `<ISSUE_URL>`은 이 값이다.

## Review Focus

1. 기존 호스트의 `~/.codex/AGENTS.md`에 **이전 버전** Change Evidence 블록이 있는 상태에서 재설치하면 새 블록으로 교체되고 블록은 1개여야 한다 → Task 3 Step 1의 `test_change_evidence_block_upgrades_previous_text`.
2. 한국어 제목이 Git 기본 cleanup을 거쳐도 바이트 그대로 남고 72자 검사(문자 수)를 통과해야 한다 → Task 2 Step 1의 values 교체.
3. tribunal 수정 커밋 문구를 바꾼 뒤에도 커밋 본문에 `review-fix round N` 문자열이 남아 `git log --grep='review-fix round'` 집계가 유지돼야 한다 → Task 3 Step 1의 `test_review_fix_commit_instruction_uses_change_evidence_format`.
4. 머지만으로는 live가 바뀌지 않는다. 배포 뒤 새 세션이 읽는 live 파일에 규칙이 있어야 한다 → Task 4 Step 5 readback.
5. 계약 문서에 절을 추가해도 validator 판정이 바뀌지 않아야 한다 → Task 1 Step 4의 기존 `tests/test_change_evidence.py` 전체 통과(보존 검사).

---

### Task 1: automation — 계약 산문과 저장소 지침

**Files:**
- Modify: `docs/change-evidence-contract-v1.md` — "Commit fields" 절 끝(템플릿 문단 "…with visible evidence before validation." 다음)
- Modify: `AGENTS.md:83-84`
- Test: `tests/test_change_evidence.py`

**Interfaces:**
- Produces: 계약 문서의 `### Language convention (not validated)` 절. Task 2·3의 문장은 이 절과 같은 뜻이어야 한다.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_change_evidence.py`에 추가

```python
def test_contract_documents_unvalidated_commit_language_convention() -> None:
    text = (ROOT / "docs/change-evidence-contract-v1.md").read_text()
    commit_section = text.split("## Commit fields", 1)[1].split("\n## ", 1)[0]
    assert "### Language convention (not validated)" in commit_section
    assert "SHOULD be written in Korean" in commit_section
    assert "The validator does not check language" in commit_section
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m pytest tests/test_change_evidence.py::test_contract_documents_unvalidated_commit_language_convention -q`
Expected: FAIL (`AssertionError` — 절 없음)

- [ ] **Step 3: 계약 문서에 절 추가** — 정확한 문구:

```markdown
### Language convention (not validated)

The title's result text (after any `type(scope): ` prefix) and the content of every field
SHOULD be written in Korean. Machine-read tokens stay as written: the `type(scope)` prefix,
field headings, trailers, commands, paths, identifiers, and error codes. The validator does
not check language, and this convention changes no field, order, requiredness, or finding.
```

- [ ] **Step 4: 통과 확인 + 보존 검사**

Run: `python3 -m pytest tests/test_change_evidence.py -q`
Expected: 새 테스트 포함 전부 PASS, 기존 테스트 수 변화 없음(실행 전후 수집 수를 기록).

- [ ] **Step 5: `AGENTS.md:83-84` 교체** — 정확한 문구:

```text
  - 계약이 요구하는 제목 규칙은 결과 중심·72자 이하뿐이다. 제목은 영문 `type(scope): ` 또는 `type: `
    접두어(scope는 선택) 뒤에 한국어 결과를 쓰고, 필드 내용도 한국어로 쓴다(계약 "Language convention").
```

- [ ] **Step 6: 커밋** — `docs(evidence): 커밋 언어 관례를 계약에 추가한다` (필드 내용 한국어, `References`에 `<ISSUE_URL>`)

### Task 2: jhw-notion — 작성 스킬과 저장소 지침

**Files:**
- Modify: `skills/claude/commit.md` — 「작성 순서」와 「메시지 형식」 사이에 「언어」 절 추가, 템플릿 제목 자리표시자 교체
- Modify: `AGENTS.md:101`
- Regenerate: `skills/codex/jhw-commit/` (`node scripts/sync-codex-skills.mjs`)
- Test: `scripts/test-commit-skill-contract.mjs`

**Interfaces:**
- Consumes: Task 1의 규칙(같은 뜻의 한국어 문장).

- [ ] **Step 1: 실패하는 단언 추가** — `scripts/test-commit-skill-contract.mjs`
  - 기존 `assert.match(skill, …)` 묶음 뒤에(이 파일의 `agents`는 `skills/COMMANDS.md`이므로 저장소
    `AGENTS.md`는 따로 읽는다):

```js
const repoAgents = await readFile(join(root, "AGENTS.md"), "utf8");
assert.match(skill, /^## 언어$/m);
assert.match(skill, /제목의 결과문\(`type\(scope\): ` 뒤\)과 필드 내용은 한국어로 쓴다\./);
assert.match(skill, /`type`·`scope`·필드 제목·트레일러·명령·경로·식별자·오류 코드는 원문 그대로 둔다\./);
assert.doesNotMatch(repoAgents, /관례는 영문 `type\(scope\): 결과`다\./);
```

  - 템플릿 단언 뒤에 `assert.match(template, /^<type\(scope\): [^>\n]+>$/m);`
  - `values`의 다섯 값을 한국어 내용으로 교체(첫 값은 `feat(commit): 직접 커밋에도 변경 증거를 남긴다`, 마지막 Issue URL은 그대로). 기존 Git cleanup 왕복 단언이 한국어 제목을 검사하게 된다.

- [ ] **Step 2: 실패 확인**

Run: `node scripts/test-commit-skill-contract.mjs`
Expected: exit 1, `^## 언어$` 단언에서 실패

- [ ] **Step 3: `commit.md` 수정**
  - 「언어」 절, 정확한 문구:

```markdown
## 언어

제목의 결과문(`type(scope): ` 뒤)과 필드 내용은 한국어로 쓴다. `type`·`scope`·필드 제목·트레일러·명령·경로·식별자·오류 코드는 원문 그대로 둔다. 저장소와 작성 도구(Claude Code·Codex 등)에 관계없이 같다.
```

  - 템플릿 첫 줄 `<72자 이내의 결과 중심 제목>` → `<type(scope): 72자 이내의 결과 중심 한국어 제목>`.

- [ ] **Step 4: `AGENTS.md:101` 교체** — 해당 문장을 `관례는 영문 `type(scope): ` 접두어 뒤 한국어 결과이고, 필드 내용도 한국어로 쓴다(`skills/claude/commit.md` 「언어」).`로 바꾼다.

- [ ] **Step 5: Codex 생성물 갱신과 통과 확인**

Run: `node scripts/sync-codex-skills.mjs && node scripts/sync-codex-skills.mjs --check && node scripts/test-commit-skill-contract.mjs && node scripts/test-pr-skill-contract.mjs && node scripts/test-claude-command-dir-contract.mjs`
Expected: 모두 exit 0 (각 명령의 종료코드를 따로 기록)

- [ ] **Step 6: 커밋** — `docs(commit): 커밋 내용을 한국어로 쓰는 규칙을 추가한다` (생성물 포함 같은 커밋, `References`에 `<ISSUE_URL>`)

### Task 3: claude-config — tribunal 수정 커밋 지시와 전역 지침

**Files:**
- Modify: `skills/pre-pr-tribunal/SKILL.md:33` — 한 문장 교체
- Modify: `claude-md/global-guidance.md` — 「Change Evidence 작성 활성화 (전역)」에 한 줄 추가
- Modify: `scripts/install-task-nudge.py` — `change_evidence_policy_block()`에 같은 줄 추가
- Test: `tests/task_nudge/test_change_evidence_guidance.py`, `tests/pre_pr_tribunal/test_policy.py`

**Interfaces:**
- Consumes: Task 1의 규칙.
- Produces: 공유 규칙 줄 `LANGUAGE_RULE` (아래 Step 3의 문구). 계획 2가 같은 블록의 "일반 git 명령을 추가로 차단하거나 …" 문장을 고칠 때 이 줄은 유지한다.

- [ ] **Step 1: 실패하는 테스트 작성**
  - `test_change_evidence_guidance.py`의 `SHARED_RULES`에 두 문자열 추가: `"커밋 제목의 결과문과 필드 내용은 한국어로 쓰고,"`, `"작성 스킬을 거치지 않은 커밋에도 적용한다."`
  - 같은 파일에 업그레이드 테스트 추가(Review Focus 1):

```python
def test_change_evidence_block_upgrades_previous_text(installer):
    current = installer.change_evidence_policy_block()
    previous = "".join(line for line in current.splitlines(keepends=True) if "한국어로 쓰고" not in line)
    assert previous != current
    old = installer.merge_change_evidence_block("keep-before\n", previous)
    upgraded = installer.merge_change_evidence_block(old, current)
    assert upgraded == installer.merge_change_evidence_block("keep-before\n", current)
    assert upgraded.count(installer.CHANGE_EVIDENCE_START) == 1
```

  - `test_policy.py`에 추가(Review Focus 3):

```python
def test_review_fix_commit_instruction_uses_change_evidence_format():
    skill = (
        Path(__file__).resolve().parents[2] / "skills/pre-pr-tribunal/SKILL.md"
    ).read_text(encoding="utf-8")
    assert "Create one `review-fix round N` commit" not in skill
    assert "use the Change Evidence commit format with a conventional `type(scope):` title" in skill
    assert "record `review-fix round N` and the addressed finding IDs in its `Why` field" in skill
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m pytest tests/task_nudge/test_change_evidence_guidance.py tests/pre_pr_tribunal/test_policy.py -q`
Expected: `test_claude_and_codex_share_bounded_activation`, `test_change_evidence_block_upgrades_previous_text`(previous == current), `test_review_fix_commit_instruction_uses_change_evidence_format` FAIL

- [ ] **Step 3: 공유 줄 추가** — `global-guidance.md` 절의 "- 이 지침은 안내 전용이며 …" 줄 바로 앞, 그리고 `change_evidence_policy_block()`의 같은 위치에 똑같은 줄:

```text
- 커밋 제목의 결과문과 필드 내용은 한국어로 쓰고, `type(scope)`·필드 제목·트레일러·명령·경로·식별자·오류 코드는 원문 그대로 둔다. 작성 스킬을 거치지 않은 커밋에도 적용한다.
```

- [ ] **Step 4: `SKILL.md:33` 문장 교체** — `Create one `review-fix round N` commit only if the new request includes committing.` →

```text
Create one commit for the round only if the new request includes committing: use the Change Evidence commit format with a conventional `type(scope):` title, and record `review-fix round N` and the addressed finding IDs in its `Why` field.
```

- [ ] **Step 5: 통과 확인**

Run: `python3 -m pytest tests/task_nudge/test_change_evidence_guidance.py tests/pre_pr_tribunal/test_policy.py -q` → 전부 PASS.
`test_change_evidence_block_upgrades_previous_text`만 남아 실패하면 `_merge_managed_block`이 기존 블록을
교체하지 않는 것이다. 마커 사이 내용을 새 블록으로 교체하도록 고치고(마커 누락·중첩 거부는 유지),
기존 `test_malformed_change_evidence_block_is_rejected`·`test_nested_or_partial_managed_blocks_fail_closed`가
계속 통과하는지 함께 본다.
그다음 저장소 전체 테스트 1회: `python3 -m pytest -q` → 변경 전 기준선과 같은 실패 집합(기준선은 수정 전에 같은 명령으로 기록).

- [ ] **Step 6: 커밋** — `docs(tribunal): 수정 커밋과 전역 지침에 한국어 커밋 규칙을 넣는다` (`References`에 `<ISSUE_URL>`)

### Task 4: 통합 — PR·머지·배포·live 확인

각 단계는 사용자 승인 뒤에만 실행한다.

- [ ] **Step 1: PR 3개** — 저장소마다 `/pre-pr-tribunal` → PASS → canonical `gh pr create` → `review:request` 라벨과 `@codex review`(저장소 관례). 순서는 automation → jhw-notion → claude-config. automation PR에는 이 브랜치의 설계·계획 문서가 함께 들어간다.
- [ ] **Step 2: 머지** — 각 저장소의 reviewed-head 머지 경로. 머지 뒤 각 main에서 Task 1~3의 테스트 명령을 다시 실행해 exit 0 확인.
- [ ] **Step 3: jhw-notion 배포** — README 절차(`--prepare` → `--status`의 `clear: true`·`uncertain: 0` → `--activate`)를 Claude 세션 밖에서 사용자가 실행. 대기 중인 이전 release가 있으면 `--status`로 먼저 확인한다.
- [ ] **Step 4: claude-config 배포** — README 「Change Evidence 안내」의 순서대로 jhw-notion 활성화를 확인한 뒤 `./install.sh` 재실행. tribunal Skill의 live 경로(현재 `worktrees/claude-config-tribunal-runtime-0e6085d`)를 새 커밋으로 옮기는 방법은 README 「pre-PR tribunal」과 기존 런타임 관례를 확인해 따른다. Codex는 `/hooks`에서 hash 변경을 직접 trust한다.
- [ ] **Step 5: live readback** — 아래가 각각 1건 이상 나와야 한다:

```bash
grep -c '한국어로 쓰고' ~/.claude/global-guidance.md
grep -c '한국어로 쓰고' ~/.codex/AGENTS.md
grep -c '^## 언어$' ~/.claude/commands/jhw/commit.md
grep -rc '^## 언어$' "$(readlink -f ~/.codex/skills/jhw-commit)"/references/commit.md
grep -c 'record `review-fix round N`' "$(readlink -f ~/.claude/skills/pre-pr-tribunal)"/SKILL.md
```

## 다음 계획으로 넘기는 것

- 계획 2(커밋 시점 게이트): 공유 블록의 "일반 git 명령을 추가로 차단하거나 Tribunal 재심사를 호출하지 않는다."(`SHARED_RULES` 마지막 항목)는 게이트와 충돌하므로 계획 2에서 함께 고친다. `/jhw:pr` auto-fix 커밋은 메시지 형식을 정의하지 않으므로 전역 지침 줄과 게이트가 맡는다.
- 계획 3(CI audit·정리): claude-config `.github/commitlint.config.mjs`의 `review-fix` 예외와 그 주석은 Task 3 뒤 쓸모가 없어지지만, 저장소별 commitlint 제거(설계 §5-D)에서 함께 지운다.
