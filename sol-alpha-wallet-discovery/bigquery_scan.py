from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

from google.cloud import bigquery

from config import FREEZE_TS, LOOKBACK_DAYS, REPLAY_END_TS, SCORING_VERSION
from scoring import WalletFeatures, freeze_snapshot, score_wallet

ROOT = Path(__file__).resolve().parent
DEFAULT_MAX_BYTES = 900_000_000_000  # fail closed below 1 TB


def run_query(client: bigquery.Client, sql_path: Path, max_bytes: int):
    sql = sql_path.read_text(encoding="utf-8")
    job_config = bigquery.QueryJobConfig(
        maximum_bytes_billed=max_bytes,
        use_query_cache=True,
    )

    dry = client.query(sql, job_config=bigquery.QueryJobConfig(dry_run=True, use_query_cache=False))
    print(f"[dry-run] {sql_path.name}: {dry.total_bytes_processed:,} bytes")
    if dry.total_bytes_processed > max_bytes:
        raise RuntimeError(
            f"Refusing query: {dry.total_bytes_processed:,} bytes > MAX_BYTES_BILLED={max_bytes:,}"
        )

    return client.query(sql, job_config=job_config).result()


def discover(client: bigquery.Client, out_dir: Path, max_bytes: int) -> list[str]:
    rows = run_query(client, ROOT / "sql" / "01_discover_wallets.sql", max_bytes)
    scores = []
    raw_path = out_dir / "wallet_features.csv"

    with raw_path.open("w", newline="", encoding="utf-8") as f:
        writer = None
        for row in rows:
            d = dict(row)
            if writer is None:
                writer = csv.DictWriter(f, fieldnames=list(d.keys()))
                writer.writeheader()
            writer.writerow(d)
            feat = WalletFeatures(
                wallet=str(d["wallet"]),
                dex_txs=int(d["dex_txs"]),
                active_days=int(d["active_days"]),
                distinct_tokens=int(d["distinct_tokens"]),
                buys=int(d["buys"]),
                sells=int(d["sells"]),
                gross_quote_out_sol=float(d["gross_quote_out_sol"] or 0),
                gross_quote_in_sol=float(d["gross_quote_in_sol"] or 0),
                realized_quote_pnl_sol=float(d["realized_quote_pnl_sol"] or 0),
                win_tokens=int(d["win_tokens"]),
                closed_tokens=int(d["closed_tokens"]),
                median_hold_minutes=float(d["median_hold_minutes"] or 0),
                rug_like_tokens=int(d["rug_like_tokens"]),
                same_slot_ratio=float(d["same_slot_ratio"] or 0),
                max_trades_per_minute=int(d["max_trades_per_minute"] or 0),
            )
            scores.append(score_wallet(feat))

    scores.sort(key=lambda s: (-s.score, s.wallet))
    metadata = {
        "freeze_ts_utc": FREEZE_TS.isoformat(),
        "replay_end_ts_utc": REPLAY_END_TS.isoformat(),
        "lookback_days": LOOKBACK_DAYS,
        "scoring_version": SCORING_VERSION,
        "anti_lookahead": True,
    }
    canonical, digest = freeze_snapshot(scores, metadata)
    (out_dir / "frozen_wallet_pool.json").write_text(
        json.dumps(json.loads(canonical), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "frozen_wallet_pool.sha256").write_text(digest + "\n", encoding="utf-8")

    wallets = [s.wallet for s in scores if s.tier in {"S", "A", "B"}]
    print(f"[freeze] candidates={len(scores)} kept={len(wallets)} sha256={digest}")
    return wallets


def replay(client: bigquery.Client, out_dir: Path, max_bytes: int, wallets: list[str]) -> None:
    if not wallets:
        (out_dir / "replay_trades.csv").write_text("", encoding="utf-8")
        return

    sql = (ROOT / "sql" / "02_replay_2026_09_03.sql").read_text(encoding="utf-8")
    cfg = bigquery.QueryJobConfig(
        maximum_bytes_billed=max_bytes,
        use_query_cache=True,
        query_parameters=[
            bigquery.ArrayQueryParameter("wallets", "STRING", wallets),
        ],
    )
    dry_cfg = bigquery.QueryJobConfig(
        dry_run=True,
        use_query_cache=False,
        query_parameters=[bigquery.ArrayQueryParameter("wallets", "STRING", wallets)],
    )
    dry = client.query(sql, job_config=dry_cfg)
    print(f"[dry-run] replay: {dry.total_bytes_processed:,} bytes")
    if dry.total_bytes_processed > max_bytes:
        raise RuntimeError("Replay query exceeds MAX_BYTES_BILLED")

    rows = client.query(sql, job_config=cfg).result()
    path = out_dir / "replay_trades.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = None
        for row in rows:
            d = dict(row)
            if writer is None:
                writer = csv.DictWriter(f, fieldnames=list(d.keys()))
                writer.writeheader()
            writer.writerow(d)
    print(f"[replay] wrote {path}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--project", default=os.getenv("GOOGLE_CLOUD_PROJECT"))
    p.add_argument("--out", default="out/2026-09-03")
    p.add_argument("--max-bytes", type=int, default=int(os.getenv("MAX_BYTES_BILLED", DEFAULT_MAX_BYTES)))
    args = p.parse_args()

    if not args.project:
        raise SystemExit("Set --project or GOOGLE_CLOUD_PROJECT.")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    client = bigquery.Client(project=args.project)

    wallets = discover(client, out_dir, args.max_bytes)
    replay(client, out_dir, args.max_bytes, wallets)


if __name__ == "__main__":
    main()
