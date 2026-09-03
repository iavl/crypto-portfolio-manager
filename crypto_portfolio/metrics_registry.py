"""Canonical decision-relevant metric definitions."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any


_EXPECTED_TYPES = {"number", "string"}
_DIRECTIONS = {"HIGHER_IS_BETTER", "LOWER_IS_BETTER", "CONTEXTUAL"}
_DECISION_ROLES = {
    "SCORING_FACTOR",
    "POSITIONING_OVERLAY",
    "CYCLE_CONTEXT",
    "EXECUTION_CONTEXT",
}
DECISION_ROLES = tuple(sorted(_DECISION_ROLES))
_FRESHNESS = {"CURRENT", "STALE", "UNKNOWN"}
_FRESHNESS_WINDOW = re.compile(r"^[1-9][0-9]*d$")
_PROTOCOL_ASSETS = ("BTC", "ETH", "SOL", "BNB", "LINK", "AAVE")
_APPLICATION_ASSETS = ("ETH", "SOL", "BNB", "LINK", "AAVE")
_DERIVATIVES_ASSETS = _PROTOCOL_ASSETS


def _freshness(value: Any, field: str) -> str | int | float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a freshness state or day window")
    if isinstance(value, (int, float)):
        if value <= 0 or value != value or value in (float("inf"), float("-inf")):
            raise ValueError(f"{field} must be a positive finite day window")
        return value
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a freshness state or day window")
    result = value.strip().upper()
    if result not in _FRESHNESS and not _FRESHNESS_WINDOW.fullmatch(result.lower()):
        raise ValueError(f"{field} must be CURRENT, STALE, UNKNOWN, or Nd")
    return result.lower() if result.lower().endswith("d") else result


def _scope(value: Any) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, str) or not isinstance(value, (tuple, list)):
        raise ValueError("metric definition asset_scope must be a sequence of symbols or null")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError("metric definition asset_scope must contain strings")
    result = tuple(item.strip().upper() for item in value)
    if any(not item for item in result) or len(result) != len(set(result)):
        raise ValueError("metric definition asset_scope must contain unique non-empty symbols")
    return result


@dataclass(frozen=True)
class MetricDefinition:
    key: str
    factor: str
    expected_type: str | None = None
    unit: str | None = None
    direction: str = "CONTEXTUAL"
    default_freshness: str | int | float = "CURRENT"
    critical: bool = False
    trend_comparison_enabled: bool = True
    asset_scope: tuple[str, ...] | None = None
    value_type: str | None = None
    freshness: str | int | float | None = None
    trend_enabled: bool | None = None
    decision_role: str = "SCORING_FACTOR"
    context_group: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not self.key.strip():
            raise ValueError("metric definition key must be a non-empty string")
        object.__setattr__(self, "key", self.key.strip().lower())
        if not isinstance(self.factor, str) or not self.factor.strip():
            raise ValueError("metric definition factor must be a non-empty string")
        object.__setattr__(self, "factor", self.factor.strip().lower())
        expected_type = self.expected_type if self.value_type is None else self.value_type
        expected_type = expected_type.strip().lower() if isinstance(expected_type, str) else expected_type
        supplied_expected = self.expected_type.strip().lower() if isinstance(self.expected_type, str) else self.expected_type
        supplied_value = self.value_type.strip().lower() if isinstance(self.value_type, str) else self.value_type
        if self.expected_type is not None and self.value_type is not None and supplied_expected != supplied_value:
            raise ValueError("metric definition expected_type and value_type disagree")
        if expected_type not in _EXPECTED_TYPES:
            raise ValueError("metric definition expected_type is unsupported")
        object.__setattr__(self, "expected_type", expected_type)
        object.__setattr__(self, "value_type", expected_type)
        if self.unit is not None and (not isinstance(self.unit, str) or not self.unit.strip()):
            raise ValueError("metric definition unit must be a non-empty string or null")
        direction = str(self.direction).strip().upper()
        if direction not in _DIRECTIONS:
            raise ValueError("metric definition direction is unsupported")
        object.__setattr__(self, "direction", direction)
        freshness = self.default_freshness if self.freshness is None else self.freshness
        freshness = _freshness(freshness, "metric definition freshness")
        object.__setattr__(self, "default_freshness", freshness)
        object.__setattr__(self, "freshness", freshness)
        if not isinstance(self.critical, bool):
            raise ValueError("metric definition critical must be boolean")
        trend_enabled = self.trend_comparison_enabled if self.trend_enabled is None else self.trend_enabled
        if (
            self.trend_enabled is not None
            and self.trend_comparison_enabled is not True
            and self.trend_comparison_enabled != self.trend_enabled
        ):
            raise ValueError("metric definition trend_comparison_enabled and trend_enabled disagree")
        if not isinstance(trend_enabled, bool):
            raise ValueError("metric definition trend_comparison_enabled must be boolean")
        object.__setattr__(self, "trend_comparison_enabled", trend_enabled)
        object.__setattr__(self, "trend_enabled", trend_enabled)
        object.__setattr__(self, "asset_scope", _scope(self.asset_scope))
        role = str(self.decision_role).strip().upper()
        if role not in _DECISION_ROLES:
            raise ValueError("metric definition decision_role is unsupported")
        context_group = self.context_group
        if context_group is not None:
            if not isinstance(context_group, str) or not context_group.strip():
                raise ValueError("metric definition context_group must be a non-empty string or null")
            context_group = context_group.strip().lower()
        if role != "SCORING_FACTOR" and context_group is None:
            raise ValueError("overlay metric definitions require context_group")
        if role == "SCORING_FACTOR" and context_group is not None:
            raise ValueError("scoring metric definitions must not set context_group")
        object.__setattr__(self, "decision_role", role)
        object.__setattr__(self, "context_group", context_group)

    def applies_to(self, asset: str) -> bool:
        """Return whether this definition explicitly covers ``asset``."""
        if not isinstance(asset, str) or not asset.strip():
            raise ValueError("asset must be a non-empty string")
        return self.asset_scope is None or asset.strip().upper() in self.asset_scope

    @property
    def is_scoring_factor(self) -> bool:
        return self.decision_role == "SCORING_FACTOR"

    @property
    def is_overlay(self) -> bool:
        return not self.is_scoring_factor

    @property
    def freshness_days(self) -> int | float | None:
        if isinstance(self.freshness, str) and self.freshness.endswith("d"):
            return int(self.freshness[:-1])
        return self.freshness if isinstance(self.freshness, (int, float)) else None

    @property
    def applicable_assets(self) -> tuple[str, ...] | None:
        return self.asset_scope

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric_key": self.key,
            "factor": self.factor,
            "expected_type": self.expected_type,
            "value_type": self.value_type,
            "unit": self.unit,
            "direction": self.direction,
            "default_freshness": self.default_freshness,
            "freshness": self.freshness,
            "critical": self.critical,
            "trend_comparison_enabled": self.trend_comparison_enabled,
            "trend_enabled": self.trend_enabled,
            "asset_scope": list(self.asset_scope) if self.asset_scope is not None else None,
            "decision_role": self.decision_role,
            "context_group": self.context_group,
        }


def _definition(
    key: str,
    factor: str,
    expected_type: str,
    unit: str | None = None,
    direction: str = "CONTEXTUAL",
    *,
    critical: bool = False,
    asset_scope: tuple[str, ...] | None = None,
    freshness: str | int | float = "CURRENT",
    trend_enabled: bool = True,
    decision_role: str = "SCORING_FACTOR",
    context_group: str | None = None,
) -> MetricDefinition:
    return MetricDefinition(
        key,
        factor,
        expected_type,
        unit,
        direction,
        freshness,
        critical,
        trend_enabled,
        asset_scope,
        expected_type,
        freshness,
        trend_enabled,
        decision_role=decision_role,
        context_group=context_group,
    )


METRIC_REGISTRY: dict[str, MetricDefinition] = {
    "market.spot_price": _definition("market.spot_price", "trend", "number", "USD", "CONTEXTUAL", critical=True, freshness="1d"),
    "market.return_30d": _definition("market.return_30d", "trend", "number", "fraction", "HIGHER_IS_BETTER", freshness="7d"),
    "market.return_90d": _definition("market.return_90d", "trend", "number", "fraction", "HIGHER_IS_BETTER", freshness="7d"),
    "market.return_180d": _definition("market.return_180d", "trend", "number", "fraction", "HIGHER_IS_BETTER", freshness="14d"),
    "market.ma20": _definition("market.ma20", "trend", "number", "USD", "CONTEXTUAL", freshness="7d"),
    "market.ma50": _definition("market.ma50", "trend", "number", "USD", "CONTEXTUAL", freshness="7d"),
    "market.ma100": _definition("market.ma100", "trend", "number", "USD", "CONTEXTUAL", freshness="7d"),
    "market.ma200": _definition("market.ma200", "trend", "number", "USD", "CONTEXTUAL", freshness="14d"),
    "market.atr14": _definition("market.atr14", "trend", "number", "USD", "CONTEXTUAL", freshness="7d"),
    "market.realized_vol_30d": _definition("market.realized_vol_30d", "trend", "number", "fraction", "LOWER_IS_BETTER", freshness="7d"),
    "market.realized_vol_90d": _definition("market.realized_vol_90d", "trend", "number", "fraction", "LOWER_IS_BETTER", freshness="14d"),
    "market.relative_volume": _definition("market.relative_volume", "trend", "number", "ratio", "CONTEXTUAL", freshness="1d"),
    "market.drawdown": _definition("market.drawdown", "valuation", "number", "fraction", "HIGHER_IS_BETTER", freshness="7d"),
    "market.btc_dominance": _definition("market.btc_dominance", "relative_strength_btc", "number", "fraction", "CONTEXTUAL", freshness="2d"),
    "market.total_crypto_market_cap": _definition("market.total_crypto_market_cap", "valuation", "number", "USD", "CONTEXTUAL", freshness="2d"),
    "market.stablecoin_supply": _definition("market.stablecoin_supply", "capital_flows", "number", "USD", "HIGHER_IS_BETTER", freshness="7d"),
    "market.breadth": _definition("market.breadth", "relative_strength_btc", "number", "fraction", "HIGHER_IS_BETTER", freshness="2d"),
    "market.btc_trend": _definition("market.btc_trend", "trend", "string", None, "CONTEXTUAL", freshness="2d", asset_scope=("BTC",)),
    "market.volatility_state": _definition("market.volatility_state", "trend", "string", None, "CONTEXTUAL", freshness="2d", asset_scope=("BTC",)),
    "market.breadth_state": _definition("market.breadth_state", "relative_strength_btc", "string", None, "CONTEXTUAL", freshness="2d", asset_scope=("MARKET",)),
    "market.flow_state": _definition("market.flow_state", "capital_flows", "string", None, "CONTEXTUAL", freshness="2d", asset_scope=("MARKET",)),
    "flows.etf_net_1d": _definition("flows.etf_net_1d", "capital_flows", "number", "USD", "HIGHER_IS_BETTER", freshness="2d"),
    "flows.etf_net_7d": _definition("flows.etf_net_7d", "capital_flows", "number", "USD", "HIGHER_IS_BETTER", freshness="7d"),
    "flows.etf_net_30d": _definition("flows.etf_net_30d", "capital_flows", "number", "USD", "HIGHER_IS_BETTER", freshness="14d"),
    "flows.exchange_netflow": _definition("flows.exchange_netflow", "capital_flows", "number", "USD", "CONTEXTUAL", freshness="2d"),
    "fundamentals.tvl": _definition("fundamentals.tvl", "fundamentals", "number", "USD", "HIGHER_IS_BETTER", freshness="7d", asset_scope=_APPLICATION_ASSETS),
    "fundamentals.fees_30d": _definition("fundamentals.fees_30d", "fundamentals", "number", "USD", "HIGHER_IS_BETTER", freshness="7d", asset_scope=_APPLICATION_ASSETS),
    "fundamentals.revenue_30d": _definition("fundamentals.revenue_30d", "fundamentals", "number", "USD", "HIGHER_IS_BETTER", freshness="7d", asset_scope=_APPLICATION_ASSETS),
    "fundamentals.stablecoin_liquidity": _definition("fundamentals.stablecoin_liquidity", "fundamentals", "number", "USD", "HIGHER_IS_BETTER", freshness="7d", asset_scope=_APPLICATION_ASSETS),
    "fundamentals.active_users": _definition("fundamentals.active_users", "fundamentals", "number", "count", "HIGHER_IS_BETTER", freshness="7d", asset_scope=_APPLICATION_ASSETS),
    "fundamentals.developer_activity": _definition("fundamentals.developer_activity", "fundamentals", "number", "count", "HIGHER_IS_BETTER", freshness="30d", asset_scope=_APPLICATION_ASSETS),
    "onchain.active_addresses": _definition("onchain.active_addresses", "onchain", "number", "count", "HIGHER_IS_BETTER", freshness="3d", asset_scope=_PROTOCOL_ASSETS),
    "onchain.transfer_volume": _definition("onchain.transfer_volume", "onchain", "number", "USD", "HIGHER_IS_BETTER", freshness="3d", asset_scope=_PROTOCOL_ASSETS),
    "onchain.blockspace_fees": _definition("onchain.blockspace_fees", "onchain", "number", "USD", "HIGHER_IS_BETTER", freshness="7d", asset_scope=_PROTOCOL_ASSETS),
    "onchain.transaction_count": _definition("onchain.transaction_count", "onchain", "number", "count", "HIGHER_IS_BETTER", freshness="3d", asset_scope=_PROTOCOL_ASSETS),
    "valuation.market_cap": _definition("valuation.market_cap", "valuation", "number", "USD", "CONTEXTUAL", freshness="2d", asset_scope=_PROTOCOL_ASSETS),
    "valuation.fdv": _definition("valuation.fdv", "valuation", "number", "USD", "CONTEXTUAL", freshness="7d", asset_scope=_PROTOCOL_ASSETS),
    "valuation.fdv_market_cap_ratio": _definition("valuation.fdv_market_cap_ratio", "valuation", "number", "ratio", "LOWER_IS_BETTER", freshness="7d", asset_scope=_PROTOCOL_ASSETS),
    "valuation.fee_revenue_multiple": _definition("valuation.fee_revenue_multiple", "valuation", "number", "ratio", "LOWER_IS_BETTER", freshness="14d", asset_scope=_APPLICATION_ASSETS),
    "relative.return_vs_btc_30d": _definition("relative.return_vs_btc_30d", "relative_strength_btc", "number", "fraction", "HIGHER_IS_BETTER", freshness="7d", asset_scope=_APPLICATION_ASSETS),
    "relative.return_vs_btc_90d": _definition("relative.return_vs_btc_90d", "relative_strength_btc", "number", "fraction", "HIGHER_IS_BETTER", freshness="7d", asset_scope=_APPLICATION_ASSETS),
    "relative.return_vs_btc_180d": _definition("relative.return_vs_btc_180d", "relative_strength_btc", "number", "fraction", "HIGHER_IS_BETTER", freshness="14d", asset_scope=_APPLICATION_ASSETS),
    "tokenomics.next_unlock_pct": _definition("tokenomics.next_unlock_pct", "event_risk", "number", "fraction", "LOWER_IS_BETTER", freshness="30d", asset_scope=_PROTOCOL_ASSETS),
    "tokenomics.annualized_emissions": _definition("tokenomics.annualized_emissions", "event_risk", "number", "fraction", "LOWER_IS_BETTER", freshness="30d", asset_scope=_PROTOCOL_ASSETS),
    "tokenomics.supply_growth": _definition("tokenomics.supply_growth", "event_risk", "number", "fraction", "LOWER_IS_BETTER", freshness="30d", asset_scope=_PROTOCOL_ASSETS),
    "risk.security_event_status": _definition("risk.security_event_status", "event_risk", "string", None, "CONTEXTUAL", critical=True, freshness="1d", asset_scope=_PROTOCOL_ASSETS),
    "risk.chain_liveness_status": _definition("risk.chain_liveness_status", "event_risk", "string", None, "CONTEXTUAL", critical=True, freshness="1d", asset_scope=_PROTOCOL_ASSETS),
    "risk.regulatory_event_status": _definition("risk.regulatory_event_status", "event_risk", "string", None, "CONTEXTUAL", critical=True, freshness="1d", asset_scope=_PROTOCOL_ASSETS),
    "risk.governance_event_status": _definition("risk.governance_event_status", "event_risk", "string", None, "CONTEXTUAL", critical=True, freshness="1d", asset_scope=_PROTOCOL_ASSETS),

    # Positioning and social context are deliberately separate from scoring.
    "derivatives.funding_rate": _definition(
        "derivatives.funding_rate", "positioning", "number", "fraction", "CONTEXTUAL",
        freshness="1d", asset_scope=_DERIVATIVES_ASSETS,
        decision_role="POSITIONING_OVERLAY", context_group="positioning",
    ),
    "derivatives.funding_rate_24h_avg": _definition(
        "derivatives.funding_rate_24h_avg", "positioning", "number", "fraction", "CONTEXTUAL",
        freshness="1d", asset_scope=_DERIVATIVES_ASSETS,
        decision_role="POSITIONING_OVERLAY", context_group="positioning",
    ),
    "derivatives.funding_rate_7d_avg": _definition(
        "derivatives.funding_rate_7d_avg", "positioning", "number", "fraction", "CONTEXTUAL",
        freshness="1d", asset_scope=_DERIVATIVES_ASSETS,
        decision_role="POSITIONING_OVERLAY", context_group="positioning",
    ),
    "derivatives.funding_rate_percentile": _definition(
        "derivatives.funding_rate_percentile", "positioning", "number", "fraction", "CONTEXTUAL",
        freshness="1d", asset_scope=_DERIVATIVES_ASSETS,
        decision_role="POSITIONING_OVERLAY", context_group="positioning",
    ),
    "derivatives.open_interest_usd": _definition(
        "derivatives.open_interest_usd", "positioning", "number", "USD", "CONTEXTUAL",
        freshness="1d", asset_scope=_DERIVATIVES_ASSETS,
        decision_role="POSITIONING_OVERLAY", context_group="positioning",
    ),
    "derivatives.open_interest_change_1d": _definition(
        "derivatives.open_interest_change_1d", "positioning", "number", "fraction", "CONTEXTUAL",
        freshness="1d", asset_scope=_DERIVATIVES_ASSETS,
        decision_role="POSITIONING_OVERLAY", context_group="positioning",
    ),
    "derivatives.open_interest_change_7d": _definition(
        "derivatives.open_interest_change_7d", "positioning", "number", "fraction", "CONTEXTUAL",
        freshness="1d", asset_scope=_DERIVATIVES_ASSETS,
        decision_role="POSITIONING_OVERLAY", context_group="positioning",
    ),
    "derivatives.open_interest_to_market_cap": _definition(
        "derivatives.open_interest_to_market_cap", "positioning", "number", "ratio", "CONTEXTUAL",
        freshness="1d", asset_scope=_DERIVATIVES_ASSETS,
        decision_role="POSITIONING_OVERLAY", context_group="positioning",
    ),
    "derivatives.long_short_account_ratio": _definition(
        "derivatives.long_short_account_ratio", "positioning", "number", "ratio", "CONTEXTUAL",
        freshness="1d", asset_scope=_DERIVATIVES_ASSETS,
        decision_role="POSITIONING_OVERLAY", context_group="positioning",
    ),
    "derivatives.top_trader_long_short_ratio": _definition(
        "derivatives.top_trader_long_short_ratio", "positioning", "number", "ratio", "CONTEXTUAL",
        freshness="1d", asset_scope=_DERIVATIVES_ASSETS,
        decision_role="POSITIONING_OVERLAY", context_group="positioning",
    ),
    "derivatives.long_liquidations_24h_usd": _definition(
        "derivatives.long_liquidations_24h_usd", "positioning", "number", "USD", "CONTEXTUAL",
        freshness="1d", asset_scope=_DERIVATIVES_ASSETS,
        decision_role="POSITIONING_OVERLAY", context_group="positioning",
    ),
    "derivatives.short_liquidations_24h_usd": _definition(
        "derivatives.short_liquidations_24h_usd", "positioning", "number", "USD", "CONTEXTUAL",
        freshness="1d", asset_scope=_DERIVATIVES_ASSETS,
        decision_role="POSITIONING_OVERLAY", context_group="positioning",
    ),
    "derivatives.total_liquidations_24h_usd": _definition(
        "derivatives.total_liquidations_24h_usd", "positioning", "number", "USD", "CONTEXTUAL",
        freshness="1d", asset_scope=_DERIVATIVES_ASSETS,
        decision_role="POSITIONING_OVERLAY", context_group="positioning",
    ),
    "derivatives.long_liquidations_7d_usd": _definition(
        "derivatives.long_liquidations_7d_usd", "positioning", "number", "USD", "CONTEXTUAL",
        freshness="7d", asset_scope=_DERIVATIVES_ASSETS,
        decision_role="POSITIONING_OVERLAY", context_group="positioning",
    ),
    "derivatives.short_liquidations_7d_usd": _definition(
        "derivatives.short_liquidations_7d_usd", "positioning", "number", "USD", "CONTEXTUAL",
        freshness="7d", asset_scope=_DERIVATIVES_ASSETS,
        decision_role="POSITIONING_OVERLAY", context_group="positioning",
    ),
    "derivatives.futures_basis_annualized": _definition(
        "derivatives.futures_basis_annualized", "positioning", "number", "fraction", "CONTEXTUAL",
        freshness="1d", asset_scope=_DERIVATIVES_ASSETS,
        decision_role="POSITIONING_OVERLAY", context_group="positioning",
    ),
    "sentiment.social_bullish_share": _definition(
        "sentiment.social_bullish_share", "sentiment", "number", "fraction", "CONTEXTUAL",
        freshness="2d", asset_scope=_DERIVATIVES_ASSETS,
        decision_role="POSITIONING_OVERLAY", context_group="positioning",
    ),
    "sentiment.social_mentions_24h": _definition(
        "sentiment.social_mentions_24h", "sentiment", "number", "count", "CONTEXTUAL",
        freshness="2d", asset_scope=_DERIVATIVES_ASSETS,
        decision_role="POSITIONING_OVERLAY", context_group="positioning",
    ),
    "sentiment.social_mentions_change_7d": _definition(
        "sentiment.social_mentions_change_7d", "sentiment", "number", "fraction", "CONTEXTUAL",
        freshness="2d", asset_scope=_DERIVATIVES_ASSETS,
        decision_role="POSITIONING_OVERLAY", context_group="positioning",
    ),
    "sentiment.social_sentiment_percentile": _definition(
        "sentiment.social_sentiment_percentile", "sentiment", "number", "fraction", "CONTEXTUAL",
        freshness="2d", asset_scope=_DERIVATIVES_ASSETS,
        decision_role="POSITIONING_OVERLAY", context_group="positioning",
    ),
    "sentiment.social_attention_percentile": _definition(
        "sentiment.social_attention_percentile", "sentiment", "number", "fraction", "CONTEXTUAL",
        freshness="2d", asset_scope=_DERIVATIVES_ASSETS,
        decision_role="POSITIONING_OVERLAY", context_group="positioning",
    ),
    "sentiment.market_fear_greed": _definition(
        "sentiment.market_fear_greed", "sentiment", "number", "score", "CONTEXTUAL",
        freshness="2d", asset_scope=("MARKET",),
        decision_role="POSITIONING_OVERLAY", context_group="positioning",
    ),
    "onchain.btc.mvrv": _definition(
        "onchain.btc.mvrv", "cycle_context", "number", "ratio", "CONTEXTUAL",
        freshness="7d", asset_scope=("BTC",),
        decision_role="CYCLE_CONTEXT", context_group="btc_cycle",
    ),
    "onchain.btc.mvrv_zscore": _definition(
        "onchain.btc.mvrv_zscore", "cycle_context", "number", "zscore", "CONTEXTUAL",
        freshness="7d", asset_scope=("BTC",),
        decision_role="CYCLE_CONTEXT", context_group="btc_cycle",
    ),
    "onchain.btc.realized_price": _definition(
        "onchain.btc.realized_price", "cycle_context", "number", "USD", "CONTEXTUAL",
        freshness="7d", asset_scope=("BTC",),
        decision_role="CYCLE_CONTEXT", context_group="btc_cycle",
    ),
    "onchain.btc.market_to_realized_price": _definition(
        "onchain.btc.market_to_realized_price", "cycle_context", "number", "ratio", "CONTEXTUAL",
        freshness="7d", asset_scope=("BTC",),
        decision_role="CYCLE_CONTEXT", context_group="btc_cycle",
    ),
    "onchain.btc.sopr": _definition(
        "onchain.btc.sopr", "cycle_context", "number", "ratio", "CONTEXTUAL",
        freshness="7d", asset_scope=("BTC",),
        decision_role="CYCLE_CONTEXT", context_group="btc_cycle",
    ),
    "onchain.btc.lth_supply_pct": _definition(
        "onchain.btc.lth_supply_pct", "cycle_context", "number", "fraction", "CONTEXTUAL",
        freshness="7d", asset_scope=("BTC",),
        decision_role="CYCLE_CONTEXT", context_group="btc_cycle",
    ),
    "onchain.btc.lth_net_position_change": _definition(
        "onchain.btc.lth_net_position_change", "cycle_context", "number", "fraction", "CONTEXTUAL",
        freshness="7d", asset_scope=("BTC",),
        decision_role="CYCLE_CONTEXT", context_group="btc_cycle",
    ),
    "onchain.btc.sth_realized_price": _definition(
        "onchain.btc.sth_realized_price", "cycle_context", "number", "USD", "CONTEXTUAL",
        freshness="7d", asset_scope=("BTC",),
        decision_role="CYCLE_CONTEXT", context_group="btc_cycle",
    ),
    "onchain.btc.lth_realized_price": _definition(
        "onchain.btc.lth_realized_price", "cycle_context", "number", "USD", "CONTEXTUAL",
        freshness="7d", asset_scope=("BTC",),
        decision_role="CYCLE_CONTEXT", context_group="btc_cycle",
    ),
    "onchain.btc.nupl": _definition(
        "onchain.btc.nupl", "cycle_context", "number", "fraction", "CONTEXTUAL",
        freshness="7d", asset_scope=("BTC",),
        decision_role="CYCLE_CONTEXT", context_group="btc_cycle",
    ),
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


def validate_metric_value(metric_key: str, value: Any) -> Any:
    """Validate simple numeric semantics without interpreting investment meaning."""
    definition = metric_definition(metric_key)
    if definition.expected_type != "number" or value is None:
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"metric {definition.key} value must be finite numeric")
    number = float(value)
    signed = (
        definition.key.startswith("market.return_")
        or definition.key.startswith("relative.return_vs_btc_")
        or definition.key == "market.drawdown"
        or definition.key.startswith("flows.")
        or definition.key.startswith("derivatives.funding_rate")
        or definition.key.startswith("derivatives.open_interest_change_")
        or definition.key == "derivatives.futures_basis_annualized"
        or definition.key == "sentiment.social_mentions_change_7d"
        or definition.key in {"onchain.btc.mvrv_zscore", "onchain.btc.lth_net_position_change", "onchain.btc.nupl"}
    )
    if not signed and number < 0:
        raise ValueError(f"metric {definition.key} value must be non-negative")
    if definition.key == "market.spot_price" and number <= 0:
        raise ValueError("metric market.spot_price value must be > 0")
    if definition.key in {"market.ma20", "market.ma50", "market.ma100", "market.ma200", "market.atr14"} and number <= 0:
        raise ValueError(f"metric {definition.key} value must be > 0")
    if definition.key.startswith(("market.return_", "relative.return_vs_btc_")) and number < -1:
        raise ValueError(f"metric {definition.key} return must be >= -1")
    if definition.key == "market.drawdown" and number > 0:
        raise ValueError("metric market.drawdown value must be <= 0")
    if definition.key in {"market.btc_dominance", "market.breadth"} and number > 1:
        raise ValueError(f"metric {definition.key} fraction must be <= 1")
    if definition.key in {
        "derivatives.funding_rate_percentile",
        "sentiment.social_bullish_share",
        "sentiment.social_sentiment_percentile",
        "sentiment.social_attention_percentile",
        "onchain.btc.lth_supply_pct",
    } and not 0 <= number <= 1:
        raise ValueError(f"metric {definition.key} fraction must be <= 1")
    if definition.key == "sentiment.market_fear_greed" and not 0 <= number <= 100:
        raise ValueError("metric sentiment.market_fear_greed score must be in [0, 100]")
    if definition.key in {
        "derivatives.long_short_account_ratio",
        "derivatives.top_trader_long_short_ratio",
        "derivatives.open_interest_to_market_cap",
        "onchain.btc.mvrv",
        "onchain.btc.market_to_realized_price",
        "onchain.btc.sopr",
    } and number <= 0:
        raise ValueError(f"metric {definition.key} ratio must be > 0")
    if definition.key.startswith(("derivatives.open_interest_change_", "sentiment.social_mentions_change_")) or definition.key in {
        "onchain.btc.lth_net_position_change",
        "onchain.btc.nupl",
    }:
        if number < -1:
            raise ValueError(f"metric {definition.key} change must be >= -1")
    return value


def known_metric_keys() -> tuple[str, ...]:
    return tuple(METRIC_REGISTRY)


def metrics_for_factor(factor: str, *, asset: str | None = None) -> tuple[MetricDefinition, ...]:
    if not isinstance(factor, str) or not factor.strip():
        raise ValueError("factor must be a non-empty string")
    factor = factor.strip().lower()
    values = tuple(
        definition
        for definition in METRIC_REGISTRY.values()
        if definition.factor == factor
        and (asset is None or definition.applies_to(asset))
    )
    return values


def metrics_for_role(
    decision_role: str,
    *,
    context_group: str | None = None,
    asset: str | None = None,
) -> tuple[MetricDefinition, ...]:
    if not isinstance(decision_role, str) or not decision_role.strip():
        raise ValueError("decision_role must be a non-empty string")
    role = decision_role.strip().upper()
    if role not in _DECISION_ROLES:
        raise ValueError("decision_role is unsupported")
    if context_group is not None and (not isinstance(context_group, str) or not context_group.strip()):
        raise ValueError("context_group must be a non-empty string or null")
    group = context_group.strip().lower() if context_group is not None else None
    return tuple(
        definition
        for definition in METRIC_REGISTRY.values()
        if definition.decision_role == role
        and (group is None or definition.context_group == group)
        and (asset is None or definition.applies_to(asset))
    )


validate_metric_key = metric_definition


__all__ = [
    "METRIC_REGISTRY",
    "METRICS",
    "DECISION_ROLES",
    "MetricDefinition",
    "get_metric_definition",
    "get_metric",
    "known_metric_keys",
    "metrics_for_factor",
    "metrics_for_role",
    "metric_definition",
    "normalize_metric_key",
    "validate_metric_key",
    "validate_metric_value",
]
