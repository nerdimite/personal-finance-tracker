"""Propose, list, apply, and reject SQL migrations.

The pending record is the migration file. Ticket 01 skips files whose first
status comment is pending or rejected. Apply and reject are importable and
available as `ft-migration`; they are not MCP tools.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from urllib.parse import urlparse

from psycopg import sql

from ft_db.connect import connect, migrator_database_url, repo_root, with_database
from ft_db.migrate import (
    AlreadyApplied,
    apply_migration_sql,
    applied_versions,
    dry_run_sql,
    list_migration_files,
    migration_status,
    migrations_dir,
)
from ft_db.schema_doc import schema_doc_path
from ft_db.sql_guard import proposal_refusal
from ft_db.sql_script import split_sql
from ft_mcp.classify import Classification, classify_statements
from ft_mcp.db_error import short_db_failure
from ft_mcp.sqltext import control_word

_TRUTHY = {"1", "true", "yes", "on"}
_HEADER_KEYS = ("status", "classification", "reason", "sha256")


class MigrationError(Exception):
    """A proposal or apply/reject failure. The message is safe to print."""


def auto_apply_enabled() -> bool:
    raw = os.environ.get("AUTO_APPLY_ADDITIVE", "false").strip().lower()
    return raw in _TRUTHY


def propose_migration(name: str, up_sql: str, reason: str) -> dict:
    """Write a pending migration and dry-run it as migrator.

    When AUTO_APPLY_ADDITIVE is true, an additive proposal is applied immediately.
    A destructive proposal is never auto-applied. COPY, DO, and the other
    review refusals raise before any database call.
    """
    slug = _slug(name)
    why = _one_line(reason, "reason")
    statements = _migration_statements(up_sql)
    refused = proposal_refusal(up_sql)
    if refused:
        raise MigrationError(refused)
    classification = classify_statements(statements)
    dry_run = _dry_run(up_sql)

    with _lock():
        version, path = _allocate(slug)
        body = _render(status="pending", classification=classification, reason=why, up_sql=up_sql)
        _write_atomic(path, body)

    result = {
        "ok": True,
        "id": version,
        "name": slug,
        "file": _relative(path),
        "classification": classification.kind,
        "notes": classification.notes,
        "status": "pending",
        "dry_run": dry_run,
        "auto_applied": False,
        "reason": why,
    }
    if classification.kind == "additive" and auto_apply_enabled():
        applied = apply_migration(version, approved_by="auto")
        result["status"] = "applied"
        result["auto_applied"] = True
        result["backup"] = applied["backup"]
        result["git"] = applied["git"]
    elif classification.kind == "destructive" and auto_apply_enabled():
        result["notes"] = [
            *classification.notes,
            "destructive proposals never auto-apply",
        ]
    return result


def list_migrations() -> list[dict]:
    applied = _recorded_versions()
    rows: list[dict] = []
    for version, name, path in list_migration_files():
        script = path.read_text()
        status = migration_status(script)
        if status is None:
            label = "applied" if version in applied else "ready"
        elif status == "pending" and version in applied:
            label = "pending but recorded"
        else:
            label = status
        rows.append(
            {
                "id": version,
                "name": name,
                "file": path.name,
                "status": label,
                "classification": _header_value(script, "classification"),
                "reason": _header_value(script, "reason"),
            }
        )
    return rows


def apply_migration(migration_id: str, *, approved_by: str = "cli") -> dict:
    """Snapshot the database, apply one pending proposal, regenerate SCHEMA.md.

    Git commit of the migration file and SCHEMA.md is skipped when git is
    missing, this directory is not a work tree, or the repository has no commit
    yet. A failed commit does not roll back the migration.
    """
    version, name, path = _resolve(migration_id)
    script = path.read_text()
    status = migration_status(script)
    if status == "rejected":
        raise MigrationError(f"migration {version} is rejected")
    if status != "pending":
        raise MigrationError(
            f"migration {version} is not a pending proposal"
            if status is None
            else f"migration {version} is {status}"
        )
    if version in _recorded_versions():
        raise MigrationError(f"migration {version} is already applied")
    refused = proposal_refusal(proposal_body(script))
    if refused:
        raise MigrationError(refused)
    assert_proposal_hash(script)

    backup = _take_backup()
    applied_sql = _set_status(script, "applied")
    try:
        with connect(migrator_database_url()) as conn:
            apply_migration_sql(
                conn,
                version=version,
                name=name,
                sql_text=applied_sql,
                approved_by=approved_by,
            )
    except AlreadyApplied:
        raise MigrationError(f"migration {version} is already applied") from None
    except MigrationError:
        raise
    except Exception as exc:
        raise MigrationError(short_db_failure(exc, "apply")) from None

    warning = None
    try:
        path.write_text(applied_sql)
    except OSError as exc:
        warning = f"applied in the database but could not rewrite {path.name}: {exc.strerror}"

    git = commit_schema_files(
        [path, schema_doc_path()],
        message=f"Apply migration {version}_{name}.\n\nApproved by {approved_by}.",
    )
    result = {
        "ok": True,
        "id": version,
        "name": name,
        "file": _relative(path),
        "status": "applied",
        "backup": backup,
        "git": git,
        "approved_by": approved_by,
    }
    if warning:
        result["warning"] = warning
    return result


def reject_migration(migration_id: str) -> dict:
    """Mark a pending proposal rejected. The file stays; the runner will skip it."""
    version, name, path = _resolve(migration_id)
    script = path.read_text()
    status = migration_status(script)
    if status == "rejected":
        return {"ok": True, "id": version, "name": name, "file": _relative(path), "status": "rejected"}
    if version in _recorded_versions() or status == "applied":
        raise MigrationError(f"migration {version} is already applied")
    if status != "pending":
        raise MigrationError(f"migration {version} is not a pending proposal")
    path.write_text(_set_status(script, "rejected"))
    return {"ok": True, "id": version, "name": name, "file": _relative(path), "status": "rejected"}


def commit_schema_files(paths: list[Path], *, message: str, repo: Path | None = None) -> str:
    """Commit specific files. Returns a short status; does not raise for git problems."""
    git = shutil.which("git")
    if git is None:
        return "skipped: git unavailable"
    root = (repo or repo_root()).resolve()

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [git, *args],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )

    inside = run("rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return "skipped: not a git repo"
    # This checkout has no commit yet. Skip rather than creating the first one
    # from a migration apply; later applies commit once HEAD exists.
    head = run("rev-parse", "--verify", "HEAD")
    if head.returncode != 0:
        return "skipped: repository has no commits yet"

    rels: list[str] = []
    for path in paths:
        resolved = path if path.is_absolute() else root / path
        try:
            rels.append(str(resolved.resolve().relative_to(root)))
        except ValueError:
            return "skipped: path is outside the repository"
    added = run("add", "--", *rels)
    if added.returncode != 0:
        return "skipped: git add failed"
    staged = run("diff", "--cached", "--quiet", "--", *rels)
    if staged.returncode == 0:
        return "skipped: no changes to commit"
    committed = run("commit", "-m", message, "--", *rels)
    if committed.returncode != 0:
        detail = _git_detail(committed)
        return f"skipped: {detail}"
    sha = run("rev-parse", "--short", "HEAD")
    return f"committed {sha.stdout.strip()}"


def _migration_statements(up_sql: str) -> list[str]:
    statements = split_sql(up_sql or "")
    if not statements:
        raise MigrationError("up_sql has no statements")
    for statement in statements:
        word = control_word(statement)
        if word:
            raise MigrationError(
                f"{word} is not allowed; the migration runner owns the transaction"
            )
    return statements


class _ScratchUnavailable(Exception):
    """Scratch database could not be built. Fall back to a rollback on the live database."""


def _dry_run(up_sql: str) -> str:
    try:
        return _dry_run_scratch(up_sql)
    except _ScratchUnavailable:
        _dry_run_live(up_sql)
        return "rolled back"


def _dry_run_live(up_sql: str) -> None:
    try:
        with connect(migrator_database_url()) as conn:
            dry_run_sql(conn, up_sql)
    except MigrationError:
        raise
    except Exception as exc:
        raise MigrationError(short_db_failure(exc, "dry-run")) from None


def _dry_run_scratch(up_sql: str) -> str:
    url = migrator_database_url()
    source_db = urlparse(url).path.lstrip("/") or "finance"
    admin_url = with_database(url, "postgres")
    name = "ft_dryrun_" + secrets.token_hex(4)
    try:
        mode = _create_scratch(admin_url, name, source_db, url)
    except _ScratchUnavailable:
        raise
    except Exception:
        raise _ScratchUnavailable() from None
    try:
        try:
            with connect(with_database(url, name)) as conn:
                dry_run_sql(conn, up_sql)
        except MigrationError:
            raise
        except Exception as exc:
            raise MigrationError(short_db_failure(exc, "dry-run")) from None
        return mode
    finally:
        _drop_database(admin_url, name)


def _create_scratch(admin_url: str, name: str, source_db: str, source_url: str) -> str:
    try:
        with connect(admin_url) as admin:
            admin.autocommit = True
            admin.execute(
                sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                    sql.Identifier(name),
                    sql.Identifier(source_db),
                )
            )
        return "scratch template"
    except Exception:
        pass
    try:
        with connect(admin_url) as admin:
            admin.autocommit = True
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    except Exception:
        raise _ScratchUnavailable() from None
    try:
        _restore_schema(source_url, with_database(source_url, name))
    except Exception:
        _drop_database(admin_url, name)
        raise _ScratchUnavailable() from None
    return "scratch schema"


def _restore_schema(source_url: str, dest_url: str) -> None:
    pg_dump = shutil.which("pg_dump")
    pg_restore = shutil.which("pg_restore")
    if pg_dump is None or pg_restore is None:
        raise _ScratchUnavailable()
    with tempfile.TemporaryDirectory() as tmp:
        dump = Path(tmp) / "schema.dump"
        dumped = subprocess.run(
            [
                pg_dump,
                "--schema-only",
                "--schema=public",
                "--no-owner",
                "--no-acl",
                "--no-comments",
                "-Fc",
                "-d",
                source_url,
                "-f",
                str(dump),
            ],
            check=False,
            capture_output=True,
        )
        if dumped.returncode != 0 or not dump.is_file():
            raise _ScratchUnavailable()
        subprocess.run(
            [pg_restore, "--no-owner", "--no-acl", "--schema=public", "-d", dest_url, str(dump)],
            check=False,
            capture_output=True,
        )
    with connect(dest_url) as conn:
        row = conn.execute("SELECT to_regclass('public.transactions')").fetchone()
    if row is None or row[0] is None:
        raise _ScratchUnavailable()


def _drop_database(admin_url: str, name: str) -> None:
    try:
        with connect(admin_url) as admin:
            admin.autocommit = True
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )
    except Exception:
        return


def proposal_digest(body: str) -> str:
    text = body.strip() + "\n"
    return hashlib.sha256(text.encode()).hexdigest()


def proposal_body(script: str) -> str:
    """SQL after the known header comments. The sha256 covers these bytes."""
    lines = script.splitlines(keepends=True)
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped:
            index += 1
            continue
        if stripped.startswith("--") and _header_key(stripped) in _HEADER_KEYS:
            index += 1
            continue
        break
    body = "".join(lines[index:])
    if body and not body.endswith("\n"):
        body += "\n"
    return body


def assert_proposal_hash(script: str) -> None:
    expected = _header_value(script, "sha256")
    if not expected:
        raise MigrationError("proposal has no sha256 header")
    actual = hashlib.sha256(proposal_body(script).encode()).hexdigest()
    if actual != expected:
        raise MigrationError("proposal SQL does not match its sha256 header")


def _slug(name: str) -> str:
    text = name.strip().lower()
    text = re.sub(r"[\s\-]+", "_", text)
    text = re.sub(r"[^a-z0-9_]", "", text)
    text = re.sub(r"_+", "_", text).strip("_")
    if not text or not text[0].isalpha():
        raise MigrationError("name must contain a letter; use a short snake_case name")
    if len(text) > 80:
        text = text[:80].rstrip("_")
    return text


def _one_line(value: str, label: str) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    if not text:
        raise MigrationError(f"{label} is required")
    if len(text) > 500:
        text = text[:500].rstrip() + "..."
    return text


def _allocate(slug: str) -> tuple[str, Path]:
    files = list_migration_files()
    if files:
        width = max(3, max(len(version) for version, _, _ in files))
        number = max(int(version) for version, _, _ in files) + 1
    else:
        width, number = 3, 1
    version = f"{number:0{width}d}"
    path = migrations_dir() / f"{version}_{slug}.sql"
    if path.exists():
        raise MigrationError(f"{path.name} already exists")
    return version, path


def _render(*, status: str, classification: Classification, reason: str, up_sql: str) -> str:
    body = up_sql.strip() + "\n"
    digest = hashlib.sha256(body.encode()).hexdigest()
    return (
        f"-- status: {status}\n"
        f"-- classification: {classification.kind}\n"
        f"-- reason: {reason}\n"
        f"-- sha256: {digest}\n"
        f"{body}"
    )


def _write_atomic(path: Path, body: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(body)
        tmp.replace(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _resolve(migration_id: str) -> tuple[str, str, Path]:
    key = migration_id.strip()
    if key.endswith(".sql"):
        key = key[:-4]
    if not key:
        raise MigrationError("migration id is required")
    files = list_migration_files()
    by_version = {version: (version, name, path) for version, name, path in files}
    by_stem = {path.stem: (version, name, path) for version, name, path in files}
    if key in by_version:
        return by_version[key]
    if key in by_stem:
        return by_stem[key]
    by_name = [item for item in files if item[1] == key]
    if len(by_name) == 1:
        return by_name[0]
    if len(by_name) > 1:
        raise MigrationError(f"name {key} matches more than one migration; pass the version")
    raise MigrationError(f"no migration {migration_id}")


def _recorded_versions() -> set[str]:
    try:
        with connect(migrator_database_url()) as conn:
            return applied_versions(conn)
    except MigrationError:
        raise
    except Exception as exc:
        raise MigrationError(short_db_failure(exc, "list")) from None


def _set_status(script: str, status: str) -> str:
    lines = script.splitlines(keepends=True)
    pattern = re.compile(r"^--\s*status:\s*\w+\s*$", re.IGNORECASE)
    for index, line in enumerate(lines):
        if pattern.match(line.strip()):
            newline = "\n" if line.endswith("\n") else ""
            lines[index] = f"-- status: {status}{newline}"
            return "".join(lines)
    return f"-- status: {status}\n{script}"


def _header_key(stripped: str) -> str | None:
    match = re.match(r"^--\s*([A-Za-z0-9_]+)\s*:", stripped)
    if not match:
        return None
    return match.group(1).lower()


def _header_value(script: str, key: str) -> str | None:
    if key not in _HEADER_KEYS:
        return None
    pattern = re.compile(rf"^--\s*{re.escape(key)}:\s*(.*)$", re.IGNORECASE)
    for line in script.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = pattern.match(stripped)
        if match:
            value = match.group(1).strip()
            return value or None
        if not stripped.startswith("--"):
            return None
    return None


def _take_backup() -> str:
    """pg_dump -Fc of the migrator URL into backups/. Host cron still uses db/backup.sh."""
    pg_dump = shutil.which("pg_dump")
    if pg_dump is None:
        raise MigrationError("pg_dump is not on PATH; refusing to apply without a snapshot")
    out_dir = repo_root() / "backups"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    dest = out_dir / f"finance-{stamp}.dump"
    if dest.exists():
        dest = out_dir / f"finance-{stamp}-{os.getpid()}.dump"
    proc = subprocess.run(
        [pg_dump, "-Fc", "-d", migrator_database_url(), "-f", str(dest)],
        check=False,
        capture_output=True,
    )
    if proc.returncode != 0 or not dest.is_file() or dest.stat().st_size == 0:
        dest.unlink(missing_ok=True)
        raise MigrationError("pg_dump failed")
    return _relative(dest)


def _git_detail(proc: subprocess.CompletedProcess[str]) -> str:
    text = (proc.stderr or proc.stdout or "git commit failed").strip()
    if not text or "://" in text or "password" in text.lower():
        return "git commit failed"
    return text.splitlines()[-1][:200]


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo_root()))
    except ValueError:
        return str(path)


@contextmanager
def _lock():
    digest = hashlib.sha256(str(repo_root().resolve()).encode()).hexdigest()[:16]
    path = Path(tempfile.gettempdir()) / f"ft-migration-{digest}.lock"
    path.touch()
    with path.open("a") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
