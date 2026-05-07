"""
Page: Reports.

Generates the end-of-month Excel report. The user picks a year + month;
report is rendered to a temp path and exposed via Streamlit's download
button.

Per clarifications #18 / #19:
  * "End of month" = calendar month based on settlement_date.
  * Top-5 deviations: per ISIN, summed across institutions.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import streamlit as st

from reports.monthly import generate_monthly_report
from storage.repository import Repository
from ui.branding import sb1_header


REPORTS_DIR = Path(__file__).resolve().parent.parent / "data" / "reports"


def render(repo: Repository) -> None:
    sb1_header(
        "Rapporter",
        "Generer månedsrapport som Excel-fil. Periode = kalendermåned basert "
        "på oppgjørsdato. Avvik summeres per ISIN på tvers av institusjoner.",
    )

    today = dt.date.today()
    default_year  = today.year if today.month > 1 else today.year - 1
    default_month = today.month - 1 if today.month > 1 else 12

    c1, c2 = st.columns(2)
    with c1:
        year = st.number_input(
            "År", min_value=2020, max_value=today.year + 1,
            value=default_year, step=1,
        )
    with c2:
        month = st.selectbox(
            "Måned",
            options=list(range(1, 13)),
            index=default_month - 1,
            format_func=lambda m: dt.date(2000, m, 1).strftime("%B"),
        )

    out_path = REPORTS_DIR / f"recon_{year:04d}_{month:02d}.xlsx"

    if st.button("📥 Generer rapport", type="primary"):
        with st.spinner(f"Genererer rapport for {dt.date(year, month, 1).strftime('%B %Y')}…"):
            generate_monthly_report(
                repo, year=int(year), month=int(month), out_path=out_path,
            )
        st.success(f"Rapport generert: `{out_path.name}`")

    if out_path.exists():
        st.divider()
        with open(out_path, "rb") as f:
            st.download_button(
                label=f"⬇️ Last ned {out_path.name}",
                data=f.read(),
                file_name=out_path.name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        st.caption(
            f"Sist generert: "
            f"{dt.datetime.fromtimestamp(out_path.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')}"
        )

    st.divider()
    st.subheader("Innhold i rapporten")
    st.markdown(
        "- **Summary** — antall per status × kilde\n"
        "- **Top Deviations** — topp-5 ISIN-er etter totalt avvik (mengde), "
        "summert på tvers av institusjoner\n"
        "- **Auto Matches** — alle auto-avstemte par som er gjort opp i måneden\n"
        "- **Partial Matches** — delvise med `institution_diff`, `date_diff` "
        "og mulige overføringssplitter\n"
        "- **Unmatched** — alle uavstemte aktive transaksjoner med oppgjør i måneden"
    )
