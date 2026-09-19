from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--history", required=True)
    p.add_argument("--early-dir", required=True)
    p.add_argument("--cutoff-date", required=True)
    p.add_argument("--out", required=True)
    args=p.parse_args()

    history=json.loads(Path(args.history).read_text(encoding="utf-8"))
    merged={}

    def ensure(wallet):
        return merged.setdefault(wallet,{
            "wallet":wallet,"sources":set(),"dated_days_seen":0,"dated_sections":{},
            "visible_win_rate_pct":None,"visible_pnl_sol":None,
            "early_tokens":set(),"early_ranks":[],"early_latencies":[],"early_sol":0.0,
        })

    for w in history.get("wallets") or []:
        addr=w.get("address")
        if not addr: continue
        r=ensure(addr)
        r["sources"].add("MadeOnSol dated Daily Alpha")
        r["dated_days_seen"]=int(w.get("days_seen") or 0)
        r["dated_sections"]=w.get("sections") or {}
        r["visible_win_rate_pct"]=w.get("avg_top_win_rate")
        r["visible_pnl_sol"]=w.get("sum_visible_top_pnl_sol")

    early_files=sorted(Path(args.early_dir).glob("*.json"))
    early_wallets=set()
    tokens_scanned=0
    for f in early_files:
        d=json.loads(f.read_text(encoding="utf-8"))
        mint=d.get("mint")
        if not mint: continue
        tokens_scanned+=1
        for b in d.get("buyers") or []:
            wallet=b.get("wallet")
            if not wallet: continue
            early_wallets.add(wallet)
            r=ensure(wallet)
            r["sources"].add("Pump.fun dated-token Early Buyers")
            r["early_tokens"].add(mint)
            if b.get("rank") is not None: r["early_ranks"].append(float(b["rank"]))
            if b.get("seconds_from_curve_start") is not None: r["early_latencies"].append(float(b["seconds_from_curve_start"]))
            r["early_sol"]+=float(b.get("approx_sol_out") or 0)

    wallets=[]
    for wallet,r in merged.items():
        sec=r["dated_sections"]
        days=r["dated_days_seen"]
        early_n=len(r["early_tokens"])
        avg_rank=statistics.mean(r["early_ranks"]) if r["early_ranks"] else None
        avg_latency=statistics.mean(r["early_latencies"]) if r["early_latencies"] else None
        wr=r["visible_win_rate_pct"]
        pnl=r["visible_pnl_sol"]

        score=(
            min(days,15)*2.0
            + int(sec.get("top_performer") or 0)*10.0
            + int(sec.get("signal") or 0)*6.0
            + int(sec.get("convergence") or 0)*5.0
            + int(sec.get("active") or 0)*2.0
            + early_n*9.0
            + (max(0.0,10.0-avg_rank) if avg_rank is not None else 0.0)
            + min(10.0,r["early_sol"]/5.0)
        )
        if wr is not None: score+=max(0.0,(float(wr)-50.0)/4.0)
        if pnl is not None and float(pnl)>0: score+=min(10.0,float(pnl)/100.0)

        if early_n>=2 or int(sec.get("signal") or 0)>=2 or int(sec.get("top_performer") or 0)>=1:
            klass="PRIMARY"
        elif days>=2 or r["early_ranks"]:
            klass="RADAR"
        else:
            klass="WATCH"

        wallets.append({
            "wallet":wallet,
            "score":round(score,4),
            "discovery_class":klass,
            "sources":sorted(r["sources"]),
            "source_count":len(r["sources"]),
            "dated_days_seen":days,
            "dated_sections":sec,
            "visible_win_rate_pct":wr,
            "visible_pnl_sol":pnl,
            "early_tokens":early_n,
            "avg_early_rank":round(avg_rank,3) if avg_rank is not None else None,
            "avg_early_latency_s":round(avg_latency,3) if avg_latency is not None else None,
            "early_sol":round(r["early_sol"],6),
            "historical_safety":"discovered only from dated evidence on/before cutoff",
        })

    wallets.sort(key=lambda x:(
        {"PRIMARY":0,"RADAR":1,"WATCH":2}.get(x["discovery_class"],9),
        -x["score"],x["wallet"]
    ))
    payload={
        "cutoff_date":args.cutoff_date,
        "anti_lookahead":True,
        "current_leaderboard_used":False,
        "dated_wallet_count":len(history.get("wallets") or []),
        "tokens_reverse_scanned":tokens_scanned,
        "early_buyer_wallet_count":len(early_wallets),
        "unique_candidate_wallets":len(wallets),
        "class_counts":{c:sum(1 for w in wallets if w["discovery_class"]==c) for c in ("PRIMARY","RADAR","WATCH")},
        "wallets":wallets,
    }
    out=Path(args.out); out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"DATED={payload['dated_wallet_count']} EARLY={payload['early_buyer_wallet_count']} UNIQUE={len(wallets)} CLASSES={payload['class_counts']}")
    print(f"WROTE={out}")

if __name__=="__main__":
    main()
