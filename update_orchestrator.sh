#!/usr/bin/env bash
# update_orchestrator.sh — Self-updating script for Wee Orchestrator.
# Auto-detects whether it's running in dev or prod based on its own location.
# Designed to run FULLY DETACHED from the parent service so it survives
# the systemd restart it triggers.
#
# Usage: launched by update_launcher.py via setsid; not called directly.

set -euo pipefail

# Auto-detect repo dir from the script's own location
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Detect environment from repo dir name
if [[ "$REPO_DIR" == *"-dev" ]]; then
    ENV_NAME="Dev"
    BRANCH="dev"
    LOG="/tmp/wee-update.log"
    SERVICES=(
        "agent-manager-api-dev.service"
        "task-scheduler-executor-dev.service"
        "webex-connector-dev.service"
        "telegram-bot-listener-dev.service"
    )
else
    ENV_NAME="Prod"
    BRANCH="main"
    LOG="/tmp/wee-update-prod.log"
    SERVICES=(
        "agent-manager-api.service"
        "task-scheduler-executor.service"
        "webex-connector.service"
        "telegram-bot-listener.service"
    )
fi

HEALTH_WAIT=(5 3 3 5)

exec > "$LOG" 2>&1
echo "=== Wee Orchestrator $ENV_NAME Update ==="
echo "Repo:    $REPO_DIR"
echo "Branch:  $BRANCH"
echo "Started: $(date -Iseconds)"
echo "PID: $$  PPID: $PPID  SID: $(ps -o sid= -p $$)"
echo ""

# ------------------------------------------------------------------
# Helper: send Telegram notification to all allowed users
# Uses the telegram_config.json from the correct repo
# ------------------------------------------------------------------
notify() {
    local msg="$1"
    local repo="$REPO_DIR"
    python3 - "$msg" "$repo" <<'PYEOF'
import sys, json, os
try:
    repo = sys.argv[2]
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
echo "--- Git pull ($BRANCH) ---"
git fetch origin "$BRANCH" 2>&1 || true
BEFORE=$(git rev-parse --short HEAD)
if ! git pull origin "$BRANCH" 2>&1; then
    echo "ERROR: git pull failed"
    notify "❌ $ENV_NAME Wee Orchestrator update FAILED — git pull error. Check $LOG"
    exit 1
fi
AFTER=$(git rev-parse --short HEAD)

# Build changelog: all commits between BEFORE and AFTER
if [ "$BEFORE" = "$AFTER" ]; then
    CHANGELOG="(already up to date — no new commits)"
else
    CHANGELOG=$(git log --oneline "${BEFORE}..${AFTER}" 2>/dev/null || echo "(could not read changelog)")
fi

echo "Before: $BEFORE  After: $AFTER"
echo "Changelog:"
echo "$CHANGELOG"
echo ""

# ------------------------------------------------------------------
# Step 2: Post-pull setup (install new deps if requirements changed)
# ------------------------------------------------------------------
if [ "$BEFORE" != "$AFTER" ] && git diff "$BEFORE".."$AFTER" --name-only | grep -q 'requirements.txt'; then
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
    notify "✅ $ENV_NAME Wee Orchestrator updated successfully.

$BEFORE → $AFTER

Changes:
$CHANGELOG

Services: all ${#SERVICES[@]} active ✓"
    echo ""
    echo "=== Update complete (success) ==="
else
    notify "⚠️ $ENV_NAME Wee Orchestrator update completed WITH ISSUES.

$BEFORE → $AFTER

Changes:
$CHANGELOG

Service status:
$(echo -e "$STATUS_LINES")
Failed: ${FAILED[*]}

Check: $LOG"
    echo ""
    echo "=== Update complete (with failures) ==="
fi

echo "Finished: $(date -Iseconds)"
