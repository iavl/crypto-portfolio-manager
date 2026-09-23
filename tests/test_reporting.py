import csv
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from crypto_portfolio.research.reporting import render_run_report
from crypto_portfolio.research.validity_gate import (
    NO_TRADES_IN_WINDOW,
    ONE_WAY_RATCHER,
    TRADING_STALLED,
    VERDICT_NOT_A_TEST,
)

EXPERIMENT = "core/core_existing/strict/main_cost"
# Near the window end, so a live experiment is not flagged as a stalled tail.
RECENT_DAY = "2026-12-20"


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _score_row(symbol: str) -> dict:
    factor = {"availability": "AVAILABLE", "reliability": 1.0, "score": 60.0}
    missing = {"availability": "MISSING", "reliability": 0.0, "score": None}
    names = (
        "trend", "valuation", "fundamentals", "onchain", "capital_flows",
        "relative_strength_btc", "btc_valuation", "macro_liquidity",
    )
    return {
        "timestamp": "2026-01-01T00:00:00Z",
        "symbol": symbol,
        "score": 60.0,
        "coverage": 0.35,
        "coverage_band": "LOW",
        "factor_scores": {name: dict(factor if name == "trend" else missing) for name in names},
    }


def _metrics() -> dict:
    return {
        "total_return": 0.10,
        "cagr": 0.05,
        "maximum_drawdown": -0.20,
        "annualized_volatility": 0.25,
        "sharpe_rf_zero": 0.30,
        "sortino_target_zero": 0.40,
        "calmar": 0.25,
        "calendar_returns": {"monthly": {}, "yearly": {"2026": 0.10}},
    }


def _comparison(
    excess: float,
    annualized: float,
    *,
    total_return: float = 0.30,
    volatility: float = 0.25,
    maximum_drawdown: float = -0.50,
) -> dict:
    return {
        "total_return": total_return,
        "excess_return": excess,
        "excess_return_annualized": annualized,
        "maximum_drawdown": maximum_drawdown,
        "cagr": 0.15,
        "annualized_volatility": volatility,
        "sharpe_rf_zero": 0.40,
        "sharpe_delta": -0.10,
    }


def _trade_rows(sides: list[str], start_day: str) -> list[dict]:
    start = date.fromisoformat(start_day)
    return [
        {
            "timestamp": date.fromordinal(start.toordinal() + index).isoformat() + "T00:00:00.000001Z",
            "symbol": "BTC",
            "side": side,
            "quantity": 1.0,
            "reference_price": 100.0,
            "execution_price": 100.0,
            "gross_notional_usd": 100.0,
            "fee_usd": 0.1,
            "cash_change_usd": 99.9,
            "reason": "REGIME_DERISK",
        }
        for index, side in enumerate(sides)
    ]


def _run_payload(*, sides: list[str], start_day: str = RECENT_DAY) -> dict:
    valuations = [
        {"timestamp": "2026-01-01T00:00:00Z", "total_value_usd": 100000.0, "cash_usd": 15000.0, "drawdown": 0.0},
        {"timestamp": "2026-12-31T00:00:00Z", "total_value_usd": 110000.0, "cash_usd": 40000.0, "drawdown": -0.20},
    ]
    return {
        "run_id": "fixture-run",
        "spec": {
            "run_id": "fixture-run",
            "start_at": "2026-01-01T00:00:00Z",
            "end_at": "2026-12-31T00:00:00Z",
        },
        "manifest": {"blockers": [], "strict_ready": True, "series": [{"metric": "market.ohlcv"}]},
        "valuation_basis": "USD",
        "strict_status": "ELIGIBLE",
        "runs": {
            EXPERIMENT: {
                "status": "COMPLETED",
                "result": {
                    "metrics": _metrics(),
                    "total_cost_usd": 12.5,
                    "total_turnover": 0.5,
                    "average_cash_weight": 0.30,
                    "benchmark_comparison": {
                        "btc_buy_and_hold_zero_cost": _comparison(-0.20, -0.10),
                        "btc_buy_and_hold_investable": _comparison(-0.19, -0.09),
                        "btc_eth_70_30_zero_cost": _comparison(-0.15, -0.07),
                        "btc_eth_70_30_investable": _comparison(-0.14, -0.06),
                        "static_initial_weights_investable": _comparison(
                            -0.05, -0.02, total_return=0.15, volatility=0.40, maximum_drawdown=-0.45
                        ),
                        "vol_matched_btc_cash_investable": _comparison(
                            -0.08, -0.03, total_return=0.18, volatility=0.25, maximum_drawdown=-0.21
                        ),
                    },
                    "benchmarks": {
                        "btc_buy_and_hold_zero_cost": {
                            "metrics": {
                                **_metrics(),
                                "calendar_returns": {"monthly": {}, "yearly": {"2026": 0.30}},
                            }
                        }
                    },
                    "valuations": valuations,
                    "trades": _trade_rows(sides, start_day),
                },
            }
        },
    }


