"""Parse a Claude Code stream-json log into a run and its tool calls."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


_OUTPUT_CAP = 200_000


@dataclass
class ParsedTool:
    seq: int
    name: str
    tool_use_id: str
    input: dict
    output: str | None
    is_error: bool
    duration_ms: int | None


@dataclass
class ParsedRun:
    session_id: str | None = None
    duration_ms: int | None = None
    num_turns: int | None = None
    cost_usd: Decimal | None = None
    is_error: bool = False
    result_text: str = ""
    tool_calls: list[ParsedTool] = field(default_factory=list)
    memory_paths: object | None = None
    saw_result: bool = False
    saw_init: bool = False


def parse_stream(text: str) -> ParsedRun:
    """Parse raw stdout. Non-JSON lines are ignored."""
    events: list[dict] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return parse_events(events)


def parse_events(events: list[dict]) -> ParsedRun:
    parsed = ParsedRun()
    pending: dict[str, ParsedTool] = {}
    started: dict[str, datetime] = {}
    order: list[str] = []
    last_text = ""

    for event in events:
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            parsed.saw_init = True
            parsed.memory_paths = event.get("memory_paths")
            if event.get("session_id"):
                parsed.session_id = str(event["session_id"])
            continue
        if kind == "assistant":
            for block in _content_blocks(event):
                if block.get("type") == "text" and block.get("text"):
                    last_text = str(block["text"])
                elif block.get("type") == "tool_use":
                    tool_id = str(block.get("id") or "")
                    if not tool_id or tool_id in pending:
                        continue
                    raw_input = block.get("input")
                    tool_input = raw_input if isinstance(raw_input, dict) else {"value": raw_input}
                    tool_input = _strip_nuls(tool_input)
                    tool = ParsedTool(
                        seq=len(order) + 1,
                        name=str(block.get("name") or "unknown"),
                        tool_use_id=tool_id,
                        input=tool_input,
                        output=None,
                        is_error=False,
                        duration_ms=None,
                    )
                    pending[tool_id] = tool
                    order.append(tool_id)
                    stamp = _parse_ts(event.get("timestamp"))
                    if stamp is not None:
                        started[tool_id] = stamp
            continue
        if kind == "user":
            end = _parse_ts(event.get("timestamp"))
            for block in _content_blocks(event):
                if block.get("type") != "tool_result":
                    continue
                tool_id = str(block.get("tool_use_id") or "")
                tool = pending.get(tool_id)
                if tool is None:
                    continue
                tool.output = _cap(_block_text(block.get("content")))
                tool.is_error = bool(block.get("is_error"))
                start = started.get(tool_id)
                if start is not None and end is not None:
                    delta = int((end - start).total_seconds() * 1000)
                    tool.duration_ms = delta if delta >= 0 else None
            continue
        if kind == "result":
            parsed.saw_result = True
            if event.get("session_id"):
                parsed.session_id = str(event["session_id"])
            parsed.duration_ms = _as_int(event.get("duration_ms"))
            parsed.num_turns = _as_int(event.get("num_turns"))
            parsed.cost_usd = _as_decimal(event.get("total_cost_usd"))
            if parsed.cost_usd is None:
                parsed.cost_usd = _as_decimal(event.get("cost_usd"))
            subtype = str(event.get("subtype") or "")
            parsed.is_error = bool(event.get("is_error")) or (
                bool(subtype) and subtype != "success"
            )
            result = event.get("result")
            parsed.result_text = _strip_nuls(result if isinstance(result, str) else last_text)
            continue

    if not parsed.result_text:
        parsed.result_text = _strip_nuls(last_text)
    elif isinstance(parsed.result_text, str):
        parsed.result_text = _strip_nuls(parsed.result_text)
    parsed.tool_calls = [pending[tool_id] for tool_id in order if tool_id in pending]
    return parsed


def _content_blocks(event: dict) -> list[dict]:
    message = event.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if isinstance(content, list):
        return [block for block in content if isinstance(block, dict)]
    return []


def _block_text(content: object) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
                elif "text" in block:
                    parts.append(str(block.get("text") or ""))
                else:
                    parts.append(json.dumps(block))
        return "\n".join(parts)
    if isinstance(content, dict):
        return json.dumps(content)
    return str(content)


def _strip_nuls(value):
    """Drop NUL bytes. Postgres rejects them in text and jsonb."""
    if isinstance(value, str):
        return value.replace("\x00", "").replace("\\u0000", "")
    if isinstance(value, list):
        return [_strip_nuls(item) for item in value]
    if isinstance(value, dict):
        return {
            _strip_nuls(key) if isinstance(key, str) else key: _strip_nuls(item)
            for key, item in value.items()
        }
    return value


def _cap(text: str) -> str:
    text = _strip_nuls(text)
    if len(text) <= _OUTPUT_CAP:
        return text
    return text[:_OUTPUT_CAP] + "\n… truncated"


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def _as_decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, str)):
        try:
            return Decimal(str(value))
        except Exception:
            return None
    return None
