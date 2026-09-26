# 2024-01-01 至 2026-09-24 回测与验证报告

- Run ID：`strategy-validation-2024-present`
- 数据口径：`USD_ASSUMED_STABLECOIN_PEG`
- 严格点时资格：`ELIGIBLE_WITH_ASSUMED_STABLE_PEG`（仅表示行情完整性，不代表信号层可用）
- 运行目录：`/Users/albert/.local/share/crypto-portfolio-manager/research/backtests/strategy-validation-2024-present`

本报告属于历史诊断与稳健性验证，不是未见样本上的预期收益证明。

## 结论先行

- 校验判决：`DEGENERATE_NOT_A_TEST_OF_THE_STRATEGY`（本运行不构成对策略的检验）
- 判据来源：`research/validity_gate.py`，只读取本运行已写出的 manifest / score-evaluation / trades.csv / decision-evaluation / series。
- 发现项：5 个 ERROR，41 个 WARNING。

> 按上述判决，本报告的业绩数字不构成对策略的检验，不得用于判断策略有效性；业绩指标只描述该运行实际走出的路径。

> 该运行使用的 policy（`0aa9fc65704d…`）与当前仓库 policy（`52e62aa06de8…`）不一致；策略可行性类结论描述的是当前 policy，而非该时点。

## 数据就绪度

### 因子可用率

| 域 | 因子 | 可用读数 | 可用占比 | MISSING | NOT_APPLICABLE |
|---|---|---|---|---|---|
| core | btc_valuation | 995/1990 | 50.00% | 0 | 995 |
| core | capital_flows | 1990/1990 | 100.00% | 0 | 0 |
| core | fundamentals | 0/1990 | 0.00% | 995 | 995 |
| core | macro_liquidity | 995/1990 | 50.00% | 0 | 995 |
| core | onchain | 995/1990 | 50.00% | 0 | 995 |
| core | relative_strength_btc | 995/1990 | 50.00% | 0 | 995 |
| core | trend | 1990/1990 | 100.00% | 0 | 0 |
| core | valuation | 0/1990 | 0.00% | 995 | 995 |
| full | btc_valuation | 995/4975 | 20.00% | 0 | 3980 |
| full | capital_flows | 3980/4975 | 80.00% | 0 | 995 |
| full | fundamentals | 0/4975 | 0.00% | 3980 | 995 |
| full | macro_liquidity | 995/4975 | 20.00% | 0 | 3980 |
| full | onchain | 995/4975 | 20.00% | 1990 | 1990 |
| full | relative_strength_btc | 3980/4975 | 80.00% | 0 | 995 |
| full | trend | 4975/4975 | 100.00% | 0 | 0 |
| full | valuation | 0/4975 | 0.00% | 3980 | 995 |

共 16 个「域 × 因子」组合，其中 4 个全程零可用读数。缺失因子按配置权重保留并把原始分收缩到中性 50，因此评分是可用因子的约化函数，而不是完整模型。

### 评分覆盖率

| 域 | 读数 | 最小 | 中位 | 最大 | 档位分布 |
|---|---|---|---|---|---|
| core | 1990 | 0.58 | 0.705 | 0.875 | HIGH 995，MEDIUM 995 |
| full | 4975 | 0.45 | 0.51 | 0.875 | HIGH 995，LOW 995，MEDIUM 2985 |

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
| core/all_cash/strict/cost_0bps | TRADING_STALLED | 43.25% | 14.10% | -22.39% | 19.03% | 0.787999 | 0 |
| core/all_cash/strict/cost_10bps | TRADING_STALLED | 43.10% | 14.06% | -22.41% | 19.03% | 0.786057 | 124.545 |
| core/all_cash/strict/cost_25bps | TRADING_STALLED | 42.82% | 13.98% | -22.43% | 19.03% | 0.782269 | 311.382 |
| core/all_cash/strict/main_cost | TRADING_STALLED | 43.02% | 14.04% | -22.41% | 19.03% | 0.784945 | 186.712 |
| core/all_cash/strict/weekly_sensitivity | TRADING_STALLED | 15.57% | 5.45% | -22.52% | 14.23% | 0.444329 | 87.7835 |
| core/all_cash/synthetic_30/main_cost | TRADING_STALLED | 34.49% | 11.49% | -20.56% | 18.56% | 0.678442 | 532.437 |
| core/all_cash/synthetic_50/main_cost | OK | 30.61% | 10.30% | -20.72% | 18.56% | 0.620822 | 520.666 |
| core/all_cash/synthetic_70/main_cost | OK | 31.51% | 10.58% | -20.91% | 18.56% | 0.634224 | 510.273 |
| core/core_existing/strict/cost_0bps | TRADING_STALLED | 34.55% | 11.51% | -22.60% | 19.38% | 0.658834 | 0 |
| core/core_existing/strict/cost_10bps | TRADING_STALLED | 34.37% | 11.46% | -22.60% | 19.38% | 0.656334 | 174.738 |
| core/core_existing/strict/cost_25bps | TRADING_STALLED | 34.10% | 11.37% | -22.63% | 19.38% | 0.652479 | 436.575 |
| core/core_existing/strict/main_cost | TRADING_STALLED | 34.27% | 11.42% | -22.61% | 19.38% | 0.654921 | 262.021 |
| core/core_existing/strict/weekly_sensitivity | TRADING_STALLED | 35.65% | 11.84% | -22.90% | 19.53% | 0.670546 | 199.689 |
| core/core_existing/synthetic_30/main_cost | TRADING_STALLED | 38.11% | 12.58% | -20.67% | 20.00% | 0.692537 | 523.449 |
| core/core_existing/synthetic_50/main_cost | OK | 38.76% | 12.78% | -20.70% | 19.91% | 0.703261 | 543.648 |
| core/core_existing/synthetic_70/main_cost | OK | 40.63% | 13.33% | -20.93% | 19.89% | 0.72841 | 555.964 |
| full/all_cash/strict/cost_0bps | TRADING_STALLED | 43.25% | 14.10% | -22.39% | 19.03% | 0.787999 | 0 |
| full/all_cash/strict/cost_10bps | TRADING_STALLED | 43.10% | 14.06% | -22.41% | 19.03% | 0.786057 | 124.545 |
| full/all_cash/strict/cost_25bps | TRADING_STALLED | 42.82% | 13.98% | -22.43% | 19.03% | 0.782269 | 311.382 |
| full/all_cash/strict/main_cost | TRADING_STALLED | 43.02% | 14.04% | -22.41% | 19.03% | 0.784945 | 186.712 |
| full/all_cash/strict/weekly_sensitivity | TRADING_STALLED | 15.57% | 5.45% | -22.52% | 14.23% | 0.444329 | 87.7835 |
| full/all_cash/synthetic_30/main_cost | TRADING_STALLED | 33.92% | 11.32% | -22.01% | 18.70% | 0.666545 | 560.051 |
| full/all_cash/synthetic_50/main_cost | OK | 41.70% | 13.65% | -20.37% | 17.61% | 0.814528 | 708.74 |
| full/all_cash/synthetic_70/main_cost | OK | 28.31% | 9.58% | -19.91% | 17.00% | 0.623014 | 692.227 |
| full/core_existing/strict/cost_0bps | TRADING_STALLED | 35.63% | 11.84% | -22.42% | 19.43% | 0.672636 | 0 |
| full/core_existing/strict/cost_10bps | TRADING_STALLED | 35.45% | 11.78% | -22.41% | 19.43% | 0.670285 | 171.438 |
| full/core_existing/strict/cost_25bps | TRADING_STALLED | 35.19% | 11.70% | -22.44% | 19.43% | 0.666485 | 428.3 |
| full/core_existing/strict/main_cost | TRADING_STALLED | 35.35% | 11.75% | -22.42% | 19.43% | 0.668898 | 257.072 |
| full/core_existing/strict/weekly_sensitivity | TRADING_STALLED | 35.65% | 11.84% | -22.90% | 19.53% | 0.670546 | 199.689 |
| full/core_existing/synthetic_30/main_cost | TRADING_STALLED | 40.16% | 13.20% | -21.99% | 19.80% | 0.724696 | 608.192 |
| full/core_existing/synthetic_50/main_cost | OK | 49.43% | 15.89% | -20.36% | 18.52% | 0.888667 | 818.18 |
| full/core_existing/synthetic_70/main_cost | OK | 34.71% | 11.56% | -19.91% | 17.76% | 0.704636 | 779.199 |

状态列由有效性校验判定：`ONE_WAY_RATCHER`（只朝一个方向交易）、`NO_TRADES_IN_WINDOW`（从未交易）、`TRADING_STALLED`（尾部长时间不交易）。本次共 24 个实验带标记，其中 0 个（单向棘轮 / 从未交易）不构成策略行为，其数字不代表策略业绩。

## 基准比较

### 决策对照（先看这张）

