from datetime import date, timedelta
import unittest

from crypto_portfolio.engine.cycle import build_btc_cycle_context
from crypto_portfolio.engine.metric_plan import build_metric_collection_plan
from crypto_portfolio.engine.overlays import cycle_deployment_factor
from crypto_portfolio.engine.scoring import score_factors
from crypto_portfolio.models.evidence import FactorScore
from crypto_portfolio.models.policy import load_policy
from crypto_portfolio.providers.base import ProviderDataError, ProviderRequest
from crypto_portfolio.providers.coinmetrics import CoinMetricsProvider
from crypto_portfolio.providers.fred import derive_macro_features, parse_fred_series
from crypto_portfolio.providers.sosovalue import parse_etf_flow_history


class BTCSpecificTests(unittest.TestCase):
    def test_btc_profile_and_collection_scope(self):
        policy = load_policy()
        self.assertEqual(policy.scoring_profile("BTC"), {
            "trend": 0.35,
            "valuation": 0.0,
            "fundamentals": 0.0,
            "onchain": 0.0,
            "capital_flows": 0.25,
            "relative_strength_btc": 0.0,
            "btc_valuation": 0.2,
            "macro_liquidity": 0.2,
        })
        self.assertEqual(policy.scoring_profile("ETH")["btc_valuation"], 0.0)
        plan = build_metric_collection_plan({"positions": [{"symbol": "BTC", "value_usd": 1}]})
        keys = {item.metric_key for item in plan.for_asset("BTC")}
        self.assertFalse(any(key.startswith("fundamentals.") for key in keys))
        self.assertNotIn("onchain.active_addresses", keys)
        self.assertIn("btc_valuation.mvrv", keys)
        self.assertIn("macro.fed_funds_change_90d", keys)
        self.assertIn("flows.btc_etf_net_to_aum_30d", keys)

    def test_zero_weight_btc_factors_are_not_coverage(self):
        result = score_factors(
            {"trend": FactorScore("trend", 80)},
            symbol="BTC",
        )
        self.assertAlmostEqual(result.coverage, 0.35)
        self.assertEqual(result.factor_availability["fundamentals"], "NOT_APPLICABLE")
        self.assertEqual(result.factor_availability["btc_valuation"], "MISSING")

    def test_coinmetrics_derives_btc_valuation_from_free_primitives(self):
        class Client:
            def get_json(self, url, **kwargs):
                if "catalog" in url:
                    return {"data": [
                        {"metric": "CapMrktCurUSD", "frequencies": [{"frequency": "1d", "assets": ["btc"]}]},
                        {"metric": "CapRealUSD", "frequencies": [{"frequency": "1d", "assets": ["btc"]}]},
                        {"metric": "SplyCur", "frequencies": [{"frequency": "1d", "assets": ["btc"]}]},
                    ]}
                return {"data": [{
                    "time": "2026-09-06T00:00:00Z",
                    "CapMrktCurUSD": "200",
                    "CapRealUSD": "100",
                    "SplyCur": "2",
                }]}

        values = CoinMetricsProvider(client=Client()).collect(ProviderRequest(
            "coinmetrics_community", "onchain", "BTC", {},
            ("btc_valuation.mvrv", "btc_valuation.realized_price"),
        ))
        by_key = {item["metric_key"]: item for item in values}
        self.assertEqual(by_key["btc_valuation.mvrv"]["value"], 2)
        self.assertEqual(by_key["btc_valuation.realized_price"]["value"], 50)
        self.assertEqual(by_key["btc_valuation.mvrv"]["source"], "python-derived")

    def test_sosovalue_normalized_flow_uses_aligned_aum(self):
        start = date(2026, 7, 1)
        rows = [{
            "date": (start + timedelta(days=index)).isoformat(),
            "totalNetInflow": index + 1,
            "totalNetAssets": 1000 + index,
        } for index in range(31)]
        values = parse_etf_flow_history(
            {"code": 0, "data": rows},
            ("flows.btc_etf_net_to_aum_7d", "flows.btc_etf_net_to_aum_30d"),
            asset="BTC",
            fetched_at="2026-08-02T00:00:00Z",
            as_of="2026-08-02T00:00:00Z",
        )
        by_key = {item["metric_key"]: item for item in values}
        self.assertAlmostEqual(by_key["flows.btc_etf_net_to_aum_7d"]["value"], sum(range(25, 32)) / 1030)
        self.assertEqual(by_key["flows.btc_etf_net_to_aum_30d"]["metadata"]["aum_anchor_date"], "2026-07-31")

        rows[-1]["totalNetAssets"] = None
        with self.assertRaises(ProviderDataError):
            parse_etf_flow_history(
                {"code": 0, "data": rows},
                ("flows.btc_etf_net_to_aum_7d",),
                asset="BTC",
                fetched_at="2026-08-02T00:00:00Z",
                as_of="2026-08-02T00:00:00Z",
            )

    def test_fred_dot_missing_and_date_aware_changes(self):
        points = parse_fred_series(
            {"observations": [
                {"date": "2026-01-01", "value": "1"},
                {"date": "2026-04-01", "value": "2"},
                {"date": "2026-07-01", "value": "."},
            ]},
            "DFF",
            as_of="2026-06-01T00:00:00Z",
        )
        self.assertEqual(len(points), 2)
        derived = derive_macro_features(
            {"macro.dff": points},
            fetched_at="2026-06-01T00:00:00Z",
            as_of="2026-06-01T00:00:00Z",
            metric_keys=("macro.fed_funds_change_90d",),
        )
        self.assertEqual(derived[0]["value"], 1)
        self.assertEqual(derived[0]["metadata"]["prior_observed_at"], "2026-01-01T00:00:00Z")

    def test_mvrv_does_not_create_second_cycle_deployment_penalty(self):
        cycle = build_btc_cycle_context(
            as_of="2026-09-07T00:00:00Z",
            current_price=100,
            observations={"onchain.btc.mvrv_zscore": 10},
        )
        self.assertNotIn(cycle.cycle_risk, {"ELEVATED", "HIGH"})
        self.assertEqual(cycle_deployment_factor(cycle), 1.0)


if __name__ == "__main__":
    unittest.main()
