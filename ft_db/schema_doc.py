"""Write workspace/SCHEMA.md from the live catalog."""

from __future__ import annotations

from pathlib import Path

import psycopg

from ft_db.connect import repo_root

_INTRO = """\
# Database schema

Generated from the live Postgres catalog by `ft-schema-doc`. Do not edit by hand.
The next migration rewrites this file.

Postings are signed double-entry and must sum to 0 on every non-void transaction.
Positive amounts increase assets and expenses. Negative amounts increase liabilities
and income. Category accounts use paths such as `expense:food:ordered_in`.

"""


def schema_doc_path() -> Path:
    return repo_root() / "workspace" / "SCHEMA.md"


def write_schema_doc(conn: psycopg.Connection, path: Path | None = None) -> Path:
    with conn.transaction():
        text = _render(conn)
    target = path or schema_doc_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    return target


def _render(conn: psycopg.Connection) -> str:
    tables = conn.execute(
        """
        SELECT c.relname, obj_description(c.oid, 'pg_class')
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind = 'r'
        ORDER BY c.relname
        """
    ).fetchall()
    columns = conn.execute(
        """
        SELECT
          c.relname,
          a.attname,
          pg_catalog.format_type(a.atttypid, a.atttypmod),
          a.attnotnull,
          col_description(c.oid, a.attnum)
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid
        WHERE n.nspname = 'public'
          AND c.relkind = 'r'
          AND a.attnum > 0
          AND NOT a.attisdropped
        ORDER BY c.relname, a.attnum
        """
    ).fetchall()
    triggers = conn.execute(
        """
        SELECT c.relname, t.tgname, obj_description(t.oid, 'pg_trigger')
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND NOT t.tgisinternal
        ORDER BY c.relname, t.tgname
        """
    ).fetchall()
    views = conn.execute(
        """
        SELECT
          c.relname,
          obj_description(c.oid, 'pg_class'),
          pg_get_viewdef(c.oid, true)
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind = 'v'
        ORDER BY c.relname
        """
    ).fetchall()

    by_table: dict[str, list[tuple]] = {}
    for relname, attname, data_type, not_null, comment in columns:
        by_table.setdefault(relname, []).append((attname, data_type, not_null, comment))
    triggers_by_table: dict[str, list[tuple]] = {}
    for relname, tgname, comment in triggers:
        triggers_by_table.setdefault(relname, []).append((tgname, comment))

    parts = [_INTRO, "## Tables\n\n"]
    for relname, table_comment in tables:
        parts.append(f"### `{relname}`\n\n")
        if table_comment:
            parts.append(f"{_cell(table_comment)}\n\n")
        parts.append("| Column | Type | Nullable | Comment |\n")
        parts.append("| --- | --- | --- | --- |\n")
        for attname, data_type, not_null, comment in by_table.get(relname, []):
            nullable = "no" if not_null else "yes"
            parts.append(
                f"| `{attname}` | `{data_type}` | {nullable} | {_cell(comment)} |\n"
            )
        parts.append("\n")
        table_triggers = triggers_by_table.get(relname, [])
        if table_triggers:
            parts.append("Triggers:\n\n")
            for tgname, comment in table_triggers:
                detail = f" — {_cell(comment)}" if comment else ""
                parts.append(f"- `{tgname}`{detail}\n")
            parts.append("\n")

    parts.append("## Views\n\n")
    for relname, comment, definition in views:
        parts.append(f"### `{relname}`\n\n")
        if comment:
            parts.append(f"{_cell(comment)}\n\n")
        body = (definition or "").strip()
        parts.append("```sql\n")
        parts.append(body)
        if not body.endswith("\n"):
            parts.append("\n")
        parts.append("```\n\n")
    return "".join(parts)


def _cell(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.replace("|", "/").split())
