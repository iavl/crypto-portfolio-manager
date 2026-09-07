"""Deterministic relative-strength calculations against BTC."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

from ...facts.models import RelativeStrengthFacts
from ...models.market import OHLCVSeries
from ...models.policy import Policy, resolve_policy
from ..metrics import annualized_volatility, simple_return
from ..technical import completed_candles, expected_latest_completed_date


_HORIZONS = (30, 90, 180)


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
    risk_adjusted_excess_returns: Mapping[str, float | None] | None = None

    def __post_init__(self) -> None:
        score = float(self.score)
        if not math.isfinite(score) or not 0 <= score <= 100:
            raise ValueError("relative-strength score must be finite and in [0, 100]")
        state = str(self.state).strip().upper()
        if state not in {"OUTPERFORM", "NEUTRAL", "UNDERPERFORM", "UNKNOWN", "NOT_APPLICABLE"}:
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
        adjusted = dict(self.risk_adjusted_excess_returns or {})
        if set(adjusted) - {f"{days}d" for days in _HORIZONS}:
            raise ValueError("risk_adjusted_excess_returns contains an unknown horizon")
        for key, value in adjusted.items():
            if value is not None and (not isinstance(value, (int, float)) or not math.isfinite(float(value))):
                raise ValueError(f"risk_adjusted_excess_returns.{key} must be finite or null")
        object.__setattr__(self, "score", score)
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "coverage", coverage)
        object.__setattr__(self, "pair_trend", pair_trend)
        object.__setattr__(self, "risk_adjusted_excess_returns", adjusted)
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
            "risk_adjusted_excess_returns": dict(self.risk_adjusted_excess_returns),
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


def _aligned_prices(
    asset: tuple[float, ...] | OHLCVSeries,
    btc: tuple[float, ...] | OHLCVSeries,
    days: int,
    *,
    daily: bool = True,
) -> tuple[tuple[float, ...], tuple[float, ...]] | None:
    if isinstance(asset, tuple) and isinstance(btc, tuple):
        if len(asset) != len(btc) or len(asset) <= days:
            return None
        return asset[-days - 1 :], btc[-days - 1 :]
    if not isinstance(asset, OHLCVSeries) or not isinstance(btc, OHLCVSeries):
        return None
    asset_candles = completed_candles(asset)
    btc_candles = completed_candles(btc)
    asset_by_time = {item.timestamp: item.close for item in asset_candles}
    btc_by_time = {item.timestamp: item.close for item in btc_candles}
    common = sorted(set(asset_by_time) & set(btc_by_time))
    if not common:
        return None
    end = common[-1]
    start = (datetime.fromisoformat(end.replace("Z", "+00:00")) - timedelta(days=days)).isoformat().replace(
        "+00:00", "Z"
    )
    if start not in asset_by_time or start not in btc_by_time:
        return None
    # Sample a common 24-hour cadence even when the input is intraday.
    end_time = datetime.fromisoformat(end.replace("Z", "+00:00"))
    selected = [
        timestamp for timestamp in common
        if start <= timestamp <= end and (
            not daily
            or (end_time - datetime.fromisoformat(timestamp.replace("Z", "+00:00"))).total_seconds() % 86400 == 0
        )
    ]
    if len(selected) < days + 1:
        return None
    return tuple(asset_by_time[timestamp] for timestamp in selected), tuple(
        btc_by_time[timestamp] for timestamp in selected
    )


def _all_aligned_prices(
    asset: tuple[float, ...] | OHLCVSeries,
    btc: tuple[float, ...] | OHLCVSeries,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    if isinstance(asset, tuple) and isinstance(btc, tuple):
        if len(asset) != len(btc):
            raise ValueError("asset and BTC histories must have equal lengths")
        return asset, btc
    if not isinstance(asset, OHLCVSeries) or not isinstance(btc, OHLCVSeries):
        return (), ()
    asset_by_time = {item.timestamp: item.close for item in completed_candles(asset)}
    btc_by_time = {item.timestamp: item.close for item in completed_candles(btc)}
    common = sorted(set(asset_by_time) & set(btc_by_time))
    return tuple(asset_by_time[timestamp] for timestamp in common), tuple(
        btc_by_time[timestamp] for timestamp in common
    )


def _relative_daily_returns(asset: Sequence[float], btc: Sequence[float]) -> tuple[float, ...]:
    return tuple(
        (asset[index] / asset[index - 1] - 1.0)
        - (btc[index] / btc[index - 1] - 1.0)
        for index in range(1, min(len(asset), len(btc)))
    )


def _risk_adjusted(excess: float | None, asset: Sequence[float], btc: Sequence[float], days: int) -> float | None:
    if excess is None:
        return None
    returns = _relative_daily_returns(asset, btc)
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    annualized = math.sqrt(variance) * math.sqrt(365.0)
    expected_horizon_vol = annualized * math.sqrt(days / 365.0)
    return excess / max(expected_horizon_vol, 1e-12)


def _legacy_volatility_adjusted(
    excess: float | None,
    asset: tuple[float, ...] | OHLCVSeries,
    btc: tuple[float, ...] | OHLCVSeries,
) -> float | None:
    if excess is None or not isinstance(asset, tuple) or not isinstance(btc, tuple):
        return None
    if len(asset) < 3 or len(btc) < 3:
        return None
    return excess / max(annualized_volatility(asset), annualized_volatility(btc), 1e-12)


def _rules(policy: Policy) -> Mapping[str, Any]:
    return policy.factor_rules["relative_strength"]


def _legacy_horizon_score(value: float, positive: float, negative: float) -> float:
    if value >= positive:
        return 100.0
    if value <= negative:
        return 0.0
    span = positive - negative
    return 100.0 * (value - negative) / span if span else 50.0


def _v2_horizon_score(value: float | None, neutral: float, saturation: float) -> float | None:
    if value is None:
        return None
    if abs(value) <= neutral:
        return 50.0
    span = saturation - neutral
    if value > 0:
        return 50.0 + 50.0 * (min(value, saturation) - neutral) / span
    return 50.0 + 50.0 * (max(value, -saturation) + neutral) / span


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
    """Calculate raw and volatility-adjusted excess returns."""
    if asset_prices is not None and asset_history is not None:
        raise ValueError("provide only one of asset_prices or asset_history")
    if btc_prices is not None and btc_history is not None:
        raise ValueError("provide only one of btc_prices or btc_history")
    resolved = policy or resolve_policy()
    normalized_symbol = str(symbol).strip().upper()
    if normalized_symbol == "BTC":
        facts = RelativeStrengthFacts(
            symbol="BTC",
            current={},
            previous={},
            changes={},
            trends={},
            coverage=0.0,
            freshness="UNKNOWN",
            source_ids=(),
            data_quality_flags=("NOT_APPLICABLE",),
        )
        return RelativeStrengthFactorResult(
            score=50.0,
            relative_30d=None,
            relative_90d=None,
            relative_180d=None,
            state="NOT_APPLICABLE",
            confidence="LOW",
            facts=facts,
            coverage=0.0,
            reasons=("BTC has no BTC-relative comparison",),
        )
    asset_prices = asset_prices if asset_prices is not None else asset_history
    btc_prices = btc_prices if btc_prices is not None else btc_history
    if asset_prices is None or btc_prices is None:
        raise ValueError("both asset and BTC price histories are required")
    asset = _values(asset_prices, "asset_prices")
    btc = _values(btc_prices, "btc_prices")
    if isinstance(asset, OHLCVSeries) and isinstance(btc, OHLCVSeries):
        asset_dates = [item.timestamp for item in completed_candles(asset)]
        btc_dates = [item.timestamp for item in completed_candles(btc)]
        if asset_dates and btc_dates and asset_dates[-1] != btc_dates[-1]:
            raise ValueError("asset and BTC histories must share the latest timestamp")
    elif isinstance(asset, tuple) and isinstance(btc, tuple) and len(asset) != len(btc):
        raise ValueError("asset and BTC histories must have equal lengths")

    aligned_by_horizon = {days: _aligned_prices(asset, btc, days, daily=resolved.policy_version >= 2) for days in _HORIZONS}
    asset_returns: dict[int, float | None] = {}
    btc_returns: dict[int, float | None] = {}
    relative: dict[int, float | None] = {}
    adjusted: dict[int, float | None] = {}
    for days in _HORIZONS:
        aligned = aligned_by_horizon[days]
        if aligned is None:
            asset_returns[days] = btc_returns[days] = relative[days] = adjusted[days] = None
            continue
        aligned_asset, aligned_btc = aligned
        asset_returns[days] = simple_return(aligned_asset[0], aligned_asset[-1])
        btc_returns[days] = simple_return(aligned_btc[0], aligned_btc[-1])
        relative[days] = asset_returns[days] - btc_returns[days]
        adjusted[days] = _risk_adjusted(relative[days], aligned_asset, aligned_btc, days)

    rules = _rules(resolved)
    weights = rules["horizon_weights"]
    signal = None
    if resolved.policy_version == 1:
        available = [days for days in _HORIZONS if relative[days] is not None]
        if available:
            positive = float(rules["positive_threshold"])
            negative = float(rules["negative_threshold"])
            total_weight = sum(float(weights[f"{days}d"]) for days in available)
            weighted_score = sum(
                _legacy_horizon_score(relative[days], positive, negative) * float(weights[f"{days}d"])
                for days in available
            ) / total_weight
        else:
            weighted_score = 50.0
        states = [_state(relative[days], float(rules["positive_threshold"]), float(rules["negative_threshold"])) for days in _HORIZONS]
    else:
        available = [days for days in _HORIZONS if adjusted[days] is not None and weights[f"{days}d"] > 0]
        neutral = float(rules["risk_adjusted_neutral_band"])
        saturation = float(rules["risk_adjusted_saturation"])
        if available:
            total_weight = sum(float(weights[f"{days}d"]) for days in available)
            scores = {
                days: _v2_horizon_score(adjusted[days], neutral, saturation) for days in available
            }
            weighted_score = sum(scores[days] * float(weights[f"{days}d"]) for days in available) / total_weight
            signal = sum(adjusted[days] * float(weights[f"{days}d"]) for days in available) / total_weight
            states = [
                "UNKNOWN" if adjusted[days] is None else
                "OUTPERFORM" if adjusted[days] > neutral else
                "UNDERPERFORM" if adjusted[days] < -neutral else "NEUTRAL"
                for days in _HORIZONS
            ]
        else:
            weighted_score = 50.0
            signal = None
            states = ["UNKNOWN"] * len(_HORIZONS)

    non_unknown = [state for state in states if state != "UNKNOWN"]
    if not non_unknown:
        state = "UNKNOWN"
    elif resolved.policy_version >= 2 and signal is not None:
        state = "OUTPERFORM" if signal > float(rules["risk_adjusted_neutral_band"]) else (
            "UNDERPERFORM" if signal < -float(rules["risk_adjusted_neutral_band"]) else "NEUTRAL"
        )
    elif non_unknown.count("OUTPERFORM") > non_unknown.count("UNDERPERFORM"):
        state = "OUTPERFORM"
    elif non_unknown.count("UNDERPERFORM") > non_unknown.count("OUTPERFORM"):
        state = "UNDERPERFORM"
    else:
        state = "NEUTRAL"
    coverage = len(available) / len(_HORIZONS) if resolved.policy_version == 1 else (
        sum(weights[f"{days}d"] for days in available) / sum(weights.values())
    )
    confidence = "HIGH" if coverage == 1 else "MEDIUM" if coverage >= 2 / 3 else "LOW"
    reasons = tuple(
        [
            *(f"{days}D excess return is {relative[days]:+.2%}" for days in _HORIZONS if relative[days] is not None),
            *(f"{days}D risk-adjusted excess return is {adjusted[days]:+.3f}" for days in _HORIZONS if adjusted[days] is not None),
        ]
        or ["relative return history is insufficient"]
    )
    all_asset, all_btc = _all_aligned_prices(asset, btc)
    ratio = tuple(left / right for left, right in zip(all_asset, all_btc))
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
    ids = list(evidence_ids)
    for series in (asset_prices, btc_prices):
        if isinstance(series, OHLCVSeries):
            ids.append(series.ohlcv_hash)
    legacy_adjusted = _legacy_volatility_adjusted(relative[90], asset, btc) if resolved.policy_version == 1 else None
    freshness = "CURRENT"
    if resolved.policy_version >= 2:
        for series in (asset, btc):
            if isinstance(series, OHLCVSeries):
                candles = completed_candles(series)
                if not series.fetched_at or not candles:
                    freshness = "UNKNOWN"
                elif freshness != "UNKNOWN" and (
                    datetime.fromisoformat(candles[-1].timestamp.replace("Z", "+00:00")).date()
                    < expected_latest_completed_date(series.fetched_at)
                ):
                    freshness = "STALE"
    facts = RelativeStrengthFacts(
        symbol=normalized_symbol,
        current={
            **{f"relative_{days}d": relative[days] for days in _HORIZONS},
            **{f"risk_adjusted_relative_{days}d": adjusted[days] for days in _HORIZONS},
            "relative_drawdown": relative_drawdown,
            "volatility_adjusted_excess_return": legacy_adjusted if resolved.policy_version == 1 else adjusted[90],
            "pair_trend": pair_trend,
        },
        previous={},
        changes={},
        trends={f"relative_{days}d": states[index] for index, days in enumerate(_HORIZONS)},
        coverage=coverage,
        freshness=freshness,
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
        volatility_adjusted_excess_return=legacy_adjusted if resolved.policy_version == 1 else adjusted[90],
        pair_trend=pair_trend,
        risk_adjusted_excess_returns={f"{days}d": adjusted[days] for days in _HORIZONS},
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
