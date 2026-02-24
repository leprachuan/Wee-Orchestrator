# Service Deployment Guide — Wee-Orchestrator

> **Installation and configuration of systemd services for Wee-Orchestrator production and development environments.**

## Overview

Wee-Orchestrator provides pre-configured systemd service files for easy deployment on Linux systems. These services run the core components:

- **Task Scheduler** - Schedules and executes jobs (AI or command mode)
- **Agent Manager API** - RESTful API for agent orchestration
- **Telegram Connector** - Telegram bot integration
- **WebEX Connector** - Cisco WebEx integration

Each service has a **production** and **development** variant.

## Prerequisites

- Linux system with systemd (Ubuntu 18.04+, Debian 10+, etc.)
- `sudo` access for service installation
- Python 3.9+
- Required dependencies installed (see main README)

## Service Files

All service files are included in the repository:

```
wee-orchestrator/
├── task-scheduler-executor.service        # Production scheduler
├── task-scheduler-executor-dev.service    # Development scheduler
├── agent-manager-api.service              # Production API
├── agent-manager-api-dev.service          # Development API
├── telegram-bot-listener.service          # Production Telegram
├── telegram-bot-listener-dev.service      # Development Telegram
├── webex-connector.service                # Production WebEx
└── webex-connector-dev.service            # Development WebEx
```

## Installation

### Quick Install (All Services)

```bash
cd /path/to/wee-orchestrator

# Copy all service files to systemd
sudo cp *.service /etc/systemd/system/

# Reload systemd configuration
sudo systemctl daemon-reload

# Enable all services (auto-start on boot)
sudo systemctl enable task-scheduler-executor.service
sudo systemctl enable agent-manager-api.service
sudo systemctl enable telegram-bot-listener.service
sudo systemctl enable webex-connector.service

# Enable dev services too (optional)
sudo systemctl enable task-scheduler-executor-dev.service
sudo systemctl enable agent-manager-api-dev.service
sudo systemctl enable telegram-bot-listener-dev.service
sudo systemctl enable webex-connector-dev.service
```

### Individual Service Installation

#### 1. Task Scheduler

**Purpose:** Execute scheduled jobs (recurring tasks, delayed commands, etc.)

```bash
# Install service
sudo cp task-scheduler-executor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable task-scheduler-executor.service

# Start service
sudo systemctl start task-scheduler-executor.service

# Verify
sudo systemctl status task-scheduler-executor.service
sudo journalctl -u task-scheduler-executor.service -f
```

#### 2. Agent Manager API

**Purpose:** RESTful API endpoint for agent operations

```bash
# Install service
sudo cp agent-manager-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable agent-manager-api.service

# Start service
sudo systemctl start agent-manager-api.service

# Verify (prod runs on port 8000)
curl http://127.0.0.1:8000/health
sudo journalctl -u agent-manager-api.service -f
```

#### 3. Telegram Connector

**Purpose:** Integration with Telegram bots

Requirements:
- Telegram bot token (from @BotFather on Telegram)
- Configuration file at `telegram_config.json`

```bash
# Install service
sudo cp telegram-bot-listener.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable telegram-bot-listener.service

# Start service
sudo systemctl start telegram-bot-listener.service

# Verify
sudo journalctl -u telegram-bot-listener.service -f
```

#### 4. WebEX Connector

**Purpose:** Integration with Cisco WebEx

Requirements:
- WebEx bot token and configuration
- Configuration file at `webex_config.json`

```bash
# Install service
sudo cp webex-connector.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable webex-connector.service

# Start service
sudo systemctl start webex-connector.service

# Verify
sudo journalctl -u webex-connector.service -f
```

## Development Environment

To run a separate development instance alongside production:

```bash
# Install dev services
sudo cp *-dev.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable agent-manager-api-dev.service
sudo systemctl enable task-scheduler-executor-dev.service

# Dev API runs on port 8001
curl http://127.0.0.1:8001/health

# Dev scheduler uses separate job queue
# Dev Telegram/WebEx use separate configuration files
```

## Configuration

### Credential Management ⚠️ SECURITY CRITICAL

**⛔ NEVER commit credentials to the repository!**

Service files contain placeholders for credentials:

```bash
# ❌ DON'T DO THIS - Hardcoded secrets
Environment="TELEGRAM_BOT_TOKEN=[REDACTED-TELEGRAM-TOKEN]"

# ✅ DO THIS - Placeholder in repo
Environment="TELEGRAM_BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN_HERE"
```

**Methods to set credentials securely:**

#### Option 1: Environment Variables (Recommended)

```bash
# Set before starting service
export TELEGRAM_BOT_TOKEN="your_actual_token_here"
export WEBEX_BOT_TOKEN="your_actual_token_here"

# Or use systemctl set-environment
sudo systemctl set-environment TELEGRAM_BOT_TOKEN="your_actual_token_here"

# Start service
sudo systemctl start telegram-bot-listener.service
```

