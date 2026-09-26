"""ETH optional active tilt (Strategy V2.2 Phases A and B).

ETH allocation authority is the ETH/BTC relative-alpha state, never a fixed
anchor share. These tests pin plan Case B (explicit tilt) and Case D (ETH
disabled) plus the tilt gating, sleeve caps, and validation rules.
"""

import json
import unittest

from crypto_portfolio.engine.allocation import _allocate_core
from crypto_portfolio.models.policy import load_policy, policy_from_mapping


def _policy(**eth):
    data = json.loads(json.dumps(load_policy().as_dict()))
    data["risk_engine"]["mode"] = "volatility_budget"
    data["core_allocation"]["mode"] = "btc_baseline_with_active_tilts"
    data["core_allocation"]["eth"].update(eth)
    return policy_from_mapping(data)


_BTC_STRONG = {"weighted_score": 75, "normalized_score": 75, "confidence": "HIGH"}
_ETH_ELIGIBLE = {
    "weighted_score": 70, "normalized_score": 70, "confidence": "HIGH",
    "relative_strength_vs_btc": 60,
}


class EthOptionalTiltTests(unittest.TestCase):
    def test_case_b_positive_alpha_tilts_the_budget_toward_eth(self):
        policy = _policy(tilt_enabled=True)
        weights, residual, _ = _allocate_core(
            policy, 0.50, {"BTC": _BTC_STRONG, "ETH": _ETH_ELIGIBLE}, {}, 0.50,
            risk_mode="volatility_budget",
            eth_alpha_state="ETH_ALPHA_POSITIVE",
        )
        # 20% of the 50% core budget goes to ETH; BTC keeps the residual.
        self.assertAlmostEqual(weights["ETH"], 0.10)
        self.assertAlmostEqual(weights["BTC"], 0.40)
        self.assertAlmostEqual(residual, 0.0)

    def test_neutral_alpha_state_earns_no_tilt(self):
        policy = _policy(tilt_enabled=True)
        weights, _, reasons = _allocate_core(
            policy, 0.50, {"BTC": _BTC_STRONG, "ETH": _ETH_ELIGIBLE}, {}, 0.50,
            risk_mode="volatility_budget",
            eth_alpha_state="ETH_ALPHA_NEUTRAL",
        )
        self.assertAlmostEqual(weights.get("ETH", 0.0), 0.0)
        self.assertAlmostEqual(weights["BTC"], 0.50)
        self.assertTrue(any("ETH_ALPHA_NEUTRAL" in reason for reason in reasons))

    def test_negative_alpha_state_earns_no_tilt(self):
        policy = _policy(tilt_enabled=True)
        weights, _, _ = _allocate_core(
            policy, 0.50, {"BTC": _BTC_STRONG, "ETH": _ETH_ELIGIBLE}, {}, 0.50,
            risk_mode="volatility_budget",
            eth_alpha_state="ETH_ALPHA_NEGATIVE",
        )
        self.assertAlmostEqual(weights.get("ETH", 0.0), 0.0)
        self.assertAlmostEqual(weights["BTC"], 0.50)

    def test_positive_alpha_requires_core_eligibility(self):
        policy = _policy(tilt_enabled=True)
        ineligible_eth = dict(_ETH_ELIGIBLE, event_risk={"state": "SEVERE"})
        weights, _, reasons = _allocate_core(
            policy, 0.50, {"BTC": _BTC_STRONG, "ETH": ineligible_eth}, {}, 0.50,
            risk_mode="volatility_budget",
            eth_alpha_state="ETH_ALPHA_POSITIVE",
        )
        self.assertAlmostEqual(weights.get("ETH", 0.0), 0.0)
        self.assertTrue(any("does not qualify" in reason for reason in reasons))

    def test_missing_relative_evidence_blocks_the_tilt(self):
        policy = _policy(tilt_enabled=True)
        missing = dict(_ETH_ELIGIBLE, relative_strength_vs_btc=None)
        weights, _, _ = _allocate_core(
            policy, 0.50, {"BTC": _BTC_STRONG, "ETH": missing}, {}, 0.50,
            risk_mode="volatility_budget",
            eth_alpha_state="ETH_ALPHA_POSITIVE",
        )
        # Fail-defensive: an unproven relative case never earns core budget.
        self.assertAlmostEqual(weights.get("ETH", 0.0), 0.0)

    def test_case_d_eth_disabled_creates_no_eth_position(self):
        policy = _policy(enabled=False, tilt_enabled=True)
        weights, _, reasons = _allocate_core(
            policy, 0.50, {"BTC": _BTC_STRONG, "ETH": _ETH_ELIGIBLE}, {}, 0.50,
            risk_mode="volatility_budget",
            eth_alpha_state="ETH_ALPHA_POSITIVE",
        )
        self.assertNotIn("ETH", weights)
        self.assertAlmostEqual(weights["BTC"], 0.50)
        self.assertTrue(any("disabled" in reason for reason in reasons))

    def test_tilt_is_capped_by_the_single_asset_cap(self):
        # Policy parse-level validation already forbids a tilt fraction above
        # the sleeve cap, so the runtime clamp is exercised through the
        # single-asset cap: a 20% tilt of a 0.50 budget against a 0.05 cap.
        policy = _policy(tilt_enabled=True)
        weights, _, reasons = _allocate_core(
            policy, 0.50, {"BTC": _BTC_STRONG, "ETH": _ETH_ELIGIBLE}, {}, 0.05,
            risk_mode="volatility_budget",
            eth_alpha_state="ETH_ALPHA_POSITIVE",
        )
        self.assertAlmostEqual(weights["ETH"], 0.05)
        self.assertTrue(any("capped" in reason for reason in reasons))

    def test_minimum_core_sleeve_share_floors_eth_when_enabled(self):
        policy = _policy(tilt_enabled=True, minimum_core_sleeve_share=0.1)
        weights, _, reasons = _allocate_core(
            policy, 0.50, {"BTC": _BTC_STRONG, "ETH": _ETH_ELIGIBLE}, {}, 0.50,
            risk_mode="volatility_budget",
            eth_alpha_state="ETH_ALPHA_NEUTRAL",
        )
        self.assertAlmostEqual(weights["ETH"], 0.05)
        self.assertTrue(any("minimum core sleeve share" in reason for reason in reasons))

    def test_research_only_tilt_ignores_positive_alpha(self):
        policy = _policy()  # canonical tilt_enabled=false
        weights, _, _ = _allocate_core(
            policy, 0.50, {"BTC": _BTC_STRONG, "ETH": _ETH_ELIGIBLE}, {}, 0.50,
            risk_mode="volatility_budget",
            eth_alpha_state="ETH_ALPHA_POSITIVE",
        )
        self.assertAlmostEqual(weights.get("ETH", 0.0), 0.0)

    def test_unknown_alpha_state_is_rejected(self):
        policy = _policy(tilt_enabled=True)
        with self.assertRaisesRegex(ValueError, "eth_alpha_state"):
            _allocate_core(
                policy, 0.50, {"BTC": _BTC_STRONG, "ETH": _ETH_ELIGIBLE}, {}, 0.50,
                risk_mode="volatility_budget",
                eth_alpha_state="ETH_ALPHA_MASSIVE",
            )

    def test_none_alpha_state_is_neutral(self):
        policy = _policy(tilt_enabled=True)
        weights, _, _ = _allocate_core(
            policy, 0.50, {"BTC": _BTC_STRONG, "ETH": _ETH_ELIGIBLE}, {}, 0.50,
            risk_mode="volatility_budget",
            eth_alpha_state=None,
        )
        self.assertAlmostEqual(weights.get("ETH", 0.0), 0.0)

    def test_policy_validates_tilt_against_the_sleeve_cap(self):
        with self.assertRaisesRegex(Exception, "tilt_fraction_positive"):
            _policy(tilt_enabled=True, tilt_fraction_positive=0.5,
                    max_core_sleeve_share=0.3)

    def test_policy_validates_minimum_against_the_sleeve_cap(self):
        with self.assertRaisesRegex(Exception, "minimum_core_sleeve_share"):
            _policy(minimum_core_sleeve_share=0.5, max_core_sleeve_share=0.3)


if __name__ == "__main__":
    unittest.main()
