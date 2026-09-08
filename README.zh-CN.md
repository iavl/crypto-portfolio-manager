[English](README.md) · [简体中文（当前）](README.zh-CN.md)

当前写入契约使用政策 v4、评分 v2、路由 v2、执行计划 v2。历史政策 v3 记录仍可读取，但不会静默按 v4 回放。Confidence 由 Python 负责，并分为 Data -> Regime -> Decision 三层。时间戳必须带时区，执行计划必须使用带时间戳的 SpotPrice。

# crypto-portfolio-manager

`crypto-portfolio-manager` 是一个 Codex Skill，用于在约 6–12 个月的周期内，以保守均衡、仅现货的方式研究加密货币投资组合。它将当前市场证据与确定性的记账、风险、配置、基准和再平衡计算结合起来。

详细的复盘流程、输入示例、历史数据行为和故障排查请参阅[使用指南](docs/USAGE.md)。

架构与实现细节请参阅[工作原理](docs/HOW_IT_WORKS.md)。

术语不熟悉？请先查看[中文术语表](docs/GLOSSARY.zh-CN.md)。

模型与推理配置使用安全默认值，并会根据运行时进行回退；请参阅[路由参考](references/model-routing.md)。

## 概览

```text
调用：    $crypto-portfolio-manager
安装：    ${CODEX_HOME:-$HOME/.codex}/skills/crypto-portfolio-manager/
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

- 支持 Agent Skills 的 Codex。
- Python 3.11 或更高版本，用于包含的脚本和开发检查。
- Git，用于 GitHub/手动安装和开发。
- 运行 Codex 的环境需要具备网络/网页访问能力，以进行实时研究或按需刷新公共 provider 数据。

正常使用 Skill 不需要安装 Python 包。仓库没有运行时 Python 依赖；`jsonschema` 和 `ruff` 仅是开发依赖。逻辑阶段路由配置在 `config/model-routing.json` 中。

## 安装

### 使用 `install.sh` 安装本地检出版本

将仓库克隆到 Codex Skill 目录之外，然后从检出目录运行安装器：

```bash
git clone https://github.com/iavl/crypto-portfolio-manager.git
cd crypto-portfolio-manager
./install.sh
```

脚本使用自身所在目录作为源目录，遵循 `${CODEX_HOME:-$HOME/.codex}`，并将完整的运行时 Skill 内容复制到：

```text
${CODEX_HOME:-$HOME/.codex}/skills/crypto-portfolio-manager/
```

它会排除 Git 元数据、测试、开发环境、缓存和仓库中的 `data/`；投资组合历史数据会保存在单独的运行时数据目录中。安装器拒绝覆盖已有目录或符号链接。如果 Skill 没有立即可用，请重新加载或重启 Codex。

### Codex skill-installer

如果不需要本地检出版本，可以在 Codex 会话中调用 `$skill-installer`，并提供：

```text
Install https://github.com/iavl/crypto-portfolio-manager as
crypto-portfolio-manager. The Skill is at the repository root; use path `.`
and name it `crypto-portfolio-manager`.
```

当前安装器辅助脚本会使用对应的 `--url`、`--path .` 和 `--name crypto-portfolio-manager` 参数。安装后的文件应为：

```text
${CODEX_HOME:-$HOME/.codex}/skills/crypto-portfolio-manager/SKILL.md
```

该辅助脚本同样拒绝覆盖已有目标目录。如果它不可用，请使用上面的本地检出方式。

## 验证安装

```bash
test -d "${CODEX_HOME:-$HOME/.codex}/skills/crypto-portfolio-manager" \
  && test -f "${CODEX_HOME:-$HOME/.codex}/skills/crypto-portfolio-manager/SKILL.md" \
  && echo "crypto-portfolio-manager installed"
```

然后在 Codex 中验证发现：

```text
$crypto-portfolio-manager explain what portfolio reviews you support.
```

当请求匹配 Skill 描述时，Codex 也可能自动选择该 Skill。需要确保发现 Skill 时，请使用显式调用。

## 使用

截图/JSON 输入、复盘类型、可复制提示词、试运行、外部数据和本地历史数据请从[使用指南](docs/USAGE.md)开始。

对于标准 Binance 流程，请将钱包总览的显示货币设置为 USD，截取资产/数量/价格-成本/浮动盈亏列，并上传截图。Agent 会提取可见字段；Python 会计算所有派生的盈亏值。显示为 `--` 的行会保持未知；部分截图会被报告为不完整，而不会被当作完整投资组合。

## 运行时数据与隐私

默认情况下，历史数据位于 Git 检出目录之外：

```text
~/.local/share/crypto-portfolio-manager/
```

设置 `CRYPTO_PORTFOLIO_DATA_DIR` 可使用其他目录。免费的公共 provider 不需要 API key；可选 provider 的 key 仅通过环境变量提供。绝不要提交真实余额、数量、成本基础、交易历史、账户标识符、凭证、私钥或助记词。

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

## 更新

`install.sh` 有意设计为只安装，不覆盖已安装的副本。拉取源代码检出版本，删除准确的旧 Skill 目录，然后重新运行安装器：

```bash
git -C /path/to/crypto-portfolio-manager \
  pull --ff-only
rm -rf "${CODEX_HOME:-$HOME/.codex}/skills/crypto-portfolio-manager"
/path/to/crypto-portfolio-manager/install.sh
```

运行前请检查删除路径。这只会删除已安装的 Skill 副本，不会删除投资组合历史数据。

## 卸载

只移除 Skill 安装目录：

```bash
rm -rf "${CODEX_HOME:-$HOME/.codex}/skills/crypto-portfolio-manager"
```

这不会删除投资组合历史数据。删除单独的默认历史目录是可选的，并且具有破坏性：

```bash
rm -rf ~/.local/share/crypto-portfolio-manager
```
