"""Render finalized backtest results without recalculating strategy outputs.

This module is a reader-facing view over artifacts a run already wrote. It
never recomputes portfolio results, and it renders both the Markdown and the
HTML report from one intermediate block model so the two cannot drift apart.

The report must surface the validity verdict **before** any performance number.
A run can be arithmetically correct and still fail to be a test of the
strategy; ``research/validity_gate.py`` already decides which of the two it is
from the run's own artifacts, so this renderer consumes that verdict instead of
re-deriving it. The manifest's blocker list covers OHLCV completeness only
(``research/data_audit.py``), so an empty blocker list is never reported as
evidence that the signal layer was usable.
"""

from __future__ import annotations

import csv
import html
import json
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import validity_gate

# Blocks are the single source both renderers consume, so a table can never be
# emitted as prose in one format and as a table in the other.
Block = tuple[str, Any]

_TRADE_CODES = (
    validity_gate.ONE_WAY_RATCHER,
    validity_gate.NO_TRADES_IN_WINDOW,
    validity_gate.TRADING_STALLED,
)

# A one-way or never-trading experiment did not measure a strategy, so its
# numbers cannot anchor a benchmark comparison. A stalled tail is a qualifier on
# an otherwise real path: it is labelled and kept, not erased.
_DISQUALIFYING_CODES = (
    validity_gate.ONE_WAY_RATCHER,
    validity_gate.NO_TRADES_IN_WINDOW,
)

_VERDICT_HEADLINE = {
    validity_gate.VERDICT_NOT_A_TEST: "本运行不构成对策略的检验",
    validity_gate.VERDICT_UNDERPOWERED: "本运行有效，但统计功效不足以支撑推断",
    validity_gate.VERDICT_OK: "未发现结构性异议",
}

_SKIPPED_BENCHMARK_NOTES = (
    "单向棘轮与从未交易的实验不进入基准比较表——它们没有测量策略行为；"
    "尾部休眠的实验保留在表中，但状态列会标出 `TRADING_STALLED`。"
)

# Reading order for benchmark families (Strategy V2 Phase 5): the primary
# risk-matched comparison leads, then the untouched-start and exposure-matched
# fair comparisons, then the secondary static anchor, and finally the BTC
# opportunity-cost reference. Unlisted families fall to the end alphabetically.
_BENCHMARK_ORDER = (
    "vol_matched_btc_cash",
    "static_initial_weights",
    "exposure_matched_btc_cash",
    "btc_eth_70_30",
    "btc_buy_and_hold",
)

_DECISION_LEGEND = (
    "`btc_buy_and_hold` = 100% BTC 买入持有（策略主基准）；`btc_eth_70_30` = 70/30 BTC/ETH 买入持有（次基准）；"
    "`static_initial_weights` = 用该实验自己的起始权重买入后不再调整；"
    "`vol_matched_btc_cash` = 按策略自身波动率解出的 BTC/现金固定比例，每日再平衡；"
    "`exposure_matched_btc_cash` = 按策略实现的平均风险仓持有的 BTC/现金固定比例，每日再平衡。"
)

_DECISION_NOTES = (
    "主基准是「同风险」（vol-matched BTC/cash）：把基准波动率压到与策略相同后再比收益，"
    "回答「这套复杂策略在承担相近风险时是否值得」。"
    "「不动起点」回答「主动决策相对『什么都不做』是否创造了价值」；"
    "100% BTC 只是机会成本参考，不是风险匹配基准——"
    "一个以降险为目标的策略可以合理地输给它而赢下同风险口径，只看 100% BTC 会把降险本身误读成失败。"
    "「年化超额」= 策略 CAGR − 基准 CAGR，为正才表示该口径下主动决策创造了价值；"
    "累计收益跨整个窗口，不能与年化数混用。"
)

# The risk-matched weight is solved before rebalancing costs, so the costed
# benchmark only approaches the target as the sample grows (measured: 3.7e-3 at
# 7 returns, 2.8e-4 at 75, ~1e-5 over 995). This is a sanity check against a
# broken match, not a precision requirement, so it stays loose enough not to
# cry wolf on every short window.
_RISK_MATCH_TOLERANCE = 1e-3


def _fmt(value: Any, *, percent: bool = False, digits: int = 6) -> str:
    if value is None:
        return "不可用"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return f"{value:.2%}" if percent else f"{value:.{digits}g}"
    return str(value)


def _day(value: Any) -> str:
    return str(value or "")[:10]


def _ordinal(value: Any) -> float | None:
    """Day number for time-axis spacing, so gaps in trading stay visible."""
    text = _day(value)
    if len(text) != 10:
        return None
    try:
        return float(date.fromisoformat(text).toordinal())
    except ValueError:
        return None


