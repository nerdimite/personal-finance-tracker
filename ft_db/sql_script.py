"""Split a SQL script into statements without breaking function bodies."""

from __future__ import annotations

import re

import psycopg

_TXN_CONTROL = re.compile(
    r"^(BEGIN|COMMIT|ROLLBACK|END|START)(\s|$)",
    re.IGNORECASE,
)


class ScriptError(Exception):
    def __init__(self, statement: str, cause: BaseException) -> None:
        preview = " ".join(statement.split())[:200]
        super().__init__(f"{cause}\nwhile executing: {preview}")
        self.statement = statement
        self.cause = cause


def split_sql(script: str) -> list[str]:
    """Split on semicolons that sit outside quotes, comments, and dollar-quotes."""
    statements: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(script)
    dollar_tag: str | None = None

    while i < n:
        ch = script[i]
        nxt = script[i + 1] if i + 1 < n else ""

        if dollar_tag is not None:
            if script.startswith(dollar_tag, i):
                buf.append(dollar_tag)
                i += len(dollar_tag)
                dollar_tag = None
            else:
                buf.append(ch)
                i += 1
            continue

        if ch == "-" and nxt == "-":
            while i < n and script[i] != "\n":
                buf.append(script[i])
                i += 1
            continue

        if ch == "/" and nxt == "*":
            while i < n - 1 and not (script[i] == "*" and script[i + 1] == "/"):
                buf.append(script[i])
                i += 1
            if i < n - 1:
                buf.append(script[i])
                buf.append(script[i + 1])
                i += 2
            continue

        if ch in {"'", '"'}:
            quote = ch
            buf.append(ch)
            i += 1
            while i < n:
                buf.append(script[i])
                if script[i] == quote:
                    if i + 1 < n and script[i + 1] == quote:
                        buf.append(script[i + 1])
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue

        if ch == "$":
            match = re.match(r"\$[A-Za-z0-9_]*\$", script[i:])
            if match:
                dollar_tag = match.group(0)
                buf.append(dollar_tag)
                i += len(dollar_tag)
                continue

        if ch == ";":
            statement = "".join(buf).strip()
            if statement and not _is_effectively_empty(statement):
                statements.append(statement)
            buf = []
            i += 1
            continue

        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail and not _is_effectively_empty(tail):
        statements.append(tail)
    return statements


def _is_effectively_empty(statement: str) -> bool:
    without_block = re.sub(r"/\*.*?\*/", "", statement, flags=re.DOTALL)
    without_line = re.sub(r"--[^\n]*", "", without_block)
    return not without_line.strip()


def _reject_transaction_control(statement: str) -> None:
    stripped = re.sub(r"/\*.*?\*/", "", statement, flags=re.DOTALL)
    stripped = re.sub(r"--[^\n]*", "", stripped).strip()
    if _TXN_CONTROL.match(stripped):
        word = stripped.split(None, 1)[0].upper()
        raise RuntimeError(
            f"SQL scripts must not include {word}; the runner owns the transaction"
        )


def exec_script(conn: psycopg.Connection, script: str) -> None:
    """Run every statement in `script` on the current transaction."""
    with conn.cursor() as cur:
        for statement in split_sql(script):
            _reject_transaction_control(statement)
            try:
                cur.execute(statement)
                if cur.description is not None:
                    cur.fetchall()
            except psycopg.Error as exc:
                raise ScriptError(statement, exc) from exc
