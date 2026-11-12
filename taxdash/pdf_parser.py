"""
Defensive PDF text extraction for ProSeries Client Checklist PDFs.

Real ProSeries checklists typically look like:
  - Client name top-left, address under it; firm letterhead top-right
  - Section "Tax Documents:" with checkbox items (and category labels)
  - Section "Additional paperwork:" with more checkbox items
  - Short closing + firm name

Extraction order:
  0. Normalize / unlock via pikepdf (empty password, repair)
  1. pdfplumber (digital text + layout)
  2. PyMuPDF (digital text, blocks, widgets/tooltips)
  3. pypdf (digital text + form fields)
  4. OCR: pymupdf+tesseract → pdf2image+tesseract → rapidocr

Always returns diagnostics + recovered text for the UI.
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

MIN_USEFUL_CHARS = 40

# ---------------------------------------------------------------------------
# ProSeries-aware patterns
# ---------------------------------------------------------------------------

CLIENT_NAME_PATTERNS = [
    re.compile(r"(?i)^\s*client(?:\s+name)?\s*[:\-]\s*(.+)$"),
    re.compile(r"(?i)^\s*taxpayer(?:\s+name)?\s*[:\-]\s*(.+)$"),
    re.compile(r"(?i)^\s*name\s*[:\-]\s*(.+)$"),
    re.compile(r"(?i)^\s*prepared\s+for\s*[:\-]?\s*(.+)$"),
    re.compile(r"(?i)^\s*for\s*[:\-]\s*([A-Z][A-Za-z'’\-]+(?:\s+[A-Z][A-Za-z'’\-]+)+)\s*$"),
]

# Primary ProSeries section headers
TAX_DOCS_HEADER = re.compile(r"(?i)^\s*tax\s+documents?\s*:?\s*$")
ADDITIONAL_HEADER = re.compile(
    r"(?i)^\s*additional\s+(?:paperwork|documents?|items?|information)\s*:?\s*$"
)

SECTION_HEADERS = re.compile(
    r"(?i)^\s*(?:tax\s+documents?|additional\s+(?:paperwork|documents?|items?)|"
    r"documents?\s+needed|please\s+provide|items?\s+(?:needed|requested)|"
    r"checklist|requested\s+documents?|information\s+needed|"
    r"what\s+we\s+need|missing\s+documents?)\s*:?\s*$"
)

CLOSING_HEADER = re.compile(
    r"(?i)^\s*(?:thank\s+you|sincerely|regards|best\s+regards|"
    r"if\s+you\s+have\s+questions|please\s+call|notes?|comments?)\b.*"
)

CATEGORY_HEADER = re.compile(
    r"(?i)^\s*(?:income|deductions?|credits?|expenses?|payments?|"
    r"employment|investments?|retirement|education|health(?:care)?|"
    r"business|rental|dependents?|identity|banking|other)\s*:?\s*$"
)

CHECKBOX_PREFIX = re.compile(
    r"^\s*(?:[\u2610\u2611\u2612\u25A1\u25FB\u25FC\u25A0\u25AF\u274F\u2B1C"
    r"\u0394\u25AD]|\[\s*[xX✓✔]?\s*\]|\(\s*[xX]?\s*\))\s*(.+)$"
)

BULLET_OR_NUMBER = re.compile(
    r"^\s*(?:[\-\*\u2022\u2023\u25E6\u2043\u2219\u00B7\u25AA\u25CF\u25CB]|"
    r"[0-9]{1,3}[.)]|[A-Za-z][.)])\s+(.+)$"
)

LEADING_BULLET = re.compile(
    r"^[\-\*\u2022\u2023\u25E6\u2043\u2219\u00B7\u25AA\u25CF\u25CB"
    r"\u2610\u2611\u25A1\[\]]+\s*"
)

INSTRUCTIONAL_LINE = re.compile(
    r"(?i)^\s*(?:please\s+(?:provide|gather|bring|return)|thank\s+you|sincerely|"
    r"if\s+you\s+have\s+questions|call\s+our\s+office|"
    r"bring\s+originals|long\s+explanatory).*"
)

DOC_KEYWORDS = re.compile(
    r"(?i)\b("
    r"w-?2|w-?2[cg]|1099(?:-[A-Z]{1,4})?|1098(?:-[A-Z]{1,3})?|1095-?[ABC]?|"
    r"k-?1|schedule\s+[A-F]|1040|941|940|"
    r"brokerage|closing\s+statement|settlement\s+statement|"
    r"property\s+tax|mortgage\s+interest|charitable|donation|"
    r"medical\s+expense|health\s+insurance|hsa|fsa|"
    r"dependent|driver.?s?\s+license|id\s+card|"
    r"bank\s+statement|voided\s+check|direct\s+deposit|"
    r"prior[\-\s]?year|organizer|questionnaire|"
    r"crypto|stock\s+sale|cost\s+basis|"
    r"rental|business\s+expense|mileage|home\s+office|"
    r"estimated\s+tax|extension|irs\s+notice|state\s+return|"
    r"wage|dividend|interest\s+income"
    r")\b"
)

SKIP_LINE = re.compile(
    r"(?i)^\s*(?:page\s+\d+(?:\s+of\s+\d+)?|confidential|proseries|intuit|copyright|"
    r"www\.|https?://|phone|fax|email\s*:|date\s*:|tax\s+year|"
    r"please\s+return|thank\s+you|sincerely).*$"
)

ADDRESS_LINE = re.compile(
    r"(?i)^\s*(?:\d{1,6}\s+\S+|p\.?\s*o\.?\s*box\b|"
    r"[A-Za-z .'-]+,\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?|"
    r"suite\s+\d+|apt\.?\s*\d+)\s*$"
)

NAME_LIKE = re.compile(
    r"^[A-Z][A-Za-z'’\-]+(?:\s+[A-Z][A-Za-z'’\-\.]+)+$"
)

FIRM_HINT = re.compile(
    r"(?i)\b("
    r"cpa|llc|llp|p\.?c\.?|"
    r"tax\s*&\s*accounting|tax\s+and\s+accounting|accounting\s+(?:firm|group)|"
    r"tax\s+(?:group|firm|associates|advisors?|services?)|"
    r"bookkeeping|associates|partners\b|"
    r"advisory\s+group"
    r")\b"
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
# Bytes helpers
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


def _with_text(attempt: EngineAttempt, text: str) -> EngineAttempt:
    attempt._text = text  # type: ignore[attr-defined]
    return attempt


def _text_of(attempt: EngineAttempt) -> str:
    return getattr(attempt, "_text", "") or ""


def _is_useful(text: str) -> bool:
    cleaned = (text or "").strip()
    if len(cleaned) < MIN_USEFUL_CHARS:
        return False
    alnum = sum(ch.isalnum() or ch.isspace() for ch in cleaned)
    return (alnum / max(len(cleaned), 1)) >= 0.45


def _normalize_pdf_bytes(data: bytes) -> tuple[bytes, EngineAttempt]:
    """Unlock/repair with pikepdf when possible; otherwise return original bytes."""
    attempt = EngineAttempt(name="pikepdf-normalize")
    try:
        import pikepdf
    except ImportError as exc:
        attempt.error = f"not installed: {exc}"
        attempt.note = "skipped"
        return data, attempt

    try:
        # Try empty password first (common for owner-restricted PDFs)
        try:
            pdf = pikepdf.open(io.BytesIO(data))
        except pikepdf.PasswordError:
            try:
                pdf = pikepdf.open(io.BytesIO(data), password="")
            except Exception as exc:
                attempt.error = f"password-protected: {exc}"
                attempt.note = "encrypted"
                return data, attempt

        with pdf:
            out = io.BytesIO()
            # Re-save strips access restrictions when the file could be opened
            # (e.g. owner-password / empty user-password ProSeries exports).
            pdf.save(out)
            normalized = out.getvalue()
        attempt.ok = True
        attempt.chars = len(normalized)
        attempt.note = "normalized"
        return normalized, attempt
    except Exception as exc:
        logger.warning("pikepdf normalize failed: %s", exc)
        attempt.error = str(exc)
        return data, attempt


def _inspect_pdf(data: bytes) -> dict[str, Any]:
    info: dict[str, Any] = {
        "page_count": 0,
        "encrypted": False,
        "needs_password": False,
        "image_only": False,
        "open_error": "",
        "has_images": False,
        "digital_chars_estimate": 0,
        "widget_count": 0,
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
            auth = doc.authenticate("")
            info["needs_password"] = not bool(auth) and bool(doc.needs_pass)
            if info["needs_password"]:
                return info

        info["page_count"] = doc.page_count
        digital_chars = 0
        image_pages = 0
        widgets = 0
        for page in doc:
            txt = (page.get_text("text") or "").strip()
            digital_chars += len(txt)
            images = page.get_images(full=True) or []
            if images:
                info["has_images"] = True
            try:
                widgets += len(page.widgets() or [])
            except Exception:
                pass
            if len(txt) < 15:
                image_pages += 1
        info["digital_chars_estimate"] = digital_chars
        info["widget_count"] = widgets
        if info["page_count"] > 0 and image_pages >= max(1, info["page_count"] // 2):
            if digital_chars < MIN_USEFUL_CHARS:
                info["image_only"] = True
    finally:
        doc.close()
    return info


# ---------------------------------------------------------------------------
# Digital extractors
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
                text = ""
                try:
                    text = page.extract_text(x_tolerance=2, y_tolerance=3) or ""
                except Exception:
                    text = ""
                if not text.strip():
                    try:
                        text = page.extract_text(layout=True) or ""
                    except Exception:
                        text = ""
                if not text.strip():
                    words = page.extract_words() or []
                    if words:
                        # Reconstruct roughly by reading order
                        words_sorted = sorted(
                            words,
                            key=lambda w: (round(w.get("top", 0), 0), w.get("x0", 0)),
                        )
                        lines: list[str] = []
                        cur_top = None
                        cur: list[str] = []
                        for w in words_sorted:
                            top = round(float(w.get("top", 0)), 0)
                            if cur_top is None or abs(top - cur_top) <= 3:
                                cur.append(w.get("text", ""))
                                cur_top = top if cur_top is None else cur_top
                            else:
                                lines.append(" ".join(cur))
                                cur = [w.get("text", "")]
                                cur_top = top
                        if cur:
                            lines.append(" ".join(cur))
                        text = "\n".join(lines)
                pages.append(text)
        text = "\n".join(pages).strip()
        attempt.chars = len(text)
        attempt.ok = _is_useful(text)
        attempt.note = "useful" if attempt.ok else "empty/sparse"
        return _with_text(attempt, text) if text else attempt
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
            widget_labels: list[str] = []
            for page in doc:
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

                # Widget / annotation tooltips often hold checkbox item labels
                try:
                    for w in page.widgets() or []:
                        for attr in ("field_label", "field_name", "text"):
                            val = getattr(w, attr, None)
                            if val and isinstance(val, str) and len(val.strip()) > 2:
                                widget_labels.append(val.strip())
                except Exception:
                    pass
                try:
                    for annot in page.annots() or []:
                        info = annot.info or {}
                        for key in ("content", "title", "subject"):
                            val = info.get(key)
                            if val and isinstance(val, str) and len(val.strip()) > 2:
                                widget_labels.append(val.strip())
                except Exception:
                    pass

            text = "\n".join(chunks).strip()
            if widget_labels:
                # Append unique widget labels to help item detection
                extras = []
                seen = {ln.strip().lower() for ln in text.splitlines() if ln.strip()}
                for lab in widget_labels:
                    if lab.lower() not in seen:
                        extras.append(lab)
                        seen.add(lab.lower())
                if extras:
                    text = (text + "\n" + "\n".join(extras)).strip()
                    attempt.note = f"widgets={len(widget_labels)}"

            attempt.chars = len(text)
            attempt.ok = _is_useful(text)
            if not attempt.note:
                attempt.note = "useful" if attempt.ok else "empty/sparse"
            return _with_text(attempt, text) if text else attempt
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

        parts: list[str] = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception as page_exc:
                logger.warning("pypdf page extract failed: %s", page_exc)

        # Form field names / tooltips
        try:
            fields = reader.get_fields() or {}
            for name, field in fields.items():
                if not isinstance(field, dict):
                    continue
                for key in ("/TU", "/T", "/V"):
                    val = field.get(key)
                    if isinstance(val, str) and len(val.strip()) > 2 and val.strip() not in ("/Off", "Off", "Yes"):
                        parts.append(val.strip())
                if name and len(str(name).strip()) > 2:
                    parts.append(str(name).strip())
        except Exception:
            pass

        text = "\n".join(parts).strip()
        attempt.chars = len(text)
        attempt.ok = _is_useful(text)
        attempt.note = "useful" if attempt.ok else "empty/sparse"
        return _with_text(attempt, text) if text else attempt
    except Exception as exc:
        logger.exception("pypdf failed: %s", exc)
        attempt.error = str(exc)
        return attempt


def _extract_pdfplumber_left_column(data: bytes) -> EngineAttempt:
    """Extra pass: crop left ~55% of first page (client block lives top-left)."""
    attempt = EngineAttempt(name="pdfplumber-left-col")
    try:
        import pdfplumber
    except ImportError as exc:
        attempt.error = f"not installed: {exc}"
        return attempt

    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            if not pdf.pages:
                attempt.note = "no pages"
                return attempt
            page = pdf.pages[0]
            w = float(page.width or 0)
            h = float(page.height or 0)
            if w <= 0 or h <= 0:
                attempt.error = "invalid page size"
                return attempt
            cropped = page.crop((0, 0, w * 0.55, h * 0.35))
            text = cropped.extract_text(x_tolerance=2, y_tolerance=3) or ""
        attempt.chars = len(text.strip())
        attempt.ok = len(text.strip()) >= 5
        attempt.note = "top-left crop"
        return _with_text(attempt, text.strip()) if text.strip() else attempt
    except Exception as exc:
        attempt.error = str(exc)
        return attempt


# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------

def _tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def _preprocess_for_ocr(img: Any) -> Any:
    """Light contrast boost to help checkbox / small text OCR."""
    try:
        from PIL import ImageEnhance, ImageOps

        g = ImageOps.grayscale(img)
        g = ImageOps.autocontrast(g)
        g = ImageEnhance.Contrast(g).enhance(1.4)
        return g
    except Exception:
        return img


def _ocr_pymupdf_tesseract(data: bytes, *, max_pages: int = 8) -> EngineAttempt:
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
                # 2.5x ~ 180-200 DPI equivalent depending on page size; sharper for OCR
                pix = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5), colorspace=fitz.csGRAY)
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                img = _preprocess_for_ocr(img)
                # Treat as sparse document; preserve layout somewhat
                text = pytesseract.image_to_string(img, config="--psm 6") or ""
                parts.append(text)
            if doc.page_count > max_pages:
                attempt.note = f"OCR limited to first {max_pages} of {doc.page_count} pages"
            text = "\n".join(parts).strip()
            attempt.chars = len(text)
            attempt.ok = _is_useful(text)
            return _with_text(attempt, text) if text else attempt
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
            dpi=300,
            fmt="png",
            first_page=1,
            last_page=max_pages,
        )
        parts = []
        for img in images:
            img = _preprocess_for_ocr(img)
            parts.append(pytesseract.image_to_string(img, config="--psm 6") or "")
        text = "\n".join(parts).strip()
        attempt.chars = len(text)
        attempt.ok = _is_useful(text)
        attempt.note = f"rendered {len(images)} page(s) @300dpi"
        return _with_text(attempt, text) if text else attempt
    except Exception as exc:
        logger.exception("pdf2image+tesseract OCR failed: %s", exc)
        attempt.error = str(exc)
        return attempt


def _ocr_rapidocr(data: bytes, *, max_pages: int = 6) -> EngineAttempt:
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
                pix = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5))
                img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
                arr = np.array(img)
                result, _ = ocr(arr)
                if result:
                    lines = [item[1] for item in result if item and len(item) >= 2]
                    parts.append("\n".join(lines))
            text = "\n".join(parts).strip()
            attempt.chars = len(text)
            attempt.ok = _is_useful(text)
            if doc.page_count > max_pages:
                attempt.note = f"OCR limited to first {max_pages} of {doc.page_count} pages"
            return _with_text(attempt, text) if text else attempt
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
    raw_data = _as_bytes(source)
    attempts: list[EngineAttempt] = []

    data, norm_attempt = _normalize_pdf_bytes(raw_data)
    attempts.append(norm_attempt)

    meta = _inspect_pdf(data)
    if meta.get("needs_password"):
        attempts.append(
            EngineAttempt(
                name="open",
                error="PDF is password-protected. Open it once (enter the ProSeries password), "
                "re-save an unlocked copy, and upload that.",
                note="encrypted",
            )
        )
        return "", "none", attempts, meta

    best_text = ""
    best_engine = "none"
    left_col_text = ""

    # Prefer PyMuPDF first — it keeps left/right letterhead columns separated
    # better than pdfplumber's default merge on ProSeries-style layouts.
    for eng in (
        _extract_pymupdf,
        _extract_pdfplumber,
        _extract_pypdf,
        _extract_pdfplumber_left_column,
    ):
        attempt = eng(data)
        attempts.append(attempt)
        text = _text_of(attempt)
        if attempt.name == "pdfplumber-left-col":
            left_col_text = text
            # Supplemental only — never the sole early-return source
            continue
        if attempt.ok and text:
            # Attach left-column crop at top to help client-name detection
            if left_col_text:
                # Ensure left-col was collected; if this engine ran before left-col,
                # we'll merge after the loop.
                pass
            best_text = text
            best_engine = attempt.name
            break
        if len(text) > len(best_text):
            best_text = text
            best_engine = attempt.name if text else best_engine

    # Always gather left-column crop for client-name detection (top-left block).
    # Do NOT merge it into the body text — that duplicates sections and pollutes items.
    if not any(a.name == "pdfplumber-left-col" for a in attempts):
        left_attempt = _extract_pdfplumber_left_column(data)
        attempts.append(left_attempt)
        left_col_text = _text_of(left_attempt)
    else:
        left_col_text = left_col_text or next(
            (_text_of(a) for a in attempts if a.name == "pdfplumber-left-col"),
            "",
        )
    if left_col_text:
        meta["left_column_text"] = left_col_text

    if not best_text and left_col_text:
        best_text = left_col_text
        best_engine = "pdfplumber-left-col"

    if _is_useful(best_text) and best_engine != "none":
        return best_text, best_engine, attempts, meta

    need_ocr = (not _is_useful(best_text)) or meta.get("image_only")
    if need_ocr:
        for eng in (
            _ocr_pymupdf_tesseract,
            _ocr_pdf2image_tesseract,
            _ocr_rapidocr,
        ):
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
        f"widgets: {meta.get('widget_count', 0)}",
    ]
    if meta.get("open_error"):
        lines.append(f"open_error: {meta['open_error']}")
    lines.append("engines:")
    for a in attempts:
        status = "OK" if a.ok else ("ERR" if a.error else "sparse")
        detail = a.error or a.note or ""
        lines.append(
            f"  - {a.name}: {status}, chars={a.chars}"
            + (f" ({detail})" if detail else "")
        )
    lines.append("=== extracted text below ===")
    if raw_text and raw_text.strip():
        lines.append(raw_text)
    else:
        lines.append("(no text recovered from any engine)")
    return "\n".join(lines)


def _strip_diagnostics(raw_text: str) -> str:
    body = raw_text or ""
    if "=== extracted text below ===" in body:
        body = body.split("=== extracted text below ===", 1)[1].lstrip("\n")
        if body.strip() == "(no text recovered from any engine)":
            body = ""
    return body


# ---------------------------------------------------------------------------
# Parsing: name + checklist items
# ---------------------------------------------------------------------------

def parse_checklist_text(raw_text: str) -> ParseResult:
    body = _strip_diagnostics(raw_text)
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

    result.items = _guess_items_proseries(non_empty)
    if not result.items:
        # Fallback to generic heuristics
        result.items = _guess_items_generic(non_empty)
    if not result.items:
        result.warnings.append(
            "No checklist items detected — review raw text and add items manually."
        )

    result.raw_text = body
    return result


def parse_pdf(source: PathOrBuffer, *, filename: str = "") -> ParseResult:
    raw, engine, attempts, meta = extract_text(source)
    result = parse_checklist_text(raw)
    result.engine = engine
    result.attempts = attempts
    result.encrypted = bool(meta.get("encrypted"))
    result.needs_password = bool(meta.get("needs_password"))
    result.image_only = bool(meta.get("image_only"))
    result.page_count = int(meta.get("page_count") or 0)

    # Prefer client name from the top-left crop (ProSeries: name left, firm right)
    left = (meta.get("left_column_text") or "").strip()
    if left:
        left_lines = [ln.strip() for ln in left.splitlines() if ln.strip()]
        left_name = _guess_client_name(left_lines)
        if left_name:
            result.client_name = left_name

    result.diagnostics = _format_diagnostics(
        filename=filename,
        attempts=attempts,
        meta=meta,
        raw_text=raw,
    )
    result.raw_text = result.diagnostics

    if result.needs_password:
        result.warnings.append(
            "This PDF is password-protected (common for emailed ProSeries checklists). "
            "Open it with the ProSeries password, re-save an unlocked PDF, and upload that."
        )
    elif not raw or not raw.strip():
        if result.image_only:
            result.warnings.append(
                "This looks like an image-only / scanned PDF. OCR was attempted but "
                "returned no usable text. Paste items manually from the document, or "
                "export a text-based checklist from ProSeries."
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


def _split_dual_column_line(line: str) -> list[str]:
    """Split pdfplumber-merged letterhead lines into left / right fragments."""
    parts = re.split(r"\s{2,}", line.strip())
    if len(parts) >= 2:
        return [p.strip() for p in parts if p.strip()]
    # No wide gap — try peeling a firm-looking suffix off a name prefix
    if FIRM_HINT.search(line):
        words = line.split()
        for i in range(1, len(words)):
            right = " ".join(words[i:])
            left = " ".join(words[:i])
            if FIRM_HINT.search(right) and NAME_LIKE.match(left):
                return [left, right]
    return [line.strip()] if line.strip() else []


def _guess_client_name(lines: list[str]) -> str:
    """Prefer labeled names, then first name-like line in the top-left block."""
    # Expand dual-column merged lines so left-side client name is visible
    expanded: list[str] = []
    for line in lines:
        expanded.extend(_split_dual_column_line(line))

    head = expanded[:40]
    for line in head:
        for pattern in CLIENT_NAME_PATTERNS:
            m = pattern.match(line)
            if m:
                name = _clean_name(m.group(1))
                if name:
                    return name

    # Top-of-doc heuristic: first name-like line that isn't firm letterhead / address
    for line in head[:16]:
        if SKIP_LINE.match(line) or SECTION_HEADERS.match(line):
            continue
        if ADDRESS_LINE.match(line):
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
        # Skip firm letterhead lines
        if FIRM_HINT.search(line):
            continue
        if NAME_LIKE.match(line) and not line.lower().startswith(
            ("please", "documents", "tax", "additional")
        ):
            return _clean_name(line)

    return ""


def _clean_name(value: str) -> str:
    value = value.strip(" \t-:;,.|")
    value = re.sub(r"\s{2,}", " ", value)
    value = re.sub(r"\s+\d{4}\s*(?:tax\s*)?year.*$", "", value, flags=re.I)
    if len(value) < 3 or len(value) > 80:
        return ""
    return value


def _guess_items_proseries(lines: list[str]) -> list[str]:
    """
    Capture checkbox items under Tax Documents: and Additional paperwork:.
    Skip long explanatory paragraphs and bare category headers.
    """
    items: list[str] = []
    seen: set[str] = set()
    mode: Optional[str] = None  # 'tax' | 'additional' | None

    for line in lines:
        if TAX_DOCS_HEADER.match(line):
            mode = "tax"
            continue
        if ADDITIONAL_HEADER.match(line):
            mode = "additional"
            continue
        if mode and CLOSING_HEADER.match(line):
            mode = None
            continue
        # Leaving checklist region when another major header appears
        if mode and SECTION_HEADERS.match(line) and not (
            TAX_DOCS_HEADER.match(line) or ADDITIONAL_HEADER.match(line)
        ):
            mode = None
            continue

        if mode is None:
            continue

        if CATEGORY_HEADER.match(line):
            continue
        if SKIP_LINE.match(line) or INSTRUCTIONAL_LINE.match(line):
            continue
        if ADDRESS_LINE.match(line):
            continue
        # Skip letterhead / dual-column merged lines that leaked into a section
        if FIRM_HINT.search(line) or re.search(r"\s{2,}", line):
            continue
        if NAME_LIKE.match(line) and not DOC_KEYWORDS.search(line):
            continue

        candidate = _line_to_item(line)
        if not candidate:
            continue
        # Skip long paragraph-like lines (explanatory copy under sections)
        if len(candidate) > 140 or candidate.count(".") >= 2:
            continue
        if len(candidate.split()) > 22 and not DOC_KEYWORDS.search(candidate):
            continue
        if ADDRESS_LINE.match(candidate) or FIRM_HINT.search(candidate):
            continue

        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(candidate)

    return items


def _guess_items_generic(lines: list[str]) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    in_section = False

    for line in lines:
        if SECTION_HEADERS.match(line):
            in_section = True
            continue
        if in_section and CLOSING_HEADER.match(line):
            in_section = False
            continue

        candidate = None
        extracted = _line_to_item(line)
        if extracted and (
            in_section
            or CHECKBOX_PREFIX.match(line)
            or BULLET_OR_NUMBER.match(line)
            or DOC_KEYWORDS.search(line)
        ):
            candidate = extracted

        if not candidate:
            continue
        if SKIP_LINE.match(candidate) or INSTRUCTIONAL_LINE.match(candidate):
            continue
        if SECTION_HEADERS.match(candidate) or CATEGORY_HEADER.match(candidate):
            continue
        if len(candidate) < 3 or len(candidate) > 140:
            continue
        if candidate.count(".") >= 2:
            continue

        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(candidate)

    return items


def _line_to_item(line: str) -> Optional[str]:
    m = CHECKBOX_PREFIX.match(line)
    if m:
        return _clean_item(m.group(1))
    m = BULLET_OR_NUMBER.match(line)
    if m:
        return _clean_item(m.group(1))
    # Plain line under a section — treat as item if short enough
    cleaned = _clean_item(line)
    if cleaned and 3 <= len(cleaned) <= 140:
        return cleaned
    return None


def _clean_item(value: str) -> str:
    value = value.strip()
    m = CHECKBOX_PREFIX.match(value)
    if m:
        value = m.group(1).strip()
    m = BULLET_OR_NUMBER.match(value)
    if m:
        value = m.group(1).strip()
    value = LEADING_BULLET.sub("", value).strip()
    value = value.strip(" \t-:;|")
    # OCR often leaves checkbox glyphs / junk like "CL", "_]", "[]"
    value = re.sub(r"^[\u2610\u2611\u25A1\[\]]+\s*", "", value).strip()
    value = re.sub(r"^(?:CL|_\]|\[\]|\|_)\s+", "", value).strip()
    return value


def items_from_multiline(text: str) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    for raw in (text or "").splitlines():
        line = _clean_item(raw)
        if not line or INSTRUCTIONAL_LINE.match(line):
            continue
        if line.startswith("===") or line.startswith("file:") or line.startswith("engines:"):
            continue
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(line)
    return items
