from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

from solders.pubkey import Pubkey

from config import PUMPFUN_PROGRAM_ID
from harvest_madeonsol_history import DEX_RE, KOL_RE, clean, context_metrics, fetch, section_at
from public_rpc_scan import DEFAULT_RPC, rpc_call

PUMPFUN = Pubkey.from_string(PUMPFUN_PROGRAM_ID)


def _pubkey(x):
    return x.get("pubkey") if isinstance(x, dict) else x


def _ui_amount(b):
    ui = b.get("uiTokenAmount") or {}
    return int(ui.get("amount") or 0) / (10 ** int(ui.get("decimals") or 0))


def _token_delta(meta, wallet, mint):
    pre = post = 0.0
    for b in meta.get("preTokenBalances") or []:
        if b.get("owner") == wallet and b.get("mint") == mint:
            pre += _ui_amount(b)
    for b in meta.get("postTokenBalances") or []:
        if b.get("owner") == wallet and b.get("mint") == mint:
            post += _ui_amount(b)
    return post - pre


def _sol_delta(tx, wallet):
    keys = [_pubkey(x) for x in tx["transaction"]["message"].get("accountKeys") or []]
    if wallet not in keys:
        return 0.0
    i = keys.index(wallet)
    meta = tx.get("meta") or {}
    pre = meta.get("preBalances") or []
    post = meta.get("postBalances") or []
    if i >= len(pre) or i >= len(post):
        return 0.0
    return (post[i] - pre[i]) / 1e9


def _first_signer(tx):
    for x in tx["transaction"]["message"].get("accountKeys") or []:
        if isinstance(x, dict) and x.get("signer"):
            return str(x["pubkey"])
    return None


def _curve_signatures(curve: str, max_pages: int, rpc: str):
    out = []
    before = None
    for _ in range(max_pages):
        opts = {"limit": 1000}
        if before:
            opts["before"] = before
        rows = rpc_call("getSignaturesForAddress", [curve, opts], rpc) or []
        if not rows:
            break
        out.extend(r for r in rows if r.get("err") is None and r.get("blockTime"))
        before = rows[-1]["signature"]
        if len(rows) < 1000:
            break
        time.sleep(0.25)
    out.sort(key=lambda x: (x["blockTime"], x["slot"]))
    return out


