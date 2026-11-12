"""Export / view still-missing documents across all clients."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

import streamlit as st

from taxdash import db
from taxdash.ui import bootstrap, inject_styles

st.set_page_config(page_title="Missing Report", page_icon="📝", layout="wide")
bootstrap()
inject_styles()

st.title("Still-missing documents")
st.caption("All open checklist items across clients — copy on-screen or download CSV.")

rows = db.missing_items_report()

if not rows:
    st.success("Nothing missing — every checklist item is marked received.")
    st.stop()

# Group on-screen by client
by_client: dict[str, list[dict]] = {}
for r in rows:
    by_client.setdefault(r["client_name"], []).append(r)

st.metric("Open items", len(rows))
st.metric("Clients with gaps", len(by_client))

for client_name, items in by_client.items():
    with st.expander(f"{client_name} — {len(items)} missing", expanded=True):
        for item in items:
            line = f"- {item['description']}"
            if item.get("notes"):
                line += f" _(note: {item['notes']})_"
            st.markdown(line)

# CSV export
buf = io.StringIO()
writer = csv.DictWriter(
    buf,
    fieldnames=["client_name", "email", "description", "notes", "source_pdf"],
)
writer.writeheader()
for r in rows:
    writer.writerow(
        {
            "client_name": r.get("client_name") or "",
            "email": r.get("email") or "",
            "description": r.get("description") or "",
            "notes": r.get("notes") or "",
            "source_pdf": r.get("source_pdf") or "",
        }
    )

stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
st.download_button(
    "Download CSV",
    data=buf.getvalue(),
    file_name=f"missing_documents_{stamp}.csv",
    mime="text/csv",
    type="primary",
)

st.dataframe(rows, use_container_width=True, hide_index=True)
