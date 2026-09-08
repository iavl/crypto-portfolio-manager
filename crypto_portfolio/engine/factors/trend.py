"""Deterministic trend factor scoring from the technical engine."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from ...facts.models import TrendFacts
from ...models.execution import PriceZone
from ...models.market import OHLCVSeries, SpotPrice, SwingPoint, TechnicalSnapshot
from ...models.volume_profile import VolumeNode
from ...models.policy import Policy, resolve_policy
from ..technical import build_technical_snapshot


_CONFIDENCE_ORDER = ("LOW", "MEDIUM", "HIGH")



@dataclass(frozen=True)
class TrendFactorResult:
    score: float
    facts: TrendFacts
    reasons: tuple[str, ...]
    confidence: str
    coverage: float
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        score = float(self.score)
        coverage = float(self.coverage)
        if not math.isfinite(score) or not 0 <= score <= 100:
            raise ValueError("trend score must be finite and in [0, 100]")
        if not math.isfinite(coverage) or not 0 <= coverage <= 1:
            raise ValueError("trend coverage must be finite and in [0, 1]")
        confidence = str(self.confidence).strip().upper()
        if confidence not in _CONFIDENCE_ORDER:
            raise ValueError("trend confidence must be HIGH, MEDIUM, or LOW")
        if not isinstance(self.facts, TrendFacts):
            raise ValueError("trend facts must be TrendFacts")
        object.__setattr__(self, "score", score)
        object.__setattr__(self, "coverage", coverage)
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "reasons", tuple(str(item) for item in self.reasons))
        ids = tuple(str(item).strip() for item in self.evidence_ids)
        if any(not item for item in ids) or len(ids) != len(set(ids)):
            raise ValueError("trend evidence_ids must contain unique non-empty strings")
        object.__setattr__(self, "evidence_ids", ids)

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "facts": self.facts.as_dict(),
            "reasons": list(self.reasons),
            "confidence": self.confidence,
            "coverage": self.coverage,
            "evidence_ids": list(self.evidence_ids),
        }


def _snapshot(
    value: TechnicalSnapshot | OHLCVSeries | Mapping[str, Any],
    *,
    spot: SpotPrice | Mapping[str, Any] | None,
    policy: Policy,
    as_of: str | None,
) -> TechnicalSnapshot:
    if isinstance(value, TechnicalSnapshot):
        return value
    if isinstance(value, Mapping):
        if "candles" in value:
            value = OHLCVSeries.from_mapping(value)
        else:
            data = dict(value)
            for field, constructor in (
                ("swing_highs", SwingPoint),
                ("swing_lows", SwingPoint),
                ("support_zones", PriceZone),
                ("resistance_zones", PriceZone),
                ("volume_hvns", VolumeNode),
                ("volume_lvns", VolumeNode),
            ):
                if field in data:
                    data[field] = tuple(
                        item if isinstance(item, constructor) else constructor.from_mapping(item)
                        for item in data[field]
                    )
            return TechnicalSnapshot(**data)
    if isinstance(value, OHLCVSeries):
        return build_technical_snapshot(value, spot, as_of=as_of, policy=policy)
    raise ValueError("trend input must be a TechnicalSnapshot or OHLCVSeries")


def _rules(policy: Policy) -> Mapping[str, float]:
    return policy.factor_rules["trend"]


def _momentum_score(value: float, neutral: float, saturation: float) -> float:
    if abs(value) <= neutral:
        return 50.0
    span = saturation - neutral
    if value > 0:
        return min(100.0, 50.0 + 50.0 * (min(value, saturation) - neutral) / span)
    return max(0.0, 50.0 + 50.0 * (max(value, -saturation) + neutral) / span)


def _confidence(coverage: float, data_confidence: str) -> str:
    index = 2 if coverage >= 0.9 else 1 if coverage >= 0.7 else 0
    data = str(data_confidence).upper()
    if data in _CONFIDENCE_ORDER:
        index = min(index, _CONFIDENCE_ORDER.index(data))
    return _CONFIDENCE_ORDER[index]


def calculate_trend_factor(
    value: TechnicalSnapshot | OHLCVSeries | Mapping[str, Any],
    *,
    spot: SpotPrice | Mapping[str, Any] | None = None,
    as_of: str | None = None,
    policy: Policy | None = None,
    evidence_ids: tuple[str, ...] | list[str] = (),
) -> TrendFactorResult:
    """Return the same score for the same validated technical snapshot."""
    resolved = policy or resolve_policy()
    snapshot = _snapshot(value, spot=spot, policy=resolved, as_of=as_of)
    rules = _rules(resolved)
    score = rules["base_score"]
    available_authority = 0.0
    total_authority = 0.0
    reasons: list[str] = []

    for window in ("20", "50", "100", "200"):
        name = f"ma{window}"
        authority = rules["ma_points"][window]
        total_authority += authority
        moving_average = getattr(snapshot, name)
        if moving_average is None:
            continue
        available_authority += authority
        if snapshot.current_spot_price >= moving_average:
            score += authority
            reasons.append(f"price is above {name.upper()}")
        else:
            score -= authority
            reasons.append(f"price is below {name.upper()}")

    alignment_authority = rules["alignment_points"]
    total_authority += alignment_authority
    alignment = tuple(
        getattr(snapshot, f"ma{window}")
        for window in rules["alignment_windows"]
    )
    if all(item is not None for item in alignment):
        available_authority += alignment_authority
        bullish = snapshot.current_spot_price > alignment[0] > alignment[1] > alignment[2]
        bearish = snapshot.current_spot_price < alignment[0] < alignment[1] < alignment[2]
        if bullish:
            score += alignment_authority
            reasons.append("moving averages are bullishly aligned")
        elif bearish:
            score -= alignment_authority
            reasons.append("moving averages are bearishly aligned")

    momentum = rules["momentum"]
    momentum_weights = momentum["horizon_weights"]
    momentum_scores: dict[str, float] = {}
    available_momentum_weight = 0.0
    for horizon in ("30d", "90d", "180d"):
        authority = momentum["max_points"] * momentum_weights[horizon]
        total_authority += authority
        name = f"return_{horizon}"
        period_return = getattr(snapshot, name)
        if period_return is None:
            continue
        available_authority += authority
        available_momentum_weight += momentum_weights[horizon]
        momentum_scores[horizon] = _momentum_score(
            period_return,
            momentum["neutral_abs"][horizon],
            momentum["saturation_abs"][horizon],
        )
        reasons.append(f"{name} momentum score is {momentum_scores[horizon]:.1f}")
    if momentum_scores:
        aggregate = sum(
            momentum_scores[horizon] * momentum_weights[horizon]
            for horizon in momentum_scores
        ) / available_momentum_weight
        score += (aggregate - 50.0) / 50.0 * momentum["max_points"]

    # An empty support set is an evaluated, neutral structural result. It is
    # not missing data; only an unavailable technical snapshot removes this
    # authority from coverage.
    support_authority = rules["support_points"]
    total_authority += support_authority
    support_available = bool(
        getattr(snapshot, "market_data_fresh", True)
        and getattr(snapshot, "history_sufficient", True)
        and "INSUFFICIENT_HISTORY" not in getattr(snapshot, "data_quality_flags", ())
    )
    if support_available:
        available_authority += support_authority
        if snapshot.support_zones:
            score += support_authority
            reasons.append("confirmed support structure is available")
        else:
            reasons.append("support structure was evaluated but no confirmed zone is present")

    volume_authority = rules["volume_points"]
    total_authority += volume_authority
    if snapshot.volume_state != "UNKNOWN":
        available_authority += volume_authority
        if snapshot.volume_state == "SUPPORTIVE":
            score += volume_authority
            reasons.append("volume confirms the move")
        elif snapshot.volume_state == "WEAK":
            score -= volume_authority
            reasons.append("volume confirmation is weak")

    # Drawdown belongs to valuation in v2; retain it only as trend context.

    if snapshot.atr14 and snapshot.support_zones:
        nearest = max(snapshot.support_zones, key=lambda zone: zone.midpoint)
        extension = (snapshot.current_spot_price - nearest.midpoint) / snapshot.atr14
        if extension > rules["extension_threshold_atr"]:
            score -= rules["extension_penalty"]
            reasons.append("spot is extended above the nearest support")

    coverage = available_authority / total_authority if total_authority else 0.0
    score = min(100.0, max(0.0, score))
    source_values = list(evidence_ids)
    if snapshot.ohlcv_hash:
        source_values.append(snapshot.ohlcv_hash)
    if snapshot.volume_profile_hash:
        source_values.append(snapshot.volume_profile_hash)
    source_ids = tuple(dict.fromkeys(source_values))
    facts = TrendFacts(
        symbol=snapshot.symbol,
        current={
            "spot_price": snapshot.current_spot_price,
            "ma20": snapshot.ma20,
            "ma50": snapshot.ma50,
            "ma100": snapshot.ma100,
            "ma200": snapshot.ma200,
            "return_30d": snapshot.return_30d,
            "return_90d": snapshot.return_90d,
            "return_180d": snapshot.return_180d,
            "atr14": snapshot.atr14,
            "atr_percent": snapshot.atr_percent,
            "realized_vol_30d": snapshot.realized_vol_30d,
            "realized_vol_90d": snapshot.realized_vol_90d,
            "relative_volume": snapshot.relative_volume,
            "drawdown": snapshot.current_drawdown,
            "trend_state": snapshot.trend_state,
            "volume_state": snapshot.volume_state,
            "volume_profile_hash": snapshot.volume_profile_hash,
            "volume_profile_poc": snapshot.volume_profile_poc,
            "volume_profile_val": snapshot.volume_profile_val,
            "volume_profile_vah": snapshot.volume_profile_vah,
        },
        previous={},
        changes={},
        trends={"trend_state": snapshot.trend_state},
        coverage=coverage,
        freshness="CURRENT" if snapshot.market_data_fresh else "STALE",
        source_ids=source_ids,
        data_quality_flags=snapshot.data_quality_flags,
    )
    return TrendFactorResult(
        score=score,
        facts=facts,
        reasons=tuple(reasons) or ("insufficient technical signals",),
        confidence=_confidence(coverage, snapshot.data_confidence),
        coverage=coverage,
        evidence_ids=source_ids,
    )


__all__ = [
    "TrendFactorResult",
    "calculate_trend_factor",
]
