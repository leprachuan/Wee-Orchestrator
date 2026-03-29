#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Wee Orchestrator — Docker Setup
# Auto-detects AI runtimes, generates docker-compose.yml, and starts containers.
# =============================================================================

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

banner() {
  echo -e "${CYAN}${BOLD}"
  echo "╔══════════════════════════════════════════════╗"
  echo "║   🍀  Wee Orchestrator — Docker Setup  🍀   ║"
  echo "╚══════════════════════════════════════════════╝"
  echo -e "${NC}"
}

info()  { echo -e "${GREEN}✓${NC} $*"; }
warn()  { echo -e "${YELLOW}○${NC} $*"; }
err()   { echo -e "${RED}✗${NC} $*"; }
step()  { echo -e "\n${BOLD}▸ $*${NC}"; }

# ── Pre-flight checks ────────────────────────────────────────────────────────
banner

step "Checking prerequisites..."
if ! command -v docker &>/dev/null; then
  err "Docker is not installed. Install it first: https://docs.docker.com/get-docker/"
  exit 1
fi
info "Docker found: $(docker --version)"

if ! docker compose version &>/dev/null; then
  err "Docker Compose plugin not found. Install it: https://docs.docker.com/compose/install/"
  exit 1
fi
info "Docker Compose found: $(docker compose version --short)"

# ── Runtime auto-detection ────────────────────────────────────────────────────
step "Detecting AI runtimes..."

declare -A RUNTIME_PATHS
DETECTED_RUNTIME=""
DETECTED_RUNTIMES=()

detect_runtime() {
  local name="$1"; shift
  for path in "$@"; do
    expanded=$(eval echo "$path" 2>/dev/null) || continue
    if [[ -n "$expanded" && -x "$expanded" ]]; then
      RUNTIME_PATHS[$name]="$expanded"
      DETECTED_RUNTIMES+=("$name")
      info "${name} found at ${expanded}"
      return 0
    fi
  done
  warn "${name} not found (optional)"
  return 1
}

detect_runtime "copilot" "~/.local/bin/copilot" "/usr/local/bin/copilot" "$(which copilot 2>/dev/null || true)" || true
detect_runtime "claude"  "~/.local/bin/claude"  "/usr/local/bin/claude"  "$(which claude 2>/dev/null || true)" || true
detect_runtime "gemini"  "~/.local/bin/gemini"  "/usr/local/bin/gemini"  "$(which gemini 2>/dev/null || true)" || true
detect_runtime "opencode" "~/.local/bin/opencode" "/usr/local/bin/opencode" "$(which opencode 2>/dev/null || true)" || true

# Detect gh CLI
GH_PATH=""
if command -v gh &>/dev/null; then
  GH_PATH="$(which gh)"
  info "gh CLI found at ${GH_PATH}"
else
  warn "gh CLI not found (optional)"
fi

# Detect auth configs
GH_HOSTS_PATH=""
for p in ~/.config/gh/hosts.yml /root/.config/gh/hosts.yml; do
  expanded=$(eval echo "$p" 2>/dev/null) || continue
  if [[ -f "$expanded" ]]; then
    GH_HOSTS_PATH="$expanded"
    info "gh auth config found at ${expanded}"
    break
  fi
done
if [[ -z "$GH_HOSTS_PATH" ]]; then warn "gh auth config not found"; fi

CLAUDE_DIR=""
for p in ~/.claude /root/.claude; do
  expanded=$(eval echo "$p" 2>/dev/null) || continue
  if [[ -d "$expanded" ]]; then
    CLAUDE_DIR="$expanded"
    info "Claude config dir found at ${expanded}"
    break
  fi
done

# Priority: copilot > claude > gemini > opencode
for rt in copilot claude gemini opencode; do
  if [[ -v "RUNTIME_PATHS[$rt]" ]]; then
    DETECTED_RUNTIME="$rt"
    break
  fi
done

if [[ -z "$DETECTED_RUNTIME" ]]; then
  err "No AI runtimes detected! Install at least one (copilot, claude, gemini, or opencode)."
  exit 1
fi
echo -e "\n${BOLD}Default runtime: ${GREEN}${DETECTED_RUNTIME}${NC}"

# ── Configuration ─────────────────────────────────────────────────────────────
step "Configuring deployment..."

DEFAULT_HOST=$(hostname -I 2>/dev/null | awk '{print $1}')
[[ -z "$DEFAULT_HOST" ]] && DEFAULT_HOST="0.0.0.0"

