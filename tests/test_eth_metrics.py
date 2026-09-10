import json
import math
from datetime import date, timedelta
from pathlib import Path
import unittest

from crypto_portfolio.engine.derived_metrics import calculate_eth_etf_flow_to_aum, calculate_net_supply_growth
from crypto_portfolio.engine.factors.flows import calculate_flow_factor
from crypto_portfolio.engine.metric_plan import build_metric_collection_plan
from crypto_portfolio.metrics_registry import metric_definition
from crypto_portfolio.models.policy import load_policy
from crypto_portfolio.providers.base import ProviderRequest
from crypto_portfolio.providers.blobscan import parse_timeseries as parse_blobscan
from crypto_portfolio.providers.coinmetrics import CoinMetricsProvider
from crypto_portfolio.providers.growthepie import parse_da_payload, parse_rent_payload
from crypto_portfolio.providers.sosovalue import parse_etf_flow_history


class _CoinMetricsClient:
    def __init__(self):
        self.rows = []

    def get_json(self, url, **kwargs):
        if "catalog" in url:
            metrics = ["SplyCur", "IssTotNtv", "CapMVRVCur", "CapRealUSD"]
            return {"metrics": [{"metric": item, "frequencies": [{"frequency": "1d", "assets": ["eth"]}]} for item in metrics]}
        return {"data": self.rows}


class EthMetricsTests(unittest.TestCase):
    def test_registry_scope_and_deleted_context_is_unknown(self):
        self.assertEqual(metric_definition("eth.monetary.current_supply_eth").asset_scope, ("ETH",))
        self.assertFalse(metric_definition("valuation.fdv").applies_to("ETH"))
        self.assertFalse(metric_definition("valuation.fdv_market_cap_ratio").applies_to("ETH"))
        with self.assertRaises(ValueError):
            metric_definition("eth.structural.builder_largest_share")

    def test_pure_eth_derivations_fail_closed(self):
        self.assertTrue(math.isclose(calculate_net_supply_growth(101, 100), 0.01))
        self.assertTrue(math.isclose(calculate_eth_etf_flow_to_aum(-10, 100), -0.1))
        with self.assertRaises(ValueError):
            calculate_eth_etf_flow_to_aum(1, 0)

    def test_eth_metric_plan_is_scoped(self):
        plan = build_metric_collection_plan(["ETH", "AAVE"], policy=load_policy())
        eth = {item.metric_key for item in plan.for_asset("ETH")}
        aave = {item.metric_key for item in plan.for_asset("AAVE")}
        self.assertIn("eth.l2.rent_paid_30d_usd", eth)
        self.assertIn("relative.return_vs_btc_180d", eth)
        self.assertNotIn("relative.return_vs_btc_365d", eth)
        self.assertIn("flows.eth_etf_net_to_aum_30d", eth)
        self.assertNotIn("valuation.fdv_market_cap_ratio", eth)
        self.assertNotIn("eth.l2.rent_paid_30d_usd", aave)

    def test_coinmetrics_eth_catalog_and_derived_realized_price(self):
        client = _CoinMetricsClient()
        for index in range(32):
            observed = date(2026, 8, 1) + timedelta(days=index)
            client.rows.append({
                "time": observed.isoformat() + "T00:00:00Z",
                "SplyCur": 100 + index / 31,
                "IssTotNtv": 1,
                "CapMVRVCur": 1.2,
                "CapRealUSD": 500 + index,
            })
        result = CoinMetricsProvider(client=client).collect(ProviderRequest(
            "coinmetrics_community", "ethereum_monetary", "ETH",
            {"as_of": "2026-09-01T00:00:00Z"},
            (
                "eth.monetary.current_supply_eth",
                "eth.monetary.issuance_30d_eth",
                "eth_valuation.mvrv",
                "eth_valuation.realized_price",
            ),
        ))
        values = {item["metric_key"]: item["value"] for item in result}
        self.assertEqual(values["eth.monetary.current_supply_eth"], 101)
        self.assertEqual(values["eth.monetary.issuance_30d_eth"], 31)
        self.assertEqual(values["eth_valuation.mvrv"], 1.2)
        self.assertAlmostEqual(values["eth_valuation.realized_price"], 531 / 101)

    def test_eth_sosovalue_normalized_flow_uses_eth_aum(self):
        payload = {
            "code": 0,
            "data": [{"date": f"2026-08-{index:02d}", "total_net_inflow": 10, "total_net_assets": 1000} for index in range(1, 32)],
        }
        result = parse_etf_flow_history(
            payload,
            ("flows.eth_etf_net_to_aum_7d", "flows.eth_etf_aum_usd"),
            asset="ETH",
            fetched_at="2026-09-01T00:00:00Z",
            as_of="2026-09-01T23:00:00Z",
        )
        values = {item["metric_key"]: item["value"] for item in result}
        self.assertAlmostEqual(values["flows.eth_etf_net_to_aum_7d"], 0.07)
        self.assertEqual(values["flows.eth_etf_aum_usd"], 1000)

    def test_eth_normalized_etf_flow_owns_flow_factor(self):
        result = calculate_flow_factor({"flows.etf_net_7d": -10_000_000, "flows.eth_etf_net_to_aum_7d": 0.005}, symbol="ETH")
        self.assertEqual(result.state, "POSITIVE")
        self.assertAlmostEqual(result.normalized_flow, 0.005)

    def test_public_eth_providers_reject_unclassified_data(self):
        rent = {"data": [{"date": f"2026-08-{index:02d}", "rent_paid_usd": 1} for index in range(1, 32)]}
        self.assertEqual(parse_rent_payload(rent, ("eth.l2.rent_paid_30d_usd",), fetched_at="2026-09-01T00:00:00Z")[0]["value"], 30)
        da = {"data": [{"date": "2026-08-31", "layer": "ethereum", "data_bytes": 10, "fees_usd": 2}, {"date": "2026-08-31", "layer": "other", "data_bytes": 5, "fees_usd": 1}]}
        share = parse_da_payload(da, ("eth.da.ethereum_share_of_tracked_da_bytes_30d",), fetched_at="2026-09-01T00:00:00Z")[0]["value"]
        self.assertAlmostEqual(share, 10 / 15)
        blob = json.loads((Path(__file__).parent / "fixtures/providers/blobscan_timeseries.json").read_text())
        self.assertEqual(parse_blobscan(blob, ("eth.blobs.count_1d",), fetched_at="2026-09-01T00:00:00Z")[0]["value"], 42000)


if __name__ == "__main__":
    unittest.main()
