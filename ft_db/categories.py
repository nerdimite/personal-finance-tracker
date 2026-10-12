"""Parse workspace/CATEGORIES.md and upsert it into accounts.

The file is the source of truth. Sync never deletes a row: a category that
leaves the file is marked inactive. `expense:uncategorized` is seeded and is
not part of the tree, so it stays active.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import psycopg

from ft_db.connect import repo_root

_LINE = re.compile(
    r"^(?P<indent> *)"
    r"(?P<body>"
    r"[a-z][a-z0-9_]*(?:\s*:\s*[a-z][a-z0-9_]*(?:\s*,\s*[a-z][a-z0-9_]*)*)?"
    r"|[a-z][a-z0-9_]*(?:\s*,\s*[a-z][a-z0-9_]*)+"
    r")\s*$"
)


@dataclass(frozen=True)
class CategoryNode:
    path: str
    kind: str
    parent_path: str | None


@dataclass(frozen=True)
class SyncStats:
    upserted: int
    deactivated: int


@dataclass
class _Frame:
    level: int
    path: str
    kind: str


def categories_path() -> Path:
    return repo_root() / "workspace" / "CATEGORIES.md"


def parse_categories(text: str) -> list[CategoryNode]:
    nodes: list[CategoryNode] = []
    frames: list[_Frame] = []
    seen: set[str] = set()

    def add(level: int, name: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            raise ValueError(f"bad category name {name!r}")
        while frames and frames[-1].level >= level:
            frames.pop()
        if level == 0:
            kind = "income" if name == "income" else "expense"
            path = "income" if name == "income" else f"expense:{name}"
            parent = None
        else:
            if not frames or frames[-1].level != level - 1:
                raise ValueError(f"category {name!r} is indented past its parent")
            parent_frame = frames[-1]
            kind = parent_frame.kind
            path = f"{parent_frame.path}:{name}"
            parent = parent_frame.path
        if path in seen:
            raise ValueError(f"duplicate category path {path}")
        seen.add(path)
        nodes.append(CategoryNode(path=path, kind=kind, parent_path=parent))
        frames.append(_Frame(level=level, path=path, kind=kind))
        return path

    for line_no, raw in enumerate(text.splitlines(), start=1):
        body = raw.split("#", 1)[0].rstrip()
        if not body.strip():
            continue
        match = _LINE.match(body)
        if not match:
            if re.search(r"[^a-z0-9_:,\s]", body):
                continue
            raise ValueError(f"CATEGORIES.md:{line_no}: cannot parse {body!r}")
        indent = match.group("indent")
        if len(indent) % 2 != 0:
            raise ValueError(f"CATEGORIES.md:{line_no}: indent must be a multiple of 2 spaces")
        level = len(indent) // 2
        content = match.group("body")
        try:
            if ":" in content:
                left, right = content.split(":", 1)
                add(level, left.strip())
                for child in _names(right):
                    add(level + 1, child)
            else:
                for name in _names(content):
                    add(level, name)
        except ValueError as exc:
            raise ValueError(f"CATEGORIES.md:{line_no}: {exc}") from exc
    return nodes


def _names(text: str) -> list[str]:
    return [part.strip() for part in text.split(",") if part.strip()]


def sync_categories(
    conn: psycopg.Connection,
    path: Path | None = None,
) -> SyncStats:
    nodes = parse_categories((path or categories_path()).read_text())
    paths = [node.path for node in nodes]
    with conn.transaction():
        for node in nodes:
            conn.execute(
                """
                INSERT INTO accounts (path, kind, subtype, is_active, attributes)
                VALUES (%s, %s, 'category', true, '{}'::jsonb)
                ON CONFLICT (path) DO UPDATE
                SET kind = EXCLUDED.kind,
                    subtype = 'category',
                    is_active = true
                """,
                (node.path, node.kind),
            )
        for node in nodes:
            if node.parent_path is None:
                conn.execute(
                    "UPDATE accounts SET parent_id = NULL WHERE path = %s",
                    (node.path,),
                )
            else:
                conn.execute(
                    """
                    UPDATE accounts AS child
                    SET parent_id = parent.id
                    FROM accounts AS parent
                    WHERE child.path = %s
                      AND parent.path = %s
                    """,
                    (node.path, node.parent_path),
                )
        deactivated = conn.execute(
            """
            UPDATE accounts
            SET is_active = false
            WHERE subtype = 'category'
              AND kind IN ('expense', 'income')
              AND path <> 'expense:uncategorized'
              AND NOT (path = ANY(%s))
              AND is_active
            RETURNING path
            """,
            (paths,),
        ).fetchall()
    return SyncStats(upserted=len(nodes), deactivated=len(deactivated))
