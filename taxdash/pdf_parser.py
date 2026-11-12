"""
Defensive PDF text extraction for ProSeries Client Checklist PDFs.

Extraction order:
  1. pdfplumber (digital text)
  2. PyMuPDF / pymupdf (digital text, multiple modes)
  3. pypdf (digital text)
  4. OCR fallback:
       a. PyMuPDF page render + pytesseract
       b. pdf2image + pytesseract
       c. rapidocr-onnxruntime (pure-Python / ONNX, no system Tesseract)

Always returns diagnostics + whatever text was recovered (even if empty)
so staff can see what each engine produced and hand-edit afterward.
"""

from __future__ import annotations

import io
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Optional, Union

from taxdash.logging_config import setup_logging

logger = setup_logging()

PathOrBuffer = Union[str, Path, BinaryIO]

# Treat very short digital extracts as failures so OCR still runs
# (ProSeries sometimes yields a few junk glyphs from broken encodings).
MIN_USEFUL_CHARS = 40

# ---------------------------------------------------------------------------
# Client / checklist heuristics
# ---------------------------------------------------------------------------

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
class EngineAttempt:
    name: str
    chars: int = 0
    ok: bool = False
    error: str = ""
    note: str = ""


@dataclass
class ParseResult:
    client_name: str = ""
    items: list[str] = field(default_factory=list)
    raw_text: str = ""
    engine: str = ""
    warnings: list[str] = field(default_factory=list)
    attempts: list[EngineAttempt] = field(default_factory=list)
    encrypted: bool = False
    needs_password: bool = False
    image_only: bool = False
    page_count: int = 0
    diagnostics: str = ""


# ---------------------------------------------------------------------------
# Bytes / PDF metadata helpers
# ---------------------------------------------------------------------------

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