if [[ "${WEE_NON_INTERACTIVE:-0}" == "1" ]]; then
  AUTH_SECRET="${WEE_AUTH_SECRET:-$(openssl rand -base64 32)}"
  API_HOST="${WEE_API_HOST:-$DEFAULT_HOST}"
  API_PORT="${WEE_API_PORT:-8000}"
  TELEGRAM_TOKEN="${WEE_TELEGRAM_TOKEN:-}"

  # Override paths if provided
  if [[ -n "${WEE_COPILOT_BIN:-}" && -f "${WEE_COPILOT_BIN}" ]]; then
    RUNTIME_PATHS[copilot]="${WEE_COPILOT_BIN}"
    if [[ ! " ${DETECTED_RUNTIMES[*]:-} " =~ " copilot " ]]; then
      DETECTED_RUNTIMES+=("copilot")
      DETECTED_RUNTIME="${DETECTED_RUNTIME:-copilot}"
    fi
    info "Using copilot override: ${WEE_COPILOT_BIN}"
  fi
  if [[ -n "${WEE_GH_AUTH:-}" && -f "${WEE_GH_AUTH}" ]]; then
    GH_HOSTS_PATH="${WEE_GH_AUTH}"
    info "Using gh auth override: ${WEE_GH_AUTH}"
  fi
else
  # Interactive prompts
  DEFAULT_SECRET=$(openssl rand -base64 32)
  read -rp "Auth token secret [auto-generated]: " AUTH_SECRET
  AUTH_SECRET="${AUTH_SECRET:-$DEFAULT_SECRET}"

  read -rp "API host [${DEFAULT_HOST}]: " API_HOST
  API_HOST="${API_HOST:-$DEFAULT_HOST}"

  read -rp "API port [8000]: " API_PORT
  API_PORT="${API_PORT:-8000}"

  read -rp "Telegram bot token (Enter to skip): " TELEGRAM_TOKEN
fi

# ── Generate docker-compose.yml ───────────────────────────────────────────────
step "Generating docker-compose.yml..."

# Build volume mount lines as an array
EXTRA_VOLUMES=()
for rt in "${DETECTED_RUNTIMES[@]:-}"; do
  src="${RUNTIME_PATHS[$rt]:-}"
  [[ -z "$src" ]] && continue
  case "$rt" in
    copilot)  EXTRA_VOLUMES+=("      - ${src}:/usr/local/bin/copilot:ro") ;;
    claude)   EXTRA_VOLUMES+=("      - ${src}:/usr/local/bin/claude:ro") ;;
    gemini)   EXTRA_VOLUMES+=("      - ${src}:/usr/local/bin/gemini:ro") ;;
    opencode) EXTRA_VOLUMES+=("      - ${src}:/usr/local/bin/opencode:ro") ;;
  esac
done

if [[ -n "$GH_HOSTS_PATH" ]]; then
  EXTRA_VOLUMES+=("      - ${GH_HOSTS_PATH}:/root/.config/gh/hosts.yml:ro")
fi
if [[ -n "$CLAUDE_DIR" ]]; then
  EXTRA_VOLUMES+=("      - ${CLAUDE_DIR}:/root/.claude:ro")
fi
if [[ -n "$GH_PATH" ]]; then
  EXTRA_VOLUMES+=("      - ${GH_PATH}:/usr/local/bin/gh:ro")
fi
if [[ -n "$TELEGRAM_TOKEN" ]]; then
  EXTRA_VOLUMES+=("      - ./config/telegram_config.json:/app/telegram_config.json:ro")
fi

