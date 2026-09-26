"""Risk-authority separation between regime and emergency overlay (Phase A).

Case D: one portfolio drawdown may not act twice. In volatility-budget mode
the drawdown changes only the emergency overlay state; in legacy mode it still
moves the regime floor, exactly as before. The stable floor in the
volatility-budget engine is the policy minimum, not the regime stable target.
"""

import json
import unittest

from crypto_portfolio.engine.allocation import build_target_allocation
from crypto_portfolio.engine.portfolio_risk import PortfolioRiskInputs
from crypto_portfolio.engine.regime import RegimeInputs, determine_regime
from crypto_portfolio.engine.risk import run_risk_gate, risk_overlay_floor
from crypto_portfolio.models.policy import load_policy, policy_from_mapping


def _policy(mode="volatility_budget"):
    data = json.loads(json.dumps(load_policy().as_dict()))
    data["risk_engine"]["mode"] = mode
    return policy_from_mapping(data)


def _inputs():
    return PortfolioRiskInputs(
        asset_volatility={"BTC": 0.6, "ETH": 0.7},
        correlations={"BTC": {"ETH": 0.85}, "ETH": {"BTC": 0.85}},
    )


_MARKET_CALM = {
    "btc_trend": "BULLISH",
    "volatility_state": "NORMAL",
    "flow_state": "POSITIVE",
    "breadth_state": "HEALTHY",
    "systemic_event_risk": False,
}

_ASSESSMENTS = {
    "BTC": {"weighted_score": 70, "confidence": "HIGH"},
    "ETH": {
        "weighted_score": 60, "confidence": "HIGH",
        "relative_strength_vs_btc": "OUTPERFORM",
    },
}


class SingleDrawdownSingleAuthorityTests(unittest.TestCase):
    def test_case_d_volatility_budget_moves_only_the_emergency_state(self):
        policy = _policy()
        inputs = RegimeInputs(portfolio_drawdown_band=-0.14, **_MARKET_CALM)
        calm_regime = determine_regime(
            RegimeInputs(portfolio_drawdown_band=0.0, **_MARKET_CALM),
            policy=policy,
            include_portfolio_drawdown=False,
        )
        stressed_regime = determine_regime(
            inputs, policy=policy, include_portfolio_drawdown=False
        )
        self.assertEqual(calm_regime.regime, stressed_regime.regime)
        _, _, calm_state = risk_overlay_floor(policy, 0.0, 0)
        _, _, stressed_state = risk_overlay_floor(policy, -0.14, 0)
        self.assertEqual(calm_state["state"], "NORMAL")
        # -14% of a 15% budget is 93% consumed: EMERGENCY ladder territory.
        self.assertEqual(stressed_state["state"], "EMERGENCY")
        self.assertLess(stressed_state["risky_cap"], calm_state["risky_cap"])

    def test_case_d_legacy_mode_still_moves_both_layers(self):
        policy = _policy("legacy_drawdown")
        calm_regime = determine_regime(
            RegimeInputs(portfolio_drawdown_band=0.0, **_MARKET_CALM), policy=policy
        )
        stressed_regime = determine_regime(
            RegimeInputs(portfolio_drawdown_band=-0.10, **_MARKET_CALM), policy=policy
        )
        self.assertEqual(calm_regime.regime, "NORMAL")
        self.assertEqual(stressed_regime.regime, "DEFENSIVE")
        _, _, state = risk_overlay_floor(policy, -0.10, 0)
        self.assertEqual(state["state"], "LEGACY_LADDER")


