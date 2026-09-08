# 使用指南

首次使用时，可先查阅[中文术语表](GLOSSARY.zh-CN.md)。

本指南说明 `crypto-portfolio-manager` 的实际使用方式、输入格式、复盘
流程、数据采集、历史记录和故障处理。

安装、更新和卸载请参阅根目录的 [README](../README.md)。架构与内部实现
请参阅[工作原理](HOW_IT_WORKS.md)。Skill 专属策略和参考资料位于根目录
的 `references/` 与 `SKILL.md`。

## 1. Skill 能做什么

本 Skill 面向约 6–12 个月周期、仅现货、保守均衡的加密货币组合研究。
它会结合当前证据和 Python 确定性计算，完成：

- 组合记账、现金流调整后的 NAV、回撤和 BTC 基准；
- 市场指标、评分、市场状态、目标配置和再平衡；
- Position P&L、成本数据覆盖率、风险检查和分批执行计划；
- 安全、治理和监管事件扫描；
- `NO_TRADE`、`HOLD`、`WAIT`、`REDUCE` 和 `EXIT` 建议。

它不会下单，不会请求交易或提现权限，也不支持期货、永续、杠杆、保证金
或自动交易。

## 2. 快速开始

1. 安装并重新加载 Skill。
2. 上传交易所钱包总览截图，或提供结构化 JSON 仓位。
3. 调用 `$crypto-portfolio-manager`。
4. 指定 `SNAPSHOT_REVIEW`、`FULL_REVIEW` 或 `EVENT_REVIEW`。

标准 Binance 截图需要显示 USD 货币、资产、数量、价格/成本和浮动盈亏。
Python 会从可见字段计算 Position P&L。`--` 和无法确认的 `$0.00` 成本会
保持未知，不会被转成 0。

示例：

```text
$crypto-portfolio-manager

这是我的最新仓位截图。

读取历史仓位和上一轮决策，获取当前市场数据，做一次 SNAPSHOT_REVIEW。
只有风险收益比足够明显时才建议交易；否则给出 NO_TRADE。
请包含 Position P&L 表格、成本数据覆盖率和调仓依据。
```

## 3. 复盘类型

### SNAPSHOT_REVIEW

用于当前仓位检查或日常更新。它会读取历史快照和上一轮决策，刷新当前证据，
重新检查 regime、风险门和再平衡阈值。

### FULL_REVIEW

用于完整比较，通常至少每 14 天一次。它还会比较 NAV、回撤、BTC 基准、
上一轮和当前 Position P&L、目标配置、评分和市场状态。

### EVENT_REVIEW

用于安全、协议、治理、监管或其他可能改变投资 thesis 的事件。事件扫描会
使用固定的 source catalog，并明确记录来源覆盖率。

### Dry run

要求不改变本地历史时使用 dry run：

```text
做一次 dry run，只输出分析和建议，不保存 snapshot、decision 或其他历史状态。
```

验证、风险控制和报告仍会运行，但不会追加运行时记录。

## 4. 报告如何给出决策依据

报告中的每个组合结论和风险资产 Action 都遵循：

```text
证据 → 事实含义 → 组合约束 → 风险门 → 调仓阈值 → Action
```

报告应说明 Evidence ID、source、observed time、数据状态、当前权重与目标权重
偏差、阈值以及改变建议的具体条件。报告只解释 finalized packet 中的内容，
不展示隐藏推理或私有 scratchpad。

报告中出现可能被误解的词时，会按需解释：

- “混合状态”表示核心资产趋势和市场广度不一致，不等于立即卖出；
- `MATERIAL_EVENT_FOUND` 表示扫描来源发现重要提案或公告，不表示漏洞、通过
  或已执行；
- Ethereum 使用“协议提案/升级活动”，Aave 使用“治理提案/风险参数活动”；
- `STALE`、`FAILED`、`SKIPPED`、Position P&L、NAV Return 和成本数据覆盖率
  都会说明其对 confidence 和交易资格的影响。

## 5. Debug 报告与脚本日志

复盘中实际执行的仓库脚本统一通过包装器运行：