def _build_run_dir(
    root: Path, *, run: dict, existing_sides: list[str] | None = None
) -> Path:
    _write_json(root / "spec.json", run["spec"])
    _write_json(root / "manifest.json", run["manifest"])
    _write_json(root / "score-evaluation.json", {
        "contract": "STRICT_POINT_IN_TIME_INPUTS",
        "scopes": {"core": {"rows": [_score_row("BTC")], "horizons": {}}},
    })
    _write_json(root / "decision-evaluation.json", {
        "valid_decisions": 1,
        "excluded_decisions": 2,
        "rows": [{"performance_class": "PAPER_ONLY", "reference": None}],
    })
    if existing_sides is not None:
        report_dir = root / "report"
        report_dir.mkdir(parents=True, exist_ok=True)
        with (report_dir / "core_core_existing_strict_main_cost.trades.csv").open(
            "w", encoding="utf-8", newline=""
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=(
                "timestamp", "symbol", "side", "gross_notional_usd", "reason",
            ))
            writer.writeheader()
            for row in _trade_rows(existing_sides, "2026-02-01"):
                writer.writerow({key: row[key] for key in writer.fieldnames})
    return root


def _render(run: dict, root: Path, output: Path) -> tuple[dict, str, str]:
    result = render_run_report(run, output, run_dir=root)
    return (
        result,
        (output / "report.zh-CN.md").read_text(encoding="utf-8"),
        (output / "report.zh-CN.html").read_text(encoding="utf-8"),
    )


def _section(markdown: str, heading: str) -> str:
    """Body of one top-level section, up to the next `## ` heading."""
    _before, separator, after = markdown.partition(f"## {heading}")
    if not separator:
        return ""
    index = after.find("\n## ")
    return after if index == -1 else after[:index]


def _render_fixture(sides: list[str], *, existing_sides: list[str] | None = None) -> tuple[dict, str, str]:
    with tempfile.TemporaryDirectory() as directory:
        run = _run_payload(sides=sides)
        root = _build_run_dir(Path(directory), run=run, existing_sides=existing_sides)
        return _render(run, root, root / "report")


class VerdictPlacementTests(unittest.TestCase):
    def test_verdict_is_reported_before_any_performance_number(self):
        result, markdown, _html = _render_fixture(["SELL", "SELL"])
        self.assertEqual(result["validity"], VERDICT_NOT_A_TEST)
        self.assertLess(markdown.index(VERDICT_NOT_A_TEST), markdown.index(EXPERIMENT))

    def test_message_names_the_verdict_as_not_a_test_of_the_strategy(self):
        _result, markdown, _html = _render_fixture(["SELL", "SELL"])
        self.assertIn("不构成对策略的检验", markdown)

    def test_manifest_without_blockers_is_never_reported_as_clean(self):
        _result, markdown, _html = _render_fixture(["SELL", "SELL"])
        self.assertNotIn("数据 manifest 未报告阻断项", markdown)
        self.assertIn("不能据此认为信号层", markdown)

    def test_readiness_section_reports_factor_availability_and_coverage(self):
        _result, markdown, _html = _render_fixture(["SELL", "SELL"])
        readiness = _section(markdown, "数据就绪度")
        self.assertIn("### 因子可用率", readiness)
        self.assertIn("| core | trend | 1/1 |", readiness)
        self.assertIn("### 评分覆盖率", readiness)
        self.assertIn("minimum_investable_coverage", readiness)

    def test_policy_mismatch_is_announced(self):
        run = _run_payload(sides=["SELL", "SELL"])
        run["spec"]["policy_hash"] = "0" * 64
        with tempfile.TemporaryDirectory() as directory:
            root = _build_run_dir(Path(directory), run=run)
            _result, markdown, _html = _render(run, root, root / "report")
        self.assertIn("与当前仓库 policy", markdown)


