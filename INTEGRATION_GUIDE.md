# Telegram Connector Integration Guide

## Overview

The Telegram connector (`telegram_connector.py`) is a shim that bridges Telegram chat with the N8N Copilot agent system via `agent_manager.py`.

## Architecture

```
Telegram User
    ↓
telegram_connector.py (polls Telegram API)
    ↓
TelegramConnector class (handles messages, user pairing, routing)
    ↓
agent_manager.py (SessionManager, executes queries)
    ↓
Configured AI agents (orchestrator, devops, family, projects, etc.)
```

## Current Implementation Status

✅ **Complete:**
- Bot connectivity verification
- Message polling and receiving
- User pairing by Telegram ID
- Configuration management
- User access control (whitelist/blacklist)
- Command handling (/start, /help, /agent, /status)
- Session tracking
- Token security (.gitignore)

🔄 **Ready for Integration:**
- Connection to `agent_manager.py` SessionManager
- Query routing to agents
- Response streaming/formatting

## Integration Steps

### 1. Update telegram_connector.py _query_agent method

Replace the placeholder in `_query_agent()`:

```python
def _query_agent(
    self, query: str, agent: str, model: str, user_id: int
) -> str:
    """Query the agent_manager"""
    from agent_manager import SessionManager
    
    try:
        # Create session with user ID as context
        session_mgr = SessionManager(
            n8n_session_id=f"telegram_{user_id}",
            agent=agent,
            model=model
        )
        
        # Run the query
        result = session_mgr.run_copilot(query)
        
        return result if result else "No response from agent"
    except Exception as e:
        return f"Error: {str(e)[:200]}"
```

### 2. Update agent_manager.py

Add support for Telegram session context:

```python
class SessionManager:
    def __init__(self, n8n_session_id, agent=None, model=None, source="telegram"):
        self.n8n_session_id = n8n_session_id
        self.source = source
        self.agent = agent or get_default_agent()
        self.model = model or get_default_model()
        # ... rest of init
```

### 3. Test Integration

```bash
# Start connector in background
export TELEGRAM_BOT_TOKEN="your-token"
python telegram_connector.py &

# In Telegram, message your bot:
# User: "What agents are available?"
# Bot should respond via agent_manager
```

## Configuration Workflow

### First-Time Setup

```bash
# 1. Create bot with @BotFather
# 2. Get token: XXX:YYY

# 3. Set config
export TELEGRAM_BOT_TOKEN="XXX:YYY"

# 4. Allow authorized users
python telegram_connector.py --allow-user 123456789

# 5. Start connector
python telegram_connector.py
```

### User Pairing Flow

When a user sends their first message:

1. **Connector** receives message from Telegram API
2. **Check Access**: Is user in allowed_users? (if list not empty)
3. **Create Session**: Store user pairing with:
   - Telegram user ID
   - Username
   - Timestamp
   - Default agent (from config)
   - Default model (from config)
4. **Send to Agent**: Pass query to agent_manager
5. **Return Response**: Send agent response back to Telegram

### User Configuration

Each user's session data:

```json
{
  "user_id": 123456789,
  "username": "john_doe",
  "paired_at": "2024-01-15T10:30:00",
  "agent": "orchestrator",
  "model": "gpt-5-mini"
}
```

Users can change their agent with `/agent` command:

```
User: /agent devops
Bot: ✅ Agent set to: devops
User: Deploy the latest version
Bot: [routes to devops agent via agent_manager]
```

## File Structure

```
n8n-copilot-shim-dev/
├── telegram_connector.py          # Main Telegram shim
├── telegram_config.json           # Runtime config (DO NOT COMMIT)
├── telegram_config.example.json   # Config template
├── TELEGRAM_CONNECTOR.md          # Full documentation
├── INTEGRATION_GUIDE.md           # This file
├── agent_manager.py               # Core agent orchestrator
├── agents.json                    # Agent configuration
└── .gitignore                     # Includes telegram_config.json
```

## Security Checklist

- ✅ Token in telegram_config.json (.gitignore)
- ✅ Use environment variable for token in production
- ✅ User whitelist for restricted access
- ✅ No credentials in logs
- ✅ Rate limiting ready (future enhancement)

## Production Deployment

### Option 1: Systemd Service

```ini
[Unit]
Description=Telegram Copilot Connector
After=network.target

[Service]
Type=simple
User=copilot
WorkingDirectory=/opt/n8n-copilot-shim-dev
Environment="TELEGRAM_BOT_TOKEN=XXX:YYY"
ExecStart=/usr/bin/python3 telegram_connector.py
Restart=always
RestartSec=10
```

### Option 2: Docker

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY . /app

RUN pip install -r requirements.txt

ENV TELEGRAM_BOT_TOKEN=""
ENV COPILOT_DEFAULT_AGENT="orchestrator"

CMD ["python", "telegram_connector.py"]
```

### Option 3: N8N Workflow

Create an N8N workflow that:

1. Triggers the Telegram connector periodically
2. Processes messages through the shim
3. Updates user session data
4. Logs interactions

## Troubleshooting

### Bot doesn't respond

```bash
# 1. Verify token
python -c "import telegram_connector; print('✅ Module loads')"

# 2. Check connectivity
curl https://api.telegram.org/bot{TOKEN}/getMe

# 3. Verify user is allowed
python telegram_connector.py --list-users

# 4. Check logs
tail -f telegram_connector.py output
```

### User not paired

- First message should create pairing
- Check telegram_config.json exists and is writable
- Verify JSON is valid: `python -m json.tool telegram_config.json`

### Agent not responding

- Verify agent_manager.py integration
- Check agent exists in agents.json
- Verify default_agent in config is valid

## Next Steps

1. **Implement agent_manager integration** in _query_agent()
2. **Add response formatting** for Telegram's message length limits (4096 chars)
3. **Implement message history** for multi-turn conversations
4. **Add webhook mode** (instead of polling) for better performance
5. **Create N8N workflow** that uses the connector
6. **Add logging** to dedicated file for debugging
7. **Implement rate limiting** per user
8. **Add inline keyboards** for command suggestions

## Related Files

- `agent_manager.py` - Core agent orchestration
- `agents.json` - Agent configuration
- `TELEGRAM_CONNECTOR.md` - Full user documentation
- `README.md` - Project overview
