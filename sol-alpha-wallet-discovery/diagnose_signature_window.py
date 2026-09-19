from __future__ import annotations
import argparse, json, time, urllib.request, urllib.error
from datetime import datetime, timezone

def call(url, address, before=None):
    opts={"limit":1000}
    if before: opts["before"]=before
    body=json.dumps({"jsonrpc":"2.0","id":1,"method":"getSignaturesForAddress","params":[address,opts]}).encode()
    req=urllib.request.Request(url,data=body,headers={"content-type":"application/json","user-agent":"s-window-diag/1.0"},method="POST")
    last=None
    for a in range(5):
        try:
            with urllib.request.urlopen(req,timeout=30) as r:
                x=json.loads(r.read().decode())
            if x.get("error"): raise RuntimeError(str(x["error"]))
            return x.get("result") or []
        except Exception as e:
            last=e; time.sleep(0.5*(a+1))
    raise RuntimeError(str(last))

def iso(ts):
    return datetime.fromtimestamp(ts,tz=timezone.utc).isoformat() if ts else None

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--wallet",required=True)
    ap.add_argument("--rpc",required=True)
    ap.add_argument("--start-ts",type=int,required=True)
    ap.add_argument("--end-ts",type=int,required=True)
    ap.add_argument("--max-pages",type=int,default=20)
    a=ap.parse_args()
    before=None; match=0; success_match=0; reached=False; pages=[]
    for p in range(a.max_pages):
        try: rows=call(a.rpc,a.wallet,before)
        except Exception as e:
            print(json.dumps({"wallet":a.wallet,"rpc":a.rpc,"error":str(e),"pages":pages},ensure_ascii=False)); return
        if not rows: break
        bts=[r.get("blockTime") for r in rows if r.get("blockTime")]
        newest=max(bts) if bts else None; oldest=min(bts) if bts else None
        m=[r for r in rows if r.get("blockTime") is not None and a.start_ts <= r["blockTime"] < a.end_ts]
        sm=[r for r in m if r.get("err") is None]
        match+=len(m); success_match+=len(sm)
        pages.append({"page":p+1,"rows":len(rows),"newest":iso(newest),"oldest":iso(oldest),"window_rows":len(m),"window_success":len(sm)})
        if oldest is not None and oldest < a.start_ts:
            reached=True; break
        before=rows[-1]["signature"]
        time.sleep(0.15)
    print(json.dumps({"wallet":a.wallet,"rpc":a.rpc,"reached_start":reached,"matching_signatures":match,"successful_matching_signatures":success_match,"pages_used":len(pages),"pages":pages},ensure_ascii=False))
if __name__=="__main__": main()
