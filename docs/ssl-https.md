# SSL/HTTPS Setup

Wee-Orchestrator supports HTTPS via self-signed or CA-issued certificates. HTTPS is **required** for WebUI live microphone recording on LAN — browsers block `navigator.mediaDevices` on non-secure (HTTP) origins except `localhost`.

## Quick Setup (Self-Signed)

### 1. Generate a certificate

```bash
mkdir -p /opt/n8n-copilot-shim-dev/certs

openssl req -x509 -newkey rsa:2048 \
  -keyout /opt/n8n-copilot-shim-dev/certs/dev-key.pem \
  -out /opt/n8n-copilot-shim-dev/certs/dev-cert.pem \
  -days 3650 -nodes \
  -subj "/CN=lepbuntu/O=Wee-Orchestrator-Dev" \
  -addext "subjectAltName=DNS:lepbuntu,DNS:localhost,IP:192.168.1.200,IP:127.0.0.1"
```

**Customize the `-addext` SANs** for your environment:
- Add your server's LAN IP (e.g., `IP:192.168.1.200`)
- Add your Tailscale IP if applicable (e.g., `IP:100.x.x.x`)
- Add any DNS hostnames you use to access the server

### 2. Configure environment variables

Add to `.env`:

```bash
# SSL/HTTPS
SSL_CERTFILE=/opt/n8n-copilot-shim-dev/certs/dev-cert.pem
SSL_KEYFILE=/opt/n8n-copilot-shim-dev/certs/dev-key.pem
```

### 3. Restart the service

```bash
sudo systemctl restart agent-manager-api-dev.service
```

### 4. Verify

```bash
# Check logs for "https://" in the listening URL
sudo journalctl -u agent-manager-api-dev.service --no-pager -n 5

# Test the endpoint
curl -sk https://127.0.0.1:8001/api/v1/transcription/status \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### 5. Accept the certificate in your browser

Navigate to `https://192.168.1.200:8001/ui/` — your browser will warn about the self-signed certificate. Click **Advanced → Proceed** (Chrome) or **Accept the Risk** (Firefox) to trust it. This only needs to be done once per browser.

## Disabling HTTPS

Remove or comment out the SSL variables in `.env` and restart:

```bash
# SSL_CERTFILE=
# SSL_KEYFILE=
```

The server falls back to HTTP automatically when these are unset or the files don't exist.

## How It Works

The `agent_manager.py` startup reads `SSL_CERTFILE` and `SSL_KEYFILE` from the environment. If both are set and point to existing files, uvicorn starts with SSL enabled. Otherwise, it starts in plain HTTP mode.

```
SSL_CERTFILE + SSL_KEYFILE set → HTTPS
Either unset or missing       → HTTP (fallback)
```

The WebUI uses relative API paths (`/api/v1/...`), so it automatically uses whichever protocol the page was loaded with — no frontend changes needed.

## Production Considerations

For production, consider:

- **Let's Encrypt / ACME**: Use `certbot` for a free, trusted certificate
- **Reverse proxy**: Put nginx or Caddy in front with automatic TLS termination
- **Certificate renewal**: Self-signed certs don't expire for 10 years, but CA certs need renewal

## Security Notes

- The `certs/` directory is git-ignored — private keys must never be committed
- Self-signed certs are fine for internal/dev use but browsers will show warnings
- The cert generated above is valid for 10 years (`-days 3650`)

## Why HTTPS Matters for the WebUI

The WebUI microphone button uses the `MediaRecorder` API for live voice recording. Browsers require a **secure context** (HTTPS or `localhost`) for `navigator.mediaDevices.getUserMedia()`. Without HTTPS:

- On `localhost` / `127.0.0.1`: mic works (always a secure context)
- On LAN IP via HTTP: mic falls back to an **audio file upload** picker with a one-time warning explaining why

With HTTPS enabled, live mic recording works from any origin.
