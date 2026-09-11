# 使用指南

首次使用时，可先查阅[中文术语表](GLOSSARY.zh-CN.md)。

本指南只说明 `crypto-portfolio-manager` 的实际使用方式：如何提供组合输入、选择复盘类型、理解报告、处理 NAV / 外部现金流、控制数据获取方式，以及遇到数据问题时如何快速定位。

相关文档：

- 策略与决策原则：[投资策略](../references/investment-strategy.md)
- 架构与内部实现：[工作原理](HOW_IT_WORKS.md)
- Provider / TLS / 开发调试：[开发与 Provider 排查](DEVELOPMENT_DEBUGGING.md)
- 来源方法论：[数据源策略](../references/data-sources.md)
- Provider、认证、缓存和限制：[数据 Provider 策略](../references/data-providers.md)

仓库 Skill 的唯一 source of truth 是 `.agents/skills/crypto-portfolio-manager/SKILL.md`。

模型和推理设置由 Codex、ZCode、Claude Code 等 Agent Skills 宿主控制。Skill 使用用户当前会话选择的模型和推理设置，不进行仓库级模型切换。

## 1. Skill 能做什么

本 Skill 面向约 3–6 个月主动配置周期、仅现货、保守均衡的加密货币组合研究。它会结合当前证据和 Python 确定性计算完成：

- 组合记账、现金流调整后的 NAV、回撤和 BTC 基准；
- 市场指标、评分、regime、目标配置和再平衡；
- Position P&L、成本数据覆盖率、风险检查和分批执行计划；
- 安全与监管事件扫描；
- 用户显式提供的重要治理、tokenomics、法律或协议上下文分析；
- `NO_TRADE`、`HOLD`、`WAIT`、`REDUCE`、`EXIT` 等建议。

它不会自动下单，不会请求交易或提现权限，也不使用期货、永续、杠杆或保证金。不要提供私钥、助记词或交易凭证。

## 2. 快速开始

### 2.1 使用 Binance 截图

1. 将钱包总览显示货币设置为 USD。
2. 截图尽量包含资产、数量、价格 / 成本、当前价值和浮动盈亏。
3. 上传截图并指定复盘类型。
4. 如果截图只有部分资产，应明确说明；Skill 也会根据 reported total 检查可见价值覆盖率。

示例：

```text
$crypto-portfolio-manager

这是我的最新 Binance 仓位截图。
读取历史仓位和上一轮决策，获取当前市场数据，做一次 SNAPSHOT_REVIEW。
只有风险收益比足够明显时才建议交易，否则给出 NO_TRADE。
请包含 Position P&L、成本数据覆盖率、confidence、当前/目标权重偏差和调仓依据。
```

Binance 行布局约定：

- `资产价格 / 成本价`：上方为当前价，下方为平均成本；
- `数量`：上方为数量，下方为当前价值；
- `--` 表示未知，不能转换为 0；
- 正数量配合 `$0.00` 当前价可能只是显示精度不足，Python 可使用 `value / quantity` 推导近似价格并记录说明。

Position P&L 的派生值由 Python 计算，不由 Agent 手工重算。

### 2.2 使用结构化 JSON

```json
{
  "timestamp": "2026-09-01T12:00:00Z",
  "positions": [
    {"symbol": "BTC", "value_usd": 10000},
    {"symbol": "ETH", "value_usd": 6000},
    {"symbol": "USDT", "value_usd": 3000}
  ]
}
```

只验证和规范化 snapshot：

```bash
python3 scripts/portfolio_snapshot.py portfolio.json
```

这个命令不会获取实时市场数据，也不会生成完整投资决策。Policy override 必须位于 snapshot 顶层 `config`；无效值、重复资产、重叠资产组或冲突 `asset_type` 会被拒绝。

### 2.3 Dry Run

如果只想测试而不写入历史：

```text
做一次 dry run，只输出分析和建议，不保存 snapshot、decision、
execution plan 或其他运行时状态。
```

验证、数据获取、风险检查和报告仍会执行。

## 3. 复盘类型

### SNAPSHOT_REVIEW

适合当前仓位检查和日常更新。它会读取历史 snapshot 和上一轮 decision，刷新当前证据，重新检查 regime、risk gate、rebalance threshold，并输出当前 Position P&L。

### FULL_REVIEW

用于完整周期复盘，通常至少每 14 天建议进行一次。除 Snapshot Review 外，还会重点比较：

- NAV、drawdown；
- 100% BTC 主基准和 70/30 BTC/ETH 次基准；
- 前后两轮 Position P&L；
- 评分、regime、目标配置和实际配置变化；
- 历史 thesis 与当前证据是否发生实质改变。

### EVENT_REVIEW

