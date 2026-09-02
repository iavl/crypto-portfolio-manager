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
_DEFAULT_RULES = {
    "base_score": 50.0,
    "price_ma_points": 6.0,
    "alignment_points": 12.0,
    "return_points": 6.0,
    "drawdown_points": 6.0,
    "support_points": 5.0,
    "volume_points": 5.0,
    "drawdown_tolerance": 0.25,
    "extension_threshold_atr": 2.0,
    "extension_penalty": 10.0,
}


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
    spot: SpotPrice | Mapping[str, Any] | float | None,
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
        supplied_spot = spot if spot is not None else value.candles[-1].close
        return build_technical_snapshot(value, supplied_spot, as_of=as_of, policy=policy)
    raise ValueError("trend input must be a TechnicalSnapshot or OHLCVSeries")


def _rules(policy: Policy) -> Mapping[str, float]:
    return {**_DEFAULT_RULES, **policy.factor_rules.get("trend", {})}


def _confidence(coverage: float, data_confidence: str) -> str:
    index = 2 if coverage >= 0.9 else 1 if coverage >= 0.7 else 0
    data = str(data_confidence).upper()
    if data in _CONFIDENCE_ORDER:
        index = min(index, _CONFIDENCE_ORDER.index(data))
    return _CONFIDENCE_ORDER[index]


def calculate_trend_factor(
    value: TechnicalSnapshot | OHLCVSeries | Mapping[str, Any],
    *,
    spot: SpotPrice | Mapping[str, Any] | float | None = None,
    as_of: str | None = None,
    policy: Policy | None = None,
    evidence_ids: tuple[str, ...] | list[str] = (),
) -> TrendFactorResult:
    """Return the same score for the same validated technical snapshot."""
    resolved = policy or resolve_policy()
    snapshot = _snapshot(value, spot=spot, policy=resolved, as_of=as_of)
    rules = _rules(resolved)
    score = rules["base_score"]
    available = 0
    total = 0
    reasons: list[str] = []

    for name in ("ma20", "ma50", "ma100", "ma200"):
        total += 1
        moving_average = getattr(snapshot, name)
        if moving_average is None:
            continue
        available += 1
        if snapshot.current_spot_price >= moving_average:
            score += rules["price_ma_points"]
            reasons.append(f"price is above {name.upper()}")
        else:
            score -= rules["price_ma_points"]
            reasons.append(f"price is below {name.upper()}")

    total += 1
    alignment = (snapshot.ma50, snapshot.ma100, snapshot.ma200)
    if all(item is not None for item in alignment):
        available += 1
        if snapshot.current_spot_price > snapshot.ma50 > snapshot.ma100 > snapshot.ma200:
            score += rules["alignment_points"]
            reasons.append("moving averages are bullishly aligned")
        elif snapshot.current_spot_price < snapshot.ma50 < snapshot.ma100 < snapshot.ma200:
            score -= rules["alignment_points"]
            reasons.append("moving averages are bearishly aligned")

    for name in ("return_30d", "return_90d", "return_180d"):
        total += 1
        period_return = getattr(snapshot, name)
        if period_return is None:
            continue
        available += 1
        if period_return > 0:
            score += rules["return_points"]
            reasons.append(f"{name} return is positive")
        elif period_return < 0:
            score -= rules["return_points"]
            reasons.append(f"{name} return is negative")

    total += 1
    if snapshot.current_drawdown is not None:
        available += 1
        if snapshot.current_drawdown >= -rules["drawdown_tolerance"]:
            score += rules["drawdown_points"]
            reasons.append("drawdown is within the trend tolerance")
        elif snapshot.current_drawdown <= -0.60:
            score -= rules["drawdown_points"]
            reasons.append("drawdown is materially elevated")

    total += 1
    if snapshot.support_zones:
        available += 1
        score += rules["support_points"]
        reasons.append("confirmed support structure is available")

    total += 1
    if snapshot.volume_state != "UNKNOWN":
        available += 1
        if snapshot.volume_state == "SUPPORTIVE":
            score += rules["volume_points"]
            reasons.append("volume confirms the move")
        elif snapshot.volume_state == "WEAK":
            score -= rules["volume_points"]
            reasons.append("volume confirmation is weak")

    if snapshot.atr14 and snapshot.support_zones:
        nearest = max(snapshot.support_zones, key=lambda zone: zone.midpoint)
        extension = (snapshot.current_spot_price - nearest.midpoint) / snapshot.atr14
        if extension > rules["extension_threshold_atr"]:
            score -= rules["extension_penalty"]
            reasons.append("spot is extended above the nearest support")

    coverage = available / total if total else 0.0
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


trend_factor = calculate_trend_factor
score_trend_factor = calculate_trend_factor
build_trend_factor = calculate_trend_factor
score_trend = calculate_trend_factor
calculate_trend_score = calculate_trend_factor
trend_score = calculate_trend_factor


__all__ = [
    "TrendFactorResult",
    "build_trend_factor",
    "calculate_trend_factor",
    "calculate_trend_score",
    "score_trend",
    "score_trend_factor",
    "trend_factor",
    "trend_score",
]
