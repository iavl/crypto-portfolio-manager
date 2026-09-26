# 2021-07-01 至 2023-12-31 回测与验证报告

- Run ID：`strategy-validation-2021-2023-bear`
- 数据口径：`USD_ASSUMED_STABLECOIN_PEG`
- 严格点时资格：`ELIGIBLE_WITH_ASSUMED_STABLE_PEG`（仅表示行情完整性，不代表信号层可用）
- 运行目录：`/Users/albert/.local/share/crypto-portfolio-manager/research/backtests/strategy-validation-2021-2023-bear`

本报告属于历史诊断与稳健性验证，不是未见样本上的预期收益证明。

## 结论先行

- 校验判决：`DEGENERATE_NOT_A_TEST_OF_THE_STRATEGY`（本运行不构成对策略的检验）
- 判据来源：`research/validity_gate.py`，只读取本运行已写出的 manifest / score-evaluation / trades.csv / decision-evaluation / series。
- 发现项：5 个 ERROR，36 个 WARNING。

> 按上述判决，本报告的业绩数字不构成对策略的检验，不得用于判断策略有效性；业绩指标只描述该运行实际走出的路径。

> 该运行使用的 policy（`0aa9fc65704d…`）与当前仓库 policy（`52e62aa06de8…`）不一致；策略可行性类结论描述的是当前 policy，而非该时点。

## 数据就绪度

### 因子可用率

| 域 | 因子 | 可用读数 | 可用占比 | MISSING | NOT_APPLICABLE |
|---|---|---|---|---|---|
| core | btc_valuation | 913/1826 | 50.00% | 0 | 913 |
| core | capital_flows | 1826/1826 | 100.00% | 0 | 0 |
| core | fundamentals | 0/1826 | 0.00% | 913 | 913 |
| core | macro_liquidity | 913/1826 | 50.00% | 0 | 913 |
| core | onchain | 913/1826 | 50.00% | 0 | 913 |
| core | relative_strength_btc | 913/1826 | 50.00% | 0 | 913 |
| core | trend | 1826/1826 | 100.00% | 0 | 0 |
| core | valuation | 0/1826 | 0.00% | 913 | 913 |
| full | btc_valuation | 913/4565 | 20.00% | 0 | 3652 |
| full | capital_flows | 3652/4565 | 80.00% | 0 | 913 |
| full | fundamentals | 0/4565 | 0.00% | 3652 | 913 |
| full | macro_liquidity | 913/4565 | 20.00% | 0 | 3652 |
| full | onchain | 913/4565 | 20.00% | 1826 | 1826 |
| full | relative_strength_btc | 3652/4565 | 80.00% | 0 | 913 |
| full | trend | 4565/4565 | 100.00% | 0 | 0 |
| full | valuation | 0/4565 | 0.00% | 3652 | 913 |

共 16 个「域 × 因子」组合，其中 4 个全程零可用读数。缺失因子按配置权重保留并把原始分收缩到中性 50，因此评分是可用因子的约化函数，而不是完整模型。

### 评分覆盖率

| 域 | 读数 | 最小 | 中位 | 最大 | 档位分布 |
|---|---|---|---|---|---|
| core | 1826 | 0.58 | 0.69 | 0.8 | HIGH 913，MEDIUM 913 |
| full | 4565 | 0.45 | 0.51 | 0.8 | HIGH 913，LOW 913，MEDIUM 2739 |

> 中位覆盖率低于 `scoring.minimum_investable_coverage` = 0.60 的域：full。覆盖率不足会把评分带强制压到 LOW，而 `confidence_deployment_factor.LOW` 在政策里是硬零，因此所有依分数加仓的路径都会被封死。

### 各 profile 可达分数区间

| profile | 覆盖率 | 可达下限 | 可达上限 |
|---|---|---|---|
| btc | 0.8 | 10 | 90 |
| default | 0.58 | 21 | 79 |
| defi_protocol | 0.45 | 27.5 | 72.5 |

说明：`manifest` 的阻断项只覆盖 OHLCV 完整性（`research/data_audit.py`），`strict_ready` 同样只表示行情齐全，两者都不代表信号层、评分覆盖或决策样本可用。

## 组合结果

| 实验 | 状态 | 累计收益 | CAGR | 最大回撤 | 波动率 | Sharpe | 成本 USD |
|---|---|---|---|---|---|---|---|
| core/all_cash/strict/cost_0bps | TRADING_STALLED | 5.51% | 2.17% | -28.31% | 13.13% | 0.229163 | 0 |
| core/all_cash/strict/cost_10bps | TRADING_STALLED | 5.47% | 2.15% | -28.30% | 13.12% | 0.227908 | 47.0381 |
| core/all_cash/strict/cost_25bps | TRADING_STALLED | 5.40% | 2.12% | -28.31% | 13.12% | 0.225903 | 117.576 |
| core/all_cash/strict/main_cost | TRADING_STALLED | 5.44% | 2.14% | -28.31% | 13.12% | 0.227149 | 70.5527 |
| core/all_cash/strict/weekly_sensitivity | TRADING_STALLED | 7.57% | 2.96% | -15.33% | 7.86% | 0.410763 | 49.3999 |
| core/all_cash/synthetic_30/main_cost | TRADING_STALLED | 4.52% | 1.79% | -23.30% | 11.45% | 0.211889 | 150.79 |
| core/all_cash/synthetic_50/main_cost | TRADING_STALLED | 4.24% | 1.67% | -23.33% | 11.18% | 0.204375 | 146.802 |
| core/all_cash/synthetic_70/main_cost | OK | 4.55% | 1.80% | -23.10% | 11.14% | 0.215636 | 139.536 |
| core/core_existing/strict/cost_0bps | TRADING_STALLED | 20.00% | 7.57% | -26.89% | 17.69% | 0.500755 | 0 |
| core/core_existing/strict/cost_10bps | TRADING_STALLED | 19.90% | 7.53% | -26.91% | 17.70% | 0.498712 | 109.786 |
| core/core_existing/strict/cost_25bps | TRADING_STALLED | 19.74% | 7.47% | -26.94% | 17.70% | 0.495648 | 274.381 |
| core/core_existing/strict/main_cost | TRADING_STALLED | 19.84% | 7.51% | -26.92% | 17.70% | 0.497722 | 164.631 |
| core/core_existing/strict/weekly_sensitivity | TRADING_STALLED | 21.41% | 8.07% | -23.39% | 16.66% | 0.549047 | 129.966 |
| core/core_existing/synthetic_30/main_cost | TRADING_STALLED | 20.04% | 7.58% | -26.37% | 17.57% | 0.50373 | 163.899 |
| core/core_existing/synthetic_50/main_cost | TRADING_STALLED | 19.58% | 7.41% | -23.27% | 16.98% | 0.506264 | 143.796 |
| core/core_existing/synthetic_70/main_cost | TRADING_STALLED | 18.40% | 6.99% | -23.44% | 16.73% | 0.487475 | 142.719 |
| full/all_cash/strict/cost_0bps | TRADING_STALLED | 5.51% | 2.17% | -28.31% | 13.13% | 0.229163 | 0 |
| full/all_cash/strict/cost_10bps | TRADING_STALLED | 5.47% | 2.15% | -28.30% | 13.12% | 0.227908 | 47.0381 |
| full/all_cash/strict/cost_25bps | TRADING_STALLED | 5.40% | 2.12% | -28.31% | 13.12% | 0.225903 | 117.576 |
| full/all_cash/strict/main_cost | TRADING_STALLED | 5.44% | 2.14% | -28.31% | 13.12% | 0.227149 | 70.5527 |
| full/all_cash/strict/weekly_sensitivity | OK | 6.68% | 2.62% | -15.33% | 7.66% | 0.375993 | 46.9702 |
| full/all_cash/synthetic_30/main_cost | TRADING_STALLED | 3.45% | 1.37% | -27.98% | 12.65% | 0.170572 | 132.521 |
| full/all_cash/synthetic_50/main_cost | OK | 5.11% | 2.01% | -22.85% | 11.12% | 0.234854 | 182.526 |
| full/all_cash/synthetic_70/main_cost | TRADING_STALLED | 5.56% | 2.19% | -22.75% | 11.00% | 0.251813 | 167.941 |
| full/core_existing/strict/cost_0bps | TRADING_STALLED | 20.00% | 7.57% | -26.89% | 17.69% | 0.500755 | 0 |
| full/core_existing/strict/cost_10bps | TRADING_STALLED | 19.90% | 7.53% | -26.91% | 17.70% | 0.498712 | 109.786 |
| full/core_existing/strict/cost_25bps | TRADING_STALLED | 19.74% | 7.47% | -26.94% | 17.70% | 0.495648 | 274.381 |
| full/core_existing/strict/main_cost | TRADING_STALLED | 19.84% | 7.51% | -26.92% | 17.70% | 0.497722 | 164.631 |
| full/core_existing/strict/weekly_sensitivity | TRADING_STALLED | 21.41% | 8.07% | -23.39% | 16.66% | 0.549047 | 129.966 |
| full/core_existing/synthetic_30/main_cost | TRADING_STALLED | 19.79% | 7.49% | -23.29% | 16.97% | 0.510647 | 171.997 |
| full/core_existing/synthetic_50/main_cost | OK | 19.40% | 7.35% | -23.14% | 16.91% | 0.504213 | 185.283 |
| full/core_existing/synthetic_70/main_cost | TRADING_STALLED | 19.38% | 7.34% | -22.67% | 16.63% | 0.509267 | 166.331 |

状态列由有效性校验判定：`ONE_WAY_RATCHER`（只朝一个方向交易）、`NO_TRADES_IN_WINDOW`（从未交易）、`TRADING_STALLED`（尾部长时间不交易）。本次共 28 个实验带标记，其中 0 个（单向棘轮 / 从未交易）不构成策略行为，其数字不代表策略业绩。

## 基准比较

### 决策对照（先看这张）

