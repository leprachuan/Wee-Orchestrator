# Telegram Connector Quick Start

## What was added

A new **Telegram connector shim** that bridges Telegram chat with your N8N Copilot agent system.

### Files Created

1. **`telegram_connector.py`** (11.5 KB)
   - Main connector implementation
   - Polls Telegram API for messages
   - Manages user pairing and sessions
   - Routes messages to agent_manager.py

2. **`telegram_config.json`** (already loaded with your bot token)
   - Runtime configuration
   - Tracks allowed users
   - Stores user session data
   - **Not committed to git (in .gitignore)**

3. **`telegram_config.example.json`**
   - Template for manual setup
   - Safe to commit

4. **`TELEGRAM_CONNECTOR.md`**
   - Full feature documentation
   - Configuration guide
   - User command reference
   - Troubleshooting

5. **`INTEGRATION_GUIDE.md`**
   - Architecture overview
   - Integration with agent_manager.py
   - Deployment options
   - Production checklist

### Files Modified

- **`pyproject.toml`**
  - Added `requests` dependency
  - Added `n8n-telegram-connector` CLI command

- **`.gitignore`**
  - Added `telegram_config.json` (protects your token)

- **`README.md`**
  - Added Telegram connector section

## Quick Start

### 1. Start the Connector

```bash
# Token is already configured
python telegram_connector.py
```

The connector will:
- Connect to Telegram API ✅ (verified)
- Poll for incoming messages
- Handle user pairing
- Route to agents

### 2. Allow Users

Add your Telegram user ID:

```bash
python telegram_connector.py --allow-user YOUR_USER_ID
```

Or use the bot commands (see below).

### 3. Chat with the Bot

On Telegram, message `@lipkeyhomebotdev_bot`:

```
/start          → Welcome message
/help           → Available commands
/status         → Your session info
/agent devops   → Switch to devops agent
/agent family   → Switch to family agent
```

Then send any message to route it to your agent.

## Features

✅ **User Management**
- Track users by Telegram ID
- Whitelist/blacklist users
- Per-user session data

✅ **Session Management**
- Automatic user pairing
- Agent selection per user
- Model configuration per user
- Session persistence

✅ **Commands**
- `/start` - Welcome
- `/help` - Command list
- `/agent <name>` - Switch agent
- `/status` - Session info
- `/list_agents` - Available agents

✅ **Security**
- Token in .gitignore
- Environment variable support
- User access control
- Config-based permissions

## Configuration

Edit `telegram_config.json` to:

```json
{
  "token": "8594875048:AAEcvAxxVFQSI-yVZDIV-PTK1wHEYLKGuYU",
  "allowed_users": [123456789],           // Telegram user IDs
  "default_agent": "orchestrator",        // Default agent
  "default_model": "gpt-5-mini",         // Default model
  "user_pairings": {}                    // Auto-filled
}
```

**Empty `allowed_users` = allow all users**

## Integration with agent_manager.py

The connector is ready for full integration with `agent_manager.py`:

Current: Placeholder responses
Next: Route to actual agents via SessionManager

See `INTEGRATION_GUIDE.md` for implementation details.

## Testing

```bash
# Test module loads
python3 -c "from telegram_connector import TelegramConnector; print('✅ OK')"

# Test config
python3 telegram_connector.py --list-users

# Test help
python3 telegram_connector.py --help

# Test user management
python3 telegram_connector.py --allow-user 999888777
python3 telegram_connector.py --deny-user 999888777
```

## Deployment Options

### Development (Polling)
```bash
python telegram_connector.py
```

### Production (Systemd Service)
See `TELEGRAM_CONNECTOR.md` - systemd section

### Docker
See `INTEGRATION_GUIDE.md` - Docker section

## What's Next

1. **Complete agent_manager integration** - Uncomment the SessionManager code in _query_agent()
2. **Add message formatting** - Handle Telegram's 4096-char limit
3. **Add conversation history** - Keep context across messages
4. **Deploy as service** - Use systemd or Docker
5. **Webhook support** - Replace polling with webhooks (faster)

## Security Notes

⚠️ **Important**
- `telegram_config.json` contains your bot token
- It's in `.gitignore` - never commit it
- Use environment variable `TELEGRAM_BOT_TOKEN` in production
- User whitelist controls who can access the bot

## Troubleshooting

**Bot doesn't respond?**
- Verify token: `python3 -c "import requests; import json; r = requests.get('https://api.telegram.org/bot8594875048:AAEcvAxxVFQSI-yVZDIV-PTK1wHEYLKGuYU/getMe'); print(json.dumps(r.json(), indent=2))"`
- Check user is in allowed list: `python3 telegram_connector.py --list-users`

**User not paired?**
- First message should create session
- Check `telegram_config.json` is writable
- Verify allowed_users includes the user ID

**Integration not working?**
- See `INTEGRATION_GUIDE.md` for implementation
- Uncomment SessionManager code in _query_agent()

## Files Summary

| File | Purpose | Size |
|------|---------|------|
| `telegram_connector.py` | Main implementation | 11.5 KB |
| `telegram_config.json` | Runtime config (secret) | 0.2 KB |
| `telegram_config.example.json` | Config template | 0.2 KB |
| `TELEGRAM_CONNECTOR.md` | Full documentation | 5.3 KB |
| `INTEGRATION_GUIDE.md` | Integration guide | 6.3 KB |

## Bot Info

Your bot is configured and verified:
- **Bot ID**: 8594875048
- **Bot Name**: lipkey_homebot_dev
- **Username**: @lipkeyhomebotdev_bot
- **Status**: ✅ Connected and ready
