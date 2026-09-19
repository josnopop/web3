from __future__ import annotations

import argparse
import json
from pathlib import Path


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--history", required=True)
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--out", required=True)
    args=p.parse_args()
    d=json.loads(Path(args.history).read_text(encoding="utf-8"))
    rows=d.get("token_mints") or []
    rows=sorted(rows, key=lambda x:(-float(x.get("reverse_discovery_priority") or 0), x.get("mint") or ""))
    mints=[x["mint"] for x in rows[:args.limit] if x.get("mint")]
    out=Path(args.out); out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps({"mints":mints,"source_history":args.history},ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"MINTS={len(mints)} WROTE={out}")

if __name__=="__main__":
    main()
