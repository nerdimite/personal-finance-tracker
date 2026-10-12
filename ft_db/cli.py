"""Command-line entry points used by the Makefile."""

from __future__ import annotations

import sys
import time

import psycopg

from ft_db.categories import sync_categories
from ft_db.connect import connect, owner_database_url, safe_target
from ft_db.migrate import apply_pending_migrations, sync_role_passwords
from ft_db.schema_doc import write_schema_doc
from ft_db.seed import apply_seed


def _public_error(exc: BaseException) -> str:
    text = str(exc)
    if "://" in text or "password" in text.lower():
        return exc.__class__.__name__
    return text


def _connect_retry(url: str, attempts: int = 30) -> psycopg.Connection:
    last: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return connect(url)
        except psycopg.OperationalError as exc:
            last = exc
            message = str(exc).lower()
            if "password authentication failed" in message or "does not exist" in message:
                raise SystemExit(
                    f"could not connect to {safe_target(url)}: {_public_error(exc)}"
                ) from exc
            print(
                f"waiting for postgres at {safe_target(url)} ({attempt}/{attempts})",
                file=sys.stderr,
            )
            time.sleep(1)
    raise SystemExit(f"could not connect to {safe_target(url)}: {_public_error(last or 'unknown')}")


def migrate_main() -> None:
    url = owner_database_url()
    with _connect_retry(url) as conn:
        applied = apply_pending_migrations(conn)
        sync_role_passwords(conn)
    if applied:
        print("applied " + ", ".join(applied))
    else:
        print("no pending migrations")
    print("wrote workspace/SCHEMA.md")


def seed_main() -> None:
    with _connect_retry(owner_database_url()) as conn:
        apply_seed(conn)
    print("seeded equity:opening, expense:uncategorized, and income roots")


def sync_categories_main() -> None:
    with _connect_retry(owner_database_url()) as conn:
        stats = sync_categories(conn)
    print(f"synced {stats.upserted} categories, marked {stats.deactivated} inactive")


def schema_doc_main() -> None:
    with _connect_retry(owner_database_url()) as conn:
        path = write_schema_doc(conn)
    print(f"wrote {path.relative_to(path.parents[1])}")


def test_db_main() -> None:
    from ft_db.verify import run_db_tests

    run_db_tests()


def reset_ledger_main() -> None:
    if "--yes" not in sys.argv[1:]:
        print(
            "refusing to reset the ledger. Re-run with --yes "
            "(or: make reset-ledger YES=1)",
            file=sys.stderr,
        )
        raise SystemExit(2)
    from ft_db.reset_ledger import format_reset_report, reset_ledger

    url = owner_database_url()
    with _connect_retry(url) as conn:
        deleted = reset_ledger(conn)
    print(format_reset_report(url, deleted))
