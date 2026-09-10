# 开发与 Provider 排查

## 目的

Provider 诊断是由 Python 执行的确定性基础设施。它解释配置、传输、上游
契约、规范化、fallback 和缓存状态，但不会改变投资组合策略、评分、配置、
风险或执行权限。

## 快速排查流程

```bash
python3 scripts/providers.py --status
python3 scripts/providers.py --doctor <provider>
python3 scripts/providers.py --probe <provider> --asset ETH
python3 scripts/providers.py --metric market.spot_price
python3 scripts/providers.py --contract <provider>
python3 scripts/providers.py --smoke ETH --metric market.spot_price
```

| 命令 | 回答的问题 | 是否联网 |
|---|---|---|
| `--status` | 配置、adapter 和 credential 是否已准备好？ | 否 |
| `--doctor` | 哪个 readiness/transport/schema 层失败？ | ready provider 会联网 |
| `--probe` | Provider 的最小 adapter 请求是否可用？ | 是 |
| `--metric` | 当前 metric 配置了哪条确定性 provider 链？ | 否 |
| `--contract` | 最小上游契约是否仍能完成规范化？ | 是 |
| `--smoke` | 真实 metric 路由是否能端到端工作？ | 是；使用 `REFRESH` |

`--status` 是离线 readiness 检查。`--probe`、`--doctor`、`--contract` 和
`--smoke` 是显式联网操作。已实际测试的失败返回 `2`；`all` 操作中跳过的
未配置 Provider 不会导致命令失败。`scripts/run_with_debug.py` 保持适合报告
流程的退出码 `0`，同时把子进程退出码 `2` 记录为 `FAILED`。

## growthepie L2 TVS 定向验证

验证 `ETH:eth.l2.tvs_usd` 时使用当前 growthepie bulk TVL 合约：

```bash
python3 scripts/providers.py --metric eth.l2.tvs_usd --asset ETH

python3 scripts/providers.py \
  --doctor growthepie \
  --asset ETH

python3 scripts/providers.py \
  --probe growthepie \
  --asset ETH

python3 scripts/providers.py \
  --contract growthepie \
  --asset ETH

python3 scripts/providers.py \
  --smoke ETH \
  --metric eth.l2.tvs_usd
```

健康结果应满足：growthepie 不需要 credential；TVS 使用
`master.json + export/tvl.json`；规范化结果的 `source=growthepie`；
`observed_at` 是已完成的 UTC 日期；chain count 非零；growthepie 是当前
L2 TVS/activity 路由。缺失链数据不能按零处理，DeFiLlama protocol TVL 也不是
该 L2 TVS 指标的替代品。

## Provider 架构

```text
metric request
    -> provider route
    -> ProviderRouter
    -> cache (AUTO/CACHE_ONLY) or HttpClient (REFRESH)
    -> normalized observation
    -> ProviderAttempt telemetry
    -> deterministic fallback
    -> unresolved evidence when all providers fail
```

`HttpClient` 负责已验证的 TLS、有限重试、响应大小限制、安全脱敏和传输分类。
Router 负责请求预算、缓存行为、fallback 以及进程内 Provider 断路器。

## Event-only acquisition

事件指标不走普通 Provider router。使用固定 source catalog 和结构化 transport，
先取 bounded candidates，再由 host 或显式配置的 classifier 做 materiality 判断；
未分类候选保持 `CLASSIFICATION_PENDING`，不会被当作 `CLEAR`。

```bash
python3 scripts/events.py --plan --asset BTC --asset ETH
python3 scripts/events.py --fetch --asset BTC --asset ETH --output pending-events.json
python3 scripts/events.py --resolve classified-events.json --asset BTC --asset ETH
python3 scripts/events.py --smoke --asset BTC --asset ETH
```

`--fetch` 只获取候选，不评分、不生成组合决策。host-assisted 流程应保留
`pending_responses`，在 `responses` 中为每个候选补充分类后再运行 `--resolve`；
程序会拒绝未知字段、source/时间戳/URL/候选身份变化。`--smoke` 只显示事件状态、
覆盖率、confidence、候选数和脱敏诊断。

