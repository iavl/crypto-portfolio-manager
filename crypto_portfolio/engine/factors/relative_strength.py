"""Deterministic relative-strength calculations against BTC."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ...facts.models import RelativeStrengthFacts
from ...models.market import OHLCVSeries
from ...models.policy import Policy, resolve_policy
from ..metrics import annualized_volatility, simple_return
from ..technical import calendar_lookback_return, completed_candles


_HORIZONS = (30, 90, 180)
_DEFAULT_RULES = {
    "positive_threshold": 0.05,
    "negative_threshold": -0.05,
    "horizon_weights": {"30d": 0.2, "90d": 0.4, "180d": 0.4},
}


@dataclass(frozen=True)
class RelativeStrengthFactorResult:
    score: float
    relative_30d: float | None
    relative_90d: float | None
    relative_180d: float | None
    state: str
    confidence: str
    facts: RelativeStrengthFacts
    coverage: float
    reasons: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    relative_drawdown: float | None = None
    volatility_adjusted_excess_return: float | None = None
    pair_trend: str = "UNKNOWN"

    def __post_init__(self) -> None:
        score = float(self.score)
        if not math.isfinite(score) or not 0 <= score <= 100:
            raise ValueError("relative-strength score must be finite and in [0, 100]")
        state = str(self.state).strip().upper()
        if state not in {"OUTPERFORM", "NEUTRAL", "UNDERPERFORM", "UNKNOWN"}:
            raise ValueError("relative-strength state is unsupported")
        confidence = str(self.confidence).strip().upper()
        if confidence not in {"HIGH", "MEDIUM", "LOW"}:
            raise ValueError("relative-strength confidence is unsupported")
        for field in ("relative_30d", "relative_90d", "relative_180d"):
            value = getattr(self, field)
            if value is not None and (not isinstance(value, (int, float)) or not math.isfinite(float(value))):
                raise ValueError(f"{field} must be finite or null")
        coverage = float(self.coverage)
        if not math.isfinite(coverage) or not 0 <= coverage <= 1:
            raise ValueError("relative-strength coverage must be in [0, 1]")
        if not isinstance(self.facts, RelativeStrengthFacts):
            raise ValueError("relative-strength facts must be RelativeStrengthFacts")
        for field in ("relative_drawdown", "volatility_adjusted_excess_return"):
            value = getattr(self, field)
            if value is not None and (not isinstance(value, (int, float)) or not math.isfinite(float(value))):
                raise ValueError(f"{field} must be finite or null")
        pair_trend = str(self.pair_trend).strip().upper()
        if pair_trend not in {"BULLISH", "BEARISH", "NEUTRAL", "UNKNOWN"}:
            raise ValueError("pair_trend is unsupported")
        object.__setattr__(self, "score", score)
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "coverage", coverage)
        object.__setattr__(self, "pair_trend", pair_trend)
        object.__setattr__(self, "reasons", tuple(str(item) for item in self.reasons))
        ids = tuple(str(item).strip() for item in self.evidence_ids)
        if any(not item for item in ids) or len(ids) != len(set(ids)):
            raise ValueError("relative-strength evidence_ids must be unique non-empty strings")
        object.__setattr__(self, "evidence_ids", ids)

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "relative_30d": self.relative_30d,
            "relative_90d": self.relative_90d,
            "relative_180d": self.relative_180d,
            "state": self.state,
            "confidence": self.confidence,
            "facts": self.facts.as_dict(),
            "coverage": self.coverage,
            "reasons": list(self.reasons),
            "evidence_ids": list(self.evidence_ids),
            "relative_drawdown": self.relative_drawdown,
            "volatility_adjusted_excess_return": self.volatility_adjusted_excess_return,
            "pair_trend": self.pair_trend,
        }


def _values(value: Sequence[float] | OHLCVSeries, name: str) -> tuple[float, ...] | OHLCVSeries:
    if isinstance(value, Mapping):
        if "candles" in value:
            value = OHLCVSeries.from_mapping(value)
        elif "prices" in value:
            value = value["prices"]
        else:
            raise ValueError(f"{name} must contain prices or candles")
    if isinstance(value, OHLCVSeries):
        return value
    if isinstance(value, (str, bytes)):
        raise ValueError(f"{name} must be a price sequence or OHLCVSeries")
    values = tuple(value)
    if not values:
        raise ValueError(f"{name} must not be empty")
    result = []
    for item in values:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ValueError(f"{name} must contain numbers")
        number = float(item)
        if not math.isfinite(number) or number <= 0:
            raise ValueError(f"{name} must contain finite prices > 0")
        result.append(number)
    return tuple(result)


def _return(value: tuple[float, ...] | OHLCVSeries, days: int) -> float | None:
    if isinstance(value, OHLCVSeries):
        return calendar_lookback_return(completed_candles(value), days)
    if len(value) <= days:
        return None
    return simple_return(value[-days - 1], value[-1])


def _ratio_history(
    asset: tuple[float, ...] | OHLCVSeries,
    btc: tuple[float, ...] | OHLCVSeries,
) -> tuple[float, ...]:
    if isinstance(asset, tuple) and isinstance(btc, tuple):
        return tuple(left / right for left, right in zip(asset, btc))
    if isinstance(asset, OHLCVSeries) and isinstance(btc, OHLCVSeries):
        btc_by_timestamp = {item.timestamp: item.close for item in completed_candles(btc)}
        return tuple(
            item.close / btc_by_timestamp[item.timestamp]
            for item in completed_candles(asset)
            if item.timestamp in btc_by_timestamp
        )
    return ()


def _volatility_adjusted(
    relative_90d: float | None,
    asset: tuple[float, ...] | OHLCVSeries,
    btc: tuple[float, ...] | OHLCVSeries,
) -> float | None:
    if relative_90d is None or isinstance(asset, OHLCVSeries) or isinstance(btc, OHLCVSeries):
        return None
    if len(asset) < 3 or len(btc) < 3:
        return None
    denominator = max(annualized_volatility(asset), annualized_volatility(btc), 1e-12)
    return relative_90d / denominator


def _rules(policy: Policy) -> Mapping[str, Any]:
    return {**_DEFAULT_RULES, **policy.factor_rules.get("relative_strength", {})}


def _state(value: float | None, positive: float, negative: float) -> str:
    if value is None:
        return "UNKNOWN"
    if value >= positive:
        return "OUTPERFORM"
    if value <= negative:
        return "UNDERPERFORM"
    return "NEUTRAL"


def calculate_relative_strength(
    asset_prices: Sequence[float] | OHLCVSeries | Mapping[str, Any] | None = None,
    btc_prices: Sequence[float] | OHLCVSeries | Mapping[str, Any] | None = None,
    *,
    asset_history: Sequence[float] | OHLCVSeries | Mapping[str, Any] | None = None,
    btc_history: Sequence[float] | OHLCVSeries | Mapping[str, Any] | None = None,
    symbol: str = "ASSET",
    policy: Policy | None = None,
    evidence_ids: tuple[str, ...] | list[str] = (),
) -> RelativeStrengthFactorResult:
    """Calculate excess returns for 30D, 90D, and 180D horizons."""
    if asset_prices is not None and asset_history is not None:
        raise ValueError("provide only one of asset_prices or asset_history")
    if btc_prices is not None and btc_history is not None:
        raise ValueError("provide only one of btc_prices or btc_history")
    asset_prices = asset_prices if asset_prices is not None else asset_history
    btc_prices = btc_prices if btc_prices is not None else btc_history
    if asset_prices is None or btc_prices is None:
        raise ValueError("both asset and BTC price histories are required")
    resolved = policy or resolve_policy()
    asset = _values(asset_prices, "asset_prices")
    btc = _values(btc_prices, "btc_prices")
    if isinstance(asset, tuple) and isinstance(btc, tuple) and len(asset) != len(btc):
        raise ValueError("asset and BTC histories must have equal lengths")
    if isinstance(asset, OHLCVSeries) and isinstance(btc, OHLCVSeries):
        asset_dates = [item.timestamp for item in completed_candles(asset)]
        btc_dates = [item.timestamp for item in completed_candles(btc)]
        if asset_dates and btc_dates and asset_dates[-1] != btc_dates[-1]:
            raise ValueError("asset and BTC histories must share the latest timestamp")
    asset_returns = {days: _return(asset, days) for days in _HORIZONS}
    btc_returns = {days: _return(btc, days) for days in _HORIZONS}
    relative = {
        days: asset_returns[days] - btc_returns[days]
        if asset_returns[days] is not None and btc_returns[days] is not None
        else None
        for days in _HORIZONS
    }
    rules = _rules(resolved)
    weights = rules["horizon_weights"]
    available = [days for days in _HORIZONS if relative[days] is not None]
    weighted_score = 50.0
    if available:
        def horizon_score(days: int) -> float:
            value = relative[days]
            positive = float(rules["positive_threshold"])
            negative = float(rules["negative_threshold"])
            if value >= positive:
                return 100.0
            if value <= negative:
                return 0.0
            span = positive - negative
            return 100.0 * (value - negative) / span if span else 50.0

        total_weight = sum(float(weights[f"{days}d"]) for days in available)
        weighted_score = sum(
            horizon_score(days) * float(weights[f"{days}d"]) for days in available
        ) / total_weight
    states = [_state(relative[days], float(rules["positive_threshold"]), float(rules["negative_threshold"])) for days in _HORIZONS]
    non_unknown = [state for state in states if state != "UNKNOWN"]
    if not non_unknown:
        state = "UNKNOWN"
    elif non_unknown.count("OUTPERFORM") > non_unknown.count("UNDERPERFORM"):
        state = "OUTPERFORM"
    elif non_unknown.count("UNDERPERFORM") > non_unknown.count("OUTPERFORM"):
        state = "UNDERPERFORM"
    else:
        state = "NEUTRAL"
    coverage = len(available) / len(_HORIZONS)
    confidence = "HIGH" if coverage == 1 else "MEDIUM" if coverage >= 2 / 3 else "LOW"
    reasons = tuple(
        f"{days}D excess return is {relative[days]:+.2%}" for days in _HORIZONS if relative[days] is not None
    ) or ("relative return history is insufficient",)
    ratio = _ratio_history(asset, btc)
    relative_drawdown = ratio[-1] / max(ratio) - 1.0 if ratio else None
    pair_trend = (
        "BULLISH"
        if relative[90] is not None and relative[180] is not None and relative[90] > 0 and relative[180] > 0
        else "BEARISH"
        if relative[90] is not None and relative[180] is not None and relative[90] < 0 and relative[180] < 0
        else "NEUTRAL"
        if relative[90] is not None or relative[180] is not None
        else "UNKNOWN"
    )
    volatility_adjusted = _volatility_adjusted(relative[90], asset, btc)
    ids = list(evidence_ids)
    for series in (asset_prices, btc_prices):
        if isinstance(series, OHLCVSeries):
            ids.append(series.ohlcv_hash)
    facts = RelativeStrengthFacts(
        symbol=symbol,
        current={
            **{f"relative_{days}d": relative[days] for days in _HORIZONS},
            "relative_drawdown": relative_drawdown,
            "volatility_adjusted_excess_return": volatility_adjusted,
            "pair_trend": pair_trend,
        },
        previous={},
        changes={},
        trends={f"relative_{days}d": _state(relative[days], float(rules["positive_threshold"]), float(rules["negative_threshold"])) for days in _HORIZONS},
        coverage=coverage,
        freshness="CURRENT",
        source_ids=tuple(dict.fromkeys(ids)),
        data_quality_flags=() if coverage == 1 else ("INSUFFICIENT_HORIZON_HISTORY",),
    )
    return RelativeStrengthFactorResult(
        score=weighted_score,
        relative_30d=relative[30],
        relative_90d=relative[90],
        relative_180d=relative[180],
        state=state,
        confidence=confidence,
        facts=facts,
        coverage=coverage,
        reasons=reasons,
        evidence_ids=facts.source_ids,
        relative_drawdown=relative_drawdown,
        volatility_adjusted_excess_return=volatility_adjusted,
        pair_trend=pair_trend,
    )


calculate_relative_strength_factor = calculate_relative_strength
relative_strength_factor = calculate_relative_strength
build_relative_strength_factor = calculate_relative_strength
relative_strength_vs_btc = calculate_relative_strength


__all__ = [
    "RelativeStrengthFactorResult",
    "build_relative_strength_factor",
    "calculate_relative_strength",
    "calculate_relative_strength_factor",
    "relative_strength_factor",
    "relative_strength_vs_btc",
]
