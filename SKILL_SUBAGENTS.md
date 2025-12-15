# Subagent Orchestration Skill

## Overview

This skill enables the n8n-copilot-shim to orchestrate and delegate work to subagents. It provides a framework for calling other agents, tracking their execution, and managing inter-agent communication.

## Key Concepts

### Subagents
Subagents are specialized AI agents configured in `agents.json` that can be called to perform specific tasks. Each subagent has:
- **Name**: Unique identifier for the subagent
- **Description**: What the subagent does
- **Path**: Working directory for the subagent

### Orchestration
The n8n-copilot-shim acts as an orchestrator that:
1. Reads available agents from `agents.json`
2. Delegates tasks to appropriate subagents
3. Tracks subagent execution status
4. Manages subagent session state
5. Aggregates results from multiple subagents

## Using Subagents

### Basic Subagent Call

To call a subagent, use the subagent call mechanism:

```bash
# Switch to a subagent
/agent set <subagent_name>

# Send a prompt (this executes in the subagent's context)
<your_prompt_or_instruction>

# The response comes from the subagent
```

### Example Workflow

```bash
# 1. Switch to the devops agent
/agent set devops

# 2. Make a request to the devops agent
"Update the Kubernetes deployment with the latest configuration"

# 3. The devops agent executes in its context (/opt/MyHomeDevops)
# 4. Results are returned and execution is tracked
```

### Tracking Subagent Sessions

Each subagent call creates a session that can be tracked:

```bash
# Get the current agent
/agent current

# Get the current session ID (visible in debug output)
# Sessions are stored per agent

# Resume a subagent session
# Switch to the agent and make a follow-up call
/agent set <subagent_name>
<follow_up_question>
# The subagent session is automatically resumed with context
```

## Available Subagents

The available subagents are defined in `agents.json`. To see what subagents are available:

```bash
/agent list
```

This will dynamically display all available subagents from your `agents.json` configuration.

### Example Subagents

```json
{
  "agents": [
    {
      "name": "devops",
      "description": "DevOps and infrastructure management",
      "path": "/opt/MyHomeDevops"
    },
    {
      "name": "family",
      "description": "Family knowledge and recipes",
      "path": "/opt/family_knowledge"
    },
    {
      "name": "orchestrator",
      "description": "AI runtime orchestration and management",
      "path": "/opt/n8n-copilot-shim"
    }
  ]
}
```

## Subagent Execution Model

### Session Management

When you switch to a subagent:
1. **New Session**: If no prior session exists, a new session is created
2. **Session Resumption**: If a session already exists for this subagent, it's resumed
3. **Context Isolation**: Each subagent has its own isolated session context
4. **Working Directory**: The subagent executes in its configured path

### Multi-Agent Workflows

You can create complex workflows by calling multiple subagents:

```bash
# Workflow: Update infrastructure and notify team

# 1. Check current infrastructure status with devops agent
/agent set devops
"What is the current status of the production cluster?"

# 2. Switch to family agent to log the update
/agent set family
"Log this infrastructure update to our knowledge base"

# 3. Switch to orchestrator for code changes
/agent set orchestrator
"Add support for the new service to agent_manager.py"
```

### Passing Context Between Agents

While each agent has an isolated session, you can manually pass information:

1. **Explicit Mention**: Reference previous results in your prompts
2. **Documentation**: Save important information to shared documentation
3. **Session IDs**: Track sessions if you need to refer to them later

Example:

```bash
# In devops agent
"The production IP is 10.0.1.100"

# Later, switch to family agent and reference this
/agent set family
"Update our documentation: Production IP is 10.0.1.100"
```

## Runtime and Model Configuration

Each subagent call uses the currently selected runtime and model:

```bash
# Set runtime for subagent calls
/runtime set codex

# Set model for subagent calls
/model set gpt-5.1-codex-max

# Now switch to a subagent - it will use codex runtime with codex-max model
/agent set devops
"Deploy the latest version"
```

## Session Storage and Tracking

Subagent sessions are stored based on the runtime:

### Copilot
- Location: `~/.copilot/session-state/`
- Format: `{session_id}.jsonl`

