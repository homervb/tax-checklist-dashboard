"""
Streamlit-safe PDF text extraction.

Designed for Streamlit Community Cloud UploadedFile objects:
  1. Force seek(0) / getvalue() to obtain raw PDF bytes
  2. Try pypdf (bytes → BytesIO)
  3. Try pdfplumber (BytesIO)
  4. Try pymupdf/fitz (stream=..., filetype=\"pdf\")

Every attempt records recovered text OR the exact exception for the UI.
"""

from __future__ import annotations

import io
import traceback
from dataclasses import dataclass, field
from typing import Any, Optional, Union

from taxdash.pdf_parser import ParseResult, parse_checklist_text

UploadedLike = Any  # streamlit.runtime.uploaded_file_manager.UploadedFile or bytes/Path


@dataclass
class AttemptResult:
    name: str
    ok: bool = False
    chars: int = 0
    text: str = ""
    error: str = ""
    traceback: str = ""
    note: str = ""


@dataclass
class StreamlitExtractResult:
    filename: str = ""
    byte_length: int = 0
    magic_ascii: str = ""
    magic_hex: str = ""
    starts_with_pdf_header: bool = False
    attempts: list[AttemptResult] = field(default_factory=list)
    raw_text: str = ""
    engine: str = "none"
    parse: ParseResult = field(default_factory=ParseResult)
    warnings: list[str] = field(default_factory=list)
    debug_report: str = ""


def read_uploaded_bytes(uploaded: UploadedLike) -> tuple[bytes, str]:
    """
    Return (pdf_bytes, how_obtained).

    Streamlit UploadedFile can be exhausted if something already read it.
    Always seek(0) when possible, and prefer getvalue() when available.
    """
    if uploaded is None:
        raise ValueError("uploaded file is None")

    if isinstance(uploaded, (bytes, bytearray)):
        return bytes(uploaded), "raw-bytes"

    # Streamlit UploadedFile exposes getvalue() — most reliable
    if hasattr(uploaded, "getvalue"):
        try:
            if hasattr(uploaded, "seek"):
                uploaded.seek(0)
        except Exception:
            pass
        data = uploaded.getvalue()
        if data is None:
            data = b""
        return bytes(data), "getvalue()"

    # Generic file-like
    if hasattr(uploaded, "seek"):
        uploaded.seek(0)
    if hasattr(uploaded, "read"):
        data = uploaded.read()
        if hasattr(uploaded, "seek"):
            try:
                uploaded.seek(0)
            except Exception:
                pass
        return bytes(data or b""), "read()+seek(0)"

    raise TypeError(f"Unsupported upload type: {type(uploaded)!r}")


def _try_pypdf(data: bytes) -> AttemptResult:
    attempt = AttemptResult(name="pypdf (BytesIO)")
    try:
        from pypdf import PdfReader
    except ImportError:
        try:
            from PyPDF2 import PdfReader  # type: ignore

            attempt.name = "PyPDF2 (BytesIO)"
            attempt.note = "using PyPDF2 fallback"
        except ImportError as exc:
            attempt.error = f"ImportError: {exc}"
            return attempt

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if getattr(reader, "is_encrypted", False):
            try:
                reader.decrypt("")
                attempt.note = "was encrypted; decrypted with empty password"
            except Exception as exc:
                attempt.error = f"encrypted/password-protected: {exc}"
                return attempt

        parts: list[str] = []
        for i, page in enumerate(reader.pages):
            try:
                try:
                    parts.append(page.extract_text(extraction_mode="layout") or "")
                except TypeError:
                    parts.append(page.extract_text() or "")
            except Exception as page_exc:
                parts.append("")
                attempt.note = (
                    (attempt.note + "; " if attempt.note else "")
                    + f"page {i} error: {type(page_exc).__name__}: {page_exc}"
                )

        text = "\n".join(parts).strip()
        attempt.text = text
        attempt.chars = len(text)
        attempt.ok = bool(text)
        if not text:
            attempt.note = (attempt.note + "; " if attempt.note else "") + (
                f"opened OK ({len(reader.pages)} pages) but extract_text() returned empty"
            )
        return attempt
    except Exception as exc:
        attempt.error = f"{type(exc).__name__}: {exc}"
        attempt.traceback = traceback.format_exc()
        return attempt


def _try_pdfplumber(data: bytes) -> AttemptResult:
    attempt = AttemptResult(name="pdfplumber (BytesIO)")
    try:
        import pdfplumber
    except ImportError as exc:
        attempt.error = f"ImportError: {exc}"
        return attempt

    try:
        pages: list[str] = []
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            attempt.note = f"pages={len(pdf.pages)}"
            for i, page in enumerate(pdf.pages):
                try:
                    t = page.extract_text(x_tolerance=2, y_tolerance=3) or ""
                    if not t.strip():
                        t = page.extract_text() or ""
                    pages.append(t)
                except Exception as page_exc:
                    pages.append("")
                    attempt.note += f"; page {i} error: {type(page_exc).__name__}: {page_exc}"

        text = "\n".join(pages).strip()
        attempt.text = text
        attempt.chars = len(text)
        attempt.ok = bool(text)
        if not text:
            attempt.note += "; opened OK but extract_text() returned empty"
        return attempt
    except Exception as exc:
        attempt.error = f"{type(exc).__name__}: {exc}"
        attempt.traceback = traceback.format_exc()
        return attempt


