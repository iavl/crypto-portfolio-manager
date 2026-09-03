import json
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from crypto_portfolio.models.decision import Decision
from crypto_portfolio.models.evidence import AssetAssessment, Evidence, FactorScore
from crypto_portfolio.models.market import Candle, OHLCVSeries, SpotPrice
from crypto_portfolio.models.portfolio import normalize_snapshot
from crypto_portfolio.state.decisions import append_decision, read_decisions
from crypto_portfolio.state.snapshots import append_snapshot, read_snapshots


SCHEMAS = Path(__file__).parents[1] / "schemas"


class SchemaValidationTests(unittest.TestCase):
    def validate(self, filename, instance):
        schema = json.loads((SCHEMAS / filename).read_text(encoding="utf-8"))
        errors = list(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(instance)
        )
        self.assertEqual([], errors, "\n".join(error.message for error in errors))

    def test_input_and_normalized_contracts_are_distinct(self):
        input_value = {
            "timestamp": "2026-09-01T00:00:00Z",
            "base_currency": "USD",
            "positions": [{"symbol": "BTC", "value_usd": 100}],
        }
        self.validate("portfolio-input.schema.json", input_value)
        self.validate("portfolio.schema.json", input_value)
        with self.assertRaises(AssertionError):
            self.validate(
                "portfolio-input.schema.json",
                {**input_value, "positions": [{"symbol": "BTC", "value_usd": 100, "computed_weight": 1}]},
            )
        self.validate("portfolio-normalized.schema.json", normalize_snapshot(input_value))

    def test_persisted_models_validate_against_record_schemas(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot_path = Path(directory) / "snapshots.jsonl"
            append_snapshot(
                {
                    "timestamp": "2026-09-01T00:00:00Z",
                    "positions": [
                        {"symbol": "BTC", "value_usd": 100},
                        {"symbol": "USDT", "value_usd": 100},
                    ],
                },
                snapshot_path,
            )
            self.validate("portfolio-record.schema.json", read_snapshots(snapshot_path)[0])

            evidence = Evidence(
                "btc-trend",
                "BTC",
                "trend",
                "test",
                "2026-09-01",
                "2026-09-01",
                "CURRENT",
                "HIGH",
            )
            decision_path = Path(directory) / "decisions.jsonl"
            append_decision(
                Decision(
                    "2026-09-01T00:00:00Z",
                    "NORMAL",
                    1,
                    {"BTC": 1.0},
                    {"BTC": 0.9, "USDT": 0.1},
                    evidence=(evidence,),
                    factor_scores={
                        "BTC": AssetAssessment(
                            "BTC",
                            {"trend": FactorScore("trend", 80, ("btc-trend",))},
                            weighted_score=80,
                            confidence="HIGH",
                            asset_type="core",
                        )
                    },
                ),
                decision_path,
            )
            self.validate("decision.schema.json", read_decisions(decision_path)[0])

    def test_position_pnl_fields_validate_against_all_portfolio_contracts(self):
        value = {
            "timestamp": "2026-09-01T00:00:00Z",
            "base_currency": "USD",
            "reported_total_value": 300,
            "positions": [
                {
                    "symbol": "AAA",
                    "quantity": 2,
                    "value_usd": 180,
                    "current_price_usd": 90,
                    "average_cost_price_usd": 100,
                    "exchange_unrealized_pnl_usd": -20,
                },
                {
                    "symbol": "USDT",
                    "quantity": 100,
                    "value_usd": 100,
                    "current_price_usd": 1,
                    "average_cost_price_usd": None,
                    "exchange_unrealized_pnl_usd": None,
                },
            ],
        }
        self.validate("portfolio-input.schema.json", value)
        self.validate("portfolio.schema.json", value)
        self.validate("portfolio-normalized.schema.json", normalize_snapshot(value))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshots.jsonl"
            append_snapshot(value, path)
            self.validate("portfolio-record.schema.json", read_snapshots(path)[0])

    def test_market_observations_validate_against_market_schema(self):
        spot = SpotPrice("ETH", 100, "2026-09-01T00:00:00Z", "exchange")
        series = OHLCVSeries(
            "ETH",
            "1D",
            (Candle("2026-08-31", 99, 101, 98, 100, 10),),
            source="exchange",
        )
        self.validate("market.schema.json", spot.as_dict())
        self.validate("market.schema.json", series.as_dict())

    def test_rebalance_action_amount_constraints_match_model(self):
        schema = json.loads((SCHEMAS / "decision.schema.json").read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema["$defs"]["rebalanceAction"], format_checker=FormatChecker())

        def record(action, amount):
            return {
                "symbol": "BTC",
                "action": action,
                "current_weight": 0.5,
                "target_weight": 0.5,
                "amount_usd": amount,
                "priority": "HIGH",
                "rationale": "x",
            }

        for action in ("INCREASE", "REDUCE", "EXIT"):
            errors = list(validator.iter_errors(record(action, 0.0)))
            self.assertNotEqual([], errors, f"{action} with amount 0 must be rejected")
            self.assertEqual([], list(validator.iter_errors(record(action, 10.0))))
        for action in ("HOLD", "WAIT", "NO_TRADE"):
            self.assertEqual([], list(validator.iter_errors(record(action, 0.0))))
            errors = list(validator.iter_errors(record(action, 10.0)))
            self.assertNotEqual([], errors, f"{action} with positive amount must be rejected")


if __name__ == "__main__":
    unittest.main()
