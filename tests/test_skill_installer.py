import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "install.sh"
SKILL = ROOT / ".agents" / "skills" / "crypto-portfolio-manager"


class SkillInstallerTests(unittest.TestCase):
    def run_installer(self, home, *args):
        environment = os.environ.copy()
        environment["HOME"] = str(home)
        return subprocess.run(
            [str(SCRIPT), *args],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_installs_all_targets_as_live_symlinks_and_is_idempotent(self):
        self.assertTrue(SCRIPT.is_file())
        self.assertTrue(os.access(SCRIPT, os.X_OK))

        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            first = self.run_installer(home, "--target", "all")
            self.assertEqual(first.returncode, 0, first.stderr)

            for parent in (".agents/skills", ".claude/skills", ".zcode/skills"):
                link = home / parent / "crypto-portfolio-manager"
                with self.subTest(link=link):
                    self.assertTrue(link.is_symlink())
                    self.assertEqual(link.resolve(), SKILL.resolve())

            second = self.run_installer(home, "--target", "all")
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("already linked", second.stdout)

    def test_refuses_to_overwrite_existing_path(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            destination = home / ".claude" / "skills" / "crypto-portfolio-manager"
            destination.parent.mkdir(parents=True)
            destination.write_text("keep", encoding="utf-8")

            result = self.run_installer(home, "--target", "claude")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("refusing to overwrite", result.stderr)
            self.assertEqual(destination.read_text(encoding="utf-8"), "keep")

    def test_repairs_legacy_codex_root_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            legacy = home / ".agents" / "skills" / "crypto-portfolio-manager"
            legacy.parent.mkdir(parents=True)
            legacy.symlink_to(ROOT)

            result = self.run_installer(home, "--target", "all")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("repaired legacy link", result.stdout)
            self.assertEqual(legacy.resolve(), SKILL.resolve())


if __name__ == "__main__":
    unittest.main()
