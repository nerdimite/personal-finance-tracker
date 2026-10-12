"""Reject proposal SQL and agent session changes that must never run.

Comments are ignored. Dollar-quoted function bodies stay visible so a payload
hidden in a DO block is still seen. A second pass keeps string literals so
EXECUTE 'COPY ... TO PROGRAM' cannot slip through.
"""

from __future__ import annotations

import re

from ft_db.sql_script import split_sql

_VISIBLE_RISKS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^COPY\b|\bCOPY\s+\(|\bCOPY\s+\S+\s+(?:TO|FROM)\b", re.IGNORECASE), "COPY"),
    (re.compile(r"^DO\b|\bDO\s+\$", re.IGNORECASE), "DO"),
    (re.compile(r"\bCREATE\s+EXTENSION\b", re.IGNORECASE), "CREATE EXTENSION"),
    (re.compile(r"\bSECURITY\s+DEFINER\b", re.IGNORECASE), "SECURITY DEFINER"),
    (re.compile(r"\bOWNER\s+TO\b", re.IGNORECASE), "ALTER OWNER"),
    (
        re.compile(r"\b(?:CREATE|ALTER|DROP)\s+(?:ROLE|USER|GROUP)\b", re.IGNORECASE),
        "role statement",
    ),
    (re.compile(r"\bSET\s+ROLE\b|\bSET\s+SESSION\s+AUTHORIZATION\b", re.IGNORECASE), "SET ROLE"),
    (re.compile(r"\bset_config\s*\(", re.IGNORECASE), "set_config"),
    (re.compile(r"\bpg_read_[A-Za-z0-9_]*", re.IGNORECASE), "pg_read_*"),
    (re.compile(r"\blo_[A-Za-z0-9_]*\s*\(", re.IGNORECASE), "lo_*"),
    (re.compile(r"\bdblink[A-Za-z0-9_]*\s*\(", re.IGNORECASE), "dblink"),
    (re.compile(r"\bCREATE\s+PROCEDURE\b|^CALL\b", re.IGNORECASE), "CREATE PROCEDURE or CALL"),
)

# Matched with comments removed but string literals kept, so dynamic SQL is caught.
_HIDDEN_RISKS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bTO\s+PROGRAM\b", re.IGNORECASE), "COPY TO PROGRAM"),
    (re.compile(r"\bpg_read_file\b|\bpg_read_binary_file\b", re.IGNORECASE), "pg_read_*"),
    (re.compile(r"\blo_export\b|\blo_import\b|\blo_from_bytea\b", re.IGNORECASE), "lo_*"),
    (re.compile(r"\bdblink[A-Za-z0-9_]*\s*\(", re.IGNORECASE), "dblink"),
    (re.compile(r"\bSECURITY\s+DEFINER\b", re.IGNORECASE), "SECURITY DEFINER"),
    (re.compile(r"\bset_config\s*\(", re.IGNORECASE), "set_config"),
    (re.compile(r"\bSET\s+ROLE\b", re.IGNORECASE), "SET ROLE"),
)


def mask_sql(statement: str, *, blank_dollar: bool = True) -> str:
    """Blank comments and quoted strings. Dollar bodies are blanked unless disabled."""
    out: list[str] = []
    i = 0
    n = len(statement)
    dollar: str | None = None

    while i < n:
        if dollar is not None:
            if statement.startswith(dollar, i):
                if blank_dollar:
                    out.append(" " * len(dollar))
                else:
                    out.append(dollar)
                i += len(dollar)
                dollar = None
            else:
                out.append(" " if blank_dollar else statement[i])
                i += 1
            continue

        ch = statement[i]
        nxt = statement[i + 1] if i + 1 < n else ""

        if ch == "-" and nxt == "-":
            while i < n and statement[i] != "\n":
                out.append(" ")
                i += 1
            continue

        if ch == "/" and nxt == "*":
            while i < n - 1 and not (statement[i] == "*" and statement[i + 1] == "/"):
                out.append(" ")
                i += 1
            if i < n - 1:
                out.append(" ")
                out.append(" ")
                i += 2
            continue

        if ch in {"'", '"'}:
            quote = ch
            out.append(" ")
            i += 1
            while i < n:
                out.append(" ")
                if statement[i] == quote:
                    if i + 1 < n and statement[i + 1] == quote:
                        out.append(" ")
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue

        if ch == "$":
            match = re.match(r"\$[A-Za-z0-9_]*\$", statement[i:])
            if match:
                dollar = match.group(0)
                if blank_dollar:
                    out.append(" " * len(dollar))
                else:
                    out.append(dollar)
                i += len(dollar)
                continue

        out.append(ch)
        i += 1

    return "".join(out)


