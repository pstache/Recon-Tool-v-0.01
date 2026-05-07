"""
Page: Review.

Lists unmatched + partial transactions, with filters. The user can:
  * Select an internal row + an external row -> "Mark matched"
  * Select a single row -> "Flag as known-unmatchable" (e.g. Between SB1 Banks)

Manual matches are 1:1 (per clarification #12 for non-transfer types). For
transfer rows we still allow 1:1 because that's how the user resolves a
specific split candidate.
"""

from __future__ import annotations

import datetime as dt
import getpass

import streamlit as st

from reference.institutions import all_known_keys, display_for_key
from storage.repository import Repository
from ui.branding import sb1_header

STATUSES = ["unmatched", "partial"]
SOURCES  = ["genus", "odin", "allfunds"]
SIDES    = ["internal", "external"]
TYPES    = ["Subscription", "Redemption", "Transfer (Subscription)", "Transfer (Redemption)"]


def render(repo: Repository) -> None:
    sb1_header(
        "Gjennomgang",
        "Se uavstemte og delvis avstemte transaksjoner. Par sammen interne "
        "og eksterne rader for å markere dem som avstemt.",
    )

    with st.expander("Filtre", expanded=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            from_date = st.date_input("Fra (handelsdato)", value=None, key="rv_from")
            to_date   = st.date_input("Til (handelsdato)", value=None, key="rv_to")
        with c2:
            isin = st.text_input("ISIN begynner med", "", key="rv_isin").strip()
            tx_type = st.selectbox("Type", ["(alle)"] + TYPES, key="rv_type")
        with c3:
            inst_keys = ["(alle)"] + all_known_keys()
            inst = st.selectbox("Institusjon", inst_keys,
                                format_func=lambda k: "(alle)" if k == "(alle)" else display_for_key(k),
                                key="rv_inst")
            source = st.selectbox("Kilde", ["(alle)"] + SOURCES, key="rv_source")
            side   = st.selectbox("Side", ["(alle)"] + SIDES, key="rv_side")
            status = st.selectbox("Status", STATUSES + ["(alle som trenger gjennomgang)"], index=2, key="rv_status")

    filters = {
        "from_date": from_date or None,
        "to_date":   to_date or None,
        "isin":      isin or None,
        "transaction_type": None if tx_type == "(alle)" else tx_type,
        "institution_key":  None if inst == "(alle)" else inst,
        "source":    None if source == "(alle)" else source,
        "side":      None if side == "(alle)" else side,
        # status filter — "(alle som trenger gjennomgang)" means unmatched OR partial
        "match_status": None if status == "(alle som trenger gjennomgang)" else status,
    }

    df = repo.fetch_with_status(filters)

    # If the catch-all option was chosen, post-filter to unmatched ∪ partial
    if status == "(alle som trenger gjennomgang)":
        df = df.filter(df["match_status"].is_in(["unmatched", "partial"]))

    st.markdown(f"**{df.height:,}** transaksjoner passer filteret.")

    # Display table — Streamlit renders Polars natively from 1.36+
    if df.is_empty():
        st.info("Ingen treff. Løsne på filtrene.")
        return

    show_cols = [
        "transaction_id", "source", "side", "match_status", "partial_reason",
        "transaction_type", "isin", "institution", "quantity",
        "trade_date", "settlement_date", "native_reference",
    ]
    show = df.select([c for c in show_cols if c in df.columns]).head(500)
    st.dataframe(show, use_container_width=True, hide_index=True)
    if df.height > 500:
        st.caption(f"Viser de første 500 av {df.height:,} radene.")

    st.divider()
    st.subheader("Manuelle handlinger")

    # Pick internal + external from the filtered set
    internal_options = df.filter(df["side"] == "internal")["transaction_id"].to_list()
    external_options = df.filter(df["side"] == "external")["transaction_id"].to_list()

    c1, c2 = st.columns(2)
    with c1:
        sel_internal = st.selectbox(
            "Intern transaksjon",
            options=["(ingen)"] + internal_options,
            key="rv_sel_int",
        )
    with c2:
        sel_external = st.selectbox(
            "Ekstern transaksjon",
            options=["(ingen)"] + external_options,
            key="rv_sel_ext",
        )

    reason = st.text_input(
        "Begrunnelse (påkrevet for manuelle handlinger)",
        "", key="rv_reason",
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("✅ Marker som avstemt", type="primary"):
            if sel_internal == "(ingen)" or sel_external == "(ingen)":
                st.error("Velg både en intern og en ekstern transaksjon.")
            elif not reason:
                st.error("Begrunnelse er påkrevet for revisjonsloggen.")
            else:
                repo.manual_match(
                    internal_id=sel_internal,
                    external_id=sel_external,
                    user_id=getpass.getuser(),
                    reason=reason,
                )
                st.success(f"Markert {sel_internal} ↔ {sel_external}")
                st.rerun()
    with c2:
        if st.button("🚩 Marker intern som ikke-avstembar"):
            if sel_internal == "(ingen)":
                st.error("Velg den interne transaksjonen først.")
            elif not reason:
                st.error("Begrunnelse er påkrevet.")
            else:
                repo.manual_match(
                    internal_id=sel_internal,
                    external_id=None,
                    user_id=getpass.getuser(),
                    reason=reason,
                    status="known_unmatchable",
                )
                st.success(f"Merket {sel_internal} som ikke-avstembar")
                st.rerun()
    with c3:
        st.caption(
            "Manuelle avstemminger logges i revisjonsloggen med brukernavn, "
            "tidsstempel og begrunnelse."
        )
