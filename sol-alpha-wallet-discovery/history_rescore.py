from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

from config import DEX_PROGRAM_IDS, QUOTE_MINTS, utc_window_for_local_day
from public_rpc_scan import DEFAULT_RPC, iter_signatures, rpc_call
from scoring import WalletFeatures, WindowMetrics, score_wallet


def _pubkey(x):
    return x.get("pubkey") if isinstance(x, dict) else x


def _ui_amount(b):
    ui = b.get("uiTokenAmount") or {}
    return int(ui.get("amount") or 0) / (10 ** int(ui.get("decimals") or 0))


def token_deltas(meta, wallet):
    pre, post = defaultdict(float), defaultdict(float)
    for b in meta.get("preTokenBalances") or []:
        if b.get("owner") == wallet:
            pre[b["mint"]] += _ui_amount(b)
    for b in meta.get("postTokenBalances") or []:
        if b.get("owner") == wallet:
            post[b["mint"]] += _ui_amount(b)
    return {
        mint: post[mint] - pre[mint]
        for mint in set(pre) | set(post)
        if abs(post[mint] - pre[mint]) > 1e-15
    }


def native_sol_delta(tx, wallet):
    keys = [_pubkey(x) for x in tx["transaction"]["message"].get("accountKeys") or []]
    if wallet not in keys:
        return 0.0
    i = keys.index(wallet)
    meta = tx.get("meta") or {}
    pre, post = meta.get("preBalances") or [], meta.get("postBalances") or []
    if i >= len(pre) or i >= len(post):
        return 0.0
    return (post[i] - pre[i]) / 1e9


def touches_dex(tx):
    keys = {_pubkey(x) for x in tx["transaction"]["message"].get("accountKeys") or []}
    return bool(keys & DEX_PROGRAM_IDS)


def decode_one(tx, wallet, signature):
    if not tx or (tx.get("meta") or {}).get("err") is not None or not touches_dex(tx):
        return None
    meta = tx.get("meta") or {}
    deltas = token_deltas(meta, wallet)
    assets = [(m, d) for m, d in deltas.items() if m not in QUOTE_MINTS]
    # Fail closed on complex multi-asset transactions; avoids false trade rows.
    if len(assets) != 1:
        return None
    mint, delta = assets[0]
    sol = native_sol_delta(tx, wallet)
    if delta > 0 and sol < -0.0005:
        side, quote = "BUY", -sol
    elif delta < 0 and sol > 0.0005:
        side, quote = "SELL", sol
    else:
        return None
    return {
        "signature": signature,
        "block_time": int(tx.get("blockTime") or 0),
        "slot": int(tx.get("slot") or 0),
        "mint": mint,
        "side": side,
        "quote_sol": float(quote),
    }