def _inspect_pdf(data: bytes) -> dict[str, Any]:
    """Return encryption / page / image-ish metadata via PyMuPDF when possible."""
    info: dict[str, Any] = {
        "page_count": 0,
        "encrypted": False,
        "needs_password": False,
        "image_only": False,
        "open_error": "",
        "has_images": False,
        "digital_chars_estimate": 0,
    }
    try:
        import pymupdf as fitz
    except ImportError:
        try:
            import fitz  # type: ignore
        except ImportError:
            info["open_error"] = "pymupdf not installed"
            return info

    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        info["open_error"] = str(exc)
        return info

    try:
        info["encrypted"] = bool(getattr(doc, "is_encrypted", False))
        if info["encrypted"]:
            # Try empty password (many “encrypted” PDFs open with "")
            auth = doc.authenticate("")
            info["needs_password"] = not bool(auth) and bool(doc.needs_pass)
            if info["needs_password"]:
                return info

        info["page_count"] = doc.page_count
        digital_chars = 0
        image_pages = 0
        for page in doc:
            txt = (page.get_text("text") or "").strip()
            digital_chars += len(txt)
            images = page.get_images(full=True) or []
            if images:
                info["has_images"] = True
            # Heuristic: page looks image-only if almost no text but has images
            # or large drawings coverage.
            if len(txt) < 15 and images:
                image_pages += 1
            elif len(txt) < 15:
                # No text and no reported images — still may be vector-drawn
                image_pages += 1
        info["digital_chars_estimate"] = digital_chars
        if info["page_count"] > 0 and image_pages >= max(1, info["page_count"] // 2):
            if digital_chars < MIN_USEFUL_CHARS:
                info["image_only"] = True
    finally:
        doc.close()
    return info


def _is_useful(text: str) -> bool:
    cleaned = (text or "").strip()
    if len(cleaned) < MIN_USEFUL_CHARS:
        return False
    # Reject mostly non-alphanumeric garbage from broken encodings
    alnum = sum(ch.isalnum() or ch.isspace() for ch in cleaned)
    return (alnum / max(len(cleaned), 1)) >= 0.45


# ---------------------------------------------------------------------------
# Digital text engines
# ---------------------------------------------------------------------------

def _extract_pdfplumber(data: bytes) -> EngineAttempt:
    attempt = EngineAttempt(name="pdfplumber")
    try:
        import pdfplumber
    except ImportError as exc:
        attempt.error = f"not installed: {exc}"
        return attempt

    try:
        pages: list[str] = []
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages:
                text = page.extract_text(x_tolerance=2, y_tolerance=3) or ""
                if not text.strip():
                    # Word assembly fallback for awkward layouts
                    words = page.extract_words() or []
                    if words:
                        text = " ".join(w.get("text", "") for w in words)
                pages.append(text)
        text = "\n".join(pages).strip()
        attempt.chars = len(text)
        attempt.ok = _is_useful(text)
        attempt.note = "useful" if attempt.ok else "empty/sparse"
        return attempt if not text else _with_text(attempt, text)
    except Exception as exc:
        logger.exception("pdfplumber failed: %s", exc)
        attempt.error = str(exc)
        return attempt


def _extract_pymupdf(data: bytes) -> EngineAttempt:
    attempt = EngineAttempt(name="pymupdf")
    try:
        import pymupdf as fitz
    except ImportError:
        try:
            import fitz  # type: ignore
        except ImportError as exc:
            attempt.error = f"not installed: {exc}"
            return attempt

    try:
        doc = fitz.open(stream=data, filetype="pdf")
        try:
            if getattr(doc, "is_encrypted", False):
                doc.authenticate("")
                if doc.needs_pass:
                    attempt.error = "password-protected (cannot decrypt)"
                    attempt.note = "encrypted"
                    return attempt

            chunks: list[str] = []
            for page in doc:
                # Prefer sorted reading order; also try blocks if sparse
                t = page.get_text("text", sort=True) or ""
                if len(t.strip()) < 20:
                    blocks = page.get_text("blocks") or []
                    block_txt = []
                    for b in blocks:
                        if isinstance(b, (list, tuple)) and len(b) >= 5 and isinstance(b[4], str):
                            block_txt.append(b[4])
                    alt = "\n".join(block_txt).strip()
                    if len(alt) > len(t.strip()):
                        t = alt
                chunks.append(t)
            text = "\n".join(chunks).strip()
            attempt.chars = len(text)
            attempt.ok = _is_useful(text)
            attempt.note = "useful" if attempt.ok else "empty/sparse"
            return attempt if not text else _with_text(attempt, text)
        finally:
            doc.close()
    except Exception as exc:
        logger.exception("pymupdf failed: %s", exc)
        attempt.error = str(exc)
        return attempt


def _extract_pypdf(data: bytes) -> EngineAttempt:
    attempt = EngineAttempt(name="pypdf")
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        attempt.error = f"not installed: {exc}"
        return attempt

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if getattr(reader, "is_encrypted", False):
            try:
                reader.decrypt("")
            except Exception:
                attempt.error = "password-protected (cannot decrypt)"
                attempt.note = "encrypted"
                return attempt
        parts = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception as page_exc:
                parts.append("")
                logger.warning("pypdf page extract failed: %s", page_exc)
        text = "\n".join(parts).strip()
        attempt.chars = len(text)
        attempt.ok = _is_useful(text)
        attempt.note = "useful" if attempt.ok else "empty/sparse"
        return attempt if not text else _with_text(attempt, text)
    except Exception as exc:
        logger.exception("pypdf failed: %s", exc)
        attempt.error = str(exc)
        return attempt


# Attach recovered text without changing dataclass signature much
def _with_text(attempt: EngineAttempt, text: str) -> EngineAttempt:
    attempt._text = text  # type: ignore[attr-defined]
    return attempt


def _text_of(attempt: EngineAttempt) -> str:
    return getattr(attempt, "_text", "") or ""


# ---------------------------------------------------------------------------
# OCR engines
# ---------------------------------------------------------------------------

def _tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def _ocr_pymupdf_tesseract(data: bytes, *, max_pages: int = 8) -> EngineAttempt:
    """Render pages with PyMuPDF and OCR with pytesseract."""
    attempt = EngineAttempt(name="ocr:pymupdf+tesseract")
    if not _tesseract_available():
        attempt.error = "tesseract binary not found on PATH"
        return attempt
    try:
        import pytesseract
        from PIL import Image
        import pymupdf as fitz
    except ImportError:
        try:
            import pytesseract
            from PIL import Image
            import fitz  # type: ignore
        except ImportError as exc:
            attempt.error = f"missing dependency: {exc}"
            return attempt

    try:
        doc = fitz.open(stream=data, filetype="pdf")
        try:
            if getattr(doc, "is_encrypted", False):
                doc.authenticate("")
                if doc.needs_pass:
                    attempt.error = "password-protected (cannot decrypt)"
                    return attempt
            parts: list[str] = []
            page_count = min(doc.page_count, max_pages)
            for i in range(page_count):
                page = doc[i]
                # 2x zoom for better OCR; gray pixmap is enough
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), colorspace=fitz.csGRAY)
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                text = pytesseract.image_to_string(img) or ""
                parts.append(text)
            if doc.page_count > max_pages:
                attempt.note = f"OCR limited to first {max_pages} of {doc.page_count} pages"
            text = "\n".join(parts).strip()
            attempt.chars = len(text)
            attempt.ok = _is_useful(text)
            return attempt if not text else _with_text(attempt, text)
        finally:
            doc.close()
    except Exception as exc:
        logger.exception("pymupdf+tesseract OCR failed: %s", exc)
        attempt.error = str(exc)
        return attempt