def _try_pymupdf(data: bytes) -> AttemptResult:
    attempt = AttemptResult(name='pymupdf/fitz (stream, filetype="pdf")')
    try:
        try:
            import pymupdf as fitz
        except ImportError:
            import fitz  # type: ignore
    except ImportError as exc:
        attempt.error = f"ImportError: {exc}"
        return attempt

    try:
        doc = fitz.open(stream=data, filetype="pdf")
        try:
            if getattr(doc, "is_encrypted", False):
                auth = doc.authenticate("")
                if doc.needs_pass and not auth:
                    attempt.error = "encrypted/password-protected (needs password)"
                    return attempt
                attempt.note = "was encrypted; authenticated with empty password"

            parts = []
            for i, page in enumerate(doc):
                try:
                    parts.append(page.get_text("text", sort=True) or "")
                except Exception as page_exc:
                    parts.append("")
                    attempt.note = (
                        (attempt.note + "; " if attempt.note else "")
                        + f"page {i} error: {type(page_exc).__name__}: {page_exc}"
                    )

            text = "\n".join(parts).strip()
            attempt.text = text
            attempt.chars = len(text)
            attempt.ok = bool(text)
            attempt.note = (
                (attempt.note + "; " if attempt.note else "")
                + f"pages={doc.page_count}"
                + ("" if text else "; get_text() returned empty")
            )
            return attempt
        finally:
            doc.close()
    except Exception as exc:
        attempt.error = f"{type(exc).__name__}: {exc}"
        attempt.traceback = traceback.format_exc()
        return attempt


def extract_and_parse_upload(
    uploaded: UploadedLike,
    *,
    filename: str = "",
) -> StreamlitExtractResult:
    """
    Main entry used by the Streamlit Upload page.

    Always populates attempts[] with per-library text or exact exceptions.
    """
    result = StreamlitExtractResult()
    result.filename = filename or getattr(uploaded, "name", "") or "(upload)"

    try:
        data, how = read_uploaded_bytes(uploaded)
    except Exception as exc:
        result.warnings.append(f"Failed to read upload bytes: {type(exc).__name__}: {exc}")
        result.debug_report = _format_debug(result)
        result.parse = parse_checklist_text("")
        result.parse.warnings = list(result.warnings)
        return result

    result.byte_length = len(data)
    head = data[:32]
    result.magic_hex = head.hex(" ")
    result.magic_ascii = "".join(chr(b) if 32 <= b < 127 else "." for b in head)
    result.starts_with_pdf_header = data[:5] == b"%PDF-"
    result.warnings.append(f"Read {len(data):,} bytes via {how}")

    if not data:
        result.warnings.append("Upload bytes are empty (0 bytes).")
    elif not result.starts_with_pdf_header:
        result.warnings.append(
            f"File does not start with %PDF- (magic={result.magic_ascii!r}). "
            "Upload may be corrupt or not a PDF."
        )

    # Exact order requested for Streamlit Cloud
    for fn in (_try_pypdf, _try_pdfplumber, _try_pymupdf):
        attempt = fn(data)
        result.attempts.append(attempt)
        if attempt.ok and attempt.text.strip() and not result.raw_text:
            result.raw_text = attempt.text
            result.engine = attempt.name

    # If nothing marked ok but some attempt has partial text, keep the longest
    if not result.raw_text:
        longest = max(result.attempts, key=lambda a: len(a.text or ""), default=None)
        if longest and longest.text.strip():
            result.raw_text = longest.text
            result.engine = longest.name

    result.parse = parse_checklist_text(result.raw_text)
    # Prefer parse warnings + our upload warnings
    result.warnings.extend(result.parse.warnings)
    result.parse.engine = result.engine
    result.parse.raw_text = result.raw_text
    result.debug_report = _format_debug(result)

    # Store combined diagnostics on parse for DB persistence compatibility
    result.parse.diagnostics = result.debug_report
    if not result.raw_text.strip():
        result.parse.raw_text = result.debug_report
    else:
        result.parse.raw_text = result.debug_report

    return result


def _format_debug(result: StreamlitExtractResult) -> str:
    lines = [
        "=== STREAMLIT UPLOAD EXTRACTION DEBUG ===",
        f"filename: {result.filename}",
        f"byte_length: {result.byte_length}",
        f"starts_with_pdf_header: {result.starts_with_pdf_header}",
        f"magic_ascii: {result.magic_ascii}",
        f"magic_hex: {result.magic_hex}",
        f"chosen_engine: {result.engine}",
        "",
        "--- per-library attempts (in order) ---",
    ]
    if not result.attempts:
        lines.append("  (no attempts — bytes could not be read)")
    for a in result.attempts:
        status = "OK" if a.ok else ("ERR" if a.error else "EMPTY")
        lines.append(f"  [{status}] {a.name} — chars={a.chars}")
        if a.note:
            lines.append(f"      note: {a.note}")
        if a.error:
            lines.append(f"      exception: {a.error}")
        if a.traceback:
            lines.append("      traceback:")
            for tl in a.traceback.strip().splitlines()[-15:]:
                lines.append(f"        {tl}")
        preview = (a.text or "")[:300].replace("\n", "\\n")
        lines.append(f"      text_preview: {preview!r}")

    lines.append("")
    lines.append("=== extracted text below ===")
    lines.append(result.raw_text.strip() if result.raw_text.strip() else "(no text recovered)")
    return "\n".join(lines)
