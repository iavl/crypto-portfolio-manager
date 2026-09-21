"""Final proposal amounts; never evidence of submitted orders or fills."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math


def amount(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return float(value)


@dataclass(frozen=True)
class OperationLine:
    symbol: str
    strategic_action: str
    approved_amount_usd: float
    proposed_amount_usd: float
    reserve_amount_usd: float
    execution_status: str
    purpose: str
    reason: str
    reserve_policy: str | None = None

    def __post_init__(self):
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("operation symbol is required")
        if self.strategic_action not in {"INCREASE", "REDUCE", "EXIT", "HOLD", "WAIT", "NO_TRADE"}:
            raise ValueError("unsupported strategic action")
        if self.execution_status not in {
            "CONDITIONAL_LIMIT_PROPOSAL",
            "CONDITIONAL_FUNDING",
            "PROPOSED_NOT_CONFIRMED",
            "FUNDING_DEFERRED",
            "GATE_HOLD",
            "NO_EXECUTABLE_CHANGE",
        }:
            raise ValueError("unsupported execution status")
        for name in ("approved_amount_usd", "proposed_amount_usd", "reserve_amount_usd"):
            amount(getattr(self, name), name)
        if self.proposed_amount_usd > self.approved_amount_usd + 1e-7:
            raise ValueError("proposal exceeds approval")
        if self.purpose not in {"RISK_INCREASE", "INDEPENDENT_REDUCTION", "BUY_FUNDING", "STABLE_RECEIPT", "NONE"}:
            raise ValueError("unsupported operation purpose")


@dataclass(frozen=True)
class FinalOperation:
    execution_actions: tuple[OperationLine, ...]
    decision: str
    planned_buy_amount_usd: float
    independent_sale_amount_usd: float
    stable_funding_amount_usd: float
    residual_stable_change_usd: float

    def __post_init__(self):
        if self.decision not in {"PROPOSED", "WAIT", "NO_TRADE"}:
            raise ValueError("unsupported final operation decision")
        for name in (
            "planned_buy_amount_usd",
            "independent_sale_amount_usd",
            "stable_funding_amount_usd",
        ):
            amount(getattr(self, name), name)
        if (
            isinstance(self.residual_stable_change_usd, bool)
            or not isinstance(self.residual_stable_change_usd, (int, float))
            or not math.isfinite(self.residual_stable_change_usd)
        ):
            raise ValueError("residual stable change must be finite")

    def as_dict(self):
        return {
            **asdict(self),
            "execution_actions": [asdict(row) for row in self.execution_actions],
            "status": "FINAL_OPERATION_VIEW",
            "confirmation_status": "NOT_CONFIRMED",
            "amount_semantics": "conditional proposals, not market orders or confirmed fills",
            "funding_deferred": any(row.execution_status == "FUNDING_DEFERRED" for row in self.execution_actions),
            "gated_buy_symbols": [row.symbol for row in self.execution_actions if row.execution_status == "GATE_HOLD"],
        }
