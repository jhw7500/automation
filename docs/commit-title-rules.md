# Commit title rules

jhw7500 fleet의 커밋 제목 규칙 정본 산문이다. 구현은 `scripts/commit_title_rules.py`,
메시지 검사 실행기는 `scripts/check_commit_message.py`이고, 둘 다 Python 3.10 표준 라이브러리만 쓴다.
커밋 시점 게이트와 CI audit가 같은 automation 커밋의 바이트로 같은 판정을 내린다.
설계 근거는 `docs/superpowers/specs/2026-10-19-commit-format-unification-design.md` §11이다.

이 파일들은 릴리스 인벤토리에 넣지 않는다(설계 §12).

## RULES_VERSION

`RULES_VERSION = "1"`. 규칙 집합이 바뀔 때만 올린다.

## 헤더 문법

첫 줄만 본다. 정규식은 `^(\w*)(?:\((.*)\))?!?: (.*)$`이고 `re.ASCII`로 컴파일한다(JS `\w`와 같은 ASCII).
맞지 않으면 type과 subject를 비어 있다고 본다.

## type 목록

`feat`, `fix`, `chore`, `refactor`, `docs`, `test`, `build`, `ci`, `perf`, `style` — 대소문자를 구분한다.

| type | 용도 |
|---|---|
| `feat` | 기능 추가 |
| `fix` | 결함 수정 |
| `chore` | 잡무 |
| `refactor` | 동작 변경 없는 구조 개선 |
| `docs` | 문서 |
| `test` | 테스트 |
| `build` | 빌드 |
| `ci` | CI |
| `perf` | 성능 |
| `style` | 서식 |

## 오류 코드 (6종)

| 코드 | 조건 |
|---|---|
| `title-type-enum` | 비어 있지 않은 type이 type 목록에 없다 |
| `title-type-case` | type이 소문자가 아니다 |
| `title-type-empty` | type이 없다(헤더가 문법에 맞지 않는 경우 포함) |
| `title-subject-empty` | subject가 없다(헤더가 문법에 맞지 않는 경우 포함) |
| `title-subject-full-stop` | 첫 줄이 `.`로 끝난다(`...`로 끝나면 제외). 헤더가 문법에 맞지 않아도 판정한다 |
| `title-header-trim` | 첫 줄 앞뒤에 공백이 있다 |

72자 제한은 계약 validator의 `commit-title-too-long`만 맡는다. 제목 규칙은 중복 finding을 내지 않는다.

검사기(`check_commit_message.py`)가 따로 내는 코드가 하나 있다. `document-too-large`는 Change Evidence Contract v1의
크기 한도(`MAX_BYTES`, 64 KiB)를 넘은 메시지에 붙는다. `validate_text()`에는 크기 검사가 없어 검사기가 대신 강제하며,
이 경우 validator는 호출하지 않고 자동 메시지로도 취급하지 않는다.

## 뺀 규칙

`header-max-length` 100(validator 72로 대체), `body-max-line-length`·`footer-max-line-length`(해제),
`subject-case`(해제), 경고 등급 `body-leading-blank`·`footer-leading-blank`, `review-fix` 예외.

## 자동 메시지

commitlint `@commitlint/is-ignored` 19.8.1의 기본 목록이다. 아래에 맞으면 제목 규칙과 validator를 모두 건너뛴다.
JS 정규식과 같은 뜻으로 옮겼다(`.`와 멀티라인 `^`·`$`는 `\n`, `\r`, `U+2028`, `U+2029`를 줄 끝으로 본다).

1. `^((Merge pull request)|(Merge (.*?) into (.*?)|(Merge branch (.*?)))(?:\r?\n)*$)` (multiline)
2. `^(Merge tag (.*?))(?:\r?\n)*$` (multiline)
3. `^(R|r)evert (.*)`
4. `^(R|r)eapply (.*)`
5. `^(amend|fixup|squash)!`
6. 첫 줄에서 `^chore(\([^)]+\))?:`를 떼고 trim한 값이 semver: npm `semver.valid()`처럼 앞의 `v` 하나를 허용하는 SemVer 2.0.0 정식 문법(엄격 모드)
7. `^(Merged (.*?)(in|into) (.*)|Merged PR (.*): (.*))`
8. `^Merge remote-tracking branch(\s*)(.*)`
9. `^Automatic merge(.*)`
10. `^Auto-merged (.*?) into (.*)`

자동 메시지의 멀티라인 패턴은 본문 줄에도 맞는다(is-ignored와 같다). 자동 메시지는 제목 규칙과 validator를 모두 건너뛰므로
게이트도 CI audit도 그런 커밋을 검사하지 않는다.

## 알려진 차이

commitlint와 남아 있는 차이다.

- 숫자로 시작하는 type(`2FA: x`)은 여기서 `title-type-case`가 붙고 commitlint는 붙이지 않는다(둘 다 `type-enum`은 실패).
- `HEADER_RE`의 `.`는 `\r`와 U+2028에도 맞는데 JS는 아니다(validator는 그런 제목을 거부한다).
- `header-trim`의 공백 집합이 다르다: JS trim은 BOM을 포함하고 Python strip은 `\x1c`-`\x1f`를 포함한다.
- 앞쪽 빈 줄은 바이트 그대로 검사한다(commitlint는 trim, validator도 거부한다).

## 1차 출처

config-conventional 19.1.0 `lib/index.js` 규칙 목록, is-ignored 19.8.1 `lib/defaults.js`,
conventional-changelog-conventionalcommits 7.0.2 `parserOpts.js:7`. 2026-10-23 npm 레지스트리·unpkg에서 받았다.

fleet 소비자가 실제로 돌린 것은 `@commitlint/is-ignored`·`rules` 19.0.3(wagoid/commitlint-github-action v6.2.1 lockfile)이다.
19.8.1은 `Reapply`와 `amend!`를 추가로 무시하므로 상위 집합이다.

## 소스 digest

`check_commit_message.source_digests()`는 `validate_change_evidence.py`, `commit_title_rules.py`,
`check_commit_message.py`의 sha256을 돌려준다. 게이트 사본은 이 digest로 비교한다.
