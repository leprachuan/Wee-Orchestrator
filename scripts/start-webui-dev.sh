#!/usr/bin/env bash
set -e

# Prevent duplicate WebUI dev instances by using a PID file check.
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PIDFILE="$PROJECT_ROOT/webui.pid"
LOGFILE="$PROJECT_ROOT/webui.log"
WEBUI_DIR="$PROJECT_ROOT/webui"

if [ -f "$PIDFILE" ]; then
  PID=$(cat "$PIDFILE" 2>/dev/null || true)
  if [ -n "$PID" ] && kill -0 "$PID" >/dev/null 2>&1; then
    echo "WebUI dev server already running (PID $PID). Exiting."
    exit 0
  else
    echo "Stale PID file found, removing."
    rm -f "$PIDFILE"
  fi
fi

# Start dev server in background and record PID
cd "$WEBUI_DIR"
# Use setsid to detach and redirect output to log
setsid npm run dev &> "$LOGFILE" &
NEWPID=$!
echo $NEWPID > "$PIDFILE"
echo "Started WebUI dev server with PID $NEWPID, logs: $LOGFILE"
