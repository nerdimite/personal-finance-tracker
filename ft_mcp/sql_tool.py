"""Run agent SQL in one transaction and return a compact JSON-ready result."""

from __future__ import annotations

import os
import re
from datetime import date, datetime
from decimal import Decimal

import psycopg

from ft_db.connect import agent_database_url, connect
from ft_db.sql_guard import session_control_reason
from ft_db.sql_script import split_sql
from ft_mcp.db_error import format_db_error
from ft_mcp.sqltext import control_word

_TIMEOUT_RE = re.compile(r"^[0-9]+\s*(ms|s|min|h)?$", re.IGNORECASE)
_DEFAULT_TIMEOUT = "15s"
_DEFAULT_ROW_CAP = 200


class QueryFailed(Exception):
    """The sql call failed. The message is safe to show to the model."""


def app_run_id() -> str | None:
    """Bot-supplied agent_runs id. Blank or an unexpanded ${...} placeholder is unset."""
    raw = os.environ.get("APP_RUN_ID", "").strip()
    if not raw or raw.startswith("${"):
        return None
    return raw


def statement_timeout() -> str:
    raw = os.environ.get("SQL_STATEMENT_TIMEOUT", _DEFAULT_TIMEOUT).strip() or _DEFAULT_TIMEOUT
    if not _TIMEOUT_RE.match(raw):
        return _DEFAULT_TIMEOUT
    return raw


def row_cap() -> int:
    raw = os.environ.get("SQL_ROW_CAP", str(_DEFAULT_ROW_CAP)).strip()
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_ROW_CAP
    return min(max(value, 1), 5000)


def run_sql(query: str) -> dict:
    """Execute `query` as the agent role. Several statements share one transaction.

    Sets app.run_id from APP_RUN_ID when that variable is present, and sets a
    statement timeout. Raises QueryFailed with Postgres error text on failure;
    the transaction is rolled back.
    """
    statements = _statements(query)
    run_id = app_run_id()
    timeout = statement_timeout()
    cap = row_cap()
    try:
        with connect(agent_database_url()) as conn:
            with conn.transaction():
                results = _execute_all(conn, statements, run_id=run_id, timeout=timeout, cap=cap)
    except QueryFailed:
        raise
    except Exception as exc:
        raise QueryFailed(format_db_error(exc)) from None
    return {"ok": True, "run_id": run_id, "statements": results}


def _statements(query: str) -> list[str]:
    if not query or not query.strip():
        raise QueryFailed("ERROR: query is empty")
    statements = split_sql(query)
    if not statements:
        raise QueryFailed("ERROR: query has no statements")
    for statement in statements:
        word = control_word(statement)
        if word:
            raise QueryFailed(
                f"ERROR: {word} is not allowed; sql runs every statement in one transaction"
            )
        reason = session_control_reason(statement)
        if reason:
            raise QueryFailed(f"ERROR: {reason}")
    return statements


def _execute_all(
    conn: psycopg.Connection,
    statements: list[str],
    *,
    run_id: str | None,
    timeout: str,
    cap: int,
) -> list[dict]:
    results: list[dict] = []
    with conn.cursor() as cur:
        for index, statement in enumerate(statements, start=1):
            _set_session(cur, timeout=timeout, run_id=run_id)
            try:
                cur.execute(statement)
            except psycopg.Error as exc:
                message = format_db_error(exc, statement=statement)
                raise QueryFailed(f"{message}\nstatement_index: {index}") from None
            results.append(_capture(cur, index, cap))
    return results


def _set_session(cur: psycopg.Cursor, *, timeout: str, run_id: str | None) -> None:
    cur.execute("SELECT set_config('statement_timeout', %s, true)", (timeout,))
    cur.fetchone()
    if run_id:
        cur.execute("SELECT set_config('app.run_id', %s, true)", (run_id,))
        cur.fetchone()


def _capture(cur: psycopg.Cursor, index: int, cap: int) -> dict:
    entry: dict = {"index": index, "status": cur.statusmessage}
    if cur.description is None:
        entry["row_count"] = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else None
        return entry

    columns = [col.name for col in cur.description]
    total = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else None
    if total is None:
        fetched = cur.fetchmany(cap + 1)
        truncated = len(fetched) > cap
        rows = fetched[:cap]
        shown_total = None
    else:
        rows = cur.fetchmany(cap)
        truncated = total > len(rows)
        shown_total = total
    entry["row_count"] = len(rows)
    entry["columns"] = columns
    entry["rows"] = [[_cell(value) for value in row] for row in rows]
    entry["truncated"] = truncated
    if truncated:
        if shown_total is None:
            entry["note"] = f"showing {len(rows)} rows; more rows were not returned"
        else:
            entry["note"] = f"showing {len(rows)} of {shown_total} rows"
    return entry


def _cell(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _cell(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_cell(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, memoryview):
        return value.tobytes().hex()
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).hex()
    return str(value)
