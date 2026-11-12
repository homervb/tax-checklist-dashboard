"""Environment / upload diagnostics for Streamlit Community Cloud debugging."""

from __future__ import annotations

import platform
import sys
import traceback
from typing import Any, Optional


def _pkg_version(name: str) -> str:
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version(name)
        except PackageNotFoundError:
            return "not installed"
    except Exception as exc:
        return f"error: {exc}"


def probe_libraries() -> dict[str, Any]:
    """Return installed PDF-related library versions and import status."""
    libs = {
        "pypdf": "pypdf",
        "PyPDF2": "PyPDF2",
        "pdfplumber": "pdfplumber",
        "pymupdf": "pymupdf",
        "pikepdf": "pikepdf",
        "pdfminer.six": "pdfminer.six",
        "pdf2image": "pdf2image",
        "pytesseract": "pytesseract",
        "Pillow": "Pillow",
        "rapidocr-onnxruntime": "rapidocr-onnxruntime",
        "streamlit": "streamlit",
    }
    out: dict[str, Any] = {
        "python": sys.version.replace("\n", " "),
        "platform": platform.platform(),
        "packages": {},
        "imports": {},
        "binaries": {},
    }
    for label, dist in libs.items():
        out["packages"][label] = _pkg_version(dist)

    # Actual import checks (more trustworthy than metadata alone)
    import_targets = {
        "pypdf": "pypdf",
        "PyPDF2": "PyPDF2",
        "pdfplumber": "pdfplumber",
        "pymupdf": "pymupdf",
        "fitz": "fitz",
        "pikepdf": "pikepdf",
        "pdfminer.high_level": "pdfminer.high_level",
        "pdf2image": "pdf2image",
        "pytesseract": "pytesseract",
        "PIL": "PIL",
    }
    for label, mod in import_targets.items():
        try:
            __import__(mod)
            out["imports"][label] = "OK"
        except Exception as exc:
            out["imports"][label] = f"FAIL: {type(exc).__name__}: {exc}"

    import shutil

    for binary in ("tesseract", "pdftoppm", "pdftotext", "qpdf"):
        path = shutil.which(binary)
        out["binaries"][binary] = path or "not found on PATH"

    return out


def probe_pdf_bytes(data: Optional[bytes], *, filename: str = "") -> dict[str, Any]:
    """Inspect uploaded PDF bytes (size, magic header, quick open)."""
    info: dict[str, Any] = {
        "filename": filename or "(buffer)",
        "byte_length": 0 if data is None else len(data),
        "is_none": data is None,
        "magic_hex": "",
        "magic_ascii": "",
        "starts_with_pdf_header": False,
        "contains_eof": False,
        "quick_open": {},
    }
    if not data:
        info["quick_open"]["error"] = "empty or missing bytes"
        return info

    head = data[:32]
    info["magic_hex"] = head.hex(" ")
    info["magic_ascii"] = "".join(chr(b) if 32 <= b < 127 else "." for b in head)
    info["starts_with_pdf_header"] = data[:5] == b"%PDF-"
    info["contains_eof"] = b"%%EOF" in data[-2048:]

    # Quick open with the lightest available library
    for label, opener in (
        ("pypdf", _quick_pypdf),
        ("pymupdf", _quick_pymupdf),
        ("pdfplumber", _quick_pdfplumber),
    ):
        try:
            info["quick_open"][label] = opener(data)
        except Exception as exc:
            info["quick_open"][label] = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(limit=6),
            }
    return info


def _quick_pypdf(data: bytes) -> dict[str, Any]:
    from pypdf import PdfReader
    import io

    reader = PdfReader(io.BytesIO(data), strict=False)
    encrypted = bool(getattr(reader, "is_encrypted", False))
    if encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:
            return {"ok": False, "encrypted": True, "error": str(exc)}
    n = len(reader.pages)
    sample = ""
    if n:
        sample = (reader.pages[0].extract_text() or "")[:200]
    return {
        "ok": True,
        "encrypted": encrypted,
        "pages": n,
        "page0_chars": len(sample),
        "page0_preview": sample,
    }


