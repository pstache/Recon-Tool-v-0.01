"""
ODIN parser: external transaction confirmations from a SpareBank 1 .xlsx export.

Source sheet: 'Action.Feltverdier Excel'.
Side: external.

Field map:
    Type            -> transaction_type (after collapse)
    Kunde           -> institution (display)
    ISIN            -> isin
    Andeler         -> quantity (signed per clarification #16)
    Beløp           -> amount
    Valuta          -> currency
    Kursdato        -> trade_date
    Transaksjon ref.-> native_reference
    (no settlement_date in source — set to None; ODIN reconciles primarily on
     trade_date which equals Kursdato)

Type collapse (clarification #4):
    Subscription / Switch (Subscription) -> Subscription
    Redemption / Switch (Redemption)     -> Redemption
    Transfer (Subscription)              -> Transfer (Subscription)
    Transfer (Redemption)                -> Transfer (Redemption)
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

ODIN_SHEET = "Action.Feltverdier Excel"

_TYPE_COLLAPSE: dict[str, tuple[str, int]] = {
    "Subscription":             ("Subscription",            +1),
    "Switch (Subscription)":    ("Subscription",            +1),
    "Redemption":               ("Redemption",              -1),
    "Switch (Redemption)":      ("Redemption",              -1),
    "Transfer (Subscription)":  ("Transfer (Subscription)", +1),
    "Transfer (Redemption)":    ("Transfer (Redemption)",   -1),
}


def _txid(*parts: Any) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(repr(p).encode("utf-8"))
        h.update(b"\x1f")
    return "odin_" + h.hexdigest()[:24]


def parse_odin(path: str | Path) -> pl.DataFrame:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    df = pl.read_excel(path, sheet_name=ODIN_SHEET)

    required = {"Type", "Kunde", "ISIN", "Andeler", "Beløp", "Kursdato"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"ODIN file missing required columns: {sorted(missing)}")

    if df.is_empty():
        return pl.DataFrame(schema=CANONICAL_SCHEMA)

    ingested_at = dt.datetime.now()

    rows: list[dict[str, Any]] = []
    for r in df.iter_rows(named=True):
        native_type = r["Type"]
        collapsed = _TYPE_COLLAPSE.get(native_type)
        if collapsed is None:
            canonical_type, sign = native_type, +1
        else:
            canonical_type, sign = collapsed

        raw_qty = r.get("Andeler")
        qty = Decimal("0") if raw_qty is None else Decimal(str(abs(float(raw_qty)))) * sign

        raw_amt = r.get("Beløp")
        amt = Decimal("0") if raw_amt is None else Decimal(str(round(float(raw_amt), 2)))

        trade_date = _coerce_date(r.get("Kursdato"))
        # ODIN file has no settlement-date column. Leave None — Subscription /
        # Redemption matching uses trade_date anyway. For Transfers (which use
        # ±30-day settlement window) ODIN matches will fall back gracefully.
        settlement_date = None

        institution_display = r.get("Kunde") or ""
        institution_key = key_from_name(institution_display)

        isin = (r.get("ISIN") or "").strip()
        currency = (r.get("Valuta") or "NOK").strip() if r.get("Valuta") else "NOK"
        native_ref = r.get("Transaksjon ref.")
        native_ref = "" if native_ref is None else str(native_ref)

        txid = _txid("odin", native_ref, isin, str(qty), trade_date, native_type)

        rows.append({
            "transaction_id":   txid,
            "source":           "odin",
            "side":             "external",
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
    def default(o):
        if isinstance(o, (dt.date, dt.datetime)):
            return o.isoformat()
        if isinstance(o, Decimal):
            return str(o)
        return str(o)
    return json.dumps(r, default=default, ensure_ascii=False)
