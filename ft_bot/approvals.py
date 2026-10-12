"""Import and migration approvals attached after an agent run.

Import buttons go on the reply. Migration proposals are a separate message.
Apply and reject call ft_mcp directly; they do not ask the agent.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from ft_bot.store import migration_tool_outputs, run_changed_ledger, summarized_batch_ids
from ft_db.migrate import list_migration_files, migration_status

log = logging.getLogger(__name__)

TELEGRAM_LIMIT = 4096
SQL_PREVIEW = 3500
_MIGRATION_ID = re.compile(r"^[A-Za-z0-9_-]{1,58}$")


@dataclass(frozen=True)
class MigrationProposal:
    id: str
    name: str
    classification: str
    reason: str
    sql: str


def split_telegram(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Split on a newline when one falls in the second half of the window."""
    body = text or ""
    if len(body) <= limit:
        return [body]
    chunks: list[str] = []
    rest = body
    while rest:
        if len(rest) <= limit:
            chunks.append(rest)
            break
        cut = rest.rfind("\n", 0, limit + 1)
        if cut < limit // 2:
            cut = limit
        chunks.append(rest[:cut].rstrip("\n"))
        rest = rest[cut:].lstrip("\n")
    return [chunk for chunk in chunks if chunk] or [""]


def ledger_changed(run_id: int) -> bool:
    """Edit and Undo are offered only when this run wrote transactions or postings."""
    try:
        return run_changed_ledger(run_id)
    except Exception:
        log.exception("could not look up ledger changes for run %s", run_id)
        return False


def batches_for_run(run_id: int) -> list[int]:
    try:
        return summarized_batch_ids(run_id)
    except Exception:
        log.exception("could not look up import batches for run %s", run_id)
        return []


def migrations_to_announce(run_id: int, announced: set[str]) -> list[MigrationProposal]:
    """Pending proposals from this run's tool calls, plus any pending file not yet shown."""
    wanted: list[str] = []
    seen: set[str] = set()

    def add(migration_id: str) -> None:
        if not migration_id or migration_id in announced or migration_id in seen:
            return
        if _MIGRATION_ID.fullmatch(migration_id) is None:
            return
        seen.add(migration_id)
        wanted.append(migration_id)

    try:
        outputs = migration_tool_outputs(run_id)
    except Exception:
        log.exception("could not read migration tool calls for run %s", run_id)
        outputs = []
    for output in outputs:
        info = parse_proposal_output(output)
        if info and info.get("status") == "pending":
            add(str(info.get("id") or ""))

    try:
        files = list_migration_files()
    except Exception:
        log.exception("could not list migration files")
        files = []
    for version, _name, path in files:
        try:
            script = path.read_text()
        except OSError:
            continue
        if migration_status(script) == "pending":
            add(version)

    proposals: list[MigrationProposal] = []
    by_version = {version: path for version, _name, path in files}
    for migration_id in wanted:
        path = by_version.get(migration_id)
        if path is None:
            for version, _name, candidate in files:
                if candidate.stem == migration_id or version == migration_id:
                    path = candidate
                    break
        if path is None:
            log.info("pending migration %s has no file", migration_id)
            continue
        proposal = read_proposal(path)
        if proposal is not None and proposal.id not in announced:
            proposals.append(proposal)
    return proposals


def parse_proposal_output(output: str) -> dict | None:
    text = (output or "").strip()
    if not text or not text.startswith("{"):
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def read_proposal(path) -> MigrationProposal | None:
    try:
        script = path.read_text()
    except OSError:
        return None
    if migration_status(script) != "pending":
        return None
    classification = _header(script, "classification") or "unknown"
    reason = _header(script, "reason") or ""
    version, name = _identity(path)
    return MigrationProposal(
        id=version,
        name=name,
        classification=classification,
        reason=reason,
        sql=_sql_body(script),
    )


def migration_text(proposal: MigrationProposal) -> tuple[str, str | None]:
    """Message text, and the full SQL when it is long enough to send as a file."""
    sql = proposal.sql.strip()
    attachment = sql if len(sql) > SQL_PREVIEW else None
    shown = sql if attachment is None else sql[:SQL_PREVIEW].rstrip() + "\n… full SQL attached"
    text = (
        f"Migration {proposal.id} {proposal.name}\n"
        f"Classification: {proposal.classification}\n"
        f"Reason: {proposal.reason or '(none)'}\n\n"
        f"SQL:\n{shown}"
    )
    return text, attachment


def import_commit_text(batch_id: int) -> str:
    return (
        f"User approved import batch {batch_id}: commit it now "
        "(status→committed) and reply with a short summary."
    )


def import_discard_text(batch_id: int) -> str:
    return (
        f"User discarded import batch {batch_id}: mark it rejected, "
        "insert nothing, reply briefly."
    )


def apply_failure_text(migration_id: str, error: str) -> str:
    detail = " ".join(error.split())[:500] or "apply failed"
    return (
        f"Applying migration {migration_id} failed: {detail}. "
        "Explain the error briefly. Do not retry the migration unless the user asks."
    )


def undo_run_text(run_id: int) -> str:
    return (
        f"Undo run {run_id}: void every transaction that run inserted, "
        f"and restore the previous values of rows it updated, "
        f"using audit_log where run_id = {run_id}. Reply with what changed."
    )


def correction_text(seq: int, text: str) -> str:
    return f"Correction for message {seq:06d}: {text}"


def _header(script: str, key: str) -> str | None:
    pattern = re.compile(rf"^--\s*{re.escape(key)}:\s*(.*)$", re.IGNORECASE)
    for line in script.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = pattern.match(stripped)
        if match:
            value = match.group(1).strip()
            return value or None
        if not stripped.startswith("--"):
            return None
    return None


def _sql_body(script: str) -> str:
    lines: list[str] = []
    started = False
    for line in script.splitlines():
        if not started and line.strip().startswith("--"):
            continue
        started = True
        lines.append(line)
    return "\n".join(lines).strip()


def _identity(path) -> tuple[str, str]:
    match = re.match(r"^(\d{3,})_(.+)\.sql$", path.name)
    if match:
        return match.group(1), match.group(2)
    return path.stem, path.stem
