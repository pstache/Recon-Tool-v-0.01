"""
Streamlit entry point: SpareBank 1 fund-transaction reconciliation tool.

Run with:
    streamlit run app.py

The app is a single-user local UI (per clarification #10). It does not
authenticate anyone, so the audit log records the OS user as a best-effort
identity for manual-match attribution.

Pages (left sidebar):
    1. Upload & Run    — drag-and-drop today's files, run matching
    2. Review          — filter unmatched + partial transactions, mark matches
    3. Search          — full-text search across all transactions
    4. Reports         — monthly Excel export
    5. Audit           — manual-match log + parse-error log
    6. Settings        — UI-editable institution overrides

Each page is implemented as a function. We use a sidebar radio rather than
Streamlit's multi-page-app feature to keep the project tree flat (easier to
review for a single-user tool).
"""

from __future__ import annotations

import getpass
import streamlit as st

from ui.branding import apply_branding
from ui.common import get_repo

# Page modules
from ui.page_upload   import render as page_upload
from ui.page_review   import render as page_review
from ui.page_search   import render as page_search
from ui.page_reports  import render as page_reports
from ui.page_audit    import render as page_audit
from ui.page_settings import render as page_settings


# Each page name is paired with its render function and a Material-Symbols-
# style emoji that sits in the SB1 visual world (we can't load the actual
# Material Symbols Rounded font in Streamlit, so emoji is the pragmatic
# substitute — kept minimal and used only as nav prefixes per the design
# system's "icons up to 48px, otherwise illustrate" guidance).
PAGES = {
    "Last opp":     ("Upload & Run",  page_upload),
    "Gjennomgang":  ("Review",        page_review),
    "Søk":          ("Search",        page_search),
    "Rapporter":    ("Reports",       page_reports),
    "Revisjon":     ("Audit",         page_audit),
    "Innstillinger":("Settings",      page_settings),
}


def main():
    st.set_page_config(
        page_title="SB1 Avstemming",
        page_icon="🔵",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    apply_branding()

    # Sidebar — brand mark, nav, live counts
    st.sidebar.markdown("# SB1 Avstemming")
    st.sidebar.caption("Fondstransaksjoner · Forvaltning")
    st.sidebar.caption(f"Innlogget som **{getpass.getuser()}**")

    st.sidebar.markdown("---")
    page_label = st.sidebar.radio(
        "Navigasjon",
        list(PAGES.keys()),
        label_visibility="collapsed",
    )

    repo = get_repo()
    counts = repo.counts()

    st.sidebar.markdown("---")
    st.sidebar.markdown("**Status nå**")
    st.sidebar.metric("Aktive transaksjoner", f"{counts['active']:,}")
    st.sidebar.metric("Uavstemte", f"{counts['unmatched']:,}")
    st.sidebar.metric("Auto-avstemt", f"{counts['auto_matched']:,}")
    st.sidebar.metric("Delvise", f"{counts['partial']:,}")
    st.sidebar.metric("Manuelt avstemt", f"{counts['manual_matched']:,}")

    _english_name, render_fn = PAGES[page_label]
    render_fn(repo)


if __name__ == "__main__":
    main()