用于可能改变投资 thesis 的重要事件。

自动事件扫描主要覆盖安全和监管来源；自动治理提案扫描已经移除。重要治理、tokenomics、法律、协议升级等信息应由用户明确提供，并作为 `ManualAssetContext` / `MANUAL_USER_INPUT` 处理。

例如：

```text
对 AAVE 做一次 EVENT_REVIEW。
人工补充：Aave 当前正在讨论 <proposal / parameter / tokenomics event>。
请将它作为 MANUAL_USER_INPUT，不要当作自动扫描结果。
```

没有人工治理上下文不会自动产生 confidence 惩罚。

## 4. 输入、Position P&L 与现金流

### 4.1 截图完整性与成本数据

Skill 会检查：

```text
quantity × current price   ≈ current value
quantity × average cost    ≈ cost basis
current value - cost basis ≈ floating P&L
```

小的显示舍入误差会保留为 warning；material mismatch 不应直接持久化。如果可见资产价值明显低于 reported total，报告必须说明截图可能不完整。

Position P&L 是当前剩余仓位的未实现表现，不代表已实现收益、含手续费收益、税务成本批次或组合生命周期收益。

`--` 或无法确认的 `$0.00` 成本保持未知。成本数据覆盖率表示当前组合价值中有多少比例具有可用成本数据。

### 4.2 外部现金流

两个 snapshot 之间如果存在充值、提现、跨账户转入 / 转出，应明确告诉 Skill：

```text
与上一轮相比，这次额外充值了 5,000 USDT。
```

如果用户没有披露外部现金流，系统默认：

```text
ASSUMED_NONE / 0 / NONE
```

余额变化按市场表现处理，NAV 保持 `FINAL`。

如果用户明确表示存在现金流，但金额或方向无法确认，则使用：

```text
UNRESOLVED / PROVISIONAL
```

此时 NAV、benchmark 和 drawdown 保持暂定，直到该历史现金流被显式解决。

## 5. 如何理解报告

每个组合结论和风险资产 Action 应遵循：

```text
证据 → 事实含义 → 组合约束 → 风险门 → 调仓阈值 → Action
```

报告应尽量给出 Evidence ID、source、observed time、数据状态、当前 / 目标权重、偏差、实际触发阈值、Action 原因以及改变建议的具体条件。

最终报告只能解释 finalized packet，不能重新计算或覆盖 Python 已确定的 score、weight、amount、zone、Action 或 risk flag。

### 5.1 数据状态

```text
SUCCESS | FAILED | STALE | CONFLICT | NOT_APPLICABLE | SKIPPED
```

- `SUCCESS`：当前数据有效；
- `FAILED`：指标适用且预期应获取，但没有取得有效值；
- `STALE`：有旧值，但已超过 freshness 要求；
- `CONFLICT`：来源存在实质冲突；
- `NOT_APPLICABLE`：该指标对该资产不适用；
- `SKIPPED`：可选 / premium Provider 未配置、无 entitlement 或没有 eligible provider。

缺失和失败不会自动变成中性值，也不会因为数据消失而提高评分。

### 5.2 Confidence

Decision Confidence 不等于简单的 API 成功率。它会考虑：

- policy-weighted factor coverage；
- critical evidence 是否完整；
- freshness 和 source conflict；
- 历史数据是否足够；
- NAV 是 `FINAL` 还是 `PROVISIONAL`；
- 事件扫描覆盖是否完整。

因此即使大多数 API 成功，只要 current price、recent trend、安全事件或组合价值等关键证据缺失，confidence 仍可能是 `LOW`。

### 5.3 事件状态

`MATERIAL_EVENT_FOUND` 只表示本轮扫描来源中发现重要公告、proposal 或事件，不等于 exploit 已发生、proposal 已通过或已经执行，也不自动代表利多 / 利空。

完整扫描未发现已知重大事件时可能使用：

```text
NO_KNOWN_MATERIAL_EVENT_IN_SCANNED_SOURCES
```

它不代表绝对安全。

来源覆盖不足时使用：

```text
INSUFFICIENT_SOURCE_COVERAGE
```

来源不可达不能解释成“没有风险”。

### 5.4 NO_TRADE / WAIT

`NO_TRADE` 是合法决策，不是系统失败。报告应说明 primary reason、secondary reasons、未满足的 gate / threshold，以及什么变化可能解除当前限制。

## 6. NAV、历史与 CashFlowResolution

系统使用现金流调整后的 unitized NAV。存款和取款改变 units，不应被计算为市场盈利或亏损。

现金流状态：

