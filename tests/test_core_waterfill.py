"""Core water-fill regressions (phase 3).

Unused core budget must be offered to still-eligible core assets before it is
converted to stablecoins.  The 2026-09-14 review left ~3.6pp of core budget in
the stable sleeve (18.62% effective stable vs the 15% strategic target) while
ETH still had core capacity.
"""

import unittest

from crypto_portfolio.engine.allocation import build_target_allocation

BTC = {"weighted_score": 65.52, "confidence": "HIGH"}
ETH = {"weighted_score": 83.78, "confidence": "HIGH", "relative_strength_vs_btc": 70}


class CoreWaterFillTests(unittest.TestCase):
    def test_residual_core_budget_water_fills_to_eligible_eth_before_stable(self):
        # BTC is capped at 50% while ETH still has core capacity (desired
        # ~20% against a 30% sleeve cap): the residual core budget must fill
        # ETH, so effective stable stays at the strategic 15% target instead
        # of absorbing the unused core budget.  The held SOL position has
        # incomplete evidence and preserves its current 10% in every phase,
        # which shrinks the core budget below the both-capped regime.
        result = build_target_allocation(
            regime="NORMAL",
            assessments={
                "BTC": {"weighted_score": 80, "confidence": "HIGH"},
                "ETH": {"weighted_score": 60, "confidence": "HIGH", "relative_strength_vs_btc": 70},
                "SOL": {
                    "weighted_score": 50, "confidence": "LOW",
                    "critical_data_complete": False, "relative_strength_vs_btc": None,
                },
            },
            current_weights={"BTC": 0.40, "ETH": 0.20, "SOL": 0.10, "USDT": 0.30},
        )
        weights = result.target_weights
        self.assertAlmostEqual(weights["BTC"], 0.50, places=6)
        self.assertAlmostEqual(weights["ETH"], 0.25, places=6)
        self.assertAlmostEqual(weights["SOL"], 0.10, places=6)
        stable = sum(weight for symbol, weight in weights.items() if symbol not in {"BTC", "ETH", "SOL"})
        self.assertAlmostEqual(stable, 0.15, places=6)

    def test_residual_becomes_stable_only_when_every_core_asset_is_capped(self):
        # BTC capped at 50%, ETH capped by max_core_sleeve_share (40% of the
        # 85% core budget = 34%): with both caps binding the leftover core
        # budget lands in the stable sleeve.
        result = build_target_allocation(
            regime="NORMAL",
            assessments={"BTC": dict(BTC), "ETH": dict(ETH)},
            current_weights={"BTC": 0.40, "ETH": 0.30, "USDT": 0.30},
        )
        weights = result.target_weights
        self.assertAlmostEqual(weights["BTC"], 0.50, places=6)
        self.assertAlmostEqual(weights["ETH"], 0.34, places=6)
        stable = sum(weight for symbol, weight in weights.items() if symbol not in {"BTC", "ETH"})
        self.assertAlmostEqual(stable, 0.16, places=6)

    def test_gated_core_assets_never_absorb_redistributed_budget(self):
        # ETH on LOW confidence is HOLD_ONLY: its raw proportion shrinks by
        # the documented core confidence multiplier (8B keeps this), and the
        # gated sleeve must not grow back by absorbing redistributed budget.
        # BTC is capped at 50%, so the residual lands in the stable sleeve.
        result = build_target_allocation(
            regime="NORMAL",
            assessments={"BTC": dict(BTC), "ETH": {**ETH, "confidence": "LOW"}},
            current_weights={"BTC": 0.40, "ETH": 0.20, "USDT": 0.40},
        )
        weights = result.target_weights
        self.assertAlmostEqual(weights["BTC"], 0.50, places=6)
        self.assertAlmostEqual(weights["ETH"], 0.093825, places=5)
        stable = sum(weight for symbol, weight in weights.items() if symbol not in {"BTC", "ETH"})
        self.assertAlmostEqual(stable, 0.406175, places=5)


if __name__ == "__main__":
    unittest.main()
