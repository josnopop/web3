from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

LOCAL_TZ = timezone(timedelta(hours=8))

def load_replays(path: Path):
    files = sorted(path.rglob("*.json")) if path.is_dir() else [path]
    trades, summaries, seen = [], [], set()
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if "trades" not in d:
            continue
        summaries.extend(d.get("summaries") or [])
        for t in d.get("trades") or []:
            key = (t.get("signature"), t.get("wallet"), t.get("token_mint"), t.get("side"))
            if key in seen:
                continue
            seen.add(key)
            trades.append(t)
    return summaries, trades

def local_day(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(LOCAL_TZ).date().isoformat()

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def probe_size(win_rate: float) -> float:
    wr = clamp(float(win_rate or 0.8), 0.8, 1.0)
    return 0.02 + 0.03 * ((wr - 0.8) / 0.2)

def confirmed_target(win_rates) -> float:
    avg = sum(win_rates) / len(win_rates)
    avg = clamp(avg, 0.8, 1.0)
    return 0.08 + 0.04 * ((avg - 0.8) / 0.2)

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
    p.add_argument("--daily-budget-sol", type=float, default=1.0)
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

    candidates = []
    for mint, signals in by_mint.items():
        signals.sort(key=lambda x: (x["block_time"], x["slot"], x["wallet"]))
        first = signals[0]
        unique = [first]
        seen_wallets = {first["wallet"]}
        for s in signals[1:]:
            if s["wallet"] in seen_wallets:
                continue
            dt = s["block_time"] - first["block_time"]
            if dt <= args.confirm_seconds:
                unique.append(s)
                break
            if dt > args.confirm_seconds:
                break

        wr1 = float(first.get("source_win_rate") or 0.8)
        initial = probe_size(wr1)
        if len(unique) >= 2:
            wrs = [float(x.get("source_win_rate") or 0.8) for x in unique[:2]]
            desired_total = max(initial, confirmed_target(wrs))
            tier = "BUY"
        else:
            desired_total = initial
            tier = "PROBE"

        candidates.append({
            "mint": mint,
            "signals": unique[:2],
            "first": first,
            "tier": tier,
            "desired_total": desired_total,
            "day": local_day(first["block_time"]),
        })

    candidates.sort(key=lambda x: (x["first"]["block_time"], x["first"]["slot"], x["first"]["wallet"]))
    day_used = defaultdict(float)
    positions, skipped = [], []
    half = args.roundtrip_friction / 2.0

    for c in candidates:
        day = c["day"]
        remain = max(0.0, args.daily_budget_sol - day_used[day])
        if remain < 0.01:
            skipped.append({"date": day, "token_mint": c["mint"], "reason": "DAILY_BUDGET_EXHAUSTED"})
            continue
        alloc = min(c["desired_total"], remain)

        signals = c["signals"]
        legs = []
        if c["tier"] == "PROBE" or len(signals) == 1:
            legs = [(alloc, signals[0])]
        else:
            p1 = min(probe_size(float(signals[0].get("source_win_rate") or 0.8)), alloc)
            p2 = max(0.0, alloc - p1)
            legs = [(p1, signals[0])]
            if p2 > 1e-12:
                legs.append((p2, signals[1])]

        token_qty = 0.0
        entry_cost = 0.0
        raw_entry_notional = 0.0
        for sol_size, sig in legs:
            raw_price = float(sig["sol_per_token"])
            eff_price = raw_price * (1.0 + half)
            qty = sol_size / eff_price
            token_qty += qty
            entry_cost += sol_size
            raw_entry_notional += qty * raw_price

        day_used[day] += entry_cost
        entry_end_ts = max(x[1]["block_time"] for x in legs)
        signal_wallets = [x["wallet"] for x in signals]

        exits = []
        for w in signal_wallets:
            for t in by_wallet_mint.get((w, c["mint"]), []):
                if t.get("side") == "SELL" and t["block_time"] > entry_end_ts:
                    exits.append(t)
        exits.sort(key=lambda x: (x["block_time"], x["slot"], x["wallet"]))

        row = {
            "date": day,
            "token_mint": c["mint"],
            "tier": c["tier"],
            "signal_wallets": signal_wallets,
            "signal_win_rates": [x.get("source_win_rate") for x in signals],
            "entry_time": entry_end_ts,
            "entry_time_local": datetime.fromtimestamp(entry_end_ts, tz=timezone.utc).astimezone(LOCAL_TZ).isoformat(),
            "entry_sol": round(entry_cost, 9),
            "desired_entry_sol": round(c["desired_total"], 9),
            "entry_price_raw_sol_per_token": raw_entry_notional / token_qty if token_qty else None,
            "entry_price_effective_sol_per_token": entry_cost / token_qty if token_qty else None,
            "token_qty": token_qty,
            "first_signal_wallet": c["first"]["wallet"],
            "first_signal_win_rate": c["first"].get("source_win_rate"),
        }

        if not exits:
            row["status"] = "OPEN_AT_WINDOW_END"
            positions.append(row)
            continue

        ex = exits[0]
        raw_exit = float(ex["sol_per_token"])
        eff_exit = raw_exit * (1.0 - half)
        proceeds = token_qty * eff_exit
        pnl = proceeds - entry_cost
        row.update({
            "status": "CLOSED",
            "exit_wallet": ex["wallet"],
            "exit_time": ex["block_time"],
            "exit_time_local": datetime.fromtimestamp(ex["block_time"], tz=timezone.utc).astimezone(LOCAL_TZ).isoformat(),
            "exit_price_raw_sol_per_token": raw_exit,
            "exit_price_effective_sol_per_token": eff_exit,
            "exit_sol": round(proceeds, 9),
            "pnl_sol": round(pnl, 9),
            "return_pct": round((pnl / entry_cost) * 100 if entry_cost else 0.0, 4),
            "win": pnl > 0,
        })
        positions.append(row)

    days = sorted({f"2026-09-{d:02d}" for d in range(12, 19)})
    daily = []
    for d in days:
        rows = [x for x in positions if x["date"] == d]
        closed = [x for x in rows if x["status"] == "CLOSED"]
        opens = [x for x in rows if x["status"] != "CLOSED"]
        wins = sum(1 for x in closed if x.get("win"))
        losses = len(closed) - wins
        deployed = sum(x["entry_sol"] for x in rows)
        realized = sum(x.get("pnl_sol", 0.0) for x in closed)
        exit_sol = sum(x.get("exit_sol", 0.0) for x in closed)
        daily.append({
            "date": d,
            "budget_sol": args.daily_budget_sol,
            "deployed_sol": round(deployed, 9),
            "unused_sol": round(args.daily_budget_sol - deployed, 9),
            "positions": len(rows),
            "closed": len(closed),
            "open": len(opens),
            "wins": wins,
            "losses": losses,
            "closed_win_rate": round(wins / len(closed), 6) if closed else None,
            "closed_exit_sol": round(exit_sol, 9),
            "realized_pnl_sol": round(realized, 9),
            "realized_return_on_daily_budget_pct": round(realized / args.daily_budget_sol * 100, 4),
        })

    closed_all = [x for x in positions if x["status"] == "CLOSED"]
    open_all = [x for x in positions if x["status"] != "CLOSED"]
    total_budget = args.daily_budget_sol * len(days)
    total_deployed = sum(x["entry_sol"] for x in positions)
    total_net = sum(x.get("pnl_sol", 0.0) for x in closed_all)
    wins = sum(1 for x in closed_all if x.get("win"))
    losses = len(closed_all) - wins

    payload = {
        "window_local": ["2026-09-12T00:00:00+08:00", "2026-09-19T00:00:00+08:00"],
        "policy": {
            "daily_budget_sol": args.daily_budget_sol,
            "sizing": "win-rate weighted: single signal 0.02-0.05 SOL for 80%-100%; two-wallet confirmation targets 0.08-0.12 SOL by average win rate",
            "confirm_seconds": args.confirm_seconds,
            "roundtrip_friction": args.roundtrip_friction,
            "budget_rule": "gross new entries per local day capped at 1 SOL; no same-day capital recycling",
            "exit": "earliest subsequent SELL by a signaling S wallet anywhere inside the 7-day replay window",
        },
        "wallets_scanned_records": len(summaries),
        "decoded_trade_rows": len(trades),
        "unique_signal_tokens": len(by_mint),
        "total_budget_sol": total_budget,
        "total_deployed_sol": round(total_deployed, 9),
        "unused_budget_sol": round(total_budget - total_deployed, 9),
        "closed": len(closed_all),
        "open": len(open_all),
        "wins": wins,
        "losses": losses,
        "closed_win_rate": round(wins / len(closed_all), 6) if closed_all else None,
        "realized_net_pnl_sol": round(total_net, 9),
        "realized_return_on_total_7sol_budget_pct": round(total_net / total_budget * 100, 4),
        "realized_return_on_deployed_pct": round(total_net / total_deployed * 100, 4) if total_deployed else None,
        "daily": daily,
        "positions": positions,
        "skipped": skipped,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "total_budget_sol": payload["total_budget_sol"],
        "total_deployed_sol": payload["total_deployed_sol"],
        "closed": payload["closed"],
        "open": payload["open"],
        "wins": payload["wins"],
        "losses": payload["losses"],
        "net": payload["realized_net_pnl_sol"],
        "return_7sol_pct": payload["realized_return_on_total_7sol_budget_pct"],
    }, ensure_ascii=False))
    print(f"WROTE={out}")

if __name__ == "__main__":
    main()
