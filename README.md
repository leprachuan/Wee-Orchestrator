# n8n-copilot-shim

A unified AI agent manager that bridges N8N workflows with multiple AI CLI tools (GitHub Copilot, OpenCode, Claude Code). Features session management, multi-agent support with dynamic configuration, and model switching.

## Overview

This shim provides a flexible framework to:
- Call AI CLIs (Copilot, OpenCode, Claude Code) from N8N workflows
- Maintain session affinity across multiple conversation turns
- Switch between different agent repositories dynamically
- Configure agents via JSON config files instead of hardcoding
- Support multiple AI models and runtimes

## Configuration

### Agent Configuration

The system loads agents from `agents.json` or a custom config file. Each agent represents a repository context where the AI CLI will operate.

**Config Format:**
```json
{
  "agents": [
    {
      "name": "devops",
      "description": "DevOps and infrastructure management",
      "path": "/path/to/MyHomeDevops"
    },
    {
      "name": "projects",
      "description": "Software development projects",
      "path": "/path/to/projects"
    }
  ]
}
```

**Configuration Fields:**
- `name` (required): Short identifier for the agent (used in `/agent set` commands)
- `description` (required): Brief human-readable description of the agent
- `path` (required): Full path to the repository or project directory

### Setup

1. **Copy the agent manager script:**
   ```bash
   cp agent_manager.py /usr/local/bin/agent-manager
   chmod +x /usr/local/bin/agent-manager
   ```

2. **Configure your agents:**
   - Copy `agents.example.json` to `agents.json`
   - Edit `agents.json` with your actual repository paths
   - Place `agents.json` in the same directory as the script or current working directory

3. **Optional: Specify config location via environment variable**
   ```bash
   export AGENTS_CONFIG=/path/to/custom/agents.json
   ```

## Usage

### Command Line

```bash
python agent_manager.py "<prompt>" [session_id] [config_file]
```

**Arguments:**
- `prompt`: The prompt/command to send to the AI CLI
- `session_id` (optional): N8N session identifier for tracking conversations (default: "default")
- `config_file` (optional): Path to agents.json config file

**Examples:**
```bash
# Basic usage
python agent_manager.py "List all files in the current directory"

# With session ID
python agent_manager.py "Continue debugging the issue" "session-123"

# With custom config file
python agent_manager.py "Deploy the app" "session-456" "/etc/agents.json"
```

### Slash Commands

Interact with the agent manager using slash commands:

#### Runtime Management
```
/runtime list              # Show available runtimes (copilot, opencode, claude)
/runtime set <runtime>     # Switch runtime (e.g., /runtime set claude)
/runtime current           # Show current runtime
```

#### Model Management
```
/model list                # Show available models for current runtime
/model set "<model>"       # Switch model (e.g., /model set "claude-opus-4.5")
/model current             # Show current model
```

#### Agent Management
```
/agent list                # Show all available agents with descriptions
/agent set "<agent>"       # Switch to an agent (e.g., /agent set "projects")
/agent current             # Show current agent and its context
```

#### Session Management
```
/session reset             # Reset the current session (starts fresh next message)
/help                      # Show all available commands
```

### N8N Integration

Use in an N8N workflow:

```javascript
// Execute the agent manager from N8N
const { exec } = require('child_process');
const prompt = "Your prompt here";
const sessionId = "n8n_session_123";
const configFile = "/path/to/agents.json";

exec(`python agent_manager.py "${prompt}" "${sessionId}" "${configFile}"`,
  (error, stdout, stderr) => {
    if (error) console.error(error);
    console.log(stdout);
  }
);
```

## Session Management

Sessions are automatically tracked and stored in:
- **Copilot:** `~/.copilot/n8n-session-map.json`
- **OpenCode:** `~/.opencode/n8n-session-map.json`
- **Claude:** `~/.claude/` (debug directory)

Each N8N session ID is mapped to:
- A unique backend session ID (for resuming AI CLI sessions)
- Current runtime (copilot/opencode/claude)
- Current model
- Current agent

Session data persists across requests, allowing multi-turn conversations.

## Default Behavior

When creating a new session:
- **Runtime:** copilot (use `/runtime set` to change)
- **Model:** gpt-5-mini (Copilot) / opencode/gpt-5-nano (OpenCode) / haiku (Claude)
- **Agent:** devops (or first available agent from config)

## Advanced Features

### Dynamic Agent Loading
Instead of hardcoding agent paths, the system:
1. Looks for `agents.json` in the current directory
2. Falls back to the script directory if not found
3. Supports custom config paths via argument

### Session Resumption
- The system automatically detects and resumes existing sessions
- If a session is lost or corrupted, it starts a fresh session automatically
- Use `/session reset` to explicitly clear session state

### Model Resolution
The system intelligently matches model names:
- Exact matches (case-insensitive)
- Substring/suffix matching
- Latest version preference for ambiguous matches

### Metadata Stripping
Automatically removes CLI metadata from output:
- Thinking tags (`<think>...</think>`)
- Token usage statistics
- Session headers and banners

## File Structure

```
n8n-copilot-shim-1/
├── agent_manager.py       # Main agent manager script
├── agents.json            # Agent configuration (git-ignored)
├── agents.example.json    # Example configuration template
└── README.md              # This file
```

## Architecture

### SessionManager Class

The core class that manages:
- Agent configuration loading
- AI CLI execution (Copilot, OpenCode, Claude)
- Session state persistence
- Slash command parsing and execution
- Model and runtime switching

### Key Methods

- `_load_agents_config()` - Load agents from JSON config file
- `execute()` - Main entry point for processing prompts and commands
- `run_copilot()`, `run_opencode()`, `run_claude()` - Execute respective CLIs
- `strip_metadata()` - Clean CLI output
- `set_agent()`, `update_session_field()` - Session state management

## Troubleshooting

### Agents not loading
- Check that `agents.json` exists in the script directory or current directory
- Verify JSON syntax with `python -m json.tool agents.json`
- Check file permissions

### Session issues
- Run `/session reset` to start fresh
- Check session storage directories exist:
  - `~/.copilot/session-state/`
  - `~/.local/share/opencode/storage/session/global/`
  - `~/.claude/debug/`

### CLI not found
- Ensure copilot, opencode, and claude binaries are in PATH or at expected locations
- Check `/usr/bin/copilot`, `/usr/bin/claude`, `~/.opencode/bin/opencode`

## Migration from Original Script

This version replaces hardcoded agents with JSON configuration:

**Before:**
```python
AGENTS = {
    'devops': {'path': '/opt/MyHomeDevops', ...},
    'family': {'path': '/opt/family_knowledge', ...}
}
```

**After:**
```json
{
  "agents": [
    {"name": "devops", "path": "/opt/MyHomeDevops", ...},
    {"name": "family", "path": "/opt/family_knowledge", ...}
  ]
}
```

To migrate:
1. Copy `agent_manager.py` to your environment
2. Create `agents.json` with your agent definitions
3. No code changes needed - same interface as before
