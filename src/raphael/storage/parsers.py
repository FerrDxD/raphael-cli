"""Local document extraction: PDF, DOCX, XLSX, and plain text formats.

Everything happens on the machine — no upload, no network (PRD §4).
Optional libraries degrade gracefully: a missing extractor yields a clear
error instead of a crash.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from raphael.utils import normalize_whitespace

TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".rst", ".log", ".csv", ".json", ".yaml", ".yml"}
SUPPORTED_SUFFIXES = TEXT_SUFFIXES | {".pdf", ".docx", ".xlsx", ".xlsm"}


class ParserError(RuntimeError):
    """Raised when a document cannot be extracted."""


def extract(path: Path) -> str:
    """Extract plain text from a single local file."""

    path = Path(path)
    if not path.exists():
        raise ParserError(f"file not found: {path}")
    if path.is_dir():
        raise ParserError(f"expected a file, got a directory: {path}")

    suffix = path.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        return _read_text(path)
    if suffix == ".pdf":
        return _read_pdf(path)
    if suffix == ".docx":
        return _read_docx(path)
    if suffix in {".xlsx", ".xlsm"}:
        return _read_xlsx(path)
    raise ParserError(
        f"unsupported file type '{suffix or path.name}'. "
        f"Supported: {', '.join(sorted(SUPPORTED_SUFFIXES))}"
    )


def _read_text(path: Path) -> str:
    # utf-8-sig: Windows editors (Notepad, PowerShell Set-Content) often prepend
    # a BOM, which would otherwise leak into the title as a stray glyph.
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    if path.suffix.lower() == ".json":
        try:
            return json.dumps(json.loads(raw), indent=2, ensure_ascii=False)
        except json.JSONDecodeError:
            return raw
    if path.suffix.lower() == ".csv":
        reader = csv.reader(io.StringIO(raw))
        return "\n".join(" | ".join(cell.strip() for cell in row) for row in reader)
    return raw


def _read_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise ParserError("PDF support requires 'pypdf' (uv add pypdf)") from exc

    try:
        reader = PdfReader(str(path))
    except Exception as exc:  # noqa: BLE001 - surface any pypdf failure
        raise ParserError(f"cannot open PDF: {exc}") from exc

    pages: list[str] = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001 - a single broken page must not abort
            text = ""
        text = normalize_whitespace(text)
        if text:
            pages.append(f"[page {number}]\n{text}")
    if not pages:
        raise ParserError("no extractable text in PDF (is it a scanned image?)")
    return "\n\n".join(pages)


def _read_docx(path: Path) -> str:
    try:
        import docx  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise ParserError("DOCX support requires 'python-docx'") from exc

    try:
        document = docx.Document(str(path))
    except Exception as exc:  # noqa: BLE001
        raise ParserError(f"cannot open DOCX: {exc}") from exc

    blocks = [paragraph.text.strip() for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                blocks.append(" | ".join(cells))
    text = normalize_whitespace("\n\n".join(block for block in blocks if block))
    if not text:
        raise ParserError("DOCX contains no readable text")
    return text


def _read_xlsx(path: Path) -> str:
    try:
        from openpyxl import load_workbook  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise ParserError("XLSX support requires 'openpyxl'") from exc

    try:
        workbook = load_workbook(str(path), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise ParserError(f"cannot open spreadsheet: {exc}") from exc

    sections: list[str] = []
    for sheet in workbook.worksheets:
        lines = [f"## Sheet: {sheet.title}"]
        for row in sheet.iter_rows(values_only=True):
            cells = ["" if cell is None else str(cell).strip() for cell in row]
            if any(cells):
                lines.append(" | ".join(cells).rstrip(" |"))
        if len(lines) > 1:
            sections.append("\n".join(lines))
    workbook.close()

    text = normalize_whitespace("\n\n".join(sections))
    if not text:
        raise ParserError("spreadsheet contains no readable data")
    return text


def extract_many(paths: list[Path]) -> list[tuple[Path, str]]:
    """Extract several files, returning ``(path, text)``; raises on first failure."""

    results: list[tuple[Path, str]] = []
    for path in paths:
        results.append((Path(path), extract(Path(path))))
    return results