def _ocr_pdf2image_tesseract(data: bytes, *, max_pages: int = 8) -> EngineAttempt:
    attempt = EngineAttempt(name="ocr:pdf2image+tesseract")
    if not _tesseract_available():
        attempt.error = "tesseract binary not found on PATH"
        return attempt
    try:
        import pytesseract
        from pdf2image import convert_from_bytes
    except ImportError as exc:
        attempt.error = f"missing dependency: {exc}"
        return attempt

    try:
        images = convert_from_bytes(
            data,
            dpi=200,
            fmt="png",
            first_page=1,
            last_page=max_pages,
        )
        parts = [pytesseract.image_to_string(img) or "" for img in images]
        text = "\n".join(parts).strip()
        attempt.chars = len(text)
        attempt.ok = _is_useful(text)
        attempt.note = f"rendered {len(images)} page(s)"
        return attempt if not text else _with_text(attempt, text)
    except Exception as exc:
        # Common on Cloud when poppler is missing
        logger.exception("pdf2image+tesseract OCR failed: %s", exc)
        attempt.error = str(exc)
        return attempt


def _ocr_rapidocr(data: bytes, *, max_pages: int = 6) -> EngineAttempt:
    """Pure-Python/ONNX OCR fallback (no system Tesseract required)."""
    attempt = EngineAttempt(name="ocr:rapidocr")
    try:
        from rapidocr_onnxruntime import RapidOCR
        import pymupdf as fitz
        import numpy as np
        from PIL import Image
    except ImportError:
        try:
            from rapidocr_onnxruntime import RapidOCR
            import fitz  # type: ignore
            import numpy as np
            from PIL import Image
        except ImportError as exc:
            attempt.error = f"missing dependency: {exc}"
            return attempt

    try:
        ocr = RapidOCR()
        doc = fitz.open(stream=data, filetype="pdf")
        try:
            if getattr(doc, "is_encrypted", False):
                doc.authenticate("")
                if doc.needs_pass:
                    attempt.error = "password-protected (cannot decrypt)"
                    return attempt
            parts: list[str] = []
            page_count = min(doc.page_count, max_pages)
            for i in range(page_count):
                page = doc[i]
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
                arr = np.array(img)
                result, _ = ocr(arr)
                if result:
                    # result items: [box, text, score]
                    lines = [item[1] for item in result if item and len(item) >= 2]
                    parts.append("\n".join(lines))
            text = "\n".join(parts).strip()
            attempt.chars = len(text)
            attempt.ok = _is_useful(text)
            if doc.page_count > max_pages:
                attempt.note = f"OCR limited to first {max_pages} of {doc.page_count} pages"
            return attempt if not text else _with_text(attempt, text)
        finally:
            doc.close()
    except Exception as exc:
        logger.exception("rapidocr failed: %s", exc)
        attempt.error = str(exc)
        return attempt


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def extract_text(source: PathOrBuffer) -> tuple[str, str, list[EngineAttempt], dict[str, Any]]:
    """
    Return (best_text, engine_name, attempts, meta).

    Always runs digital extractors; OCR runs when digital text is empty/sparse
    or the PDF looks image-only.
    """
    data = _as_bytes(source)
    meta = _inspect_pdf(data)
    attempts: list[EngineAttempt] = []

    if meta.get("needs_password"):
        attempts.append(
            EngineAttempt(
                name="open",
                error="PDF is password-protected. Remove the password and re-upload.",
                note="encrypted",
            )
        )
        return "", "none", attempts, meta

    digital_engines = (_extract_pdfplumber, _extract_pymupdf, _extract_pypdf)
    best_text = ""
    best_engine = "none"

    for eng in digital_engines:
        attempt = eng(data)
        attempts.append(attempt)
        text = _text_of(attempt)
        if attempt.ok and text:
            return text, attempt.name, attempts, meta
        if len(text) > len(best_text):
            best_text = text
            best_engine = attempt.name if text else best_engine

    # OCR fallback for empty/sparse / image-only ProSeries exports
    need_ocr = (not _is_useful(best_text)) or meta.get("image_only")
    if need_ocr:
        ocr_engines = (
            _ocr_pymupdf_tesseract,
            _ocr_pdf2image_tesseract,
            _ocr_rapidocr,
        )
        for eng in ocr_engines:
            attempt = eng(data)
            attempts.append(attempt)
            text = _text_of(attempt)
            if attempt.ok and text:
                meta["image_only"] = meta.get("image_only") or True
                return text, attempt.name, attempts, meta
            if len(text) > len(best_text):
                best_text = text
                best_engine = attempt.name if text else best_engine

    return best_text, (best_engine if best_text else "none"), attempts, meta


