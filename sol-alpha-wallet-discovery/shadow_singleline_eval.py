from __future__ import annotations
import argparse, json
from pathlib import Path

USD_QUOTES={"USDC","USDT"}

def compatible_quote(a,b):
    if a==b:
        return True
    return a in USD_QUOTES and b in USD_QUOTES

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--input",required=True)
    p.add_argument("--out",required=True)
    p.add_argument("--stake",type=float,default=50.0)
    args=p.parse_args()

    raw=json.loads(Path(args.input).read_text(encoding="utf-8"))
    rows=sorted(raw.get("rows") or [], key=lambda x:(int(x.get("block_time") or 0), x.get("signature") or "", 0 if x.get("side")=="BUY" else 1))
    buys=[x for x in rows if x.get("side")=="BUY"]
    buy_opportunities=len(buys)

    pos=None
    trades=[]
    skipped=0
    skipped_same_token_adds=0
    skipped_other_buys=0
    quote_mismatches=0
    current_skips=0
    max_skips_in_position=0

    for r in rows:
        side=r.get("side")
        token=r.get("token_mint")
        if side=="BUY":
            if pos is None:
                pos={
                    "token":token,
                    "entry_time":int(r.get("block_time") or 0),
                    "entry_price":float(r.get("quote_per_token") or 0),
                    "entry_quote":r.get("quote_mint"),
                    "entry_sig":r.get("signature"),
                    "skipped_during_hold":0,
                }
                current_skips=0
            else:
                skipped += 1
                current_skips += 1
                pos["skipped_during_hold"] += 1
                if token==pos["token"]:
                    skipped_same_token_adds += 1
                else:
                    skipped_other_buys += 1
            continue

        if side=="SELL" and pos is not None and token==pos["token"]:
            ep=pos["entry_price"]
            xp=float(r.get("quote_per_token") or 0)
            eq=pos["entry_quote"]; xq=r.get("quote_mint")
            if ep>0 and xp>0 and compatible_quote(eq,xq):
                roi=xp/ep-1.0
                pnl=args.stake*roi
                stress_roi=(xp*0.99)/(ep*1.01)-1.0
                stress_pnl=args.stake*stress_roi
                hold_s=max(0,int(r.get("block_time") or 0)-pos["entry_time"])
                trades.append({
                    "token_mint":pos["token"],
                    "entry_time":pos["entry_time"],
                    "exit_time":int(r.get("block_time") or 0),
                    "hold_seconds":hold_s,
                    "entry_quote":eq,
                    "exit_quote":xq,
                    "entry_price":ep,
                    "exit_price":xp,
                    "roi":roi,
                    "pnl_u":pnl,
                    "stress_roi":stress_roi,
                    "stress_pnl_u":stress_pnl,
                    "skipped_during_hold":pos["skipped_during_hold"],
                    "entry_sig":pos["entry_sig"],
                    "exit_sig":r.get("signature")
                })
                max_skips_in_position=max(max_skips_in_position,pos["skipped_during_hold"])
                pos=None
                current_skips=0
            else:
                quote_mismatches += 1

    realized=sum(t["pnl_u"] for t in trades)
    stress=sum(t["stress_pnl_u"] for t in trades)
    wins=sum(t["pnl_u"]>0 for t in trades)
    losses=sum(t["pnl_u"]<0 for t in trades)
    flats=len(trades)-wins-losses
    entries=len(trades)+(1 if pos is not None else 0)
    win_rate=(wins/len(trades)*100) if trades else 0.0
    max_hold=max((t["hold_seconds"] for t in trades),default=0)
    avg_hold=(sum(t["hold_seconds"] for t in trades)/len(trades)) if trades else 0
    result={
        "wallet":raw.get("source",{}).get("wallet"),
        "date":raw.get("date"),
        "raw_full_signature_total":raw.get("full_signature_total"),
        "raw_partition_signatures":raw.get("successful_txs"),
        "raw_rows":len(rows),
        "raw_errors":len(raw.get("errors") or []),
        "buy_opportunities":buy_opportunities,
        "entries":entries,
        "completed_trades":len(trades),
        "wins":wins,"losses":losses,"flats":flats,
        "win_rate_pct":win_rate,
        "skipped_buy_opportunities":skipped,
        "skipped_same_token_adds":skipped_same_token_adds,
        "skipped_other_buys":skipped_other_buys,
        "position_open_at_end":pos is not None,
        "open_position":pos,
        "max_skips_in_one_position":max_skips_in_position if pos is None else max(max_skips_in_position,pos["skipped_during_hold"]),
        "max_hold_seconds_closed":max_hold,
        "avg_hold_seconds_closed":avg_hold,
        "realized_pnl_u":realized,
        "final_cash_if_no_open_position_u":100.0+realized if pos is None else None,
        "stress_realized_pnl_u":stress,
        "stress_final_cash_if_no_open_position_u":100.0+stress if pos is None else None,
        "quote_mismatches":quote_mismatches,
        "trades":trades,
    }
    Path(args.out).parent.mkdir(parents=True,exist_ok=True)
    Path(args.out).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if k!="trades"},ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
