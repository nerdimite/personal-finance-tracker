"""Synthetic fixtures only. Real statements in zzz/ are not used here."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pandas as pd
import pytest

from ft_bot.preparse import (
    _ENCRYPTED_NOTE,
    load_statement_passwords,
    parse_statement_passwords,
    preparse_message_folder,
    wants_layout_json,
)
from ft_bot.runner import child_env

_SECRET = "synthetic-not-a-bank"
_MARKER = "SYNTHETIC-LEDGER-ROW"


def test_password_list_parses_json_and_pairs() -> None:
    assert parse_statement_passwords("") == []
    assert parse_statement_passwords('{"hdfc":"one","axis":"two"}') == [("hdfc", "one"), ("axis", "two")]
    assert parse_statement_passwords("hdfc=one, axis=two") == [("hdfc", "one"), ("axis", "two")]
    assert parse_statement_passwords('{"hdfc":""}') == []


def test_invalid_password_json_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STATEMENT_PASSWORDS", "{")
    assert load_statement_passwords() == []


def test_child_env_drops_statement_passwords(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STATEMENT_PASSWORDS", _SECRET)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "telegram-token-value")
    monkeypatch.setenv("OWNER_DATABASE_URL", "postgresql://owner:secret@127.0.0.1/finance")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    monkeypatch.setenv("DASHBOARD_DATABASE_URL", "postgresql://dashboard:secret@127.0.0.1/finance")
    monkeypatch.setenv("AGENT_DATABASE_URL", "postgresql://agent:secret@127.0.0.1/finance")
    monkeypatch.setenv("MIGRATOR_DATABASE_URL", "postgresql://migrator:secret@127.0.0.1/finance")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-value")
    monkeypatch.setenv("UNRELATED_BOT_VAR", "nope")
    env = child_env(4)
    assert "STATEMENT_PASSWORDS" not in env
    assert "TELEGRAM_BOT_TOKEN" not in env
    assert "OWNER_DATABASE_URL" not in env
    assert "POSTGRES_PASSWORD" not in env
    assert "DASHBOARD_DATABASE_URL" not in env
    assert "UNRELATED_BOT_VAR" not in env
    assert "AGENT_DATABASE_URL" in env
    assert "MIGRATOR_DATABASE_URL" in env
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth-value"
    assert env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"
    assert env["APP_RUN_ID"] == "4"
    from ft_bot.preparse import _tool_env

    tool_env = _tool_env()
    assert "AGENT_DATABASE_URL" not in tool_env
    assert "OWNER_DATABASE_URL" not in tool_env
    assert "POSTGRES_PASSWORD" not in tool_env


def test_user_text_that_says_parsed_is_kept(tmp_path: Path) -> None:
    folder = _folder(tmp_path, "01_note.txt", b"hello")
    (folder / "message.md").write_text("# Message\n\n## Parsed\n\nthis is the user\n")
    preparse_message_folder(folder, passwords=[])
    body = (folder / "message.md").read_text()
    assert "## Parsed" in body
    assert "this is the user" in body
    assert "<!-- ft-parsed -->" in body


def test_qpdf_password_is_not_on_argv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from ft_bot.preparse import _qpdf_decrypt

    seen: dict = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = list(argv)
        seen["input"] = kwargs.get("input")
        Path(argv[-1]).write_bytes(b"%PDF")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr("ft_bot.preparse.subprocess.run", fake_run)
    assert _qpdf_decrypt(tmp_path / "in.pdf", tmp_path / "out.pdf", "s3cret-value")
    assert "s3cret-value" not in " ".join(seen["argv"])
    assert b"s3cret-value" in seen["input"]
    assert "--password-file=-" in seen["argv"]


def test_layout_json_for_sparse_or_aligned_text() -> None:
    assert wants_layout_json("short")
    gutter = "\n".join(f"alpha  beta {i} gamma words continue here." for i in range(12))
    assert wants_layout_json(gutter)
    dense = "\n".join(f"sentence number {i} has several ordinary words in it." for i in range(20))
    assert not wants_layout_json(dense)


def test_csv_header_block_is_kept(tmp_path: Path) -> None:
    raw = "\n".join(
        [
            "Account Name Example",
            "City Testville",
            "",
            "Date,Narration,Amount",
            "2024-01-02,Tea,12",
            "",
            "Closing note",
            "",
        ]
    )
    folder = _folder(tmp_path, "01_acct.csv", raw.encode())
    notes = preparse_message_folder(folder, passwords=[])
    text = (folder / "01_acct.normalized.csv").read_text()
    assert text.startswith("Account Name Example\nCity Testville\n\nDate,Narration,Amount\n")
    assert "2024-01-02,Tea,12" in text
    assert "Closing note" in text
    assert "header block kept" in notes[0].detail
    assert "header block kept" in (folder / "message.md").read_text()


def test_xlsx_writes_one_csv_per_sheet(tmp_path: Path) -> None:
    path = tmp_path / "book.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame([["Item", "Amount"], ["Tea", "12"]]).to_excel(
            writer, sheet_name="Camp", index=False, header=False
        )
        pd.DataFrame([["Note"], ["torch"]]).to_excel(
            writer, sheet_name="Notes", index=False, header=False
        )
    folder = _folder(tmp_path, "01_trip.xlsx", path.read_bytes())
    notes = preparse_message_folder(folder, passwords=[])
    camp = (folder / "01_trip__Camp.csv").read_text()
    notes_csv = (folder / "01_trip__Notes.csv").read_text()
    assert camp.splitlines()[0] == "Item,Amount"
    assert "Tea,12" in camp
    assert notes_csv.splitlines()[0] == "Note"
    assert any("sheet: Camp" in note.detail for note in notes)


def test_numbers_writes_one_csv_per_table(tmp_path: Path) -> None:
    from numbers_parser import Document

    source = tmp_path / "spend.numbers"
    document = Document(num_rows=2, num_cols=2, num_header_rows=0, num_header_cols=0)
    table = document.sheets[0].tables[0]
    table.write(0, 0, "Item")
    table.write(0, 1, "Amount")
    table.write(1, 0, "Tea")
    table.write(1, 1, 12)
    document.save(source)
    folder = _folder(tmp_path, "01_spend.numbers", source.read_bytes())
    notes = preparse_message_folder(folder, passwords=[])
    csvs = list(folder.glob("01_spend__*.csv"))
    assert len(csvs) == 1
    text = csvs[0].read_text()
    assert "Item,Amount" in text.replace(" ", "")
    assert "Tea" in text
    assert notes[0].ok


def test_image_is_not_parsed(tmp_path: Path) -> None:
    folder = _folder(tmp_path, "01_shot.png", b"\x89PNG\r\n\x1a\n", kind="image")
    notes = preparse_message_folder(folder, passwords=[])
    assert notes[0].detail == "image: 01_shot.png (not parsed)"
    names = {path.name for path in folder.iterdir()}
    assert names == {"01_shot.png", "meta.json", "message.md"}


def test_encrypted_pdf_uses_qpdf_and_hides_the_password(tmp_path: Path) -> None:
    if not _have("qpdf") or not _have("lit"):
        pytest.skip("qpdf and lit are required")
    plain = tmp_path / "plain.pdf"
    _write_pdf(plain, _MARKER)
    encrypted = tmp_path / "02_statement.pdf"
    _encrypt(plain, encrypted, _SECRET)
    folder = _folder(tmp_path, "02_statement.pdf", encrypted.read_bytes())
    wrong = _folder(tmp_path / "wrong", "02_statement.pdf", encrypted.read_bytes())

    missed = preparse_message_folder(wrong, passwords=[("demo", "not-the-password")])
    assert any(_ENCRYPTED_NOTE in note.detail for note in missed)
    assert not (wrong / "02_statement.pdf.lit.txt").exists()
    _assert_secret_absent(wrong, "not-the-password")

    notes = preparse_message_folder(folder, passwords=[("other", "nope"), ("demo", _SECRET)])
    text = (folder / "02_statement.pdf.lit.txt").read_text()
    assert _MARKER in text
    assert (folder / "02_statement.decrypted.pdf").is_file()
    assert (folder / "02_statement.pdf.lit.json").is_file()
    message = (folder / "message.md").read_text()
    assert "parsed text: 02_statement.pdf.lit.txt, decrypted with: demo" in message
    assert "decrypted copy: 02_statement.decrypted.pdf" in message
    _assert_secret_absent(folder, _SECRET)
    assert all(_SECRET not in note.detail for note in notes)


def test_docx_parses_with_lit(tmp_path: Path) -> None:
    if not _have("lit"):
        pytest.skip("lit is required")
    payload = tmp_path / "note.docx"
    _write_docx(payload, _MARKER)
    folder = _folder(tmp_path, "01_note.docx", payload.read_bytes())
    notes = preparse_message_folder(folder, passwords=[])
    text = (folder / "01_note.docx.lit.txt").read_text()
    assert _MARKER in text
    assert any(note.detail.startswith("parsed text:") for note in notes)


def _folder(root: Path, name: str, data: bytes, kind: str = "file") -> Path:
    folder = root / "msg"
    folder.mkdir(parents=True)
    (folder / name).write_bytes(data)
    meta = {"attachments": [{"file": name, "kind": kind}]}
    (folder / "meta.json").write_text(json.dumps(meta))
    (folder / "message.md").write_text(f"# Message\n\n## Attachments\n\n- {name} ({kind})\n")
    return folder


def _have(binary: str) -> bool:
    from shutil import which

    return which(binary) is not None


def _assert_secret_absent(folder: Path, secret: str) -> None:
    encoded = secret.encode()
    for path in folder.rglob("*"):
        if path.is_file() and encoded in path.read_bytes():
            raise AssertionError(f"secret appeared in {path.name}")


def _write_pdf(path: Path, text: str) -> None:
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({safe}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    path.write_bytes(bytes(out))


def _encrypt(source: Path, dest: Path, secret: str) -> None:
    result = subprocess.run(
        ["qpdf", "--encrypt", secret, secret, "256", "--", str(source), str(dest)],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError("qpdf could not encrypt the synthetic pdf")


def _write_docx(path: Path, text: str) -> None:
    import zipfile

    document = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body>
</w:document>
"""
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document)
