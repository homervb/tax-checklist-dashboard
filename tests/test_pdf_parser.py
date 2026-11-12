"""Unit tests for ProSeries checklist text parsing (no Streamlit required)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from taxdash.pdf_parser import items_from_multiline, parse_checklist_text  # noqa: E402


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


if __name__ == "__main__":
    test_parse_client_name_and_items()
    test_items_from_multiline()
    test_empty_text_warns()
    print("OK")
