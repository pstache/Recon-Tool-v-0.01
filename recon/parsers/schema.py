"""
Canonical transaction schema.

Every parser produces a Polars DataFrame with EXACTLY these columns and dtypes,
in this order. Adding a column = update CANONICAL_SCHEMA + every parser + the
DuckDB DDL in storage.db.
"""

from __future__ import annotations

import polars as pl

# Polars dtype map. We use Decimal for money/qty per the spec ("never float").
# Quantity is signed (negative = sell side, per Q&A #16).
CANONICAL_SCHEMA: dict[str, pl.DataType] = {
    "transaction_id":   pl.Utf8,
    "source":           pl.Utf8,        # genus | odin | allfunds
    "side":             pl.Utf8,        # internal | external
    "isin":             pl.Utf8,
    "institution":      pl.Utf8,        # display name (e.g. "SPAREBANK 1 SR")
    "institution_key":  pl.Utf8,        # canonical match key (e.g. "sb1_sr")
    "transaction_type": pl.Utf8,        # see TX_TYPE_VALUES
    "quantity":         pl.Decimal(20, 6),  # signed; 6dp
    "amount":           pl.Decimal(20, 2),  # NOK; 2dp
    "trade_date":       pl.Date,
    "settlement_date":  pl.Date,
    "currency":         pl.Utf8,
    "native_reference": pl.Utf8,
    "raw_payload":      pl.Utf8,        # JSON-encoded original row
    "ingested_at":      pl.Datetime("us"),
    "match_status":     pl.Utf8,        # unmatched | auto_matched | manual_matched | partial
    "matched_with":     pl.Utf8,        # transaction_id of counterparty, or null
    "is_active":        pl.Boolean,     # False if Cancelled or known_unmatchable archived
}

# Closed enum of canonical transaction types (post-collapse).
# Switches and Investment-Plan rows from Genus/ODIN collapse into these.
# Distribution Cost collapses to Redemption (Q&A #5 + answer to Q4).
TX_TYPE_VALUES: tuple[str, ...] = (
    "Subscription",
    "Redemption",
    "Transfer (Subscription)",
    "Transfer (Redemption)",
)

MATCH_STATUS_VALUES: tuple[str, ...] = (
    "unmatched",
    "auto_matched",
    "manual_matched",
    "partial",
    "known_unmatchable",
)

SOURCE_VALUES: tuple[str, ...] = ("genus", "odin", "allfunds")
SIDE_VALUES: tuple[str, ...] = ("internal", "external")
