import json
import tempfile
import unittest
from pathlib import Path

from crypto_portfolio.models.policy import PolicyError, load_policy, policy_hash, resolve_policy


class PolicyTests(unittest.TestCase):
    def test_canonical_policy_loads_and_normalizes(self):
        policy = load_policy()
        self.assertEqual(policy.policy_version, 1)
        self.assertEqual(policy.core_symbols, ("BTC", "ETH"))
        self.assertEqual(policy.classify(" usdc "), "stablecoin")
        self.assertEqual(policy.classify("USD"), "cash")

    def test_policy_hash_is_canonical_and_changes_with_policy(self):
        policy = load_policy()
        self.assertEqual(policy.canonical_hash, policy_hash(policy))
        changed = resolve_policy({"min_stablecoin_weight": 0.2})
        self.assertNotEqual(policy.canonical_hash, changed.canonical_hash)

    def test_partial_override_is_explicit_and_uppercase(self):
        policy = resolve_policy({"core_symbols": [" alpha "]})
        self.assertEqual(policy.core_symbols, ("ALPHA",))
        self.assertEqual(policy.classify("alpha"), "core")

    def test_invalid_override_values_fail(self):
        for override in (
            {"min_stablecoin_weight": -0.1},
            {"max_portfolio_drawdown": 0},
            {"min_stablecoin_weight": float("nan")},
            {"unknown": True},
            {"core_symbols": ["BTC"], "satellite_symbols": [" btc "]},
        ):
            with self.subTest(override=override):
                with self.assertRaises(PolicyError):
                    resolve_policy(override)

    def test_invalid_canonical_policy_values_fail(self):
        original = load_policy().as_dict()
        cases = []
        invalid_benchmark = json.loads(json.dumps(original))
        invalid_benchmark["benchmarks"]["primary"]["BTC"] = 0.9
        cases.append(invalid_benchmark)
        invalid_scoring = json.loads(json.dumps(original))
        invalid_scoring["scoring_weights"]["trend"] = 0.9
        cases.append(invalid_scoring)
        invalid_unknown = json.loads(json.dumps(original))
        invalid_unknown["unexpected"] = True
        cases.append(invalid_unknown)
        invalid_nested = json.loads(json.dumps(original))
        invalid_nested["risk"]["extra"] = 1
        cases.append(invalid_nested)
        for data in cases:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "policy.json"
                path.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(PolicyError):
                    load_policy(path)


if __name__ == "__main__":
    unittest.main()
