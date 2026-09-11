import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = (
    ROOT / "AGENTS.md",
    ROOT / "README.md",
    ROOT / ".agents",
    ROOT / "config",
    ROOT / "crypto_portfolio",
    ROOT / "docs",
    ROOT / "references",
    ROOT / "scripts",
    ROOT / "schemas",
)
FORBIDDEN = (
    "model-routing",
    "model_routing",
    "CURRENT_SESSION",
    "LUNA_MAX",
    "TERRA",
    "gpt-5.6-luna",
    "gpt-5.6-terra",
    "gpt-5.6-sol",
    "reasoning_effort",
    "CRYPTO_PORTFOLIO_MODEL_PROFILE",
    "CRYPTO_PORTFOLIO_MODEL_CONFIG",
    "routing_metadata",
    "requested_model",
    "effective_model",
    "fallback_reason",
    "collector_model",
    "classifier_model",
    "SolReview",
    "sol_review",
    "should_run_sol",
    "high_impact_final_review",
    "major_event_analysis",
)


class RepositoryModelOwnershipTests(unittest.TestCase):
    def test_routing_feature_files_are_removed(self):
        for relative in (
            "config/model-routing.json",
            "crypto_portfolio/model_routing.py",
            "scripts/model_routing.py",
            "references/model-routing.md",
            "schemas/model-routing.schema.json",
        ):
            with self.subTest(relative=relative):
                self.assertFalse((ROOT / relative).exists())

    def test_source_and_docs_do_not_reintroduce_model_routing(self):
        paths = []
        for root in SCAN_ROOTS:
            paths.extend((root,) if root.is_file() else root.rglob("*"))
        for path in paths:
            if not path.is_file() or path == Path(__file__):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for term in FORBIDDEN:
                with self.subTest(path=path.relative_to(ROOT), term=term):
                    self.assertNotIn(term, text)


if __name__ == "__main__":
    unittest.main()
