"""Apply db/seed.sql (opening equity, uncategorized, income roots)."""

from __future__ import annotations

from pathlib import Path

import psycopg

from ft_db.connect import repo_root
from ft_db.sql_script import exec_script


def seed_path() -> Path:
    return repo_root() / "db" / "seed.sql"


def apply_seed(conn: psycopg.Connection, path: Path | None = None) -> None:
    script = (path or seed_path()).read_text()
    with conn.transaction():
        exec_script(conn, script)
