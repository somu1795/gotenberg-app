"""
Tests for the gateway endpoints and middleware.
"""

import pytest
from fastapi.testclient import TestClient
from tests.conftest import multipart_form


class TestGatewayInfo:
    """Tests for GET / — gateway info endpoint."""

    def test_returns_200(self, fresh_client):
        resp = fresh_client.get("/")
        assert resp.status_code == 200

    def test_returns_service_name(self, fresh_client):
        data = fresh_client.get("/").json()
        assert data["service"] == "Gotenberg Gateway"

    def test_returns_version(self, fresh_client):
        data = fresh_client.get("/").json()
        assert data["version"] == "2.0.0"

    def test_returns_capacity_info(self, fresh_client):
        data = fresh_client.get("/").json()
        cap = data["capacity"]
        assert "max_concurrent" in cap
        assert "max_queue" in cap
        assert "per_ip_concurrent" in cap
        assert "per_ip_queue" in cap
        assert "active_jobs" in cap
        assert "queued_jobs" in cap

    def test_returns_features(self, fresh_client):
        data = fresh_client.get("/").json()
        features = data["features"]
        assert "circuit_breaker" in features
        assert "max_upload_size_mb" in features

    def test_returns_client_info(self, fresh_client):
        data = fresh_client.get("/").json()
        assert "client" in data
        assert "ip" in data["client"]
        assert "active_jobs" in data["client"]
        assert "queued_jobs" in data["client"]


class TestHealthCheck:
    """Tests for GET /health."""

    def test_returns_gateway_info(self, fresh_client):
        resp = fresh_client.get("/health")
        data = resp.json()
        assert "status" in data
        assert "gateway" in data
        assert "gotenberg" in data
        assert "circuit_breaker" in data

    def test_includes_uptime(self, fresh_client):
        data = fresh_client.get("/health").json()
        assert "uptime_seconds" in data["gateway"]

    def test_includes_job_counters(self, fresh_client):
        data = fresh_client.get("/health").json()
        gw = data["gateway"]
        assert "active_jobs" in gw
        assert "queued_jobs" in gw
        assert "total_processed" in gw
        assert "total_rejected" in gw

    def test_includes_circuit_breaker_state(self, fresh_client):
        data = fresh_client.get("/health").json()
        cb = data["circuit_breaker"]
        assert cb["state"] == "closed"
        assert "failure_count" in cb


class TestRequestID:
    """Tests for X-Request-ID header."""

    def test_generates_request_id(self, fresh_client):
        resp = fresh_client.get("/")
        assert "x-request-id" in resp.headers
        assert len(resp.headers["x-request-id"]) == 36

    def test_echoes_provided_request_id(self, fresh_client):
        custom_id = "my-trace-id-123"
        resp = fresh_client.get("/", headers={"X-Request-ID": custom_id})
        assert resp.headers["x-request-id"] == custom_id

    def test_unique_ids(self, fresh_client):
        ids = {fresh_client.get("/").headers["x-request-id"] for _ in range(5)}
        assert len(ids) == 5


class TestSecurityHeaders:
    """Tests for security headers."""

    def test_x_content_type_options(self, fresh_client):
        assert fresh_client.get("/").headers.get("x-content-type-options") == "nosniff"

    def test_x_frame_options(self, fresh_client):
        assert fresh_client.get("/").headers.get("x-frame-options") == "DENY"

    def test_x_xss_protection(self, fresh_client):
        assert fresh_client.get("/").headers.get("x-xss-protection") == "1; mode=block"

    def test_server_header(self, fresh_client):
        assert fresh_client.get("/").headers.get("server") == "gotenberg-gateway"


class TestRouteWhitelist:
    """Tests for route whitelisting."""

    def test_blocks_random_path(self, fresh_client):
        resp = fresh_client.get("/some/random/path")
        assert resp.status_code == 403
        assert "not allowed" in resp.json()["message"]

    def test_blocks_admin(self, fresh_client):
        assert fresh_client.get("/admin").status_code == 403

    def test_blocks_debug(self, fresh_client):
        assert fresh_client.get("/debug/pprof").status_code == 403

    def test_allows_root(self, fresh_client):
        assert fresh_client.get("/").status_code == 200

    def test_allows_health(self, fresh_client):
        assert fresh_client.get("/health").status_code in (200, 503)

    def test_allows_chromium_route(self, fresh_client):
        resp = fresh_client.post(
            "/forms/chromium/convert/url",
            files=multipart_form(url="https://example.com"),
        )
        assert resp.status_code != 403

    def test_allows_libreoffice_route(self, fresh_client):
        assert fresh_client.post("/forms/libreoffice/convert").status_code != 403


class TestNoAuth:
    """Verify there is no authentication — all requests pass through."""

    def test_no_auth_on_root(self, fresh_client):
        resp = fresh_client.get("/")
        assert resp.status_code == 200

    def test_no_auth_on_conversion(self, fresh_client):
        resp = fresh_client.post(
            "/forms/chromium/convert/url",
            files=multipart_form(url="https://example.com"),
        )
        # Should not be 401 or 403 (auth-related)
        assert resp.status_code not in (401, 403)