def _equity_svg(points: Sequence[Mapping[str, Any]], width: int = 960, height: int = 360) -> str:
    """Equity curve on a real time axis, with the deepest drawdown marked."""
    if len(points) < 2:
        return "<svg xmlns='http://www.w3.org/2000/svg' width='960' height='120'><text x='20' y='60'>数据不足</text></svg>"
    ordinals = [_ordinal(row.get("timestamp")) for row in points]
    if any(value is None for value in ordinals):
        ordinals = [float(index) for index in range(len(points))]
    span_x = max(max(ordinals) - min(ordinals), 1.0)
    values = [float(row["total_value_usd"]) for row in points]
    low, high = min(values), max(values)
    span_y = max(high - low, 1e-12)
    coords = []
    for index, value in enumerate(values):
        x = 60.0 + (ordinals[index] - min(ordinals)) * (width - 120.0) / span_x
        y = 20.0 + (high - value) * (height - 70.0) / span_y
        coords.append((x, y))
    deepest = min(range(len(values)), key=lambda index: values[index])
    marker_x, marker_y = coords[deepest]
    first_day, last_day = _day(points[0].get("timestamp")), _day(points[-1].get("timestamp"))
    return (
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' "
        f"viewBox='0 0 {width} {height}' role='img'>"
        "<rect width='100%' height='100%' fill='#fffdf7'/>"
        f"<line x1='60' y1='20' x2='60' y2='{height - 50}' stroke='#777' stroke-width='1'/>"
        f"<line x1='60' y1='{height - 50}' x2='{width - 60}' y2='{height - 50}' stroke='#777' stroke-width='1'/>"
        f"<polyline fill='none' stroke='#153e75' stroke-width='2' points='"
        + " ".join(f"{x:.2f},{y:.2f}" for x, y in coords)
        + "'/>"
        f"<circle cx='{marker_x:.2f}' cy='{marker_y:.2f}' r='3.5' fill='#b3261e'/>"
        f"<text x='66' y='18' font-size='12'>${high:,.0f}</text>"
        f"<text x='66' y='{height - 26}' font-size='12'>${low:,.0f}</text>"
        f"<text x='60' y='{height - 12}' font-size='12'>{first_day}</text>"
        f"<text x='{width - 60}' y='{height - 12}' font-size='12' text-anchor='end'>{last_day}</text>"
        f"<text x='{width - 60}' y='34' font-size='12' text-anchor='end' fill='#b3261e'>"
        f"最深回撤 {_fmt(points[deepest].get('drawdown'), percent=True)} @ {_day(points[deepest].get('timestamp'))}</text>"
        "</svg>"
    )


def _safe_name(name: str) -> str:
    """Artifact file stem for an experiment name.

    Experiment keys use ``/`` (``core/core_existing/strict/main_cost``) while the
    validity gate derives its subjects from file stems, where ``/`` has been
    replaced by ``_``. Every lookup across that boundary goes through here.
    """
    return "".join(character if character.isalnum() or character in "-_" else "_" for character in name)


def _experiment_status(validity: Any, names: Sequence[str]) -> dict[str, tuple[str, ...]]:
    """Map each experiment to the degradation codes the validity gate found."""
    original = {_safe_name(name): name for name in names}
    status: dict[str, list[str]] = {name: [] for name in names}
    for finding in getattr(validity, "findings", ()):
        if finding.code not in _TRADE_CODES:
            continue
        name = original.get(finding.subject)
        if name is not None:
            status[name].append(finding.code)
    return {name: tuple(sorted(set(codes))) for name, codes in status.items()}


def _gate_experiment(validity: Any, name: str) -> Mapping[str, Any]:
    """The gate's trade summary for one experiment, keyed by either name form."""
    if validity is None:
        return {}
    experiments = getattr(validity, "experiments", None) or {}
    return experiments.get(_safe_name(name)) or experiments.get(name) or {}


def _status_label(codes: tuple[str, ...], *, known: bool) -> str:
    if not known:
        return "未校验"
    return " / ".join(codes) if codes else "OK"


def _is_comparable(codes: tuple[str, ...]) -> bool:
    """Whether an experiment's numbers may anchor a benchmark comparison."""
    return not any(code in _DISQUALIFYING_CODES for code in codes)


def _benchmark_family(name: str) -> tuple[str, str]:
    """Split a benchmark name into (family, cost basis)."""
    for suffix, basis in (("_zero_cost", "零成本"), ("_investable", "含成本")):
        if name.endswith(suffix):
            return name[: -len(suffix)], basis
    return name, "—"


def _family_sort_key(family: str) -> tuple[int, str]:
    """Keep the policy benchmarks and the two fair comparisons in reading order."""
    if family in _BENCHMARK_ORDER:
        return (_BENCHMARK_ORDER.index(family), family)
    return (len(_BENCHMARK_ORDER), family)