| 实验 | 策略累计 | 策略波动率 | 策略MaxDD | 策略Sharpe | 不动起点累计 | 不动起点年化超额 | 同风险累计 | 同风险年化超额 |
|---|---|---|---|---|---|---|---|---|
| core/all_cash/strict/cost_0bps | 5.51% | 13.13% | -28.31% | 0.229163 | 0.00% | 2.17% | 12.19% | -2.54% |
| core/all_cash/strict/cost_10bps | 5.47% | 13.12% | -28.30% | 0.227908 | 0.00% | 2.15% | 11.78% | -2.40% |
| core/all_cash/strict/cost_25bps | 5.40% | 13.12% | -28.31% | 0.225903 | 0.00% | 2.12% | 11.19% | -2.21% |
| core/all_cash/strict/main_cost | 5.44% | 13.12% | -28.31% | 0.227149 | 0.00% | 2.14% | 11.58% | -2.34% |
| core/all_cash/strict/weekly_sensitivity | 7.57% | 7.86% | -15.33% | 0.410763 | 0.00% | 2.96% | 7.30% | 0.10% |
| core/all_cash/synthetic_30/main_cost | 4.52% | 11.45% | -23.30% | 0.211889 | 0.00% | 1.79% | 10.28% | -2.21% |
| core/all_cash/synthetic_50/main_cost | 4.24% | 11.18% | -23.33% | 0.204375 | 0.00% | 1.67% | 10.07% | -2.24% |
| core/all_cash/synthetic_70/main_cost | 4.55% | 11.14% | -23.10% | 0.215636 | 0.00% | 1.80% | 10.03% | -2.10% |
| core/core_existing/strict/cost_0bps | 20.00% | 17.69% | -26.89% | 0.500755 | 12.23% | 2.84% | 15.59% | 1.60% |
| core/core_existing/strict/cost_10bps | 19.90% | 17.70% | -26.91% | 0.498712 | 12.14% | 2.84% | 15.09% | 1.74% |
| core/core_existing/strict/cost_25bps | 19.74% | 17.70% | -26.94% | 0.495648 | 12.01% | 2.83% | 14.35% | 1.96% |
| core/core_existing/strict/main_cost | 19.84% | 17.70% | -26.92% | 0.497722 | 12.09% | 2.84% | 14.85% | 1.82% |
| core/core_existing/strict/weekly_sensitivity | 21.41% | 16.66% | -23.39% | 0.549047 | 12.09% | 3.40% | 14.15% | 2.63% |
| core/core_existing/synthetic_30/main_cost | 20.04% | 17.57% | -26.37% | 0.50373 | 12.09% | 2.91% | 14.76% | 1.92% |
| core/core_existing/synthetic_50/main_cost | 19.58% | 16.98% | -23.27% | 0.506264 | 12.09% | 2.74% | 14.36% | 1.90% |
| core/core_existing/synthetic_70/main_cost | 18.40% | 16.73% | -23.44% | 0.487475 | 12.09% | 2.32% | 14.20% | 1.53% |
| full/all_cash/strict/cost_0bps | 5.51% | 13.13% | -28.31% | 0.229163 | 0.00% | 2.17% | 12.19% | -2.54% |
| full/all_cash/strict/cost_10bps | 5.47% | 13.12% | -28.30% | 0.227908 | 0.00% | 2.15% | 11.78% | -2.40% |
| full/all_cash/strict/cost_25bps | 5.40% | 13.12% | -28.31% | 0.225903 | 0.00% | 2.12% | 11.19% | -2.21% |
| full/all_cash/strict/main_cost | 5.44% | 13.12% | -28.31% | 0.227149 | 0.00% | 2.14% | 11.58% | -2.34% |
| full/all_cash/strict/weekly_sensitivity | 6.68% | 7.66% | -15.33% | 0.375993 | 0.00% | 2.62% | 7.13% | -0.17% |
| full/all_cash/synthetic_30/main_cost | 3.45% | 12.65% | -27.98% | 0.170572 | 0.00% | 1.37% | 11.23% | -2.98% |
| full/all_cash/synthetic_50/main_cost | 5.11% | 11.12% | -22.85% | 0.234854 | 0.00% | 2.01% | 10.02% | -1.88% |
| full/all_cash/synthetic_70/main_cost | 5.56% | 11.00% | -22.75% | 0.251813 | 0.00% | 2.19% | 9.93% | -1.67% |
| full/core_existing/strict/cost_0bps | 20.00% | 17.69% | -26.89% | 0.500755 | 12.23% | 2.84% | 15.59% | 1.60% |
| full/core_existing/strict/cost_10bps | 19.90% | 17.70% | -26.91% | 0.498712 | 12.14% | 2.84% | 15.09% | 1.74% |
| full/core_existing/strict/cost_25bps | 19.74% | 17.70% | -26.94% | 0.495648 | 12.01% | 2.83% | 14.35% | 1.96% |
| full/core_existing/strict/main_cost | 19.84% | 17.70% | -26.92% | 0.497722 | 12.09% | 2.84% | 14.85% | 1.82% |
| full/core_existing/strict/weekly_sensitivity | 21.41% | 16.66% | -23.39% | 0.549047 | 12.09% | 3.40% | 14.15% | 2.63% |
| full/core_existing/synthetic_30/main_cost | 19.79% | 16.97% | -23.29% | 0.510647 | 12.09% | 2.82% | 14.36% | 1.98% |
| full/core_existing/synthetic_50/main_cost | 19.40% | 16.91% | -23.14% | 0.504213 | 12.09% | 2.68% | 14.32% | 1.85% |
| full/core_existing/synthetic_70/main_cost | 19.38% | 16.63% | -22.67% | 0.509267 | 12.09% | 2.67% | 14.13% | 1.91% |

主基准是「同风险」（vol-matched BTC/cash）：把基准波动率压到与策略相同后再比收益，回答「这套复杂策略在承担相近风险时是否值得」。「不动起点」回答「主动决策相对『什么都不做』是否创造了价值」；100% BTC 只是机会成本参考，不是风险匹配基准——一个以降险为目标的策略可以合理地输给它而赢下同风险口径，只看 100% BTC 会把降险本身误读成失败。「年化超额」= 策略 CAGR − 基准 CAGR，为正才表示该口径下主动决策创造了价值；累计收益跨整个窗口，不能与年化数混用。

### 逐个基准明细

单向棘轮与从未交易的实验不进入基准比较表——它们没有测量策略行为；尾部休眠的实验保留在表中，但状态列会标出 `TRADING_STALLED`。

