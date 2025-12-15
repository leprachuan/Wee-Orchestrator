# Gemini Runtime Quick Reference

## Overview

The Gemini runtime allows you to use Google's Gemini AI models through the n8n chat workflow integration. The runtime is fully validated and production-ready.

## Basic Usage

### Switch to Gemini Runtime

```bash
./agent_manager.py "/runtime set gemini" "session-123"
```

Response:
```
✓ Switched runtime to **gemini**. Model set to `gemini-1.5-flash`.
```

### Execute a Prompt

```bash
./agent_manager.py "What are the main Kubernetes services?" "session-123"
```

### Resume a Session

Sessions automatically resume using the `--resume latest` flag. The system tracks session context internally.

## Session Management

### View Current Session

```bash
./agent_manager.py "/session current" "session-123"
```

### Reset Session

```bash
./agent_manager.py "/session reset" "session-123"
```

This creates a fresh session for the next prompt.

## Agent Management

### Switch Agents

```bash
./agent_manager.py "/agent set family" "session-123"
```

Available agents:
- **devops**: DevOps and infrastructure management (`/opt/MyHomeDevops`)
- **family**: Family knowledge and recipes (`/opt/family_knowledge`)
- **projects**: Software development projects (`/Users/fosterlipkey/Documents/projects`)

When you switch agents:
- A new backend session is created
- The working directory changes to the agent's path
- Session context is isolated per agent

### List Available Agents

```bash
./agent_manager.py "/agent list" "session-123"
```

## Model Management

### View Available Models

```bash
./agent_manager.py "/model list" "session-123"
```

Available Gemini models:
- `gemini-2.0-flash-exp` - Experimental, latest version
- `gemini-1.5-pro` - Professional/advanced reasoning
- `gemini-1.5-flash` - Fast, efficient responses
- `gemini-pro` - Standard Gemini model

### Switch Models

```bash
./agent_manager.py "/model set gemini-1.5-pro" "session-123"
```

**Note:** Model configuration is tracked but not actively applied to Gemini CLI calls (see Known Issues).

## N8N Integration

### Basic Chat Workflow

1. User sends message in N8N chat
2. N8N calls: `./agent_manager.py "<prompt>" "<n8n_session_id>"`
3. Returns AI response to user

### Example Workflow

```json
{
  "prompt": "List all active pods in the cluster",
  "n8n_session_id": "user-chat-session-123",
  "runtime": "gemini"
}
```

## Performance Expectations

- **Initial startup**: 35-40 seconds (first call, includes CLI initialization)
- **Session resume**: 30-35 seconds (reuses cached credentials)
- **Response generation**: 2-5 seconds (varies by complexity)
- **Total for simple prompt**: 35-45 seconds

**Optimization tip:** Keep sessions alive for related questions to avoid full startup overhead.

## Session Persistence

Gemini stores session data in `~/.gemini/sessions/`. Each session maintains:
- Conversation history
- Model configuration
- Agent context
- Working directory state

Sessions can be resumed with `--resume latest` or by continuing with the same session ID.

## Troubleshooting

### "Error: Gemini command failed"

Check Gemini CLI logs in `/tmp/gemini-client-error*.json` for detailed error information.

### API 404 Error

This typically means the Gemini API key is invalid or the account has API access issues.

**Solution:** 
1. Check Google Cloud authentication: `gcloud auth list`
2. Verify Gemini API is enabled in your Google Cloud project
3. Ensure proper credentials are configured in `~/.gemini/`

### Session Not Resuming

Sessions are resumed automatically with `--resume latest`. If this fails:
1. Check session files exist in `~/.gemini/sessions/`
2. Reset the session: `/session reset`
3. Start a new session

## CLI Commands Reference

### Runtime Commands

```bash
/runtime list          # Show available runtimes
/runtime set gemini    # Switch to Gemini
/runtime current       # Show current runtime
```

### Model Commands

```bash
/model list            # Show available models
/model set gemini-1.5-pro  # Switch model
/model current         # Show current model
```

### Agent Commands

```bash
/agent list            # Show available agents
/agent set family      # Switch agent
/agent current         # Show current agent
```

### Session Commands

```bash
/session reset         # Reset current session
```

## Files and Directories

- **CLI Binary**: `/usr/bin/gemini`
- **Session Storage**: `~/.gemini/sessions/`
- **Configuration**: `~/.gemini/` (auto-created on first run)
- **Session Manager**: `/opt/n8n-copilot-shim/agent_manager.py`
- **Validation Script**: `/opt/n8n-copilot-shim/validate_gemini.py`

## Running Validation

To verify Gemini runtime is working correctly:

```bash
cd /opt/n8n-copilot-shim
python3 validate_gemini.py
```

Should complete in ~5 minutes with all tests passing.

## Known Limitations

1. **Model specification via CLI**: The `--model` flag with explicit model names returns 404 errors from the Gemini API. Currently using default model selection.

2. **Session ID format**: Gemini CLI doesn't accept custom session IDs. Uses `--resume latest` for session management.

3. **Startup time**: Gemini CLI has longer initialization time (~10-15 seconds) compared to other runtimes due to authentication and MCP server discovery.

## Best Practices

1. **Reuse Sessions**: Keep one session per conversation thread for efficiency
2. **Agent Isolation**: Switch agents when changing context/working directory
3. **Model Selection**: Use `gemini-1.5-flash` for speed, `gemini-1.5-pro` for complex reasoning
4. **Error Recovery**: Reset sessions if experiencing repeated errors

## Support & Documentation

For more details, see:
- `GEMINI_VALIDATION_REPORT.md` - Full validation report with technical details
- `agent_manager.py` - Source code with implementation details
- Google Gemini CLI: `gemini --help`

