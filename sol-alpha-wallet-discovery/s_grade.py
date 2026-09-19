from __future__ import annotations

from dataclasses import dataclass
from scoring import WalletScore


@dataclass(frozen=True)
class SGrade:
    wallet: str
    s_score: float
    grade: str
    reason: tuple[str, ...]


def grade_s_wallet(s: WalletScore) -> SGrade | None:
    """
    Elite copy-wallet gate.
    Deliberately much stricter than A/A-:
    - strong absolute 30D profit
    - high and stable win rate across 7/15/30D
    - enough copyability and confidence
    - diversified profit (not one lucky moonshot)
    - positive median token ROI
    """
    reasons: list[str] = []

    if s.pnl_30d_sol < 15:
        reasons.append("pnl30_lt_15sol")
    if min(s.win_rate_7d, s.win_rate_15d, s.win_rate_30d) < 0.72:
        reasons.append("winrate_window_lt_72pct")
    if s.win_rate_30d < 0.78:
        reasons.append("winrate30_lt_78pct")
    if s.median_roi_30d < 0.10:
        reasons.append("median_roi_lt_10pct")
    if s.top1_concentration > 0.35:
        reasons.append("top1_profit_gt_35pct")
    if s.top3_concentration > 0.65:
        reasons.append("top3_profit_gt_65pct")
    if s.copyability < 0.70:
        reasons.append("copyability_lt_70pct")
    if s.data_confidence < 0.45:
        reasons.append("confidence_lt_45pct")
    if s.wallet_class in ("Rejected", "Non-copyable Alpha"):
        reasons.append("not_copyable_class")
    if any(x in s.notes for x in (
        "sample_small",
        "top1_profit_concentrated",
        "rug_exposure_high",
        "hft_or_market_maker",
        "bundle_or_cluster_risk",
        "non_copyable_speed",
    )):
        reasons.append("risk_flag")

    if reasons:
        return None

    stability = min(s.win_rate_7d, s.win_rate_15d, s.win_rate_30d)
    pnl_q = min(s.pnl_30d_sol / 40.0, 1.0)
    roi_q = min(max(s.median_roi_30d, 0.0) / 0.60, 1.0)
    diversification = 1.0 - min(s.top1_concentration / 0.35, 1.0)
    s_score = 100 * (
        0.24 * stability
        + 0.20 * s.win_rate_30d
        + 0.18 * pnl_q
        + 0.12 * roi_q
        + 0.12 * s.copyability
        + 0.08 * s.data_confidence
        + 0.06 * diversification
    )

    grade = "S+" if s_score >= 88 and s.pnl_30d_sol >= 25 else "S"
    return SGrade(s.wallet, round(s_score, 4), grade, ())


def filter_s_wallets(scores: list[WalletScore]) -> list[SGrade]:
    out = [x for s in scores if (x := grade_s_wallet(s)) is not None]
    return sorted(out, key=lambda x: (-x.s_score, x.wallet))
