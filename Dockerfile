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
#
# Pre-installed AI runtime CLIs (binaries only — NO credentials):
#   copilot  — GitHub Copilot CLI   (@github/copilot via npm)
#   claude   — Claude Code CLI      (@anthropic-ai/claude-code via npm)
#   gemini   — Gemini CLI           (@google/gemini-cli via npm)
#   codex    — OpenAI Codex CLI     (@openai/codex via npm)
#   opencode — OpenCode CLI         (binary from GitHub release)
#
# After deployment, the operator must authenticate each runtime they want
# to use.  See DOCKER_QUICKSTART.md § "Runtime Authentication".
# =============================================================================

# --------------- stage 1: build Python deps ---------------------------------
FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ libffi-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# --------------- stage 2: runtime -------------------------------------------
FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/leprachuan/Wee-Orchestrator"
LABEL org.opencontainers.image.description="Wee Orchestrator — Multi-agent AI orchestration platform"
LABEL org.opencontainers.image.licenses="MIT"

# ---- System deps + Node.js (for npm-based AI runtime CLIs) -----------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates gnupg ripgrep && \
    # Install Node.js 22 LTS from NodeSource
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/* && \
    useradd -r -m -s /bin/bash wee

# ---- AI Runtime CLIs (binaries only — NO auth/credentials) -----------------
# These are installed globally so agent_manager.py can find them via
# shutil.which() / find_executable().  Deployers must authenticate each
# runtime after startup — see DOCKER_QUICKSTART.md.
#
# copilot  — GitHub Copilot CLI   (requires `copilot auth login`)
# claude   — Claude Code CLI      (requires ANTHROPIC_API_KEY env var)
# gemini   — Gemini CLI           (requires GEMINI_API_KEY or `gemini auth`)
# codex    — OpenAI Codex CLI     (requires OPENAI_API_KEY env var)
RUN npm install -g --no-fund --no-audit \
        @github/copilot \
        @anthropic-ai/claude-code \
        @google/gemini-cli \
        @openai/codex && \
    npm cache clean --force

# opencode — OpenCode CLI (Go binary, no npm package available)
#            Requires API key configuration after startup.
RUN OPENCODE_VERSION=$(curl -fsSL https://api.github.com/repos/opencode-ai/opencode/releases/latest | \
        python3 -c "import sys,json; print(json.load(sys.stdin)['tag_name'].lstrip('v'))") && \
    curl -fsSL "https://github.com/opencode-ai/opencode/releases/download/v${OPENCODE_VERSION}/opencode-linux-x86_64.tar.gz" | \
        tar -xz -C /usr/local/bin && \
    chmod +x /usr/local/bin/opencode

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
RUN mkdir -p /data/sessions /data/scheduler/logs /data/scheduler/results /data/certs /home/wee/.gemini /home/wee/.claude /home/wee/.codex /home/wee/.copilot /home/wee/.config/gh \
             /app/config && \
    chown -R wee:wee /app /data /home/wee && \
    chmod +x /entrypoint.sh

# Default env vars (can all be overridden at runtime)
ENV API_PORT=8000 \
    APP_ENV=DOCKER \
    PYTHONUNBUFFERED=1 \
    SCHEDULER_JOBS_FILE=/data/scheduler/jobs.json \
    SCHEDULER_LOGS_DIR=/data/scheduler/logs/ \
    SCHEDULER_RESULTS_DIR=/data/scheduler/results/

EXPOSE 8000

USER wee

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsk https://localhost:${API_PORT}/api/v1/health || \
        curl -fs  http://localhost:${API_PORT}/api/v1/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
