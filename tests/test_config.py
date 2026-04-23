"""
Tests for the configuration loader.
"""

import os
import pytest

from config import load_config


class TestConfigDefaults:
    """Tests that default values are applied when config is missing."""

    def test_loads_with_no_file(self, tmp_path):
        cfg = load_config(str(tmp_path / "nonexistent.yaml"))
        assert type(cfg).__name__ == "GatewayConfig"

    def test_default_port(self, tmp_path):
        cfg = load_config(str(tmp_path / "nonexistent.yaml"))
        assert cfg.server.port == 9225

    def test_default_upstream_url(self, tmp_path):
        cfg = load_config(str(tmp_path / "nonexistent.yaml"))
        assert cfg.gotenberg.upstream_url == "http://localhost:9125"

    def test_default_max_concurrent(self, tmp_path):
        cfg = load_config(str(tmp_path / "nonexistent.yaml"))
        assert cfg.concurrency.max_concurrent == 10

    def test_default_max_queue(self, tmp_path):
        cfg = load_config(str(tmp_path / "nonexistent.yaml"))
        assert cfg.concurrency.max_queue == 100

    def test_default_per_ip_concurrent(self, tmp_path):
        cfg = load_config(str(tmp_path / "nonexistent.yaml"))
        assert cfg.concurrency.per_ip_concurrent == 2

    def test_default_per_ip_queue(self, tmp_path):
        cfg = load_config(str(tmp_path / "nonexistent.yaml"))
        assert cfg.concurrency.per_ip_queue == 5

    def test_default_circuit_breaker_threshold(self, tmp_path):
        cfg = load_config(str(tmp_path / "nonexistent.yaml"))
        assert cfg.circuit_breaker.failure_threshold == 5

    def test_default_max_upload_size(self, tmp_path):
        cfg = load_config(str(tmp_path / "nonexistent.yaml"))
        assert cfg.security.max_upload_size == 5 * 1024 * 1024

    def test_default_allowed_routes_not_empty(self, tmp_path):
        cfg = load_config(str(tmp_path / "nonexistent.yaml"))
        assert len(cfg.security.allowed_routes) > 0


class TestConfigYAML:
    """Tests loading values from a YAML file."""

    def test_custom_port(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("server:\n  port: 9999\n")
        cfg = load_config(str(config_file))
        assert cfg.server.port == 9999

    def test_custom_concurrency(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("concurrency:\n  max_concurrent: 20\n  max_queue: 100\n")
        cfg = load_config(str(config_file))
        assert cfg.concurrency.max_concurrent == 20
        assert cfg.concurrency.max_queue == 100

    def test_custom_circuit_breaker(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("circuit_breaker:\n  failure_threshold: 10\n  recovery_timeout: 60\n")
        cfg = load_config(str(config_file))
        assert cfg.circuit_breaker.failure_threshold == 10
        assert cfg.circuit_breaker.recovery_timeout == 60


class TestConfigEnvOverrides:
    """Tests that environment variables override YAML values."""

    def test_env_port_override(self, tmp_path):
        os.environ["GATEWAY_PORT"] = "7777"
        try:
            cfg = load_config(str(tmp_path / "nonexistent.yaml"))
            assert cfg.server.port == 7777
        finally:
            del os.environ["GATEWAY_PORT"]

    def test_env_max_concurrent_override(self, tmp_path):
        os.environ["GATEWAY_MAX_CONCURRENT"] = "25"
        try:
            cfg = load_config(str(tmp_path / "nonexistent.yaml"))
            assert cfg.concurrency.max_concurrent == 25
        finally:
            del os.environ["GATEWAY_MAX_CONCURRENT"]

    def test_env_max_queue_override(self, tmp_path):
        os.environ["GATEWAY_MAX_QUEUE"] = "200"
        try:
            cfg = load_config(str(tmp_path / "nonexistent.yaml"))
            assert cfg.concurrency.max_queue == 200
        finally:
            del os.environ["GATEWAY_MAX_QUEUE"]


class TestConfigFromProject:
    """Test loading the actual project config.yaml."""

    def test_loads_project_config(self):
        cfg = load_config("config.yaml")
        assert cfg.server.port == 9225
        assert "localhost:9125" in cfg.gotenberg.upstream_url
        assert cfg.concurrency.max_concurrent == 10
