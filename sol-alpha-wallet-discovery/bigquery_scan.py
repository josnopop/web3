from __future__ import annotations

import argparse
import csv
import json
import os
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from google.cloud import bigquery

from config import (
    DEX_PROGRAM_IDS,
    LOOKBACK_WINDOWS,
    QUOTE_MINTS,
    SCORING_VERSION,
    utc_window_for_local_day,
)
from scoring import WalletFeatures, WindowMetrics, freeze_snapshot, score_wallet

ROOT = Path(__file__).resolve().parent
DEFAULT_MAX_BYTES = 900_000_000_000


def _params(replay_day: date):
    replay_start, replay_end = utc_window_for_local_day(replay_day)
    lookback_start = replay_start - timedelta(days=max(LOOKBACK_WINDOWS))
    return replay_start, replay_end, lookback_start


def _cfg(max_bytes, params, dry=False):
    return bigquery.QueryJobConfig(
        maximum_bytes_billed=max_bytes,
        use_query_cache=not dry,
        dry_run=dry,
        query_parameters=params,
    )


def run_sql(client, sql, max_bytes, params, label):
    dry = client.query(sql, job_config=_cfg(max_bytes, params, True))
    print(f"[dry-run] {label}: {dry.total_bytes_processed:,} bytes")
    if dry.total_bytes_processed > max_bytes:
        raise RuntimeError(f"{label} exceeds MAX_BYTES_BILLED")
    return client.query(sql, job_config=_cfg(max_bytes, params, False)).result()


def discover(client, out_dir, max_bytes, replay_day):
    replay_start, replay_end, lookback_start = _params(replay_day)
    sql = (ROOT / "sql" / "01_discover_wallets.sql").read_text(encoding="utf-8")
    params = [
        bigquery.ScalarQueryParameter("lookback_start", "TIMESTAMP", lookback_start),
        bigquery.ScalarQueryParameter("freeze_ts", "TIMESTAMP", replay_start),
        bigquery.ArrayQueryParameter("dex_program_ids", "STRING", sorted(DEX_PROGRAM_IDS)),
        bigquery.ArrayQueryParameter("quote_mints", "STRING", sorted(QUOTE_MINTS)),
    ]
    rows = list(run_sql(client, sql, max_bytes, params, "discover"))

    grouped = defaultdict(dict)
    raw_path = out_dir / "wallet_window_features.csv"
    with raw_path.open("w", newline="", encoding="utf-8") as f:
        writer = None
        for row in rows:
            d = dict(row)
            if writer is None:
                writer = csv.DictWriter(f, fieldnames=list(d))
                writer.writeheader()
            writer.writerow(d)
            grouped[str(d["wallet"])][int(d["days"])] = d

    scores = []
    for wallet, ws in grouped.items():
        if not all(k in ws for k in LOOKBACK_WINDOWS):
            continue

        def w(days):
            d = ws[days]
            return WindowMetrics(
                days=days,
                dex_txs=int(d["dex_txs"]),
                active_days=int(d["active_days"]),
                distinct_tokens=int(d["distinct_tokens"]),
                realized_pnl_sol=float(d["realized_pnl_sol"] or 0),
                win_tokens=int(d["win_tokens"]),
                closed_tokens=int(d["closed_tokens"]),
                median_token_roi=float(d["median_token_roi"] or 0),
                top1_profit_concentration=float(d["top1_profit_concentration"] or 0),
                top3_profit_concentration=float(d["top3_profit_concentration"] or 0),
                median_hold_minutes=float(d["median_hold_minutes"] or 0),
                rug_like_tokens=int(d["rug_like_tokens"]),
                same_slot_ratio=float(d["same_slot_ratio"] or 0),
                max_trades_per_minute=int(d["max_trades_per_minute"] or 0),
            )

        scores.append(
            score_wallet(
                WalletFeatures(
                    wallet=wallet,
                    w7=w(7),
                    w15=w(15),
                    w30=w(30),
                )
            )
        )

    scores.sort(key=lambda s: (-s.score, s.wallet))
    metadata = {
        "freeze_ts_utc": replay_start.isoformat(),
        "replay_end_ts_utc": replay_end.isoformat(),
        "windows_days": list(LOOKBACK_WINDOWS),
        "scoring_version": SCORING_VERSION,
        "anti_lookahead": True,
        "kol_bonus": 0,
    }
    canonical, digest = freeze_snapshot(scores, metadata)
    (out_dir / "frozen_wallet_pool.json").write_text(
        json.dumps(json.loads(canonical), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "frozen_wallet_pool.sha256").write_text(digest + "\n", encoding="utf-8")

    kept = [s.wallet for s in scores if s.wallet_class != "Rejected"]
    print(
        f"[freeze] raw_wallets={len(grouped)} scored={len(scores)} "
        f"kept={len(kept)} sha256={digest}"
    )
    return kept


def replay(client, out_dir, max_bytes, replay_day, wallets):
    if not wallets:
        (out_dir / "replay_trades.csv").write_text("", encoding="utf-8")
        return

    replay_start, replay_end, _ = _params(replay_day)
    sql = (ROOT / "sql" / "02_replay.sql").read_text(encoding="utf-8")
    params = [
        bigquery.ScalarQueryParameter("replay_start", "TIMESTAMP", replay_start),
        bigquery.ScalarQueryParameter("replay_end", "TIMESTAMP", replay_end),
        bigquery.ArrayQueryParameter("wallets", "STRING", wallets),
        bigquery.ArrayQueryParameter("dex_program_ids", "STRING", sorted(DEX_PROGRAM_IDS)),
        bigquery.ArrayQueryParameter("quote_mints", "STRING", sorted(QUOTE_MINTS)),
    ]
    rows = run_sql(client, sql, max_bytes, params, "replay")

    path = out_dir / "replay_trades.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = None
        for row in rows:
            d = dict(row)
            if writer is None:
                writer = csv.DictWriter(f, fieldnames=list(d))
                writer.writeheader()
            writer.writerow(d)
    print(f"[replay] wrote {path}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        "--date",
        required=True,
        help="Replay day in UTC+8, e.g. 2026-09-16. Freeze cutoff is that day 00:00.",
    )
    p.add_argument("--project", default=os.getenv("GOOGLE_CLOUD_PROJECT"))
    p.add_argument("--out")
    p.add_argument(
        "--max-bytes",
        type=int,
        default=int(os.getenv("MAX_BYTES_BILLED", DEFAULT_MAX_BYTES)),
    )
    args = p.parse_args()
    day = date.fromisoformat(args.date)

    if not args.project:
        raise SystemExit("Set --project or GOOGLE_CLOUD_PROJECT.")

    out_dir = Path(args.out or f"out/{day.isoformat()}")
    out_dir.mkdir(parents=True, exist_ok=True)
    client = bigquery.Client(project=args.project)

    wallets = discover(client, out_dir, args.max_bytes, day)
    replay(client, out_dir, args.max_bytes, day, wallets)


if __name__ == "__main__":
    main()
