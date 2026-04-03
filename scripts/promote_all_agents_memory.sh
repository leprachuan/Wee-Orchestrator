#!/usr/bin/env bash
# promote_all_agents_memory.sh — Trigger memory promotion for ALL agents.
# Called by the weekly-memory-promoter scheduled job.
# Replaces the old single-agent memory_promoter.py invocation.
#
# Usage: ./scripts/promote_all_agents_memory.sh
#
# Requires API_SHARED_KEY env var (or falls back to .env file).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"

# Resolve API port (dev=8001, prod=8000)
API_PORT="${API_PORT:-8000}"
API_URL="https://127.0.0.1:${API_PORT}"

# Resolve shared key
if [ -z "${API_SHARED_KEY:-}" ] && [ -f "${BASE_DIR}/.env" ]; then
    API_SHARED_KEY="$(grep '^API_SHARED_KEY=' "${BASE_DIR}/.env" | cut -d= -f2- | tr -d '"' || true)"
fi
API_SHARED_KEY="${API_SHARED_KEY:?ERROR: API_SHARED_KEY not set}"

echo "[promote-all] Calling POST ${API_URL}/api/v1/memory/promote-all"
RESULT=$(curl -s -k -X POST "${API_URL}/api/v1/memory/promote-all" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer shared_${API_SHARED_KEY}" \
    -H "X-User-Identity: memory-promoter" \
    -H "X-Auth-Channel: scheduler" \
    --max-time 600)

echo "$RESULT" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(f'[promote-all] {d.get(\"total\", 0)} agents processed: {d.get(\"succeeded\", 0)} OK, {d.get(\"failed\", 0)} failed')
    for r in d.get('results', []):
        status = r.get('status', 'unknown')
        icon = '✓' if status == 'ok' else '✗'
        print(f'  {icon} {r[\"agent\"]:20} ({r.get(\"agent_path\",\"?\")}) → {status}')
except Exception as e:
    print(f'[promote-all] Error parsing result: {e}', file=sys.stderr)
    sys.exit(1)
"