def reverse_early_buyers(mint: str, buyers: int, tx_limit: int, max_pages: int, rpc: str):
    try:
        mint_pk = Pubkey.from_string(mint)
        curve, _ = Pubkey.find_program_address([b"bonding-curve", bytes(mint_pk)], PUMPFUN)
        sigs = _curve_signatures(str(curve), max_pages, rpc)
    except Exception:
        return {"mint": mint, "buyers": [], "errors": 1, "signature_rows": 0}

    earliest = sigs[0]["blockTime"] if sigs else None
    found = []
    seen = set()
    errors = 0
    for row in sigs[:tx_limit]:
        if len(found) >= buyers:
            break
        try:
            tx = rpc_call(
                "getTransaction",
                [row["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
                rpc,
                retries=4,
            )
            if not tx or (tx.get("meta") or {}).get("err") is not None:
                continue
            wallet = _first_signer(tx)
            if not wallet or wallet in seen:
                continue
            td = _token_delta(tx.get("meta") or {}, wallet, mint)
            sd = _sol_delta(tx, wallet)
            if td > 0 and sd < -0.0001:
                seen.add(wallet)
                found.append({
                    "wallet": wallet,
                    "rank": len(found) + 1,
                    "seconds_from_curve_start": row["blockTime"] - earliest if earliest else None,
                    "approx_sol_out": round(abs(sd), 9),
                    "block_time": row["blockTime"],
                })
        except Exception:
            errors += 1
        time.sleep(0.02)
    return {"mint": mint, "buyers": found, "errors": errors, "signature_rows": len(sigs)}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--cutoff-date", required=True, help="Last dated evidence day, e.g. 2026-09-13")
    p.add_argument("--lookback-days", type=int, default=30)
    p.add_argument("--token-limit", type=int, default=30)
    p.add_argument("--buyers-per-token", type=int, default=15)
    p.add_argument("--tx-limit", type=int, default=140)
    p.add_argument("--curve-pages", type=int, default=2)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--rpc", default=DEFAULT_RPC)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    cutoff = date.fromisoformat(args.cutoff_date)
    start = cutoff - timedelta(days=args.lookback_days - 1)

    dated_wallets = defaultdict(lambda: {
        "dates": set(),
        "sections": defaultdict(int),
        "win_rates": [],
        "pnl": [],
    })
    tokens = defaultdict(lambda: {"dates": set(), "sections": defaultdict(int)})

    day = start
    pages = []
    while day <= cutoff:
        ds = day.isoformat()
        try:
            raw = fetch(ds)
        except Exception as e:
            pages.append({"date": ds, "ok": False, "error": f"{type(e).__name__}: {e}"})
            day += timedelta(days=1)
            continue

        page_wallets = set()
        page_tokens = set()
        for m in KOL_RE.finditer(raw):
            addr = m.group(1)
            sec = section_at(raw, m.start())
            met = context_metrics(raw, m.start(), m.end())
            w = dated_wallets[addr]
            w["dates"].add(ds)
            w["sections"][sec] += 1
            if met.get("win_rate") is not None:
                w["win_rates"].append(float(met["win_rate"]))
            if met.get("pnl_sol") is not None:
                w["pnl"].append(float(met["pnl_sol"]))
            page_wallets.add(addr)

        for m in DEX_RE.finditer(raw):
            mint = m.group(1)
            sec = section_at(raw, m.start())
            t = tokens[mint]
            t["dates"].add(ds)
            t["sections"][sec] += 1
            page_tokens.add(mint)

        pages.append({"date": ds, "ok": True, "wallets": len(page_wallets), "token_mints": len(page_tokens)})
        day += timedelta(days=1)

    ranked_tokens = []
    for mint, t in tokens.items():
        score = (
            12 * t["sections"].get("signal", 0)
            + 6 * t["sections"].get("convergence", 0)
            + 3 * t["sections"].get("big_buy", 0)
            + len(t["dates"])
        )
        ranked_tokens.append((score, mint))
    ranked_tokens.sort(key=lambda x: (-x[0], x[1]))
    selected_mints = [m for _, m in ranked_tokens[: args.token_limit]]

    early_results = []
    if selected_mints:
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futs = {
                pool.submit(
                    reverse_early_buyers,
                    mint,
                    args.buyers_per_token,
                    args.tx_limit,
                    args.curve_pages,
                    args.rpc,
                ): mint
                for mint in selected_mints
            }
            for fut in as_completed(futs):
                early_results.append(fut.result())

    merged = {}

    def ensure(wallet):
        return merged.setdefault(wallet, {
            "wallet": wallet,
            "sources": set(),
            "dated_days_seen": 0,
            "dated_sections": {},
            "early_tokens": set(),
            "early_ranks": [],
            "early_latencies": [],
            "early_sol": 0.0,
            "visible_win_rates": [],
            "visible_pnl_sol": [],
        })

    for wallet, w in dated_wallets.items():
        row = ensure(wallet)
        row["sources"].add("MadeOnSol dated Daily Alpha")
        row["dated_days_seen"] = len(w["dates"])
        row["dated_sections"] = dict(w["sections"])
        row["visible_win_rates"].extend(w["win_rates"])
        row["visible_pnl_sol"].extend(w["pnl"])

    for result in early_results:
        mint = result["mint"]
        for b in result.get("buyers") or []:
            row = ensure(b["wallet"])
            row["sources"].add("Pump.fun dated-token Early Buyers")
            row["early_tokens"].add(mint)
            row["early_ranks"].append(int(b["rank"]))
            if b.get("seconds_from_curve_start") is not None:
                row["early_latencies"].append(float(b["seconds_from_curve_start"]))
            row["early_sol"] += float(b.get("approx_sol_out") or 0.0)

    wallets = []
    for wallet, row in merged.items():
        sec = row["dated_sections"]
        days = row["dated_days_seen"]
        repeat_early = len(row["early_tokens"])
        avg_rank = statistics.mean(row["early_ranks"]) if row["early_ranks"] else None
        avg_latency = statistics.mean(row["early_latencies"]) if row["early_latencies"] else None
        visible_wr = statistics.mean(row["visible_win_rates"]) if row["visible_win_rates"] else None
        visible_pnl = sum(row["visible_pnl_sol"]) if row["visible_pnl_sol"] else None

        # Discovery score is intentionally broad. It only prioritizes historical evidence;
        # it does NOT decide whether the wallet may be copied.
        score = (
            min(days, 15) * 2.0
            + sec.get("top_performer", 0) * 10.0
            + sec.get("signal", 0) * 6.0
            + sec.get("convergence", 0) * 5.0
            + sec.get("active", 0) * 2.0
            + repeat_early * 9.0
            + (max(0.0, 10.0 - avg_rank) if avg_rank is not None else 0.0)
            + min(10.0, row["early_sol"] / 5.0)
        )
        if visible_wr is not None:
            score += max(0.0, (visible_wr - 50.0) / 4.0)
        if visible_pnl is not None and visible_pnl > 0:
            score += min(10.0, visible_pnl / 100.0)

        if repeat_early >= 2 or sec.get("signal", 0) >= 2 or sec.get("top_performer", 0) >= 1:
            discovery_class = "PRIMARY"
        elif days >= 2 or row["early_ranks"]:
            discovery_class = "RADAR"
        else:
            discovery_class = "WATCH"

        wallets.append({
            "wallet": wallet,
            "score": round(score, 4),
            "discovery_class": discovery_class,
            "sources": sorted(row["sources"]),
            "source_count": len(row["sources"]),
            "dated_days_seen": days,
            "dated_sections": sec,
            "visible_win_rate_pct": round(visible_wr, 4) if visible_wr is not None else None,
            "visible_pnl_sol": round(visible_pnl, 6) if visible_pnl is not None else None,
            "early_tokens": repeat_early,
            "avg_early_rank": round(avg_rank, 3) if avg_rank is not None else None,
            "avg_early_latency_s": round(avg_latency, 3) if avg_latency is not None else None,
            "early_sol": round(row["early_sol"], 6),
            "historical_safety": "candidate discovered only from dated evidence on/before cutoff; no current leaderboard metrics used",
        })

    wallets.sort(key=lambda x: (
        {"PRIMARY": 0, "RADAR": 1, "WATCH": 2}.get(x["discovery_class"], 9),
        -x["score"],
        x["wallet"],
    ))

    payload = {
        "cutoff_date": args.cutoff_date,
        "history_window": [start.isoformat(), cutoff.isoformat()],
        "anti_lookahead": True,
        "current_leaderboard_used": False,
        "dated_wallet_count": len(dated_wallets),
        "dated_token_count": len(tokens),
        "reverse_scanned_token_count": len(selected_mints),
        "early_buyer_wallet_count": len({
            b["wallet"] for r in early_results for b in (r.get("buyers") or [])
        }),
        "unique_candidate_wallets": len(wallets),
        "class_counts": {
            c: sum(1 for w in wallets if w["discovery_class"] == c)
            for c in ("PRIMARY", "RADAR", "WATCH")
        },
        "pages": pages,
        "wallets": wallets,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"DATED={len(dated_wallets)} EARLY={payload['early_buyer_wallet_count']} "
        f"UNIQUE={len(wallets)} CLASSES={payload['class_counts']}"
    )
    print(f"WROTE={out}")


if __name__ == "__main__":
    main()
