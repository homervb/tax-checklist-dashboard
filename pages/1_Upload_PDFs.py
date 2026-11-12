"""Upload / drop ProSeries Client Checklist PDFs and review extraction."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from taxdash import db
from taxdash.pdf_debug import probe_libraries
from taxdash.pdf_parser import items_from_multiline
from taxdash.streamlit_extract import extract_and_parse_upload
from taxdash.ui import bootstrap, inject_styles, select_client

st.set_page_config(page_title="Upload PDFs", page_icon="📤", layout="wide")
bootstrap()
inject_styles()

st.title("Upload ProSeries PDFs")
st.caption(
    "Drop one or more Client Checklist PDFs. Extraction reads Streamlit upload "
    "bytes via seek(0)/getvalue(), then tries pypdf → pdfplumber → pymupdf."
)

with st.expander("Environment / library debug (Streamlit Cloud)", expanded=False):
    libs = probe_libraries()
    st.json(
        {
            "python": libs.get("python"),
            "platform": libs.get("platform"),
            "packages": libs.get("packages"),
            "imports": libs.get("imports"),
            "binaries": libs.get("binaries"),
        }
    )

UPLOAD_DIR = Path(__file__).resolve().parent.parent / "data" / "raw_pdfs"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

uploaded = st.file_uploader(
    "ProSeries Client Checklist PDFs",
    type=["pdf"],
    accept_multiple_files=True,
)

if not uploaded:
    st.info(
        "Tip: sample PDFs live in `sample_pdfs/`. "
        "Generate with `python scripts/make_sample_pdf.py`."
    )
    st.stop()

if "parse_drafts" not in st.session_state:
    st.session_state["parse_drafts"] = {}


def _run_extract(uploaded_file) -> dict:
    """Extract from a Streamlit UploadedFile using the Cloud-safe path."""
    # Explicit seek before anything else (as requested)
    try:
        uploaded_file.seek(0)
    except Exception:
        pass

    filename = getattr(uploaded_file, "name", "upload.pdf")
    result = extract_and_parse_upload(uploaded_file, filename=filename)

    # Also keep a raw copy on disk for re-parse
    saved_path = ""
    try:
        # Re-seek / getvalue for saving
        try:
            uploaded_file.seek(0)
        except Exception:
            pass
        data = (
            uploaded_file.getvalue()
            if hasattr(uploaded_file, "getvalue")
            else uploaded_file.read()
        )
        data = bytes(data or b"")
        dest = UPLOAD_DIR / Path(filename).name
        dest.write_bytes(data)
        saved_path = str(dest)
    except Exception as exc:
        st.warning(f"Could not cache PDF to disk: {exc}")

    parse = result.parse
    return {
        "filename": filename,
        "saved_path": saved_path,
        "byte_length": result.byte_length,
        "magic_ascii": result.magic_ascii,
        "magic_hex": result.magic_hex,
        "starts_with_pdf_header": result.starts_with_pdf_header,
        "client_name": parse.client_name or Path(filename).stem.replace("_", " "),
        "email": "",
        "items_text": "\n".join(parse.items),
        "raw_text": result.raw_text,
        "debug_report": result.debug_report,
        "engine": result.engine,
        "warnings": result.warnings,
        "attempts": [
            {
                "name": a.name,
                "ok": a.ok,
                "chars": a.chars,
                "text": a.text,
                "error": a.error,
                "traceback": a.traceback,
                "note": a.note,
            }
            for a in result.attempts
        ],
    }


# Parse each upload once (keyed by name+size)
for f in uploaded:
    key = f"{f.name}:{f.size}"
    if key not in st.session_state["parse_drafts"]:
        try:
            st.session_state["parse_drafts"][key] = _run_extract(f)
        except Exception as exc:
            st.exception(exc)
            st.session_state["parse_drafts"][key] = {
                "filename": f.name,
                "saved_path": "",
                "byte_length": getattr(f, "size", 0),
                "client_name": Path(f.name).stem.replace("_", " "),
                "email": "",
                "items_text": "",
                "raw_text": "",
                "debug_report": f"TOP-LEVEL ERROR: {type(exc).__name__}: {exc}",
                "engine": "none",
                "warnings": [str(exc)],
                "attempts": [],
            }

st.divider()
st.subheader("Review extracted data")

for f in uploaded:
    key = f"{f.name}:{f.size}"
    draft = st.session_state["parse_drafts"].get(key)
    if not draft:
        continue

    failed = not (draft.get("raw_text") or "").strip()

    with st.expander(f"📄 {draft['filename']}", expanded=True):
        for w in draft.get("warnings") or []:
            st.warning(w)

        st.caption(
            f"engine: `{draft.get('engine')}` · "
            f"bytes: {draft.get('byte_length', '?')} · "
            f"pdf_header: {draft.get('starts_with_pdf_header')} · "
            f"magic: `{draft.get('magic_ascii')}`"
        )

        # ---- Per-library attempt results (always shown) ----
        st.markdown("#### Extraction attempts (copy this if it still fails)")
        attempts = draft.get("attempts") or []
        if not attempts:
            st.error("No library attempts were recorded.")
        for a in attempts:
            status = "✅ OK" if a.get("ok") else ("❌ ERR" if a.get("error") else "⚪ EMPTY")
            with st.container(border=True):
                st.markdown(f"**{a.get('name')}** — {status} — {a.get('chars', 0)} chars")
                if a.get("note"):
                    st.caption(a["note"])
                if a.get("error"):
                    st.error(a["error"])
                if a.get("traceback"):
                    st.code(a["traceback"], language="text")
                st.text_area(
                    f"Raw text from {a.get('name')}",
                    value=a.get("text") or "(empty)",
                    height=140,
                    key=f"attempt_text_{key}_{a.get('name')}",
                )

        with st.expander("Full debug dump", expanded=failed):
            st.text_area(
                "debug",
                value=draft.get("debug_report") or draft.get("raw_text") or "(empty)",
                height=280,
                key=f"debug_{key}",
            )

        st.markdown("#### Parsed fields (editable)")
        col1, col2 = st.columns(2)
        with col1:
            draft["client_name"] = st.text_input(
                "Client name",
                value=draft.get("client_name") or "",
                key=f"name_{key}",
            )
        with col2:
            draft["email"] = st.text_input(
                "Email (optional)",
                value=draft.get("email") or "",
                key=f"email_{key}",
            )

        draft["items_text"] = st.text_area(
            "Checklist items (from Tax Documents: / Additional paperwork:)",
            value=draft.get("items_text") or "",
            height=220,
            key=f"items_{key}",
        )

        b1, b2, b3 = st.columns([1, 1, 2])
        with b1:
            if st.button("Re-parse upload", key=f"reparse_{key}"):
                # Re-run against the same UploadedFile in this request if possible,
                # else from cached disk bytes.
                try:
                    # Force a fresh extract from the live uploader object
                    for uf in uploaded:
                        if f"{uf.name}:{uf.size}" == key:
                            st.session_state["parse_drafts"][key] = _run_extract(uf)
                            break
                    else:
                        path = Path(draft.get("saved_path") or "")
                        if path.exists():
                            st.session_state["parse_drafts"][key] = _run_extract(
                                path.read_bytes()
                            )
                            st.session_state["parse_drafts"][key]["filename"] = draft[
                                "filename"
                            ]
                            st.session_state["parse_drafts"][key]["saved_path"] = str(
                                path
                            )
                        else:
                            st.error("Could not re-parse — re-upload the PDF.")
                    st.rerun()
                except Exception as exc:
                    st.exception(exc)
        with b2:
            save = st.button("Save client", type="primary", key=f"save_{key}")
        with b3:
            st.caption("Saves client + checklist items to SQLite.")

        if save:
            name = (draft.get("client_name") or "").strip()
            items = items_from_multiline(draft.get("items_text") or "")
            if not name:
                st.error("Client name is required.")
            else:
                client_id = db.create_client(
                    name,
                    email=draft.get("email") or None,
                    source_filename=draft["filename"],
                    raw_pdf_text=draft.get("debug_report")
                    or draft.get("raw_text")
                    or "",
                    items=items,
                )
                st.success(
                    f"Saved **{name}** with {len(items)} item(s) (client #{client_id})."
                )
                select_client(client_id)
                if st.button("Open client detail", key=f"open_{key}"):
                    st.switch_page("pages/2_Client_Detail.py")
