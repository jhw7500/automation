# Workflow Fleet Rollout

`scripts/rollout_workflow_fleet.py`는 중앙 reusable workflow caller와 필요한
repository secret 전제조건을 한 실행에서 조율한다. GitHub는 Git PR과 secret write를
하나의 transaction으로 제공하지 않으므로, 도구는 둘을 원자적이라고 주장하지 않는다.
대신 저장소 metadata와 caller 계약을 준비·검증한 뒤 secret을 쓰고, 검증을 통과한
저장소만 독립 branch/PR로 게시한다.

## 보존하는 것

- 기존 workflow 파일과 caller job만 처리하고 새 workflow를 강제로 추가하지 않는다.
- 저장소별 trigger, `if` guard, 기존 `with` 입력을 보존한다. `permissions`도 보존하되,
  OpenCode 두 caller는 OIDC 제거와 review 출력에 필요한 최소 계약으로 정규화한다.
- 중앙 release ref, `secrets` mapping, 지원되는 `app_id`, `automation_ref`만 변경한다.
- caller가 없는 저장소는 skip한다.
- required secret이 없고 안전한 source도 없으면 해당 저장소만 blocked 처리한다.

## Secret source

- 이미 존재하는 repository secret은 값을 읽거나 다시 쓰지 않는다.
- `CLAUDE_CODE_OAUTH_TOKEN` 누락 시에만 `~/.claude/.credentials.json`을 source로 쓸 수 있다.
- 그 외 누락 secret은 같은 이름의 환경변수가 있고 `--allow-env-secret NAME`으로
  그 이름을 이번 실행에 명시 승인했을 때만 쓸 수 있다. 승인된 이름은 값 없이
  preflight 출력에 표시된다.
- `--sync-missing-secrets`가 없으면 이름 검사만 하며 어떠한 secret도 쓰지 않는다.
- 이름은 존재하지만 값이 만료된 키는 `--refresh-secret NAME`으로 명시 회전할 수 있다.
  이 옵션은 `publish + --confirm + --sync-missing-secrets`에서만 허용되고, 같은 이름의
  `--allow-env-secret NAME`(또는 Claude 전용 fan-out 승인)이 반드시 필요하다. 기존 값은
  읽지 않으며 새 값도 argv, 로그, manifest에 기록하지 않는다.
- refresh 이름은 해당 저장소의 managed caller가 실제 요구하는 secret이어야 하며,
  metadata/caller/actionlint 검증이 실패하거나 caller가 없으면 값을 쓰지 않는다.
- 개인 Claude OAuth token fan-out은 추가로 `--allow-personal-oauth-fanout`을 명시해야
  한다. 이 옵션은 저장소마다 자격증명을 복제해 blast radius를 넓히므로, 이미 배포된
  `personal-ops/claude-token-sync`의 대상/rotation 정책을 승인한 운영에서만 사용한다.
- Claude token의 지속적인 rotation은 `personal-ops/claude-token-sync`가 담당한다.

## 실행

workspace는 reset/clean 가능한 전용 disposable clone 디렉터리여야 한다. 최초 한 번
명시적으로 초기화한다.
HTTPS clone/push는 ambient `GITHUB_TOKEN`/`GH_TOKEN`을 사용하지 않으므로 사전에
`gh auth login`과 `gh auth setup-git`이 완료돼 있어야 한다.

```bash
# 읽기 전용 계획. 원격 secret/variable은 이름만 조회한다.
python3 scripts/rollout_workflow_fleet.py \
  --workspace /path/to/disposable-fleet \
  --initialize-workspace \
  --mode plan \
  --actionlint /path/to/actionlint

# 로컬 branch 준비까지만 수행
python3 scripts/rollout_workflow_fleet.py \
  --workspace /path/to/disposable-fleet \
  --mode prepare \
  --repo example-repo \
  --actionlint /path/to/actionlint

# 필요한 secret이 있으면 먼저 동기화한 뒤 저장소별 PR 생성
python3 scripts/rollout_workflow_fleet.py \
  --workspace /path/to/disposable-fleet \
  --mode publish \
  --sync-missing-secrets \
  --allow-personal-oauth-fanout \
  --allow-env-secret GEMINI_API_KEY \
  --confirm \
  --actionlint /path/to/actionlint

# 만료된 기존 키 교체 + 누락 키 보충 + workflow PR 생성을 한 번에 수행
ZHIPU_API_KEY=... python3 scripts/rollout_workflow_fleet.py \
  --workspace /path/to/disposable-fleet \
  --ref v1.36 \
  --mode publish \
  --sync-missing-secrets \
  --allow-env-secret ZHIPU_API_KEY \
  --refresh-secret ZHIPU_API_KEY \
  --repo wlan-driver \
  --repo wlan-driver-v2 \
  --repo wlan-opc \
  --confirm \
  --actionlint /path/to/actionlint
```

`plan`과 `prepare`에서는 `--sync-missing-secrets`를 지정할 수 없으며 parser가 즉시
거부한다. 실제 repository secret write는 `publish + --sync-missing-secrets +
--confirm` 조합에서만 가능하다. `--refresh-secret`은 기존 secret도 의도적으로 덮어쓰므로
실수로 전체 fleet를 덮어쓰지 않도록 하나 이상의 대상 `--repo`를 반드시 지정해야 한다.

각 실행은 `rollout-manifest.json`에 base/head/PR/blocked 결과를 남긴다. 한 저장소가
blocked여도 다른 저장소의 준비는 계속하지만 명령은 non-zero로 끝난다.
여러 secret 중 일부를 쓴 뒤 후속 secret write나 workflow 준비가 실패한 경우에도,
실제로 완료된 secret **이름**은 blocked 항목에 남긴다(값은 기록하지 않는다).
동일 release branch를 다시 게시할 때는 push 직전 원격 SHA를 조회해 exact
`--force-with-lease=<ref>:<sha>`로 보호하므로 다른 실행이나 사람이 갱신한 branch를
덮어쓰지 않는다.

계약은 현재 checkout이 아니라 지정한 local+remote release tag artifact에서 직접
추출한다. 따라서 `--ref v1.36`을 사용하면 branch도
`codex/automation-v1.36-fleet`로 분리되고, 원격 tag와 local tag가 같은 commit인지
검증되지 않으면 어떤 소비 저장소도 수정하지 않는다.

## Merge와 rollback

PR 생성은 한 명령으로 가능하지만 merge는 CI 결과를 확인해 배치별로 수행한다.
문제 발생 시 해당 저장소 PR을 닫거나 branch를 삭제한다. merge 후에는 ref만 되돌리지
말고 직전 ref와 직전 `secrets` mapping을 함께 복원한다. release tag는 이동하지 않는다.
