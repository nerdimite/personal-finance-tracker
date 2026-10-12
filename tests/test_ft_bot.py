"""Bot tests. The Claude CLI is faked; one test uses Postgres when it is up."""

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

from ft_bot.access import chat_allowed, is_builtin_command, parse_callback
from ft_bot.config import apply_db_host, rewrite_database_url
from ft_bot.folders import write_message_folder
from ft_bot.models import Attachment, Incoming, RunOutcome
from ft_bot.pipeline import process_incoming
from ft_bot.runner import build_command
from ft_bot.service import FinanceBot
from ft_bot.state import BotState
from ft_bot.store import run_row, tool_call_count
from ft_bot.trace import parse_stream

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8


class RecordingNotifier:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    async def ack(self, chat_id: int, importing: bool) -> int:
        self.events.append(("ack", chat_id, importing))
        return len(self.events)

    async def finish(self, chat_id, ack_id, text: str, seq: int, batch_ids=None, editable: bool = False, run_id: int = 0) -> None:
        self.events.append(("finish", chat_id, ack_id, text, seq, list(batch_ids or []), bool(editable), run_id))

    async def reply(self, chat_id: int, text: str) -> None:
        self.events.append(("reply", chat_id, text))

    async def splitwise_item(self, chat_id: int, text: str, queue_id: int) -> None:
        self.events.append(("splitwise", chat_id, text, queue_id))

    async def migration_prompt(self, chat_id: int, proposal) -> None:
        self.events.append(("migration", chat_id, proposal.id))


def test_chat_allow_list_fails_closed() -> None:
    assert chat_allowed(5, "5")
    assert not chat_allowed(6, "5")
    assert not chat_allowed(None, "5")
    assert not chat_allowed(5, "")
    assert not chat_allowed(5, "  ")


def test_builtin_commands_and_callbacks() -> None:
    assert is_builtin_command("/new")
    assert is_builtin_command("/status@FinanceBot")
    assert is_builtin_command("/splitwise")
    assert is_builtin_command("/splitwise@FinanceBot")
    assert not is_builtin_command("/newsession")
    assert not is_builtin_command("hello")
    assert parse_callback("e:000042") == ("edit", 42)
    assert parse_callback("ur:42") == ("undo_run", 42)
    assert parse_callback("u:000042") is None
    assert parse_callback("ic:12") == ("import_commit", 12)
    assert parse_callback("ix:12") == ("import_discard", 12)
    assert parse_callback("ma:003") == ("migration_apply", "003")
    assert parse_callback("mr:003") == ("migration_reject", "003")
    assert parse_callback("edit:1") is None
    assert parse_callback("e:0") is None
    assert parse_callback("ic:0") is None
    assert parse_callback("ma:../001") is None
    assert parse_callback("sa:7") == ("splitwise_added", 7)
    assert parse_callback("sx:7") == ("splitwise_cancel", 7)
    assert parse_callback("sa:0") is None
    assert parse_callback("sx:nope") is None


def test_rewrite_database_url_keeps_the_password() -> None:
    url = "postgresql://owner:secret@127.0.0.1:5433/finance"
    assert (
        rewrite_database_url(url, "postgres", "5432")
        == "postgresql://owner:secret@postgres:5432/finance"
    )


