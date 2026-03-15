# Runtime Handoff Logging

## Overview

The Wee Orchestrator now logs all runtime handoff events to a dedicated audit log, providing complete visibility into when, why, and how users switch between runtimes (Claude, Copilot, OpenCode, Gemini, Codex).

## Log File Location

**Path:** `~/.copilot/logs/handoff.log`

This log file is:
- **Persistent** across sessions
- **Append-only** (never truncated)
- **Timestamped** with ISO 8601 format
- **Structured** for easy parsing and analysis

## Log Entry Examples

### 1. Runtime Change Detected
```
2026-03-09 22:30:45 [INFO] Runtime change detected: claude → copilot (n8n_session=abc123)
```
Triggered when the system detects that a user is switching runtimes. Records the old runtime, new runtime, and the session ID.

### 2. Handoff Reason (User Intent)
```
2026-03-09 22:30:45 [INFO] HANDOFF REASON: User executed '/runtime set copilot' command | n8n_session=abc123 | current_agent=fosterbot
```
Captures the specific command that triggered the handoff, along with the current agent context.

### 3. Handoff Initiated
```
2026-03-09 22:30:45 [INFO] HANDOFF INITIATED: claude → copilot | prev_session=8c3a9e1a-4d5f-... new_session=f7c2b1d8-9e3a-... | n8n_session=abc123
```
Records the start of the handoff process with:
- Previous and new runtimes
- Previous and new session IDs
- The orchestrator's n8n session ID

### 4. Transcript Exported
```
2026-03-09 22:30:45 [DEBUG] Transcript exported: ~/.copilot/session-state/8c3a9e1a-4d5f-4c7a-.../transcript.md | session_id=8c3a9e1a-4d5f-... | messages=23
```
Logs when the conversation transcript has been exported for reference.

### 5. Handoff Summary Written
```
2026-03-09 22:30:45 [INFO] HANDOFF SUMMARY WRITTEN: ~/.copilot/session-state/f7c2b1d8-9e3a-.../handoff.md | transcript=/path/to/transcript.md | messages_included=20
```
Indicates successful creation of the handoff summary file with the number of messages included.

### 6. Handoff Context Loaded
```
2026-03-09 22:30:47 [INFO] HANDOFF CONTEXT LOADED: session_id=f7c2b1d8-9e3a-... | claude → copilot | switched_at=2026-03-09 22:30:45 UTC
```
Logged when the new runtime loads the handoff context on the first message.

### 7. Cleanup
```
2026-03-09 22:30:47 [DEBUG] Handoff files cleaned up for session_id=f7c2b1d8-9e3a-...
```
Records successful cleanup of one-time handoff files after context is loaded.

## Error Logging

### Runtime Detection Error
```
2026-03-09 22:30:45 [WARNING] Error detecting runtime change for abc123: [error details]
```

### No Chat History
```
2026-03-09 22:30:45 [WARNING] No chat history found for n8n_session=abc123. Handoff cancelled.
```

### Handoff Preparation Failure
```
2026-03-09 22:30:45 [ERROR] HANDOFF FAILED: [error details] | prev_runtime=claude new_runtime=copilot
```

## Log Structure

Each log entry contains:
- **Timestamp**: `YYYY-MM-DD HH:MM:SS` (UTC)
- **Level**: `[INFO]`, `[DEBUG]`, `[WARNING]`, `[ERROR]`
- **Message**: Detailed event description with key identifiers

## Key Information Captured

For each handoff event, the logs record:

| Information | Example | Purpose |
|-------------|---------|---------|
| **From Runtime** | `claude` | Track which runtime was being used |
| **To Runtime** | `copilot` | Track which runtime was requested |
| **Reason** | `/runtime set copilot` | Understand user intent |
| **Previous Session ID** | `8c3a9e1a-4d5f-...` | Reference old session |
| **New Session ID** | `f7c2b1d8-9e3a-...` | Reference new session |
| **n8n Session ID** | `abc123` | Track orchestrator context |
| **Current Agent** | `fosterbot` | Track which agent was active |
| **Timestamp** | `2026-03-09 22:30:45` | When the handoff occurred |
| **Message Count** | `20` | How much context was transferred |

## Viewing the Log

### View recent handoff events
```bash
tail -20 ~/.copilot/logs/handoff.log
```

### Watch handoff events in real-time
```bash
tail -f ~/.copilot/logs/handoff.log
```

### Filter for specific runtime
```bash
grep "claude → copilot" ~/.copilot/logs/handoff.log
```

### Get only errors
```bash
grep "\[ERROR\]" ~/.copilot/logs/handoff.log
```

### Count handoffs per runtime pair
```bash
grep "HANDOFF INITIATED" ~/.copilot/logs/handoff.log | sort | uniq -c
```

## Use Cases

### Debugging Handoff Issues
- Check the log to see if handoff was triggered
- Verify transcript and summary files were created
- Identify any errors or warnings during transfer

### Audit Trail
- See when users switched runtimes
- Understand which runtimes are being used
- Track multi-runtime sessions

### Performance Analysis
- Monitor how many handoffs occur
- Identify patterns (e.g., frequent switching)
- Optimize handoff performance

### User Support
- Help troubleshoot handoff problems
- Show users what context was transferred
- Verify session continuity

## Log Rotation

The log file will grow over time. Consider periodic archival or rotation:

```bash
# Archive the log
mv ~/.copilot/logs/handoff.log ~/.copilot/logs/handoff.log.$(date +%Y%m%d)

# Or use logrotate with a cron job
```

## Configuration

Log level can be controlled in `session_handoff.py`:

```python
# Line 43: Default is INFO
handoff_logger.setLevel(logging.INFO)  # Change to DEBUG for verbose logging
```

- **INFO**: Normal handoff events (recommended default)
- **DEBUG**: Detailed handoff flow (verbose, use for troubleshooting)
- **WARNING**: Problems that didn't block handoff
- **ERROR**: Critical failures

## Integration with Monitoring

The handoff log can be integrated with:
- Log aggregation systems (ELK, Splunk, etc.)
- Monitoring dashboards
- Alert systems (e.g., notify on handoff errors)
- Analytics pipelines

Example monitoring query (Splunk):
```
source="~/.copilot/logs/handoff.log" "HANDOFF FAILED" | stats count by new_runtime
```
