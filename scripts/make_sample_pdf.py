#!/usr/bin/env python3
"""Generate a sample ProSeries-style Client Checklist PDF for local testing."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "sample_pdfs"


def build_pdf(path: Path, client_name: str, items: list[str]) -> None:
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise SystemExit("pymupdf is required: pip install pymupdf") from exc

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    y = 72
    lines = [
        "ProSeries Professional — Client Checklist",
        f"Tax Year 2025",
        "",
        f"Client Name: {client_name}",
        "Email: client@example.com",
        "",
        "Documents Needed:",
        "",
        *[f"• {item}" for item in items],
        "",
        "Please provide the items listed above at your earliest convenience.",
        "Thank you.",
    ]
    for line in lines:
        page.insert_text((72, y), line, fontsize=11, fontname="helv")
        y += 16
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()


def main() -> None:
    samples = [
        (
            "jordan_lee_checklist.pdf",
            "Jordan Lee",
            [
                "2025 Form W-2 from Northwind Traders",
                "1099-INT from First National Bank",
                "1099-B brokerage statement (cost basis)",
                "Property tax bill for primary residence",
                "Charitable donation receipts",
                "Health insurance Form 1095-A",
            ],
        ),
        (
            "sam_patel_checklist.pdf",
            "Sam Patel",
            [
                "Prior-year tax return",
                "Schedule K-1 from Patel Consulting LLC",
                "Business expense summary / mileage log",
                "Estimated tax payment vouchers",
                "Voided check for direct deposit",
            ],
        ),
    ]
    for filename, name, items in samples:
        path = OUT_DIR / filename
        build_pdf(path, name, items)
        print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
