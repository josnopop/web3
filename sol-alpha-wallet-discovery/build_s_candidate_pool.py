from __future__ import annotations

import argparse
import json
from pathlib import Path


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--limit", type=int, default=80)
    p.add_argument("--out", required=True)
    args=p.parse_args()
    d=json.loads(Path(args.input).read_text(encoding="utf-8"))
    wallets=d.get("wallets") or []

    # High-recall prefilter for expensive on-chain validation.
    # S is NOT decided here.
    rows=[
        w for w in wallets
        if int(w.get("tokens") or 0) >= 8
        and float(w.get("win_rate") or 0) >= 0.58
        and float(w.get("net_pnl_sol") or 0) >= 2.0
        and float(w.get("roi") or 0) >= 0.08
    ]
    rows.sort(key=lambda w:(
        -float(w.get("score") or 0),
        -float(w.get("net_pnl_sol") or 0),
        w.get("wallet") or ""
    ))
    rows=rows[:args.limit]
    payload={
        "purpose":"high-recall S-candidate prefilter; final S requires chain validation",
        "source_raw":d.get("raw_unique_wallets"),
        "source_candidates":d.get("eligible_count"),
        "prefilter_count":len(rows),
        "wallets":rows,
    }
    out=Path(args.out); out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"RAW={payload['source_raw']} BROAD={payload['source_candidates']} S_PREFILTER={len(rows)}")
    print(f"WROTE={out}")


if __name__=="__main__":
    main()
