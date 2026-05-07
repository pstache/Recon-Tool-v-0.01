"""
Genus parser: internal fund transactions from a SpareBank 1 .xlsx export.

Source sheet: 'Fund Transaction'.
Side: internal.

Transformations applied:
  * Cancelled rows (Cancelled = True) are dropped — confirmed in clarification #8.
  * trade_date := NAV Date (Q&A: explicitly NOT 'Created').
  * Native transaction type collapses to the 4-value canonical enum:
        Subscription / Investment Plan Subscription / Switch (Subscription) /
            Fund Class Switch (Subscription)                  -> Subscription
        Redemption / Investment Plan Redemption / Switch (Redemption) /
            Fund Class Switch (Redemption) / Distribution Cost -> Redemption
        Transfer (Subscription)                                -> Transfer (Subscription)
        Transfer (Redemption)                                  -> Transfer (Redemption)
  * Quantity sign convention (clarification #16): redemption-side rows get a
    negative sign; subscription-side stays positive. Source data may already be
    signed; we normalise to "abs * direction" so the sign is always correct.
  * native_reference := Cross Transaction Reference (per spec; null is allowed
    because we observed it null on every row in the sample).
  * raw_payload preserves the original row as JSON for the audit trail.

Note on TradeDomain: this parser does NOT filter by TradeDomain. Every row
(External, Between SB1 Banks, Internal) is normalised and ingested. The
matching engine decides which rows attempt which reconciliation:
  * Genus External + Genus Between-SB1-Banks       -> match against Allfunds
  * Genus Distribution Cost                        -> match against Allfunds (exception)
  * All Genus rows                                 -> match against ODIN (no TradeDomain filter)

That decision lives in matching.engine, not here, so this parser stays simple
and the TradeDomain stays visible in raw_payload.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import polars as pl

from parsers.schema import CANONICAL_SCHEMA
from reference.institutions import key_from_name, normalize_name

GENUS_SHEET = "Fund Transaction"

# Maps the native TransactionType -> canonical type (and a sign hint).
# Sign: +1 for buy-side, -1 for sell-side. Used to canonicalise the quantity
# sign on ingest (clarification #16).
_TYPE_COLLAPSE: dict[str, tuple[str, int]] = {
    "Subscription":                       ("Subscription",            +1),
    "Investment Plan Subscription":       ("Subscription",            +1),
    "Switch (Subscription)":              ("Subscription",            +1),
    "Fund Class Switch (Subscription)":   ("Subscription",            +1),
    "Redemption":                         ("Redemption",              -1),
    "Investment Plan Redemption":         ("Redemption",              -1),
    "Switch (Redemption)":                ("Redemption",              -1),
    "Fund Class Switch (Redemption)":     ("Redemption",              -1),
    "Distribution Cost":                  ("Redemption",              -1),
    "Transfer (Subscription)":            ("Transfer (Subscription)", +1),
    "Transfer (Redemption)":              ("Transfer (Redemption)",   -1),
}


def _txid(*parts: Any) -> str:
    """Stable hash for transaction_id (idempotent re-ingest)."""
    h = hashlib.sha1()
    for p in parts:
        h.update(repr(p).encode("utf-8"))
        h.update(b"\x1f")
    return "genus_" + h.hexdigest()[:24]


def parse_genus(path: str | Path) -> pl.DataFrame:
    """Read a Genus .xlsx and return a canonical DataFrame.

    Raises:
        FileNotFoundError: file missing
        ValueError: required sheet missing or required column absent
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    df = pl.read_excel(path, sheet_name=GENUS_SHEET)

    required = {
        "ISIN", "InstitutionName", "NAV Date", "Settlement Date",
        "Settlement Amount", "SettlementCurrency", "TransactionType",
        "TradeDomain", "Units", "Cancelled", "Cross Transaction Reference",
    }
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Genus file missing required columns: {sorted(missing)}")

    # Drop cancelled rows on ingest (clarification #8). They never reconcile.
    df = df.filter(~pl.col("Cancelled").fill_null(False))

    if df.is_empty():
        return _empty_canonical()

    ingested_at = dt.datetime.now()

    rows: list[dict[str, Any]] = []
    for r in df.iter_rows(named=True):
        native_type = r["TransactionType"]
        collapsed = _TYPE_COLLAPSE.get(native_type)
        if collapsed is None:
            # Surface unknown types rather than silently mis-classify. The
            # parser caller (storage layer) is responsible for routing these
            # to parse_errors. We use a sentinel so the row is not lost.
            canonical_type, sign = native_type, +1
        else:
            canonical_type, sign = collapsed

        # Coerce quantity to (sign * |units|) per clarification #16.
        raw_units = r.get("Units")
        if raw_units is None:
            qty = Decimal("0")
        else:
            qty = Decimal(str(abs(float(raw_units)))) * sign

        # Settlement amount: float -> Decimal(2dp). Genus always has it.
        raw_amt = r.get("Settlement Amount")
        amt = Decimal("0") if raw_amt is None else Decimal(str(round(float(raw_amt), 2)))

        # Dates: trade = NAV Date (Q&A); settlement = Settlement Date.
        trade_date = _coerce_date(r.get("NAV Date"))
        settlement_date = _coerce_date(r.get("Settlement Date"))

        institution_display = r.get("InstitutionName") or ""
        institution_key = key_from_name(institution_display)

        isin = (r.get("ISIN") or "").strip()
        currency = (r.get("SettlementCurrency") or "NOK").strip()
        native_ref = r.get("Cross Transaction Reference")
        if native_ref is None:
            native_ref = r.get("Transaction Reference")
        native_ref = "" if native_ref is None else str(native_ref)

        txid = _txid("genus", native_ref, isin, str(qty), trade_date, native_type)

        rows.append({
            "transaction_id":   txid,
            "source":           "genus",
            "side":             "internal",
            "isin":             isin,
            "institution":      normalize_name(institution_display),
            "institution_key":  institution_key,
            "transaction_type": canonical_type,
            "quantity":         qty,
            "amount":           amt,
            "trade_date":       trade_date,
            "settlement_date":  settlement_date,
            "currency":         currency,
            "native_reference": native_ref,
            "raw_payload":      _safe_json(r),
            "ingested_at":      ingested_at,
            "match_status":     "unmatched",
            "matched_with":     None,
            "is_active":        True,
        })

    return pl.DataFrame(rows, schema=CANONICAL_SCHEMA)


def _coerce_date(v: Any) -> dt.date | None:
    if v is None:
        return None
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            return dt.date.fromisoformat(s[:10])
        except ValueError:
            return None
    return None


def _safe_json(r: dict[str, Any]) -> str:
    """JSON-encode a row, coercing dates and Decimals to strings."""
    def default(o):
        if isinstance(o, (dt.date, dt.datetime)):
            return o.isoformat()
        if isinstance(o, Decimal):
            return str(o)
        return str(o)
    return json.dumps(r, default=default, ensure_ascii=False)


def _empty_canonical() -> pl.DataFrame:
    return pl.DataFrame(schema=CANONICAL_SCHEMA)