| 实验 | 基准 | 基准(零成本) | 基准(含成本) | 策略 | 累计超额 | 年化超额 | 基准MaxDD | 策略MaxDD | Sharpe差 | 基准波动率 |
|---|---|---|---|---|---|---|---|---|---|---|
| core/all_cash/strict/cost_0bps | vol_matched_btc_cash | 不可用 | 12.19% | 5.51% | -6.67% | -2.54% | -25.34% | -28.31% | -0.186847 | 13.13% |
| core/all_cash/strict/cost_0bps | vol_matched_btc_eth_70_30_cash | 不可用 | 12.05% | 5.51% | -6.54% | -2.49% | -23.74% | -28.31% | -0.183152 | 13.13% |
| core/all_cash/strict/cost_0bps | static_initial_weights | 不可用 | 0.00% | 5.51% | 5.51% | 2.17% | 0.00% | -28.31% | 不可用 | 0.00% |
| core/all_cash/strict/cost_0bps | exposure_matched_btc_cash | 不可用 | 11.05% | 5.51% | -5.54% | -2.11% | -22.91% | -28.31% | -0.186847 | 11.74% |
| core/all_cash/strict/cost_0bps | btc_eth_70_30 | 14.38% | 14.38% | 5.51% | -8.87% | -3.35% | -76.74% | -28.31% | -0.161738 | 59.91% |
| core/all_cash/strict/cost_0bps | btc_buy_and_hold | 20.25% | 20.25% | 5.51% | -14.73% | -5.48% | -76.63% | -28.31% | -0.186847 | 57.28% |
| core/all_cash/strict/cost_10bps | vol_matched_btc_cash | 不可用 | 11.78% | 5.47% | -6.31% | -2.40% | -25.45% | -28.30% | -0.177288 | 13.12% |
| core/all_cash/strict/cost_10bps | vol_matched_btc_eth_70_30_cash | 不可用 | 11.62% | 5.47% | -6.15% | -2.34% | -23.86% | -28.30% | -0.172896 | 13.12% |
| core/all_cash/strict/cost_10bps | static_initial_weights | 不可用 | 0.00% | 5.47% | 5.47% | 2.15% | 0.00% | -28.30% | 不可用 | 0.00% |
| core/all_cash/strict/cost_10bps | exposure_matched_btc_cash | 不可用 | 10.68% | 5.47% | -5.22% | -1.99% | -23.00% | -28.30% | -0.17697 | 11.73% |
| core/all_cash/strict/cost_10bps | btc_eth_70_30 | 14.38% | 14.28% | 5.47% | -8.82% | -3.33% | -76.74% | -28.30% | -0.162387 | 59.90% |
| core/all_cash/strict/cost_10bps | btc_buy_and_hold | 20.25% | 20.13% | 5.47% | -14.66% | -5.46% | -76.63% | -28.30% | -0.187413 | 57.28% |
| core/all_cash/strict/cost_25bps | vol_matched_btc_cash | 不可用 | 11.19% | 5.40% | -5.79% | -2.21% | -25.63% | -28.31% | -0.163073 | 13.12% |
| core/all_cash/strict/cost_25bps | vol_matched_btc_eth_70_30_cash | 不可用 | 10.99% | 5.40% | -5.59% | -2.13% | -24.05% | -28.31% | -0.157635 | 13.12% |
| core/all_cash/strict/cost_25bps | static_initial_weights | 不可用 | 0.00% | 5.40% | 5.40% | 2.12% | 0.00% | -28.31% | 不可用 | 0.00% |
| core/all_cash/strict/cost_25bps | exposure_matched_btc_cash | 不可用 | 10.14% | 5.40% | -4.74% | -1.82% | -23.17% | -28.31% | -0.162277 | 11.73% |
| core/all_cash/strict/cost_25bps | btc_eth_70_30 | 14.38% | 14.13% | 5.40% | -8.74% | -3.31% | -76.74% | -28.31% | -0.163487 | 59.90% |
| core/all_cash/strict/cost_25bps | btc_buy_and_hold | 20.25% | 19.95% | 5.40% | -14.55% | -5.42% | -76.63% | -28.31% | -0.188387 | 57.28% |
| core/all_cash/strict/main_cost | vol_matched_btc_cash | 不可用 | 11.58% | 5.44% | -6.14% | -2.34% | -25.51% | -28.31% | -0.172653 | 13.12% |
| core/all_cash/strict/main_cost | vol_matched_btc_eth_70_30_cash | 不可用 | 11.41% | 5.44% | -5.97% | -2.28% | -23.92% | -28.31% | -0.167915 | 13.12% |
| core/all_cash/strict/main_cost | static_initial_weights | 不可用 | 0.00% | 5.44% | 5.44% | 2.14% | 0.00% | -28.31% | 不可用 | 0.00% |
| core/all_cash/strict/main_cost | exposure_matched_btc_cash | 不可用 | 10.50% | 5.44% | -5.06% | -1.93% | -23.06% | -28.31% | -0.172176 | 11.73% |
| core/all_cash/strict/main_cost | btc_eth_70_30 | 14.38% | 14.23% | 5.44% | -8.79% | -3.32% | -76.74% | -28.31% | -0.162819 | 59.91% |
| core/all_cash/strict/main_cost | btc_buy_and_hold | 20.25% | 20.07% | 5.44% | -14.63% | -5.45% | -76.63% | -28.31% | -0.187828 | 57.28% |
| core/all_cash/strict/weekly_sensitivity | vol_matched_btc_cash | 不可用 | 7.30% | 7.57% | 0.27% | 0.10% | -15.94% | -15.33% | 0.0127649 | 7.86% |
| core/all_cash/strict/weekly_sensitivity | vol_matched_btc_eth_70_30_cash | 不可用 | 7.20% | 7.57% | 0.37% | 0.14% | -14.87% | -15.33% | 0.0173937 | 7.86% |
| core/all_cash/strict/weekly_sensitivity | static_initial_weights | 不可用 | 0.00% | 7.57% | 7.57% | 2.96% | 0.00% | -15.33% | 不可用 | 0.00% |
| core/all_cash/strict/weekly_sensitivity | exposure_matched_btc_cash | 不可用 | 7.41% | 7.57% | 0.16% | 0.06% | -16.19% | -15.33% | 0.0127196 | 7.99% |
| core/all_cash/strict/weekly_sensitivity | btc_eth_70_30 | 14.38% | 14.23% | 7.57% | -6.66% | -2.50% | -76.74% | -15.33% | 0.0207941 | 59.91% |
| core/all_cash/strict/weekly_sensitivity | btc_buy_and_hold | 20.25% | 20.07% | 7.57% | -12.50% | -4.63% | -76.63% | -15.33% | -0.00421408 | 57.28% |
| core/all_cash/synthetic_30/main_cost | vol_matched_btc_cash | 不可用 | 10.28% | 4.52% | -5.76% | -2.21% | -22.57% | -23.30% | -0.187342 | 11.45% |
| core/all_cash/synthetic_30/main_cost | vol_matched_btc_eth_70_30_cash | 不可用 | 10.14% | 4.52% | -5.61% | -2.15% | -21.13% | -23.30% | -0.182639 | 11.45% |
| core/all_cash/synthetic_30/main_cost | static_initial_weights | 不可用 | 0.00% | 4.52% | 4.52% | 1.79% | 0.00% | -23.30% | 不可用 | 0.00% |
| core/all_cash/synthetic_30/main_cost | exposure_matched_btc_cash | 不可用 | 8.18% | 4.52% | -3.65% | -1.41% | -17.88% | -23.30% | -0.186461 | 8.89% |
| core/all_cash/synthetic_30/main_cost | btc_eth_70_30 | 14.38% | 14.23% | 4.52% | -9.70% | -3.68% | -76.74% | -23.30% | -0.178079 | 59.91% |
| core/all_cash/synthetic_30/main_cost | btc_buy_and_hold | 20.25% | 20.07% | 4.52% | -15.54% | -5.80% | -76.63% | -23.30% | -0.203088 | 57.28% |
| core/all_cash/synthetic_50/main_cost | vol_matched_btc_cash | 不可用 | 10.07% | 4.24% | -5.83% | -2.24% | -22.09% | -23.33% | -0.194764 | 11.18% |
| core/all_cash/synthetic_50/main_cost | vol_matched_btc_eth_70_30_cash | 不可用 | 9.93% | 4.24% | -5.69% | -2.18% | -20.67% | -23.33% | -0.190067 | 11.18% |
| core/all_cash/synthetic_50/main_cost | static_initial_weights | 不可用 | 0.00% | 4.24% | 4.24% | 1.67% | 0.00% | -23.33% | 不可用 | 0.00% |
| core/all_cash/synthetic_50/main_cost | exposure_matched_btc_cash | 不可用 | 7.96% | 4.24% | -3.72% | -1.44% | -17.40% | -23.33% | -0.193887 | 8.63% |
| core/all_cash/synthetic_50/main_cost | btc_eth_70_30 | 14.38% | 14.23% | 4.24% | -9.99% | -3.79% | -76.74% | -23.33% | -0.185594 | 59.91% |
| core/all_cash/synthetic_50/main_cost | btc_buy_and_hold | 20.25% | 20.07% | 4.24% | -15.83% | -5.92% | -76.63% | -23.33% | -0.210602 | 57.28% |
| core/all_cash/synthetic_70/main_cost | vol_matched_btc_cash | 不可用 | 10.03% | 4.55% | -5.48% | -2.10% | -22.01% | -23.10% | -0.183487 | 11.14% |
| core/all_cash/synthetic_70/main_cost | vol_matched_btc_eth_70_30_cash | 不可用 | 9.89% | 4.55% | -5.34% | -2.05% | -20.60% | -23.10% | -0.178792 | 11.14% |
| core/all_cash/synthetic_70/main_cost | static_initial_weights | 不可用 | 0.00% | 4.55% | 4.55% | 1.80% | 0.00% | -23.10% | 不可用 | 0.00% |
| core/all_cash/synthetic_70/main_cost | exposure_matched_btc_cash | 不可用 | 7.94% | 4.55% | -3.39% | -1.31% | -17.36% | -23.10% | -0.18262 | 8.61% |
| core/all_cash/synthetic_70/main_cost | btc_eth_70_30 | 14.38% | 14.23% | 4.55% | -9.67% | -3.67% | -76.74% | -23.10% | -0.174333 | 59.91% |
| core/all_cash/synthetic_70/main_cost | btc_buy_and_hold | 20.25% | 20.07% | 4.55% | -15.51% | -5.79% | -76.63% | -23.10% | -0.199341 | 57.28% |
| core/core_existing/strict/cost_0bps | vol_matched_btc_cash | 不可用 | 15.59% | 20.00% | 4.41% | 1.60% | -32.94% | -26.89% | 0.0847446 | 17.69% |
| core/core_existing/strict/cost_0bps | vol_matched_btc_eth_70_30_cash | 不可用 | 15.40% | 20.00% | 4.60% | 1.67% | -31.00% | -26.89% | 0.0884393 | 17.69% |
| core/core_existing/strict/cost_0bps | static_initial_weights | 不可用 | 12.23% | 20.00% | 7.77% | 2.84% | -70.47% | -26.89% | 0.158009 | 49.81% |
| core/core_existing/strict/cost_0bps | exposure_matched_btc_cash | 不可用 | 12.13% | 20.00% | 7.87% | 2.88% | -25.22% | -26.89% | 0.0847446 | 13.06% |
| core/core_existing/strict/cost_0bps | btc_eth_70_30 | 14.38% | 14.38% | 20.00% | 5.62% | 2.04% | -76.74% | -26.89% | 0.109854 | 59.91% |
| core/core_existing/strict/cost_0bps | btc_buy_and_hold | 20.25% | 20.25% | 20.00% | -0.25% | -0.09% | -76.63% | -26.89% | 0.0847446 | 57.28% |
| core/core_existing/strict/cost_10bps | vol_matched_btc_cash | 不可用 | 15.09% | 19.90% | 4.80% | 1.74% | -33.07% | -26.91% | 0.0924697 | 17.70% |
| core/core_existing/strict/cost_10bps | vol_matched_btc_eth_70_30_cash | 不可用 | 14.86% | 19.90% | 5.03% | 1.83% | -31.15% | -26.91% | 0.0969313 | 17.70% |
| core/core_existing/strict/cost_10bps | static_initial_weights | 不可用 | 12.14% | 19.90% | 7.76% | 2.84% | -70.50% | -26.91% | 0.156417 | 49.86% |
| core/core_existing/strict/cost_10bps | exposure_matched_btc_cash | 不可用 | 11.74% | 19.90% | 8.16% | 2.99% | -25.35% | -26.91% | 0.0935291 | 13.06% |
| core/core_existing/strict/cost_10bps | btc_eth_70_30 | 14.38% | 14.28% | 19.90% | 5.61% | 2.04% | -76.74% | -26.91% | 0.108417 | 59.90% |
| core/core_existing/strict/cost_10bps | btc_buy_and_hold | 20.25% | 20.13% | 19.90% | -0.23% | -0.08% | -76.63% | -26.91% | 0.0833912 | 57.28% |
| core/core_existing/strict/cost_25bps | vol_matched_btc_cash | 不可用 | 14.35% | 19.74% | 5.38% | 1.96% | -33.28% | -26.94% | 0.104052 | 17.71% |
| core/core_existing/strict/cost_25bps | vol_matched_btc_eth_70_30_cash | 不可用 | 14.07% | 19.74% | 5.67% | 2.07% | -31.37% | -26.94% | 0.109664 | 17.71% |
| core/core_existing/strict/cost_25bps | static_initial_weights | 不可用 | 12.01% | 19.74% | 7.73% | 2.83% | -70.55% | -26.94% | 0.154024 | 49.93% |
| core/core_existing/strict/cost_25bps | exposure_matched_btc_cash | 不可用 | 11.15% | 19.74% | 8.59% | 3.15% | -25.53% | -26.94% | 0.106703 | 13.06% |
| core/core_existing/strict/cost_25bps | btc_eth_70_30 | 14.38% | 14.13% | 19.74% | 5.61% | 2.04% | -76.74% | -26.94% | 0.106258 | 59.90% |
| core/core_existing/strict/cost_25bps | btc_buy_and_hold | 20.25% | 19.95% | 19.74% | -0.21% | -0.07% | -76.63% | -26.94% | 0.0813585 | 57.28% |
| core/core_existing/strict/main_cost | vol_matched_btc_cash | 不可用 | 14.85% | 19.84% | 5.00% | 1.82% | -33.14% | -26.92% | 0.0963498 | 17.70% |
| core/core_existing/strict/main_cost | vol_matched_btc_eth_70_30_cash | 不可用 | 14.60% | 19.84% | 5.25% | 1.91% | -31.22% | -26.92% | 0.101192 | 17.70% |
| core/core_existing/strict/main_cost | static_initial_weights | 不可用 | 12.09% | 19.84% | 7.75% | 2.84% | -70.50% | -26.92% | 0.155782 | 49.86% |
| core/core_existing/strict/main_cost | exposure_matched_btc_cash | 不可用 | 11.54% | 19.84% | 8.30% | 3.04% | -25.41% | -26.92% | 0.0979394 | 13.06% |
| core/core_existing/strict/main_cost | btc_eth_70_30 | 14.38% | 14.23% | 19.84% | 5.62% | 2.05% | -76.74% | -26.92% | 0.107754 | 59.91% |
| core/core_existing/strict/main_cost | btc_buy_and_hold | 20.25% | 20.07% | 19.84% | -0.22% | -0.08% | -76.63% | -26.92% | 0.0827457 | 57.28% |
| core/core_existing/strict/weekly_sensitivity | vol_matched_btc_cash | 不可用 | 14.15% | 21.41% | 7.26% | 2.63% | -31.47% | -23.39% | 0.148029 | 16.66% |
| core/core_existing/strict/weekly_sensitivity | vol_matched_btc_eth_70_30_cash | 不可用 | 13.92% | 21.41% | 7.49% | 2.72% | -29.62% | -23.39% | 0.152847 | 16.66% |
| core/core_existing/strict/weekly_sensitivity | static_initial_weights | 不可用 | 12.09% | 21.41% | 9.31% | 3.40% | -70.50% | -23.39% | 0.207107 | 49.86% |
| core/core_existing/strict/weekly_sensitivity | exposure_matched_btc_cash | 不可用 | 9.78% | 21.41% | 11.63% | 4.27% | -21.44% | -23.39% | 0.150032 | 10.82% |
| core/core_existing/strict/weekly_sensitivity | btc_eth_70_30 | 14.38% | 14.23% | 21.41% | 7.18% | 2.60% | -76.74% | -23.39% | 0.159079 | 59.91% |
| core/core_existing/strict/weekly_sensitivity | btc_buy_and_hold | 20.25% | 20.07% | 21.41% | 1.34% | 0.48% | -76.63% | -23.39% | 0.13407 | 57.28% |
| core/core_existing/synthetic_30/main_cost | vol_matched_btc_cash | 不可用 | 14.76% | 20.04% | 5.28% | 1.92% | -32.93% | -26.37% | 0.102401 | 17.57% |
| core/core_existing/synthetic_30/main_cost | vol_matched_btc_eth_70_30_cash | 不可用 | 14.52% | 20.04% | 5.52% | 2.01% | -31.02% | -26.37% | 0.107241 | 17.57% |
| core/core_existing/synthetic_30/main_cost | static_initial_weights | 不可用 | 12.09% | 20.04% | 7.94% | 2.91% | -70.50% | -26.37% | 0.16179 | 49.86% |
| core/core_existing/synthetic_30/main_cost | exposure_matched_btc_cash | 不可用 | 11.18% | 20.04% | 8.85% | 3.25% | -24.60% | -26.37% | 0.104106 | 12.60% |
| core/core_existing/synthetic_30/main_cost | btc_eth_70_30 | 14.38% | 14.23% | 20.04% | 5.81% | 2.11% | -76.74% | -26.37% | 0.113762 | 59.91% |
| core/core_existing/synthetic_30/main_cost | btc_buy_and_hold | 20.25% | 20.07% | 20.04% | -0.03% | -0.01% | -76.63% | -26.37% | 0.0887535 | 57.28% |
| core/core_existing/synthetic_50/main_cost | vol_matched_btc_cash | 不可用 | 14.36% | 19.58% | 5.21% | 1.90% | -31.98% | -23.27% | 0.105139 | 16.98% |
| core/core_existing/synthetic_50/main_cost | vol_matched_btc_eth_70_30_cash | 不可用 | 14.13% | 19.58% | 5.45% | 1.99% | -30.10% | -23.27% | 0.109964 | 16.98% |
| core/core_existing/synthetic_50/main_cost | static_initial_weights | 不可用 | 12.09% | 19.58% | 7.48% | 2.74% | -70.50% | -23.27% | 0.164324 | 49.86% |
| core/core_existing/synthetic_50/main_cost | exposure_matched_btc_cash | 不可用 | 9.76% | 19.58% | 9.81% | 3.62% | -21.40% | -23.27% | 0.107255 | 10.80% |
| core/core_existing/synthetic_50/main_cost | btc_eth_70_30 | 14.38% | 14.23% | 19.58% | 5.35% | 1.95% | -76.74% | -23.27% | 0.116295 | 59.91% |
| core/core_existing/synthetic_50/main_cost | btc_buy_and_hold | 20.25% | 20.07% | 19.58% | -0.49% | -0.18% | -76.63% | -23.27% | 0.0912871 | 57.28% |
| core/core_existing/synthetic_70/main_cost | vol_matched_btc_cash | 不可用 | 14.20% | 18.40% | 4.20% | 1.53% | -31.58% | -23.44% | 0.0864333 | 16.73% |
| core/core_existing/synthetic_70/main_cost | vol_matched_btc_eth_70_30_cash | 不可用 | 13.97% | 18.40% | 4.43% | 1.62% | -29.73% | -23.44% | 0.0912532 | 16.73% |
| core/core_existing/synthetic_70/main_cost | static_initial_weights | 不可用 | 12.09% | 18.40% | 6.31% | 2.32% | -70.50% | -23.44% | 0.145535 | 49.86% |
| core/core_existing/synthetic_70/main_cost | exposure_matched_btc_cash | 不可用 | 9.32% | 18.40% | 9.08% | 3.36% | -20.40% | -23.44% | 0.0886556 | 10.25% |
| core/core_existing/synthetic_70/main_cost | btc_eth_70_30 | 14.38% | 14.23% | 18.40% | 4.17% | 1.52% | -76.74% | -23.44% | 0.0975067 | 59.91% |
| core/core_existing/synthetic_70/main_cost | btc_buy_and_hold | 20.25% | 20.07% | 18.40% | -1.67% | -0.60% | -76.63% | -23.44% | 0.0724985 | 57.28% |
| full/all_cash/strict/cost_0bps | vol_matched_btc_cash | 不可用 | 12.19% | 5.51% | -6.67% | -2.54% | -25.34% | -28.31% | -0.186847 | 13.13% |
| full/all_cash/strict/cost_0bps | vol_matched_btc_eth_70_30_cash | 不可用 | 12.05% | 5.51% | -6.54% | -2.49% | -23.74% | -28.31% | -0.183152 | 13.13% |
| full/all_cash/strict/cost_0bps | static_initial_weights | 不可用 | 0.00% | 5.51% | 5.51% | 2.17% | 0.00% | -28.31% | 不可用 | 0.00% |
| full/all_cash/strict/cost_0bps | exposure_matched_btc_cash | 不可用 | 11.05% | 5.51% | -5.54% | -2.11% | -22.91% | -28.31% | -0.186847 | 11.74% |
| full/all_cash/strict/cost_0bps | btc_eth_70_30 | 14.38% | 14.38% | 5.51% | -8.87% | -3.35% | -76.74% | -28.31% | -0.161738 | 59.91% |
| full/all_cash/strict/cost_0bps | btc_buy_and_hold | 20.25% | 20.25% | 5.51% | -14.73% | -5.48% | -76.63% | -28.31% | -0.186847 | 57.28% |
| full/all_cash/strict/cost_10bps | vol_matched_btc_cash | 不可用 | 11.78% | 5.47% | -6.31% | -2.40% | -25.45% | -28.30% | -0.177288 | 13.12% |
| full/all_cash/strict/cost_10bps | vol_matched_btc_eth_70_30_cash | 不可用 | 11.62% | 5.47% | -6.15% | -2.34% | -23.86% | -28.30% | -0.172896 | 13.12% |
| full/all_cash/strict/cost_10bps | static_initial_weights | 不可用 | 0.00% | 5.47% | 5.47% | 2.15% | 0.00% | -28.30% | 不可用 | 0.00% |
| full/all_cash/strict/cost_10bps | exposure_matched_btc_cash | 不可用 | 10.68% | 5.47% | -5.22% | -1.99% | -23.00% | -28.30% | -0.17697 | 11.73% |
| full/all_cash/strict/cost_10bps | btc_eth_70_30 | 14.38% | 14.28% | 5.47% | -8.82% | -3.33% | -76.74% | -28.30% | -0.162387 | 59.90% |
| full/all_cash/strict/cost_10bps | btc_buy_and_hold | 20.25% | 20.13% | 5.47% | -14.66% | -5.46% | -76.63% | -28.30% | -0.187413 | 57.28% |
| full/all_cash/strict/cost_25bps | vol_matched_btc_cash | 不可用 | 11.19% | 5.40% | -5.79% | -2.21% | -25.63% | -28.31% | -0.163073 | 13.12% |
| full/all_cash/strict/cost_25bps | vol_matched_btc_eth_70_30_cash | 不可用 | 10.99% | 5.40% | -5.59% | -2.13% | -24.05% | -28.31% | -0.157635 | 13.12% |
| full/all_cash/strict/cost_25bps | static_initial_weights | 不可用 | 0.00% | 5.40% | 5.40% | 2.12% | 0.00% | -28.31% | 不可用 | 0.00% |
| full/all_cash/strict/cost_25bps | exposure_matched_btc_cash | 不可用 | 10.14% | 5.40% | -4.74% | -1.82% | -23.17% | -28.31% | -0.162277 | 11.73% |
| full/all_cash/strict/cost_25bps | btc_eth_70_30 | 14.38% | 14.13% | 5.40% | -8.74% | -3.31% | -76.74% | -28.31% | -0.163487 | 59.90% |
| full/all_cash/strict/cost_25bps | btc_buy_and_hold | 20.25% | 19.95% | 5.40% | -14.55% | -5.42% | -76.63% | -28.31% | -0.188387 | 57.28% |
| full/all_cash/strict/main_cost | vol_matched_btc_cash | 不可用 | 11.58% | 5.44% | -6.14% | -2.34% | -25.51% | -28.31% | -0.172653 | 13.12% |
| full/all_cash/strict/main_cost | vol_matched_btc_eth_70_30_cash | 不可用 | 11.41% | 5.44% | -5.97% | -2.28% | -23.92% | -28.31% | -0.167915 | 13.12% |
| full/all_cash/strict/main_cost | static_initial_weights | 不可用 | 0.00% | 5.44% | 5.44% | 2.14% | 0.00% | -28.31% | 不可用 | 0.00% |
| full/all_cash/strict/main_cost | exposure_matched_btc_cash | 不可用 | 10.50% | 5.44% | -5.06% | -1.93% | -23.06% | -28.31% | -0.172176 | 11.73% |
| full/all_cash/strict/main_cost | btc_eth_70_30 | 14.38% | 14.23% | 5.44% | -8.79% | -3.32% | -76.74% | -28.31% | -0.162819 | 59.91% |
| full/all_cash/strict/main_cost | btc_buy_and_hold | 20.25% | 20.07% | 5.44% | -14.63% | -5.45% | -76.63% | -28.31% | -0.187828 | 57.28% |
| full/all_cash/strict/weekly_sensitivity | vol_matched_btc_cash | 不可用 | 7.13% | 6.68% | -0.45% | -0.17% | -15.56% | -15.33% | -0.0219365 | 7.66% |
| full/all_cash/strict/weekly_sensitivity | vol_matched_btc_eth_70_30_cash | 不可用 | 7.03% | 6.68% | -0.35% | -0.14% | -14.51% | -15.33% | -0.0173117 | 7.66% |
| full/all_cash/strict/weekly_sensitivity | static_initial_weights | 不可用 | 0.00% | 6.68% | 6.68% | 2.62% | 0.00% | -15.33% | 不可用 | 0.00% |
| full/all_cash/strict/weekly_sensitivity | exposure_matched_btc_cash | 不可用 | 7.24% | 6.68% | -0.56% | -0.21% | -15.80% | -15.33% | -0.0219803 | 7.78% |
| full/all_cash/strict/weekly_sensitivity | btc_eth_70_30 | 14.38% | 14.23% | 6.68% | -7.55% | -2.85% | -76.74% | -15.33% | -0.0139757 | 59.91% |
| full/all_cash/strict/weekly_sensitivity | btc_buy_and_hold | 20.25% | 20.07% | 6.68% | -13.39% | -4.97% | -76.63% | -15.33% | -0.0389839 | 57.28% |
| full/all_cash/synthetic_30/main_cost | vol_matched_btc_cash | 不可用 | 11.23% | 3.45% | -7.77% | -2.98% | -24.69% | -27.98% | -0.22907 | 12.65% |
| full/all_cash/synthetic_30/main_cost | vol_matched_btc_eth_70_30_cash | 不可用 | 11.06% | 3.45% | -7.61% | -2.92% | -23.15% | -27.98% | -0.224342 | 12.65% |
| full/all_cash/synthetic_30/main_cost | static_initial_weights | 不可用 | 0.00% | 3.45% | 3.45% | 1.37% | 0.00% | -27.98% | 不可用 | 0.00% |
| full/all_cash/synthetic_30/main_cost | exposure_matched_btc_cash | 不可用 | 9.89% | 3.45% | -6.44% | -2.48% | -21.68% | -27.98% | -0.22849 | 10.96% |
| full/all_cash/synthetic_30/main_cost | btc_eth_70_30 | 14.38% | 14.23% | 3.45% | -10.77% | -4.10% | -76.74% | -27.98% | -0.219397 | 59.91% |
| full/all_cash/synthetic_30/main_cost | btc_buy_and_hold | 20.25% | 20.07% | 3.45% | -16.62% | -6.22% | -76.63% | -27.98% | -0.244405 | 57.28% |
| full/all_cash/synthetic_50/main_cost | vol_matched_btc_cash | 不可用 | 10.02% | 5.11% | -4.91% | -1.88% | -21.98% | -22.85% | -0.164263 | 11.12% |
| full/all_cash/synthetic_50/main_cost | vol_matched_btc_eth_70_30_cash | 不可用 | 9.88% | 5.11% | -4.77% | -1.83% | -20.57% | -22.85% | -0.159568 | 11.12% |
| full/all_cash/synthetic_50/main_cost | static_initial_weights | 不可用 | 0.00% | 5.11% | 5.11% | 2.01% | 0.00% | -22.85% | 不可用 | 0.00% |
| full/all_cash/synthetic_50/main_cost | exposure_matched_btc_cash | 不可用 | 7.96% | 5.11% | -2.85% | -1.10% | -17.40% | -22.85% | -0.163408 | 8.63% |
| full/all_cash/synthetic_50/main_cost | btc_eth_70_30 | 14.38% | 14.23% | 5.11% | -9.12% | -3.45% | -76.74% | -22.85% | -0.155115 | 59.91% |
| full/all_cash/synthetic_50/main_cost | btc_buy_and_hold | 20.25% | 20.07% | 5.11% | -14.96% | -5.58% | -76.63% | -22.85% | -0.180123 | 57.28% |
| full/all_cash/synthetic_70/main_cost | vol_matched_btc_cash | 不可用 | 9.93% | 5.56% | -4.37% | -1.67% | -21.77% | -22.75% | -0.147264 | 11.01% |
| full/all_cash/synthetic_70/main_cost | vol_matched_btc_eth_70_30_cash | 不可用 | 9.79% | 5.56% | -4.22% | -1.62% | -20.37% | -22.75% | -0.142571 | 11.01% |
| full/all_cash/synthetic_70/main_cost | static_initial_weights | 不可用 | 0.00% | 5.56% | 5.56% | 2.19% | 0.00% | -22.75% | 不可用 | 0.00% |
| full/all_cash/synthetic_70/main_cost | exposure_matched_btc_cash | 不可用 | 8.01% | 5.56% | -2.44% | -0.94% | -17.50% | -22.75% | -0.146468 | 8.68% |
| full/all_cash/synthetic_70/main_cost | btc_eth_70_30 | 14.38% | 14.23% | 5.56% | -8.66% | -3.28% | -76.74% | -22.75% | -0.138155 | 59.91% |
| full/all_cash/synthetic_70/main_cost | btc_buy_and_hold | 20.25% | 20.07% | 5.56% | -14.50% | -5.40% | -76.63% | -22.75% | -0.163163 | 57.28% |
| full/core_existing/strict/cost_0bps | vol_matched_btc_cash | 不可用 | 15.59% | 20.00% | 4.41% | 1.60% | -32.94% | -26.89% | 0.0847446 | 17.69% |
| full/core_existing/strict/cost_0bps | vol_matched_btc_eth_70_30_cash | 不可用 | 15.40% | 20.00% | 4.60% | 1.67% | -31.00% | -26.89% | 0.0884393 | 17.69% |
| full/core_existing/strict/cost_0bps | static_initial_weights | 不可用 | 12.23% | 20.00% | 7.77% | 2.84% | -70.47% | -26.89% | 0.158009 | 49.81% |
| full/core_existing/strict/cost_0bps | exposure_matched_btc_cash | 不可用 | 12.13% | 20.00% | 7.87% | 2.88% | -25.22% | -26.89% | 0.0847446 | 13.06% |
| full/core_existing/strict/cost_0bps | btc_eth_70_30 | 14.38% | 14.38% | 20.00% | 5.62% | 2.04% | -76.74% | -26.89% | 0.109854 | 59.91% |
| full/core_existing/strict/cost_0bps | btc_buy_and_hold | 20.25% | 20.25% | 20.00% | -0.25% | -0.09% | -76.63% | -26.89% | 0.0847446 | 57.28% |
| full/core_existing/strict/cost_10bps | vol_matched_btc_cash | 不可用 | 15.09% | 19.90% | 4.80% | 1.74% | -33.07% | -26.91% | 0.0924697 | 17.70% |
| full/core_existing/strict/cost_10bps | vol_matched_btc_eth_70_30_cash | 不可用 | 14.86% | 19.90% | 5.03% | 1.83% | -31.15% | -26.91% | 0.0969313 | 17.70% |
| full/core_existing/strict/cost_10bps | static_initial_weights | 不可用 | 12.14% | 19.90% | 7.76% | 2.84% | -70.50% | -26.91% | 0.156417 | 49.86% |
| full/core_existing/strict/cost_10bps | exposure_matched_btc_cash | 不可用 | 11.74% | 19.90% | 8.16% | 2.99% | -25.35% | -26.91% | 0.0935291 | 13.06% |
| full/core_existing/strict/cost_10bps | btc_eth_70_30 | 14.38% | 14.28% | 19.90% | 5.61% | 2.04% | -76.74% | -26.91% | 0.108417 | 59.90% |
| full/core_existing/strict/cost_10bps | btc_buy_and_hold | 20.25% | 20.13% | 19.90% | -0.23% | -0.08% | -76.63% | -26.91% | 0.0833912 | 57.28% |
| full/core_existing/strict/cost_25bps | vol_matched_btc_cash | 不可用 | 14.35% | 19.74% | 5.38% | 1.96% | -33.28% | -26.94% | 0.104052 | 17.71% |
| full/core_existing/strict/cost_25bps | vol_matched_btc_eth_70_30_cash | 不可用 | 14.07% | 19.74% | 5.67% | 2.07% | -31.37% | -26.94% | 0.109664 | 17.71% |
| full/core_existing/strict/cost_25bps | static_initial_weights | 不可用 | 12.01% | 19.74% | 7.73% | 2.83% | -70.55% | -26.94% | 0.154024 | 49.93% |
| full/core_existing/strict/cost_25bps | exposure_matched_btc_cash | 不可用 | 11.15% | 19.74% | 8.59% | 3.15% | -25.53% | -26.94% | 0.106703 | 13.06% |
| full/core_existing/strict/cost_25bps | btc_eth_70_30 | 14.38% | 14.13% | 19.74% | 5.61% | 2.04% | -76.74% | -26.94% | 0.106258 | 59.90% |
| full/core_existing/strict/cost_25bps | btc_buy_and_hold | 20.25% | 19.95% | 19.74% | -0.21% | -0.07% | -76.63% | -26.94% | 0.0813585 | 57.28% |
| full/core_existing/strict/main_cost | vol_matched_btc_cash | 不可用 | 14.85% | 19.84% | 5.00% | 1.82% | -33.14% | -26.92% | 0.0963498 | 17.70% |
| full/core_existing/strict/main_cost | vol_matched_btc_eth_70_30_cash | 不可用 | 14.60% | 19.84% | 5.25% | 1.91% | -31.22% | -26.92% | 0.101192 | 17.70% |
| full/core_existing/strict/main_cost | static_initial_weights | 不可用 | 12.09% | 19.84% | 7.75% | 2.84% | -70.50% | -26.92% | 0.155782 | 49.86% |
| full/core_existing/strict/main_cost | exposure_matched_btc_cash | 不可用 | 11.54% | 19.84% | 8.30% | 3.04% | -25.41% | -26.92% | 0.0979394 | 13.06% |
| full/core_existing/strict/main_cost | btc_eth_70_30 | 14.38% | 14.23% | 19.84% | 5.62% | 2.05% | -76.74% | -26.92% | 0.107754 | 59.91% |
| full/core_existing/strict/main_cost | btc_buy_and_hold | 20.25% | 20.07% | 19.84% | -0.22% | -0.08% | -76.63% | -26.92% | 0.0827457 | 57.28% |
| full/core_existing/strict/weekly_sensitivity | vol_matched_btc_cash | 不可用 | 14.15% | 21.41% | 7.26% | 2.63% | -31.47% | -23.39% | 0.148029 | 16.66% |
| full/core_existing/strict/weekly_sensitivity | vol_matched_btc_eth_70_30_cash | 不可用 | 13.92% | 21.41% | 7.49% | 2.72% | -29.62% | -23.39% | 0.152847 | 16.66% |
| full/core_existing/strict/weekly_sensitivity | static_initial_weights | 不可用 | 12.09% | 21.41% | 9.31% | 3.40% | -70.50% | -23.39% | 0.207107 | 49.86% |
| full/core_existing/strict/weekly_sensitivity | exposure_matched_btc_cash | 不可用 | 9.78% | 21.41% | 11.63% | 4.27% | -21.44% | -23.39% | 0.150032 | 10.82% |
| full/core_existing/strict/weekly_sensitivity | btc_eth_70_30 | 14.38% | 14.23% | 21.41% | 7.18% | 2.60% | -76.74% | -23.39% | 0.159079 | 59.91% |
| full/core_existing/strict/weekly_sensitivity | btc_buy_and_hold | 20.25% | 20.07% | 21.41% | 1.34% | 0.48% | -76.63% | -23.39% | 0.13407 | 57.28% |
| full/core_existing/synthetic_30/main_cost | vol_matched_btc_cash | 不可用 | 14.36% | 19.79% | 5.44% | 1.98% | -31.97% | -23.29% | 0.109524 | 16.97% |
| full/core_existing/synthetic_30/main_cost | vol_matched_btc_eth_70_30_cash | 不可用 | 14.12% | 19.79% | 5.67% | 2.07% | -30.10% | -23.29% | 0.114349 | 16.97% |
| full/core_existing/synthetic_30/main_cost | static_initial_weights | 不可用 | 12.09% | 19.79% | 7.70% | 2.82% | -70.50% | -23.29% | 0.168707 | 49.86% |
| full/core_existing/synthetic_30/main_cost | exposure_matched_btc_cash | 不可用 | 9.96% | 19.79% | 9.83% | 3.62% | -21.84% | -23.29% | 0.111555 | 11.05% |
| full/core_existing/synthetic_30/main_cost | btc_eth_70_30 | 14.38% | 14.23% | 19.79% | 5.57% | 2.03% | -76.74% | -23.29% | 0.120679 | 59.91% |
| full/core_existing/synthetic_30/main_cost | btc_buy_and_hold | 20.25% | 20.07% | 19.79% | -0.27% | -0.10% | -76.63% | -23.29% | 0.0956706 | 57.28% |
| full/core_existing/synthetic_50/main_cost | vol_matched_btc_cash | 不可用 | 14.32% | 19.40% | 5.09% | 1.85% | -31.87% | -23.14% | 0.103112 | 16.91% |
| full/core_existing/synthetic_50/main_cost | vol_matched_btc_eth_70_30_cash | 不可用 | 14.08% | 19.40% | 5.32% | 1.94% | -30.00% | -23.14% | 0.107936 | 16.91% |
| full/core_existing/synthetic_50/main_cost | static_initial_weights | 不可用 | 12.09% | 19.40% | 7.31% | 2.68% | -70.50% | -23.14% | 0.162273 | 49.86% |
| full/core_existing/synthetic_50/main_cost | exposure_matched_btc_cash | 不可用 | 9.82% | 19.40% | 9.58% | 3.53% | -21.53% | -23.14% | 0.105181 | 10.87% |
| full/core_existing/synthetic_50/main_cost | btc_eth_70_30 | 14.38% | 14.23% | 19.40% | 5.18% | 1.89% | -76.74% | -23.14% | 0.114245 | 59.91% |
| full/core_existing/synthetic_50/main_cost | btc_buy_and_hold | 20.25% | 20.07% | 19.40% | -0.66% | -0.24% | -76.63% | -23.14% | 0.0892367 | 57.28% |
| full/core_existing/synthetic_70/main_cost | vol_matched_btc_cash | 不可用 | 14.13% | 19.38% | 5.25% | 1.91% | -31.42% | -22.67% | 0.10826 | 16.63% |
| full/core_existing/synthetic_70/main_cost | vol_matched_btc_eth_70_30_cash | 不可用 | 13.90% | 19.38% | 5.48% | 2.00% | -29.57% | -22.67% | 0.113078 | 16.63% |
| full/core_existing/synthetic_70/main_cost | static_initial_weights | 不可用 | 12.09% | 19.38% | 7.29% | 2.67% | -70.50% | -22.67% | 0.167327 | 49.86% |
| full/core_existing/synthetic_70/main_cost | exposure_matched_btc_cash | 不可用 | 9.44% | 19.38% | 9.93% | 3.67% | -20.69% | -22.67% | 0.110394 | 10.41% |
| full/core_existing/synthetic_70/main_cost | btc_eth_70_30 | 14.38% | 14.23% | 19.38% | 5.15% | 1.88% | -76.74% | -22.67% | 0.119299 | 59.91% |
| full/core_existing/synthetic_70/main_cost | btc_buy_and_hold | 20.25% | 20.07% | 19.38% | -0.69% | -0.25% | -76.63% | -22.67% | 0.0942904 | 57.28% |

