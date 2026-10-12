"""Who the bot is willing to talk to."""

from __future__ import annotations


def chat_allowed(chat_id: int | None, allowed: str) -> bool:
    """Fail closed: a missing allow-list accepts nobody."""
    allowed = (allowed or "").strip()
    if chat_id is None or not allowed:
        return False
    return str(chat_id) == allowed


def is_builtin_command(text: str | None) -> bool:
    """True for /new, /status, and /splitwise, including the /cmd@BotName form."""
    if not text:
        return False
    head = text.strip().split()[0].split("@", 1)[0].lower()
    return head in {"/new", "/status", "/splitwise"}


def parse_callback(data: str) -> tuple[str, int] | tuple[str, str] | None:
    """`e:000042` -> ("edit", 42). `ic:12` -> ("import_commit", 12). `ma:003` -> ("migration_apply", "003")."""
    if data.startswith("ic:") or data.startswith("ix:"):
        action = "import_commit" if data.startswith("ic:") else "import_discard"
        return _positive_int(action, data.split(":", 1)[1])
    if data.startswith("ur:"):
        return _positive_int("undo_run", data.split(":", 1)[1])
    if data.startswith("ma:") or data.startswith("mr:"):
        action = "migration_apply" if data.startswith("ma:") else "migration_reject"
        migration_id = data.split(":", 1)[1]
        if not migration_id or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-" for char in migration_id):
            return None
        if len(migration_id) > 58:
            return None
        return action, migration_id
    if data.startswith("sa:") or data.startswith("sx:"):
        action = "splitwise_added" if data.startswith("sa:") else "splitwise_cancel"
        return _positive_int(action, data.split(":", 1)[1])
    if len(data) < 3 or data[1] != ":":
        return None
    action = {"e": "edit"}.get(data[0])
    if action is None:
        return None
    parsed = _positive_int(action, data[2:])
    return parsed


def _positive_int(action: str, raw: str) -> tuple[str, int] | None:
    try:
        value = int(raw)
    except ValueError:
        return None
    if value <= 0:
        return None
    return action, value
