# Heartbeat Instructions

<!-- Write tasks here. An hourly runner will execute them and clear this file. -->

## Tasks

## Context

## Model Hint
<!-- simple | medium | complex -->

### Test Heartbeat Task


## Test Heartbeat Task
```bash
# Task: Test heartbeat execution
# Description: Run a simple diagnostic check to verify the heartbeat scheduler is working
echo "=== HEARTBEAT TASK EXECUTED ==="
echo "Time: $(date)"
echo "Host: $(hostname)"
echo "User: $(whoami)"
echo "Disk Used: $(df -h | grep -E "^/dev|/mnt" | awk '{print $2, $3, $5}')"
echo "Memory: $(free -h | tail -1 | awk '{print $2, $4, $6}')"
echo "Running since: $(uptime | sed 's/.*up \([^,]*\).*/\1/')
```
