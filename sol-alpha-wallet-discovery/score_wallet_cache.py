from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from config import utc_window_for_local_day
from history_rescore import window_metrics
from scoring import WalletFeatures, score_wallet


def load_rows(path: Path):
    files = sorted(path.glob("*.json")) if path.is_dir() else [path]
    rows = {}
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        for r in d.get("rows") or []:
            rows[r["wallet"]] = r
    return rows


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--cache", required=True)
    p.add_argument("--cutoff-date", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    cutoff, _ = utc_window_for_local_day(date.fromisoformat(args.cutoff_date))
    cutoff_ts = int(cutoff.timestamp())
    rows = load_rows(Path(args.cache))

    output = []
    for wallet, row in rows.items():
        trades = [t for t in row.get("trades") or [] if int(t.get("block_time") or 0) < cutoff_ts]
        if not row.get("complete_enough"):
            output.append({
                "wallet": wallet,
                "status": "RADAR_HEAVY_INCOMPLETE",
                "reason": "history cache incomplete; preserve as radar source, never direct-copy from this status alone",
                "signature_count": row.get("signature_count"),
                "rpc_errors": row.get("rpc_errors"),
                "truncated": row.get("truncated"),
            })
            continue

        w7 = window_metrics(trades, cutoff_ts, 7)
        w15 = window_metrics(trades, cutoff_ts, 15)
        w30 = window_metrics(trades, cutoff_ts, 30)
        score = score_wallet(WalletFeatures(wallet=wallet, w7=w7, w15=w15, w30=w30))
        output.append({
            "wallet": wallet,
            "status": "KEEP" if score.wallet_class != "Rejected" else "REJECT",
            "chain_score": score.__dict__,
            "w7": w7.__dict__,
            "w15": w15.__dict__,
            "w30": w30.__dict__,
        })

    payload = {
        "cutoff_utc": cutoff.isoformat(),
        "wallets": len(output),
        "keep": sum(1 for x in output if x["status"] == "KEEP"),
        "reject": sum(1 for x in output if x["status"] == "REJECT"),
        "radar_heavy": sum(1 for x in output if x["status"] == "RADAR_HEAVY_INCOMPLETE"),
        "rows": output,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"KEEP={payload['keep']} REJECT={payload['reject']} "
        f"RADAR_HEAVY={payload['radar_heavy']} WROTE={out}"
    )


if __name__ == "__main__":
    main()
