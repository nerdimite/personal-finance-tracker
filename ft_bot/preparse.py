"""Turn saved attachments into sibling files the agent can read.

Passwords from STATEMENT_PASSWORDS are written to qpdf's stdin, never argv.
They are never written to message.md, meta.json, prompts, or logs.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from ft_db.connect import load_env

from ft_bot.folders import safe_filename

log = logging.getLogger(__name__)

_IMAGE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".heic",
    ".heif",
    ".tif",
    ".tiff",
    ".bmp",
}
_TABLE_SUFFIXES = {".xls", ".xlsx", ".xlsm", ".csv"}
_PARSED_HEADER = "<!-- ft-parsed -->"
_ENCRYPTED_NOTE = "PDF is encrypted and no password matched"


@dataclass(frozen=True)
class ParseNote:
    file: str
    ok: bool
    detail: str


def parse_statement_passwords(raw: str) -> list[tuple[str, str]]:
    """JSON object or comma-separated bank=password pairs. Values stay in memory."""
    text = raw.strip()
    if not text:
        return []
    if text.startswith("{"):
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("STATEMENT_PASSWORDS JSON must be an object")
        pairs: list[tuple[str, str]] = []
        for key, value in data.items():
            label = str(key).strip()
            secret = str(value).strip()
            if label and secret:
                pairs.append((label, secret))
        return pairs
    pairs = []
    for part in text.split(","):
        item = part.strip()
        if not item or "=" not in item:
            continue
        label, secret = item.split("=", 1)
        label, secret = label.strip(), secret.strip()
        if label and secret:
            pairs.append((label, secret))
    return pairs


def load_statement_passwords() -> list[tuple[str, str]]:
    load_env()
    raw = os.environ.get("STATEMENT_PASSWORDS", "")
    try:
        return parse_statement_passwords(raw)
    except (json.JSONDecodeError, ValueError):
        log.warning("STATEMENT_PASSWORDS is set but is not valid JSON or bank=password pairs")
        return []


def preparse_message_folder(
    folder: Path,
    passwords: list[tuple[str, str]] | None = None,
) -> list[ParseNote]:
    """Write sibling files and a Parsed section. Failures become notes, not exceptions."""
    meta_path = folder / "meta.json"
    if not meta_path.is_file():
        return []
    meta = json.loads(meta_path.read_text())
    attachments = list(meta.get("attachments") or [])
    if not attachments:
        return []
    if passwords is None:
        passwords = load_statement_passwords()
    notes: list[ParseNote] = []
    for item in attachments:
        name = str(item.get("file") or "")
        kind = str(item.get("kind") or "")
        path = folder / name
        if not name or not path.is_file():
            notes.append(ParseNote(name or "?", False, f"{name or 'attachment'}: file is missing"))
            continue
        try:
            notes.extend(_parse_one(path, kind, passwords))
        except Exception as exc:
            log.warning("preparse failed file=%s error_type=%s", name, type(exc).__name__)
            notes.append(ParseNote(name, False, f"{name}: could not be parsed"))
    _write_notes(folder, meta, notes)
    return notes


def wants_layout_json(text: str) -> bool:
    """True when extracted text is too thin to trust, or looks like aligned columns."""
    stripped = text.strip()
    if len(stripped) < 400:
        return True
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 8:
        return True
    piped = sum(1 for line in lines if line.count("|") >= 2)
    gutter = sum(1 for line in lines if re.search(r"\S\s{2,}\S", line))
    return piped >= 5 or gutter >= 5


def _parse_one(path: Path, kind: str, passwords: list[tuple[str, str]]) -> list[ParseNote]:
    suffix = path.suffix.lower()
    if kind == "image" or suffix in _IMAGE_SUFFIXES:
        return [ParseNote(path.name, True, f"image: {path.name} (not parsed)")]
    if suffix == ".numbers":
        return _parse_numbers(path)
    if suffix in _TABLE_SUFFIXES:
        return _parse_table(path)
    if suffix == ".pdf":
        return _parse_pdf(path, passwords)
    return _parse_with_lit(path, path, decrypted_with=None)


def _parse_table(path: Path) -> list[ParseNote]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return [_normalize_csv(path)]
    engine = "xlrd" if suffix == ".xls" else "openpyxl"
    book = pd.ExcelFile(path, engine=engine)
    notes: list[ParseNote] = []
    multiple = len(book.sheet_names) > 1
    for sheet in book.sheet_names:
        frame = pd.read_excel(book, sheet_name=sheet, header=None, dtype=str)
        frame = frame.fillna("")
        if frame.empty or not any(str(value).strip() for value in frame.to_numpy().ravel()):
            notes.append(ParseNote(path.name, True, f"{path.name}: sheet {sheet} is empty"))
            continue
        out_name = _csv_name(path, sheet if multiple else None)
        out = path.with_name(out_name)
        frame.to_csv(out, index=False, header=False, lineterminator="\n")
        label = f", sheet: {sheet}" if multiple else ""
        notes.append(ParseNote(path.name, True, f"normalized csv: {out.name}{label}"))
    if not notes:
        notes.append(ParseNote(path.name, False, f"{path.name}: spreadsheet has no sheets"))
    return notes


def _normalize_csv(path: Path) -> ParseNote:
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    preamble, table, trailer = _split_header_block(raw)
    out = path.with_name(f"{path.stem}.normalized.csv")
    try:
        frame = pd.read_csv(io.StringIO(table), header=None, dtype=str, keep_default_na=False)
        body = frame.to_csv(index=False, header=False, lineterminator="\n")
        if preamble:
            kept = "header block kept"
        else:
            kept = "header row kept"
    except Exception as exc:
        log.warning("csv normalize failed file=%s error_type=%s", path.name, type(exc).__name__)
        body = table if table.endswith("\n") else table + "\n"
        kept = "header block kept; table left as text"
    chunks: list[str] = []
    if preamble:
        chunks.append("\n".join(preamble).rstrip("\n") + "\n\n")
    chunks.append(body if body.endswith("\n") else body + "\n")
    if trailer:
        chunks.append("\n" + "\n".join(trailer).rstrip("\n") + "\n")
    out.write_text("".join(chunks), encoding="utf-8")
    return ParseNote(path.name, True, f"normalized csv: {out.name} ({kept})")


def _split_header_block(raw: str) -> tuple[list[str], str, list[str]]:
    """Bank CSVs often have account lines before the first real table row.

    The table is the first run of rows with the same column count. Lines after
    that run stay as a trailer so a summary section is not forced into the table.
    """
    lines = raw.splitlines()
    for index, line in enumerate(lines):
        count = _field_count(line)
        if count < 2:
            continue
        nxt = next((cursor for cursor in range(index + 1, len(lines)) if lines[cursor].strip()), None)
        if nxt is None or _field_count(lines[nxt]) != count:
            continue
        last = index
        cursor = index
        while cursor < len(lines):
            if not lines[cursor].strip():
                following = next(
                    (item for item in range(cursor + 1, len(lines)) if lines[item].strip()),
                    None,
                )
                if following is None or _field_count(lines[following]) != count:
                    break
                cursor += 1
                continue
            if _field_count(lines[cursor]) != count:
                break
            last = cursor
            cursor += 1
        table = "\n".join(lines[index : last + 1]) + "\n"
        return lines[:index], table, lines[last + 1 :]
    text = raw if raw.endswith("\n") or not raw else raw + "\n"
    return [], text, []


def _field_count(line: str) -> int:
    try:
        return len(next(csv.reader([line])))
    except csv.Error:
        return 0


def _parse_numbers(path: Path) -> list[ParseNote]:
    from numbers_parser import Document

    document = Document(path)
    notes: list[ParseNote] = []
    for sheet in document.sheets:
        for table in sheet.tables:
            rows = _number_rows(table.rows(values_only=True))
            if not rows:
                notes.append(
                    ParseNote(path.name, True, f"{path.name}: table {sheet.name}/{table.name} is empty")
                )
                continue
            out_name = _csv_name(path, f"{sheet.name}__{table.name}")
            out = path.with_name(out_name)
            with out.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle, lineterminator="\n")
                writer.writerows(rows)
            notes.append(
                ParseNote(
                    path.name,
                    True,
                    f"normalized csv: {out.name} (sheet: {sheet.name}, table: {table.name})",
                )
            )
    if not notes:
        notes.append(ParseNote(path.name, False, f"{path.name}: Numbers file has no tables"))
    return notes


def _number_rows(rows: list[list[object]]) -> list[list[str]]:
    rendered: list[list[str]] = []
    for row in rows:
        rendered.append([_cell_text(value) for value in row])
    while rendered and all(not cell.strip() for cell in rendered[-1]):
        rendered.pop()
    return rendered


def _cell_text(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def _parse_pdf(path: Path, passwords: list[tuple[str, str]]) -> list[ParseNote]:
    if not _needs_password(path):
        return _parse_with_lit(path, path, decrypted_with=None)
    unlocked, label = _decrypt_pdf(path, passwords)
    if unlocked is None:
        empty = not passwords
        detail = _ENCRYPTED_NOTE + ("; STATEMENT_PASSWORDS is empty" if empty else "")
        return [ParseNote(path.name, False, f"{path.name}: {detail}")]
    public = _public_label(label, passwords)
    notes = _parse_with_lit(unlocked, path, decrypted_with=public)
    if any(note.ok and note.detail.startswith("parsed text:") for note in notes):
        copy = f"decrypted copy: {unlocked.name}"
    else:
        copy = f"decrypted copy: {unlocked.name}, decrypted with: {public}"
    return [ParseNote(path.name, True, copy), *notes]


def _decrypt_pdf(path: Path, passwords: list[tuple[str, str]]) -> tuple[Path | None, str]:
    if shutil_which("qpdf") is None:
        log.warning("qpdf is not installed; cannot decrypt %s", path.name)
        return None, ""
    out = path.with_name(f"{path.stem}.decrypted.pdf")
    for label, secret in passwords:
        if _qpdf_decrypt(path, out, secret):
            return out, label
        out.unlink(missing_ok=True)
    return None, ""


def _qpdf_decrypt(source: Path, dest: Path, secret: str) -> bool:
    result = _run(
        ["qpdf", "--password-file=-", "--decrypt", str(source), str(dest)],
        stdin=secret.encode() + b"\n",
    )
    return result is not None and result.returncode == 0 and dest.is_file()


def _needs_password(path: Path) -> bool:
    if shutil_which("qpdf") is None:
        return False
    result = _run(["qpdf", "--requires-password", str(path)])
    if result is None:
        return False
    return result.returncode == 0


def _parse_with_lit(source: Path, named: Path, decrypted_with: str | None) -> list[ParseNote]:
    """`decrypted_with` is the public bank label, never the password."""
    if shutil_which("lit") is None:
        return [ParseNote(named.name, False, f"{named.name}: lit is not installed")]
    text_out = named.with_name(named.name + ".lit.txt")
    json_out = named.with_name(named.name + ".lit.json")
    text_result = _run(
        ["lit", "parse", str(source), "--format", "text", "-o", str(text_out), "-q", "--no-ocr"]
    )
    if text_result is None or text_result.returncode != 0 or not text_out.is_file():
        code = text_result.returncode if text_result is not None else "timeout"
        log.warning("lit text failed file=%s exit=%s", named.name, code)
        text_out.unlink(missing_ok=True)
        return [ParseNote(named.name, False, f"{named.name}: lit parse failed")]
    text = text_out.read_text(encoding="utf-8", errors="replace")
    detail = f"parsed text: {text_out.name}"
    if decrypted_with:
        detail += f", decrypted with: {decrypted_with}"
    notes = [ParseNote(named.name, True, detail)]
    if not wants_layout_json(text):
        json_out.unlink(missing_ok=True)
        return notes
    json_result = _run(
        ["lit", "parse", str(source), "--format", "json", "-o", str(json_out), "-q", "--no-ocr"]
    )
    if json_result is not None and json_result.returncode == 0 and json_out.is_file():
        notes.append(ParseNote(named.name, True, f"parsed json: {json_out.name}"))
    else:
        json_out.unlink(missing_ok=True)
        log.warning("lit json failed file=%s", named.name)
    return notes


def _public_label(label: str, passwords: list[tuple[str, str]]) -> str:
    secret = ""
    for name, value in passwords:
        if name == label:
            secret = value
            break
    if not label or (secret and (label == secret or secret in label)):
        return "(label omitted)"
    return label


def _csv_name(path: Path, suffix: str | None) -> str:
    if not suffix:
        return f"{path.stem}.csv"
    return f"{path.stem}__{safe_filename(suffix)}.csv"


def _write_notes(folder: Path, meta: dict, notes: list[ParseNote]) -> None:
    meta["parsed"] = [{"file": note.file, "ok": note.ok, "detail": note.detail} for note in notes]
    (folder / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    message_path = folder / "message.md"
    if not message_path.is_file():
        return
    body = message_path.read_text()
    if _PARSED_HEADER in body:
        body = body.split(_PARSED_HEADER)[0].rstrip() + "\n"
    lines = [note.detail for note in notes] or ["(none)"]
    bullets = "\n".join(f"- {line}" for line in lines)
    message_path.write_text(body.rstrip() + f"\n\n{_PARSED_HEADER}\n\n{bullets}\n")


def _run(argv: list[str], stdin: bytes | None = None) -> subprocess.CompletedProcess[bytes] | None:
    try:
        return subprocess.run(
            argv,
            input=stdin,
            capture_output=True,
            timeout=_parse_timeout_s(),
            check=False,
            env=_tool_env(),
        )
    except subprocess.TimeoutExpired:
        log.warning("preparse command timed out argv0=%s", argv[0] if argv else "?")
        return None
    except FileNotFoundError:
        log.warning("preparse command missing argv0=%s", argv[0] if argv else "?")
        return None


def _tool_env() -> dict[str, str]:
    """lit and qpdf get no database URLs and no secrets."""
    env = dict(os.environ)
    for key in list(env):
        upper = key.upper()
        if any(word in upper for word in ("PASSWORD", "SECRET", "TOKEN", "API_KEY", "DATABASE", "POSTGRES")):
            env.pop(key, None)
    return env


def _parse_timeout_s() -> float:
    raw = os.environ.get("FT_PARSE_TIMEOUT_S", "180").strip()
    try:
        value = float(raw)
    except ValueError:
        return 180.0
    return value if value > 0 else 180.0


def shutil_which(name: str) -> str | None:
    from shutil import which

    return which(name)
