"""
Tax Checklist Dashboard — main page (client overview).

Run:
  streamlit run app.py
"""

from __future__ import annotations

import streamlit as st

from taxdash import db
from taxdash.frp_client import FRPError
from taxdash.sync import sync_from_frp
from taxdash.ui import bootstrap, inject_styles, select_client

st.set_page_config(
    page_title="Tax Checklist Dashboard",
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="expanded",
)

bootstrap()
inject_styles()

st.title("Tax Checklist Dashboard")
st.caption("Internal document status tracker — expected vs. received per client.")

# --- Sidebar actions ---
with st.sidebar:
    st.header("Quick actions")
    if st.button("Sync from File Request Pro", use_container_width=True, type="primary"):
        with st.spinner("Syncing from File Request Pro…"):
            try:
                summary = sync_from_frp()
                st.success(
                    f"Sync done — {summary['files_created']} new, "
                    f"{summary['files_updated']} updated. "
                    f"Auto-matched {summary['auto_match']['assigned']} file(s)."
                )
                if summary.get("errors"):
                    for err in summary["errors"]:
                        st.warning(err)
            except FRPError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"Unexpected sync error: {exc}")

    st.page_link("pages/1_Paste_Checklist.py", label="Paste checklist text", icon="📋")
    st.page_link("pages/3_Unmatched_Files.py", label="Unmatched FRP files", icon="🔗")
    st.page_link("pages/4_Missing_Report.py", label="Missing documents report", icon="📝")
    st.page_link("pages/5_Settings.py", label="Settings / API token", icon="⚙️")

# --- Summary metrics ---
summary = db.dashboard_summary()
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Clients", summary["clients"])
c2.metric("Checklist items", summary["items"])
c3.metric("Received", summary["received"])
c4.metric("Missing", summary["missing"])
c5.metric("Unmatched files", summary["unmatched_files"])

st.divider()

# --- Filters ---
fc1, fc2, fc3 = st.columns([3, 1, 1])
with fc1:
    search = st.text_input("Search clients", placeholder="Name or email…")
with fc2:
    missing_only = st.checkbox("Missing only", value=False)
with fc3:
    st.write("")  # spacer
    st.write("")
    refresh = st.button("Refresh")

if refresh:
    st.rerun()

clients = db.list_clients(search=search, missing_only=missing_only)

if not clients:
    st.info(
        "No clients yet. Paste a ProSeries Client Checklist from **Paste checklist text**, "
        "or adjust your search filter."
    )
    st.stop()

# Build display table
rows = []
for c in clients:
    missing = int(c["missing_count"])
    rows.append(
        {
            "ID": c["id"],
            "Client": c["name"],
            "Email": c.get("email") or "",
            "Total": int(c["total_items"]),
            "Received": int(c["received_count"]),
            "Missing": missing,
            "% Complete": c["pct_complete"],
            "Last activity": c.get("last_activity") or c.get("updated_at") or "",
            "Status": "Missing docs" if missing else "Complete",
        }
    )

st.subheader(f"Clients ({len(rows)})")
st.dataframe(
    rows,
    use_container_width=True,
    hide_index=True,
    column_config={
        "% Complete": st.column_config.ProgressColumn(
            "% Complete",
            min_value=0,
            max_value=100,
            format="%.0f%%",
        ),
        "Missing": st.column_config.NumberColumn("Missing"),
        "Status": st.column_config.TextColumn("Status"),
    },
)

st.markdown("#### Open a client")
st.caption("Click a client to open their checklist.")
# Stable per-client buttons (more reliable than selectbox + switch_page alone)
btn_cols = st.columns(3)
for idx, c in enumerate(clients):
    with btn_cols[idx % 3]:
        missing = int(c["missing_count"])
        label = f"{c['name']}"
        help_txt = f"{missing} missing · {c['pct_complete']}% complete"
        if st.button(label, key=f"open_client_{c['id']}", help=help_txt, use_container_width=True):
            select_client(int(c["id"]))
            st.switch_page("pages/2_Client_Detail.py")
