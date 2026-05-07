# Synthetic Allfunds fixture for parser tests.
# Contents: 4 lines, 800 chars wide each.
#   line 1: filter='40', tx_code='10' (Subscription), ISIN NO0010662836,
#           portfolio 3920 (Nordmøre), trade 2026-04-24, sett 2026-04-27,
#           qty 100.000000 (raw 0000000100000000 -> /1e6 = 100), gross/net 50000.00,
#           sett_amt 50000.00 (raw 00000000005000000 -> /100 = 50000.00).
#   line 2: filter='40', tx_code='20' (Redemption), ISIN NO0012447137,
#           portfolio 4210 (SMN), trade/sett 2026-04-24/2026-04-27,
#           qty -50.000000 on ingest (raw stays positive in file),
#           sett_amt 25000.00.
#   line 3: filter='99' (rejected by filter — should be silently skipped).
#   line 4: filter='40', tx_code='ZZ' (unknown — should be silently skipped, NOT an error).
#
# This file is generated programmatically by the test, not stored as a literal,
# because building 800-char lines by hand is fragile.