| 实验 | 策略累计 | 策略波动率 | 策略MaxDD | 策略Sharpe | 不动起点累计 | 不动起点年化超额 | 同风险累计 | 同风险年化超额 |
|---|---|---|---|---|---|---|---|---|
| core/all_cash/strict/cost_0bps | 43.25% | 19.03% | -22.39% | 0.787999 | 0.00% | 14.10% | 43.44% | -0.06% |
| core/all_cash/strict/cost_10bps | 43.10% | 19.03% | -22.41% | 0.786057 | 0.00% | 14.06% | 42.79% | 0.09% |
| core/all_cash/strict/cost_25bps | 42.82% | 19.03% | -22.43% | 0.782269 | 0.00% | 13.98% | 41.82% | 0.30% |
| core/all_cash/strict/main_cost | 43.02% | 19.03% | -22.41% | 0.784945 | 0.00% | 14.04% | 42.46% | 0.16% |
| core/all_cash/strict/weekly_sensitivity | 15.57% | 14.23% | -22.52% | 0.444329 | 0.00% | 5.45% | 31.39% | -5.09% |
| core/all_cash/synthetic_30/main_cost | 34.49% | 18.56% | -20.56% | 0.678442 | 0.00% | 11.49% | 41.38% | -2.06% |
| core/all_cash/synthetic_50/main_cost | 30.61% | 18.56% | -20.72% | 0.620822 | 0.00% | 10.30% | 41.36% | -3.25% |
| core/all_cash/synthetic_70/main_cost | 31.51% | 18.56% | -20.91% | 0.634224 | 0.00% | 10.58% | 41.38% | -2.98% |
| core/core_existing/strict/cost_0bps | 34.55% | 19.38% | -22.60% | 0.658834 | 67.91% | -9.45% | 44.26% | -2.89% |
| core/core_existing/strict/cost_10bps | 34.37% | 19.38% | -22.60% | 0.656334 | 67.83% | -9.48% | 43.59% | -2.75% |
| core/core_existing/strict/cost_25bps | 34.10% | 19.38% | -22.63% | 0.652479 | 67.70% | -9.53% | 42.61% | -2.54% |
| core/core_existing/strict/main_cost | 34.27% | 19.38% | -22.61% | 0.654921 | 67.75% | -9.49% | 43.26% | -2.68% |
| core/core_existing/strict/weekly_sensitivity | 35.65% | 19.53% | -22.90% | 0.670546 | 67.75% | -9.07% | 43.61% | -2.37% |
| core/core_existing/synthetic_30/main_cost | 38.11% | 20.00% | -20.67% | 0.692537 | 67.75% | -8.33% | 44.69% | -1.94% |
| core/core_existing/synthetic_50/main_cost | 38.76% | 19.91% | -20.70% | 0.703261 | 67.75% | -8.13% | 44.50% | -1.69% |
| core/core_existing/synthetic_70/main_cost | 40.63% | 19.89% | -20.93% | 0.72841 | 67.75% | -7.58% | 44.45% | -1.12% |
| full/all_cash/strict/cost_0bps | 43.25% | 19.03% | -22.39% | 0.787999 | 0.00% | 14.10% | 43.44% | -0.06% |
| full/all_cash/strict/cost_10bps | 43.10% | 19.03% | -22.41% | 0.786057 | 0.00% | 14.06% | 42.79% | 0.09% |
| full/all_cash/strict/cost_25bps | 42.82% | 19.03% | -22.43% | 0.782269 | 0.00% | 13.98% | 41.82% | 0.30% |
| full/all_cash/strict/main_cost | 43.02% | 19.03% | -22.41% | 0.784945 | 0.00% | 14.04% | 42.46% | 0.16% |
| full/all_cash/strict/weekly_sensitivity | 15.57% | 14.23% | -22.52% | 0.444329 | 0.00% | 5.45% | 31.39% | -5.09% |
| full/all_cash/synthetic_30/main_cost | 33.92% | 18.70% | -22.01% | 0.666545 | 0.00% | 11.32% | 41.70% | -2.33% |
| full/all_cash/synthetic_50/main_cost | 41.70% | 17.61% | -20.37% | 0.814528 | 0.00% | 13.65% | 39.17% | 0.75% |
| full/all_cash/synthetic_70/main_cost | 28.31% | 17.00% | -19.91% | 0.623014 | 0.00% | 9.58% | 37.77% | -2.90% |
| full/core_existing/strict/cost_0bps | 35.63% | 19.43% | -22.42% | 0.672636 | 67.91% | -9.12% | 44.38% | -2.60% |
| full/core_existing/strict/cost_10bps | 35.45% | 19.43% | -22.41% | 0.670285 | 67.83% | -9.15% | 43.71% | -2.45% |
| full/core_existing/strict/cost_25bps | 35.19% | 19.43% | -22.44% | 0.666485 | 67.70% | -9.20% | 42.73% | -2.25% |
| full/core_existing/strict/main_cost | 35.35% | 19.43% | -22.42% | 0.668898 | 67.75% | -9.16% | 43.38% | -2.39% |
| full/core_existing/strict/weekly_sensitivity | 35.65% | 19.53% | -22.90% | 0.670546 | 67.75% | -9.07% | 43.61% | -2.37% |
| full/core_existing/synthetic_30/main_cost | 40.16% | 19.80% | -21.99% | 0.724696 | 67.75% | -7.72% | 44.24% | -1.20% |
| full/core_existing/synthetic_50/main_cost | 49.43% | 18.52% | -20.36% | 0.888667 | 67.75% | -5.03% | 41.27% | 2.36% |
| full/core_existing/synthetic_70/main_cost | 34.71% | 17.76% | -19.91% | 0.704636 | 67.75% | -9.35% | 39.51% | -1.44% |

主基准是「同风险」（vol-matched BTC/cash）：把基准波动率压到与策略相同后再比收益，回答「这套复杂策略在承担相近风险时是否值得」。「不动起点」回答「主动决策相对『什么都不做』是否创造了价值」；100% BTC 只是机会成本参考，不是风险匹配基准——一个以降险为目标的策略可以合理地输给它而赢下同风险口径，只看 100% BTC 会把降险本身误读成失败。「年化超额」= 策略 CAGR − 基准 CAGR，为正才表示该口径下主动决策创造了价值；累计收益跨整个窗口，不能与年化数混用。

### 逐个基准明细

单向棘轮与从未交易的实验不进入基准比较表——它们没有测量策略行为；尾部休眠的实验保留在表中，但状态列会标出 `TRADING_STALLED`。

