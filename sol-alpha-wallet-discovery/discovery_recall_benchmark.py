from __future__ import annotations

import argparse
import json
from pathlib import Path

DEFAULT_BENCHMARK = {
    "6SAze": "6SAzeAUFCxCWwrDikbkkKZigre1SiJ7hcZE6gHwkuMJf",
    "4EvY": "4EvYSYpt8ZbZNTwB2kjg7s8nXtiESryxCrorFm7cLGLR",
    "2gSr": "2gSrymoc2o9cfd13CpaqCWj8SfP5hi87s3tLYMFmg2e9",
    "FVrq": "FVrq8ctqGVBYmrbHmT7VPtJKcNUbEkq7QWJBngHi4yc4",
    "3DUD": "3DUDTSL6DJo5b9GGborbtfrCAdwx438tR1spK3nBK5yD",
    "BmCk": "BmCkRoBxKfQ5XQpQ4VsRcXsaqK27YP9iu8tw7BJ6HYHd",
    "CKx": "CKxSCwbap1Tf7bMTbGELT8MfKJBh85oEoLegEAVhwGAa",
}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--candidates", required=True)
    p.add_argument("--out")
    args=p.parse_args()
    d=json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    wallets={w.get("wallet") for w in d.get("wallets") or []}
    rows=[]
    for name,address in DEFAULT_BENCHMARK.items():
        rows.append({"name":name,"wallet":address,"found":address in wallets})
    found=sum(1 for r in rows if r["found"])
    payload={
        "note":"QA-only discovery recall benchmark; benchmark wallets are never auto-injected into candidate pool.",
        "candidate_count":len(wallets),
        "benchmark_count":len(rows),
        "found":found,
        "recall":found/len(rows) if rows else 0,
        "rows":rows,
    }
    print(f"RECALL={found}/{len(rows)} ({payload['recall']:.1%}) CANDIDATES={len(wallets)}")
    for r in rows:
        print(("FOUND " if r["found"] else "MISS  ")+r["name"]+" "+r["wallet"])
    if args.out:
        out=Path(args.out); out.parent.mkdir(parents=True,exist_ok=True)
        out.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
        print(f"WROTE={out}")

if __name__=="__main__":
    main()