`btc_buy_and_hold` = 100% BTC 买入持有（策略主基准）；`btc_eth_70_30` = 70/30 BTC/ETH 买入持有（次基准）；`static_initial_weights` = 用该实验自己的起始权重买入后不再调整；`vol_matched_btc_cash` = 按策略自身波动率解出的 BTC/现金固定比例，每日再平衡；`exposure_matched_btc_cash` = 按策略实现的平均风险仓持有的 BTC/现金固定比例，每日再平衡。

超额 = 策略累计收益 − 基准累计收益，单位为百分点；同一行的 MaxDD 分成基准与策略两列，避免把基准回撤误读为策略回撤。风险不对等的基准（如 100% BTC）只能说明仓位与风险差异，不能单独证明策略优劣。「基准波动率」让「同风险」一行可自证：它应当等于同行的策略波动率。

### 分年度收益（策略与基准）

| 来源 | 2021 | 2022 | 2023 |
|---|---|---|---|
| 策略（可比实验并集） | 9.47% | -17.68% | 17.90% |
| vol_matched_btc_cash | 9.08% | -17.96% | 26.03% |
| vol_matched_btc_eth_70_30_cash | 10.50% | -16.95% | 22.64% |
| static_initial_weights | 0.00% | 0.00% | 0.00% |
| exposure_matched_btc_cash | 8.14% | -16.14% | 23.03% |
| btc_eth_70_30 | 43.02% | -65.20% | 134.34% |
| btc_buy_and_hold | 34.46% | -64.07% | 154.74% |

