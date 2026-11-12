"""
SQLite data access for the tax checklist dashboard.

Schema overview (sample data structure):

  clients
    id              INTEGER PK
    name            TEXT NOT NULL
    email           TEXT (optional)
    proseries_id    TEXT (optional ProSeries / source identifier)
    source_filename TEXT (PDF filename used at ingest)
    notes           TEXT
    raw_pdf_text    TEXT (full extracted text for re-parse / debugging)
    created_at      TEXT ISO-8601
    updated_at      TEXT ISO-8601
    last_activity   TEXT ISO-8601

  checklist_items
    id              INTEGER PK
    client_id       INTEGER FK → clients.id
    description     TEXT NOT NULL
    received        INTEGER 0/1
    received_date   TEXT ISO-8601 nullable
    notes           TEXT
    source_pdf      TEXT
    matched_file_id INTEGER FK → uploaded_files.id nullable
    created_at      TEXT ISO-8601
    updated_at      TEXT ISO-8601

  uploaded_files   (from File Request Pro sync + manual)
    id                INTEGER PK
    frp_file_id       TEXT (FRP remote id; unique when set)
    frp_submission_id TEXT
    filename          TEXT NOT NULL
    submitter_name    TEXT
    submitter_email   TEXT
    received_at       TEXT ISO-8601
    client_id         INTEGER FK → clients.id nullable (manual/auto match)
    checklist_item_id INTEGER FK → checklist_items.id nullable
    raw_payload       TEXT JSON
    matched           INTEGER 0/1
    notes             TEXT
    created_at        TEXT ISO-8601

  settings
    key   TEXT PK
    value TEXT
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator, Iterable, Optional

from taxdash.logging_config import setup_logging

logger = setup_logging()

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = ROOT / "data" / "taxdash.db"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def get_db_path() -> Path:
    override = os.getenv("DATABASE_PATH")
    if override:
        return Path(override)
    return DEFAULT_DB_PATH


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_connection(db_path: Optional[Path] = None) -> Generator[sqlite3.Connection, None, None]:
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Optional[Path] = None) -> None:
    """Create tables if they do not exist."""
    with get_connection(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT,
                proseries_id TEXT,
                source_filename TEXT,
                notes TEXT DEFAULT '',
                raw_pdf_text TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_activity TEXT
            );

            -- matched_file_id / checklist_item_id are soft references (no cross-FK)
            -- to avoid a circular foreign-key dependency between these tables.
            CREATE TABLE IF NOT EXISTS checklist_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL,
                description TEXT NOT NULL,
                received INTEGER NOT NULL DEFAULT 0,
                received_date TEXT,
                notes TEXT DEFAULT '',
                source_pdf TEXT,
                matched_file_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS uploaded_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                frp_file_id TEXT,
                frp_submission_id TEXT,
                filename TEXT NOT NULL,
                submitter_name TEXT,
                submitter_email TEXT,
                received_at TEXT,
                client_id INTEGER,
                checklist_item_id INTEGER,
                raw_payload TEXT,
                matched INTEGER NOT NULL DEFAULT 0,
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE SET NULL
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_uploaded_files_frp_file_id
                ON uploaded_files(frp_file_id)
                WHERE frp_file_id IS NOT NULL AND frp_file_id != '';

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_checklist_client ON checklist_items(client_id);
            CREATE INDEX IF NOT EXISTS idx_files_client ON uploaded_files(client_id);
            CREATE INDEX IF NOT EXISTS idx_clients_name ON clients(name);
            """
        )
    logger.info("Database initialized at %s", get_db_path() if db_path is None else db_path)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def get_frp_token() -> Optional[str]:
    """Prefer Settings-page token; fall back to FRP_API_TOKEN env var."""
    stored = get_setting("frp_api_token")
    if stored:
        return stored.strip() or None
    env = os.getenv("FRP_API_TOKEN", "").strip()
    return env or None


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------

