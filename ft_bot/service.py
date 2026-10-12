"""Long polling, one-at-a-time queue, and the Edit / Undo buttons."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ft_bot.access import chat_allowed, is_builtin_command, parse_callback
from ft_bot.approvals import (
    apply_failure_text,
    batches_for_run,
    correction_text,
    ledger_changed,
    import_commit_text,
    import_discard_text,
    migration_text,
    migrations_to_announce,
    split_telegram,
    undo_run_text,
)
from ft_bot.splitwise import format_splitwise_item, with_splitwise_hint
from ft_bot.config import (
    album_quiet_s,
    allowed_chat_id,
    apply_db_host,
    configure_logging,
    state_path,
    telegram_token,
)
from ft_bot.models import Attachment, Incoming
from ft_bot.pipeline import incoming_from_folder, process_incoming, public_text
from ft_bot.state import BotState
from ft_bot.store import (
    insert_message,
    interrupted_message_seqs,
    latest_run,
    list_pending_splitwise,
    message_seqs_without_runs,
    set_splitwise_status,
    splitwise_insert_count,
)
from ft_mcp import MigrationError, apply_migration, reject_migration

log = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self) -> None:
        self.bot = None

    def bind(self, bot) -> None:
        self.bot = bot

    async def ack(self, chat_id: int, importing: bool) -> int | None:
        text = "📥 importing…" if importing else "⏳ working…"
        message = await self.bot.send_message(chat_id=chat_id, text=text)
        return message.message_id

    async def finish(
        self,
        chat_id: int | None,
        ack_id: int | None,
        text: str,
        seq: int,
        batch_ids: list[int] | None = None,
        editable: bool = False,
        run_id: int = 0,
    ) -> None:
        if chat_id is None or self.bot is None:
            return
        chunks = split_telegram(text)
        markup = _reply_keyboard(seq, batch_ids or [], editable=editable, run_id=run_id)
        last = len(chunks) - 1
        for index, chunk in enumerate(chunks):
            buttons = markup if index == last else None
            if index == 0 and ack_id is not None:
                try:
                    await self.bot.edit_message_text(
                        text=chunk,
                        chat_id=chat_id,
                        message_id=ack_id,
                        reply_markup=buttons,
                    )
                    continue
                except Exception:
                    log.exception("could not edit the working message")
            await self.bot.send_message(chat_id=chat_id, text=chunk, reply_markup=buttons)

    async def reply(self, chat_id: int, text: str) -> None:
        for chunk in split_telegram(text):
            await self.bot.send_message(chat_id=chat_id, text=chunk)

    async def migration_prompt(self, chat_id: int, proposal) -> None:
        text, sql = migration_text(proposal)
        chunks = split_telegram(text)
        markup = _migration_keyboard(proposal.id)
        last = len(chunks) - 1
        for index, chunk in enumerate(chunks):
            buttons = markup if index == last else None
            await self.bot.send_message(chat_id=chat_id, text=chunk, reply_markup=buttons)
        if sql:
            filename = f"{proposal.id}_{proposal.name}.sql"
            await self.bot.send_document(
                chat_id=chat_id,
                document=InputFile(sql.encode(), filename=filename),
            )

    async def splitwise_item(self, chat_id: int, text: str, queue_id: int) -> None:
        chunks = split_telegram(text)
        markup = _splitwise_keyboard(queue_id)
        last = len(chunks) - 1
        for index, chunk in enumerate(chunks):
            buttons = markup if index == last else None
            await self.bot.send_message(chat_id=chat_id, text=chunk, reply_markup=buttons)


def _reply_keyboard(
    seq: int,
    batch_ids: list[int],
    *,
    editable: bool = False,
    run_id: int = 0,
) -> InlineKeyboardMarkup | None:
    """Edit and Undo only when this run changed transactions or postings. Undo carries the run id."""
    rows: list[list[InlineKeyboardButton]] = []
    if editable and seq > 0 and run_id > 0:
        rows.append([
            InlineKeyboardButton("✏️ Edit", callback_data=f"e:{seq:06d}"),
            InlineKeyboardButton("↩︎ Undo", callback_data=f"ur:{run_id}"),
        ])
    for batch_id in batch_ids:
        rows.append([
            InlineKeyboardButton("✅ Commit", callback_data=f"ic:{batch_id}"),
            InlineKeyboardButton("❌ Discard", callback_data=f"ix:{batch_id}"),
        ])
    if not rows:
        return None
    return InlineKeyboardMarkup(rows)


def _migration_keyboard(migration_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Apply", callback_data=f"ma:{migration_id}"),
        InlineKeyboardButton("Reject", callback_data=f"mr:{migration_id}"),
    ]])


def _splitwise_keyboard(queue_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Added", callback_data=f"sa:{queue_id}"),
        InlineKeyboardButton("✖ Cancel", callback_data=f"sx:{queue_id}"),
    ]])


def _without_import_buttons(markup: InlineKeyboardMarkup | None) -> InlineKeyboardMarkup | None:
    if markup is None:
        return None
    rows = []
    for row in markup.inline_keyboard:
        kept = [
            button
            for button in row
            if not str(button.callback_data or "").startswith(("ic:", "ix:"))
        ]
        if kept:
            rows.append(kept)
    if not rows:
        return None
    return InlineKeyboardMarkup(rows)


@dataclass
class ReadyJob:
    incoming: Incoming
    ack_id: int | None


@dataclass
class MigrationJob:
    action: str
    migration_id: str
    chat_id: int | None


@dataclass
class SplitwiseJob:
    """A /splitwise list or a status button. Runs on the worker, not beside an agent turn."""

    action: str
    chat_id: int | None
    queue_id: int = 0


class AlbumJob:
    """Parts of one media group. The worker waits until new parts stop arriving."""

    def __init__(self, group_id: str, chat_id: int | None, ack_id: int | None, quiet: float) -> None:
        self.group_id = group_id
        self.chat_id = chat_id
        self.ack_id = ack_id
        self.parts: list[Incoming] = []
        self._quiet = quiet
        self._last = time.monotonic()
        self._added = asyncio.Event()

    def add(self, incoming: Incoming) -> None:
        self.parts.append(incoming)
        self._last = time.monotonic()
        self._added.set()

    async def wait_parts(self) -> list[Incoming]:
        while True:
            idle = time.monotonic() - self._last
            if self.parts and idle >= self._quiet:
                return list(self.parts)
            timeout = max(0.01, self._quiet - idle)
            self._added.clear()
            try:
                await asyncio.wait_for(self._added.wait(), timeout=timeout)
            except TimeoutError:
                continue


def merge_incomings(parts: list[Incoming]) -> Incoming:
    ordered = sorted(
        parts,
        key=lambda part: part.telegram_message_ids[0] if part.telegram_message_ids else 0,
    )
    attachments: list[Attachment] = []
    ids: list[int] = []
    for part in ordered:
        attachments.extend(part.attachments)
        ids.extend(part.telegram_message_ids)
    text = next((part.text for part in ordered if part.text), None)
    caption = next((part.caption for part in ordered if part.caption), None)
    first = ordered[0]
    return Incoming(
        chat_id=first.chat_id,
        telegram_message_ids=ids,
        text=text,
        caption=caption,
        received_at=min(part.received_at for part in ordered),
        media_group_id=first.media_group_id,
        attachments=attachments,
        file_count=len(attachments),
    )


async def download_file(bot, file_id: str) -> bytes:
    tg_file = await bot.get_file(file_id)
    return bytes(await tg_file.download_as_bytearray())


async def incoming_from_update(update: Update, download) -> Incoming:
    message = update.effective_message
    if message is None:
        raise ValueError("update has no message")
    attachments: list[Attachment] = []
    if message.photo:
        largest = message.photo[-1]
        data = await download(largest.file_id)
        attachments.append(Attachment("photo.jpg", data, "photo"))
    document = message.document
    if document is not None:
        data = await download(document.file_id)
        attachments.append(Attachment(document.file_name or "file", data, "document"))
    received = message.date
    if received.tzinfo is None:
        from datetime import timezone

        received = received.replace(tzinfo=timezone.utc)
    ids = [message.message_id] if message.message_id is not None else []
    return Incoming(
        chat_id=message.chat_id,
        telegram_message_ids=ids,
        text=message.text,
        caption=message.caption,
        received_at=received,
        media_group_id=message.media_group_id,
        attachments=attachments,
        file_count=len(attachments),
    )


@dataclass
class FinanceBot:
    allowed: str
    notifier: object
    state: BotState
    album_quiet: float = 1.2
    process: object = field(default=process_incoming)
    _queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    _albums: dict[str, AlbumJob] = field(default_factory=dict)
    _acks: dict[int, int | None] = field(default_factory=dict)
    _inflight: set[int] = field(default_factory=set)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _task: asyncio.Task | None = None
    running: bool = False

    def _allowed(self, update: Update) -> bool:
        chat = update.effective_chat
        chat_id = chat.id if chat is not None else None
        if chat_allowed(chat_id, self.allowed):
            return True
        log.info("ignored update from a chat that is not allowed")
        return False

    async def start(self) -> None:
        await self.start_worker()
        await self.recover()

    async def start_worker(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def recover(self) -> None:
        try:
            interrupted = await asyncio.to_thread(interrupted_message_seqs)
        except Exception:
            log.exception("could not look for interrupted runs")
            interrupted = []
        for seq in interrupted:
            await self._report_interrupted(seq)
        try:
            seqs = await asyncio.to_thread(message_seqs_without_runs)
        except Exception:
            log.exception("could not look for unfinished messages")
            return
        for seq in seqs:
            try:
                incoming = incoming_from_folder(seq)
            except Exception:
                log.exception("skip recovery of message %s", seq)
                continue
            if not chat_allowed(incoming.chat_id, self.allowed):
                continue
            self._acks[seq] = await self._ack(incoming.chat_id, incoming.has_files)
            await self._queue.put("claim")
            log.info("requeued message %s", f"{seq:06d}")

    async def _report_interrupted(self, seq: int) -> None:
        try:
            incoming = incoming_from_folder(seq)
        except Exception:
            log.exception("could not describe interrupted message %s", seq)
            return
        if not chat_allowed(incoming.chat_id, self.allowed):
            return
        await self.notifier.reply(
            incoming.chat_id,
            f"Message {seq:06d} was interrupted; check before resending.",
        )

    async def on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.handle_message(update, download=lambda file_id: download_file(context.bot, file_id))

    async def handle_message(self, update: Update, download) -> None:
        if update.edited_message is not None:
            if self._allowed(update):
                chat = update.effective_chat
                if chat is not None:
                    await self.notifier.reply(chat.id, "Edits aren't picked up — use ✏️ Edit")
            return
        if not self._allowed(update):
            return
        message = update.effective_message
        if message is None or is_builtin_command(message.text):
            return
        if message.message_id is not None:
            async with self._lock:
                reserve = getattr(self.state, "reserve", None)
                if reserve is not None:
                    if not reserve(message.message_id):
                        log.info("skip duplicate telegram message")
                        return
                elif self.state.seq_for(message.message_id) is not None:
                    log.info("skip duplicate telegram message")
                    return
        try:
            incoming = await incoming_from_update(update, download)
        except Exception:
            log.exception("could not read the telegram message")
            self._release_ids([message.message_id] if message.message_id is not None else [])
            chat = update.effective_chat
            if chat is not None:
                await self.notifier.reply(chat.id, "Could not download that file.")
            return
        incoming = self._apply_correction(incoming)
        if incoming.media_group_id:
            await self._add_album(incoming)
            return
        await self._persist_and_claim(incoming)

    async def _persist_and_claim(self, incoming: Incoming, ack_id: int | None = None) -> None:
        if ack_id is None:
            ack_id = await self._ack(incoming.chat_id, incoming.has_files)
        try:
            seq = await asyncio.to_thread(insert_message, incoming, self.state.session_id)
        except Exception:
            log.exception("could not store the message")
            self._release_ids(incoming.telegram_message_ids)
            if incoming.chat_id is not None:
                await self.notifier.finish(incoming.chat_id, ack_id, "Could not store that message.", 0)
            return
        incoming.stored_seq = seq
        async with self._lock:
            remember = getattr(self.state, "remember", None)
            if remember is not None:
                for telegram_id in incoming.telegram_message_ids:
                    remember(telegram_id, seq)
        self._acks[seq] = ack_id
        await self._queue.put("claim")

    def _release_ids(self, telegram_ids: list[int]) -> None:
        release = getattr(self.state, "release", None)
        if release is None:
            return
        for telegram_id in telegram_ids:
            release(telegram_id)

    async def _add_album(self, incoming: Incoming) -> None:
        group_id = incoming.media_group_id or ""
        job = self._albums.get(group_id)
        if job is None:
            ack_id = await self._ack(incoming.chat_id, True)
            job = AlbumJob(group_id, incoming.chat_id, ack_id, self.album_quiet)
            self._albums[group_id] = job
            job.add(incoming)
            asyncio.create_task(self._watch_album(group_id))
            return
        job.add(incoming)

    async def _watch_album(self, group_id: str) -> None:
        job = self._albums.get(group_id)
        if job is None:
            return
        parts = await job.wait_parts()
        self._albums.pop(group_id, None)
        if not parts:
            await self.notifier.finish(job.chat_id, job.ack_id, "Could not read that album.", 0)
            return
        await self._persist_and_claim(merge_incomings(parts), ack_id=job.ack_id)

    async def on_new(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._allowed(update):
            return
        async with self._lock:
            session_id = self.state.new_session()
        chat = update.effective_chat
        if chat is not None:
            await self.notifier.reply(chat.id, f"Started a new session.\n{session_id}")

    async def on_splitwise(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._allowed(update):
            return
        chat = update.effective_chat
        if chat is None:
            return
        await self._queue.put(SplitwiseJob("list", chat.id))

    async def on_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._allowed(update):
            return
        chat = update.effective_chat
        if chat is None:
            return
        await self.notifier.reply(chat.id, await self.status_text())

    async def status_text(self) -> str:
        async with self._lock:
            session = self.state.session_id
            session_date = self.state.session_date
            established = "yes" if self.state.established else "no"
        waiting = self._queue.qsize()
        running = "yes" if self.running else "no"
        try:
            last = await asyncio.to_thread(latest_run)
        except Exception:
            log.exception("status query failed")
            last = None
            last_line = "Last run: database unavailable"
        else:
            last_line = _format_last_run(last)
        return (
            f"Queue: {waiting} waiting\n"
            f"Running: {running}\n"
            f"Session: {session}\n"
            f"Session date: {session_date}\n"
            f"Established: {established}\n"
            f"{last_line}"
        )

    def _apply_correction(self, incoming: Incoming) -> Incoming:
        """Prefix the next plain text message after Edit. Photos do not consume it."""
        pending = getattr(self.state, "pending_correction_seq", None)
        if not pending or not incoming.text or incoming.media_group_id:
            return incoming
        incoming.text = correction_text(int(pending), incoming.text)
        clear = getattr(self.state, "clear_correction", None)
        if clear is not None:
            clear()
        return incoming

    async def on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._allowed(update):
            return
        query = update.callback_query
        if query is None:
            return
        await _answer_callback(query)
        parsed = parse_callback(query.data or "")
        if parsed is None:
            return
        action, payload = parsed
        chat = update.effective_chat
        chat_id = chat.id if chat is not None else None
        if action == "edit":
            self.state.expect_correction(int(payload))
            if chat_id is not None:
                await self.notifier.reply(chat_id, f"Send the correction for message {int(payload):06d}.")
            return
        if action == "undo_run":
            await self._queue_text(chat_id, undo_run_text(int(payload)))
            return
        if action in {"import_commit", "import_discard"}:
            await _clear_import_buttons(query)
            batch_id = int(payload)
            text = import_commit_text(batch_id) if action == "import_commit" else import_discard_text(batch_id)
            await self._queue_text(chat_id, text)
            return
        if action in {"migration_apply", "migration_reject"}:
            await _clear_message_buttons(query)
            await self._queue.put(MigrationJob(action, str(payload), chat_id))
            return
        if action in {"splitwise_added", "splitwise_cancel"}:
            await _clear_message_buttons(query)
            await self._queue.put(SplitwiseJob(action, chat_id, int(payload)))

    async def _queue_text(
        self,
        chat_id: int | None,
        text: str,
        synthetic_action: str | None = None,
        synthetic_target_seq: int | None = None,
    ) -> None:
        from datetime import datetime, timezone

        incoming = Incoming(
            chat_id=chat_id,
            telegram_message_ids=[],
            text=text,
            caption=None,
            received_at=datetime.now(timezone.utc),
            synthetic_action=synthetic_action,
            synthetic_target_seq=synthetic_target_seq,
        )
        ack_id = await self._ack(chat_id, False)
        await self._persist_and_claim(incoming, ack_id=ack_id)

    async def _apply_migration(self, chat_id: int | None, migration_id: str) -> None:
        try:
            result = await asyncio.to_thread(apply_migration, migration_id, approved_by="telegram")
        except MigrationError as exc:
            if chat_id is not None:
                await self.notifier.reply(chat_id, f"Could not apply migration {migration_id}.")
            await self._queue_text(chat_id, apply_failure_text(migration_id, str(exc)))
            return
        except Exception:
            log.exception("apply migration failed id=%s", migration_id)
            if chat_id is not None:
                await self.notifier.reply(chat_id, f"Could not apply migration {migration_id}.")
            await self._queue_text(chat_id, apply_failure_text(migration_id, "apply failed"))
            return
        if chat_id is not None:
            await self.notifier.reply(chat_id, _format_apply_result(result))

    async def _reject_migration(self, chat_id: int | None, migration_id: str) -> None:
        try:
            result = await asyncio.to_thread(reject_migration, migration_id)
        except MigrationError as exc:
            log.info("reject migration refused id=%s", migration_id)
            if chat_id is not None:
                await self.notifier.reply(chat_id, f"Could not reject migration {migration_id}. {exc}")
            return
        except Exception:
            log.exception("reject migration failed id=%s", migration_id)
            if chat_id is not None:
                await self.notifier.reply(chat_id, f"Could not reject migration {migration_id}.")
            return
        if chat_id is not None:
            await self.notifier.reply(
                chat_id,
                f"Rejected migration {result.get('id')} {result.get('name')}.",
            )

    async def _run_splitwise(self, job: SplitwiseJob) -> None:
        if job.action == "list":
            try:
                await self._list_splitwise(job.chat_id)
            except Exception:
                log.exception("could not list the splitwise queue")
                if job.chat_id is not None:
                    await self.notifier.reply(job.chat_id, "Could not read the Splitwise queue.")
            return
        try:
            await self._update_splitwise(job)
        except Exception:
            log.exception("could not update splitwise item %s", job.queue_id)
            if job.chat_id is not None:
                await self.notifier.reply(job.chat_id, "Could not update that Splitwise item.")

    async def _list_splitwise(self, chat_id: int | None) -> None:
        if chat_id is None:
            return
        items = await asyncio.to_thread(list_pending_splitwise)
        if not items:
            await self.notifier.reply(chat_id, "Nothing pending on Splitwise.")
            return
        for item in items:
            await self.notifier.splitwise_item(chat_id, format_splitwise_item(item), item.id)

    async def _update_splitwise(self, job: SplitwiseJob) -> None:
        status = "added" if job.action == "splitwise_added" else "cancelled"
        result = await asyncio.to_thread(set_splitwise_status, job.queue_id, status)
        if job.chat_id is None:
            return
        if result == "added":
            text = "Marked added."
        elif result == "cancelled":
            text = "Cancelled."
        else:
            text = "That Splitwise item is no longer pending."
        await self.notifier.reply(job.chat_id, text)

    async def _ack(self, chat_id: int | None, importing: bool) -> int | None:
        if chat_id is None:
            return None
        try:
            return await self.notifier.ack(chat_id, importing)
        except Exception:
            log.exception("could not send the working message")
            return None

    async def _loop(self) -> None:
        while True:
            job = await self._queue.get()
            self.running = True
            chat_id: int | None = None
            ack_id: int | None = None
            try:
                if job == "claim":
                    await self._claim_one()
                elif isinstance(job, MigrationJob):
                    if job.action == "migration_apply":
                        await self._apply_migration(job.chat_id, job.migration_id)
                    else:
                        await self._reject_migration(job.chat_id, job.migration_id)
                elif isinstance(job, SplitwiseJob):
                    await self._run_splitwise(job)
                elif isinstance(job, ReadyJob):
                    incoming = job.incoming
                    chat_id = incoming.chat_id
                    ack_id = job.ack_id
                    await self._run_incoming(incoming, ack_id)
            except Exception:
                log.exception("job failed")
                if chat_id is not None:
                    try:
                        await self.notifier.finish(chat_id, ack_id, "The agent run failed.", 0)
                    except Exception:
                        log.exception("could not report the failure")
            finally:
                self.running = False
                self._queue.task_done()

    async def _claim_one(self) -> None:
        seqs = await asyncio.to_thread(message_seqs_without_runs)
        seq = next((item for item in seqs if item not in self._inflight), None)
        if seq is None:
            return
        self._inflight.add(seq)
        incoming = None
        try:
            incoming = await asyncio.to_thread(incoming_from_folder, seq)
            if not chat_allowed(incoming.chat_id, self.allowed):
                return
            ack_id = self._acks.pop(seq, None)
            await self._run_incoming(incoming, ack_id)
        except Exception:
            log.exception("claimed message %s failed", f"{seq:06d}")
            chat_id = incoming.chat_id if incoming is not None else None
            ack_id = self._acks.pop(seq, None)
            if chat_id is not None:
                await self.notifier.finish(chat_id, ack_id, "The agent run failed.", 0)
        finally:
            self._inflight.discard(seq)

    async def _run_incoming(self, incoming: Incoming, ack_id: int | None) -> None:
        chat_id = incoming.chat_id
        outcome = await self.process(incoming, state=self.state, lock=self._lock)
        batches = batches_for_run(outcome.run_id)
        editable = ledger_changed(outcome.run_id)
        try:
            queued = await asyncio.to_thread(splitwise_insert_count, outcome.run_id)
        except Exception:
            log.exception("could not count splitwise rows for run %s", outcome.run_id)
            queued = 0
        await self.notifier.finish(
            chat_id,
            ack_id,
            with_splitwise_hint(public_text(outcome), queued),
            outcome.seq,
            batches,
            editable=editable,
            run_id=outcome.run_id,
        )
        await self._announce_migrations(chat_id, outcome.run_id)

    async def _announce_migrations(self, chat_id: int | None, run_id: int) -> None:
        if chat_id is None:
            return
        announced = set(getattr(self.state, "announced_migrations", []) or [])
        try:
            proposals = await asyncio.to_thread(migrations_to_announce, run_id, announced)
        except Exception:
            log.exception("could not look up migration proposals")
            return
        for proposal in proposals:
            try:
                await self.notifier.migration_prompt(chat_id, proposal)
            except Exception:
                log.exception("could not announce migration %s", proposal.id)
                continue
            note = getattr(self.state, "note_announced", None)
            if note is not None:
                note(proposal.id)


def _format_apply_result(result: dict) -> str:
    lines = [f"Applied migration {result.get('id')} {result.get('name')}."]
    backup = result.get("backup")
    if backup:
        lines.append(f"Backup: {backup}")
    warning = result.get("warning")
    if warning:
        lines.append(str(warning))
    return "\n".join(lines)


async def _answer_callback(query) -> None:
    try:
        await query.answer()
    except Exception:
        log.debug("callback answer skipped")


async def _clear_import_buttons(query) -> None:
    message = query.message
    if message is None:
        return
    try:
        await query.edit_message_reply_markup(
            reply_markup=_without_import_buttons(message.reply_markup)
        )
    except Exception:
        log.debug("could not remove import buttons")


async def _clear_message_buttons(query) -> None:
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        log.debug("could not remove migration buttons")


def _format_last_run(last) -> str:
    if last is None:
        return "Last run: none"
    seq, duration, cost, is_error, _started = last
    seq_s = f"{int(seq):06d}" if seq is not None else "?"
    cost_s = f"${cost}" if cost is not None else "?"
    dur_s = f"{duration} ms" if duration is not None else "in progress"
    flag = "error" if is_error else "ok"
    return f"Last run: message {seq_s}, {dur_s}, {cost_s}, {flag}"


def run_polling() -> None:
    apply_db_host()
    configure_logging()
    token = telegram_token()
    allowed = allowed_chat_id()
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set")
    if not allowed:
        raise SystemExit("TELEGRAM_ALLOWED_CHAT_ID is not set")
    notifier = TelegramNotifier()
    bot = FinanceBot(
        allowed=allowed,
        notifier=notifier,
        state=BotState.load(state_path()),
        album_quiet=album_quiet_s(),
    )

    async def post_init(application: Application) -> None:
        notifier.bind(application.bot)
        await bot.start()

    application = Application.builder().token(token).post_init(post_init).build()
    application.add_handler(CommandHandler("new", bot.on_new))
    application.add_handler(CommandHandler("status", bot.on_status))
    application.add_handler(CommandHandler("splitwise", bot.on_splitwise))
    application.add_handler(CallbackQueryHandler(bot.on_callback))
    application.add_handler(
        MessageHandler(
            filters.TEXT
            | filters.PHOTO
            | filters.Document.ALL
            | filters.CAPTION
            | filters.COMMAND,
            bot.on_message,
        )
    )
    log.info("bot polling started")
    application.run_polling(drop_pending_updates=False, allowed_updates=Update.ALL_TYPES)
