
# crypto-portfolio-manager

`crypto-portfolio-manager` 是一个 Agent Skill，用于在约 3–6 个月的主动配置周期内，以保守均衡、仅现货的方式研究加密货币投资组合。它将当前市场证据与确定性的记账、风险、配置、基准和再平衡计算结合起来，可由支持 Agent Skills 的 Codex、Claude Code 和 ZCode 使用。

详细的复盘流程、输入示例、历史数据行为和故障排查请参阅[使用指南](docs/USAGE.md)。

投资理念和组合策略请参阅[投资策略](references/investment-strategy.md)。

架构与实现细节请参阅[工作原理](docs/HOW_IT_WORKS.md)。

术语不熟悉？请先查看[中文术语表](docs/GLOSSARY.zh-CN.md)。

模型与推理配置使用安全默认值，并会根据运行时进行回退；请参阅[路由参考](references/model-routing.md)。

## 概览

```text
调用：    $crypto-portfolio-manager
Skill：   .agents/skills/crypto-portfolio-manager/SKILL.md
历史数据：~/.local/share/crypto-portfolio-manager/
交易：    仅提供建议；不会自动执行
```

## 功能

- 根据规范的 `config/policy.json` 策略校验持仓。
- 执行现金流调整后的 NAV、风险、配置、基准和再平衡计算。
- 使用 Python 优先的流程：由注册表驱动的指标计划、规范化观测值、确定性 Facts，以及紧凑且不可变的复盘/报告数据包。
- 保留证据、因子评分和追加写入的决策历史。
- 持久化与决策相关的 `MetricObservation` 历史记录和采集失败记录，用于紧凑的当前与此前趋势比较。
- 按需通过免费的结构化公共 API 获取数据，并使用考虑新鲜度的本地 provider 缓存；无需后台服务。
- 配置后，可使用 SoSoValue 文档化的美国 BTC/ETH ETF 汇总历史记录作为结构化 ETF 资金流背景；清算数据不会归因于该来源。年化基差使用 Binance 最近的交易型 USDT 交割合约及精确的标记价格/指数价格；不支持的资产保持不可用。
- 增加衍生品/社交持仓和 BTC 周期背景等非评分 overlay；这些 overlay 可以保守地限制即时部署规模。
- 在匹配的评估周期内，对比 100% BTC 和 70/30 BTC/ETH 持有不动的基准。
- 将稳定币和现金视为同一资金篮子，并允许 `NO_TRADE`。
- 从 Binance 钱包截图导入结构化字段，并确定性地计算每个仓位的成本基础、未实现盈亏、收益率和数据覆盖率。
- 基于带时间戳的现货数据和完整的 OHLCV，结合日历覆盖检查、确定性的 ATR 感知区间、已确认的摆动点、Volume Profile POC/价值区间/HVN 背景、分批方案以及 `WAIT` 处理，对获批的再平衡金额制定分阶段执行计划。
- 可配置模型/推理配置使用安全默认值，并根据运行时进行回退；Luna 阶段仅允许使用 `LUNA_MAX`。

## 安全边界 / 它不是什么

此 Skill 提供分析结果和建议的执行区间。它不会下单，也不会请求交易所交易或提现权限。

它不是短线交易机器人，也不是杠杆或保证金系统、期货/永续合约系统或托管式交易所集成。绝不要提供私钥、助记词或交易凭证。

投资组合配置决定总的美元敞口。技术执行层只决定如何分批执行已经获批的金额；每个计划都绑定对应的再平衡批准，可以只执行其中一部分，并且不会下单。`planned_amount_usd` 表示分阶段建议的额度，不代表已成交订单。

## 环境要求

- 支持 Agent Skills 的 Codex、Claude Code 或 ZCode 宿主。
- Python 3.11 或更高版本，用于包含的脚本和开发检查。
- Git，用于克隆和开发。
- 运行 Agent 的环境需要具备网络/网页访问能力，以进行实时研究或按需刷新公共 provider 数据。

正常使用 Skill 不需要安装 Python 包。仓库没有运行时 Python 依赖；`jsonschema` 和 `ruff` 仅是开发依赖。逻辑阶段路由配置在 `config/model-routing.json` 中。

## Skill 安装与使用

```bash
git clone https://github.com/iavl/crypto-portfolio-manager.git
cd crypto-portfolio-manager
```

仓库唯一 source of truth 是：

```text
.agents/skills/crypto-portfolio-manager/SKILL.md
```

Codex 从仓库根目录或其子目录启动即可自动发现该 Skill；Claude Code 和 ZCode
需要各自的 Skill 目录或 Import 操作。所有宿主都应使用 symlink 指向上述目录，
避免产生过期副本。完整命令、旧版迁移和运行时数据边界请参阅[多宿主安装指南](docs/USAGE.md#多宿主-skill-使用与安装)。

仓库 Skill 不需要单独的 Python 安装；如果需要运行开发检查，再执行：

```bash
python3 -m pip install -e ".[dev]"
```

## 使用

截图/JSON 输入、复盘类型、可复制提示词、试运行、外部数据和本地历史数据请从[使用指南](docs/USAGE.md)开始。

对于标准 Binance 流程，请将钱包总览的显示货币设置为 USD，截取资产/数量/价格-成本/浮动盈亏列，并上传截图。Agent 会提取可见字段；Python 会计算所有派生的盈亏值。显示为 `--` 的行会保持未知；部分截图会被报告为不完整，而不会被当作完整投资组合。

## 运行时数据与隐私

默认情况下，历史数据位于 Git 检出目录之外：

```text
~/.local/share/crypto-portfolio-manager/
```

设置 `CRYPTO_PORTFOLIO_DATA_DIR` 可使用其他目录。免费的公共 provider 不需要 API key；可选 provider 的 key 仅通过环境变量提供。仓库只支持当前运行时数据契约；破坏性变更后的不兼容生成状态必须手动重新生成。绝不要提交真实余额、数量、成本基础、交易历史、账户标识符、凭证、私钥或助记词。

内容寻址的公共 OHLCV 回放数据存储在同一运行时目录下的 `market-data/sha256/<ohlcv_hash>.json` 中。指标观测和采集事件存储在 `metrics/` 下；缓存的 Volume Profile 结果存储在 `volume-profiles/sha256/<profile_hash>.json` 下。Volume Profile 是历史成交量集中度的代理指标，不是精确的持仓成本基础。

Provider 响应和序列清单缓存在 `provider-cache/` 下；模式和清理方式请参阅[数据 Provider](references/data-providers.md)。仓位盈亏是剩余仓位的未实现表现；此功能不声称提供已实现盈亏、手续费、税务批次或生命周期收益率。
Provider 或 API 出现问题？请参阅[开发与 Provider 排查指南](docs/DEVELOPMENT_DEBUGGING.md)。

## 开发

```bash
git clone https://github.com/iavl/crypto-portfolio-manager.git
cd crypto-portfolio-manager
python3 -m pip install -e ".[dev]"
```

运行 CI 使用的检查：

```bash
python3 -m unittest discover -s tests -v
ruff check .
python3 -m compileall crypto_portfolio scripts
```

规范化结构化快照：

```bash
python3 scripts/portfolio_snapshot.py path/to/snapshot.json
```
