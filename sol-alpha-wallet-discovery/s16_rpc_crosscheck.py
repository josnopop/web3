from __future__ import annotations
import argparse, json, urllib.request, urllib.error, time
from datetime import datetime, timezone

def call(url, method, params, retries=4):
    payload=json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params}).encode()
    last=None
    for i in range(retries):
        req=urllib.request.Request(url,data=payload,headers={"content-type":"application/json","user-agent":"s16-crosscheck/1.0"},method="POST")
        try:
            with urllib.request.urlopen(req,timeout=30) as r:
                body=json.loads(r.read().decode())
            if "error" in body: raise RuntimeError(body["error"])
            return body.get("result")
        except Exception as e:
            last=e; time.sleep(0.5*(i+1))
    raise RuntimeError(str(last))

def count_window(url,address,start_ts,end_ts,pages=4):
    before=None; matches=[]; raw=0
    for _ in range(pages):
        opts={"limit":1000}
        if before: opts["before"]=before
        rows=call(url,"getSignaturesForAddress",[address,opts]) or []
        raw += len(rows)
        if not rows: break
        stop=False
        for row in rows:
            bt=row.get("blockTime")
            if bt is None: continue
            if bt < start_ts:
                stop=True; break
            if start_ts <= bt < end_ts and row.get("err") is None:
                matches.append((row["signature"],int(bt),int(row["slot"])))
        before=rows[-1]["signature"]
        if stop or len(rows)<1000: break
        time.sleep(.15)
    return {"count":len(matches),"first":matches[-1][1] if matches else None,"last":matches[0][1] if matches else None,
            "signatures":[x[0] for x in matches]}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--wallet",required=True)
    p.add_argument("--out",required=True)
    p.add_argument("--pages",type=int,default=4)
    args=p.parse_args()
    start=int(datetime(2026,9,15,16,0,tzinfo=timezone.utc).timestamp())
    end=int(datetime(2026,9,16,16,0,tzinfo=timezone.utc).timestamp())
    providers={
      "solana":"https://api.mainnet.solana.com",
      "publicnode":"https://solana-rpc.publicnode.com",
      "drpc":"https://solana.drpc.org/"
    }
    out={"wallet":args.wallet,"window_utc":["2026-09-15T16:00:00Z","2026-09-16T16:00:00Z"],"providers":{}}
    union=set()
    for name,url in providers.items():
        try:
            r=count_window(url,args.wallet,start,end,args.pages)
            out["providers"][name]=r
            union.update(r["signatures"])
        except Exception as e:
            out["providers"][name]={"error":str(e)}
    out["union_count"]=len(union)
    with open(args.out,"w",encoding="utf-8") as f: json.dump(out,f,ensure_ascii=False,indent=2)
    print(json.dumps(out,ensure_ascii=False))
if __name__=="__main__": main()