分年度数据来自引擎已产出的 `metrics.calendar_returns.yearly`，未在此重算。单一区间的累计差额可能完全由其中某一年造成，因此累计数必须与分年度数一起看。

## 风险与择时诊断

### 择时贡献与地板钉住

| 实验 | 平均风险仓 | 路径累计 | 同均仓累计 | 择时贡献 | overlay 生效占比 | 标签≥防御占比 | 回撤≤地板占比 | 现金4%时CAGR | 现金5%时CAGR |
|---|---|---|---|---|---|---|---|---|---|
| core/all_cash/strict/cost_0bps | 20.49% | 4.29% | 11.05% | -6.76% | 72.29% | 85.76% | 82.80% | 5.41% | 6.21% |
| core/all_cash/strict/cost_10bps | 20.47% | 4.30% | 11.04% | -6.75% | 72.51% | 85.76% | 82.80% | 5.39% | 6.19% |
| core/all_cash/strict/cost_25bps | 20.47% | 4.30% | 11.04% | -6.74% | 72.62% | 85.76% | 82.80% | 5.36% | 6.17% |
| core/all_cash/strict/main_cost | 20.47% | 4.30% | 11.04% | -6.75% | 72.51% | 85.76% | 82.80% | 5.38% | 6.18% |
| core/all_cash/strict/weekly_sensitivity | 13.95% | 7.41% | 7.80% | -0.39% | 1.20% | 67.25% | 51.59% | 6.50% | 7.38% |
| core/all_cash/synthetic_30/main_cost | 15.51% | 3.58% | 8.60% | -5.02% | 71.74% | 84.34% | 80.07% | 5.22% | 6.07% |
| core/all_cash/synthetic_50/main_cost | 15.06% | 4.39% | 8.37% | -3.98% | 72.18% | 84.12% | 79.63% | 5.12% | 5.98% |
| core/all_cash/synthetic_70/main_cost | 15.03% | 4.83% | 8.36% | -3.53% | 70.97% | 84.12% | 79.63% | 5.25% | 6.11% |
| core/core_existing/strict/cost_0bps | 22.80% | 14.22% | 12.13% | 2.09% | 71.30% | 85.98% | 83.24% | 10.87% | 11.70% |
| core/core_existing/strict/cost_10bps | 22.80% | 14.23% | 12.13% | 2.10% | 71.30% | 85.98% | 83.24% | 10.83% | 11.66% |
| core/core_existing/strict/cost_25bps | 22.81% | 14.24% | 12.13% | 2.10% | 71.30% | 85.98% | 83.24% | 10.78% | 11.60% |
| core/core_existing/strict/main_cost | 22.80% | 14.23% | 12.13% | 2.10% | 71.30% | 85.98% | 83.24% | 10.82% | 11.64% |
| core/core_existing/strict/weekly_sensitivity | 18.89% | 17.14% | 10.29% | 6.85% | 69.99% | 86.53% | 84.34% | 11.56% | 12.43% |
| core/core_existing/synthetic_30/main_cost | 22.00% | 14.89% | 11.76% | 3.13% | 72.40% | 85.98% | 83.46% | 10.92% | 11.75% |
| core/core_existing/synthetic_50/main_cost | 18.86% | 14.02% | 10.27% | 3.75% | 74.15% | 85.98% | 83.24% | 10.89% | 11.75% |
| core/core_existing/synthetic_70/main_cost | 17.90% | 13.90% | 9.80% | 4.10% | 77.66% | 85.98% | 83.24% | 10.49% | 11.36% |
| full/all_cash/strict/cost_0bps | 20.49% | 4.29% | 11.05% | -6.76% | 72.29% | 86.42% | 82.80% | 5.41% | 6.21% |
| full/all_cash/strict/cost_10bps | 20.47% | 4.30% | 11.04% | -6.75% | 72.51% | 86.42% | 82.80% | 5.39% | 6.19% |
| full/all_cash/strict/cost_25bps | 20.47% | 4.30% | 11.04% | -6.74% | 72.62% | 86.42% | 82.80% | 5.36% | 6.17% |
| full/all_cash/strict/main_cost | 20.47% | 4.30% | 11.04% | -6.75% | 72.51% | 86.42% | 82.80% | 5.38% | 6.18% |
| full/all_cash/strict/weekly_sensitivity | 13.59% | 6.54% | 7.61% | -1.07% | 1.20% | 68.46% | 52.25% | 6.16% | 7.04% |
| full/all_cash/synthetic_30/main_cost | 19.13% | 3.14% | 10.40% | -7.26% | 73.71% | 85.10% | 80.18% | 4.63% | 5.45% |
| full/all_cash/synthetic_50/main_cost | 15.06% | 4.54% | 8.37% | -3.84% | 69.33% | 84.45% | 79.30% | 5.47% | 6.33% |
| full/all_cash/synthetic_70/main_cost | 15.16% | 5.44% | 8.42% | -2.99% | 66.81% | 84.45% | 79.30% | 5.65% | 6.51% |
| full/core_existing/strict/cost_0bps | 22.80% | 14.22% | 12.13% | 2.09% | 71.30% | 86.20% | 83.24% | 10.87% | 11.70% |
| full/core_existing/strict/cost_10bps | 22.80% | 14.23% | 12.13% | 2.10% | 71.30% | 86.20% | 83.24% | 10.83% | 11.66% |
| full/core_existing/strict/cost_25bps | 22.81% | 14.24% | 12.13% | 2.10% | 71.30% | 86.20% | 83.24% | 10.78% | 11.60% |
| full/core_existing/strict/main_cost | 22.80% | 14.23% | 12.13% | 2.10% | 71.30% | 86.20% | 83.24% | 10.82% | 11.64% |
| full/core_existing/strict/weekly_sensitivity | 18.89% | 17.14% | 10.29% | 6.85% | 69.99% | 86.75% | 84.34% | 11.56% | 12.43% |
| full/core_existing/synthetic_30/main_cost | 19.29% | 13.68% | 10.48% | 3.20% | 71.96% | 85.98% | 82.91% | 10.95% | 11.81% |
| full/core_existing/synthetic_50/main_cost | 18.98% | 15.18% | 10.33% | 4.85% | 71.52% | 85.32% | 81.82% | 10.82% | 11.68% |
| full/core_existing/synthetic_70/main_cost | 18.17% | 15.60% | 9.93% | 5.67% | 72.40% | 85.65% | 82.15% | 10.84% | 11.71% |

