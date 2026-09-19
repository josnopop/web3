from __future__ import annotations

import argparse
import json
from pathlib import Path


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--out", required=True)
    args=p.parse_args()
    rows={}
    checked=0
    for f in sorted(Path(args.input).glob("*.json")):
        d=json.loads(f.read_text(encoding="utf-8"))
        checked+=int(d.get("checked_wallets") or 0)
        for r in d.get("rows") or []:
            w=r["wallet"]
            src=r.get("source_candidate") or {"wallet":w}
            rows[w]=src
    wallets=list(rows.values())
    wallets.sort(key=lambda x:(-float(x.get("score") or 0),x.get("wallet") or x.get("address") or ""))
    payload={"checked_wallets":checked,"active_wallet_count":len(wallets),"wallets":wallets}
    out=Path(args.out); out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"CHECKED={checked} ACTIVE={len(wallets)} WROTE={out}")

if __name__=="__main__":
    main()
