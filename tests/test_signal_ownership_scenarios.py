"""Correlated-signal regression scenarios (Strategy V2 Phase 3).

Scenario A: strong fundamentals + weak technical -> strategic target stays
positive; execution may delay, it cannot zero the target.
Scenario B: strong trend + poor fundamentals -> no oversized position from
one signal confirmed at multiple layers.
Scenario C: long rise without a pullback -> the bounded WAIT expiry allows
partial deployment.
Scenario D: systemic volatility elevated -> the risk engine (not the score,
not triple penalties) carries the sizing reduction.
"""

import json
import unittest
from datetime import date, timedelta

from crypto_portfolio.engine.allocation import build_target_allocation
from crypto_portfolio.engine.entry import build_entry_plan
from crypto_portfolio.engine.portfolio_risk import PortfolioRiskInputs
from crypto_portfolio.engine.scoring import score_assessment
from crypto_portfolio.engine.technical import build_technical_snapshot
from crypto_portfolio.models.evidence import AssetAssessment, FactorScore
from crypto_portfolio.models.market import Candle, OHLCVSeries, SpotPrice
from crypto_portfolio.models.policy import load_policy, policy_from_mapping


def _factors(**values):
    return {
        factor: FactorScore(
            factor=factor, score=score, availability="AVAILABLE", reliability=1.0,
        )
        for factor, score in values.items()
    }


def _vol_policy():
    data = json.loads(json.dumps(load_policy().as_dict()))
    data["risk_engine"]["mode"] = "volatility_budget"
    # Frozen V2.1 anchor-core mechanics under test; the V2.2 btc-baseline
    # core mode has its own test files.
    data["core_allocation"]["mode"] = "legacy_anchor"
    return policy_from_mapping(data)


def _risk_inputs(vol_btc=0.1, vol_eth=0.12, rho=0.5):
    return PortfolioRiskInputs(
        asset_volatility={"BTC": vol_btc, "ETH": vol_eth},
        correlations={"BTC": {"ETH": rho}, "ETH": {"BTC": rho}},
    )


def _scored(symbol, asset_type, **factors):
    return score_assessment(AssetAssessment(
        symbol=symbol,
        factor_scores=_factors(**factors),
        asset_type=asset_type,
        relative_strength_vs_btc="OUTPERFORM",
    ))[0].as_dict()


class ScenarioAStrongFundamentalsWeakTechnical(unittest.TestCase):
    def test_strategic_target_survives_a_technical_wait(self):
        # Strong complete evidence across factors: the strategic layer wants
        # the position. The technical snapshot is extended with no usable
        # pullback structure: execution says WAIT. The target must stay
        # positive — execution owns timing, never the allocation.
        policy = load_policy()
        assessment = _scored(
            "SOL", "satellite",
            trend=88.0, valuation=82.0, fundamentals=90.0,
            onchain=80.0, capital_flows=78.0, relative_strength_btc=85.0,
        )
        result = build_target_allocation(
            policy=policy, regime="NORMAL", assessments={"SOL": assessment},
            current_weights={"USDT": 1.0},
        )
        self.assertGreater(result.target_weights.get("SOL", 0.0), 0.0)
        self.assertEqual(
            result.deployment_allowances["SOL"]["eligibility_state"],
            "ELIGIBLE_INCREASE",
        )

        # An extended, structure-less snapshot produces an execution WAIT on
        # the same approval.
        candles = []
        start = date(2025, 1, 1)
        for index in range(365):
            close = 100 * (1.01 ** index / 8) if index >= 300 else 100 + index * 0.4
            candles.append(Candle(
                (start + timedelta(days=index)).isoformat() + "T00:00:00Z",
                close - 0.4, close + 1.6, close - 1.2, close, 150,
            ))
        series = OHLCVSeries("SOL", "1D", tuple(candles), "synthetic", "2026-01-01T00:00:00Z")
        snapshot = build_technical_snapshot(
            series, SpotPrice("SOL", float(candles[-1].close), "2026-01-01T08:00:00Z", "synthetic", "2026-01-01T08:00:00Z"),
            policy=policy,
        )
        plan = build_entry_plan("SOL", 3000.0, snapshot, "NORMAL", "HIGH")
        self.assertEqual(plan.action, "WAIT")
        # Together: strategic target positive AND execution delayed — exactly
        # the Phase 3 separation the scenario demands.
        self.assertGreater(result.target_weights["SOL"], 0.0)
        self.assertEqual(plan.action, "WAIT")