def test_claude_command_matches_the_ticket(tmp_path: Path) -> None:
    command = build_command("hello", "11111111-1111-1111-1111-111111111111", "session-id")
    assert "--bare" not in command
    assert command[command.index("--setting-sources") + 1] == "project"
    assert "--strict-mcp-config" in command
    assert command[command.index("--mcp-config") + 1].endswith("bot/agent/mcp.json")
    assert "--settings" in command
    assert command[command.index("--tools") + 1] == "Read,Write,Edit,Glob,Grep"
    allowed = command[command.index("--allowedTools") + 1]
    assert "mcp__finance__*" in allowed.split(",")
    assert "Bash" not in allowed
    assert "Bash(lit *)" not in allowed
    assert "Read(" in allowed
    assert "MEMORY.md" in allowed
    assert "CATEGORIES.md" in allowed
    from ft_bot.runner import harness_settings

    denied = harness_settings(tmp_path)["permissions"]["deny"]
    assert "Bash" in denied
    assert "Read(//proc/**)" in denied
    assert any(rule.startswith("Edit(") and rule.endswith("/CLAUDE.md)") for rule in denied)
    assert any("/.git/**)" in rule and rule.startswith("Edit(") for rule in denied)
    assert any(rule.startswith("Read(") and rule.endswith("/db/**)") for rule in denied)
    assert command[command.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in command
    assert command[command.index("--session-id") + 1] == "11111111-1111-1111-1111-111111111111"
    resumed = build_command("hello", "11111111-1111-1111-1111-111111111111", "resume")
    assert resumed[resumed.index("--resume") + 1] == "11111111-1111-1111-1111-111111111111"
    assert "--session-id" not in resumed


def test_parse_stream_pairs_tool_calls() -> None:
    raw = "\n".join(
        [
            json.dumps(
                {
                    "type": "system",
                    "subtype": "init",
                    "session_id": "s",
                    "memory_paths": None,
                }
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "2026-10-11T12:00:00+00:00",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "t1",
                                "name": "Read",
                                "input": {"file_path": "messages/000001/message.md"},
                            }
                        ]
                    },
                }
            ),
            json.dumps(
                {
                    "type": "user",
                    "timestamp": "2026-10-11T12:00:01+00:00",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "t1",
                                "content": "hello",
                                "is_error": False,
                            }
                        ]
                    },
                }
            ),
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "duration_ms": 10,
                    "num_turns": 1,
                    "result": "pong",
                    "total_cost_usd": 0.001,
                    "session_id": "s",
                }
            ),
        ]
    )
    parsed = parse_stream(raw)
    assert parsed.result_text == "pong"
    assert parsed.duration_ms == 10
    assert parsed.num_turns == 1
    assert parsed.cost_usd is not None
    assert len(parsed.tool_calls) == 1
    tool = parsed.tool_calls[0]
    assert tool.name == "Read"
    assert tool.seq == 1
    assert tool.output == "hello"
    assert tool.duration_ms == 1000
    assert tool.is_error is False
    assert parsed.memory_paths is None


def test_message_folder_text_and_photo(tmp_path: Path) -> None:
    received = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
    incoming = Incoming(
        chat_id=7,
        telegram_message_ids=[10, 11],
        text="hello",
        caption="receipt",
        received_at=received,
        media_group_id="g1",
        attachments=[
            Attachment("photo.jpg", _PNG, "photo"),
            Attachment("../stmt.pdf", b"pdf", "document"),
        ],
    )
    folder = write_message_folder(tmp_path, 4, incoming)
    assert (folder / "01_photo.jpg").read_bytes() == _PNG
    assert (folder / "02_stmt.pdf").read_bytes() == b"pdf"
    body = (folder / "message.md").read_text()
    assert "# Message 000004" in body
    assert "hello" in body
    assert "receipt" in body
    meta = json.loads((folder / "meta.json").read_text())
    assert meta["telegram_message_ids"] == [10, 11]
    assert meta["attachments"][1]["file"] == "02_stmt.pdf"