#### Option 2: Systemd Drop-In Override

```bash
# Create override directory
sudo mkdir -p /etc/systemd/system/telegram-bot-listener.service.d/

# Create override file with secrets
sudo tee /etc/systemd/system/telegram-bot-listener.service.d/override.conf << EOF
[Service]
Environment="TELEGRAM_BOT_TOKEN=your_actual_token_here"
EOF

# Protect the override file
sudo chmod 600 /etc/systemd/system/telegram-bot-listener.service.d/override.conf

# Reload and restart
sudo systemctl daemon-reload
sudo systemctl restart telegram-bot-listener.service
```

#### Option 3: .env File (with caution)

```bash
# Create .env file in working directory
cat > /opt/n8n-copilot-shim/.env << EOF
TELEGRAM_BOT_TOKEN=your_actual_token_here
WEBEX_BOT_TOKEN=your_actual_token_here
RABBITMQ_PASSWORD=your_password_here
EOF

# Protect it
chmod 600 /opt/n8n-copilot-shim/.env

# Modify service to load it
# Add to [Service] section:
# EnvironmentFile=/opt/n8n-copilot-shim/.env
```

**Best Practice:** Use Option 2 (systemd drop-in overrides) for production.

### Getting Credentials

- **Telegram:** Message @BotFather, get token from `/newbot` command
- **WebEx:** Create bot at https://developer.webex.com/my-apps
- **RabbitMQ:** Set username/password when deploying RabbitMQ

### Environment Variables

Services can be configured via environment variables in the service files:

**Edit service file:**
```bash
sudo nano /etc/systemd/system/agent-manager-api.service
```

**Add environment variables in [Service] section:**
```ini
[Service]
...
Environment="AGENTS_CONFIG=/opt/custom/agents.json"
Environment="LOG_LEVEL=DEBUG"
Environment="API_PORT=8000"
```

**Reload and restart:**
```bash
sudo systemctl daemon-reload
sudo systemctl restart agent-manager-api.service
```

### Configuration Files

Each service may require configuration files:

- **Task Scheduler:** Uses `agents.json` for agent settings
- **Telegram Connector:** Requires `telegram_config.json`
- **WebEx Connector:** Requires `webex_config.json`
- **Agent Manager API:** Uses `agents.json`

Place config files in the repository root or in the path specified by service files.

## Service Management

### Start/Stop

```bash
# Start a service
sudo systemctl start task-scheduler-executor.service

# Stop a service
sudo systemctl stop task-scheduler-executor.service

# Restart a service
sudo systemctl restart task-scheduler-executor.service

# Restart all services
sudo systemctl restart agent-manager-api.service task-scheduler-executor.service telegram-bot-listener.service webex-connector.service
```

### Status & Logs

```bash
# Check service status
sudo systemctl status task-scheduler-executor.service

# Follow logs in real-time
sudo journalctl -u task-scheduler-executor.service -f

# View last 50 lines of logs
sudo journalctl -u task-scheduler-executor.service -n 50

# View logs since specific time
sudo journalctl -u task-scheduler-executor.service --since "2 hours ago"

# Filter by log level
sudo journalctl -u task-scheduler-executor.service -p err
```

### Auto-start on Boot

```bash
# Enable (auto-start on boot)
sudo systemctl enable task-scheduler-executor.service

# Disable (don't auto-start)
sudo systemctl disable task-scheduler-executor.service

# List enabled services
sudo systemctl list-unit-files | grep wee-orchestrator
```

## Troubleshooting

### Service fails to start

**Check logs:**
```bash
sudo journalctl -u task-scheduler-executor.service -n 100
```

**Common issues:**
- Python not found: Check shebang in service file (`/usr/bin/python3`)
- Missing dependencies: Run `pip install -r requirements.txt`
- Permission denied: Ensure user has permissions (service runs as `flipkey`)
- Port already in use: Check `ss -tlnp | grep :8000`

### Service runs but doesn't work

**Verify configuration:**
```bash
# Check if config files exist
ls -la /opt/n8n-copilot-shim/agents.json
ls -la /opt/n8n-copilot-shim/telegram_config.json

# Check environment variables in service file
systemctl show -p Environment task-scheduler-executor.service

# Check working directory
systemctl show -p WorkingDirectory agent-manager-api.service
```

### Port conflicts

```bash
# Check what's using the port
sudo ss -tlnp | grep :8000
sudo ss -tlnp | grep :8001  # Dev port

# Kill process using port (if needed)
sudo kill -9 <PID>
```

## Production vs Development

### Key Differences

