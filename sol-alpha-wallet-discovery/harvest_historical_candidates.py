from __future__ import annotations

import argparse
import csv
import json
import re
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

BASE = "https://uwuu.ai/trader/"
ADDR_RE = re.compile(r"/trader/([1-9A-HJ-NP-Za-km-z]{32,44})")
SNAPSHOT = "refreshed Sep 15, 2026"


def fetch_profile(address: str, timeout: int = 15) -> tuple[str, str]:
    req = urllib.request.Request(
        BASE + address,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; SOL-Wallet-Research/0.3)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return address, r.read().decode("utf-8", errors="ignore")


def textify(html: str) -> str:
    txt = re.sub(r"<script\b[^>]*>.*?</script>", " ", html, flags=re.I | re.S)
    txt = re.sub(r"<style\b[^>]*>.*?</style>", " ", txt, flags=re.I | re.S)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = txt.replace("&nbsp;", " ").replace("&#x27;", "'").replace("&amp;", "&")
    return re.sub(r"\s+", " ", txt).strip()


def first_num(pattern: str, text: str, default=None):
    m = re.search(pattern, text, flags=re.I)
    if not m:
        return default
    raw = m.group(1).replace(",", "").replace("$", "").replace("%", "")
    try:
        return float(raw)
    except ValueError:
        return default


def parse_profile(address: str, html: str) -> tuple[dict, list[str]]:
    txt = textify(html)
    historical = SNAPSHOT.lower() in txt.lower()

    title = ""
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, flags=re.I | re.S)
    if m:
        title = textify(m.group(1)).replace("— Solana Wallet", "").strip()

    row = {
        "name": title or address[:6] + "…" + address[-4:],
        "address": address,
        "snapshot": "2026-09-15" if historical else None,
        "pnl_usd_30d": first_num(r"30d\s*PnL\s*\$?([+-]?[\d,]+(?:\.\d+)?)", txt),
        "roi_30d_pct": first_num(r"30d\s*ROI\s*([+-]?[\d,]+(?:\.\d+)?)\s*%", txt),
        "trades_30d": first_num(r"Trades\s*([\d,]+)", txt),
        "win_rate_30d_pct": first_num(r"Win rate\s*([\d.]+)\s*%", txt),
        "tokens_30d": first_num(r"Tokens traded\s*([\d,]+)", txt),
        "source": "uwuu historical profile snapshot",
        "kol_bonus": 0,
    }
    if row["trades_30d"] is not None:
        row["trades_30d"] = int(row["trades_30d"])
    if row["tokens_30d"] is not None:
        row["tokens_30d"] = int(row["tokens_30d"])

    links = list(dict.fromkeys(ADDR_RE.findall(html)))
    return row, links


def gate(row: dict) -> tuple[bool, list[str]]:
    reasons = []
    pnl = row.get("pnl_usd_30d")
    roi = row.get("roi_30d_pct")
    trades = row.get("trades_30d")
    wr = row.get("win_rate_30d_pct")
    tokens = row.get("tokens_30d")

    if row.get("snapshot") != "2026-09-15":
        reasons.append("snapshot_not_sep15")
    if pnl is None or pnl <= 0:
        reasons.append("pnl_nonpositive_or_missing")
    if trades is None or trades < 20:
        reasons.append("sample_lt_20_trades")
    if tokens is None or tokens < 5:
        reasons.append("tokens_lt_5")
    if wr is None or wr < 45:
        reasons.append("winrate_lt_45")
    if roi is None or roi < 8:
        reasons.append("roi_lt_8pct")
    return not reasons, reasons


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--seed-file", default="frozen_wallets_2026_09_15.json")
    p.add_argument("--max-profiles", type=int, default=500)
    p.add_argument("--workers", type=int, default=20)
    p.add_argument("--batch-size", type=int, default=40)
    p.add_argument("--out", default="out/2026-09-15/historical_candidates.json")
    args = p.parse_args()

    seed = json.loads(Path(args.seed_file).read_text(encoding="utf-8"))
    q = deque(w["address"] for w in seed["wallets"])
    q.extend([
        "EaVboaPxFCYanjoNWdkxTbPvt57nhXGu5i6m9m6ZS2kK",
        "4s2WzRLa35FB58bZY1i4CN3WoywJeuYrGYHnTKFsT23z",
        "9FEHWFjgbYnFCRRHkesJNq6znHjc5Aaq7TiKi1rCVSnH",
        "7Js5gmq57y9jG2sseKrAeJt3vbncSWSFFHDEsyJDnyVm",
        "AeLaMjzxErZt4drbWVWvcxpVyo8p94xu5vrg41eZPFe3",
    ])

    seen: set[str] = set()
    queued = set(q)
    rows: list[dict] = []
    errors: list[dict] = []

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        while q and len(seen) < args.max_profiles:
            batch = []
            while q and len(batch) < args.batch_size and len(seen) + len(batch) < args.max_profiles:
                addr = q.popleft()
                if addr in seen:
                    continue
                seen.add(addr)
                batch.append(addr)

            futs = {pool.submit(fetch_profile, addr): addr for addr in batch}
            for fut in as_completed(futs):
                addr = futs[fut]
                try:
                    _, html = fut.result()
                    row, links = parse_profile(addr, html)
                    ok, reasons = gate(row)
                    row["pre_filter_pass"] = ok
                    row["reject_reasons"] = reasons
                    rows.append(row)

                    for found in links:
                        if found not in seen and found not in queued and len(queued) < args.max_profiles * 4:
                            q.append(found)
                            queued.add(found)
                except Exception as e:
                    errors.append({"address": addr, "error": f"{type(e).__name__}: {e}"})

            print(
                f"BATCH_DONE fetched={len(batch)} total_profiles={len(rows)} "
                f"queue={len(q)} accepted_so_far={sum(1 for r in rows if r['pre_filter_pass'])}"
            )

    accepted = [r for r in rows if r["pre_filter_pass"]]
    accepted.sort(
        key=lambda r: (
            -(r.get("win_rate_30d_pct") or 0),
            -(r.get("roi_30d_pct") or 0),
            -(r.get("pnl_usd_30d") or 0),
        )
    )

    result = {
        "cutoff_snapshot": "2026-09-15",
        "anti_lookahead": True,
        "discovered_profiles": len(rows),
        "accepted_pre_filter": len(accepted),
        "errors": errors,
        "accepted": accepted,
        "all_profiles": rows,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    csv_path = out.with_suffix(".csv")
    cols = [
        "name","address","snapshot","pnl_usd_30d","roi_30d_pct",
        "trades_30d","win_rate_30d_pct","tokens_30d","pre_filter_pass",
        "source","kol_bonus"
    ]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(accepted)

    print(f"DISCOVERED={len(rows)} ACCEPTED={len(accepted)} ERRORS={len(errors)}")
    print(f"WROTE={out}")
    print(f"WROTE={csv_path}")


if __name__ == "__main__":
    main()
