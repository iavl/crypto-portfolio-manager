"""Portfolio risk metrics for the volatility-budget risk engine.

Pure, deterministic, standard-library-only math over daily decimal returns.
The stable/cash sleeve is outside the covariance model by construction:
inputs carry risky assets only and the sleeve contributes zero variance, so
an all-cash book has exactly zero portfolio volatility.

Every function fails closed: NaN/Infinity, non-positive prices, insufficient
history, or an exposed symbol without covariance is a clear ``ValueError``,
never a zero fill or a renormalization over the surviving assets.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field as dataclass_field
from typing import Any, Mapping, Sequence


def _finite_float(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def daily_returns(closes: Sequence[float]) -> list[float]:
    """Simple decimal returns of a strictly positive close series."""
    if len(closes) < 2:
        raise ValueError("daily returns require at least two closes")
    prices: list[float] = []
    for index, value in enumerate(closes):
        price = _finite_float(value, f"close[{index}]")
        if price <= 0:
            raise ValueError("close prices must be strictly positive")
        prices.append(price)
    return [prices[i] / prices[i - 1] - 1.0 for i in range(1, len(prices))]


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def _sample_variance(values: Sequence[float]) -> float:
    if len(values) < 2:
        raise ValueError("sample variance requires at least two observations")
    average = _mean(values)
    return sum((item - average) ** 2 for item in values) / (len(values) - 1)


def _sample_covariance(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or len(left) < 2:
        raise ValueError("sample covariance requires two equal-length series")
    left_mean, right_mean = _mean(left), _mean(right)
    return sum(
        (a - left_mean) * (b - right_mean) for a, b in zip(left, right)
    ) / (len(left) - 1)


def pearson_correlation(left: Sequence[float], right: Sequence[float]) -> float | None:
    """Sample Pearson correlation; ``None`` when either series is constant."""
    if len(left) != len(right) or len(left) < 2:
        raise ValueError("correlation requires two equal-length series")
    left_variance = _sample_variance(left)
    right_variance = _sample_variance(right)
    if left_variance <= 0.0 or right_variance <= 0.0:
        return None
    covariance = _sample_covariance(left, right)
    return covariance / math.sqrt(left_variance * right_variance)


def trailing_window(values: Sequence[float], window: int) -> Sequence[float]:
    if isinstance(window, bool) or not isinstance(window, int) or window < 2:
        raise ValueError("window must be an integer >= 2")
    if len(values) < window:
        raise ValueError(
            f"insufficient history: {len(values)} observations for a {window}-observation window"
        )
    return values[-window:]


def realized_volatility(
    returns: Sequence[float], *, annualization_days: int
) -> float:
    """Annualized realized volatility of decimal daily returns."""
    days = _annualization_days(annualization_days)
    if len(returns) < 2:
        raise ValueError("realized volatility requires at least two returns")
    for index, value in enumerate(returns):
        result = _finite_float(value, f"return[{index}]")
        if result <= -1.0:
            raise ValueError("returns must be greater than -100%")
    return math.sqrt(_sample_variance(list(returns))) * math.sqrt(days)


def _annualization_days(value: Any) -> int:
    days = value
    if isinstance(days, bool) or not isinstance(days, int) or days < 1:
        raise ValueError("annualization_days must be a positive integer")
    return days


def blended_asset_volatility(
    returns: Sequence[float],
    *,
    window_weights: Mapping[str, float],
    annualization_days: int,
) -> dict[str, float]:
    """Blend trailing realized volatilities, e.g. 40% 30D / 60% 90D.

    ``window_weights`` maps day-count labels such as ``"30d"``/``"90d"`` to
    weights summing to 1. The blend is weighted in volatility space, which
    keeps the result directly comparable to a target-volatility band.
    """
    if not window_weights:
        raise ValueError("window_weights must not be empty")
    windows: dict[int, float] = {}
    for label, weight in window_weights.items():
        text = str(label).strip().lower().rstrip("d")
        if not text.isdigit():
            raise ValueError(f"window label {label!r} must look like '30d'")
        day_count = int(text)
        if day_count < 2:
            raise ValueError("window label must describe at least two days")
        weight_value = _finite_float(weight, f"window_weights[{label}]")
        if weight_value < 0:
            raise ValueError("window weights must be non-negative")
        windows[day_count] = windows.get(day_count, 0.0) + weight_value
    if not math.isclose(sum(windows.values()), 1.0, abs_tol=1e-9):
        raise ValueError("window weights must sum to 1")
    result: dict[str, float] = {}
    blended = 0.0
    for day_count, weight in sorted(windows.items()):
        volatility = realized_volatility(
            trailing_window(list(returns), day_count), annualization_days=annualization_days
        )
        result[f"{day_count}d"] = volatility
        blended += weight * volatility
    result["blended"] = blended
    return result


def correlation_matrix(
    returns_by_symbol: Mapping[str, Sequence[float]],
    *,
    window: int,
) -> dict[str, dict[str, float | None]]:
    """Pairwise trailing-window correlations over aligned return tails.

    Each series is first trimmed to its trailing ``window`` observations
    (insufficient history is an error, not a shorter estimate), then each
    pair is aligned on its common tail so assets with different history
    lengths still compare over the observations they share.
    """
    symbols, series = _aligned_series(returns_by_symbol, window)
    trimmed = {
        symbol: list(trailing_window(list(values), window))
        for symbol, values in series.items()
    }
    matrix: dict[str, dict[str, float | None]] = {}
    for left in symbols:
        row: dict[str, float | None] = {}
        for right in symbols:
            if left == right:
                row[right] = 1.0
            else:
                overlap = min(len(trimmed[left]), len(trimmed[right]))
                row[right] = pearson_correlation(
                    trimmed[left][-overlap:], trimmed[right][-overlap:]
                )
        matrix[left] = row
    return matrix


def beta_to_btc(
    returns_by_symbol: Mapping[str, Sequence[float]],
    *,
    window: int,
    btc_symbol: str = "BTC",
) -> dict[str, float]:
    """``Cov(asset, BTC) / Var(BTC)`` over the aligned trailing window."""
    btc_symbol = str(btc_symbol).strip().upper()
    symbols, series = _aligned_series(returns_by_symbol, window)
    if btc_symbol not in symbols:
        raise ValueError(f"beta to BTC requires returns for {btc_symbol}")
    btc_tail = list(trailing_window(list(series[btc_symbol]), window))
    btc_variance = _sample_variance(btc_tail)
    if btc_variance <= 0.0:
        raise ValueError("beta is undefined when BTC returns are constant")
    result: dict[str, float] = {}
    for symbol in symbols:
        if symbol == btc_symbol:
            result[symbol] = 1.0
        else:
            asset_tail = list(trailing_window(list(series[symbol]), window))
            overlap = min(len(asset_tail), len(btc_tail))
            result[symbol] = (
                _sample_covariance(asset_tail[-overlap:], btc_tail[-overlap:]) / btc_variance
            )
    return result


def _aligned_series(
    returns_by_symbol: Mapping[str, Sequence[float]], window: int
) -> tuple[list[str], dict[str, Sequence[float]]]:
    if not returns_by_symbol:
        raise ValueError("at least one return series is required")
    symbols: list[str] = []
    normalized: dict[str, Sequence[float]] = {}
    for raw_symbol, values in returns_by_symbol.items():
        symbol = str(raw_symbol).strip().upper()
        if not symbol:
            raise ValueError("return series symbols must be non-empty")
        if symbol in normalized:
            raise ValueError(f"duplicate return series for {symbol}")
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            raise ValueError(f"returns for {symbol} must be a sequence of numbers")
        normalized[symbol] = [ _finite_float(v, f"returns[{symbol}]") for v in values ]
        symbols.append(symbol)
    return symbols, normalized


def portfolio_volatility(
    weights: Mapping[str, float],
    covariance: Mapping[str, Mapping[str, float]],
) -> float:
    """``sqrt(w' Sigma w)`` in annualized volatility units.

    ``covariance`` must be annualized and square over the risky symbols.
    A positive weight on a symbol without a covariance row is an error; a
    zero weight never requires one.
    """
    parsed = _risky_weights(weights)
    exposed = [symbol for symbol, weight in parsed.items() if weight > 0]
    if not exposed:
        return 0.0
    total = 0.0
    for left in exposed:
        row = covariance.get(left)
        if not isinstance(row, Mapping):
            raise ValueError(f"covariance is missing for exposed asset {left}")
        for right in exposed:
            value = row.get(right)
            if value is None:
                raise ValueError(f"covariance[{left}][{right}] is missing")
            entry = _finite_float(value, f"covariance[{left}][{right}]")
            total += parsed[left] * parsed[right] * entry
    if total < 0:
        # A covariance matrix that is not positive semi-definite is a broken
        # input, not a portfolio with imaginary volatility.
        raise ValueError("covariance matrix is not positive semi-definite")
    return math.sqrt(total)


def marginal_risk_contributions(
    weights: Mapping[str, Mapping[str, float] | float] | Mapping[str, float],
    covariance: Mapping[str, Mapping[str, float]],
) -> dict[str, dict[str, float]]:
    """Per-asset marginal contribution ``w_i (Sigma w)_i / sigma_p``.

    Also returns each asset's share of total portfolio variance, which sums
    to 1 up to floating error. Weights are the risky sleeve only.
    """
    parsed = _risky_weights(weights)
    exposed = {symbol: weight for symbol, weight in parsed.items() if weight > 0}
    sigma = portfolio_volatility(parsed, covariance)
    if sigma <= 0:
        if exposed:
            raise ValueError("risk contributions require positive portfolio volatility")
        return {}
    sigma_w: dict[str, float] = {}
    for left in exposed:
        row = covariance.get(left)
        if not isinstance(row, Mapping):
            raise ValueError(f"covariance is missing for exposed asset {left}")
        sigma_w[left] = sum(
            exposed[right] * _finite_float(row.get(right), f"covariance[{left}][{right}]")
            for right in exposed
        )
    result: dict[str, dict[str, float]] = {}
    for symbol, weight in sorted(exposed.items()):
        contribution = weight * sigma_w[symbol] / sigma
        result[symbol] = {
            "weight": weight,
            "marginal_risk_contribution": contribution,
            "risk_contribution_share": contribution / sigma,
        }
    share_total = sum(item["risk_contribution_share"] for item in result.values())
    if not math.isclose(share_total, 1.0, abs_tol=1e-6):
        raise ValueError("risk contribution shares must sum to 1")
    return result


def _risky_weights(value: Mapping[str, Any]) -> dict[str, float]:
    if not isinstance(value, Mapping):
        raise ValueError("weights must be an object")
    result: dict[str, float] = {}
    for raw_symbol, raw_weight in value.items():
        symbol = str(raw_symbol).strip().upper()
        if not symbol:
            raise ValueError("weights contain an empty symbol")
        if symbol in result:
            raise ValueError(f"weights contain duplicate symbol {symbol}")
        weight = _finite_float(raw_weight, f"weights[{symbol}]")
        if weight < 0:
            raise ValueError(f"weights[{symbol}] must be non-negative")
        result[symbol] = weight
    return result


def regime_target_volatility_multiplier(
    regime: str,
    scaling: Mapping[str, Mapping[str, float]],
) -> float:
    """Configured target-volatility multiplier for one regime label.

    ``scaling`` is ``risk_engine.regime_risk_scaling`` as resolved by the
    policy model: every regime label must be present, so an unknown label is
    an error rather than a silent 1.0.
    """
    name = str(regime).strip().upper()
    entry = scaling.get(name) if isinstance(scaling, Mapping) else None
    if not isinstance(entry, Mapping):
        raise ValueError(f"regime_risk_scaling has no entry for regime {name}")
    multiplier = _finite_float(
        entry.get("target_volatility_multiplier"),
        f"regime_risk_scaling.{name}.target_volatility_multiplier",
    )
    if not 0 < multiplier <= 1:
        raise ValueError(
            f"regime_risk_scaling.{name}.target_volatility_multiplier must be in (0, 1]"
        )
    return multiplier


def effective_target_volatility(
    base_target_volatility: float,
    regime_volatility_multiplier: float,
) -> float:
    """``base_target_volatility x regime_volatility_multiplier``.

    The regime scales the target-volatility band instead of the stable sleeve:
    NORMAL keeps the full band, defensive regimes shrink it. The result can
    never exceed the configured base target, so the max-volatility ceiling
    stays intact without re-validation here.
    """
    base = _finite_float(base_target_volatility, "base_target_volatility")
    multiplier = _finite_float(regime_volatility_multiplier, "regime_volatility_multiplier")
    if base <= 0 or not 0 < multiplier <= 1:
        raise ValueError(
            "base_target_volatility must be positive and the multiplier in (0, 1]"
        )
    return base * multiplier


def volatility_budget_scale(
    portfolio_volatility_value: float,
    *,
    target_volatility: float,
    max_volatility: float,
) -> dict[str, Any]:
    """Scale factor ``min(1, target / vol)`` with a hard max-vol flag.

    A zero-volatility (all-cash) book consumes none of the budget, so the
    scale is exactly 1. The max band never shrinks further on its own; it
    exists to flag that even the target band was exceeded before scaling,
    which callers must surface rather than absorb.
    """
    vol = _finite_float(portfolio_volatility_value, "portfolio_volatility")
    target = _finite_float(target_volatility, "target_volatility")
    maximum = _finite_float(max_volatility, "max_volatility")
    if vol < 0 or target <= 0 or maximum <= 0:
        raise ValueError("volatilities must be positive (vol may be zero)")
    if target > maximum:
        raise ValueError("target_volatility must not exceed max_volatility")
    if vol <= 0:
        return {
            "risk_scaling_factor": 1.0,
            "binding": "none",
            "max_volatility_exceeded": False,
        }
    scale = min(1.0, target / vol)
    return {
        "risk_scaling_factor": scale,
        "binding": "volatility_budget" if scale < 1.0 else "none",
        "max_volatility_exceeded": vol > maximum,
    }


def emergency_drawdown_state(
    portfolio_drawdown: float,
    *,
    budget: float,
    caution_fraction: float,
    emergency_fraction: float,
    breach_fraction: float,
    caution_risky_cap: float,
    emergency_risky_cap: float,
    breach_risky_cap: float,
) -> dict[str, Any]:
    """Staged emergency brake: NORMAL / CAUTION / EMERGENCY / BREACH.

    ``consumed = |drawdown| / budget`` decides the stage; each stage owns a
    maximum risky weight. Only the strict breach (``drawdown < -budget``) is
    a hard failure signal; the stages below it are the emergency ladder that
    replaces the legacy continuous ``1 - consumed`` cap.
    """
    drawdown = _finite_float(portfolio_drawdown, "portfolio_drawdown")
    if drawdown > 0:
        raise ValueError("portfolio_drawdown must be <= 0")
    budget_value = _finite_float(budget, "budget")
    if budget_value <= 0 or budget_value > 1:
        raise ValueError("budget must be a fraction in (0, 1]")
    fractions = (
        _finite_float(caution_fraction, "caution_fraction"),
        _finite_float(emergency_fraction, "emergency_fraction"),
        _finite_float(breach_fraction, "breach_fraction"),
    )
    caps = (
        _finite_float(caution_risky_cap, "caution_risky_cap"),
        _finite_float(emergency_risky_cap, "emergency_risky_cap"),
        _finite_float(breach_risky_cap, "breach_risky_cap"),
    )
    if not 0 < fractions[0] < fractions[1] < fractions[2] <= 1:
        raise ValueError("emergency fractions must satisfy 0 < caution < emergency < breach <= 1")
    if not 1 >= caps[0] > caps[1] > caps[2] >= 0:
        raise ValueError("emergency risky caps must satisfy caution > emergency > breach")
    consumed = max(0.0, -drawdown / budget_value)
    if drawdown < -budget_value:
        state, cap = "BREACH", caps[2]
    elif consumed >= fractions[1]:
        state, cap = "EMERGENCY", caps[1]
    elif consumed >= fractions[0]:
        state, cap = "CAUTION", caps[0]
    else:
        state, cap = "NORMAL", 1.0
    return {
        "state": state,
        "budget_consumed": consumed,
        "risky_cap": cap,
    }


def combined_risk_cap(candidates: Mapping[str, float | None]) -> dict[str, Any]:
    """Tightest cap wins; caps never multiply.

    ``None`` entries are ignored (that constraint is unavailable), and an
    empty set of candidates leaves the risky sleeve unbounded by this gate.
    """
    if not isinstance(candidates, Mapping):
        raise ValueError("risk cap candidates must be an object")
    binding: tuple[str, float] | None = None
    for raw_name, raw_value in candidates.items():
        name = str(raw_name).strip()
        if not name:
            raise ValueError("risk cap candidate names must be non-empty")
        if raw_value is None:
            continue
        value = _finite_float(raw_value, f"risk_cap[{name}]")
        if value < 0 or value > 1:
            raise ValueError(f"risk cap {name} must be in [0, 1]")
        if binding is None or value < binding[1]:
            binding = (name, value)
    if binding is None:
        return {"cap": 1.0, "binding_constraint": "none", "candidates": {}}
    return {
        "cap": binding[1],
        "binding_constraint": binding[0],
        "candidates": {
            str(name): value for name, value in candidates.items() if value is not None
        },
    }


@dataclass(frozen=True)
class PortfolioRiskInputs:
    """Validated per-symbol annualized volatility and correlation estimates.

    Built from aligned daily returns; consumed by the volatility-budget
    allocation mode. Correlation entries may be ``None`` only on the
    diagonal-free upper/lower pairs where a constant series made the sample
    correlation undefined — such a pair is treated as zero covariance by
    ``covariance()`` and never as fabricated co-movement.
    """

    asset_volatility: Mapping[str, float]
    correlations: Mapping[str, Mapping[str, float | None]] = dataclass_field(default_factory=dict)
    annualization_days: int = 365
    window_weights: Mapping[str, float] = dataclass_field(default_factory=lambda: {"30d": 0.4, "90d": 0.6})
    correlation_window_days: int = 90

    def __post_init__(self) -> None:
        if not self.asset_volatility:
            raise ValueError("asset_volatility must not be empty")
        vols: dict[str, float] = {}
        for raw_symbol, raw_value in self.asset_volatility.items():
            symbol = str(raw_symbol).strip().upper()
            if not symbol:
                raise ValueError("asset_volatility contains an empty symbol")
            if symbol in vols:
                raise ValueError(f"asset_volatility contains duplicate symbol {symbol}")
            value = _finite_float(raw_value, f"asset_volatility[{symbol}]")
            if value <= 0:
                raise ValueError(f"asset_volatility[{symbol}] must be positive")
            vols[symbol] = value
        object.__setattr__(self, "asset_volatility", vols)
        correlations: dict[str, dict[str, float | None]] = {}
        for raw_symbol, row in (self.correlations or {}).items():
            symbol = str(raw_symbol).strip().upper()
            if symbol not in vols:
                raise ValueError(f"correlations reference unknown asset {symbol}")
            parsed: dict[str, float | None] = {}
            for raw_other, raw_value in (row or {}).items():
                other = str(raw_other).strip().upper()
                if other not in vols:
                    raise ValueError(f"correlations reference unknown asset {other}")
                if raw_value is None:
                    if other == symbol:
                        raise ValueError("self-correlation must be 1.0, not None")
                    parsed[other] = None
                else:
                    value = _finite_float(raw_value, f"correlations[{symbol}][{other}]")
                    if not -1 <= value <= 1:
                        raise ValueError(f"correlations[{symbol}][{other}] must be in [-1, 1]")
                    if other == symbol and not math.isclose(value, 1.0, abs_tol=1e-9):
                        raise ValueError("self-correlation must be 1.0")
                    parsed[other] = value
            correlations[symbol] = parsed
        for symbol in vols:
            for other in vols:
                if other == symbol:
                    continue
                left = correlations.get(symbol, {}).get(other)
                right = correlations.get(other, {}).get(symbol)
                if (left is None) != (right is None):
                    raise ValueError(
                        f"correlations[{symbol}][{other}] and its mirror disagree on availability"
                    )
                if left is not None and right is not None and not math.isclose(left, right, abs_tol=1e-9):
                    raise ValueError(f"correlations[{symbol}][{other}] is not symmetric")
        object.__setattr__(self, "correlations", correlations)
        object.__setattr__(self, "annualization_days", _annualization_days(self.annualization_days))
        window_days = self.correlation_window_days
        if isinstance(window_days, bool) or not isinstance(window_days, int) or window_days < 2:
            raise ValueError("correlation_window_days must be an integer >= 2")
        object.__setattr__(self, "correlation_window_days", window_days)

    def covariance(self) -> dict[str, dict[str, float]]:
        """Annualized covariance matrix implied by vols and correlations."""
        symbols = sorted(self.asset_volatility)
        matrix: dict[str, dict[str, float]] = {}
        for left in symbols:
            row: dict[str, float] = {}
            for right in symbols:
                if left == right:
                    vol = self.asset_volatility[left]
                    row[right] = vol * vol
                else:
                    correlation = (self.correlations.get(left) or {}).get(right)
                    # An undefined sample correlation decays to zero sample
                    # covariance, which is what a constant series implies.
                    row[right] = 0.0 if correlation is None else (
                        correlation * self.asset_volatility[left] * self.asset_volatility[right]
                    )
            matrix[left] = row
        return matrix

    def as_dict(self) -> dict[str, Any]:
        return {
            "asset_volatility": dict(self.asset_volatility),
            "correlations": {
                symbol: dict(row) for symbol, row in self.correlations.items()
            },
            "annualization_days": self.annualization_days,
            "window_weights": dict(self.window_weights),
            "correlation_window_days": self.correlation_window_days,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "PortfolioRiskInputs":
        if not isinstance(value, Mapping):
            raise ValueError("portfolio risk inputs must be an object")
        return cls(
            asset_volatility=value.get("asset_volatility") or {},
            correlations=value.get("correlations") or {},
            annualization_days=value.get("annualization_days", 365),
            window_weights=value.get("window_weights") or {"30d": 0.4, "90d": 0.6},
            correlation_window_days=value.get("correlation_window_days", 90),
        )

    @classmethod
    def from_daily_returns(
        cls,
        returns_by_symbol: Mapping[str, Sequence[float]],
        *,
        window_weights: Mapping[str, float],
        correlation_window_days: int,
        annualization_days: int,
    ) -> "PortfolioRiskInputs":
        symbols, series = _aligned_series(returns_by_symbol, 2)
        vols: dict[str, float] = {}
        for symbol in symbols:
            profile = blended_asset_volatility(
                series[symbol],
                window_weights=window_weights,
                annualization_days=annualization_days,
            )
            vols[symbol] = profile["blended"]
        correlations = correlation_matrix(series, window=correlation_window_days)
        return cls(
            asset_volatility=vols,
            correlations=correlations,
            annualization_days=annualization_days,
            window_weights=dict(window_weights),
            correlation_window_days=correlation_window_days,
        )


def build_portfolio_risk_inputs_from_closes(
    closes_by_symbol: Mapping[str, Sequence[float]],
    *,
    window_weights: Mapping[str, float],
    correlation_window_days: int,
    annualization_days: int,
    minimum_history_days: int,
) -> PortfolioRiskInputs:
    """Point-in-time constructor from trailing daily closes.

    Symbols with fewer than ``minimum_history_days`` closes are dropped from
    the inputs entirely rather than partially estimated; allocating weight to
    a dropped symbol under the volatility budget then fails closed with the
    missing-covariance error, which is the contract.
    """
    if isinstance(minimum_history_days, bool) or not isinstance(minimum_history_days, int):
        raise ValueError("minimum_history_days must be an integer")
    if minimum_history_days < 2:
        raise ValueError("minimum_history_days must be at least 2")
    usable: dict[str, list[float]] = {}
    for raw_symbol, closes in closes_by_symbol.items():
        symbol = str(raw_symbol).strip().upper()
        if not symbol:
            raise ValueError("closes_by_symbol contains an empty symbol")
        if not isinstance(closes, Sequence) or isinstance(closes, (str, bytes)):
            raise ValueError(f"closes for {symbol} must be a sequence of numbers")
        if len(closes) < minimum_history_days:
            continue
        usable[symbol] = daily_returns(list(closes))
    if "BTC" not in usable:
        raise ValueError(
            f"portfolio risk inputs require at least {minimum_history_days} BTC closes"
        )
    return PortfolioRiskInputs.from_daily_returns(
        usable,
        window_weights=window_weights,
        correlation_window_days=correlation_window_days,
        annualization_days=annualization_days,
    )


__all__ = [
    "PortfolioRiskInputs",
    "beta_to_btc",
    "blended_asset_volatility",
    "build_portfolio_risk_inputs_from_closes",
    "combined_risk_cap",
    "correlation_matrix",
    "daily_returns",
    "effective_target_volatility",
    "emergency_drawdown_state",
    "marginal_risk_contributions",
    "pearson_correlation",
    "portfolio_volatility",
    "realized_volatility",
    "regime_target_volatility_multiplier",
    "volatility_budget_scale",
]
