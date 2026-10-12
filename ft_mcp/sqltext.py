"""Mask SQL text so keyword checks ignore comments and literals."""

from __future__ import annotations

from ft_db.sql_guard import mask_sql, normalize_sql

_CONTROL = {"BEGIN", "COMMIT", "ROLLBACK", "END", "START"}


def control_word(statement: str) -> str | None:
    """Return BEGIN/COMMIT/ROLLBACK/END/START when the statement is transaction control."""
    normalized = normalize_sql(statement)
    if not normalized:
        return None
    word = normalized.split(None, 1)[0].upper()
    if word in _CONTROL:
        return word
    return None
