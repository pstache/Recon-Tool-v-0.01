"""
Matching rule constants and per-type key definitions.

Two rule families:

  Subscription / Redemption        -> match on (ISIN, qty, institution_key, type, trade_date)
  Transfer (Subscription) /         -> match on (ISIN, qty, institution_key, type) PLUS
  Transfer (Redemption)                  settlement_date within ±30 days

Tolerances:
  Quantity: ±0.0001 (Q&A explicit; clarification #16 — qty is signed)

Note on signed quantity:
  Per clarification #16, redemptions/transfer-out have negative qty on both
  sides. So a Genus Redemption with qty=-131.8677 matches an Allfunds
  Redemption with qty=-131.8677, and never accidentally matches a
  Subscription with qty=+131.8677. This means we don't need a "direction"
  field — the sign of qty *is* the direction.

Note on partial matches (clarification #13):
  A row is `partial` if it has a candidate where ISIN + type + qty all
  agree, AND ONE of (institution, date) differs. ('institution differs but
  qty/date match' OR 'date differs but institution/qty match'.)
  Quantity-differs is NOT partial — it's unmatched.
"""

from __future__ import annotations

from decimal import Decimal

QTY_TOLERANCE = Decimal("0.0001")
TRANSFER_DATE_WINDOW_DAYS = 30


# Which transaction types use which rule.
TRANSFER_TYPES = frozenset({"Transfer (Subscription)", "Transfer (Redemption)"})
TRADE_TYPES    = frozenset({"Subscription", "Redemption"})
