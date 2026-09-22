"""Render finalized backtest results without recalculating strategy outputs."""

from __future__ import annotations

import csv
import html
import json
from pathlib import Path
from typing import Any, Mapping


def _fmt(value: Any, *, percent: bool = False) -> str:
    if value is None:
        return "不可用"
    if isinstance(value, (int, float)):
        return f"{value:.2%}" if percent else f"{value:.6g}"
    return str(value)


def _equity_svg(valuations: list[Mapping[str, Any]], width: int = 960, height: int = 360) -> str:
    if len(valuations) < 2:
        return "<svg xmlns='http://www.w3.org/2000/svg' width='960' height='120'><text x='20' y='60'>数据不足</text></svg>"
    values = [float(item["total_value_usd"]) for item in valuations]
    low, high = min(values), max(values)
    span = max(high - low, 1e-12)
    points = []
    for index, value in enumerate(values):
        x = 40 + index * (width - 80) / (len(values) - 1)
        y = 20 + (high - value) * (height - 60) / span
        points.append(f"{x:.2f},{y:.2f}")
    return (
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' viewBox='0 0 {width} {height}'>"
        "<rect width='100%' height='100%' fill='#fffdf7'/><line x1='40' y1='20' x2='40' y2='320' stroke='#777'/>"
        "<line x1='40' y1='320' x2='920' y2='320' stroke='#777'/>"
        f"<polyline fill='none' stroke='#153e75' stroke-width='2' points='{' '.join(points)}'/>"
        f"<text x='45' y='18' font-size='12'>${high:,.2f}</text><text x='45' y='340' font-size='12'>${low:,.2f}</text></svg>"
    )


def render_run_report(run: Mapping[str, Any], output_root: str | Path) -> dict[str, str]:
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    runs = run.get("runs", {})
    lines = [
        "# 2024 年至今回测与验证报告", "",
        f"- Run ID：`{run.get('run_id', 'unknown')}`",
        f"- 数据口径：`{run.get('valuation_basis', 'UNKNOWN')}`",
        f"- 严格点时资格：`{run.get('strict_status', 'UNKNOWN')}`", "",
        "本报告属于历史诊断与稳健性验证，不是未见样本上的预期收益证明。", "",
        "## 组合结果", "",
        "| 实验 | 累计收益 | CAGR | 最大回撤 | 波动率 | Sharpe | Sortino | Calmar | 成本 USD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, result in runs.items():
        if result.get("status") != "COMPLETED":
            lines.append(f"| {name} | {result.get('status')} | -- | -- | -- | -- | -- | -- | -- |")
            continue
        metrics = result["result"]["metrics"]
        lines.append(
            f"| {name} | {_fmt(metrics['total_return'], percent=True)} | {_fmt(metrics['cagr'], percent=True)} | "
            f"{_fmt(metrics['maximum_drawdown'], percent=True)} | {_fmt(metrics['annualized_volatility'], percent=True)} | "
            f"{_fmt(metrics['sharpe_rf_zero'])} | {_fmt(metrics['sortino_target_zero'])} | {_fmt(metrics['calmar'])} | "
            f"{_fmt(result['result']['total_cost_usd'])} |"
        )
    lines.extend(["", "## 基准比较", "", "| 实验 | 基准 | 基准累计收益 | 策略超额收益 | 基准最大回撤 |", "|---|---|---:|---:|---:|"])
    for name, result in runs.items():
        if result.get("status") != "COMPLETED":
            continue
        for benchmark, comparison in result["result"].get("benchmark_comparison", {}).items():
            lines.append(
                f"| {name} | {benchmark} | {_fmt(comparison['total_return'], percent=True)} | "
                f"{_fmt(comparison['excess_return'], percent=True)} | {_fmt(comparison['maximum_drawdown'], percent=True)} |"
            )
    lines.extend(["", "## 可信度与限制", ""])
    for blocker in run.get("manifest", {}).get("blockers", ()):
        lines.append(f"- `{blocker}`")
    if not run.get("manifest", {}).get("blockers"):
        lines.append("- 数据 manifest 未报告阻断项。")
    lines.extend([
        "", "严格模式保留缺失因子和硬门控；`SYNTHETIC_ASSUMPTIONS` 结果仅说明机制对 30/50/70 分假设的敏感度。",
        "未执行建议、合成评分和 USDT 计价近似均不计入真实账户业绩。", "",
    ])
    markdown = "\n".join(lines)
    markdown_path = root / "report.zh-CN.md"
    markdown_path.write_text(markdown + "\n", encoding="utf-8")
    html_path = root / "report.zh-CN.html"
    html_body = "<br>\n".join(html.escape(line) for line in lines)
    html_path.write_text(
        "<!doctype html><meta charset='utf-8'><title>策略回测验证报告</title>"
        "<style>body{max-width:1100px;margin:40px auto;font:15px/1.6 system-ui;color:#172033}"
        "table{border-collapse:collapse}td,th{border:1px solid #ccd;padding:6px}code{background:#f3f4f6}</style>"
        f"<body>{html_body}</body>\n", encoding="utf-8",
    )
    csv_paths = []
    for name, item in runs.items():
        if item.get("status") != "COMPLETED":
            continue
        safe = "".join(character if character.isalnum() or character in "-_" else "_" for character in name)
        valuations = item["result"]["valuations"]
        value_path = root / f"{safe}.valuations.csv"
        with value_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=("timestamp", "total_value_usd", "cash_usd", "drawdown"))
            writer.writeheader()
            for row in valuations:
                writer.writerow({key: row[key] for key in writer.fieldnames})
        trade_path = root / f"{safe}.trades.csv"
        trades = item["result"]["trades"]
        with trade_path.open("w", newline="", encoding="utf-8") as handle:
            fields = ("timestamp", "symbol", "side", "quantity", "reference_price", "execution_price",
                      "gross_notional_usd", "fee_usd", "cash_change_usd", "reason")
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in trades:
                writer.writerow({key: row[key] for key in fields})
        svg_path = root / f"{safe}.equity.svg"
        svg_path.write_text(_equity_svg(valuations), encoding="utf-8")
        csv_paths.extend((str(value_path), str(trade_path), str(svg_path)))
    json_path = root / "result.json"
    json_path.write_text(json.dumps(run, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return {
        "json": str(json_path), "markdown": str(markdown_path), "html": str(html_path),
        "artifacts": csv_paths,
    }


__all__ = ["render_run_report"]
