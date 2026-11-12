#!/usr/bin/env python3
"""Load sample_pdfs/*.pdf into the local SQLite DB for a quick demo."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from taxdash import db
from taxdash.pdf_parser import parse_pdf


def main() -> int:
    db.init_db()
    pdf_dir = ROOT / "sample_pdfs"
    pdfs = sorted(pdf_dir.glob("*.pdf"))
    if not pdfs:
        print("No PDFs in sample_pdfs/. Run: python scripts/make_sample_pdf.py")
        return 1

    raw_dir = ROOT / "data" / "raw_pdfs"
    raw_dir.mkdir(parents=True, exist_ok=True)

    for path in pdfs:
        result = parse_pdf(path, filename=path.name)
        dest = raw_dir / path.name
        dest.write_bytes(path.read_bytes())
        cid = db.create_client(
            result.client_name or path.stem.replace("_", " "),
            source_filename=path.name,
            raw_pdf_text=result.raw_text,
            items=result.items,
        )
        print(f"Client #{cid}: {result.client_name} ({len(result.items)} items) from {path.name}")

    # Demo unmatched FRP-style file
    db.upsert_uploaded_file(
        filename="mystery_upload_bank_statement.pdf",
        frp_file_id="demo-unmatched-1",
        submitter_name="Unknown Client",
        submitter_email="unknown@example.com",
        raw_payload={"demo": True},
    )
    print("Added 1 demo unmatched FRP file.")
    print("Done. Run: streamlit run app.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