「择时贡献」= 按策略实际风险仓路径持有 BTC 腿的累计收益 − 按其平均风险仓常数持有的累计收益，带符号：正数说明持仓时机在均值之上，负数说明把敞口加在了腿下跌的时段。「标签≥防御占比」与「回撤≤地板占比」接近相等即 `REGIME_PINNED_BY_OWN_DRAWDOWN`：regime 标签跟随账面回撤而非市场。现金收益敏感性是诊断口径：回放把稳定腿记为零收益，这里按假设年化收益重记策略自身路径，不改变任何引擎记账，也不与零收益基准直接比较。

### 风险引擎诊断

| 实验 | 模式 | 平均估计组合波动率 | 最大估计组合波动率 | 约束生效分布 | 应急状态分布 |
|---|---|---|---|---|---|
| core/all_cash/strict/cost_0bps | volatility_budget | 24.46% | 48.59% | emergency_overlay:660 / volatility_budget:216 / strategic_target:37 | BREACH:660 / CAUTION:24 / EMERGENCY:72 / NORMAL:157 |
| core/all_cash/strict/cost_10bps | volatility_budget | 24.46% | 48.59% | emergency_overlay:662 / volatility_budget:215 / strategic_target:36 | BREACH:662 / CAUTION:24 / EMERGENCY:70 / NORMAL:157 |
| core/all_cash/strict/cost_25bps | volatility_budget | 24.46% | 48.59% | emergency_overlay:663 / volatility_budget:214 / strategic_target:36 | BREACH:663 / CAUTION:24 / EMERGENCY:69 / NORMAL:157 |
| core/all_cash/strict/main_cost | volatility_budget | 24.46% | 48.59% | emergency_overlay:662 / volatility_budget:215 / strategic_target:36 | BREACH:662 / CAUTION:24 / EMERGENCY:70 / NORMAL:157 |
| core/all_cash/strict/weekly_sensitivity | volatility_budget | 26.90% | 48.59% | volatility_budget:523 / strategic_target:379 / emergency_overlay:11 | BREACH:11 / CAUTION:224 / EMERGENCY:236 / NORMAL:442 |
| core/all_cash/synthetic_30/main_cost | volatility_budget | 26.17% | 61.40% | emergency_overlay:655 / volatility_budget:234 / strategic_target:24 | BREACH:655 / CAUTION:12 / EMERGENCY:64 / NORMAL:182 |
| core/all_cash/synthetic_50/main_cost | volatility_budget | 27.04% | 61.40% | emergency_overlay:659 / volatility_budget:242 / strategic_target:12 | BREACH:659 / CAUTION:10 / EMERGENCY:58 / NORMAL:186 |
| core/all_cash/synthetic_70/main_cost | volatility_budget | 27.74% | 61.40% | emergency_overlay:648 / volatility_budget:245 / strategic_target:20 | BREACH:648 / CAUTION:10 / EMERGENCY:69 / NORMAL:186 |
| core/core_existing/strict/cost_0bps | volatility_budget | 27.46% | 73.34% | emergency_overlay:651 / volatility_budget:230 / strategic_target:32 | BREACH:651 / CAUTION:27 / EMERGENCY:82 / NORMAL:153 |
| core/core_existing/strict/cost_10bps | volatility_budget | 27.47% | 73.35% | emergency_overlay:651 / volatility_budget:230 / strategic_target:32 | BREACH:651 / CAUTION:27 / EMERGENCY:82 / NORMAL:153 |
| core/core_existing/strict/cost_25bps | volatility_budget | 27.47% | 73.37% | emergency_overlay:651 / volatility_budget:230 / strategic_target:32 | BREACH:651 / CAUTION:27 / EMERGENCY:82 / NORMAL:153 |
| core/core_existing/strict/main_cost | volatility_budget | 27.47% | 73.35% | emergency_overlay:651 / volatility_budget:230 / strategic_target:32 | BREACH:651 / CAUTION:27 / EMERGENCY:82 / NORMAL:153 |
| core/core_existing/strict/weekly_sensitivity | volatility_budget | 27.47% | 73.35% | emergency_overlay:639 / volatility_budget:235 / strategic_target:39 | BREACH:639 / CAUTION:37 / EMERGENCY:94 / NORMAL:143 |
| core/core_existing/synthetic_30/main_cost | volatility_budget | 28.55% | 73.35% | emergency_overlay:661 / volatility_budget:225 / strategic_target:27 | BREACH:661 / CAUTION:29 / EMERGENCY:72 / NORMAL:151 |
| core/core_existing/synthetic_50/main_cost | volatility_budget | 29.35% | 73.35% | emergency_overlay:677 / volatility_budget:226 / strategic_target:10 | BREACH:677 / CAUTION:27 / EMERGENCY:56 / NORMAL:153 |
| core/core_existing/synthetic_70/main_cost | volatility_budget | 30.01% | 73.35% | emergency_overlay:709 / volatility_budget:204 | BREACH:709 / CAUTION:27 / EMERGENCY:24 / NORMAL:153 |
| full/all_cash/strict/cost_0bps | volatility_budget | 24.46% | 48.59% | emergency_overlay:660 / volatility_budget:216 / strategic_target:37 | BREACH:660 / CAUTION:24 / EMERGENCY:72 / NORMAL:157 |
| full/all_cash/strict/cost_10bps | volatility_budget | 24.46% | 48.59% | emergency_overlay:662 / volatility_budget:215 / strategic_target:36 | BREACH:662 / CAUTION:24 / EMERGENCY:70 / NORMAL:157 |
| full/all_cash/strict/cost_25bps | volatility_budget | 24.46% | 48.59% | emergency_overlay:663 / volatility_budget:214 / strategic_target:36 | BREACH:663 / CAUTION:24 / EMERGENCY:69 / NORMAL:157 |
| full/all_cash/strict/main_cost | volatility_budget | 24.46% | 48.59% | emergency_overlay:662 / volatility_budget:215 / strategic_target:36 | BREACH:662 / CAUTION:24 / EMERGENCY:70 / NORMAL:157 |
| full/all_cash/strict/weekly_sensitivity | volatility_budget | 26.87% | 48.59% | volatility_budget:523 / strategic_target:379 / emergency_overlay:11 | BREACH:11 / CAUTION:230 / EMERGENCY:236 / NORMAL:436 |
| full/all_cash/synthetic_30/main_cost | volatility_budget | 26.14% | 61.40% | emergency_overlay:673 / volatility_budget:237 / strategic_target:3 | BREACH:673 / CAUTION:10 / EMERGENCY:49 / NORMAL:181 |
| full/all_cash/synthetic_50/main_cost | volatility_budget | 27.32% | 61.20% | emergency_overlay:633 / volatility_budget:254 / strategic_target:26 | BREACH:633 / CAUTION:19 / EMERGENCY:72 / NORMAL:189 |
| full/all_cash/synthetic_70/main_cost | volatility_budget | 28.32% | 62.03% | emergency_overlay:610 / volatility_budget:275 / strategic_target:28 | BREACH:610 / CAUTION:23 / EMERGENCY:91 / NORMAL:189 |
| full/core_existing/strict/cost_0bps | volatility_budget | 27.45% | 73.34% | emergency_overlay:651 / volatility_budget:230 / strategic_target:32 | BREACH:651 / CAUTION:27 / EMERGENCY:82 / NORMAL:153 |
| full/core_existing/strict/cost_10bps | volatility_budget | 27.45% | 73.35% | emergency_overlay:651 / volatility_budget:230 / strategic_target:32 | BREACH:651 / CAUTION:27 / EMERGENCY:82 / NORMAL:153 |
| full/core_existing/strict/cost_25bps | volatility_budget | 27.45% | 73.37% | emergency_overlay:651 / volatility_budget:230 / strategic_target:32 | BREACH:651 / CAUTION:27 / EMERGENCY:82 / NORMAL:153 |
| full/core_existing/strict/main_cost | volatility_budget | 27.45% | 73.35% | emergency_overlay:651 / volatility_budget:230 / strategic_target:32 | BREACH:651 / CAUTION:27 / EMERGENCY:82 / NORMAL:153 |
| full/core_existing/strict/weekly_sensitivity | volatility_budget | 27.46% | 73.35% | emergency_overlay:639 / volatility_budget:235 / strategic_target:39 | BREACH:639 / CAUTION:37 / EMERGENCY:94 / NORMAL:143 |
| full/core_existing/synthetic_30/main_cost | volatility_budget | 28.58% | 73.35% | emergency_overlay:657 / volatility_budget:235 / strategic_target:21 | BREACH:657 / CAUTION:24 / EMERGENCY:76 / NORMAL:156 |
| full/core_existing/synthetic_50/main_cost | volatility_budget | 29.64% | 73.35% | emergency_overlay:653 / volatility_budget:242 / strategic_target:18 | BREACH:653 / CAUTION:20 / EMERGENCY:74 / NORMAL:166 |
| full/core_existing/synthetic_70/main_cost | volatility_budget | 30.44% | 73.35% | emergency_overlay:661 / volatility_budget:234 / strategic_target:18 | BREACH:661 / CAUTION:21 / EMERGENCY:68 / NORMAL:163 |

