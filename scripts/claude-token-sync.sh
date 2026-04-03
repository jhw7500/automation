#!/bin/bash
# Claude OAuth accessToken → GitHub 레포 시크릿 자동 동기화 데몬
# credentials 파일이 없으면 60초 간격으로 대기 후 재시도
# 파일 존재 시 inotifywait로 변경 감지하여 즉시 동기화

CRED_FILE="$HOME/.claude/.credentials.json"
LOG_FILE="$HOME/.claude/token_sync.log"
REPOS=(
    wlan-package wlan-driver max9296 gstApp wlan-bridge streamApp automation
    cts-email-mcp-server cts-ta-mcp-server cts-ta-webapp
    pim-check redmine sc16is7xx wpa-supplicant
)
WAIT_INTERVAL=60

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"; }

log "[START] claude-token-sync daemon (pid=$$)"

# Phase 1: credentials 파일이 생길 때까지 대기
while [ ! -f "$CRED_FILE" ]; do
    log "[WAIT] $CRED_FILE not found, retrying in ${WAIT_INTERVAL}s"
    sleep "$WAIT_INTERVAL"
done

# Phase 2: 유효한 accessToken이 나타날 때까지 대기
PREV_TOKEN=""
while true; do
    PREV_TOKEN=$(jq -r '.claudeAiOauth.accessToken // empty' "$CRED_FILE" 2>/dev/null)
    if [ -n "$PREV_TOKEN" ]; then
        break
    fi
    log "[WAIT] accessToken empty, retrying in ${WAIT_INTERVAL}s"
    sleep "$WAIT_INTERVAL"
done

EXPIRES=$(jq -r '.claudeAiOauth.expiresAt // 0' "$CRED_FILE" 2>/dev/null)
EXPIRES_DATE=$(date -d @$((EXPIRES / 1000)) '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo "unknown")
log "[READY] token=${PREV_TOKEN:0:20}... expires=${EXPIRES_DATE}"

# Phase 3: 변경 감지 및 동기화
sync_repos() {
    local token="$1"
    local fail=0
    for repo in "${REPOS[@]}"; do
        if echo "$token" | gh secret set CLAUDE_CODE_OAUTH_TOKEN --repo "jhw7500/$repo" 2>/dev/null; then
            log "[SYNC] $repo OK"
        else
            log "[SYNC] $repo FAIL"
            fail=$((fail + 1))
        fi
    done
    log "[SYNC] completed (${#REPOS[@]} repos, $fail failures)"
}

while true; do
    # inotifywait가 실패하면 (파일 삭제 등) polling fallback
    if ! inotifywait -qq -e modify -t 300 "$CRED_FILE" 2>/dev/null; then
        # 타임아웃 또는 에러 — 파일 존재 확인 후 재시도
        if [ ! -f "$CRED_FILE" ]; then
            log "[WARN] $CRED_FILE disappeared, waiting..."
            while [ ! -f "$CRED_FILE" ]; do sleep "$WAIT_INTERVAL"; done
            log "[RECOVERED] $CRED_FILE found again"
        fi
        continue
    fi

    sleep 0.5
    CUR_TOKEN=$(jq -r '.claudeAiOauth.accessToken // empty' "$CRED_FILE" 2>/dev/null) || continue
    if [ -n "$CUR_TOKEN" ] && [ "$CUR_TOKEN" != "$PREV_TOKEN" ]; then
        EXPIRES=$(jq -r '.claudeAiOauth.expiresAt // 0' "$CRED_FILE" 2>/dev/null)
        EXPIRES_DATE=$(date -d @$((EXPIRES / 1000)) '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo "unknown")
        log "[CHANGED] new_token=${CUR_TOKEN:0:20}... expires=${EXPIRES_DATE}"
        sync_repos "$CUR_TOKEN"
        PREV_TOKEN="$CUR_TOKEN"
    fi
done