当前结构化 source 映射为：Ethereum Foundation security 使用 Blog RSS，
Ethereum EIPs 使用 `ethereum/EIPs` GitHub commits，Aave security 使用
Governance Risk Discourse JSON，AAVE governance 同时使用 Governance V3
Ethereum contract `0x9AEE0B04504CeF83A65AC3f0e838D0593BCb2BC7` 的
allowlisted `RPC_LOGS` 和官方 forum/proposals。AAVE governance 只有
`ONCHAIN_AND_ONE_OFFCHAIN` 两组都满足时才是 `SUFFICIENT`；ESMA/MiCA 使用
ESMA RSS。GitHub commits 使用
请求的 `lookback_start`/`as_of` 作为 `since`/`until`；Discourse 只跟随同源的
`more_topics_url`，达到有界分页上限时保持 incomplete。`NO_STRUCTURED_TRANSPORT`
表示固定 source 没有配置受支持的结构化 endpoint，与
`DNS_RESOLUTION_FAILED`、`TLS_*`、`HTTP_*`、`PROVIDER_TRANSPORT_ERROR` 不同。

事件阶段状态和错误边界如下：

```text
FETCHED -> CLASSIFICATION_PENDING -> CLASSIFIED -> SUCCESS
FETCH_FAILED | CLASSIFICATION_FAILED | INSUFFICIENT_SOURCE_COVERAGE | CONFLICT
```

`CACHE_ONLY` 只读 event transport cache，cache miss 保持不可达；它不会发 HTTP
或调用 classifier API。可选 API classifier 使用环境变量：
`EVENT_CLASSIFIER_MODE=api`、`EVENT_CLASSIFIER_API_KEY`、
`EVENT_CLASSIFIER_API_URL`、`EVENT_CLASSIFIER_PROVIDER`、`EVENT_CLASSIFIER_MODEL`。
缺少 key 或 API 失败会保持 pending，并不会产生 `CLEAR`。GitHub source 若无
`GITHUB_TOKEN`，smoke 输出应明确是 unauthenticated mode。

## 决策树

```text
NOT_READY
  -> 配置 / adapter / credential

DNS / CONNECT_TIMEOUT / READ_TIMEOUT / TLS / PROXY
  -> 本地传输环境或临时网络故障

HTTP_401 / HTTP_403_AUTH
  -> credential 或认证契约

HTTP_403_ACCESS_DENIED / HTTP_403_REGION_RESTRICTED / HTTP_403_WAF
  -> endpoint 或 Provider 访问策略

HTTP_403_RATE_LIMIT / HTTP_429
  -> rate limit；可能进行有限重试

HTTP_5XX / CONNECTION_RESET
  -> 临时上游/网络故障

HTTP_402 / ENTITLEMENT_REQUIRED / PROVIDER_PLAN_RESTRICTED
  -> 订阅或 Provider 权限不足；只有明确 optional/premium metric 才可 SKIPPED

RATED_SUBSCRIPTION_INACTIVE
  -> Rated 返回 401 Subscription is not active；affected staking metrics 为 optional SKIPPED，provider attempt 保留诊断

RATE_LIMITED / HTTP_429
  -> provider rate limit；保留 retryable，不填零、不当作成功

UNAVAILABLE_BY_METHODOLOGY
  -> 输入不足以证明所需计算方法；不是 transport failure

INVALID_JSON / PROVIDER_SCHEMA_ERROR / PROVIDER_SCHEMA_CHANGED
  -> 上游契约或规范化回归

CACHE_MISS / CACHE_EXPIRED / CACHE_CORRUPT
  -> 本地缓存状态

CIRCUIT_OPEN / REQUEST_BUDGET_EXHAUSTED
  -> 本地安全门；检查更早的根因尝试
```

普通 403 只有在安全响应元数据明确指出认证、rate limit、区域限制、访问拒绝
或 WAF 时，才会分类为对应代码；否则是 `HTTP_403_UNKNOWN`。原始 HTML challenge
页面和响应正文不会写入报告。

## 错误码参考

传输错误码包括 `DNS_RESOLUTION_FAILED`、`CONNECT_TIMEOUT`、`READ_TIMEOUT`、
`CONNECTION_REFUSED`、`CONNECTION_RESET`、`TLS_*` 和 `PROXY_ERROR`。HTTP 错误码
包括 `HTTP_400`、`HTTP_401`、`HTTP_402`、分类后的 `HTTP_403_*` 系列、`HTTP_404`、
`HTTP_429` 和 `HTTP_5XX`。

