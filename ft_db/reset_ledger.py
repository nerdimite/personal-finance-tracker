"""Delete ledger rows from the finance database and re-seed it.

Does not touch logs/ or workspace/messages/. Requires an explicit --yes.
"""

from __future__ import annotations

import psycopg
from psycopg import sql

from ft_db.categories import categories_path, parse_categories, sync_categories
from ft_db.connect import safe_target
from ft_db.seed import apply_seed

# Child tables first, then the rows they reference. audit_log is last because
# the audit trigger writes a row for every delete above.
_DELETE_FIRST = (
    "tool_calls",
    "agent_runs",
    "import_rows",
    "import_batches",
    "attachments",
    "postings",
    "transactions",
    "balance_checks",
    "messages",
    "trips",
)

_SEED_PATHS = (
    "equity:opening",
    "expense:uncategorized",
    "income",
    "income:salary",
    "income:interest",
    "income:dividends",
    "income:refunds_cashback",
    "income:gifts_received",
    "income:other",
)


def kept_account_paths() -> list[str]:
    """Seed accounts plus every path declared in CATEGORIES.md."""
    paths = {node.path for node in parse_categories(categories_path().read_text())}
    paths.update(_SEED_PATHS)
    return sorted(paths)


def reset_ledger(conn: psycopg.Connection) -> dict[str, int]:
    """Delete ledger data, then seed and sync categories. Returns delete counts."""
    keep = kept_account_paths()
    deleted: dict[str, int] = {}
    removed_paths: list[str] = []
    with conn.transaction():
        for table in _DELETE_FIRST:
            deleted[table] = conn.execute(
                sql.SQL("DELETE FROM {}").format(sql.Identifier(table))
            ).rowcount
        removed = conn.execute(
            """
            DELETE FROM accounts
            WHERE NOT (path = ANY(%s))
            RETURNING path
            """,
            (keep,),
        ).fetchall()
        removed_paths = sorted(row[0] for row in removed)
        deleted["accounts"] = len(removed_paths)
        deleted["people"] = conn.execute("DELETE FROM people").rowcount
        deleted["audit_log"] = conn.execute("DELETE FROM audit_log").rowcount
    apply_seed(conn)
    stats = sync_categories(conn)
    deleted["categories_upserted"] = stats.upserted
    deleted["categories_deactivated"] = stats.deactivated
    deleted["removed_account_paths"] = removed_paths  # type: ignore[assignment]
    return deleted


def format_reset_report(url: str, deleted: dict[str, int]) -> str:
    paths = deleted.get("removed_account_paths", [])
    path_text = ", ".join(paths) if paths else "none"
    lines = [
        f"reset ledger on {safe_target(url)}",
        (
            "deleted "
            f"{deleted['transactions']} transactions, "
            f"{deleted['postings']} postings, "
            f"{deleted['people']} people, "
            f"{deleted['accounts']} accounts, "
            f"{deleted['messages']} messages, "
            f"{deleted['agent_runs']} agent_runs, "
            f"{deleted['tool_calls']} tool_calls, "
            f"{deleted['audit_log']} audit rows"
        ),
        f"removed accounts: {path_text}",
        (
            f"re-seeded categories ({deleted['categories_upserted']} upserted, "
            f"{deleted['categories_deactivated']} marked inactive)"
        ),
    ]
    return "\n".join(lines)