def _format_diagnostics(
    *,
    filename: str,
    attempts: list[EngineAttempt],
    meta: dict[str, Any],
    raw_text: str,
) -> str:
    lines = [
        "=== PDF extraction diagnostics ===",
        f"file: {filename or '(buffer)'}",
        f"pages: {meta.get('page_count', '?')}",
        f"encrypted: {meta.get('encrypted', False)}",
        f"needs_password: {meta.get('needs_password', False)}",
        f"image_only_heuristic: {meta.get('image_only', False)}",
        f"digital_chars_estimate: {meta.get('digital_chars_estimate', 0)}",
    ]
    if meta.get("open_error"):
        lines.append(f"open_error: {meta['open_error']}")
    lines.append("engines:")
    for a in attempts:
        status = "OK" if a.ok else ("ERR" if a.error else "sparse")
        detail = a.error or a.note or ""
        lines.append(f"  - {a.name}: {status}, chars={a.chars}" + (f" ({detail})" if detail else ""))
    lines.append("=== extracted text below ===")
    if raw_text and raw_text.strip():
        lines.append(raw_text)
    else:
        lines.append("(no text recovered from any engine)")
    return "\n".join(lines)


def parse_checklist_text(raw_text: str) -> ParseResult:
    """Parse already-extracted text into client name + checklist items."""
    # Strip diagnostics header if present when re-parsing displayed raw text
    body = raw_text or ""
    if "=== extracted text below ===" in body:
        body = body.split("=== extracted text below ===", 1)[1].lstrip("\n")
        if body.strip() == "(no text recovered from any engine)":
            body = ""

    result = ParseResult(raw_text=raw_text or "")
    if not body or not body.strip():
        result.warnings.append("No text extracted from PDF.")
        return result

    lines = [ln.rstrip() for ln in body.splitlines()]
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

    # Keep structured raw_text as the body for storage when diagnostics aren't needed
    result.raw_text = body
    return result


