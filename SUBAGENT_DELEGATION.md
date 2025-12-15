# Sub-Agent Delegation Guide

## Overview

The n8n-copilot-shim supports delegating tasks to specialized sub-agents. Each sub-agent has its own working directory containing domain-specific knowledge and files.

## Available Agents

```bash
/agent list
```

Output shows all available agents with descriptions:

- **devops**: DevOps and infrastructure management (`/opt/MyHomeDevops`)
- **family**: Family knowledge and recipes (`/opt/family_knowledge`)
- **projects**: Software development projects (`/Users/fosterlipkey/Documents/projects`)
- **orchestrator**: AI runtime orchestration (`/opt/n8n-copilot-shim`)

## How to Use Sub-Agents

### Method 1: Switch Agent (Persistent)

Switch to an agent and all subsequent prompts will use that agent's context:

```bash
/agent set family
Now ask: "What are Parker's Christmas ideas?"
```

The agent will remain active until you switch to another agent.

**How it works:**
1. `/agent set family` changes the active agent
2. Subsequent prompts execute in the family agent's directory
3. The AI model receives context about the family agent
4. All prompts are executed with `cwd=/opt/family_knowledge`

### Method 2: Invoke Agent (One-Off Delegation)

Delegate a single task to an agent without changing the active agent:

```bash
/agent invoke family "What are Parker's Christmas ideas for 2025?"
```

**How it works:**
1. Creates a new temporary session for the sub-agent
2. Executes the prompt in the sub-agent's context
3. Returns the result
4. Active agent remains unchanged

**Syntax:**
```
/agent invoke <agent_name> "<prompt>"
```

Note: Prompts with multiple words should be quoted.

## Agent Context Information

When you interact with an agent, it receives:

1. **Agent Name**: Identifies which agent it is
2. **Agent Description**: What the agent is responsible for
3. **Session ID**: Unique session identifier
4. **Workspace Files**: List of available files in agent's directory
5. **Current Prompt**: The task you're asking it to complete

Example context provided to an agent:

```
[Session ID: abc123def456]
[Agent Context: family]
Family knowledge and recipes. Shared family documentation and cooking recipes.

Available resources in this agent's workspace:
  - family_christmas_list.md
  - FAMILY.md
  - Favorite_Dinners.md
  - holidays/
  - personal_interests/

User Request:
What are Parker's Christmas ideas for 2025?
```

## Real-World Examples

### Example 1: Find Family Christmas Ideas

**Scenario:** You want to know what gifts to buy for Parker

**Using Method 1 (Switch Agent):**
```bash
/agent set family
"What are Parker's Christmas ideas for 2025? Please provide all categories."
```

**Using Method 2 (Invoke Agent):**
```bash
/agent invoke family "What are Parker's Christmas ideas for 2025? Please provide all categories."
```

**What happens:**
1. Agent executes in `/opt/family_knowledge` directory
2. Agent can see `holidays/Christmas 2025/family_christmas_list.md`
3. Agent searches for Parker section in the list
4. Agent returns all gift ideas, book suggestions, and stocking stuffers

**Expected response includes:**
- Gymnastics camp ideas
- Rock climbing / trampoline park passes
- Simone Biles autobiography
- Balance beam for home practice
- Violin bow upgrade
- Book series recommendations (Wings of Fire, Keeper of the Lost Cities, etc.)
- Stocking stuffer ideas (Percy Jackson items, gymnastics gear, violin accessories)

### Example 2: Get Recipe from Family Knowledge

**Using Method 1:**
```bash
/agent set family
"What is our family's favorite recipe for baked chicken?"
```

**Using Method 2:**
```bash
/agent invoke family "What is our family's favorite recipe for baked chicken?"
```

### Example 3: Check DevOps Infrastructure

**Using Method 1:**
```bash
/agent set devops
"What Kubernetes clusters do we have deployed?"
```

**Using Method 2:**
```bash
/agent invoke devops "Check the status of production deployments"
```

### Example 4: Access Project Code

**Using Method 1:**
```bash
/agent set projects
"Show me the authentication logic in the main web app"
```

**Using Method 2:**
```bash
/agent invoke projects "What database schema do we use for user profiles?"
```

## Working Directory Behavior

Each agent has a working directory (`cwd`) where CLI tools like Copilot, Claude, etc. execute:

| Agent | Working Directory |
|-------|-------------------|
| devops | `/opt/MyHomeDevops` |
| family | `/opt/family_knowledge` |
| projects | `/Users/fosterlipkey/Documents/projects` |
| orchestrator | `/opt/n8n-copilot-shim` |

When you switch to an agent or invoke it, the CLI runs in that directory, giving the AI model access to:
- All files in that directory
- Version control history (git)
- Project structure
- Documentation
- Data files

## Agent Context Awareness

When an agent is active or invoked, the AI model is aware:

1. **Which agent it is** - "[Agent Context: family]"
2. **What the agent's purpose is** - Gets the description
3. **What resources are available** - First 10 files in the directory
4. **The session ID** - For tracking

