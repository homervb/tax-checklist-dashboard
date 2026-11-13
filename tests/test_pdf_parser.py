"""Unit tests for ProSeries checklist text parsing (paste workflow)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from taxdash.checklist_parser import items_from_multiline, parse_checklist_text  # noqa: E402


SAMPLE = """
Michael Orlando
123 Palm Ave
Orlando, FL 32801
Homer VB Tax & Accounting
456 Firm Street, Suite 200

Please gather the following documents so we can prepare your income tax return.
Bring originals or clear copies. Long explanatory paragraphs like this one should
not be captured as checklist items by the parser.

Tax Documents:
□ W-2 wage statements from all employers
□ 1099-INT interest income statements
□ 1099-DIV dividend statements
□ 1099-B brokerage / stock sale statements
□ Form 1095-A health insurance marketplace statement
□ Property tax bills for real estate owned

Additional paperwork:
□ Copy of driver's license (taxpayer and spouse)
□ Voided check for direct deposit
□ Prior-year federal and state tax returns
□ Charitable contribution receipts

Thank you for your prompt attention. If you have questions, please call our office.

Homer VB Tax & Accounting
"""


def test_parse_proseries_structure():
    result = parse_checklist_text(SAMPLE)
    assert result.client_name == "Michael Orlando"
    assert len(result.items) >= 8
    joined = " ".join(result.items).lower()
    assert "w-2" in joined
    assert "additional" not in joined
    assert "thank you" not in joined
    assert not any("long explanatory paragraphs" in i.lower() for i in result.items)


def test_items_from_multiline():
    text = "- W-2\n2) 1099-INT\n\n• Brokerage statement\n"
    items = items_from_multiline(text)
    assert items == ["W-2", "1099-INT", "Brokerage statement"]


def test_empty_text_warns():
    result = parse_checklist_text("")
    assert result.warnings
    assert result.items == []


if __name__ == "__main__":
    test_parse_proseries_structure()
    test_items_from_multiline()
    test_empty_text_warns()
    print("OK")
