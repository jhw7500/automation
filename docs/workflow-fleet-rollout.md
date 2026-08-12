# Workflow Fleet Rollout

`scripts/rollout_workflow_fleet.py`는 중앙 reusable workflow caller와 필요한
repository secret 전제조건을 한 실행에서 조율한다. GitHub는 Git PR과 secret write를
하나의 transaction으로 제공하지 않으므로, 도구는 둘을 원자적이라고 주장하지 않는다.
대신 secret 전제조건을 먼저 통과한 저장소만 독립 branch/PR로 게시한다.

## 보존하는 것

- 기존 workflow 파일과 caller job만 처리하고 새 workflow를 강제로 추가하지 않는다.
- 저장소별 trigger, `if` guard, `permissions`, 기존 `with` 입력을 보존한다.
- 중앙 release ref, `secrets` mapping, 지원되는 `app_id`, `automation_ref`만 변경한다.
- caller가 없는 저장소는 skip한다.
- required secret이 없고 안전한 source도 없으면 해당 저장소만 blocked 처리한다.

## Secret source

- 이미 존재하는 repository secret은 값을 읽거나 다시 쓰지 않는다.
- `CLAUDE_CODE_OAUTH_TOKEN` 누락 시에만 `~/.claude/.credentials.json`을 source로 쓸 수 있다.
- 그 외 누락 secret은 같은 이름의 환경변수가 있을 때만 쓸 수 있다.
- `--sync-missing-secrets`가 없으면 이름 검사만 하며 어떠한 secret도 쓰지 않는다.
- Claude token의 지속적인 rotation은 `personal-ops/claude-token-sync`가 담당한다.

## 실행

workspace는 reset/clean 가능한 전용 disposable clone 디렉터리여야 한다. 최초 한 번
명시적으로 초기화한다.

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
  --confirm \
  --actionlint /path/to/actionlint
```

각 실행은 `rollout-manifest.json`에 base/head/PR/blocked 결과를 남긴다. 한 저장소가
blocked여도 다른 저장소의 준비는 계속하지만 명령은 non-zero로 끝난다.

## Merge와 rollback

PR 생성은 한 명령으로 가능하지만 merge는 CI 결과를 확인해 배치별로 수행한다.
문제 발생 시 해당 저장소 PR을 닫거나 branch를 삭제한다. merge 후에는 ref만 되돌리지
말고 직전 ref와 직전 `secrets` mapping을 함께 복원한다. release tag는 이동하지 않는다.
