#!/bin/bash
# =============================================================================
# Wee Orchestrator — Docker Entrypoint
# =============================================================================
# Starts optional bot connectors if their credentials are configured,
# then starts the main API in the foreground.
# =============================================================================
set -e

echo "=== Wee Orchestrator ==="
echo "Starting up..."
echo ""

# ---------------------------------------------------------------------------
# Telegram Bot
# Starts if TELEGRAM_BOT_TOKEN env var is set, OR if telegram_config.json
# is mounted and contains a non-empty token field.
# ---------------------------------------------------------------------------
_start_telegram=false

if [ -n "${TELEGRAM_BOT_TOKEN}" ]; then
    _start_telegram=true
    echo "[telegram] TELEGRAM_BOT_TOKEN set — starting bot"
elif [ -f "/app/telegram_config.json" ]; then
    _token=$(python3 -c "import json; d=json.load(open('/app/telegram_config.json')); print(d.get('token',''))" 2>/dev/null || echo "")
    if [ -n "$_token" ]; then
        _start_telegram=true
        echo "[telegram] telegram_config.json has token — starting bot"
    fi
fi

if [ "$_start_telegram" = "true" ]; then
    python3 /app/telegram_connector.py &
    echo "[telegram] started (PID $!)"
else
    echo "[telegram] skipped (set TELEGRAM_BOT_TOKEN or mount telegram_config.json with a token)"
fi

# ---------------------------------------------------------------------------
# WebEx Bot
# Starts if WEBEX_BOT_TOKEN + RABBITMQ_PASSWORD env vars are both set,
# OR if webex_config.json is mounted and contains a non-empty token field.
# ---------------------------------------------------------------------------
_start_webex=false

if [ -n "${WEBEX_BOT_TOKEN}" ] && [ -n "${RABBITMQ_PASSWORD}" ]; then
    _start_webex=true
    echo "[webex] WEBEX_BOT_TOKEN + RABBITMQ_PASSWORD set — starting connector"
elif [ -f "/app/webex_config.json" ]; then
    _wtoken=$(python3 -c "import json; d=json.load(open('/app/webex_config.json')); print(d.get('token',''))" 2>/dev/null || echo "")
    if [ -n "$_wtoken" ]; then
        _start_webex=true
        echo "[webex] webex_config.json has token — starting connector"
    fi
fi

if [ "$_start_webex" = "true" ]; then
    python3 /app/webex_connector.py &
    echo "[webex] started (PID $!)"
else
    echo "[webex] skipped (set WEBEX_BOT_TOKEN + RABBITMQ_PASSWORD, or mount webex_config.json with a token)"
fi

echo ""
echo "[api] starting on port ${API_PORT:-8000}"
exec python3 /app/agent_manager.py --api
