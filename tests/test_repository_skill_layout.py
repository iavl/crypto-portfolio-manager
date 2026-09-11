import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / ".agents" / "skills" / "crypto-portfolio-manager"
SKILL_PATH = SKILL_DIR / "SKILL.md"


class RepositorySkillLayoutTests(unittest.TestCase):
    def test_repository_skill_is_the_only_skill_payload(self):
        self.assertTrue(SKILL_PATH.is_file())
        self.assertFalse(SKILL_PATH.is_symlink())
        self.assertFalse((ROOT / "SKILL.md").exists())
        self.assertFalse((ROOT / "install.sh").exists())

        skill = SKILL_PATH.read_text(encoding="utf-8")
        self.assertTrue(skill.startswith("---\n"))
        self.assertIn("name: crypto-portfolio-manager", skill)
        self.assertIn("description:", skill)
        self.assertIn("REPO_ROOT", skill)

        for directory in (
            "config",
            "references",
            "schemas",
            "crypto_portfolio",
            "scripts",
            "docs",
        ):
            with self.subTest(directory=directory):
                self.assertFalse((SKILL_DIR / directory).exists())

        for relative_path in (
            "AGENTS.md",
            "config/policy.json",
            "references/investment-policy.md",
            "schemas/decision.schema.json",
            "crypto_portfolio/__init__.py",
            "scripts/portfolio_snapshot.py",
            "docs/USAGE.md",
            "tests",
        ):
            with self.subTest(relative_path=relative_path):
                self.assertTrue((ROOT / relative_path).exists())


if __name__ == "__main__":
    unittest.main()
