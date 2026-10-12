"""Postgres errors safe to show to the agent. No connection strings."""

from __future__ import annotations

import psycopg

from ft_db.sql_script import ScriptError


def format_db_error(exc: BaseException, *, statement: str | None = None) -> str:
    if isinstance(exc, ScriptError):
        cause = exc.cause if isinstance(exc.cause, BaseException) else exc
        return format_db_error(cause, statement=statement or exc.statement)

    lines: list[str] = []
    diag = exc.diag if isinstance(exc, psycopg.Error) else None
    primary = diag.message_primary if diag is not None else None
    if primary:
        head = primary
        if diag is not None and diag.sqlstate:
            head = f"{head} ({diag.sqlstate})"
        lines.append(f"ERROR: {head}")
        if diag is not None and diag.message_detail:
            lines.append(f"detail: {diag.message_detail}")
        if diag is not None and diag.message_hint:
            lines.append(f"hint: {diag.message_hint}")
        if diag is not None and diag.constraint_name:
            lines.append(f"constraint: {diag.constraint_name}")
        if diag is not None and diag.table_name:
            lines.append(f"table: {diag.table_name}")
        if diag is not None and diag.column_name:
            lines.append(f"column: {diag.column_name}")
    else:
        text = str(exc).strip() or exc.__class__.__name__
        if "://" in text or "password" in text.lower():
            text = exc.__class__.__name__
        lines.append(f"ERROR: {text}")

    if statement:
        preview = " ".join(statement.split())
        if len(preview) > 300:
            preview = preview[:300] + "..."
        lines.append(f"statement: {preview}")
    return "\n".join(lines)


def short_db_failure(exc: BaseException, action: str) -> str:
    """SQLSTATE only. Proposal errors must not echo message text that a RAISE can fill."""
    current = exc
    if isinstance(exc, ScriptError):
        cause = exc.cause
        if isinstance(cause, BaseException):
            current = cause
    state = getattr(current, "sqlstate", None)
    if isinstance(state, str) and state:
        return f"{action} failed ({state})"
    return f"{action} failed"
