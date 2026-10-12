"""Host and container checks that a fake Claude cannot cover.

Resets the state file it is given so a repeat run starts a new session.
Point it at a scratch file. The production file is logs/bot_state.json,
which `python -m ft_bot` uses and this command does not touch unless you
pass that path to --state.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from ft_bot.access import chat_allowed
from ft_bot.config import apply_db_host, configure_logging, logs_dir, messages_dir, telegram_token
from ft_bot.models import Attachment, Incoming
from ft_bot.pipeline import process_incoming
from ft_bot.service import FinanceBot
from ft_bot.state import BotState
from ft_bot.store import run_row, tool_call_count

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class _Silent:
    async def ack(self, chat_id: int, importing: bool) -> int:
        return 1

    async def finish(self, chat_id, ack_id, text: str, seq: int) -> None:
        return None

    async def reply(self, chat_id: int, text: str) -> None:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the bot pipeline against claude and Telegram.")
    parser.add_argument(
        "--state",
        default="",
        help="Session file to reset and use. Default: logs/hostcheck-state.json",
    )
    parser.add_argument("--once", action="store_true", help="Text message only, no photo follow-up.")
    parser.add_argument("--skip-telegram", action="store_true", help="Do not call getMe.")
    args = parser.parse_args(argv)

    apply_db_host()
    configure_logging()
    state_file = Path(args.state) if args.state else logs_dir() / "hostcheck-state.json"
    if state_file.exists():
        state_file.unlink()
    problems: list[str] = []

    if not args.skip_telegram:
        try:
            username = asyncio.run(_get_me())
            print(f"PASS getMe username={username}")
        except Exception as exc:
            problems.append(f"getMe failed: {_safe_exc(exc)}")

    if chat_allowed(1, "1") and not chat_allowed(2, "1") and not chat_allowed(None, "1"):
        print("PASS chat allow-list")
    else:
        problems.append("chat allow-list check failed")

    ignored = asyncio.run(_ignored_update())
    if ignored:
        print("PASS ignored a chat that is not allowed")
    else:
        problems.append("a disallowed chat was not ignored")

    state = BotState.load(state_file)
    lock = asyncio.Lock()
    text = Incoming(
        chat_id=None,
        telegram_message_ids=[],
        text="host check text",
        caption=None,
        received_at=datetime.now(timezone.utc),
        prompt_override=(
            "Read {folder}/message.md with the Read tool. "
            "Then reply with the first line of that file and nothing else."
        ),
    )
    try:
        first = asyncio.run(process_incoming(text, state=state, lock=lock))
    except Exception as exc:
        problems.append(f"text pipeline failed: {_safe_exc(exc)}")
        first = None
    if first is not None:
        problems.extend(_check_outcome(first, "text", expect_mode="session-id"))

    if not args.once:
        photo = Incoming(
            chat_id=None,
            telegram_message_ids=[],
            text=None,
            caption="host check photo",
            received_at=datetime.now(timezone.utc),
            attachments=[Attachment("photo.jpg", _PNG, "photo")],
            prompt_override=(
                "Read {folder}/message.md with the Read tool. "
                "Then reply with the single word photo-ok."
            ),
        )
        try:
            second = asyncio.run(process_incoming(photo, state=state, lock=lock))
        except Exception as exc:
            problems.append(f"photo pipeline failed: {_safe_exc(exc)}")
            second = None
        if second is not None:
            problems.extend(_check_outcome(second, "photo", expect_mode="resume"))
            folder = messages_dir() / f"{second.seq:06d}"
            if not (folder / "01_photo.jpg").is_file():
                problems.append("photo folder has no 01_photo.jpg")

    if problems:
        for problem in problems:
            print(f"FAIL {problem}")
        return 1
    print("PASS hostcheck")
    return 0


def _check_outcome(outcome, label: str, expect_mode: str) -> list[str]:
    problems: list[str] = []
    folder = messages_dir() / f"{outcome.seq:06d}"
    log_file = logs_dir() / "runs" / f"{outcome.seq:06d}.jsonl"
    print(
        f"PASS {label} seq={outcome.seq:06d} run_id={outcome.run_id} "
        f"mode={outcome.session_mode} tools={outcome.tool_calls} error={outcome.is_error}"
    )
    if outcome.session_mode != expect_mode:
        problems.append(f"{label} session mode {outcome.session_mode} != {expect_mode}")
    if outcome.is_error:
        problems.append(f"{label} run is_error result={outcome.result_text[:180]!r}")
    if not (folder / "message.md").is_file() or not (folder / "meta.json").is_file():
        problems.append(f"{label} folder is incomplete")
    if not log_file.is_file():
        problems.append(f"{label} jsonl missing")
    else:
        problems.extend(_check_log(log_file, label))
    row = run_row(outcome.run_id)
    if row is None:
        problems.append(f"{label} agent_runs row missing")
    elif row[6] or not (row[7] or "").strip():
        problems.append(f"{label} agent_runs row not filled")
    if tool_call_count(outcome.run_id) < 1:
        problems.append(f"{label} has no tool_calls row")
    return problems


def _check_log(path: Path, label: str) -> list[str]:
    saw_init = False
    saw_result = False
    for line in path.read_text(errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            saw_init = True
            memory = event.get("memory_paths")
            if memory not in (None, {}):
                return [f"{label} init loaded memory_paths={memory!r}"]
        if event.get("type") == "result":
            saw_result = True
    problems = []
    if not saw_init:
        problems.append(f"{label} jsonl has no system/init event")
    if not saw_result:
        problems.append(f"{label} jsonl has no result event")
    return problems


async def _get_me() -> str:
    from telegram import Bot

    token = telegram_token()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    async with Bot(token) as bot:
        me = await bot.get_me()
    return me.username or str(me.id)


async def _ignored_update() -> bool:
    from telegram import Update

    bot = FinanceBot(allowed="1", notifier=_Silent(), state=_ScratchState(), process=_boom)
    update = Update.de_json(
        {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "date": 1_700_000_000,
                "chat": {"id": 2, "type": "private"},
                "from": {"id": 2, "is_bot": False, "first_name": "X"},
                "text": "nope",
            },
        },
        bot=None,
    )
    await bot.handle_message(update, download=_no_download)
    return bot._queue.qsize() == 0 and not _boom.called


async def _no_download(file_id: str) -> bytes:
    raise AssertionError(file_id)


class _ScratchState:
    def seq_for(self, telegram_id: int) -> None:
        return None


async def _boom(*args, **kwargs):
    _boom.called = True
    raise AssertionError("disallowed chat reached the pipeline")


_boom.called = False


def _safe_exc(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}"
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    oauth = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
    for secret in (token, oauth):
        if secret and secret in text:
            text = text.replace(secret, "[redacted]")
    return text[:300]


if __name__ == "__main__":
    sys.exit(main())
