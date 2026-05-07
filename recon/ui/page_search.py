"""
Page: Search.

Free-form lookup across the full transaction store, including matched and
archived rows. The Review page is for the daily reconciliation queue;
Search is for "where is this specific transaction?" investigations — e.g.
finding every leg of a particular Cross Transaction Reference, or pulling
up everything for a given ISIN over a date range.

Search keys (any combo, all optional):
  * Transaction ID (exact)
  * Native reference (substring)
  * ISIN (prefix)
  * Institution
  * Trade-date range
  * Source / side
  * Match status

The page also includes "Show inactive" so the user can search archived rows
(per clarification #14 — auto-archived after 180 days).
"""

from __future__ import annotations

import streamlit as st

from reference.institutions import all_known_keys, display_for_key
from storage.repository import Repository
from ui.branding import sb1_header

SOURCES = ["genus", "odin", "allfunds"]
SIDES   = ["internal", "external"]
STATUSES = ["unmatched", "auto_matched", "manual_matched", "partial", "known_unmatchable"]


def render(repo: Repository) -> None:
    sb1_header(
        "Søk",
        "Finn transaksjoner i hele lageret — også avstemte og arkiverte.",
    )

    with st.expander("Søkekriterier", expanded=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            txid = st.text_input("Transaksjons-ID (eksakt)", "", key="s_txid").strip()
            native_ref = st.text_input("Original referanse (delstreng)", "", key="s_ref").strip()
            isin = st.text_input("ISIN begynner med", "", key="s_isin").strip()
        with c2:
            from_date = st.date_input("Fra (handelsdato)", value=None, key="s_from")
            to_date   = st.date_input("Til (handelsdato)", value=None, key="s_to")
            inst_keys = ["(alle)"] + all_known_keys()
            inst = st.selectbox(
                "Institusjon", inst_keys,
                format_func=lambda k: "(alle)" if k == "(alle)" else display_for_key(k),
                key="s_inst",
            )
        with c3:
            source = st.selectbox("Kilde", ["(alle)"] + SOURCES, key="s_source")
            side   = st.selectbox("Side", ["(alle)"] + SIDES, key="s_side")
            status = st.selectbox("Avstemmingsstatus", ["(alle)"] + STATUSES, key="s_status")
            include_inactive = st.checkbox("Inkluder arkiverte", value=False)

    # Direct-ID lookup — short-circuit, return everything we know.
    if txid:
        row = repo.get_transaction(txid)
        if row is None:
            st.warning(f"Ingen transaksjon med ID `{txid}`.")
            return
        st.subheader(f"Transaksjon `{txid}`")
        st.json(row)
        return

    # Build filters and query.
    filters = {
        "from_date":        from_date or None,
        "to_date":          to_date or None,
        "isin":             isin or None,
        "institution_key":  None if inst == "(alle)" else inst,
        "source":           None if source == "(alle)" else source,
        "side":             None if side == "(alle)" else side,
        "match_status":     None if status == "(alle)" else status,
    }

    df = repo.fetch_with_status(filters)

    if include_inactive:
        # fetch_with_status defaults is_active = TRUE — re-query with override.
        # Simpler: do a second SQL call directly.
        df = repo.db.execute(
            "SELECT * FROM transactions_with_status ORDER BY trade_date DESC LIMIT 5000"
        ).pl()

    # Native-reference substring filter, applied client-side because it's
    # a substring match (DB index on transaction_id, not native_reference).
    if native_ref:
        df = df.filter(df["native_reference"].str.contains(native_ref, literal=False))

    st.markdown(f"**{df.height:,}** transaksjoner passer.")

    if df.is_empty():
        st.info("Ingen treff. Løsne på kriteriene.")
        return

    # Show the most useful columns; drop raw_payload (huge JSON) by default.
    show_cols = [
        "transaction_id", "source", "side", "match_status", "partial_reason",
        "transaction_type", "isin", "institution", "quantity", "amount",
        "trade_date", "settlement_date", "native_reference", "matched_with",
    ]
    show = df.select([c for c in show_cols if c in df.columns]).head(500)
    st.dataframe(show, use_container_width=True, hide_index=True)
    if df.height > 500:
        st.caption(f"Viser de første 500 av {df.height:,} radene. Stram inn filtrene for å snevre inn.")

    # Drill-in: pick a row and view its raw payload.
    st.divider()
    st.subheader("Inspiser en transaksjon")
    options = ["(ingen)"] + df["transaction_id"].head(500).to_list()
    sel = st.selectbox("Transaksjons-ID", options=options, key="s_inspect")
    if sel != "(ingen)":
        row = repo.get_transaction(sel)
        if row is not None:
            st.json(row)
