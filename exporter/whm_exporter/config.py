"""Configuration loaded from environment variables.

Everything is configured through env vars so the same image works in Docker
Compose, systemd or a plain shell. Invalid values fail fast at startup with a
message that names the variable.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

# Default refresh interval (seconds) for each collector. Cheap checks run
# often; expensive ones (disk quotas, SSL inventory, mail stats) run rarely.
DEFAULT_INTERVALS: dict[str, int] = {
    "server": 3600,
    "services": 30,
    "accounts": 300,
    "disk": 300,
    "ssl": 900,
    "cphulk": 60,
    "email": 300,
    "exim_queue": 60,
}

ALL_COLLECTORS: tuple[str, ...] = tuple(DEFAULT_INTERVALS)


class ConfigError(ValueError):
    """Raised when an environment variable is missing or invalid."""


def _get_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be true/false, got {raw!r}")


def _get_int(env: Mapping[str, str], name: str, default: int, minimum: int = 1) -> int:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {value}")
    return value


def _read_token(env: Mapping[str, str]) -> str:
    """Token comes from WHM_API_TOKEN or from the file named by WHM_API_TOKEN_FILE."""
    token_file = env.get("WHM_API_TOKEN_FILE", "").strip()
    if token_file:
        try:
            token = Path(token_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ConfigError(f"cannot read WHM_API_TOKEN_FILE {token_file!r}: {exc}") from exc
    else:
        token = env.get("WHM_API_TOKEN", "").strip()
    if not token:
        raise ConfigError(
            "WHM API token is missing: set WHM_API_TOKEN (or WHM_API_TOKEN_FILE). "
            "Create one in WHM > Development > Manage API Tokens."
        )
    return token


def _parse_collectors(env: Mapping[str, str]) -> tuple[str, ...]:
    raw = env.get("COLLECTORS", "").strip()
    if not raw or raw.lower() == "all":
        return ALL_COLLECTORS
    names = tuple(dict.fromkeys(n.strip() for n in raw.split(",") if n.strip()))
    unknown = sorted(set(names) - set(ALL_COLLECTORS))
    if unknown:
        raise ConfigError(
            f"COLLECTORS contains unknown collector(s) {', '.join(unknown)}; "
            f"valid: {', '.join(ALL_COLLECTORS)}"
        )
    return names


@dataclass(frozen=True)
class Config:
    whm_url: str
    whm_user: str
    whm_token: str = field(repr=False)
    verify_tls: bool = False
    request_timeout: int = 30
    listen_addr: str = "127.0.0.1"
    listen_port: int = 9877
    collectors: tuple[str, ...] = ALL_COLLECTORS
    intervals: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_INTERVALS))
    # Serve cached data for at most this many intervals after the last success;
    # after that the collector's series disappear instead of going stale.
    max_age_intervals: int = 3
    exim_spool_dir: str = "/var/spool/exim/input"
    exim_max_files: int = 50000
    exim_top_users: int = 10
    email_window: int = 3600
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Config":
        env = os.environ if env is None else env

        whm_url = env.get("WHM_URL", "https://127.0.0.1:2087").strip().rstrip("/")
        if not whm_url.startswith(("http://", "https://")):
            raise ConfigError(f"WHM_URL must start with http:// or https://, got {whm_url!r}")

        intervals = dict(DEFAULT_INTERVALS)
        for name in ALL_COLLECTORS:
            intervals[name] = _get_int(env, f"INTERVAL_{name.upper()}", intervals[name], minimum=5)

        log_level = env.get("LOG_LEVEL", "INFO").strip().upper() or "INFO"
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ConfigError(f"LOG_LEVEL must be DEBUG/INFO/WARNING/ERROR, got {log_level!r}")

        return cls(
            whm_url=whm_url,
            whm_user=env.get("WHM_USER", "root").strip() or "root",
            whm_token=_read_token(env),
            verify_tls=_get_bool(env, "WHM_VERIFY_TLS", False),
            request_timeout=_get_int(env, "WHM_TIMEOUT", 30),
            listen_addr=env.get("EXPORTER_LISTEN_ADDR", "127.0.0.1").strip() or "127.0.0.1",
            listen_port=_get_int(env, "EXPORTER_PORT", 9877, minimum=1),
            collectors=_parse_collectors(env),
            intervals=intervals,
            max_age_intervals=_get_int(env, "MAX_AGE_INTERVALS", 3),
            exim_spool_dir=env.get("EXIM_SPOOL_DIR", "/var/spool/exim/input").strip(),
            exim_max_files=_get_int(env, "EXIM_MAX_FILES", 50000),
            exim_top_users=_get_int(env, "EXIM_TOP_USERS", 10, minimum=0),
            email_window=_get_int(env, "EMAIL_STATS_WINDOW", 3600, minimum=60),
            log_level=log_level,
        )
