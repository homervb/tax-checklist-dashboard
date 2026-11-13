"""Paste ProSeries Client Checklist text and extract client + items."""

from __future__ import annotations

import streamlit as st

from taxdash import db
from taxdash.pdf_parser import items_from_multiline, parse_checklist_text
from taxdash.ui import bootstrap, inject_styles, select_client

st.set_page_config(page_title="Paste Checklist", page_icon="📋", layout="wide")
bootstrap()
inject_styles()

st.title("Paste ProSeries checklist")
st.caption(
    "Open the Client Checklist PDF on your computer → Select All → Copy → "
    "paste the text below. The app extracts the client name and requested documents."
)

st.markdown(
    """
**How to use**
1. Open the ProSeries Client Checklist PDF locally (Preview, Acrobat, browser, etc.).
2. Select all text and copy it.
3. Paste into the box below and click **Extract**.
4. Review/edit the client name and checklist items, then **Save client**.
"""
)

SAMPLE_HINT = """Example of what to paste (your real checklist will be longer):

Michael Orlando
123 Palm Ave
Orlando, FL 32801

Tax Documents:
W-2 wage statements from all employers
1099-INT interest income statements
1099-DIV dividend statements

Additional paperwork:
Voided check for direct deposit
Prior-year federal and state tax returns

Thank you for your prompt attention.
"""

pasted = st.text_area(
    "Paste ProSeries Client Checklist text here",
    height=320,
    placeholder=SAMPLE_HINT,
    key="paste_checklist_raw",
)

col_a, col_b = st.columns([1, 3])
with col_a:
    extract = st.button("Extract", type="primary", use_container_width=True)
with col_b:
    st.caption(
        "Looks for the client name near the top, then items under "
        "**Tax Documents:** and **Additional paperwork:**."
    )

if extract:
    text = (pasted or "").strip()
    if not text:
        st.error("Paste checklist text first.")
        st.session_state.pop("paste_extract", None)
    else:
        result = parse_checklist_text(text)
        st.session_state["paste_extract"] = {
            "raw_text": text,
            "client_name": result.client_name or "",
            "email": "",
            "items_text": "\n".join(result.items),
            "warnings": list(result.warnings),
        }

draft = st.session_state.get("paste_extract")

if not draft:
    st.info("Paste checklist text and click **Extract** to continue.")
    st.stop()

st.divider()
st.subheader("Review & save")

for w in draft.get("warnings") or []:
    st.warning(w)

st.text_area(
    "Raw pasted text (for comparison)",
    value=draft.get("raw_text") or "",
    height=200,
    disabled=True,
    key="paste_raw_display",
)

c1, c2 = st.columns(2)
with c1:
    draft["client_name"] = st.text_input(
        "Client name",
        value=draft.get("client_name") or "",
        key="paste_client_name",
    )
with c2:
    draft["email"] = st.text_input(
        "Email (optional)",
        value=draft.get("email") or "",
        key="paste_client_email",
    )

draft["items_text"] = st.text_area(
    "Checklist items (one per line — edit freely)",
    value=draft.get("items_text") or "",
    height=260,
    key="paste_items_text",
    help="From Tax Documents: and Additional paperwork: — remove anything that isn’t a real request.",
)

item_count = len(items_from_multiline(draft.get("items_text") or ""))
st.caption(f"{item_count} item(s) ready to save.")

s1, s2, s3 = st.columns([1, 1, 2])
with s1:
    save = st.button("Save client", type="primary", use_container_width=True)
with s2:
    if st.button("Clear", use_container_width=True):
        st.session_state.pop("paste_extract", None)
        st.session_state.pop("paste_checklist_raw", None)
        st.rerun()
with s3:
    st.caption("Saves to SQLite and appears on the dashboard.")

if save:
    name = (draft.get("client_name") or "").strip()
    items = items_from_multiline(draft.get("items_text") or "")
    if not name:
        st.error("Client name is required — type it in if extraction missed it.")
    elif not items:
        st.error("Add at least one checklist item before saving.")
    else:
        client_id = db.create_client(
            name,
            email=draft.get("email") or None,
            source_filename="pasted-checklist-text",
            raw_pdf_text=draft.get("raw_text") or "",
            items=items,
        )
        st.success(f"Saved **{name}** with {len(items)} item(s) (client #{client_id}).")
        select_client(client_id)
        st.session_state.pop("paste_extract", None)
        if st.button("Open client detail"):
            st.switch_page("pages/2_Client_Detail.py")
