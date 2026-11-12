"""SQLite + matching smoke tests using a temp database."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from taxdash import db  # noqa: E402
from taxdash.matching import find_best_checklist_item, find_best_client  # noqa: E402


def test_client_checklist_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        os.environ["DATABASE_PATH"] = str(db_path)
        # Reset module default by re-init
        db.init_db(db_path)

        cid = db.create_client(
            "Jordan Lee",
            email="jordan@example.com",
            source_filename="jordan.pdf",
            raw_pdf_text="raw",
            items=["2025 Form W-2", "1099-INT", "Property tax bill"],
        )
        clients = db.list_clients()
        assert len(clients) == 1
        assert clients[0]["total_items"] == 3
        assert clients[0]["missing_count"] == 3

        items = db.list_checklist_items(cid)
        db.set_item_received(items[0]["id"], True)
        clients = db.list_clients()
        assert clients[0]["received_count"] == 1
        assert clients[0]["missing_count"] == 2

        missing = db.missing_items_report()
        assert len(missing) == 2


def test_matching_helpers():
    clients = [
        {"id": 1, "name": "Jordan Lee", "email": "jordan@example.com"},
        {"id": 2, "name": "Sam Patel", "email": "sam@example.com"},
    ]
    match = find_best_client(
        submitter_name="Jordan Lee",
        submitter_email=None,
        clients=clients,
    )
    assert match and match["id"] == 1

    match_email = find_best_client(
        submitter_name="Someone Else",
        submitter_email="sam@example.com",
        clients=clients,
    )
    assert match_email and match_email["id"] == 2

    items = [
        {"id": 10, "description": "2025 Form W-2 from Northwind", "received": 0},
        {"id": 11, "description": "Charitable donation receipts", "received": 0},
    ]
    best = find_best_checklist_item("Jordan_Lee_W2_2025.pdf", items)
    assert best and best["id"] == 10


if __name__ == "__main__":
    test_client_checklist_roundtrip()
    test_matching_helpers()
    print("OK")
