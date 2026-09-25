"""Forward-label diagnostics for reconstructable historical scores."""

from __future__ import annotations

import math
from datetime import timedelta
from typing import Any, Iterable, Mapping, Sequence

from ..models.time import parse_timestamp


def _ranks(values: Sequence[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    result = [0.0] * len(values)
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        rank = (index + 1 + end) / 2.0
        for original, _ in ordered[index:end]:
            result[original] = rank
        index = end
    return result


def _correlation(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 3 or len(set(left)) < 2 or len(set(right)) < 2:
        return None
    mean_left, mean_right = sum(left) / len(left), sum(right) / len(right)
    numerator = sum((a - mean_left) * (b - mean_right) for a, b in zip(left, right))
    denominator = math.sqrt(sum((a - mean_left) ** 2 for a in left) * sum((b - mean_right) ** 2 for b in right))
    return numerator / denominator if denominator > 0 else None


def _price_history(value: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, tuple[tuple[Any, float], ...]]:
    result = {}
    for raw_symbol, rows in value.items():
        parsed = tuple(sorted(
            ((parse_timestamp(row["timestamp"]), float(row["price"])) for row in rows),
            key=lambda item: item[0],
        ))
        if any(price <= 0 for _, price in parsed):
            raise ValueError("score evaluation prices must be positive")
        result[str(raw_symbol).strip().upper()] = parsed
    return result


def _first_at_or_after(rows: Sequence[tuple[Any, float]], moment: Any) -> tuple[Any, float] | None:
    return next((item for item in rows if item[0] >= moment), None)


def _coverage_band(value: float) -> str:
    return "HIGH" if value >= 0.8 else "MEDIUM" if value >= 0.5 else "LOW"


def evaluate_scores(
    observations: Iterable[Mapping[str, Any]],
    prices: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    horizons: Sequence[int] = (30, 90, 180),
) -> dict[str, Any]:
    history = _price_history(prices)
    rows: list[dict[str, Any]] = []
    for raw in observations:
        allowed = {"timestamp", "symbol", "score", "normalized_score", "coverage", "factor_scores", "synthetic"}
        if not isinstance(raw, Mapping) or set(raw) - allowed:
            raise ValueError("score observation contains unsupported fields")
        if raw.get("synthetic") is True:
            # Synthetic semantic scores are mechanism experiments, never
            # evidence of predictive score quality.
            continue
        moment = parse_timestamp(raw["timestamp"])
        symbol = str(raw["symbol"]).strip().upper()
        score = float(raw["score"])
        coverage = float(raw["coverage"])
        if not 0 <= score <= 100 or not 0 <= coverage <= 1:
            raise ValueError("score must be in [0,100] and coverage in [0,1]")
        base = _first_at_or_after(history.get(symbol, ()), moment)
        btc_base = _first_at_or_after(history.get("BTC", ()), moment)
        result = {
            "timestamp": raw["timestamp"], "symbol": symbol, "score": score,
            "normalized_score": raw.get("normalized_score"),
            "factor_scores": dict(raw.get("factor_scores", {})), "coverage": coverage,
            "coverage_band": _coverage_band(coverage), "decile": min(9, int(score // 10)),
            "labels": {},
        }
        for raw_horizon in horizons:
            horizon = int(raw_horizon)
            target = moment + timedelta(days=horizon)
            endpoint = _first_at_or_after(history.get(symbol, ()), target)
            btc_endpoint = _first_at_or_after(history.get("BTC", ()), target)
            if base is None or endpoint is None:
                result["labels"][str(horizon)] = {"status": "PENDING"}
                continue
            forward = endpoint[1] / base[1] - 1.0
            relative = None
            if btc_base is not None and btc_endpoint is not None:
                relative = forward - (btc_endpoint[1] / btc_base[1] - 1.0)
            path = [price / base[1] - 1.0 for at, price in history[symbol] if base[0] <= at <= endpoint[0]]
            result["labels"][str(horizon)] = {
                "status": "AVAILABLE", "forward_return": forward,
                "relative_return_vs_btc": relative,
                "maximum_adverse_excursion": min(path) if path else None,
            }
        rows.append(result)

    summaries: dict[str, Any] = {}
    for raw_horizon in horizons:
        horizon = int(raw_horizon)
        available = [row for row in rows if row["labels"][str(horizon)]["status"] == "AVAILABLE"]
        scores = [row["score"] for row in available]
        returns = [row["labels"][str(horizon)]["forward_return"] for row in available]
        relative_pairs = [(row["score"], row["labels"][str(horizon)]["relative_return_vs_btc"])
                          for row in available if row["labels"][str(horizon)]["relative_return_vs_btc"] is not None]
        last_by_symbol: dict[str, Any] = {}
        non_overlapping = []
        for row in sorted(available, key=lambda item: parse_timestamp(item["timestamp"])):
            moment = parse_timestamp(row["timestamp"])
            prior = last_by_symbol.get(row["symbol"])
            if prior is None or moment >= prior + timedelta(days=horizon):
                non_overlapping.append(row)
                last_by_symbol[row["symbol"]] = moment
        deciles = {}
        for decile in range(10):
            selected = [row["labels"][str(horizon)]["forward_return"] for row in available if row["decile"] == decile]
            if selected:
                deciles[str(decile)] = {"samples": len(selected), "mean_forward_return": sum(selected) / len(selected)}
        summaries[str(horizon)] = {
            "available_samples": len(available), "pending_samples": len(rows) - len(available),
            "non_overlapping_samples": len(non_overlapping),
            "score_forward_spearman": _correlation(_ranks(scores), _ranks(returns)) if scores else None,
            "score_relative_spearman": (
                _correlation(_ranks([item[0] for item in relative_pairs]), _ranks([item[1] for item in relative_pairs]))
                if relative_pairs else None
            ),
            "deciles": deciles,
            "status": "AVAILABLE" if len(non_overlapping) >= 10 else "INSUFFICIENT_EVIDENCE",
        }
    return {"synthetic_scores_excluded": True, "rows": rows, "horizons": summaries}


__all__ = ["evaluate_scores"]
