"""
Match File Request Pro uploads to clients and checklist items.

Matching rules (v1, intentionally simple):
  1. Client by email (exact, case-insensitive) when both sides have email.
  2. Client by name (normalized token overlap / containment).
  3. Checklist item by keyword overlap between filename and item description.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from taxdash.logging_config import setup_logging

logger = setup_logging()

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_STOP = {
    "the", "and", "or", "for", "of", "a", "an", "to", "in", "on", "at",
    "pdf", "jpg", "jpeg", "png", "doc", "docx", "xls", "xlsx", "zip",
    "scan", "copy", "file", "document", "documents", "tax", "year",
}


def normalize(text: str) -> str:
    return _NON_ALNUM.sub(" ", (text or "").lower()).strip()


def tokens(text: str) -> set[str]:
    return {t for t in normalize(text).split() if t and t not in _STOP and len(t) > 1}


def name_similarity(a: str, b: str) -> float:
    """0..1 score based on token Jaccard + containment."""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    inter = ta & tb
    union = ta | tb
    jaccard = len(inter) / len(union)
    # Boost when one name fully contains the other's tokens
    containment = len(inter) / min(len(ta), len(tb))
    return max(jaccard, containment * 0.9)


def find_best_client(
    *,
    submitter_name: Optional[str],
    submitter_email: Optional[str],
    clients: list[dict[str, Any]],
    min_name_score: float = 0.55,
) -> Optional[dict[str, Any]]:
    if submitter_email:
        email = submitter_email.strip().lower()
        for client in clients:
            c_email = (client.get("email") or "").strip().lower()
            if c_email and c_email == email:
                return client

    if not submitter_name:
        return None

    best = None
    best_score = 0.0
    for client in clients:
        score = name_similarity(submitter_name, client.get("name") or "")
        # Also try matching against filename-style "Last_First"
        if score > best_score:
            best_score = score
            best = client

    if best and best_score >= min_name_score:
        return best
    return None


def find_best_checklist_item(
    filename: str,
    items: list[dict[str, Any]],
    *,
    min_score: float = 0.25,
) -> Optional[dict[str, Any]]:
    """Pick the open checklist item whose description best overlaps the filename."""
    file_tokens = tokens(filename)
    if not file_tokens:
        return None

    best = None
    best_score = 0.0
    for item in items:
        if item.get("received"):
            continue
        desc = item.get("description") or ""
        item_tokens = tokens(desc)
        if not item_tokens:
            continue
        inter = file_tokens & item_tokens
        if not inter:
            # Also check substring presence for compact codes like w2 / 1099
            desc_n = normalize(desc)
            file_n = normalize(filename)
            compact_hits = 0
            for t in item_tokens:
                if len(t) >= 3 and t in file_n.replace(" ", ""):
                    compact_hits += 1
                elif len(t) >= 3 and t in file_n:
                    compact_hits += 1
            score = compact_hits / max(len(item_tokens), 1)
        else:
            score = len(inter) / len(item_tokens)

        # Prefer more specific overlaps
        if score > best_score:
            best_score = score
            best = item

    if best and best_score >= min_score:
        return best
    return None


def match_file_record(
    file_row: dict[str, Any],
    clients: list[dict[str, Any]],
    items_by_client: dict[int, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Return suggested {client_id, checklist_item_id, reason}."""
    client = find_best_client(
        submitter_name=file_row.get("submitter_name"),
        submitter_email=file_row.get("submitter_email"),
        clients=clients,
    )

    # Fallback: try matching filename against client names
    if not client:
        filename = file_row.get("filename") or ""
        best = None
        best_score = 0.0
        for c in clients:
            score = name_similarity(filename, c.get("name") or "")
            if score > best_score:
                best_score = score
                best = c
        if best and best_score >= 0.5:
            client = best

    if not client:
        return {"client_id": None, "checklist_item_id": None, "reason": "no_client_match"}

    items = items_by_client.get(int(client["id"]), [])
    item = find_best_checklist_item(file_row.get("filename") or "", items)
    return {
        "client_id": int(client["id"]),
        "checklist_item_id": int(item["id"]) if item else None,
        "reason": "matched_item" if item else "matched_client_only",
    }
