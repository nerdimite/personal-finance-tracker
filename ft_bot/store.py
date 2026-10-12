"""Owner-role writes for messages, agent_runs, and tool_calls.

`agent_runs` has no status column. A row inserted before `claude` with a null
duration and a null result is the in-progress run. The bot updates that row
after it parses the log.
"""

from __future__ import annotations

import hashlib
import shutil
from datetime import datetime
from pathlib import Path

from psycopg.types.json import Jsonb

from ft_bot.config import messages_dir
from ft_bot.folders import display_text, write_message_folder
from ft_bot.models import Incoming
from ft_bot.splitwise import SplitwiseItem
from ft_bot.trace import ParsedRun, ParsedTool
from ft_db.connect import connect, owner_database_url

_LOCK_KEY = 91403103


def owner_conn():
    return connect(owner_database_url())


def max_folder_seq() -> int:
    root = messages_dir()
    if not root.is_dir():
        return 0
    best = 0
    for path in root.iterdir():
        if path.is_dir() and path.name.isdigit():
            best = max(best, int(path.name))
    return best


def insert_message(incoming: Incoming, session_id: str) -> int:
    """Allocate the next seq, insert the messages row, and write the folder."""
    root = messages_dir()
    root.mkdir(parents=True, exist_ok=True)
    folder = None
    conn = owner_conn()
    try:
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
            row = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM messages").fetchone()
            seq = max(int(row[0]), max_folder_seq()) + 1
            telegram_id = incoming.telegram_message_ids[0] if incoming.telegram_message_ids else None
            folder_path = f"messages/{seq:06d}"
            conn.execute(
                """
                INSERT INTO messages (
                  seq, telegram_message_id, received_at, text, folder_path, session_id
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    seq,
                    telegram_id,
                    incoming.received_at,
                    display_text(incoming) or None,
                    folder_path,
                    session_id,
                ),
            )
            folder = write_message_folder(root, seq, incoming)
        return seq
    except Exception:
        if folder is not None and folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
        raise
    finally:
        conn.close()


def insert_run(message_seq: int, session_id: str, log_path: str, started_at: datetime) -> int:
    """Insert the run row before Claude starts. Returns agent_runs.id."""
    conn = owner_conn()
    try:
        with conn.transaction():
            row = conn.execute(
                """
                INSERT INTO agent_runs (message_seq, session_id, started_at, log_path)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (message_seq, session_id, started_at, log_path),
            ).fetchone()
        return int(row[0])
    finally:
        conn.close()


def finish_run(run_id: int, session_id: str | None, parsed: ParsedRun) -> None:
    """Fill in the run and its tool calls. Sets app.run_id for the audit trigger."""
    conn = owner_conn()
    try:
        with conn.transaction():
            conn.execute("SELECT set_config('app.run_id', %s, true)", (str(run_id),))
            conn.execute(
                """
                UPDATE agent_runs
                SET duration_ms = %s,
                    num_turns = %s,
                    cost_usd = %s,
                    is_error = %s,
                    result_text = %s,
                    session_id = %s
                WHERE id = %s
                """,
                (
                    parsed.duration_ms,
                    parsed.num_turns,
                    parsed.cost_usd,
                    parsed.is_error,
                    parsed.result_text,
                    session_id or parsed.session_id,
                    run_id,
                ),
            )
            for tool in parsed.tool_calls:
                _insert_tool(conn, run_id, tool)
    finally:
        conn.close()


def _insert_tool(conn, run_id: int, tool: ParsedTool) -> None:
    conn.execute(
        """
        INSERT INTO tool_calls (run_id, seq, tool_name, input, output, is_error, duration_ms)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            run_id,
            tool.seq,
            tool.name,
            Jsonb(tool.input),
            tool.output,
            tool.is_error,
            tool.duration_ms,
        ),
    )


def interrupted_message_seqs() -> list[int]:
    """Runs left in progress (null duration and null result) after a crash or restart."""
    conn = owner_conn()
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT message_seq
            FROM agent_runs
            WHERE duration_ms IS NULL
              AND result_text IS NULL
              AND message_seq IS NOT NULL
            ORDER BY message_seq
            """
        ).fetchall()
    finally:
        conn.close()
    return [int(row[0]) for row in rows]


def fill_attachment_hashes(seq: int) -> None:
    """Fill attachments.sha256 for files in this message folder. The agent leaves it null."""
    folder = messages_dir() / f"{seq:06d}"
    if not folder.is_dir():
        return
    hashes: dict[str, str] = {}
    for path in folder.iterdir():
        if not path.is_file() or path.name in {"message.md", "meta.json"}:
            continue
        hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not hashes:
        return
    conn = owner_conn()
    try:
        with conn.transaction():
            rows = conn.execute(
                """
                SELECT id, path
                FROM attachments
                WHERE message_seq = %s AND sha256 IS NULL
                """,
                (seq,),
            ).fetchall()
            for attachment_id, stored in rows:
                name = Path(stored or "").name
                digest = hashes.get(name)
                if not digest:
                    continue
                conn.execute(
                    "UPDATE attachments SET sha256 = %s WHERE id = %s AND sha256 IS NULL",
                    (digest, attachment_id),
                )
    finally:
        conn.close()


