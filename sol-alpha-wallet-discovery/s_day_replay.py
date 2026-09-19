from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path

from config import QUOTE_MINTS, WSOL
from decode_wallet_trades import token_deltas, native_sol_delta
from public_rpc_scan import DEFAULT_RPC, iter_signatures, rpc_call

MIN_SOL = 0.0001


def _wallet_index(tx: dict, wallet: str):
    keys = tx.get("transaction", {}).get("message", {}).get("accountKeys") or []
    vals = [(x.get("pubkey") if isinstance(x, dict) else x) for x in keys]
    try:
        return vals.index(wallet)
    except ValueError:
        return None


def _fee_adjusted_native_delta(tx: dict, wallet: str) -> float:
    d = native_sol_delta(tx, wallet)
    idx = _wallet_index(tx, wallet)
    if idx == 0:
        d += float((tx.get("meta") or {}).get("fee") or 0) / 1e9
    return d


def decode_one(tx: dict, wallet: str, signature: str):
    """Balance-delta decoder.

    Intentionally does not require a known DEX program id. Jupiter/multi-hop
    routes can hide the venue in inner/CPI instructions; the wallet's actual
    pre/post token and SOL balances are the source of truth.

    Returns one row for a simple single-asset SOL/WSOL swap. Ambiguous
    multi-asset changes are rejected instead of guessed.
    """
    if not tx or (tx.get("meta") or {}).get("err") is not None:
        return None

    meta = tx.get("meta") or {}
    deltas = token_deltas(meta, wallet)
    assets = [(m, float(d)) for m, d in deltas.items()
              if m not in QUOTE_MINTS and abs(float(d)) > 1e-15]
    if len(assets) != 1:
        return None

    mint, asset_delta = assets[0]
    native = _fee_adjusted_native_delta(tx, wallet)
    wsol = float(deltas.get(WSOL, 0.0))
    sol_quote_delta = native + wsol

    if asset_delta > 0 and sol_quote_delta < -MIN_SOL:
        side, quote_sol = "BUY", -sol_quote_delta
    elif asset_delta < 0 and sol_quote_delta > MIN_SOL:
        side, quote_sol = "SELL", sol_quote_delta
    else:
        return None

    token_amount = abs(asset_delta)
    if token_amount <= 0 or quote_sol <= 0:
        return None

    return {
        "wallet": wallet,
        "signature": signature,
        "block_time": int(tx.get("blockTime") or 0),
        "slot": int(tx.get("slot") or 0),
        "side": side,
        "token_mint": mint,
        "token_amount": token_amount,
        "quote_sol": float(quote_sol),
        "sol_per_token": float(quote_sol) / token_amount,
        "fee_sol": float(meta.get("fee") or 0) / 1e9,
        "decoder": "balance_delta_v2",
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--pool", required=True)
    p.add_argument("--date", required=True)
    p.add_argument("--shard", type=int, required=True)
    p.add_argument("--shards", type=int, default=12)
    p.add_argument("--pages", type=int, default=4)
    p.add_argument("--rpc", default=DEFAULT_RPC)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    from datetime import date
    from config import utc_window_for_local_day

    data = json.loads(Path(args.pool).read_text(encoding="utf-8"))
    wallets = data.get("wallets") or []
    selected = [w for i, w in enumerate(wallets) if i % args.shards == args.shard]
    start, end = utc_window_for_local_day(date.fromisoformat(args.date))
    start_ts, end_ts = int(start.timestamp()), int(end.timestamp())

    rows, summaries = [], []
    for src in selected:
        wallet = src["wallet"]
        sigs = list(iter_signatures(wallet, start_ts, end_ts, max_pages=args.pages, rpc_url=args.rpc, sleep_s=.15))
        decoded, errors = [], 0
        for i, s in enumerate(sigs, 1):
            try:
                tx = rpc_call("getTransaction", [s.signature, {"encoding":"jsonParsed","maxSupportedTransactionVersion":0}], args.rpc, retries=6)
                t = decode_one(tx, wallet, s.signature)
                if t:
                    t["source_win_rate"]=src.get("win_rate")
                    t["source_net_pnl_sol"]=src.get("net_pnl_sol")
                    t["source_tokens"]=src.get("tokens")
                    decoded.append(t)
            except Exception:
                errors += 1
            if i % 100 == 0: time.sleep(.12)
        rows.extend(decoded)
        summaries.append({"wallet":wallet,"successful_txs":len(sigs),"decoded_sol_swap_rows":len(decoded),
                          "buys":sum(x["side"]=="BUY" for x in decoded),"sells":sum(x["side"]=="SELL" for x in decoded),
                          "rpc_errors":errors,"possible_page_cap":len(sigs)>=args.pages*1000,"source":src})
        print(wallet[:8], len(sigs), len(decoded), summaries[-1]["buys"], summaries[-1]["sells"], errors)

    out=Path(args.out); out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps({"date_local":args.date,"window_utc":[start.isoformat(),end.isoformat()],
                               "shard":args.shard,"wallets":len(selected),"summaries":summaries,"trades":rows},
                              ensure_ascii=False,indent=2),encoding="utf-8")


if __name__ == "__main__":
    main()