def test_session_rolls_over_and_new(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = BotState(
        path=path,
        session_id="old",
        session_date="2000-01-01",
        established=True,
    )
    state.roll_if_needed("2026-10-11")
    assert state.session_id != "old"
    assert state.established is False
    assert state.session_date == "2026-10-11"
    first = state.session_id
    mode, sid = state.claim_for_run("2026-10-11")
    assert mode == "session-id"
    assert sid == first
    state.note_finished(first, first, got_session=True)
    mode, sid = state.claim_for_run("2026-10-11")
    assert mode == "resume"
    assert sid == first
    new_id = state.new_session()
    assert new_id != first
    assert state.established is False
    loaded = BotState.load(path)
    assert loaded.session_id == new_id


def test_disallowed_chat_does_not_queue() -> None:
    bot = FinanceBot(allowed="1", notifier=RecordingNotifier(), state=_Scratch(), album_quiet=0.05)
    update = _update(1, 1, 2, text="nope")

    async def scenario() -> None:
        await bot.handle_message(update, download=_unused_download)

    asyncio.run(scenario())
    assert bot._queue.qsize() == 0
    assert bot.notifier.events == []


def test_album_is_one_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if not _postgres_up():
        pytest.skip("postgres unavailable")
    from tests.support import delete_messages_above, max_message_seq

    _use_tmp_runtime(monkeypatch, tmp_path)
    watermark = max_message_seq()
    seen: list[Incoming] = []

    async def process(incoming: Incoming, *, state, lock) -> RunOutcome:
        seen.append(incoming)
        return RunOutcome(
            seq=1,
            run_id=1,
            result_text="ok",
            is_error=False,
            tool_calls=0,
            session_mode="session-id",
            log_path="logs/runs/000001.jsonl",
        )

    bot = FinanceBot(
        allowed="5",
        notifier=RecordingNotifier(),
        state=BotState.load(tmp_path / "state.json"),
        album_quiet=0.05,
        process=process,
    )
    first = _update(
        1,
        10,
        5,
        media_group_id="album",
        caption="receipt",
        photo=[{"file_id": "p1", "file_unique_id": "u1", "width": 1, "height": 1}],
    )
    second = _update(
        2,
        11,
        5,
        media_group_id="album",
        photo=[{"file_id": "p2", "file_unique_id": "u2", "width": 2, "height": 2}],
    )

    async def scenario() -> None:
        await bot.start_worker()
        await bot.handle_message(first, download=_bytes_download)
        await bot.handle_message(second, download=_bytes_download)
        for _ in range(40):
            if seen and any(event[0] == "finish" for event in bot.notifier.events):
                break
            await asyncio.sleep(0.05)
        bot._task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await bot._task

    try:
        asyncio.run(scenario())
        assert len(seen) == 1
        assert seen[0].telegram_message_ids == [10, 11]
        assert seen[0].file_count == 2
        assert seen[0].caption == "receipt"
        assert seen[0].stored_seq is not None
        assert bot.notifier.events[0] == ("ack", 5, True)
        assert bot.notifier.events[-1][0] == "finish"
    finally:
        delete_messages_above(watermark)


def test_pipeline_with_fake_claude_writes_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if not _postgres_up():
        pytest.skip("postgres unavailable")
    _use_tmp_runtime(monkeypatch, tmp_path)
    from tests.support import delete_messages_above, max_message_seq

    watermark = max_message_seq()
    script = tmp_path / "fake-claude"
    log_path = tmp_path / "fake-log.jsonl"
    script.write_text(_FAKE_CLAUDE.format(python=sys.executable))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("FT_CLAUDE_BIN", str(script))
    monkeypatch.setenv("FT_FAKE_CLAUDE_LOG", str(log_path))
    apply_db_host()
    monkeypatch.setenv("FT_FAKE_DATABASE_URL", os.environ["OWNER_DATABASE_URL"])
    state = BotState.load(tmp_path / "state.json")
    lock = asyncio.Lock()
    text = Incoming(
        chat_id=None,
        telegram_message_ids=[8_000_000_001],
        text="unit pipeline",
        caption=None,
        received_at=datetime.now(timezone.utc),
        prompt_override="Reply about {folder}.",
    )
    photo = Incoming(
        chat_id=None,
        telegram_message_ids=[8_000_000_002],
        text=None,
        caption="pic",
        received_at=datetime.now(timezone.utc),
        attachments=[Attachment("photo.jpg", _PNG, "photo")],
        prompt_override="Reply about {folder}.",
    )

    async def scenario():
        first = await process_incoming(text, state=state, lock=lock)
        second = await process_incoming(photo, state=state, lock=lock)
        return first, second

    first: RunOutcome | None = None
    second: RunOutcome | None = None
    try:
        first, second = asyncio.run(scenario())
        records = [json.loads(line) for line in log_path.read_text().splitlines()]
        assert len(records) == 2
        assert records[0]["app_run_id"] == str(first.run_id)
        assert records[0]["row_before"] == [None, None]
        assert records[0]["auto_memory"] == "1"
        assert "--session-id" in records[0]["argv"]
        assert "--bare" not in records[0]["argv"]
        assert "--resume" in records[1]["argv"]
        assert records[1]["app_run_id"] == str(second.run_id)
        for outcome, label in ((first, "unit pipeline"), (second, "pic")):
            row = run_row(outcome.run_id)
            assert row is not None
            assert row[7] == "pong"
            assert row[6] is False
            assert tool_call_count(outcome.run_id) == 1
            from ft_bot.config import messages_dir, runs_dir

            folder = messages_dir() / f"{outcome.seq:06d}"
            assert label in (folder / "message.md").read_text()
            assert (folder / "meta.json").is_file()
            assert (runs_dir() / f"{outcome.seq:06d}.jsonl").is_file()
            assert folder.is_relative_to(tmp_path)
        from ft_bot.config import messages_dir as messages_root

        photo_md = (messages_root() / f"{second.seq:06d}" / "message.md").read_text()
        assert "image: 01_photo.jpg (not parsed)" in photo_md
        assert (messages_root() / f"{second.seq:06d}" / "01_photo.jpg").is_file()
    finally:
        delete_messages_above(watermark)


def _use_tmp_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Folders and run logs stay out of the real workspace."""
    workspace = tmp_path / "workspace"
    logs = tmp_path / "logs"
    workspace.mkdir()
    logs.mkdir()
    monkeypatch.setenv("FT_WORKSPACE", str(workspace))
    monkeypatch.setenv("FT_LOGS", str(logs))


def _postgres_up() -> bool:
    try:
        apply_db_host()
        from ft_db.connect import connect, owner_database_url

        conn = connect(owner_database_url())
        conn.execute("SELECT 1")
        conn.close()
    except Exception:
        return False
    return True


def _update(update_id: int, message_id: int, chat_id: int, **message) -> Update:
    payload = {
        "update_id": update_id,
        "message": {
            "message_id": message_id,
            "date": 1_700_000_000,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": chat_id, "is_bot": False, "first_name": "T"},
            **message,
        },
    }
    return Update.de_json(payload, bot=None)


async def _unused_download(file_id: str) -> bytes:
    raise AssertionError(file_id)


async def _bytes_download(file_id: str) -> bytes:
    return _PNG + file_id.encode()


class _Scratch:
    def seq_for(self, telegram_id: int):
        return None


_FAKE_CLAUDE = """#!{python}
import json, os, sys
import psycopg

log_path = os.environ["FT_FAKE_CLAUDE_LOG"]
run_id = os.environ["APP_RUN_ID"]
with psycopg.connect(os.environ["FT_FAKE_DATABASE_URL"]) as conn:
    row = conn.execute(
        "SELECT result_text, duration_ms FROM agent_runs WHERE id = %s",
        (int(run_id),),
    ).fetchone()
record = {{
    "argv": sys.argv,
    "app_run_id": run_id,
    "row_before": [row[0], row[1]] if row else None,
    "auto_memory": os.environ.get("CLAUDE_CODE_DISABLE_AUTO_MEMORY"),
}}
with open(log_path, "a") as handle:
    handle.write(json.dumps(record) + "\\n")
args = sys.argv
if "--session-id" in args:
    session = args[args.index("--session-id") + 1]
else:
    session = args[args.index("--resume") + 1]
events = [
    {{"type": "system", "subtype": "init", "session_id": session, "memory_paths": None}},
    {{
        "type": "assistant",
        "timestamp": "2026-10-11T12:00:00+00:00",
        "message": {{
            "content": [
                {{"type": "tool_use", "id": "t1", "name": "Read", "input": {{"file_path": "message.md"}}}}
            ]
        }},
    }},
    {{
        "type": "user",
        "timestamp": "2026-10-11T12:00:01+00:00",
        "message": {{
            "content": [
                {{"type": "tool_result", "tool_use_id": "t1", "content": "ok", "is_error": False}}
            ]
        }},
    }},
    {{"type": "result", "subtype": "success", "is_error": False, "duration_ms": 10,
      "num_turns": 1, "result": "pong", "total_cost_usd": 0.001, "session_id": session}},
]
for event in events:
    sys.stdout.write(json.dumps(event) + "\\n")
sys.stdout.flush()
"""
