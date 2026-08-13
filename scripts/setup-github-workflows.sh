#!/usr/bin/env bash
set -euo pipefail

cat >&2 <<'EOF'
This writer is retired and performs no changes.
Use python3 scripts/rollout_workflow_fleet.py --mode plan for workflow PR planning.
After review, use python3 scripts/rollout_workflow_fleet.py --mode publish with explicit repositories and --confirm.
Workflow rollout never synchronizes credentials; Claude rotation remains in personal-ops/claude-token-sync.
EOF
exit 2
