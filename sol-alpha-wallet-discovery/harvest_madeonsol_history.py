from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import urllib.request
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

BASE = "https://madeonsol.com/daily-alpha/{day}"
KOL_RE = re.compile(
    r'<a[^>]+href=["\'](?:https://madeonsol\.com)?/kol-tracker/([1-9A-HJ-NP-Za-km-z]{32,44})["\'][^>]*>(.*?)</a>',
    re.I | re.S,
)
DEX_RE = re.compile(
    r'href=["\']https?://(?:www\.)?dexscreener\.com/solana/([1-9A-HJ-NP-Za-km-z]{32,44})(?:\?[^"\']*)?["\']',
    re.I,
)


def fetch(day: str) -> str:
    req = urllib.request.Request(
        BASE.format(day=day),
        headers={"User-Agent": "Mozilla/5.0 (SOL-WalkForward/0.3)"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="ignore")


def clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s)
    s = html_lib.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def section_at(raw: str, pos: int) -> str:
    markers = [
        ("signal", "Signal of the Day"),
        ("big_buy", "Biggest KOL Buys"),
        ("convergence", "Convergence Signals"),
        ("top_performer", "Top Performers Today"),
        ("active", "Most Active KOLs"),
    ]
    best = ("other", -1)
    low = raw.lower()
    for name, marker in markers:
        p = low.rfind(marker.lower(), 0, pos)
        if p > best[1]:
            best = (name, p)
    return best[0]


def context_metrics(raw: str, start: int, end: int) -> dict:
    ctx = clean(raw[max(0, start - 220): min(len(raw), end + 320)])
    wr = re.search(r"(\d{1,3}(?:\.\d+)?)\s*%\s*win", ctx, re.I)
    pnl = re.search(r"([+-]\s*[\d,.]+)\s*SOL", ctx, re.I)
    buys = re.search(r"(\d[\d,]*)\s*buys", ctx, re.I)
    sells = re.search(r"(\d[\d,]*)\s*sells", ctx, re.I)
    return {
        "win_rate": float(wr.group(1)) if wr else None,
        "pnl_sol": float(pnl.group(1).replace(" ", "").replace(",", "")) if pnl else None,
        "buys": int(buys.group(1).replace(",", "")) if buys else None,
        "sells": int(sells.group(1).replace(",", "")) if sells else None,
        "context": ctx[:500],
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2026-09-01")
    p.add_argument("--end", default="2026-09-15")
    p.add_argument("--out", default="out/2026-09-15/madeonsol_history.json")
    args = p.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    wallets = defaultdict(lambda: {
        "dates": set(), "sections": defaultdict(int), "names": defaultdict(int),
        "daily_evidence": []
    })
    tokens = defaultdict(lambda: {"dates": set(), "sections": defaultdict(int)})
    pages = []

    day = start
    while day <= end:
        ds = day.isoformat()
        raw = fetch(ds)
        page_wallets = set()
        page_tokens = set()

        for m in KOL_RE.finditer(raw):
            addr = m.group(1)
            name = clean(m.group(2))
            sec = section_at(raw, m.start())
            met = context_metrics(raw, m.start(), m.end())
            w = wallets[addr]
            w["dates"].add(ds)
            w["sections"][sec] += 1
            if name:
                w["names"][name] += 1
            w["daily_evidence"].append({"date": ds, "section": sec, **met})
            page_wallets.add(addr)

        for m in DEX_RE.finditer(raw):
            mint = m.group(1)
            sec = section_at(raw, m.start())
            tokens[mint]["dates"].add(ds)
            tokens[mint]["sections"][sec] += 1
            page_tokens.add(mint)

        pages.append({"date": ds, "wallets": len(page_wallets), "token_mints": len(page_tokens)})
        print(f"{ds}: wallets={len(page_wallets)} token_mints={len(page_tokens)}")
        day += timedelta(days=1)

    out_wallets = []
    for addr, w in wallets.items():
        names = sorted(w["names"].items(), key=lambda x: (-x[1], x[0]))
        top_evidence = [e for e in w["daily_evidence"] if e["section"] == "top_performer"]
        valid_wr = [e["win_rate"] for e in top_evidence if e["win_rate"] is not None]
        valid_pnl = [e["pnl_sol"] for e in top_evidence if e["pnl_sol"] is not None]

        score = (
            2 * len(w["dates"])
            + 8 * w["sections"].get("top_performer", 0)
            + 4 * w["sections"].get("signal", 0)
            + 3 * w["sections"].get("active", 0)
            + 1 * w["sections"].get("big_buy", 0)
        )
        # This is historical evidence strength, NOT a KOL bonus.
        if valid_wr:
            score += max(0, (sum(valid_wr) / len(valid_wr) - 50) / 5)
        if valid_pnl and sum(valid_pnl) > 0:
            score += min(10, sum(valid_pnl) / 100)

        out_wallets.append({
            "name": names[0][0] if names else addr[:6] + "…" + addr[-4:],
            "address": addr,
            "days_seen": len(w["dates"]),
            "dates": sorted(w["dates"]),
            "sections": dict(w["sections"]),
            "avg_top_win_rate": round(sum(valid_wr)/len(valid_wr), 4) if valid_wr else None,
            "sum_visible_top_pnl_sol": round(sum(valid_pnl), 4) if valid_pnl else None,
            "historical_evidence_score": round(score, 4),
            "kol_bonus": 0,
            "daily_evidence": w["daily_evidence"],
        })

    out_wallets.sort(key=lambda x: (-x["historical_evidence_score"], -x["days_seen"], x["address"]))

    out_tokens = []
    for mint, t in tokens.items():
        score = (
            10 * t["sections"].get("signal", 0)
            + 4 * t["sections"].get("big_buy", 0)
            + 3 * t["sections"].get("convergence", 0)
            + len(t["dates"])
        )
        out_tokens.append({
            "mint": mint,
            "dates": sorted(t["dates"]),
            "sections": dict(t["sections"]),
            "reverse_discovery_priority": score,
        })
    out_tokens.sort(key=lambda x: (-x["reverse_discovery_priority"], x["mint"]))

    result = {
        "window": [args.start, args.end],
        "anti_lookahead": True,
        "source": "MadeOnSol dated Daily Alpha pages",
        "wallet_count": len(out_wallets),
        "token_mint_count": len(out_tokens),
        "pages": pages,
        "wallets": out_wallets,
        "token_mints": out_tokens,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"RESULT wallets={len(out_wallets)} token_mints={len(out_tokens)}")
    print(f"WROTE={out}")


if __name__ == "__main__":
    main()