class ExperimentStatusTests(unittest.TestCase):
    def test_live_experiment_is_marked_ok(self):
        _result, markdown, _html = _render_fixture(["SELL", "BUY"])
        self.assertIn(f"| {EXPERIMENT} | OK |", markdown)

    def test_one_way_experiment_is_labelled(self):
        _result, markdown, _html = _render_fixture(["SELL", "SELL"])
        self.assertIn(f"| {EXPERIMENT} | {ONE_WAY_RATCHER} |", markdown)

    def test_never_traded_experiment_is_labelled(self):
        _result, markdown, _html = _render_fixture([])
        self.assertIn(f"| {EXPERIMENT} | {NO_TRADES_IN_WINDOW} |", markdown)

    def test_stalled_tail_is_labelled(self):
        run = _run_payload(sides=["SELL", "BUY"], start_day="2026-02-01")
        with tempfile.TemporaryDirectory() as directory:
            root = _build_run_dir(Path(directory), run=run)
            _result, markdown, _html = _render(run, root, root / "report")
        self.assertIn(TRADING_STALLED, markdown)
        self.assertIn("无人管理状态", markdown)

    def test_stalled_but_live_experiment_still_anchors_the_benchmark_table(self):
        """A stalled tail qualifies the path; it must not erase the comparison."""
        run = _run_payload(sides=["SELL", "BUY"], start_day="2026-02-01")
        with tempfile.TemporaryDirectory() as directory:
            root = _build_run_dir(Path(directory), run=run)
            _result, markdown, _html = _render(run, root, root / "report")
        benchmark = _section(markdown, "基准比较")
        self.assertIn(f"| {EXPERIMENT} | btc_buy_and_hold |", benchmark)
        self.assertNotIn("已跳过", benchmark)
        self.assertIn(TRADING_STALLED, _section(markdown, "组合结果"))

    def test_degenerate_experiment_is_excluded_from_the_benchmark_table(self):
        _result, markdown, _html = _render_fixture(["SELL", "SELL"])
        benchmark = _section(markdown, "基准比较")
        self.assertNotIn(f"| {EXPERIMENT} | btc_buy_and_hold |", benchmark)
        self.assertIn("没有可比实验参与基准比较", benchmark)
        self.assertIn("已跳过 1 个实验（ONE_WAY_RATCHER 1 个）", benchmark)

    def test_skip_note_only_counts_disqualifying_codes(self):
        """A stalled tail qualifies a path; it is not a reason for exclusion."""
        run = _run_payload(sides=["SELL", "SELL"], start_day="2026-02-01")
        with tempfile.TemporaryDirectory() as directory:
            root = _build_run_dir(Path(directory), run=run)
            _result, markdown, _html = _render(run, root, root / "report")
        benchmark = _section(markdown, "基准比较")
        self.assertIn("已跳过 1 个实验（ONE_WAY_RATCHER 1 个）", benchmark)
        self.assertNotIn("TRADING_STALLED 1 个", benchmark)

    def test_live_experiment_stays_in_the_benchmark_table(self):
        _result, markdown, _html = _render_fixture(["SELL", "BUY"])
        benchmark = _section(markdown, "基准比较")
        self.assertIn(f"| {EXPERIMENT} | btc_buy_and_hold |", benchmark)
        self.assertIn(f"| {EXPERIMENT} | btc_eth_70_30 |", benchmark)
        self.assertNotIn("已跳过", benchmark)

    def test_benchmark_rows_carry_strategy_and_benchmark_drawdown_side_by_side(self):
        _result, markdown, _html = _render_fixture(["SELL", "BUY"])
        benchmark = _section(markdown, "基准比较")
        self.assertIn("基准MaxDD", benchmark)
        self.assertIn("策略MaxDD", benchmark)
        row_prefix = f"| {EXPERIMENT} | btc_buy_and_hold | 30.00% | 30.00% | 10.00% | -19.00% |"
        self.assertIn(row_prefix, benchmark)


class DecisionComparisonTests(unittest.TestCase):
    """The two fair comparisons decide the question; they must lead the section."""

    def test_decision_table_leads_the_benchmark_section(self):
        _result, markdown, _html = _render_fixture(["SELL", "BUY"])
        benchmark = _section(markdown, "基准比较")
        self.assertIn("### 决策对照（先看这张）", benchmark)
        self.assertLess(benchmark.index("决策对照"), benchmark.index("逐个基准明细"))

    def test_decision_row_carries_both_fair_comparisons(self):
        _result, markdown, _html = _render_fixture(["SELL", "BUY"])
        benchmark = _section(markdown, "基准比较")
        self.assertIn(f"| {EXPERIMENT} | 10.00% | 25.00% | -20.00% | 0.3 | 15.00% | -2.00% |", benchmark)
        self.assertIn("| 18.00% | -3.00% |", benchmark)

    def test_risk_matched_row_exposes_the_benchmark_volatility(self):
        _result, markdown, _html = _render_fixture(["SELL", "BUY"])
        benchmark = _section(markdown, "基准比较")
        self.assertIn(f"| {EXPERIMENT} | vol_matched_btc_cash | 不可用 | 18.00% |", benchmark)
        self.assertIn("基准波动率", benchmark)

    def test_a_failed_risk_match_is_announced(self):
        run = _run_payload(sides=["SELL", "BUY"])
        run["runs"][EXPERIMENT]["result"]["benchmark_comparison"][
            "vol_matched_btc_cash_investable"
        ]["annualized_volatility"] = 0.40
        with tempfile.TemporaryDirectory() as directory:
            root = _build_run_dir(Path(directory), run=run)
            _result, markdown, _html = _render(run, root, root / "report")
        self.assertIn("该口径的对照精度不足", markdown)

    def test_degenerate_experiment_is_absent_from_the_decision_table(self):
        _result, markdown, _html = _render_fixture(["SELL", "SELL"])
        benchmark = _section(markdown, "基准比较")
        self.assertNotIn("决策对照", benchmark)


