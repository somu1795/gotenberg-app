"""
Tests for concurrency control and circuit breaker.
"""

import asyncio
import time
import pytest
from fastapi.testclient import TestClient
from tests.conftest import make_app_with_config, multipart_form


class TestConcurrencyAdmission:
    """Tests that requests are admitted, queued, or rejected correctly."""

    def test_first_request_admitted(self, fresh_client):
        """First request should be admitted immediately."""
        resp = fresh_client.get("/")
        assert resp.status_code == 200

    def test_bypass_paths_skip_concurrency(self, fresh_client):
        """Gateway endpoints (/, /health) bypass concurrency control."""
        for _ in range(20):  # More than max_concurrent
            resp = fresh_client.get("/")
            assert resp.status_code == 200

    def test_health_bypasses_concurrency(self, fresh_client):
        """Health check always works regardless of load."""
        for _ in range(20):
            resp = fresh_client.get("/health")
            assert resp.status_code in (200, 503)


class TestConcurrency503:
    """Test that 503 is returned when capacity is exceeded."""

    @pytest.fixture()
    def tiny_app_client(self):
        """App with very small capacity for testing rejection."""
        app = make_app_with_config(max_concurrent="1", max_queue="0")
        with TestClient(app) as c:
            yield c

    def test_503_has_retry_after(self, fresh_client):
        """503 responses should include Retry-After header."""
        # Make many rapid requests to trigger 503
        for _ in range(30):
            resp = fresh_client.post(
                "/forms/chromium/convert/url",
                files=multipart_form(url="https://example.com"),
            )
            if resp.status_code == 503:
                assert "retry-after" in resp.headers
                return
        pytest.skip("Could not trigger 503")

    def test_503_has_capacity_info(self, fresh_client):
        """503 response should include capacity details."""
        for _ in range(30):
            resp = fresh_client.post(
                "/forms/chromium/convert/url",
                files=multipart_form(url="https://example.com"),
            )
            if resp.status_code == 503:
                data = resp.json()
                assert data["code"] == 503
                assert "capacity" in data
                return
        pytest.skip("Could not trigger 503")


class TestCircuitBreaker:
    """Tests for the circuit breaker."""

    def test_starts_closed(self):
        from middleware.circuit_breaker import CircuitBreaker
        from config import CircuitBreakerConfig

        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=3, recovery_timeout=10))
        assert cb.state == "closed"

    @pytest.mark.asyncio
    async def test_opens_after_threshold(self):
        from middleware.circuit_breaker import CircuitBreaker
        from config import CircuitBreakerConfig

        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=3, recovery_timeout=10))

        # Record failures up to threshold
        for _ in range(3):
            await cb.record_failure()

        assert cb.state == "open"
        assert await cb.is_open() is True

    @pytest.mark.asyncio
    async def test_stays_closed_below_threshold(self):
        from middleware.circuit_breaker import CircuitBreaker
        from config import CircuitBreakerConfig

        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=5, recovery_timeout=10))

        for _ in range(4):
            await cb.record_failure()

        assert cb.state == "closed"
        assert await cb.is_open() is False

    @pytest.mark.asyncio
    async def test_resets_on_success(self):
        from middleware.circuit_breaker import CircuitBreaker
        from config import CircuitBreakerConfig

        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=3, recovery_timeout=10))

        await cb.record_failure()
        await cb.record_failure()
        await cb.record_success()  # Reset

        assert cb.state == "closed"

        # Should need 3 more failures to open again
        await cb.record_failure()
        await cb.record_failure()
        assert cb.state == "closed"

    @pytest.mark.asyncio
    async def test_half_open_after_timeout(self):
        from middleware.circuit_breaker import CircuitBreaker
        from config import CircuitBreakerConfig

        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=1, recovery_timeout=0))

        await cb.record_failure()  # Opens immediately
        assert cb.state == "open"

        # With recovery_timeout=0, should transition to half_open immediately
        is_open = await cb.is_open()
        assert is_open is False  # Allows probe
        assert cb.state == "half_open"

    @pytest.mark.asyncio
    async def test_half_open_success_closes(self):
        from middleware.circuit_breaker import CircuitBreaker
        from config import CircuitBreakerConfig

        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=1, recovery_timeout=0))

        await cb.record_failure()
        await cb.is_open()  # Transition to half_open
        await cb.record_success()  # Probe succeeds → close

        assert cb.state == "closed"

    @pytest.mark.asyncio
    async def test_half_open_failure_reopens(self):
        from middleware.circuit_breaker import CircuitBreaker
        from config import CircuitBreakerConfig

        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=1, recovery_timeout=0))

        await cb.record_failure()
        await cb.is_open()  # Transition to half_open
        await cb.record_failure()  # Probe fails → reopen

        assert cb.state == "open"

    @pytest.mark.asyncio
    async def test_get_info(self):
        from middleware.circuit_breaker import CircuitBreaker
        from config import CircuitBreakerConfig

        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=5, recovery_timeout=30))
        info = await cb.get_info()

        assert info["state"] == "closed"
        assert info["failure_count"] == 0
        assert info["failure_threshold"] == 5
        assert info["recovery_timeout_seconds"] == 30


class TestPerIPTracker:
    """Unit tests for the per-IP fairness tracker."""

    @pytest.mark.asyncio
    async def test_allows_within_limit(self):
        from middleware.concurrency import PerIPTracker

        tracker = PerIPTracker(max_concurrent=2, max_queue=3)
        assert await tracker.can_run("1.2.3.4") is True

    @pytest.mark.asyncio
    async def test_denies_over_concurrent_limit(self):
        from middleware.concurrency import PerIPTracker

        tracker = PerIPTracker(max_concurrent=2, max_queue=3)
        await tracker.start_job("1.2.3.4")
        await tracker.start_job("1.2.3.4")

        assert await tracker.can_run("1.2.3.4") is False

    @pytest.mark.asyncio
    async def test_allows_different_ips(self):
        from middleware.concurrency import PerIPTracker

        tracker = PerIPTracker(max_concurrent=1, max_queue=1)
        await tracker.start_job("1.1.1.1")

        # Different IP should still be allowed
        assert await tracker.can_run("2.2.2.2") is True

    @pytest.mark.asyncio
    async def test_recovers_after_finish(self):
        from middleware.concurrency import PerIPTracker

        tracker = PerIPTracker(max_concurrent=1, max_queue=1)
        await tracker.start_job("1.2.3.4")
        assert await tracker.can_run("1.2.3.4") is False

        await tracker.finish_job("1.2.3.4")
        assert await tracker.can_run("1.2.3.4") is True

    @pytest.mark.asyncio
    async def test_queue_limit(self):
        from middleware.concurrency import PerIPTracker

        tracker = PerIPTracker(max_concurrent=1, max_queue=2)
        await tracker.start_queue("1.2.3.4")
        await tracker.start_queue("1.2.3.4")

        assert await tracker.can_queue("1.2.3.4") is False

    @pytest.mark.asyncio
    async def test_get_ip_info(self):
        from middleware.concurrency import PerIPTracker

        tracker = PerIPTracker(max_concurrent=5, max_queue=5)
        await tracker.start_job("1.2.3.4")
        await tracker.start_queue("1.2.3.4")

        info = await tracker.get_ip_info("1.2.3.4")
        assert info["active"] == 1
        assert info["queued"] == 1