Provider 错误码包括 `PROVIDER_PLAN_RESTRICTED`、`RATED_SUBSCRIPTION_INACTIVE`、
`ENTITLEMENT_REQUIRED`、`RATE_LIMITED`、`UNAVAILABLE_BY_METHODOLOGY`、
`PROVIDER_INSUFFICIENT_HISTORY`、`PROVIDER_UNSUPPORTED`、
`PROVIDER_NOT_APPLICABLE`、`PROVIDER_SCHEMA_ERROR` 和
`PROVIDER_SCHEMA_CHANGED`、`SOURCE_METHOD_MISMATCH`、
`DERIVED_INPUT_UNAVAILABLE` 和 `QUERY_BUDGET_EXCEEDED`。缓存及本地安全错误码包括 `CACHE_MISS`、
`CACHE_EXPIRED`、`CACHE_CORRUPT`、`CIRCUIT_OPEN` 和
`REQUEST_BUDGET_EXHAUSTED`。

## 配置与凭证

`--status` 会分别显示配置、adapter 注册、credential 是否存在以及 runtime
readiness。凭证只通过环境变量提供。不要把 API key 写入
`config/data-providers.json`、请求 recording、缓存文件或日志。

```bash
export COINGECKO_API_KEY='...'
python3 scripts/providers.py --status
python3 scripts/providers.py --doctor coingecko
```

凭证存在不代表 Provider 健康：DNS、TLS、plan 权限或上游规范化仍可能不可用。

## Why is Decision Confidence LOW?

Decision Confidence is action-scoped. Inspect the persisted component breakdown
before changing thresholds:

```bash
python3 scripts/confidence.py --json
python3 scripts/confidence.py --explain --json
```

The JSON contains `scope`, component scores, hard `caps`, one-time
`soft_penalties`, `why_low`, and the bounded conditions that can increase
confidence. A low-confidence watchlist asset with zero exposure should not
appear in a HOLD scope. Conversely, unknown target security or chain liveness
must remain an action blocker even if other components are HIGH.

## 代理与 TLS 排查

客户端要求证书和主机名校验。如果本地信任库不完整，请提供可信 CA bundle：

```bash
export CRYPTO_PORTFOLIO_CA_BUNDLE=/path/to/trusted-ca-bundle.pem
python3 scripts/providers.py --doctor binance
```

当 Python/OpenSSL 没有默认 CA 路径时，客户端会优先使用已安装的 `certifi`
CA bundle；如果环境没有 `certifi`，再使用 macOS 的 `/etc/ssl/cert.pem`（如存在）。
也可以用 `CRYPTO_PORTFOLIO_CA_BUNDLE` 显式指定可信 bundle。

不要使用 `verify=False`、未验证的 SSL context 或 `curl -k`。TLS 失败代表证据
不可用，不代表链或 Provider 已停止。

## 速率限制与重试

`HTTP_429` 和有限的 `HTTP_403_RATE_LIMIT` 只会对幂等请求进行重试。重试延迟
遵循 `Retry-After`；否则使用有上限的指数 full jitter。永久性 4xx、plan、
unsupported 和 schema 错误会快速失败。

Router 在同一 Provider 出现三次可重试失败后，会打开该 Provider 的进程内断路器
60 秒。断路器打开时发出 `CIRCUIT_OPEN` 并继续执行配置的 fallback，不会抹掉原始
失败原因。

## 缓存与回退排查

需要证明流程离线时使用 `CACHE_ONLY`。`REFRESH` 会绕过可变响应缓存。缺失、
过期、损坏或 unsupported 数据仍保持不可用，绝不会被转换成零。Provider attempt
会保留 provider、dataset、asset、metric keys、request hash、网络请求数、endpoint、
method、错误码、retryability、status 和安全 detail。

## 记录 / 回放

临时的公共响应 recording 可以放在已被 Git 忽略的路径中：

```bash
python3 scripts/providers.py --probe binance --record
python3 scripts/providers.py --replay .data/provider-recordings/<file>.json
```

Recording 只包含版本、Provider、method、脱敏 endpoint、时间戳、status 和脱敏后的
JSON 响应。请求头、cookie、authorization 值、query credential 和原始响应正文都
不会被序列化。默认 recorder 会跳过带凭证的请求。只有在安全且确实有助于 parser
回归时，才将脱敏后的公共 fixture 放入 `tests/fixtures/providers/`。

离线回放测试使用现有 `HttpClient` 和 Provider adapter 配合 `ReplayTransport`。
回放请求必须匹配 recording 的 method 和脱敏 endpoint；不匹配时会失败，不会静默
返回错误 payload。

## 离线、契约与冒烟测试

普通单元测试不得访问互联网：

```bash
python3 -m unittest discover -s tests -v
```

实时契约检查必须显式执行且请求保持最小化：

