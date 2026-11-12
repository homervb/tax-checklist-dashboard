"""On-demand sync from File Request Pro into local SQLite + auto-matching."""

from __future__ import annotations

from typing import Any, Optional

from taxdash import db
from taxdash.frp_client import (
    FRPError,
    FileRequestProClient,
    extract_submitter,
)
from taxdash.logging_config import setup_logging
from taxdash.matching import match_file_record

logger = setup_logging()


def sync_from_frp(
    *,
    token: Optional[str] = None,
    max_pages: int = 10,
    auto_match: bool = True,
) -> dict[str, Any]:
    """
    Pull recent FRP submissions/files into uploaded_files, then optionally
    auto-match unmatched rows to clients / checklist items.

    Returns a summary dict suitable for Streamlit display.
    """
    api_token = token or db.get_frp_token()
    if not api_token:
        raise FRPError(
            "No File Request Pro API token configured. "
            "Add it on the Settings page or set FRP_API_TOKEN."
        )

    client = FileRequestProClient(api_token)
    created = 0
    updated = 0
    errors: list[str] = []

    # Prefer submissions (include form name/email + nested files)
    submissions_seen = 0
    try:
        for submission in client.iter_all_submissions(max_pages=max_pages):
            submissions_seen += 1
            sub_id = submission.get("id")
            submitter_name, submitter_email = extract_submitter(submission)
            files = submission.get("files") or []
            if not files:
                continue
            for f in files:
                c, u = _store_file(
                    f,
                    frp_submission_id=sub_id,
                    submitter_name=submitter_name,
                    submitter_email=submitter_email,
                    raw_payload={"submission": submission, "file": f},
                )
                created += c
                updated += u
    except FRPError as exc:
        logger.warning("Submission sync failed, falling back to /files: %s", exc)
        errors.append(f"Submissions endpoint: {exc}")

    # Also pull /files to catch uploads not nested under submissions
    try:
        for f in client.iter_all_files(max_pages=max_pages):
            submitter_name, submitter_email = extract_submitter(f)
            c, u = _store_file(
                f,
                frp_submission_id=f.get("file_request_id") or f.get("submission_id"),
                submitter_name=submitter_name,
                submitter_email=submitter_email,
                raw_payload={"file": f},
            )
            created += c
            updated += u
    except FRPError as exc:
        logger.exception("Files sync failed: %s", exc)
        errors.append(f"Files endpoint: {exc}")
        if created == 0 and updated == 0:
            raise

    match_summary = {"assigned": 0, "item_matched": 0, "skipped": 0}
    if auto_match:
        match_summary = auto_match_unmatched()

    summary = {
        "submissions_scanned": submissions_seen,
        "files_created": created,
        "files_updated": updated,
        "auto_match": match_summary,
        "errors": errors,
    }
    logger.info("FRP sync complete: %s", summary)
    return summary


def _store_file(
    file_obj: dict[str, Any],
    *,
    frp_submission_id: Any = None,
    submitter_name: Optional[str] = None,
    submitter_email: Optional[str] = None,
    raw_payload: Optional[dict[str, Any]] = None,
) -> tuple[int, int]:
    """Returns (created_count, updated_count) as 0/1 ints."""
    filename = (
        file_obj.get("original_filename")
        or file_obj.get("filename")
        or file_obj.get("name")
        or "unknown"
    )
    frp_file_id = file_obj.get("id")
    received_at = file_obj.get("created_at") or file_obj.get("updated_at")
    local_id, is_new = db.upsert_uploaded_file(
        filename=str(filename),
        frp_file_id=str(frp_file_id) if frp_file_id is not None else None,
        frp_submission_id=str(frp_submission_id) if frp_submission_id is not None else None,
        submitter_name=submitter_name,
        submitter_email=submitter_email,
        received_at=str(received_at) if received_at else None,
        raw_payload=raw_payload,
    )
    _ = local_id
    return (1, 0) if is_new else (0, 1)


def auto_match_unmatched() -> dict[str, int]:
    clients = db.list_clients()
    items_by_client: dict[int, list[dict[str, Any]]] = {
        int(c["id"]): db.list_checklist_items(int(c["id"])) for c in clients
    }
    unmatched = db.list_uploaded_files(unmatched_only=True)
    assigned = 0
    item_matched = 0
    skipped = 0

    for row in unmatched:
        suggestion = match_file_record(row, clients, items_by_client)
        if not suggestion["client_id"]:
            skipped += 1
            continue
        db.assign_file_to_client(
            int(row["id"]),
            suggestion["client_id"],
            checklist_item_id=suggestion["checklist_item_id"],
            mark_item_received=bool(suggestion["checklist_item_id"]),
        )
        assigned += 1
        if suggestion["checklist_item_id"]:
            item_matched += 1
            # Refresh in-memory items so a file isn't matched twice
            cid = suggestion["client_id"]
            items_by_client[cid] = db.list_checklist_items(cid)

    return {
        "assigned": assigned,
        "item_matched": item_matched,
        "skipped": skipped,
    }