```bash
python3 scripts/run_with_debug.py \
  --script scripts/providers.py -- \
  python3 scripts/providers.py --probe sosovalue
```

包装器捕获非零退出、超时和启动异常，并输出可传给
`build_report_packet(..., script_executions=...)` 的 JSON 记录。最终报告的
`Debug 报告`同时列出最终数据抓取失败、Provider 尝试日志和异常脚本日志。
包装器会用记录中的 `status` 表示子脚本失败，但自身返回 0，确保报告流程不会
因 `set -e` 在收集日志前中断。
日志会脱敏并限制长度；不会保存凭证、请求头、原始响应 body 或私有 reasoning。
成功脚本和仅包含普通 warning 的脚本不会列入异常列表。

可作为 --probe 参数的有：
- coingecko
- fred
- coinmetrics_community
- sosovalue
- l2beat
- growthepie
- blobscan
- defillama
- github
- chain_liveness

## 6. 模型与 Python 边界

系统采用 Python-first 流程：

```text
截图字段提取/未解决来源检索
→ Python 输入校验、历史和 MetricObservation
→ 有界语义判断
→ Python 评分、regime、配置、风险、再平衡和执行数学
→ finalized ReportPacket
→ 中文报告
```

模型不能发明 metric key、重算组合金额、修改 Python 目标权重或覆盖风险门。
模型路由由 `config/model-routing.json` 控制；Python-owned stages 永远保持
Python。

查看模型配置：

```bash
python3 scripts/model_routing.py --validate
python3 scripts/model_routing.py --list-profiles
python3 scripts/model_routing.py --show-effective
```

## 7. 提供结构化 JSON

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

验证和规范化：

```bash
python3 scripts/portfolio_snapshot.py portfolio.json
```

这个命令只验证和规范化 snapshot，不获取实时市场数据，也不生成完整投资
决定。Policy override 必须放在顶层 `config`，无效值、重复资产、重叠资产组
和冲突的 `asset_type` 会被拒绝。

## 8. 数据获取

默认获取顺序是：

```text
新鲜 MetricObservation
→ provider cache / 不可变 OHLCV 历史
→ 免费结构化 API
→ 可选 API-key provider
→ 仅针对明确未解决请求的 Web fallback
```

获取模式：

- `AUTO`：优先复用新鲜记录和缓存，再刷新缺失或过期的 mutable 数据；
- `CACHE_ONLY`：不发网络请求，缺失数据保持可见；
- `REFRESH`：刷新 mutable 当前数据，completed historical series 仍可复用。

```bash
export CRYPTO_PORTFOLIO_FETCH_MODE=CACHE_ONLY
export CRYPTO_PORTFOLIO_FETCH_MODE=REFRESH
```

运行级选择优先于环境变量。来源含义、字段边界和限制见
[数据源策略](../references/data-sources.md)与
[数据 Provider 策略](../references/data-providers.md)。

## 9. 链运行状态

`BTC`、`ETH`、`BNB` 和 `SOL` 使用结构化 block/slot provider 获取
`risk.chain_liveness_status`。AAVE 和 LINK 不作为独立链检查。

- `HEALTHY`：canonical progress 新鲜；
- `DEGRADED`：仍在推进，但年龄或 finality 异常；
- `HALTED`：独立来源共同证明严重停滞；
- `UNKNOWN`：没有可信的结构化证据。

DNS、TLS、timeout、403、429 或 provider outage 都是不可用证据，不是链已停止
的证据。链状态默认缓存 300 秒，不会通过普通 Web 搜索代替。

```bash
python3 scripts/providers.py --probe chain_liveness --asset BTC
python3 scripts/providers.py --probe chain_liveness --asset ETH
python3 scripts/providers.py --probe chain_liveness --asset SOL
python3 scripts/providers.py --probe chain_liveness --asset BNB
```

## 10. Position P&L、NAV 和现金流

Position P&L 是剩余仓位的未实现表现，不是已实现收益或组合终身收益。
成本覆盖率表示当前价值中有可用成本数据的比例。

