"""Paths, env, and the database-host rewrite used inside Compose."""

from __future__ import annotations

import logging
import os
import shlex
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from ft_db.connect import load_env

URL_KEYS = (
    "OWNER_DATABASE_URL",
    "AGENT_DATABASE_URL",
    "DASHBOARD_DATABASE_URL",
)


def repo_root() -> Path:
    override = os.environ.get("FT_REPO_ROOT", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[1]


def workspace_dir() -> Path:
    override = os.environ.get("FT_WORKSPACE", "").strip()
    if override:
        return Path(override)
    return repo_root() / "workspace"


def logs_dir() -> Path:
    override = os.environ.get("FT_LOGS", "").strip()
    if override:
        return Path(override)
    return repo_root() / "logs"


def messages_dir() -> Path:
    return workspace_dir() / "messages"


def runs_dir() -> Path:
    return logs_dir() / "runs"


def state_path() -> Path:
    override = os.environ.get("FT_BOT_STATE", "").strip()
    if override:
        return Path(override)
    return logs_dir() / "bot_state.json"


def claude_bin() -> str:
    return os.environ.get("FT_CLAUDE_BIN", "claude").strip() or "claude"


def claude_timeout_s() -> float:
    raw = os.environ.get("FT_CLAUDE_TIMEOUT_S", "900").strip()
    try:
        value = float(raw)
    except ValueError:
        return 900.0
    return value if value > 0 else 900.0


def album_quiet_s() -> float:
    raw = os.environ.get("FT_ALBUM_QUIET_S", "1.2").strip()
    try:
        value = float(raw)
    except ValueError:
        return 1.2
    return value if value > 0 else 1.2


def telegram_token() -> str:
    load_env()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if token == "replace-me":
        return ""
    return token


def allowed_chat_id() -> str:
    load_env()
    return os.environ.get("TELEGRAM_ALLOWED_CHAT_ID", "").strip()


def rewrite_database_url(url: str, host: str | None, port: str | None) -> str:
    """Replace the host and port. The user and password stay as they are."""
    parsed = urlsplit(url)
    hostname = host or parsed.hostname or "localhost"
    if port:
        port_i: int | None = int(port)
    else:
        port_i = parsed.port
    username = parsed.username or ""
    password = parsed.password or ""
    auth = username
    if password:
        auth = f"{username}:{password}"
    netloc = f"{auth}@{hostname}" if auth else hostname
    if port_i:
        netloc = f"{netloc}:{port_i}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def apply_db_host() -> None:
    """Point database URLs at FT_DB_HOST when that variable is set.

    Compose injects the host `.env` URLs (127.0.0.1 and the published port) and
    sets FT_DB_HOST=postgres so the same passwords work on the Docker network.
    """
    load_env()
    host = os.environ.get("FT_DB_HOST", "").strip()
    port = os.environ.get("FT_DB_PORT", "").strip()
    if not host and not port:
        return
    for key in URL_KEYS:
        raw = os.environ.get(key, "").strip()
        if not raw:
            continue
        os.environ[key] = rewrite_database_url(raw, host or None, port or None)


def shell_exports() -> str:
    """Shell assignments for the rewritten URLs. Callers must not log this."""
    apply_db_host()
    lines: list[str] = []
    for key in URL_KEYS:
        value = os.environ.get(key)
        if value:
            lines.append(f"export {key}={shlex.quote(value)}")
    return "\n".join(lines)


def configure_logging() -> None:
    logs_dir().mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(logs_dir() / "bot.log"),
        ],
        force=True,
    )
    # httpx logs the full request URL, and the Telegram URL contains the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