| 实验 | 基准 | 基准(零成本) | 基准(含成本) | 策略 | 累计超额 | 年化超额 | 基准MaxDD | 策略MaxDD | Sharpe差 | 基准波动率 |
|---|---|---|---|---|---|---|---|---|---|---|
| core/all_cash/strict/cost_0bps | vol_matched_btc_cash | 不可用 | 43.44% | 43.25% | -0.20% | -0.06% | -24.64% | -22.39% | -0.00276161 | 19.03% |
| core/all_cash/strict/cost_0bps | vol_matched_btc_eth_70_30_cash | 不可用 | 36.01% | 43.25% | 7.23% | 2.15% | -25.26% | -22.39% | 0.0998274 | 19.03% |
| core/all_cash/strict/cost_0bps | static_initial_weights | 不可用 | 0.00% | 43.25% | 43.25% | 14.10% | 0.00% | -22.39% | 不可用 | 0.00% |
| core/all_cash/strict/cost_0bps | exposure_matched_btc_cash | 不可用 | 41.34% | 43.25% | 1.90% | 0.56% | -23.59% | -22.39% | -0.00276161 | 18.14% |
| core/all_cash/strict/cost_0bps | btc_eth_70_30 | 79.90% | 79.90% | 43.25% | -36.65% | -9.95% | -56.07% | -22.39% | 0.106805 | 49.68% |
| core/all_cash/strict/cost_0bps | btc_buy_and_hold | 104.85% | 104.85% | 43.25% | -61.61% | -16.01% | -52.97% | -22.39% | -0.00276161 | 47.48% |
| core/all_cash/strict/cost_10bps | vol_matched_btc_cash | 不可用 | 42.79% | 43.10% | 0.31% | 0.09% | -24.73% | -22.41% | 0.00406527 | 19.03% |
| core/all_cash/strict/cost_10bps | vol_matched_btc_eth_70_30_cash | 不可用 | 35.31% | 43.10% | 7.79% | 2.32% | -25.36% | -22.41% | 0.107826 | 19.03% |
| core/all_cash/strict/cost_10bps | static_initial_weights | 不可用 | 0.00% | 43.10% | 43.10% | 14.06% | 0.00% | -22.41% | 不可用 | 0.00% |
| core/all_cash/strict/cost_10bps | exposure_matched_btc_cash | 不可用 | 40.71% | 43.10% | 2.39% | 0.70% | -23.67% | -22.41% | 0.00431923 | 18.14% |
| core/all_cash/strict/cost_10bps | btc_eth_70_30 | 79.90% | 79.78% | 43.10% | -36.67% | -9.96% | -56.06% | -22.41% | 0.105321 | 49.67% |
| core/all_cash/strict/cost_10bps | btc_buy_and_hold | 104.85% | 104.65% | 43.10% | -61.55% | -16.01% | -52.97% | -22.41% | -0.00395523 | 47.47% |
| core/all_cash/strict/cost_25bps | vol_matched_btc_cash | 不可用 | 41.82% | 42.82% | 1.01% | 0.30% | -24.85% | -22.43% | 0.0134372 | 19.03% |
| core/all_cash/strict/cost_25bps | vol_matched_btc_eth_70_30_cash | 不可用 | 34.27% | 42.82% | 8.55% | 2.55% | -25.50% | -22.43% | 0.118954 | 19.03% |
| core/all_cash/strict/cost_25bps | static_initial_weights | 不可用 | 0.00% | 42.82% | 42.82% | 13.98% | 0.00% | -22.43% | 不可用 | 0.00% |
| core/all_cash/strict/cost_25bps | exposure_matched_btc_cash | 不可用 | 39.75% | 42.82% | 3.07% | 0.91% | -23.79% | -22.43% | 0.0140768 | 18.13% |
| core/all_cash/strict/cost_25bps | btc_eth_70_30 | 79.90% | 79.59% | 42.82% | -36.77% | -10.00% | -56.05% | -22.43% | 0.10222 | 49.66% |
| core/all_cash/strict/cost_25bps | btc_buy_and_hold | 104.85% | 104.34% | 42.82% | -61.52% | -16.02% | -52.97% | -22.43% | -0.00662027 | 47.47% |
| core/all_cash/strict/main_cost | vol_matched_btc_cash | 不可用 | 42.46% | 43.02% | 0.56% | 0.16% | -24.77% | -22.41% | 0.00735271 | 19.03% |
| core/all_cash/strict/main_cost | vol_matched_btc_eth_70_30_cash | 不可用 | 34.96% | 43.02% | 8.06% | 2.40% | -25.41% | -22.41% | 0.111696 | 19.03% |
| core/all_cash/strict/main_cost | static_initial_weights | 不可用 | 0.00% | 43.02% | 43.02% | 14.04% | 0.00% | -22.41% | 不可用 | 0.00% |
| core/all_cash/strict/main_cost | exposure_matched_btc_cash | 不可用 | 40.39% | 43.02% | 2.63% | 0.77% | -23.71% | -22.41% | 0.00773347 | 18.14% |
| core/all_cash/strict/main_cost | btc_eth_70_30 | 79.90% | 79.69% | 43.02% | -36.67% | -9.97% | -56.06% | -22.41% | 0.104572 | 49.67% |
| core/all_cash/strict/main_cost | btc_buy_and_hold | 104.85% | 104.55% | 43.02% | -61.53% | -16.01% | -52.97% | -22.41% | -0.00469269 | 47.47% |
| core/all_cash/strict/weekly_sensitivity | vol_matched_btc_cash | 不可用 | 31.39% | 15.57% | -15.83% | -5.09% | -18.98% | -22.52% | -0.331217 | 14.22% |
| core/all_cash/strict/weekly_sensitivity | vol_matched_btc_eth_70_30_cash | 不可用 | 26.20% | 15.57% | -10.64% | -3.46% | -19.49% | -22.52% | -0.22718 | 14.23% |
| core/all_cash/strict/weekly_sensitivity | static_initial_weights | 不可用 | 0.00% | 15.57% | 15.57% | 5.45% | 0.00% | -22.52% | 不可用 | 0.00% |
| core/all_cash/strict/weekly_sensitivity | exposure_matched_btc_cash | 不可用 | 30.57% | 15.57% | -15.00% | -4.83% | -18.53% | -22.52% | -0.331063 | 13.86% |
| core/all_cash/strict/weekly_sensitivity | btc_eth_70_30 | 79.90% | 79.69% | 15.57% | -64.12% | -18.55% | -56.06% | -22.52% | -0.236043 | 49.67% |
| core/all_cash/strict/weekly_sensitivity | btc_buy_and_hold | 104.85% | 104.55% | 15.57% | -88.98% | -24.59% | -52.97% | -22.52% | -0.345308 | 47.47% |
| core/all_cash/synthetic_30/main_cost | vol_matched_btc_cash | 不可用 | 41.38% | 34.49% | -6.89% | -2.06% | -24.22% | -20.56% | -0.0989497 | 18.56% |
| core/all_cash/synthetic_30/main_cost | vol_matched_btc_eth_70_30_cash | 不可用 | 34.12% | 34.49% | 0.37% | 0.11% | -24.84% | -20.56% | 0.00536106 | 18.56% |
| core/all_cash/synthetic_30/main_cost | static_initial_weights | 不可用 | 0.00% | 34.49% | 34.49% | 11.49% | 0.00% | -20.56% | 不可用 | 0.00% |
| core/all_cash/synthetic_30/main_cost | exposure_matched_btc_cash | 不可用 | 35.26% | 34.49% | -0.77% | -0.23% | -21.04% | -20.56% | -0.0978206 | 15.91% |
| core/all_cash/synthetic_30/main_cost | btc_eth_70_30 | 79.90% | 79.69% | 34.49% | -45.20% | -12.51% | -56.06% | -20.56% | -0.00193012 | 49.67% |
| core/all_cash/synthetic_30/main_cost | btc_buy_and_hold | 104.85% | 104.55% | 34.49% | -70.06% | -18.55% | -52.97% | -20.56% | -0.111195 | 47.47% |
| core/all_cash/synthetic_50/main_cost | vol_matched_btc_cash | 不可用 | 41.36% | 30.61% | -10.75% | -3.25% | -24.21% | -20.72% | -0.156567 | 18.55% |
| core/all_cash/synthetic_50/main_cost | vol_matched_btc_eth_70_30_cash | 不可用 | 34.10% | 30.61% | -3.49% | -1.07% | -24.83% | -20.72% | -0.0522565 | 18.55% |
| core/all_cash/synthetic_50/main_cost | static_initial_weights | 不可用 | 0.00% | 30.61% | 30.61% | 10.30% | 0.00% | -20.72% | 不可用 | 0.00% |
| core/all_cash/synthetic_50/main_cost | exposure_matched_btc_cash | 不可用 | 34.49% | 30.61% | -3.89% | -1.19% | -20.64% | -20.72% | -0.1553 | 15.58% |
| core/all_cash/synthetic_50/main_cost | btc_eth_70_30 | 79.90% | 79.69% | 30.61% | -49.08% | -13.70% | -56.06% | -20.72% | -0.0595508 | 49.67% |
| core/all_cash/synthetic_50/main_cost | btc_buy_and_hold | 104.85% | 104.55% | 30.61% | -73.94% | -19.74% | -52.97% | -20.72% | -0.168815 | 47.47% |
| core/all_cash/synthetic_70/main_cost | vol_matched_btc_cash | 不可用 | 41.38% | 31.51% | -9.87% | -2.98% | -24.22% | -20.91% | -0.143168 | 18.56% |
| core/all_cash/synthetic_70/main_cost | vol_matched_btc_eth_70_30_cash | 不可用 | 34.11% | 31.51% | -2.61% | -0.80% | -24.84% | -20.91% | -0.0388573 | 18.56% |
| core/all_cash/synthetic_70/main_cost | static_initial_weights | 不可用 | 0.00% | 31.51% | 31.51% | 10.58% | 0.00% | -20.91% | 不可用 | 0.00% |
| core/all_cash/synthetic_70/main_cost | exposure_matched_btc_cash | 不可用 | 34.54% | 31.51% | -3.03% | -0.93% | -20.66% | -20.91% | -0.141906 | 15.60% |
| core/all_cash/synthetic_70/main_cost | btc_eth_70_30 | 79.90% | 79.69% | 31.51% | -48.18% | -13.42% | -56.06% | -20.91% | -0.0461487 | 49.67% |
| core/all_cash/synthetic_70/main_cost | btc_buy_and_hold | 104.85% | 104.55% | 31.51% | -73.04% | -19.47% | -52.97% | -20.91% | -0.155413 | 47.47% |
| core/core_existing/strict/cost_0bps | vol_matched_btc_cash | 不可用 | 44.26% | 34.55% | -9.71% | -2.89% | -25.05% | -22.60% | -0.131927 | 19.38% |
| core/core_existing/strict/cost_0bps | vol_matched_btc_eth_70_30_cash | 不可用 | 36.65% | 34.55% | -2.10% | -0.64% | -25.68% | -22.60% | -0.0293379 | 19.38% |
| core/core_existing/strict/cost_0bps | static_initial_weights | 不可用 | 67.91% | 34.55% | -33.36% | -9.45% | -52.60% | -22.60% | 0.0104672 | 44.68% |
| core/core_existing/strict/cost_0bps | exposure_matched_btc_cash | 不可用 | 41.49% | 34.55% | -6.94% | -2.08% | -23.66% | -22.60% | -0.131927 | 18.20% |
| core/core_existing/strict/cost_0bps | btc_eth_70_30 | 79.90% | 79.90% | 34.55% | -45.35% | -12.55% | -56.07% | -22.60% | -0.0223605 | 49.68% |
| core/core_existing/strict/cost_0bps | btc_buy_and_hold | 104.85% | 104.85% | 34.55% | -70.30% | -18.61% | -52.97% | -22.60% | -0.131927 | 47.48% |
| core/core_existing/strict/cost_10bps | vol_matched_btc_cash | 不可用 | 43.59% | 34.37% | -9.22% | -2.75% | -25.13% | -22.60% | -0.125755 | 19.38% |
| core/core_existing/strict/cost_10bps | vol_matched_btc_eth_70_30_cash | 不可用 | 35.94% | 34.37% | -1.57% | -0.48% | -25.77% | -22.60% | -0.0219783 | 19.38% |
| core/core_existing/strict/cost_10bps | static_initial_weights | 不可用 | 67.83% | 34.37% | -33.46% | -9.48% | -52.62% | -22.60% | 0.00848802 | 44.70% |
| core/core_existing/strict/cost_10bps | exposure_matched_btc_cash | 不可用 | 40.84% | 34.37% | -6.47% | -1.94% | -23.74% | -22.60% | -0.125419 | 18.20% |
| core/core_existing/strict/cost_10bps | btc_eth_70_30 | 79.90% | 79.78% | 34.37% | -45.40% | -12.57% | -56.06% | -22.60% | -0.0244021 | 49.67% |
| core/core_existing/strict/cost_10bps | btc_buy_and_hold | 104.85% | 104.65% | 34.37% | -70.28% | -18.61% | -52.97% | -22.60% | -0.133678 | 47.47% |
| core/core_existing/strict/cost_25bps | vol_matched_btc_cash | 不可用 | 42.61% | 34.10% | -8.51% | -2.54% | -25.26% | -22.63% | -0.116598 | 19.38% |
| core/core_existing/strict/cost_25bps | vol_matched_btc_eth_70_30_cash | 不可用 | 34.88% | 34.10% | -0.78% | -0.24% | -25.91% | -22.63% | -0.0110407 | 19.38% |
| core/core_existing/strict/cost_25bps | static_initial_weights | 不可用 | 67.70% | 34.10% | -33.60% | -9.53% | -52.65% | -22.63% | 0.00541387 | 44.74% |
| core/core_existing/strict/cost_25bps | exposure_matched_btc_cash | 不可用 | 39.90% | 34.10% | -5.80% | -1.74% | -23.87% | -22.63% | -0.115758 | 18.19% |
| core/core_existing/strict/cost_25bps | btc_eth_70_30 | 79.90% | 79.59% | 34.10% | -45.49% | -12.61% | -56.05% | -22.63% | -0.0275698 | 49.66% |
| core/core_existing/strict/cost_25bps | btc_buy_and_hold | 104.85% | 104.34% | 34.10% | -70.24% | -18.62% | -52.97% | -22.63% | -0.13641 | 47.47% |
| core/core_existing/strict/main_cost | vol_matched_btc_cash | 不可用 | 43.26% | 34.27% | -8.99% | -2.68% | -25.17% | -22.61% | -0.122817 | 19.38% |
| core/core_existing/strict/main_cost | vol_matched_btc_eth_70_30_cash | 不可用 | 35.58% | 34.27% | -1.31% | -0.40% | -25.82% | -22.61% | -0.01845 | 19.38% |
| core/core_existing/strict/main_cost | static_initial_weights | 不可用 | 67.75% | 34.27% | -33.48% | -9.49% | -52.62% | -22.61% | 0.0074337 | 44.70% |
| core/core_existing/strict/main_cost | exposure_matched_btc_cash | 不可用 | 40.52% | 34.27% | -6.25% | -1.88% | -23.78% | -22.61% | -0.122314 | 18.19% |
| core/core_existing/strict/main_cost | btc_eth_70_30 | 79.90% | 79.69% | 34.27% | -45.42% | -12.58% | -56.06% | -22.61% | -0.0254511 | 49.67% |
| core/core_existing/strict/main_cost | btc_buy_and_hold | 104.85% | 104.55% | 34.27% | -70.28% | -18.62% | -52.97% | -22.61% | -0.134716 | 47.47% |
| core/core_existing/strict/weekly_sensitivity | vol_matched_btc_cash | 不可用 | 43.61% | 35.65% | -7.96% | -2.37% | -25.35% | -22.90% | -0.107258 | 19.53% |
| core/core_existing/strict/weekly_sensitivity | vol_matched_btc_eth_70_30_cash | 不可用 | 35.86% | 35.65% | -0.21% | -0.06% | -26.00% | -22.90% | -0.00288059 | 19.53% |
| core/core_existing/strict/weekly_sensitivity | static_initial_weights | 不可用 | 67.75% | 35.65% | -32.10% | -9.07% | -52.62% | -22.90% | 0.0230578 | 44.70% |
| core/core_existing/strict/weekly_sensitivity | exposure_matched_btc_cash | 不可用 | 40.20% | 35.65% | -4.54% | -1.36% | -23.61% | -22.90% | -0.106629 | 18.05% |
| core/core_existing/strict/weekly_sensitivity | btc_eth_70_30 | 79.90% | 79.69% | 35.65% | -44.03% | -12.16% | -56.06% | -22.90% | -0.00982698 | 49.67% |
| core/core_existing/strict/weekly_sensitivity | btc_buy_and_hold | 104.85% | 104.55% | 35.65% | -68.89% | -18.20% | -52.97% | -22.90% | -0.119092 | 47.47% |
| core/core_existing/synthetic_30/main_cost | vol_matched_btc_cash | 不可用 | 44.69% | 38.11% | -6.58% | -1.94% | -25.89% | -20.67% | -0.0854644 | 19.99% |
| core/core_existing/synthetic_30/main_cost | vol_matched_btc_eth_70_30_cash | 不可用 | 36.69% | 38.11% | 1.42% | 0.43% | -26.55% | -20.67% | 0.0189457 | 19.99% |
| core/core_existing/synthetic_30/main_cost | static_initial_weights | 不可用 | 67.75% | 38.11% | -29.64% | -8.33% | -52.62% | -20.67% | 0.045049 | 44.70% |
| core/core_existing/synthetic_30/main_cost | exposure_matched_btc_cash | 不可用 | 37.30% | 38.11% | 0.81% | 0.24% | -22.12% | -20.67% | -0.0841044 | 16.80% |
| core/core_existing/synthetic_30/main_cost | btc_eth_70_30 | 79.90% | 79.69% | 38.11% | -41.57% | -11.42% | -56.06% | -20.67% | 0.0121642 | 49.67% |
| core/core_existing/synthetic_30/main_cost | btc_buy_and_hold | 104.85% | 104.55% | 38.11% | -66.44% | -17.46% | -52.97% | -20.67% | -0.0971005 | 47.47% |
| core/core_existing/synthetic_50/main_cost | vol_matched_btc_cash | 不可用 | 44.50% | 38.76% | -5.73% | -1.69% | -25.79% | -20.70% | -0.0747052 | 19.91% |
| core/core_existing/synthetic_50/main_cost | vol_matched_btc_eth_70_30_cash | 不可用 | 36.54% | 38.76% | 2.22% | 0.67% | -26.45% | -20.70% | 0.029699 | 19.91% |
| core/core_existing/synthetic_50/main_cost | static_initial_weights | 不可用 | 67.75% | 38.76% | -28.99% | -8.13% | -52.62% | -20.70% | 0.0557729 | 44.70% |
| core/core_existing/synthetic_50/main_cost | exposure_matched_btc_cash | 不可用 | 37.01% | 38.76% | 1.75% | 0.52% | -21.97% | -20.70% | -0.0733274 | 16.67% |
| core/core_existing/synthetic_50/main_cost | btc_eth_70_30 | 79.90% | 79.69% | 38.76% | -40.92% | -11.22% | -56.06% | -20.70% | 0.0228881 | 49.67% |
| core/core_existing/synthetic_50/main_cost | btc_buy_and_hold | 104.85% | 104.55% | 38.76% | -65.78% | -17.26% | -52.97% | -20.70% | -0.0863766 | 47.47% |
| core/core_existing/synthetic_70/main_cost | vol_matched_btc_cash | 不可用 | 44.45% | 40.63% | -3.82% | -1.12% | -25.77% | -20.93% | -0.0495485 | 19.89% |
| core/core_existing/synthetic_70/main_cost | vol_matched_btc_eth_70_30_cash | 不可用 | 36.51% | 40.63% | 4.12% | 1.23% | -26.43% | -20.93% | 0.0548545 | 19.89% |
| core/core_existing/synthetic_70/main_cost | static_initial_weights | 不可用 | 67.75% | 40.63% | -27.12% | -7.58% | -52.62% | -20.93% | 0.0809218 | 44.70% |
| core/core_existing/synthetic_70/main_cost | exposure_matched_btc_cash | 不可用 | 37.08% | 40.63% | 3.56% | 1.06% | -22.00% | -20.93% | -0.0481897 | 16.70% |
| core/core_existing/synthetic_70/main_cost | btc_eth_70_30 | 79.90% | 79.69% | 40.63% | -39.05% | -10.67% | -56.06% | -20.93% | 0.048037 | 49.67% |
| core/core_existing/synthetic_70/main_cost | btc_buy_and_hold | 104.85% | 104.55% | 40.63% | -63.91% | -16.71% | -52.97% | -20.93% | -0.0612277 | 47.47% |
| full/all_cash/strict/cost_0bps | vol_matched_btc_cash | 不可用 | 43.44% | 43.25% | -0.20% | -0.06% | -24.64% | -22.39% | -0.00276161 | 19.03% |
| full/all_cash/strict/cost_0bps | vol_matched_btc_eth_70_30_cash | 不可用 | 36.01% | 43.25% | 7.23% | 2.15% | -25.26% | -22.39% | 0.0998274 | 19.03% |
| full/all_cash/strict/cost_0bps | static_initial_weights | 不可用 | 0.00% | 43.25% | 43.25% | 14.10% | 0.00% | -22.39% | 不可用 | 0.00% |
| full/all_cash/strict/cost_0bps | exposure_matched_btc_cash | 不可用 | 41.34% | 43.25% | 1.90% | 0.56% | -23.59% | -22.39% | -0.00276161 | 18.14% |
| full/all_cash/strict/cost_0bps | btc_eth_70_30 | 79.90% | 79.90% | 43.25% | -36.65% | -9.95% | -56.07% | -22.39% | 0.106805 | 49.68% |
| full/all_cash/strict/cost_0bps | btc_buy_and_hold | 104.85% | 104.85% | 43.25% | -61.61% | -16.01% | -52.97% | -22.39% | -0.00276161 | 47.48% |
| full/all_cash/strict/cost_10bps | vol_matched_btc_cash | 不可用 | 42.79% | 43.10% | 0.31% | 0.09% | -24.73% | -22.41% | 0.00406527 | 19.03% |
| full/all_cash/strict/cost_10bps | vol_matched_btc_eth_70_30_cash | 不可用 | 35.31% | 43.10% | 7.79% | 2.32% | -25.36% | -22.41% | 0.107826 | 19.03% |
| full/all_cash/strict/cost_10bps | static_initial_weights | 不可用 | 0.00% | 43.10% | 43.10% | 14.06% | 0.00% | -22.41% | 不可用 | 0.00% |
| full/all_cash/strict/cost_10bps | exposure_matched_btc_cash | 不可用 | 40.71% | 43.10% | 2.39% | 0.70% | -23.67% | -22.41% | 0.00431923 | 18.14% |
| full/all_cash/strict/cost_10bps | btc_eth_70_30 | 79.90% | 79.78% | 43.10% | -36.67% | -9.96% | -56.06% | -22.41% | 0.105321 | 49.67% |
| full/all_cash/strict/cost_10bps | btc_buy_and_hold | 104.85% | 104.65% | 43.10% | -61.55% | -16.01% | -52.97% | -22.41% | -0.00395523 | 47.47% |
| full/all_cash/strict/cost_25bps | vol_matched_btc_cash | 不可用 | 41.82% | 42.82% | 1.01% | 0.30% | -24.85% | -22.43% | 0.0134372 | 19.03% |
| full/all_cash/strict/cost_25bps | vol_matched_btc_eth_70_30_cash | 不可用 | 34.27% | 42.82% | 8.55% | 2.55% | -25.50% | -22.43% | 0.118954 | 19.03% |
| full/all_cash/strict/cost_25bps | static_initial_weights | 不可用 | 0.00% | 42.82% | 42.82% | 13.98% | 0.00% | -22.43% | 不可用 | 0.00% |
| full/all_cash/strict/cost_25bps | exposure_matched_btc_cash | 不可用 | 39.75% | 42.82% | 3.07% | 0.91% | -23.79% | -22.43% | 0.0140768 | 18.13% |
| full/all_cash/strict/cost_25bps | btc_eth_70_30 | 79.90% | 79.59% | 42.82% | -36.77% | -10.00% | -56.05% | -22.43% | 0.10222 | 49.66% |
| full/all_cash/strict/cost_25bps | btc_buy_and_hold | 104.85% | 104.34% | 42.82% | -61.52% | -16.02% | -52.97% | -22.43% | -0.00662027 | 47.47% |
| full/all_cash/strict/main_cost | vol_matched_btc_cash | 不可用 | 42.46% | 43.02% | 0.56% | 0.16% | -24.77% | -22.41% | 0.00735271 | 19.03% |
| full/all_cash/strict/main_cost | vol_matched_btc_eth_70_30_cash | 不可用 | 34.96% | 43.02% | 8.06% | 2.40% | -25.41% | -22.41% | 0.111696 | 19.03% |
| full/all_cash/strict/main_cost | static_initial_weights | 不可用 | 0.00% | 43.02% | 43.02% | 14.04% | 0.00% | -22.41% | 不可用 | 0.00% |
| full/all_cash/strict/main_cost | exposure_matched_btc_cash | 不可用 | 40.39% | 43.02% | 2.63% | 0.77% | -23.71% | -22.41% | 0.00773347 | 18.14% |
| full/all_cash/strict/main_cost | btc_eth_70_30 | 79.90% | 79.69% | 43.02% | -36.67% | -9.97% | -56.06% | -22.41% | 0.104572 | 49.67% |
| full/all_cash/strict/main_cost | btc_buy_and_hold | 104.85% | 104.55% | 43.02% | -61.53% | -16.01% | -52.97% | -22.41% | -0.00469269 | 47.47% |
| full/all_cash/strict/weekly_sensitivity | vol_matched_btc_cash | 不可用 | 31.39% | 15.57% | -15.83% | -5.09% | -18.98% | -22.52% | -0.331217 | 14.22% |
| full/all_cash/strict/weekly_sensitivity | vol_matched_btc_eth_70_30_cash | 不可用 | 26.20% | 15.57% | -10.64% | -3.46% | -19.49% | -22.52% | -0.22718 | 14.23% |
| full/all_cash/strict/weekly_sensitivity | static_initial_weights | 不可用 | 0.00% | 15.57% | 15.57% | 5.45% | 0.00% | -22.52% | 不可用 | 0.00% |
| full/all_cash/strict/weekly_sensitivity | exposure_matched_btc_cash | 不可用 | 30.57% | 15.57% | -15.00% | -4.83% | -18.53% | -22.52% | -0.331063 | 13.86% |
| full/all_cash/strict/weekly_sensitivity | btc_eth_70_30 | 79.90% | 79.69% | 15.57% | -64.12% | -18.55% | -56.06% | -22.52% | -0.236043 | 49.67% |
| full/all_cash/strict/weekly_sensitivity | btc_buy_and_hold | 104.85% | 104.55% | 15.57% | -88.98% | -24.59% | -52.97% | -22.52% | -0.345308 | 47.47% |
| full/all_cash/synthetic_30/main_cost | vol_matched_btc_cash | 不可用 | 41.70% | 33.92% | -7.78% | -2.33% | -24.38% | -22.01% | -0.110906 | 18.70% |
| full/all_cash/synthetic_30/main_cost | vol_matched_btc_eth_70_30_cash | 不可用 | 34.36% | 33.92% | -0.45% | -0.14% | -25.01% | -22.01% | -0.00658558 | 18.70% |
| full/all_cash/synthetic_30/main_cost | static_initial_weights | 不可用 | 0.00% | 33.92% | 33.92% | 11.32% | 0.00% | -22.01% | 不可用 | 0.00% |
| full/all_cash/synthetic_30/main_cost | exposure_matched_btc_cash | 不可用 | 36.24% | 33.92% | -2.32% | -0.70% | -21.56% | -22.01% | -0.1099 | 16.34% |
| full/all_cash/synthetic_30/main_cost | btc_eth_70_30 | 79.90% | 79.69% | 33.92% | -45.77% | -12.68% | -56.06% | -22.01% | -0.0138275 | 49.67% |
| full/all_cash/synthetic_30/main_cost | btc_buy_and_hold | 104.85% | 104.55% | 33.92% | -70.63% | -18.73% | -52.97% | -22.01% | -0.123092 | 47.47% |
| full/all_cash/synthetic_50/main_cost | vol_matched_btc_cash | 不可用 | 39.17% | 41.70% | 2.53% | 0.75% | -23.09% | -20.37% | 0.0375424 | 17.61% |
| full/all_cash/synthetic_50/main_cost | vol_matched_btc_eth_70_30_cash | 不可用 | 32.38% | 41.70% | 9.32% | 2.80% | -23.68% | -20.37% | 0.14179 | 17.61% |
| full/all_cash/synthetic_50/main_cost | static_initial_weights | 不可用 | 0.00% | 41.70% | 41.70% | 13.65% | 0.00% | -20.37% | 不可用 | 0.00% |
| full/all_cash/synthetic_50/main_cost | exposure_matched_btc_cash | 不可用 | 33.90% | 41.70% | 7.80% | 2.34% | -20.32% | -20.37% | 0.038517 | 15.32% |
| full/all_cash/synthetic_50/main_cost | btc_eth_70_30 | 79.90% | 79.69% | 41.70% | -37.99% | -10.35% | -56.06% | -20.37% | 0.134155 | 49.67% |
| full/all_cash/synthetic_50/main_cost | btc_buy_and_hold | 104.85% | 104.55% | 41.70% | -62.85% | -16.39% | -52.97% | -20.37% | 0.0248907 | 47.47% |
| full/all_cash/synthetic_70/main_cost | vol_matched_btc_cash | 不可用 | 37.77% | 28.31% | -9.46% | -2.90% | -22.36% | -19.91% | -0.153713 | 17.00% |
| full/all_cash/synthetic_70/main_cost | vol_matched_btc_eth_70_30_cash | 不可用 | 31.28% | 28.31% | -2.97% | -0.93% | -22.94% | -19.91% | -0.0495056 | 17.00% |
| full/all_cash/synthetic_70/main_cost | static_initial_weights | 不可用 | 0.00% | 28.31% | 28.31% | 9.58% | 0.00% | -19.91% | 不可用 | 0.00% |
| full/all_cash/synthetic_70/main_cost | exposure_matched_btc_cash | 不可用 | 31.50% | 28.31% | -3.19% | -0.99% | -19.04% | -19.91% | -0.152552 | 14.27% |
| full/all_cash/synthetic_70/main_cost | btc_eth_70_30 | 79.90% | 79.69% | 28.31% | -51.38% | -14.42% | -56.06% | -19.91% | -0.0573589 | 49.67% |
| full/all_cash/synthetic_70/main_cost | btc_buy_and_hold | 104.85% | 104.55% | 28.31% | -76.24% | -20.46% | -52.97% | -19.91% | -0.166624 | 47.47% |
| full/core_existing/strict/cost_0bps | vol_matched_btc_cash | 不可用 | 44.38% | 35.63% | -8.75% | -2.60% | -25.11% | -22.42% | -0.118125 | 19.43% |
| full/core_existing/strict/cost_0bps | vol_matched_btc_eth_70_30_cash | 不可用 | 36.75% | 35.63% | -1.12% | -0.34% | -25.74% | -22.42% | -0.0155362 | 19.43% |
| full/core_existing/strict/cost_0bps | static_initial_weights | 不可用 | 67.91% | 35.63% | -32.28% | -9.12% | -52.60% | -22.42% | 0.0242689 | 44.68% |
| full/core_existing/strict/cost_0bps | exposure_matched_btc_cash | 不可用 | 41.78% | 35.63% | -6.15% | -1.84% | -23.81% | -22.42% | -0.118125 | 18.33% |
| full/core_existing/strict/cost_0bps | btc_eth_70_30 | 79.90% | 79.90% | 35.63% | -44.27% | -12.22% | -56.07% | -22.42% | -0.00855884 | 49.68% |
| full/core_existing/strict/cost_0bps | btc_buy_and_hold | 104.85% | 104.85% | 35.63% | -69.23% | -18.28% | -52.97% | -22.42% | -0.118125 | 47.48% |
| full/core_existing/strict/cost_10bps | vol_matched_btc_cash | 不可用 | 43.71% | 35.45% | -8.26% | -2.45% | -25.19% | -22.41% | -0.111819 | 19.43% |
| full/core_existing/strict/cost_10bps | vol_matched_btc_eth_70_30_cash | 不可用 | 36.03% | 35.45% | -0.58% | -0.18% | -25.83% | -22.41% | -0.00803997 | 19.43% |
| full/core_existing/strict/cost_10bps | static_initial_weights | 不可用 | 67.83% | 35.45% | -32.37% | -9.15% | -52.62% | -22.41% | 0.0224385 | 44.70% |
| full/core_existing/strict/cost_10bps | exposure_matched_btc_cash | 不可用 | 41.12% | 35.45% | -5.67% | -1.70% | -23.88% | -22.41% | -0.111503 | 18.32% |
| full/core_existing/strict/cost_10bps | btc_eth_70_30 | 79.90% | 79.78% | 35.45% | -44.32% | -12.24% | -56.06% | -22.41% | -0.0104516 | 49.67% |
| full/core_existing/strict/cost_10bps | btc_buy_and_hold | 104.85% | 104.65% | 35.45% | -69.20% | -18.28% | -52.97% | -22.41% | -0.119727 | 47.47% |
| full/core_existing/strict/cost_25bps | vol_matched_btc_cash | 不可用 | 42.73% | 35.19% | -7.54% | -2.25% | -25.32% | -22.44% | -0.102629 | 19.43% |
| full/core_existing/strict/cost_25bps | vol_matched_btc_eth_70_30_cash | 不可用 | 34.98% | 35.19% | 0.21% | 0.06% | -25.98% | -22.44% | 0.00293414 | 19.43% |
| full/core_existing/strict/cost_25bps | static_initial_weights | 不可用 | 67.70% | 35.19% | -32.51% | -9.20% | -52.65% | -22.44% | 0.0194196 | 44.74% |
| full/core_existing/strict/cost_25bps | exposure_matched_btc_cash | 不可用 | 40.18% | 35.19% | -4.99% | -1.50% | -24.01% | -22.44% | -0.10184 | 18.32% |
| full/core_existing/strict/cost_25bps | btc_eth_70_30 | 79.90% | 79.59% | 35.19% | -44.41% | -12.28% | -56.05% | -22.44% | -0.0135641 | 49.66% |
| full/core_existing/strict/cost_25bps | btc_buy_and_hold | 104.85% | 104.34% | 35.19% | -69.16% | -18.29% | -52.97% | -22.44% | -0.122404 | 47.47% |
| full/core_existing/strict/main_cost | vol_matched_btc_cash | 不可用 | 43.38% | 35.35% | -8.02% | -2.39% | -25.23% | -22.42% | -0.108862 | 19.43% |
| full/core_existing/strict/main_cost | vol_matched_btc_eth_70_30_cash | 不可用 | 35.67% | 35.35% | -0.32% | -0.10% | -25.88% | -22.42% | -0.00449163 | 19.43% |
| full/core_existing/strict/main_cost | static_initial_weights | 不可用 | 67.75% | 35.35% | -32.40% | -9.16% | -52.62% | -22.42% | 0.0214102 | 44.70% |
| full/core_existing/strict/main_cost | exposure_matched_btc_cash | 不可用 | 40.80% | 35.35% | -5.45% | -1.63% | -23.92% | -22.42% | -0.108389 | 18.31% |
| full/core_existing/strict/main_cost | btc_eth_70_30 | 79.90% | 79.69% | 35.35% | -44.33% | -12.25% | -56.06% | -22.42% | -0.0114746 | 49.67% |
| full/core_existing/strict/main_cost | btc_buy_and_hold | 104.85% | 104.55% | 35.35% | -69.20% | -18.29% | -52.97% | -22.42% | -0.120739 | 47.47% |
| full/core_existing/strict/weekly_sensitivity | vol_matched_btc_cash | 不可用 | 43.61% | 35.65% | -7.96% | -2.37% | -25.35% | -22.90% | -0.107258 | 19.53% |
| full/core_existing/strict/weekly_sensitivity | vol_matched_btc_eth_70_30_cash | 不可用 | 35.86% | 35.65% | -0.21% | -0.06% | -26.00% | -22.90% | -0.00288059 | 19.53% |
| full/core_existing/strict/weekly_sensitivity | static_initial_weights | 不可用 | 67.75% | 35.65% | -32.10% | -9.07% | -52.62% | -22.90% | 0.0230578 | 44.70% |
| full/core_existing/strict/weekly_sensitivity | exposure_matched_btc_cash | 不可用 | 40.20% | 35.65% | -4.54% | -1.36% | -23.61% | -22.90% | -0.106629 | 18.05% |
| full/core_existing/strict/weekly_sensitivity | btc_eth_70_30 | 79.90% | 79.69% | 35.65% | -44.03% | -12.16% | -56.06% | -22.90% | -0.00982698 | 49.67% |
| full/core_existing/strict/weekly_sensitivity | btc_buy_and_hold | 104.85% | 104.55% | 35.65% | -68.89% | -18.20% | -52.97% | -22.90% | -0.119092 | 47.47% |
| full/core_existing/synthetic_30/main_cost | vol_matched_btc_cash | 不可用 | 44.24% | 40.16% | -4.08% | -1.20% | -25.67% | -21.99% | -0.0532226 | 19.80% |
| full/core_existing/synthetic_30/main_cost | vol_matched_btc_eth_70_30_cash | 不可用 | 36.35% | 40.16% | 3.82% | 1.14% | -26.32% | -21.99% | 0.0511739 | 19.80% |
| full/core_existing/synthetic_30/main_cost | static_initial_weights | 不可用 | 67.75% | 40.16% | -27.59% | -7.72% | -52.62% | -21.99% | 0.0772087 | 44.70% |
| full/core_existing/synthetic_30/main_cost | exposure_matched_btc_cash | 不可用 | 38.11% | 40.16% | 2.06% | 0.61% | -22.54% | -21.99% | -0.0520934 | 17.15% |
| full/core_existing/synthetic_30/main_cost | btc_eth_70_30 | 79.90% | 79.69% | 40.16% | -39.52% | -10.81% | -56.06% | -21.99% | 0.0443239 | 49.67% |
| full/core_existing/synthetic_30/main_cost | btc_buy_and_hold | 104.85% | 104.55% | 40.16% | -64.38% | -16.85% | -52.97% | -21.99% | -0.0649409 | 47.47% |
| full/core_existing/synthetic_50/main_cost | vol_matched_btc_cash | 不可用 | 41.27% | 49.43% | 8.16% | 2.36% | -24.16% | -20.36% | 0.111294 | 18.52% |
| full/core_existing/synthetic_50/main_cost | vol_matched_btc_eth_70_30_cash | 不可用 | 34.04% | 49.43% | 15.40% | 4.53% | -24.79% | -20.36% | 0.215602 | 18.52% |
| full/core_existing/synthetic_50/main_cost | static_initial_weights | 不可用 | 67.75% | 49.43% | -18.32% | -5.03% | -52.62% | -20.36% | 0.24118 | 44.70% |
| full/core_existing/synthetic_50/main_cost | exposure_matched_btc_cash | 不可用 | 35.63% | 49.43% | 13.80% | 4.05% | -21.24% | -20.36% | 0.112335 | 16.07% |
| full/core_existing/synthetic_50/main_cost | btc_eth_70_30 | 79.90% | 79.69% | 49.43% | -30.25% | -8.11% | -56.06% | -20.36% | 0.208295 | 49.67% |
| full/core_existing/synthetic_50/main_cost | btc_buy_and_hold | 104.85% | 104.55% | 49.43% | -55.12% | -14.16% | -52.97% | -20.36% | 0.0990302 | 47.47% |
| full/core_existing/synthetic_70/main_cost | vol_matched_btc_cash | 不可用 | 39.51% | 34.71% | -4.80% | -1.44% | -23.26% | -19.91% | -0.0724122 | 17.75% |
| full/core_existing/synthetic_70/main_cost | vol_matched_btc_eth_70_30_cash | 不可用 | 32.65% | 34.71% | 2.06% | 0.63% | -23.86% | -19.91% | 0.0318446 | 17.75% |
| full/core_existing/synthetic_70/main_cost | static_initial_weights | 不可用 | 67.75% | 34.71% | -33.04% | -9.35% | -52.62% | -19.91% | 0.0571484 | 44.70% |
| full/core_existing/synthetic_70/main_cost | exposure_matched_btc_cash | 不可用 | 32.89% | 34.71% | 1.82% | 0.56% | -19.79% | -19.91% | -0.0711888 | 14.88% |
| full/core_existing/synthetic_70/main_cost | btc_eth_70_30 | 79.90% | 79.69% | 34.71% | -44.97% | -12.44% | -56.06% | -19.91% | 0.0242636 | 49.67% |
| full/core_existing/synthetic_70/main_cost | btc_buy_and_hold | 104.85% | 104.55% | 34.71% | -69.84% | -18.49% | -52.97% | -19.91% | -0.0850011 | 47.47% |

