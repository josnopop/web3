from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path

from config import DEX_PROGRAM_IDS, QUOTE_MINTS, USDC, USDT, WSOL
from public_rpc_scan import DEFAULT_RPC, rpc_call


def _pubkey(x):
    return x.get("pubkey") if isinstance(x, dict) else x


def _ui_amount(b):
    ui = b.get("uiTokenAmount") or {}
    raw = int(ui.get("amount") or 0)
    dec = int(ui.get("decimals") or 0)
    return raw / (10 ** dec)


def token_deltas(meta, wallet):
    pre = defaultdict(float)
    post = defaultdict(float)
    for b in meta.get("preTokenBalances") or []:
        if b.get("owner") == wallet:
            pre[b["mint"]] += _ui_amount(b)
    for b in meta.get("postTokenBalances") or []:
        if b.get("owner") == wallet:
            post[b["mint"]] += _ui_amount(b)
    out = {}
    for mint in set(pre) | set(post):
        d = post[mint] - pre[mint]
        if abs(d) > 1e-15:
            out[mint] = d
    return out


def native_sol_delta(tx, wallet):
    msg = tx["transaction"]["message"]
    keys = [_pubkey(x) for x in msg.get("accountKeys") or []]
    try:
        idx = keys.index(wallet)
    except ValueError:
        return 0.0
    meta = tx.get("meta") or {}
    pre = meta.get("preBalances") or []
    post = meta.get("postBalances") or []
    if idx >= len(pre) or idx >= len(post):
        return 0.0
    return (post[idx] - pre[idx]) / 1e9


def touches_dex(tx):
    keys = {_pubkey(x) for x in tx["transaction"]["message"].get("accountKeys") or []}
    return bool(keys & DEX_PROGRAM_IDS)


def classify_asset(asset_delta, quotes, sol_delta):
    # Prefer tokenized quote assets because native SOL delta includes tx fees.
    for mint in (WSOL, USDC, USDT):
        q = quotes.get(mint, 0.0)
        if asset_delta > 0 and q < 0:
            return "BUY", mint, abs(q)
        if asset_delta < 0 and q > 0:
            return "SELL", mint, abs(q)
    if asset_delta > 0 and sol_delta < -0.0001:
        return "BUY", "SOL", abs(sol_delta)
    if asset_delta < 0 and sol_delta > 0.0001:
        return "SELL", "SOL", abs(sol_delta)
    return None, None, 0.0


def decode_wallet_file(path: Path, rpc_url: str, sleep_s: float):
    src = json.loads(path.read_text(encoding="utf-8"))
    w = src["wallets"][0]
    wallet = w["address"]
    trades = []
    errors = []
    non_dex = 0
    no_swap = 0

    sigs = w.get("signatures") or []
    for i, sig in enumerate(sigs, 1):
        try:
            tx = rpc_call(
                "getTransaction",
                [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
                rpc_url,
            )
            if not tx or tx.get("meta", {}).get("err") is not None:
                continue
            if not touches_dex(tx):
                non_dex += 1
                continue

            meta = tx.get("meta") or {}
            deltas = token_deltas(meta, wallet)
            quotes = {m: d for m, d in deltas.items() if m in QUOTE_MINTS}
            assets = {m: d for m, d in deltas.items() if m not in QUOTE_MINTS}
            sol_delta = native_sol_delta(tx, wallet)

            emitted = 0
            for mint, delta in assets.items():
                side, quote_mint, quote_amount = classify_asset(delta, quotes, sol_delta)
                if side:
                    trades.append({
                        "wallet_name": w["name"],
                        "wallet": wallet,
                        "signature": sig,
                        "block_time": tx.get("blockTime"),
                        "slot": tx.get("slot"),
                        "side": side,
                        "token_mint": mint,
                        "token_delta": delta,
                        "quote_mint": quote_mint,
                        "quote_amount": quote_amount,
                        "native_sol_delta": sol_delta,
                        "fee_sol": (meta.get("fee") or 0) / 1e9,
                    })
                    emitted += 1
            if emitted == 0:
                no_swap += 1
        except Exception as e:
            errors.append({"signature": sig, "error": f"{type(e).__name__}: {e}"})
        if sleep_s:
            time.sleep(sleep_s)
        if i % 100 == 0:
            print(f"{w['name']}: decoded {i}/{len(sigs)}")

    return {
        "wallet_name": w["name"],
        "wallet": wallet,
        "input_successful_transactions": len(sigs),
        "dex_swap_rows": len(trades),
        "buy_rows": sum(1 for x in trades if x["side"] == "BUY"),
        "sell_rows": sum(1 for x in trades if x["side"] == "SELL"),
        "non_dex_transactions": non_dex,
        "dex_without_classified_swap": no_swap,
        "rpc_errors": errors,
        "trades": trades,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--rpc", default=DEFAULT_RPC)
    p.add_argument("--sleep", type=float, default=0.15)
    args = p.parse_args()

    path = Path(args.input)
    if path.is_dir():
        files = sorted(path.glob("*.json"))
        if len(files) != 1:
            raise SystemExit(f"Expected one JSON in {path}, found {len(files)}")
        path = files[0]

    result = decode_wallet_file(path, args.rpc, args.sleep)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"RESULT {result['wallet_name']}: tx={result['input_successful_transactions']} "
        f"dex_rows={result['dex_swap_rows']} buys={result['buy_rows']} "
        f"sells={result['sell_rows']} errors={len(result['rpc_errors'])}"
    )


if __name__ == "__main__":
    main()