### OpenCode
- Location: `~/.local/share/opencode/storage/session/global/`
- Format: `{session_id}.json`

### Claude
- Location: `~/.claude/debug/`
- Format: `{session_id}.txt`

### Gemini
- Location: `~/.gemini/sessions/`
- Format: `{session_id}.json`

### CODEX
- Location: `~/.codex/sessions/YYYY/MM/DD/`
- Format: `rollout-<datetime>-{session_id}.jsonl`

## Advanced Patterns

### Agent Chaining

Create sequential workflows where output from one agent becomes input to another:

```bash
/agent set devops
"Generate a deployment plan"
# Get output...

/agent set family
"Here's a deployment plan from our devops team: [insert plan]
Please add this to our documentation"
```

### Conditional Agent Routing

Route to different agents based on task type:

```bash
# Infrastructure tasks
/agent set devops

# Knowledge tasks
/agent set family

# Code improvement tasks
/agent set orchestrator
```

### Parallel Agent Execution (Simulated)

While true parallel execution isn't available in this model, you can simulate it:

```bash
# Call all agents with similar tasks and collect results

/agent set devops
"What deployments are in progress?"

/agent set family
"What family tasks are pending?"

/agent set orchestrator
"What code improvements have been requested?"

# Aggregate results manually
```

## Debugging Subagent Calls

### View Current Agent
```bash
/agent current
```

### List Available Agents
```bash
/agent list
```

### Reset Agent Session
```bash
/session reset
```
This resets the current agent's session for a fresh start.

### View Runtime and Model
```bash
/runtime current
/model current
```

## Best Practices

1. **Clear Separation of Concerns**: Use agents for their intended purpose
   - devops agent → infrastructure tasks
   - family agent → knowledge management
   - orchestrator agent → code improvements

2. **Document Context**: When switching agents, explain the context
   ```bash
   /agent set devops
   "Based on the recent outage, create a post-mortem and update procedures"
   ```

3. **Check Agent Capabilities**: Verify the agent can perform the task
   ```bash
   /agent current
   # Review the description to ensure it's appropriate
   ```

4. **Session Management**: Understand when to reset vs. resume
   - **Resume**: For follow-up questions to the same agent
   - **Reset**: For completely new, unrelated tasks

5. **Error Handling**: If an agent fails, try a different approach
   ```bash
   /agent set <same_agent>
   "I got an error: [error message]. Can you retry with a different approach?"
   ```

## Session State and Context

### N8N Integration

Each N8N user gets their own session map:
- Location: `~/.copilot/n8n-session-map.json`
- Format: Maps N8N session IDs to backend session data
- Tracks: runtime, model, agent, and backend session ID per user

### Per-Agent Context

When you switch agents, the system:
1. Saves current agent session data
2. Creates/retrieves the new agent's session
3. Isolates context between agents
4. Maintains agent-specific session state

## Limitations and Considerations

1. **Context Isolation**: Agents don't have automatic access to other agents' memory
2. **File System Isolation**: Each agent works in its own path
3. **Session IDs**: Backend session IDs are different from N8N session IDs
4. **Runtime Switching**: Changing runtime affects all subsequent agent calls

## Troubleshooting

### Agent Not Found
```bash
/agent list
# Check if the agent name matches exactly (case-sensitive)
```

### Session Not Resuming
```bash
/session reset
/agent set <agent_name>
# Start with a fresh session
```

### Agent Executing in Wrong Directory
```bash
/agent current
# Verify the path is correct in agents.json
```

### Getting Default Responses
```bash
/runtime current
/model current
# Verify you're using the right runtime and model
```

## Advanced: Custom Agent Integration

To add a new subagent:

1. Create or identify a project directory: `/path/to/project`
2. Add to `agents.json`:
   ```json
   {
     "name": "my-agent",
     "description": "My custom agent description",
     "path": "/path/to/project"
   }
   ```
3. Use the new agent:
   ```bash
   /agent set my-agent
   "Your instructions"
   ```

The agent_manager will automatically load the new agent from `agents.json`.

## See Also

- `AGENTS.md` - Orchestration and agent overview
- `README.md` - Main documentation
- `agents.json` - Agent configuration file
- `agent_manager.py` - Core implementation
