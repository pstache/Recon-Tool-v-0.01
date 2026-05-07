"""
Repository layer: high-level operations over the DB.

Everything that touches the database lives here. The Streamlit app, the
matching engine, and the report generator all go through Repository — they
never speak SQL directly. This keeps the schema changeable without rewiring
the world.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from pathlib import Path
from typing import Any

import polars as pl

from storage.db import DB

ARCHIVE_AGE_DAYS = 180   # Clarification #14


class FileAlreadyIngestedError(Exception):
    """Raised when an exact-bytes-identical file is uploaded a second time.
    (Per clarification #11 — hard error on filename+contents match.)"""


class Repository:
    """High-level operations over the recon DB."""

    def __init__(self, db: DB):
        self.db = db

    # ------------------------------------------------------------------ files

    @staticmethod
    def hash_file(path: str | Path) -> str:
        """SHA-256 of file contents — used as the idempotency key."""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    def file_already_ingested(self, file_hash: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT file_name, source, row_count, ingested_at "
            "FROM ingested_files WHERE file_hash = ?",
            [file_hash],
        ).fetchone()
        if row is None:
            return None
        return {
            "file_name": row[0],
            "source": row[1],
            "row_count": row[2],
            "ingested_at": row[3],
        }

    def record_ingested_file(
        self, *, file_hash: str, file_name: str, source: str, row_count: int
    ) -> None:
        self.db.execute(
            "INSERT INTO ingested_files VALUES (?, ?, ?, ?, ?)",
            [file_hash, file_name, source, row_count, dt.datetime.now()],
        )

    # --------------------------------------------------------- transactions

    def upsert_transactions(self, df: pl.DataFrame) -> tuple[int, int]:
        """Insert rows whose transaction_id is new; silently skip existing.
        Returns (inserted, skipped_dupes).

        Idempotent re-ingest at row level (clarification #11 second clause).
        """
        if df.is_empty():
            return (0, 0)

        # Get existing IDs once. At hundreds of thousands of rows this still
        # fits in memory; if it didn't we'd switch to a temp table + anti-join.
        ids = df.select("transaction_id").to_series().to_list()
        # DuckDB has a 1024-arg LIMIT_PARAMS quirk on prepared statements;
        # batch the existence check to be safe.
        existing: set[str] = set()
        BATCH = 4000
        for i in range(0, len(ids), BATCH):
            chunk = ids[i:i + BATCH]
            placeholders = ",".join("?" * len(chunk))
            rs = self.db.execute(
                f"SELECT transaction_id FROM transactions_all WHERE transaction_id IN ({placeholders})",
                chunk,
            ).fetchall()
            existing.update(r[0] for r in rs)

        if existing:
            new_df = df.filter(~pl.col("transaction_id").is_in(list(existing)))
        else:
            new_df = df

        skipped = df.height - new_df.height
        if new_df.is_empty():
            return (0, skipped)

        # DuckDB can register a Polars frame directly without going via pandas.
        # We deliberately drop match_status/matched_with from the frame because
        # those are derived from the matches table.
        write_df = new_df.drop(["match_status", "matched_with"])
        # Cast Decimal columns to explicit DuckDB-friendly precision via SQL,
        # avoiding pandas round-tripping that loses Decimal type info.
        self.db.conn.register("__insert_df", write_df)
        self.db.execute("""
            INSERT INTO transactions_all
                (transaction_id, source, side, isin, institution, institution_key,
                 transaction_type, quantity, amount, trade_date, settlement_date,
                 currency, native_reference, raw_payload, ingested_at, is_active,
                 archived_at)
            SELECT
                transaction_id, source, side, isin, institution, institution_key,
                transaction_type,
                CAST(quantity AS DECIMAL(20,6)),
                CAST(amount   AS DECIMAL(20,2)),
                trade_date, settlement_date,
                currency, native_reference, raw_payload, ingested_at, is_active,
                NULL
            FROM __insert_df
        """)
        self.db.conn.unregister("__insert_df")

        return (new_df.height, skipped)

    def record_parse_errors(
        self, source: str, source_file: str, errors: list[dict[str, Any]]
    ) -> None:
        if not errors:
            return
        now = dt.datetime.now()
        for e in errors:
            self.db.execute("""
                INSERT INTO parse_errors VALUES (
                    nextval('parse_errors_seq'), ?, ?, ?, ?, ?, ?
                )
            """, [
                source, source_file,
                e.get("line_number"), e.get("raw_line"), e.get("reason"),
                now,
            ])

    # ----------------------------------------------------- matched / unmatched

    def fetch_unmatched(self) -> pl.DataFrame:
        return self.db.execute("SELECT * FROM unmatched_pool").pl()

    def fetch_unmatched_by_side(self, side: str) -> pl.DataFrame:
        return self.db.execute(
            "SELECT * FROM unmatched_pool WHERE side = ?", [side]
        ).pl()

    def fetch_with_status(self, filters: dict[str, Any] | None = None) -> pl.DataFrame:
        """Return transactions_with_status, optionally filtered.

        filters keys (all optional):
            from_date, to_date  — on trade_date
            isin                — substring (uppercase prefix match)
            institution_key
            transaction_type
            source
            match_status        — 'unmatched' | 'auto_matched' | 'manual_matched' | 'partial' | 'known_unmatchable'
            side
        """
        sql = "SELECT * FROM transactions_with_status WHERE is_active = TRUE"
        params: list[Any] = []
        f = filters or {}
        if f.get("from_date"):
            sql += " AND trade_date >= ?"; params.append(f["from_date"])
        if f.get("to_date"):
            sql += " AND trade_date <= ?"; params.append(f["to_date"])
        if f.get("isin"):
            sql += " AND isin LIKE ?"; params.append(f["isin"].upper() + "%")
        if f.get("institution_key"):
            sql += " AND institution_key = ?"; params.append(f["institution_key"])
        if f.get("transaction_type"):
            sql += " AND transaction_type = ?"; params.append(f["transaction_type"])
        if f.get("source"):
            sql += " AND source = ?"; params.append(f["source"])
        if f.get("side"):
            sql += " AND side = ?"; params.append(f["side"])
        if f.get("match_status"):
            sql += " AND match_status = ?"; params.append(f["match_status"])
        sql += " ORDER BY trade_date DESC, transaction_id"
        return self.db.execute(sql, params).pl()

    def get_transaction(self, transaction_id: str) -> dict[str, Any] | None:
        rs = self.db.execute(
            "SELECT * FROM transactions_with_status WHERE transaction_id = ?",
            [transaction_id],
        ).fetchone()
        if rs is None:
            return None
        cols = [d[0] for d in self.db.conn.description]
        return dict(zip(cols, rs))

    # --------------------------------------------------------------- matches

    def insert_matches(self, matches: list[dict[str, Any]]) -> int:
        """Bulk insert. matches rows: {internal_id, external_id (or None),
        status, partial_reason (or None), matched_at, matched_by, reason}."""
        if not matches:
            return 0
        rows = []
        for m in matches:
            rows.append((
                m.get("match_id") or str(uuid.uuid4()),
                m["internal_id"],
                m.get("external_id"),
                m["status"],
                m.get("partial_reason"),
                m.get("matched_at") or dt.datetime.now(),
                m.get("matched_by"),
                m.get("reason"),
            ))
        self.db.conn.executemany(
            "INSERT INTO matches VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows
        )
        return len(rows)

    def manual_match(
        self, *, internal_id: str, external_id: str | None,
        user_id: str, reason: str, status: str = "manual_matched",
    ) -> str:
        """Mark a pair as manually matched. external_id may be None for
        'flag as known-unmatchable'."""
        match_id = str(uuid.uuid4())
        now = dt.datetime.now()
        self.db.execute(
            "INSERT INTO matches VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [match_id, internal_id, external_id, status, None, now, user_id, reason],
        )
        action = "flag_unmatchable" if external_id is None and status == "known_unmatchable" else "manual_match"
        self.db.execute("""
            INSERT INTO manual_match_log VALUES (
                nextval('manual_match_log_seq'), ?, ?, ?, ?, ?, ?
            )
        """, [action, internal_id, external_id, user_id, reason, now])
        return match_id

    def unmatch(self, *, match_id: str, user_id: str, reason: str) -> None:
        """Delete a match, restoring both sides to the unmatched pool."""
        m = self.db.execute(
            "SELECT internal_id, external_id FROM matches WHERE match_id = ?",
            [match_id],
        ).fetchone()
        if m is None:
            return
        self.db.execute("DELETE FROM matches WHERE match_id = ?", [match_id])
        self.db.execute("""
            INSERT INTO manual_match_log VALUES (
                nextval('manual_match_log_seq'), 'unmatch', ?, ?, ?, ?, ?
            )
        """, [m[0], m[1], user_id, reason, dt.datetime.now()])

    # ---------------------------------------------------------- audit / log

    def fetch_audit_log(self, limit: int = 500) -> pl.DataFrame:
        return self.db.execute(
            "SELECT * FROM manual_match_log ORDER BY acted_at DESC LIMIT ?",
            [limit],
        ).pl()

    def fetch_parse_errors(self) -> pl.DataFrame:
        return self.db.execute(
            "SELECT * FROM parse_errors ORDER BY ingested_at DESC"
        ).pl()

    # ------------------------------------------------------- archive (180 d)

    def archive_old_unmatched(self, today: dt.date | None = None) -> int:
        """Mark unmatched transactions older than ARCHIVE_AGE_DAYS as inactive.
        Returns number archived. Per clarification #14."""
        cutoff = (today or dt.date.today()) - dt.timedelta(days=ARCHIVE_AGE_DAYS)
        rs = self.db.execute("""
            UPDATE transactions_all
            SET    is_active = FALSE,
                   archived_at = ?
            WHERE  is_active = TRUE
              AND  trade_date < ?
              AND  transaction_id NOT IN (SELECT internal_id FROM matches)
              AND  transaction_id NOT IN (
                       SELECT external_id FROM matches WHERE external_id IS NOT NULL
                   )
            RETURNING transaction_id
        """, [dt.datetime.now(), cutoff]).fetchall()
        return len(rs)

    # ----------------------------------------------- institution overrides

    def get_institution_overrides(self) -> pl.DataFrame:
        return self.db.execute(
            "SELECT * FROM institution_overrides ORDER BY lookup_kind, lookup_value"
        ).pl()

    def upsert_institution_override(
        self, *, lookup_kind: str, lookup_value: str,
        canonical_key: str, display_name: str | None, added_by: str,
    ) -> None:
        if lookup_kind not in ("name", "code"):
            raise ValueError(f"lookup_kind must be 'name' or 'code', got {lookup_kind!r}")
        # DuckDB upsert via INSERT OR REPLACE / ON CONFLICT
        self.db.execute("""
            INSERT INTO institution_overrides VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (lookup_kind, lookup_value) DO UPDATE
              SET canonical_key = EXCLUDED.canonical_key,
                  display_name  = EXCLUDED.display_name,
                  added_by      = EXCLUDED.added_by,
                  added_at      = EXCLUDED.added_at
        """, [lookup_kind, lookup_value, canonical_key, display_name, added_by, dt.datetime.now()])

    def delete_institution_override(self, lookup_kind: str, lookup_value: str) -> None:
        self.db.execute(
            "DELETE FROM institution_overrides WHERE lookup_kind = ? AND lookup_value = ?",
            [lookup_kind, lookup_value],
        )

    # ----------------------------------------------- counts / dashboard

    def counts(self) -> dict[str, int]:
        """High-level counts for the upload-results summary."""
        c = self.db.execute("""
            SELECT
                (SELECT COUNT(*) FROM transactions_all WHERE is_active),
                (SELECT COUNT(*) FROM unmatched_pool),
                (SELECT COUNT(*) FROM matches WHERE status='auto_matched'),
                (SELECT COUNT(*) FROM matches WHERE status='manual_matched'),
                (SELECT COUNT(*) FROM matches WHERE status='partial'),
                (SELECT COUNT(*) FROM matches WHERE status='known_unmatchable')
        """).fetchone()
        return {
            "active":             c[0],
            "unmatched":          c[1],
            "auto_matched":       c[2],
            "manual_matched":     c[3],
            "partial":            c[4],
            "known_unmatchable":  c[5],
        }
