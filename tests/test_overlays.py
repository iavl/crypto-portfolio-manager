import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator, FormatChecker

from crypto_portfolio.data_collection import collection_summary
from crypto_portfolio.engine.cycle import build_btc_cycle_context
from crypto_portfolio.engine.decision_packet import build_decision_review_packet
from crypto_portfolio.engine.entry import build_entry_plan
from crypto_portfolio.engine.overlays import apply_overlay_deployment_cap, effective_deployment_factor
from crypto_portfolio.engine.positioning import build_positioning_facts, validate_source_compatibility
from crypto_portfolio.engine.report_packet import build_report_packet
from crypto_portfolio.engine.risk import run_risk_gate
from crypto_portfolio.engine.scoring import score_factors
from crypto_portfolio.metrics_registry import METRIC_REGISTRY
from crypto_portfolio.models.metrics_history import CollectionEvent
from crypto_portfolio.models.market_overlays import MarketOverlays
from crypto_portfolio.models.market import SpotPrice
from crypto_portfolio.models.policy import load_policy
from tests.test_execution_engine import series
from crypto_portfolio.engine.technical import build_technical_snapshot


AS_OF = "2026-09-03T00:00:00Z"


class OverlayTests(unittest.TestCase):
    def test_registry_roles_and_plan_metrics_are_separate(self):
        self.assertEqual(METRIC_REGISTRY["derivatives.funding_rate"].decision_role, "POSITIONING_OVERLAY")
        self.assertEqual(METRIC_REGISTRY["derivatives.funding_rate"].context_group, "positioning")
        self.assertEqual(METRIC_REGISTRY["onchain.btc.mvrv"].decision_role, "CYCLE_CONTEXT")
        self.assertEqual(load_policy().scoring_weights["trend"], 0.25)

    def test_positioning_scenarios(self):
        building = build_positioning_facts(
            {"derivatives.funding_rate_7d_avg": 0.0001, "derivatives.open_interest_change_7d": 0.25},
            as_of=AS_OF,
        )
        self.assertEqual(building.leverage_state, "BUILDING")
        self.assertEqual(building.risk, "ELEVATED")

        crowded = build_positioning_facts(
            {
                "derivatives.funding_rate_7d_avg": 0.0004,
                "derivatives.open_interest_change_7d": 0.4,
                "derivatives.long_short_account_ratio": 1.6,
                "derivatives.futures_basis_annualized": 0.12,
            },
            as_of=AS_OF,
        )
        self.assertEqual(crowded.leverage_state, "CROWDED")
        self.assertEqual(crowded.bias, "LONG_CROWDED")
        self.assertEqual(crowded.risk, "HIGH")

        extreme = build_positioning_facts(
            {
                "derivatives.funding_rate_7d_avg": 0.0012,
                "derivatives.open_interest_change_7d": 0.5,
                "derivatives.long_short_account_ratio": 1.8,
                "derivatives.futures_basis_annualized": 0.21,
            },
            as_of=AS_OF,
        )
        self.assertEqual(extreme.leverage_state, "EXTREME")
        self.assertEqual(extreme.risk, "EXTREME")

        deleveraged = build_positioning_facts(
            {
                "derivatives.funding_rate_7d_avg": 0.0,
                "derivatives.open_interest_usd": 1000,
                "derivatives.open_interest_change_7d": -0.3,
                "derivatives.long_liquidations_24h_usd": 200,
            },
            as_of=AS_OF,
        )
        self.assertEqual(deleveraged.leverage_state, "DELEVERAGED")
        self.assertEqual(deleveraged.risk, "LOW")

        social = build_positioning_facts(
            {"sentiment.social_bullish_share": 0.85, "sentiment.social_mentions_change_7d": 2.1},
            as_of=AS_OF,
        )
        self.assertEqual(social.social_state, "EUPHORIC")
        self.assertEqual(social.leverage_state, "UNKNOWN")
        self.assertEqual(social.risk, "ELEVATED")

    def test_source_compatibility_and_lookahead(self):
        left = {"metric_key": "derivatives.long_short_account_ratio", "value": 1.2, "source": "binance"}
        right = {"metric_key": "derivatives.long_short_account_ratio", "value": 1.3, "source": "bybit"}
        self.assertFalse(validate_source_compatibility((left, right)))
        facts = build_positioning_facts(
            [
                {"metric_key": "derivatives.funding_rate_7d_avg", "value": 0.0, "source": "x", "observed_at": AS_OF},
                {"metric_key": "derivatives.funding_rate_7d_avg", "value": 0.01, "source": "x", "observed_at": "2026-09-04T00:00:00Z"},
            ],
            as_of=AS_OF,
        )
        self.assertEqual(facts.funding_rate_7d_avg, 0.0)

    def test_cycle_clock_is_context_only_and_multisignal_is_conservative(self):
        clock = build_btc_cycle_context(as_of=AS_OF)
        self.assertEqual(clock.halving_context, "UNKNOWN")
        self.assertEqual(clock.confidence, "LOW")
        self.assertEqual(clock.market_cycle_state, "UNKNOWN")

        positioning = build_positioning_facts(
            {
                "derivatives.funding_rate_7d_avg": 0.0004,
                "derivatives.open_interest_change_7d": 0.4,
                "derivatives.long_short_account_ratio": 1.6,
            },
            as_of=AS_OF,
        )
        cycle = build_btc_cycle_context(
            as_of=AS_OF,
            current_price=120,
            price_at_halving=60,
            distance_from_ath=0.05,
            price_extension_atr=3,
            mvrv_zscore=5,
            lth_net_position_change=-0.1,
            positioning=positioning,
        )
        self.assertEqual(cycle.market_cycle_state, "OVERHEATED")
        self.assertEqual(cycle.cycle_risk, "HIGH")
        self.assertEqual(cycle.confidence, "HIGH")

    def test_caps_are_minimum_and_never_a_boost(self):
        self.assertEqual(effective_deployment_factor(1.0, positioning_factor=0.5, cycle_factor=0.8), 0.5)
        positioning = build_positioning_facts(
            {"derivatives.funding_rate_7d_avg": 0.0004, "derivatives.open_interest_change_7d": 0.4},
            as_of=AS_OF,
        )
        deployment = apply_overlay_deployment_cap(2000, positioning=positioning)
        self.assertEqual(deployment.effective_factor, 0.5)
        self.assertEqual(deployment.planned_amount_usd, 1000)
        self.assertEqual(deployment.unallocated_amount_usd, 1000)

        deleveraged = build_positioning_facts(
            {"derivatives.open_interest_change_7d": -0.3, "derivatives.long_liquidations_24h_usd": 1},
            as_of=AS_OF,
        )
        self.assertEqual(effective_deployment_factor(1.0, positioning=deleveraged), 1.0)

    def test_entry_planner_caps_only_staged_dollars(self):
        snapshot = build_technical_snapshot(
            series(),
            SpotPrice("ETH", 282, "2026-01-01T08:00:00Z", "synthetic", "2026-01-01T08:00:00Z"),
        )
        positioning = build_positioning_facts(
            {"derivatives.funding_rate_7d_avg": 0.0004, "derivatives.open_interest_change_7d": 0.4},
            as_of="2026-01-01T08:00:00Z",
        )
        base = build_entry_plan("ETH", 2000, snapshot, "NORMAL", "HIGH")
        capped = build_entry_plan("ETH", 2000, snapshot, "NORMAL", "HIGH", positioning=positioning)
        self.assertEqual(base.approved_amount_usd, capped.approved_amount_usd)
        self.assertLessEqual(capped.planned_amount_usd, base.planned_amount_usd)
        self.assertEqual(capped.positioning_summary["risk"], "HIGH")

    def test_risk_packets_and_base_score(self):
        positioning = build_positioning_facts(
            {"derivatives.funding_rate_7d_avg": 0.0004, "derivatives.open_interest_change_7d": 0.4},
            as_of=AS_OF,
        )
        cycle = build_btc_cycle_context(as_of=AS_OF)
        overlays = MarketOverlays({"BTC": positioning}, cycle)
        risk = run_risk_gate({"BTC": 0.5, "ETH": 0.4, "USDT": 0.1}, overlays=overlays)
        self.assertTrue(risk.ok)
        self.assertIn("POSITIONING_CROWDED_LONG", {item.code for item in risk.violations})
        packet = build_decision_review_packet(
            current_weights={"BTC": 0.5, "ETH": 0.4, "USDT": 0.1},
            target_weights={"BTC": 0.5, "ETH": 0.4, "USDT": 0.1},
            overlays=overlays,
        )
        report = build_report_packet(packet)
        self.assertEqual(report.positioning_summaries["BTC"]["risk"], "HIGH")
        self.assertEqual(
            score_factors({"trend": 80}).score,
            score_factors({"trend": 80}).score,
        )
        with self.assertRaises(ValueError):
            score_factors({"trend": 80, "sentiment": 90})

    def test_collection_summary_excludes_overlay_from_base_coverage(self):
        base = CollectionEvent("base", AS_OF, "BTC", "market.spot_price", "SUCCESS", source="x", observed_at=AS_OF, fetched_at=AS_OF)
        overlay = CollectionEvent("overlay", AS_OF, "BTC", "derivatives.funding_rate", "FAILED", reason="unavailable", source="x")
        summary = collection_summary((base, overlay))
        self.assertEqual(summary["coverage"], 1.0)
        self.assertEqual(summary["overlay_requested"], 1)

    def test_serialized_overlay_contracts_validate(self):
        root = Path(__file__).parents[1]
        positioning = build_positioning_facts({"derivatives.funding_rate_7d_avg": 0.0}, as_of=AS_OF)
        cycle = build_btc_cycle_context(as_of=AS_OF)
        overlays = MarketOverlays({"BTC": positioning}, cycle)
        for filename, payload in (
            ("positioning-facts.schema.json", positioning.as_dict()),
            ("btc-cycle-context.schema.json", cycle.as_dict()),
            ("market-overlays.schema.json", overlays.as_dict()),
        ):
            schema = json.loads((root / "schemas" / filename).read_text())
            errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload))
            self.assertEqual(errors, [], "\n".join(error.message for error in errors))


if __name__ == "__main__":
    unittest.main()
