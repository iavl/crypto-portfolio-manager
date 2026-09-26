"""Unified signal admission registry (Strategy V2.3 Phase 5).

One inventory of every preregistered BTC-relative alpha signal (ETH, BNB,
AAVE; SOL stays research-only with no production signals), its family and
provenance, and its admission status derived from the unified admission
rule. Only ADMITTED signals may enter an asset ensemble, only an admitted
ensemble can earn a production tilt, and the registry hash freezes the
admission identity behind every experiment. The generic composite score is
diagnostic from here on: nothing in this registry reads it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from ..engine import aave_relative_alpha, bnb_relative_alpha, eth_relative_alpha

ADMISSION_STATUSES = (
    "ADMITTED",
    "RESEARCH_ONLY",
    "REJECTED",
    "INSUFFICIENT_SAMPLE",
)

# Preregistered signal inventory: signal id -> spec. The signal sets are
# owned by the engine modules; this registry only documents provenance and
# tracks admission, so the sets can never drift apart silently.
_PRICE_FAMILY = "relative_price"


@dataclass(frozen=True)
class SignalSpec:
    """One preregistered signal.

    ``signal_id`` is asset-scoped (``BNB:chain_tvl_growth_90d``) because the
    price signals share names across assets; ``name`` is the engine module's
    signal key the allocation policy's ``admitted_signals`` lists use.
    """

    signal_id: str
    asset: str
    family: str
    name: str = ""
    decision_horizon: int = 90
    lookback_days: int | None = None
    source: str = ""
    point_in_time: bool = True

    @classmethod
    def register(cls, name: str, asset: str, family: str, **kwargs: Any) -> "SignalSpec":
        return cls(signal_id=f"{asset}:{name}", asset=asset, family=family, name=name, **kwargs)


def _eth_specs() -> dict[str, SignalSpec]:
    specs: dict[str, SignalSpec] = {}
    for name in eth_relative_alpha.SIGNAL_NAMES:
        if name.startswith("rel_return_"):
            lookback = int(name.split("_")[-1].rstrip("d"))
            specs[f"ETH:{name}"] = SignalSpec.register(
                name, "ETH", _PRICE_FAMILY, lookback_days=lookback,
            )
        elif name in ("ma_structure", "momentum_persistence_90d",
                      "risk_adjusted_momentum_90d"):
            specs[f"ETH:{name}"] = SignalSpec.register(
                name, "ETH", _PRICE_FAMILY, lookback_days=90,
            )
        elif name == "relative_drawdown_180d":
            specs[f"ETH:{name}"] = SignalSpec.register(
                name, "ETH", _PRICE_FAMILY, lookback_days=180,
            )
        elif name == "etf_flow_differential_30d":
            specs[f"ETH:{name}"] = SignalSpec.register(
                name, "ETH", "flow_differential", lookback_days=30,
                source="sosovalue:etf:{ETH,BTC}:{netflow,aum}",
            )
    return specs


def _bnb_specs() -> dict[str, SignalSpec]:
    specs: dict[str, SignalSpec] = {}
    for name in bnb_relative_alpha.SIGNAL_NAMES:
        if name in bnb_relative_alpha.GROWTH_WINDOWS:
            series_key, days = bnb_relative_alpha.GROWTH_WINDOWS[name]
            specs[f"BNB:{name}"] = SignalSpec.register(
                name, "BNB", "chain_growth", lookback_days=days,
                source=f"defillama:{series_key}:BNB",
            )
        elif name.startswith("rel_return_"):
            specs[f"BNB:{name}"] = SignalSpec.register(
                name, "BNB", _PRICE_FAMILY,
                lookback_days=int(name.split("_")[-1].rstrip("d")),
            )
        else:
            specs[f"BNB:{name}"] = SignalSpec.register(
                name, "BNB", _PRICE_FAMILY, lookback_days=180,
            )
    return specs


def _aave_specs() -> dict[str, SignalSpec]:
    specs: dict[str, SignalSpec] = {}
    for name in aave_relative_alpha.SIGNAL_NAMES:
        if name in aave_relative_alpha.GROWTH_WINDOWS:
            series_key, days = aave_relative_alpha.GROWTH_WINDOWS[name]
            specs[f"AAVE:{name}"] = SignalSpec.register(
                name, "AAVE", "protocol_growth", lookback_days=days,
                source=f"defillama:{series_key}:aave",
            )
        elif name.startswith("rel_return_"):
            specs[f"AAVE:{name}"] = SignalSpec.register(
                name, "AAVE", _PRICE_FAMILY,
                lookback_days=int(name.split("_")[-1].rstrip("d")),
            )
        else:
            specs[f"AAVE:{name}"] = SignalSpec.register(
                name, "AAVE", _PRICE_FAMILY, lookback_days=180,
            )
    return specs


def preregistered_specs() -> dict[str, SignalSpec]:
    """The full preregistered inventory across ETH, BNB, and AAVE."""
    specs: dict[str, SignalSpec] = {}
    for builder in (_eth_specs, _bnb_specs, _aave_specs):
        for signal_id, spec in builder().items():
            if signal_id in specs:
                raise ValueError(f"duplicate preregistered signal id {signal_id}")
            specs[signal_id] = spec
    return specs


def classify_signal_verdict(verdict: Mapping[str, Any]) -> str:
    """Admission status from one unified-rule signal verdict.

    ADMITTED — the rule passed (positive 90D IC in both windows, terciles
    not inverted, enough independent blocks). REJECTED — the direction is
    consistently negative. INSUFFICIENT_SAMPLE — the only failures are
    sample-count ones. RESEARCH_ONLY — everything else (disagreement,
    inverted terciles, unavailable IC).
    """
    if verdict.get("admitted"):
        return "ADMITTED"
    reasons = [str(reason) for reason in verdict.get("reasons", ())]
    sample_failures = (
        "independent 90D blocks" in reason or "no 90D IC available" in reason
        for reason in reasons
    )
    directional_failures = (
        "90D IC is not positive" in reason
        or "top tercile below bottom tercile" in reason
        or "disagree" in reason
        for reason in reasons
    )
    if any(sample_failures) and not any(directional_failures):
        return "INSUFFICIENT_SAMPLE"
    if verdict.get("direction", 0) == -1 and not any(
        "disagree" in reason for reason in reasons
    ):
        return "REJECTED"
    return "RESEARCH_ONLY"


def build_registry(
    admissions: Mapping[str, Mapping[str, Any]],
    *,
    specs: Mapping[str, SignalSpec] | None = None,
) -> dict[str, Any]:
    """Registry from per-asset admission outputs (the unified rule's shape).

    ``admissions`` maps the asset name to the output of
    ``evaluate_signal_admission``-style evaluation (``signals`` key with
    per-signal verdicts). Signals without an evaluation stay RESEARCH_ONLY
    so nothing is admitted by silence.
    """
    inventory = dict(specs or preregistered_specs())
    signals: dict[str, dict[str, Any]] = {}
    covered: set[str] = set()
    for asset, block in sorted(admissions.items()):
        verdicts = (block or {}).get("signals") or {}
        for raw_name, verdict in sorted(verdicts.items()):
            name = str(raw_name)
            signal_id = f"{str(asset).upper()}:{name}"
            spec = inventory.get(signal_id)
            if spec is None:
                spec = SignalSpec.register(name, str(asset).upper(), "unclassified")
            signals[signal_id] = {
                **asdict(spec),
                "admission_status": classify_signal_verdict(verdict),
                "reasons": [str(reason) for reason in verdict.get("reasons", ())],
            }
            covered.add(signal_id)
    for signal_id, spec in sorted(inventory.items()):
        if signal_id not in covered:
            signals[signal_id] = {
                **asdict(spec),
                "admission_status": "RESEARCH_ONLY",
                "reasons": ["no admission evaluation supplied"],
            }
    # SOL has no production signals by design (V2.3 canonical): its
    # research-only status is part of the registry identity.
    signals["sol_production_alpha"] = {
        "signal_id": "sol_production_alpha",
        "asset": "SOL",
        "family": "research_only_sentinel",
        "decision_horizon": 90,
        "lookback_days": None,
        "source": "",
        "point_in_time": True,
        "admission_status": "RESEARCH_ONLY",
        "reasons": ["SOL production alpha disabled until a SOL/BTC admission passes"],
    }
    registry = {
        "contract": "ALPHA_REGISTRY_V23",
        "signals": signals,
    }
    registry["registry_hash"] = registry_hash(registry)
    return registry


def registry_hash(registry: Mapping[str, Any]) -> str:
    """Canonical SHA-256 of the registry's identity (hash field excluded)."""
    payload = {
        key: value for key, value in registry.items() if key != "registry_hash"
    }
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def admitted_signals_for_asset(
    registry: Mapping[str, Any], asset: str
) -> tuple[str, ...]:
    """Admitted engine signal names of one asset, in preregistered order.

    These are the exact identifiers ``satellite_alpha.<asset>.
    admitted_signals`` accepts: the engine module's own signal keys.
    """
    name = str(asset).strip().upper()
    inventory = preregistered_specs()
    order = {signal_id: index for index, signal_id in enumerate(inventory)}
    matched = [
        entry for entry in registry["signals"].values()
        if str(entry.get("asset", "")).upper() == name
        and entry.get("admission_status") == "ADMITTED"
    ]
    matched.sort(key=lambda entry: order.get(str(entry["signal_id"]), len(order)))
    return tuple(str(entry["name"]) for entry in matched if entry.get("name"))


def asset_admission_summary(registry: Mapping[str, Any]) -> dict[str, Any]:
    """Per-asset production-readiness summary for reports and gates."""
    summary: dict[str, Any] = {}
    for signal_id, entry in sorted(registry["signals"].items()):
        asset = str(entry.get("asset", "")).upper()
        block = summary.setdefault(asset, {
            "signals": 0, "admitted": 0, "rejected": 0,
            "research_only": 0, "insufficient_sample": 0,
        })
        block["signals"] += 1
        status = str(entry.get("admission_status"))
        key = {
            "ADMITTED": "admitted",
            "REJECTED": "rejected",
            "RESEARCH_ONLY": "research_only",
            "INSUFFICIENT_SAMPLE": "insufficient_sample",
        }[status]
        block[key] += 1
    for asset, block in summary.items():
        block["production_tilt_unlocked"] = block["admitted"] > 0
    return summary


__all__ = [
    "ADMISSION_STATUSES",
    "SignalSpec",
    "admitted_signals_for_asset",
    "asset_admission_summary",
    "build_registry",
    "classify_signal_verdict",
    "preregistered_specs",
    "registry_hash",
]
