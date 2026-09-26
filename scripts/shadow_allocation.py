#!/usr/bin/env python3
"""Shadow allocation: legacy vs Strategy V2.3 targets on live holdings.

Strategy V2.3 Phase 7 migration diagnostics (plan 11.3), read-only:

- dry-run the deterministic pipeline on the LATEST runtime snapshot and the
  latest decision's frozen factor scores;
- build targets under the canonical legacy policy, the V2.3
  volatility-budget policy with every tilt still locked, and optionally a
  V2.3 policy with tilt authority unlocked per an accepted validation
  registry (manual alpha states can be injected for scenario dry-runs);
- classify each asset's target delta through the rebalance bands and the
  staged transition constraints;
- reconcile the read-only open-order book against the V2.3 targets;
- never place, cancel, or modify anything.

The canonical policy switch itself is a gated human decision (plan 11.2);
this script only makes the transition visible before that decision.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from crypto_portfolio.engine.allocation import build_target_allocation  # noqa: E402
from crypto_portfolio.engine.portfolio_risk import build_portfolio_risk_inputs_from_closes  # noqa: E402
from crypto_portfolio.engine.regime import RegimeInputs, determine_regime  # noqa: E402
from crypto_portfolio.models.market import OHLCVSeries  # noqa: E402
from crypto_portfolio.models.policy import load_policy, policy_from_mapping  # noqa: E402
from crypto_portfolio.state.snapshots import runtime_data_dir  # noqa: E402


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                records.append(json.loads(line))
    return records


def _latest_snapshot(root: Path) -> dict[str, Any]:
    records = _read_jsonl(root / "portfolio" / "snapshots.jsonl")
    if not records:
        raise SystemExit("no snapshots found in runtime state")
    return records[-1]


def _latest_decision(root: Path) -> dict[str, Any]:
    records = _read_jsonl(root / "decisions" / "decisions.jsonl")
    if not records:
        raise SystemExit("no decisions found in runtime state")
    return records[-1]


def _snapshot_weights(snapshot: dict[str, Any]) -> tuple[dict[str, float], float]:
    positions = snapshot.get("positions") or {}
    if isinstance(positions, dict):
        positions = [
            {"symbol": symbol, **position} for symbol, position in positions.items()
        ]
    values: dict[str, float] = {}
    for position in positions:
        symbol = str(position.get("symbol") or "").upper()
        value = position.get("value_usd")
        if value is None:
            amount = float(position.get("quantity") or 0.0)
            price = float(position.get("current_price_usd") or position.get("price_usd") or 0.0)
            value = amount * price
        if symbol and float(value) > 0:
            values[symbol] = values.get(symbol, 0.0) + float(value)
    total = float(snapshot.get("total_value") or snapshot.get("reported_total_value_usd") or 0.0)
    if total <= 0 or not values:
        raise SystemExit("latest snapshot carries no valued positions")
    weights = {symbol: value / total for symbol, value in values.items()}
    # Dust below 1 bp of the book rounds away from the comparison.
    weights = {symbol: weight for symbol, weight in weights.items() if weight > 1e-4}
    scale = sum(weights.values())
    return {symbol: weight / scale for symbol, weight in weights.items()}, total


def _risk_inputs(closes_dir: Path | None, policy, symbols) -> Any | None:
    if closes_dir is None or not closes_dir.is_dir():
        return None
    closes: dict[str, list[float]] = {}
    # Series files sanitize ":" to "-" (see research/dataset.py).
    for path in sorted(closes_dir.glob("binance-*-1D.json")):
        parts = path.name.split("-")
        if len(parts) < 3:
            continue
        symbol = parts[1]
        if symbol not in symbols:
            continue
        try:
            series = OHLCVSeries.from_mapping(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        candles = series.completed_candles()
        if len(candles) < 30:
            continue
        # Point-in-time tail only: the freshest 200 completed candles.
        closes[symbol] = [float(candle.close) for candle in candles[-200:]]
    if not closes:
        return None
    config = policy.risk_engine["portfolio_risk"]
    try:
        return build_portfolio_risk_inputs_from_closes(
            closes,
            window_weights=config["volatility_window_weights"],
            correlation_window_days=int(config["correlation_window_days"]),
            annualization_days=int(config["annualization_days"]),
            minimum_history_days=int(config["minimum_history_days"]),
        )
    except ValueError:
        return None


def _v23_policy(**overrides) -> Any:
    data = json.loads(json.dumps(load_policy().as_dict()))
    data["risk_engine"]["mode"] = "volatility_budget"
    data["core_allocation"]["mode"] = "btc_baseline_with_active_tilts"
    for symbol, entry in overrides.get("satellite_alpha", {}).items():
        data["satellite_alpha"][symbol].update(entry)
    if "eth" in overrides:
        data["core_allocation"]["eth"].update(overrides["eth"])
    return policy_from_mapping(data)


def _band(delta_pp: float) -> str:
    magnitude = abs(delta_pp)
    if magnitude < 2:
        return "HOLD"
    if magnitude < 4:
        return "WATCH"
    if magnitude < 8:
        return "ELIGIBLE"
    return "HIGH_PRIORITY"


def _shadow_target(policy, *, regime, assessments, weights, value, drawdown,
                   risk_inputs, alpha_states) -> dict[str, Any]:
    allocation = build_target_allocation(
        policy=policy, regime=regime, assessments=assessments,
        current_weights=weights, portfolio_drawdown=drawdown,
        risk_inputs=risk_inputs, eth_alpha_state=None,
        satellite_alpha_states=alpha_states or None,
    )
    return {
        "target_weights": dict(allocation.target_weights),
        "stable_target": allocation.stable_sleeve_target,
        "risk_engine": allocation.risk_engine,
        "allowances": {
            symbol: {
                key: value for key, value in details.items() if key in (
                    "eligibility_state", "conviction_state", "alpha_authority",
                    "risk_envelope_weight", "requested_strategic_weight",
                    "strategic_target_weight", "preserve_existing",
                )
            }
            for symbol, details in (allocation.deployment_allowances or {}).items()
        },
        "reasons": list(allocation.allocation_reasons),
        "value_usd": value,
    }


def command(args: argparse.Namespace) -> None:
    root = Path(args.runtime_root) if args.runtime_root else runtime_data_dir()
    snapshot = _latest_snapshot(root)
    decision = _latest_decision(root)
    weights, value = _snapshot_weights(snapshot)
    assessments = decision.get("factor_scores") or {}
    regime_name = str(decision.get("market_regime") or "NORMAL").upper()
    nav = decision.get("nav_performance") or {}
    drawdown = float(nav.get("current_drawdown") or 0.0)
    regime = determine_regime(
        RegimeInputs(portfolio_drawdown_band=drawdown),
        policy=load_policy(), include_portfolio_drawdown=False,
    ).regime if regime_name not in {"NORMAL", "DEFENSIVE", "CAPITAL_PRESERVATION"} else regime_name

    closes_dir = Path(args.risk_closes) if args.risk_closes else None
    canonical = load_policy()
    risk_inputs = _risk_inputs(closes_dir, canonical, set(weights) - {
        symbol for symbol in weights if canonical.classify(symbol) in {"stablecoin", "cash"}
    })

    variants: dict[str, dict[str, Any]] = {}
    variants["legacy_canonical"] = {
        "policy": canonical, "alpha_states": None,
        "risk_inputs": None, "note": "risk_engine.mode=legacy_drawdown (live today)",
    }
    variants["v23_locked"] = {
        "policy": _v23_policy(), "alpha_states": None,
        "risk_inputs": risk_inputs,
        "note": "V2.3 volatility budget with every tilt locked (no admitted alpha)",
    }
    if args.v23_registry:
        registry = json.loads(Path(args.v23_registry).read_text(encoding="utf-8"))
        summary = registry.get("alpha_registry_summary") or {}
        satellite_overrides: dict[str, dict[str, Any]] = {}
        for symbol, block in summary.items():
            if symbol in ("BNB", "AAVE") and block.get("production_tilt_unlocked"):
                satellite_overrides[symbol] = {"tilt_enabled": True}
        eth_unlocked = bool(summary.get("ETH", {}).get("production_tilt_unlocked"))
        variants["v23_validated"] = {
            "policy": _v23_policy(
                satellite_alpha=satellite_overrides,
                **({"eth": {"tilt_enabled": True}} if eth_unlocked else {}),
            ),
            "alpha_states": None, "risk_inputs": risk_inputs,
            "note": "V2.3 with the accepted registry's tilt authority unlocked; "
                    "live alpha states still required before any tilt engages",
        }
    manual_states: dict[str, str] = {}
    for item in args.alpha_state or []:
        symbol, _, state = item.partition("=")
        manual_states[symbol.strip().upper()] = state.strip().upper()
    if manual_states:
        variants["v23_scenario"] = {
            "policy": _v23_policy(**({
                "satellite_alpha": {
                    symbol: {"tilt_enabled": True} for symbol in manual_states
                    if symbol in ("BNB", "AAVE")
                },
                **({"eth": {"tilt_enabled": True}} if "ETH" in manual_states else {}),
            })),
            "alpha_states": manual_states, "risk_inputs": risk_inputs,
            "note": "scenario dry-run with manually injected alpha states",
        }

    results: dict[str, Any] = {}
    for name, variant in variants.items():
        if variant["policy"].risk_engine.get("mode") == "volatility_budget" \
                and variant["risk_inputs"] is None:
            results[name] = {
                "status": "UNAVAILABLE",
                "reason": "volatility-budget shadow needs daily closes: pass "
                          "--risk-closes <dataset>/series",
                "note": variant["note"],
            }
            continue
        results[name] = {
            "status": "AVAILABLE", "note": variant["note"],
            **_shadow_target(
                variant["policy"], regime=regime, assessments=assessments,
                weights=weights, value=value, drawdown=drawdown,
                risk_inputs=variant["risk_inputs"],
                alpha_states=variant["alpha_states"],
            ),
        }

    # Transition table: per-asset deltas and bands against every variant.
    staging = canonical.rebalance.get("staging", {})
    max_step_pp = float(staging.get("max_step_pp", 4.0))
    for name, block in results.items():
        if block.get("status") != "AVAILABLE":
            continue
        rows: dict[str, Any] = {}
        target = block["target_weights"]
        for symbol in sorted(set(weights) | set(target)):
            current = weights.get(symbol, 0.0)
            goal = target.get(symbol, 0.0)
            delta_pp = (goal - current) * 100.0
            rows[symbol] = {
                "current_weight": current,
                "target_weight": goal,
                "delta_pp": delta_pp,
                "band": _band(delta_pp),
                "reviews_to_converge_at_max_step": (
                    int(abs(delta_pp) / max_step_pp) + (1 if abs(delta_pp) % max_step_pp else 0)
                    if abs(delta_pp) > 1e-9 else 0
                ),
                "amount_usd": abs(delta_pp) / 100.0 * value,
            }
        block["transition"] = rows

    # Open-order reconciliation against the tightest available V2.3 target.
    reference = next(
        (results[name] for name in ("v23_scenario", "v23_validated", "v23_locked")
         if name in results and results[name].get("status") == "AVAILABLE"),
        None,
    )
    open_orders: dict[str, Any] = {}
    orders_path = root / "orders" / "open-orders.json"
    if orders_path.is_file() and reference is not None:
        book = json.loads(orders_path.read_text(encoding="utf-8"))
        target = reference["target_weights"]
        for symbol, orders in (book.get("orders_by_symbol") or {}).items():
            name = str(symbol).upper()
            delta_pp = (target.get(name, 0.0) - weights.get(name, 0.0)) * 100.0
            open_orders[name] = {
                "resting_orders": len(orders),
                "target_delta_pp": delta_pp,
                "alignment": (
                    "SUPPORTS_TARGET" if delta_pp > 2
                    else "CONFLICTS_WITH_TARGET" if delta_pp < -2 else "NEUTRAL"
                ),
            }

    output = {
        "contract": "V23_SHADOW_ALLOCATION",
        "read_only": True,
        "snapshot": {
            "snapshot_id": snapshot.get("snapshot_id"),
            "timestamp": snapshot.get("timestamp"),
            "total_value_usd": value,
            "weights": weights,
        },
        "decision": {
            "decision_id": decision.get("decision_id"),
            "timestamp": decision.get("timestamp"),
            "market_regime": regime,
            "current_drawdown": drawdown,
        },
        "risk_inputs_available": risk_inputs is not None,
        "variants": results,
        "open_order_reconciliation": open_orders,
        "gate": (
            "this is a shadow dry-run: the canonical risk_engine.mode switch "
            "stays a gated human decision (plan 11.2)"
        ),
    }
    destination = Path(args.output) if args.output else (
        root / "reports" / "v23-shadow-allocation.json"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(destination),
        "variants": {name: block.get("status") for name, block in results.items()},
        "snapshot_total_usd": round(value, 2),
        "market_regime": regime,
        "risk_inputs_available": risk_inputs is not None,
    }, ensure_ascii=False, indent=2))


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runtime-root", default=None,
        help="runtime data root (defaults to the configured runtime dir)",
    )
    parser.add_argument(
        "--risk-closes", default=None,
        help="dataset series directory with binance:*:1D.json closes for the "
             "volatility-budget shadow (e.g. <dataset>/series)",
    )
    parser.add_argument(
        "--v23-registry", default=None,
        help="path to v23-validation.json whose accepted registry unlocks tilts",
    )
    parser.add_argument(
        "--alpha-state", action="append", default=[],
        metavar="SYMBOL=STATE",
        help="scenario dry-run: inject an alpha state, e.g. BNB=BNB_ALPHA_POSITIVE",
    )
    parser.add_argument("--output", default=None)
    args = parser.parse_args(argv)
    command(args)


if __name__ == "__main__":
    main()
