#!/bin/bash
#
# sync-secrets.sh — 모든 repo에 CLAUDE_CODE_OAUTH_TOKEN 일괄 동기화
#
# Usage:
#   ./sync-secrets.sh                  # ~/.claude 토큰 자동 읽어서 설정
#   ./sync-secrets.sh "sk-ant-..."     # 직접 토큰 지정
#

# Fine-grained PAT 대신 gh auth 저장된 토큰(repo scope) 사용
unset GITHUB_TOKEN

REPOS=(
    jhw7500/gstApp
    jhw7500/wlan-driver
    jhw7500/automation
    jhw7500/wlan-package
    jhw7500/max9296
    jhw7500/wlan-bridge
    jhw7500/cts-email-mcp-server
    jhw7500/cts-ta-mcp-server
    jhw7500/cts-ta-webapp
    jhw7500/pim-check
    jhw7500/redmine
    jhw7500/sc16is7xx
    jhw7500/wpa-supplicant
)

SECRET_NAME="CLAUDE_CODE_OAUTH_TOKEN"

# 토큰 결정
if [ -n "$1" ]; then
    TOKEN="$1"
else
    CRED_FILE="$HOME/.claude/.credentials.json"
    if [ ! -f "$CRED_FILE" ]; then
        echo "Error: $CRED_FILE not found. Run 'claude' to authenticate first."
        exit 1
    fi
    TOKEN=$(python3 -c "
import json
with open('$CRED_FILE') as f:
    d = json.load(f)
print(d['claudeAiOauth']['accessToken'])
" 2>/dev/null)
    if [ -z "$TOKEN" ]; then
        echo "Error: Failed to extract token from $CRED_FILE"
        exit 1
    fi
    echo "Token loaded from $CRED_FILE"
fi

echo "Syncing $SECRET_NAME to ${#REPOS[@]} repos..."
echo ""

for repo in "${REPOS[@]}"; do
    echo -n "  $repo ... "
    if echo "$TOKEN" | gh secret set "$SECRET_NAME" --repo "$repo" --body - 2>/dev/null; then
        echo "OK"
    else
        echo "FAILED (check permissions)"
    fi
done

echo ""
echo "Done."
