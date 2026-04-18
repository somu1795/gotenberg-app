"""
Configuration loader for the Gotenberg Gateway.

Loads settings from config.yaml with environment variable overrides.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import yaml


@dataclass
class ServerConfig:
    port: int = 9225
    host: str = "0.0.0.0"


@dataclass
class GotenbergConfig:
    upstream_url: str = "http://localhost:9125"
    request_timeout: int = 120


@dataclass
class ConcurrencyConfig:
    max_concurrent: int = 10
    max_queue: int = 50
    queue_timeout: int = 60
    per_ip_concurrent: int = 2
    per_ip_queue: int = 5


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5
    recovery_timeout: int = 30


@dataclass
class SecurityConfig:
    cors_origins: List[str] = field(default_factory=lambda: ["*"])
    ip_allowlist: List[str] = field(default_factory=list)
    ip_blocklist: List[str] = field(default_factory=list)
    max_upload_size: int = 5 * 1024 * 1024  # 5MB
    allowed_routes: List[str] = field(default_factory=lambda: [
        "/forms/",
        "/health",
    ])


@dataclass
class LoggingConfig:
    level: str = "INFO"
    format: str = "json"


@dataclass
class GatewayConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    gotenberg: GotenbergConfig = field(default_factory=GotenbergConfig)
    concurrency: ConcurrencyConfig = field(default_factory=ConcurrencyConfig)
    circuit_breaker: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


def _merge_dict(dataclass_type, data: dict):
    """Create a dataclass instance from a dict, ignoring unknown keys."""
    if data is None:
        return dataclass_type()
    valid_fields = {f.name for f in dataclass_type.__dataclass_fields__.values()}
    filtered = {k: v for k, v in data.items() if k in valid_fields}
    return dataclass_type(**filtered)


def load_config(config_path: str = "config.yaml") -> GatewayConfig:
    """Load configuration from YAML file with environment variable overrides."""
    path = Path(config_path)

    raw = {}
    if path.exists():
        with open(path) as f:
            raw = yaml.safe_load(f) or {}

    cfg = GatewayConfig(
        server=_merge_dict(ServerConfig, raw.get("server")),
        gotenberg=_merge_dict(GotenbergConfig, raw.get("gotenberg")),
        concurrency=_merge_dict(ConcurrencyConfig, raw.get("concurrency")),
        circuit_breaker=_merge_dict(CircuitBreakerConfig, raw.get("circuit_breaker")),
        security=_merge_dict(SecurityConfig, raw.get("security")),
        logging=_merge_dict(LoggingConfig, raw.get("logging")),
    )

    # Environment variable overrides
    if env_port := os.environ.get("GATEWAY_PORT"):
        cfg.server.port = int(env_port)
    if env_host := os.environ.get("GATEWAY_HOST"):
        cfg.server.host = env_host
    if env_url := os.environ.get("GOTENBERG_URL"):
        cfg.gotenberg.upstream_url = env_url
    if env_log_level := os.environ.get("GATEWAY_LOG_LEVEL"):
        cfg.logging.level = env_log_level
    if env_max_concurrent := os.environ.get("GATEWAY_MAX_CONCURRENT"):
        cfg.concurrency.max_concurrent = int(env_max_concurrent)
    if env_max_queue := os.environ.get("GATEWAY_MAX_QUEUE"):
        cfg.concurrency.max_queue = int(env_max_queue)

    return cfg
