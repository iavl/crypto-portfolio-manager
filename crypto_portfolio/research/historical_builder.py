"""Point-in-time review construction from frozen OHLCV datasets."""

from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timedelta
from dataclasses import replace
from typing import Any, Mapping, Sequence

from ..engine.factors.relative_strength import calculate_relative_strength
from ..engine.factors.trend import calculate_trend_factor
from ..engine.regime_inputs import build_regime_inputs
from ..engine.portfolio_risk import (
    beta_to_btc,
    daily_returns,
    realized_volatility,
    trailing_window,
)
from ..engine.risk_tier import estimate_risk_tiers
from ..engine.scoring import score_assessment
from ..engine.strategy_replay import ReplayReview
from ..engine.technical import build_technical_snapshot, moving_average
from ..engine.eth_relative_alpha import eth_alpha_state, ethbtc_ratio_series, relative_signals
from ..models.evidence import AssetAssessment, EventRiskAssessment, FactorScore
from ..models.market import Candle, OHLCVSeries, SpotPrice
from ..models.policy import Policy, SCORING_FACTORS
from ..models.time import parse_timestamp
from .evidence_series import EvidenceContext

BREADTH_MA_WINDOW = 200


def breadth_above_ma(
    completed_by_symbol: Mapping[str, Sequence[Candle]], *, window: int = BREADTH_MA_WINDOW,
) -> float | None:
    """Fraction of symbols whose close exceeds their own simple moving average.

    Point-in-time research proxy for the regime breadth domain: the caller
    supplies only candles completed at the review boundary, so no future bar
    can reach it.  Production derives ``market.breadth`` from CoinGecko's
    fraction of the top-20 non-stable universe with a positive 30d return;
    until that history is harvested this proxy keeps the domain deterministic
    from frozen OHLCV alone.  Returns None when any symbol lacks a full
    window, which the regime reads as UNKNOWN rather than a fabricated value.
    """
    if not completed_by_symbol:
        raise ValueError("breadth requires at least one symbol")
    if isinstance(window, bool) or not isinstance(window, int) or window < 1:
        raise ValueError("breadth window must be a positive integer")
    above = 0
    for candles in completed_by_symbol.values():
        if len(candles) < window:
            return None
        if candles[-1].close > moving_average(candles[-window:], window):
            above += 1
    return above / len(completed_by_symbol)


def _candidate_boundaries(
    btc_candles: Sequence[Candle], *, start: datetime, end: datetime,
) -> list[tuple[datetime, datetime]]:
    """Daily decision boundaries (as_of, period_end) inside [start, end]."""
    boundaries: list[tuple[datetime, datetime]] = []
    for candle in btc_candles:
        as_of_moment = parse_timestamp(candle.timestamp) + timedelta(days=1)
        if as_of_moment < start:
            continue
        if as_of_moment + timedelta(days=1) > end:
            break
        boundaries.append((as_of_moment, as_of_moment + timedelta(days=1)))
    return boundaries


