"""
Tests for the three parsers: Allfunds, Genus, ODIN.

Allfunds is fully tested from a synthetic fixture (small, known-good lines).
Genus + ODIN are tested via tiny in-memory DataFrames using polars.write_excel
to round-trip through the actual file format.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest

from parsers.allfunds import ALLFUNDS_OFFSETS, parse_allfunds
from parsers.genus import parse_genus
from parsers.odin import parse_odin


# ===========================================================================
# Allfunds parser
# ===========================================================================

def _allfunds_record(
    *,
    record_type: str = "40",
    tx_type_code: str = "10",
    isin: str = "NO0010662836",
    portfolio_code: str = "3920",
    trade_date: str = "2026-04-24",
    settlement_date: str = "2026-04-27",
    quantity: int = 100_000_000,         # /1e6 = 100.0
    gross_amount: int = 5_000_000,       # /100 = 50000.00
    net_amount: int = 5_000_000,         # /100 = 50000.00
    settlement_amount: int = 5_000_000,  # /100 = 50000.00
    tx_ref: str = "TXREF000001",
    alt_ref: str = "ALT001",
) -> str:
    """Build one 800-char Allfunds-style line with the given values at the
    canonical empirical offsets."""
    line = [" "] * 800

    def write(off: int, length: int, val: str) -> None:
        v = val[:length].ljust(length)
        for i, ch in enumerate(v):
            line[off + i] = ch

    def write_zeropad(off: int, length: int, val: int) -> None:
        s = str(val).zfill(length)[-length:]
        for i, ch in enumerate(s):
            line[off + i] = ch

    write(ALLFUNDS_OFFSETS["record_type"].offset, 2, record_type)
    write(ALLFUNDS_OFFSETS["tx_type_code"].offset, 2, tx_type_code)
    write(ALLFUNDS_OFFSETS["isin"].offset, 12, isin)
    write(ALLFUNDS_OFFSETS["tx_ref"].offset, 20, tx_ref)
    write(ALLFUNDS_OFFSETS["alt_ref"].offset, 13, alt_ref)
    write(ALLFUNDS_OFFSETS["portfolio_code"].offset, 4, portfolio_code)
    write(ALLFUNDS_OFFSETS["trade_date"].offset, 10, trade_date)
    write(ALLFUNDS_OFFSETS["settlement_date"].offset, 10, settlement_date)
    write_zeropad(ALLFUNDS_OFFSETS["gross_amount"].offset, 17, gross_amount)
    write_zeropad(ALLFUNDS_OFFSETS["net_amount"].offset, 17, net_amount)
    write_zeropad(ALLFUNDS_OFFSETS["quantity"].offset, 17, quantity)
    write_zeropad(ALLFUNDS_OFFSETS["settlement_amount"].offset, 17, settlement_amount)
    return "".join(line)


def _write_allfunds_fixture(tmp_path: Path, lines: list[str]) -> Path:
    p = tmp_path / "allfunds_fixture.txt"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_allfunds_parses_known_good_record(tmp_path):
    line = _allfunds_record()
    path = _write_allfunds_fixture(tmp_path, [line])
    df, errors = parse_allfunds(path)

    assert errors == []
    assert df.height == 1
    row = df.row(0, named=True)
    assert row["source"] == "allfunds"
    assert row["side"] == "external"
    assert row["isin"] == "NO0010662836"
    assert row["institution_key"] == "sb1_nordmore"
    assert row["transaction_type"] == "Subscription"
    assert row["quantity"] == Decimal("100.000000")
    assert row["amount"] == Decimal("50000.00")
    assert row["trade_date"] == dt.date(2026, 4, 24)
    assert row["settlement_date"] == dt.date(2026, 4, 27)
    assert row["currency"] == "NOK"
    assert row["match_status"] == "unmatched"


def test_allfunds_redemption_quantity_is_negative_on_ingest(tmp_path):
    """Clarification #16: Redemption qty is negative regardless of file sign."""
    line = _allfunds_record(tx_type_code="20", isin="NO0012447137",
                             portfolio_code="4210", quantity=50_000_000)
    path = _write_allfunds_fixture(tmp_path, [line])
    df, errors = parse_allfunds(path)
    assert errors == []
    assert df.height == 1
    assert df.row(0, named=True)["transaction_type"] == "Redemption"
    assert df.row(0, named=True)["quantity"] == Decimal("-50.000000")


