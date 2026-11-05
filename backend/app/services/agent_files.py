"""Uploaded-file intelligence for the AI Agent.

THE RULE
--------
A 12,000-row CSV must never be pasted into a model's context. It would blow
the window, cost a fortune and still be unreadable. Instead:

  * the file is parsed **once** on upload and stored;
  * the model is given a compact *summary* (columns, row count, five sample
    rows, per-column stats and detected types);
  * the model then asks the file questions through tools —
    ``read_file_rows(file_id, offset, limit)`` and ``query_file(file_id, ...)``
    — and imports through ``import_csv(file_id, mapping, list_id)``.

Parsing lives here so both the upload endpoint and the tools use the exact
same reader, and so a corrupt file produces one clear error rather than a
stack trace in the middle of a chat.

Supported: CSV/TSV, XLSX, TXT and common text formats, PDF, DOCX, PPTX,
ZIP archives and images. Archives are read without extracting paths to disk;
image content is retained for a multimodal provider, while tabular files stay
queryable/importable as rows.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import mimetypes
import re
import zipfile
from pathlib import PurePosixPath
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: What the composer accepts. Extension -> human label.
ACCEPTED_EXTENSIONS = {
    "csv": "CSV",
    "tsv": "TSV",
    "xlsx": "Excel",
    "xlsm": "Macro-enabled Excel",
    "txt": "Text",
    "md": "Markdown",
    "json": "JSON",
    "html": "HTML",
    "htm": "HTML",
    "xml": "XML",
    "yaml": "YAML",
    "yml": "YAML",
    "log": "Log",
    "rtf": "Rich text",
    "pdf": "PDF",
    "docx": "Word",
    "pptx": "PowerPoint",
    "zip": "ZIP archive",
    "png": "Image",
    "jpg": "Image",
    "jpeg": "Image",
    "webp": "Image",
}

MAX_BYTES = 25 * 1024 * 1024  # 25 MB — a contact export, not a database dump
MAX_IMAGE_BYTES = 10 * 1024 * 1024
BINARY_STORAGE_PREFIX = "__ai_agent_binary_v1__:"
MAX_ARCHIVE_ENTRIES = 100
MAX_ARCHIVE_UNPACKED_BYTES = 40 * 1024 * 1024
MAX_ARCHIVE_ENTRY_BYTES = 10 * 1024 * 1024

#: Header names we recognise before asking the model to invent a mapping.
_HEADER_HINTS: dict[str, tuple[str, ...]] = {
    "phone_number": ("phone", "phone_number", "mobile", "telephone", "tel", "number",
                     "whatsapp", "msisdn", "contact", "phone no", "gsm"),
    "email": ("email", "e-mail", "mail", "email address", "email_address"),
    # "name" alone is deliberately absent: a bare "Name" column is a FULL name
    # (handled below), and matching it here is what made "Business Name" land in
    # last_name instead of business_name.
    "first_name": ("first", "first_name", "firstname", "given", "given_name"),
    "last_name": ("last", "last_name", "lastname", "surname", "family", "family_name"),
    "business_name": ("business", "company", "organisation", "organization",
                      "business_name", "brand"),
    "city": ("city", "town", "location"),
    "state": ("state", "region", "province"),
    "country": ("country",),
    "notes": ("notes", "note", "comment", "description", "remark"),
    "tags": ("tags", "tag", "labels", "groups"),
}

#: Header names that are *not* a person's first name even though they contain
#: "name" — a naive matcher maps "business_name" to first_name.
_NOT_A_NAME = re.compile(r"(business|company|brand|org|full)", re.I)

#: Priority when two rules could claim the same column. The *specific* field
#: wins over the generic one: "Business Name" must become ``business_name``,
#: not ``last_name``, because the "name" token also matches last_name.
_FIELD_PRIORITY = (
    "phone_number",
    "email",
    "business_name",
    "first_name",
    "last_name",
    "full_name",
    "city",
    "state",
    "country",
    "tags",
    "notes",
)


def match_header(header: str) -> Optional[str]:
    """Map one CSV header to a contact field, or None.

    Matching is by whole token, so "organised" never matches "organ", and the
    field with the best-matching token wins: a header containing both "name"
    and "business" is a business, not a surname.
    """
    key = re.sub(r"[^a-z0-9]+", "_", (header or "").strip().lower()).strip("_")
    if not key:
        return None
    tokens = {t for t in key.split("_") if t}
    key_flat = key.replace("_", "")

    best_field: Optional[str] = None
    best_score = 0
    for field_name in _FIELD_PRIORITY:
        for hint in _HEADER_HINTS.get(field_name, ()):
            hint_key = re.sub(r"[^a-z0-9]+", "_", hint).strip("_")
            hint_flat = hint_key.replace("_", "")
            hint_tokens = {t for t in hint_key.split("_") if t}
            score = 0
            if key == hint_key or key_flat == hint_flat:
                score = 1000 + len(hint_key)
            elif hint_tokens <= tokens:
                score = sum(len(t) for t in hint_tokens)
            elif len(hint_flat) >= 4 and key_flat.startswith(hint_flat):
                score = len(hint_flat)
            if score > best_score:
                best_score = score
                best_field = field_name
    if best_field == "first_name" and _NOT_A_NAME.search(key):
        return None
    return best_field


class FileParseError(Exception):
    """A file we cannot read, with a message written for the operator."""


@dataclass
class ParsedFile:
    kind: str                       # csv | xlsx | text | pdf | docx | pptx | zip | image
    text: str = ""                  # normalized content or extracted text
    headers: list[str] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    total_rows: int = 0
    summary: str = ""
    preview: dict = field(default_factory=dict)
    archive_entries: list[dict] = field(default_factory=list)

    @property
    def is_tabular(self) -> bool:
        return bool(self.headers)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def extension_of(filename: str) -> str:
    name = (filename or "").lower()
    return name.rsplit(".", 1)[-1] if "." in name else ""


def _clean_header(raw: str, index: int) -> str:
    header = (raw or "").strip().strip("\ufeff")
    return header or f"column_{index + 1}"


def _dedupe(headers: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out: list[str] = []
    for header in headers:
        if header not in seen:
            seen[header] = 1
            out.append(header)
        else:
            seen[header] += 1
            out.append(f"{header}_{seen[header]}")
    return out


def _looks_numeric(values: list[str]) -> bool:
    checked = [v for v in values if str(v).strip()]
    if not checked:
        return False
    for value in checked:
        stripped = str(value).replace(",", "").replace(" ", "").lstrip("+-").replace(".", "", 1)
        if not stripped.isdigit():
            return False
    return True


def _diallable_ratio(values: list[str]) -> float:
    from app.services.suppression_service import is_diallable

    checked = [v for v in values if str(v).strip()]
    if not checked:
        return 0.0
    return sum(1 for v in checked if is_diallable(v)) / len(checked)


def _email_ratio(values: list[str]) -> float:
    pattern = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    checked = [v for v in values if str(v).strip()]
    if not checked:
        return 0.0
    return sum(1 for v in checked if pattern.match(str(v).strip())) / len(checked)


def suggest_mapping(headers: list[str], rows: list[dict]) -> dict[str, str]:
    """Guess ``{csv_header: contact_field}`` from header names **and** content.

    Header names lie often ("Name" holding a business, "Phone" holding an
    email), so each guess is confirmed against the values in the first rows.
    The model can still override the mapping; this just gives it a correct
    starting point so a plain "import this" works.
    """
    sample = rows[:50]

    def values_for(header: str) -> list[str]:
        return [str(row.get(header, "")) for row in sample]

    mapping: dict[str, str] = {}
    used_fields: set[str] = set()
    phone_candidates: list[tuple[float, str]] = []
    email_candidates: list[tuple[float, str]] = []

    for header in headers:
        values = values_for(header)
        phone_score = _diallable_ratio(values)
        email_score = _email_ratio(values)
        # Content beats the header name for the two fields that decide whether
        # a contact can be reached at all.
        if email_score >= 0.6 and "email" not in used_fields:
            email_candidates.append((email_score, header))
            continue
        if phone_score >= 0.6 and "phone_number" not in used_fields:
            phone_candidates.append((phone_score, header))
            continue
        field = match_header(header)
        if field and field not in used_fields:
            mapping[header] = field
            used_fields.add(field)

    if phone_candidates and "phone_number" not in used_fields:
        header = max(phone_candidates)[1]
        mapping[header] = "phone_number"
        used_fields.add("phone_number")
    if email_candidates and "email" not in used_fields:
        header = max(email_candidates)[1]
        mapping[header] = "email"
        used_fields.add("email")

    # "Name" / "Full name" / "Customer name" with no first/last split.
    for header in headers:
        if header in mapping:
            continue
        key = re.sub(r"[^a-z0-9]+", "_", header.strip().lower()).strip("_")
        if key in ("name", "full_name", "fullname", "customer_name", "contact_name", "client_name"):
            if "full_name" not in used_fields and "first_name" not in used_fields:
                mapping[header] = "full_name"
                used_fields.add("full_name")

    # A single "Name" column is a full name: split it at import time.
    for header, target in list(mapping.items()):
        if target == "first_name":
            values = [v for v in values_for(header) if str(v).strip()]
            if values and sum(1 for v in values if len(str(v).split()) > 1) >= len(values) * 0.7:
                mapping[header] = "full_name"
    return mapping


# ---------------------------------------------------------------------------
# parsers
# ---------------------------------------------------------------------------
def _parse_delimited(text: str, delimiter: str | None = None) -> ParsedFile:
    stream = io.StringIO(text)
    sample = text[:8192]
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
    reader = csv.reader(stream, delimiter=delimiter)
    try:
        raw_headers = next(reader)
    except StopIteration:
        raise FileParseError("The file is empty — there is no header row to map.")
    headers = _dedupe([_clean_header(h, i) for i, h in enumerate(raw_headers)])
    rows: list[dict] = []
    for values in reader:
        if not any(str(v).strip() for v in values):
            continue  # blank line
        row = {}
        for index, header in enumerate(headers):
            row[header] = values[index].strip() if index < len(values) else ""
        rows.append(row)
    return ParsedFile(kind="csv", text=text, headers=headers, rows=rows, total_rows=len(rows))


def _parse_xlsx(content: bytes) -> ParsedFile:
    """Read the first worksheet of an XLSX.

    Read with the stdlib zipfile + XML rather than adding pandas/openpyxl to
    every deployment: an XLSX is a zip of XML and a contact export only ever
    needs text out of it.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise FileParseError(
            "That .xlsx file is corrupt or is actually an old .xls. Open it in Excel, "
            "save as CSV, and upload the CSV."
        ) from exc
    try:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            from xml.etree import ElementTree as ET

            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for node in root:
                shared.append("".join(node.itertext()))
        sheets = sorted(
            name for name in archive.namelist()
            if re.match(r"xl/worksheets/sheet\d+\.xml$", name)
        )
        if not sheets:
            raise FileParseError("That workbook has no worksheets.")
        from xml.etree import ElementTree as ET

        root = ET.fromstring(archive.read(sheets[0]))
        ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        grid: list[list[str]] = []
        for row in root.findall(".//m:sheetData/m:row", ns):
            values: list[str] = []
            for cell in row.findall("m:c", ns):
                # XLSX omits empty cells; use the cell reference to preserve columns.
                ref = re.match(r"([A-Z]+)", cell.get("r", ""))
                if ref:
                    column = 0
                    for char in ref.group(1):
                        column = column * 26 + ord(char) - ord("A") + 1
                    while len(values) < column - 1:
                        values.append("")
                cell_type = cell.get("t")
                if cell_type == "inlineStr":
                    values.append("".join(cell.itertext()))
                    continue
                value_node = cell.find("m:v", ns)
                raw = value_node.text if value_node is not None and value_node.text else ""
                if cell_type == "s" and raw.isdigit():
                    index = int(raw)
                    values.append(shared[index] if index < len(shared) else "")
                else:
                    values.append(raw)
            grid.append(values)
    finally:
        archive.close()

    if not grid:
        raise FileParseError("The first worksheet is empty.")
    headers = _dedupe([_clean_header(h, i) for i, h in enumerate(grid[0])])
    rows = []
    for values in grid[1:]:
        if not any(str(v).strip() for v in values):
            continue
        rows.append({h: (values[i].strip() if i < len(values) else "") for i, h in enumerate(headers)})
    # Store a lossless CSV representation for subsequent reads and imports.
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=headers)
    writer.writeheader()
    writer.writerows(rows)
    return ParsedFile(kind="xlsx", text=output.getvalue(), headers=headers, rows=rows, total_rows=len(rows))


