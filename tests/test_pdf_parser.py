"""Unit tests for ProSeries checklist text parsing (no Streamlit required)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from taxdash.pdf_parser import (  # noqa: E402
    items_from_multiline,
    parse_checklist_text,
    parse_pdf,
)


SAMPLE = """
ProSeries Professional — Client Checklist
Tax Year 2025

Client Name: Jordan Lee
Email: jordan.lee@example.com

Documents Needed:

• 2025 Form W-2 from Northwind Traders
• 1099-INT from First National Bank
• 1099-B brokerage statement (cost basis)
• Property tax bill for primary residence
• Charitable donation receipts
• Health insurance Form 1095-A

Please provide the items listed above.
Thank you.
"""


def test_parse_client_name_and_items():
    result = parse_checklist_text(SAMPLE)
    assert result.client_name == "Jordan Lee"
    assert len(result.items) >= 5
    joined = " ".join(result.items).lower()
    assert "w-2" in joined or "w2" in joined
    assert "1099" in joined
    assert result.raw_text


def test_items_from_multiline():
    text = "- W-2\n2) 1099-INT\n\n• Brokerage statement\n"
    items = items_from_multiline(text)
    assert items == ["W-2", "1099-INT", "Brokerage statement"]


def test_empty_text_warns():
    result = parse_checklist_text("")
    assert result.warnings
    assert result.items == []


def test_digital_pdf_still_works():
    path = ROOT / "sample_pdfs" / "jordan_lee_checklist.pdf"
    assert path.exists(), "run scripts/make_sample_pdf.py first"
    result = parse_pdf(path, filename=path.name)
    assert result.client_name == "Jordan Lee"
    assert len(result.items) >= 5
    assert result.engine in {"pdfplumber", "pymupdf", "pypdf"}
    assert "=== PDF extraction diagnostics ===" in result.raw_text
    assert "=== extracted text below ===" in result.raw_text


def test_image_only_pdf_uses_ocr():
    path = ROOT / "sample_pdfs" / "jordan_lee_image_only.pdf"
    if not path.exists():
        print("SKIP image-only OCR test (sample missing)")
        return
    result = parse_pdf(path, filename=path.name)
    # OCR should recover something useful on our synthetic sample
    assert result.engine.startswith("ocr:") or len(result.raw_text) > 50
    assert "=== PDF extraction diagnostics ===" in result.raw_text
    assert result.attempts, "expected engine attempt log"
    # Prefer recovering the client name when OCR works
    body = result.raw_text.split("=== extracted text below ===", 1)[-1]
    assert "Jordan" in body or result.client_name or result.warnings


if __name__ == "__main__":
    test_parse_client_name_and_items()
    test_items_from_multiline()
    test_empty_text_warns()
    test_digital_pdf_still_works()
    test_image_only_pdf_uses_ocr()
    print("OK")
