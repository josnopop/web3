from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from config import utc_window_for_local_day
from history_rescore import window_metrics


def load_rows(path: Path):
    files = sorted(path.glob("*.json")) if path.is_dir() else [path]
    rows = {}
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        for r in d.get("rows") or []:
            rows[r["wallet"]] = r
    return rows


def classify(row: dict, cutoff_ts: int):
    if not row.get("complete_enough"):
        return None, ["incomplete_history"], {}, {}
    trades = [t for t in row.get("trades") or [] if int(t.get("block_time") or 0) < cutoff_ts]
    w7 = window_metrics(trades, cutoff_ts, 7)
    w15 = window_metrics(trades, cutoff_ts, 15)
    wr7 = w7.win_tokens / max(w7.closed_tokens, 1)
    wr15 = w15.win_tokens / max(w15.closed_tokens, 1)

    reasons = []
    checks = [
        ("closed15<10", w15.closed_tokens >= 10),
        ("wr15<72%", wr15 >= 0.72),
        ("pnl15<3sol", w15.realized_pnl_sol >= 3.0),
        ("median_roi15<12%", w15.median_token_roi >= 0.12),
        ("top1>45%", w15.top1_profit_concentration <= 0.45),
        ("top3>80%", w15.top3_profit_concentration <= 0.80),
        ("hft", w15.max_trades_per_minute < 30),
        ("same_slot>40%", w15.same_slot_ratio < 0.40),
    ]
    # 7D is a freshness gate only when sample is meaningful.
    if w7.closed_tokens >= 4:
        checks += [
            ("wr7<65%", wr7 >= 0.65),
            ("pnl7<=0", w7.realized_pnl_sol > 0),
        ]
    for reason, ok in checks:
        if not ok:
            reasons.append(reason)

    if reasons:
        return None, reasons, w7.__dict__, w15.__dict__

    tier = "S"
    if (
        w15.closed_tokens >= 15
        and wr15 >= 0.82
        and w15.realized_pnl_sol >= 8.0
        and w15.median_token_roi >= 0.20
        and w15.top1_profit_concentration <= 0.35
        and w15.top3_profit_concentration <= 0.70
    ):
        tier = "S+_CANDIDATE"
    return tier, [], w7.__dict__, w15.__dict__


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--cache", required=True)
    p.add_argument("--cutoff-date", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    cutoff, _ = utc_window_for_local_day(date.fromisoformat(args.cutoff_date))
    cutoff_ts = int(cutoff.timestamp())
    rows = load_rows(Path(args.cache))

    selected, rejected = [], []
    for wallet, row in rows.items():
        tier, reasons, w7, w15 = classify(row, cutoff_ts)
        item = {
            "wallet": wallet,
            "source": row.get("source"),
            "w7": w7,
            "w15": w15,
        }
        if tier:
            item["tier"] = tier
            selected.append(item)
        else:
            item["reject_reasons"] = reasons
            rejected.append(item)

    def wr(w):
        return float(w.get("win_tokens") or 0) / max(int(w.get("closed_tokens") or 0), 1)

    selected.sort(key=lambda x: (
        0 if x["tier"] == "S+_CANDIDATE" else 1,
        -wr(x["w15"]),
        -float(x["w15"].get("realized_pnl_sol") or 0),
        x["wallet"],
    ))
    payload = {
        "policy": "7D freshness + 15D S qualification; 30D reserved for S+ or disputes",
        "wallets_checked": len(rows),
        "s_count": sum(1 for x in selected if x["tier"] == "S"),
        "s_plus_candidate_count": sum(1 for x in selected if x["tier"] == "S+_CANDIDATE"),
        "wallets": selected,
        "rejected_count": len(rejected),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"CHECKED={len(rows)} S={payload['s_count']} "
        f"SPLUS_CAND={payload['s_plus_candidate_count']} REJECT={len(rejected)}"
    )
    for x in selected[:50]:
        w=x["w15"]
        print(
            f"{x['tier']} {x['wallet']} closed15={w['closed_tokens']} "
            f"wr15={wr(w):.1%} pnl15={w['realized_pnl_sol']:.3f} "
            f"roi15={w['median_token_roi']:.1%}"
        )
    print(f"WROTE={out}")


if __name__ == "__main__":
    main()
