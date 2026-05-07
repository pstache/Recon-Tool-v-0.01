"""Matching engine: builds match keys and joins internal vs external rows."""

from matching.engine import run_matching, MatchResult
from matching.rules import QTY_TOLERANCE, TRANSFER_DATE_WINDOW_DAYS

__all__ = ["run_matching", "MatchResult", "QTY_TOLERANCE", "TRANSFER_DATE_WINDOW_DAYS"]
