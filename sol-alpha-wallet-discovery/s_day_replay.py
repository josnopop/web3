from __future__ import annotations

import argparse
import json
import time
from datetime import date
from pathlib import Path

from config import QUOTE_MINTS, WSOL, USDC, USDT, utc_window_for_local_day
from decode_wallet_trades import token_deltas, native_sol_delta, touches_dex
from public_rpc_scan import DEFAULT_RPC, iter_signatures, rpc_call


def decode_one(tx: dict, wallet: str, signature: str):
    if not tx or (tx.get("meta") or {}).get("err") is not None or not touches_dex(tx):
        return None

    meta = tx.get("meta") or {}
    deltas = token_deltas(meta, wallet)
    assets = [(m, d) for m, d in deltas.items() if m not in QUOTE_MINTS]
    if len(assets) != 1:
        return None

    mint, asset_delta = assets[0]
    native = native_sol_delta(tx, wallet)
    wsol = float(deltas.get(WSOL, 0.0))
    sol_quote_delta = native + wsol

    side = None
    quote_sol = 0.0
    if asset_delta > 0 and sol_quote_delta < -0.0001:
        side = "BUY"
        quote_sol = -sol_quote_delta
    elif asset_delta < 0 and sol_quote_delta > 0.0001:
        side = "SELL"
        quote_sol = sol_quote_delta

    # Keep the first simulation in SOL terms only. Stablecoin-routed rows are
    # skipped rather than mixing USD units into SOL PnL.
    if not side or quote_sol <= 0:
        return None

    token_amount = abs(float(asset_delta))
    if token_amount <= 0:
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

    data = json.loads(Path(args.pool).read_text(encoding="utf-8"))
    wallets = data.get("wallets") or []
    selected = [w for i, w in enumerate(wallets) if i % args.shards == args.shard]

    start, end = utc_window_for_local_day(date.fromisoformat(args.date))
    start_ts, end_ts = int(start.timestamp()), int(end.timestamp())

    rows = []
    summaries = []
    for src in selected:
        wallet = src["wallet"]
        sigs = list(iter_signatures(
            wallet, start_ts, end_ts,
            max_pages=args.pages, rpc_url=args.rpc, sleep_s=0.15
        ))
        errors = 0
        decoded = []
        for i, s in enumerate(sigs, 1):
            try:
                tx = rpc_call(
                    "getTransaction",
                    [s.signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
                    args.rpc,
                    retries=6,
                )
                t = decode_one(tx, wallet, s.signature)
                if t:
                    t["source_win_rate"] = src.get("win_rate")
                    t["source_net_pnl_sol"] = src.get("net_pnl_sol")
                    t["source_tokens"] = src.get("tokens")
                    decoded.append(t)
            except Exception:
                errors += 1
            if i % 100 == 0:
                time.sleep(0.12)

        rows.extend(decoded)
        summaries.append({
            "wallet": wallet,
            "successful_txs": len(sigs),
            "decoded_sol_swap_rows": len(decoded),
            "buys": sum(1 for x in decoded if x["side"] == "BUY"),
            "sells": sum(1 for x in decoded if x["side"] == "SELL"),
            "rpc_errors": errors,
            "possible_page_cap": len(sigs) >= args.pages * 1000,
            "source": src,
        })
        print(
            f"{wallet[:8]} tx={len(sigs)} decoded={len(decoded)} "
            f"buy={summaries[-1]['buys']} sell={summaries[-1]['sells']} err={errors}"
        )

    payload = {
        "date_local": args.date,
        "window_utc": [start.isoformat(), end.isoformat()],
        "shard": args.shard,
        "wallets": len(selected),
        "summaries": summaries,
        "trades": rows,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"WROTE={out}")


if __name__ == "__main__":
    main()
