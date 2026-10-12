"""Stop a test from leaving message folders, run logs, or sequence numbers behind."""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]


def _names(root: Path) -> set[str]:
    if not root.is_dir():
        return set()
    return {path.name for path in root.iterdir()}


@pytest.fixture(autouse=True)
def real_message_tree_unchanged():
    messages = _REPO / "workspace" / "messages"
    runs = _REPO / "logs" / "runs"
    before_messages = _names(messages)
    before_runs = _names(runs)
    yield
    assert _names(messages) == before_messages
    assert _names(runs) == before_runs


@pytest.fixture(autouse=True)
def message_sequence_unchanged():
    try:
        from tests.support import max_message_seq
    except Exception:
        yield
        return
    try:
        before = max_message_seq()
    except Exception:
        yield
        return
    yield
    after = max_message_seq()
    assert after == before, "a test left messages rows behind"
