# Demo: /status and /cancel Commands

This document demonstrates the new query tracking and management features.

## Overview

The `/status` and `/cancel` commands allow you to monitor and control long-running queries across all AI runtimes (copilot, opencode, claude, gemini, codex).

## Use Cases

### 1. Check if a query is running
```bash
# User sends a complex query that might take a while
python agent_manager.py "Analyze all Python files in the repository" session_123

# From another terminal or N8N workflow, check status
python agent_manager.py "/status" session_123
```

**Output when running:**
```
🔄 **Query Running**

**Runtime:** copilot
**Agent:** projects
**PID:** 12345
**Elapsed Time:** 2m 15s
**Prompt:** Analyze all Python files in the repository...

**Recent Output:**
Analyzing file: agent_manager.py
Found 62 test cases
Checking code quality...
```

**Output when idle:**
```
✓ No running query for this session
```

### 2. Cancel a long-running query
```bash
# Cancel the running query
python agent_manager.py "/cancel" session_123
```

**Output:**
```
✓ Cancelled running query (PID: 12345, Runtime: copilot)
```

### 3. Check help for new commands
```bash
python agent_manager.py "/help" session_123
```

**Output includes:**
```
**Query Management:**
   • `/status` - Check status of running query for this session
   • `/cancel` - Cancel running query for this session
```

## How It Works

### Process Tracking
When you execute a query, the system:
1. Starts the AI CLI process using `Popen` (not `subprocess.run`)
2. Captures the Process ID (PID)
3. Stores tracking info in `~/.copilot/running-queries.json`:
   - PID
   - Runtime (copilot, opencode, claude, gemini, codex)
   - Agent name
   - Prompt (first 200 chars)
   - Start timestamp
   - Last output (last 500 chars)
4. Updates the output snippet as the query progresses
5. Cleans up tracking when complete

### Status Checking
The `/status` command:
1. Looks up the session in the tracking file
2. Checks if the PID is still running
3. Calculates elapsed time
4. Shows recent output snippet
5. Returns a formatted status message

### Cancellation
The `/cancel` command:
1. Looks up the session's running query
2. Verifies the process is still active
3. Sends SIGKILL (9) to terminate the process
4. Cleans up the tracking entry
5. Confirms the cancellation

## Multi-Runtime Support

The tracking system works with all supported runtimes:

- ✅ **Copilot** (`/usr/bin/copilot`)
- ✅ **OpenCode** (`~/.opencode/bin/opencode`)
- ✅ **Claude** (`/usr/bin/claude`)
- ✅ **Gemini** (`gemini`)
- ✅ **CODEX** (`codex`)

Each runtime execution is tracked identically, making the commands consistent across all backends.

## Example N8N Workflow

```javascript
// Node 1: Start a long-running query
const startQuery = {
  exec: `python agent_manager.py "Complex analysis task" ${sessionId}`,
  async: true  // Don't wait for completion
};

// Node 2: Check status after 30 seconds
const checkStatus = {
  exec: `python agent_manager.py "/status" ${sessionId}`,
  delay: 30000
};

// Node 3: Cancel if taking too long
const cancelQuery = {
  exec: `python agent_manager.py "/cancel" ${sessionId}`,
  condition: "status.includes('Elapsed Time: 5m')"
};
```

## Testing

Run the tests to verify functionality:

```bash
# Run all tests including query tracking tests
python3 -m unittest tests.test_agent_manager -v

# Run only query tracking tests
python3 -m unittest tests.test_agent_manager.TestQueryTracking -v
```

All 8 query tracking tests should pass:
- ✅ `test_track_running_query`
- ✅ `test_update_query_output`
- ✅ `test_clear_running_query`
- ✅ `test_status_command_no_running_query`
- ✅ `test_status_command_with_running_query`
- ✅ `test_cancel_command_no_running_query`
- ✅ `test_cancel_command_with_running_query`
- ✅ `test_help_includes_status_and_cancel`

## Limitations

1. **PID Tracking Only**: The system tracks process IDs. If the process terminates abnormally, tracking may not be cleaned up immediately (will be detected on next `/status` check).

2. **Single Query per Session**: Each session can track one running query at a time. Starting a new query overwrites the previous tracking.

3. **No Query History**: Only the currently running query is tracked. Historical query data is not preserved.

4. **Local Process Only**: Can only track and cancel processes on the local machine where the agent manager is running.

## Future Enhancements

Possible improvements for future versions:
- Query history tracking
- Multiple concurrent queries per session
- Query timeout warnings
- Progress percentage estimation
- Query performance metrics
