from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


CLASS_WEIGHT = {
    "Copyable Candidate": 1.0,
    "Radar Candidate": 0.70,
    "Confirmation Candidate": 0.35,
}


def load_replays(path: Path) -> list[dict]:
    files = sorted(path.glob("*.json")) if path.is_dir() else [path]
    rows = []
    seen = set()
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        for t in d.get("trades") or []:
            key = (t.get("signature"), t.get("wallet"), t.get("token_mint"), t.get("side"))
            if key in seen:
                continue
            seen.add(key)
            rows.append(t)
    return rows


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--pool", required=True)
    p.add_argument("--replays", required=True)
    p.add_argument("--out", default="out/2026-09-16/v03_stage1_candidates.json")
    args = p.parse_args()

    pool = json.loads(Path(args.pool).read_text(encoding="utf-8"))
    class_map = {w["wallet"]: w["wallet_class"] for w in pool["wallets"]}
    score_map = {w["wallet"]: w.get("score") for w in pool["wallets"]}

    trades = load_replays(Path(args.replays))
    buys = [t for t in trades if t.get("side") == "BUY"]
    by_mint = defaultdict(list)
    for t in buys:
        by_mint[t["token_mint"]].append(t)

    tokens = []
    for mint, rows in by_mint.items():
        rows.sort(key=lambda x: (x.get("block_time") or 0, x.get("slot") or 0, x.get("wallet")))
        first_by_wallet = {}
        for r in rows:
            first_by_wallet.setdefault(r["wallet"], r)
        firsts = sorted(first_by_wallet.values(), key=lambda x: x["block_time"])
        wallets = [r["wallet"] for r in firsts]
        classes = [class_map.get(w, "Unknown") for w in wallets]
        times = [int(r["block_time"]) for r in firsts if r.get("block_time") is not None]
        dispersion = (max(times) - min(times)) if len(times) >= 2 else None
        earliest = min(times) if times else None

        within_10s = sum(1 for t in times if earliest is not None and t - earliest <= 10)
        within_60s = sum(1 for t in times if earliest is not None and t - earliest <= 60)
        within_10m = sum(1 for t in times if earliest is not None and t - earliest <= 600)
        copyable = sum(1 for c in classes if c == "Copyable Candidate")
        radar = sum(1 for c in classes if c == "Radar Candidate")
        confirmation = sum(1 for c in classes if c == "Confirmation Candidate")
        strength = sum(CLASS_WEIGHT.get(c, 0.0) for c in classes)

        if copyable == 0 and radar == 0:
            stage1 = "PASS"
            reason = "confirmation_only"
        elif len(wallets) == 1 and copyable == 0:
            stage1 = "WATCH"
            reason = "single_radar_signal"
        elif len(wallets) == 1:
            stage1 = "STRUCTURE_CHECK"
            reason = "single_copyable_signal"
        elif within_60s >= 2 and strength >= 1.4:
            stage1 = "STRONG_CONFIRMATION"
            reason = "independent_wallets_within_60s"
        elif within_10m >= 2:
            stage1 = "CONFIRMATION"
            reason = "independent_wallets_within_10m"
        else:
            stage1 = "STRUCTURE_CHECK"
            reason = "multi_wallet_but_dispersion_wide"

        tokens.append({
            "token_mint": mint,
            "stage1": stage1,
            "stage1_reason": reason,
            "independent_wallets": len(wallets),
            "copyable_wallets": copyable,
            "radar_wallets": radar,
            "confirmation_wallets": confirmation,
            "weighted_wallet_strength": round(strength, 4),
            "entry_dispersion_seconds": dispersion,
            "wallets_within_10s": within_10s,
            "wallets_within_60s": within_60s,
            "wallets_within_10m": within_10m,
            "first_signal_block_time": earliest,
            "signals": [{
                "wallet": r["wallet"],
                "wallet_class": class_map.get(r["wallet"], "Unknown"),
                "wallet_score": score_map.get(r["wallet"]),
                "block_time": r.get("block_time"),
                "signature": r.get("signature"),
                "quote_mint": r.get("quote_mint"),
                "quote_amount": r.get("quote_amount"),
                "token_delta": r.get("token_delta"),
            } for r in firsts],
            "final_v03": "PENDING_TOKEN_STRUCTURE",
        })

    order = {
        "STRONG_CONFIRMATION": 0,
        "CONFIRMATION": 1,
        "STRUCTURE_CHECK": 2,
        "WATCH": 3,
        "PASS": 4,
    }
    tokens.sort(key=lambda x: (order.get(x["stage1"], 99), -(x["weighted_wallet_strength"]), x["first_signal_block_time"] or 0))

    payload = {
        "freeze_sha256": pool.get("sha256"),
        "frozen_wallet_count": pool.get("frozen_wallet_count"),
        "raw_trade_rows": len(trades),
        "buy_rows": len(buys),
        "unique_buy_tokens": len(tokens),
        "stage1_counts": dict(__import__("collections").Counter(x["stage1"] for x in tokens)),
        "rule": "Stage1 never emits BUY/PROBE; all survivors require contemporaneous token-structure V0.3.",
        "tokens": tokens,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"TRADES={len(trades)} BUYS={len(buys)} TOKENS={len(tokens)} "
        f"COUNTS={payload['stage1_counts']}"
    )
    for x in tokens:
        print(
            f"{x['stage1']:<20} {x['token_mint']} wallets={x['independent_wallets']} "
            f"copy={x['copyable_wallets']} radar={x['radar_wallets']} "
            f"confirm={x['confirmation_wallets']} dispersion={x['entry_dispersion_seconds']}"
        )
    print(f"WROTE={out}")


if __name__ == "__main__":
    main()
