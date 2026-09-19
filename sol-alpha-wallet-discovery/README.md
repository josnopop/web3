# SOL Alpha Wallet Discovery V0.3 — market-wide Walk-forward engine

This is the wallet-discovery front end for the SOL primary-market bot. It does **not** start from KOLs.

## Locked rules

- Discovery starts from all covered Solana DEX/launch-program transactions, not a seed wallet list.
- For replay day **D**, freeze at UTC+8 midnight at the start of D; only earlier data may score wallets.
- Recompute **7D / 15D / 30D** from the same historical slice.
- KOL / Smart Money are labels only: **0 bonus**.
- Frozen pools are canonicalized and SHA-256 hashed. Replay accepts only that frozen address set.
- Live GMGN / DegenRadar output may help current discovery, but may not be back-filled into a historical pool unless the evidence itself is timestamp-bounded to the cutoff.

## Assembled parts

1. **BigQuery Solana public chain history** — market-wide historical DEX signer discovery and D-1 freeze.
2. **DegenRadar** — auxiliary token→wallet harvesting / MEV-HFT-insider filtering concepts and current discovery source.
3. **GMGN open-source CLI** — auxiliary Smart Money discovery, wallet stats, wallet scoring/holder analysis. Its KOL/Smart Money tags never increase our score.
4. **Solana Holder Analyzer (Helius + OKX)** — independent funding/transfer/holder/dev relationship verification before Trade Quality V0.3.

Upstreams:
- https://github.com/carlosmmora26/DegenRadar
- https://github.com/GMGNAI/gmgn-skills
- https://github.com/non-contextual/solana-holder-analyzer

Run `setup_tools.ps1` on Windows to clone/update those upstreams into `vendor/` and install `gmgn-cli`.

## Wallet score V0.3

Historical score uses:

- 7D / 15D / 30D realized PnL and Win Rate
- sample size / active days
- Median token ROI
- Top1 / Top3 profit concentration
- hold-time and execution-speed Copyability
- same-slot / HFT / bundle-like risk
- catastrophic-loss / rug exposure
- Data Confidence and independent-source count

Optional enrichment fields are reserved for:
`Early Entry / Alpha Decay / Entry Skill / Hold Skill / Exit Quality`.

Classes:
`Radar Wallet / Copyable Wallet / Confirmation Wallet / Non-copyable Alpha / Rejected`.

## Run 15→16

```bat
cd sol-alpha-wallet-discovery
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
set GOOGLE_CLOUD_PROJECT=YOUR_PROJECT
python bigquery_scan.py --date 2026-09-16
```

The date argument is the **test day D**. For `2026-09-16`, the wallet pool is frozen using only data before 2026-09-16 00:00 UTC+8.

Outputs:

- `out/2026-09-16/wallet_window_features.csv`
- `out/2026-09-16/frozen_wallet_pool.json`
- `out/2026-09-16/frozen_wallet_pool.sha256`
- `out/2026-09-16/replay_trades.csv`

`replay_trades.csv` then goes into **Trade Quality V0.3**. A profitable wallet never creates an automatic bot BUY.