def _decision_rows(
    runs: Mapping[str, Any], status: Mapping[str, tuple[str, ...]]
) -> tuple[list[list[str]], float]:
    """One row per experiment for the two comparisons that decide the question.

    Also returns the largest gap between a risk-matched benchmark's realized
    volatility and the strategy's, so a failed match cannot be read as a result.
    """
    rows: list[list[str]] = []
    worst_match = 0.0
    for name, result in runs.items():
        if result.get("status") != "COMPLETED" or not _is_comparable(status.get(name, ())):
            continue
        payload = result["result"]
        metrics = payload["metrics"]
        comparison = payload.get("benchmark_comparison") or {}
        untouched = comparison.get("static_initial_weights_investable") or {}
        matched = comparison.get("vol_matched_btc_cash_investable") or {}
        strategy_volatility = metrics.get("annualized_volatility")
        matched_volatility = matched.get("annualized_volatility")
        if strategy_volatility is not None and matched_volatility is not None:
            worst_match = max(worst_match, abs(float(matched_volatility) - float(strategy_volatility)))
        rows.append([
            name,
            _fmt(metrics.get("total_return"), percent=True),
            _fmt(strategy_volatility, percent=True),
            _fmt(metrics.get("maximum_drawdown"), percent=True),
            _fmt(metrics.get("sharpe_rf_zero")),
            _fmt(untouched.get("total_return"), percent=True),
            _fmt(untouched.get("excess_return_annualized"), percent=True),
            _fmt(matched.get("total_return"), percent=True),
            _fmt(matched.get("excess_return_annualized"), percent=True),
        ])
    return rows, worst_match


def _render_markdown(blocks: Sequence[Block]) -> str:
    lines: list[str] = []
    for kind, payload in blocks:
        if kind == "h1":
            lines.extend([f"# {payload}", ""])
        elif kind == "h2":
            lines.extend([f"## {payload}", ""])
        elif kind == "h3":
            lines.extend([f"### {payload}", ""])
        elif kind == "p":
            lines.extend([str(payload), ""])
        elif kind == "quote":
            lines.extend([f"> {payload}", ""])
        elif kind == "ul":
            lines.extend([f"- {item}" for item in payload] + [""])
        elif kind == "table":
            header, rows = payload
            lines.append("| " + " | ".join(str(cell) for cell in header) + " |")
            lines.append("|" + "|".join("---" for _ in header) + "|")
            lines.extend("| " + " | ".join(str(cell) for cell in row) + " |" for row in rows)
            lines.append("")
        else:  # pragma: no cover - guards against a typo in a block kind
            raise ValueError(f"unknown report block: {kind}")
    return "\n".join(lines).rstrip("\n") + "\n"


