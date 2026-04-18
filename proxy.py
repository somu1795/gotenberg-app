"""
Reverse proxy to the upstream Gotenberg container.

Streams requests from the client to Gotenberg and streams responses back.
Preserves multipart/form-data bodies exactly as-is.
Integrates with the circuit breaker to track upstream failures/successes.
"""

import logging
from typing import Optional

import httpx
from fastapi import Request
from starlette.responses import Response

from middleware.circuit_breaker import CircuitBreaker

logger = logging.getLogger("gateway.proxy")


class GotenbergProxy:
    """Async reverse proxy for the Gotenberg API."""

    def __init__(self, upstream_url: str, timeout: int = 120, circuit_breaker: CircuitBreaker = None):
        """
        Args:
            upstream_url: Base URL of the Gotenberg container (e.g. http://localhost:1795)
            timeout: Request timeout in seconds.
            circuit_breaker: Circuit breaker instance for upstream failure tracking.
        """
        self.upstream_url = upstream_url.rstrip("/")
        self.timeout = httpx.Timeout(timeout, connect=10.0)
        self.circuit_breaker = circuit_breaker
        self._client: Optional[httpx.AsyncClient] = None

    async def start(self):
        """Initialize the HTTP client."""
        self._client = httpx.AsyncClient(
            base_url=self.upstream_url,
            timeout=self.timeout,
            follow_redirects=False,
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=20,
                keepalive_expiry=30,
            ),
        )
        logger.info(f"Proxy initialized, upstream: {self.upstream_url}")

    async def stop(self):
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            logger.info("Proxy client closed")

    async def forward(self, request: Request) -> Response:
        """
        Forward a request to Gotenberg and stream the response back.

        Integrates with the circuit breaker:
          - If circuit is open → 503 immediately (don't hit Gotenberg)
          - On success → record_success()
          - On failure (5xx, timeout, connection error) → record_failure()
        """
        request_id = getattr(request.state, "request_id", "")

        # Circuit breaker check
        if self.circuit_breaker and await self.circuit_breaker.is_open():
            cb_info = await self.circuit_breaker.get_info()
            logger.warning(
                "Circuit breaker OPEN — rejecting request",
                extra={"request_id": request_id, "circuit_state": cb_info["state"]},
            )
            return Response(
                content=(
                    '{"error":"Service Unavailable",'
                    '"message":"Gotenberg is temporarily unavailable. The system is recovering automatically.",'
                    '"circuit_breaker":"open",'
                    '"code":503}'
                ),
                status_code=503,
                media_type="application/json",
                headers={"Retry-After": str(self.circuit_breaker.recovery_timeout)},
            )

        # Build upstream URL
        path = request.url.path
        query = str(request.url.query) if request.url.query else ""
        upstream_path = f"{path}?{query}" if query else path

        # Sanitize headers
        headers = self._sanitize_request_headers(request.headers)
        if request_id:
            headers["Gotenberg-Trace"] = request_id

        try:
            body = await request.body()

            upstream_response = await self._client.request(
                method=request.method,
                url=upstream_path,
                headers=headers,
                content=body,
            )

            response_headers = self._sanitize_response_headers(upstream_response.headers)

            # Track success/failure for circuit breaker
            if self.circuit_breaker:
                if upstream_response.status_code >= 500:
                    await self.circuit_breaker.record_failure()
                else:
                    await self.circuit_breaker.record_success()

            logger.debug(
                "Proxied request",
                extra={
                    "request_id": request_id,
                    "upstream_path": upstream_path,
                    "upstream_status": upstream_response.status_code,
                },
            )

            return Response(
                content=upstream_response.content,
                status_code=upstream_response.status_code,
                headers=response_headers,
            )

        except httpx.TimeoutException:
            if self.circuit_breaker:
                await self.circuit_breaker.record_failure()
            logger.error(
                "Upstream timeout",
                extra={"request_id": request_id, "path": path},
            )
            return Response(
                content='{"error":"Gateway Timeout","message":"Gotenberg did not respond in time.","code":504}',
                status_code=504,
                media_type="application/json",
            )
        except httpx.ConnectError:
            if self.circuit_breaker:
                await self.circuit_breaker.record_failure()
            logger.error(
                "Cannot connect to upstream",
                extra={"request_id": request_id, "upstream_url": self.upstream_url},
            )
            return Response(
                content='{"error":"Bad Gateway","message":"Cannot connect to Gotenberg service.","code":502}',
                status_code=502,
                media_type="application/json",
            )
        except Exception as e:
            if self.circuit_breaker:
                await self.circuit_breaker.record_failure()
            logger.error(
                "Proxy error",
                extra={"request_id": request_id, "error": str(e)},
            )
            return Response(
                content='{"error":"Internal Server Error","message":"An unexpected error occurred.","code":500}',
                status_code=500,
                media_type="application/json",
            )

    async def health_check(self) -> dict:
        """Check the health of the upstream Gotenberg service."""
        try:
            response = await self._client.get("/health")
            return {
                "status": "healthy" if response.status_code == 200 else "unhealthy",
                "status_code": response.status_code,
            }
        except Exception as e:
            return {
                "status": "unreachable",
                "error": str(e),
            }

    @staticmethod
    def _sanitize_request_headers(headers) -> dict:
        """Remove hop-by-hop and sensitive headers before forwarding upstream."""
        skip_headers = {
            "host", "connection", "keep-alive", "transfer-encoding",
            "te", "trailer", "upgrade", "proxy-authorization", "proxy-connection",
            "authorization", "x-api-key",
        }
        return {k: v for k, v in headers.items() if k.lower() not in skip_headers}

    @staticmethod
    def _sanitize_response_headers(headers) -> dict:
        """Remove hop-by-hop headers from the upstream response."""
        skip_headers = {
            "connection", "keep-alive", "transfer-encoding",
            "te", "trailer", "upgrade",
        }
        return {k: v for k, v in headers.items() if k.lower() not in skip_headers}
