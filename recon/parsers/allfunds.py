"""
Allfunds parser: external confirmations from a fixed-width .txt file.

Side: external.
Filter: keep only records where the byte at offset 11 (length 2) equals '40'.
        (Per clarification #1: the spec said '24' but the actual file uses '40'.)

The offset table below was empirically verified against
  Allfunds__External__-_26042912167E210.txt
in the project. It differs from the offsets quoted in the original Q&A by 1
(every offset >= 12 in the spec is one byte too high), which is consistent
with a 1-indexed -> 0-indexed translation error somewhere upstream. ISIN was
not in the spec at all and turned out to live at offset 60, length 12.

If Allfunds ever ships a new file format with shifted offsets, edit ONLY the
ALLFUNDS_OFFSETS dict below — every offset is sourced from there and nowhere
else. (Clarification #21: this is also exposed as a DB-editable table; see
storage.repository.get_allfunds_offsets.)

We stream the file line-by-line. A single Allfunds daily file can be tens of
megabytes; we never load it whole.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

import polars as pl

from parsers.schema import CANONICAL_SCHEMA
from reference.institutions import key_from_code, display_for_key

# ----- offset table (empirically verified) ------------------------------------

@dataclass(frozen=True)
class _Field:
    offset: int
    length: int
    divisor: int = 1   # divide raw integer by this (used for amounts/qty)

# Public so tests / UI can introspect.
ALLFUNDS_OFFSETS: dict[str, _Field] = {
    # filter
    "record_type":       _Field(11, 2),
    # core
    "tx_type_code":      _Field(56, 2),
    "isin":              _Field(60, 12),
    "tx_ref":            _Field(119, 20),
    "alt_ref":           _Field(139, 13),
    "portfolio_code":    _Field(152, 4),    # first 4 chars of a 34-wide field
    "trade_date":        _Field(323, 10),
    "settlement_date":   _Field(363, 10),
    "gross_amount":      _Field(373, 17, 100),
    "net_amount":        _Field(390, 17, 100),
    "quantity":          _Field(407, 17, 1_000_000),
    "settlement_amount": _Field(563, 17, 100),
}

RECORD_TYPE_FILTER = "40"

# Native code -> (canonical type, sign). Direction implements clarification #16.
TX_TYPE_CODE_MAP: dict[str, tuple[str, int]] = {
    # subscriptions
    "10": ("Subscription",            +1),
    "12": ("Subscription",            +1),
    "60": ("Subscription",            +1),
    "61": ("Subscription",            +1),
    "62": ("Subscription",            +1),
    # transfer subscriptions
    "13": ("Transfer (Subscription)", +1),
    # redemptions
    "20": ("Redemption",              -1),
    "22": ("Redemption",              -1),
    "24": ("Redemption",              -1),
    "75": ("Redemption",              -1),
    "76": ("Redemption",              -1),
    "77": ("Redemption",              -1),
    "78": ("Redemption",              -1),
    "79": ("Redemption",              -1),
    "86": ("Redemption",              -1),
    # transfer redemptions
    "23": ("Transfer (Redemption)",   -1),
}

# ----- low-level field extractors --------------------------------------------

def _slice(line: str, f: _Field) -> str:
    return line[f.offset:f.offset + f.length]

def _parse_int_field(raw: str) -> int:
    """Parse an Allfunds zero-padded integer. Empty / blank -> 0."""
    s = raw.lstrip("0").strip()
    if not s:
        return 0
    try:
        return int(s)
    except ValueError:
        return 0

def _parse_decimal_field(raw: str, divisor: int) -> Decimal:
    return Decimal(_parse_int_field(raw)) / Decimal(divisor)

def _parse_date_field(raw: str) -> dt.date | None:
    s = raw.strip()
    if len(s) < 10:
        return None
    try:
        return dt.date.fromisoformat(s[:10])
    except ValueError:
        return None

def _parse_isin(raw: str) -> str | None:
    s = raw.strip()
    # ISIN = 12 chars, 2 letters + 10 alphanumerics. Cheap sanity check.
    if len(s) != 12 or not s[:2].isalpha():
        return None
    return s

def _parse_ref(raw: str) -> str:
    """Reference fields: strip leading zeros if numeric, else strip whitespace."""
    s = raw.strip()
    if s.isdigit():
        return s.lstrip("0") or "0"
    return s

# ----- top-level parser -------------------------------------------------------

def _txid(*parts: Any) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(repr(p).encode("utf-8"))
        h.update(b"\x1f")
    return "allfunds_" + h.hexdigest()[:24]


def parse_allfunds(path: str | Path) -> tuple[pl.DataFrame, list[dict[str, Any]]]:
    """Stream-parse an Allfunds fixed-width file.

    Returns:
        (df, parse_errors) — df is canonical, parse_errors is a list of
        {"line_number", "raw_line", "reason"} dicts. parse_errors is never
        silently dropped — caller is expected to persist it.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    ingested_at = dt.datetime.now()
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for lineno, line in _iter_filtered_lines(path):
        try:
            row = _parse_one(line, ingested_at)
            if row is None:
                # silently skipped (record_type didn't match) — not an error
                continue
            rows.append(row)
        except Exception as e:    # noqa: BLE001 — we *want* to capture all
            errors.append({
                "line_number": lineno,
                "raw_line": line,
                "reason": f"{type(e).__name__}: {e}",
            })

    if not rows:
        return pl.DataFrame(schema=CANONICAL_SCHEMA), errors

    return pl.DataFrame(rows, schema=CANONICAL_SCHEMA), errors


