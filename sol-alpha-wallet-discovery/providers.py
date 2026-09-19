from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum


class Coverage(str, Enum):
    FULL_EXACT_HISTORY = "FULL_EXACT_HISTORY"
    PARTIAL_EXACT_HISTORY = "PARTIAL_EXACT_HISTORY"
    LIVE_ONLY = "LIVE_ONLY"


@dataclass(frozen=True)
class DataSourceStatus:
    name: str
    available: bool
    coverage: Coverage
    reason: str


def detect_sources() -> list[DataSourceStatus]:
    return [
        DataSourceStatus(
            "BigQuery Solana public dataset",
            bool(os.getenv("GOOGLE_CLOUD_PROJECT")),
            Coverage.FULL_EXACT_HISTORY,
            "GOOGLE_CLOUD_PROJECT configured" if os.getenv("GOOGLE_CLOUD_PROJECT")
            else "needs Google Cloud project/auth for billed query execution",
        ),
        DataSourceStatus(
            "Helius",
            bool(os.getenv("HELIUS_API_KEY")),
            Coverage.FULL_EXACT_HISTORY,
            "HELIUS_API_KEY configured" if os.getenv("HELIUS_API_KEY")
            else "API key not configured",
        ),
        DataSourceStatus(
            "Bitquery",
            bool(os.getenv("BITQUERY_API_KEY")),
            Coverage.FULL_EXACT_HISTORY,
            "BITQUERY_API_KEY configured" if os.getenv("BITQUERY_API_KEY")
            else "API key not configured",
        ),
        DataSourceStatus(
            "Solana public RPC",
            True,
            Coverage.PARTIAL_EXACT_HISTORY,
            "no key required; exact timestamps but rate-limited/bounded historical coverage",
        ),
        DataSourceStatus(
            "GMGN / DegenRadar live discovery",
            True,
            Coverage.LIVE_ONLY,
            "auxiliary discovery only; never back-filled into historical freezes",
        ),
    ]


def best_historical_source() -> DataSourceStatus:
    sources = detect_sources()
    for name in (
        "BigQuery Solana public dataset",
        "Helius",
        "Bitquery",
        "Solana public RPC",
    ):
        src = next(x for x in sources if x.name == name)
        if src.available:
            return src
    raise RuntimeError("No historical source available")


def assert_formal_freeze_allowed(source: DataSourceStatus) -> None:
    if source.coverage != Coverage.FULL_EXACT_HISTORY:
        raise RuntimeError(
            f"{source.name} coverage={source.coverage}; formal frozen-pool benchmark "
            "requires FULL_EXACT_HISTORY. Use this source only for recovery/verification."
        )