def _parse_pdf(content: bytes) -> ParsedFile:
    """Extract text from a PDF, or describe it honestly if it is a scan."""
    buffer = io.BytesIO(content)
    try:
        from pypdf import PdfReader  # optional dependency

        reader = PdfReader(buffer)
        pages = [(page.extract_text() or "") for page in reader.pages[:40]]
        text = "\n".join(p for p in pages if p).strip()
    except ModuleNotFoundError:
        text = ""
    except Exception as exc:
        raise FileParseError(f"Could not read that PDF: {exc}") from exc
    if not text:
        return ParsedFile(
            kind="pdf",
            text="",
            summary="PDF with no extractable text (probably a scan). Ask the operator to paste the key fields.",
        )
    return ParsedFile(kind="pdf", text=text, summary="PDF text extracted.")


def _parse_docx(content: bytes) -> ParsedFile:
    from xml.etree import ElementTree as ET

    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            document = ET.fromstring(archive.read("word/document.xml"))
    except Exception as exc:
        raise FileParseError(f"Could not read that .docx file: {exc}") from exc

    word_ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs = []
    for paragraph in document.iter(f"{word_ns}p"):
        pieces = []
        for node in paragraph.iter():
            if node.tag == f"{word_ns}t" and node.text:
                pieces.append(node.text)
            elif node.tag in (f"{word_ns}tab", f"{word_ns}br"):
                pieces.append("\t" if node.tag.endswith("tab") else "\n")
        line = "".join(pieces).strip()
        if line:
            paragraphs.append(line)
    text = "\n".join(paragraphs).strip()
    return ParsedFile(kind="docx", text=text, summary="Word document text extracted.")


