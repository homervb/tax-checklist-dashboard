"""Shared Streamlit UI helpers."""

from __future__ import annotations

import streamlit as st

from taxdash import db
from taxdash.logging_config import setup_logging

logger = setup_logging()


def bootstrap() -> None:
    """Initialize DB and page chrome once per session."""
    if "db_ready" not in st.session_state:
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            pass
        db.init_db()
        st.session_state["db_ready"] = True
        logger.info("UI bootstrap complete")


def inject_styles() -> None:
    st.markdown(
        """
        <style>
        .block-container { padding-top: 1.4rem; max-width: 1200px; }
        div[data-testid="stMetricValue"] { font-size: 1.6rem; }
        .missing-row { background: #fff4e5; }
        .ok-pill {
            display: inline-block; padding: 0.15rem 0.55rem; border-radius: 999px;
            background: #e6f4ea; color: #137333; font-size: 0.85rem; font-weight: 600;
        }
        .warn-pill {
            display: inline-block; padding: 0.15rem 0.55rem; border-radius: 999px;
            background: #fce8e6; color: #c5221f; font-size: 0.85rem; font-weight: 600;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def select_client(client_id: int) -> None:
    st.session_state["selected_client_id"] = int(client_id)
    st.query_params["client_id"] = str(client_id)


def get_selected_client_id() -> int | None:
    if "selected_client_id" in st.session_state and st.session_state["selected_client_id"]:
        return int(st.session_state["selected_client_id"])
    qp = st.query_params.get("client_id")
    if qp:
        try:
            return int(qp)
        except ValueError:
            return None
    return None
