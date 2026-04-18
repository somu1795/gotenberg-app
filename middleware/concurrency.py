"""
Concurrency control middleware for the Gotenberg Gateway.

Implements a three-tier admission control system:

  1. **Slot available** → Run immediately
  2. **Queue has room** → Wait in queue (with timeout)
  3. **Queue full** → 503 Service Unavailable (instant rejection)

Per-IP fairness ensures one user can't monopolize all slots or fill the queue.
"""

import asyncio
import logging
import time
from collections import defaultdict
from typing import Dict

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from config import ConcurrencyConfig

logger = logging.getLogger("gateway.concurrency")

# Paths that bypass concurrency control (gateway-internal endpoints)
BYPASS_PATHS = {"/", "/health", "/docs", "/openapi.json"}


class ConcurrencyStats:
    """Thread-safe counters for monitoring."""

    def __init__(self):
        self.active_jobs: int = 0
        self.queued_jobs: int = 0
        self.total_processed: int = 0
        self.total_queued: int = 0
        self.total_rejected: int = 0
        self.total_queue_timeouts: int = 0
        self._lock = asyncio.Lock()

    async def snapshot(self) -> dict:
        async with self._lock:
            return {
                "active_jobs": self.active_jobs,
                "queued_jobs": self.queued_jobs,
                "total_processed": self.total_processed,
                "total_queued": self.total_queued,
                "total_rejected": self.total_rejected,
                "total_queue_timeouts": self.total_queue_timeouts,
            }


class PerIPTracker:
    """Tracks per-IP concurrent and queued request counts."""

    def __init__(self, max_concurrent: int, max_queue: int):
        self.max_concurrent = max_concurrent
        self.max_queue = max_queue
        self._active: Dict[str, int] = defaultdict(int)
        self._queued: Dict[str, int] = defaultdict(int)
        self._lock = asyncio.Lock()

    async def can_run(self, ip: str) -> bool:
        """Check if this IP can start a new job."""
        async with self._lock:
            return self._active[ip] < self.max_concurrent

    async def can_queue(self, ip: str) -> bool:
        """Check if this IP can queue another request."""
        async with self._lock:
            return self._queued[ip] < self.max_queue

    async def start_job(self, ip: str):
        async with self._lock:
            self._active[ip] += 1

    async def finish_job(self, ip: str):
        async with self._lock:
            self._active[ip] = max(0, self._active[ip] - 1)
            if self._active[ip] == 0:
                del self._active[ip]

    async def start_queue(self, ip: str):
        async with self._lock:
            self._queued[ip] += 1

    async def finish_queue(self, ip: str):
        async with self._lock:
            self._queued[ip] = max(0, self._queued[ip] - 1)
            if self._queued[ip] == 0:
                del self._queued[ip]

    async def get_ip_info(self, ip: str) -> dict:
        async with self._lock:
            return {
                "active": self._active.get(ip, 0),
                "queued": self._queued.get(ip, 0),
            }


