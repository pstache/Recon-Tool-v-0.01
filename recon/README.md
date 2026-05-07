# SB1 Reconciliation Tool

Reconciles SpareBank 1 Forvaltning's internal Genus fund transactions
against external confirmations from ODIN and Allfunds.

## What it does

1. **Ingest** three source files daily:
   - `Genus_Internal_FundTransaction_Export_*.xlsx` (internal)
   - `ODIN_External_*Transaksjonsbekreftelse*.xlsx` (external)
   - `Allfunds_External_*.txt` (external, fixed-width)
2. **Normalise** each into one canonical schema (signed quantity, canonical
   institution keys, collapsed transaction-type enum).
3. **Match** internal vs external via a vectorised SQL join on
   ISIN + institution + type + signed-quantity (±0.0001) + date.
4. **Surface partials** (institution-differs or date-differs) for human
   review.
5. **Roll forward** unmatched rows automatically — yesterday's unmatched
   are still in scope today, with no manual carry-over step.
6. **Audit** every manual action and log every parse error.
7. **Report** end-of-month: auto/partial/unmatched counts, top-5 ISIN
   deviations, full Excel export.

## Quick start

```bash
# 1. Install
python -m pip install -r requirements.txt

# 2. Run tests (recommended on first install)
python -m pytest tests/

# 3. Launch the UI
streamlit run app.py
```

The app opens at <http://localhost:8501>. Use the left sidebar to navigate.

## Daily workflow

1. **Upload & Run** — drag your three files in. Source is auto-detected from
   filename. Click *Run matching*.
2. **Review** — work through the partials and remaining unmatched rows.
   Pair them manually or flag known-unmatchable.
3. **Reports** — month-end, generate the Excel report and download.

## Project layout

```
recon/
├── app.py                  Streamlit entry point
├── parsers/                Source-specific file readers
│   ├── schema.py           Canonical schema (single source of truth)
│   ├── genus.py            Internal .xlsx parser
│   ├── odin.py             External .xlsx parser
│   └── allfunds.py         External fixed-width .txt parser
├── reference/
│   └── institutions.py     Canonical institution keys, Sør-Norge merger
├── storage/
│   ├── db.py               DuckDB connection + DDL
│   └── repository.py       High-level CRUD; everything else goes through this
├── matching/
│   ├── rules.py            Tolerances and constants
│   └── engine.py           Vectorised matching engine
├── reports/
│   └── monthly.py          End-of-month Excel generator
├── ui/                     Streamlit pages
│   ├── common.py
│   ├── page_upload.py
│   ├── page_review.py
│   ├── page_search.py
│   ├── page_reports.py
│   ├── page_audit.py
│   └── page_settings.py
└── tests/                  pytest suite
    ├── test_institutions.py
    ├── test_parsers.py
    └── test_matching.py
```

## Configuration

By default the database lives at `recon/data/recon.duckdb`. Override with:

```bash
RECON_DB=/path/to/your.duckdb streamlit run app.py
```

Uploaded files are persisted to `recon/data/uploads/` (timestamp-prefixed
copies, kept indefinitely for audit). Generated reports land in
`recon/data/reports/`.

## Adding a new external source

1. Write a parser in `parsers/<source>.py` that returns
   `pl.DataFrame(schema=CANONICAL_SCHEMA)` plus a list of parse errors.
   Apply the signed-quantity convention (negative for sell-side).
2. Register the source name in `parsers/schema.SOURCE_VALUES`.
3. Add filename detection in `ui/common.detect_source`.
4. Add a routing branch in `matching/engine.run_matching` if it has its own
   eligibility rules (like Allfunds' TradeDomain filter).
5. Add tests in `tests/test_parsers.py` mirroring the Genus/ODIN/Allfunds
   test groups.

The canonical schema, institution-key resolver, and matching engine are
source-agnostic — you should not need to touch them.

## Adding a new institution

Edit `reference/institutions.py`:

```python
NAME_TO_KEY["NEW INSTITUTION NAME"] = "sb1_new_key"
CODE_TO_KEY["1234"] = "sb1_new_key"     # if present in Allfunds
KEY_TO_DISPLAY["sb1_new_key"] = "SpareBank 1 New"
```

For runtime overrides without a redeploy, use the **Settings** page in the
UI — those are stored in `institution_overrides` and consulted alongside the
static map.

## Handling Allfunds layout changes

The fixed-width offsets in `parsers/allfunds.py` (`ALLFUNDS_OFFSETS`) were
empirically verified against the production export. If Allfunds ships a new
layout, edit that dict — every offset is sourced from there and nowhere
else. The **Settings** page surfaces the current offsets read-only for
audit.

## Known limitations

- Single-user local app: no auth, no multi-tenant. Audit log records the OS
  user (`getpass.getuser()`) for attribution. If multi-user is needed,
  layer a real auth provider in front and pass user id via session state.
- The matching engine is greedy 1:1. If two valid pairings exist for the
  same (ISIN, institution, type, qty, date) tuple, the lexicographically
  smaller external_id wins. Real-world ambiguity is rare given the date
  granularity, but be aware.
- Transfer-split candidates are flagged, not auto-resolved. The user picks
  which split combination is correct from the **Review** page.