def test_allfunds_filter_rejects_non_40(tmp_path):
    line_40 = _allfunds_record(record_type="40", tx_ref="GOOD")
    line_99 = _allfunds_record(record_type="99", tx_ref="REJECTED")
    path = _write_allfunds_fixture(tmp_path, [line_99, line_40])
    df, errors = parse_allfunds(path)
    assert errors == []
    assert df.height == 1
    assert df.row(0, named=True)["native_reference"] == "GOOD"


def test_allfunds_unknown_tx_code_silently_skipped_not_error(tmp_path):
    """Records with tx_type code outside the known set are continuation/
    secondary records, NOT errors. They must be skipped silently."""
    line_known = _allfunds_record(tx_type_code="10")
    line_unknown = _allfunds_record(tx_type_code="ZZ")
    path = _write_allfunds_fixture(tmp_path, [line_known, line_unknown])
    df, errors = parse_allfunds(path)
    assert errors == []        # NOT an error
    assert df.height == 1


def test_allfunds_unknown_portfolio_code_is_an_error(tmp_path):
    line = _allfunds_record(portfolio_code="9999")
    path = _write_allfunds_fixture(tmp_path, [line])
    df, errors = parse_allfunds(path)
    assert df.height == 0
    assert len(errors) == 1
    assert "9999" in errors[0]["reason"]


def test_allfunds_falls_back_to_net_when_settlement_amount_zero(tmp_path):
    line = _allfunds_record(settlement_amount=0, net_amount=4_000_000)
    path = _write_allfunds_fixture(tmp_path, [line])
    df, errors = parse_allfunds(path)
    assert errors == []
    assert df.row(0, named=True)["amount"] == Decimal("40000.00")


def test_allfunds_short_line_skipped_silently(tmp_path):
    p = tmp_path / "short.txt"
    p.write_text("too short\n", encoding="utf-8")
    df, errors = parse_allfunds(p)
    assert df.height == 0
    assert errors == []


def test_allfunds_transfer_subscription_sign_positive(tmp_path):
    """Transfer (Subscription) keeps positive qty per #16."""
    line = _allfunds_record(tx_type_code="13", quantity=10_000_000)
    path = _write_allfunds_fixture(tmp_path, [line])
    df, errors = parse_allfunds(path)
    assert df.row(0, named=True)["transaction_type"] == "Transfer (Subscription)"
    assert df.row(0, named=True)["quantity"] == Decimal("10.000000")


# ===========================================================================
# Genus parser — exercised via a small synthetic xlsx
# ===========================================================================

def _make_genus_xlsx(tmp_path: Path, rows: list[dict]) -> Path:
    df = pl.DataFrame(rows)
    p = tmp_path / "genus.xlsx"
    df.write_excel(p, worksheet="Fund Transaction")
    return p


def _genus_required_cols(**overrides) -> dict:
    base = {
        "ISIN":                       "NO0010662836",
        "InstitutionName":            "SPAREBANK 1 NORDMØRE",
        "NAV Date":                   dt.date(2026, 4, 24),
        "Settlement Date":            dt.date(2026, 4, 27),
        "Settlement Amount":          50000.0,
        "SettlementCurrency":         "NOK",
        "TransactionType":            "Subscription",
        "TradeDomain":                "External",
        "Units":                      100.0,
        "Cancelled":                  False,
        "Cross Transaction Reference": "TX0001",
    }
    base.update(overrides)
    return base


def test_genus_subscription_keeps_positive_quantity(tmp_path):
    p = _make_genus_xlsx(tmp_path, [_genus_required_cols(Units=100.0)])
    df = parse_genus(p)
    assert df.height == 1
    assert df.row(0, named=True)["quantity"] == Decimal("100.000000")
    assert df.row(0, named=True)["transaction_type"] == "Subscription"


def test_genus_redemption_gets_negative_quantity(tmp_path):
    p = _make_genus_xlsx(tmp_path, [
        _genus_required_cols(TransactionType="Redemption", Units=80.0)
    ])
    df = parse_genus(p)
    assert df.row(0, named=True)["quantity"] == Decimal("-80.000000")
    assert df.row(0, named=True)["transaction_type"] == "Redemption"


def test_genus_distribution_cost_collapses_to_redemption_with_negative_qty(tmp_path):
    p = _make_genus_xlsx(tmp_path, [
        _genus_required_cols(TransactionType="Distribution Cost", Units=12.5)
    ])
    df = parse_genus(p)
    row = df.row(0, named=True)
    assert row["transaction_type"] == "Redemption"
    assert row["quantity"] == Decimal("-12.500000")