class ConcurrencyMiddleware(BaseHTTPMiddleware):
    """
    Admission control middleware with bounded concurrency and a wait queue.

    - Uses an asyncio.Semaphore for slot management
    - Excess requests wait in a bounded queue with timeout
    - Per-IP limits prevent fairness abuse
    - Reports rich status in 503 responses
    """

    def __init__(self, app, config: ConcurrencyConfig):
        super().__init__(app)
        self.config = config

        # Global concurrency semaphore
        self._semaphore = asyncio.Semaphore(config.max_concurrent)

        # Queue tracking
        self._queue_count = 0
        self._queue_lock = asyncio.Lock()

        # Per-IP tracker
        self._ip_tracker = PerIPTracker(
            max_concurrent=config.per_ip_concurrent,
            max_queue=config.per_ip_queue,
        )

        # Stats
        self.stats = ConcurrencyStats()

    async def dispatch(self, request: Request, call_next) -> Response:
        # Bypass for gateway-internal endpoints
        if request.url.path in BYPASS_PATHS:
            return await call_next(request)

        client_ip = getattr(request.state, "client_ip", "unknown")
        request_id = getattr(request.state, "request_id", "")

        # --- Per-IP check: can this IP run or queue? ---
        can_run_ip = await self._ip_tracker.can_run(client_ip)
        can_queue_ip = await self._ip_tracker.can_queue(client_ip)

        if not can_run_ip and not can_queue_ip:
            ip_info = await self._ip_tracker.get_ip_info(client_ip)
            logger.warning(
                "Per-IP limit reached",
                extra={
                    "request_id": request_id,
                    "client_ip": client_ip,
                    "ip_active": ip_info["active"],
                    "ip_queued": ip_info["queued"],
                },
            )
            return self._busy_response(
                reason=f"Per-IP limit reached ({self.config.per_ip_concurrent} concurrent, {self.config.per_ip_queue} queued)",
                retry_after=5,
            )

        # --- Try to acquire a slot immediately ---
        if self._semaphore._value > 0:  # Slot available
            return await self._run_with_slot(request, call_next, client_ip, request_id)

        # --- No slot available, try to queue ---
        async with self._queue_lock:
            if self._queue_count >= self.config.max_queue:
                # Queue is full — reject immediately
                self.stats.total_rejected += 1
                stats = await self.stats.snapshot()
                logger.warning(
                    "Queue full, rejecting request",
                    extra={
                        "request_id": request_id,
                        "client_ip": client_ip,
                        "active_jobs": stats["active_jobs"],
                        "queued_jobs": stats["queued_jobs"],
                    },
                )
                return self._busy_response(
                    reason="All conversion slots and queue positions are occupied",
                    retry_after=10,
                )
            self._queue_count += 1
            self.stats.queued_jobs += 1
            self.stats.total_queued += 1

        await self._ip_tracker.start_queue(client_ip)

        logger.info(
            "Request queued",
            extra={
                "request_id": request_id,
                "client_ip": client_ip,
                "queue_position": self._queue_count,
            },
        )

        try:
            # Wait for a slot with timeout
            try:
                await asyncio.wait_for(
                    self._semaphore.acquire(),
                    timeout=self.config.queue_timeout,
                )
            except asyncio.TimeoutError:
                self.stats.total_queue_timeouts += 1
                logger.warning(
                    "Queue timeout",
                    extra={
                        "request_id": request_id,
                        "client_ip": client_ip,
                        "waited_seconds": self.config.queue_timeout,
                    },
                )
                return JSONResponse(
                    status_code=408,
                    content={
                        "error": "Request Timeout",
                        "message": f"Your request waited {self.config.queue_timeout}s in the queue but no slot became available. Please try again.",
                        "code": 408,
                    },
                    headers={"Retry-After": "10"},
                )
        finally:
            await self._ip_tracker.finish_queue(client_ip)
            async with self._queue_lock:
                self._queue_count = max(0, self._queue_count - 1)
                self.stats.queued_jobs = max(0, self.stats.queued_jobs - 1)

        # Got a slot from the queue — run the job
        return await self._execute_with_slot(request, call_next, client_ip, request_id)

    async def _run_with_slot(self, request, call_next, client_ip, request_id) -> Response:
        """Acquire a slot immediately and run."""
        await self._semaphore.acquire()
        return await self._execute_with_slot(request, call_next, client_ip, request_id)

    async def _execute_with_slot(self, request, call_next, client_ip, request_id) -> Response:
        """Execute the request while holding a concurrency slot."""
        await self._ip_tracker.start_job(client_ip)
        self.stats.active_jobs += 1

        try:
            response = await call_next(request)
            self.stats.total_processed += 1
            return response
        finally:
            self.stats.active_jobs = max(0, self.stats.active_jobs - 1)
            self._semaphore.release()
            await self._ip_tracker.finish_job(client_ip)

    def _busy_response(self, reason: str, retry_after: int) -> JSONResponse:
        """Return a 503 Service Unavailable with queue info."""
        return JSONResponse(
            status_code=503,
            content={
                "error": "Service Busy",
                "message": f"{reason}. Please retry shortly.",
                "retry_after_seconds": retry_after,
                "capacity": {
                    "max_concurrent": self.config.max_concurrent,
                    "max_queue": self.config.max_queue,
                },
                "code": 503,
            },
            headers={"Retry-After": str(retry_after)},
        )