`btc_buy_and_hold` = 100% BTC 买入持有（策略主基准）；`btc_eth_70_30` = 70/30 BTC/ETH 买入持有（次基准）；`static_initial_weights` = 用该实验自己的起始权重买入后不再调整；`vol_matched_btc_cash` = 按策略自身波动率解出的 BTC/现金固定比例，每日再平衡；`exposure_matched_btc_cash` = 按策略实现的平均风险仓持有的 BTC/现金固定比例，每日再平衡。

超额 = 策略累计收益 − 基准累计收益，单位为百分点；同一行的 MaxDD 分成基准与策略两列，避免把基准回撤误读为策略回撤。风险不对等的基准（如 100% BTC）只能说明仓位与风险差异，不能单独证明策略优劣。「基准波动率」让「同风险」一行可自证：它应当等于同行的策略波动率。

### 分年度收益（策略与基准）

| 来源 | 2024 | 2025 | 2026 |
|---|---|---|---|
| 策略（可比实验并集） | 46.40% | -2.03% | -0.29% |
| vol_matched_btc_cash | 41.71% | -0.16% | 1.43% |
| vol_matched_btc_eth_70_30_cash | 33.15% | 0.94% | 1.32% |
| static_initial_weights | 0.00% | 0.00% | 0.00% |
| exposure_matched_btc_cash | 39.55% | -0.09% | 1.42% |
| btc_eth_70_30 | 97.81% | -6.65% | -2.32% |
| btc_buy_and_hold | 119.45% | -5.44% | -1.17% |

