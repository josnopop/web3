from __future__ import annotations
import json,time,urllib.request,urllib.error
from pathlib import Path
from s_quote_replay import decode

WALLET="GnBbV25vuKvW8z8gGyutt1JxLYnDBPnNAuWEbcAhvS84"
RPCS=[
 "https://rpc.solanatracker.io/public",
 "https://solana-rpc.publicnode.com",
 "https://solana.drpc.org/",
 "https://api.mainnet-beta.solana.com",
 "https://api.mainnet.solana.com",
 "https://rpc.ankr.com/solana",
]

def fetch(sig):
    payload=json.dumps({"jsonrpc":"2.0","id":1,"method":"getTransaction","params":[sig,{"encoding":"jsonParsed","maxSupportedTransactionVersion":0}]}).encode()
    errors=[]
    for rnd in range(8):
        for url in RPCS:
            try:
                req=urllib.request.Request(url,data=payload,headers={"content-type":"application/json","user-agent":"sol-shadow-repair/1.0"},method="POST")
                with urllib.request.urlopen(req,timeout=25) as r:
                    body=json.loads(r.read().decode())
                if not body.get("error") and body.get("result") is not None:
                    return body["result"],url,None
                errors.append(f"{url}:{body.get('error')}")
            except Exception as e:
                errors.append(f"{url}:{type(e).__name__}:{str(e)[:80]}")
        time.sleep(min(0.6*(rnd+1),3.0))
    return None,None,errors[-6:]

def main():
    sigs=json.loads(Path("gnbb_missing_35.json").read_text())
    rows=[];errs=[];sources={}
    for i,sig in enumerate(sigs,1):
        tx,url,err=fetch(sig)
        if tx is None:
            errs.append({"signature":sig,"error":err})
            print("FAIL",i,sig[:8])
        else:
            rr=decode(tx,WALLET,sig)
            rows.extend(rr)
            sources[url]=sources.get(url,0)+1
            print("OK",i,sig[:8],"rows",len(rr),"url",url)
        time.sleep(.35)
    out={"wallet":WALLET,"input":len(sigs),"recovered_transactions":len(sigs)-len(errs),"rows":rows,"errors":errs,"sources":sources}
    Path("gnbb_repair.json").write_text(json.dumps(out,ensure_ascii=False,indent=2))
    print(json.dumps({"input":len(sigs),"recovered":len(sigs)-len(errs),"rows":len(rows),"buys":sum(r["side"]=="BUY" for r in rows),"sells":sum(r["side"]=="SELL" for r in rows),"errors":len(errs),"sources":sources},ensure_ascii=False))

if __name__=="__main__": main()
