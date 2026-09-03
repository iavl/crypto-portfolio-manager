"""Conservative execution caps derived from finalized market overlays."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from ..models.cycle import BTCCycleContext
from ..models.market_overlays import MarketOverlays
from ..models.positioning import PositioningFacts
from ..models.policy import Policy, resolve_policy


def _factor(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a number")
    result = float(value)
    if not math.isfinite(result) or not 0 <= result <= 1:
        raise ValueError(f"{field_name} must be finite and in [0, 1]")
    return result


def _amount(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a number")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{field_name} must be finite and >= 0")
    return result


def _positioning(value: Any) -> PositioningFacts | None:
    if value is None:
        return None
    if isinstance(value, PositioningFacts):
        return value
    if isinstance(value, Mapping):
        return PositioningFacts.from_mapping(value)
    raise ValueError("positioning must be PositioningFacts, mapping, or null")


def _cycle(value: Any) -> BTCCycleContext | None:
    if value is None:
        return None
    if isinstance(value, BTCCycleContext):
        return value
    if isinstance(value, Mapping):
        return BTCCycleContext.from_mapping(value)
    raise ValueError("btc_cycle must be BTCCycleContext, mapping, or null")


def positioning_deployment_factor(
    positioning: PositioningFacts | Mapping[str, Any] | None,
    *,
    policy: Policy | None = None,
    action: str = "INCREASE",
) -> float:
    if str(action).strip().upper() != "INCREASE":
        return 1.0
    resolved = policy or resolve_policy()
    if isinstance(resolved.positioning, Mapping) and resolved.positioning.get("enabled") is False:
        return 1.0
    facts = _positioning(positioning)
    if facts is None or facts.bias not in {"LONG_BIASED", "LONG_CROWDED"}:
        return 1.0
    configured = resolved.execution_overlay.get("positioning", {})
    default = {"NORMAL": 1.0, "ELEVATED": 0.75, "HIGH": 0.50, "EXTREME": 0.25, "UNKNOWN": 1.0}
    mapping = {**default, **configured} if isinstance(configured, Mapping) else default
    return _factor(mapping.get(facts.risk, mapping.get("UNKNOWN", 1.0)), "positioning deployment factor")


def cycle_deployment_factor(
    btc_cycle: BTCCycleContext | Mapping[str, Any] | None,
    *,
    policy: Policy | None = None,
    action: str = "INCREASE",
) -> float:
    if str(action).strip().upper() != "INCREASE":
        return 1.0
    resolved = policy or resolve_policy()
    if isinstance(resolved.btc_cycle, Mapping) and resolved.btc_cycle.get("enabled") is False:
        return 1.0
    context = _cycle(btc_cycle)
    if context is None:
        return 1.0
    configured = resolved.execution_overlay.get("btc_cycle", {})
    default = {"NORMAL": 1.0, "ELEVATED": 0.80, "HIGH": 0.50, "UNKNOWN": 1.0}
    mapping = {**default, **configured} if isinstance(configured, Mapping) else default
    return _factor(mapping.get(context.cycle_risk, mapping.get("UNKNOWN", 1.0)), "cycle deployment factor")


def effective_deployment_factor(
    base_deployment_factor: float = 1.0,
    positioning: PositioningFacts | Mapping[str, Any] | float | None = None,
    btc_cycle: BTCCycleContext | Mapping[str, Any] | float | None = None,
    *,
    positioning_factor: float | None = None,
    cycle_factor: float | None = None,
    overlays: MarketOverlays | Mapping[str, Any] | None = None,
    policy: Policy | None = None,
    action: str = "INCREASE",
) -> float:
    base = _factor(base_deployment_factor, "base_deployment_factor")
    if overlays is not None:
        container = overlays if isinstance(overlays, MarketOverlays) else MarketOverlays.from_mapping(overlays)
        if positioning is None:
            positioning = container.positioning_by_asset.get("BTC")
        if btc_cycle is None:
            btc_cycle = container.btc_cycle
    if positioning_factor is None:
        positioning_factor = (
            _factor(positioning, "positioning_factor")
            if isinstance(positioning, (int, float)) and not isinstance(positioning, bool)
            else positioning_deployment_factor(positioning, policy=policy, action=action)
        )
    else:
        positioning_factor = _factor(positioning_factor, "positioning_factor")
    if cycle_factor is None:
        cycle_factor = (
            _factor(btc_cycle, "cycle_factor")
            if isinstance(btc_cycle, (int, float)) and not isinstance(btc_cycle, bool)
            else cycle_deployment_factor(btc_cycle, policy=policy, action=action)
        )
    else:
        cycle_factor = _factor(cycle_factor, "cycle_factor")
    return min(base, positioning_factor, cycle_factor)


def overlay_wait_required(
    positioning: PositioningFacts | Mapping[str, Any] | None,
    technical_extension_atr: float | None,
    *,
    btc_cycle: BTCCycleContext | Mapping[str, Any] | None = None,
    policy: Policy | None = None,
    action: str = "INCREASE",
) -> bool:
    if str(action).strip().upper() != "INCREASE" or technical_extension_atr is None:
        return False
    resolved = policy or resolve_policy()
    if isinstance(resolved.positioning, Mapping) and resolved.positioning.get("enabled") is False:
        return False
    extension = _amount(technical_extension_atr, "technical_extension_atr")
    facts = _positioning(positioning)
    if facts is None or facts.bias not in {"LONG_BIASED", "LONG_CROWDED"}:
        return False
    if facts.confidence not in {"HIGH", "MEDIUM"}:
        return False
    settings = resolved.execution_overlay.get("wait", {})
    if not isinstance(settings, Mapping) or not settings.get("enabled", True):
        return False
    threshold = float(settings.get("minimum_extension_atr", 2.0))
    confirmed_crowding = facts.risk in {"HIGH", "EXTREME"} or facts.leverage_state in {"CROWDED", "EXTREME"}
    cycle = _cycle(btc_cycle)
    cycle_strengthens = cycle is not None and cycle.cycle_risk in {"ELEVATED", "HIGH"}
    return extension >= threshold and (confirmed_crowding or cycle_strengthens and facts.risk == "ELEVATED")


@dataclass(frozen=True)
class OverlayDeployment:
    approved_amount_usd: float
    planned_amount_usd: float
    unallocated_amount_usd: float
    effective_factor: float
    positioning_factor: float
    cycle_factor: float
    wait: bool
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "approved_amount_usd",
            "planned_amount_usd",
            "unallocated_amount_usd",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0:
                raise ValueError(f"{field_name} must be finite and >= 0")
        if self.planned_amount_usd > self.approved_amount_usd + 1e-9:
            raise ValueError("planned_amount_usd must not exceed approved_amount_usd")
        if not math.isclose(self.planned_amount_usd + self.unallocated_amount_usd, self.approved_amount_usd, abs_tol=1e-7):
            raise ValueError("overlay deployment amounts must reconcile")
        object.__setattr__(self, "effective_factor", _factor(self.effective_factor, "effective_factor"))
        object.__setattr__(self, "positioning_factor", _factor(self.positioning_factor, "positioning_factor"))
        object.__setattr__(self, "cycle_factor", _factor(self.cycle_factor, "cycle_factor"))
        if not isinstance(self.wait, bool):
            raise ValueError("wait must be boolean")
        warnings = tuple(str(item).strip() for item in self.warnings if str(item).strip())
        object.__setattr__(self, "warnings", tuple(dict.fromkeys(warnings)))

    def as_dict(self) -> dict[str, Any]:
        return {
            "approved_amount_usd": float(self.approved_amount_usd),
            "planned_amount_usd": float(self.planned_amount_usd),
            "unallocated_amount_usd": float(self.unallocated_amount_usd),
            "effective_factor": self.effective_factor,
            "positioning_factor": self.positioning_factor,
            "cycle_factor": self.cycle_factor,
            "wait": self.wait,
            "warnings": list(self.warnings),
        }


def apply_overlay_deployment_cap(
    approved_amount_usd: float,
    *,
    base_deployment_factor: float = 1.0,
    positioning: PositioningFacts | Mapping[str, Any] | None = None,
    btc_cycle: BTCCycleContext | Mapping[str, Any] | None = None,
    technical_extension_atr: float | None = None,
    policy: Policy | None = None,
    action: str = "INCREASE",
) -> OverlayDeployment:
    approved = _amount(approved_amount_usd, "approved_amount_usd")
    pos_factor = positioning_deployment_factor(positioning, policy=policy, action=action)
    cyc_factor = cycle_deployment_factor(btc_cycle, policy=policy, action=action)
    factor = effective_deployment_factor(
        base_deployment_factor,
        positioning_factor=pos_factor,
        cycle_factor=cyc_factor,
        action=action,
    )
    wait = overlay_wait_required(
        positioning,
        technical_extension_atr,
        btc_cycle=btc_cycle,
        policy=policy,
        action=action,
    )
    if wait:
        factor = 0.0
    planned = approved * factor
    warnings = []
    if pos_factor < 1.0:
        warnings.append(f"positioning deployment cap {pos_factor:.0%}")
    if cyc_factor < 1.0:
        warnings.append(f"BTC cycle deployment cap {cyc_factor:.0%}")
    if wait:
        warnings.append("confirmed crowded positioning plus technical extension requires WAIT")
    return OverlayDeployment(
        approved_amount_usd=approved,
        planned_amount_usd=planned,
        unallocated_amount_usd=approved - planned,
        effective_factor=factor,
        positioning_factor=pos_factor,
        cycle_factor=cyc_factor,
        wait=wait,
        warnings=tuple(warnings),
    )


def build_market_overlays(
    positioning_by_asset: Mapping[str, PositioningFacts | Mapping[str, Any]] | None = None,
    btc_cycle: BTCCycleContext | Mapping[str, Any] | None = None,
    *,
    policy: Policy | None = None,
) -> MarketOverlays:
    """Assemble compact overlay outcomes and per-asset deployment caps."""
    resolved = policy or resolve_policy()
    facts = {
        str(symbol).strip().upper(): _positioning(value)
        for symbol, value in (positioning_by_asset or {}).items()
    }
    parsed_facts = {symbol: value for symbol, value in facts.items() if value is not None}
    cycle = _cycle(btc_cycle)
    caps = {
        symbol: effective_deployment_factor(
            1.0,
            positioning=value,
            btc_cycle=cycle,
            policy=resolved,
        )
        for symbol, value in parsed_facts.items()
    }
    if cycle is not None and "BTC" not in caps:
        caps["BTC"] = cycle_deployment_factor(cycle, policy=resolved)
    warnings = []
    for symbol, value in parsed_facts.items():
        if value.bias == "LONG_CROWDED" and value.risk in {"HIGH", "EXTREME"}:
            warnings.append("POSITIONING_CROWDED_LONG:" + symbol)
        if value.risk == "EXTREME":
            warnings.append("POSITIONING_EXTREME:" + symbol)
    if cycle is not None and cycle.cycle_risk in {"ELEVATED", "HIGH"}:
        warnings.append("BTC_CYCLE_RISK_" + cycle.cycle_risk)
    return MarketOverlays(
        positioning_by_asset=parsed_facts,
        btc_cycle=cycle,
        warnings=tuple(warnings),
        effective_deployment_caps=caps,
    )


deployment_factor = effective_deployment_factor
apply_deployment_cap = apply_overlay_deployment_cap


__all__ = [
    "OverlayDeployment",
    "apply_deployment_cap",
    "apply_overlay_deployment_cap",
    "build_market_overlays",
    "cycle_deployment_factor",
    "deployment_factor",
    "effective_deployment_factor",
    "overlay_wait_required",
    "positioning_deployment_factor",
]