外部存款和取款必须使用现金流调整后的 NAV 处理。若两个快照之间出现了
未分类的明显余额变化，NAV、回撤和基准表现标记为 `PROVISIONAL`，不会把
余额变化误认为投资收益。

截图中的 `$0.00` 成本若精度不足，按未知成本处理；稳定币和现金仍计入组合
总值及 stable sleeve。

## 11. 执行计划和 Volume Profile

只有 Python 再平衡先批准 `INCREASE` 后，才会进入技术执行阶段。技术层可以
减少立即部署金额或返回 `WAIT`，不能增加组合层批准金额，也不能下单。

执行层使用带时间戳的现货价格和 completed OHLCV，优先至少 120 根日线，
并计算 MA20/50/100/200、日历 30D/90D/180D 收益、ATR14、波动率、相对成交量、
回撤和确认的 swing zones。

Volume Profile 使用 completed `1H`/`4H` 数据优先，按
`(high + low + close) / 3` 分配成交量，输出 `POC`、`VAL`、`VAH`、HVN 和
LVN。它是历史成交量集中度代理，不是持仓者精确成本；LVN 只能作为背景。

v1 只生成 `PULLBACK` 计划。`BREAKOUT` 返回 `WAIT`，`MIXED` 被拒绝。
`planned_amount_usd` 是建议分配额度，不代表已经成交。

## 12. 历史数据与隐私

运行时数据默认位于：

```text
~/.local/share/crypto-portfolio-manager/
├── portfolio/snapshots.jsonl
├── decisions/decisions.jsonl
├── metrics/observations.jsonl
├── metrics/collection-events.jsonl
├── market-data/sha256/<ohlcv_hash>.json
├── volume-profiles/sha256/<profile_hash>.json
└── provider-cache/
```

可以设置 `CRYPTO_PORTFOLIO_DATA_DIR` 修改目录。不要把真实余额、数量、成本
基础、账户标识、凭证、私钥或助记词提交到 Git。

## 13. Provider 状态与 TLS 排查

```bash
python3 scripts/providers.py --status
python3 scripts/providers.py --list
python3 scripts/provider_cache.py --stats
python3 scripts/providers.py --probe binance
python3 scripts/providers.py --probe coingecko
python3 scripts/providers.py --probe defillama
python3 scripts/providers.py --probe alternative_me
python3 scripts/providers.py --probe sosovalue
```

`--status` 是离线配置检查；`--probe` 才会发网络请求，并且不会输出响应
正文或凭证。

Python HTTPS 始终启用证书和主机名校验。信任库不完整时可指定：

```bash
export CRYPTO_PORTFOLIO_CA_BUNDLE=/path/to/trusted-ca-bundle.pem
```

在 macOS 上，如果 OpenSSL 没有默认 CA 文件或目录，客户端会使用存在的
`/etc/ssl/cert.pem`。不要使用 `verify=False`、未验证 SSL context 或
`curl -k`。

## 14. 可选 API key

API key 只通过环境变量提供，绝不写入配置、cache、JSONL、日志或报告：

```bash
export SOSOVALUE_API_KEY='...'
export COINMETRICS_API_KEY='...'
export COINGECKO_API_KEY='...'
export GITHUB_TOKEN='...'
```

配置存在不代表 provider 一定可用；应同时检查 adapter、credential、runtime
status 和实际 probe 结果。

安全检查不会输出 key 值：

python3 scripts/providers.py --status
python3 scripts/providers.py --probe coingecko --asset BNB
python3 scripts/providers.py --probe github --asset ETH
python3 scripts/providers.py --probe github --asset AAVE
python3 scripts/providers.py --probe sosovalue --asset BTC
python3 scripts/providers.py --probe sosovalue --asset ETH

## 15. 开发检查

```bash
python3 -m unittest discover -s tests -v
ruff check .
python3 -m compileall crypto_portfolio scripts
```

出现缺失、过期、冲突或来源不可用时，报告必须保留状态和对 confidence、
交易资格及最终 Action 的影响。未知数据不会被默认为安全，也不会被填成零。
