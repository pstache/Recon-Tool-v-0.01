"""
Page: Upload & Run.

Drag-and-drop one or more daily files. For each file:
  1. Save to /home/claude/recon/data/uploads/<timestamp>_<name>
  2. Compute SHA-256, check ingested_files for an exact-bytes match
       -> if match: hard error (clarification #11)
  3. Auto-detect source from filename (Genus / ODIN / Allfunds)
       -> let the user override if detection failed
  4. Parse via the appropriate parser
  5. Upsert into transactions_all (silent dedupe by transaction_id)
  6. Record in ingested_files

After all files are processed, the user clicks "Run matching" to invoke the
matching engine. Results are summarised inline.
"""

from __future__ import annotations

import getpass
from pathlib import Path

import streamlit as st

from matching.engine import run_matching
from storage.repository import Repository
from ui.branding import sb1_header
from ui.common import (
    detect_source,
    file_already_uploaded,
    parse_dispatch,
    save_uploaded_file,
)

UPLOADS_DIR = Path(__file__).resolve().parent.parent / "data" / "uploads"


def render(repo: Repository) -> None:
    sb1_header(
        "Last opp og kjør avstemming",
        "Slipp dagens filer her og kjør auto-avstemming mot ODIN og Allfunds.",
    )
    st.markdown(
        "Genus-eksport er `.xlsx`. ODIN-eksport er `.xlsx`. "
        "Allfunds-eksport er fastformat `.txt`."
    )

    files = st.file_uploader(
        "Slipp filer her",
        type=["xlsx", "txt"],
        accept_multiple_files=True,
        help="Du kan velge flere filer samtidig. Kilden hentes fra filnavnet.",
    )

    if files:
        for f in files:
            with st.expander(f"📄 {f.name}", expanded=True):
                _process_file(repo, f)

    st.divider()
    if st.button("▶ Kjør avstemming", type="primary"):
        with st.spinner("Avstemmer mot Allfunds og ODIN…"):
            result = run_matching(repo)
        st.success("Avstemming ferdig.")
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Auto-avstemt (denne kjøringen)", result.auto_matched)
        c2.metric("Delvis (denne kjøringen)", result.partial)
        c3.metric("Mulige delsplitt (overføring)", result.transfer_split_candidates)
        c4.metric("Uavstemt internt", result.unmatched_internal)
        c5.metric("Uavstemt eksternt", result.unmatched_external)


def _process_file(repo: Repository, uploaded) -> None:
    saved_path = save_uploaded_file(uploaded, UPLOADS_DIR)

    # Idempotency: hard error on exact-bytes re-upload.
    prior = file_already_uploaded(repo, saved_path)
    if prior is not None:
        st.error(
            f"❌ Denne filen er allerede importert "
            f"**{prior['ingested_at']}** som `{prior['file_name']}` "
            f"({prior['source']}, {prior['row_count']:,} rader). "
            f"Identiske filer kan ikke importeres på nytt. Hvis dette er en "
            f"korrigert versjon, gi den nytt navn først."
        )
        return

    detected = detect_source(uploaded.name)
    source = st.selectbox(
        "Kilde",
        options=["genus", "odin", "allfunds"],
        index=["genus", "odin", "allfunds"].index(detected) if detected else 0,
        key=f"src_{uploaded.name}",
        help=(
            "Funnet fra filnavnet. Overstyr her om gjenkjenningen ble feil."
            if detected else
            "Kunne ikke gjenkjenne kilden — velg riktig her."
        ),
    )

    try:
        df, parse_errors = parse_dispatch(saved_path, source)
    except Exception as e:    # noqa: BLE001
        st.error(f"❌ Kunne ikke parse filen: {type(e).__name__}: {e}")
        return

    inserted, skipped = repo.upsert_transactions(df)

    file_hash = repo.hash_file(saved_path)
    repo.record_ingested_file(
        file_hash=file_hash,
        file_name=uploaded.name,
        source=source,
        row_count=df.height,
    )
    if parse_errors:
        repo.record_parse_errors(source, uploaded.name, parse_errors)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Rader lest", f"{df.height:,}")
    c2.metric("Nye importert", f"{inserted:,}")
    c3.metric("Hoppet over (duplikat)", f"{skipped:,}")
    c4.metric("Parsefeil", f"{len(parse_errors):,}")
    if parse_errors:
        st.warning(
            f"{len(parse_errors)} linje(r) kunne ikke leses og er logget i "
            f"`parse_errors`-tabellen. Se Revisjon-siden."
        )
