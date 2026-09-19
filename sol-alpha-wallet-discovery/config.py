from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

LOCAL_TZ = timezone(timedelta(hours=8))
LOOKBACK_WINDOWS = (7, 15, 30)

WSOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
QUOTE_MINTS = {WSOL, USDC, USDT}

# Official Pump.fun bonding-curve program id from pump-fun/pump-public-docs.
PUMPFUN_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

DEX_PROGRAM_IDS = {
    PUMPFUN_PROGRAM_ID,  # Pump.fun bonding curve
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",  # PumpSwap
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",  # Jupiter v6
    "675kPX9MHTjS2zt1qfr1NYHuzeP4f4VgFkZyJgB9wCt",  # Raydium AMM v4
    "CAMMCzo5YL8w4VFF8KVHrK22GGUQpKHpHrG2GGLS6V",  # Raydium CLMM
    "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo",  # Meteora DLMM
    "whirLbMiicVdio4qvUfM5KAg6CtB8VDbQ8tX1YDPJ2",  # Orca Whirlpools
}

SCORING_VERSION = "wallet-score-v0.3.1-pump-id-fix"


def utc_window_for_local_day(day: date) -> tuple[datetime, datetime]:
    start_local = datetime.combine(day, time.min, tzinfo=LOCAL_TZ)
    start_utc = start_local.astimezone(timezone.utc)
    return start_utc, start_utc + timedelta(days=1)