def review_gap_diagnostics(
    daily_by_symbol: Mapping[str, OHLCVSeries],
    *,
    symbols: Sequence[str],
    start_at: str,
    end_at: str,
    produced_reviews: int,
) -> dict[str, Any]:
    """Explain review boundaries dropped to daily-candle gaps, not traded.

    A skipped boundary is one where the frozen data could not support a
    complete review (a symbol lacked the next daily candle, so no return
    label exists).  The per-symbol counts are lower bounds on skip causes;
    execution-bar gaps can drop additional boundaries.
    """
    risk_symbols = tuple(symbol for symbol in symbols if symbol != "USD")
    if "BTC" not in risk_symbols:
        raise ValueError("review gap diagnostics require BTC as the boundary anchor")
    missing_daily = sorted(set(risk_symbols) - set(daily_by_symbol))
    if missing_daily:
        raise ValueError("daily OHLCV is missing for: " + ", ".join(missing_daily))
    start, end = parse_timestamp(start_at), parse_timestamp(end_at)
    boundaries = _candidate_boundaries(daily_by_symbol["BTC"].completed_candles(), start=start, end=end)
    if produced_reviews > len(boundaries):
        raise ValueError("produced reviews exceed the candidate boundary count")
    missing_next: dict[str, int] = {}
    for symbol in risk_symbols:
        times = tuple(parse_timestamp(item.timestamp) for item in daily_by_symbol[symbol].completed_candles())
        count = 0
        for as_of_moment, period_end in boundaries:
            available = bisect_right(times, as_of_moment - timedelta(days=1))
            following = bisect_right(times, period_end - timedelta(days=1))
            if following <= available:
                count += 1
        missing_next[symbol] = count
    return {
        "candidate_boundaries": len(boundaries),
        "produced_reviews": produced_reviews,
        "skipped_boundaries": len(boundaries) - produced_reviews,
        "boundaries_missing_next_candle": missing_next,
    }


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
    evidence: EvidenceContext | None = None,
    risk_tier: str = "normal",
    risk_tier_source: str = "POLICY_DEFAULT",
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
    harvested: dict[str, FactorScore] = (
        evidence.factor_scores(symbol, as_of) if evidence is not None else {}
    )
    for factor in SCORING_FACTORS:
        if profile[factor] <= 0:
            factors[factor] = FactorScore(factor, None, availability="NOT_APPLICABLE")
        elif factor in factors:
            continue
        elif factor in harvested:
            factors[factor] = harvested[factor]
        else:
            factors[factor] = FactorScore(
                factor,
                None if semantic_score is None else float(semantic_score),
                availability="MISSING" if semantic_score is None else "AVAILABLE",
                reliability=None if semantic_score is None else 1.0,
            )
    # An asset whose entire positive-weight profile is AVAILABLE from frozen
    # point-in-time inputs has complete critical data for replay purposes:
    # the judgment layer is explicitly ablated, not silently ignored.
    # Strategy V2.1 (volatility-budget mode): the entry-critical evidence is
    # the MARKET family — missing structural factors cap conviction
    # (TACTICAL_ONLY) instead of classifying the asset as having incomplete
    # critical data, which previously made satellites permanently
    # NOT_ACTIONABLE in strict replay. Legacy mode keeps the full-profile
    # definition unchanged.
    if (policy.risk_engine or {}).get("mode", "legacy_drawdown") == "volatility_budget":
        from ..engine.scoring_v3 import scoring_v3_families
        market_factors = set(scoring_v3_families(symbol, policy))
        missing_critical = any(
            profile[factor] > 0
            and factor in market_factors
            and factors[factor].availability == "MISSING"
            for factor in SCORING_FACTORS
        )
    else:
        missing_critical = any(
            profile[factor] > 0 and factors[factor].availability == "MISSING"
            for factor in SCORING_FACTORS
        )
    raw = AssetAssessment(
        symbol=symbol,
        factor_scores=factors,
        asset_type=_asset_type(policy, symbol),
        relative_strength_vs_btc=relative_score,
        risk_tier=risk_tier,
        risk_tier_source=risk_tier_source,
        critical_data_complete=semantic_score is not None or not missing_critical,
        event_risk=EventRiskAssessment(
            "NORMAL", reasons=("historical event state is unresolved",) if semantic_score is None and missing_critical else (),
            unresolved=semantic_score is None and missing_critical,
        ),
    )
    return score_assessment(raw, policy=policy)[0]


