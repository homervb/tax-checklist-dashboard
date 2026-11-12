"""
File Request Pro (FRP) API client.

Docs:
  https://filerequestpro.com/docs/frp/api-authentication
  https://filerequestpro.com/docs/frp/api-submissions
  https://filerequestpro.com/docs/frp/api-file

Auth: Authorization: Bearer <token>
Base: https://filerequestpro.com/api  (override with FRP_API_BASE_URL)

v1 uses on-demand sync (no webhooks yet).
"""

from __future__ import annotations

import os
from typing import Any, Optional

import requests

from taxdash.logging_config import setup_logging

logger = setup_logging()

DEFAULT_BASE_URL = "https://filerequestpro.com/api"


class FRPError(Exception):
    """Raised when the File Request Pro API returns an error."""

    def __init__(self, message: str, *, status_code: Optional[int] = None, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class FileRequestProClient:
    def __init__(
        self,
        token: str,
        *,
        base_url: Optional[str] = None,
        timeout: float = 30.0,
    ):
        if not token or not token.strip():
            raise FRPError("File Request Pro API token is empty.")
        self.token = token.strip()
        self.base_url = (base_url or os.getenv("FRP_API_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
                "User-Agent": "tax-checklist-dashboard/0.1",
            }
        )

    def _get(self, path: str, params: Optional[dict[str, Any]] = None) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            resp = self.session.get(url, params=params or {}, timeout=self.timeout)
        except requests.RequestException as exc:
            logger.exception("FRP request failed: %s", exc)
            raise FRPError(f"Network error calling File Request Pro: {exc}") from exc

        if resp.status_code == 401:
            raise FRPError(
                "Unauthorized — check your File Request Pro API token.",
                status_code=401,
            )
        if resp.status_code >= 400:
            raise FRPError(
                f"File Request Pro API error HTTP {resp.status_code}: {resp.text[:500]}",
                status_code=resp.status_code,
                payload=resp.text,
            )

        try:
            return resp.json()
        except ValueError as exc:
            raise FRPError("File Request Pro returned non-JSON response.") from exc

    def list_files(
        self,
        *,
        page: int = 1,
        page_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """GET /files — paginated list of uploaded files."""
        params: dict[str, Any] = {"page": page}
        if page_id:
            params["page_id"] = page_id
        return self._get("files", params=params)

    def get_file(self, file_id: str | int) -> dict[str, Any]:
        """GET /files/{id}"""
        return self._get(f"files/{file_id}")

    def list_submissions(
        self,
        *,
        page: int = 1,
        page_id: Optional[str] = None,
        search: Optional[str] = None,
    ) -> dict[str, Any]:
        """GET /submissions — paginated submissions with files + form entries."""
        params: dict[str, Any] = {"page": page}
        if page_id:
            params["page_id"] = page_id
        if search:
            params["search"] = search
        return self._get("submissions", params=params)

    def get_submission(self, submission_id: str | int) -> dict[str, Any]:
        """GET /submissions/{id}"""
        return self._get(f"submissions/{submission_id}")

    def iter_all_files(
        self,
        *,
        max_pages: int = 20,
        page_id: Optional[str] = None,
    ):
        """Yield file records across pages (stops after max_pages)."""
        for page_num in range(1, max_pages + 1):
            payload = self.list_files(page=page_num, page_id=page_id)
            data = _unwrap_page(payload)
            items = data.get("data") or []
            if not items:
                break
            for item in items:
                yield item
            last_page = int(data.get("last_page") or page_num)
            if page_num >= last_page:
                break

    def iter_all_submissions(
        self,
        *,
        max_pages: int = 20,
        page_id: Optional[str] = None,
    ):
        """Yield submission records across pages (stops after max_pages)."""
        for page_num in range(1, max_pages + 1):
            payload = self.list_submissions(page=page_num, page_id=page_id)
            data = _unwrap_page(payload)
            items = data.get("data") or []
            if not items:
                break
            for item in items:
                yield item
            last_page = int(data.get("last_page") or page_num)
            if page_num >= last_page:
                break

    def test_connection(self) -> dict[str, Any]:
        """Lightweight connectivity check — fetch first page of files."""
        payload = self.list_files(page=1)
        data = _unwrap_page(payload)
        total = data.get("total")
        return {
            "ok": True,
            "total_files": total,
            "message": f"Connected. FRP reports {total} file(s)."
            if total is not None
            else "Connected.",
        }


def _unwrap_page(payload: Any) -> dict[str, Any]:
    """Normalize FRP paginated responses: {success, data: {current_page, data: [...]}}."""
    if not isinstance(payload, dict):
        return {"data": [], "last_page": 1}
    data = payload.get("data", payload)
    if isinstance(data, list):
        return {"data": data, "last_page": 1, "total": len(data)}
    if isinstance(data, dict):
        return data
    return {"data": [], "last_page": 1}


def extract_submitter(submission_or_file: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    """Best-effort name/email from FRP form entries or file form_response."""
    name = None
    email = None

    entries = (
        submission_or_file.get("formentries")
        or submission_or_file.get("form_response")
        or submission_or_file.get("form_entries")
        or []
    )
    if isinstance(entries, dict):
        entries = [
            {"field_label": k, "value": v} for k, v in entries.items()
        ]

    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        label = str(entry.get("field_label") or entry.get("label") or "").strip().lower()
        value = entry.get("value")
        if value is None:
            continue
        value_s = str(value).strip()
        if not value_s:
            continue
        if label in {"name", "full name", "client name", "taxpayer", "contact name"} and not name:
            name = value_s
        elif "email" in label and not email:
            email = value_s

    # Top-level fallbacks some FRP payloads use
    name = name or submission_or_file.get("name") or submission_or_file.get("client_name")
    email = email or submission_or_file.get("email") or submission_or_file.get("client_email")
    return (
        str(name).strip() if name else None,
        str(email).strip() if email else None,
    )
