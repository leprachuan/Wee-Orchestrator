# WebEX Connector Deployment Guide

## Installation as systemd Service

### 1. Copy Service File

```bash
sudo cp /opt/n8n-copilot-shim-dev/webex-connector.service /etc/systemd/system/
```

### 2. Reload systemd

```bash
sudo systemctl daemon-reload
```

### 3. Enable and Start the Service

```bash
# Enable service to start on boot
sudo systemctl enable webex-connector.service

# Start the service immediately
sudo systemctl start webex-connector.service
```

### 4. Verify It's Running

```bash
# Check status
sudo systemctl status webex-connector.service

# View logs in real-time
sudo journalctl -u webex-connector.service -f

# View recent logs
sudo journalctl -u webex-connector.service -n 50
```

## Service Management

### Start/Stop/Restart

```bash
# Start
sudo systemctl start webex-connector.service

# Stop
sudo systemctl stop webex-connector.service

# Restart
sudo systemctl restart webex-connector.service

# Reload configuration
sudo systemctl reload webex-connector.service
```

### Check Service Status

```bash
# Status (will show if running/failed/inactive)
sudo systemctl status webex-connector.service

# Check if enabled on boot
sudo systemctl is-enabled webex-connector.service

# View service details
systemctl show webex-connector.service
```

### View Logs

```bash
# Real-time logs
sudo journalctl -u webex-connector.service -f

# Last 100 lines
sudo journalctl -u webex-connector.service -n 100

# Filter by level (error, warning, info)
sudo journalctl -u webex-connector.service -p err

# Since last boot
sudo journalctl -u webex-connector.service -b

# Time range
sudo journalctl -u webex-connector.service --since "2 hours ago"
```

## Configuration

### Environment Variables

Edit the service file to change settings:

```bash
sudo nano /etc/systemd/system/webex-connector.service
```

Modify the `Environment=` lines:

```ini
Environment="WEBEX_BOT_TOKEN=your-token-here"
Environment="RABBITMQ_HOST=192.168.0.85"
Environment="RABBITMQ_PORT=5672"
Environment="RABBITMQ_USER=admin"
Environment="RABBITMQ_PASSWORD=your-password"
```

After editing, reload:

```bash
sudo systemctl daemon-reload
sudo systemctl restart webex-connector.service
```

### Configuration File

Or use the JSON config file instead:

```bash
# Edit the config file
nano /opt/n8n-copilot-shim-dev/webex_config.json
```

The service will read from this file automatically.

## Troubleshooting

### Service Won't Start

```bash
# Check if there are any syntax errors
sudo systemctl start webex-connector.service

# View detailed error logs
sudo journalctl -u webex-connector.service -n 50 -e

# Check if Python is installed
python3 --version

# Verify file permissions
ls -la /opt/n8n-copilot-shim-dev/webex_connector.py
```

### Connection Issues

```bash
# Test RabbitMQ connectivity
telnet 192.168.0.85 5672

# Test WebEX API connectivity
curl -H "Authorization: Bearer YOUR_TOKEN" https://webexapis.com/v1/me

# Check DNS resolution
nslookup 192.168.0.85
```

### High Memory Usage

Adjust in service file:

```ini
MemoryMax=512M  # Increase if needed
```

### CPU Throttling

Adjust in service file:

```ini
CPUQuota=50%  # Increase to 100% if needed
```

## Monitoring

### Create a monitoring alias

```bash
# Add to ~/.bashrc or ~/.zshrc
alias webex-log='sudo journalctl -u webex-connector.service -f'
alias webex-status='sudo systemctl status webex-connector.service'
```

Then use:

```bash
webex-log      # View logs
webex-status   # Check status
```

### Set up alerts (optional)

```bash
# Check every 5 minutes if service is running
*/5 * * * * systemctl is-active --quiet webex-connector.service || systemctl restart webex-connector.service
```

## Security Notes

⚠️ **Important**:

- The service file contains credentials in plain text (environment variables)
- Restrict file permissions: `sudo chmod 600 /etc/systemd/system/webex-connector.service`
- Consider using environment files instead: `EnvironmentFile=/etc/webex-connector.env`
- Set restrictive permissions on the config: `chmod 600 /opt/n8n-copilot-shim-dev/webex_config.json`

## Auto-restart on Failure

The service is configured to automatically restart on failure with 10-second delay:

```ini
Restart=always
RestartSec=10
```

To disable auto-restart:

```bash
sudo systemctl mask webex-connector.service
```

To re-enable:

```bash
sudo systemctl unmask webex-connector.service
```

## Uninstall

```bash
# Stop the service
sudo systemctl stop webex-connector.service

# Disable auto-start
sudo systemctl disable webex-connector.service

# Remove service file
sudo rm /etc/systemd/system/webex-connector.service

# Reload systemd
sudo systemctl daemon-reload
```

## Integration with n8n

The WebEX connector will:
1. Listen to RabbitMQ queue `webex`
2. Route messages to `agent_manager` (which handles n8n workflows)
3. Send responses back to WebEX via API
4. Support slash commands that interact with the agent

## Testing

```bash
# Send a test message to RabbitMQ
python3 << 'EOF'
import json
import pika

credentials = pika.PlainCredentials('admin', '[REDACTED-RABBITMQ-PASSWORD]')
parameters = pika.ConnectionParameters('192.168.0.85', credentials=credentials)
connection = pika.BlockingConnection(parameters)
channel = connection.channel()

message = {
    "id": "test-message-1",
    "roomId": "test-room-id",
    "roomType": "direct",
    "text": "Hello from test!",
    "personId": "test-person-id",
    "personEmail": "test@example.com",
    "created": "2026-02-15T00:00:00Z"
}

channel.basic_publish(exchange='', routing_key='webex', body=json.dumps(message))
connection.close()
print("✅ Test message sent to queue")
EOF
```

## Performance

- Memory: Limited to 512MB
- CPU: Limited to 50% of one core
- Restart delay: 10 seconds on failure
- Logging: All output to journalctl

Adjust these in the service file as needed for your environment.
