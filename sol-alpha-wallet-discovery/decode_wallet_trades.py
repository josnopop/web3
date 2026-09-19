from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

from config import DEX_PROGRAM_IDS, QUOTE_MINTS, USDC, USDT, WSOL

PUBLICNODE_RPC = "https://solana-rpc.publicnode.com"


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


def _program_ids(tx):
    ids = set()
    msg = tx.get("transaction", {}).get("message", {})
    for key in msg.get("accountKeys") or []:
        pk = _pubkey(key)
        if pk:
            ids.add(pk)
    for ins in msg.get("instructions") or []:
        pid = ins.get("programId") if isinstance(ins, dict) else None
        if pid:
            ids.add(pid)
    for group in (tx.get("meta") or {}).get("innerInstructions") or []:
        for ins in group.get("instructions") or []:
            pid = ins.get("programId") if isinstance(ins, dict) else None
            if pid:
                ids.add(pid)
    return ids


def touches_dex(tx):
    return bool(_program_ids(tx) & DEX_PROGRAM_IDS)


def classify_asset(asset_delta, quotes, sol_delta):
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


def batch_transactions(signatures, rpc_url, batch_size=20, retries=6):
    for start in range(0, len(signatures), batch_size):
        chunk = signatures[start:start + batch_size]
        payload = []
        for i, sig in enumerate(chunk):
            payload.append({
                "jsonrpc": "2.0",
                "id": i,
                "method": "getTransaction",
                "params": [
                    sig,
                    {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0},
                ],
            })
        body = json.dumps(payload).encode("utf-8")
        last = None
        for attempt in range(retries):
            req = urllib.request.Request(
                rpc_url,
                data=body,
                headers={"content-type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    result = json.loads(r.read().decode("utf-8"))
                if not isinstance(result, list):
                    raise RuntimeError(f"Batch RPC returned non-list: {type(result).__name__}")
                by_id = {int(x.get("id")): x for x in result}
                for i, sig in enumerate(chunk):
                    item = by_id.get(i, {})
                    if item.get("error"):
                        yield sig, None, f"RPC error: {item['error']}"
                    else:
                        yield sig, item.get("result"), None
                break
            except urllib.error.HTTPError as e:
                last = e
                if e.code != 429 or attempt == retries - 1:
                    for sig in chunk:
                        yield sig, None, f"HTTPError {e.code}: {e}"
                    break
                wait = float(e.headers.get("Retry-After") or min(2 ** attempt, 20))
                print(f"[batch] 429; retry {attempt + 1}/{retries} in {wait}s")
                time.sleep(wait)
            except Exception as e:
                last = e
                if attempt == retries - 1:
                    for sig in chunk:
                        yield sig, None, f"{type(e).__name__}: {e}"
                    break
                time.sleep(min(2 ** attempt, 20))
        if last is None:
            time.sleep(0.03)


def decode_wallet_file(path: Path, rpc_url: str, batch_size: int):
    src = json.loads(path.read_text(encoding="utf-8"))
    w = src["wallets"][0]
    wallet = w["address"]
    trades = []
    errors = []
    non_dex = 0
    no_swap = 0

    sigs = w.get("signatures") or []
    for i, (sig, tx, error) in enumerate(
        batch_transactions(sigs, rpc_url, batch_size=batch_size), 1
    ):
        if error:
            errors.append({"signature": sig, "error": error})
            continue
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
            if not side:
                continue
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
    p.add_argument("--rpc", default=PUBLICNODE_RPC)
    p.add_argument("--batch-size", type=int, default=20)
    args = p.parse_args()

    path = Path(args.input)
    if path.is_dir():
        files = sorted(path.glob("*.json"))
        if len(files) != 1:
            raise SystemExit(f"Expected one JSON in {path}, found {len(files)}")
        path = files[0]

    result = decode_wallet_file(path, args.rpc, args.batch_size)
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
