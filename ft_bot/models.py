"""Values that move from Telegram (or a test) into the runner."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Attachment:
    filename: str
    data: bytes
    kind: str


@dataclass
class Incoming:
    """One user turn. An album is several Telegram messages merged into one of these."""

    chat_id: int | None
    telegram_message_ids: list[int]
    text: str | None
    caption: str | None
    received_at: datetime
    media_group_id: str | None = None
    attachments: list[Attachment] = field(default_factory=list)
    synthetic_action: str | None = None
    synthetic_target_seq: int | None = None
    prompt_override: str | None = None
    file_count: int = 0
    # Set when the folder and messages row already exist (startup recovery).
    stored_seq: int | None = None

    def __post_init__(self) -> None:
        if self.file_count == 0 and self.attachments:
            self.file_count = len(self.attachments)

    @property
    def has_files(self) -> bool:
        return self.file_count > 0


@dataclass
class RunOutcome:
    seq: int
    run_id: int
    result_text: str
    is_error: bool
    tool_calls: int
    session_mode: str
    log_path: str
    memory_paths: object | None = None
