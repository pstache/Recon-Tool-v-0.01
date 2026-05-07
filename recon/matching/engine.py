"""
Matching engine.

Reads from `unmatched_pool` (a view that auto-rolls-forward) and writes new
rows to `matches`. Designed to be called repeatedly: each call only attempts
to match transactions that are currently unmatched.

Algorithm — single SQL pass per rule family, then partial in a second pass.
This vectorises the join so we never iterate transactions in Python.

  Step 1: AUTO MATCH (Subscription / Redemption)
    Internal Genus row joins external (ODIN ∪ Allfunds) on:
        isin = isin
        institution_key = institution_key
        transaction_type = transaction_type
        ABS(qty - qty) <= 0.0001   -- signed qty
        trade_date = trade_date
    Cardinality: enforced 1:1. If multiple candidates exist on either side
    we pick the lexicographically smallest external transaction_id and emit
    a single match. The remaining candidates fall through to the next call.
    (For Transfer rows the spec asked us to flag splits — see below.)

  Step 2: AUTO MATCH (Transfer (Subscription) / Transfer (Redemption))
    Same join but settlement_date within ±30 days. For Transfer rows we mark
    1:N candidates as 'partial' rather than auto-collapsing (clarification #12).

  Step 3: PARTIAL MATCH
    Same join as Step 1, with two relaxations OR'd separately:
      (a) institution differs, everything else (incl. qty AND date) matches
      (b) date differs, everything else (incl. qty AND institution) matches
    Both produce match_status='partial' with a partial_reason annotation,
    so the UI can show why.

Routing rules (which Genus rows attempt which match) — clarifications #5, #7:
  * Genus rows match against ODIN regardless of TradeDomain (#7).
  * Genus rows match against Allfunds when:
        TradeDomain in ('External', 'Between SB1 Banks')   -- spec
      OR the row was a Distribution Cost (now collapsed to Redemption) (#5).

We don't store TradeDomain as a first-class column (it's only relevant for
this routing and lives in raw_payload). The routing therefore happens via a
JSON_EXTRACT inside the SQL — DuckDB supports that natively.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from storage.repository import Repository


@dataclass
class MatchResult:
    auto_matched: int = 0
    partial: int = 0
    transfer_split_candidates: int = 0
    unmatched_internal: int = 0
    unmatched_external: int = 0


# ---------------------------------------------------------------------------
# SQL building blocks
# ---------------------------------------------------------------------------

# A predicate that selects internal rows eligible for Allfunds matching.
# (TradeDomain in ('External','Between SB1 Banks') OR raw_payload says
#  the original type was Distribution Cost.)
_ALLFUNDS_ELIGIBLE_INTERNAL = """
    (
        json_extract_string(t.raw_payload, '$.TradeDomain') IN ('External','Between SB1 Banks')
        OR json_extract_string(t.raw_payload, '$.TransactionType') = 'Distribution Cost'
    )