def window_metrics(trades, cutoff_ts, days):
    start = cutoff_ts - days * 86400
    rows = [x for x in trades if start <= x["block_time"] < cutoff_ts]
    per = defaultdict(lambda: {"spent": 0.0, "recv": 0.0, "first_buy": None, "last_sell": None})
    for x in rows:
        p = per[x["mint"]]
        if x["side"] == "BUY":
            p["spent"] += x["quote_sol"]
            p["first_buy"] = x["block_time"] if p["first_buy"] is None else min(p["first_buy"], x["block_time"])
        else:
            p["recv"] += x["quote_sol"]
            p["last_sell"] = x["block_time"] if p["last_sell"] is None else max(p["last_sell"], x["block_time"])

    closed = []
    for mint, p in per.items():
        if p["spent"] > 0 and p["recv"] > 0:
            pnl = p["recv"] - p["spent"]
            roi = pnl / p["spent"]
            hold = 0.0
            if p["first_buy"] and p["last_sell"] and p["last_sell"] >= p["first_buy"]:
                hold = (p["last_sell"] - p["first_buy"]) / 60.0
            closed.append((mint, pnl, roi, hold, p["spent"]))

    positives = sorted((max(x[1], 0.0) for x in closed), reverse=True)
    pos_total = sum(positives)
    top1 = positives[0] / pos_total if pos_total > 0 and positives else 0.0
    top3 = sum(positives[:3]) / pos_total if pos_total > 0 else 0.0

    slots = Counter(x["slot"] for x in rows)
    same_slot = sum(n for n in slots.values() if n > 1) / len(rows) if rows else 0.0
    mins = Counter(x["block_time"] // 60 for x in rows)
    max_tpm = max(mins.values()) if mins else 0

    return WindowMetrics(
        days=days,
        dex_txs=len({x["signature"] for x in rows}),
        active_days=len({x["block_time"] // 86400 for x in rows}),
        distinct_tokens=len({x["mint"] for x in rows}),
        realized_pnl_sol=sum(x[1] for x in closed),
        win_tokens=sum(1 for x in closed if x[1] > 0),
        closed_tokens=len(closed),
        median_token_roi=statistics.median([x[2] for x in closed]) if closed else 0.0,
        top1_profit_concentration=top1,
        top3_profit_concentration=top3,
        median_hold_minutes=statistics.median([x[3] for x in closed]) if closed else 0.0,
        rug_like_tokens=sum(1 for x in closed if x[4] >= 0.05 and x[2] <= -0.90),
        same_slot_ratio=same_slot,
        max_trades_per_minute=max_tpm,
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--candidates", required=True)
    p.add_argument("--cutoff-date", default="2026-09-16")
    p.add_argument("--shard", type=int, required=True)
    p.add_argument("--shards", type=int, default=16)
    p.add_argument("--pages", type=int, default=5)
    p.add_argument("--rpc", default=DEFAULT_RPC)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    cutoff, _ = utc_window_for_local_day(date.fromisoformat(args.cutoff_date))
    cutoff_ts = int(cutoff.timestamp())
    start_ts = cutoff_ts - 30 * 86400

    candidates = json.loads(Path(args.candidates).read_text(encoding="utf-8"))["wallets"]
    selected = [w for i, w in enumerate(candidates) if i % args.shards == args.shard]

    output = []
    for wi, src in enumerate(selected, 1):
        wallet = src["wallet"]
        sigs = list(iter_signatures(wallet, start_ts, cutoff_ts, max_pages=args.pages, rpc_url=args.rpc, sleep_s=0.3))
        truncated = len(sigs) >= args.pages * 1000
        decoded, errors = [], 0
        if not truncated:
            for j, s in enumerate(sigs, 1):
                try:
                    tx = rpc_call(
                        "getTransaction",
                        [s.signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
                        args.rpc,
                        retries=6,
                    )
                    row = decode_one(tx, wallet, s.signature)
                    if row:
                        decoded.append(row)
                except Exception:
                    errors += 1
                if j % 100 == 0:
                    time.sleep(0.2)

        if truncated or errors > max(5, len(sigs) * 0.02):
            output.append({
                "wallet": wallet,
                "source_score": src.get("score"),
                "status": "REJECT_INCOMPLETE_HISTORY",
                "signature_count": len(sigs),
                "rpc_errors": errors,
                "truncated": truncated,
            })
            print(f"{wallet[:8]} incomplete sigs={len(sigs)} errors={errors}")
            continue

        w7 = window_metrics(decoded, cutoff_ts, 7)
        w15 = window_metrics(decoded, cutoff_ts, 15)
        w30 = window_metrics(decoded, cutoff_ts, 30)
        score = score_wallet(WalletFeatures(wallet=wallet, w7=w7, w15=w15, w30=w30))
        output.append({
            "wallet": wallet,
            "source_score": src.get("score"),
            "status": "KEEP" if score.wallet_class != "Rejected" else "REJECT",
            "signature_count": len(sigs),
            "decoded_dex_trades": len(decoded),
            "rpc_errors": errors,
            "truncated": False,
            "chain_score": score.__dict__,
            "w7": w7.__dict__,
            "w15": w15.__dict__,
            "w30": w30.__dict__,
        })
        print(
            f"{wallet[:8]} sigs={len(sigs)} dex={len(decoded)} "
            f"closed30={w30.closed_tokens} pnl30={w30.realized_pnl_sol:.3f} "
            f"wr30={w30.win_tokens/max(w30.closed_tokens,1):.1%} "
            f"score={score.score:.1f} {score.wallet_class}"
        )

    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"cutoff_utc": cutoff.isoformat(), "rows": output}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("SUMMARY", json.dumps({
        "wallets": len(output),
        "keep": sum(1 for x in output if x["status"] == "KEEP"),
        "reject": sum(1 for x in output if x["status"] == "REJECT"),
        "incomplete": sum(1 for x in output if x["status"] == "REJECT_INCOMPLETE_HISTORY"),
    }))
    print(f"WROTE={path}")


if __name__ == "__main__":
    main()
