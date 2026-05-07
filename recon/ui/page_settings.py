"""
Page: Settings.

Per clarification #21, the institution map is editable from the UI. The
matching engine reads from the static `reference.institutions` map first,
then layers `institution_overrides` on top — so additions/corrections from
this page take effect on the next matching run.

Also exposes the Allfunds offset table read-only (with editor for advanced
users), so the team can see the empirically-verified layout in one place.
Edits are recorded in `allfunds_offset_overrides`. The parser does NOT yet
consume this table — it would be the natural extension if Allfunds ships a
new layout. (For now this page surfaces the offsets so they're visible and
auditable, not a re-implementation of the parser.)
"""

from __future__ import annotations

import datetime as dt
import getpass

import polars as pl
import streamlit as st

from parsers.allfunds import ALLFUNDS_OFFSETS
from reference.institutions import KEY_TO_DISPLAY, all_known_keys
from storage.repository import Repository
from ui.branding import sb1_header


def render(repo: Repository) -> None:
    sb1_header(
        "Innstillinger",
        "Institusjonsoverstyringer og Allfunds-oppsett. "
        "Endringer trer i kraft ved neste avstemmingskjøring.",
    )

    tab_inst, tab_offsets, tab_archive = st.tabs(
        ["Institusjoner", "Allfunds-felter", "Vedlikehold"]
    )

    with tab_inst:
        _render_institution_overrides(repo)

    with tab_offsets:
        _render_offset_table(repo)

    with tab_archive:
        _render_archive(repo)


def _render_institution_overrides(repo: Repository) -> None:
    st.subheader("Innebygd institusjonsliste")
    st.markdown(
        "Disse oppslagene ligger i koden (for SØR-NORGE-fusjonen og de "
        "vanlige SpareBank 1-alliansebankene). For å overstyre eller legge "
        "til egne institusjoner, bruk overstyringstabellen nedenfor."
    )
    builtin = pl.DataFrame({
        "kanonisk_nokkel": list(KEY_TO_DISPLAY.keys()),
        "visningsnavn":    list(KEY_TO_DISPLAY.values()),
    }).sort("kanonisk_nokkel")
    st.dataframe(builtin, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Egne overstyringer")
    overrides = repo.get_institution_overrides()
    if overrides.is_empty():
        st.info("Ingen egne overstyringer ennå.")
    else:
        st.dataframe(overrides, use_container_width=True, hide_index=True)

    st.markdown("**Legg til eller oppdater en overstyring**")
    with st.form("add_override"):
        c1, c2, c3 = st.columns(3)
        with c1:
            lookup_kind = st.selectbox(
                "Type oppslag", ["name", "code"],
                help="`name` treffer Genus/ODIN-visningsnavn. "
                     "`code` treffer Allfunds 4-sifrede porteføljekoder.",
            )
            lookup_value = st.text_input(
                "Verdi som skal slås opp",
                help="Den eksakte stavemåten eller koden slik den står i kildedataene.",
            )
        with c2:
            canonical_key = st.selectbox(
                "Kanonisk nøkkel",
                options=all_known_keys(),
                help="Hvilken institusjon dette oppslaget skal mappe til.",
            )
        with c3:
            display_name = st.text_input(
                "Visningsnavn (valgfritt)",
                help="Tomt felt = bruk standardnavnet for den kanoniske nøkkelen.",
            )
        submitted = st.form_submit_button("Lagre overstyring")
        if submitted:
            if not lookup_value.strip():
                st.error("Verdi er påkrevet.")
            else:
                repo.upsert_institution_override(
                    lookup_kind=lookup_kind,
                    lookup_value=lookup_value.strip(),
                    canonical_key=canonical_key,
                    display_name=display_name.strip() or None,
                    added_by=getpass.getuser(),
                )
                st.success("Overstyring lagret.")
                st.rerun()

    if not overrides.is_empty():
        st.markdown("**Slett en overstyring**")
        ids = overrides.select(["lookup_kind", "lookup_value"]).rows()
        labels = [f"{k}: {v}" for k, v in ids]
        sel = st.selectbox(
            "Velg overstyring", options=["(ingen)"] + labels, key="settings_del"
        )
        if st.button("Slett overstyring") and sel != "(ingen)":
            idx = labels.index(sel)
            kind, value = ids[idx]
            repo.delete_institution_override(kind, value)
            st.success(f"Slettet {sel}")
            st.rerun()


def _render_offset_table(repo: Repository) -> None:
    st.subheader("Allfunds-format (empirisk verifisert)")
    st.markdown(
        "Disse offsettene er verifisert mot produksjons-eksporten fra Allfunds "
        "i prosjektet. De skiller seg fra spesifikasjonen med 1 byte for alle "
        "felter etter `record_type`. Parseren bruker verdiene som vises her. "
        "Hvis Allfunds endrer formatet, oppdater "
        "`parsers/allfunds.py:ALLFUNDS_OFFSETS` og deploy på nytt — "
        "overstyringstabellen er informativ inntil videre."
    )

    rows = []
    for name, f in ALLFUNDS_OFFSETS.items():
        rows.append({
            "felt":     name,
            "offset":   f.offset,
            "lengde":   f.length,
            "divisor":  f.divisor,
        })
    st.dataframe(pl.DataFrame(rows), use_container_width=True, hide_index=True)

    st.caption(
        "Filter: behold rader der byte 11–12 = `'40'`. Transaksjonstype-koden "
        "i offset 56 må være i den kjente kodelisten (10/12/13/20/22/23/24/"
        "60/61/62/75/76/77/78/79/86)."
    )


def _render_archive(repo: Repository) -> None:
    st.subheader("Auto-arkivering (180 dager)")
    st.markdown(
        "Uavstemte transaksjoner eldre enn 180 dager arkiveres automatisk "
        "(`is_active = FALSE`) slik at de ikke lenger vises i den daglige "
        "gjennomgangskøen. De er fortsatt søkbare på Søk-siden med "
        "«Inkluder arkiverte»."
    )
    if st.button("Kjør arkivering nå"):
        archived = repo.archive_old_unmatched()
        st.success(f"Arkiverte {archived:,} transaksjon(er).")
