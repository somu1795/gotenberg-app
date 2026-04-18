"""
Security middleware for the Gotenberg Gateway.

Provides:
  - Route whitelisting: only allows configured Gotenberg API routes
  - IP filtering: allowlist and blocklist with CIDR support
  - Security headers: standard hardening headers on all responses
  - Request size enforcement

Note: SSRF protection is handled at the Gotenberg container level via
      --chromium-deny-list and Docker network isolation. The gateway
      does NOT inspect or decode request bodies.
"""

import ipaddress
import logging
from typing import List

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger("gateway.security")


class RouteWhitelistMiddleware(BaseHTTPMiddleware):
    """Only allows requests to pre-approved Gotenberg routes."""

    def __init__(self, app, allowed_routes: List[str]):
        super().__init__(app)
        self.allowed_routes = allowed_routes

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path

        # Always allow gateway-level endpoints
        if path in {"/", "/health", "/docs", "/openapi.json"}:
            return await call_next(request)

        # Check if path matches any allowed route prefix
        if not any(path.startswith(route) for route in self.allowed_routes):
            logger.warning(
                "Blocked request to non-whitelisted route",
                extra={
                    "path": path,
                    "client_ip": getattr(request.state, "client_ip", "unknown"),
                    "request_id": getattr(request.state, "request_id", ""),
                },
            )
            return JSONResponse(
                status_code=403,
                content={
                    "error": "Forbidden",
                    "message": f"Route '{path}' is not allowed.",
                    "code": 403,
                },
            )

        return await call_next(request)





class IPFilterMiddleware(BaseHTTPMiddleware):
    """Filters requests based on IP allowlist and blocklist (CIDR supported)."""

    def __init__(self, app, allowlist: List[str], blocklist: List[str]):
        super().__init__(app)
        self.allowlist = self._parse_networks(allowlist)
        self.blocklist = self._parse_networks(blocklist)
        self.has_allowlist = len(self.allowlist) > 0
        self.has_blocklist = len(self.blocklist) > 0

    async def dispatch(self, request: Request, call_next) -> Response:
        if not self.has_allowlist and not self.has_blocklist:
            return await call_next(request)

        client_ip_str = getattr(request.state, "client_ip", None)
        if not client_ip_str:
            return JSONResponse(
                status_code=403,
                content={"error": "Forbidden", "message": "Access denied.", "code": 403},
            )

        try:
            client_ip = ipaddress.ip_address(client_ip_str)
        except ValueError:
            logger.warning(f"Could not parse client IP: {client_ip_str}")
            return JSONResponse(
                status_code=403,
                content={"error": "Forbidden", "message": "Access denied.", "code": 403},
            )

        # Check blocklist first
        if self.has_blocklist:
            for network in self.blocklist:
                if client_ip in network:
                    logger.warning(
                        "Blocked IP",
                        extra={"client_ip": client_ip_str, "request_id": getattr(request.state, "request_id", "")},
                    )
                    return JSONResponse(
                        status_code=403,
                        content={"error": "Forbidden", "message": "Access denied.", "code": 403},
                    )

        # Check allowlist
        if self.has_allowlist:
            allowed = any(client_ip in network for network in self.allowlist)
            if not allowed:
                logger.warning(
                    "IP not in allowlist",
                    extra={"client_ip": client_ip_str, "request_id": getattr(request.state, "request_id", "")},
                )
                return JSONResponse(
                    status_code=403,
                    content={"error": "Forbidden", "message": "Access denied.", "code": 403},
                )

        return await call_next(request)

    @staticmethod
    def _parse_networks(entries: List[str]) -> List[ipaddress.IPv4Network | ipaddress.IPv6Network]:
        """Parse a list of IPs or CIDRs into network objects."""
        networks = []
        for entry in entries:
            try:
                networks.append(ipaddress.ip_network(entry, strict=False))
            except ValueError:
                logger.warning(f"Invalid IP/CIDR in config: {entry}")
        return networks


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds security headers to all responses."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Server"] = "gotenberg-gateway"
        return response


class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    """Rejects requests with bodies exceeding the configured maximum size."""

    def __init__(self, app, max_size: int):
        super().__init__(app)
        self.max_size = max_size

    async def dispatch(self, request: Request, call_next) -> Response:
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > self.max_size:
            max_mb = self.max_size / (1024 * 1024)
            return JSONResponse(
                status_code=413,
                content={
                    "error": "Payload Too Large",
                    "message": f"Request body exceeds maximum allowed size of {max_mb:.0f} MB.",
                    "code": 413,
                },
            )
        return await call_next(request)