def _quick_pymupdf(data: bytes) -> dict[str, Any]:
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz  # type: ignore

    doc = fitz.open(stream=data, filetype="pdf")
    try:
        encrypted = bool(doc.is_encrypted)
        if encrypted:
            doc.authenticate("")
            if doc.needs_pass:
                return {"ok": False, "encrypted": True, "error": "needs password"}
        sample = (doc[0].get_text("text") if doc.page_count else "")[:200]
        return {
            "ok": True,
            "encrypted": encrypted,
            "pages": doc.page_count,
            "page0_chars": len(sample),
            "page0_preview": sample,
        }
    finally:
        doc.close()


def _quick_pdfplumber(data: bytes) -> dict[str, Any]:
    import pdfplumber
    import io

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        n = len(pdf.pages)
        sample = ""
        if n:
            sample = (pdf.pages[0].extract_text() or "")[:200]
        return {
            "ok": True,
            "pages": n,
            "page0_chars": len(sample),
            "page0_preview": sample,
        }


def format_debug_report(
    *,
    file_info: dict[str, Any],
    lib_info: dict[str, Any],
    attempts: list[Any],
    raw_text: str = "",
) -> str:
    """Human-readable debug blob for the Streamlit UI."""
    lines: list[str] = []
    lines.append("=== STREAMLIT CLOUD / EXTRACTION DEBUG ===")
    lines.append(f"python: {lib_info.get('python')}")
    lines.append(f"platform: {lib_info.get('platform')}")
    lines.append("")
    lines.append("--- uploaded file ---")
    for key in (
        "filename",
        "byte_length",
        "is_none",
        "starts_with_pdf_header",
        "contains_eof",
        "magic_ascii",
        "magic_hex",
    ):
        lines.append(f"{key}: {file_info.get(key)}")
    lines.append("quick_open:")
    for eng, detail in (file_info.get("quick_open") or {}).items():
        lines.append(f"  {eng}: {detail}")

    lines.append("")
    lines.append("--- package versions ---")
    for name, ver in (lib_info.get("packages") or {}).items():
        lines.append(f"  {name}: {ver}")
    lines.append("--- imports ---")
    for name, status in (lib_info.get("imports") or {}).items():
        lines.append(f"  {name}: {status}")
    lines.append("--- binaries ---")
    for name, status in (lib_info.get("binaries") or {}).items():
        lines.append(f"  {name}: {status}")

    lines.append("")
    lines.append("--- extraction attempts ---")
    if not attempts:
        lines.append("  (none recorded)")
    for a in attempts:
        # Support dataclass or dict
        if hasattr(a, "name"):
            name = a.name
            ok = a.ok
            chars = a.chars
            error = a.error
            note = a.note
            tb = getattr(a, "traceback", "") or ""
            preview = getattr(a, "preview", "") or ""
        else:
            name = a.get("name")
            ok = a.get("ok")
            chars = a.get("chars")
            error = a.get("error")
            note = a.get("note")
            tb = a.get("traceback") or ""
            preview = a.get("preview") or ""
        status = "OK" if ok else ("ERR" if error else "sparse")
        lines.append(f"  - {name}: {status}, chars={chars}")
        if note:
            lines.append(f"      note: {note}")
        if error:
            lines.append(f"      error: {error}")
        if tb:
            lines.append("      traceback:")
            for tb_line in str(tb).strip().splitlines()[-12:]:
                lines.append(f"        {tb_line}")
        if preview:
            lines.append(f"      preview: {preview!r}")

    lines.append("")
    lines.append("=== extracted text below ===")
    body = (raw_text or "").strip()
    lines.append(body if body else "(no text recovered from any engine)")
    return "\n".join(lines)
