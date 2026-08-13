#!/usr/bin/env bash
set -euo pipefail

cat >&2 <<'EOF'
This writer is retired and performs no changes.
Use the separately reviewed personal-ops/claude-token-sync lifecycle for Claude token rotation.
This guard does not forward arguments, read local credentials, or write repository secrets.
EOF
exit 2