def create_client(
    name: str,
    *,
    email: Optional[str] = None,
    proseries_id: Optional[str] = None,
    source_filename: Optional[str] = None,
    notes: str = "",
    raw_pdf_text: str = "",
    items: Optional[Iterable[str]] = None,
) -> int:
    now = _now()
    item_list = [d.strip() for d in (items or []) if d and str(d).strip()]
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO clients(
                name, email, proseries_id, source_filename, notes, raw_pdf_text,
                created_at, updated_at, last_activity
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name.strip(),
                (email or "").strip() or None,
                (proseries_id or "").strip() or None,
                source_filename,
                notes or "",
                raw_pdf_text or "",
                now,
                now,
                now,
            ),
        )
        client_id = int(cur.lastrowid)
        for desc in item_list:
            conn.execute(
                """
                INSERT INTO checklist_items(
                    client_id, description, received, source_pdf,
                    created_at, updated_at
                ) VALUES (?, ?, 0, ?, ?, ?)
                """,
                (client_id, desc, source_filename, now, now),
            )
        logger.info("Created client id=%s name=%r items=%s", client_id, name, len(item_list))
        return client_id


def update_client(
    client_id: int,
    *,
    name: Optional[str] = None,
    email: Optional[str] = None,
    proseries_id: Optional[str] = None,
    notes: Optional[str] = None,
    raw_pdf_text: Optional[str] = None,
    touch_activity: bool = True,
) -> None:
    fields: list[str] = []
    values: list[Any] = []
    if name is not None:
        fields.append("name = ?")
        values.append(name.strip())
    if email is not None:
        fields.append("email = ?")
        values.append(email.strip() or None)
    if proseries_id is not None:
        fields.append("proseries_id = ?")
        values.append(proseries_id.strip() or None)
    if notes is not None:
        fields.append("notes = ?")
        values.append(notes)
    if raw_pdf_text is not None:
        fields.append("raw_pdf_text = ?")
        values.append(raw_pdf_text)

    now = _now()
    fields.append("updated_at = ?")
    values.append(now)
    if touch_activity:
        fields.append("last_activity = ?")
        values.append(now)

    if not fields:
        return

    values.append(client_id)
    with get_connection() as conn:
        conn.execute(f"UPDATE clients SET {', '.join(fields)} WHERE id = ?", values)


