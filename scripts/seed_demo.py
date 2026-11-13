#!/usr/bin/env python3
"""Seed SQLite with a couple of pasted-checklist-style demo clients."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from taxdash import db
from taxdash.checklist_parser import parse_checklist_text

SAMPLES = [
    """Michael Orlando
123 Palm Ave
Orlando, FL 32801

Tax Documents:
W-2 wage statements from all employers
1099-INT interest income statements
1099-DIV dividend statements
Property tax bills for real estate owned

Additional paperwork:
Voided check for direct deposit
Prior-year federal and state tax returns

Thank you.
""",
    """Sam Patel
88 Market St
San Jose, CA 95113

Tax Documents:
Schedule K-1 from Patel Consulting LLC
Business expense summary / mileage log
Estimated tax payment vouchers

Additional paperwork:
Voided check for direct deposit

Thank you.
""",
]


def main() -> int:
    db.init_db()
    for text in SAMPLES:
        result = parse_checklist_text(text)
        cid = db.create_client(
            result.client_name or "Unknown",
            source_filename="pasted-checklist-text",
            raw_pdf_text=result.raw_text,
            items=result.items,
        )
        print(f"Client #{cid}: {result.client_name} ({len(result.items)} items)")

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