This helps the AI model provide better responses that are:
- Domain-specific
- File-aware
- Context-appropriate
- Focused on the agent's responsibilities

## Tips and Best Practices

### 1. Use Method 2 for One-Off Queries

If you only need to ask one thing from another agent:

```bash
/agent invoke family "Find gifts for Oliver under $50"
```

This is cleaner than:
```bash
/agent set family
"Find gifts for Oliver under $50"
/agent set devops  # to switch back
```

### 2. Use Method 1 for Multi-Part Tasks

If you need to ask several related questions:

```bash
/agent set family
"What Christmas ideas do we have for Parker?"
"Which ones are already purchased?"
"What budget do we have left?"
```

Then:
```bash
/agent set <previous_agent>  # Switch back
```

### 3. Be Specific in Prompts

Give the agent clear instructions:

✗ Poor: "Find Christmas ideas"
✓ Good: "Look in the family Christmas list for Parker and list all gift ideas organized by category"

### 4. Reference Files Explicitly

If the agent should focus on specific files:

```bash
/agent invoke family "In the family_christmas_list.md file, what are the purchased items for Leslie?"
```

### 5. Quote Multi-Word Prompts

Always quote prompts with spaces:

✗ Bad: `/agent invoke family What are Parker's Christmas ideas?`
✓ Good: `/agent invoke family "What are Parker's Christmas ideas?"`

## How Agent Delegation Works

### Architecture

```
User Request
    ↓
Parse /agent invoke <agent> <prompt>
    ↓
Check agent exists
    ↓
Build context with:
  - Agent name & description
  - Workspace file list
  - Session ID
    ↓
Set working directory to agent's path
    ↓
Execute prompt with current runtime
  (Copilot/OpenCode/Claude/Gemini/CODEX)
    ↓
AI model accesses files in agent's directory
    ↓
Return specialized response
```

### Key Points

1. **Working Directory Changes** - CLI runs in agent's directory
2. **Context Awareness** - AI knows which agent it is
3. **File Access** - AI can read files in agent's workspace
4. **Session Tracking** - Each delegation gets unique session ID
5. **Runtime Flexibility** - Works with any configured runtime

## Troubleshooting

### Agent Response Doesn't Find Expected Files

**Problem:** Agent says it can't find a file you know exists

**Solution:**
1. Verify file exists: Check `/opt/family_knowledge/holidays/Christmas\ 2025/` manually
2. Check path: Ensure path in agents.json is correct
3. Be explicit: Tell agent exact filename in your prompt

```bash
/agent invoke family "Open holidays/Christmas 2025/family_christmas_list.md and find Parker section"
```

### Agent Switching Took Too Long

**Problem:** Agent set takes a long time

**Solution:**
1. This is normal for first execution (creates new session)
2. Subsequent prompts are faster (resume existing session)
3. Use `/agent invoke` for one-off queries (avoid session overhead)

### Can't Find Agent

**Problem:** "Unknown agent: 'xyz'"

**Solution:**
1. List available agents: `/agent list`
2. Check agent name spelling
3. Verify agents.json has the agent configured

### Working Directory Doesn't Seem Right

**Problem:** Agent says "file not found" but file exists

**Solution:**
1. Check agents.json has correct path
2. Verify directory exists: `ls /opt/family_knowledge`
3. Check file permissions: `ls -la /opt/family_knowledge/holidays/`

## Advanced Usage

### Chaining Agent Calls

Delegate to multiple agents in sequence:

```bash
/agent invoke devops "What is the production database status?"
# Now use that info...
/agent invoke family "Based on this status, should we cancel the Christmas party?"
```

### Passing Context Between Agents

Include previous agent's response in next query:

```bash
/agent invoke devops "List all servers currently down"
# Then...
/agent invoke projects "The following servers are down: [output]. How does this affect our deployments? [list]"
```

### Creating Agent Workflows

Combine multiple agents for complex tasks:

```bash
# 1. Get infrastructure status
/agent invoke devops "Is production healthy?"

# 2. Document findings
/agent invoke family "Add this infrastructure update: [status]"

# 3. Update project plans
/agent invoke projects "Given infrastructure [status], what development work should we prioritize?"
```

## See Also

- [AGENTS.md](./AGENTS.md) - Agent orchestration overview
- [SKILL_SUBAGENTS.md](./SKILL_SUBAGENTS.md) - Detailed subagent skills
- [README.md](./README.md) - Main project documentation

## FAQ

**Q: Can agents talk to each other?**
A: Not automatically. You can read responses from one agent and pass them to another using `/agent invoke`.

**Q: Do agents share sessions?**
A: No. Each agent maintains its own separate sessions for isolation and security.

**Q: Can I create new agents?**
A: Yes. Edit `agents.json` and add a new agent entry. It will be immediately available.

**Q: What happens if an agent path doesn't exist?**
A: The agent defaults to `/opt/MyHomeDevops`. You should fix the path in `agents.json`.

**Q: Can agents access each other's directories?**
A: Only through their working directory (cwd). Each agent's default context is its own directory.

