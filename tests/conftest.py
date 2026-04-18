"""
Shared test fixtures for the Gotenberg Gateway test suite.
"""

import os
import sys

import pytest
from fastapi.testclient import TestClient

# Ensure the project root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def _create_app(**env_overrides):
    """
    Create a completely fresh app instance by reloading modules.
    Each call gets fresh concurrency state, fresh middleware, etc.
    """
    import importlib

    old_env = {}
    env_map = {
        "port": "GATEWAY_PORT",
        "upstream_url": "GOTENBERG_URL",
        "log_level": "GATEWAY_LOG_LEVEL",
        "max_concurrent": "GATEWAY_MAX_CONCURRENT",
        "max_queue": "GATEWAY_MAX_QUEUE",
    }

    for key, env_var in env_map.items():
        if key in env_overrides:
            old_env[env_var] = os.environ.get(env_var)
            os.environ[env_var] = str(env_overrides[key])

    try:
        import config as config_mod
        import proxy as proxy_mod
        import middleware.logging as mlog
        import middleware.concurrency as mconc
        import middleware.circuit_breaker as mcb
        import middleware.security as msec
        import main as main_mod

        importlib.reload(config_mod)
        importlib.reload(proxy_mod)
        importlib.reload(mlog)
        importlib.reload(mconc)
        importlib.reload(mcb)
        importlib.reload(msec)
        importlib.reload(main_mod)

        return main_mod.app
    finally:
        for env_var, old_val in old_env.items():
            if old_val is None:
                os.environ.pop(env_var, None)
            else:
                os.environ[env_var] = old_val


make_app_with_config = _create_app


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture()
def fresh_client():
    """Per-test client with a completely fresh app (fresh concurrency state)."""
    app = _create_app()
    with TestClient(app) as c:
        yield c


def multipart_form(**fields):
    """
    Build multipart/form-data for TestClient.

    TestClient's data={} sends x-www-form-urlencoded, which doesn't
    have a boundary for SSRF parsing.
    Use files=multipart_form(url="https://example.com") instead.
    """
    return {k: (None, v, "text/plain") for k, v in fields.items()}
