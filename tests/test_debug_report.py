import json
from pathlib import Path
import subprocess
import sys
import unittest

from jsonschema import Draft202012Validator

from crypto_portfolio.engine.decision_packet import build_decision_review_packet
from crypto_portfolio.engine.report_packet import build_final_review_output, build_report_packet
from crypto_portfolio.models.report_packet import ReportPacket
from scripts.run_with_debug import run_script


def decision_packet():
    return build_decision_review_packet(
        review_type="SNAPSHOT_REVIEW",
        market_regime="NORMAL",
        current_weights={"ETH": 1},
        target_weights={"ETH": 1},
        assessments={"ETH": {"weighted_score": 70, "confidence": "HIGH", "factor_scores": {"trend": 70}}},
    )


class DebugReportTests(unittest.TestCase):
    def test_failed_script_prefers_stderr_and_redacts_secrets(self):
        record = run_script(
            "scripts/example.py",
            [
                sys.executable,
                "-c",
                "import sys; sys.stderr.write('Traceback\\nGITHUB_TOKEN=secret-value\\n'); sys.exit(3)",
            ],
        )
        self.assertEqual(record["status"], "FAILED")
        self.assertEqual(record["exit_code"], 3)
        self.assertEqual(record["log_source"], "STDERR")
        self.assertIn("Traceback", record["log"])
        self.assertNotIn("secret-value", json.dumps(record))

    def test_failed_script_uses_stdout_when_stderr_is_empty(self):
        record = run_script(
            "scripts/example.py",
            [sys.executable, "-c", "import sys; print('stdout failure'); sys.exit(4)"],
        )
        self.assertEqual(record["status"], "FAILED")
        self.assertEqual(record["exit_code"], 4)
        self.assertEqual(record["log_source"], "STDOUT")
        self.assertIn("stdout failure", record["log"])

        whitespace = run_script(
            "scripts/example.py",
            [sys.executable, "-c", "import sys; sys.stderr.write('  \\n'); sys.exit(4)"],
        )
        self.assertEqual(whitespace["log_source"], "LAUNCHER")
        self.assertIn("exited with code 4", whitespace["log"])

    def test_raw_response_logs_are_redacted(self):
        record = run_script(
            "scripts/example.py",
            [sys.executable, "-c", "import sys; sys.stderr.write('{\\\"body\\\":\\\"raw\\\"}'); sys.exit(6)"],
        )
        self.assertEqual(record["status"], "FAILED")
        self.assertEqual(record["log"], "[REDACTED]")

    def test_failure_log_is_bounded(self):
        record = run_script(
            "scripts/example.py",
            [sys.executable, "-c", "import sys; sys.stderr.write('x' * 13000); sys.exit(5)"],
        )
        self.assertLessEqual(len(record["log"]), 12000)
        self.assertIn("...[truncated]...", record["log"])

    def test_timeout_and_launch_failure_are_structured(self):
        timed_out = run_script(
            "scripts/example.py",
            [sys.executable, "-c", "import time; time.sleep(1)"],
            timeout_seconds=0.05,
        )
        self.assertEqual(timed_out["status"], "TIMEOUT")
        self.assertIsNone(timed_out["exit_code"])
        self.assertTrue(timed_out["log"])

        launch_failed = run_script("scripts/example.py", ["/path/that/does/not/exist"])
        self.assertEqual(launch_failed["status"], "LAUNCH_FAILED")
        self.assertIsNone(launch_failed["exit_code"])
        self.assertIn("No such file", launch_failed["log"])

    def test_report_keeps_only_non_success_script_records(self):
        success = run_script(
            "scripts/example.py",
            [sys.executable, "-c", "print('ok')"],
        )
        failure = run_script(
            "scripts/example.py",
            [sys.executable, "-c", "import sys; sys.exit(2)"],
        )
        packet = build_report_packet(
            decision_packet(),
            script_executions=(success, failure),
        )
        self.assertEqual(len(packet.script_failures), 1)
        self.assertEqual(packet.script_failures[0]["exit_code"], 2)
        restored = ReportPacket.from_mapping(packet.as_dict())
        self.assertEqual(restored.script_failures, packet.script_failures)
        output = build_final_review_output(packet)
        self.assertEqual(output["debug_report"]["script_failures"], packet.as_dict()["script_failures"])
        self.assertEqual(output["debug_report"]["data_fetch_failures"], [])

        schema = json.loads((Path(__file__).parents[1] / "schemas" / "report-packet.schema.json").read_text())
        self.assertEqual(list(Draft202012Validator(schema).iter_errors(packet.as_dict())), [])

    def test_cli_returns_record_without_aborting_the_report_workflow(self):
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).parents[1] / "scripts" / "run_with_debug.py"),
                "--script",
                "scripts/example.py",
                "--",
                sys.executable,
                "-c",
                "import sys; sys.stderr.write('failed'); sys.exit(9)",
            ],
            cwd=Path(__file__).parents[1],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["status"], "FAILED")


if __name__ == "__main__":
    unittest.main()