def _render_html(blocks: Sequence[Block]) -> str:
    parts: list[str] = []
    open_list = False
    for kind, payload in blocks:
        if kind != "ul" and open_list:
            parts.append("</ul>")
            open_list = False
        if kind == "h1":
            parts.append(f"<h1>{html.escape(str(payload))}</h1>")
        elif kind == "h2":
            parts.append(f"<h2>{html.escape(str(payload))}</h2>")
        elif kind == "h3":
            parts.append(f"<h3>{html.escape(str(payload))}</h3>")
        elif kind == "p":
            parts.append(f"<p>{html.escape(str(payload))}</p>")
        elif kind == "quote":
            parts.append(f"<blockquote>{html.escape(str(payload))}</blockquote>")
        elif kind == "ul":
            if not open_list:
                parts.append("<ul>")
                open_list = True
            parts.extend(f"<li>{html.escape(str(item))}</li>" for item in payload)
        elif kind == "table":
            header, rows = payload
            head = "".join(f"<th>{html.escape(str(cell))}</th>" for cell in header)
            body = "".join(
                "<tr>" + "".join(f"<td>{html.escape(str(cell))}</td>" for cell in row) + "</tr>"
                for row in rows
            )
            parts.append(f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>")
        else:  # pragma: no cover
            raise ValueError(f"unknown report block: {kind}")
    if open_list:
        parts.append("</ul>")
    return "".join(parts)


def _current_policy() -> tuple[Any | None, float | None]:
    """Resolve the current policy and its coverage floor; never raise."""
    try:
        from ..models.policy import resolve_policy

        resolved = resolve_policy()
        return resolved, float(resolved.scoring.get("minimum_investable_coverage", 0.0))
    except Exception:  # pragma: no cover - a policy failure must not kill the report
        return None, None


def _verdict_blocks(run: Mapping[str, Any], validity: Any) -> list[Block]:
    blocks: list[Block] = [("h2", "结论先行")]
    if validity is None:
        blocks.append(("quote", "未提供运行目录，无法执行有效性校验；本报告的业绩数字缺少可行性判定。"))
        return blocks
    verdict = validity.verdict
    headline = _VERDICT_HEADLINE.get(verdict, "未识别的判决")
    blocks.append(("ul", [
        f"校验判决：`{verdict}`（{headline}）",
        "判据来源：`research/validity_gate.py`，只读取本运行已写出的 "
        "manifest / score-evaluation / trades.csv / decision-evaluation / series。",
        f"发现项：{len(validity.errors)} 个 ERROR，{len(validity.warnings)} 个 WARNING。",
    ]))
    if verdict != validity_gate.VERDICT_OK:
        blocks.append(("quote",
            "按上述判决，本报告的业绩数字不构成对策略的检验，不得用于判断策略有效性；"
            "业绩指标只描述该运行实际走出的路径。"))

    recorded = str((run.get("spec") or {}).get("policy_hash") or "")
    resolved, _ = _current_policy()
    if recorded and resolved is not None:
        try:
            from ..models.policy import policy_hash

            current = policy_hash(resolved)
        except Exception:  # pragma: no cover
            current = ""
        if current and current != recorded:
            blocks.append(("quote",
                f"该运行使用的 policy（`{recorded[:12]}…`）与当前仓库 policy"
                f"（`{current[:12]}…`）不一致；策略可行性类结论描述的是当前 policy，而非该时点。"))
    return blocks


def _readiness_blocks(validity: Any) -> list[Block]:
    blocks: list[Block] = [("h2", "数据就绪度")]
    if validity is None:
        return blocks
    _resolved, floor = _current_policy()

    availability = validity.factor_availability or {}
    rows: list[list[str]] = []
    for scope in sorted(availability):
        counts = availability[scope]
        total = max((sum(bucket.values()) for bucket in counts.values()), default=0)
        for factor in sorted(counts):
            bucket = counts[factor]
            available = int(bucket.get("AVAILABLE", 0))
            rows.append([
                scope, factor, f"{available}/{total}", _fmt(available / total if total else 0.0, percent=True),
                str(int(bucket.get("MISSING", 0))), str(int(bucket.get("NOT_APPLICABLE", 0))),
            ])
    if rows:
        blocks.append(("h3", "因子可用率"))
        blocks.append(("table", (
            ["域", "因子", "可用读数", "可用占比", "MISSING", "NOT_APPLICABLE"], rows,
        )))
        dead = sum(1 for row in rows if row[2].startswith("0/"))
        blocks.append(("p",
            f"共 {len(rows)} 个「域 × 因子」组合，其中 {dead} 个全程零可用读数。"
            "缺失因子按配置权重保留并把原始分收缩到中性 50，因此评分是可用因子的约化函数，"
            "而不是完整模型。"))

    coverage_rows: list[list[str]] = []
    for scope in sorted(validity.coverage or {}):
        stats = validity.coverage[scope]
        bands = stats.get("bands") or {}
        label = "，".join(f"{key} {int(value)}" for key, value in sorted(bands.items())) or "—"
        coverage_rows.append([
            scope, str(int(stats.get("samples", 0))), _fmt(stats.get("min")),
            _fmt(stats.get("median")), _fmt(stats.get("max")), label,
        ])
    if coverage_rows:
        blocks.append(("h3", "评分覆盖率"))
        blocks.append(("table", (["域", "读数", "最小", "中位", "最大", "档位分布"], coverage_rows)))
        if floor is not None:
            below = [
                scope for scope in sorted(validity.coverage or {})
                if float(validity.coverage[scope].get("median") or 0.0) < floor
            ]
            if below:
                blocks.append(("quote",
                    f"中位覆盖率低于 `scoring.minimum_investable_coverage` = {floor:.2f} 的域："
                    f"{'、'.join(below)}。覆盖率不足会把评分带强制压到 LOW，"
                    "而 `confidence_deployment_factor.LOW` 在政策里是硬零，"
                    "因此所有依分数加仓的路径都会被封死。"))

    bands = (validity.feasibility or {}).get("score_bands") or {}
    if bands:
        band_rows = [
            [name, _fmt(payload.get("coverage")), _fmt(payload.get("low")), _fmt(payload.get("high"))]
            for name, payload in sorted(bands.items())
        ]
        blocks.append(("h3", "各 profile 可达分数区间"))
        blocks.append(("table", (["profile", "覆盖率", "可达下限", "可达上限"], band_rows)))

    blocks.append(("p",
        "说明：`manifest` 的阻断项只覆盖 OHLCV 完整性（`research/data_audit.py`），"
        "`strict_ready` 同样只表示行情齐全，两者都不代表信号层、评分覆盖或决策样本可用。"))
    return blocks


def _summary_blocks(run: Mapping[str, Any], validity: Any) -> list[Block]:
    runs = run.get("runs") or {}
    status = _experiment_status(validity, list(runs)) if validity is not None else {}
    known = validity is not None
    blocks: list[Block] = [("h2", "组合结果")]
    rows: list[list[str]] = []
    for name, result in runs.items():
        label = _status_label(status.get(name, ()), known=known)
        if result.get("status") != "COMPLETED":
            rows.append([name, label, str(result.get("status"))] + ["—"] * 6)
            continue
        metrics = result["result"]["metrics"]
        rows.append([
            name, label,
            _fmt(metrics.get("total_return"), percent=True),
            _fmt(metrics.get("cagr"), percent=True),
            _fmt(metrics.get("maximum_drawdown"), percent=True),
            _fmt(metrics.get("annualized_volatility"), percent=True),
            _fmt(metrics.get("sharpe_rf_zero")),
            _fmt(result["result"].get("total_cost_usd")),
        ])
    blocks.append(("table", (
        ["实验", "状态", "累计收益", "CAGR", "最大回撤", "波动率", "Sharpe", "成本 USD"], rows,
    )))
    disqualified = [
        name for name, codes in status.items() if codes and not _is_comparable(codes)
    ]
    blocks.append(("p",
        f"状态列由有效性校验判定：`{validity_gate.ONE_WAY_RATCHER}`（只朝一个方向交易）、"
        f"`{validity_gate.NO_TRADES_IN_WINDOW}`（从未交易）、"
        f"`{validity_gate.TRADING_STALLED}`（尾部长时间不交易）。"
        f"本次共 {len(rows) - sum(1 for codes in status.values() if not codes)} 个实验带标记，"
        f"其中 {len(disqualified)} 个（单向棘轮 / 从未交易）不构成策略行为，其数字不代表策略业绩。"))
    return blocks


def _benchmark_blocks(run: Mapping[str, Any], validity: Any) -> list[Block]:
    runs = run.get("runs") or {}
    status = _experiment_status(validity, list(runs)) if validity is not None else {}
    blocks: list[Block] = [("h2", "基准比较")]

    decision_rows, worst_match = _decision_rows(runs, status)
    if decision_rows:
        blocks.append(("h3", "决策对照（先看这张）"))
        blocks.append(("table", (
            ["实验", "策略累计", "策略波动率", "策略MaxDD", "策略Sharpe",
             "不动起点累计", "不动起点年化超额", "同风险累计", "同风险年化超额"],
            decision_rows,
        )))
        blocks.append(("p", _DECISION_NOTES))
        if worst_match > _RISK_MATCH_TOLERANCE:
            blocks.append(("p",
                f"⚠️ 同风险基准的实际波动率与策略波动率最大相差 {worst_match:.2%}，"
                "该口径的对照精度不足，不要据此下结论。"))

    blocks.append(("h3", "逐个基准明细"))
    blocks.append(("p", _SKIPPED_BENCHMARK_NOTES))
    rows: list[list[str]] = []
    skipped: list[tuple[str, tuple[str, ...]]] = []
    for name, result in runs.items():
        if result.get("status") != "COMPLETED":
            continue
        codes = status.get(name, ())
        if not _is_comparable(codes):
            skipped.append((name, codes))
            continue
        payload = result["result"]
        metrics = payload["metrics"]
        families: dict[str, dict[str, Mapping[str, Any]]] = {}
        for benchmark, comparison in (payload.get("benchmark_comparison") or {}).items():
            family, basis = _benchmark_family(benchmark)
            families.setdefault(family, {})[basis] = comparison
        for family in sorted(families, key=_family_sort_key):
            variants = families[family]
            zero, investable = variants.get("零成本"), variants.get("含成本")
            reference = investable or zero or {}
            if not reference:
                continue
            rows.append([
                name, family,
                _fmt((zero or {}).get("total_return"), percent=True),
                _fmt((investable or {}).get("total_return"), percent=True),
                _fmt(metrics.get("total_return"), percent=True),
                _fmt(reference.get("excess_return"), percent=True),
                _fmt(reference.get("excess_return_annualized"), percent=True),
                _fmt(reference.get("maximum_drawdown"), percent=True),
                _fmt(metrics.get("maximum_drawdown"), percent=True),
                _fmt(reference.get("sharpe_delta")),
                _fmt(reference.get("annualized_volatility"), percent=True),
            ])
    if rows:
        blocks.append(("table", (
            ["实验", "基准", "基准(零成本)", "基准(含成本)", "策略", "累计超额",
             "年化超额", "基准MaxDD", "策略MaxDD", "Sharpe差", "基准波动率"], rows,
        )))
        blocks.append(("p", _DECISION_LEGEND))
        blocks.append(("p",
            "超额 = 策略累计收益 − 基准累计收益，单位为百分点；同一行的 MaxDD 分成基准与策略两列，"
            "避免把基准回撤误读为策略回撤。风险不对等的基准（如 100% BTC）只能说明仓位与风险差异，"
            "不能单独证明策略优劣。「基准波动率」让「同风险」一行可自证：它应当等于同行的策略波动率。"))
    else:
        blocks.append(("p", "没有可比实验参与基准比较。"))
    if skipped:
        tally: dict[str, int] = {}
        for _name, codes in skipped:
            for code in codes:
                if code in _DISQUALIFYING_CODES:
                    tally[code] = tally.get(code, 0) + 1
        detail = "，".join(f"{code} {count} 个" for code, count in sorted(tally.items()))
        blocks.append(("p",
            f"已跳过 {len(skipped)} 个实验（{detail}）：它们没有测量策略行为，"
            "其数字不能作为策略业绩或基准比较的依据。"
            "其余状态标记（如 `TRADING_STALLED`）只出现在状态列，不构成剔除理由。"))

    strategy_yearly: dict[str, float] = {}
    benchmark_yearly: dict[str, dict[str, float]] = {}
    for name, result in runs.items():
        if result.get("status") != "COMPLETED":
            continue
        yearly = (result["result"]["metrics"].get("calendar_returns") or {}).get("yearly") or {}
        if _is_comparable(status.get(name, ())):
            for year, value in yearly.items():
                strategy_yearly.setdefault(year, value)
        for benchmark, payload in (result["result"].get("benchmarks") or {}).items():
            family, _basis = _benchmark_family(benchmark)
            target = benchmark_yearly.setdefault(family, {})
            for year, value in ((payload.get("metrics") or {}).get("calendar_returns") or {}).get("yearly", {}).items():
                target.setdefault(year, value)
    years = sorted(set(strategy_yearly) | {year for values in benchmark_yearly.values() for year in values})
    if years:
        table_rows = [["策略（可比实验并集）"] + [_fmt(strategy_yearly.get(y), percent=True) for y in years]]
        table_rows.extend(
            [family] + [_fmt(benchmark_yearly[family].get(y), percent=True) for y in years]
            for family in sorted(benchmark_yearly, key=_family_sort_key)
        )
        blocks.append(("h3", "分年度收益（策略与基准）"))
        blocks.append(("table", (["来源"] + years, table_rows)))
        blocks.append(("p",
            "分年度数据来自引擎已产出的 `metrics.calendar_returns.yearly`，未在此重算。"
            "单一区间的累计差额可能完全由其中某一年造成，因此累计数必须与分年度数一起看。"))
    return blocks


def _diagnostics_blocks(run: Mapping[str, Any], validity: Any) -> list[Block]:
    """Exposure timing, floor pinning, and cash-yield sensitivity diagnostics."""
    runs = run.get("runs") or {}
    blocks: list[Block] = [("h2", "风险与择时诊断")]
    timing_rows: list[list[str]] = []
    for name, result in runs.items():
        if result.get("status") != "COMPLETED":
            continue
        payload = result["result"]
        timing = payload.get("exposure_timing")
        if not timing:
            continue
        floor = payload.get("regime_floor_diagnostics") or {}
        cash = payload.get("cash_yield_sensitivity") or {}
        scenarios = cash.get("scenarios") or {}
        timing_rows.append([
            name,
            _fmt(timing.get("realized_average_risky_weight"), percent=True),
            _fmt(timing.get("path_total_return"), percent=True),
            _fmt(timing.get("constant_total_return"), percent=True),
            _fmt(timing.get("timing_contribution"), percent=True),
            _fmt(floor.get("overlay_binding_share"), percent=True),
            _fmt(floor.get("label_defensive_or_worse_share"), percent=True),
            _fmt(floor.get("drawdown_at_or_below_defensive_floor_share"), percent=True),
            _fmt((scenarios.get("yield_4.00%") or {}).get("cagr"), percent=True),
            _fmt((scenarios.get("yield_5.00%") or {}).get("cagr"), percent=True),
        ])
    if timing_rows:
        blocks.append(("h3", "择时贡献与地板钉住"))
        blocks.append(("table", (
            ["实验", "平均风险仓", "路径累计", "同均仓累计", "择时贡献",
             "overlay 生效占比", "标签≥防御占比", "回撤≤地板占比",
             "现金4%时CAGR", "现金5%时CAGR"], timing_rows,
        )))
        blocks.append(("p",
            "「择时贡献」= 按策略实际风险仓路径持有 BTC 腿的累计收益 − 按其平均风险仓常数持有的累计收益，"
            "带符号：正数说明持仓时机在均值之上，负数说明把敞口加在了腿下跌的时段。"
            "「标签≥防御占比」与「回撤≤地板占比」接近相等即 `REGIME_PINNED_BY_OWN_DRAWDOWN`："
            "regime 标签跟随账面回撤而非市场。现金收益敏感性是诊断口径：回放把稳定腿记为零收益，"
            "这里按假设年化收益重记策略自身路径，不改变任何引擎记账，也不与零收益基准直接比较。"))
    else:
        blocks.append(("p", "本运行没有择时诊断数据。"))
    return blocks


def _exposure_blocks(run: Mapping[str, Any], validity: Any) -> list[Block]:
    runs = run.get("runs") or {}
    status = _experiment_status(validity, list(runs)) if validity is not None else {}
    window_end = _day((run.get("spec") or {}).get("end_at"))
    blocks: list[Block] = [("h2", "仓位、换手与存续")]
    rows: list[list[str]] = []
    for name, result in runs.items():
        if result.get("status") != "COMPLETED":
            continue
        payload = result["result"]
        valuations = payload.get("valuations") or []
        summary = _gate_experiment(validity, name)
        trades = int(summary.get("trades") or len(payload.get("trades") or []))
        last_trade = summary.get("last_trade")
        stall = "—"
        if last_trade and window_end:
            try:
                stall = str((date.fromisoformat(window_end) - date.fromisoformat(last_trade)).days)
            except ValueError:
                stall = "—"
        end_cash = "—"
        if valuations:
            last = valuations[-1]
            total = float(last.get("total_value_usd") or 0.0)
            if total:
                end_cash = _fmt(float(last.get("cash_usd") or 0.0) / total, percent=True)
        rows.append([
            name, _status_label(status.get(name, ()), known=validity is not None),
            str(trades), str(summary.get("first_trade") or "—"), str(last_trade or "—"), stall,
            end_cash, _fmt(payload.get("average_cash_weight"), percent=True),
            _fmt(payload.get("total_turnover")), _fmt(payload.get("total_cost_usd")),
        ])
    if rows:
        blocks.append(("table", (
            ["实验", "状态", "成交笔数", "首笔", "末笔", "末笔距期末(天)",
             "期末现金占比", "平均现金占比", "总换手", "总成本 USD"], rows,
        )))
        blocks.append(("p",
            "「末笔距期末」大于 60 天即说明曲线尾部处于无人管理状态，"
            "该段产生的回撤不代表策略的主动风控结果。"))
    return blocks


def _evaluation_blocks(run_dir: Path | None) -> list[Block]:
    if run_dir is None:
        return []
    blocks: list[Block] = [("h2", "评估产物")]
    decision = _load_json(run_dir / "decision-evaluation.json")
    if decision:
        rows_ = decision.get("rows") or []
        classes: dict[str, int] = {}
        referenced = 0
        for row in rows_:
            label = str(row.get("performance_class", "UNKNOWN"))
            classes[label] = classes.get(label, 0) + 1
            if row.get("reference") is not None:
                referenced += 1
        blocks.append(("h3", "决策评估"))
        blocks.append(("ul", [
            f"有效决策 {decision.get('valid_decisions')} 条；被排除 {decision.get('excluded_decisions')} 条。",
            f"样本行 {len(rows_)} 行，其中带已实现参考价的 {referenced} 行。",
            f"业绩分类：{'，'.join(f'{key} {value}' for key, value in sorted(classes.items())) or '—'}。",
        ]))
        if not referenced:
            blocks.append(("quote",
                "没有任何样本行带已实现参考价，因此决策质量没有样本，"
                "既不能判为有效，也不能判为无效。"))
    score = _load_json(run_dir / "score-evaluation.json")
    if score:
        blocks.append(("h3", "评分评估"))
        blocks.append(("ul", [
            f"合同：`{score.get('contract', 'UNKNOWN')}`。",
            f"域：{'、'.join(sorted(score.get('scopes') or {})) or '—'}。",
        ]))
    return blocks if len(blocks) > 1 else []


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


def _write_valuations_and_trades(root: Path, name: str, result: Mapping[str, Any]) -> list[str]:
    safe = _safe_name(name)
    payload = result["result"]
    valuations = payload["valuations"]
    value_path = root / f"{safe}.valuations.csv"
    with value_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("timestamp", "total_value_usd", "cash_usd", "drawdown"))
        writer.writeheader()
        for row in valuations:
            writer.writerow({key: row[key] for key in writer.fieldnames})
    trade_path = root / f"{safe}.trades.csv"
    fields = ("timestamp", "symbol", "side", "quantity", "reference_price", "execution_price",
              "gross_notional_usd", "fee_usd", "cash_change_usd", "reason")
    with trade_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in payload["trades"]:
            writer.writerow({key: row[key] for key in fields})
    svg_path = root / f"{safe}.equity.svg"
    svg_path.write_text(_equity_svg(valuations), encoding="utf-8")
    return [str(value_path), str(trade_path), str(svg_path)]


