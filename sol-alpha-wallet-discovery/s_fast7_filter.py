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
        return None, ["incomplete_history"], {}

    trades = [
        t for t in row.get("trades") or []
        if int(t.get("block_time") or 0) < cutoff_ts
    ]
    w7 = window_metrics(trades, cutoff_ts, 7)
    wr7 = w7.win_tokens / max(w7.closed_tokens, 1)

    reasons = []
    checks = [
        ("closed7<6", w7.closed_tokens >= 6),
        ("wr7<75%", wr7 >= 0.75),
        ("pnl7<1.5sol", w7.realized_pnl_sol >= 1.5),
        ("median_roi7<12%", w7.median_token_roi >= 0.12),
        ("top1>50%", w7.top1_profit_concentration <= 0.50),
        ("top3>85%", w7.top3_profit_concentration <= 0.85),
        ("rug_exposure", w7.rug_like_tokens == 0),
        ("hft", w7.max_trades_per_minute < 30),
        ("same_slot>40%", w7.same_slot_ratio < 0.40),
    ]
    for reason, ok in checks:
        if not ok:
            reasons.append(reason)

    if reasons:
        return None, reasons, w7.__dict__

    tier = "S"
    if (
        w7.closed_tokens >= 10
        and wr7 >= 0.85
        and w7.realized_pnl_sol >= 5.0
        and w7.median_token_roi >= 0.20
        and w7.top1_profit_concentration <= 0.35
        and w7.top3_profit_concentration <= 0.70
    ):
        tier = "S+_CANDIDATE"

    return tier, [], w7.__dict__


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
        tier, reasons, w7 = classify(row, cutoff_ts)
        item = {
            "wallet": wallet,
            "source": row.get("source"),
            "w7": w7,
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
        -wr(x["w7"]),
        -float(x["w7"].get("realized_pnl_sol") or 0),
        x["wallet"],
    ))

    payload = {
        "policy": "7D-only S qualification; 15D/30D reserved for optional deep review",
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
        w = x["w7"]
        print(
            f"{x['tier']} {x['wallet']} closed7={w['closed_tokens']} "
            f"wr7={wr(w):.1%} pnl7={w['realized_pnl_sol']:.3f} "
            f"roi7={w['median_token_roi']:.1%}"
        )
    print(f"WROTE={out}")


if __name__ == "__main__":
    main()
