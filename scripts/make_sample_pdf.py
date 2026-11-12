#!/usr/bin/env python3
"""Generate sample ProSeries-style Client Checklist PDFs for local testing."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "sample_pdfs"


def build_proseries_style(
    path: Path,
    *,
    client_name: str,
    address_lines: list[str],
    firm_lines: list[str],
    tax_items: list[str],
    additional_items: list[str],
    intro: str,
) -> None:
    try:
        import fitz  # pymupdf
    except ImportError as exc:
        raise SystemExit("pymupdf is required: pip install pymupdf") from exc

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)

    y = 60
    page.insert_text((54, y), client_name, fontsize=12, fontname="helv")
    for i, line in enumerate(address_lines):
        page.insert_text((54, y + 16 * (i + 1)), line, fontsize=10, fontname="helv")

    for i, line in enumerate(firm_lines):
        page.insert_text((380, y + 16 * i), line, fontsize=10 if i else 11, fontname="helv")

    page.insert_textbox(fitz.Rect(54, 130, 558, 185), intro, fontsize=10, fontname="helv")

    y = 200
    page.insert_text((54, y), "Tax Documents:", fontsize=12, fontname="hebo")
    y += 22
    for item in tax_items:
        page.draw_rect(fitz.Rect(54, y - 8, 66, y + 4), width=0.8)
        page.insert_text((74, y), item, fontsize=10, fontname="helv")
        y += 18

    y += 10
    page.insert_text((54, y), "Additional paperwork:", fontsize=12, fontname="hebo")
    y += 22
    for item in additional_items:
        page.draw_rect(fitz.Rect(54, y - 8, 66, y + 4), width=0.8)
        page.insert_text((74, y), item, fontsize=10, fontname="helv")
        y += 18

    y += 24
    page.insert_textbox(
        fitz.Rect(54, y, 558, y + 60),
        "Thank you for your prompt attention. If you have questions, please call our office.\n\n"
        + (firm_lines[0] if firm_lines else ""),
        fontsize=10,
        fontname="helv",
    )
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()


def make_image_only(src: Path, dest: Path) -> None:
    import fitz

    doc = fitz.open(src)
    out = fitz.open()
    for page in doc:
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        npage = out.new_page(width=page.rect.width, height=page.rect.height)
        npage.insert_image(page.rect, pixmap=pix)
    doc.close()
    out.save(dest)
    out.close()


def main() -> int:
    # Simple earlier demo
    build_proseries_style(
        OUT_DIR / "jordan_lee_checklist.pdf",
        client_name="Jordan Lee",
        address_lines=["100 Demo Road", "Austin, TX 78701"],
        firm_lines=["Demo Tax Group", "1 Main St", "Austin, TX 78701"],
        tax_items=[
            "2025 Form W-2 from Northwind Traders",
            "1099-INT from First National Bank",
            "1099-B brokerage statement (cost basis)",
            "Property tax bill for primary residence",
            "Charitable donation receipts",
            "Health insurance Form 1095-A",
        ],
        additional_items=[
            "Voided check for direct deposit",
            "Prior-year tax return",
        ],
        intro=(
            "Please gather the following documents so we can prepare your income tax return. "
            "Long explanatory paragraphs like this one should not be captured as checklist items."
        ),
    )

    # Structure matching real ProSeries Client Checklists
    orlando = OUT_DIR / "michael_orlando_checklist.pdf"
    build_proseries_style(
        orlando,
        client_name="Michael Orlando",
        address_lines=["123 Palm Ave", "Orlando, FL 32801"],
        firm_lines=["Homer VB Tax & Accounting", "456 Firm Street, Suite 200", "Orlando, FL 32801"],
        tax_items=[
            "W-2 wage statements from all employers",
            "1099-INT interest income statements",
            "1099-DIV dividend statements",
            "1099-B brokerage / stock sale statements",
            "Form 1095-A health insurance marketplace statement",
            "Property tax bills for real estate owned",
        ],
        additional_items=[
            "Copy of driver's license (taxpayer and spouse)",
            "Voided check for direct deposit",
            "Prior-year federal and state tax returns",
            "Charitable contribution receipts",
        ],
        intro=(
            "Please gather the following documents so we can prepare your income tax return. "
            "Bring originals or clear copies. Long explanatory paragraphs like this one should "
            "not be captured as checklist items by the parser."
        ),
    )
    make_image_only(orlando, OUT_DIR / "michael_orlando_image_only.pdf")
    make_image_only(OUT_DIR / "jordan_lee_checklist.pdf", OUT_DIR / "jordan_lee_image_only.pdf")

    # Keep Sam Patel sample too
    build_proseries_style(
        OUT_DIR / "sam_patel_checklist.pdf",
        client_name="Sam Patel",
        address_lines=["88 Market St", "San Jose, CA 95113"],
        firm_lines=["Demo Tax Group", "1 Main St", "Austin, TX 78701"],
        tax_items=[
            "Prior-year tax return",
            "Schedule K-1 from Patel Consulting LLC",
            "Business expense summary / mileage log",
            "Estimated tax payment vouchers",
        ],
        additional_items=["Voided check for direct deposit"],
        intro="Please provide the items listed below for your tax return.",
    )

    for p in sorted(OUT_DIR.glob("*.pdf")):
        print(f"Wrote {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