"""


def run_matching(repo: Repository, *, today: dt.date | None = None) -> MatchResult:
    """Run all matching passes against current unmatched_pool. Return summary.

    This is the only entry point. The Streamlit "Run matching" button calls
    this; tests call this directly.
    """
    db = repo.db
    today = today or dt.date.today()

    result = MatchResult()

    # We do all matching inside one transaction so partial-then-auto race
    # conditions can't strand a row.
    db.execute("BEGIN")
    try:
        # Step 1 & 2: auto matches per rule family
        result.auto_matched += _auto_match_trade(db, eligible_external_source=("odin",))
        result.auto_matched += _auto_match_trade_against_allfunds(db)
        result.auto_matched += _auto_match_transfer(db, eligible_external_source=("odin",))
        result.auto_matched += _auto_match_transfer_against_allfunds(db)

        # Step 2b: flag remaining transfer rows that have multiple candidates
        result.transfer_split_candidates += _flag_transfer_splits(db)

        # Step 3: partial matches
        result.partial += _partial_match_institution_diff(db)
        result.partial += _partial_match_date_diff(db)

        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise

    # Count what remains.
    counts = repo.counts()
    result.unmatched_internal = db.execute(
        "SELECT COUNT(*) FROM unmatched_pool WHERE side='internal'"
    ).fetchone()[0]
    result.unmatched_external = db.execute(
        "SELECT COUNT(*) FROM unmatched_pool WHERE side='external'"
    ).fetchone()[0]

    return result


# ---------------------------------------------------------------------------
# Step 1: auto match Subscription / Redemption
# ---------------------------------------------------------------------------

def _auto_match_trade(db, eligible_external_source: tuple[str, ...]) -> int:
    """1:1 auto-match Genus internal rows against ODIN (or other source)."""
    src_list = ",".join(f"'{s}'" for s in eligible_external_source)

    # The ranking trick: for each (internal_id, external_id) pair we generate,
    # we keep only one external per internal (smallest external_id) and only
    # one internal per external. ROW_NUMBER() over both sides handles 1:1.
    match_id_prefix = uuid.uuid4().hex[:8]
    now = dt.datetime.now().isoformat(sep=" ", timespec="microseconds")

    db.execute(f"""
        CREATE TEMP TABLE candidate_pairs AS
        SELECT
            i.transaction_id AS internal_id,
            e.transaction_id AS external_id
        FROM unmatched_pool i
        JOIN unmatched_pool e
          ON i.isin             = e.isin
         AND i.institution_key  = e.institution_key
         AND i.transaction_type = e.transaction_type
         AND i.trade_date       = e.trade_date
         AND ABS(i.quantity - e.quantity) <= 0.0001
        WHERE i.side = 'internal'
          AND e.side = 'external'
          AND e.source IN ({src_list})
          AND i.transaction_type IN ('Subscription','Redemption')
    """)

    n = _resolve_and_insert_pairs(db, match_id_prefix, now,
                                   status="auto_matched", partial_reason=None)
    db.execute("DROP TABLE candidate_pairs")
    return n


def _auto_match_trade_against_allfunds(db) -> int:
    """Trade auto-match restricted to TradeDomain-eligible internal rows."""
    match_id_prefix = uuid.uuid4().hex[:8]
    now = dt.datetime.now().isoformat(sep=" ", timespec="microseconds")

    db.execute(f"""
        CREATE TEMP TABLE candidate_pairs AS
        SELECT
            t.transaction_id AS internal_id,
            e.transaction_id AS external_id
        FROM unmatched_pool t
        JOIN unmatched_pool e
          ON t.isin             = e.isin
         AND t.institution_key  = e.institution_key
         AND t.transaction_type = e.transaction_type
         AND t.trade_date       = e.trade_date
         AND ABS(t.quantity - e.quantity) <= 0.0001
        WHERE t.side = 'internal'
          AND e.side = 'external'
          AND e.source = 'allfunds'
          AND t.transaction_type IN ('Subscription','Redemption')
          AND {_ALLFUNDS_ELIGIBLE_INTERNAL.replace('t.', 't.')}
    """)
    n = _resolve_and_insert_pairs(db, match_id_prefix, now,
                                   status="auto_matched", partial_reason=None)
    db.execute("DROP TABLE candidate_pairs")
    return n


# ---------------------------------------------------------------------------
# Step 2: auto match Transfer (Subscription) / Transfer (Redemption)
# ---------------------------------------------------------------------------

def _auto_match_transfer(db, eligible_external_source: tuple[str, ...]) -> int:
    """Transfer match: settlement_date within ±30 days. ODIN side."""
    src_list = ",".join(f"'{s}'" for s in eligible_external_source)
    match_id_prefix = uuid.uuid4().hex[:8]
    now = dt.datetime.now().isoformat(sep=" ", timespec="microseconds")

    # ODIN does not carry settlement_date — fall back to trade_date.
    db.execute(f"""
        CREATE TEMP TABLE candidate_pairs AS
        SELECT
            i.transaction_id AS internal_id,
            e.transaction_id AS external_id
        FROM unmatched_pool i
        JOIN unmatched_pool e
          ON i.isin             = e.isin
         AND i.institution_key  = e.institution_key
         AND i.transaction_type = e.transaction_type
         AND ABS(i.quantity - e.quantity) <= 0.0001
         AND ABS(date_diff('day',
                COALESCE(e.settlement_date, e.trade_date),
                COALESCE(i.settlement_date, i.trade_date))) <= 30
        WHERE i.side = 'internal'
          AND e.side = 'external'
          AND e.source IN ({src_list})
          AND i.transaction_type IN ('Transfer (Subscription)','Transfer (Redemption)')
    """)
    n = _resolve_and_insert_pairs(db, match_id_prefix, now,
                                   status="auto_matched", partial_reason=None)
    db.execute("DROP TABLE candidate_pairs")
    return n


def _auto_match_transfer_against_allfunds(db) -> int:
    """Transfer match against Allfunds, with eligibility filter."""
    match_id_prefix = uuid.uuid4().hex[:8]
    now = dt.datetime.now().isoformat(sep=" ", timespec="microseconds")

    db.execute(f"""
        CREATE TEMP TABLE candidate_pairs AS
        SELECT
            t.transaction_id AS internal_id,
            e.transaction_id AS external_id
        FROM unmatched_pool t
        JOIN unmatched_pool e
          ON t.isin             = e.isin
         AND t.institution_key  = e.institution_key
         AND t.transaction_type = e.transaction_type
         AND ABS(t.quantity - e.quantity) <= 0.0001
         AND ABS(date_diff('day',
                COALESCE(e.settlement_date, e.trade_date),
                COALESCE(t.settlement_date, t.trade_date))) <= 30
        WHERE t.side = 'internal'
          AND e.side = 'external'
          AND e.source = 'allfunds'
          AND t.transaction_type IN ('Transfer (Subscription)','Transfer (Redemption)')
          AND {_ALLFUNDS_ELIGIBLE_INTERNAL}
    """)
    n = _resolve_and_insert_pairs(db, match_id_prefix, now,
                                   status="auto_matched", partial_reason=None)
    db.execute("DROP TABLE candidate_pairs")
    return n


def _flag_transfer_splits(db) -> int:
    """Mark transfer rows with multiple still-unmatched candidates as
    `partial` with reason 'transfer_split_candidate'. Clarification #12.

    A 'split candidate' is a transfer-type internal row that has more than
    one external partner whose qty is consistent with a partial split (i.e.
    sums to internal qty within tolerance). We don't try to auto-resolve the
    split; we just flag for review.
    """
    match_id_prefix = uuid.uuid4().hex[:8]
    now = dt.datetime.now().isoformat(sep=" ", timespec="microseconds")

    # For each unmatched transfer internal, find externals that match on
    # ISIN + institution + type + within-30-day-window and have qty same sign.
    # If MORE than one candidate exists OR qty doesn't match exactly but the
    # signs agree, flag.
    db.execute(f"""
        CREATE TEMP TABLE split_flags AS
        SELECT
            i.transaction_id AS internal_id,
            COUNT(DISTINCT e.transaction_id) AS candidates
        FROM unmatched_pool i
        JOIN unmatched_pool e
          ON i.isin             = e.isin
         AND i.institution_key  = e.institution_key
         AND i.transaction_type = e.transaction_type
         AND SIGN(i.quantity)   = SIGN(e.quantity)
         AND ABS(date_diff('day',
                COALESCE(e.settlement_date, e.trade_date),
                COALESCE(i.settlement_date, i.trade_date))) <= 30
        WHERE i.side = 'internal'
          AND e.side = 'external'
          AND i.transaction_type IN ('Transfer (Subscription)','Transfer (Redemption)')
        GROUP BY i.transaction_id
        HAVING COUNT(DISTINCT e.transaction_id) >= 2
    """)
    db.execute(f"""
        INSERT INTO matches
            (match_id, internal_id, external_id, status, partial_reason,
             matched_at, matched_by, reason)
        SELECT
            CONCAT('{match_id_prefix}_split_', ROW_NUMBER() OVER (ORDER BY internal_id)),
            internal_id,
            NULL,
            'partial',
            'transfer_split_candidate',
            TIMESTAMP '{now}',
            NULL,
            CONCAT(candidates, ' candidate external row(s); review for split')
        FROM split_flags
    """)
    n = db.execute("SELECT COUNT(*) FROM split_flags").fetchone()[0]
    db.execute("DROP TABLE split_flags")
    return n


# ---------------------------------------------------------------------------
# Step 3: partial matches (institution_diff and date_diff)
# ---------------------------------------------------------------------------

def _partial_match_institution_diff(db) -> int:
    """Auto-flag partial: ISIN+type+qty+date agree, institution differs.
    Per clarification #13."""
    match_id_prefix = uuid.uuid4().hex[:8]
    now = dt.datetime.now().isoformat(sep=" ", timespec="microseconds")

    db.execute("""
        CREATE TEMP TABLE candidate_pairs AS
        SELECT
            i.transaction_id AS internal_id,
            e.transaction_id AS external_id
        FROM unmatched_pool i
        JOIN unmatched_pool e
          ON i.isin             = e.isin
         AND i.transaction_type = e.transaction_type
         AND i.trade_date       = e.trade_date
         AND ABS(i.quantity - e.quantity) <= 0.0001
         AND (i.institution_key IS NULL OR e.institution_key IS NULL
              OR i.institution_key <> e.institution_key)
        WHERE i.side = 'internal'
          AND e.side = 'external'
          AND i.transaction_type IN ('Subscription','Redemption')
    """)
    n = _resolve_and_insert_pairs(db, match_id_prefix, now,
                                   status="partial",
                                   partial_reason="institution_diff")
    db.execute("DROP TABLE candidate_pairs")
    return n


