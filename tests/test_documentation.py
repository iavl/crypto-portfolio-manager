import os
import subprocess
import tomllib
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DocumentationTests(unittest.TestCase):
    def test_skill_package_is_self_contained(self):
        skill = ROOT / "SKILL.md"
        self.assertTrue(skill.is_file())
        self.assertIn("name: crypto-portfolio-manager", skill.read_text(encoding="utf-8"))
        self.assertIn("[Usage Guide](docs/USAGE.md)", (ROOT / "README.md").read_text(encoding="utf-8"))
        self.assertIn(
            "[中文术语表](docs/GLOSSARY.zh-CN.md)",
            (ROOT / "README.zh-CN.md").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "[中文术语表](GLOSSARY.zh-CN.md)",
            (ROOT / "docs" / "USAGE.md").read_text(encoding="utf-8"),
        )

        required_paths = (
            "docs/USAGE.md",
            "docs/HOW_IT_WORKS.md",
            "docs/DEVELOPMENT_DEBUGGING.md",
            "docs/GLOSSARY.zh-CN.md",
            "README.zh-CN.md",
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
            "scripts/run_with_debug.py",
        )
        for relative_path in required_paths:
            with self.subTest(relative_path=relative_path):
                self.assertTrue((ROOT / relative_path).exists())

    def test_documented_python_requirement_matches_project(self):
        with (ROOT / "pyproject.toml").open("rb") as stream:
            project = tomllib.load(stream)
        self.assertEqual(project["project"]["requires-python"], ">=3.11")
        self.assertIn("Python 3.11 or newer", (ROOT / "README.md").read_text(encoding="utf-8"))

    def test_data_sources_document_source_policy(self):
        source_policy = (ROOT / "references/data-sources.md").read_text(encoding="utf-8")
        for text in (
            "Tier 1", "Tier 2", "Tier 3", "Chain liveness",
            "ETF / institutional flows", "Fundamentals", "Events",
            "Missing data", "Conflict handling",
        ):
            with self.subTest(text=text):
                self.assertIn(text, source_policy)

    def test_data_providers_document_current_provider_boundaries(self):
        provider_policy = (ROOT / "references/data-providers.md").read_text(encoding="utf-8")
        for provider in (
            "Binance", "Bybit", "CoinGecko", "DeFiLlama", "Alternative.me",
            "Chain liveness", "Coin Metrics", "GitHub", "SoSoValue", "EventScanner",
        ):
            with self.subTest(provider=provider):
                self.assertIn(provider, provider_policy)
        for text in (
            "COINGECKO_API_KEY", "GITHUB_TOKEN", "SOSOVALUE_API_KEY",
            "CapMrktEstUSD", "historicalInflowChart", "PROVIDER_INSUFFICIENT_HISTORY",
            "L2BEAT_API_KEY", "query parameter `apiKey`", "OpenAPI `3.1.0`",
            "`SKIPPED`", "`NOT_APPLICABLE`",
        ):
            with self.subTest(text=text):
                self.assertIn(text, provider_policy)

    def test_no_obsolete_data_source_layer_references(self):
        obsolete = "data-source-" + "inventory.md"
        paths = (
            ROOT / "SKILL.md",
            ROOT / "README.md",
            ROOT / "README.zh-CN.md",
            ROOT / "docs" / "USAGE.md",
            ROOT / "docs" / "HOW_IT_WORKS.md",
            ROOT / "references" / "data-sources.md",
            ROOT / "references" / "data-providers.md",
            ROOT / "tests" / "test_documentation.py",
        )
        for path in paths:
            with self.subTest(path=path):
                self.assertNotIn(obsolete, path.read_text(encoding="utf-8"))
        self.assertFalse((ROOT / "references" / obsolete).exists())

    def test_reports_document_decision_basis_and_ambiguous_terms(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        template = (ROOT / "references/output-template.md").read_text(encoding="utf-8")
        for text in (
            "证据 → 事实含义 → 组合约束 → 风险门 → 调仓阈值 → Action",
            "Evidence ID",
            "MATERIAL_EVENT_FOUND",
            "协议提案/升级活动",
            "治理提案/风险参数活动",
            "无法确认",
        ):
            with self.subTest(text=text):
                self.assertTrue(text in skill or text in template)
        for text in (
            "### 结论依据",
            "### 本轮数据抓取失败明细",
            "failed_data_fetches",
            "失败原因",
            "错误码",
            "最近可用数据",
            "决策影响",
            "### 术语解释与决策影响",
            "事实",
            "判断",
            "STALE",
            "Position P&L",
            "NAV Return",
            "成本数据覆盖率",
            "scoring/decision effect",
            "Debug 报告",
            "脚本执行异常",
            "程序失败日志",
            "直接失败日志",
        ):
            with self.subTest(text=text):
                self.assertIn(text, template)
        self.assertIn("`SKIPPED` and `NOT_APPLICABLE` are not failed fetches", template)
        self.assertLess(
            template.index("### 结论依据"),
            template.index("### 本轮数据抓取失败明细"),
        )
        self.assertLess(
            template.index("### 本轮数据抓取失败明细"),
            template.index("## 2. 组合诊断"),
        )
        self.assertIn("MATERIAL_EVENT_FOUND` means a relevant proposal or announcement", skill)
        self.assertIn("result.pending_event_scans", skill)
        self.assertIn("result.finalized", skill)
        self.assertIn("policy.universe.excluded", skill)
        self.assertIn("pending external", skill)
        self.assertIn("scripts/run_with_debug.py", skill)
        self.assertIn("script_executions", skill)
        self.assertNotIn("result.event_source_scan_requests", skill)
        self.assertIn("not proof of an exploit, approval, or execution", template)

    def test_provider_debugging_guide_matches_cli_contract(self):
        guide = (ROOT / "docs" / "DEVELOPMENT_DEBUGGING.md").read_text(encoding="utf-8")
        for text in (
            "--status", "--doctor", "--probe", "--contract", "--smoke",
            "HTTP_403_UNKNOWN", "CONNECT_TIMEOUT", "READ_TIMEOUT",
            "PROVIDER_SCHEMA_CHANGED", "CIRCUIT_OPEN", "Record / replay",
            "run_with_debug.py", "credential", "TLS verification",
        ):
            with self.subTest(text=text):
                self.assertIn(text, guide)
        self.assertIn("DEVELOPMENT_DEBUGGING.md", (ROOT / "README.md").read_text(encoding="utf-8"))
        self.assertIn("DEVELOPMENT_DEBUGGING.md", (ROOT / "README.zh-CN.md").read_text(encoding="utf-8"))
        self.assertIn("DEVELOPMENT_DEBUGGING.md", (ROOT / "docs" / "USAGE.md").read_text(encoding="utf-8"))

    def test_install_script_copies_payload_and_refuses_existing_destination(self):
        script = ROOT / "install.sh"
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertTrue(script.is_file())
        self.assertTrue(os.access(script, os.X_OK))
        self.assertIn("./install.sh", readme)
        self.assertIn("refuses to overwrite", readme)

        with tempfile.TemporaryDirectory() as directory:
            temporary_root = Path(directory)
            environment = os.environ.copy()
            environment["CODEX_HOME"] = str(temporary_root / "codex")
            environment["HOME"] = str(temporary_root / "home")

            first = subprocess.run(
                [str(script)],
                cwd=temporary_root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(first.returncode, 0, first.stderr)

            installed = Path(environment["CODEX_HOME"]) / "skills" / "crypto-portfolio-manager"
            self.assertTrue(installed.is_dir())
            self.assertFalse(installed.is_symlink())
            for relative_path in (
                "SKILL.md",
                "README.md",
                "README.zh-CN.md",
                "docs/USAGE.md",
                "docs/HOW_IT_WORKS.md",
                "docs/GLOSSARY.zh-CN.md",
                "config/policy.json",
                "references/risk-model.md",
                "references/data-sources.md",
                "references/data-providers.md",
                "schemas/decision.schema.json",
                "crypto_portfolio/__init__.py",
                "scripts/portfolio_snapshot.py",
            ):
                with self.subTest(relative_path=relative_path):
                    self.assertTrue((installed / relative_path).is_file())

            for excluded_path in (
                ".git",
                "tests",
                "data",
                "USAGE.md",
                "HOW_IT_WORKS.md",
                "install.sh",
                "pyproject.toml",
                "AGENTS.md",
                "plan.md",
            ):
                with self.subTest(excluded_path=excluded_path):
                    self.assertFalse((installed / excluded_path).exists())
            self.assertFalse(any(installed.rglob("__pycache__")))
            obsolete = "data-source-" + "inventory.md"
            self.assertFalse((installed / "references" / obsolete).exists())

            marker = installed / "install-smoke-marker"
            marker.write_text("preserve", encoding="utf-8")
            second = subprocess.run(
                [str(script)],
                cwd=temporary_root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(second.returncode, 0)
            self.assertIn("already exists", second.stderr)
            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")

            broken_home = temporary_root / "broken-codex"
            broken_skills = broken_home / "skills"
            broken_skills.mkdir(parents=True)
            broken_destination = broken_skills / "crypto-portfolio-manager"
            broken_destination.symlink_to(temporary_root / "missing-skill", target_is_directory=True)
            broken_environment = environment.copy()
            broken_environment["CODEX_HOME"] = str(broken_home)
            broken = subprocess.run(
                [str(script)],
                cwd=temporary_root,
                env=broken_environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(broken.returncode, 0)
            self.assertIn("already exists", broken.stderr)
            self.assertTrue(broken_destination.is_symlink())

    def test_glossary_covers_current_terms_and_boundaries(self):
        glossary = (ROOT / "docs" / "GLOSSARY.zh-CN.md").read_text(encoding="utf-8")
        for text in (
            "# 术语表（新手版）",
            "## 1. 复盘类型、结论与动作",
            "## 2. 组合与风险",
            "## 3. 记账与表现",
            "## 4. 市场数据与指标",
            "## 5. 证据、评分与系统",
            "SNAPSHOT_REVIEW",
            "NO_TRADE",
            "HOLD_ONLY",
            "NAV",
            "unitized NAV",
            "OHLCV",
            "ATR14",
            "Volume Profile",
            "MVRV / SOPR / NUPL",
            "trend、valuation、fundamentals、onchain、capital_flows 和 relative_strength_btc",
            "NOT_APPLICABLE",
            "SKIPPED",
            "MetricObservation",
            "CollectionEvent",
            "AUTO / CACHE_ONLY / REFRESH",
            "DecisionReviewPacket",
            "ReportPacket",
            "score > 某值就买",
            "不是持仓者的精确成本基础",
            "链数据传输失败不等于链已 HALTED",
            "不是系统错误",
            "observed_at",
            "fetched_at",
            "as_of",
        ):
            with self.subTest(text=text):
                self.assertIn(text, glossary)

    def test_evidence_collection_and_decision_chain_are_documented(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        template = (ROOT / "references/output-template.md").read_text(encoding="utf-8")
        self.assertIn("Data Collection Log", skill)
        self.assertIn("Never silently omit", skill)
        for status in ("SUCCESS", "FAILED", "STALE", "CONFLICT", "NOT_APPLICABLE", "SKIPPED"):
            with self.subTest(status=status):
                self.assertIn(status, skill)
        self.assertIn("CRITICAL DATA FAILURE", skill)
        self.assertIn("Evidence → Factor Score", template)
        self.assertIn("Policy weight / effective weight", template)
        self.assertIn("NO_TRADE", template)
        self.assertIn("Data Collection Summary", template)


if __name__ == "__main__":
    unittest.main()