def normalize_sql(statement: str) -> str:
    return re.sub(r"\s+", " ", mask_sql(statement)).strip()


def expose_sql(statement: str) -> str:
    """Comments and quotes removed; dollar-quoted bodies kept."""
    return re.sub(r"\s+", " ", mask_sql(statement, blank_dollar=False)).strip()


def comments_removed(statement: str) -> str:
    """Drop comments only, so risks hidden inside string literals remain visible."""
    out: list[str] = []
    i = 0
    n = len(statement)
    while i < n:
        ch = statement[i]
        nxt = statement[i + 1] if i + 1 < n else ""
        if ch == "-" and nxt == "-":
            while i < n and statement[i] != "\n":
                out.append(" ")
                i += 1
            continue
        if ch == "/" and nxt == "*":
            while i < n - 1 and not (statement[i] == "*" and statement[i + 1] == "/"):
                out.append(" ")
                i += 1
            if i < n - 1:
                out.append(" ")
                out.append(" ")
                i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def headerless_allowed(version: str) -> bool:
    """Only the bootstrap file 001 may omit a -- status: header."""
    return version == "001"


def proposal_refusal(sql_text: str) -> str | None:
    """Return a short reason to refuse this proposal, or None."""
    statements = split_sql(sql_text or "")
    if not statements:
        return None
    for index, statement in enumerate(statements, start=1):
        reason = _statement_refusal(statement)
        if reason:
            return f"statement {index}: {reason} is not allowed in a proposal"
        view = _view_refusal(statement)
        if view:
            return f"statement {index}: {view}"
    # Catches the same risks when they are split across statements or hidden in strings.
    hidden = comments_removed(sql_text or "")
    for pattern, label in _HIDDEN_RISKS:
        if pattern.search(hidden):
            return f"{label} is not allowed in a proposal"
    return None


def session_control_reason(statement: str) -> str | None:
    """Reject SET and set_config in agent SQL, including inside DO bodies."""
    exposed = expose_sql(statement)
    if not exposed:
        return None
    first = exposed.split(None, 1)[0].upper()
    if first == "SET":
        return "SET is not allowed"
    if re.search(r"\bset_config\s*\(", exposed, re.IGNORECASE):
        return "set_config is not allowed"
    if re.search(r"\bSET\s+ROLE\b|\bSET\s+SESSION\s+AUTHORIZATION\b", exposed, re.IGNORECASE):
        return "SET ROLE is not allowed"
    hidden = comments_removed(statement)
    if re.search(r"\bset_config\s*\(", hidden, re.IGNORECASE):
        return "set_config is not allowed"
    return None


def _statement_refusal(statement: str) -> str | None:
    exposed = expose_sql(statement)
    if not exposed:
        return None
    for pattern, label in _VISIBLE_RISKS:
        if pattern.search(exposed):
            return label
    if exposed.split(None, 1)[0].upper() == "SET":
        return "SET"
    return None


def _view_refusal(statement: str) -> str | None:
    normalized = normalize_sql(statement)
    if not re.match(r"^CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\b", normalized, re.IGNORECASE):
        return None
    if re.search(
        r"\bsecurity_invoker\s*=\s*(?:true|on|1|yes)\b",
        normalized,
        re.IGNORECASE,
    ):
        return None
    return "CREATE VIEW must set security_invoker = true"

