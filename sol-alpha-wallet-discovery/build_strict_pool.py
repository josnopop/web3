from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def evidence_class(w: dict) -> tuple[str | None, float, list[str]]:
    sec = w.get("sections") or {}
    days = int(w.get("days_seen") or 0)
    top = int(sec.get("top_performer") or 0)
    active = int(sec.get("active") or 0)
    signal = int(sec.get("signal") or 0)
    convergence = int(sec.get("convergence") or 0)
    big_buy = int(sec.get("big_buy") or 0)
    wr = w.get("avg_top_win_rate")
    pnl = w.get("sum_visible_top_pnl_sol")

    reasons: list[str] = []
    klass = None

    if top >= 2 and wr is not None and wr >= 55 and pnl is not None and pnl > 0:
        klass = "Copyable Candidate"
        reasons.append("repeat_top_performer")
    elif top >= 1 and wr is not None and wr >= 65 and pnl is not None and pnl >= 150:
        klass = "Radar Candidate"
        reasons.append("strong_single_top_performer")
    elif signal >= 2 and days >= 2:
        klass = "Radar Candidate"
        reasons.append("repeat_signal_wallet")
    elif days >= 5 and active >= 4:
        klass = "Confirmation Candidate"
        reasons.append("persistent_active_wallet")

    score = (
        min(days, 15) * 2.0
        + top * 10.0
        + signal * 5.0
        + convergence * 4.0
        + active * 1.5
        + big_buy * 0.5
    )
    if wr is not None:
        score += max(0.0, (float(wr) - 50.0) / 2.5)
    if pnl is not None and float(pnl) > 0:
        score += min(20.0, float(pnl) / 100.0)

    return klass, round(score, 4), reasons


def class_rank(name: str) -> int:
    return {
        "Copyable Candidate": 3,
        "Radar Candidate": 2,
        "Confirmation Candidate": 1,
    }.get(name, 0)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--history", required=True)
    p.add_argument("--original", default="frozen_wallets_2026_09_15.json")
    p.add_argument("--out", default="out/2026-09-15/strict_frozen_pool.json")
    args = p.parse_args()

    history = json.loads(Path(args.history).read_text(encoding="utf-8"))
    original = json.loads(Path(args.original).read_text(encoding="utf-8"))

    merged: dict[str, dict] = {}
    rejected = 0

    for w in history.get("wallets") or []:
        klass, score, reasons = evidence_class(w)
        if not klass:
            rejected += 1
            continue
        addr = w["address"]
        merged[addr] = {
            "wallet": addr,
            "name": w.get("name") or addr[:6] + "…" + addr[-4:],
            "wallet_class": klass,
            "score": score,
            "days_seen": w.get("days_seen"),
            "sections": w.get("sections") or {},
            "win_rate": (
                float(w["avg_top_win_rate"]) / 100.0
                if w.get("avg_top_win_rate") is not None else None
            ),
            "net_pnl_sol": w.get("sum_visible_top_pnl_sol"),
            "source": "MadeOnSol dated Daily Alpha historical pages",
            "selection_reasons": reasons,
            "kol_bonus": 0,
        }

    # Preserve the original first verified pool as an independent source.
    original_default = {
        "Gh0stee": "Copyable Candidate",
        "Maze": "Copyable Candidate",
        "gr3g": "Copyable Candidate",
        "Mr. Frog": "Copyable Candidate",
        "Dani": "Copyable Candidate",
        "slingoor": "Copyable Candidate",
        "OGAntD": "Copyable Candidate",
        "cap": "Radar Candidate",
        "decu": "Radar Candidate",
        "Schoen": "Radar Candidate",
        "japbitch": "Radar Candidate",
        "trunoest": "Radar Candidate",
        "bandit": "Confirmation Candidate",
        "merky": "Confirmation Candidate",
        "36A6…kDKG": "Radar Candidate",
    }
    for w in original.get("wallets") or []:
        addr = w["address"]
        klass = original_default.get(w["name"], "Radar Candidate")
        if addr not in merged:
            merged[addr] = {
                "wallet": addr,
                "name": w["name"],
                "wallet_class": klass,
                "score": 50.0,
                "days_seen": None,
                "sections": {},
                "win_rate": None,
                "net_pnl_sol": None,
                "source": "original Sep-15 verified first batch",
                "selection_reasons": ["original_verified_pool"],
                "kol_bonus": 0,
            }
        else:
            row = merged[addr]
            row["name"] = w["name"]
            row["source"] += " + original verified pool"
            row["selection_reasons"].append("original_verified_pool")
            if class_rank(klass) > class_rank(row["wallet_class"]):
                row["wallet_class"] = klass

    wallets = sorted(
        merged.values(),
        key=lambda x: (-class_rank(x["wallet_class"]), -float(x["score"]), x["wallet"]),
    )
    payload = {
        "freeze_cutoff": "2026-09-16T00:00:00+08:00",
        "anti_lookahead": True,
        "kol_bonus": 0,
        "history_window": history.get("window"),
        "source_wallet_count": history.get("wallet_count"),
        "rejected_by_evidence_gate": rejected,
        "frozen_wallet_count": len(wallets),
        "wallets": wallets,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"SOURCE={history.get('wallet_count')} REJECTED={rejected} "
        f"FROZEN={len(wallets)} SHA256={payload['sha256']}"
    )
    for w in wallets[:50]:
        print(
            f"{w['wallet_class']:<24} {w['name'][:22]:<22} "
            f"score={w['score']} days={w['days_seen']} wr={w['win_rate']} pnl={w['net_pnl_sol']}"
        )
    print(f"WROTE={out}")


if __name__ == "__main__":
    main()
