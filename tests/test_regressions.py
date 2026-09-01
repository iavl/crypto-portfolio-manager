import json
import unittest
from pathlib import Path

from crypto_portfolio.engine.allocation import build_target_allocation
from crypto_portfolio.engine.ledger import PortfolioSnapshot, cash_flow_adjusted_return
from crypto_portfolio.engine.rebalance import recommend_rebalance
from crypto_portfolio.engine.regime import RegimeInputs, determine_regime
from crypto_portfolio.engine.risk import run_risk_gate
from crypto_portfolio.engine.scoring import score_factors
from crypto_portfolio.models.portfolio import normalize_snapshot


FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class RegressionFixtureTests(unittest.TestCase):
    def test_regime_and_allocation_envelopes(self):
        normal = fixture("normal_market.json")
        defensive = fixture("defensive_market.json")
        capital = fixture("capital_preservation.json")
        self.assertEqual(determine_regime(RegimeInputs(**normal["regime_inputs"])).regime, "NORMAL")
        self.assertEqual(determine_regime(RegimeInputs(**defensive["regime_inputs"])).regime, "DEFENSIVE")
        self.assertEqual(determine_regime(RegimeInputs(**capital["regime_inputs"])).regime, "CAPITAL_PRESERVATION")
        normal_target = build_target_allocation(regime="NORMAL", assessments=normal["assessments"])
        capital_target = build_target_allocation(regime="CAPITAL_PRESERVATION", assessments=capital["assessments"])
        self.assertGreaterEqual(normal_target.target_weights["USDT"], 0.10)
        self.assertGreaterEqual(capital_target.target_weights["USDT"], 0.50)
        self.assertLessEqual(capital_target.target_weights.get("SOL", 0), 0.05)

    def test_cash_flow_fixtures_are_neutral(self):
        for name in ("deposit_no_market_move.json", "withdrawal_no_market_move.json"):
            snapshots = [PortfolioSnapshot(**item) for item in fixture(name)["snapshots"]]
            self.assertAlmostEqual(cash_flow_adjusted_return(snapshots), 0.0)

    def test_btc_overweight_and_satellite_concentration(self):
        overweight = fixture("btc_overweight.json")
        action = recommend_rebalance(
            overweight["current_weights"], overweight["target_weights"], overweight["portfolio_value"]
        )
        self.assertEqual(action[0].action, "REDUCE")
        concentrated = fixture("satellite_concentration.json")
        result = run_risk_gate(concentrated["target_weights"], regime=concentrated["regime"])
        self.assertIn("SATELLITE_CAP", {item.code for item in result.violations})

    def test_stable_floor_and_missing_factor_fixtures(self):
        result = normalize_snapshot(fixture("stablecoin_below_floor.json"))
        self.assertTrue(any("below configured minimum" in warning for warning in result["warnings"]))
        missing = fixture("missing_factor_data.json")
        score = score_factors(missing["factor_scores"], confidence=missing["confidence"])
        self.assertEqual(score.missing_factors, ("fundamentals", "onchain", "capital_flows", "relative_strength_btc", "event_risk"))
        self.assertEqual(score.confidence, "LOW")

    def test_thesis_failure_fixture_exits(self):
        data = fixture("thesis_failure.json")
        actions = recommend_rebalance(
            data["current_weights"],
            data["target_weights"],
            data["portfolio_value"],
            thesis_broken=data["thesis_broken"],
        )
        self.assertEqual(next(item for item in actions if item.symbol == "SOL").action, "EXIT")


if __name__ == "__main__":
    unittest.main()