| Aspect | Production | Development |
|--------|-----------|-------------|
| **Service suffix** | none | `-dev` |
| **API port** | 8000 | 8001 |
| **Scheduler DB** | `/opt/.task-scheduler/` | `/opt/.task-scheduler-dev/` |
| **Config** | `telegram_config.json` | `telegram_config.json` (separate token) |
| **Queue** | `webex` | `webex-dev` |
| **Restart on fail** | Yes (production-safe) | Yes (aggressive restart) |
| **Users affected** | Live users | Development only |
| **When to restart** | Maintenance window | Anytime |

### Running Both

Production and development instances can run simultaneously on the same machine:

```bash
# Both services running
sudo systemctl status agent-manager-api.service agent-manager-api-dev.service

# Prod on :8000, Dev on :8001
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8001/health

# Check both are responding
ss -tlnp | grep python3
```

## Health Checks

### API Health

```bash
# Production
curl http://127.0.0.1:8000/health

# Development
curl http://127.0.0.1:8001/health
```

### Scheduler Health

```bash
# Check job queue
tail -f /opt/.task-scheduler/executor.log

# Check if jobs are executing
grep "completed successfully" /opt/.task-scheduler/executor.log | tail -5
```

### Bot Connectivity

```bash
# Telegram
sudo journalctl -u telegram-bot-listener.service | grep -i "listening\|error"

# WebEx
sudo journalctl -u webex-connector.service | grep -i "listening\|error"
```

## Performance & Resource Limits

Service files include default resource limits:

```ini
[Service]
MemoryMax=256M     # Max memory
CPUQuota=50%       # Max CPU
```

To modify:

```bash
# Edit service file
sudo nano /etc/systemd/system/agent-manager-api.service

# Change limits (e.g., increase to 512M memory)
MemoryMax=512M

# Reload and restart
sudo systemctl daemon-reload
sudo systemctl restart agent-manager-api.service
```

## Security

### Service Isolation

- Services run as `flipkey` user (non-root)
- `NoNewPrivileges=true` prevents privilege escalation
- `PrivateTmp=true` isolates temp files
- Each service has its own working directory

### Credentials

**Never commit credentials to the repository:**

```bash
# ✅ CORRECT - Use environment variables or config files
export TELEGRAM_BOT_TOKEN="xxx"
systemctl set-environment TELEGRAM_BOT_TOKEN="xxx"

# ❌ WRONG - Don't hardcode in service files
# ExecStart=/usr/bin/python3 ... --token="xxx"
```

**Config files should be:**
- Git-ignored (add to `.gitignore`)
- Deployed separately
- Protected with restrictive permissions: `chmod 600 telegram_config.json`

## Updates & Maintenance

### Updating Service Files

After updating the repository:

```bash
# Copy updated service files
sudo cp *.service /etc/systemd/system/

# Reload systemd
sudo systemctl daemon-reload

# Restart affected services
sudo systemctl restart agent-manager-api.service task-scheduler-executor.service
```

### Graceful Restart

For zero-downtime updates:

```bash
# Restart dev service first (test the update)
sudo systemctl restart agent-manager-api-dev.service

# If successful, restart prod during maintenance window
sudo systemctl restart agent-manager-api.service
```

### Backup Before Changes

```bash
# Backup current config
sudo cp -r /opt/n8n-copilot-shim /opt/n8n-copilot-shim.backup

# Make changes
# ... edit files ...

# If needed, restore
sudo rm -rf /opt/n8n-copilot-shim && mv /opt/n8n-copilot-shim.backup /opt/n8n-copilot-shim
```

## Testing Services

### Unit Tests

```bash
# Run test suite
cd /opt/n8n-copilot-shim
python -m pytest tests/ -v
```

### Integration Tests

```bash
# Test API endpoint
curl -X POST http://127.0.0.1:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{"task": "echo test", "mode": "command"}'

# Test task scheduler
# Jobs with mode="command" should execute directly
# Jobs with mode="ai" should use agent_manager.py
```

## Next Steps

1. **Copy service files** to `/etc/systemd/system/`
2. **Create config files** (telegram_config.json, webex_config.json)
3. **Test services** individually before running in production
4. **Set up monitoring** (journalctl, process monitoring, health checks)
5. **Document your setup** (keep backup of working configurations)

## See Also

- [ARCHITECTURE.md](./ARCHITECTURE.md) - System architecture overview
- [TELEGRAM_PRODUCTION_SETUP.md](./TELEGRAM_PRODUCTION_SETUP.md) - Telegram-specific setup
- [WEBEX_DEPLOYMENT.md](./WEBEX_DEPLOYMENT.md) - WebEx-specific setup
- [README.md](./README.md) - Main documentation

---

**Last Updated:** 2026-02-24  
**Service Files Version:** v2.1.0