「约束生效分布」回答本窗口内谁在主导仓位（volatility_budget=波动率预算、emergency_overlay=应急刹车、strategic_target=战略目标自身）；「应急状态分布」是分段刹车 NORMAL/CAUTION/EMERGENCY/BREACH 的评审计数（legacy 模式下为 LEGACY_LADDER 连续梯子）。估计组合波动率来自评审时点的点时协方差，与事后实现波动率互相独立。

## 仓位、换手与存续

| 实验 | 状态 | 成交笔数 | 首笔 | 末笔 | 末笔距期末(天) | 期末现金占比 | 平均现金占比 | 总换手 | 总成本 USD |
|---|---|---|---|---|---|---|---|---|---|
| core/all_cash/strict/cost_0bps | TRADING_STALLED | 77 | 2021-07-01 | 2022-11-22 | 404 | 75.01% | 79.50% | 0.443916 | 0 |
| core/all_cash/strict/cost_10bps | TRADING_STALLED | 77 | 2021-07-01 | 2022-11-22 | 404 | 75.03% | 79.52% | 0.444106 | 47.0381 |
| core/all_cash/strict/cost_25bps | TRADING_STALLED | 78 | 2021-07-01 | 2022-11-22 | 404 | 75.03% | 79.53% | 0.444177 | 117.576 |
| core/all_cash/strict/main_cost | TRADING_STALLED | 77 | 2021-07-01 | 2022-11-22 | 404 | 75.03% | 79.52% | 0.444168 | 70.5527 |
| core/all_cash/strict/weekly_sensitivity | TRADING_STALLED | 20 | 2021-07-26 | 2022-11-22 | 404 | 62.31% | 86.03% | 0.335133 | 49.3999 |
| core/all_cash/synthetic_30/main_cost | TRADING_STALLED | 170 | 2021-07-01 | 2023-01-10 | 355 | 84.45% | 84.49% | 0.921164 | 150.79 |
| core/all_cash/synthetic_50/main_cost | TRADING_STALLED | 168 | 2021-07-01 | 2023-01-05 | 360 | 84.80% | 84.94% | 0.898415 | 146.802 |
| core/all_cash/synthetic_70/main_cost | OK | 170 | 2021-07-01 | 2023-12-28 | 3 | 84.80% | 84.97% | 0.85188 | 139.536 |
| core/core_existing/strict/cost_0bps | TRADING_STALLED | 114 | 2021-07-01 | 2022-11-22 | 404 | 77.39% | 77.20% | 0.942022 | 0 |
| core/core_existing/strict/cost_10bps | TRADING_STALLED | 115 | 2021-07-01 | 2022-11-22 | 404 | 77.39% | 77.20% | 0.942247 | 109.786 |
| core/core_existing/strict/cost_25bps | TRADING_STALLED | 116 | 2021-07-01 | 2022-11-22 | 404 | 77.39% | 77.19% | 0.942584 | 274.381 |
| core/core_existing/strict/main_cost | TRADING_STALLED | 115 | 2021-07-01 | 2022-11-22 | 404 | 77.39% | 77.20% | 0.942053 | 164.631 |
| core/core_existing/strict/weekly_sensitivity | TRADING_STALLED | 74 | 2021-07-01 | 2022-11-22 | 404 | 83.64% | 81.11% | 0.764363 | 129.966 |
| core/core_existing/synthetic_30/main_cost | TRADING_STALLED | 168 | 2021-07-01 | 2023-01-10 | 355 | 78.93% | 78.01% | 0.945229 | 163.899 |
| core/core_existing/synthetic_50/main_cost | TRADING_STALLED | 168 | 2021-07-01 | 2023-01-05 | 360 | 85.11% | 81.14% | 0.844926 | 143.796 |
| core/core_existing/synthetic_70/main_cost | TRADING_STALLED | 170 | 2021-07-01 | 2023-04-05 | 270 | 86.23% | 82.11% | 0.841727 | 142.719 |
| full/all_cash/strict/cost_0bps | TRADING_STALLED | 71 | 2021-07-01 | 2022-11-22 | 404 | 75.01% | 79.50% | 0.443916 | 0 |
| full/all_cash/strict/cost_10bps | TRADING_STALLED | 71 | 2021-07-01 | 2022-11-22 | 404 | 75.03% | 79.52% | 0.444106 | 47.0381 |
| full/all_cash/strict/cost_25bps | TRADING_STALLED | 73 | 2021-07-01 | 2022-11-22 | 404 | 75.03% | 79.53% | 0.444177 | 117.576 |
| full/all_cash/strict/main_cost | TRADING_STALLED | 71 | 2021-07-01 | 2022-11-22 | 404 | 75.03% | 79.52% | 0.444168 | 70.5527 |
| full/all_cash/strict/weekly_sensitivity | OK | 22 | 2021-07-26 | 2023-12-18 | 13 | 64.35% | 86.39% | 0.318268 | 46.9702 |
| full/all_cash/synthetic_30/main_cost | TRADING_STALLED | 168 | 2021-07-01 | 2023-01-10 | 355 | 78.93% | 80.87% | 0.808169 | 132.521 |
| full/all_cash/synthetic_50/main_cost | OK | 177 | 2021-07-01 | 2023-11-10 | 51 | 84.50% | 84.94% | 1.10806 | 182.526 |
| full/all_cash/synthetic_70/main_cost | TRADING_STALLED | 177 | 2021-07-01 | 2022-11-05 | 421 | 84.06% | 84.84% | 1.01763 | 167.941 |
| full/core_existing/strict/cost_0bps | TRADING_STALLED | 111 | 2021-07-01 | 2022-11-22 | 404 | 77.39% | 77.20% | 0.942022 | 0 |
| full/core_existing/strict/cost_10bps | TRADING_STALLED | 112 | 2021-07-01 | 2022-11-22 | 404 | 77.39% | 77.20% | 0.942247 | 109.786 |
| full/core_existing/strict/cost_25bps | TRADING_STALLED | 113 | 2021-07-01 | 2022-11-22 | 404 | 77.39% | 77.19% | 0.942584 | 274.381 |
| full/core_existing/strict/main_cost | TRADING_STALLED | 112 | 2021-07-01 | 2022-11-22 | 404 | 77.39% | 77.20% | 0.942053 | 164.631 |
| full/core_existing/strict/weekly_sensitivity | TRADING_STALLED | 71 | 2021-07-01 | 2022-11-22 | 404 | 83.64% | 81.11% | 0.764363 | 129.966 |
| full/core_existing/synthetic_30/main_cost | TRADING_STALLED | 165 | 2021-07-01 | 2023-01-10 | 355 | 84.45% | 80.72% | 0.989526 | 171.997 |
| full/core_existing/synthetic_50/main_cost | OK | 182 | 2021-07-01 | 2023-12-03 | 28 | 84.80% | 81.02% | 1.05883 | 185.283 |
| full/core_existing/synthetic_70/main_cost | TRADING_STALLED | 200 | 2021-07-01 | 2022-11-05 | 421 | 85.65% | 81.83% | 0.957746 | 166.331 |

