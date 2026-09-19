from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path

from solders.pubkey import Pubkey

from config import PUMPFUN_PROGRAM_ID
from public_rpc_scan import DEFAULT_RPC, rpc_call

PUMPFUN = Pubkey.from_string(PUMPFUN_PROGRAM_ID)


def pk(x):
    return x.get("pubkey") if isinstance(x, dict) else x


def ui_amount(b):
    ui = b.get("uiTokenAmount") or {}
    return int(ui.get("amount") or 0) / (10 ** int(ui.get("decimals") or 0))


def token_delta(meta, wallet, mint):
    pre = post = 0.0
    for b in meta.get("preTokenBalances") or []:
        if b.get("owner") == wallet and b.get("mint") == mint:
            pre += ui_amount(b)
    for b in meta.get("postTokenBalances") or []:
        if b.get("owner") == wallet and b.get("mint") == mint:
            post += ui_amount(b)
    return post - pre


def sol_delta(tx, wallet):
    keys = [pk(x) for x in tx["transaction"]["message"].get("accountKeys") or []]
    if wallet not in keys:
        return 0.0
    i = keys.index(wallet)
    meta = tx.get("meta") or {}
    pre = meta.get("preBalances") or []
    post = meta.get("postBalances") or []
    if i >= len(pre) or i >= len(post):
        return 0.0
    return (post[i] - pre[i]) / 1e9


def first_signer(tx):
    for x in tx["transaction"]["message"].get("accountKeys") or []:
        if isinstance(x, dict) and x.get("signer"):
            return str(x["pubkey"])
    return None


def signatures(address: str, pages: int, rpc: str):
    out = []
    before = None
    for _ in range(pages):
        opts = {"limit": 1000}
        if before:
            opts["before"] = before
        rows = rpc_call("getSignaturesForAddress", [address, opts], rpc, retries=8) or []
        if not rows:
            break
        out.extend(x for x in rows if x.get("err") is None and x.get("blockTime") is not None)
        before = rows[-1]["signature"]
        if len(rows) < 1000:
            break
        time.sleep(0.5)
    # dedupe then chronological
    uniq = {x["signature"]: x for x in out}
    return sorted(uniq.values(), key=lambda x: (x["blockTime"], x["slot"]))


def decode_trade(row, mint, rpc):
    tx = rpc_call(
        "getTransaction",
        [row["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
        rpc,
        retries=8,
    )
    if not tx or (tx.get("meta") or {}).get("err") is not None:
        return None
    wallet = first_signer(tx)
    if not wallet:
        return None
    meta = tx.get("meta") or {}
    td = token_delta(meta, wallet, mint)
    sd = sol_delta(tx, wallet)
    if td > 0 and sd < -0.0001:
        side = "BUY"
    elif td < 0 and sd > 0.0001:
        side = "SELL"
    else:
        return None
    return {
        "signature": row["signature"],
        "block_time": int(row["blockTime"]),
        "slot": int(row["slot"]),
        "wallet": wallet,
        "side": side,
        "token_delta": td,
        "sol_delta": sd,
        "sol_amount": abs(sd),
        "observed_sol_per_token": abs(sd / td) if td else None,
    }


def summarize(trades, signal_ts):
    if not trades:
        return {}
    launch_ts = min(t["block_time"] for t in trades)
    before = [t for t in trades if t["block_time"] <= signal_ts]
    first_buys = {}
    for t in before:
        if t["side"] == "BUY":
            first_buys.setdefault(t["wallet"], t)

    def window(seconds):
        a = [t for t in before if signal_ts - seconds <= t["block_time"] <= signal_ts]
        buys = [t for t in a if t["side"] == "BUY"]
        sells = [t for t in a if t["side"] == "SELL"]
        return {
            "rows": len(a),
            "buys": len(buys),
            "sells": len(sells),
            "unique_buyers": len({t["wallet"] for t in buys}),
            "unique_sellers": len({t["wallet"] for t in sells}),
            "net_sol_flow": round(
                sum(t["sol_amount"] for t in buys) - sum(t["sol_amount"] for t in sells), 9
            ),
        }

    first10 = [
        t for t in trades
        if t["side"] == "BUY" and launch_ts <= t["block_time"] <= launch_ts + 10
    ]
    first60 = [
        t for t in trades
        if t["side"] == "BUY" and launch_ts <= t["block_time"] <= launch_ts + 60
    ]
    pre_buys = [t for t in before if t["side"] == "BUY"]

    return {
        "launch_ts": launch_ts,
        "signal_ts": signal_ts,
        "token_age_seconds_at_signal": signal_ts - launch_ts,
        "decoded_trades_before_signal": len(before),
        "buys_before_signal": len(pre_buys),
        "unique_buyers_before_signal": len(first_buys),
        "first_10s_buy_rows": len(first10),
        "first_10s_unique_buyers": len({t["wallet"] for t in first10}),
        "first_60s_unique_buyers": len({t["wallet"] for t in first60}),
        "sniper_like_first10_share_of_unique_prebuyers": round(
            len({t["wallet"] for t in first10}) / max(len(first_buys), 1), 6
        ),
        "window_30s": window(30),
        "window_60s": window(60),
        "window_300s": window(300),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mint", required=True)
    p.add_argument("--signal-ts", type=int, required=True)
    p.add_argument("--pages", type=int, default=10)
    p.add_argument("--rpc", default=DEFAULT_RPC)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    mint_pk = Pubkey.from_string(args.mint)
    curve, _ = Pubkey.find_program_address([b"bonding-curve", bytes(mint_pk)], PUMPFUN)
    sigs = signatures(str(curve), args.pages, args.rpc)

    decoded = []
    errors = 0
    # Decode all rows around launch through one hour after signal; avoids wasting RPC
    # on far-later history while preserving contemporaneous signal context.
    for i, row in enumerate(sigs):
        if row["blockTime"] > args.signal_ts + 3600:
            continue
        try:
            t = decode_trade(row, args.mint, args.rpc)
            if t:
                decoded.append(t)
        except Exception:
            errors += 1
        if i and i % 100 == 0:
            time.sleep(0.25)

    summary = summarize(decoded, args.signal_ts)
    payload = {
        "mint": args.mint,
        "pumpfun_program_id": PUMPFUN_PROGRAM_ID,
        "bonding_curve": str(curve),
        "signature_rows_fetched": len(sigs),
        "decoded_trade_rows": len(decoded),
        "decode_errors": errors,
        "history_may_be_truncated": len(sigs) >= args.pages * 1000,
        "snapshot": summary,
        "trades": decoded,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k:v for k,v in payload.items() if k != "trades"}, ensure_ascii=False, indent=2))
    print(f"WROTE={out}")


if __name__ == "__main__":
    main()