def message_seqs_without_runs() -> list[int]:
    conn = owner_conn()
    try:
        rows = conn.execute(
            """
            SELECT m.seq
            FROM messages m
            WHERE NOT EXISTS (
              SELECT 1 FROM agent_runs r WHERE r.message_seq = m.seq
            )
            ORDER BY m.seq
            """
        ).fetchall()
    finally:
        conn.close()
    return [int(row[0]) for row in rows]


def latest_run() -> tuple | None:
    conn = owner_conn()
    try:
        return conn.execute(
            """
            SELECT message_seq, duration_ms, cost_usd, is_error, started_at
            FROM agent_runs
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    finally:
        conn.close()


def run_row(run_id: int) -> tuple | None:
    conn = owner_conn()
    try:
        return conn.execute(
            """
            SELECT id, message_seq, session_id, duration_ms, num_turns, cost_usd,
                   is_error, result_text, log_path
            FROM agent_runs
            WHERE id = %s
            """,
            (run_id,),
        ).fetchone()
    finally:
        conn.close()


def run_changed_ledger(run_id: int) -> bool:
    """True when this run inserted, updated, or deleted a transactions or postings row."""
    conn = owner_conn()
    try:
        row = conn.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM audit_log
                WHERE "table" IN ('transactions', 'postings')
                  AND run_id = %s
            )
            """,
            (str(run_id),),
        ).fetchone()
    finally:
        conn.close()
    return bool(row[0])


def summarized_batch_ids(run_id: int) -> list[int]:
    """Batches this run left in `summarized`, based on the latest audit row."""
    conn = owner_conn()
    try:
        rows = conn.execute(
            """
            SELECT b.id
            FROM import_batches b
            WHERE b.status = 'summarized'
              AND (
                SELECT a.run_id
                FROM audit_log a
                WHERE a."table" = 'import_batches'
                  AND a.row_id = b.id::text
                ORDER BY a.id DESC
                LIMIT 1
              ) = %s
            ORDER BY b.id
            """,
            (str(run_id),),
        ).fetchall()
    finally:
        conn.close()
    return [int(row[0]) for row in rows]


def migration_tool_outputs(run_id: int) -> list[str]:
    conn = owner_conn()
    try:
        rows = conn.execute(
            """
            SELECT output
            FROM tool_calls
            WHERE run_id = %s
              AND tool_name IN ('mcp__finance__propose_migration', 'propose_migration')
              AND NOT is_error
            ORDER BY seq
            """,
            (run_id,),
        ).fetchall()
    finally:
        conn.close()
    return [row[0] or "" for row in rows]


def list_pending_splitwise() -> list[SplitwiseItem]:
    """Pending queue rows, oldest first."""
    conn = owner_conn()
    try:
        rows = conn.execute(
            """
            SELECT id, description, total_amount, paid_by, split_method, shares
            FROM splitwise_queue
            WHERE status = 'pending'
            ORDER BY created_at, id
            """
        ).fetchall()
    finally:
        conn.close()
    items: list[SplitwiseItem] = []
    for row in rows:
        shares = row[5] if isinstance(row[5], list) else []
        items.append(
            SplitwiseItem(
                id=int(row[0]),
                description=row[1] or "",
                total_amount=row[2],
                paid_by=row[3] or "me",
                split_method=row[4] or "equal",
                shares=tuple(share for share in shares if isinstance(share, dict)),
            )
        )
    return items


def set_splitwise_status(queue_id: int, status: str) -> str:
    """Mark a pending row added or cancelled. Returns the new status, or 'unchanged'."""
    if status not in {"added", "cancelled"}:
        raise ValueError(status)
    conn = owner_conn()
    try:
        with conn.transaction():
            if status == "added":
                row = conn.execute(
                    """
                    UPDATE splitwise_queue
                    SET status = 'added', added_at = now()
                    WHERE id = %s AND status = 'pending'
                    RETURNING id
                    """,
                    (queue_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    UPDATE splitwise_queue
                    SET status = 'cancelled'
                    WHERE id = %s AND status = 'pending'
                    RETURNING id
                    """,
                    (queue_id,),
                ).fetchone()
    finally:
        conn.close()
    return status if row else "unchanged"


def splitwise_insert_count(run_id: int) -> int:
    """Queue rows this run inserted, from audit_log. Updates do not count."""
    conn = owner_conn()
    try:
        row = conn.execute(
            """
            SELECT COUNT(DISTINCT row_id)
            FROM audit_log
            WHERE "table" = 'splitwise_queue'
              AND op = 'INSERT'
              AND run_id = %s
            """,
            (str(run_id),),
        ).fetchone()
    finally:
        conn.close()
    return int(row[0])


def tool_call_count(run_id: int) -> int:
    conn = owner_conn()
    try:
        row = conn.execute(
            "SELECT count(*) FROM tool_calls WHERE run_id = %s",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()
    return int(row[0])
