import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DocumentationTests(unittest.TestCase):
    def test_skill_package_is_self_contained(self):
        skill = ROOT / "SKILL.md"
        self.assertTrue(skill.is_file())
        self.assertIn("name: crypto-portfolio-manager", skill.read_text(encoding="utf-8"))
        self.assertIn("[Usage Guide](USAGE.md)", (ROOT / "README.md").read_text(encoding="utf-8"))

        required_paths = (
            "USAGE.md",
            "config/policy.json",
            "references/investment-policy.md",
            "references/scoring-model.md",
            "references/risk-model.md",
            "references/decision-rules.md",
            "references/data-sources.md",
            "references/output-template.md",
            "crypto_portfolio",
            "crypto_portfolio/events",
            "schemas",
            "schemas/execution-plan.schema.json",
            "schemas/market.schema.json",
            "schemas/metric-observation.schema.json",
            "schemas/collection-event.schema.json",
            "schemas/volume-profile.schema.json",
            "config/model-routing.json",
            "config/data-providers.json",
            "references/data-providers.md",
            "schemas/data-providers.schema.json",
            "schemas/provider-request.schema.json",
            "schemas/event-scan-result.schema.json",
            "schemas/event-source-scan-request.schema.json",
            "schemas/event-source-scan-response.schema.json",
            "schemas/provider-runtime-status.schema.json",
            "references/model-routing.md",
            "schemas/metric-collection-plan.schema.json",
            "schemas/factor-packet.schema.json",
            "schemas/decision-review-packet.schema.json",
            "schemas/report-packet.schema.json",
            "schemas/factor-judgment.schema.json",
            "scripts",
        )
        for relative_path in required_paths:
            with self.subTest(relative_path=relative_path):
                self.assertTrue((ROOT / relative_path).exists())

    def test_documented_python_requirement_matches_project(self):
        with (ROOT / "pyproject.toml").open("rb") as stream:
            project = tomllib.load(stream)
        self.assertEqual(project["project"]["requires-python"], ">=3.11")
        self.assertIn("Python 3.11 or newer", (ROOT / "README.md").read_text(encoding="utf-8"))

    def test_evidence_collection_and_decision_chain_are_documented(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        template = (ROOT / "references/output-template.md").read_text(encoding="utf-8")
        self.assertIn("Data Collection Log", skill)
        self.assertIn("Never silently omit", skill)
        for status in ("SUCCESS", "FAILED", "STALE", "CONFLICT", "NOT_APPLICABLE"):
            with self.subTest(status=status):
                self.assertIn(status, skill)
        self.assertIn("CRITICAL DATA FAILURE", skill)
        self.assertIn("Evidence → Factor Score", template)
        self.assertIn("Policy weight / effective weight", template)
        self.assertIn("NO_TRADE", template)
        self.assertIn("Data Collection Summary", template)


if __name__ == "__main__":
    unittest.main()