分年度数据来自引擎已产出的 `metrics.calendar_returns.yearly`，未在此重算。单一区间的累计差额可能完全由其中某一年造成，因此累计数必须与分年度数一起看。

## 风险与择时诊断

### 择时贡献与地板钉住

| 实验 | 平均风险仓 | 路径累计 | 同均仓累计 | 择时贡献 | overlay 生效占比 | 标签≥防御占比 | 回撤≤地板占比 | 现金4%时CAGR | 现金5%时CAGR |
|---|---|---|---|---|---|---|---|---|---|
| core/all_cash/strict/cost_0bps | 38.21% | 41.23% | 41.34% | -0.11% | 27.34% | 42.31% | 39.40% | 16.90% | 17.59% |
| core/all_cash/strict/cost_10bps | 38.21% | 41.24% | 41.34% | -0.11% | 27.34% | 42.31% | 39.40% | 16.86% | 17.55% |
| core/all_cash/strict/cost_25bps | 38.19% | 41.17% | 41.33% | -0.15% | 27.44% | 42.31% | 39.50% | 16.77% | 17.47% |
| core/all_cash/strict/main_cost | 38.21% | 41.22% | 41.34% | -0.12% | 27.34% | 42.31% | 39.40% | 16.83% | 17.53% |
| core/all_cash/strict/weekly_sensitivity | 29.21% | 14.48% | 31.33% | -16.85% | 28.34% | 41.71% | 35.98% | 8.42% | 9.16% |
| core/all_cash/synthetic_30/main_cost | 33.51% | 38.29% | 36.11% | 2.18% | 31.76% | 45.33% | 41.31% | 14.43% | 15.16% |
| core/all_cash/synthetic_50/main_cost | 32.81% | 37.55% | 35.34% | 2.21% | 32.86% | 49.65% | 45.93% | 13.24% | 13.97% |
| core/all_cash/synthetic_70/main_cost | 32.85% | 36.71% | 35.38% | 1.34% | 31.96% | 47.04% | 43.32% | 13.53% | 14.26% |
| core/core_existing/strict/cost_0bps | 38.33% | 37.16% | 41.49% | -4.32% | 29.45% | 48.84% | 48.24% | 14.24% | 14.91% |
| core/core_existing/strict/cost_10bps | 38.33% | 37.18% | 41.48% | -4.30% | 29.65% | 48.94% | 48.34% | 14.18% | 14.86% |
| core/core_existing/strict/cost_25bps | 38.33% | 37.19% | 41.48% | -4.29% | 29.85% | 49.05% | 48.44% | 14.10% | 14.77% |
| core/core_existing/strict/main_cost | 38.32% | 37.17% | 41.47% | -4.30% | 29.65% | 48.94% | 48.34% | 14.15% | 14.83% |
| core/core_existing/strict/weekly_sensitivity | 38.03% | 40.10% | 41.14% | -1.04% | 29.95% | 49.25% | 48.74% | 14.60% | 15.28% |
| core/core_existing/synthetic_30/main_cost | 35.38% | 44.50% | 38.20% | 6.30% | 33.47% | 51.76% | 50.95% | 15.47% | 16.19% |
| core/core_existing/synthetic_50/main_cost | 35.12% | 46.52% | 37.90% | 8.62% | 32.86% | 51.46% | 50.65% | 15.68% | 16.41% |
| core/core_existing/synthetic_70/main_cost | 35.18% | 48.52% | 37.97% | 10.55% | 31.96% | 48.34% | 47.54% | 16.25% | 16.98% |
| full/all_cash/strict/cost_0bps | 38.21% | 41.23% | 41.34% | -0.11% | 27.34% | 43.82% | 39.40% | 16.90% | 17.59% |
| full/all_cash/strict/cost_10bps | 38.21% | 41.24% | 41.34% | -0.11% | 27.34% | 43.82% | 39.40% | 16.86% | 17.55% |
| full/all_cash/strict/cost_25bps | 38.19% | 41.17% | 41.33% | -0.15% | 27.44% | 43.82% | 39.50% | 16.77% | 17.47% |
| full/all_cash/strict/main_cost | 38.21% | 41.22% | 41.34% | -0.12% | 27.34% | 43.82% | 39.40% | 16.83% | 17.53% |
| full/all_cash/strict/weekly_sensitivity | 29.21% | 14.48% | 31.33% | -16.85% | 28.34% | 43.62% | 35.98% | 8.42% | 9.16% |
| full/all_cash/synthetic_30/main_cost | 34.41% | 37.28% | 37.11% | 0.17% | 31.66% | 45.53% | 41.21% | 14.22% | 14.94% |
| full/all_cash/synthetic_50/main_cost | 32.27% | 39.79% | 34.73% | 5.06% | 30.95% | 43.72% | 38.19% | 16.71% | 17.47% |
| full/all_cash/synthetic_70/main_cost | 30.06% | 32.94% | 32.28% | 0.66% | 30.45% | 45.33% | 40.60% | 12.63% | 13.38% |
| full/core_existing/strict/cost_0bps | 38.60% | 38.11% | 41.78% | -3.67% | 28.04% | 50.15% | 48.04% | 14.56% | 15.24% |
| full/core_existing/strict/cost_10bps | 38.58% | 38.13% | 41.76% | -3.63% | 28.14% | 50.25% | 48.14% | 14.51% | 15.18% |
| full/core_existing/strict/cost_25bps | 38.59% | 38.14% | 41.77% | -3.63% | 28.14% | 50.25% | 48.14% | 14.43% | 15.10% |
| full/core_existing/strict/main_cost | 38.58% | 38.12% | 41.76% | -3.64% | 28.14% | 50.25% | 48.14% | 14.48% | 15.15% |
| full/core_existing/strict/weekly_sensitivity | 38.03% | 40.10% | 41.14% | -1.04% | 29.95% | 50.55% | 48.74% | 14.60% | 15.28% |
| full/core_existing/synthetic_30/main_cost | 36.12% | 46.44% | 39.02% | 7.42% | 31.76% | 48.24% | 46.93% | 16.07% | 16.78% |
| full/core_existing/synthetic_50/main_cost | 33.85% | 49.51% | 36.49% | 13.02% | 30.85% | 43.72% | 38.79% | 18.93% | 19.69% |
| full/core_existing/synthetic_70/main_cost | 31.34% | 40.72% | 33.70% | 7.02% | 30.45% | 45.33% | 40.70% | 14.60% | 15.36% |

