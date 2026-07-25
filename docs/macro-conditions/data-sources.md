# Optix 宏观环境 · 数据源

## 1. 来源与署名

| 来源 | 用途 |
| --- | --- |
| FRED（圣路易斯联储） | 全部 24 个宏观时间序列的取数接口 |
| 联储理事会（H.4.1 / H.10 / H.15） | 资产负债表、准备金、美元指数、利率 |
| 纽约联储 | SOFR、OBFR、EFFR、ON RRP 余额与中标利率 |
| 芝加哥联储 | NFCI 全国金融条件指数 |
| 美国财政部 | 国债与 TIPS 收益率曲线 |
| Cboe Global Markets | VIX、VXV、OVX |
| 美国能源信息署 | WTI 原油、亨利枢纽天然气 |
| Option Pro 现有股票日线数据源 | 8 个跨资产 ETF 代理（Massive 优先，Yahoo 回落） |

UI 的来源说明固定为：

> 宏观数据来自 FRED、纽约联储、联储理事会、芝加哥联储和 Cboe；跨资产代理使用
> Option Pro 当前股票日线数据源。分数为过去 5 年历史分位，不是预测。

**不做的事**：不抓取 bhadial.com Dashboard 接口，不解析 MacroDial 页面 JSON，
不复制其名称、Logo、品牌色或 PDF 版式。

---

## 2. FRED 客户端

固定使用官方 HTTPS Origin `https://api.stlouisfed.org`。

- **没有** `FRED_BASE_URL` 环境变量，**没有**自定义 FRED 代理，**没有**新的 Provider 抽象。
- `httpx` + `trust_env=false`（忽略宿主的代理变量）+ `follow_redirects=false`
  （重定向会离开官方 Origin，直接判为不可用且不重试）。
- Connect 5s / Read 20s / Total 30s；响应体上限 8 MiB（先看 `Content-Length`，再看实际体积）。
- 必须是 `application/json`；非 JSON 或 JSON 解析失败判为 `fred_schema_mismatch` 且不重试。
- 并发 1，按顺序逐个 Series 拉取。
- 429 尊重 `Retry-After`；5xx 用有界指数退避（1s→2s→4s，上限 8s）；最多 3 次尝试。
- 非限流 4xx（400/401/403/404/422）**不重试**。
- 日志只写 Series ID 与安全错误码；不写 Key、请求 URL、上游响应体。
- 测试通过 `httpx.MockTransport` 注入替换网络，CI 从不访问真实 FRED。

### 元数据校验

每次取数前先读 `/fred/series` 元数据并逐项校验：

1. 返回的 `id` 必须等于请求的 Series ID；
2. `units` 必须属于注册的单位族；
3. `frequency_short` 不得高于注册频率（周度系列突然按日发布 = 元数据漂移）；
4. 校验不通过时抛 `fred_schema_mismatch` 或 `fred_units_mismatch`，该 Series 计入
   `series_failed`，其余 Series 不受影响。

### 观察值解析

- `"."` 与空串一律解析为缺失（`None`），不是 0。
- 非有限值（NaN/Inf/非数字文本）也当缺失，永不入库、永不进入分位窗口。
- 同一 `observation_date` 重复出现时按稳定规则收敛：升序里最后一行胜出，
  且真实值永远优先于缺失值。

---

## 3. 单位归一

**所有金额统一为 USD billions。** 换算倍数从**官方元数据的 `units` 字符串**解析，
绝不按系列名猜：

| FRED units | → USD billions |
| --- | --- |
| `Trillions of U.S. Dollars` | × 1000 |
| `Billions of U.S. Dollars` | × 1 |
| `Millions of U.S. Dollars` | ÷ 1000 |
| `Thousands of U.S. Dollars` | ÷ 1 000 000 |

未识别的单位**直接拒绝**，不按 1.0 静默通过——余额类系列一个数量级的错误会污染
全部流动性分数。因此 `WALCL` 与 `RRPONTSYD` 各自按自己的元数据换算，不共用假设。

其余单位族：`percent`（百分点，×1）、`index`（指数点，×1）、
`usd_per_barrel`、`usd_per_mmbtu`。

---

## 4. 24 个 FRED Series 注册表

每项注册 `series_id / display_name_zh / expected_frequency / expected_units_family /
canonical_unit / scale / max_stale_calendar_days / source_name / source_attribution /
enabled / required_for`。`required_for` 与因子表**双向校验**，任一侧漂移即测试失败。

