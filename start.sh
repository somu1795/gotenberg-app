#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
# start.sh — Start the Gotenberg Gateway
#
# Two deployment modes:
#
#   ./start.sh              Production: both gateway + Gotenberg in containers
#                           Uses: docker-compose.yml + Dockerfile
#
#   ./start.sh --dev        Development: Gotenberg in Docker, gateway locally
#                           with uvicorn --reload for instant code reloading
#
# The Dockerfile is ONLY used by docker-compose.yml (production mode).
# ─────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [ -f ".env" ]; then
    set -a
    source .env
    set +a
fi

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}[start]${NC} $*"; }
warn() { echo -e "${YELLOW}[start]${NC} $*"; }
info() { echo -e "${CYAN}[start]${NC} $*"; }
err()  { echo -e "${RED}[start]${NC} $*"; }

# ── Helper: remove standalone containers that conflict with compose ──
cleanup_conflicting_containers() {
    for name in gotenberg gotenberg-gateway; do
        if docker ps -a --format '{{.Names}}' | grep -qx "$name"; then
            # Check if the container was created by compose (has com.docker.compose.project label)
            local is_compose
            is_compose=$(docker inspect --format='{{index .Config.Labels "com.docker.compose.project"}}' "$name" 2>/dev/null || echo "")
            if [ -z "$is_compose" ]; then
                warn "Removing standalone container '$name' (not from compose)..."
                docker rm -f "$name" >/dev/null 2>&1 || true
            fi
        fi
    done
}

# ── Helper: wait for a URL to return 200 ──
wait_for_health() {
    local url="$1"
    local label="$2"
    local max_wait="${3:-30}"

    for i in $(seq 1 "$max_wait"); do
        if curl -sf "$url" > /dev/null 2>&1; then
            log "✅ $label is healthy!"
            return 0
        fi
        sleep 1
    done
    err "❌ $label did not become healthy in ${max_wait}s"
    return 1
}

# ── Docker Compose mode (default) ────────────────────────────────
start_compose() {
    log "Starting Gotenberg Gateway via Docker Compose..."

    # Clean up any standalone containers that would block compose
    cleanup_conflicting_containers

    docker compose up -d --build

    echo ""
    info "Gateway:    http://localhost:${GATEWAY_PORT:-9225}"
    info "Health:     http://localhost:${GATEWAY_PORT:-9225}/health"
    info "API Docs:   http://localhost:${GATEWAY_PORT:-9225}/docs"
    echo ""

    log "Waiting for services to be healthy..."
    if wait_for_health "http://localhost:${GATEWAY_PORT:-9225}/health" "Gateway" 30; then
        echo ""
        log "🚀 Gateway is ready to accept requests!"
    else
        warn "Check logs: docker compose logs -f"
    fi
}

# ── Dev mode (auto-reload) ───────────────────────────────────────
start_dev() {
    log "Starting in development mode (auto-reload)..."

    # Ensure Gotenberg is running, start if not
    if ! curl -sf http://localhost:${GOTENBERG_PORT:-9125}/health > /dev/null 2>&1; then
        warn "Gotenberg not running. Starting container..."
        docker rm -f gotenberg 2>/dev/null || true
        docker run -d \
            --name gotenberg \
            --restart unless-stopped \
            -p 127.0.0.1:${GOTENBERG_PORT:-9125}:${GOTENBERG_PORT:-9125} \
            --dns=8.8.8.8 \
            --dns=1.1.1.1 \
            --read-only \
            --tmpfs /tmp:size=2G \
            --tmpfs /home/gotenberg:size=512M \
            --log-opt max-size=10m \
            --log-opt max-file=3 \
            --shm-size=1gb \
            --memory=6g \
            --cpus="3.2" \
            gotenberg/gotenberg:8 \
            gotenberg \
            --api-port=${GOTENBERG_PORT:-9125}

        wait_for_health "http://localhost:${GOTENBERG_PORT:-9125}/health" "Gotenberg" 30
    else
        log "Gotenberg already running."
    fi

    # Activate venv if it exists
    if [ -f "venv/bin/activate" ]; then
        source venv/bin/activate
    fi

    echo ""
    info "Gateway:    http://localhost:${GATEWAY_PORT:-9225}"
    info "Health:     http://localhost:${GATEWAY_PORT:-9225}/health"
    echo ""

    uvicorn main:app \
        --host 0.0.0.0 \
        --port "${GATEWAY_PORT:-9225}" \
        --reload \
        --log-level warning
}

# ── Main ─────────────────────────────────────────────────────────
case "${1:-}" in
    --dev)
        start_dev
        ;;
    --help|-h)
        echo "Usage: ./start.sh [--dev|--help]"
        echo ""
        echo "Deployment modes:"
        echo "  (default)   Production: both gateway + Gotenberg in containers"
        echo "              Uses: docker-compose.yml + Dockerfile"
        echo "  --dev       Development: Gotenberg in Docker, gateway with auto-reload"
        echo "              Uses: docker run + uvicorn --reload"
        echo ""
        echo "Environment variables:"
        echo "  GATEWAY_PORT           Port to listen on (default: 9225)"
        echo "  GOTENBERG_PORT         Gotenberg upstream port (default: 9125)"
        echo "  GATEWAY_MAX_CONCURRENT Max simultaneous Gotenberg jobs (default: 10)"
        echo "  GATEWAY_MAX_QUEUE      Max queued requests (default: 100)"
        echo "  GATEWAY_LOG_LEVEL      Log level: DEBUG/INFO/WARNING/ERROR (default: INFO)"
        ;;
    *)
        start_compose
        ;;
esac
