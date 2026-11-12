"""
Defensive PDF text extraction for ProSeries Client Checklist PDFs.

Strategy:
  1. Extract raw text with pdfplumber; fall back to PyMuPDF (fitz).
  2. Always return the full raw text so staff can re-parse or hand-edit later.
  3. Heuristically pull a client name from the top of the document.
  4. Heuristically pull checklist items from bullets, numbers, and common
     tax-document phrases (W-2, 1099, etc.).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Optional, Union

from taxdash.logging_config import setup_logging

logger = setup_logging()

PathOrBuffer = Union[str, Path, BinaryIO]

# Common ProSeries / organizer cues
CLIENT_NAME_PATTERNS = [
    re.compile(r"(?i)^\s*client(?:\s+name)?\s*[:\-]\s*(.+)$"),
    re.compile(r"(?i)^\s*taxpayer(?:\s+name)?\s*[:\-]\s*(.+)$"),
    re.compile(r"(?i)^\s*name\s*[:\-]\s*(.+)$"),
    re.compile(r"(?i)^\s*prepared\s+for\s*[:\-]?\s*(.+)$"),
    re.compile(r"(?i)^\s*for\s*[:\-]\s*([A-Z][A-Za-z'’\-]+(?:\s+[A-Z][A-Za-z'’\-]+)+)\s*$"),
]

SECTION_HEADERS = re.compile(
    r"(?i)^\s*(?:documents?\s+needed|please\s+provide|items?\s+(?:needed|requested)|"
    r"checklist|requested\s+documents?|information\s+needed|"
    r"what\s+we\s+need|missing\s+documents?)\s*[:\-]?\s*$"
)

BULLET_OR_NUMBER = re.compile(
    r"^\s*(?:[\-\*\u2022\u2023\u25E6\u2043\u2219\u00B7\u25AA\u25CF\u25CB]|"
    r"[0-9]{1,3}[.)]|[A-Za-z][.)])\s+(.+)$"
)

LEADING_BULLET = re.compile(
    r"^[\-\*\u2022\u2023\u25E6\u2043\u2219\u00B7\u25AA\u25CF\u25CB]+\s*"
)

INSTRUCTIONAL_LINE = re.compile(
    r"(?i)^\s*(?:please\s+provide|please\s+return|thank\s+you|sincerely|"
    r"if\s+you\s+have\s+questions|call\s+our\s+office).*"
)

# Tax document keywords used to salvage lines that aren't clearly bulleted
DOC_KEYWORDS = re.compile(
    r"(?i)\b("
    r"w-?2|w-?2[cg]|1099(?:-[A-Z]{1,4})?|1098(?:-[A-Z]{1,3})?|1095-?[ABC]?|"
    r"k-?1|schedule\s+[A-F]|1040|941|940|"
    r"brokerage\s+statement|closing\s+statement|settlement\s+statement|"
    r"property\s+tax|mortgage\s+interest|charitable|donation|"
    r"medical\s+expense|health\s+insurance|hsa|fsa|"
    r"dependent|ssn|itin|driver.?s?\s+license|id\s+card|"
    r"bank\s+statement|voided\s+check|direct\s+deposit|"
    r"prior[\-\s]?year\s+return|organizer|questionnaire|"
    r"crypto|cryptocurrency|stock\s+sale|1099-B|cost\s+basis|"
    r"rental\s+income|business\s+expense|mileage|home\s+office|"
    r"estimated\s+tax|extension|irs\s+notice|state\s+return"
    r")\b"
)

SKIP_LINE = re.compile(
    r"(?i)^\s*(?:page\s+\d+|confidential|proseries|intuit|copyright|"
    r"www\.|http|phone|fax|email\s*:|date\s*:|tax\s+year|"
    r"please\s+return|thank\s+you|sincerely).*$"
)

NAME_LIKE = re.compile(
    r"^[A-Z][A-Za-z'’\-]+(?:\s+[A-Z][A-Za-z'’\-\.]+)+$"
)


@dataclass
class ParseResult:
    client_name: str = ""
    items: list[str] = field(default_factory=list)
    raw_text: str = ""
    engine: str = ""
    warnings: list[str] = field(default_factory=list)


def extract_text(source: PathOrBuffer) -> tuple[str, str]:
    """Return (text, engine_name). Tries pdfplumber then PyMuPDF."""
    text, engine = _extract_pdfplumber(source)
    if text and text.strip():
        return text, engine

    text2, engine2 = _extract_pymupdf(source)
    if text2 and text2.strip():
        return text2, engine2

    combined = (text or "") + ("\n" + text2 if text2 else "")
    return combined.strip(), engine or engine2 or "none"


def _as_bytes(source: PathOrBuffer) -> bytes:
    if isinstance(source, (str, Path)):
        return Path(source).read_bytes()
    pos = source.tell() if hasattr(source, "tell") else None
    data = source.read()
    if hasattr(source, "seek") and pos is not None:
        try:
            source.seek(pos)
        except Exception:
            pass
    if isinstance(data, bytes):
        return data
    return data.encode("utf-8", errors="ignore")


def _extract_pdfplumber(source: PathOrBuffer) -> tuple[str, str]:
    try:
        import pdfplumber
    except ImportError:
        logger.warning("pdfplumber not installed")
        return "", ""

    try:
        data = _as_bytes(source)
        import io

        pages: list[str] = []
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages:
                pages.append(page.extract_text() or "")
        return "\n".join(pages).strip(), "pdfplumber"
    except Exception as exc:
        logger.exception("pdfplumber extraction failed: %s", exc)
        return "", ""


def _extract_pymupdf(source: PathOrBuffer) -> tuple[str, str]:
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.warning("pymupdf not installed")
        return "", ""

    try:
        data = _as_bytes(source)
        doc = fitz.open(stream=data, filetype="pdf")
        try:
            parts = [page.get_text("text") or "" for page in doc]
        finally:
            doc.close()
        return "\n".join(parts).strip(), "pymupdf"
    except Exception as exc:
        logger.exception("pymupdf extraction failed: %s", exc)
        return "", ""


def parse_checklist_text(raw_text: str) -> ParseResult:
    """Parse already-extracted text into client name + checklist items."""
    result = ParseResult(raw_text=raw_text or "")
    if not raw_text or not raw_text.strip():
        result.warnings.append("No text extracted from PDF.")
        return result

    lines = [ln.rstrip() for ln in raw_text.splitlines()]
    non_empty = [ln.strip() for ln in lines if ln.strip()]

    result.client_name = _guess_client_name(non_empty)
    if not result.client_name:
        result.warnings.append(
            "Could not confidently detect client name — please set it manually."
        )

    result.items = _guess_items(non_empty)
    if not result.items:
        result.warnings.append(
            "No checklist items detected — review raw text and add items manually."
        )

    return result


def parse_pdf(source: PathOrBuffer, *, filename: str = "") -> ParseResult:
    """Extract + parse a ProSeries Client Checklist PDF."""
    raw, engine = extract_text(source)
    result = parse_checklist_text(raw)
    result.engine = engine
    if not raw:
        result.warnings.append(
            f"Failed to extract text from {filename or 'PDF'} with available engines."
        )
    logger.info(
        "Parsed PDF %s via %s: name=%r items=%d warnings=%d",
        filename or "(buffer)",
        engine,
        result.client_name,
        len(result.items),
        len(result.warnings),
    )
    return result


def _guess_client_name(lines: list[str]) -> str:
    # Prefer explicit labeled lines near the top
    head = lines[:25]
    for line in head:
        for pattern in CLIENT_NAME_PATTERNS:
            m = pattern.match(line)
            if m:
                name = _clean_name(m.group(1))
                if name:
                    return name

    # Fall back: first name-like line that isn't a header / skip line
    for line in head:
        if SKIP_LINE.match(line) or SECTION_HEADERS.match(line):
            continue
        if DOC_KEYWORDS.search(line):
            continue
        if len(line) > 60:
            continue
        # Strip common prefixes like "Client Checklist for Jane Doe"
        for prefix in (
            r"(?i)^client\s+checklist\s+for\s+",
            r"(?i)^checklist\s+for\s+",
            r"(?i)^organizer\s+for\s+",
        ):
            stripped = re.sub(prefix, "", line).strip()
            if stripped != line:
                line = stripped
                break
        if NAME_LIKE.match(line) and not line.lower().startswith(("please", "documents")):
            return _clean_name(line)

    return ""


def _clean_name(value: str) -> str:
    value = value.strip(" \t-:;,.|")
    value = re.sub(r"\s{2,}", " ", value)
    # Drop trailing tax-year noise
    value = re.sub(r"\s+\d{4}\s*(?:tax\s*)?year.*$", "", value, flags=re.I)
    if len(value) < 3 or len(value) > 80:
        return ""
    return value


def _guess_items(lines: list[str]) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    in_section = False

    for line in lines:
        if SECTION_HEADERS.match(line):
            in_section = True
            continue

        # Stop gathering when we hit a clear footer / unrelated block
        if in_section and re.match(r"(?i)^\s*(notes?|comments?|signature|office\s+use)\b", line):
            in_section = False

        candidate: Optional[str] = None
        m = BULLET_OR_NUMBER.match(line)
        if m:
            candidate = m.group(1).strip()
        elif in_section and len(line) > 3 and not SKIP_LINE.match(line):
            candidate = line.strip()
        elif DOC_KEYWORDS.search(line) and not SKIP_LINE.match(line):
            # Salvage keyword-bearing lines even outside a section
            candidate = BULLET_OR_NUMBER.sub(r"\1", line).strip()
            candidate = candidate.lstrip("-•* ").strip()

        if not candidate:
            continue
        candidate = _clean_item(candidate)
        if not candidate:
            continue
        if SKIP_LINE.match(candidate) or INSTRUCTIONAL_LINE.match(candidate):
            continue
        if SECTION_HEADERS.match(candidate):
            continue
        if len(candidate) < 3 or len(candidate) > 200:
            continue

        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(candidate)

    return items


def _clean_item(value: str) -> str:
    value = value.strip()
    m = BULLET_OR_NUMBER.match(value)
    if m:
        value = m.group(1).strip()
    value = LEADING_BULLET.sub("", value).strip()
    value = value.strip(" \t-:;|")
    return value


def items_from_multiline(text: str) -> list[str]:
    """Split a staff-edited textarea into checklist item strings."""
    items: list[str] = []
    seen: set[str] = set()
    for raw in (text or "").splitlines():
        line = _clean_item(raw)
        if not line or INSTRUCTIONAL_LINE.match(line):
            continue
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(line)
    return items
