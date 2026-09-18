# SOL Alpha Wallet Discovery — 2026-09-03 forward replay

This branch turns the smart-wallet layer into a market-wide discovery pipeline instead of a hand-picked wallet list.

## Anti-lookahead lock

- Freeze cutoff: **2026-09-03 00:00 UTC+8** = 2026-09-02 16:00 UTC.
- Wallet features use only the 30 days before the cutoff.
- Replay window: **2026-09-03 00:00–23:59:59 UTC+8**.
- The frozen pool is serialized and SHA-256 hashed. Replay accepts only that frozen address array.
- No 9/3 result is allowed to alter the 9/3 wallet pool or scoring version.

## Pipeline

1. Scan every successful transaction touching a configured Solana DEX/launch program.
2. Extract the fee-payer/signer wallet; there is no seed wallet list.
3. Infer per-wallet token buys/sells from pre/post token balances plus native SOL balance delta.
4. Aggregate 30-day wallet behavior.
5. Penalize tiny samples, extreme HFT/market-making behavior, same-slot/bundle-like behavior, and catastrophic closed-token losses.
6. Score wallets and keep S/A/B.
7. Freeze to `frozen_wallet_pool.json` + `frozen_wallet_pool.sha256`.
8. Replay every covered DEX buy/sell by those frozen wallets during 9/3.

## Run

```bash
cd sol-alpha-wallet-discovery
python -m venv .venv
# Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
set GOOGLE_CLOUD_PROJECT=YOUR_PROJECT
python bigquery_scan.py
```

The runner always performs a BigQuery dry-run first and refuses to execute if estimated bytes exceed `MAX_BYTES_BILLED` (default 900 GB). Raise that ceiling only deliberately.

## Output

- `out/2026-09-03/wallet_features.csv`
- `out/2026-09-03/frozen_wallet_pool.json`
- `out/2026-09-03/frozen_wallet_pool.sha256`
- `out/2026-09-03/replay_trades.csv`

## Important limitation of this first executable slice

This commit establishes **market-wide wallet discovery + immutable freeze + 9/3 trade replay**. It does not yet pretend that raw wallet PnL equals final bot PnL. The next stage consumes `replay_trades.csv` and applies Token Safety, Dev/Holder/Insider, independent-wallet clustering, liquidity/momentum, execution delay/slippage, and the paper-trade exit engine before SOL/U settlement.

Do not hand-edit the frozen pool after generation.
