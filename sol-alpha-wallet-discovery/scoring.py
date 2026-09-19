from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Iterable


@dataclass(frozen=True)
class WindowMetrics:
    days: int
    dex_txs: int
    active_days: int
    distinct_tokens: int
    realized_pnl_sol: float
    win_tokens: int
    closed_tokens: int
    median_token_roi: float
    top1_profit_concentration: float
    top3_profit_concentration: float
    median_hold_minutes: float
    rug_like_tokens: int
    same_slot_ratio: float
    max_trades_per_minute: int


@dataclass(frozen=True)
class WalletFeatures:
    wallet: str
    w7: WindowMetrics
    w15: WindowMetrics
    w30: WindowMetrics
    source_count: int = 1
    early_entry: float | None = None
    alpha_decay: float | None = None
    entry_skill: float | None = None
    hold_skill: float | None = None
    exit_quality: float | None = None
    external_data_confidence: float | None = None


@dataclass(frozen=True)
class WalletScore:
    wallet: str
    score: float
    tier: str
    wallet_class: str
    pnl_30d_sol: float
    win_rate_7d: float
    win_rate_15d: float
    win_rate_30d: float
    median_roi_30d: float
    top1_concentration: float
    top3_concentration: float
    copyability: float
    data_confidence: float
    notes: tuple[str, ...]


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _wr(w: WindowMetrics) -> float:
    return w.win_tokens / max(w.closed_tokens, 1)


def score_wallet(f: WalletFeatures) -> WalletScore:
    w7, w15, w30 = f.w7, f.w15, f.w30
    wr7, wr15, wr30 = _wr(w7), _wr(w15), _wr(w30)

    pnl_quality = _clamp((w30.realized_pnl_sol + 5.0) / 35.0)
    win_quality = _clamp((0.45 * wr7 + 0.25 * wr15 + 0.30 * wr30 - 0.35) / 0.40)
    median_roi_quality = _clamp((w30.median_token_roi + 0.10) / 0.90)
    sample_quality = _clamp(w30.closed_tokens / 30.0) * _clamp(w30.active_days / 12.0)

    concentration_penalty = 0.55 * _clamp((w30.top1_profit_concentration - 0.35) / 0.55)
    concentration_penalty += 0.45 * _clamp((w30.top3_profit_concentration - 0.70) / 0.30)

    hft_penalty = _clamp((w30.max_trades_per_minute - 8) / 42.0)
    cluster_penalty = _clamp((w30.same_slot_ratio - 0.10) / 0.50)
    hold_copy = 1.0 - _clamp((5.0 - w30.median_hold_minutes) / 5.0) if w30.median_hold_minutes < 5 else 1.0
    copyability = _clamp(hold_copy * (1 - 0.60 * hft_penalty) * (1 - 0.55 * cluster_penalty))

    rug_exposure = w30.rug_like_tokens / max(w30.distinct_tokens, 1)
    risk_penalty = _clamp(rug_exposure / 0.25)

    source_conf = _clamp(f.source_count / 3.0)
    data_confidence = _clamp(
        0.70 * sample_quality
        + 0.20 * source_conf
        + 0.10 * (f.external_data_confidence or 0.0)
    )

    optional_alpha = [
        x for x in (f.early_entry, f.entry_skill, f.hold_skill, f.exit_quality)
        if x is not None
    ]
    alpha_quality = sum(optional_alpha) / len(optional_alpha) if optional_alpha else 0.50
    decay_quality = 1.0 - _clamp(f.alpha_decay or 0.0)

    raw = (
        23 * pnl_quality
        + 17 * win_quality
        + 10 * median_roi_quality
        + 12 * sample_quality
        + 13 * copyability
        + 8 * alpha_quality
        + 5 * decay_quality
        + 12 * data_confidence
        - 16 * concentration_penalty
        - 18 * risk_penalty
    )
    score = _clamp(raw / 100.0) * 100.0

    notes: list[str] = []
    if w30.closed_tokens < 8:
        notes.append("sample_small")
        score = min(score, 49.9)
    if w30.top1_profit_concentration >= 0.80:
        notes.append("top1_profit_concentrated")
        score = min(score, 59.9)
    if rug_exposure >= 0.20:
        notes.append("rug_exposure_high")
        score = min(score, 39.9)
    if w30.max_trades_per_minute >= 50:
        notes.append("hft_or_market_maker")
        score = min(score, 34.9)
    if w30.same_slot_ratio >= 0.60:
        notes.append("bundle_or_cluster_risk")
        score = min(score, 39.9)
    if copyability < 0.35:
        notes.append("non_copyable_speed")

    if score >= 78 and copyability >= 0.55 and data_confidence >= 0.45:
        tier, wallet_class = "A", "Copyable Wallet"
    elif score >= 62 and copyability < 0.35:
        tier, wallet_class = "ALPHA", "Non-copyable Alpha"
    elif score >= 62 and data_confidence >= 0.35:
        tier, wallet_class = "A-", "Radar Wallet"
    elif score >= 50:
        tier, wallet_class = "B+", "Confirmation Wallet"
    else:
        tier, wallet_class = "REJECT", "Rejected"

    return WalletScore(
        wallet=f.wallet,
        score=round(score, 4),
        tier=tier,
        wallet_class=wallet_class,
        pnl_30d_sol=round(w30.realized_pnl_sol, 9),
        win_rate_7d=round(wr7, 6),
        win_rate_15d=round(wr15, 6),
        win_rate_30d=round(wr30, 6),
        median_roi_30d=round(w30.median_token_roi, 6),
        top1_concentration=round(w30.top1_profit_concentration, 6),
        top3_concentration=round(w30.top3_profit_concentration, 6),
        copyability=round(copyability, 6),
        data_confidence=round(data_confidence, 6),
        notes=tuple(notes),
    )


def freeze_snapshot(scores: Iterable[WalletScore], metadata: dict) -> tuple[str, str]:
    kept = [asdict(s) for s in scores if s.wallet_class != "Rejected"]
    kept.sort(key=lambda x: (-x["score"], x["wallet"]))
    payload = {"metadata": metadata, "wallets": kept}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return canonical, digest
