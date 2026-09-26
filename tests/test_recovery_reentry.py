"""Staged re-entry through the allocation path (Strategy V2.1 Phase B).

Case D: a book whose NAV sits far below its old high but whose market regime
has recovered must be allowed staged re-entry — the recovery block bounds the
risky sleeve at the stage caps instead of pinning it at the breach cap until
NAV heals. End-to-end, the strict replay carries the FSM and emits the Phase
B diagnostics block.
"""

import json
import unittest
from datetime import datetime, timedelta, timezone

from crypto_portfolio.engine.allocation import build_target_allocation
from crypto_portfolio.engine.portfolio_risk import PortfolioRiskInputs
from crypto_portfolio.engine.risk_recovery import (
    RecoveryState,
    advance_emergency_recovery,
)
from crypto_portfolio.models.market import Candle, OHLCVSeries
from crypto_portfolio.models.policy import load_policy, policy_from_mapping
from crypto_portfolio.research.historical_builder import build_historical_reviews
from crypto_portfolio.research.orchestrator import (
    build_risk_inputs_for_reviews,
    run_historical_backtest,
)


def _policy():
    data = json.loads(json.dumps(load_policy().as_dict()))
    data["risk_engine"]["mode"] = "volatility_budget"
    # V2.3 stress-loss budget disabled: these files pin the emergency/
    # recovery mechanics in isolation; the combined caps have their own tests.
    data["risk"]["stress_loss_budget"]["enabled"] = False
    return policy_from_mapping(data)


def _inputs():
    return PortfolioRiskInputs(
        asset_volatility={"BTC": 0.6, "ETH": 0.7},
        correlations={"BTC": {"ETH": 0.85}, "ETH": {"BTC": 0.85}},
    )


_ASSESSMENTS = {
    "BTC": {"weighted_score": 70, "normalized_score": 70, "confidence": "HIGH"},
    "ETH": {
        "weighted_score": 60, "normalized_score": 60, "confidence": "HIGH",
        "relative_strength_vs_btc": "OUTPERFORM",
    },
}


def _recovery_block(state, reviews, policy):
    for _ in range(reviews):
        state, block = advance_emergency_recovery(
            state, portfolio_drawdown=-0.18, market_only_regime="NORMAL",
            portfolio_volatility=0.10, policy=policy,
        )
    return block


class StagedReentryTests(unittest.TestCase):
    def setUp(self):
        self.policy = _policy()

    def test_case_d_deep_drawdown_with_recovered_market_re_risks_in_stages(self):
        breach = _recovery_block(RecoveryState(), 3, self.policy)
        stage_1 = _recovery_block(RecoveryState(), 6, self.policy)
        stage_2 = _recovery_block(RecoveryState(), 11, self.policy)
        self.assertEqual(breach["state"], "BREACH")
        self.assertEqual(stage_1["state"], "RECOVERY_1")
        self.assertEqual(stage_2["state"], "RECOVERY_2")

        def _risky(block):
            result = build_target_allocation(
                policy=self.policy, regime="NORMAL", assessments=_ASSESSMENTS,
                portfolio_drawdown=-0.18, risk_inputs=_inputs(),
                recovery_state=block,
            )
            return 1.0 - result.stable_sleeve_target, result

        breach_risky, breach_result = _risky(breach)
        stage1_risky, _ = _risky(stage_1)
        stage2_risky, _ = _risky(stage_2)
        self.assertLessEqual(breach_risky, 0.25 + 1e-9)
        self.assertGreater(stage1_risky, breach_risky)
        self.assertGreater(stage2_risky, stage1_risky)
        self.assertEqual(
            breach_result.risk_engine["emergency_overlay_state"]["state"], "BREACH"
        )

    def test_recovery_block_supersedes_the_streak_floor(self):
        stage_1 = _recovery_block(RecoveryState(), 6, self.policy)
        result = build_target_allocation(
            policy=self.policy, regime="NORMAL", assessments=_ASSESSMENTS,
            portfolio_drawdown=-0.18, risk_inputs=_inputs(),
            market_recovery_streak=99, recovery_state=stage_1,
        )
        self.assertEqual(
            result.risk_engine["emergency_overlay_state"]["state"], "RECOVERY_1"
        )


class ReplayIntegrationTests(unittest.TestCase):
    def test_strict_replay_carries_the_fsm_and_emits_diagnostics(self):
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
            end_at="2024-01-10T00:00:00Z", policy=policy, semantic_score=None,
        )
        risk_inputs = build_risk_inputs_for_reviews(
            reviews, daily_by_symbol=daily, policy=policy
        )
        result = run_historical_backtest(
            reviews, policy=policy, fee_bps=10, slippage_bps=5,
            risk_inputs_by_review=risk_inputs,
        )
        diagnostics = result["emergency_recovery_diagnostics"]
        self.assertIsNotNone(diagnostics)
        self.assertEqual(diagnostics["reviews"], len(reviews))
        self.assertIn("recovery_efficiency", diagnostics)
        self.assertIn("breach_duration", diagnostics)
        self.assertIn("state_transition_counts", diagnostics)


if __name__ == "__main__":
    unittest.main()
