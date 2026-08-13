#!/bin/bash
set -euo pipefail

printf '%s\n' \
  'This writer is retired and performs no changes.' \
  'Use the separately reviewed personal-ops/claude-token-sync lifecycle for Claude token rotation.' \
  'This guard does not forward arguments, read local credentials, or write repository secrets.' \
  >&2
exit 2
