#!/usr/bin/env bash
set -e

# Prevent duplicate Telegram dev instances by using a PID file check.
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PIDFILE="$PROJECT_ROOT/telegram_connector.pid"
LOGFILE="$PROJECT_ROOT/telegram_connector.log"
SCRIPT="$PROJECT_ROOT/telegram_connector.py"

if [ ! -f "$SCRIPT" ]; then
  echo "telegram_connector.py not found in project root: $SCRIPT" >&2
  exit 1
fi

if [ -f "$PIDFILE" ]; then
  PID=$(cat "$PIDFILE" 2>/dev/null || true)
  if [ -n "$PID" ] && kill -0 "$PID" >/dev/null 2>&1; then
    echo "Telegram dev service already running (PID $PID). Exiting."
    exit 0
  else
    echo "Stale PID file found, removing."
    rm -f "$PIDFILE"
  fi
fi

# Start connector in background and record PID
cd "$PROJECT_ROOT"
setsid python3 "$SCRIPT" &> "$LOGFILE" &
NEWPID=$!
echo $NEWPID > "$PIDFILE"
echo "Started Telegram dev service with PID $NEWPID, logs: $LOGFILE"