class StableFloorAuthorityTests(unittest.TestCase):
    def test_volatility_budget_stable_floor_is_the_policy_minimum(self):
        policy = _policy()
        result = build_target_allocation(
            policy=policy, regime="CAPITAL_PRESERVATION", assessments=_ASSESSMENTS,
            risk_inputs=_inputs(),
        )
        self.assertAlmostEqual(
            result.strategic_stable_target, policy.min_stablecoin_weight
        )
        # The regime stablecoin target (50% under CAPITAL_PRESERVATION) no
        # longer sets the strategic floor; only caps can raise stable weight.
        self.assertLess(
            result.strategic_stable_target,
            policy.regime("CAPITAL_PRESERVATION").stablecoin_target,
        )

    def test_legacy_stable_floor_keeps_the_regime_target(self):
        policy = _policy("legacy_drawdown")
        result = build_target_allocation(
            policy=policy, regime="CAPITAL_PRESERVATION", assessments=_ASSESSMENTS,
        )
        self.assertAlmostEqual(
            result.strategic_stable_target,
            max(
                policy.min_stablecoin_weight,
                policy.regime("CAPITAL_PRESERVATION").stablecoin_target,
            ),
        )

    def test_risk_gate_drops_regime_stable_authority_in_volatility_budget(self):
        policy = _policy()
        # 18% stable under CAPITAL_PRESERVATION (50% regime target): legal in
        # volatility-budget mode because the regime scales the vol band, but a
        # STABLECOIN_FLOOR error under legacy semantics.
        weights = {"USDT": 0.18, "BTC": 0.62, "ETH": 0.20}
        budget_gate = run_risk_gate(
            weights, policy=policy, regime="CAPITAL_PRESERVATION",
        )
        codes = {v.code for v in budget_gate.violations}
        self.assertNotIn("STABLECOIN_FLOOR", codes)

        legacy = _policy("legacy_drawdown")
        legacy_gate = run_risk_gate(
            weights, policy=legacy, regime="CAPITAL_PRESERVATION",
        )
        self.assertIn(
            "STABLECOIN_FLOOR", {v.code for v in legacy_gate.violations}
        )

    def test_risk_gate_keeps_minimum_and_emergency_floors(self):
        policy = _policy()
        below_minimum = {"USDT": 0.05, "BTC": 0.75, "ETH": 0.20}
        gate = run_risk_gate(
            below_minimum, policy=policy, regime="NORMAL",
        )
        self.assertIn("STABLECOIN_FLOOR", {v.code for v in gate.violations})
        _, _, emergency = risk_overlay_floor(policy, -0.14, 0)
        floored = run_risk_gate(
            {"USDT": 0.30, "BTC": 0.50, "ETH": 0.20},
            policy=policy, regime="NORMAL", current_drawdown=-0.14,
        )
        # 30% stable against an emergency floor of 40% (60% risky cap) fails.
        self.assertIn("STABLECOIN_FLOOR", {v.code for v in floored.violations})
        self.assertEqual(emergency["state"], "EMERGENCY")


class ReplayDiagnosticContractTests(unittest.TestCase):
    def test_orchestrator_reports_the_authority_flag(self):
        from datetime import datetime, timedelta, timezone

        from crypto_portfolio.models.market import Candle, OHLCVSeries
        from crypto_portfolio.research.historical_builder import build_historical_reviews
        from crypto_portfolio.research.orchestrator import (
            build_risk_inputs_for_reviews,
            run_historical_backtest,
        )

        policy = _policy()
        daily_start = datetime(2023, 5, 1, tzinfo=timezone.utc)
        daily = {}
        for symbol, base in (("BTC", 30_000), ("ETH", 2_000)):
            daily[symbol] = OHLCVSeries(
                symbol, "1D",
                tuple(
                    Candle(
                        (daily_start + timedelta(days=index)).isoformat(),
                        base + index, base + index + 10, base + index - 10, base + index, 100,
                    )
                    for index in range(255)
                ),
                "test", "2026-09-26T00:00:00Z", "TEST", "spot", "USD",
            )
        reviews = build_historical_reviews(
            daily_by_symbol=daily, execution_by_symbol=daily, execution_timeframe="1D",
            symbols=("BTC", "ETH", "USD"), initial_weights={"USD": 1.0},
            initial_value=100_000, start_at="2024-01-01T00:00:00Z",
            end_at="2024-01-04T00:00:00Z", policy=policy, semantic_score=None,
        )
        risk_inputs = build_risk_inputs_for_reviews(
            reviews, daily_by_symbol=daily, policy=policy
        )
        result = run_historical_backtest(
            reviews, policy=policy, fee_bps=10, slippage_bps=5,
            risk_inputs_by_review=risk_inputs,
        )
        separation = result["risk_authority_separation"]
        self.assertEqual(separation["drawdown_influenced_regime"], "NO")
        self.assertIn("NORMAL", separation["market_only_regime_counts"])
        self.assertGreater(separation["effective_target_volatility"]["reviews"], 0)
        self.assertAlmostEqual(
            separation["effective_target_volatility"]["max"], 0.25
        )
        # The legacy engine on identical reviews still declares YES.
        legacy = _policy("legacy_drawdown")
        legacy_reviews = build_historical_reviews(
            daily_by_symbol=daily, execution_by_symbol=daily, execution_timeframe="1D",
            symbols=("BTC", "ETH", "USD"), initial_weights={"USD": 1.0},
            initial_value=100_000, start_at="2024-01-01T00:00:00Z",
            end_at="2024-01-04T00:00:00Z", policy=legacy, semantic_score=None,
        )
        legacy_result = run_historical_backtest(legacy_reviews, policy=legacy)
        self.assertEqual(
            legacy_result["risk_authority_separation"]["drawdown_influenced_regime"], "YES"
        )


if __name__ == "__main__":
    unittest.main()
