"""Tests for Streamlit-safe upload extraction path."""

from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from taxdash.streamlit_extract import (  # noqa: E402
    extract_and_parse_upload,
    read_uploaded_bytes,
)


class FakeUpload(io.BytesIO):
    """Minimal stand-in for streamlit.UploadedFile."""

    def __init__(self, data: bytes, name: str = "2.pdf"):
        super().__init__(data)
        self.name = name
        self.size = len(data)

    def getvalue(self) -> bytes:
        return bytes(self.getbuffer())


def test_read_seek_and_getvalue():
    data = b"%PDF-1.4 fake"
    up = FakeUpload(data)
    up.read(4)  # exhaust partially
    got, how = read_uploaded_bytes(up)
    assert got.startswith(b"%PDF-")
    assert "getvalue" in how or "bytes" in how


def test_extract_sample_pdf():
    path = ROOT / "sample_pdfs" / "michael_orlando_checklist.pdf"
    assert path.exists()
    up = FakeUpload(path.read_bytes(), name="2.pdf")
    # Simulate Streamlit having already peeked at the file
    _ = up.read(16)
    result = extract_and_parse_upload(up, filename="2.pdf")
    assert result.byte_length > 0
    assert result.starts_with_pdf_header
    assert result.attempts, "expected per-library attempts"
    assert result.engine != "none"
    assert result.raw_text.strip()
    assert result.parse.client_name == "Michael Orlando"
    assert len(result.parse.items) >= 8
    # Each attempt should have text or an explicit error/empty note
    for a in result.attempts:
        assert a.name
        assert a.ok or a.error or a.chars == 0


if __name__ == "__main__":
    test_read_seek_and_getvalue()
    test_extract_sample_pdf()
    print("OK")