- `ASSUMED_NONE`：用户未披露现金流，默认无外部现金流；
- `CONFIRMED_NONE`：用户明确确认没有外部现金流；
- `CONFIRMED_AMOUNT`：用户确认了存款或取款金额；
- `UNRESOLVED`：确认存在现金流，但金额或方向尚未解决；
- `BASELINE_RESET`：从指定 snapshot 建立新的验证记账基线。

resolution 是 append-only，不重写旧 snapshot。

查看 unresolved：

```bash
python3 scripts/cash_flow_resolutions.py list-unresolved
python3 scripts/cash_flow_resolutions.py validate
```

确认无外部现金流：

```bash
python3 scripts/cash_flow_resolutions.py resolve-none \
  --snapshot-id <id> --rationale "User confirmed no external cash flow"
```

确认存款 / 取款：

```bash
python3 scripts/cash_flow_resolutions.py resolve-amount \
  --snapshot-id <id> --type DEPOSIT --amount 1000 \
  --rationale "User confirmed deposit"
```

建立新基线：

```bash
python3 scripts/cash_flow_resolutions.py baseline-reset \
  --snapshot-id <id> --rationale "Start verified baseline here"
```

只要相关历史中仍有未解决的 `UNRESOLVED` snapshot，NAV、benchmark 与 drawdown 可能继续保持 `PROVISIONAL`。

## 7. 数据获取

数据流概念上是：

```text
fresh MetricObservation
→ provider cache / 内容寻址历史数据
→ structured provider
→ 必要时产生有界的外部解析任务
```

重要边界：

- 数值和时间序列 metric 以结构化 Provider 为准，不能用普通 Web 搜索随意替代；
- chain liveness 只能使用结构化 block / slot / RPC 类来源；
- 安全和监管事件使用固定 source catalog；
- Web / Agent 外部检索只处理明确返回的、有边界的 unresolved qualitative / event work；
- 已被 fresh observation、cache 或 structured provider 成功解析的数据不重复抓取。

### 7.1 Fetch Mode

- `AUTO`：优先 fresh observation 和 cache，再刷新缺失 / 过期数据；
- `CACHE_ONLY`：不发网络请求，缺失数据保持可见；
- `REFRESH`：刷新 mutable 当前数据，completed historical artifact 仍可复用。

```bash
export CRYPTO_PORTFOLIO_FETCH_MODE=CACHE_ONLY
# 或
export CRYPTO_PORTFOLIO_FETCH_MODE=REFRESH
```

运行级显式选择优先于环境变量。

### 7.2 Chain Liveness

BTC、ETH、BNB、SOL 等 chain-native 资产使用结构化运行状态证据。AAVE、LINK 等 token / protocol 资产不作为独立链检查。

典型结果：

- `HEALTHY`：canonical progress 新鲜；
- `DEGRADED`：仍在推进，但 freshness / finality 异常；
- `HALTED`：可信独立来源共同证明严重停滞；
- `UNKNOWN`：没有足够可信的结构化证据。

DNS、TLS、timeout、403、429 或 Provider outage 都只表示证据不可用，不是链已停止。

### 7.3 可选 API Key

部分可选 Provider 可通过环境变量配置，例如：

```bash
export SOSOVALUE_API_KEY='...'
export COINGECKO_API_KEY='...'
export GITHUB_TOKEN='...'
export RATED_API_KEY='...'
export LUNARCRUSH_API_KEY='...'
```

Key 不应写入仓库配置、JSONL、cache、日志或报告。配置存在也不代表 subscription / entitlement 一定有效。

完整说明见[数据 Provider 策略](../references/data-providers.md)。

## 8. 执行计划与 Volume Profile

只有 Python 再平衡层先批准 `INCREASE` 后，才进入技术执行阶段。

技术层可以减少立即部署金额、分批或返回 `WAIT`；不能增加组合层批准金额、修改 strategic target、绕过 risk gate 或自动下单。

执行层使用 timestamped SpotPrice、completed OHLCV、MA20/50/100/200、30D/90D/180D returns、ATR14、realized volatility、relative volume、drawdown、confirmed swing zones 和 Volume Profile。

日线优先至少 200 根 completed candles，最好约 240 根。Volume Profile 优先 completed `1H` / `4H`，使用 `(high + low + close) / 3` 代表 bar price，输出 `POC`、`VAL`、`VAH`、HVN 和 LVN。

Volume Profile 是历史成交量集中度代理，不代表 holder 的精确成本基础。

当前执行计划只生成 `PULLBACK`；`BREAKOUT` 返回 `WAIT`，`MIXED` 被拒绝。`planned_amount_usd` 是建议分阶段部署额度，不代表已成交。

## 9. Agent 与 Python 的责任边界

原则：

> 能从结构化数据确定性推导的结果由 Python 负责；Agent 只处理无法确定性推导的有界语义问题和最终解释。

