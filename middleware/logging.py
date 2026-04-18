"""
Request logging middleware for the Gotenberg Gateway.

Provides:
  - Structured JSON or text logging for every request
  - UUID-based request ID tracing (generated or passed via X-Request-ID)
  - Client IP extraction from proxy headers
  - Duration, status code, and response size tracking
"""

import logging
import time
import uuid

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("gateway.access")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """
    Sets up request context: assigns request ID and extracts client IP.

    This MUST be the outermost middleware so all other middleware can
    access request.state.request_id and request.state.client_ip.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Assign or extract request ID
        request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
        request.state.request_id = request_id

        # Extract client IP (respecting proxy headers)
        request.state.client_ip = self._extract_client_ip(request)

        # Initialize optional state fields
        request.state.api_key = None

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @staticmethod
    def _extract_client_ip(request: Request) -> str:
        """Extract the real client IP, respecting proxy headers."""
        # X-Forwarded-For: client, proxy1, proxy2
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()

        # X-Real-IP
        xri = request.headers.get("x-real-ip")
        if xri:
            return xri.strip()

        # Fall back to connection remote address
        if request.client:
            return request.client.host

        return "unknown"


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Logs each completed request with timing and metadata."""

    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.monotonic()

        response = await call_next(request)

        duration_ms = (time.monotonic() - start) * 1000

        # Build log entry
        log_data = {
            "request_id": getattr(request.state, "request_id", ""),
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": round(duration_ms, 2),
            "client_ip": getattr(request.state, "client_ip", "unknown"),
            "user_agent": request.headers.get("user-agent", ""),
        }

        # Include API key if present (already masked by auth middleware)
        api_key = getattr(request.state, "api_key", None)
        if api_key:
            log_data["api_key"] = api_key

        # Log at appropriate level based on status code
        if response.status_code >= 500:
            logger.error("Request completed", extra=log_data)
        elif response.status_code >= 400:
            logger.warning("Request completed", extra=log_data)
        else:
            logger.info("Request completed", extra=log_data)

        return response