```bash
python3 scripts/providers.py --contract coingecko
python3 scripts/providers.py --contract all
```

缺少可选 credential 时标记为 `SKIPPED`/`NOT_READY`；已提供但被拒绝的 credential、
schema 变更或规范化失败则属于失败。冒烟检查使用生产 Router 和 `REFRESH`：

```bash
python3 scripts/providers.py --smoke ETH --metric market.spot_price
```

结果会报告 chain、attempts、选中的 Provider、网络请求数、缓存命中数、fallback 数、
最终 status 和 unresolved diagnostics，不输出原始市场 payload。

## 调试报告集成

需要把 live 或 contract 命令结果纳入报告时，使用包装器：

```bash
python3 scripts/run_with_debug.py \
  --script scripts/providers.py -- \
  python3 scripts/providers.py --probe coingecko --asset ETH
```

包装器返回可写入报告的 JSON 记录。子进程非零退出会记录为 `FAILED`；超时和启动
异常分别记录为 `TIMEOUT` 和 `LAUNCH_FAILED`。日志会限长并脱敏。包装器自身可以
返回 `0`，保证报告收集继续进行。

## CI Provider 检查

`.github/workflows/provider-contracts.yml` 与确定性的单元测试 CI 分离，并按计划或
手动触发。公共检查不需要 secret。只有对应 GitHub secret 存在时才运行 credentialed
检查；缺少 credential 会报告为 skipped。CI 会区分临时网络、rate limit、认证、plan、
schema 和 normalization 失败。

## 新增 Provider

新增 Provider 只有同时包含以下内容才算完成：

```text
adapter、config、capabilities、route、readiness/doctor 行为、
离线 fixture 测试、最小 contract 检查、确定性错误映射、
secret 脱敏以及文档。
```

不得加入交易 key、下单、杠杆或 autonomous execution 能力。

本计划新增/修复的只读契约探测：

```bash
python3 scripts/providers.py --probe coinmetrics_community --asset BTC
python3 scripts/providers.py --probe coinmetrics_community --asset ETH
python3 scripts/providers.py --probe coinmetrics_community --asset BNB
python3 scripts/providers.py --probe bgeometrics --asset BTC
python3 scripts/providers.py --probe lunarcrush --asset BTC
python3 scripts/providers.py --probe rated --asset ETH
python3 scripts/providers.py --probe ethereum_beacon --asset ETH
python3 scripts/providers.py --probe etherscan --asset ETH
```

Coin Metrics 输出按 asset 显示真实 1D catalog；LunarCrush 使用 API v4
Bearer credential，同一 asset 的 social metrics 共用一次 time-series request，
并把 402/429/circuit 分别记录为 `ENTITLEMENT_REQUIRED`/`RATE_LIMITED`/`CIRCUIT_OPEN`。
Rated 401 body `{"detail":"Subscription is not active."}` 记录为
`RATED_SUBSCRIPTION_INACTIVE`，对应 optional staking metric 归一化为
`SKIPPED`；provider attempt 仍保留诊断。普通 metric plan 默认不请求
LunarCrush，显式 `--probe` 仍保留真实错误诊断。Ethereum protocol probe 只读取一个 latest block。数值历史缺失保持
`PROVIDER_INSUFFICIENT_HISTORY`，不会生成 Web 请求。

BGeometrics 不需要 key；LunarCrush 需要 `LUNARCRUSH_API_KEY`；Rated 需要
`RATED_API_KEY`；Beacon 使用 `ETH_BEACON_API_URL`，Ethereum protocol 使用
`ETHEREUM_RPC_URL` 或默认 PublicNode 地址。
缺少这些可选配置时，`--status`/`--doctor` 会显示未准备状态，不会将
provider failure 变成零值。

报告调试栏按四类展示：`decision_blocking_failures`、
`required_scoring_failures`、`optional_data_unavailable` 和
`provider_operational_failures`。只有前两类进入决策语义；optional/context
缺口不会进入适用评分分母。

## 合并前检查清单

- [ ] 确定性单元测试保持离线；
- [ ] Provider failure 保留安全的根因诊断；
- [ ] 403 和 timeout 阶段分类正确；
- [ ] TLS 校验保持启用；
- [ ] fixture、cache、log 和 report 中不存在 credential；
- [ ] fallback 和 circuit 行为有测试覆盖；
- [ ] contract/smoke 命令是显式的并且有文档；
- [ ] 完整测试、Ruff 和 compile 检查通过。
