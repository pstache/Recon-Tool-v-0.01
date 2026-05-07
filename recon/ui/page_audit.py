"""
Page: Audit.

Read-only views of the two audit logs:
  * manual_match_log  — every manual action (match, flag-unmatchable, unmatch)
                        with user, reason, timestamp.
  * parse_errors      — every line we couldn't parse, with raw payload and
                        the exception that triggered it.

Plus a single mutating action: "Undo manual match", which deletes a match
row and pushes both sides back into the unmatched pool. This is itself
audit-logged (action = 'unmatch').
"""

from __future__ import annotations

import getpass

import streamlit as st

from storage.repository import Repository
from ui.branding import sb1_header


def render(repo: Repository) -> None:
    sb1_header(
        "Revisjon",
        "Manuelle handlinger, angring og logg over parsefeil.",
    )

    tab_manual, tab_errors, tab_undo = st.tabs(
        ["Manuell logg", "Parsefeil", "Angre en avstemming"]
    )

    with tab_manual:
        df = repo.fetch_audit_log(limit=1000)
        st.markdown(f"Viser de siste **{df.height:,}** oppføringene.")
        if df.is_empty():
            st.info("Ingen manuelle handlinger registrert ennå.")
        else:
            st.dataframe(df, use_container_width=True, hide_index=True)

    with tab_errors:
        df = repo.fetch_parse_errors()
        st.markdown(f"**{df.height:,}** parsefeil registrert.")
        if df.is_empty():
            st.success("Ingen parsefeil. 🎉")
        else:
            st.dataframe(df, use_container_width=True, hide_index=True)
            st.caption(
                "Parsefeil stopper ikke import — resten av filen importeres "
                "som vanlig. Bruk `raw_line` for å undersøke."
            )

    with tab_undo:
        st.markdown(
            "Når du angrer en avstemming, fjernes oppføringen fra `matches` "
            "og begge sider havner tilbake i uavstemt-puljen. Selve angringen "
            "logges også."
        )
        # Show currently active matches with a "delete" affordance.
        rs = repo.db.execute("""
            SELECT m.match_id, m.status, m.matched_at, m.matched_by, m.reason,
                   i.transaction_id AS internal_id, i.source AS i_src, i.isin,
                   i.quantity AS i_qty,
                   e.transaction_id AS external_id, e.source AS e_src
            FROM matches m
            JOIN transactions_all i ON m.internal_id = i.transaction_id
            LEFT JOIN transactions_all e ON m.external_id = e.transaction_id
            WHERE m.matched_by IS NOT NULL    -- manual matches only
            ORDER BY m.matched_at DESC
            LIMIT 200
        """).pl()
        if rs.is_empty():
            st.info("Ingen manuelle avstemminger å angre.")
            return

        st.dataframe(rs, use_container_width=True, hide_index=True)

        match_ids = rs["match_id"].to_list()
        sel = st.selectbox(
            "Match-ID å angre", options=["(ingen)"] + match_ids, key="audit_undo_sel"
        )
        reason = st.text_input("Begrunnelse for angring (påkrevet)", key="audit_undo_reason").strip()
        if st.button("Angre avstemming", type="secondary"):
            if sel == "(ingen)":
                st.error("Velg en match-ID.")
            elif not reason:
                st.error("Begrunnelse er påkrevet for revisjonsloggen.")
            else:
                repo.unmatch(match_id=sel, user_id=getpass.getuser(), reason=reason)
                st.success(f"Angret avstemming {sel}")
                st.rerun()
