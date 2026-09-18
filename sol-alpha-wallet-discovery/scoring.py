from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Iterable


@dataclass(frozen=True)
class WalletFeatures:
    wallet: str
    dex_txs: int
    active_days: int
    distinct_tokens: int
    buys: int
    sells: int
    gross_quote_out_sol: float
    gross_quote_in_sol: float
    realized_quote_pnl_sol: float
    win_tokens: int
    closed_tokens: int
    median_hold_minutes: float
    rug_like_tokens: int
    same_slot_ratio: float
    max_trades_per_minute: int


@dataclass(frozen=True)
class WalletScore:
    wallet: str
    score: float
    tier: str
    copyable_pnl_sol: float
    win_rate: float
    rug_exposure: float
    sample_quality: float
    hft_penalty: float
    notes: tuple[str, ...]


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def score_wallet(f: WalletFeatures) -> WalletScore:
    closed = max(f.closed_tokens, 1)
    win_rate = f.win_tokens / closed
    rug_exposure = f.rug_like_tokens / max(f.distinct_tokens, 1)

    # Sample quality rewards sustained activity but saturates; raw HFT volume is not alpha.
    sample_quality = _clamp(f.closed_tokens / 30.0) * _clamp(f.active_days / 10.0)
    pnl_quality = _clamp((f.realized_quote_pnl_sol + 5.0) / 25.0)
    win_quality = _clamp((win_rate - 0.35) / 0.35)
    hold_quality = 1.0 - _clamp(abs(f.median_hold_minutes - 45.0) / 240.0)
    diversity = _clamp(f.distinct_tokens / 40.0)

    # Penalize machine-gun flow, same-slot/bundle-like behavior and rug exposure.
    hft_penalty = _clamp((f.max_trades_per_minute - 8) / 35.0)
    cluster_penalty = _clamp((f.same_slot_ratio - 0.10) / 0.50)
    rug_penalty = _clamp(rug_exposure / 0.25)

    raw = (
        30.0 * pnl_quality
        + 20.0 * win_quality
        + 15.0 * sample_quality
        + 10.0 * hold_quality
        + 10.0 * diversity
        + 15.0 * _clamp((f.realized_quote_pnl_sol + 2.0) / 12.0)
        - 20.0 * hft_penalty
        - 15.0 * cluster_penalty
        - 25.0 * rug_penalty
    )
    score = max(0.0, min(100.0, raw))

    notes: list[str] = []
    if f.closed_tokens < 8:
        notes.append("sample_small")
        score = min(score, 59.9)
    if rug_exposure >= 0.20:
        notes.append("rug_exposure_high")
        score = min(score, 39.9)
    if f.max_trades_per_minute >= 50:
        notes.append("hft_or_market_maker")
        score = min(score, 29.9)
    if f.same_slot_ratio >= 0.60:
        notes.append("bundle_or_cluster_risk")
        score = min(score, 39.9)

    if score >= 80:
        tier = "S"
    elif score >= 65:
        tier = "A"
    elif score >= 50:
        tier = "B"
    else:
        tier = "REJECT"

    return WalletScore(
        wallet=f.wallet,
        score=round(score, 4),
        tier=tier,
        copyable_pnl_sol=round(f.realized_quote_pnl_sol, 9),
        win_rate=round(win_rate, 6),
        rug_exposure=round(rug_exposure, 6),
        sample_quality=round(sample_quality, 6),
        hft_penalty=round(hft_penalty, 6),
        notes=tuple(notes),
    )


def freeze_snapshot(scores: Iterable[WalletScore], metadata: dict) -> tuple[str, str]:
    kept = [asdict(s) for s in scores if s.tier in {"S", "A", "B"}]
    kept.sort(key=lambda x: (-x["score"], x["wallet"]))
    payload = {"metadata": metadata, "wallets": kept}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return canonical, digest
