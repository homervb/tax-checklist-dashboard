"""Settings — File Request Pro API token and diagnostics."""

from __future__ import annotations

import os

import streamlit as st

from taxdash import db
from taxdash.frp_client import FRPError, FileRequestProClient
from taxdash.ui import bootstrap, inject_styles

st.set_page_config(page_title="Settings", page_icon="⚙️", layout="wide")
bootstrap()
inject_styles()

st.title("Settings")
st.caption("API credentials and environment. Tokens are never hardcoded in source.")

st.subheader("File Request Pro API")
st.markdown(
    """
How to add a real API token:

1. Sign in to [File Request Pro](https://filerequestpro.com).
2. Open **Your Settings → API**.
3. Create a token dedicated to this dashboard (so you can revoke it later).
4. Paste it below **or** set `FRP_API_TOKEN` in a `.env` file / environment variable.
5. Click **Test connection**, then use **Sync from File Request Pro** on the dashboard.

Docs: [Authentication](https://filerequestpro.com/docs/frp/api-authentication) ·
[Files](https://filerequestpro.com/docs/frp/api-file) ·
[Submissions](https://filerequestpro.com/docs/frp/api-submissions)
"""
)

stored = db.get_setting("frp_api_token") or ""
env_token = os.getenv("FRP_API_TOKEN", "").strip()
effective = db.get_frp_token()

if stored:
    st.success("A token is saved in the local database (Settings page).")
elif env_token:
    st.info("Using `FRP_API_TOKEN` from the environment (no DB token saved).")
else:
    st.warning("No API token configured yet.")

token_input = st.text_input(
    "API token",
    value=stored,
    type="password",
    help="Stored in local SQLite settings. Leave blank and save to clear.",
    placeholder="Paste Bearer token from FRP → Your Settings → API",
)

c1, c2, c3 = st.columns(3)
with c1:
    if st.button("Save token", type="primary"):
        db.set_setting("frp_api_token", token_input.strip())
        if token_input.strip():
            st.success("Token saved to local database.")
        else:
            st.success("Saved token cleared.")
        st.rerun()
with c2:
    if st.button("Test connection"):
        tok = token_input.strip() or effective
        if not tok:
            st.error("Enter or configure a token first.")
        else:
            try:
                client = FileRequestProClient(tok)
                result = client.test_connection()
                st.success(result["message"])
            except FRPError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"Unexpected error: {exc}")
with c3:
    if st.button("Clear saved token"):
        db.set_setting("frp_api_token", "")
        st.success("Cleared.")
        st.rerun()

st.divider()
st.subheader("Environment")
st.code(
    f"DATABASE_PATH = {db.get_db_path()}\n"
    f"FRP_API_BASE_URL = {os.getenv('FRP_API_BASE_URL', 'https://filerequestpro.com/api')}\n"
    f"FRP_API_TOKEN env set = {'yes' if env_token else 'no'}\n"
    f"Effective token present = {'yes' if effective else 'no'}",
    language="text",
)

st.subheader("Danger zone")
if st.button("Re-initialize database schema (safe — CREATE IF NOT EXISTS)"):
    db.init_db()
    st.success("Schema verified / created.")
