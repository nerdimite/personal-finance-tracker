"""Connection URLs and a secret-safe connector.

Host-side commands default to 127.0.0.1 and the published Postgres port.
Compose services override the URLs so the host name is `postgres`.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlparse, urlunparse

import psycopg

DEFAULT_OWNER_DATABASE_URL = "postgresql://owner:owner@127.0.0.1:5433/finance"
DEFAULT_AGENT_DATABASE_URL = "postgresql://agent:agent@127.0.0.1:5433/finance"
DEFAULT_DASHBOARD_DATABASE_URL = "postgresql://dashboard:dashboard@127.0.0.1:5433/finance"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_env(path: Path | None = None) -> None:
    """Load KEY=VALUE lines into the environment without overriding or printing them."""
    env_path = path or (repo_root() / ".env")
    if not env_path.is_file():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip("'").strip('"')


def owner_database_url() -> str:
    load_env()
    return os.environ.get("OWNER_DATABASE_URL", DEFAULT_OWNER_DATABASE_URL)


def agent_database_url() -> str:
    load_env()
    return os.environ.get("AGENT_DATABASE_URL", DEFAULT_AGENT_DATABASE_URL)


def dashboard_database_url() -> str:
    load_env()
    return os.environ.get("DASHBOARD_DATABASE_URL", DEFAULT_DASHBOARD_DATABASE_URL)


def migrator_database_url() -> str:
    """Non-superuser URL for proposal dry-run and apply. No default: a missing URL must fail."""
    load_env()
    url = os.environ.get("MIGRATOR_DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("MIGRATOR_DATABASE_URL is not set")
    return url


def with_database(url: str, database: str) -> str:
    """Same connection URL, with the database name replaced."""
    parsed = urlparse(url)
    return urlunparse(parsed._replace(path=f"/{database}"))


def password_from_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.password is None:
        return ""
    return unquote(parsed.password)


def safe_target(url: str) -> str:
    """Host, port, and database name only — no user or password."""
    parsed = urlparse(url)
    host = parsed.hostname or "?"
    port = parsed.port or 5432
    name = parsed.path.lstrip("/") or "?"
    return f"{host}:{port}/{name}"


def connect(url: str) -> psycopg.Connection:
    conn = psycopg.connect(url, connect_timeout=5)
    # Migrations change the catalog; avoid cached prepared statements.
    conn.prepare_threshold = None
    return conn