def _iter_filtered_lines(path: Path) -> Iterator[tuple[int, str]]:
    """Yield (1-indexed lineno, line) for every record matching the filter.

    Streamed read, no whole-file load.
    """
    rt = ALLFUNDS_OFFSETS["record_type"]
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for lineno, raw in enumerate(f, start=1):
            line = raw.rstrip("\n").rstrip("\r")
            if len(line) < 581:        # last useful field ends at 580
                continue
            if line[rt.offset:rt.offset + rt.length] != RECORD_TYPE_FILTER:
                continue
            yield lineno, line


def _parse_one(line: str, ingested_at: dt.datetime) -> dict[str, Any] | None:
    """Parse a single Allfunds record into a canonical row dict.

    Returns None if the record should be silently skipped (unknown tx code).
    Raises if the record looks malformed (caller catches and logs).
    """
    tx_code = _slice(line, ALLFUNDS_OFFSETS["tx_type_code"])
    collapsed = TX_TYPE_CODE_MAP.get(tx_code)
    if collapsed is None:
        # The '40' record set contains continuation/secondary records whose
        # tx_type_code is '00' / '  ' / etc. These are not real trades — skip.
        return None
    canonical_type, sign = collapsed

    isin = _parse_isin(_slice(line, ALLFUNDS_OFFSETS["isin"]))
    if isin is None:
        raise ValueError(f"invalid ISIN: {_slice(line, ALLFUNDS_OFFSETS['isin'])!r}")

    portfolio_code = _slice(line, ALLFUNDS_OFFSETS["portfolio_code"]).strip()
    institution_key = key_from_code(portfolio_code)
    if institution_key is None:
        raise ValueError(f"unknown Allfunds portfolio code: {portfolio_code!r}")

    institution_display = display_for_key(institution_key)

    trade_date = _parse_date_field(_slice(line, ALLFUNDS_OFFSETS["trade_date"]))
    settlement_date = _parse_date_field(_slice(line, ALLFUNDS_OFFSETS["settlement_date"]))

    qty_field = ALLFUNDS_OFFSETS["quantity"]
    qty_abs = _parse_decimal_field(_slice(line, qty_field), qty_field.divisor)
    qty = qty_abs * sign

    sett_field = ALLFUNDS_OFFSETS["settlement_amount"]
    sett_amt = _parse_decimal_field(_slice(line, sett_field), sett_field.divisor)
    # Some Allfunds rows have settlement_amount=0 with gross/net populated.
    # Use net_amount when settlement is zero, so the deviation report has data.
    if sett_amt == 0:
        net_field = ALLFUNDS_OFFSETS["net_amount"]
        sett_amt = _parse_decimal_field(_slice(line, net_field), net_field.divisor)

    tx_ref = _parse_ref(_slice(line, ALLFUNDS_OFFSETS["tx_ref"]))

    raw_payload = json.dumps({
        "tx_type_code":       tx_code,
        "isin":               isin,
        "portfolio_code":     portfolio_code,
        "trade_date":         trade_date.isoformat() if trade_date else None,
        "settlement_date":    settlement_date.isoformat() if settlement_date else None,
        "gross_amount":       str(_parse_decimal_field(
            _slice(line, ALLFUNDS_OFFSETS["gross_amount"]),
            ALLFUNDS_OFFSETS["gross_amount"].divisor)),
        "net_amount":         str(_parse_decimal_field(
            _slice(line, ALLFUNDS_OFFSETS["net_amount"]),
            ALLFUNDS_OFFSETS["net_amount"].divisor)),
        "quantity":           str(qty),
        "settlement_amount":  str(sett_amt),
        "tx_ref":             tx_ref,
        "alt_ref":            _parse_ref(_slice(line, ALLFUNDS_OFFSETS["alt_ref"])),
    }, ensure_ascii=False)

    txid = _txid("allfunds", tx_ref, isin, str(qty), trade_date, tx_code)

    return {
        "transaction_id":   txid,
        "source":           "allfunds",
        "side":             "external",
        "isin":             isin,
        "institution":      institution_display,
        "institution_key":  institution_key,
        "transaction_type": canonical_type,
        "quantity":         qty,
        "amount":           sett_amt,
        "trade_date":       trade_date,
        "settlement_date":  settlement_date,
        "currency":         "NOK",
        "native_reference": tx_ref,
        "raw_payload":      raw_payload,
        "ingested_at":      ingested_at,
        "match_status":     "unmatched",
        "matched_with":     None,
        "is_active":        True,
    }