class StaleTradeLogTests(unittest.TestCase):
    """The gate must classify the CSVs this run wrote, not the previous run's."""

    def test_leftover_two_sided_log_does_not_mask_a_one_way_run(self):
        _result, markdown, _html = _render_fixture(["SELL", "SELL"], existing_sides=["SELL", "BUY"])
        self.assertIn(ONE_WAY_RATCHER, markdown)

    def test_repeated_renders_agree(self):
        run = _run_payload(sides=["SELL", "SELL"])
        with tempfile.TemporaryDirectory() as directory:
            root = _build_run_dir(Path(directory), run=run)
            _first, first, _html_a = _render(run, root, root / "report")
            _second, second, _html_b = _render(run, root, root / "report")
        self.assertEqual(first, second)


class HtmlRenderTests(unittest.TestCase):
    def test_html_contains_real_tables_not_markdown(self):
        _result, _markdown, html = _render_fixture(["SELL", "BUY"])
        self.assertIn("<table>", html)
        self.assertIn("<th>", html)
        self.assertNotIn("|---", html)

    def test_html_and_markdown_carry_the_same_experiment(self):
        _result, markdown, html = _render_fixture(["SELL", "BUY"])
        self.assertIn(EXPERIMENT, html)
        self.assertIn(EXPERIMENT, markdown)

    def test_html_escapes_are_not_double_encoded(self):
        _result, _markdown, html = _render_fixture(["SELL", "BUY"])
        self.assertNotIn("&amp;lt;", html)


class ArtifactTests(unittest.TestCase):
    def test_title_uses_the_spec_window(self):
        _result, markdown, _html = _render_fixture(["SELL", "BUY"])
        self.assertIn("# 2026-01-01 至 2026-12-31 回测与验证报告", markdown)

    def test_summary_is_slim_and_replaces_the_full_result_dump(self):
        run = _run_payload(sides=["SELL", "BUY"])
        with tempfile.TemporaryDirectory() as directory:
            root = _build_run_dir(Path(directory), run=run)
            output = root / "report"
            result, _markdown, _html = _render(run, root, output)
            summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
        self.assertFalse((output / "result.json").exists())
        self.assertEqual(result["summary"], str(output / "summary.json"))
        self.assertEqual(summary["verdict"], VERDICT_NOT_A_TEST)
        self.assertIn(EXPERIMENT, summary["experiments"])
        self.assertNotIn("valuations", summary["experiments"][EXPERIMENT])
        self.assertNotIn("trades", summary["experiments"][EXPERIMENT])

    def test_exposure_panel_reports_turnover_and_stall_days(self):
        _result, markdown, _html = _render_fixture(["SELL", "BUY"])
        exposure = _section(markdown, "仓位、换手与存续")
        self.assertIn("末笔距期末(天)", exposure)
        self.assertIn("| 2 |", exposure)
        self.assertIn("36.36%", exposure)

    def test_decision_evaluation_reports_an_empty_realized_sample(self):
        _result, markdown, _html = _render_fixture(["SELL", "BUY"])
        evaluation = _section(markdown, "评估产物")
        self.assertIn("带已实现参考价的 0 行", evaluation)
        self.assertIn("决策质量没有样本", evaluation)

    def test_run_dir_defaults_to_the_output_root_parent(self):
        run = _run_payload(sides=["SELL", "SELL"])
        with tempfile.TemporaryDirectory() as directory:
            root = _build_run_dir(Path(directory), run=run)
            result = render_run_report(run, root / "report")
        self.assertEqual(result["validity"], VERDICT_NOT_A_TEST)

    def test_missing_run_dir_degrades_without_crashing(self):
        run = _run_payload(sides=["SELL", "SELL"])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output" / "report"
            result = render_run_report(run, output, run_dir=Path(directory) / "absent")
            markdown = (output / "report.zh-CN.md").read_text(encoding="utf-8")
        self.assertIsNone(result["validity"])
        self.assertIn("无法执行有效性校验", markdown)


if __name__ == "__main__":
    unittest.main()
