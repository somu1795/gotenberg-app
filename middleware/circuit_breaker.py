"""
Circuit breaker for the upstream Gotenberg service.

Prevents cascading failures by stopping requests to Gotenberg when it's
consistently failing. After a recovery timeout, allows a single probe
request through to check if Gotenberg has recovered.

States:
  - CLOSED: Normal operation, requests flow through
  - OPEN: Gotenberg is failing, all requests get 503 immediately
  - HALF_OPEN: Allowing one probe request to check recovery
"""

import asyncio
import logging
import time
from enum import Enum

from config import CircuitBreakerConfig

logger = logging.getLogger("gateway.circuit_breaker")


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Circuit breaker for the Gotenberg upstream."""

    def __init__(self, config: CircuitBreakerConfig):
        self.failure_threshold = config.failure_threshold
        self.recovery_timeout = config.recovery_timeout

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> str:
        """Current circuit state as a string."""
        return self._state.value

    async def is_open(self) -> bool:
        """Check if the circuit is open (should reject requests)."""
        async with self._lock:
            if self._state == CircuitState.CLOSED:
                return False

            if self._state == CircuitState.OPEN:
                # Check if recovery timeout has elapsed
                elapsed = time.monotonic() - self._last_failure_time
                if elapsed >= self.recovery_timeout:
                    self._state = CircuitState.HALF_OPEN
                    logger.info(
                        "Circuit breaker → HALF_OPEN (allowing probe request)",
                        extra={"elapsed_seconds": round(elapsed, 1)},
                    )
                    return False  # Allow one probe request
                return True  # Still open

            # HALF_OPEN: allow the probe request through
            return False

    async def record_success(self):
        """Record a successful request. Resets the circuit if half-open."""
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                logger.info("Circuit breaker → CLOSED (probe succeeded)")
            self._state = CircuitState.CLOSED
            self._failure_count = 0

    async def record_failure(self):
        """Record a failed request. Opens the circuit if threshold is reached."""
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()

            if self._state == CircuitState.HALF_OPEN:
                # Probe failed — reopen
                self._state = CircuitState.OPEN
                logger.warning(
                    "Circuit breaker → OPEN (probe failed)",
                    extra={"failure_count": self._failure_count},
                )
            elif self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN
                logger.warning(
                    "Circuit breaker → OPEN (threshold reached)",
                    extra={
                        "failure_count": self._failure_count,
                        "threshold": self.failure_threshold,
                        "recovery_timeout": self.recovery_timeout,
                    },
                )

    async def get_info(self) -> dict:
        """Get circuit breaker status for health checks."""
        async with self._lock:
            return {
                "state": self._state.value,
                "failure_count": self._failure_count,
                "failure_threshold": self.failure_threshold,
                "recovery_timeout_seconds": self.recovery_timeout,
            }
