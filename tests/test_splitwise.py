"""Splitwise queue: copy text, /splitwise, and status buttons."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from psycopg.types.json import Jsonb

from ft_bot.models import Incoming, RunOutcome
from ft_bot.service import FinanceBot, ReadyJob
from ft_bot.splitwise import SplitwiseItem, format_splitwise_item, with_splitwise_hint
from ft_bot.state import BotState
from ft_bot.store import list_pending_splitwise, set_splitwise_status, splitwise_insert_count
from tests.test_approvals import _callback
from tests.test_ft_bot import RecordingNotifier, _postgres_up, _update

_PROBE_RUN = 900_000_001
_SHARES = [
    {"person_id": None, "name": "me", "amount": 304.50},
    {"person_id": None, "name": "Flatmate", "amount": 304.50},
]


def test_copy_text_matches_the_ticket() -> None:
    text = format_splitwise_item(
        SplitwiseItem(
            id=1,
            description="Blinkit: milk, eggs, detergent",
            total_amount=Decimal("609"),
            paid_by="me",
            split_method="equal",
            shares=tuple(_SHARES),
        )
    )
    assert text == (
        "Blinkit: milk, eggs, detergent\n"
        "₹609.00 · paid by me · split equally (2)\n"
        "Flatmate owes ₹304.50"
    )


def test_other_split_methods_and_several_people() -> None:
    text = format_splitwise_item(
        SplitwiseItem(
            id=2,
            description="Dinner",
            total_amount=Decimal("3000"),
            paid_by="me",
            split_method="exact",
            shares=(
                {"name": "me", "amount": 1000},
                {"name": "Flatmate", "amount": 1200},
                {"name": "Alex", "amount": 800},
            ),
        )
    )
    assert "₹3000.00 · paid by me · split by exact amounts (3)" in text
    assert "Flatmate owes ₹1200.00" in text
    assert "Alex owes ₹800.00" in text
    assert "me owes" not in text


def test_hint_uses_the_insert_count() -> None:
    assert with_splitwise_hint("Saved.", 0) == "Saved."
    assert with_splitwise_hint("Saved.", 2) == (
        "Saved.\n\n🧾 2 added to Splitwise queue — /splitwise"
    )


def test_splitwise_command_does_not_start_the_agent(tmp_path) -> None:
    bot = FinanceBot(
        allowed="5",
        notifier=RecordingNotifier(),
        state=BotState.load(tmp_path / "state.json"),
        album_quiet=0.01,
        process=_fail_if_called,
    )

    async def scenario() -> None:
        await bot.handle_message(_update(1, 1, 5, text="/splitwise"), download=_unused)
        assert bot._queue.qsize() == 0

    asyncio.run(scenario())


def test_list_and_buttons_use_the_worker(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    items = [
        SplitwiseItem(4, "Older", Decimal("10"), "me", "equal", ({"name": "me", "amount": 5}, {"name": "Flatmate", "amount": 5})),
        SplitwiseItem(5, "Newer", Decimal("20"), "me", "percent", ({"name": "me", "amount": 10}, {"name": "Flatmate", "amount": 10})),
    ]
    updates: list[tuple[int, str]] = []
    monkeypatch.setattr("ft_bot.service.list_pending_splitwise", lambda: list(items))
    monkeypatch.setattr(
        "ft_bot.service.set_splitwise_status",
        lambda queue_id, status: updates.append((queue_id, status)) or status,
    )
    bot = FinanceBot(
        allowed="5",
        notifier=RecordingNotifier(),
        state=BotState.load(tmp_path / "state.json"),
        album_quiet=0.01,
        process=_remember_agent,
    )

    async def scenario() -> None:
        await bot.start_worker()
        await bot.on_splitwise(_update(1, 1, 5, text="/splitwise"), None)
        for _ in range(80):
            if sum(event[0] == "splitwise" for event in bot.notifier.events) >= 2:
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("list did not send both items")
        listed = [event for event in bot.notifier.events if event[0] == "splitwise"]
        assert [event[3] for event in listed] == [4, 5]
        assert listed[0][2].startswith("Older")
        assert "split equally (2)" in listed[0][2]
        assert "split by percent (2)" in listed[1][2]
        await bot.on_callback(_callback("sa:4"), None)
        await _wait_reply(bot, "Marked added.")
        await bot.on_callback(_callback("sx:5"), None)
        await _wait_reply(bot, "Cancelled.")
        assert not any(isinstance(job, ReadyJob) or job == "claim" for job in _drain(bot))
        bot._task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await bot._task

    asyncio.run(scenario())
    assert updates == [(4, "added"), (5, "cancelled")]
    assert _agent_calls == []


def test_reply_appends_the_hint(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ft_bot.service.ledger_changed", lambda run_id: False)
    monkeypatch.setattr("ft_bot.service.batches_for_run", lambda run_id: [])
    monkeypatch.setattr("ft_bot.service.migrations_to_announce", lambda run_id, announced: [])
    monkeypatch.setattr("ft_bot.service.splitwise_insert_count", lambda run_id: 1)

    async def process(incoming, *, state, lock) -> RunOutcome:
        return RunOutcome(
            seq=4,
            run_id=42,
            result_text="Saved the dinner.",
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
        await bot._queue.put(ReadyJob(_incoming("dinner"), 1))
        await _wait_reply_kind(bot, "finish")
        bot._task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await bot._task

    asyncio.run(scenario())
    finish = next(event for event in bot.notifier.events if event[0] == "finish")
    assert finish[3] == (
        "Saved the dinner.\n\n🧾 1 added to Splitwise queue — /splitwise"
    )


def test_empty_queue_says_nothing_pending(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ft_bot.service.list_pending_splitwise", lambda: [])
    bot = FinanceBot(
        allowed="5",
        notifier=RecordingNotifier(),
        state=BotState.load(tmp_path / "state.json"),
        album_quiet=0.01,
        process=_fail_if_called,
    )

    async def scenario() -> None:
        await bot.start_worker()
        await bot.on_splitwise(_update(1, 1, 5, text="/splitwise"), None)
        await _wait_reply(bot, "Nothing pending on Splitwise.")
        bot._task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await bot._task

    asyncio.run(scenario())


def test_queue_status_updates_in_postgres() -> None:
    if not _postgres_up():
        pytest.skip("postgres unavailable")
    if not _wait_for_splitwise_table():
        pytest.fail("splitwise_queue did not appear")
    created = _insert_probe_rows()
    try:
        pending = [item for item in list_pending_splitwise() if item.id in created["queue_ids"]]
        assert [item.id for item in pending] == created["queue_ids"]
        assert pending[0].description == "Blinkit: milk, eggs, detergent"
        assert splitwise_insert_count(_PROBE_RUN) == 2
        assert set_splitwise_status(created["queue_ids"][0], "added") == "added"
        assert set_splitwise_status(created["queue_ids"][0], "cancelled") == "unchanged"
        assert set_splitwise_status(created["queue_ids"][1], "cancelled") == "cancelled"
        added, cancelled = _statuses(created["queue_ids"])
        assert added[0] == "added" and added[1] is not None
        assert cancelled == ("cancelled", None)
    finally:
        _delete_probe(created)


_agent_calls: list = []


async def _remember_agent(*args, **kwargs):
    _agent_calls.append(args)


async def _fail_if_called(*args, **kwargs):
    raise AssertionError("agent should not run")


async def _unused(file_id: str) -> bytes:
    raise AssertionError(file_id)


def _incoming(text: str) -> Incoming:
    return Incoming(
        chat_id=5,
        telegram_message_ids=[1],
        text=text,
        caption=None,
        received_at=datetime(2026, 10, 11, tzinfo=timezone.utc),
    )


async def _wait_reply(bot: FinanceBot, text: str) -> None:
    for _ in range(80):
        if any(event[0] == "reply" and event[2] == text for event in bot.notifier.events):
            return
        await asyncio.sleep(0.05)
    raise AssertionError(text)


async def _wait_reply_kind(bot: FinanceBot, kind: str) -> None:
    for _ in range(80):
        if any(event[0] == kind for event in bot.notifier.events):
            return
        await asyncio.sleep(0.05)
    raise AssertionError(kind)


def _drain(bot: FinanceBot) -> list:
    jobs = []
    while not bot._queue.empty():
        jobs.append(bot._queue.get_nowait())
    return jobs


def _wait_for_splitwise_table() -> bool:
    from ft_db.connect import connect, owner_database_url

    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        try:
            conn = connect(owner_database_url())
            try:
                table = conn.execute("SELECT to_regclass('public.splitwise_queue')").fetchone()
                accounts = conn.execute("SELECT count(*) FROM accounts").fetchone()
                if table and table[0] is not None and int(accounts[0]) >= 2:
                    return True
            finally:
                conn.close()
        except Exception:
            pass
        time.sleep(2)
    return False


def _insert_probe_rows() -> dict:
    from ft_db.connect import connect, owner_database_url

    conn = connect(owner_database_url())
    try:
        with conn.transaction():
            conn.execute("SELECT set_config('app.run_id', %s, true)", (str(_PROBE_RUN),))
            accounts = conn.execute("SELECT id FROM accounts ORDER BY id LIMIT 2").fetchall()
            first, second = int(accounts[0][0]), int(accounts[1][0])
            transaction_id = conn.execute(
                """
                INSERT INTO transactions (date, description)
                VALUES (CURRENT_DATE, 'ft-bot-splitwise-probe')
                RETURNING id
                """
            ).fetchone()[0]
            posting_ids = []
            for account_id, amount in ((first, "10.00"), (second, "-10.00")):
                posting_ids.append(
                    conn.execute(
                        """
                        INSERT INTO postings (transaction_id, account_id, amount)
                        VALUES (%s, %s, %s)
                        RETURNING id
                        """,
                        (transaction_id, account_id, amount),
                    ).fetchone()[0]
                )
            queue_ids = []
            for description in ("Blinkit: milk, eggs, detergent", "Dinner"):
                queue_ids.append(
                    conn.execute(
                        """
                        INSERT INTO splitwise_queue (
                            transaction_id, date, description, total_amount, paid_by,
                            split_method, shares, my_share, others_owe
                        )
                        VALUES (%s, CURRENT_DATE, %s, 609.00, 'me', 'equal', %s, 304.50, 304.50)
                        RETURNING id
                        """,
                        (transaction_id, description, Jsonb(_SHARES)),
                    ).fetchone()[0]
                )
        return {
            "transaction_id": int(transaction_id),
            "posting_ids": [int(item) for item in posting_ids],
            "queue_ids": [int(item) for item in queue_ids],
        }
    finally:
        conn.close()


def _statuses(queue_ids: list[int]) -> tuple[tuple, tuple]:
    from ft_db.connect import connect, owner_database_url

    conn = connect(owner_database_url())
    try:
        rows = conn.execute(
            """
            SELECT id, status, added_at
            FROM splitwise_queue
            WHERE id = ANY(%s)
            ORDER BY id
            """,
            (queue_ids,),
        ).fetchall()
    finally:
        conn.close()
    by_id = {int(row[0]): (row[1], row[2]) for row in rows}
    return by_id[queue_ids[0]], by_id[queue_ids[1]]


def _delete_probe(created: dict) -> None:
    from ft_db.connect import connect, owner_database_url

    conn = connect(owner_database_url())
    try:
        with conn.transaction():
            conn.execute("SELECT set_config('app.run_id', '', true)")
            conn.execute(
                "DELETE FROM splitwise_queue WHERE id = ANY(%s)",
                (created["queue_ids"],),
            )
            conn.execute(
                "DELETE FROM postings WHERE transaction_id = %s",
                (created["transaction_id"],),
            )
            conn.execute(
                "DELETE FROM transactions WHERE id = %s",
                (created["transaction_id"],),
            )
            conn.execute(
                """
                DELETE FROM audit_log
                WHERE run_id = %s
                   OR ("table" = 'splitwise_queue' AND row_id = ANY(%s))
                   OR ("table" = 'transactions' AND row_id = %s)
                   OR ("table" = 'postings' AND row_id = ANY(%s))
                """,
                (
                    str(_PROBE_RUN),
                    [str(item) for item in created["queue_ids"]],
                    str(created["transaction_id"]),
                    [str(item) for item in created["posting_ids"]],
                ),
            )
    finally:
        conn.close()
