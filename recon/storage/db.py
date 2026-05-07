"""
DuckDB connection + schema management.

Why DuckDB and not SQLite (per spec's open question):
  * Polars-native: we can register a DataFrame directly without a row-by-row
    insert loop, which matters at hundreds of thousands of rows.
  * Decimal type support without the precision loss SQLite forces (it stores
    everything as REAL).
  * Embedded, single-file, no daemon — same operational profile as SQLite.

Schema design notes:
  * `transactions_all` is the immutable canonical store. Once a row is in,
    it's never updated in place — match status is derived from `matches`.
  * `matches` carries the (internal_id, external_id, status, audit) record.
    A row appears at most once on each side (1:1 cardinality, except for
    Transfer rows where the spec allows splits — flagged via match_status).
  * `unmatched_pool` is a VIEW, not a table. This is the roll-forward trick:
    the daily run reads from this view, so any transaction that hasn't been
    matched yet — including those from prior days — is automatically in scope.
    Adding a new transaction or a new match changes the view's contents
    without any explicit roll-forward step.
  * `parse_errors` keeps every line we couldn't parse, with the raw payload,
    so nothing is silently dropped (per spec).
  * `ingested_files` records every file we've processed, by SHA-256 of
    contents AND by filename, so we can give a hard error on exact-file
    re-uploads (per clarification #11) while still silently deduping at the
    transaction level when the same transactions arrive in a differently-
    named file.
  * `manual_match_log` is the audit trail (per clarifications #6, #11).
  * `institution_overrides` is the UI-editable institution map (per #21).

Indexes are critical for matching at hundreds of thousands of rows. We index
both the (isin, institution_key, trade_date) and the
(isin, institution_key, settlement_date) triples since Subscription/Redemption
matches on trade_date and Transfer matches on settlement_date.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

import duckdb


def default_db_path() -> Path:
    """Default database path, override with $RECON_DB env var."""
    p = os.environ.get("RECON_DB")
    if p:
        return Path(p)
    here = Path(__file__).resolve().parent.parent
    return here / "data" / "recon.duckdb"


class DB:
    """Thin wrapper around a duckdb connection. Owns schema migrations.

    Use as a context manager:
        with DB() as db:
            db.execute("SELECT 1")

    Or persistently:
        db = DB()
        db.connect()
        ...
        db.close()
    """

    SCHEMA_VERSION = 1

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: duckdb.DuckDBPyConnection | None = None

    def __enter__(self) -> "DB":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @property
    def conn(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            raise RuntimeError("DB.connect() not called")
        return self._conn

    def connect(self) -> None:
        if self._conn is not None:
            return
        self._conn = duckdb.connect(str(self.path))
        self._migrate()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def execute(self, sql: str, params: list | tuple | None = None):
        return self.conn.execute(sql, params or [])

    def _migrate(self) -> None:
        """Apply DDL idempotently. Safe to call on every connect."""
        c = self._conn
        assert c is not None
        c.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)")
        # The transactions store. Decimal types use Polars-compatible widths.
        c.execute("""
            CREATE TABLE IF NOT EXISTS transactions_all (
                transaction_id   VARCHAR PRIMARY KEY,
                source           VARCHAR NOT NULL,
                side             VARCHAR NOT NULL,
                isin             VARCHAR NOT NULL,
                institution      VARCHAR,
                institution_key  VARCHAR,
                transaction_type VARCHAR NOT NULL,
                quantity         DECIMAL(20, 6) NOT NULL,
                amount           DECIMAL(20, 2),
                trade_date       DATE,
                settlement_date  DATE,
                currency         VARCHAR,
                native_reference VARCHAR,
                raw_payload      VARCHAR,
                ingested_at      TIMESTAMP NOT NULL,
                is_active        BOOLEAN NOT NULL DEFAULT TRUE,
                archived_at      TIMESTAMP            -- set when auto-archived after 180 days
            )
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_tx_trade
                ON transactions_all (isin, institution_key, trade_date)
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_tx_settlement
                ON transactions_all (isin, institution_key, settlement_date)
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_tx_source_active
                ON transactions_all (source, is_active)
        """)
        # The matches table — one row per matched pair, or per known-unmatchable
        # row (in which case external_id is null).
        c.execute("""
            CREATE TABLE IF NOT EXISTS matches (
                match_id      VARCHAR PRIMARY KEY,
                internal_id   VARCHAR NOT NULL,
                external_id   VARCHAR,                 -- null = known_unmatchable
                status        VARCHAR NOT NULL,        -- auto_matched | manual_matched | partial | known_unmatchable
                partial_reason VARCHAR,                 -- if status=partial: 'institution_diff' | 'date_diff'
                matched_at    TIMESTAMP NOT NULL,
                matched_by    VARCHAR,                 -- user id (null = automatic)
                reason        VARCHAR,                 -- free-text justification (manual only)
                FOREIGN KEY (internal_id) REFERENCES transactions_all(transaction_id),
                FOREIGN KEY (external_id) REFERENCES transactions_all(transaction_id)
            )
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_match_internal ON matches (internal_id)
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_match_external ON matches (external_id)
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_match_status ON matches (status)
        """)
        # The unmatched-pool view: any active transaction not referenced
        # in any matches row. This is the roll-forward.
        c.execute("""
            CREATE OR REPLACE VIEW unmatched_pool AS
            SELECT t.*
            FROM   transactions_all t
            WHERE  t.is_active = TRUE
              AND  NOT EXISTS (
                  SELECT 1 FROM matches m
                  WHERE  m.internal_id = t.transaction_id
                     OR  m.external_id = t.transaction_id
              )
        """)
        # Convenience view: every transaction with its current status,
        # so the UI can filter on "matched_with" without reasoning about joins.
        c.execute("""
            CREATE OR REPLACE VIEW transactions_with_status AS
            SELECT
                t.*,
                COALESCE(m_int.status, m_ext.status, 'unmatched') AS match_status,
                COALESCE(m_int.external_id, m_ext.internal_id) AS matched_with,
                COALESCE(m_int.partial_reason, m_ext.partial_reason) AS partial_reason
            FROM transactions_all t
            LEFT JOIN matches m_int ON t.transaction_id = m_int.internal_id
            LEFT JOIN matches m_ext ON t.transaction_id = m_ext.external_id
        """)
        # Parse-error log so nothing is silently lost.
        c.execute("""
            CREATE TABLE IF NOT EXISTS parse_errors (
                error_id    INTEGER PRIMARY KEY,
                source      VARCHAR NOT NULL,
                source_file VARCHAR NOT NULL,
                line_number INTEGER,
                raw_line    VARCHAR,
                reason      VARCHAR,
                ingested_at TIMESTAMP NOT NULL
            )
        """)
        c.execute("CREATE SEQUENCE IF NOT EXISTS parse_errors_seq START 1")
        # File-level idempotency log (clarification #11).
        c.execute("""
            CREATE TABLE IF NOT EXISTS ingested_files (
                file_hash    VARCHAR PRIMARY KEY,    -- SHA-256 of file contents
                file_name    VARCHAR NOT NULL,
                source       VARCHAR NOT NULL,
                row_count    INTEGER NOT NULL,
                ingested_at  TIMESTAMP NOT NULL
            )
        """)
        # Audit log for every manual user action.
        c.execute("""
            CREATE TABLE IF NOT EXISTS manual_match_log (
                log_id      INTEGER PRIMARY KEY,
                action      VARCHAR NOT NULL,        -- 'manual_match' | 'flag_unmatchable' | 'unmatch'
                internal_id VARCHAR,
                external_id VARCHAR,
                user_id     VARCHAR,
                reason      VARCHAR,
                acted_at    TIMESTAMP NOT NULL
            )
        """)
        c.execute("CREATE SEQUENCE IF NOT EXISTS manual_match_log_seq START 1")
        # UI-editable institution overrides (clarification #21). The matching
        # engine consults this table FIRST, falling back to the static map.
        c.execute("""
            CREATE TABLE IF NOT EXISTS institution_overrides (
                lookup_kind  VARCHAR NOT NULL,        -- 'name' | 'code'
                lookup_value VARCHAR NOT NULL,
                canonical_key VARCHAR NOT NULL,
                display_name VARCHAR,
                added_by     VARCHAR,
                added_at     TIMESTAMP NOT NULL,
                PRIMARY KEY (lookup_kind, lookup_value)
            )
        """)
        # Allfunds-offset overrides — also UI-editable, for if Allfunds shifts
        # the layout again (we've been bitten once).
        c.execute("""
            CREATE TABLE IF NOT EXISTS allfunds_offset_overrides (
                field_name VARCHAR PRIMARY KEY,
                offset_pos INTEGER NOT NULL,
                length     INTEGER NOT NULL,
                divisor    INTEGER NOT NULL DEFAULT 1,
                updated_by VARCHAR,
                updated_at TIMESTAMP NOT NULL
            )
        """)
        # Mark schema version.
        existing = c.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
        if existing == 0:
            c.execute("INSERT INTO schema_version VALUES (?)", [self.SCHEMA_VERSION])
