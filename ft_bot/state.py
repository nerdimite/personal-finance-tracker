"""Session id and the telegram-id index. One JSON file, replaced atomically."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


def local_today() -> str:
    return datetime.now().astimezone().date().isoformat()


@dataclass
class BotState:
    path: Path
    session_id: str
    session_date: str
    established: bool
    telegram_ids: dict[str, int] = field(default_factory=dict)
    pending_correction_seq: int | None = None
    announced_migrations: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> BotState:
        if not path.is_file():
            state = cls(
                path=path,
                session_id=str(uuid.uuid4()),
                session_date=local_today(),
                established=False,
            )
            state.save()
            return state
        try:
            raw = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            state = cls(
                path=path,
                session_id=str(uuid.uuid4()),
                session_date=local_today(),
                established=False,
            )
            state.save()
            return state
        session_id = str(raw.get("session_id") or uuid.uuid4())
        session_date = str(raw.get("session_date") or local_today())
        established = bool(raw.get("established"))
        ids: dict[str, int] = {}
        for key, value in (raw.get("telegram_ids") or {}).items():
            try:
                ids[str(key)] = int(value)
            except (TypeError, ValueError):
                continue
        pending = raw.get("pending_correction_seq")
        try:
            pending_seq = int(pending) if pending else None
        except (TypeError, ValueError):
            pending_seq = None
        announced = [str(item) for item in (raw.get("announced_migrations") or []) if item]
        state = cls(
            path=path,
            session_id=session_id,
            session_date=session_date,
            established=established,
            telegram_ids=ids,
            pending_correction_seq=pending_seq,
            announced_migrations=announced,
        )
        state.roll_if_needed(local_today())
        return state

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "session_id": self.session_id,
            "session_date": self.session_date,
            "established": self.established,
            "telegram_ids": self.telegram_ids,
            "pending_correction_seq": self.pending_correction_seq,
            "announced_migrations": self.announced_migrations,
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n")
        tmp.replace(self.path)

    def roll_if_needed(self, today: str) -> None:
        if self.session_date == today and self.session_id:
            return
        self.session_id = str(uuid.uuid4())
        self.session_date = today
        self.established = False
        self.save()

    def claim_for_run(self, today: str | None = None) -> tuple[str, str]:
        """Return (`session-id` or `resume`, session uuid) and persist it."""
        self.roll_if_needed(today or local_today())
        mode = "resume" if self.established else "session-id"
        self.save()
        return mode, self.session_id

    def new_session(self) -> str:
        self.session_id = str(uuid.uuid4())
        self.session_date = local_today()
        self.established = False
        self.save()
        return self.session_id

    def note_finished(self, requested_id: str, actual_id: str | None, got_session: bool) -> None:
        """Mark the session resumable, unless /new replaced it during the run."""
        if self.session_id != requested_id:
            return
        if actual_id and actual_id != requested_id:
            self.session_id = actual_id
        if got_session:
            self.established = True
        self.save()

    def reserve(self, telegram_id: int) -> bool:
        """Mark a telegram id as seen before its seq exists. False if it was already seen."""
        key = str(telegram_id)
        if key in self.telegram_ids:
            return False
        self.telegram_ids[key] = 0
        self.save()
        return True

    def release(self, telegram_id: int) -> None:
        """Drop a reservation that never received a seq."""
        key = str(telegram_id)
        if self.telegram_ids.get(key) == 0:
            del self.telegram_ids[key]
            self.save()

    def remember(self, telegram_id: int, seq: int) -> None:
        self.telegram_ids[str(telegram_id)] = seq
        self.save()

    def seq_for(self, telegram_id: int) -> int | None:
        value = self.telegram_ids.get(str(telegram_id))
        return int(value) if value is not None else None

    def expect_correction(self, seq: int) -> None:
        self.pending_correction_seq = seq
        self.save()

    def clear_correction(self) -> None:
        self.pending_correction_seq = None
        self.save()

    def note_announced(self, migration_id: str) -> None:
        if migration_id in self.announced_migrations:
            return
        self.announced_migrations.append(migration_id)
        self.save()