def _parse_pptx(content: bytes) -> ParsedFile:
    """Extract slide text from a PowerPoint package using its XML parts."""
    from xml.etree import ElementTree as ET

    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            slides = sorted(
                name for name in archive.namelist()
                if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
            )
            if not slides:
                raise FileParseError("That PowerPoint file has no readable slides.")
            paragraphs = []
            for index, name in enumerate(slides, start=1):
                root = ET.fromstring(archive.read(name))
                texts = [node.text or "" for node in root.iter() if node.tag.endswith("}t")]
                slide_text = " ".join(value.strip() for value in texts if value.strip())
                if slide_text:
                    paragraphs.append(f"Slide {index}: {slide_text}")
    except FileParseError:
        raise
    except Exception as exc:
        raise FileParseError(f"Could not read that PowerPoint file: {exc}") from exc
    return ParsedFile(kind="pptx", text="\n".join(paragraphs), summary=f"PowerPoint text extracted from {len(slides)} slides.")


def _parse_zip(content: bytes) -> ParsedFile:
    """Read a ZIP's supported files safely without extracting paths to disk."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise FileParseError("That ZIP archive is corrupt or not a ZIP file.") from exc

    entries: list[dict] = []
    total_unpacked = 0
    skipped = 0
    try:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        if len(infos) > MAX_ARCHIVE_ENTRIES:
            raise FileParseError(
                f"That archive contains more than {MAX_ARCHIVE_ENTRIES} files. Split it into smaller archives."
            )
        if sum(info.file_size for info in infos) > MAX_ARCHIVE_UNPACKED_BYTES:
            raise FileParseError("That archive expands beyond the 40 MB safety limit.")

        for info in infos:
            # Never trust archive names. No files are extracted, but rejecting
            # absolute/traversal paths also keeps the preview unambiguous.
            safe_path = PurePosixPath(info.filename.replace("\\", "/"))
            if safe_path.is_absolute() or ".." in safe_path.parts:
                skipped += 1
                continue
            extension = extension_of(info.filename)
            if extension == "zip":
                skipped += 1
                continue
            if info.file_size > MAX_ARCHIVE_ENTRY_BYTES:
                skipped += 1
                continue
            total_unpacked += info.file_size
            if total_unpacked > MAX_ARCHIVE_UNPACKED_BYTES:
                raise FileParseError("That archive expands beyond the 40 MB safety limit.")
            try:
                raw = archive.read(info)
                parsed = parse_file(info.filename, raw)
            except (OSError, RuntimeError, zipfile.BadZipFile, FileParseError):
                skipped += 1
                continue
            entry = {
                "name": info.filename,
                "kind": parsed.kind,
                "content": parsed.text,
                "summary": parsed.summary,
                "preview": parsed.preview,
                "headers": parsed.headers,
                "total_rows": parsed.total_rows,
            }
            entries.append(entry)
    finally:
        archive.close()

    if not entries:
        raise FileParseError("The ZIP contains no safe, readable file members.")

    primary = next((entry for entry in entries if entry.get("headers")), None)
    text_head = "\n\n".join(
        f"{entry['name']}: {(entry.get('content') or '')[:1200]}"
        for entry in entries
        if entry.get("content") and not entry.get("headers")
    )[:6000]
    summary = (
        f"ZIP archive with {len(entries)} readable file(s). "
        + "; ".join(
            f"{entry['name']} ({entry['kind']}, {entry.get('total_rows', 0)} rows)"
            for entry in entries[:12]
        )
        + (f"; {skipped} unsupported, unsafe, oversized or unreadable file(s) skipped." if skipped else "")
    )
    stored = json.dumps({"__ai_archive__": 1, "entries": entries}, ensure_ascii=False)
    if primary:
        table_preview = primary.get("preview") or {}
        preview = {
            **table_preview,
            "kind": "zip",
            "primary_entry": primary["name"],
            "entries": [
                {
                    "name": entry["name"],
                    "kind": entry["kind"],
                    "summary": entry.get("summary"),
                    "total_rows": entry.get("total_rows", 0),
                    "headers": entry.get("headers") or [],
                }
                for entry in entries
            ],
            "text_head": text_head,
        }
        return ParsedFile(
            kind="zip",
            text=stored,
            headers=list(primary.get("headers") or []),
            total_rows=int(primary.get("total_rows") or 0),
            preview=preview,
            summary=summary,
            archive_entries=entries,
        )

    preview = {
        "kind": "zip",
        "headers": [],
        "columns": [],
        "sample_rows": [],
        "total_rows_estimate": 0,
        "suggested_mapping": {},
        "entries": [
            {
                "name": entry["name"],
                "kind": entry["kind"],
                "summary": entry.get("summary"),
                "total_rows": entry.get("total_rows", 0),
                "headers": entry.get("headers") or [],
            }
            for entry in entries
        ],
        "text_head": text_head,
    }
    return ParsedFile(kind="zip", text=stored, summary=summary, preview=preview, archive_entries=entries)


def _parse_rtf(text: str) -> str:
    """Remove common RTF control words while retaining visible text."""
    text = re.sub(r"\\'[0-9a-fA-F]{2}", " ", text)
    text = re.sub(r"\\par[d]?\b", "\n", text)
    text = re.sub(r"\\tab\b", "\t", text)
    text = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", text)
    return re.sub(r"[{}]", "", text).strip()


def _decode_text(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Nigerian exports are often saved by Excel as Windows-1252.
        return content.decode("cp1252", errors="replace")


def _looks_like_text(content: bytes) -> bool:
    sample = content[:4096]
    if not sample or b"\x00" in sample:
        return False
    decoded = _decode_text(sample)
    visible = sum(char.isprintable() or char in "\r\n\t" for char in decoded)
    return visible / max(1, len(decoded)) >= 0.85


def parse_file(filename: str, content: bytes, *, content_type: str | None = None) -> ParsedFile:
    """Parse common documents and safely accept any other uploaded file type.

    Text-like unknown formats are still searchable. Opaque binary formats are
    retained as file records with their type/size and an honest note when this
    agent cannot extract their contents.
    """
    if len(content) > MAX_BYTES:
        raise FileParseError(
            f"That file is larger than {MAX_BYTES // (1024 * 1024)} MB. Split it into smaller files."
        )
    ext = extension_of(filename)
    if ext == "zip":
        return _parse_zip(content)

    if ext in {"xlsx", "xlsm"}:
        parsed = _parse_xlsx(content)
    elif ext == "pptx":
        parsed = _parse_pptx(content)
    elif ext in ("png", "jpg", "jpeg", "webp"):
        if len(content) > MAX_IMAGE_BYTES:
            raise FileParseError("Images must be 10 MB or smaller so they can be sent to the vision model.")
        mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
        preview = {
            "kind": "image",
            "mime_type": mime,
            "headers": [],
            "sample_rows": [],
            "total_rows_estimate": 0,
            "text_head": "",
        }
        return ParsedFile(
            kind="image",
            text="",
            summary=f"Image ({len(content) // 1024} KB). Attach it to a message to ask about its contents.",
            preview=preview,
        )
    elif ext == "pdf":
        parsed = _parse_pdf(content)
    elif ext == "docx":
        parsed = _parse_docx(content)
    elif ext in {"csv", "tsv"}:
        parsed = _parse_delimited(_decode_text(content), delimiter="\t" if ext == "tsv" else None)
    elif ext == "rtf":
        parsed = ParsedFile(kind="rtf", text=_parse_rtf(_decode_text(content)), summary="Rich-text document extracted.")
    elif ext in {"txt", "md", "json", "html", "htm", "xml", "yaml", "yml", "log"}:
        text = _decode_text(content)
        parsed = ParsedFile(kind=ext, text=text, summary=f"{ext.upper()} text extracted.")
    else:
        mime = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        office_binary = ext in {"doc", "xls", "ppt", "odt", "ods", "odp"}
        if not office_binary and (mime.startswith("text/") or _looks_like_text(content)):
            text = _decode_text(content)
            kind = "text" if not ext else ext
            parsed = ParsedFile(kind=kind, text=text, summary=f"Text extracted from {filename}.")
        else:
            label = f".{ext}" if ext else "unknown file type"
            parsed = ParsedFile(
                kind="binary",
                text="",
                summary=f"{label} file ({len(content) // 1024} KB). The file is attached, but its contents could not be extracted in this chat.",
                preview={"kind": "binary", "mime_type": mime, "size_bytes": len(content)},
            )

    if parsed.is_tabular:
        parsed.preview = build_preview(parsed)
        parsed.summary = summarise(parsed)
    else:
        parsed.summary = parsed.summary or f"{ext.upper() or 'File'} document, {len(parsed.text)} characters."
        parsed.preview = {
            "headers": [],
            "sample_rows": [],
            "total_rows_estimate": 0,
            "text_head": parsed.text[:1500],
            "kind": parsed.kind,
            **(parsed.preview or {}),
        }
    return parsed


def build_preview(parsed: ParsedFile) -> dict:
    columns = []
    sample = parsed.rows[:50]
    for header in parsed.headers:
        values = [str(row.get(header, "")) for row in sample]
        non_empty = [v for v in values if v.strip()]
        column = {
            "name": header,
            "filled": len(non_empty),
            "type": "number" if _looks_numeric(non_empty) else "text",
            "sample": non_empty[:3],
        }
        if _diallable_ratio(non_empty) >= 0.6:
            column["type"] = "phone"
        elif _email_ratio(non_empty) >= 0.6:
            column["type"] = "email"
        columns.append(column)
    return {
        "headers": parsed.headers,
        "sample_rows": [{h: parsed.rows[i].get(h, "") for h in parsed.headers} for i in range(min(5, len(parsed.rows)))],
        "total_rows_estimate": parsed.total_rows,
        "columns": columns,
        "suggested_mapping": suggest_mapping(parsed.headers, parsed.rows),
        "kind": parsed.kind,
    }


def summarise(parsed: ParsedFile) -> str:
    """The compact description handed to the model — deliberately small."""
    preview = parsed.preview or build_preview(parsed)
    columns = ", ".join(
        f"{c['name']} ({c['type']})" for c in preview.get("columns", [])
    )
    return (
        f"{parsed.kind.upper()} with {parsed.total_rows} data rows and "
        f"{len(parsed.headers)} columns: {columns}. "
        f"Suggested mapping: {json.dumps(preview.get('suggested_mapping', {}))}."
    )


# ---------------------------------------------------------------------------
# row-level access used by the agent tools
# ---------------------------------------------------------------------------
def slice_rows(rows: list[dict], offset: int = 0, limit: int = 20) -> list[dict]:
    safe_limit = max(1, min(int(limit or 20), 200))
    safe_offset = max(0, int(offset or 0))
    return rows[safe_offset: safe_offset + safe_limit]


def query_rows(rows: list[dict], query: str = "", limit: int = 20) -> list[dict]:
    """Case-insensitive substring match across every column."""
    needle = (query or "").strip().lower()
    if not needle:
        return slice_rows(rows, 0, limit)
    matches = [
        row for row in rows
        if any(needle in str(value).lower() for value in row.values())
    ]
    return slice_rows(matches, 0, limit)


def columns_of(rows: list[dict]) -> list[str]:
    seen: list[str] = []
    for row in rows[:1]:
        seen.extend(row.keys())
    return seen


def _parse_stored_entry(entry: dict) -> ParsedFile:
    name = str(entry.get("name") or "file.txt")
    ext = extension_of(name)
    stored_content = str(entry.get("content") or "")
    if entry.get("kind") == "binary":
        return ParsedFile(
            kind="binary",
            text="",
            summary=str(entry.get("summary") or "Binary file; contents were not extracted."),
            preview=entry.get("preview") or {"kind": "binary"},
        )
    if ext in {"xlsx", "xlsm"}:
        return parse_file("stored.csv", stored_content.encode("utf-8"))
    if ext in {"pdf", "docx", "pptx", "txt", "md", "json", "html", "htm", "xml", "yaml", "yml", "log", "rtf"}:
        return ParsedFile(
            kind=ext,
            text=stored_content,
            summary=str(entry.get("summary") or "Extracted document text."),
            preview=entry.get("preview") or {},
            headers=list(entry.get("headers") or []),
            total_rows=int(entry.get("total_rows") or 0),
        )
    if ext in {"png", "jpg", "jpeg", "webp"}:
        return ParsedFile(
            kind="image",
            text="",
            summary=str(entry.get("summary") or "Image file."),
            preview=entry.get("preview") or {},
        )
    return parse_file(name, stored_content.encode("utf-8"))


def parse_stored(filename: str, content: str, *, entry: Optional[str] = None) -> ParsedFile:
    """Read the persisted, normalized representation of an uploaded file.

    For ZIP uploads, ``entry`` selects a member by its archive path. Omitting it
    chooses the first tabular member (ideal for the upload preview/import flow).
    """
    ext = extension_of(filename)
    if ext in {"png", "jpg", "jpeg", "webp"}:
        mime_type = "image/jpeg" if ext in {"jpg", "jpeg"} else f"image/{ext}"
        return ParsedFile(
            kind="image",
            text="",
            summary="Uploaded image. Attach it to a message to ask about its contents.",
            preview={
                "kind": "image",
                "mime_type": mime_type,
                "headers": [],
                "sample_rows": [],
                "total_rows_estimate": 0,
                "text_head": "",
            },
        )
    if content.startswith(BINARY_STORAGE_PREFIX):
        try:
            binary = json.loads(content[len(BINARY_STORAGE_PREFIX):])
        except (TypeError, json.JSONDecodeError):
            raise FileParseError("The saved binary upload could not be read.")
        return ParsedFile(
            kind="binary",
            text="",
            summary=str(binary.get("summary") or "Binary file; contents were not extracted."),
            preview=binary.get("preview") or {"kind": "binary"},
        )
    if ext == "zip":
        try:
            envelope = json.loads(content)
        except (TypeError, json.JSONDecodeError):
            raise FileParseError("The saved ZIP archive could not be read.")
        if not isinstance(envelope, dict) or envelope.get("__ai_archive__") != 1:
            raise FileParseError("The saved ZIP archive has an unsupported format.")
        entries = envelope.get("entries") or []
        selected = None
        if entry:
            selected = next((item for item in entries if item.get("name") == entry), None)
            if selected is None:
                raise FileParseError(f"No readable file named “{entry}” is in this archive.")
        else:
            selected = next((item for item in entries if item.get("headers")), None)
            selected = selected or (entries[0] if entries else None)
        if selected is None:
            return ParsedFile(kind="zip", text="", summary="Empty archive.", archive_entries=entries)
        parsed = _parse_stored_entry(selected)
        parsed.archive_entries = entries
        return parsed

    if ext in {"xlsx", "xlsm"}:
        return parse_file("stored.csv", content.encode("utf-8"))
    if ext in {"pdf", "docx", "pptx", "txt", "md", "json", "html", "htm", "xml", "yaml", "yml", "log", "rtf"}:
        return ParsedFile(kind=ext, text=content, summary="Extracted document text.")
    return parse_file(filename, content.encode("utf-8"))
