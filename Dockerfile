# =============================================================================
# Wee Orchestrator — Docker Image
# =============================================================================
# Multi-stage build for a lean production image.
# All configuration is injected via environment variables at runtime.
# Config JSON files (agents.json, telegram_config.json, etc.) are mounted
# as volumes — see docker-compose.yml.
#
# Bot connectors start automatically if their credentials are configured:
#   Telegram: set TELEGRAM_BOT_TOKEN env var
#   WebEx:    set WEBEX_BOT_TOKEN + RABBITMQ_PASSWORD env vars
# =============================================================================

# --------------- stage 1: build deps ----------------------------------------
FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends         gcc g++ libffi-dev &&     rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# --------------- stage 2: runtime -------------------------------------------
FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/leprachuan/Wee-Orchestrator"
LABEL org.opencontainers.image.description="Wee Orchestrator — Multi-agent AI orchestration platform"
LABEL org.opencontainers.image.licenses="MIT"

# Runtime system deps
RUN apt-get update && apt-get install -y --no-install-recommends         git curl &&     rm -rf /var/lib/apt/lists/* &&     useradd -r -m -s /bin/bash wee

# Copy Python packages from builder
COPY --from=builder /install /usr/local

# App directory
WORKDIR /app

# Copy application code
COPY agent_manager.py .
COPY canvas.py .
COPY audio_transcriber.py .
COPY telegram_connector.py .
COPY webex_connector.py .
COPY scheduler/ scheduler/
COPY webui/ webui/
COPY static/ static/

# Entrypoint script (conditionally starts bots before API)
COPY entrypoint.sh /entrypoint.sh

# Copy example config files (for reference inside container)
COPY agents.example.json ./agents.example.json
COPY telegram_config.example.json ./telegram_config.example.json

# Create directories for runtime data
RUN mkdir -p /data/sessions /data/scheduler/logs /data/scheduler/results /data/certs              /app/config &&     chown -R wee:wee /app /data &&     chmod +x /entrypoint.sh

# Default env vars (can all be overridden at runtime)
ENV API_PORT=8000     APP_ENV=DOCKER     PYTHONUNBUFFERED=1     SCHEDULER_JOBS_FILE=/data/scheduler/jobs.json     SCHEDULER_LOGS_DIR=/data/scheduler/logs/     SCHEDULER_RESULTS_DIR=/data/scheduler/results/

EXPOSE 8000

USER wee

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3     CMD curl -fsk https://localhost:${API_PORT}/api/v1/health ||         curl -fs  http://localhost:${API_PORT}/api/v1/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