def parse_pdf(source: PathOrBuffer, *, filename: str = "") -> ParseResult:
    """Extract + parse a ProSeries Client Checklist PDF."""
    raw, engine, attempts, meta = extract_text(source)
    result = parse_checklist_text(raw)
    result.engine = engine
    result.attempts = attempts
    result.encrypted = bool(meta.get("encrypted"))
    result.needs_password = bool(meta.get("needs_password"))
    result.image_only = bool(meta.get("image_only"))
    result.page_count = int(meta.get("page_count") or 0)

    # Always expose diagnostics + recovered text in raw_text for the UI
    result.diagnostics = _format_diagnostics(
        filename=filename,
        attempts=attempts,
        meta=meta,
        raw_text=raw,
    )
    result.raw_text = result.diagnostics

    if result.needs_password:
        result.warnings.append(
            "This PDF is password-protected. Remove encryption in ProSeries/Acrobat "
            "and upload again."
        )
    elif not raw or not raw.strip():
        if result.image_only:
            result.warnings.append(
                "This looks like an image-only / scanned PDF. OCR was attempted but "
                "returned no usable text. Try exporting a text-based checklist from "
                "ProSeries, or paste items manually from the raw diagnostics below."
            )
        else:
            result.warnings.append(
                f"Failed to extract usable text from {filename or 'PDF'} with "
                f"available engines (including OCR). See raw diagnostics below."
            )
    elif engine.startswith("ocr:"):
        result.warnings.append(
            "Digital text was empty/sparse — used OCR fallback. Please review "
            "extracted items carefully."
        )

    if result.image_only and raw.strip():
        result.warnings.append(
            "PDF appears image-only (common for some ProSeries exports). "
            "Text came from OCR."
        )

    logger.info(
        "Parsed PDF %s via %s: name=%r items=%d pages=%s image_only=%s warnings=%d",
        filename or "(buffer)",
        engine,
        result.client_name,
        len(result.items),
        result.page_count,
        result.image_only,
        len(result.warnings),
    )
    return result


# ---------------------------------------------------------------------------
# Name / item heuristics
# ---------------------------------------------------------------------------

def _guess_client_name(lines: list[str]) -> str:
    head = lines[:40]
    for line in head:
        for pattern in CLIENT_NAME_PATTERNS:
            m = pattern.match(line)
            if m:
                name = _clean_name(m.group(1))
                if name:
                    return name

    for line in head:
        if SKIP_LINE.match(line) or SECTION_HEADERS.match(line):
            continue
        if DOC_KEYWORDS.search(line):
            continue
        if len(line) > 60:
            continue
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

        if in_section and re.match(r"(?i)^\s*(notes?|comments?|signature|office\s+use)\b", line):
            in_section = False

        candidate: Optional[str] = None
        m = BULLET_OR_NUMBER.match(line)
        if m:
            candidate = m.group(1).strip()
        elif in_section and len(line) > 3 and not SKIP_LINE.match(line):
            candidate = line.strip()
        elif DOC_KEYWORDS.search(line) and not SKIP_LINE.match(line):
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
        # Skip diagnostic header lines if user pastes full raw pane
        if line.startswith("===") or line.startswith("file:") or line.startswith("engines:"):
            continue
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(line)
    return items
