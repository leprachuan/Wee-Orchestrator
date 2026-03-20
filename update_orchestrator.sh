#!/usr/bin/env bash
# update_orchestrator.sh — Self-updating script for Wee Orchestrator (dev).
# Designed to run FULLY DETACHED from the parent service so it survives
# the systemd restart it triggers.
#
# Usage: launched by update_launcher.py via setsid; not called directly.

set -euo pipefail

REPO_DIR="/opt/n8n-copilot-shim-dev"
LOG="/tmp/wee-update.log"
SERVICES=(
    "agent-manager-api-dev.service"
    "task-scheduler-executor-dev.service"
    "webex-connector-dev.service"
    "telegram-bot-listener-dev.service"
)
HEALTH_WAIT=(5 3 3 5)

exec > "$LOG" 2>&1
echo "=== Wee Orchestrator Dev Update ==="
echo "Started: $(date -Iseconds)"
echo "PID: $$  PPID: $PPID  SID: $(ps -o sid= -p $$)"
echo ""

# ------------------------------------------------------------------
# Helper: send Telegram notification to all allowed users
# ------------------------------------------------------------------
notify() {
    local msg="$1"
    python3 - "$msg" <<'PYEOF'
import sys, json, os
try:
    repo = "/opt/n8n-copilot-shim-dev"
    sys.path.insert(0, repo)
    from telegram_connector import TelegramConnector
    cfg_path = os.path.join(repo, "telegram_config.json")
    if not os.path.exists(cfg_path):
        print("No telegram_config.json — skipping notification")
        sys.exit(0)
    with open(cfg_path) as f:
        cfg = json.load(f)
    connector = TelegramConnector(cfg)
    for chat_id in cfg.get("allowed_users", []):
        try:
            connector.send_message(chat_id, sys.argv[1])
        except Exception as e:
            print(f"Notify {chat_id} failed: {e}")
except Exception as e:
    print(f"Notification error: {e}")
PYEOF
}

# ------------------------------------------------------------------
# Give the parent time to send its immediate reply
# ------------------------------------------------------------------
sleep 3

# ------------------------------------------------------------------
# Step 1: Git pull
# ------------------------------------------------------------------
cd "$REPO_DIR"
echo "--- Git pull ---"
git fetch origin dev 2>&1 || true
BEFORE=$(git rev-parse --short HEAD)
if ! git pull origin dev 2>&1; then
    echo "ERROR: git pull failed"
    notify "❌ Dev Wee Orchestrator update FAILED — git pull error. Check /tmp/wee-update.log"
    exit 1
fi
AFTER=$(git rev-parse --short HEAD)
LATEST=$(git log --oneline -1)
echo "Before: $BEFORE  After: $AFTER"
echo "Latest: $LATEST"
echo ""

# ------------------------------------------------------------------
# Step 2: Post-pull setup (install new deps if requirements changed)
# ------------------------------------------------------------------
if git diff "$BEFORE".."$AFTER" --name-only | grep -q 'requirements.txt'; then
    echo "--- requirements.txt changed — installing ---"
    pip install -r requirements.txt 2>&1 || echo "WARNING: pip install failed"
    echo ""
fi

# ------------------------------------------------------------------
# Step 3: Restart services with health checks
# ------------------------------------------------------------------
echo "--- Restarting services ---"
FAILED=()
for i in "${!SERVICES[@]}"; do
    svc="${SERVICES[$i]}"
    wait="${HEALTH_WAIT[$i]}"
    echo "Restarting $svc ..."
    if ! sudo systemctl restart "$svc" 2>&1; then
        echo "  ERROR: restart command failed for $svc"
        FAILED+=("$svc")
        continue
    fi
    sleep "$wait"
    if sudo systemctl is-active --quiet "$svc"; then
        echo "  ✓ $svc is active"
    else
        echo "  ✗ $svc is NOT active"
        FAILED+=("$svc")
    fi
done
echo ""

# ------------------------------------------------------------------
# Step 4: Final verification and notification
# ------------------------------------------------------------------
echo "--- Final status ---"
ALL_OK=true
STATUS_LINES=""
for svc in "${SERVICES[@]}"; do
    state=$(sudo systemctl is-active "$svc" 2>/dev/null || echo "unknown")
    icon="✓"
    if [ "$state" != "active" ]; then
        icon="✗"
        ALL_OK=false
    fi
    STATUS_LINES+="  $icon $svc: $state\n"
    echo "$icon $svc: $state"
done

if [ ${#FAILED[@]} -eq 0 ] && $ALL_OK; then
    notify "✅ Dev Wee Orchestrator updated successfully.

Pulled: $LATEST
Before: $BEFORE → After: $AFTER

Services: all ${#SERVICES[@]} active"
    echo ""
    echo "=== Update complete (success) ==="
else
    notify "⚠️ Dev Wee Orchestrator update completed WITH ISSUES.

Pulled: $LATEST
Before: $BEFORE → After: $AFTER

Service status:
$(echo -e "$STATUS_LINES")
Failed: ${FAILED[*]}

Check: /tmp/wee-update.log"
    echo ""
    echo "=== Update complete (with failures) ==="
fi

echo "Finished: $(date -Iseconds)"
