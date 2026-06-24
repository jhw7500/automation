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

# Repo 목록은 workflow-config.json에서 소싱한다(하드코딩 drift 방지 — 과거 14개 목록이
# config의 19개와 어긋나 일부 repo에 토큰이 silent 누락됐었다). secrets != false 인 repo만.
# automation(소스 repo, self-review 토큰 필요)은 config.repos에 없으므로 명시 포함 후 dedup.
CONFIG_JSON="$(cd "$(dirname "$0")" && pwd)/workflow-config.json"
if ! command -v jq >/dev/null 2>&1 || [ ! -f "$CONFIG_JSON" ]; then
    echo "Error: jq and $CONFIG_JSON required to resolve repo list"
    exit 1
fi
OWNER="$(jq -r '.gh_owner // "jhw7500"' "$CONFIG_JSON")"
mapfile -t REPOS < <(
    {
        printf '%s/automation\n' "$OWNER"
        jq -r --arg o "$OWNER" '.repos | to_entries[] | select(.value.secrets != false) | $o + "/" + .key' "$CONFIG_JSON"
    } | awk 'NF && !seen[$0]++'
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
