from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def load_replays(path: Path):
    files = sorted(path.glob("*.json")) if path.is_dir() else [path]
    trades = []
    summaries = []
    seen = set()
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        summaries.extend(d.get("summaries") or [])
        for t in d.get("trades") or []:
            key = (t.get("signature"), t.get("wallet"), t.get("token_mint"), t.get("side"))
            if key in seen:
                continue
            seen.add(key)
            trades.append(t)
    return summaries, trades


def first_buy_per_wallet(trades):
    out = {}
    for t in sorted(trades, key=lambda x: (x["block_time"], x["slot"], x["wallet"])):
        if t.get("side") != "BUY":
            continue
        key = (t["wallet"], t["token_mint"])
        out.setdefault(key, t)
    return list(out.values())


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--replays", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--probe-sol", type=float, default=0.03)
    p.add_argument("--buy-sol", type=float, default=0.10)
    p.add_argument("--confirm-seconds", type=int, default=600)
    p.add_argument("--roundtrip-friction", type=float, default=0.015)
    args = p.parse_args()

    summaries, trades = load_replays(Path(args.replays))
    buys = first_buy_per_wallet(trades)

    by_mint = defaultdict(list)
    by_wallet_mint = defaultdict(list)
    for t in trades:
        by_wallet_mint[(t["wallet"], t["token_mint"])].append(t)
    for t in buys:
        by_mint[t["token_mint"]].append(t)

    half_friction = args.roundtrip_friction / 2.0
    closed = []
    open_positions = []

    for mint, signals in by_mint.items():
        signals.sort(key=lambda x: (x["block_time"], x["slot"], x["wallet"]))
        first = signals[0]
        unique = [first]
        seen_wallets = {first["wallet"]}
        for s in signals[1:]:
            if s["wallet"] in seen_wallets:
                continue
            if s["block_time"] - first["block_time"] <= args.confirm_seconds:
                unique.append(s)
                seen_wallets.add(s["wallet"])
                break
            if s["block_time"] - first["block_time"] > args.confirm_seconds:
                break

        legs = [(args.probe_sol, first)]
        tier = "PROBE"
        if len(unique) >= 2:
            legs.append((args.buy_sol - args.probe_sol, unique[1]))
            tier = "BUY"

        entry_end_ts = max(x[1]["block_time"] for x in legs)
        token_qty = 0.0
        entry_cost = 0.0
        for sol_size, sig in legs:
            raw_price = float(sig["sol_per_token"])
            effective_price = raw_price * (1.0 + half_friction)
            token_qty += sol_size / effective_price
            entry_cost += sol_size

        signal_wallets = [x["wallet"] for x in unique]
        exits = []
        for w in signal_wallets:
            for t in by_wallet_mint.get((w, mint), []):
                if t.get("side") == "SELL" and t["block_time"] > entry_end_ts:
                    exits.append(t)
        exits.sort(key=lambda x: (x["block_time"], x["slot"], x["wallet"]))

        base = {
            "token_mint": mint,
            "tier": tier,
            "signal_wallets": signal_wallets,
            "signal_times": [x["block_time"] for x in unique],
            "entry_sol": round(entry_cost, 9),
            "first_signal_wallet": first["wallet"],
            "first_signal_win_rate": first.get("source_win_rate"),
            "first_signal_source_pnl_sol": first.get("source_net_pnl_sol"),
        }

        if not exits:
            base["status"] = "OPEN_AT_DAY_END"
            base["token_qty"] = token_qty
            open_positions.append(base)
            continue

        ex = exits[0]
        raw_exit = float(ex["sol_per_token"])
        effective_exit = raw_exit * (1.0 - half_friction)
        proceeds = token_qty * effective_exit
        pnl = proceeds - entry_cost
        ret = pnl / entry_cost if entry_cost else 0.0
        base.update({
            "status": "CLOSED",
            "exit_wallet": ex["wallet"],
            "exit_time": ex["block_time"],
            "exit_sol": round(proceeds, 9),
            "pnl_sol": round(pnl, 9),
            "return_pct": round(ret * 100, 4),
            "win": pnl > 0,
        })
        closed.append(base)

    closed.sort(key=lambda x: x["signal_times"][0])
    open_positions.sort(key=lambda x: x["signal_times"][0])

    total_deployed = sum(x["entry_sol"] for x in closed)
    net = sum(x["pnl_sol"] for x in closed)
    wins = sum(1 for x in closed if x["win"])
    losses = len(closed) - wins

    payload = {
        "policy": {
            "single_S_signal": f"{args.probe_sol:.2f} SOL PROBE",
            "second_independent_S_within_seconds": args.confirm_seconds,
            "confirmed_position_sol": args.buy_sol,
            "exit": "earliest subsequent SELL by a signaling S wallet within the same 24h window",
            "roundtrip_friction": args.roundtrip_friction,
            "open_positions": "reported separately; excluded from realized PnL",
        },
        "wallets_scanned": len(summaries),
        "wallet_summaries": summaries,
        "decoded_trade_rows": len(trades),
        "unique_signal_tokens": len(by_mint),
        "closed_trades": len(closed),
        "open_positions": len(open_positions),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / max(len(closed), 1), 6),
        "realized_deployed_sol": round(total_deployed, 9),
        "realized_net_pnl_sol": round(net, 9),
        "realized_return_on_deployed_pct": round(net / max(total_deployed, 1e-12) * 100, 4),
        "trades": closed,
        "open": open_positions,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"WALLETS={payload['wallets_scanned']} SIGNAL_TOKENS={len(by_mint)} "
        f"CLOSED={len(closed)} OPEN={len(open_positions)} W={wins} L={losses} "
        f"NET={net:.6f} SOL RETURN={payload['realized_return_on_deployed_pct']:.2f}%"
    )
    for x in closed:
        print(
            f"{x['tier']} {x['token_mint']} entry={x['entry_sol']:.2f} "
            f"ret={x['return_pct']:+.2f}% pnl={x['pnl_sol']:+.6f}"
        )
    print(f"WROTE={out}")


if __name__ == "__main__":
    main()
