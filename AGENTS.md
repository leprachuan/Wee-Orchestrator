# N8N Copilot Shim - Agent Orchestration

## Overview

The n8n-copilot-shim is an **orchestration agent** that manages multiple specialized subagents, each configured to handle specific domains and tasks. This document describes the agent system architecture and how to work with agents.

## The Orchestration Model

```
N8N Chat User
    ↓
n8n-copilot-shim (Orchestrator)
    ├→ devops (Infrastructure & Deployment)
    ├→ family (Knowledge & Documentation)
    ├→ projects (Software Development)
    ├→ n8n-copilot-shim (Self-Improvement)
    └→ [Custom Agents] (User-Defined)
```

The orchestrator:
- Routes requests to appropriate subagents
- Manages agent sessions and context
- Tracks execution across multiple agents
- Provides a unified interface for N8N

## Agent Discovery

Agents are **dynamically loaded from `agents.json`**. This means:

1. **No Hardcoded Agents**: Agent list is not in AGENTS.md
2. **Configuration-Driven**: All agents defined in `agents.json`
3. **Runtime Discovery**: Agents are loaded when needed
4. **Easy Expansion**: Add new agents by editing `agents.json`

### Viewing Available Agents

```bash
/agent list
```

This dynamically loads and displays all agents from your `agents.json`.

## Default Agents

The system comes with these default agents (configured in `agents.json`):

| Agent | Purpose | Path |
|-------|---------|------|
| `devops` | Infrastructure & Deployment | `/opt/MyHomeDevops` |
| `family` | Knowledge & Documentation | `/opt/family_knowledge` |
| `projects` | Software Development | `//Users/fosterlipkey/Documents/projects` |
| `n8n-copilot-shim` | Self-Improvement & Code Evolution | `/opt/n8n-copilot-shim` |

**Note**: This table is for reference only. The actual agents are defined in `agents.json` and loaded dynamically.

## Working with Agents

### Basic Operations

**Switch to an agent:**
```bash
/agent set <agent_name>
```

**See current agent:**
```bash
/agent current
```

**List all available agents:**
```bash
/agent list
```

### Making Agent Calls

Once you've switched to an agent, any prompt you send is executed in that agent's context:

```bash
# Switch to devops agent
/agent set devops

# This prompt executes in the devops agent
"Deploy the latest version to production"
```

### Session Management

Each agent maintains its own session:

```bash
# Continue conversation with same agent (resumes session)
/agent set devops
"What's the deployment status?"  # Same session as before

# Switch to different agent (new session)
/agent set family
"Document the deployment process"  # New session

# Reset current agent's session
/session reset
```

## Runtime and Model Selection

You can choose which AI runtime to use with agents:

```bash
# Available runtimes
/runtime list

# Set a runtime
/runtime set codex

# Set a model
/model set gpt-5.1-codex-max

# Now agent calls use the selected runtime and model
/agent set devops
"Deploy the application"
```

## Agent Examples

### Example 1: Infrastructure Deployment

```bash
# Switch to devops agent
/agent set devops

# Request infrastructure work
"Update the Kubernetes cluster to version 1.28 and verify all nodes are healthy"

# Results are returned
```

### Example 2: Knowledge Documentation

```bash
# Switch to family agent
/agent set family

# Request knowledge work
"Create a summary of our holiday plans for 2025"

# Documentation is updated
```

### Example 3: Self-Improvement

```bash
# Switch to n8n-copilot-shim agent
/agent set n8n-copilot-shim

# Request code improvements
"Add support for retry logic with exponential backoff in the run_codex() method"

# Code changes are made and committed
```

## Multi-Agent Workflows

You can create complex workflows by orchestrating multiple agents:

### Workflow Example: Infrastructure Update

```bash
# 1. Check status with devops
/agent set devops
"What is the current production status?"

# 2. Document findings
/agent set family
"Update our infrastructure status: [results from step 1]"

# 3. Improve code if needed
/agent set n8n-copilot-shim
"Add monitoring for the new service we just deployed"
```

## Agent Configuration (agents.json)

Agents are defined in `agents.json`:

```json
{
  "agents": [
    {
      "name": "agent-name",
      "description": "What this agent does",
      "path": "/path/to/agent/context"
    }
  ]
}
```

### Adding a New Agent

