"""Approval buttons. Claude is faked. Files stay in tmp; message rows are deleted."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from telegram import Update

from ft_bot.approvals import migration_text, split_telegram
from ft_bot.config import apply_db_host, messages_dir
from ft_bot.models import Incoming
from ft_bot.service import FinanceBot, ReadyJob
from ft_bot.state import BotState
from ft_mcp import MigrationError
from tests.support import delete_messages_above, max_message_seq
from tests.test_ft_bot import RecordingNotifier, _postgres_up, _update, _use_tmp_runtime

_PROPOSAL = {
    "ok": True,
    "id": "901",
    "name": "add_note",
    "status": "pending",
    "classification": "additive",
    "reason": "need a note",
}


def test_split_telegram_stays_within_the_limit() -> None:
    assert split_telegram("short") == ["short"]
    text = "\n".join(f"line {i} " + ("x" * 40) for i in range(200))
    chunks = split_telegram(text)
    assert len(chunks) > 1
    assert all(len(chunk) <= 4096 for chunk in chunks)
    assert "line 0" in chunks[0]
    assert "line 199" in chunks[-1]


def test_long_sql_is_attached_separately() -> None:
    from ft_bot.approvals import MigrationProposal

    proposal = MigrationProposal(
        id="901",
        name="add_note",
        classification="additive",
        reason="need a note",
        sql="ALTER TABLE accounts ADD COLUMN note text;\n" + ("-- pad\n" * 800),
    )
    text, attachment = migration_text(proposal)
    assert attachment is not None
    assert "full SQL attached" in text
    assert len(text) < 4096
    assert "need a note" in text


def test_edit_undo_only_after_a_transaction_change() -> None:
    from ft_bot.service import _reply_keyboard

    def keys(markup) -> list[str]:
        if markup is None:
            return []
        return [button.callback_data for row in markup.inline_keyboard for button in row]

    assert keys(_reply_keyboard(4, [], editable=False, run_id=9)) == []
    assert keys(_reply_keyboard(4, [3], editable=False, run_id=9)) == ["ic:3", "ix:3"]
    assert keys(_reply_keyboard(4, [], editable=True)) == []
    assert keys(_reply_keyboard(4, [], editable=True, run_id=9)) == ["e:000004", "ur:9"]
    assert keys(_reply_keyboard(4, [3], editable=True, run_id=9)) == ["e:000004", "ur:9", "ic:3", "ix:3"]
    assert _reply_keyboard(0, [], editable=True, run_id=9) is None


def test_transaction_audit_row_is_what_enables_edit() -> None:
    if not _postgres_up():
        pytest.skip("postgres unavailable")
    from ft_bot.approvals import ledger_changed
    from ft_db.connect import connect, owner_database_url

    txn_run = 880_000_001
    import_run = 880_000_002
    posting_run = 880_000_003
    marker = "ft-test-edit-buttons"
    conn = connect(owner_database_url())
    try:
        already = conn.execute(
            "SELECT count(*) FROM audit_log WHERE row_id = %s",
            (marker,),
        ).fetchone()
        assert int(already[0]) == 0
        conn.execute(
            """
            INSERT INTO audit_log ("table", row_id, op, run_id)
            VALUES ('transactions', %s, 'UPDATE', %s),
                   ('import_batches', %s, 'INSERT', %s),
                   ('postings', %s, 'INSERT', %s)
            """,
            (marker, str(txn_run), marker, str(import_run), marker, str(posting_run)),
        )
        conn.commit()
        assert ledger_changed(txn_run) is True
        assert ledger_changed(import_run) is False
        assert ledger_changed(posting_run) is True
    finally:
        conn.execute(
            "DELETE FROM audit_log WHERE row_id = %s AND run_id = ANY(%s)",
            (marker, [str(txn_run), str(import_run), str(posting_run)]),
        )
        conn.commit()
        conn.close()


def test_finish_passes_edit_only_when_transactions_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ft_bot.models import RunOutcome

    flags = {"editable": False, "batches": [3]}
    monkeypatch.setattr("ft_bot.service.ledger_changed", lambda run_id: flags["editable"])
    monkeypatch.setattr("ft_bot.service.batches_for_run", lambda run_id: list(flags["batches"]))
    monkeypatch.setattr("ft_bot.service.migrations_to_announce", lambda run_id, announced: [])

    async def process(incoming, *, state, lock) -> RunOutcome:
        return RunOutcome(
            seq=4,
            run_id=880_000_001,
            result_text="noted",
            is_error=False,
            tool_calls=0,
            session_mode="session-id",
            log_path="logs/runs/unused.jsonl",
        )

    bot = FinanceBot(
        allowed="5",
        notifier=RecordingNotifier(),
        state=BotState.load(tmp_path / "state.json"),
        album_quiet=0.01,
        process=process,
    )

    async def scenario() -> None:
        await bot.start_worker()
        await bot._queue.put(ReadyJob(_incoming("what did I spend"), 1))
        await _wait_event(bot, "finish")
        flags["editable"] = True
        flags["batches"] = []
        await bot._queue.put(ReadyJob(_incoming("rent paid"), 2))
        for _ in range(80):
            if sum(event[0] == "finish" for event in bot.notifier.events) >= 2:
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("second finish missing")
        bot._task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await bot._task

    asyncio.run(scenario())
    finishes = [event for event in bot.notifier.events if event[0] == "finish"]
    assert finishes[0][5:7] == ([3], False)
    assert finishes[1][5:7] == ([], True)


def test_edit_waits_for_the_next_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if not _postgres_up():
        pytest.skip("postgres unavailable")
    watermark = max_message_seq()
    try:
        _use_tmp_runtime(monkeypatch, tmp_path)
        state = BotState.load(tmp_path / "state.json")
        bot = FinanceBot(allowed="5", notifier=RecordingNotifier(), state=state, album_quiet=0.01)

        async def scenario() -> None:
            await bot.on_callback(_callback("e:000007"), None)
            assert state.pending_correction_seq == 7
            assert bot._queue.qsize() == 0
            photo = _update(1, 3, 5, photo=[{"file_id": "p", "file_unique_id": "u", "width": 1, "height": 1}])
            await bot.handle_message(photo, download=_png)
            assert state.pending_correction_seq == 7
            await bot.handle_message(_update(2, 4, 5, text="it was 80 not 120"), download=_png)
            assert state.pending_correction_seq is None

        asyncio.run(scenario())
        assert any(event[0] == "reply" and "000007" in event[2] for event in bot.notifier.events)
        assert _folder_contains("Correction for message 000007: it was 80 not 120")
        photo_text = ""
        for path in messages_dir().glob("*/message.md"):
            body = path.read_text()
            if "01_photo.jpg" in body:
                photo_text = body
        assert "Correction for message" not in photo_text
    finally:
        delete_messages_above(watermark)


def test_import_commit_goes_through_fake_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if not _postgres_up():
        pytest.skip("postgres unavailable")
    watermark = max_message_seq()
    try:
        _use_tmp_runtime(monkeypatch, tmp_path)
        script = _install_fake(tmp_path, monkeypatch, _IMPORT_FAKE)
        monkeypatch.setenv("FT_CLAUDE_BIN", str(script))
        apply_db_host()
        monkeypatch.setenv("FT_FAKE_DATABASE_URL", os.environ.get("OWNER_DATABASE_URL", ""))
        bot = _bot(tmp_path)

        async def scenario() -> None:
            await bot.start_worker()
            await bot._queue.put(ReadyJob(_incoming("please import"), 1))
            finish = await _wait_event(bot, "finish")
            assert finish[5], "expected commit buttons"
            batch_id = finish[5][0]
            await bot.on_callback(_callback(f"ic:{batch_id}"), None)
            await _wait_for(lambda: _folder_contains("commit it now"))
            bot._task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await bot._task

        asyncio.run(scenario())
        assert _folder_contains("commit it now (status→committed)")
    finally:
        delete_messages_above(watermark)


def test_migration_apply_and_reject_skip_the_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not _postgres_up():
        pytest.skip("postgres unavailable")
    watermark = max_message_seq()
    applied: list[tuple[str, str]] = []
    try:
        _use_tmp_runtime(monkeypatch, tmp_path)
        _pending_file(tmp_path)
        monkeypatch.setattr("ft_db.migrate.migrations_dir", lambda: tmp_path / "migrations")
        script = _install_fake(tmp_path, monkeypatch, _MIGRATION_FAKE)
        monkeypatch.setenv("FT_CLAUDE_BIN", str(script))
        monkeypatch.setattr(
            "ft_bot.service.apply_migration",
            lambda migration_id, *, approved_by="cli": applied.append((migration_id, approved_by))
            or {"ok": True, "id": migration_id, "name": "add_note", "backup": "backups/synthetic.dump"},
        )
        monkeypatch.setattr(
            "ft_bot.service.reject_migration",
            lambda migration_id: {"ok": True, "id": migration_id, "name": "add_note", "status": "rejected"},
        )
        apply_db_host()
        monkeypatch.setenv("FT_FAKE_DATABASE_URL", os.environ.get("OWNER_DATABASE_URL", ""))
        bot = _bot(tmp_path)

        async def scenario() -> None:
            await bot.start_worker()
            await bot._queue.put(ReadyJob(_incoming("propose a column"), 1))
            await _wait_event(bot, "migration")
            assert bot.state.announced_migrations == ["901"]
            await bot.on_callback(_callback("ma:901"), None)
            for _ in range(80):
                if applied:
                    break
                await asyncio.sleep(0.05)
            assert applied == [("901", "telegram")]
            await bot.on_callback(_callback("mr:901"), None)
            for _ in range(80):
                if any(
                    event[0] == "reply" and str(event[2]).startswith("Rejected migration 901")
                    for event in bot.notifier.events
                ):
                    break
                await asyncio.sleep(0.05)
            replies = [event[2] for event in bot.notifier.events if event[0] == "reply"]
            assert any(text.startswith("Applied migration 901") for text in replies)
            assert any(text.startswith("Rejected migration 901") for text in replies)
            bot._task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await bot._task

        asyncio.run(scenario())
    finally:
        delete_messages_above(watermark)


def test_failed_apply_tells_the_agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if not _postgres_up():
        pytest.skip("postgres unavailable")
    watermark = max_message_seq()
    try:
        _use_tmp_runtime(monkeypatch, tmp_path)
        _pending_file(tmp_path)
        monkeypatch.setattr("ft_db.migrate.migrations_dir", lambda: tmp_path / "migrations")
        script = _install_fake(tmp_path, monkeypatch, _MIGRATION_FAKE)
        monkeypatch.setenv("FT_CLAUDE_BIN", str(script))

        def boom(migration_id, *, approved_by="cli"):
            raise MigrationError("column note already exists")

        monkeypatch.setattr("ft_bot.service.apply_migration", boom)
        apply_db_host()
        monkeypatch.setenv("FT_FAKE_DATABASE_URL", os.environ.get("OWNER_DATABASE_URL", ""))
        bot = _bot(tmp_path)

        async def scenario() -> None:
            await bot.start_worker()
            await bot._queue.put(ReadyJob(_incoming("propose a column"), 1))
            await _wait_event(bot, "migration")
            await bot.on_callback(_callback("ma:901"), None)
            await _wait_for(lambda: _folder_contains("column note already exists"))
            bot._task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await bot._task

        asyncio.run(scenario())
        assert _folder_contains("Do not retry the migration")
    finally:
        delete_messages_above(watermark)


def _bot(tmp_path: Path) -> FinanceBot:
    return FinanceBot(
        allowed="5",
        notifier=RecordingNotifier(),
        state=BotState.load(tmp_path / "state.json"),
        album_quiet=0.01,
    )


def _incoming(text: str) -> Incoming:
    return Incoming(
        chat_id=5,
        telegram_message_ids=[42],
        text=text,
        caption=None,
        received_at=datetime.now(timezone.utc),
    )


def _pending_file(tmp_path: Path) -> None:
    root = tmp_path / "migrations"
    root.mkdir()
    (root / "901_add_note.sql").write_text(
        "-- status: pending\n"
        "-- classification: additive\n"
        "-- reason: need a note\n"
        "ALTER TABLE accounts ADD COLUMN note text;\n"
    )


def _install_fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> Path:
    script = tmp_path / "fake-claude"
    # The proposal is a JSON string literal, so `true` stays inside quotes.
    literal = json.dumps(json.dumps(_PROPOSAL))
    script.write_text(body.format(python=sys.executable, proposal=literal))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("FT_FAKE_CLAUDE_LOG", str(tmp_path / "fake-log.jsonl"))
    (tmp_path / "fake-log.jsonl").write_text("")
    return script


def _folder_contains(snippet: str) -> bool:
    root = messages_dir()
    if not root.is_dir():
        return False
    for path in root.glob("*/message.md"):
        if snippet in path.read_text():
            return True
    return False


async def _wait_event(bot: FinanceBot, kind: str):
    for _ in range(80):
        for event in bot.notifier.events:
            if event[0] == kind:
                return event
        await asyncio.sleep(0.05)
    raise AssertionError(kind)


async def _wait_for(predicate) -> None:
    for _ in range(80):
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("timed out")


def _callback(data: str) -> Update:
    payload = {
        "update_id": 9,
        "callback_query": {
            "id": "cq",
            "data": data,
            "chat_instance": "1",
            "from": {"id": 5, "is_bot": False, "first_name": "T"},
            "message": {
                "message_id": 9,
                "date": 1_700_000_000,
                "chat": {"id": 5, "type": "private"},
                "text": "hi",
            },
        },
    }
    return Update.de_json(payload, bot=None)


async def _png(file_id: str) -> bytes:
    return b"\x89PNG\r\n\x1a\n"


_IMPORT_FAKE = """#!{python}
import json, os, sys
import psycopg