「择时贡献」= 按策略实际风险仓路径持有 BTC 腿的累计收益 − 按其平均风险仓常数持有的累计收益，带符号：正数说明持仓时机在均值之上，负数说明把敞口加在了腿下跌的时段。「标签≥防御占比」与「回撤≤地板占比」接近相等即 `REGIME_PINNED_BY_OWN_DRAWDOWN`：regime 标签跟随账面回撤而非市场。现金收益敏感性是诊断口径：回放把稳定腿记为零收益，这里按假设年化收益重记策略自身路径，不改变任何引擎记账，也不与零收益基准直接比较。

### 风险引擎诊断

| 实验 | 模式 | 平均估计组合波动率 | 最大估计组合波动率 | 约束生效分布 | 应急状态分布 |
|---|---|---|---|---|---|
| core/all_cash/strict/cost_0bps | volatility_budget | 21.57% | 35.11% | strategic_target:469 / emergency_overlay:272 / volatility_budget:254 | BREACH:272 / CAUTION:72 / EMERGENCY:48 / NORMAL:603 |
| core/all_cash/strict/cost_10bps | volatility_budget | 21.57% | 35.11% | strategic_target:469 / emergency_overlay:272 / volatility_budget:254 | BREACH:272 / CAUTION:72 / EMERGENCY:48 / NORMAL:603 |
| core/all_cash/strict/cost_25bps | volatility_budget | 21.57% | 35.11% | strategic_target:468 / emergency_overlay:273 / volatility_budget:254 | BREACH:273 / CAUTION:73 / EMERGENCY:47 / NORMAL:602 |
| core/all_cash/strict/main_cost | volatility_budget | 21.57% | 35.11% | strategic_target:469 / emergency_overlay:272 / volatility_budget:254 | BREACH:272 / CAUTION:72 / EMERGENCY:48 / NORMAL:603 |
| core/all_cash/strict/weekly_sensitivity | volatility_budget | 21.59% | 35.11% | strategic_target:455 / emergency_overlay:282 / volatility_budget:258 | BREACH:282 / CAUTION:43 / EMERGENCY:33 / NORMAL:637 |
| core/all_cash/synthetic_30/main_cost | volatility_budget | 25.57% | 56.05% | volatility_budget:455 / emergency_overlay:316 / strategic_target:224 | BREACH:316 / CAUTION:50 / EMERGENCY:45 / NORMAL:584 |
| core/all_cash/synthetic_50/main_cost | volatility_budget | 26.18% | 56.05% | volatility_budget:474 / emergency_overlay:327 / strategic_target:194 | BREACH:327 / CAUTION:76 / EMERGENCY:54 / NORMAL:538 |
| core/all_cash/synthetic_70/main_cost | volatility_budget | 26.72% | 56.05% | volatility_budget:499 / emergency_overlay:318 / strategic_target:178 | BREACH:318 / CAUTION:54 / EMERGENCY:59 / NORMAL:564 |
| core/core_existing/strict/cost_0bps | volatility_budget | 22.24% | 40.15% | strategic_target:423 / emergency_overlay:293 / volatility_budget:279 | BREACH:293 / CAUTION:92 / EMERGENCY:95 / NORMAL:515 |
| core/core_existing/strict/cost_10bps | volatility_budget | 22.24% | 40.15% | strategic_target:421 / emergency_overlay:295 / volatility_budget:279 | BREACH:295 / CAUTION:92 / EMERGENCY:94 / NORMAL:514 |
| core/core_existing/strict/cost_25bps | volatility_budget | 22.24% | 40.16% | strategic_target:419 / emergency_overlay:297 / volatility_budget:279 | BREACH:297 / CAUTION:93 / EMERGENCY:92 / NORMAL:513 |
| core/core_existing/strict/main_cost | volatility_budget | 22.24% | 40.15% | strategic_target:421 / emergency_overlay:295 / volatility_budget:279 | BREACH:295 / CAUTION:92 / EMERGENCY:94 / NORMAL:514 |
| core/core_existing/strict/weekly_sensitivity | volatility_budget | 23.01% | 40.11% | volatility_budget:365 / strategic_target:332 / emergency_overlay:298 | BREACH:298 / CAUTION:136 / EMERGENCY:51 / NORMAL:510 |
| core/core_existing/synthetic_30/main_cost | volatility_budget | 26.04% | 56.05% | volatility_budget:495 / emergency_overlay:333 / strategic_target:167 | BREACH:333 / CAUTION:118 / EMERGENCY:56 / NORMAL:488 |
| core/core_existing/synthetic_50/main_cost | volatility_budget | 26.60% | 56.05% | volatility_budget:521 / emergency_overlay:327 / strategic_target:147 | BREACH:327 / CAUTION:110 / EMERGENCY:67 / NORMAL:491 |
| core/core_existing/synthetic_70/main_cost | volatility_budget | 27.07% | 56.05% | volatility_budget:534 / emergency_overlay:318 / strategic_target:143 | BREACH:318 / CAUTION:90 / EMERGENCY:65 / NORMAL:522 |
| full/all_cash/strict/cost_0bps | volatility_budget | 21.57% | 35.11% | strategic_target:469 / emergency_overlay:272 / volatility_budget:254 | BREACH:272 / CAUTION:72 / EMERGENCY:48 / NORMAL:603 |
| full/all_cash/strict/cost_10bps | volatility_budget | 21.57% | 35.11% | strategic_target:469 / emergency_overlay:272 / volatility_budget:254 | BREACH:272 / CAUTION:72 / EMERGENCY:48 / NORMAL:603 |
| full/all_cash/strict/cost_25bps | volatility_budget | 21.57% | 35.11% | strategic_target:468 / emergency_overlay:273 / volatility_budget:254 | BREACH:273 / CAUTION:73 / EMERGENCY:47 / NORMAL:602 |
| full/all_cash/strict/main_cost | volatility_budget | 21.57% | 35.11% | strategic_target:469 / emergency_overlay:272 / volatility_budget:254 | BREACH:272 / CAUTION:72 / EMERGENCY:48 / NORMAL:603 |
| full/all_cash/strict/weekly_sensitivity | volatility_budget | 21.59% | 35.11% | strategic_target:455 / emergency_overlay:282 / volatility_budget:258 | BREACH:282 / CAUTION:43 / EMERGENCY:33 / NORMAL:637 |
| full/all_cash/synthetic_30/main_cost | volatility_budget | 25.63% | 56.05% | volatility_budget:454 / emergency_overlay:315 / strategic_target:226 | BREACH:315 / CAUTION:50 / EMERGENCY:45 / NORMAL:585 |
| full/all_cash/synthetic_50/main_cost | volatility_budget | 29.22% | 54.61% | volatility_budget:578 / emergency_overlay:308 / strategic_target:109 | BREACH:308 / CAUTION:58 / EMERGENCY:14 / NORMAL:615 |
| full/all_cash/synthetic_70/main_cost | volatility_budget | 32.24% | 54.65% | volatility_budget:643 / emergency_overlay:303 / strategic_target:49 | BREACH:303 / CAUTION:70 / EMERGENCY:31 / NORMAL:591 |
| full/core_existing/strict/cost_0bps | volatility_budget | 22.24% | 40.15% | strategic_target:437 / emergency_overlay:279 / volatility_budget:279 | BREACH:279 / CAUTION:90 / EMERGENCY:109 / NORMAL:517 |
| full/core_existing/strict/cost_10bps | volatility_budget | 22.24% | 40.15% | strategic_target:436 / emergency_overlay:280 / volatility_budget:279 | BREACH:280 / CAUTION:90 / EMERGENCY:109 / NORMAL:516 |
| full/core_existing/strict/cost_25bps | volatility_budget | 22.24% | 40.16% | strategic_target:436 / emergency_overlay:280 / volatility_budget:279 | BREACH:280 / CAUTION:90 / EMERGENCY:109 / NORMAL:516 |
| full/core_existing/strict/main_cost | volatility_budget | 22.24% | 40.15% | strategic_target:436 / emergency_overlay:280 / volatility_budget:279 | BREACH:280 / CAUTION:90 / EMERGENCY:109 / NORMAL:516 |
| full/core_existing/strict/weekly_sensitivity | volatility_budget | 23.01% | 40.11% | volatility_budget:365 / strategic_target:332 / emergency_overlay:298 | BREACH:298 / CAUTION:136 / EMERGENCY:51 / NORMAL:510 |
| full/core_existing/synthetic_30/main_cost | volatility_budget | 25.89% | 56.05% | volatility_budget:493 / emergency_overlay:316 / strategic_target:186 | BREACH:316 / CAUTION:93 / EMERGENCY:58 / NORMAL:528 |
| full/core_existing/synthetic_50/main_cost | volatility_budget | 29.35% | 54.61% | volatility_budget:577 / emergency_overlay:307 / strategic_target:111 | BREACH:307 / CAUTION:64 / EMERGENCY:15 / NORMAL:609 |
| full/core_existing/synthetic_70/main_cost | volatility_budget | 32.37% | 54.65% | volatility_budget:644 / emergency_overlay:303 / strategic_target:48 | BREACH:303 / CAUTION:71 / EMERGENCY:31 / NORMAL:590 |

