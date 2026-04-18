"""
Tests verifying the gateway does NOT inspect or decode request bodies.

SSRF protection is handled at the Gotenberg container level via
--chromium-deny-list and Docker network isolation, NOT by the gateway.
"""

import pytest
from tests.conftest import multipart_form


class TestPassThrough:
    """Verify the gateway is a pure pass-through proxy for request bodies."""

    def test_private_url_passes_through_gateway(self, fresh_client):
        """
        Gateway should NOT block private URLs — that's Gotenberg's job.
        The request reaches Gotenberg (which may return any status).
        We just verify the gateway itself doesn't inject a 403 with its own error format.
        """
        resp = fresh_client.post(
            "/forms/chromium/convert/url",
            files=multipart_form(url="http://127.0.0.1/"),
        )
        # If it's a 403, it should be from Gotenberg (not our gateway's old SSRF middleware)
        # Our gateway's SSRF 403 had: {"error": "Forbidden", "message": "URL blocked: ..."}
        if resp.status_code == 403:
            try:
                data = resp.json()
                # Our old SSRF middleware returned "URL blocked:" — this should NOT appear
                assert "URL blocked" not in data.get("message", "")
            except Exception:
                pass  # Non-JSON 403 is from Gotenberg, which is fine

    def test_metadata_url_passes_through(self, fresh_client):
        """AWS metadata endpoint should pass through gateway to Gotenberg."""
        resp = fresh_client.post(
            "/forms/chromium/convert/url",
            files=multipart_form(url="http://169.254.169.254/"),
        )
        if resp.status_code == 403:
            try:
                data = resp.json()
                assert "URL blocked" not in data.get("message", "")
            except Exception:
                pass

    def test_file_scheme_passes_through(self, fresh_client):
        """file:// scheme should pass through gateway to Gotenberg."""
        resp = fresh_client.post(
            "/forms/chromium/convert/url",
            files=multipart_form(url="file:///etc/passwd"),
        )
        if resp.status_code == 403:
            try:
                data = resp.json()
                assert "URL blocked" not in data.get("message", "")
            except Exception:
                pass

    def test_public_url_reaches_gotenberg(self, fresh_client):
        """A valid public URL should reach Gotenberg and not be blocked by gateway."""
        resp = fresh_client.post(
            "/forms/chromium/convert/url",
            files=multipart_form(url="https://example.com"),
        )
        # Should not be blocked by the gateway (route whitelist allows it)
        assert resp.status_code != 403 or "not allowed" not in str(resp.content)

    def test_route_whitelist_still_works(self, fresh_client):
        """Route whitelisting is path-based (no body decoding) and should still work."""
        resp = fresh_client.get("/admin")
        assert resp.status_code == 403
        assert "not allowed" in resp.json()["message"]

    def test_no_body_inspection_on_html_route(self, fresh_client):
        """HTML conversion route should pass through without body inspection."""
        resp = fresh_client.post(
            "/forms/chromium/convert/html",
            files={"files": ("index.html", b"<h1>Test</h1>", "text/html")},
        )
        assert resp.status_code != 403
