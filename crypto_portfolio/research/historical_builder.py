"""Point-in-time review construction from frozen OHLCV datasets."""

from __future__ import annotations

from bisect import bisect_right
from datetime import timedelta
from dataclasses import replace
from typing import Any, Mapping, Sequence

from ..engine.factors.relative_strength import calculate_relative_strength
from ..engine.factors.trend import calculate_trend_factor
from ..engine.regime_inputs import build_regime_inputs
from ..engine.scoring import score_assessment
from ..engine.strategy_replay import ReplayReview
from ..engine.technical import build_technical_snapshot
from ..models.evidence import AssetAssessment, EventRiskAssessment, FactorScore
from ..models.market import OHLCVSeries, SpotPrice
from ..models.policy import Policy, SCORING_FACTORS
from ..models.time import parse_timestamp


def _asset_type(policy: Policy, symbol: str) -> str:
    return "core" if symbol in policy.core_symbols else "satellite" if symbol in policy.satellite_symbols else "other"


def _assessment(
    *,
    symbol: str,
    snapshot: Any,
    daily: OHLCVSeries,
    btc_daily: OHLCVSeries,
    as_of: str,
    policy: Policy,
    semantic_score: int | None,
) -> AssetAssessment:
    trend = calculate_trend_factor(snapshot, policy=policy)
    factors: dict[str, FactorScore] = {
        "trend": FactorScore("trend", trend.score, availability="AVAILABLE", reliability=trend.coverage),
    }
    relative_score: float | None = None
    if symbol != "BTC":
        relative = calculate_relative_strength(daily, btc_daily, symbol=symbol, policy=policy, as_of=as_of)
        factors["relative_strength_btc"] = FactorScore(
            "relative_strength_btc", relative.score, availability="AVAILABLE", reliability=relative.coverage,
        )
        relative_score = relative.score
    profile = policy.scoring_profile(symbol)
    for factor in SCORING_FACTORS:
        if profile[factor] <= 0:
            factors[factor] = FactorScore(factor, None, availability="NOT_APPLICABLE")
        elif factor not in factors:
            factors[factor] = FactorScore(
                factor,
                None if semantic_score is None else float(semantic_score),
                availability="MISSING" if semantic_score is None else "AVAILABLE",
                reliability=None if semantic_score is None else 1.0,
            )
    raw = AssetAssessment(
        symbol=symbol,
        factor_scores=factors,
        asset_type=_asset_type(policy, symbol),
        relative_strength_vs_btc=relative_score,
        risk_tier="normal",
        risk_tier_source="POLICY_DEFAULT",
        critical_data_complete=semantic_score is not None,
        event_risk=EventRiskAssessment(
            "NORMAL", reasons=("historical event state is unresolved",) if semantic_score is None else (),
            unresolved=semantic_score is None,
        ),
    )
    return score_assessment(raw, policy=policy)[0]


