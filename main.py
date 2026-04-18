"""
Gotenberg Gateway — Main Application

A robust reverse proxy for the Gotenberg API with concurrency control,
circuit breaker, SSRF protection, and structured logging.

Designed to never crash under load — gracefully queues excess requests
and rejects with 503 when at capacity.
"""

import json
import logging
import sys
import time
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from config import load_config
from middleware.circuit_breaker import CircuitBreaker
from middleware.concurrency import ConcurrencyMiddleware
from middleware.logging import AccessLogMiddleware, RequestContextMiddleware
from middleware.security import (
    IPFilterMiddleware,
    MaxBodySizeMiddleware,
    RouteWhitelistMiddleware,
    SecurityHeadersMiddleware,
)
from proxy import GotenbergProxy

__version__ = "2.0.0"

# ─── Load configuration ──────────────────────────────────────────────
cfg = load_config()

# ─── Configure logging ───────────────────────────────────────────────

class JSONFormatter(logging.Formatter):
    """JSON log formatter for structured logging."""

    def format(self, record):
        log_entry = {
            "time": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Merge extra fields
        for attr in (
            "request_id", "client_ip", "method", "path", "status",
            "duration_ms", "user_agent", "target_url", "reason",
            "error", "upstream_path", "upstream_status",
            "queue_position", "active_jobs", "queued_jobs",
            "ip_active", "ip_queued", "circuit_state",
            "failure_count", "elapsed_seconds", "waited_seconds",
        ):
            if hasattr(record, attr):
                log_entry[attr] = getattr(record, attr)
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = str(record.exc_info[1])
        return json.dumps(log_entry)


def setup_logging():
    """Configure application-wide logging."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, cfg.logging.level.upper(), logging.INFO))

    handler = logging.StreamHandler(sys.stdout)
    if cfg.logging.format == "json":
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )

    root.handlers = [handler]

    # Quiet down noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


setup_logging()
app_logger = logging.getLogger("gateway")

# ─── Circuit breaker ─────────────────────────────────────────────────
circuit_breaker = CircuitBreaker(cfg.circuit_breaker)

# ─── Proxy instance ──────────────────────────────────────────────────
proxy = GotenbergProxy(
    upstream_url=cfg.gotenberg.upstream_url,
    timeout=cfg.gotenberg.request_timeout,
    circuit_breaker=circuit_breaker,
)

# ─── Track startup time ──────────────────────────────────────────────
_start_time = time.monotonic()


# ─── App lifecycle ───────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage startup and shutdown of the proxy client."""
    await proxy.start()
    app_logger.info(
        "Gotenberg Gateway started",
        extra={
            "version": __version__,
            "port": cfg.server.port,
            "upstream": cfg.gotenberg.upstream_url,
            "max_concurrent": cfg.concurrency.max_concurrent,
            "max_queue": cfg.concurrency.max_queue,
            "per_ip_concurrent": cfg.concurrency.per_ip_concurrent,
            "per_ip_queue": cfg.concurrency.per_ip_queue,

        },
    )
    yield
    await proxy.stop()
    app_logger.info("Gotenberg Gateway stopped")


# ─── Create FastAPI app ──────────────────────────────────────────────
app = FastAPI(
    title="Gotenberg Gateway",
    description="Robust reverse proxy for the Gotenberg document conversion API.",
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url=None,
)

# ─── Concurrency middleware (for stats access in health check) ────────
concurrency_mw = ConcurrencyMiddleware(app=None, config=cfg.concurrency)

# ─── Middleware stack ─────────────────────────────────────────────────
# NOTE: Starlette middleware is applied in REVERSE order.
# The LAST added middleware runs FIRST on the request.

# 5. Security headers (innermost)
app.add_middleware(SecurityHeadersMiddleware)

# 4. Route whitelisting
app.add_middleware(RouteWhitelistMiddleware, allowed_routes=cfg.security.allowed_routes)

# 3. Max body size
app.add_middleware(MaxBodySizeMiddleware, max_size=cfg.security.max_upload_size)

# 2. Concurrency control (replaces rate limiting)
app.add_middleware(ConcurrencyMiddleware, config=cfg.concurrency)

# 1. IP filtering
app.add_middleware(IPFilterMiddleware, allowlist=cfg.security.ip_allowlist, blocklist=cfg.security.ip_blocklist)

# B. Access logging
app.add_middleware(AccessLogMiddleware)

# A. Request context (outermost)
app.add_middleware(RequestContextMiddleware)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=cfg.security.cors_origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["X-Request-ID", "Content-Type"],
    max_age=86400,
)


