"""One message: folder, agent_runs row, claude, then the parsed trace."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone

from ft_bot.config import messages_dir
from ft_bot.models import Incoming, RunOutcome
from ft_bot.preparse import preparse_message_folder
from ft_bot.runner import ClaudeRun, build_prompt, log_file_for, relative_log_path, run_claude
from ft_bot.state import BotState
from ft_bot.store import fill_attachment_hashes, finish_run, insert_message, insert_run

log = logging.getLogger(__name__)

_RESUME_MISSING = ("no conversation found", "session not found")


def public_text(outcome: RunOutcome) -> str:
    text = (outcome.result_text or "").strip()
    if not text:
        return "The agent run failed." if outcome.is_error else "(no reply)"
    return telegram_plain(text)


_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.S)
_CODE = re.compile(r"`([^`\n]+)`")
_HEADING = re.compile(r"^#{1,6}\s+", re.M)


def telegram_plain(text: str) -> str:
    """Replies go out as plain text; strip the Markdown the model sometimes emits."""
    text = _BOLD.sub(lambda m: m.group(1) or m.group(2), text)
    text = _CODE.sub(r"\1", text)
    return _HEADING.sub("", text)


def resume_missing(claude: ClaudeRun) -> bool:
    blob = f"{claude.stderr}\n{claude.parsed.result_text}".lower()
    return any(phrase in blob for phrase in _RESUME_MISSING)


async def _one_run(
    *,
    seq: int,
    session_id: str,
    session_mode: str,
    prompt: str,
    log_path: str,
) -> tuple[int, ClaudeRun]:
    started = datetime.now(timezone.utc)
    run_id = await asyncio.to_thread(insert_run, seq, session_id, log_path, started)
    log.info("run started seq=%s run_id=%s mode=%s", f"{seq:06d}", run_id, session_mode)
    claude = await run_claude(
        prompt=prompt,
        session_id=session_id,
        session_mode=session_mode,
        run_id=run_id,
        log_path=log_file_for(seq),
    )
    actual_session = claude.parsed.session_id or session_id
    try:
        await asyncio.to_thread(finish_run, run_id, actual_session, claude.parsed)
    except Exception:
        log.exception("could not store the run result seq=%s run_id=%s", f"{seq:06d}", run_id)
        claude.parsed.tool_calls = []
        try:
            await asyncio.to_thread(finish_run, run_id, actual_session, claude.parsed)
        except Exception:
            log.exception("could not store even the result text seq=%s run_id=%s", f"{seq:06d}", run_id)
    return run_id, claude


def incoming_from_folder(seq: int) -> Incoming:
    folder = messages_dir() / f"{seq:06d}"
    meta = json.loads((folder / "meta.json").read_text())
    received = datetime.fromisoformat(meta["received_at"])
    if received.tzinfo is None:
        received = received.replace(tzinfo=timezone.utc)
    ids = []
    for item in meta.get("telegram_message_ids") or []:
        ids.append(int(item))
    target = meta.get("synthetic_target_seq")
    return Incoming(
        chat_id=meta.get("chat_id"),
        telegram_message_ids=ids,
        text=meta.get("text"),
        caption=meta.get("caption"),
        received_at=received,
        media_group_id=meta.get("media_group_id"),
        attachments=[],
        synthetic_action=meta.get("synthetic_action"),
        synthetic_target_seq=int(target) if target else None,
        file_count=len(meta.get("attachments") or []),
        stored_seq=seq,
    )


async def process_incoming(
    incoming: Incoming,
    *,
    state: BotState,
    lock: asyncio.Lock,
) -> RunOutcome:
    async with lock:
        session_mode, session_id = state.claim_for_run()

    if incoming.stored_seq is None:
        seq = await asyncio.to_thread(insert_message, incoming, session_id)
        async with lock:
            for telegram_id in incoming.telegram_message_ids:
                state.remember(telegram_id, seq)
    else:
        seq = incoming.stored_seq

    folder = messages_dir() / f"{seq:06d}"
    if folder.is_dir():
        try:
            await asyncio.to_thread(preparse_message_folder, folder)
        except Exception:
            log.exception("preparse failed seq=%s", f"{seq:06d}")

    log_path = relative_log_path(seq)
    prompt = build_prompt(incoming, seq)
    run_id, claude = await _one_run(
        seq=seq,
        session_id=session_id,
        session_mode=session_mode,
        prompt=prompt,
        log_path=log_path,
    )
    if session_mode == "resume" and resume_missing(claude):
        log.info("resume missed the session seq=%s; retrying once", f"{seq:06d}")
        async with lock:
            session_id = state.new_session()
        session_mode = "session-id"
        run_id, claude = await _one_run(
            seq=seq,
            session_id=session_id,
            session_mode=session_mode,
            prompt=prompt,
            log_path=log_path,
        )
    established = bool(claude.parsed.saw_init and claude.parsed.session_id)
    async with lock:
        state.note_finished(
            requested_id=session_id,
            actual_id=claude.parsed.session_id,
            got_session=established,
        )
        if not established:
            state.new_session()
    try:
        await asyncio.to_thread(fill_attachment_hashes, seq)
    except Exception:
        log.exception("could not fill attachment hashes seq=%s", f"{seq:06d}")
    log.info(
        "run finished seq=%s run_id=%s error=%s tools=%s",
        f"{seq:06d}",
        run_id,
        claude.parsed.is_error,
        len(claude.parsed.tool_calls),
    )
    return RunOutcome(
        seq=seq,
        run_id=run_id,
        result_text=claude.parsed.result_text,
        is_error=claude.parsed.is_error,
        tool_calls=len(claude.parsed.tool_calls),
        session_mode=session_mode,
        log_path=log_path,
        memory_paths=claude.parsed.memory_paths,
    )
