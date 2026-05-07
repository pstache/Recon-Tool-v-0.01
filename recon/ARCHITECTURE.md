# Architecture notes

This document explains the load-bearing design decisions. Everything else in
the code follows from these.

## 1. One canonical schema, three parsers

Every source file produces the *same* Polars DataFrame shape, defined once
in `parsers/schema.py:CANONICAL_SCHEMA`. The matching engine, storage layer,
and reports never look at source-specific fields — they all consume the
canonical schema.

The translation cost is paid once, in the parser. After that, ODIN and
Allfunds rows are interchangeable from the engine's point of view.
Adding a new external source therefore means: write one parser. Don't
touch the engine.

## 2. Signed quantity *is* the direction

Per Q&A clarification #16, every redemption / transfer-out / distribution-
cost row has its quantity stored negative; every subscription / transfer-in
keeps it positive. This is applied **on ingest** in each parser.

The matching engine then has no concept of "direction" — it just compares
quantity values directly. A Genus Redemption with `qty = -100` cannot
accidentally match a Subscription with `qty = +100` because the signs
differ. This collapses two coupled checks (type + qty-sign) into one.

A side benefit: deviation reports can sum signed quantities to get a net
position per ISIN without per-row direction bookkeeping.

## 3. Roll-forward via a view, not a daily job

`unmatched_pool` is a `VIEW` over `transactions_all` minus everything
currently in `matches`. The matching engine reads from this view — so
yesterday's unmatched rows are automatically in scope today, with no
explicit "carry forward" step.

Concretely: when matching runs, it sees every transaction from every prior
day that hasn't been matched yet, plus today's new rows. When a match is
recorded, both sides drop out of the view in the same transaction. There
is no separate "carryover queue" table that could drift out of sync.

The 180-day auto-archive (clarification #14) is a `is_active = FALSE` flag
on the row. The view filters on `is_active`, so archived rows are silently
removed from the pool but remain searchable on the Search page.

## 4. Matching is SQL, not Python

The engine pushes the entire join down to DuckDB via SQL. Polars is used
for ingest and for displaying results in the UI; matching itself is a
single SQL `INSERT INTO matches SELECT ... FROM unmatched_pool i JOIN
unmatched_pool e ON ...` per rule.

Why: at hundreds of thousands of rows, a Python loop joining each internal
row against externals is several orders of magnitude slower. SQL also
gives us free transactional integrity — the whole matching pass is wrapped
in `BEGIN ... COMMIT` so partial failures don't leave the matches table
inconsistent.

## 5. Greedy iterative pairing for 1:1

When N internals and N externals all share an identical match key, a
single rank-1-on-both-sides pass picks only one pair. The engine therefore
loops: pick winners → remove them → repeat, until no more pairs.

Order is deterministic (lexicographic on internal_id then external_id), so
re-running matching on the same state is idempotent.

This is greedy, not optimal — if two internals could each match either of
two externals with different quality scores, we don't try to find the
globally best assignment. In practice the match keys are tight enough
(ISIN + institution + type + qty + date) that ambiguous N:M cases are
rare. If they become a real problem, swap `_resolve_and_insert_pairs` for
the Hungarian algorithm without touching anything else.

## 6. Two TradeDomain rules, one engine, JSON-extract for routing

The spec routes Genus rows to Allfunds vs ODIN differently:

- **Allfunds** sees Genus rows where TradeDomain ∈ `{External, Between SB1
  Banks}`, plus Distribution Cost rows regardless of TradeDomain
  (clarification #5).
- **ODIN** sees all Genus rows — no TradeDomain filter (clarification #7).

We don't promote `TradeDomain` to a top-level column because it's only
relevant for this one routing decision. Instead, `raw_payload` stores the
full original row as JSON, and the eligibility predicate uses
`json_extract_string(raw_payload, '$.TradeDomain')` inline in the matching
SQL. This keeps the canonical schema source-agnostic.

## 7. Idempotency at two levels

Per clarification #11:

- **File-level (hard error):** every uploaded file's SHA-256 is stored in
  `ingested_files`. Re-uploading the exact same bytes raises an error in
  the UI rather than silently re-ingesting.
- **Transaction-level (silent dedupe):** `transaction_id` is a stable
  hash of `(source, native_reference, ISIN, signed_qty, trade_date,
  native_type)`. Re-ingesting the same transactions in a renamed file
  silently skips duplicates instead of producing two copies.

This means an Operator can re-pull a file with a corrected name without
fear, and can also re-run an ingest after a bug fix without polluting the
data.

## 8. DuckDB choice

DuckDB over SQLite for two reasons:

1. **Decimal precision.** SQLite stores numbers as `REAL` (8-byte float),
   which loses precision on quantities like `-18379.318` over enough
   round-trips. DuckDB's native `DECIMAL(20,6)` matches Polars' Decimal
   type exactly.
2. **Polars integration.** DuckDB can register a Polars DataFrame as a
   table without going through pandas, which would force the same
   precision loss as SQLite.

DuckDB is still embedded and single-file, so the operational profile
(no daemon, drop-in deployable) is identical to SQLite.

## 9. Audit trail is a write-once log, not a mutable status

`manual_match_log` records every action — match, undo-match, flag-as-
unmatchable — with user, timestamp, and free-text reason. Undoing a match
**deletes** the `matches` row but **inserts** an `unmatch` log entry, so
the history is preserved even though the current state is restored.

If a match is undone and then re-applied later, the audit log shows both
events in order. There is no "current status" field on the audit log
itself — the matches table is the current state, the log is the history.

## 10. UI is a sidebar-radio, not multipage

Streamlit's official "multipage app" feature requires a `pages/` folder
with magic-named files. For a single-user local tool, a sidebar radio in
`app.py` keeps the project tree flat and the entry point obvious. Each
page is a `render(repo)` function in `ui/page_*.py`.

This is a deliberate trade-off: easier to read and review, slightly less
"native" Streamlit. If the page count grows past ~10, switch to pages/.