1. Edit `agents.json`
2. Add a new agent entry
3. The agent is immediately available

```json
{
  "name": "ml-research",
  "description": "Machine learning research and experimentation",
  "path": "/home/user/ml-projects"
}
```

4. Use the new agent:
```bash
/agent set ml-research
"Run the latest training pipeline"
```

## Agent Execution Context

Each agent:
- Works in its configured directory (path)
- Maintains separate session state
- Can access files in its working directory
- Executes with the current runtime and model

### Working Directory

When you call an agent, the execution happens in its configured path:

```bash
/agent set family
"Create a new recipe document"
# This file is created in /opt/family_knowledge/
```

## Session Tracking

Sessions are tracked per N8N user and per agent:

### Session Map Format

`~/.copilot/n8n-session-map.json`:
```json
{
  "n8n-session-id": {
    "session_id": "backend-session-id",
    "model": "current-model",
    "agent": "current-agent",
    "runtime": "current-runtime"
  }
}
```

### Session Storage

Each runtime stores sessions differently:
- **Copilot**: `~/.copilot/session-state/`
- **OpenCode**: `~/.local/share/opencode/storage/session/global/`
- **Claude**: `~/.claude/debug/`
- **Gemini**: `~/.gemini/sessions/`
- **CODEX**: `~/.codex/sessions/`

## Agent Capabilities

All agents support:
- ✅ Full session management
- ✅ Context preservation across calls
- ✅ File system access (in their working directory)
- ✅ Multiple runtime backends
- ✅ Switching between agents
- ✅ Session resumption

## Best Practices

1. **Use Agent-Appropriate Tasks**
   - devops → infrastructure work
   - family → knowledge management
   - projects → software development
   - n8n-copilot-shim → code improvements

2. **Provide Clear Context**
   ```bash
   /agent set devops
   "Based on the performance metrics, optimize the database queries"
   ```

3. **Leverage Session Continuity**
   ```bash
   # Ask follow-up questions to same agent (session resumes)
   /agent set devops
   "What's the deployment status now?"
   ```

4. **Reset When Changing Topics**
   ```bash
   # Unrelated task - reset session
   /session reset
   ```

5. **Document Important Results**
   - Switch to family agent to document important findings
   - Keep knowledge base updated with agent activities

## Troubleshooting

### Agent Not Found
```bash
/agent list  # Check available agents
# Verify agent name in agents.json
```

### Wrong Execution Context
```bash
/agent current
# Verify the path matches your expectation
```

### Session Issues
```bash
/session reset
/agent set <agent_name>
# Start fresh
```

## Advanced Patterns

### Agent Chaining

```bash
# Execute in sequence
/agent set A
"Step 1"

/agent set B  
"Step 2 (results from step 1: [...])"

/agent set C
"Step 3 (accumulated results)"
```

### Conditional Routing

```bash
# Determine which agent to use based on task
if infrastructure_task:
  /agent set devops
else if knowledge_task:
  /agent set family
else if code_task:
  /agent set n8n-copilot-shim
```

### Error Recovery

```bash
/agent set <agent_name>
"I encountered an error: [details]. 
Please retry with a different approach."
```

## Related Documentation

- `SKILL_SUBAGENTS.md` - Detailed subagent orchestration guide
- `README.md` - Main project documentation
- `agents.json` - Agent configuration file
- `agent_manager.py` - Core implementation

## Extending the Agent System

### Custom Agents

Add custom agents by modifying `agents.json`:

```bash
# Edit agents.json
nano agents.json

# Add your agent entry
# Test the agent
/agent set my-custom-agent
```

### Agent-Specific Features

Create specialized agents for your workflows:

```json
{
  "name": "data-science",
  "description": "Data analysis and ML experimentation",
  "path": "/home/user/data-projects"
}
```

## Summary

The n8n-copilot-shim is an orchestration system that:

1. **Dynamically loads agents** from `agents.json`
2. **Manages agent contexts** and sessions independently
3. **Supports multiple runtimes** (Copilot, OpenCode, Claude, Gemini, CODEX)
4. **Enables workflows** across multiple specialized agents
5. **Maintains isolation** while allowing agent chaining

All agent configuration is stored in `agents.json`, making it easy to add, remove, or modify agents without changing the core codebase.
