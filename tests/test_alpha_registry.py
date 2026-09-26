"""Unified signal admission registry (Strategy V2.3 Phase 5)."""

import unittest

from crypto_portfolio.engine import (
    aave_relative_alpha,
    bnb_relative_alpha,
    eth_relative_alpha,
)
from crypto_portfolio.research.alpha_registry import (
    ADMISSION_STATUSES,
    admitted_signals_for_asset,
    asset_admission_summary,
    build_registry,
    classify_signal_verdict,
    preregistered_specs,
    registry_hash,
)


def _verdict(admitted=False, direction=0, reasons=()):
    return {"admitted": admitted, "direction": direction, "reasons": list(reasons)}


class RegistryInventoryTests(unittest.TestCase):
    def test_every_engine_signal_is_in_the_inventory(self):
        specs = preregistered_specs()
        for module, asset in (
            (eth_relative_alpha, "ETH"),
            (bnb_relative_alpha, "BNB"),
            (aave_relative_alpha, "AAVE"),
        ):
            for name in module.SIGNAL_NAMES:
                self.assertIn(f"{asset}:{name}", specs)
                self.assertEqual(specs[f"{asset}:{name}"].asset, asset)
                self.assertEqual(specs[f"{asset}:{name}"].name, name)

    def test_specs_carry_provenance_and_horizon(self):
        specs = preregistered_specs()
        self.assertEqual(specs["BNB:rel_return_30d"].family, "relative_price")
        self.assertEqual(specs["BNB:chain_tvl_growth_90d"].source, "defillama:chain_tvl:BNB")
        self.assertEqual(specs["AAVE:protocol_tvl_growth_90d"].family, "protocol_growth")
        self.assertEqual(specs["ETH:etf_flow_differential_30d"].family, "flow_differential")
        # Signal ids are asset-scoped; shared price names cannot collide.
        for spec in specs.values():
            self.assertTrue(spec.signal_id.startswith(spec.asset + ":"))
        for spec in specs.values():
            self.assertTrue(spec.point_in_time)
            self.assertEqual(spec.decision_horizon, 90)


class ClassificationTests(unittest.TestCase):
    def test_admitted(self):
        self.assertEqual(
            classify_signal_verdict(_verdict(admitted=True, direction=1)), "ADMITTED",
        )

    def test_consistent_negative_direction_is_rejected(self):
        verdict = _verdict(
            direction=-1,
            reasons=["w1: 90D IC is not positive", "w2: 90D IC is not positive"],
        )
        self.assertEqual(classify_signal_verdict(verdict), "REJECTED")

    def test_sample_shortfall_alone_is_insufficient_sample(self):
        verdict = _verdict(
            direction=1,
            reasons=["w1: only 4 independent 90D blocks"],
        )
        self.assertEqual(classify_signal_verdict(verdict), "INSUFFICIENT_SAMPLE")

    def test_disagreement_and_inversions_stay_research_only(self):
        self.assertEqual(
            classify_signal_verdict(
                _verdict(direction=1, reasons=["window 90D directions disagree or are flat"]),
            ),
            "RESEARCH_ONLY",
        )
        self.assertEqual(
            classify_signal_verdict(
                _verdict(direction=1, reasons=["w1: 90D top tercile below bottom tercile"]),
            ),
            "RESEARCH_ONLY",
        )


class RegistryBuildTests(unittest.TestCase):
    def _admissions(self):
        return {
            "ETH": {"signals": {
                "rel_return_90d": _verdict(admitted=True, direction=1),
                "ma_structure": _verdict(direction=-1, reasons=[
                    "w1: 90D IC is not positive", "w2: 90D IC is not positive",
                ]),
            }},
            "BNB": {"signals": {
                "chain_tvl_growth_90d": _verdict(direction=1, reasons=[
                    "w1: only 3 independent 90D blocks",
                ]),
            }},
        }

    def test_statuses_derive_from_the_rule_outputs(self):
        registry = build_registry(self._admissions())
        signals = registry["signals"]
        self.assertEqual(signals["ETH:rel_return_90d"]["admission_status"], "ADMITTED")
        self.assertEqual(signals["ETH:ma_structure"]["admission_status"], "REJECTED")
        self.assertEqual(
            signals["BNB:chain_tvl_growth_90d"]["admission_status"], "INSUFFICIENT_SAMPLE",
        )
        self.assertEqual(signals["ETH:rel_return_30d"]["admission_status"], "RESEARCH_ONLY")

    def test_unevaluated_signals_stay_research_only(self):
        registry = build_registry({})
        self.assertTrue(registry["signals"])
        for entry in registry["signals"].values():
            if entry["signal_id"] == "sol_production_alpha":
                continue
            self.assertEqual(entry["admission_status"], "RESEARCH_ONLY")

    def test_sol_sentinel_declares_research_only(self):
        registry = build_registry({})
        sentinel = registry["signals"]["sol_production_alpha"]
        self.assertEqual(sentinel["asset"], "SOL")
        self.assertEqual(sentinel["admission_status"], "RESEARCH_ONLY")

    def test_registry_hash_is_deterministic_and_content_addressed(self):
        first = build_registry(self._admissions())
        second = build_registry(self._admissions())
        self.assertEqual(first["registry_hash"], second["registry_hash"])
        self.assertEqual(registry_hash(first), first["registry_hash"])
        other = build_registry({})
        self.assertNotEqual(first["registry_hash"], other["registry_hash"])

    def test_unknown_engine_signals_land_as_unclassified_research_only(self):
        registry = build_registry({"BNB": {"signals": {
            "some_unregistered_signal": _verdict(admitted=True, direction=1),
        }}})
        entry = registry["signals"]["BNB:some_unregistered_signal"]
        self.assertEqual(entry["family"], "unclassified")
        self.assertEqual(entry["admission_status"], "ADMITTED")

    def test_admitted_signals_for_asset_follow_preregistered_order(self):
        registry = build_registry({
            "ETH": {"signals": {
                "rel_return_90d": _verdict(admitted=True, direction=1),
                "rel_return_30d": _verdict(admitted=True, direction=1),
            }},
        })
        self.assertEqual(
            admitted_signals_for_asset(registry, "ETH"),
            ("rel_return_30d", "rel_return_90d"),
        )
        self.assertEqual(admitted_signals_for_asset(registry, "BNB"), ())

    def test_summary_counts_and_tilt_gate(self):
        registry = build_registry(self._admissions())
        summary = asset_admission_summary(registry)
        self.assertTrue(summary["ETH"]["production_tilt_unlocked"])
        self.assertFalse(summary["BNB"]["production_tilt_unlocked"])
        self.assertEqual(summary["ETH"]["admitted"], 1)
        self.assertEqual(summary["ETH"]["rejected"], 1)
        self.assertEqual(summary["SOL"]["research_only"], 1)

    def test_statuses_are_the_declared_set(self):
        self.assertEqual(
            ADMISSION_STATUSES,
            ("ADMITTED", "RESEARCH_ONLY", "REJECTED", "INSUFFICIENT_SAMPLE"),
        )


if __name__ == "__main__":
    unittest.main()
