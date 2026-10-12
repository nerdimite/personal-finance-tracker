"""Keep pytest off the real message sequence."""

from __future__ import annotations


def max_message_seq() -> int:
    from ft_db.connect import connect, owner_database_url

    conn = connect(owner_database_url())
    try:
        row = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM messages").fetchone()
        return int(row[0])
    finally:
        conn.close()


def delete_messages_above(watermark: int) -> None:
    """Delete messages created after `watermark`, and the rows that point at them."""
    from ft_db.connect import connect, owner_database_url

    conn = connect(owner_database_url())
    try:
        seqs = [
            int(row[0])
            for row in conn.execute(
                "SELECT seq FROM messages WHERE seq > %s",
                (watermark,),
            ).fetchall()
        ]
        if not seqs:
            return
        run_ids = [
            int(row[0])
            for row in conn.execute(
                "SELECT id FROM agent_runs WHERE message_seq = ANY(%s)",
                (seqs,),
            ).fetchall()
        ]
        batch_ids = [
            int(row[0])
            for row in conn.execute(
                "SELECT id FROM import_batches WHERE message_seq = ANY(%s)",
                (seqs,),
            ).fetchall()
        ]
        import_row_ids: list[int] = []
        if batch_ids:
            import_row_ids = [
                int(row[0])
                for row in conn.execute(
                    "SELECT id FROM import_rows WHERE batch_id = ANY(%s)",
                    (batch_ids,),
                ).fetchall()
            ]
            conn.execute("DELETE FROM import_rows WHERE batch_id = ANY(%s)", (batch_ids,))
            conn.execute("DELETE FROM import_batches WHERE id = ANY(%s)", (batch_ids,))
        if run_ids:
            conn.execute("DELETE FROM tool_calls WHERE run_id = ANY(%s)", (run_ids,))
            conn.execute("DELETE FROM agent_runs WHERE id = ANY(%s)", (run_ids,))
        conn.execute("DELETE FROM messages WHERE seq = ANY(%s)", (seqs,))
        conn.execute(
            """
            DELETE FROM audit_log
            WHERE ("table" = 'messages' AND row_id = ANY(%s))
               OR ("table" = 'agent_runs' AND row_id = ANY(%s))
               OR ("table" = 'import_batches' AND row_id = ANY(%s))
               OR ("table" = 'import_rows' AND row_id = ANY(%s))
               OR ("table" = 'tool_calls' AND (
                    ("new"->>'run_id') = ANY(%s) OR ("old"->>'run_id') = ANY(%s)
               ))
            """,
            (
                [str(seq) for seq in seqs],
                [str(run_id) for run_id in run_ids],
                [str(batch_id) for batch_id in batch_ids],
                [str(row_id) for row_id in import_row_ids],
                [str(run_id) for run_id in run_ids],
                [str(run_id) for run_id in run_ids],
            ),
        )
        conn.commit()
    finally:
        conn.close()
