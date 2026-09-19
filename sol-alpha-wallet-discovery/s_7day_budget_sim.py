from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

LOCAL_TZ = timezone(timedelta(hours=8))

def load_replays(path: Path):
    files = sorted(path.rglob("*.json")) if path.is_dir() else [path]
    trades, summaries, seen = [], [], set()
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

def local_day(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(LOCAL_TZ).date().isoformat()

def wallet_size(win_rate: float) -> float:
    wr = float(win_rate or 0.0)
    if wr >= 0.98:
        return 0.060
    if wr >= 0.95:
        return 0.055
    if wr >= 0.90:
        return 0.045
    if wr >= 0.85:
        return 0.035
    return 0.025

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
    p.add_argument("--csv", required=True)
    p.add_argument("--daily-budget-sol", type=float, default=1.0)
    p.add_argument("--confirm-seconds", type=int, default=600)
    p.add_argument("--roundtrip-friction", type=float, default=0.015)
    args = p.parse_args()

    summaries, trades = load_replays(Path(args.replays))
    trades.sort(key=lambda x: (x["block_time"], x["slot"], x["wallet"]))
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
        candidates.append((first["block_time"], mint, unique))
    candidates.sort()

    daily_used = defaultdict(float)
    half_friction = args.roundtrip_friction / 2.0
    positions = []
    skipped_budget = []

    for _, mint, signals in candidates:
        first = signals[0]
        d1 = local_day(first["block_time"])
        plan1 = wallet_size(first.get("source_win_rate"))
        rem1 = max(0.0, args.daily_budget_sol - daily_used[d1])
        leg1 = min(plan1, rem1)
        if leg1 < 0.009999:
            skipped_budget.append({"token_mint": mint, "time": first["block_time"], "day": d1, "reason": "daily_budget_exhausted"})
            continue

        legs = [(leg1, first)]
        daily_used[d1] += leg1

        if len(signals) >= 2:
            second = signals[1]
            d2 = local_day(second["block_time"])
            plan2 = wallet_size(second.get("source_win_rate"))
            # Keep confirmed total position at or below 0.10 SOL, matching the prior BUY cap.
            plan2 = min(plan2, max(0.0, 0.10 - leg1))
            rem2 = max(0.0, args.daily_budget_sol - daily_used[d2])
            leg2 = min(plan2, rem2)
            if leg2 >= 0.009999:
                legs.append((leg2, second))
                daily_used[d2] += leg2

        token_qty = 0.0
        entry_cost = 0.0
        leg_details = []
        for sol_size, sig in legs:
            raw = float(sig["sol_per_token"])
            eff = raw * (1.0 + half_friction)
            qty = sol_size / eff
            token_qty += qty
            entry_cost += sol_size
            leg_details.append({
                "wallet": sig["wallet"],
                "time": sig["block_time"],
                "day": local_day(sig["block_time"]),
                "win_rate": sig.get("source_win_rate"),
                "raw_price_sol_per_token": raw,
                "effective_price_sol_per_token": eff,
                "entry_sol": round(sol_size, 9),
                "token_qty": qty,
            })

        entry_end_ts = max(sig["block_time"] for _, sig in legs)
        actual_wallets = [sig["wallet"] for _, sig in legs]
        exits = []
        for w in actual_wallets:
            for t in by_wallet_mint.get((w, mint), []):
                if t.get("side") == "SELL" and t["block_time"] > entry_end_ts:
                    exits.append(t)
        exits.sort(key=lambda x: (x["block_time"], x["slot"], x["wallet"]))

        avg_entry = entry_cost / token_qty if token_qty else 0.0
        pos = {
            "entry_day": local_day(first["block_time"]),
            "token_mint": mint,
            "tier": "BUY" if len(legs) >= 2 else "PROBE",
            "signal_wallets": actual_wallets,
            "first_signal_time": first["block_time"],
            "entry_end_time": entry_end_ts,
            "entry_sol": round(entry_cost, 9),
            "avg_effective_entry_price_sol_per_token": avg_entry,
            "legs": leg_details,
            "status": "OPEN",
        }

        if exits:
            ex = exits[0]
            raw_exit = float(ex["sol_per_token"])
            eff_exit = raw_exit * (1.0 - half_friction)
            proceeds = token_qty * eff_exit
            pnl = proceeds - entry_cost
            ret = pnl / entry_cost if entry_cost else 0.0
            pos.update({
                "status": "CLOSED",
                "exit_wallet": ex["wallet"],
                "exit_time": ex["block_time"],
                "exit_day": local_day(ex["block_time"]),
                "raw_exit_price_sol_per_token": raw_exit,
                "effective_exit_price_sol_per_token": eff_exit,
                "exit_sol": round(proceeds, 9),
                "pnl_sol": round(pnl, 9),
                "return_pct": round(ret * 100.0, 4),
                "win": pnl > 0,
            })
        positions.append(pos)

    days = sorted({local_day(t["block_time"]) for t in trades})
    day_rows = []
    for day in days:
        ps = [x for x in positions if x["entry_day"] == day]
        closed = [x for x in ps if x["status"] == "CLOSED"]
        openp = [x for x in ps if x["status"] != "CLOSED"]
        wins = sum(1 for x in closed if x["win"])
        losses = len(closed) - wins
        deployed = sum(x["entry_sol"] for x in ps)
        exit_sol = sum(x.get("exit_sol", 0.0) for x in closed)
        pnl = sum(x.get("pnl_sol", 0.0) for x in closed)
        day_rows.append({
            "day": day,
            "budget_sol": args.daily_budget_sol,
            "deployed_sol": round(deployed, 9),
            "unused_budget_sol": round(max(0.0, args.daily_budget_sol - deployed), 9),
            "entries": len(ps),
            "closed": len(closed),
            "open": len(openp),
            "wins": wins,
            "losses": losses,
            "closed_win_rate": round(wins / max(1, len(closed)), 6),
            "closed_exit_sol": round(exit_sol, 9),
            "realized_pnl_sol": round(pnl, 9),
            "realized_return_on_total_deployed_pct": round(pnl / deployed * 100.0, 4) if deployed else 0.0,
        })

    closed_all = [x for x in positions if x["status"] == "CLOSED"]
    open_all = [x for x in positions if x["status"] != "CLOSED"]
    total_deployed = sum(x["entry_sol"] for x in positions)
    total_exit = sum(x.get("exit_sol", 0.0) for x in closed_all)
    total_pnl = sum(x.get("pnl_sol", 0.0) for x in closed_all)
    wins = sum(1 for x in closed_all if x["win"])

    payload = {
        "period_local": [days[0] if days else None, days[-1] if days else None],
        "policy": {
            "daily_new_entry_budget_sol": args.daily_budget_sol,
            "budget_recycling_same_day": False,
            "sizing_by_source_win_rate": {
                ">=98%": 0.060,
                "95%-<98%": 0.055,
                "90%-<95%": 0.045,
                "85%-<90%": 0.035,
                "80%-<85%": 0.025,
            },
            "second_independent_S_confirm_seconds": args.confirm_seconds,
            "confirmed_position_cap_sol": 0.10,
            "roundtrip_friction": args.roundtrip_friction,
            "exit": "earliest subsequent SELL by an actually-followed signaling S wallet within the 7-day replay window",
            "open_positions": "reported separately; unrealized PnL not guessed",
        },
        "wallets_scanned_unique": len({s.get("wallet") for s in summaries if s.get("wallet")}),
        "decoded_trade_rows": len(trades),
        "unique_signal_tokens": len(by_mint),
        "skipped_for_budget": len(skipped_budget),
        "total_positions": len(positions),
        "closed": len(closed_all),
        "open": len(open_all),
        "wins": wins,
        "losses": len(closed_all) - wins,
        "closed_win_rate": round(wins / max(1, len(closed_all)), 6),
        "total_deployed_sol": round(total_deployed, 9),
        "total_closed_exit_sol": round(total_exit, 9),
        "realized_net_pnl_sol": round(total_pnl, 9),
        "realized_return_on_total_deployed_pct": round(total_pnl / total_deployed * 100.0, 4) if total_deployed else 0.0,
        "open_principal_sol": round(sum(x["entry_sol"] for x in open_all), 9),
        "daily": day_rows,
        "positions": positions,
        "skipped_budget_examples": skipped_budget[:50],
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    csv_path = Path(args.csv)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "entry_day","token_mint","tier","signal_wallets","first_signal_time_local",
            "entry_end_time_local","entry_sol","avg_effective_entry_price_sol_per_token",
            "status","exit_wallet","exit_time_local","effective_exit_price_sol_per_token",
            "exit_sol","pnl_sol","return_pct"
        ])
        for x in positions:
            fmt = lambda ts: datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(LOCAL_TZ).isoformat() if ts else ""
            w.writerow([
                x["entry_day"], x["token_mint"], x["tier"], "|".join(x["signal_wallets"]),
                fmt(x["first_signal_time"]), fmt(x["entry_end_time"]), x["entry_sol"],
                x["avg_effective_entry_price_sol_per_token"], x["status"], x.get("exit_wallet",""),
                fmt(x.get("exit_time")), x.get("effective_exit_price_sol_per_token",""),
                x.get("exit_sol",""), x.get("pnl_sol",""), x.get("return_pct",""),
            ])

    print(
        f"DAYS={len(days)} POS={len(positions)} CLOSED={len(closed_all)} OPEN={len(open_all)} "
        f"W={wins} L={len(closed_all)-wins} DEPLOYED={total_deployed:.6f} "
        f"NET={total_pnl:+.6f} SOL RETURN={payload['realized_return_on_total_deployed_pct']:+.2f}%"
    )
    for d in day_rows:
        print(
            f"DAY {d['day']} deployed={d['deployed_sol']:.3f} entries={d['entries']} "
            f"closed={d['closed']} open={d['open']} W={d['wins']} L={d['losses']} "
            f"pnl={d['realized_pnl_sol']:+.6f}"
        )
    print(f"WROTE={out}")
    print(f"WROTE={csv_path}")

if __name__ == "__main__":
    main()