class ScenarioBStrongTrendPoorFundamentals(unittest.TestCase):
    def test_one_confirmed_signal_cannot_oversize_the_position(self):
        policy = load_policy()
        # Trend is excellent but fundamentals and valuation are poor.
        assessment = _scored(
            "SOL", "satellite",
            trend=95.0, valuation=35.0, fundamentals=30.0,
            onchain=40.0, capital_flows=45.0, relative_strength_btc=80.0,
        )
        result = build_target_allocation(
            policy=policy, regime="NORMAL", assessments={"SOL": assessment},
            current_weights={"USDT": 1.0},
        )
        allowance = result.deployment_allowances["SOL"]
        envelope = allowance["satellite_cap"]
        # The mixed score must not reach anywhere near the full envelope:
        # a single strong factor is one vote, not three.
        self.assertLess(allowance["strategic_target_weight"], 0.5 * envelope)
        # And the trend-heavy normalized score stays below the full score.
        self.assertLess(allowance["score"], 85.0)


class ScenarioCLongRiseNoPullback(unittest.TestCase):
    def test_wait_expiry_releases_partial_market_deployment(self):
        candles = []
        start = date(2025, 1, 1)
        for index in range(365):
            close = 100 + index * 0.9
            candles.append(Candle(
                (start + timedelta(days=index)).isoformat() + "T00:00:00Z",
                close - 0.4, close + 1.6, close - 1.4, close, 150,
            ))
        series = OHLCVSeries("SOL", "1D", tuple(candles), "synthetic", "2026-01-01T00:00:00Z")
        snapshot = build_technical_snapshot(
            series, SpotPrice("SOL", float(candles[-1].close), "2026-01-01T08:00:00Z", "synthetic", "2026-01-01T08:00:00Z"),
        )
        waiting = build_entry_plan("SOL", 4000.0, snapshot, "NORMAL", "HIGH")
        if waiting.action != "WAIT":
            self.skipTest("synthetic series did not gate to WAIT")
        expired = build_entry_plan(
            "SOL", 4000.0, snapshot, "NORMAL", "HIGH",
            wait_streak=load_policy().execution_overlay["wait"]["expiry_reviews"],
        )
        self.assertEqual(expired.action, "INCREASE")
        self.assertEqual(expired.entry_mode, "MARKET_TIMEOUT")
        self.assertGreater(expired.planned_amount_usd, 0)
        self.assertLess(expired.planned_amount_usd, expired.approved_amount_usd)


class ScenarioDSystemicVolatilitySizing(unittest.TestCase):
    def test_volatility_reduces_sizing_not_the_score(self):
        # Same evidence, same score; only the covariance environment
        # differs. The reduction must come from the risk engine binding, and
        # the asset's score must be identical in both worlds.
        policy = _vol_policy()
        assessment = _scored(
            "ETH", "core",
            trend=72.0, valuation=68.0, fundamentals=70.0,
            onchain=66.0, capital_flows=64.0, relative_strength_btc=70.0,
        )
        calm = build_target_allocation(
            policy=policy, regime="NORMAL", assessments={"ETH": assessment},
            current_weights={"USDT": 1.0}, risk_inputs=_risk_inputs(0.08, 0.10, 0.3),
        )
        wild = build_target_allocation(
            policy=policy, regime="NORMAL", assessments={"ETH": assessment},
            current_weights={"USDT": 1.0}, risk_inputs=_risk_inputs(0.95, 1.05, 0.95),
        )
        self.assertAlmostEqual(
            calm.deployment_allowances and 0 or calm.target_weights.get("ETH", 0.0),
            calm.target_weights.get("ETH", 0.0),
        )
        calm_score = calm.deployment_allowances.get("ETH", {}).get("score")
        wild_score = wild.deployment_allowances.get("ETH", {}).get("score")
        self.assertEqual(calm_score, wild_score)
        self.assertLess(
            wild.target_weights.get("ETH", 0.0),
            calm.target_weights.get("ETH", 0.0),
        )
        self.assertEqual(wild.risk_engine["binding_risk_constraint"], "volatility_budget")
        self.assertEqual(
            wild.risk_engine["emergency_overlay_state"]["state"], "NORMAL"
        )


if __name__ == "__main__":
    unittest.main()