def build_historical_reviews(
    *,
    daily_by_symbol: Mapping[str, OHLCVSeries],
    hourly_by_symbol: Mapping[str, OHLCVSeries],
    symbols: Sequence[str],
    initial_weights: Mapping[str, float],
    initial_value: float,
    start_at: str,
    end_at: str,
    policy: Policy,
    semantic_score: int | None = None,
) -> tuple[ReplayReview, ...]:
    """Build reviews using only candles completed at each decision boundary."""
    risk_symbols = tuple(symbol for symbol in symbols if symbol != "USD")
    if "BTC" not in risk_symbols:
        raise ValueError("historical reviews require BTC as the benchmark and regime anchor")
    missing_daily = sorted(set(risk_symbols) - set(daily_by_symbol))
    missing_hourly = sorted(set(risk_symbols) - set(hourly_by_symbol))
    if missing_daily:
        raise ValueError("daily OHLCV is missing for: " + ", ".join(missing_daily))
    if missing_hourly:
        raise ValueError("hourly execution OHLCV is missing for: " + ", ".join(missing_hourly))
    start, end = parse_timestamp(start_at), parse_timestamp(end_at)
    btc = daily_by_symbol["BTC"]
    daily_cache = {symbol: tuple(series.completed_candles()) for symbol, series in daily_by_symbol.items()}
    daily_times = {symbol: tuple(parse_timestamp(item.timestamp) for item in candles)
                   for symbol, candles in daily_cache.items()}
    hourly_cache = {symbol: tuple(series.completed_candles()) for symbol, series in hourly_by_symbol.items()}
    hourly_times = {symbol: tuple(parse_timestamp(item.timestamp) for item in candles)
                    for symbol, candles in hourly_cache.items()}
    btc_candles = [item for item in daily_cache["BTC"]
                   if start <= parse_timestamp(item.timestamp) + timedelta(days=1) < end]
    reviews: list[ReplayReview] = []
    for btc_candle in btc_candles:
        as_of_moment = parse_timestamp(btc_candle.timestamp) + timedelta(days=1)
        period_end = as_of_moment + timedelta(days=1)
        if period_end > end:
            break
        as_of = as_of_moment.isoformat().replace("+00:00", "Z")
        period_end_text = period_end.isoformat().replace("+00:00", "Z")
        snapshots: dict[str, Any] = {}
        assessments: dict[str, Any] = {}
        next_returns: dict[str, float] = {"USD": 0.0}
        current_prices: dict[str, float] = {}
        execution_bars: dict[str, tuple[Mapping[str, Any], ...]] = {}
        usable = True
        for symbol in risk_symbols:
            series = daily_by_symbol[symbol]
            available_index = bisect_right(daily_times[symbol], as_of_moment - timedelta(days=1))
            next_index = bisect_right(daily_times[symbol], period_end - timedelta(days=1))
            completed = daily_cache[symbol][:available_index]
            next_completed = daily_cache[symbol][:next_index]
            if not completed or len(next_completed) <= len(completed):
                usable = False
                break
            current_close = completed[-1].close
            current_prices[symbol] = current_close
            next_close = next_completed[-1].close
            spot = SpotPrice(
                symbol, current_close, as_of, series.source,
                fetched_at=series.fetched_at, venue=series.venue,
                market=series.market, quote_currency=series.quote_currency,
            )
            snapshot_series = OHLCVSeries(
                series.symbol, series.timeframe,
                tuple(completed[-max(240, int(policy.execution.get("minimum_history_days", 200))):]),
                series.source, series.fetched_at, series.venue, series.market, series.quote_currency,
            )
            snapshot = build_technical_snapshot(snapshot_series, spot, as_of=as_of, policy=policy)
            snapshots[symbol] = (snapshot.as_dict(),)
            asset_assessment_series = OHLCVSeries(
                symbol, "1D", tuple(completed[-400:]), series.source, series.fetched_at,
                series.venue, series.market, series.quote_currency,
            )
            btc_assessment_series = OHLCVSeries(
                "BTC", "1D", tuple(daily_cache["BTC"][:available_index][-400:]), btc.source,
                btc.fetched_at, btc.venue, btc.market, btc.quote_currency,
            )
            assessments[symbol] = _assessment(
                symbol=symbol, snapshot=snapshot, daily=asset_assessment_series,
                btc_daily=btc_assessment_series,
                as_of=as_of, policy=policy, semantic_score=semantic_score,
            ).as_dict()
            next_returns[symbol] = next_close / current_close - 1.0
            bars = []
            start_index = bisect_right(hourly_times[symbol], as_of_moment)
            end_index = bisect_right(hourly_times[symbol], period_end - timedelta(microseconds=1))
            for candle in hourly_cache[symbol][start_index:end_index]:
                bars.append({
                    "timestamp": candle.timestamp, "open": candle.open, "high": candle.high,
                    "low": candle.low, "close": candle.close,
                })
            if not bars:
                usable = False
                break
            execution_bars[symbol] = tuple(bars)
        if not usable:
            continue
        btc_snapshot = snapshots["BTC"][-1]
        regime_inputs = build_regime_inputs(
            btc_snapshot, portfolio_drawdown=0.0, breadth="UNKNOWN",
            systemic_event_risk=False, provenance_complete=semantic_score is not None,
        ).as_dict()
        reviews.append(ReplayReview(
            as_of=as_of, period_end=period_end_text,
            current_weights=dict(initial_weights), portfolio_value=float(initial_value),
            assessments=assessments, regime_inputs=regime_inputs,
            next_returns=next_returns, technical_inputs=snapshots,
            execution_bars=execution_bars, current_prices=current_prices,
        ))
    if not reviews:
        raise ValueError("historical dataset produced no complete review periods")
    return tuple(reviews)


def rebind_initial_weights(
    reviews: Sequence[ReplayReview], weights: Mapping[str, float], initial_value: float,
) -> tuple[ReplayReview, ...]:
    """Reuse immutable market/evidence work for another independent start."""
    return tuple(replace(review, current_weights=dict(weights), portfolio_value=float(initial_value)) for review in reviews)


def apply_semantic_scenario(
    reviews: Sequence[ReplayReview], policy: Policy, semantic_score: int,
) -> tuple[ReplayReview, ...]:
    """Fill only explicit MISSING positive-weight judgments for mechanism tests."""
    if semantic_score not in {30, 50, 70}:
        raise ValueError("semantic_score must be one of 30, 50, 70")
    result = []
    for review in reviews:
        assessments = {}
        for symbol, raw in review.assessments.items():
            assessment = raw if isinstance(raw, AssetAssessment) else AssetAssessment.from_mapping(symbol, raw)
            factors = dict(assessment.factor_scores)
            profile = policy.scoring_profile(symbol)
            for factor, value in list(factors.items()):
                if profile.get(factor, 0.0) <= 0 or value is None:
                    continue
                parsed = value if isinstance(value, FactorScore) else FactorScore(
                    factor, None, availability="MISSING",
                )
                if parsed.availability == "MISSING":
                    factors[factor] = FactorScore(
                        factor, float(semantic_score), availability="AVAILABLE", reliability=1.0,
                    )
            completed = replace(
                assessment, factor_scores=factors, critical_data_complete=True,
                event_risk=EventRiskAssessment("NORMAL"),
            )
            assessments[symbol] = score_assessment(completed, policy=policy)[0].as_dict()
        regime_inputs = dict(review.regime_inputs)
        regime_inputs["provenance_complete"] = True
        result.append(replace(review, assessments=assessments, regime_inputs=regime_inputs))
    return tuple(result)


__all__ = ["apply_semantic_scenario", "build_historical_reviews", "rebind_initial_weights"]
