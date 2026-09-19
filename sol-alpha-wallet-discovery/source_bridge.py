from __future__ import annotations

"""Open-source/tool bridge for candidate enrichment.

Important anti-lookahead rule:
- Historical replay pools may use only evidence timestamped <= the freeze cutoff.
- Live GMGN / DegenRadar output is never back-filled into an old pool.
- KOL / Smart Money labels carry zero score bonus.
"""

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

SOL_ADDR = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


@dataclass(frozen=True)
class SourceCandidate:
    wallet: str
    source: str
    label: str = ""
    bonus: float = 0.0


def _walk(obj, source: str, label: str = "") -> list[SourceCandidate]:
    out: list[SourceCandidate] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            k = str(key).lower()
            if (
                isinstance(value, str)
                and ("wallet" in k or "address" in k)
                and SOL_ADDR.fullmatch(value)
            ):
                out.append(SourceCandidate(value, source, label, 0.0))
            out.extend(_walk(value, source, label))
    elif isinstance(obj, list):
        for item in obj:
            out.extend(_walk(item, source, label))
    return out


def _dedupe(items: list[SourceCandidate]) -> list[SourceCandidate]:
    seen: set[tuple[str, str]] = set()
    out: list[SourceCandidate] = []
    for item in items:
        key = (item.wallet, item.source)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def gmgn_smartmoney(limit: int = 200) -> list[SourceCandidate]:
    """Current/live auxiliary discovery only unless upstream response is historical."""
    exe = shutil.which("gmgn-cli")
    if not exe:
        raise RuntimeError("gmgn-cli not installed")
    cp = subprocess.run(
        [
            exe,
            "track",
            "smartmoney",
            "--chain",
            "sol",
            "--limit",
            str(limit),
            "--raw",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return _dedupe(_walk(json.loads(cp.stdout), "GMGN", "smartmoney"))


def gmgn_wallet_stats(wallet: str, period: str = "30d") -> dict:
    exe = shutil.which("gmgn-cli")
    if not exe:
        raise RuntimeError("gmgn-cli not installed")
    cp = subprocess.run(
        [
            exe,
            "portfolio",
            "stats",
            "--chain",
            "sol",
            "--wallet",
            wallet,
            "--period",
            period,
            "--raw",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(cp.stdout)


def addresses_from_json_file(path: str | Path, source: str) -> list[SourceCandidate]:
    """Import DegenRadar/other tool JSON without trusting its score as our score."""
    with Path(path).open("r", encoding="utf-8") as f:
        data = json.load(f)
    return _dedupe(_walk(data, source))


def merge_candidates(*groups: list[SourceCandidate]) -> dict[str, dict]:
    """Merge evidence; labels do not add wallet score."""
    merged: dict[str, dict] = {}
    for group in groups:
        for item in group:
            row = merged.setdefault(
                item.wallet,
                {"wallet": item.wallet, "sources": set(), "labels": set(), "bonus": 0.0},
            )
            row["sources"].add(item.source)
            if item.label:
                row["labels"].add(item.label)
    for row in merged.values():
        row["sources"] = sorted(row["sources"])
        row["labels"] = sorted(row["labels"])
        row["source_count"] = len(row["sources"])
        row["bonus"] = 0.0
    return merged
