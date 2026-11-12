"""Upload / drop ProSeries Client Checklist PDFs and review extraction."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from taxdash import db
from taxdash.pdf_parser import items_from_multiline, parse_pdf
from taxdash.ui import bootstrap, inject_styles, select_client

st.set_page_config(page_title="Upload PDFs", page_icon="📤", layout="wide")
bootstrap()
inject_styles()

st.title("Upload ProSeries PDFs")
st.caption(
    "Drop one or more Client Checklist PDFs. We extract the client name and "
    "requested documents, save the raw text, and let you correct anything before saving."
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
        "Tip: place sample PDFs in `sample_pdfs/` for reference. "
        "You can also generate a demo PDF with `python scripts/make_sample_pdf.py`."
    )
    st.stop()

if "parse_drafts" not in st.session_state:
    st.session_state["parse_drafts"] = {}

# Parse newly uploaded files (keyed by name+size)
for f in uploaded:
    key = f"{f.name}:{f.size}"
    if key not in st.session_state["parse_drafts"]:
        data = f.getvalue()
        result = parse_pdf(data, filename=f.name)
        # Persist raw PDF bytes for re-parse later
        safe_name = Path(f.name).name
        dest = UPLOAD_DIR / safe_name
        try:
            dest.write_bytes(data)
        except OSError as exc:
            st.warning(f"Could not save a local copy of {safe_name}: {exc}")
        st.session_state["parse_drafts"][key] = {
            "filename": f.name,
            "saved_path": str(dest),
            "client_name": result.client_name or Path(f.name).stem.replace("_", " "),
            "email": "",
            "items_text": "\n".join(result.items),
            "raw_text": result.raw_text or result.diagnostics or "(no text)",
            "engine": result.engine,
            "warnings": result.warnings,
            "image_only": result.image_only,
            "encrypted": result.needs_password or result.encrypted,
            "page_count": result.page_count,
            "attempts": [
                {
                    "name": a.name,
                    "ok": a.ok,
                    "chars": a.chars,
                    "error": a.error,
                    "note": a.note,
                }
                for a in (result.attempts or [])
            ],
        }

st.divider()
st.subheader("Review extracted data")

drafts = st.session_state["parse_drafts"]
keys = [f"{f.name}:{f.size}" for f in uploaded]

for key in keys:
    draft = drafts.get(key)
    if not draft:
        continue

    with st.expander(f"📄 {draft['filename']}", expanded=True):
        if draft.get("warnings"):
            for w in draft["warnings"]:
                st.warning(w)
        meta_bits = [
            f"engine: `{draft.get('engine') or 'n/a'}`",
            f"pages: {draft.get('page_count') or '?'}",
        ]
        if draft.get("image_only"):
            meta_bits.append("image-only/scanned")
        if draft.get("encrypted"):
            meta_bits.append("encrypted")
        st.caption(" · ".join(meta_bits))
        if draft.get("attempts"):
            with st.expander("Extraction engine attempts", expanded=False):
                for a in draft["attempts"]:
                    status = "OK" if a.get("ok") else ("error" if a.get("error") else "sparse")
                    detail = a.get("error") or a.get("note") or ""
                    st.write(
                        f"- **{a.get('name')}** — {status}, "
                        f"{a.get('chars', 0)} chars"
                        + (f" ({detail})" if detail else "")
                    )

        col1, col2 = st.columns(2)
        with col1:
            draft["client_name"] = st.text_input(
                "Client name",
                value=draft["client_name"],
                key=f"name_{key}",
            )
        with col2:
            draft["email"] = st.text_input(
                "Email (optional)",
                value=draft.get("email") or "",
                key=f"email_{key}",
            )

        draft["items_text"] = st.text_area(
            "Checklist items (one per line — edit freely)",
            value=draft["items_text"],
            height=220,
            key=f"items_{key}",
        )

        with st.expander("Raw extracted text + diagnostics (always saved)", expanded=True):
            st.text_area(
                "Raw text",
                value=draft["raw_text"] or "(empty)",
                height=280,
                key=f"raw_{key}",
                disabled=True,
            )

        b1, b2, b3 = st.columns([1, 1, 2])
        with b1:
            if st.button("Re-parse PDF", key=f"reparse_{key}"):
                path = Path(draft["saved_path"])
                if path.exists():
                    result = parse_pdf(path, filename=draft["filename"])
                    draft["client_name"] = result.client_name or draft["client_name"]
                    draft["items_text"] = "\n".join(result.items)
                    draft["raw_text"] = result.raw_text
                    draft["engine"] = result.engine
                    draft["warnings"] = result.warnings
                    st.rerun()
                else:
                    st.error("Saved PDF copy not found.")
        with b2:
            save = st.button("Save client", type="primary", key=f"save_{key}")
        with b3:
            st.caption("Saving creates a client record + checklist items in SQLite.")

        if save:
            name = (draft["client_name"] or "").strip()
            items = items_from_multiline(draft["items_text"])
            if not name:
                st.error("Client name is required.")
            else:
                client_id = db.create_client(
                    name,
                    email=draft.get("email") or None,
                    source_filename=draft["filename"],
                    raw_pdf_text=draft.get("raw_text") or "",
                    items=items,
                )
                st.success(f"Saved **{name}** with {len(items)} item(s) (client #{client_id}).")
                select_client(client_id)
                if st.button("Open client detail", key=f"open_{key}"):
                    st.switch_page("pages/2_Client_Detail.py")
