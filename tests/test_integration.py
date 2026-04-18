"""
Integration tests requiring a running Gotenberg container on port 1795.
"""

import pytest
import httpx
from fastapi.testclient import TestClient
from tests.conftest import multipart_form


def gotenberg_is_running() -> bool:
    try:
        return httpx.get("http://localhost:1795/health", timeout=3.0).status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not gotenberg_is_running(),
    reason="Gotenberg container not running on port 1795",
)


class TestHealthIntegration:
    def test_health_reports_healthy(self, fresh_client):
        resp = fresh_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["gotenberg"]["status"] == "healthy"
        assert data["circuit_breaker"]["state"] == "closed"


class TestURLToPDF:
    def test_convert_url(self, fresh_client):
        resp = fresh_client.post(
            "/forms/chromium/convert/url",
            files=multipart_form(url="https://example.com"),
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.content[:5] == b"%PDF-"

    def test_pdf_nonzero_size(self, fresh_client):
        resp = fresh_client.post(
            "/forms/chromium/convert/url",
            files=multipart_form(url="https://example.com"),
        )
        assert len(resp.content) > 1000

    def test_has_request_id(self, fresh_client):
        resp = fresh_client.post(
            "/forms/chromium/convert/url",
            files=multipart_form(url="https://example.com"),
        )
        assert "x-request-id" in resp.headers

    def test_has_security_headers(self, fresh_client):
        resp = fresh_client.post(
            "/forms/chromium/convert/url",
            files=multipart_form(url="https://example.com"),
        )
        assert resp.headers.get("x-content-type-options") == "nosniff"
        assert resp.headers.get("server") == "gotenberg-gateway"


class TestHTMLToPDF:
    def test_convert_html(self, fresh_client):
        html = b"<html><body><h1>Gateway Test</h1></body></html>"
        resp = fresh_client.post(
            "/forms/chromium/convert/html",
            files={"files": ("index.html", html, "text/html")},
        )
        assert resp.status_code == 200
        assert resp.content[:5] == b"%PDF-"


class TestProxyErrors:
    def test_invalid_request(self, fresh_client):
        resp = fresh_client.post(
            "/forms/chromium/convert/url",
            files=multipart_form(url="not-a-url"),
        )
        assert resp.status_code >= 400

    def test_no_auth_required(self, fresh_client):
        """Verify no authentication is needed for conversion."""
        resp = fresh_client.post(
            "/forms/chromium/convert/url",
            files=multipart_form(url="https://example.com"),
        )
        assert resp.status_code not in (401, 403)
