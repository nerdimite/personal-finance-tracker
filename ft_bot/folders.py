"""Write workspace/messages/NNNNNN/{message.md,meta.json,files}."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from ft_bot.models import Incoming

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(name: str) -> str:
    base = Path(name).name
    cleaned = _UNSAFE.sub("_", base).strip("._")
    return (cleaned or "file")[:120]


def display_text(incoming: Incoming) -> str:
    parts = [part for part in (incoming.text, incoming.caption) if part]
    return "\n".join(parts)


def write_message_folder(root: Path, seq: int, incoming: Incoming) -> Path:
    """Create the folder. `root` is the messages directory, not the workspace."""
    folder = root / f"{seq:06d}"
    folder.mkdir(parents=True, exist_ok=False)
    try:
        return _write_folder(folder, seq, incoming)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def _write_folder(folder: Path, seq: int, incoming: Incoming) -> Path:
    written: list[dict[str, str]] = []
    for index, attachment in enumerate(incoming.attachments, start=1):
        filename = f"{index:02d}_{safe_filename(attachment.filename)}"
        (folder / filename).write_bytes(attachment.data)
        written.append({"file": filename, "kind": attachment.kind})
    meta = {
        "seq": seq,
        "telegram_message_ids": incoming.telegram_message_ids,
        "chat_id": incoming.chat_id,
        "media_group_id": incoming.media_group_id,
        "received_at": incoming.received_at.isoformat(),
        "text": incoming.text,
        "caption": incoming.caption,
        "attachments": written,
        "synthetic_action": incoming.synthetic_action,
        "synthetic_target_seq": incoming.synthetic_target_seq,
    }
    (folder / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    (folder / "message.md").write_text(_message_md(seq, incoming, written))
    return folder


def _message_md(seq: int, incoming: Incoming, written: list[dict[str, str]]) -> str:
    ids = ", ".join(str(item) for item in incoming.telegram_message_ids) or "(none)"
    lines = [
        f"# Message {seq:06d}",
        "",
        f"- received_at: {incoming.received_at.isoformat()}",
        f"- telegram_message_ids: {ids}",
        f"- chat_id: {incoming.chat_id if incoming.chat_id is not None else ''}",
        f"- media_group_id: {incoming.media_group_id or ''}",
        "",
        "## Text",
        "",
        incoming.text if incoming.text else "(none)",
        "",
    ]
    if incoming.caption:
        lines.extend(["## Caption", "", incoming.caption, ""])
    lines.append("## Attachments")
    lines.append("")
    if written:
        for item in written:
            lines.append(f"- {item['file']} ({item['kind']})")
    else:
        lines.append("(none)")
    lines.append("")
    return "\n".join(lines)