| 责任 | 所有者 |
|---|---|
| 截图字段提取 | Agent |
| Metric plan | Python |
| Provider 获取与规范化 | Python |
| Position P&L | Python |
| 技术指标 / Volume Profile | Python |
| 有界语义判断和事件解释 | Agent |
| scoring / regime / allocation / risk / rebalance | Python |
| 高影响复核 | Agent，在 Python predicate 触发时 |
| 最终报告文字 | Agent |

这里的 `Agent` 指用户在当前宿主中已经选择的模型 / 会话。

仓库不选择或切换 LLM model，不修改 reasoning / thinking level，也不实现 LLM model fallback。高影响复核仍然存在，但由当前 Agent 完成，不代表切换到特殊模型。

详细架构见[工作原理](HOW_IT_WORKS.md)。

## 10. 运行时数据与隐私

默认运行时数据位于：

```text
~/.local/share/crypto-portfolio-manager/
├── portfolio/snapshots.jsonl
├── decisions/decisions.jsonl
├── decisions/status-events.jsonl
├── metrics/observations.jsonl
├── metrics/collection-events.jsonl
├── market-data/sha256/<ohlcv_hash>.json
├── volume-profiles/sha256/<profile_hash>.json
└── provider-cache/
```

可以设置：

```bash
export CRYPTO_PORTFOLIO_DATA_DIR=/path/to/runtime-data
```

真实余额、数量、成本基础、账户标识、交易历史、API key、私钥和助记词都不应提交到 Git。

只读检查当前 confidence / runtime 状态：

```bash
python3 scripts/confidence.py \
  --data-dir "$CRYPTO_PORTFOLIO_DATA_DIR" \
  --json
```

仓库只支持当前内部运行时数据契约。破坏性变更导致旧生成状态不兼容时，应先备份再重新建立状态：

```bash
mv "$CRYPTO_PORTFOLIO_DATA_DIR" \
   "${CRYPTO_PORTFOLIO_DATA_DIR}.backup"
```

没有足够历史时，只能建立 baseline，不能声称存在可靠的历史 performance。

## 11. 多宿主 Skill 使用与安装

如果已经能在当前仓库正常调用 Skill，可以跳过本节。

在仓库根目录一键安装：

```bash
./install.sh --target all
```

也可以只安装一个宿主：

```bash
./install.sh --target codex
./install.sh --target claude
./install.sh --target zcode
```

安装脚本使用 symlink 指向 `.agents/skills/crypto-portfolio-manager/`，不会复制运行时代码、配置或 references。

### Codex

从仓库根目录或子目录启动 Codex：

```bash
git clone https://github.com/iavl/crypto-portfolio-manager.git
cd crypto-portfolio-manager
codex
```

调用：

```text
$crypto-portfolio-manager
```

Codex Repository Skill 直接从当前 Git working tree 读取实现。

### Claude Code

可使用用户级 symlink：

```bash
cd /path/to/crypto-portfolio-manager
mkdir -p "$HOME/.claude/skills"
ln -s "$PWD/.agents/skills/crypto-portfolio-manager" \
  "$HOME/.claude/skills/crypto-portfolio-manager"
claude .
```

调用：

```text
/crypto-portfolio-manager
```

### ZCode

推荐在 `Settings → Skills → Import` 中选择该 Skill，并优先使用 `Symlink` 模式；也可以链接到：

```text
~/.zcode/skills/crypto-portfolio-manager
```

调用：

```text
$crypto-portfolio-manager
```

不同宿主分别决定 Skill 触发方式、模型、reasoning / thinking 设置、Web、shell、文件系统和网络权限。仓库不覆盖这些宿主设置。

## 12. 出现问题时怎么办

普通 Portfolio Review 不要求用户理解 Provider 内部实现。若报告出现大量 `FAILED`、`STALE`、`SKIPPED` 或 `CONFLICT`，先检查：

```bash
python3 scripts/providers.py --status
```

进一步诊断某个 Provider：

```bash
python3 scripts/providers.py --doctor coingecko
python3 scripts/providers.py --probe coingecko --asset ETH
```

事件源问题：

```bash
python3 scripts/events.py --plan --asset BTC --asset ETH
python3 scripts/events.py --smoke --asset BTC --asset ETH
```

不要因为 DNS / TLS / timeout / HTTP 401、403、429 / subscription failure 就手工把缺失证据标成成功。

完整 diagnosis、TLS CA bundle、record/replay、contract、smoke 和开发测试流程见[开发与 Provider 排查](DEVELOPMENT_DEBUGGING.md)。

核心原则：

```text
不确定性降低行动性，而不是被猜测抹掉。
```
