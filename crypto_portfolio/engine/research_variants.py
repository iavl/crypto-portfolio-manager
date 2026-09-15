"""Fixed research candidates; never imported by production allocation or scoring."""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import Any

from ..models.evidence import AssetAssessment, FactorScore
from ..models.time import parse_timestamp
from .factors.trend import calculate_trend_factor
from .scoring import score_assessment


class ResearchSignals:
    def __init__(self, variant: str, policy: Any):
        if variant not in {"baseline", "volume_mean_3", "confirm_2"}:
            raise ValueError("unknown research variant")
        self.variant = variant
        self.policy = policy
        self.previous: dict[str, tuple[Any, str]] = {}

    def snapshots(self, view: Any, symbol: str, count: int):
        values = view.technical_inputs.get(symbol, ())
        if len(values) < count:
            raise ValueError(f"MISSING_TECHNICAL_INPUTS: {symbol} needs {count} completed snapshots")
        chosen = list(values)[-count:]
        dates = []
        for value in chosen:
            tail = (value.get("ohlcv_metadata") or {}).get("latest_candle_timestamp")
            if tail is None:
                raise ValueError("MISSING_COMPLETED_CANDLE_BOUNDARY")
            moment = parse_timestamp(tail)
            if moment != moment.replace(hour=0, minute=0, second=0, microsecond=0):
                raise ValueError("INVALID_COMPLETED_CANDLE_BOUNDARY")
            if moment + timedelta(days=1) > parse_timestamp(value["as_of"]) or parse_timestamp(value["as_of"]) > parse_timestamp(view.as_of):
                raise ValueError("FUTURE_TECHNICAL_INPUT")
            dates.append(moment)
        if any(b - a != timedelta(days=1) for a, b in zip(dates, dates[1:])):
            raise ValueError("TECHNICAL_INPUTS_NOT_CONSECUTIVE")
        return chosen, dates[-1]

    def assessments(self, view: Any):
        if self.variant != "volume_mean_3":
            return view.assessments
        result = {}
        for symbol, raw in view.assessments.items():
            snapshots, _ = self.snapshots(view, symbol, 3)
            calculated = [calculate_trend_factor(s, policy=self.policy) for s in snapshots]
            asset = AssetAssessment.from_mapping(symbol, raw)
            trend = asset.factor_scores.get("trend")
            if trend is None or trend.score is None or abs(trend.score - calculated[-1].score) > 1e-9:
                raise ValueError(f"FROZEN_TREND_NOT_REPRODUCIBLE: {symbol}")
            last = calculated[-1]
            unbounded = last.score - last.contributions["clipping"]
            smoothed = min(100.0, max(0.0, unbounded - last.contributions["volume"]
                                     + sum(c.contributions["volume"] for c in calculated) / 3))
            factors = dict(asset.factor_scores)
            factors["trend"] = FactorScore("trend", smoothed, trend.evidence_ids,
                                            trend.availability, trend.reliability,
                                            trend.freshness, trend.source_quality, trend.redundancy)
            result[symbol] = score_assessment(replace(asset, factor_scores=factors), policy=self.policy)[0]
        return result

    def confirmed(self, view: Any, actions: Any):
        if self.variant != "confirm_2":
            return list(actions)
        allowed = []
        active = set()
        for action in actions:
            if action.symbol in self.policy.stable_symbols or action.action not in {"INCREASE", "REDUCE", "EXIT"}:
                continue
            active.add(action.symbol)
            if action.action_reason in self.policy.rebalance["staging"]["bypass_reasons"]:
                allowed.append(action)
                self.previous.pop(action.symbol, None)
                continue
            _, day = self.snapshots(view, action.symbol, 1)
            previous = self.previous.get(action.symbol)
            if previous is not None and previous[0] > day:
                raise ValueError("RESEARCH_CANDLE_TIME_REGRESSION")
            if previous and day - previous[0] == timedelta(days=1) and previous[1] == action.action:
                allowed.append(action)
            if previous is None or day > previous[0]:
                self.previous[action.symbol] = (day, action.action)
        for symbol in set(self.previous) - active:
            self.previous.pop(symbol)
        return allowed
