from __future__ import annotations

import argparse
import json
from pathlib import Path


def qualifies_s(row: dict) -> tuple[bool, list[str]]:
    score = row.get("chain_score") or {}
    w7 = row.get("w7") or {}
    w15 = row.get("w15") or {}
    w30 = row.get("w30") or {}

    reasons = []
    checks = [
        ("score<80", float(score.get("score") or 0) >= 80.0),
        ("copyability<0.70", float(score.get("copyability") or 0) >= 0.70),
        ("confidence<0.50", float(score.get("data_confidence") or 0) >= 0.50),
        ("wr7<70%", float(score.get("win_rate_7d") or 0) >= 0.70),
        ("wr15<68%", float(score.get("win_rate_15d") or 0) >= 0.68),
        ("wr30<65%", float(score.get("win_rate_30d") or 0) >= 0.65),
        ("pnl7<=0", float(w7.get("realized_pnl_sol") or 0) > 0),
        ("pnl15<=0", float(w15.get("realized_pnl_sol") or 0) > 0),
        ("pnl30<5sol", float(w30.get("realized_pnl_sol") or 0) >= 5.0),
        ("closed30<15", int(w30.get("closed_tokens") or 0) >= 15),
        ("active7<3", int(w7.get("active_days") or 0) >= 3),
        ("median_roi30<15%", float(w30.get("median_token_roi") or 0) >= 0.15),
        ("top1>40%", float(w30.get("top1_profit_concentration") or 1) <= 0.40),
        ("top3>75%", float(w30.get("top3_profit_concentration") or 1) <= 0.75),
        ("rug_exposure", int(w30.get("rug_like_tokens") or 0) == 0),
        ("hft", int(w30.get("max_trades_per_minute") or 0) < 25),
        ("same_slot>35%", float(w30.get("same_slot_ratio") or 0) < 0.35),
        ("score_notes", not bool(score.get("notes"))),
    ]
    for reason, ok in checks:
        if not ok:
            reasons.append(reason)
    return not reasons, reasons


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--scores", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    d = json.loads(Path(args.scores).read_text(encoding="utf-8"))
    rows = d.get("rows") or []
    selected, rejected = [], []
    for row in rows:
        if row.get("status") != "KEEP":
            continue
        ok, reasons = qualifies_s(row)
        item = {
            "wallet": row.get("wallet"),
            "chain_score": row.get("chain_score"),
            "w7": row.get("w7"),
            "w15": row.get("w15"),
            "w30": row.get("w30"),
        }
        if ok:
            item["tier"] = "S"
            selected.append(item)
        else:
            item["reject_reasons"] = reasons
            rejected.append(item)

    selected.sort(
        key=lambda x: (
            -float((x.get("chain_score") or {}).get("score") or 0),
            -float((x.get("w30") or {}).get("realized_pnl_sol") or 0),
            x.get("wallet") or "",
        )
    )
    payload = {
        "policy": "S-only copyable wallet shortlist",
        "s_rules": {
            "score_min": 80,
            "copyability_min": 0.70,
            "confidence_min": 0.50,
            "wr7_min": 0.70,
            "wr15_min": 0.68,
            "wr30_min": 0.65,
            "pnl30_min_sol": 5.0,
            "closed30_min": 15,
            "active7_min": 3,
            "median_roi30_min": 0.15,
            "top1_max": 0.40,
            "top3_max": 0.75,
            "rug_like_tokens_max": 0,
            "max_trades_per_minute_lt": 25,
            "same_slot_ratio_lt": 0.35,
            "all_7d_15d_30d_pnl_positive": True,
            "score_notes_must_be_empty": True,
        },
        "s_count": len(selected),
        "wallets": selected,
        "rejected_keep_wallets": len(rejected),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"S_COUNT={len(selected)} FROM_KEEP={sum(1 for r in rows if r.get('status') == 'KEEP')}")
    for x in selected[:50]:
        s=x["chain_score"]; w=x["w30"]
        print(
            f"S {x['wallet']} score={s['score']} pnl30={w['realized_pnl_sol']:.3f} "
            f"wr7={s['win_rate_7d']:.1%} wr30={s['win_rate_30d']:.1%} "
            f"roi30={w['median_token_roi']:.1%} copy={s['copyability']:.2f}"
        )
    print(f"WROTE={out}")


if __name__ == "__main__":
    main()
