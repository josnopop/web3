from __future__ import annotations
import json, urllib.request, urllib.error

SIG = "278xjWZr5b7b2eT7kpjLWutf9Drc8qodMaQX24R98a92yfUi44TCupUxS2P7rzEks1i4tTXCfHEKcgMq4X8KL5cS"
ENDPOINTS = [
    "https://api.mainnet-beta.solana.com",
    "https://api.mainnet.solana.com",
    "https://solana.drpc.org/",
    "https://solana-rpc.publicnode.com",
    "https://rpc.solanatracker.io/public",
]

payload = json.dumps({
    "jsonrpc":"2.0","id":1,"method":"getTransaction",
    "params":[SIG,{"encoding":"jsonParsed","maxSupportedTransactionVersion":0}]
}).encode()

for url in ENDPOINTS:
    req=urllib.request.Request(url,data=payload,headers={"content-type":"application/json","user-agent":"sol-wallet-discovery/0.3"},method="POST")
    try:
        with urllib.request.urlopen(req,timeout=30) as r:
            body=json.loads(r.read().decode())
        result=body.get("result")
        print(json.dumps({
            "url":url,"status":"OK","has_result":bool(result),
            "slot":result.get("slot") if result else None,
            "blockTime":result.get("blockTime") if result else None,
            "rpc_error":body.get("error")
        }))
    except urllib.error.HTTPError as e:
        print(json.dumps({"url":url,"status":f"HTTP_{e.code}","body":e.read().decode(errors="replace")[:300]}))
    except Exception as e:
        print(json.dumps({"url":url,"status":"ERROR","error":f"{type(e).__name__}: {e}"}))