def delete_client(client_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM clients WHERE id = ?", (client_id,))
    logger.info("Deleted client id=%s", client_id)


def get_client(client_id: int) -> Optional[dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
        return dict(row) if row else None


def list_clients(
    *,
    search: str = "",
    missing_only: bool = False,
) -> list[dict[str, Any]]:
    """Return clients with aggregate checklist stats."""
    sql = """
        SELECT
            c.*,
            COUNT(i.id) AS total_items,
            COALESCE(SUM(CASE WHEN i.received = 1 THEN 1 ELSE 0 END), 0) AS received_count,
            COALESCE(SUM(CASE WHEN i.received = 0 THEN 1 ELSE 0 END), 0) AS missing_count
        FROM clients c
        LEFT JOIN checklist_items i ON i.client_id = c.id
    """
    clauses: list[str] = []
    params: list[Any] = []
    if search.strip():
        clauses.append("(c.name LIKE ? OR IFNULL(c.email, '') LIKE ?)")
        like = f"%{search.strip()}%"
        params.extend([like, like])

    if clauses:
        sql += " WHERE " + " AND ".join(clauses)

    sql += " GROUP BY c.id"

    if missing_only:
        sql += " HAVING missing_count > 0"

    sql += " ORDER BY COALESCE(c.last_activity, c.updated_at) DESC, c.name COLLATE NOCASE"

    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            total = int(d["total_items"] or 0)
            received = int(d["received_count"] or 0)
            d["pct_complete"] = round((received / total) * 100, 1) if total else 0.0
            results.append(d)
        return results


# ---------------------------------------------------------------------------
# Checklist items
# ---------------------------------------------------------------------------

def list_checklist_items(client_id: int) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT i.*, f.filename AS matched_filename
            FROM checklist_items i
            LEFT JOIN uploaded_files f ON f.id = i.matched_file_id
            WHERE i.client_id = ?
            ORDER BY i.received ASC, i.id ASC
            """,
            (client_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def replace_checklist_items(
    client_id: int,
    descriptions: Iterable[str],
    *,
    source_pdf: Optional[str] = None,
) -> None:
    """Replace all checklist items for a client (used after re-parse / edit).

    Keeps received=True items whose description still matches (case-insensitive).
    """
    now = _now()
    cleaned = [d.strip() for d in descriptions if d and d.strip()]
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id, description, received, received_date, notes, matched_file_id "
            "FROM checklist_items WHERE client_id = ?",
            (client_id,),
        ).fetchall()
        by_desc = {r["description"].strip().lower(): dict(r) for r in existing}

        conn.execute("DELETE FROM checklist_items WHERE client_id = ?", (client_id,))
        for desc in cleaned:
            prev = by_desc.get(desc.lower())
            if prev:
                conn.execute(
                    """
                    INSERT INTO checklist_items(
                        client_id, description, received, received_date, notes,
                        source_pdf, matched_file_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        client_id,
                        desc,
                        prev["received"],
                        prev["received_date"],
                        prev["notes"] or "",
                        source_pdf,
                        prev["matched_file_id"],
                        now,
                        now,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO checklist_items(
                        client_id, description, received, source_pdf,
                        created_at, updated_at
                    ) VALUES (?, ?, 0, ?, ?, ?)
                    """,
                    (client_id, desc, source_pdf, now, now),
                )
        conn.execute(
            "UPDATE clients SET updated_at = ?, last_activity = ? WHERE id = ?",
            (now, now, client_id),
        )


def set_item_received(
    item_id: int,
    received: bool,
    *,
    notes: Optional[str] = None,
    matched_file_id: Optional[int] = None,
) -> None:
    now = _now()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT client_id FROM checklist_items WHERE id = ?", (item_id,)
        ).fetchone()
        if not row:
            return
        fields = [
            "received = ?",
            "received_date = ?",
            "updated_at = ?",
        ]
        values: list[Any] = [1 if received else 0, now if received else None, now]
        if notes is not None:
            fields.append("notes = ?")
            values.append(notes)
        if matched_file_id is not None:
            fields.append("matched_file_id = ?")
            values.append(matched_file_id)
        elif not received:
            fields.append("matched_file_id = NULL")

        values.append(item_id)
        conn.execute(
            f"UPDATE checklist_items SET {', '.join(fields)} WHERE id = ?",
            values,
        )
        conn.execute(
            "UPDATE clients SET updated_at = ?, last_activity = ? WHERE id = ?",
            (now, now, row["client_id"]),
        )


def add_checklist_item(client_id: int, description: str, *, source_pdf: Optional[str] = None) -> int:
    now = _now()
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO checklist_items(
                client_id, description, received, source_pdf, created_at, updated_at
            ) VALUES (?, ?, 0, ?, ?, ?)
            """,
            (client_id, description.strip(), source_pdf, now, now),
        )
        conn.execute(
            "UPDATE clients SET updated_at = ?, last_activity = ? WHERE id = ?",
            (now, now, client_id),
        )
        return int(cur.lastrowid)


def delete_checklist_item(item_id: int) -> None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT client_id FROM checklist_items WHERE id = ?", (item_id,)
        ).fetchone()
        conn.execute("DELETE FROM checklist_items WHERE id = ?", (item_id,))
        if row:
            now = _now()
            conn.execute(
                "UPDATE clients SET updated_at = ?, last_activity = ? WHERE id = ?",
                (now, now, row["client_id"]),
            )


def missing_items_report() -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT c.name AS client_name, c.email, i.description, i.notes, i.source_pdf
            FROM checklist_items i
            JOIN clients c ON c.id = i.client_id
            WHERE i.received = 0
            ORDER BY c.name COLLATE NOCASE, i.description COLLATE NOCASE
            """
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Uploaded files (FRP)
# ---------------------------------------------------------------------------

def upsert_uploaded_file(
    *,
    filename: str,
    frp_file_id: Optional[str] = None,
    frp_submission_id: Optional[str] = None,
    submitter_name: Optional[str] = None,
    submitter_email: Optional[str] = None,
    received_at: Optional[str] = None,
    raw_payload: Optional[dict[str, Any]] = None,
) -> tuple[int, bool]:
    """Insert or update an uploaded file by frp_file_id.

    Returns (local_id, created_new).
    """
    now = _now()
    payload_json = json.dumps(raw_payload) if raw_payload is not None else None
    with get_connection() as conn:
        existing = None
        if frp_file_id:
            existing = conn.execute(
                "SELECT id FROM uploaded_files WHERE frp_file_id = ?",
                (str(frp_file_id),),
            ).fetchone()

        if existing:
            conn.execute(
                """
                UPDATE uploaded_files SET
                    filename = ?,
                    frp_submission_id = COALESCE(?, frp_submission_id),
                    submitter_name = COALESCE(?, submitter_name),
                    submitter_email = COALESCE(?, submitter_email),
                    received_at = COALESCE(?, received_at),
                    raw_payload = COALESCE(?, raw_payload)
                WHERE id = ?
                """,
                (
                    filename,
                    frp_submission_id,
                    submitter_name,
                    submitter_email,
                    received_at,
                    payload_json,
                    existing["id"],
                ),
            )
            return int(existing["id"]), False

        cur = conn.execute(
            """
            INSERT INTO uploaded_files(
                frp_file_id, frp_submission_id, filename, submitter_name,
                submitter_email, received_at, raw_payload, matched, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                str(frp_file_id) if frp_file_id else None,
                str(frp_submission_id) if frp_submission_id else None,
                filename,
                submitter_name,
                submitter_email,
                received_at or now,
                payload_json,
                now,
            ),
        )
        return int(cur.lastrowid), True