# Write compose file programmatically
{
  cat <<HEADER
services:
  wee:
    image: ghcr.io/leprachuan/wee-orchestrator:latest
    container_name: wee-orchestrator
    restart: unless-stopped
    ports:
      - "${API_PORT}:8000"
    volumes:
      - ./config/agents.json:/app/agents.json:ro
      - ./data:/app/data
HEADER
  for vol in "${EXTRA_VOLUMES[@]}"; do
    echo "$vol"
  done
  cat <<MIDDLE
    environment:
      - AUTH_TOKEN_SECRET=${AUTH_SECRET}
      - API_HOST=0.0.0.0
      - API_PORT=8000
      - COPILOT_DEFAULT_RUNTIME=${DETECTED_RUNTIME}
    healthcheck:
      test: ["CMD", "python3", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 15s
    networks:
      - wee-net
MIDDLE
  if [[ -n "$TELEGRAM_TOKEN" ]]; then
    cat <<TELEGRAM
  telegram:
    image: ghcr.io/leprachuan/wee-orchestrator:latest
    container_name: wee-telegram
    restart: unless-stopped
    command: python3 telegram_connector.py
    volumes:
      - ./telegram_connector.py:/app/telegram_connector.py:ro
      - ./audio_transcriber.py:/app/audio_transcriber.py:ro
      - ./config/telegram_config.json:/app/telegram_config.json:ro
      - ./telegram_downloads:/app/telegram_downloads
    environment:
      - TELEGRAM_BOT_TOKEN=${TELEGRAM_TOKEN}
      - WEE_API_URL=http://wee:8000
    depends_on:
      wee:
        condition: service_healthy
    networks:
      - wee-net
TELEGRAM
  fi
  cat <<FOOTER
networks:
  wee-net:
    driver: bridge
FOOTER
} > docker-compose.yml

info "docker-compose.yml generated"

# ── Create directories and config files ───────────────────────────────────────
step "Creating config directories..."
mkdir -p config data telegram_downloads
info "Created: config/ data/ telegram_downloads/"

# agents.json
if [[ ! -f config/agents.json ]]; then
  cat > config/agents.json <<'AGENTS'
{
  "agents": {
    "fosterbot": {
      "name": "fosterbot",
      "description": "Main orchestrator agent",
      "runtime": "copilot",
      "model": "claude-sonnet-4-5",
      "working_dir": "/app",
      "timeout": 900
    }
  },
  "default_agent": "fosterbot"
}
AGENTS
  info "config/agents.json created"
else
  info "config/agents.json already exists (keeping)"
fi

# telegram_config.json
if [[ -n "$TELEGRAM_TOKEN" ]]; then
  cat > config/telegram_config.json <<TGCFG
{
  "bot_token": "${TELEGRAM_TOKEN}",
  "api_url": "http://wee:8000",
  "auth_token": "${AUTH_SECRET}",
  "allowed_user_ids": [],
  "download_path": "/app/telegram_downloads"
}
TGCFG
  info "config/telegram_config.json created"
fi

# ── Check for telegram connector files ────────────────────────────────────────
if [[ -n "$TELEGRAM_TOKEN" ]]; then
  step "Checking Telegram connector files..."
  MISSING_FILES=()
  [[ ! -f telegram_connector.py ]] && MISSING_FILES+=("telegram_connector.py")
  [[ ! -f audio_transcriber.py ]] && MISSING_FILES+=("audio_transcriber.py")

  if [[ ${#MISSING_FILES[@]} -gt 0 ]]; then
    warn "Missing files needed for Telegram service: ${MISSING_FILES[*]}"
    echo "  Copy from the Wee Orchestrator repo or download from GitHub:"
    echo "  curl -fsSL https://raw.githubusercontent.com/leprachuan/Wee-Orchestrator/main/telegram_connector.py -o telegram_connector.py"
    echo "  curl -fsSL https://raw.githubusercontent.com/leprachuan/Wee-Orchestrator/main/audio_transcriber.py -o audio_transcriber.py"
    echo ""
    echo "  Telegram service will fail to start until these files are present."
  else
    info "Telegram connector files present"
  fi
fi

# ── Pull and start ────────────────────────────────────────────────────────────
step "Pulling Docker image and starting services..."
docker compose pull
docker compose up -d

echo ""
step "Waiting for health check..."
for i in 1 2 3 4 5; do
  if docker compose exec -T wee python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')" &>/dev/null; then
    info "Wee Orchestrator is healthy!"
    break
  fi
  if [[ $i -eq 5 ]]; then
    warn "Health check timed out — container may still be starting"
  else
    echo "  Waiting... (attempt $i/5)"
    sleep 10
  fi
done

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}${BOLD}╔══════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}${BOLD}║          Setup Complete! 🎉                  ║${NC}"
echo -e "${CYAN}${BOLD}╚══════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${BOLD}WebUI:${NC}     http://${API_HOST}:${API_PORT}/ui/"
echo -e "  ${BOLD}API:${NC}       http://${API_HOST}:${API_PORT}/api/v1/health"
echo -e "  ${BOLD}Runtime:${NC}   ${DETECTED_RUNTIME}"
if [[ -n "$TELEGRAM_TOKEN" ]]; then
echo -e "  ${BOLD}Telegram:${NC}  enabled ✓"
fi
echo ""
echo "  Manage:  docker compose logs -f"
echo "  Stop:    docker compose down"
echo ""