「末笔距期末」大于 60 天即说明曲线尾部处于无人管理状态，该段产生的回撤不代表策略的主动风控结果。

## 评估产物

### 决策评估

- 有效决策 5 条；被排除 52 条。
- 样本行 20 行，其中带已实现参考价的 0 行。
- 业绩分类：PAPER_ONLY 12，REALIZED_ELIGIBLE 8。

> 没有任何样本行带已实现参考价，因此决策质量没有样本，既不能判为有效，也不能判为无效。

### 评分评估

- 合同：`STRICT_POINT_IN_TIME_INPUTS`。
- 域：core、full。

## 校验发现项

- `INFO` `MANIFEST_BLOCKERS_IGNORE_SIGNAL_LAYER` manifest.json — manifest blockers cover btc_valuation.mvrv, flows.etf_aum, flows.etf_net, macro.dff, macro.walcl, market.ohlcv, market.stablecoin_supply, onchain.blockspace_fees, risk.chain_liveness_status, risk.security_event completeness only; factor availability and scoring coverage are not gated here, so an empty blocker list does not mean the signal layer is usable
- `ERROR` `COVERAGE_BELOW_INVESTABLE` full — median coverage 0.5100 never reaches minimum_investable_coverage 0.60 (max 0.8000); the score is a reduced function of the available factors, not the full model
- `ERROR` `SORTING_UNDERPOWERED` core/180d — only 10 independent blocks behind 1470 overlapping samples; spearman -0.2959718059899314 is not distinguishable from noise at this power
- `WARNING` `TRADING_STALLED` core_all_cash_strict_cost_0bps — last trade 2022-11-22 is 404 days before the window ends 2023-12-31; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` core_all_cash_strict_cost_10bps — last trade 2022-11-22 is 404 days before the window ends 2023-12-31; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` core_all_cash_strict_cost_25bps — last trade 2022-11-22 is 404 days before the window ends 2023-12-31; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` core_all_cash_strict_main_cost — last trade 2022-11-22 is 404 days before the window ends 2023-12-31; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` core_all_cash_strict_weekly_sensitivity — last trade 2022-11-22 is 404 days before the window ends 2023-12-31; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` core_all_cash_synthetic_30_main_cost — last trade 2023-01-10 is 355 days before the window ends 2023-12-31; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` core_all_cash_synthetic_50_main_cost — last trade 2023-01-05 is 360 days before the window ends 2023-12-31; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` core_core_existing_strict_cost_0bps — last trade 2022-11-22 is 404 days before the window ends 2023-12-31; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` core_core_existing_strict_cost_10bps — last trade 2022-11-22 is 404 days before the window ends 2023-12-31; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` core_core_existing_strict_cost_25bps — last trade 2022-11-22 is 404 days before the window ends 2023-12-31; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` core_core_existing_strict_main_cost — last trade 2022-11-22 is 404 days before the window ends 2023-12-31; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` core_core_existing_strict_weekly_sensitivity — last trade 2022-11-22 is 404 days before the window ends 2023-12-31; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` core_core_existing_synthetic_30_main_cost — last trade 2023-01-10 is 355 days before the window ends 2023-12-31; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` core_core_existing_synthetic_50_main_cost — last trade 2023-01-05 is 360 days before the window ends 2023-12-31; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` core_core_existing_synthetic_70_main_cost — last trade 2023-04-05 is 270 days before the window ends 2023-12-31; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` full_all_cash_strict_cost_0bps — last trade 2022-11-22 is 404 days before the window ends 2023-12-31; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` full_all_cash_strict_cost_10bps — last trade 2022-11-22 is 404 days before the window ends 2023-12-31; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` full_all_cash_strict_cost_25bps — last trade 2022-11-22 is 404 days before the window ends 2023-12-31; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` full_all_cash_strict_main_cost — last trade 2022-11-22 is 404 days before the window ends 2023-12-31; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` full_all_cash_synthetic_30_main_cost — last trade 2023-01-10 is 355 days before the window ends 2023-12-31; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` full_all_cash_synthetic_70_main_cost — last trade 2022-11-05 is 421 days before the window ends 2023-12-31; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` full_core_existing_strict_cost_0bps — last trade 2022-11-22 is 404 days before the window ends 2023-12-31; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` full_core_existing_strict_cost_10bps — last trade 2022-11-22 is 404 days before the window ends 2023-12-31; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` full_core_existing_strict_cost_25bps — last trade 2022-11-22 is 404 days before the window ends 2023-12-31; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` full_core_existing_strict_main_cost — last trade 2022-11-22 is 404 days before the window ends 2023-12-31; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` full_core_existing_strict_weekly_sensitivity — last trade 2022-11-22 is 404 days before the window ends 2023-12-31; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` full_core_existing_synthetic_30_main_cost — last trade 2023-01-10 is 355 days before the window ends 2023-12-31; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` full_core_existing_synthetic_70_main_cost — last trade 2022-11-05 is 421 days before the window ends 2023-12-31; the tail of the path is unmanaged
- `WARNING` `SCORE_BAND_COLLAPSED` btc — profile btc can only produce scores in [10.00, 90.00] at coverage 0.8000
- `WARNING` `SCORE_BAND_COLLAPSED` default — profile default can only produce scores in [21.00, 79.00] at coverage 0.5800
- `WARNING` `SCORE_BAND_COLLAPSED` defi_protocol — profile defi_protocol can only produce scores in [27.50, 72.50] at coverage 0.4500
- `ERROR` `SCORE_THRESHOLD_UNREACHABLE` satellite_full_score — threshold 85.0 needs coverage >= 0.7000 but observed coverage is lower for default, defi_protocol; the gate can never fire for those profiles
- `ERROR` `ENTRY_LOCKED_BY_COVERAGE` default — coverage 0.5800 is below minimum_investable_coverage 0.60, so the coverage gate is LOW and confidence_deployment_factor zeroes LOW; every score-driven increase is blocked regardless of score
- `ERROR` `ENTRY_LOCKED_BY_COVERAGE` defi_protocol — coverage 0.4500 is below minimum_investable_coverage 0.60, so the coverage gate is LOW and confidence_deployment_factor zeroes LOW; every score-driven increase is blocked regardless of score
- `WARNING` `REGIME_BELOW_REQUIRED_STABLE_TARGET` CAPITAL_PRESERVATION/WORST_CONFIGURED_ASSET — regime CAPITAL_PRESERVATION targets 0.5000 in stables but needs 0.6250 to hold the budget under POLICY_STRESS
- `WARNING` `REGIME_BELOW_REQUIRED_STABLE_TARGET` DEFENSIVE/CORE_ANCHOR — regime DEFENSIVE targets 0.3000 in stables but needs 0.3478 to hold the budget under POLICY_STRESS
- `WARNING` `REGIME_BELOW_REQUIRED_STABLE_TARGET` DEFENSIVE/WORST_CONFIGURED_ASSET — regime DEFENSIVE targets 0.3000 in stables but needs 0.6250 to hold the budget under POLICY_STRESS
- `WARNING` `REGIME_BELOW_REQUIRED_STABLE_TARGET` NORMAL/CORE_ANCHOR — regime NORMAL targets 0.1500 in stables but needs 0.3478 to hold the budget under POLICY_STRESS
- `WARNING` `REGIME_BELOW_REQUIRED_STABLE_TARGET` NORMAL/WORST_CONFIGURED_ASSET — regime NORMAL targets 0.1500 in stables but needs 0.6250 to hold the budget under POLICY_STRESS

## 限制

manifest 未报告阻断项——但阻断项只覆盖 OHLCV 完整性，不能据此认为信号层、评分覆盖或决策样本足以支撑结论。

严格模式保留缺失因子和硬门控；`SYNTHETIC_ASSUMPTIONS` 结果仅说明机制对 30/50/70 分假设的敏感度。未执行建议、合成评分和 USDT 计价近似均不计入真实账户业绩。
