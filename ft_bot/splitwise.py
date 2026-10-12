"""Copy-ready Splitwise text. The bot writes status; it does not call the agent."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


_SPLIT = {
    "equal": "split equally",
    "exact": "split by exact amounts",
    "percent": "split by percent",
    "shares": "split by shares",
}


@dataclass(frozen=True)
class SplitwiseItem:
    id: int
    description: str
    total_amount: Decimal
    paid_by: str
    split_method: str
    shares: tuple


def format_splitwise_item(item: SplitwiseItem) -> str:
    """Plain text a person can copy into Splitwise."""
    shares = _share_list(item.shares)
    phrase = _SPLIT.get(item.split_method, f"split {item.split_method}")
    if shares:
        phrase = f"{phrase} ({len(shares)})"
    lines = [
        item.description,
        f"{_rupee(item.total_amount)} · paid by {item.paid_by or 'me'} · {phrase}",
    ]
    for share in shares:
        if _is_me(share):
            continue
        amount = share.get("amount")
        if amount is None or amount == "":
            continue
        name = str(share.get("name") or "Someone").strip() or "Someone"
        if str(name).strip().lower() == "others":
            lines.append(f"Others owe {_rupee(amount)}")
        else:
            lines.append(f"{name} owes {_rupee(amount)}")
    return "\n".join(lines)


def with_splitwise_hint(text: str, count: int) -> str:
    """Append the post-run hint. A zero count leaves the reply unchanged."""
    if count <= 0:
        return text
    hint = f"🧾 {count} added to Splitwise queue — /splitwise"
    body = (text or "").rstrip()
    if not body:
        return hint
    return f"{body}\n\n{hint}"


def _share_list(shares) -> list:
    if isinstance(shares, list | tuple):
        return [share for share in shares if isinstance(share, dict)]
    return []


def _is_me(share: dict) -> bool:
    return str(share.get("name") or "").strip().lower() == "me"


def _rupee(value: object) -> str:
    amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"₹{amount:.2f}"