run_id = os.environ["APP_RUN_ID"]
with psycopg.connect(os.environ["FT_FAKE_DATABASE_URL"]) as conn:
    seq = conn.execute(
        "SELECT message_seq FROM agent_runs WHERE id = %s",
        (int(run_id),),
    ).fetchone()[0]
    conn.execute("SELECT set_config('app.run_id', %s, true)", (run_id,))
    conn.execute(
        \"\"\"
        INSERT INTO import_batches (message_seq, status, file_path)
        VALUES (%s, 'summarized', 'synthetic.csv')
        \"\"\",
        (seq,),
    )
args = sys.argv
if "--session-id" in args:
    session = args[args.index("--session-id") + 1]
else:
    session = args[args.index("--resume") + 1]
event = {{
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "duration_ms": 5,
    "num_turns": 1,
    "result": "3 new, 1 duplicate",
    "total_cost_usd": 0,
    "session_id": session,
}}
sys.stdout.write(json.dumps(event) + "\\n")
"""


_MIGRATION_FAKE = """#!{python}
import json, os, sys

args = sys.argv
if "--session-id" in args:
    session = args[args.index("--session-id") + 1]
else:
    session = args[args.index("--resume") + 1]
proposal = {proposal}
events = [
    {{"type": "system", "subtype": "init", "session_id": session, "memory_paths": None}},
    {{
        "type": "assistant",
        "timestamp": "2026-10-11T12:00:00+00:00",
        "message": {{
            "content": [{{
                "type": "tool_use",
                "id": "t1",
                "name": "mcp__finance__propose_migration",
                "input": {{"name": "add_note", "up_sql": "select 1", "reason": "need a note"}},
            }}]
        }},
    }},
    {{
        "type": "user",
        "timestamp": "2026-10-11T12:00:01+00:00",
        "message": {{
            "content": [{{
                "type": "tool_result",
                "tool_use_id": "t1",
                "content": proposal,
                "is_error": False,
            }}]
        }},
    }},
    {{
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "duration_ms": 5,
        "num_turns": 1,
        "result": "proposed",
        "total_cost_usd": 0,
        "session_id": session,
    }},
]
for event in events:
    sys.stdout.write(json.dumps(event) + "\\n")
"""
