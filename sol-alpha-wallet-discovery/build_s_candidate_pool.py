from __future__ import annotations

import argparse
import json
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    d = json.loads(Path(args.input).read_text(encoding="utf-8"))
    wallets = d.get("wallets") or []

    # First-pass S candidate rule: only three things matter here.
    # 1) good sample size, 2) high win rate, 3) high realized profit.
    # No copyability / HFT / active-days / hold-time / 15D / 30D gates here.
    rows = [
        w for w in wallets
        if int(w.get("tokens") or 0) >= 15
        and float(w.get("win_rate") or 0) >= 0.80
        and float(w.get("net_pnl_sol") or 0) >= 30.0
    ]

    rows.sort(key=lambda w: (
        -float(w.get("win_rate") or 0),
        -float(w.get("net_pnl_sol") or 0),
        -int(w.get("tokens") or 0),
        w.get("wallet") or "",
    ))
    rows = rows[:args.limit]

    payload = {
        "purpose": "simple S candidate pool: profit + win rate + sample size only",
        "source_raw": d.get("raw_unique_wallets"),
        "source_candidates": d.get("eligible_count"),
        "rules": {
            "tokens_min": 15,
            "win_rate_min": 0.80,
            "net_pnl_sol_min": 30.0
        },
        "candidate_count": len(rows),
        "wallets": rows,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"RAW={payload['source_raw']} BROAD={payload['source_candidates']} "
        f"S_SIMPLE={len(rows)}"
    )
    print(f"WROTE={out}")


if __name__ == "__main__":
    main()
