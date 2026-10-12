"""Apply plain-SQL migrations, one file per transaction.

Ticket 02 calls `apply_migration_sql` after a proposal is approved, and
`dry_run_sql` to roll a proposal back. Files whose first comment is
`-- status: pending` or `-- status: rejected` are left for that flow.
"""

from __future__ import annotations

import re
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.pq import TransactionStatus

from ft_db.connect import (
    agent_database_url,
    dashboard_database_url,
    migrator_database_url,
    password_from_url,
    repo_root,
)
from ft_db.schema_doc import write_schema_doc
from ft_db.sql_guard import headerless_allowed, proposal_refusal
from ft_db.sql_script import exec_script

_FILE_RE = re.compile(r"^(\d{3,})_(.+)\.sql$")
_STATUS_RE = re.compile(r"^--\s*status:\s*(\w+)\s*$", re.IGNORECASE)
_SKIP_STATUSES = {"pending", "rejected"}


class AlreadyApplied(Exception):
    def __init__(self, version: str) -> None:
        super().__init__(f"migration {version} is already applied")
        self.version = version


def migrations_dir() -> Path:
    return repo_root() / "db" / "migrations"


def migration_status(script: str) -> str | None:
    """Return a leading `-- status:` value, if the file declares one."""
    for line in script.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = _STATUS_RE.match(stripped)
        if match:
            return match.group(1).lower()
        if stripped.startswith("--"):
            continue
        return None
    return None


def list_migration_files(directory: Path | None = None) -> list[tuple[str, str, Path]]:
    root = directory or migrations_dir()
    found: list[tuple[str, str, Path]] = []
    seen: dict[str, Path] = {}
    for path in sorted(root.glob("*.sql")):
        match = _FILE_RE.match(path.name)
        if not match:
            raise RuntimeError(
                f"{path.name} must look like NNN_name.sql (at least three digits)"
            )
        version, name = match.group(1), match.group(2)
        if version in seen:
            raise RuntimeError(
                f"duplicate migration version {version}: {seen[version].name} and {path.name}"
            )
        seen[version] = path
        found.append((version, name, path))
    return found


def _require_idle(conn: psycopg.Connection, action: str) -> None:
    if conn.info.transaction_status != TransactionStatus.IDLE:
        raise RuntimeError(f"{action} needs an idle connection; it owns the transaction")


def applied_versions(conn: psycopg.Connection) -> set[str]:
    with conn.transaction():
        row = conn.execute("SELECT to_regclass('public.schema_migrations')").fetchone()
        if row is None or row[0] is None:
            return set()
        rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
        return {r[0] for r in rows}


def apply_migration_sql(
    conn: psycopg.Connection,
    *,
    version: str,
    name: str,
    sql_text: str,
    approved_by: str | None = None,
    regenerate_schema: bool = True,
    schema_path: Path | None = None,
) -> None:
    """Run one migration and record it, in a single transaction.

    Regenerates workspace/SCHEMA.md after the commit. Refuses to run when a
    transaction is already open, so a caller's rollback cannot undo the
    schema file after the database commit.
    """
    _require_idle(conn, "apply_migration_sql")
    if version in applied_versions(conn):
        raise AlreadyApplied(version)

    with conn.transaction():
        exec_script(conn, sql_text)
        conn.execute(
            """
            INSERT INTO schema_migrations (version, name, approved_by)
            VALUES (%s, %s, %s)
            """,
            (version, name, approved_by),
        )

    if regenerate_schema:
        write_schema_doc(conn, schema_path)


def dry_run_sql(conn: psycopg.Connection, sql_text: str) -> None:
    """Execute `sql_text` and roll it back. Raise if any statement fails."""
    _require_idle(conn, "dry_run_sql")

    class _DryRunRollback(Exception):
        pass

    try:
        with conn.transaction():
            exec_script(conn, sql_text)
            raise _DryRunRollback()
    except _DryRunRollback:
        return


def apply_pending_migrations(
    conn: psycopg.Connection,
    directory: Path | None = None,
    *,
    approved_by: str | None = "bootstrap",
    regenerate_schema: bool = True,
    schema_path: Path | None = None,
) -> list[str]:
    """Apply every ready migration file that is not yet in schema_migrations.

    Returns the versions applied, in order. Pending and rejected files are skipped.
    SCHEMA.md is regenerated after each applied file, and once at the end when
    nothing was pending, so the doc matches the live database.
    """
    applied = applied_versions(conn)
    done: list[str] = []
    for version, name, path in list_migration_files(directory):
        script = path.read_text()
        status = migration_status(script)
        if status in _SKIP_STATUSES:
            continue
        if version in applied:
            continue
        if not headerless_allowed(version):
            if status is None:
                raise RuntimeError(
                    f"{path.name} has no -- status: header; only 001 may omit it"
                )
            refused = proposal_refusal(script)
            if refused:
                raise RuntimeError(f"{path.name}: {refused}")
        apply_migration_sql(
            conn,
            version=version,
            name=name,
            sql_text=script,
            approved_by=approved_by,
            regenerate_schema=regenerate_schema,
            schema_path=schema_path,
        )
        done.append(version)
        applied.add(version)

    if regenerate_schema and not done:
        write_schema_doc(conn, schema_path)
    return done


def sync_role_passwords(conn: psycopg.Connection) -> None:
    """Set agent, dashboard, and migrator passwords from their database URLs.

    Passwords stay out of SQL files. A missing agent or dashboard role is
    skipped so this can run again after the init migration creates them.
    Once migrator exists, a missing MIGRATOR_DATABASE_URL is an error.
    """
    _require_idle(conn, "sync_role_passwords")
    targets = {
        "agent": password_from_url(agent_database_url()),
        "dashboard": password_from_url(dashboard_database_url()),
    }
    with conn.transaction():
        for role, password in targets.items():
            if not password:
                continue
            row = conn.execute(
                "SELECT 1 FROM pg_roles WHERE rolname = %s",
                (role,),
            ).fetchone()
            if row is None:
                continue
            conn.execute(
                sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                    sql.Identifier(role),
                    sql.Literal(password),
                )
            )
        migrator = conn.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = 'migrator'"
        ).fetchone()
        if migrator is not None:
            migrator_password = password_from_url(migrator_database_url())
            if not migrator_password:
                raise RuntimeError("MIGRATOR_DATABASE_URL has no password")
            conn.execute(
                sql.SQL("ALTER ROLE migrator PASSWORD {}").format(
                    sql.Literal(migrator_password),
                )
            )