def build_historical_reviews(
    *,
    daily_by_symbol: Mapping[str, OHLCVSeries],
    hourly_by_symbol: Mapping[str, OHLCVSeries] | None = None,
    execution_by_symbol: Mapping[str, OHLCVSeries] | None = None,
    execution_timeframe: str = "1H",
    symbols: Sequence[str],
    initial_weights: Mapping[str, float],
    initial_value: float,
    start_at: str,
    end_at: str,
    policy: Policy,
    semantic_score: int | None = None,
    evidence: EvidenceContext | None = None,
) -> tuple[ReplayReview, ...]:
    """Build reviews using only candles completed at each decision boundary."""
    risk_symbols = tuple(symbol for symbol in symbols if symbol != "USD")
    if "BTC" not in risk_symbols:
        raise ValueError("historical reviews require BTC as the benchmark and regime anchor")
    missing_daily = sorted(set(risk_symbols) - set(daily_by_symbol))
    execution_series = execution_by_symbol or hourly_by_symbol or {}
    execution_timeframe = str(execution_timeframe).strip().upper()
    if execution_timeframe not in {"1D", "1H"}:
        raise ValueError("execution_timeframe must be 1D or 1H")
    missing_hourly = sorted(set(risk_symbols) - set(execution_series))
    if missing_daily:
        raise ValueError("daily OHLCV is missing for: " + ", ".join(missing_daily))
    if missing_hourly:
        raise ValueError("hourly execution OHLCV is missing for: " + ", ".join(missing_hourly))
    start, end = parse_timestamp(start_at), parse_timestamp(end_at)
    btc = daily_by_symbol["BTC"]
    daily_cache = {symbol: tuple(series.completed_candles()) for symbol, series in daily_by_symbol.items()}
    daily_times = {symbol: tuple(parse_timestamp(item.timestamp) for item in candles)
                   for symbol, candles in daily_cache.items()}
    execution_cache = {symbol: tuple(series.completed_candles()) for symbol, series in execution_series.items()}
    execution_times = {symbol: tuple(parse_timestamp(item.timestamp) for item in candles)
                       for symbol, candles in execution_cache.items()}
    btc_candles = daily_cache["BTC"]
    # ETH/BTC relative-alpha series (Strategy V2.2 Phase B): computed once
    # from completed daily candles, then evaluated point-in-time at every
    # boundary. The state is a research diagnostic here; only the policy's
    # tilt_enabled gate lets it influence allocation.
    eth_ratio_points = (
        ethbtc_ratio_series(daily_by_symbol["ETH"], daily_by_symbol["BTC"])
        if "ETH" in daily_by_symbol else ()
    )
    tier_config = policy.risk_tier_estimation or {}
    tier_history = int(tier_config.get("minimum_history_days", 100)) if tier_config else 100
    previous_tiers: dict[str, str] = {}
    reviews: list[ReplayReview] = []
    for as_of_moment, period_end in _candidate_boundaries(btc_candles, start=start, end=end):
        as_of = as_of_moment.isoformat().replace("+00:00", "Z")
        period_end_text = period_end.isoformat().replace("+00:00", "Z")
        snapshots: dict[str, Any] = {}
        assessments: dict[str, Any] = {}
        next_returns: dict[str, float] = {"USD": 0.0}
        current_prices: dict[str, float] = {}
        execution_bars: dict[str, tuple[Mapping[str, Any], ...]] = {}
        completed_by_symbol: dict[str, tuple[Candle, ...]] = {}
        # Deterministic risk tiers (Strategy V2 Phase 4): measured from the
        # same completed candles the assessments use, with the hysteresis
        # carried across boundaries through previous_tiers.
        boundary_tiers: dict[str, dict[str, Any]] = {}
        try:
            boundary = as_of_moment - timedelta(days=1)
            returns_by_symbol: dict[str, list[float]] = {}
            for tier_symbol in risk_symbols:
                times = daily_times.get(tier_symbol)
                candles = daily_cache.get(tier_symbol)
                if times is None or candles is None:
                    continue
                available = bisect_right(times, boundary)
                window = candles[max(0, available - tier_history):available]
                if len(window) < tier_history:
                    continue
                returns_by_symbol[tier_symbol] = daily_returns(
                    [float(item.close) for item in window]
                )
            if "BTC" in returns_by_symbol and len(returns_by_symbol) > 1:
                vols = {
                    tier_symbol: realized_volatility(
                        list(trailing_window(values, 90)), annualization_days=365,
                    )
                    for tier_symbol, values in returns_by_symbol.items()
                }
                betas = beta_to_btc(returns_by_symbol, window=90)
                measured = estimate_risk_tiers(
                    annualized_volatility=vols,
                    beta=betas,
                    btc_symbol="BTC",
                    previous_tiers=previous_tiers or None,
                    policy=policy,
                )
                for tier_symbol, row in measured.items():
                    boundary_tiers[tier_symbol] = row
                    previous_tiers[tier_symbol] = row["tier"]
        except ValueError:
            # An undefined measurement (constant series, insufficient BTC
            # history) leaves the tier unmeasured at this boundary: the
            # assessment keeps the policy default, never a fabricated tier.
            boundary_tiers = {}
        universe_config = policy.dynamic_universe or {}
        universe_membership = bool(universe_config.get("enabled", False))
        universe_history = int(universe_config.get("minimum_history_days", 365))
        universe_volume_floor = float(universe_config.get("minimum_median_volume_usd", 0.0))
        usable = True
        for symbol in risk_symbols:
            series = daily_by_symbol[symbol]
            available_index = bisect_right(daily_times[symbol], as_of_moment - timedelta(days=1))
            next_index = bisect_right(daily_times[symbol], period_end - timedelta(days=1))
            completed = daily_cache[symbol][:available_index]
            completed_by_symbol[symbol] = completed
            next_completed = daily_cache[symbol][:next_index]
            if not completed or len(next_completed) <= len(completed):
                usable = False
                break
            # Point-in-time universe membership (Strategy V2 Phase 6): BTC is
            # the anchor asset and always a member; every other asset must
            # qualify on trailing history and liquidity at THIS boundary or
            # it receives no assessment and no technical snapshot - no new
            # entries - while prices and bars keep flowing so held positions
            # stay marked and can still be reduced.
            universe_eligible = True
            if universe_membership and symbol != "BTC":
                if len(completed) < universe_history:
                    universe_eligible = False
                elif universe_volume_floor > 0:
                    window = completed[-universe_history:]
                    dollar_volumes = sorted(
                        float(item.volume) * float(item.close) for item in window
                    )
                    median = dollar_volumes[len(dollar_volumes) // 2]
                    if median < universe_volume_floor:
                        universe_eligible = False
            current_close = completed[-1].close
            current_prices[symbol] = current_close
            next_close = next_completed[-1].close
            # Prices and returns keep flowing for every listed symbol so held
            # positions stay marked and reducible regardless of membership.
            next_returns[symbol] = next_close / current_close - 1.0
            if not universe_eligible:
                # Not a universe member at this boundary: no technical
                # snapshot and no assessment, so the allocation layer cannot
                # give it a target or a new entry this review.
                continue
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
            measured_tier = boundary_tiers.get(symbol)
            assessments[symbol] = _assessment(
                symbol=symbol, snapshot=snapshot, daily=asset_assessment_series,
                btc_daily=btc_assessment_series,
                as_of=as_of, policy=policy, semantic_score=semantic_score,
                evidence=evidence,
                risk_tier=(
                    measured_tier["tier"] if measured_tier is not None else "normal"
                ),
                risk_tier_source=(
                    measured_tier["source"] if measured_tier is not None else "POLICY_DEFAULT"
                ),
            ).as_dict()
            bars = []
            if execution_timeframe == "1D":
                start_index = bisect_right(execution_times[symbol], as_of_moment - timedelta(microseconds=1))
                end_index = bisect_right(execution_times[symbol], period_end - timedelta(microseconds=1))
                interval = timedelta(days=1)
            else:
                start_index = bisect_right(execution_times[symbol], as_of_moment)
                end_index = bisect_right(execution_times[symbol], period_end - timedelta(microseconds=1))
                interval = timedelta(hours=1)
            for candle in execution_cache[symbol][start_index:end_index]:
                actual_timestamp = parse_timestamp(candle.timestamp)
                execution_timestamp = actual_timestamp
                if execution_timeframe == "1D" and actual_timestamp == as_of_moment:
                    execution_timestamp = as_of_moment + timedelta(microseconds=1)
                bars.append({
                    "timestamp": execution_timestamp.isoformat().replace("+00:00", "Z"),
                    "mark_timestamp": (actual_timestamp + interval).isoformat().replace("+00:00", "Z"),
                    "open": candle.open, "high": candle.high, "low": candle.low, "close": candle.close,
                })
            if not bars:
                usable = False
                break
            execution_bars[symbol] = tuple(bars)
        if not usable:
            continue
        btc_snapshot = snapshots["BTC"][-1]
        eth_signals = (
            relative_signals(eth_ratio_points, as_of_moment) if eth_ratio_points else {}
        )
        market_flow = evidence.market_flow_state(as_of) if evidence is not None else None
        regime_inputs = build_regime_inputs(
            btc_snapshot, portfolio_drawdown=0.0, breadth=breadth_above_ma(completed_by_symbol),
            flow_facts={"state": market_flow} if market_flow is not None else None,
            systemic_event_risk=False,
            provenance_complete=semantic_score is not None or market_flow is not None,
        ).as_dict()
        reviews.append(ReplayReview(
            as_of=as_of, period_end=period_end_text,
            current_weights=dict(initial_weights), portfolio_value=float(initial_value),
            assessments=assessments, regime_inputs=regime_inputs,
            next_returns=next_returns, technical_inputs=snapshots,
            execution_bars=execution_bars, current_prices=current_prices,
            eth_alpha_state=eth_alpha_state(eth_signals) if eth_signals else None,
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


__all__ = [
    "BREADTH_MA_WINDOW",
    "apply_semantic_scenario",
    "breadth_above_ma",
    "build_historical_reviews",
    "rebind_initial_weights",
    "review_gap_diagnostics",
]
