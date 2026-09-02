"""Canonical decision-relevant metric definitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_EXPECTED_TYPES = {"number", "string"}
_DIRECTIONS = {"HIGHER_IS_BETTER", "LOWER_IS_BETTER", "CONTEXTUAL"}
_FRESHNESS = {"CURRENT", "STALE", "UNKNOWN"}


@dataclass(frozen=True)
class MetricDefinition:
    key: str
    factor: str
    expected_type: str
    unit: str | None = None
    direction: str = "CONTEXTUAL"
    default_freshness: str = "CURRENT"
    critical: bool = False
    trend_comparison_enabled: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not self.key.strip():
            raise ValueError("metric definition key must be a non-empty string")
        if not isinstance(self.factor, str) or not self.factor.strip():
            raise ValueError("metric definition factor must be a non-empty string")
        if self.expected_type not in _EXPECTED_TYPES:
            raise ValueError("metric definition expected_type is unsupported")
        if self.unit is not None and (not isinstance(self.unit, str) or not self.unit.strip()):
            raise ValueError("metric definition unit must be a non-empty string or null")
        if self.direction not in _DIRECTIONS:
            raise ValueError("metric definition direction is unsupported")
        if self.default_freshness not in _FRESHNESS:
            raise ValueError("metric definition default_freshness is unsupported")
        if not isinstance(self.critical, bool):
            raise ValueError("metric definition critical must be boolean")
        if not isinstance(self.trend_comparison_enabled, bool):
            raise ValueError("metric definition trend_comparison_enabled must be boolean")

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric_key": self.key,
            "factor": self.factor,
            "expected_type": self.expected_type,
            "unit": self.unit,
            "direction": self.direction,
            "default_freshness": self.default_freshness,
            "critical": self.critical,
            "trend_comparison_enabled": self.trend_comparison_enabled,
        }


def _definition(
    key: str,
    factor: str,
    expected_type: str,
    unit: str | None = None,
    direction: str = "CONTEXTUAL",
    *,
    critical: bool = False,
) -> MetricDefinition:
    return MetricDefinition(
        key,
        factor,
        expected_type,
        unit,
        direction,
        critical=critical,
    )


METRIC_REGISTRY: dict[str, MetricDefinition] = {
    "market.spot_price": _definition("market.spot_price", "trend", "number", "USD", "CONTEXTUAL", critical=True),
    "market.return_30d": _definition("market.return_30d", "trend", "number", "fraction", "HIGHER_IS_BETTER"),
    "market.return_90d": _definition("market.return_90d", "trend", "number", "fraction", "HIGHER_IS_BETTER"),
    "market.return_180d": _definition("market.return_180d", "trend", "number", "fraction", "HIGHER_IS_BETTER"),
    "market.ma20": _definition("market.ma20", "trend", "number", "USD", "CONTEXTUAL"),
    "market.ma50": _definition("market.ma50", "trend", "number", "USD", "CONTEXTUAL"),
    "market.ma100": _definition("market.ma100", "trend", "number", "USD", "CONTEXTUAL"),
    "market.ma200": _definition("market.ma200", "trend", "number", "USD", "CONTEXTUAL"),
    "market.atr14": _definition("market.atr14", "trend", "number", "USD", "CONTEXTUAL"),
    "market.realized_vol_30d": _definition("market.realized_vol_30d", "trend", "number", "fraction", "LOWER_IS_BETTER"),
    "market.realized_vol_90d": _definition("market.realized_vol_90d", "trend", "number", "fraction", "LOWER_IS_BETTER"),
    "market.relative_volume": _definition("market.relative_volume", "trend", "number", "ratio", "CONTEXTUAL"),
    "market.drawdown": _definition("market.drawdown", "valuation", "number", "fraction", "HIGHER_IS_BETTER"),
    "market.btc_dominance": _definition("market.btc_dominance", "relative_strength_btc", "number", "fraction", "CONTEXTUAL"),
    "market.total_crypto_market_cap": _definition("market.total_crypto_market_cap", "valuation", "number", "USD", "CONTEXTUAL"),
    "market.stablecoin_supply": _definition("market.stablecoin_supply", "capital_flows", "number", "USD", "HIGHER_IS_BETTER"),
    "flows.etf_net_1d": _definition("flows.etf_net_1d", "capital_flows", "number", "USD", "HIGHER_IS_BETTER"),
    "flows.etf_net_7d": _definition("flows.etf_net_7d", "capital_flows", "number", "USD", "HIGHER_IS_BETTER"),
    "flows.exchange_netflow": _definition("flows.exchange_netflow", "capital_flows", "number", "USD", "CONTEXTUAL"),
    "fundamentals.tvl": _definition("fundamentals.tvl", "fundamentals", "number", "USD", "HIGHER_IS_BETTER"),
    "fundamentals.fees_30d": _definition("fundamentals.fees_30d", "fundamentals", "number", "USD", "HIGHER_IS_BETTER"),
    "fundamentals.revenue_30d": _definition("fundamentals.revenue_30d", "fundamentals", "number", "USD", "HIGHER_IS_BETTER"),
    "fundamentals.stablecoin_liquidity": _definition("fundamentals.stablecoin_liquidity", "fundamentals", "number", "USD", "HIGHER_IS_BETTER"),
    "fundamentals.active_users": _definition("fundamentals.active_users", "fundamentals", "number", "count", "HIGHER_IS_BETTER"),
    "onchain.active_addresses": _definition("onchain.active_addresses", "onchain", "number", "count", "HIGHER_IS_BETTER"),
    "onchain.transfer_volume": _definition("onchain.transfer_volume", "onchain", "number", "USD", "HIGHER_IS_BETTER"),
    "onchain.blockspace_fees": _definition("onchain.blockspace_fees", "onchain", "number", "USD", "HIGHER_IS_BETTER"),
    "relative.return_vs_btc_30d": _definition("relative.return_vs_btc_30d", "relative_strength_btc", "number", "fraction", "HIGHER_IS_BETTER"),
    "relative.return_vs_btc_90d": _definition("relative.return_vs_btc_90d", "relative_strength_btc", "number", "fraction", "HIGHER_IS_BETTER"),
    "relative.return_vs_btc_180d": _definition("relative.return_vs_btc_180d", "relative_strength_btc", "number", "fraction", "HIGHER_IS_BETTER"),
    "tokenomics.next_unlock_pct": _definition("tokenomics.next_unlock_pct", "event_risk", "number", "fraction", "LOWER_IS_BETTER"),
    "tokenomics.annualized_emissions": _definition("tokenomics.annualized_emissions", "event_risk", "number", "fraction", "LOWER_IS_BETTER"),
    "risk.security_event_status": _definition("risk.security_event_status", "event_risk", "string", None, "CONTEXTUAL", critical=True),
    "risk.chain_liveness_status": _definition("risk.chain_liveness_status", "event_risk", "string", None, "CONTEXTUAL", critical=True),
}

METRICS = METRIC_REGISTRY


def normalize_metric_key(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("metric_key must be a non-empty string")
    return value.strip().lower()


def metric_definition(metric_key: str) -> MetricDefinition:
    key = normalize_metric_key(metric_key)
    try:
        return METRIC_REGISTRY[key]
    except KeyError as exc:
        raise ValueError(f"unknown metric key: {key}") from exc


get_metric_definition = metric_definition
get_metric = metric_definition


def known_metric_keys() -> tuple[str, ...]:
    return tuple(METRIC_REGISTRY)


__all__ = [
    "METRIC_REGISTRY",
    "METRICS",
    "MetricDefinition",
    "get_metric_definition",
    "get_metric",
    "known_metric_keys",
    "metric_definition",
    "normalize_metric_key",
]