| 模块 | Series | 频率 | 单位族 | 最大陈旧 |
| --- | --- | --- | --- | --- |
| liquidity | `WALCL` 联储总资产 | W | usd_amount | 14 天 |
| liquidity | `WTREGEN` 财政部一般账户 | W | usd_amount | 14 天 |
| liquidity | `RRPONTSYD` 隔夜逆回购余额 | D | usd_amount | 7 天 |
| liquidity | `WRESBAL` 银行准备金 | W | usd_amount | 14 天 |
| funding | `SOFR` 有担保隔夜融资利率 | D | percent | 7 天 |
| funding | `OBFR` 银行隔夜融资利率 | D | percent | 7 天 |
| funding | `IORB` 准备金余额利率 | D | percent | 7 天 |
| funding | `RRPONTSYAWARD` ON RRP 中标利率 | D | percent | 7 天 |
| funding | `EFFR` 联邦基金有效利率 | D | percent | 7 天 |
| funding | `DCPF3M` 3M 金融商业票据 | D | percent | 7 天 |
| funding | `DTB3` 3M 国库券贴现率 | D | percent | 7 天 |
| treasury | `DGS2` / `DGS10` / `DGS30` | D | percent | 7 天 |
| rates | `DFII5` / `DFII10` TIPS 实际收益率 | D | percent | 7 天 |
| rates | `T10YIE` 10Y 通胀预期 | D | percent | 7 天 |
| credit | `NFCI` 全国金融条件指数 | W | index | 14 天 |
| risk | `VIXCLS` / `VXVCLS` | D | index | 7 天 |
| external | `DTWEXBGS` 美元广义指数 | D | index | 7 天 |
| external | `DCOILWTICO` WTI 原油 | D | usd_per_barrel | 7 天 |
| external | `OVXCLS` 原油波动率指数 | D | index | 7 天 |
| external | `DHHNGSP` 亨利枢纽天然气 | D | usd_per_mmbtu | 7 天 |

---

## 5. 8 个 ETF 代理

`HYG · IEI · LQD · IEF · KRE · SPY · TLT · IWM`

- **复用现有日线链**：`app.services.signals.daily_adjusted_history()` →
  Massive `ticker_range(adjusted=True)` 优先，空则 Yahoo `history(auto_adjust=True)`。
  两条路都是复权收盘，不会混用未复权与复权价格。
- 不新增 Alpha Vantage，不新增股票 API Key，不建立第二套 Provider 框架。
- 实际服务的 Provider 记录在 `macro_etf_observations.provider`（来自
  `frame.attrs["price_provider"]`）。
- 直接调用后端内部 Service，**不**通过 HTTP 回调自身 `/api/*`。
- 只使用**已完成交易日**：当日收盘（含提前收盘日）之前的当天 K 线一律排除。
- 单个 ETF 缺失只影响依赖它的因子。
- 陈旧阈值按**交易日**计（5 个交易日），与按自然日计的 FRED 系列区分开。

相对收益公式（单位百分点）：

```
relative_return_63d = 100 × [ ln(A_t / A_{t-63}) − ln(B_t / B_{t-63}) ]
```

要求两只 ETF 至少有 **64 个共同有效交易日**（t 与 t−63 各占一端）；不足则该因子缺失，
不退化成更短的窗口。任一价格 ≤ 0 时也判缺失。

---

## 6. 服务端密钥

新增 `FRED_API_KEY`，与既有密钥完全同一套流程：

- `./personal.sh secrets set FRED_API_KEY`（从标准输入读，永不回显）
- `./personal.sh secrets status` → `{"FRED_API_KEY": {"configured": true}}`
- `./personal.sh secrets remove FRED_API_KEY`
- 格式本地校验：FRED 签发的是 32 位小写字母数字；`validate` 只做本地格式与文件权限
  检查（`local_validation_only`），不把 Key 放进任何 URL。

必须同步的五面镜子（漏一处 CI 或 CLI 就拒）：

1. `personal.sh` 的 `select_affected_services()` case 连续字面量
2. `backend/app/tools/personal_secrets.py::SECRET_KEYS`
3. `backend/app/legacy_env_adapter.py::SECRET_KEYS`
4. `tests/test_personal_secrets.py` 的精确集合断言
5. `tests/test_personal_secrets.py` 的 shell 连续字面量断言

外加 `secrets.env.example`、`backend/app/config.py`、`backend/app/api/settings.py`、
`tests/test_personal_compose.py` 的模板清单。

Key 变化时只重建/重启 `backend` 与 `worker`。Key 绝不写入 `personal.toml`、
`machine.env`、HTML、JavaScript、Cookie、localStorage、sessionStorage、日志、
错误响应或 GitHub Artifact。
