"""Review FRP files that could not be auto-matched; assign manually."""

from __future__ import annotations

import streamlit as st

from taxdash import db
from taxdash.frp_client import FRPError
from taxdash.sync import auto_match_unmatched, sync_from_frp
from taxdash.ui import bootstrap, inject_styles

st.set_page_config(page_title="Unmatched Files", page_icon="🔗", layout="wide")
bootstrap()
inject_styles()

st.title("Unmatched File Request Pro files")
st.caption(
    "Files synced from FRP that still need a client (and optionally a checklist item). "
    "Auto-match uses name/email + filename keyword overlap."
)

c1, c2 = st.columns(2)
with c1:
    if st.button("Sync from File Request Pro", type="primary"):
        with st.spinner("Syncing…"):
            try:
                summary = sync_from_frp()
                st.success(
                    f"Created {summary['files_created']}, updated {summary['files_updated']}. "
                    f"Auto-matched {summary['auto_match']['assigned']}."
                )
            except FRPError as exc:
                st.error(str(exc))
with c2:
    if st.button("Re-run auto-match on unmatched"):
        result = auto_match_unmatched()
        st.success(
            f"Assigned {result['assigned']} "
            f"({result['item_matched']} to checklist items); "
            f"skipped {result['skipped']}."
        )
        st.rerun()

show_all = st.checkbox("Show all uploaded files (not just unmatched)", value=False)
files = db.list_uploaded_files(unmatched_only=not show_all)

if not files:
    st.info("No unmatched files. Sync from FRP or upload activity will appear here.")
    st.stop()

clients = db.list_clients()
client_options = {"— unassigned —": None}
client_options.update({f"{c['name']} (#{c['id']})": c["id"] for c in clients})

for f in files:
    with st.expander(
        f"{'✅' if f.get('client_id') else '❓'} {f['filename']}"
        + (f" — {f.get('submitter_name') or 'unknown submitter'}" ),
        expanded=not bool(f.get("client_id")),
    ):
        st.write(
            {
                "Submitter": f.get("submitter_name") or "",
                "Email": f.get("submitter_email") or "",
                "Received at": f.get("received_at") or "",
                "FRP file id": f.get("frp_file_id") or "",
                "Current client": f.get("client_name") or "—",
                "Current item": f.get("item_description") or "—",
            }
        )

        # Default select index
        current_label = "— unassigned —"
        for label, cid in client_options.items():
            if cid is not None and cid == f.get("client_id"):
                current_label = label
                break

        chosen_label = st.selectbox(
            "Assign to client",
            options=list(client_options.keys()),
            index=list(client_options.keys()).index(current_label),
            key=f"client_{f['id']}",
        )
        chosen_client = client_options[chosen_label]

        item_id = None
        if chosen_client:
            items = db.list_checklist_items(int(chosen_client))
            item_map = {"— none / client only —": None}
            item_map.update(
                {
                    f"{'✓ ' if i['received'] else ''}{i['description']}": i["id"]
                    for i in items
                }
            )
            default_item = "— none / client only —"
            for label, iid in item_map.items():
                if iid is not None and iid == f.get("checklist_item_id"):
                    default_item = label
                    break
            item_label = st.selectbox(
                "Match checklist item",
                options=list(item_map.keys()),
                index=list(item_map.keys()).index(default_item),
                key=f"item_{f['id']}",
            )
            item_id = item_map[item_label]

        if st.button("Save assignment", key=f"save_{f['id']}"):
            db.assign_file_to_client(
                int(f["id"]),
                int(chosen_client) if chosen_client else None,
                checklist_item_id=int(item_id) if item_id else None,
                mark_item_received=bool(item_id),
            )
            st.success("Assignment saved.")
            st.rerun()
