"""Per-client checklist view with manual overrides and matched files."""

from __future__ import annotations

import streamlit as st

from taxdash import db
from taxdash.pdf_parser import items_from_multiline, parse_checklist_text
from taxdash.ui import bootstrap, get_selected_client_id, inject_styles, select_client

st.set_page_config(page_title="Client Detail", page_icon="👤", layout="wide")
bootstrap()
inject_styles()

st.title("Client detail")

clients = db.list_clients()
if not clients:
    st.info("No clients yet. Paste a checklist from **Paste checklist text**.")
    st.stop()

selected_id = get_selected_client_id()
id_to_label = {c["id"]: f"{c['name']}  (#{c['id']})" for c in clients}
labels = list(id_to_label.values())
default_idx = 0
if selected_id and selected_id in id_to_label:
    default_idx = labels.index(id_to_label[selected_id])

choice = st.selectbox("Client", options=labels, index=default_idx)
client_id = next(cid for cid, lab in id_to_label.items() if lab == choice)
select_client(client_id)

client = db.get_client(client_id)
if not client:
    st.error("Client not found.")
    st.stop()

items = db.list_checklist_items(client_id)
total = len(items)
received = sum(1 for i in items if i["received"])
missing = total - received
pct = round((received / total) * 100, 1) if total else 0.0

m1, m2, m3, m4 = st.columns(4)
m1.metric("Total items", total)
m2.metric("Received", received)
m3.metric("Missing", missing)
m4.metric("% Complete", f"{pct}%")

if missing:
    st.warning(f"{missing} document(s) still missing for **{client['name']}**.")
else:
    st.success(f"All checklist items received for **{client['name']}**.")

# --- Profile / notes ---
with st.expander("Client profile & notes", expanded=False):
    name = st.text_input("Name", value=client["name"])
    email = st.text_input("Email", value=client.get("email") or "")
    proseries_id = st.text_input(
        "ProSeries ID / source ref",
        value=client.get("proseries_id") or "",
    )
    notes = st.text_area("Notes", value=client.get("notes") or "", height=100)
    if st.button("Save profile"):
        db.update_client(
            client_id,
            name=name,
            email=email,
            proseries_id=proseries_id,
            notes=notes,
        )
        st.success("Profile saved.")
        st.rerun()

st.subheader("Checklist")


def _on_toggle_received(item_id: int, key: str) -> None:
    db.set_item_received(item_id, bool(st.session_state.get(key)))


if not items:
    st.info("No checklist items. Add some below or re-extract from stored pasted text.")
else:
    for item in items:
        item_id = int(item["id"])
        chk_key = f"chk_{item_id}"
        # Initialize once from DB; on_change keeps SQLite in sync afterward.
        if chk_key not in st.session_state:
            st.session_state[chk_key] = bool(item["received"])

        cols = st.columns([0.1, 0.55, 0.25, 0.1])
        with cols[0]:
            st.checkbox(
                "Got it",
                key=chk_key,
                on_change=_on_toggle_received,
                args=(item_id, chk_key),
                help="Mark received / not received",
            )
        with cols[1]:
            label = item["description"]
            if st.session_state[chk_key]:
                st.markdown(f"✅ ~~{label}~~")
            else:
                st.markdown(f"⬜ **{label}**")
            meta = []
            if item.get("received_date"):
                meta.append(f"received {item['received_date']}")
            if item.get("matched_filename"):
                meta.append(f"file: `{item['matched_filename']}`")
            if item.get("notes"):
                meta.append(item["notes"])
            if meta:
                st.caption(" · ".join(meta))
        with cols[2]:
            note_key = f"note_{item_id}"
            if note_key not in st.session_state:
                st.session_state[note_key] = item.get("notes") or ""
            st.text_input(
                "Note",
                key=note_key,
                label_visibility="collapsed",
                placeholder="Note…",
            )
            if st.button("Save note", key=f"save_note_{item_id}"):
                db.set_item_received(
                    item_id,
                    bool(st.session_state[chk_key]),
                    notes=st.session_state[note_key],
                )
                st.toast("Note saved")
                st.rerun()
        with cols[3]:
            if st.button("Del", key=f"del_{item_id}", help="Delete item"):
                # Clear widget keys so they don't resurrect stale state
                st.session_state.pop(chk_key, None)
                st.session_state.pop(f"note_{item_id}", None)
                db.delete_checklist_item(item_id)
                st.rerun()

st.markdown("##### Add item")
new_item = st.text_input("New checklist item", placeholder="e.g. 2024 W-2 from Acme Corp")
if st.button("Add item") and new_item.strip():
    db.add_checklist_item(
        client_id,
        new_item.strip(),
        source_pdf=client.get("source_filename"),
    )
    st.rerun()

# --- Matched files ---
st.subheader("Matched / linked files")
files = db.list_uploaded_files(client_id=client_id)
if not files:
    st.caption("No File Request Pro files linked to this client yet.")
else:
    st.dataframe(
        [
            {
                "Filename": f["filename"],
                "Submitter": f.get("submitter_name") or "",
                "Email": f.get("submitter_email") or "",
                "Received at": f.get("received_at") or "",
                "Matched item": f.get("item_description") or "",
                "FRP file id": f.get("frp_file_id") or "",
            }
            for f in files
        ],
        use_container_width=True,
        hide_index=True,
    )

# --- Edit items / re-extract from stored pasted text ---
st.subheader("Edit checklist")
items_text = "\n".join(i["description"] for i in items)
edited = st.text_area(
    "Checklist items (one per line). Saving replaces the list; received status is kept for matching descriptions.",
    value=items_text,
    height=200,
)
c1, c2 = st.columns(2)
with c1:
    if st.button("Save edited checklist", type="primary"):
        db.replace_checklist_items(
            client_id,
            items_from_multiline(edited),
            source_pdf=client.get("source_filename"),
        )
        st.success("Checklist updated.")
        st.rerun()
with c2:
    raw_stored = (client.get("raw_pdf_text") or "").strip()
    if raw_stored and st.button("Re-extract from stored pasted text"):
        result = parse_checklist_text(raw_stored)
        if result.client_name:
            db.update_client(client_id, name=result.client_name)
        db.replace_checklist_items(
            client_id,
            result.items,
            source_pdf=client.get("source_filename") or "pasted-checklist-text",
        )
        st.success(f"Re-extracted {len(result.items)} item(s). Review and edit if needed.")
        st.rerun()
    elif not raw_stored:
        st.caption("No stored pasted text on this client.")

with st.expander("Stored pasted checklist text"):
    st.text_area(
        "Raw text",
        value=client.get("raw_pdf_text") or "(none)",
        height=240,
        disabled=True,
    )

st.divider()
if st.button("Delete this client", type="secondary"):
    db.delete_client(client_id)
    st.session_state.pop("selected_client_id", None)
    st.success("Client deleted.")
    st.switch_page("app.py")