「约束生效分布」回答本窗口内谁在主导仓位（volatility_budget=波动率预算、emergency_overlay=应急刹车、strategic_target=战略目标自身）；「应急状态分布」是分段刹车 NORMAL/CAUTION/EMERGENCY/BREACH 的评审计数（legacy 模式下为 LEGACY_LADDER 连续梯子）。估计组合波动率来自评审时点的点时协方差，与事后实现波动率互相独立。

## 仓位、换手与存续

| 实验 | 状态 | 成交笔数 | 首笔 | 末笔 | 末笔距期末(天) | 期末现金占比 | 平均现金占比 | 总换手 | 总成本 USD |
|---|---|---|---|---|---|---|---|---|---|
| core/all_cash/strict/cost_0bps | TRADING_STALLED | 163 | 2024-01-04 | 2026-07-01 | 85 | 75.63% | 61.81% | 1.02793 | 0 |
| core/all_cash/strict/cost_10bps | TRADING_STALLED | 164 | 2024-01-04 | 2026-07-01 | 85 | 75.63% | 61.81% | 1.02811 | 124.545 |
| core/all_cash/strict/cost_25bps | TRADING_STALLED | 162 | 2024-01-04 | 2026-07-01 | 85 | 75.71% | 61.82% | 1.02921 | 311.382 |
| core/all_cash/strict/main_cost | TRADING_STALLED | 164 | 2024-01-04 | 2026-07-01 | 85 | 75.63% | 61.81% | 1.02787 | 186.712 |
| core/all_cash/strict/weekly_sensitivity | TRADING_STALLED | 54 | 2024-01-08 | 2026-07-01 | 85 | 75.84% | 70.80% | 0.534163 | 87.7835 |
| core/all_cash/synthetic_30/main_cost | TRADING_STALLED | 404 | 2024-01-04 | 2026-07-01 | 85 | 85.06% | 66.51% | 2.78174 | 532.437 |
| core/all_cash/synthetic_50/main_cost | OK | 396 | 2024-01-04 | 2026-08-17 | 38 | 85.42% | 67.20% | 2.77752 | 520.666 |
| core/all_cash/synthetic_70/main_cost | OK | 386 | 2024-01-04 | 2026-08-17 | 38 | 85.75% | 67.17% | 2.7143 | 510.273 |
| core/core_existing/strict/cost_0bps | TRADING_STALLED | 203 | 2024-01-01 | 2026-07-01 | 85 | 75.93% | 61.68% | 1.41012 | 0 |
| core/core_existing/strict/cost_10bps | TRADING_STALLED | 200 | 2024-01-01 | 2026-07-01 | 85 | 75.97% | 61.69% | 1.41078 | 174.738 |
| core/core_existing/strict/cost_25bps | TRADING_STALLED | 203 | 2024-01-01 | 2026-07-01 | 85 | 75.99% | 61.69% | 1.41131 | 436.575 |
| core/core_existing/strict/main_cost | TRADING_STALLED | 199 | 2024-01-01 | 2026-07-01 | 85 | 75.97% | 61.69% | 1.41071 | 262.021 |
| core/core_existing/strict/weekly_sensitivity | TRADING_STALLED | 88 | 2024-01-01 | 2026-07-01 | 85 | 76.25% | 61.99% | 1.08368 | 199.689 |
| core/core_existing/synthetic_30/main_cost | TRADING_STALLED | 469 | 2024-01-01 | 2026-07-01 | 85 | 85.06% | 64.64% | 2.65949 | 523.449 |
| core/core_existing/synthetic_50/main_cost | OK | 452 | 2024-01-01 | 2026-08-17 | 38 | 85.42% | 64.90% | 2.764 | 543.648 |
| core/core_existing/synthetic_70/main_cost | OK | 454 | 2024-01-01 | 2026-08-17 | 38 | 85.75% | 64.84% | 2.79506 | 555.964 |
| full/all_cash/strict/cost_0bps | TRADING_STALLED | 162 | 2024-01-04 | 2026-07-01 | 85 | 75.63% | 61.81% | 1.02793 | 0 |
| full/all_cash/strict/cost_10bps | TRADING_STALLED | 163 | 2024-01-04 | 2026-07-01 | 85 | 75.63% | 61.81% | 1.02811 | 124.545 |
| full/all_cash/strict/cost_25bps | TRADING_STALLED | 161 | 2024-01-04 | 2026-07-01 | 85 | 75.71% | 61.82% | 1.02921 | 311.382 |
| full/all_cash/strict/main_cost | TRADING_STALLED | 162 | 2024-01-04 | 2026-07-01 | 85 | 75.63% | 61.81% | 1.02787 | 186.712 |
| full/all_cash/strict/weekly_sensitivity | TRADING_STALLED | 55 | 2024-01-08 | 2026-07-01 | 85 | 75.84% | 70.80% | 0.534163 | 87.7835 |
| full/all_cash/synthetic_30/main_cost | TRADING_STALLED | 402 | 2024-01-04 | 2026-07-01 | 85 | 83.33% | 65.61% | 2.91206 | 560.051 |
| full/all_cash/synthetic_50/main_cost | OK | 461 | 2024-01-04 | 2026-08-17 | 38 | 85.31% | 67.75% | 3.63839 | 708.74 |
| full/all_cash/synthetic_70/main_cost | OK | 477 | 2024-01-07 | 2026-08-20 | 35 | 85.89% | 69.95% | 3.71 | 692.227 |
| full/core_existing/strict/cost_0bps | TRADING_STALLED | 200 | 2024-01-01 | 2026-07-01 | 85 | 75.63% | 61.42% | 1.37901 | 0 |
| full/core_existing/strict/cost_10bps | TRADING_STALLED | 198 | 2024-01-01 | 2026-07-01 | 85 | 75.71% | 61.43% | 1.3801 | 171.438 |
| full/core_existing/strict/cost_25bps | TRADING_STALLED | 200 | 2024-01-01 | 2026-07-01 | 85 | 75.71% | 61.43% | 1.3805 | 428.3 |
| full/core_existing/strict/main_cost | TRADING_STALLED | 196 | 2024-01-01 | 2026-07-01 | 85 | 75.71% | 61.44% | 1.38002 | 257.072 |
| full/core_existing/strict/weekly_sensitivity | TRADING_STALLED | 86 | 2024-01-01 | 2026-07-01 | 85 | 76.25% | 61.99% | 1.08368 | 199.689 |
| full/core_existing/synthetic_30/main_cost | TRADING_STALLED | 464 | 2024-01-01 | 2026-07-01 | 85 | 83.33% | 63.90% | 3.0349 | 608.192 |
| full/core_existing/synthetic_50/main_cost | OK | 550 | 2024-01-01 | 2026-08-17 | 38 | 85.31% | 66.17% | 4.08294 | 818.18 |
| full/core_existing/synthetic_70/main_cost | OK | 565 | 2024-01-01 | 2026-08-20 | 35 | 85.89% | 68.67% | 4.07285 | 779.199 |

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
- `ERROR` `COVERAGE_BELOW_INVESTABLE` full — median coverage 0.5100 never reaches minimum_investable_coverage 0.60 (max 0.8750); the score is a reduced function of the available factors, not the full model
- `ERROR` `SORTING_UNDERPOWERED` core/180d — only 10 independent blocks behind 1632 overlapping samples; spearman -0.2516101951106908 is not distinguishable from noise at this power
- `WARNING` `TRADING_STALLED` core_all_cash_strict_cost_0bps — last trade 2026-07-01 is 85 days before the window ends 2026-09-24; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` core_all_cash_strict_cost_10bps — last trade 2026-07-01 is 85 days before the window ends 2026-09-24; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` core_all_cash_strict_cost_25bps — last trade 2026-07-01 is 85 days before the window ends 2026-09-24; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` core_all_cash_strict_main_cost — last trade 2026-07-01 is 85 days before the window ends 2026-09-24; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` core_all_cash_strict_weekly_sensitivity — last trade 2026-07-01 is 85 days before the window ends 2026-09-24; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` core_all_cash_synthetic_30_main_cost — last trade 2026-07-01 is 85 days before the window ends 2026-09-24; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` core_core_existing_strict_cost_0bps — last trade 2026-07-01 is 85 days before the window ends 2026-09-24; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` core_core_existing_strict_cost_10bps — last trade 2026-07-01 is 85 days before the window ends 2026-09-24; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` core_core_existing_strict_cost_25bps — last trade 2026-07-01 is 85 days before the window ends 2026-09-24; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` core_core_existing_strict_main_cost — last trade 2026-07-01 is 85 days before the window ends 2026-09-24; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` core_core_existing_strict_weekly_sensitivity — last trade 2026-07-01 is 85 days before the window ends 2026-09-24; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` core_core_existing_synthetic_30_main_cost — last trade 2026-07-01 is 85 days before the window ends 2026-09-24; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` full_all_cash_strict_cost_0bps — last trade 2026-07-01 is 85 days before the window ends 2026-09-24; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` full_all_cash_strict_cost_10bps — last trade 2026-07-01 is 85 days before the window ends 2026-09-24; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` full_all_cash_strict_cost_25bps — last trade 2026-07-01 is 85 days before the window ends 2026-09-24; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` full_all_cash_strict_main_cost — last trade 2026-07-01 is 85 days before the window ends 2026-09-24; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` full_all_cash_strict_weekly_sensitivity — last trade 2026-07-01 is 85 days before the window ends 2026-09-24; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` full_all_cash_synthetic_30_main_cost — last trade 2026-07-01 is 85 days before the window ends 2026-09-24; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` full_core_existing_strict_cost_0bps — last trade 2026-07-01 is 85 days before the window ends 2026-09-24; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` full_core_existing_strict_cost_10bps — last trade 2026-07-01 is 85 days before the window ends 2026-09-24; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` full_core_existing_strict_cost_25bps — last trade 2026-07-01 is 85 days before the window ends 2026-09-24; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` full_core_existing_strict_main_cost — last trade 2026-07-01 is 85 days before the window ends 2026-09-24; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` full_core_existing_strict_weekly_sensitivity — last trade 2026-07-01 is 85 days before the window ends 2026-09-24; the tail of the path is unmanaged
- `WARNING` `TRADING_STALLED` full_core_existing_synthetic_30_main_cost — last trade 2026-07-01 is 85 days before the window ends 2026-09-24; the tail of the path is unmanaged
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
- `WARNING` `STRESS_UNDERSTATES_REALIZED` BTC — BTC realized -0.5306 against a configured stress of -0.2000, a factor of 2.65; the budget was calibrated to the configured value
- `WARNING` `REGIME_BELOW_REQUIRED_STABLE_TARGET` CAPITAL_PRESERVATION/CORE_ANCHOR — regime CAPITAL_PRESERVATION targets 0.5000 in stables but needs 0.7388 to hold the budget under REALIZED_HISTORY
- `WARNING` `REGIME_BELOW_REQUIRED_STABLE_TARGET` CAPITAL_PRESERVATION/WORST_CONFIGURED_ASSET — regime CAPITAL_PRESERVATION targets 0.5000 in stables but needs 0.8216 to hold the budget under REALIZED_HISTORY
- `WARNING` `REGIME_BELOW_REQUIRED_STABLE_TARGET` DEFENSIVE/CORE_ANCHOR — regime DEFENSIVE targets 0.3000 in stables but needs 0.7388 to hold the budget under REALIZED_HISTORY
- `WARNING` `REGIME_BELOW_REQUIRED_STABLE_TARGET` DEFENSIVE/WORST_CONFIGURED_ASSET — regime DEFENSIVE targets 0.3000 in stables but needs 0.8216 to hold the budget under REALIZED_HISTORY
- `WARNING` `REGIME_BELOW_REQUIRED_STABLE_TARGET` NORMAL/CORE_ANCHOR — regime NORMAL targets 0.1500 in stables but needs 0.7388 to hold the budget under REALIZED_HISTORY
- `WARNING` `REGIME_BELOW_REQUIRED_STABLE_TARGET` NORMAL/WORST_CONFIGURED_ASSET — regime NORMAL targets 0.1500 in stables but needs 0.8216 to hold the budget under REALIZED_HISTORY
- `WARNING` `REGIME_PINNED_BY_OWN_DRAWDOWN` core/core_existing/synthetic_30/main_cost — regime label was DEFENSIVE or worse on 51.8% of reviews while the portfolio's own drawdown sat at or below its floor on 51.0%; market domains pushed defense beyond the floor on only 8 of 995 reviews, so the label tracked the book's own P&L, not the market
- `WARNING` `REGIME_PINNED_BY_OWN_DRAWDOWN` core/core_existing/synthetic_50/main_cost — regime label was DEFENSIVE or worse on 51.5% of reviews while the portfolio's own drawdown sat at or below its floor on 50.7%; market domains pushed defense beyond the floor on only 8 of 995 reviews, so the label tracked the book's own P&L, not the market

## 限制

manifest 未报告阻断项——但阻断项只覆盖 OHLCV 完整性，不能据此认为信号层、评分覆盖或决策样本足以支撑结论。

严格模式保留缺失因子和硬门控；`SYNTHETIC_ASSUMPTIONS` 结果仅说明机制对 30/50/70 分假设的敏感度。未执行建议、合成评分和 USDT 计价近似均不计入真实账户业绩。
