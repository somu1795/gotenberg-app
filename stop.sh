#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
# stop.sh — Stop the Gotenberg Gateway
#
# Usage:
#   ./stop.sh              # Stop Docker Compose services
#   ./stop.sh --dev        # Stop dev-mode gateway + Gotenberg container
#   ./stop.sh --all        # Stop everything
# ─────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[stop]${NC} $*"; }
warn() { echo -e "${YELLOW}[stop]${NC} $*"; }

stop_compose() {
    log "Stopping Docker Compose services..."
    if docker compose ps --quiet 2>/dev/null | grep -q .; then
        docker compose down
        log "✅ Compose services stopped"
    else
        warn "No compose services running"
    fi
}

stop_dev() {
    log "Stopping dev gateway process..."
    pkill -f "uvicorn main:app" 2>/dev/null && log "✅ Gateway process stopped" || warn "No gateway process found"

    log "Stopping Gotenberg container..."
    if docker ps --format '{{.Names}}' | grep -qx gotenberg; then
        # Only remove if it's a standalone container (not from compose)
        local is_compose
        is_compose=$(docker inspect --format='{{index .Config.Labels "com.docker.compose.project"}}' gotenberg 2>/dev/null || echo "")
        if [ -z "$is_compose" ]; then
            docker rm -f gotenberg >/dev/null 2>&1
            log "✅ Gotenberg container stopped"
        else
            warn "Gotenberg container belongs to compose — use './stop.sh' instead"
        fi
    else
        warn "No gotenberg container found"
    fi
}

case "${1:-}" in
    --dev)
        stop_dev
        ;;
    --all)
        stop_compose
        stop_dev
        ;;
    --help|-h)
        echo "Usage: ./stop.sh [--dev|--compose|--help]"
        echo ""
        echo "  (default)   Stop everything (compose services + dev containers)"
        echo "  --compose   Stop Docker Compose services only"
        echo "  --dev       Stop dev-mode gateway + Gotenberg container only"
        ;;
    --compose)
        stop_compose
        ;;
    *)
        stop_compose
        stop_dev
        ;;
esac
