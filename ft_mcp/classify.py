"""Label a migration additive or destructive.

The label decides auto-apply only. It does not decide whether the SQL is safe
to propose: COPY, DO, and the other review refusals are rejected before this
runs. Strings and comments are masked so a column named type, or the word DROP
inside a default literal, is not treated as DDL.
"""

from __future__ import annotations

import re

from ft_mcp.sqltext import normalize_sql


class Classification(str):
    """`additive` or `destructive`. `notes` explains a destructive label."""

    notes: list[str]

    def __new__(cls, value: str, notes: list[str] | None = None):
        obj = str.__new__(cls, value)
        obj.notes = list(notes or [])
        return obj

    @property
    def kind(self) -> str:
        return "additive" if str(self) == "additive" else "destructive"


def classify_statements(statements: list[str]) -> Classification:
    notes: list[str] = []
    destructive = False
    for index, statement in enumerate(statements, start=1):
        normalized = normalize_sql(statement)
        if not normalized:
            continue
        reason = _destructive_reason(normalized)
        if reason:
            destructive = True
            notes.append(f"statement {index}: {reason}")
            continue
        if _is_additive(normalized):
            continue
        destructive = True
        notes.append(f"statement {index}: not on the additive list")
    if not destructive:
        return Classification("additive")
    return Classification("destructive", notes)


def _destructive_reason(sql: str) -> str | None:
    if re.match(r"^CREATE\s+OR\s+REPLACE\b", sql, re.IGNORECASE):
        return "CREATE OR REPLACE replaces an existing object"
    if re.match(r"^CREATE\s+VIEW\b", sql, re.IGNORECASE) and not _security_invoker(sql):
        return "CREATE VIEW without security_invoker"
    if re.search(r"\bDROP\b", sql, re.IGNORECASE):
        return "DROP"
    if re.search(r"\bTRUNCATE\b", sql, re.IGNORECASE):
        return "TRUNCATE"
    if re.search(r"\bRENAME\b", sql, re.IGNORECASE):
        return "RENAME"
    if re.search(r"\bALTER\s+TYPE\b", sql, re.IGNORECASE):
        return "ALTER TYPE"
    if re.search(r"\bALTER\s+COLUMN\b[^;]*\bTYPE\b", sql, re.IGNORECASE):
        return "ALTER COLUMN TYPE"
    if re.search(r"\bSET\s+DATA\s+TYPE\b", sql, re.IGNORECASE):
        return "SET DATA TYPE"
    if re.search(r"\bSET\s+NOT\s+NULL\b", sql, re.IGNORECASE):
        return "SET NOT NULL"
    actions = _alter_actions(sql)
    if actions is None:
        return None
    for action in actions:
        if not _is_add_column(action):
            return "ALTER action is not ADD COLUMN"
        if re.search(r"\bNOT\s+NULL\b", action, re.IGNORECASE) and not re.search(
            r"\bDEFAULT\b", action, re.IGNORECASE
        ):
            return "ADD COLUMN NOT NULL without a default"
    return None


def _is_additive(sql: str) -> bool:
    if re.match(r"^CREATE\s+TABLE\b", sql, re.IGNORECASE):
        return True
    if re.match(r"^CREATE\s+(UNIQUE\s+)?INDEX\b", sql, re.IGNORECASE):
        return True
    if re.match(r"^CREATE\s+VIEW\b", sql, re.IGNORECASE) and _security_invoker(sql):
        return True
    actions = _alter_actions(sql)
    return actions is not None and all(_is_add_column(action) for action in actions)


def _security_invoker(sql: str) -> bool:
    return re.search(
        r"\bsecurity_invoker\s*=\s*(?:true|on|1|yes)\b",
        sql,
        re.IGNORECASE,
    ) is not None


# ADD CONSTRAINT / ADD PRIMARY KEY are not column adds. The COLUMN keyword is optional.
_NOT_A_COLUMN = {"CONSTRAINT", "PRIMARY", "UNIQUE", "CHECK", "FOREIGN", "EXCLUDE"}


def _is_add_column(action: str) -> bool:
    match = re.match(
        r"^ADD\s+(?:COLUMN\s+)?(?:IF\s+NOT\s+EXISTS\s+)?(\S+)",
        action,
        re.IGNORECASE,
    )
    if not match:
        return False
    if re.match(r"^ADD\s+COLUMN\b", action, re.IGNORECASE):
        return True
    token = match.group(1).strip('"').upper()
    return token not in _NOT_A_COLUMN


def _alter_actions(sql: str) -> list[str] | None:
    """Top-level actions of one ALTER TABLE, or None when this is not ALTER TABLE."""
    match = re.match(r"^ALTER\s+TABLE\s+(?:ONLY\s+|IF\s+EXISTS\s+)?(.+)$", sql, re.IGNORECASE)
    if not match:
        return None
    rest = match.group(1)
    name, _, tail = _take_relation(rest)
    if not name or not tail:
        return [rest.strip()] if rest.strip() else None
    return [part.strip() for part in _split_top_level(tail) if part.strip()]


def _take_relation(text: str) -> tuple[str, int, str]:
    """Read schema.table or "quoted" name. Returns (name, end, remainder)."""
    i = 0
    n = len(text)
    parts: list[str] = []
    while i < n:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            break
        if text[i] == '"':
            end = i + 1
            while end < n:
                if text[end] == '"':
                    if end + 1 < n and text[end + 1] == '"':
                        end += 2
                        continue
                    end += 1
                    break
                end += 1
            parts.append(text[i:end])
            i = end
        else:
            end = i
            while end < n and (text[end].isalnum() or text[end] in {"_", "$"}):
                end += 1
            if end == i:
                break
            parts.append(text[i:end])
            i = end
        while i < n and text[i].isspace():
            i += 1
        if i < n and text[i] == ".":
            parts.append(".")
            i += 1
            continue
        break
    name = "".join(parts)
    return name, i, text[i:].strip()


def _split_top_level(text: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")" and depth:
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:index])
            start = index + 1
    parts.append(text[start:])
    return parts