def list_uploaded_files(
    *,
    unmatched_only: bool = False,
    client_id: Optional[int] = None,
) -> list[dict[str, Any]]:
    sql = """
        SELECT f.*, c.name AS client_name, i.description AS item_description
        FROM uploaded_files f
        LEFT JOIN clients c ON c.id = f.client_id
        LEFT JOIN checklist_items i ON i.id = f.checklist_item_id
        WHERE 1=1
    """
    params: list[Any] = []
    if unmatched_only:
        sql += " AND (f.client_id IS NULL OR f.matched = 0)"
    if client_id is not None:
        sql += " AND f.client_id = ?"
        params.append(client_id)
    sql += " ORDER BY COALESCE(f.received_at, f.created_at) DESC"
    with get_connection() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def assign_file_to_client(
    file_id: int,
    client_id: Optional[int],
    *,
    checklist_item_id: Optional[int] = None,
    mark_item_received: bool = True,
) -> None:
    now = _now()
    with get_connection() as conn:
        matched = 1 if client_id else 0
        conn.execute(
            """
            UPDATE uploaded_files
            SET client_id = ?, checklist_item_id = ?, matched = ?
            WHERE id = ?
            """,
            (client_id, checklist_item_id, matched, file_id),
        )
        if client_id and checklist_item_id and mark_item_received:
            conn.execute(
                """
                UPDATE checklist_items
                SET received = 1, received_date = ?, matched_file_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, file_id, now, checklist_item_id),
            )
            conn.execute(
                "UPDATE clients SET updated_at = ?, last_activity = ? WHERE id = ?",
                (now, now, client_id),
            )
        elif client_id:
            conn.execute(
                "UPDATE clients SET updated_at = ?, last_activity = ? WHERE id = ?",
                (now, now, client_id),
            )


def dashboard_summary() -> dict[str, Any]:
    with get_connection() as conn:
        clients = conn.execute("SELECT COUNT(*) AS n FROM clients").fetchone()["n"]
        items = conn.execute("SELECT COUNT(*) AS n FROM checklist_items").fetchone()["n"]
        received = conn.execute(
            "SELECT COUNT(*) AS n FROM checklist_items WHERE received = 1"
        ).fetchone()["n"]
        missing = conn.execute(
            "SELECT COUNT(*) AS n FROM checklist_items WHERE received = 0"
        ).fetchone()["n"]
        unmatched = conn.execute(
            "SELECT COUNT(*) AS n FROM uploaded_files WHERE client_id IS NULL OR matched = 0"
        ).fetchone()["n"]
        files = conn.execute("SELECT COUNT(*) AS n FROM uploaded_files").fetchone()["n"]
    return {
        "clients": clients,
        "items": items,
        "received": received,
        "missing": missing,
        "unmatched_files": unmatched,
        "files": files,
    }
