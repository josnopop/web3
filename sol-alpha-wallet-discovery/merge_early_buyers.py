from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--input",required=True)
    p.add_argument("--known",default="frozen_wallets_2026_09_15.json")
    p.add_argument("--out",default="out/2026-09-15/anonymous_early_buyer_candidates.json")
    args=p.parse_args()

    known_data=json.loads(Path(args.known).read_text(encoding="utf-8"))
    known={w["address"] for w in known_data["wallets"]}

    agg=defaultdict(lambda:{"tokens":set(),"ranks":[],"latencies":[],"sol":[]})
    token_results=[]
    for path in sorted(Path(args.input).glob("*.json")):
        d=json.loads(path.read_text(encoding="utf-8"))
        token_results.append(d)
        for b in d.get("buyers") or []:
            a=agg[b["wallet"]]
            a["tokens"].add(d["mint"])
            a["ranks"].append(b["rank"])
            if b.get("seconds_from_curve_start") is not None:
                a["latencies"].append(b["seconds_from_curve_start"])
            a["sol"].append(b.get("approx_sol_out") or 0)

    candidates=[]
    for wallet,a in agg.items():
        n=len(a["tokens"])
        avg_rank=sum(a["ranks"])/len(a["ranks"])
        avg_latency=sum(a["latencies"])/len(a["latencies"]) if a["latencies"] else None
        bot_like=(n>=2 and avg_latency is not None and avg_latency<2.0)
        score=10*n + max(0,10-avg_rank) + min(10,sum(a["sol"])/5)
        if bot_like:
            score -= 12
        candidates.append({
            "wallet":wallet,
            "early_tokens":n,
            "avg_early_rank":round(avg_rank,3),
            "avg_seconds_from_curve_start":round(avg_latency,3) if avg_latency is not None else None,
            "total_approx_sol_out":round(sum(a["sol"]),6),
            "bot_like_ultrafast":bot_like,
            "already_known":wallet in known,
            "candidate_score":round(score,3),
            "mints":sorted(a["tokens"]),
        })

    candidates.sort(key=lambda x:(-x["candidate_score"],-x["early_tokens"],x["avg_early_rank"]))
    result={
        "source":"Pump.fun early buyers reverse-discovered from dated Sep1-15 signal tokens",
        "tokens_scanned":len(token_results),
        "unique_early_buyers":len(candidates),
        "repeat_early_buyers":sum(1 for x in candidates if x["early_tokens"]>=2),
        "repeat_non_ultrafast":sum(1 for x in candidates if x["early_tokens"]>=2 and not x["bot_like_ultrafast"]),
        "candidates":candidates,
        "token_results":token_results,
    }
    out=Path(args.out)
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(
        f"RESULT tokens={len(token_results)} unique={len(candidates)} "
        f"repeat={result['repeat_early_buyers']} non_ultrafast={result['repeat_non_ultrafast']}"
    )
    print(f"WROTE={out}")


if __name__=="__main__":
    main()
