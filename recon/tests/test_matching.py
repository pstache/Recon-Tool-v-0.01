"""
Matching engine tests.

Each test sets up a tiny in-memory state by inserting canonical rows directly
into the DB (bypassing the parsers) and runs run_matching(). We assert on the
contents of the matches table.

Coverage:
  * Subscription auto-match: ISIN + qty + institution + type + date all agree
  * Redemption auto-match: same but with negative qty
  * Quantity tolerance: ±0.0001 inclusive
  * Quantity-differs > tolerance → unmatched (NEVER partial — clarification C)
  * Partial: institution_diff
  * Partial: date_diff
  * Type differs → no match (Subscription vs Redemption never match)
  * 1:1 enforcement: 2 internals + 2 externals → 2 matches, not 4
  * Transfer ±30 day window
  * TradeDomain routing for Allfunds (Internal-domain ineligible, External
    eligible, Distribution Cost eligible)
  * No TradeDomain filter for ODIN
  * Roll-forward: a transaction from yesterday's run is still in the pool today
"""

from __future__ import annotations

import datetime as dt
import json
import os
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest

from matching.engine import run_matching
from parsers.schema import CANONICAL_SCHEMA
from storage.db import DB
from storage.repository import Repository


# ---------------------------------------------------------------- helpers

@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Fresh DuckDB per test, isolated under tmp_path."""
    db_path = tmp_path / "test.duckdb"
    monkeypatch.setenv("RECON_DB", str(db_path))
    db = DB(path=db_path)
    db.connect()
    repo = Repository(db)
    yield repo
    db.close()


def _row(
    *,
    transaction_id: str,
    source: str,
    side: str,
    isin: str = "NO0010000001",
    institution_key: str = "sb1_smn",
    transaction_type: str = "Subscription",
    quantity: Decimal = Decimal("100.000000"),
    trade_date: dt.date = dt.date(2026, 4, 24),
    settlement_date: dt.date | None = None,
    trade_domain: str = "External",
    native_type: str | None = None,
) -> dict:
    payload = {"TradeDomain": trade_domain, "TransactionType": native_type or transaction_type}
    return {
        "transaction_id":   transaction_id,
        "source":           source,
        "side":             side,
        "isin":             isin,
        "institution":      institution_key.upper(),
        "institution_key":  institution_key,
        "transaction_type": transaction_type,
        "quantity":         quantity,
        "amount":           Decimal("0.00"),
        "trade_date":       trade_date,
        "settlement_date":  settlement_date or trade_date,
        "currency":         "NOK",
        "native_reference": transaction_id,
        "raw_payload":      json.dumps(payload),
        "ingested_at":      dt.datetime(2026, 4, 24, 12, 0),
        "match_status":     "unmatched",
        "matched_with":     None,
        "is_active":        True,
    }


def _ingest(repo: Repository, rows: list[dict]) -> None:
    df = pl.DataFrame(rows, schema=CANONICAL_SCHEMA)
    repo.upsert_transactions(df)


def _match_count(repo: Repository, status: str | None = None,
                 partial_reason: str | None = None) -> int:
    sql = "SELECT COUNT(*) FROM matches WHERE 1=1"
    params: list = []
    if status is not None:
        sql += " AND status = ?"; params.append(status)
    if partial_reason is not None:
        sql += " AND partial_reason = ?"; params.append(partial_reason)
    return repo.db.execute(sql, params).fetchone()[0]


# ============================================================================
# Auto-match: Subscription, Redemption — basic cases
# ============================================================================

def test_basic_subscription_auto_match(repo):
    _ingest(repo, [
        _row(transaction_id="g1", source="genus", side="internal",
             transaction_type="Subscription", quantity=Decimal("100.000000")),
        _row(transaction_id="a1", source="allfunds", side="external",
             transaction_type="Subscription", quantity=Decimal("100.000000")),
    ])
    result = run_matching(repo)
    assert result.auto_matched == 1
    assert _match_count(repo, "auto_matched") == 1


def test_basic_redemption_auto_match_with_negative_qty(repo):
    _ingest(repo, [
        _row(transaction_id="g1", source="genus", side="internal",
             transaction_type="Redemption", quantity=Decimal("-50.000000")),
        _row(transaction_id="a1", source="allfunds", side="external",
             transaction_type="Redemption", quantity=Decimal("-50.000000")),
    ])
    result = run_matching(repo)
    assert result.auto_matched == 1


def test_subscription_does_not_match_redemption_even_if_qty_signs_align(repo):
    """Type must match exactly — Sub vs Red never auto-match even with
    sign-aware qty. (Sanity check that signed-qty doesn't bypass type check.)"""
    _ingest(repo, [
        _row(transaction_id="g1", source="genus", side="internal",
             transaction_type="Subscription", quantity=Decimal("100.000000")),
        _row(transaction_id="a1", source="allfunds", side="external",
             transaction_type="Redemption", quantity=Decimal("100.000000")),
    ])
    result = run_matching(repo)
    assert result.auto_matched == 0
    assert _match_count(repo) == 0


# ============================================================================
# Quantity tolerance ±0.0001
# ============================================================================

def test_quantity_within_tolerance_matches(repo):
    _ingest(repo, [
        _row(transaction_id="g1", source="genus", side="internal",
             quantity=Decimal("100.000000")),
        _row(transaction_id="a1", source="allfunds", side="external",
             quantity=Decimal("100.000099")),   # +0.000099 < 0.0001
    ])
    assert run_matching(repo).auto_matched == 1


def test_quantity_at_tolerance_boundary_matches(repo):
    _ingest(repo, [
        _row(transaction_id="g1", source="genus", side="internal",
             quantity=Decimal("100.000000")),
        _row(transaction_id="a1", source="allfunds", side="external",
             quantity=Decimal("100.000100")),  # exactly 0.0001 — inclusive
    ])
    assert run_matching(repo).auto_matched == 1


def test_quantity_just_outside_tolerance_does_not_match(repo):
    _ingest(repo, [
        _row(transaction_id="g1", source="genus", side="internal",
             quantity=Decimal("100.000000")),
        _row(transaction_id="a1", source="allfunds", side="external",
             quantity=Decimal("100.000200")),   # 0.0002 > 0.0001
    ])
    result = run_matching(repo)
    assert result.auto_matched == 0


def test_quantity_differs_is_unmatched_not_partial(repo):
    """Clarification C: quantity-differs is NEVER partial. It's unmatched."""
    _ingest(repo, [
        _row(transaction_id="g1", source="genus", side="internal",
             quantity=Decimal("100.000000")),
        _row(transaction_id="a1", source="allfunds", side="external",
             quantity=Decimal("99.000000")),
    ])
    run_matching(repo)
    assert _match_count(repo) == 0     # not auto, not partial


# ============================================================================
# Partial matches: institution_diff and date_diff
# ============================================================================

def test_partial_institution_diff(repo):
    """ISIN + type + qty + date agree; institution differs -> partial."""
    _ingest(repo, [
        _row(transaction_id="g1", source="genus", side="internal",
             institution_key="sb1_smn"),
        _row(transaction_id="a1", source="allfunds", side="external",
             institution_key="sb1_nordmore"),
    ])
    run_matching(repo)
    assert _match_count(repo, "partial", "institution_diff") == 1
    assert _match_count(repo, "auto_matched") == 0


def test_partial_date_diff(repo):
    """ISIN + type + qty + institution agree; date differs -> partial."""
    _ingest(repo, [
        _row(transaction_id="g1", source="genus", side="internal",
             trade_date=dt.date(2026, 4, 24)),
        _row(transaction_id="a1", source="allfunds", side="external",
             trade_date=dt.date(2026, 4, 25)),
    ])
    run_matching(repo)
    assert _match_count(repo, "partial", "date_diff") == 1


def test_qty_diff_does_not_create_partial_even_if_inst_and_date_agree(repo):
    """Reaffirms clarification C: qty must match for any partial."""
    _ingest(repo, [
        _row(transaction_id="g1", source="genus", side="internal",
             quantity=Decimal("100.000000")),
        _row(transaction_id="a1", source="allfunds", side="external",
             quantity=Decimal("99.000000")),
    ])
    run_matching(repo)
    assert _match_count(repo) == 0


# ============================================================================
# 1:1 cardinality enforcement
# ============================================================================

def test_one_to_one_pairing_with_two_candidates_each_side(repo):
    """Two internal + two external all on identical key -> 2 matches, not 4."""
    _ingest(repo, [
        _row(transaction_id="g1", source="genus", side="internal"),
        _row(transaction_id="g2", source="genus", side="internal"),
        _row(transaction_id="a1", source="allfunds", side="external"),
        _row(transaction_id="a2", source="allfunds", side="external"),
    ])
    result = run_matching(repo)
    assert result.auto_matched == 2
    # Each transaction is on exactly one match row (1:1).
    rs = repo.db.execute("""
        SELECT internal_id, external_id FROM matches WHERE status='auto_matched'
    """).fetchall()
    internals = {r[0] for r in rs}
    externals = {r[1] for r in rs}
    assert internals == {"g1", "g2"}
    assert externals == {"a1", "a2"}


# ============================================================================
# Transfer types: ±30 day settlement window
# ============================================================================

def test_transfer_within_30_days_matches(repo):
    _ingest(repo, [
        _row(transaction_id="g1", source="genus", side="internal",
             transaction_type="Transfer (Subscription)",
             trade_date=dt.date(2026, 4, 1),
             settlement_date=dt.date(2026, 4, 1)),
        _row(transaction_id="a1", source="allfunds", side="external",
             transaction_type="Transfer (Subscription)",
             trade_date=dt.date(2026, 4, 25),
             settlement_date=dt.date(2026, 4, 25)),  # 24 days later
    ])
    assert run_matching(repo).auto_matched == 1


def test_transfer_outside_30_days_does_not_match(repo):
    _ingest(repo, [
        _row(transaction_id="g1", source="genus", side="internal",
             transaction_type="Transfer (Subscription)",
             trade_date=dt.date(2026, 4, 1),
             settlement_date=dt.date(2026, 4, 1)),
        _row(transaction_id="a1", source="allfunds", side="external",
             transaction_type="Transfer (Subscription)",
             trade_date=dt.date(2026, 5, 5),
             settlement_date=dt.date(2026, 5, 5)),   # 34 days later
    ])
    assert run_matching(repo).auto_matched == 0


# ============================================================================
# Source routing: Allfunds eligibility (TradeDomain) — clarifications #5/#7
# ============================================================================

def test_internal_domain_genus_is_NOT_matched_against_allfunds(repo):
    _ingest(repo, [
        _row(transaction_id="g1", source="genus", side="internal",
             trade_domain="Internal"),
        _row(transaction_id="a1", source="allfunds", side="external"),
    ])
    assert run_matching(repo).auto_matched == 0


def test_external_domain_genus_IS_matched_against_allfunds(repo):
    _ingest(repo, [
        _row(transaction_id="g1", source="genus", side="internal",
             trade_domain="External"),
        _row(transaction_id="a1", source="allfunds", side="external"),
    ])
    assert run_matching(repo).auto_matched == 1


def test_between_sb1_banks_genus_IS_matched_against_allfunds(repo):
    _ingest(repo, [
        _row(transaction_id="g1", source="genus", side="internal",
             trade_domain="Between SB1 Banks"),
        _row(transaction_id="a1", source="allfunds", side="external"),
    ])
    assert run_matching(repo).auto_matched == 1


def test_distribution_cost_internal_domain_IS_matched_against_allfunds(repo):
    """Clarification #5: Distribution Cost is the EXCEPTION to the Internal-
    domain filter. After collapse it's a Redemption, but the original
    TransactionType in raw_payload is what allows the routing."""
    _ingest(repo, [
        _row(transaction_id="g1", source="genus", side="internal",
             trade_domain="Internal",
             transaction_type="Redemption",       # post-collapse
             native_type="Distribution Cost",     # pre-collapse, in payload
             quantity=Decimal("-100.000000")),
        _row(transaction_id="a1", source="allfunds", side="external",
             transaction_type="Redemption",
             quantity=Decimal("-100.000000")),
    ])
    assert run_matching(repo).auto_matched == 1


def test_internal_domain_genus_IS_matched_against_odin_no_filter(repo):
    """Clarification #7: NO TradeDomain filter for ODIN. Internal-domain
    Genus rows still attempt ODIN match."""
    _ingest(repo, [
        _row(transaction_id="g1", source="genus", side="internal",
             trade_domain="Internal"),
        _row(transaction_id="o1", source="odin", side="external"),
    ])
    assert run_matching(repo).auto_matched == 1


# ============================================================================
# Roll-forward: re-running matching is idempotent and rolls in new rows
# ============================================================================

def test_rerunning_matching_is_idempotent(repo):
    """Same state, run twice — should not duplicate matches."""
    _ingest(repo, [
        _row(transaction_id="g1", source="genus", side="internal"),
        _row(transaction_id="a1", source="allfunds", side="external"),
    ])
    run_matching(repo)
    n1 = _match_count(repo)
    run_matching(repo)
    n2 = _match_count(repo)
    assert n1 == n2 == 1


def test_yesterdays_unmatched_match_against_todays_external(repo):
    """Roll-forward scenario: ingest internal day 1 (no match), then ingest
    external day 2 with same key. Second run should match them."""
    # Day 1
    _ingest(repo, [
        _row(transaction_id="g1", source="genus", side="internal"),
    ])
    run_matching(repo)
    assert _match_count(repo) == 0   # nothing to match against

    # Day 2: external arrives, re-run
    _ingest(repo, [
        _row(transaction_id="a1", source="allfunds", side="external"),
    ])
    run_matching(repo)
    assert _match_count(repo, "auto_matched") == 1


# ============================================================================
# Counts / status views
# ============================================================================

def test_unmatched_pool_view_excludes_matched_rows(repo):
    _ingest(repo, [
        _row(transaction_id="g1", source="genus", side="internal"),
        _row(transaction_id="a1", source="allfunds", side="external"),
        _row(transaction_id="g2", source="genus", side="internal",
             isin="NO0010000002"),    # no external partner
    ])
    run_matching(repo)
    pool = repo.fetch_unmatched()
    pool_ids = set(pool["transaction_id"].to_list())
    assert "g1" not in pool_ids
    assert "a1" not in pool_ids
    assert "g2" in pool_ids
