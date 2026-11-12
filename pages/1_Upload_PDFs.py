"""Upload / drop ProSeries Client Checklist PDFs and review extraction."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from taxdash import db
from taxdash.pdf_debug import probe_libraries
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

# Always-visible environment probe (helps Streamlit Cloud debugging)
with st.expander("Environment / library debug (Streamlit Cloud)", expanded=False):
    libs = probe_libraries()
    st.write(
        {
            "python": libs.get("python"),
            "platform": libs.get("platform"),
            "packages": libs.get("packages"),
            "imports": libs.get("imports"),
            "binaries": libs.get("binaries"),
        }
    )
    st.caption(
        "On Streamlit Community Cloud, `packages.txt` should install "
        "`tesseract-ocr`, `tesseract-ocr-eng`, and `poppler-utils`. "
        "Digital text extraction uses pypdf / PyMuPDF / pdfplumber and does not need those."
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


def _draft_from_result(filename: str, data: bytes, result, saved_path: str) -> dict:
    return {
        "filename": filename,
        "saved_path": saved_path,
        "byte_length": len(data),
        "client_name": result.client_name or Path(filename).stem.replace("_", " "),
        "email": "",
        "items_text": "\n".join(result.items),
        "raw_text": result.raw_text or result.diagnostics or "(no text)",
        "engine": result.engine,
        "warnings": result.warnings,
        "image_only": result.image_only,
        "encrypted": result.needs_password or result.encrypted,
        "page_count": result.page_count,
        "file_info": result.file_info,
        "lib_info": result.lib_info,
        "attempts": [
            {
                "name": a.name,
                "ok": a.ok,
                "chars": a.chars,
                "error": a.error,
                "note": a.note,
                "traceback": getattr(a, "traceback", "") or "",
                "preview": getattr(a, "preview", "") or "",
            }
            for a in (result.attempts or [])
        ],
    }


# Parse newly uploaded files from in-memory bytes (no temp-file dependency)
for f in uploaded:
    key = f"{f.name}:{f.size}"
    if key not in st.session_state["parse_drafts"]:
        data = f.getvalue()
        st.caption(f"Read `{f.name}` from uploader: **{len(data):,}** bytes")
        safe_name = Path(f.name).name
        dest = UPLOAD_DIR / safe_name
        try:
            dest.write_bytes(data)
        except OSError as exc:
            st.warning(f"Could not save a local copy of {safe_name}: {exc}")
            dest = Path("")  # extraction still uses in-memory bytes

        try:
            result = parse_pdf(data, filename=f.name)
        except Exception as exc:
            st.exception(exc)
            st.session_state["parse_drafts"][key] = {
                "filename": f.name,
                "saved_path": str(dest),
                "byte_length": len(data),
                "client_name": Path(f.name).stem.replace("_", " "),
                "email": "",
                "items_text": "",
                "raw_text": f"TOP-LEVEL PARSE ERROR: {type(exc).__name__}: {exc}",
                "engine": "none",
                "warnings": [f"parse_pdf crashed: {exc}"],
                "image_only": False,
                "encrypted": False,
                "page_count": 0,
                "file_info": {"byte_length": len(data)},
                "lib_info": probe_libraries(),
                "attempts": [],
            }
            continue

        st.session_state["parse_drafts"][key] = _draft_from_result(
            f.name, data, result, str(dest)
        )

st.divider()
st.subheader("Review extracted data")

drafts = st.session_state["parse_drafts"]
keys = [f"{f.name}:{f.size}" for f in uploaded]

for key in keys:
    draft = drafts.get(key)
    if not draft:
        continue

    failed = (draft.get("engine") in (None, "", "none")) and not (
        draft.get("items_text") or ""
    ).strip()

    with st.expander(f"📄 {draft['filename']}", expanded=True):
        if draft.get("warnings"):
            for w in draft["warnings"]:
                st.warning(w)
        meta_bits = [
            f"engine: `{draft.get('engine') or 'n/a'}`",
            f"pages: {draft.get('page_count') or '?'}",
            f"upload bytes: {draft.get('byte_length', '?')}",
        ]
        if draft.get("image_only"):
            meta_bits.append("image-only/scanned")
        if draft.get("encrypted"):
            meta_bits.append("encrypted")
        st.caption(" · ".join(meta_bits))

        # Prominent debug when extraction failed (or always available)
        with st.expander(
            "Cloud extraction debug (copy/paste this back)",
            expanded=failed,
        ):
            fi = draft.get("file_info") or {}
            st.markdown("**Uploaded file probe**")
            st.code(
                "\n".join(
                    [
                        f"filename: {fi.get('filename', draft.get('filename'))}",
                        f"byte_length: {fi.get('byte_length', draft.get('byte_length'))}",
                        f"starts_with_pdf_header: {fi.get('starts_with_pdf_header')}",
                        f"contains_eof: {fi.get('contains_eof')}",
                        f"magic_ascii: {fi.get('magic_ascii')}",
                        f"magic_hex: {fi.get('magic_hex')}",
                        f"quick_open: {fi.get('quick_open')}",
                    ]
                ),
                language="text",
            )
            st.markdown("**Per-engine attempts**")
            if draft.get("attempts"):
                for a in draft["attempts"]:
                    status = "OK" if a.get("ok") else ("ERR" if a.get("error") else "sparse")
                    st.markdown(
                        f"- **{a.get('name')}** — `{status}`, chars={a.get('chars', 0)}"
                    )
                    if a.get("note"):
                        st.caption(f"note: {a['note']}")
                    if a.get("error"):
                        st.error(a["error"])
                    if a.get("preview"):
                        st.caption(f"preview: {a['preview']}")
                    if a.get("traceback"):
                        st.code(a["traceback"], language="text")
            else:
                st.write("No attempts recorded.")

            st.markdown("**Full debug dump**")
            st.text_area(
                "debug_dump",
                value=draft.get("raw_text") or "(empty)",
                height=320,
                key=f"debug_dump_{key}",
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

        with st.expander("Raw extracted text + diagnostics (always saved)", expanded=not failed):
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
                # Prefer in-memory re-read from saved bytes; fall back to path
                path = Path(draft["saved_path"]) if draft.get("saved_path") else None
                try:
                    if path and path.exists():
                        data = path.read_bytes()
                    else:
                        st.error("Saved PDF copy not found; re-upload the file.")
                        data = b""
                    if data:
                        result = parse_pdf(data, filename=draft["filename"])
                        st.session_state["parse_drafts"][key] = _draft_from_result(
                            draft["filename"], data, result, str(path or "")
                        )
                        st.rerun()
                except Exception as exc:
                    st.exception(exc)
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
