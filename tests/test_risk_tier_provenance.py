"""Risk-tier provenance and sizing semantics (Strategy V2 Phase 4)."""

import json
import unittest

from crypto_portfolio.engine.allocation import build_target_allocation
from crypto_portfolio.engine.portfolio_risk import PortfolioRiskInputs
from crypto_portfolio.engine.risk_tier import tier_strategic_fraction
from crypto_portfolio.models.policy import load_policy, policy_from_mapping


def _vol_policy():
    data = json.loads(json.dumps(load_policy().as_dict()))
    data["risk_engine"]["mode"] = "volatility_budget"
    # V2.3 stress-loss budget disabled: this file pins its own mechanics
    # in isolation; the combined caps are pinned in test_stress_cap_integration.
    data["risk"]["stress_loss_budget"]["enabled"] = False
    return policy_from_mapping(data)


def _inputs():
    return PortfolioRiskInputs(
        asset_volatility={"BTC": 0.1, "ETH": 0.12, "SOL": 0.14},
        correlations={
            "BTC": {"ETH": 0.5, "SOL": 0.5},
            "ETH": {"BTC": 0.5, "SOL": 0.5},
            "SOL": {"BTC": 0.5, "ETH": 0.5},
        },
    )


class ProvenanceTests(unittest.TestCase):
    def test_measured_tier_keeps_its_source_through_allocation(self):
        policy = load_policy()
        assessment = {
            "weighted_score": 90, "normalized_score": 90, "confidence": "HIGH",
            "relative_strength_vs_btc": "OUTPERFORM",
            "risk_tier": "high_beta",
            "risk_tier_source": "DETERMINISTIC_ESTIMATE",
        }
        result = build_target_allocation(
            policy=policy, regime="NORMAL", assessments={"SOL": assessment},
            current_weights={"USDT": 1.0},
        )
        allowance = result.deployment_allowances["SOL"]
        self.assertEqual(allowance["risk_tier"], "high_beta")
        self.assertEqual(allowance["risk_tier_source"], "DETERMINISTIC_ESTIMATE")
        # Legacy mode keeps the V1 halved envelope for every source.
        self.assertAlmostEqual(allowance["risk_tier_cap_fraction"], 0.5)

    def test_fraction_semantics_by_source_and_engine(self):
        policy = load_policy()
        vol_policy = _vol_policy()
        for source, engine, expected in (
            ("POLICY_DEFAULT", policy, 0.5),
            ("MANUAL_ASSESSMENT", policy, 0.5),
            ("DETERMINISTIC_ESTIMATE", policy, 0.5),
            ("POLICY_DEFAULT", vol_policy, 0.5),
            ("MANUAL_ASSESSMENT", vol_policy, 0.5),
            ("DETERMINISTIC_ESTIMATE", vol_policy, 1.0),
        ):
            self.assertAlmostEqual(
                tier_strategic_fraction("high_beta", source=source, policy=engine),
                expected,
                msg=f"{source} under {engine.risk_engine['mode']}",
            )


class SecondaryConstraintTests(unittest.TestCase):
    def test_measured_high_beta_competes_for_the_full_envelope_under_vol_budget(self):
        policy = _vol_policy()
        base = {
            "weighted_score": 90, "normalized_score": 90, "confidence": "HIGH",
            "relative_strength_vs_btc": "OUTPERFORM",
        }
        measured = {
            **base, "risk_tier": "high_beta",
            "risk_tier_source": "DETERMINISTIC_ESTIMATE",
        }
        manual = {
            **base, "risk_tier": "high_beta",
            "risk_tier_source": "MANUAL_ASSESSMENT",
        }
        measured_result = build_target_allocation(
            policy=policy, regime="NORMAL", assessments={"SOL": measured},
            current_weights={"USDT": 1.0}, risk_inputs=_inputs(),
        )
        manual_result = build_target_allocation(
            policy=policy, regime="NORMAL", assessments={"SOL": manual},
            current_weights={"USDT": 1.0}, risk_inputs=_inputs(),
        )
        measured_envelope = measured_result.deployment_allowances["SOL"]["risk_envelope_weight"]
        manual_envelope = manual_result.deployment_allowances["SOL"]["risk_envelope_weight"]
        # The measured tier's envelope is the full satellite cap; a manual
        # severity flag still halves it in the same engine.
        self.assertAlmostEqual(measured_envelope, manual_envelope * 2.0, places=9)
        # The tier is recorded but no longer the sizing engine: with calm
        # covariance the measured high_beta sleeve runs at its full envelope.
        self.assertEqual(
            measured_result.risk_engine["binding_risk_constraint"], "strategic_target"
        )


if __name__ == "__main__":
    unittest.main()