def test_genus_switch_subscription_collapses(tmp_path):
    p = _make_genus_xlsx(tmp_path, [
        _genus_required_cols(TransactionType="Switch (Subscription)", Units=50.0)
    ])
    df = parse_genus(p)
    assert df.row(0, named=True)["transaction_type"] == "Subscription"
    assert df.row(0, named=True)["quantity"] == Decimal("50.000000")


def test_genus_fund_class_switch_redemption_collapses(tmp_path):
    p = _make_genus_xlsx(tmp_path, [
        _genus_required_cols(
            TransactionType="Fund Class Switch (Redemption)", Units=30.0
        )
    ])
    df = parse_genus(p)
    assert df.row(0, named=True)["transaction_type"] == "Redemption"
    assert df.row(0, named=True)["quantity"] == Decimal("-30.000000")


def test_genus_investment_plan_subscription_collapses(tmp_path):
    p = _make_genus_xlsx(tmp_path, [
        _genus_required_cols(
            TransactionType="Investment Plan Subscription", Units=10.0
        )
    ])
    df = parse_genus(p)
    assert df.row(0, named=True)["transaction_type"] == "Subscription"


def test_genus_cancelled_rows_dropped(tmp_path):
    p = _make_genus_xlsx(tmp_path, [
        _genus_required_cols(Cancelled=True),
        _genus_required_cols(Cancelled=False),
    ])
    df = parse_genus(p)
    assert df.height == 1


def test_genus_missing_required_column_raises(tmp_path):
    bad = _genus_required_cols()
    del bad["ISIN"]
    p = _make_genus_xlsx(tmp_path, [bad])
    with pytest.raises(ValueError, match="ISIN"):
        parse_genus(p)


def test_genus_input_value_negative_units_still_signed_correctly(tmp_path):
    """If Units is already negative for a Redemption, we should still get a
    negative quantity (we use abs * sign)."""
    p = _make_genus_xlsx(tmp_path, [
        _genus_required_cols(TransactionType="Redemption", Units=-80.0),
    ])
    df = parse_genus(p)
    assert df.row(0, named=True)["quantity"] == Decimal("-80.000000")


# ===========================================================================
# ODIN parser
# ===========================================================================

def _make_odin_xlsx(tmp_path: Path, rows: list[dict]) -> Path:
    df = pl.DataFrame(rows)
    p = tmp_path / "odin.xlsx"
    df.write_excel(p, worksheet="Action.Feltverdier Excel")
    return p


def _odin_required_cols(**overrides) -> dict:
    base = {
        "Type":              "Subscription",
        "Kunde":             "SPAREBANK 1 SØR-NORGE ASA",
        "ISIN":              "NO0010662836",
        "Andeler":           100.0,
        "Beløp":             50000.0,
        "Valuta":            "NOK",
        "Kursdato":          dt.date(2026, 4, 28),
        "Transaksjon ref.":  "ODR0001",
    }
    base.update(overrides)
    return base


def test_odin_subscription_positive_qty(tmp_path):
    p = _make_odin_xlsx(tmp_path, [_odin_required_cols()])
    df = parse_odin(p)
    assert df.row(0, named=True)["transaction_type"] == "Subscription"
    assert df.row(0, named=True)["quantity"] == Decimal("100.000000")
    assert df.row(0, named=True)["institution_key"] == "sb1_sor_norge"


def test_odin_redemption_negative_qty(tmp_path):
    p = _make_odin_xlsx(tmp_path, [
        _odin_required_cols(Type="Redemption", Andeler=80.0)
    ])
    df = parse_odin(p)
    assert df.row(0, named=True)["quantity"] == Decimal("-80.000000")


def test_odin_switch_subscription_collapses_to_subscription(tmp_path):
    p = _make_odin_xlsx(tmp_path, [
        _odin_required_cols(Type="Switch (Subscription)", Andeler=50.0)
    ])
    df = parse_odin(p)
    assert df.row(0, named=True)["transaction_type"] == "Subscription"
    assert df.row(0, named=True)["quantity"] == Decimal("50.000000")


def test_odin_switch_redemption_collapses_to_redemption(tmp_path):
    p = _make_odin_xlsx(tmp_path, [
        _odin_required_cols(Type="Switch (Redemption)", Andeler=30.0)
    ])
    df = parse_odin(p)
    assert df.row(0, named=True)["transaction_type"] == "Redemption"
    assert df.row(0, named=True)["quantity"] == Decimal("-30.000000")


def test_odin_settlement_date_is_none(tmp_path):
    """ODIN file has no settlement_date column; we set it None."""
    p = _make_odin_xlsx(tmp_path, [_odin_required_cols()])
    df = parse_odin(p)
    assert df.row(0, named=True)["settlement_date"] is None