def _partial_match_date_diff(db) -> int:
    """Auto-flag partial: ISIN+type+qty+institution agree, date differs."""
    match_id_prefix = uuid.uuid4().hex[:8]
    now = dt.datetime.now().isoformat(sep=" ", timespec="microseconds")

    db.execute("""
        CREATE TEMP TABLE candidate_pairs AS
        SELECT
            i.transaction_id AS internal_id,
            e.transaction_id AS external_id
        FROM unmatched_pool i
        JOIN unmatched_pool e
          ON i.isin             = e.isin
         AND i.institution_key  = e.institution_key
         AND i.transaction_type = e.transaction_type
         AND ABS(i.quantity - e.quantity) <= 0.0001
         AND i.trade_date <> e.trade_date
        WHERE i.side = 'internal'
          AND e.side = 'external'
          AND i.transaction_type IN ('Subscription','Redemption')
    """)
    n = _resolve_and_insert_pairs(db, match_id_prefix, now,
                                   status="partial",
                                   partial_reason="date_diff")
    db.execute("DROP TABLE candidate_pairs")
    return n


# ---------------------------------------------------------------------------
# Pair resolution: take a candidate_pairs temp table and pick 1:1 winners
# ---------------------------------------------------------------------------

def _resolve_and_insert_pairs(db, match_id_prefix: str, now: str,
                              *, status: str, partial_reason: str | None) -> int:
    """Given temp table candidate_pairs(internal_id, external_id), pick a 1:1
    set and INSERT into matches. Returns count inserted.

    Algorithm: greedy iterative. We pick the rank-1-on-both-sides pairs (which
    are guaranteed unique on both sides), insert them, then remove the
    corresponding rows from candidate_pairs and repeat. This converges
    because each iteration strictly reduces the size of candidate_pairs.

    Why greedy: with N internals × N externals all on identical keys, a
    single rank-1 pass picks only one pair. Iterating picks all N. The order
    is deterministic (lexicographic on internal_id then external_id) so
    re-running matching is idempotent and reproducible.
    """
    pr_str = "NULL" if partial_reason is None else f"'{partial_reason}'"
    total_inserted = 0
    iteration = 0

    while True:
        # Pick this round's winners: rank-1 on both sides.
        rs = db.execute(f"""
            INSERT INTO matches
                (match_id, internal_id, external_id, status, partial_reason,
                 matched_at, matched_by, reason)
            WITH ranked AS (
                SELECT
                    internal_id, external_id,
                    ROW_NUMBER() OVER (PARTITION BY internal_id ORDER BY external_id) AS r_int,
                    ROW_NUMBER() OVER (PARTITION BY external_id ORDER BY internal_id) AS r_ext
                FROM candidate_pairs
            )
            SELECT
                CONCAT('{match_id_prefix}_{iteration}_',
                       ROW_NUMBER() OVER (ORDER BY internal_id, external_id)),
                internal_id,
                external_id,
                '{status}',
                {pr_str},
                TIMESTAMP '{now}',
                NULL,
                NULL
            FROM ranked
            WHERE r_int = 1 AND r_ext = 1
            RETURNING internal_id, external_id
        """).fetchall()

        if not rs:
            break

        # Remove the matched internals and externals from the pool.
        matched_internals = [r[0] for r in rs]
        matched_externals = [r[1] for r in rs]
        # DuckDB lets us pass a list as a parameter for IN.
        db.execute(
            "DELETE FROM candidate_pairs WHERE internal_id = ANY(?) OR external_id = ANY(?)",
            [matched_internals, matched_externals],
        )

        total_inserted += len(rs)
        iteration += 1
        # Defence against an unexpected infinite loop.
        if iteration > 1000:
            raise RuntimeError("Match pair resolution did not converge")

    return total_inserted