def _slim_summary(run: Mapping[str, Any], validity: Any) -> dict[str, Any]:
    """Machine-readable digest, without the per-day valuations of every experiment."""
    runs = run.get("runs") or {}
    status = _experiment_status(validity, list(runs)) if validity is not None else {}
    return {
        "run_id": run.get("run_id"),
        "verdict": getattr(validity, "verdict", None),
        "experiments": {
            name: {
                "status": list(status.get(name, ())),
                "metrics": result["result"].get("metrics") if result.get("status") == "COMPLETED" else None,
                "benchmark_comparison": (
                    result["result"].get("benchmark_comparison") if result.get("status") == "COMPLETED" else None
                ),
            }
            for name, result in runs.items()
        },
    }


def _window_title(run: Mapping[str, Any]) -> str:
    spec = run.get("spec") or {}
    start, end = _day(spec.get("start_at")), _day(spec.get("end_at"))
    if start and end:
        return f"{start} 至 {end} 回测与验证报告"
    return "历史回测与验证报告"


def render_run_report(
    run: Mapping[str, Any], output_root: str | Path, *, run_dir: str | Path | None = None
) -> dict[str, Any]:
    """Render the Markdown and HTML report plus a slim machine-readable summary.

    ``run_dir`` defaults to the parent of ``output_root``, because the default
    output root is ``<run_dir>/report``. The trade CSVs are written *before* the
    validity gate runs: the gate classifies each experiment from those files, so
    calling it first would classify the previous run's leftovers.
    """
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    resolved_run_dir = Path(run_dir) if run_dir is not None else root.parent

    runs = run.get("runs") or {}
    artifacts: list[str] = []
    for name, item in runs.items():
        if item.get("status") != "COMPLETED":
            continue
        artifacts.extend(_write_valuations_and_trades(root, name, item))

    validity: Any = None
    if resolved_run_dir.is_dir():
        validity = validity_gate.build_validity_report(resolved_run_dir)

    blocks: list[Block] = [("h1", _window_title(run))]
    blocks.append(("ul", [
        f"Run ID：`{run.get('run_id', 'unknown')}`",
        f"数据口径：`{run.get('valuation_basis', 'UNKNOWN')}`",
        f"严格点时资格：`{run.get('strict_status', 'UNKNOWN')}`（仅表示行情完整性，不代表信号层可用）",
        f"运行目录：`{resolved_run_dir}`",
    ]))
    blocks.append(("p", "本报告属于历史诊断与稳健性验证，不是未见样本上的预期收益证明。"))
    blocks.extend(_verdict_blocks(run, validity))
    blocks.extend(_readiness_blocks(validity))
    blocks.extend(_summary_blocks(run, validity))
    blocks.extend(_benchmark_blocks(run, validity))
    blocks.extend(_diagnostics_blocks(run, validity))
    blocks.extend(_exposure_blocks(run, validity))
    blocks.extend(_evaluation_blocks(resolved_run_dir if resolved_run_dir.is_dir() else None))

    if validity is not None and validity.findings:
        blocks.append(("h2", "校验发现项"))
        blocks.append(("ul", [
            f"`{finding.severity}` `{finding.code}` {finding.subject} — {finding.message}"
            for finding in validity.findings
        ]))

    blocks.append(("h2", "限制"))
    blockers = tuple((run.get("manifest") or {}).get("blockers") or ())
    if blockers:
        blocks.append(("ul", [f"`{blocker}`" for blocker in blockers]))
    else:
        blocks.append(("p",
            "manifest 未报告阻断项——但阻断项只覆盖 OHLCV 完整性，"
            "不能据此认为信号层、评分覆盖或决策样本足以支撑结论。"))
    blocks.append(("p",
        "严格模式保留缺失因子和硬门控；`SYNTHETIC_ASSUMPTIONS` 结果仅说明机制对 30/50/70 分假设的敏感度。"
        "未执行建议、合成评分和 USDT 计价近似均不计入真实账户业绩。"))

    markdown_path = root / "report.zh-CN.md"
    markdown_path.write_text(_render_markdown(blocks), encoding="utf-8")
    html_path = root / "report.zh-CN.html"
    html_path.write_text(
        "<!doctype html><meta charset='utf-8'><title>策略回测验证报告</title>"
        "<style>body{max-width:1180px;margin:40px auto;padding:0 16px;font:15px/1.6 system-ui;color:#172033}"
        "table{border-collapse:collapse;margin:12px 0;font-size:13px}"
        "td,th{border:1px solid #ccd;padding:6px 8px;text-align:right;vertical-align:top}"
        "th:first-child,td:first-child{text-align:left}"
        "code{background:#f3f4f6;padding:1px 4px;border-radius:3px}"
        "blockquote{margin:12px 0;padding:8px 14px;border-left:3px solid #b3261e;background:#fdf3f2}"
        "ul{margin:8px 0 8px 18px}h1{font-size:24px}h2{font-size:19px;margin-top:32px}</style>"
        f"<body>{_render_html(blocks)}</body>\n",
        encoding="utf-8",
    )

    summary_path = root / "summary.json"
    summary_path.write_text(
        json.dumps(_slim_summary(run, validity), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return {
        "markdown": str(markdown_path),
        "html": str(html_path),
        "summary": str(summary_path),
        "validity": getattr(validity, "verdict", None),
        "artifacts": artifacts,
    }


__all__ = ["render_run_report"]