# ─── Helper to get concurrency stats ─────────────────────────────────
def _get_concurrency_middleware():
    """Walk the middleware stack to find the ConcurrencyMiddleware instance."""
    current = app.middleware_stack
    while current is not None:
        if isinstance(current, ConcurrencyMiddleware):
            return current
        current = getattr(current, "app", None)
    return None


# ─── Gateway endpoints ───────────────────────────────────────────────

@app.get("/")
async def gateway_info(request: Request):
    """Gateway information and status."""
    mw = _get_concurrency_middleware()
    stats = await mw.stats.snapshot() if mw else {}

    client_ip = getattr(request.state, "client_ip", "unknown")
    ip_info = {"active": 0, "queued": 0}
    if mw and hasattr(mw, "_ip_tracker") and client_ip != "unknown":
        ip_info = await mw._ip_tracker.get_ip_info(client_ip)

    return {
        "service": "Gotenberg Gateway",
        "version": __version__,
        "status": "running",
        "docs": "/docs",
        "health": "/health",
        "client": {
            "ip": client_ip,
            "active_jobs": ip_info["active"],
            "queued_jobs": ip_info["queued"],
        },
        "capacity": {
            "max_concurrent": cfg.concurrency.max_concurrent,
            "max_queue": cfg.concurrency.max_queue,
            "per_ip_concurrent": cfg.concurrency.per_ip_concurrent,
            "per_ip_queue": cfg.concurrency.per_ip_queue,
            "active_jobs": stats.get("active_jobs", 0),
            "queued_jobs": stats.get("queued_jobs", 0),
        },
        "features": {
            "circuit_breaker": "enabled",
            "max_upload_size_mb": cfg.security.max_upload_size / (1024 * 1024),
        },
    }


@app.get("/health")
async def health_check():
    """
    Comprehensive health check.

    Reports gateway status, capacity, circuit breaker state,
    and upstream Gotenberg health.
    """
    upstream_health = await proxy.health_check()
    cb_info = await circuit_breaker.get_info()

    mw = _get_concurrency_middleware()
    stats = await mw.stats.snapshot() if mw else {}

    gateway_healthy = upstream_health.get("status") == "healthy"
    cb_ok = cb_info["state"] != "open"

    uptime = time.monotonic() - _start_time

    return JSONResponse(
        status_code=200 if (gateway_healthy and cb_ok) else 503,
        content={
            "status": "healthy" if (gateway_healthy and cb_ok) else "degraded",
            "gateway": {
                "uptime_seconds": round(uptime, 1),
                "active_jobs": stats.get("active_jobs", 0),
                "queued_jobs": stats.get("queued_jobs", 0),
                "total_processed": stats.get("total_processed", 0),
                "total_rejected": stats.get("total_rejected", 0),
                "total_queue_timeouts": stats.get("total_queue_timeouts", 0),
            },
            "circuit_breaker": cb_info,
            "gotenberg": upstream_health,
        },
    )


# ─── Catch-all proxy route ───────────────────────────────────────────

@app.api_route("/{path:path}", methods=["POST"], include_in_schema=False)
async def proxy_to_gotenberg(request: Request, path: str):
    """
    Forward all non-gateway requests to the upstream Gotenberg service.

    The middleware stack applies concurrency control, SSRF protection,
    and route whitelisting before the request reaches this handler.
    """
    return await proxy.forward(request)


# ─── Entry point ──────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=cfg.server.host,
        port=cfg.server.port,
        log_level="warning",
        access_log=False,
    )
