"""Public deterministic execution-planning façade and validation."""

from .rebalance import validate_execution_plan as _legacy_validate_execution_plan
from .entry import build_entry_plan, build_execution_evidence
from ..models.execution import ExecutionPlan
from ..models.market import TechnicalSnapshot
from typing import Any, Iterable, Mapping


def validate_typed_execution_plan(plan: ExecutionPlan | Mapping[str, Any]) -> bool:
    """Validate all typed execution financial and structural invariants."""
    model = plan if isinstance(plan, ExecutionPlan) else ExecutionPlan.from_mapping(plan)
    if model.entry_mode == "PULLBACK" and model.tranches:
        references = [tranche.reference_price for tranche in model.tranches]
        if references != sorted(references, reverse=True):
            raise ValueError("pullback tranche zones must descend from nearest to deepest support")
        if any(tranche.price_high > model.current_price + 1e-9 for tranche in model.tranches):
            raise ValueError("pullback tranche zones must not be above current price")
    if model.entry_mode == "BREAKOUT" and model.tranches:
        raise ValueError("BREAKOUT plans are disabled until breakout/retest structure is implemented")
    for tranche in model.tranches:
        if not tranche.structural_sources:
            raise ValueError("each tranche must retain structural_sources")
    return True


def validate_execution_plan(plan: ExecutionPlan | Mapping[str, Any] | Iterable[Mapping[str, Any]]) -> bool:
    if isinstance(plan, ExecutionPlan) or (
        isinstance(plan, Mapping) and {"tranches", "execution_plan_version"} & set(plan)
    ):
        return validate_typed_execution_plan(plan)
    return _legacy_validate_execution_plan(plan)


def build_execution_plan(
    symbol: str,
    approved_amount_usd: float,
    technical_snapshot: TechnicalSnapshot,
    regime: str,
    portfolio_confidence: str,
    action: str = "INCREASE",
    **kwargs: Any,
) -> ExecutionPlan:
    plan = build_entry_plan(
        symbol,
        approved_amount_usd,
        technical_snapshot,
        regime,
        portfolio_confidence,
        action,
        **kwargs,
    )
    validate_typed_execution_plan(plan)
    return plan


__all__ = [
    "build_entry_plan",
    "build_execution_plan",
    "build_execution_evidence",
    "validate_execution_plan",
    "validate_typed_execution_plan",
]
