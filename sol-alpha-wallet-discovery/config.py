from __future__ import annotations

from datetime import datetime, timezone

# 2026-09-03 in UTC+8. Freeze at local midnight before the replay day.
FREEZE_TS = datetime(2026, 9, 2, 16, 0, 0, tzinfo=timezone.utc)
REPLAY_END_TS = datetime(2026, 9, 3, 16, 0, 0, tzinfo=timezone.utc)
LOOKBACK_DAYS = 30

# Quote assets used to infer buy/sell direction and copyable entry price.
WSOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT = "Es9vMFrzaCERmJfrF4H2FYD7D5n2Gx9QjqE6XQY8F9n"
QUOTE_MINTS = {WSOL, USDC, USDT}

# Initial production DEX/launch coverage. Keep this config versioned; never change it
# after a replay starts. Programs can be added for later dates, not retroactively.
DEX_PROGRAM_IDS = {
    # Pump.fun bonding curve
    "6EF8rrecthR5Dkzf5NzcraK8xtoqf2QvF6C4Zss5F6P",
    # PumpSwap
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",
    # Jupiter v6
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",
    # Raydium AMM v4
    "675kPX9MHTjS2zt1qfr1NYHuzeP4f4VgFkZyJgB9wCt",
    # Raydium CLMM
    "CAMMCzo5YL8w4VFF8KVHrK22GGUQpKHpHrG2GGLS6V",
    # Meteora DLMM
    "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo",
    # Orca Whirlpools
    "whirLbMiicVdio4qvUfM5KAg6CtB8VDbQ8tX1YDPJ2",
}

SCORING_VERSION = "wallet-score-2026-09-03-v1"
