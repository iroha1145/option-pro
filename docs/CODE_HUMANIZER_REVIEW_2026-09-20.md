# Code Humanizer 审查记录 · 2026-09-20

审查对象：提交 `bcd80f1ddfe96c53890f972b3028622be1dbb39a`（`main`，#178）。  
审查工具：[code-humanizer](https://github.com/LeonardNJU/code-humanizer) v0.2.0，**Mode A（扫描 → 只出报告）**。  
审查范围：`backend/app`、`frontend-src/src`、`tests`、`scripts`、配置与部署边界。不包含 `node_modules`、`.venv`、已构建的 `frontend/` 产物本身。

**本次只给意见，未改代码、未进入 Mode B 清理。** 行为保全优先：潜在 bug 只记录，不建议在清理提交里顺手改报错类型或时机。结论来自当前工作树与本环境实测，不能解释为线上已经有问题，也不能证明所有 slop 都已穷尽。

严重度：`0` 无 · `1` 有理由豁免（不要动） · `2` 轻债 · `3` 明确抬维护成本 · `4` 伤语义或架构边界。  
行为风险：`none` · `error-type` · `needs-maintainer`。

---

## 1. Oracle（测试裁判）

| 套件 | 结果 | 含义 |
|------|------|------|
| Python `pytest` | **3729 passed / 19 failed / 6 skipped**（约 5 分 38 秒） | 套件存在且大体覆盖核心域，但本环境未全绿 |
| 前端 `frontend-src/tests/*.test.mjs` | **1060 passed / 2 failed / 1 skipped** | 行为契约测试健康；2 个失败依赖已构建的 `dist/` |

19 个 Python 失败高度集中：`test_chart_contract`、`test_api_data_states` 自选、`test_price_data_staleness`、`test_backend_calculations` 历史重试。本环境 `MASSIVE_API_KEY` 已设置，而 `_stock_chart_impl` **先走 Massive、失败才回落 Yahoo**：

```python
# backend/app/api/stocks.py:3686 附近
# 主源 Massive；失败/未配置/不支持的代码回落 Yahoo。
hist = _massive_chart_history(...)
if hist is None or hist.empty:
    tk = yf.Ticker(...)
    hist = tk.history(...)
```

`tests/test_chart_contract.py` 只 `monkeypatch` 了 `stocks.yf.Ticker`。Massive 一通就返回上千根真 K 线，断言 `len == 3` 失败。这不是「代码坏了」，是 **测试没封住主源**。

前端 2 个失败是对 `frontend-src/dist` 产物的断言（EPS recovery chunk、en/ja 字典不进 shared entry）。审查当轮未跑 `npm run build`，属环境缺口，不是 `src` 逻辑证据。

按 code-humanizer 铁律 2：**没有可靠全绿裁判，只能出报告，不能进入 Mode B 清理。**

---

## 2. 仓库画像

Optix Pro 是个人美股期权 / 突破 / 新闻工作台：`backend` 与统一 `worker` 同镜像，前端 Vite 源在 `frontend-src/`，提交物在 `frontend/`。CI 很重（pytest、前端契约、Playwright、镜像边界、WAL、离线 smoke）。

**这不是典型 vibe-coding 屎山。** 大量模块有版本戳、缺失即缺失（不填中性 50）、公开读路径禁冷拉、信任边界测试写得很凶。code-humanizer 的招牌破绽在这里主要是：

1. **SSOT 已经建了，但后写的代码又抄了一份。**
2. **对外供应商路径上的宽泛吞异常。**

防御性探测链、try-import 双栈、`_v2` 双活实现、测试里 `assert_called` 并不是主画像。

最大的结构压力是神文件，不是抽象层泛滥：

| 行数 | 文件 |
|------|------|
| 8552 | `tests/test_catalyst_local_intelligence.py` |
| 7333 | `backend/app/services/catalysts/local_intelligence.py` |
| 4315 | `backend/app/api/stocks.py` |
| 4242 | `backend/app/services/breakouts/repository.py` |
| 3273 | `backend/app/worker/tasks.py` |

---

## 3. Helper 索引（Pattern 1 对照表）

已有、应优先复用的中枢：

| 域 | 位置 |
|----|------|
| 日历 / 时段 | `services/market_calendar.py`（`last_completed_trading_day`、`previous_trading_day`、`market_datetime`）；`breakouts/clock.py` 的 `MarketClock` |
| 符号 | `services/symbols.quote_symbol`；Massive 映射 `massive.to_symbol`（厂商域，豁免合并） |
| 数值 | `quote_quality.finite_number` / `vendor_iv`；`strength/scoring.finite_number` / `weighted_available`；`signals.clamp` |
| 缓存 / IO | `cache.TTLCache`、`snapshot_read_cache.FingerprintedFileCache`、`http_read_cache`、各处 `mkstemp + os.replace` |
| 算法身份 | `algorithm_modes` + `ranking_variants`（拆得干净） |
| 前端格式 / 重试 | `lib/format.ts` + `numericFormat.ts`、`retryDelay.ts`、`boundedReadRetry.ts` |
| 前端读取 | `api/live.ts` 的 `num` / `str` / `pick*`、`quoteSymbol.ts`、`queryRegistry.ts` |

后面所有「重复实现」都对照这张表。

---

## 4. Findings（按 16 条 pattern）

每条格式：`pattern · 位置 · 严重度 · 证据 · 建议 · 行为风险`。

### Tier 1 — 重复与重造

#### Pattern 1 · 重复实现已有 helper（本仓主债）

- **1** · `backend/app/services/macro_conditions/market_proxy.py:32` · **3** · `last_completed_trading_day` 与 `market_calendar.py:149` 几乎逐行相同（14 步回走、早收、16:00） · 只从 `market_calendar` 导入 · **needs-maintainer**（macro 已从本地副本进口，两份会分叉）
- **1** · `backend/app/services/breakouts/feature_engine.py:53` · **3** · `_previous_trading_day` 再实现 `previous_trading_day`，且是 **无上限 while** · 改为调用日历 helper（15 步上限 + `RuntimeError`） · **error-type**（日历坏了这里会死循环）
- **1** · `backend/app/api/market.py:312` vs `backend/app/services/breakouts/clock.py:73` · **3** · `market_status()` 与 `MarketClock.snapshot` 各自实现盘前/盘中/盘后/周末/假期 · 抽共享 session 相位 · **needs-maintainer**（前端已有两套 mapper，后端再分叉等于三套真相）
- **1** · `backend/app/api/market.py:187` · **2** · `_last_settled_trading_date` 在完成日上再加 +30 分钟缓冲 · 给日历 helper 加可选 `close_buffer_minutes` · **needs-maintainer**（CTA「已结算」语义）
- **1** · `backend/app/services/breakouts/clock.py:45` · **2** · `_at_minutes` 复制 `market_datetime` · 直接调用 · **none**
- **1** · `backend/app/services/breakouts/scoring.py:24` vs `backend/app/services/strength/scoring.py:58` · **3** · `weighted_score` / `weighted_available` 同一套缺失重配权（`min_active_weight=0.25`、confidence、missing 列表），返回类型已分叉 · 抽 `weighted_aggregate`，域包装保留 · **needs-maintainer**
- **1** · `backend/app/services/scoring.py:61`、`backend/app/services/strength/market_regime.py` 的 `_weighted_available` · **2** · 第三、第四份加权聚合 · 文档标 legacy 或并入核心循环 · **needs-maintainer** / **error-type**
- **1** · 多处 `finite` / `_safe_float` / `_clamp`（`quote_quality`、`signals`、`strength/*`、`public_home_snapshot`、`t1_priority`） · **2** · 边界不同：`marketdata._clamp(None)=50`，`signals.clamp(None)=0` · 共享 numeric，**禁止盲合并** · **error-type**
- **1** · `backend/app/services/signals.py:95` + `backend/app/services/yahoo.py` 私有 TTL · **2** · 与 `TTLCache` 平行的 dict+锁 · 抽同步 KeyedTTL / 让 yahoo 保留 provenance 元组 · **needs-maintainer**
- **1** · `backend/app/stock_pull_snapshot.py` 文件身份缓存 · **3** · 与 `FingerprintedFileCache` 同形；`snapshot_read_cache` 注释已点名 stock_pull · 迁到共享指纹缓存 · **needs-maintainer**
- **1** · ticker 正则散落（breakouts / accounts / signals / catalysts / stock_pull / realtime / public_home） · **2** · 严格度故意不同，但没有兼容矩阵 · 先写矩阵再抽组合子 · **needs-maintainer**
- **1** · `frontend-src/src/components/breakouts/LeadBigCard.tsx` 本地 `num`/`str` · **2** · 复述 `api/live.ts` · 改为导入 · **none**
- **1** · `frontend-src/src/components/breakouts/HistoryRail.tsx` 私有 `hhmm` · **2** · 复述 `fmtNyHHmm` · 复用 format · **none**
- **1** · `frontend-src/src/api/modules/market.ts:62` vs `frontend-src/src/components/market/api.ts:25` · **3** · 两套 session 归一化（`SESSION_MAP` vs `normalizeMarketState`），别名集合不完全相同 · 一个 mapper · **needs-maintainer**（`premarket`/`postmarket` vs 连字符契约）
- **1** · `frontend-src/src/lib/chart.ts:438` vs `frontend-src/src/lib/strengthColor.ts` · **2** · 85/70/50 阈值两份 · 一张表两个渲染器 · **none**

#### Pattern 2 · `_v2` / `_new` / `_impl` 克隆

- **2** · 后端 SQLite `_migrate_v2` / `pre_v2_snapshot` / `*_v3_new` · **1** · 有文档的迁移，不是双活实现 · 保持 · **none**
- **2** · 路由 `_stock_chart_impl` / `_stock_overview_impl` · **1** · worker 与 HTTP 共用的单实现，不是克隆 · 保持 · **none**
- **2** · 前端 `api/modules/market.ts` + `components/market/api.ts` · **3** · 注释写脚手架「不可改动」，于是再写一套网关 · 收敛 mapper，或正式废弃脚手架约束 · **needs-maintainer**
- **2** · `api/modules/catalysts.ts` vs `components/catalysts/api.ts` · **2** · 催化剂 API 双门面 · 归一化只留一处 · 低

#### Pattern 3 · 重造标准库 / 已装依赖

- **3** · `backend/app/services/signals.py` 手写 OrderedDict TTL · **2** · 已有 `TTLCache` · 复用或抽同步版 · **needs-maintainer**
- **3** · `backend/app/services/yahoo.py` 私有缓存 · **1** · 带 `fetched_at`/stale 元组，不是 drop-in · 豁免 · **needs-maintainer**
- **3** · 前端相对时间手写 · **1** · UI 文案域，可抽 `diffMinutes` · **none**

### Tier 2 — 投机性架构

#### Pattern 4 · 单实现抽象

- **4** · `backend/app/services/breakouts/protocols.py` 的 `DiscoveryProvider` + 仅 TradingView · **1** · 可替换端口；config 已 `Literal["tradingview"]` · 第二个 provider 到来前保持 · **none**
- **4** · `backend/app/services/macro_conditions/registry.py` · **1** · 真注册表（30 factor / 7 module），启动 `validate_registry()` · 豁免 · **none**
- **4** · `backend/app/services/technical/layer_registry.py` + 前端 `registry.ts` · **1** · 双边图表层，有 parity 测试 · 豁免 · **none**
- **4** · `frontend-src/src/api/sharedRead.ts` 一行转发 · **1** · 稳定导入边界 · 豁免 · **none**

#### Pattern 5 · 死代码 / 备将来用

- **5** · `frontend-src/src/lib/chart.ts:438` 的 `strengthColor` · **2** · 导出后 UI 全走 `strengthBarClass`，无生产引用 · 删或接到图表 · **none**
- 后端抽查的 export 都有调用点（含脚本/测试）。未发现「flexible/extensible」空 helper 成片。

#### Pattern 6 · 零增值 wrapper

- **6** · `ExistingStrengthAdapter` · **1** · 有 DTO 映射、禁期权、range-persistence 策略 · 豁免 · **none**
- **6** · `LeadBigCard` 的 `const fmtEventTime = fmtNyEventTime` · **1** · 别名噪音 · 直接用原名 · **none**

#### Pattern 7 · Config / API 蔓延

- **7** · `BreakoutSettings` 大表面 · **1** · 单 provider 下可接受 · 豁免到第二后端 · **none**
- 未发现「全局 flag 只被一处读取、且无域边界」的典型 AI 旋钮。个人版把模型/并发钉死在 toml，这是纪律，不是蔓延。

### Tier 3 — 防御性屎感

#### Pattern 8 · 宽泛吞异常（密度最高、风险最大）

`backend/app` 约 **280** 处 `except Exception`，**0** 处裸 `except:`。最密：

| 文件 | 约计数 |
|------|--------|
| `api/stocks.py` | 32 |
| `worker/tasks.py` | 18 |
| `services/breakouts/worker.py` | 18 |
| `services/breakouts/repository.py` | 18 |
| `api/earnings.py` | 18 |
| `services/strength/scanner.py` | 15 |
| `services/catalysts/personal_service.py` | 15 |

**应报（3–4）——静默把崩溃变成错数据：**

- **8** · `backend/app/services/breakouts/worker.py:242-245` · **4** · `overlay_t1_evaluations` 失败直接 `pass`，扫描当成功 · 打日志 + 降级旗标，禁止静默 · **needs-maintainer**（生产 T1 排序可错、健康检查仍绿）
- **8** · 同文件 `493-495`、`779-780`、`907-909` · **3–4** · overlay / persist / clear 同一 `pass` 族 · 持久化失败必须可见 · **needs-maintainer**
- **8** · `backend/app/api/earnings.py:202-267` · **4** · `_collect_dates` / `_calendar_get` / `_table_value` 对 pandas/yfinance 形状一律 `pass`/`None` · 收到 `KeyError`/`TypeError`/`AttributeError` 并打 debug · **needs-maintainer**（财报日历变空，看起来像「没有财报」）
- **8** · `backend/app/api/stocks.py:1374` · **3** · 解析昨收整段 `except Exception: pass` · 收窄供应商异常并计数 · **needs-maintainer**（自选涨跌幅被扭曲）
- **8** · `backend/app/api/stocks.py:2445` · **3** · `_fetch_quotes` 外层失败返回 `{}, [], []`，再被 `<30%` 守卫抬成 `RuntimeError` · 保留部分成功结果 + 记异常类型 · **needs-maintainer**
- **8** · `backend/app/api/stocks.py:3159` · **3** · overview 的 `yf.Ticker` 整块 `pass` · warning + `quote_provider_status` · **needs-maintainer**
- **8** · `backend/app/api/stocks.py:2926` · **3** · macro 导入失败原样返回 payload，**不写** `macro_shadow_status`（对比 2964 路径） · 与运行失败路径对齐 · **needs-maintainer**
- **8** · `backend/app/services/signals.py:257-290` · **3** · Massive 批与 yfinance 批失败都 `pass` · 记 `history_batch_failures` 再走单票 · **needs-maintainer**
- **8** · `backend/app/services/strength/scanner.py:385+` · **3** · Finnhub/Stooq 失败变 `{}` · 区分 429 与解析错误 · **needs-maintainer**
- **8** · `backend/app/api/worker_actions.py:239-240` · **3** · 算法解析失败 **退回 raw_parameters** · 应 400/503，不要用未解析算法去 hash · **needs-maintainer**（UI 显示的算法 ≠ worker 执行的算法）
- **8** · `backend/app/worker/tasks.py:1907` · **3** · `asyncio.shield` 后非取消异常 `pass` 再 harvest · 只忽略预期取消 · **needs-maintainer**
- **8** · `backend/app/services/yahoo.py:58-59` · **2** · 签名自省失败仍打 monkey-patch · 自省失败应跳过 patch · **error-type**

**豁免（1）——边界上有回滚/映射/日志：**

- `breakouts/repository.py`：`rollback; raise`
- `catalysts/personal_service.py`：`_is_local_store_error` 过滤后重抛
- `worker/tasks.py` ETL：写入 `errors[stream]`
- `yahoo.py` 缓存加载：带 stale provenance
- `cache.py` 体积估算失败
- 前端 `api/client.ts` 解析错误体、SSE 解析、prefetch `.catch`：信任边界 / 预取

#### Pattern 9 · 无理由 try-import

- **9** · `backend/app/personal_config.py` 的 tomllib/tomli · **1** · 版本门 · 豁免
- **9** · `backend/app/services/yahoo.py` 的 curl_cffi · **1** · 有 warning · 豁免
- **9** · `api/breakouts.py` / `api/stocks.py` / `scanner.py` 对 macro 模块 `except Exception` 当导入失败 · **2** · 只捕 `ImportError`；DB/运行错误不要伪装成「没装模块」 · **needs-maintainer**

#### Pattern 10 · 属性探测链

- **10** · `backend/app/api/earnings.py:120-267` · **3** · `hasattr(iloc/get/loc/tolist)` + 吞异常，同时接 dict / DataFrame / Series · 用 `isinstance` 正规化成一张表 · **needs-maintainer**
- **10** · `backend/app/api/stocks.py:3162` 的 `_fast_value` · **2** · `getattr` + 宽 except · 只捕 `AttributeError` · 低
- **10** · `backend/app/services/catalysts/local_intelligence.py` 长 `isinstance(dict)` 梯 · **2** · 发布边界应上 pydantic · 低
- **10** · `backend/app/services/breakouts/worker.py` 大量 `getattr(settings, name, default)` · **2** · 拼错字段名会静默走默认 · 用类型化 settings · **needs-maintainer**
- **10** · 前端 `pick()` 多形状错误体 · **1** · 契约容忍，豁免

#### Pattern 11 · 偏执重校验

- **11** · `backend/app/api/stocks.py` 构建后再 `validate_stock_pull_payload` · **2** · 双拒可能把合法内部 payload 打成 503 · 校验放在落盘/出站一次 · **error-type**
- **11** · yahoo 缓存 `is_valid` 再检查 · **1** · 过期契约，豁免

### Tier 4 — 噪音

#### Pattern 12 · 复读注释

- **12** · `backend/app/api/sectors.py:168`、`backend/app/api/ai.py:261`、部分 breakout 卡片 · **2** · 复述实现或「这次改动保证正确」 · 只留不变量 · **none**
- **12** · `backend/app/worker/tasks.py` 引用具体测试名 · **1** · 取消语义契约，豁免
- 大量 audit 长注释（queryRegistry、format、strength） · **1** · 多数在讲 *why*，不是复读

#### Pattern 13 · 模板 docstring

- **13** · `api/earnings.py` / `api/strength.py` 若干「Return the …」 · **2** · 函数名空格化 · 改写前置条件（时区、副作用） · **none**
- 多数模块头是约束文档，不是 *robust/seamless* 营销腔。

#### Pattern 14 · 死 import / 装饰横幅 / debug

- **14** · `backend/app/services/macro_conditions/registry.py` 的 `# --- section ---` · **2** · 装饰横幅 · 可删 · **none**
- **14** · `backend/app/services/breakouts/research.py` 模块内 `print(json.dumps)` · **2** · 若被 worker 调会污染 stdout · 收到 `__main__` · **none**
- **14** · 前端 **15+** 个 `api/modules/*` 静态 `import '@/mocks/fixtures*'`；`Home.tsx` / `IndexCards` / `catalysts/api` 从 mocks 再导出类型 · **3** · live 运行时有 `isMock` 门，但 **打包图绑死 mock** · 类型迁到 `api/types`，mock 改为动态 import · 功能上 **none**，维护上是体积与耦合
- 未发现生产路径 emoji 标识符或成片遗留 `console.log`。

### Tier 5 — 测试屎感（默认只报告）

#### Pattern 15 · 断言 mock

Python 几乎不用 `unittest.mock.assert_called*`。形态是手写 `calls.append` + 全员 `monkeypatch`。

- **15** · `tests/test_public_home_snapshot.py` · **3** · 大量 `assert calls == [...]` · 断言落盘字节 + HTTP 体 · 编排对、语义错仍绿
- **15** · `tests/test_personal_worker.py`（约 3.4k 行） · **3** · 71 处 monkeypatch，成功常等于「任务名跑了 N 次」 · 对 `DATA_DIR` 做金样 · 真实仓库未练到
- **15** · `tests/test_catalyst_local_intelligence.py` · **3** · 280 处 patch，不少用例断言薄 · 按子系统拆，测不变量 · 高回归风险
- **15** · `tests/test_public_snapshot_boundary.py` / `tests/test_gateway_resource_bounds.py` · **1** · 同样「一碰 provider 就 fail」——这是信任边界，**豁免**
- **15** · 本环境 19 个失败 · **3** · 只 stub Yahoo、不 stub Massive · 测图表/自选必须封主源或强制 `configured()=False` · **needs-maintainer**（隔离债，不是产品 bug）
- 前端测试几乎不 mock 协作对象 · **0**

#### Pattern 16 · 琐碎 / 重复断言

- **16** · `tests/test_catalyst_local_intelligence.py`（8552 行 / 142 tests） · **4** · 神测试文件 · 按 ETL / focus / job 拆 · 评审疲劳 + 场景分叉
- **16** · `tests/test_api_data_states.py` 三条 503 骨架克隆 · **3** · parametrize · 一端改形状另两端过期
- **16** · `tests/test_catalyst_etl_client.py` vs `tests/test_catalyst_etl_sync.py` · **2** · 重试/鉴权体复制 · 共享 support
- **16** · `tests/test_audit_*` / `tests/test_pr*` / `tests/test_remediation_*`（约 14 个）与领域套件重叠 · **2** · 票关闭后折进领域文件 · 阈值互相打架
- **16** · `tests/test_ai_jobs.py` 连续两行 `assert calls == 1` · **2** · 删一行 · **none**
- **16** · `frontend-src/tests/audit-state-20260912.test.mjs` 自带 vm harness · **2** · 抽 `_harness.mjs` · harness 漂移
- 字面量金样（密钥 allowlist、i18n、layer registry） · **1** · 护栏，豁免

高质量对照（不要当 slop 清掉）：

- `tests/test_public_snapshot_boundary.py`
- `tests/test_read_cache_behaviors.py`
- `tests/test_market_shape_point_in_time_replay.py`
- `tests/test_technical_structure_rigor.py`
- `tests/test_breakout_api_contract.py`
- `tests/test_chart_contract.py`（意图对，隔离不够）
- `frontend-src/tests/zero-vs-missing-contract.test.mjs`

---

## 5. 按文件汇总（高分优先）

| 文件 | 主要 pattern | 大致 severities | 一句话 |
|------|----------------|-----------------|--------|
| `backend/app/services/breakouts/worker.py` | 8, 10 | 4+3+3 | T1 overlay 静默失败，生产排序可错 |
| `backend/app/api/earnings.py` | 8, 10, 13 | 4+3+2 | yfinance 形状探测把「坏数据」做成「没数据」 |
| `backend/app/api/stocks.py` | 1, 8, 9, 11 | 3 簇 | 神路由 + 自选/overview 吞异常 + Massive/Yahoo 双源 |
| `backend/app/api/worker_actions.py` | 8 | 3 | 刷新动作可能用未解析算法 |
| `backend/app/services/signals.py` | 1, 3, 8 | 3+2 | 批历史 `pass` + 私有 TTL |
| `backend/app/services/macro_conditions/market_proxy.py` | 1 | 3 | 日历 SSOT 被复制 |
| `backend/app/services/breakouts/feature_engine.py` | 1 | 3 | 无界交易日回走 |
| `backend/app/services/breakouts/scoring.py` + `strength/scoring.py` | 1 | 3 | 缺失重配权双实现 |
| `backend/app/api/market.py` | 1 | 3 | session 第三套实现 |
| `backend/app/stock_pull_snapshot.py` | 1 | 3 | 指纹缓存分叉 |
| `backend/app/services/strength/scanner.py` | 8, 9 | 3 | 供应商失败变空宇宙 |
| `backend/app/worker/tasks.py` | 8 | 3 | shield 后吞非取消异常 |
| 前端 market 双网关 | 1, 2, 14 | 3 | session 映射 + mock 类型泄漏 |
| `frontend-src/src/api/modules/*` mock 静态导入 | 14 | 3 | live bundle 绑死 fixtures |
| `tests/test_catalyst_local_intelligence.py` | 15, 16 | 4 | 最大测试债 |
| `tests/test_public_home_snapshot.py` / `tests/test_personal_worker.py` | 15 | 3 | 编排计数当正确性 |
| `frontend-src/src/lib/chart.ts` | 5, 1 | 2 | 死导出 + 阈值重复 |

相对干净：`data_paths.py`、`market_calendar.py`（只要别人真用它）、`quote_quality.py`、`algorithm_modes.py` + `ranking_variants.py`、`http_read_cache.py`、`finnhub_budget.py`、`worker/lock.py`、前端 `retryDelay` 链、`watchlistSort.ts`、`personalWatchlist.ts`、`scripts/`、`personal.sh` / `setup.sh`。

---

## 6. 豁免清单（severity 1，不要为击杀数去改）

- 公开读 / 网关：禁止冷访问供应商（`test_public_snapshot_boundary`）
- 账户 vs 突破 vs Yahoo 的 ticker 语法：信任边界不同
- `massive.to_symbol` vs `quote_symbol`：厂商映射
- 真插件 / 真注册表：macro factors、chart layers
- 文档化 schema 迁移：`_v2` / `_v3_new` / `pre_v2_snapshot`
- SQLite `rollback; raise`、催化剂 store-error 过滤
- yahoo stale 缓存元组、curl_cffi 降级日志
- `tomllib` / `tomli`
- 前端三套读缓存（queryRegistry / marketRead / boot prefetch）：作用域不同且有注释
- `boundedReadRetry` vs `retryDelay`：语义不同（max(local, Retry-After) vs 阶梯）
- CLI `print(json.dumps)`
- 探索性 `scripts/perf` 百分位小复制

---

## 7. 潜伏行为问题（只报告，清理时禁止「顺便修」）

1. **T1 overlay `pass`**：carryover / 收盘评估失败仍发布事件，雷达排序可错且看起来健康。
2. **财报探测链**：yfinance 列改名 → 空日历，无服务端信号。`_coerce_date` 对 `value > 1_000_000` 才当时间戳，`20250920` 这类整数会当「无日期」。
3. **macro 导入失败 vs 运行失败**：影子字段有无不一致，前端可能把「模块不可用」当成「分数就是空」。
4. **worker action 算法**：解析失败仍用 raw，手动刷新可能和 GET `/strength/scan` 不是同一算法。
5. **图表 / 自选测试在 Massive 配置下会绿变红**：隔离不足；也说明生产路径「只 mock Yahoo」的测试不能代表主源。
6. **watchlist 外层 except 清空 quotes**：再叠加 30% 守卫，边界批次可能整表失败或整表脏发布。
7. **`yahoo.py` 自省失败仍 patch**：少见但会变成全局 Ticker 初始化故障。

这些都带行为风险。铁律 1：不要在 deslop 提交里改异常类型或时机。

---

## 8. 全方位补充意见

### 架构

边界清楚：toml 管行为、`machine.env` 七项、`secrets.env` 八钥、compose 必须走 `scripts/compose.sh`。`frontend/` 是构建产物，源在 `frontend-src/`——这是有意的双目录，不是克隆；改 UI 不重建再提交会让 CI `diff -r` 红。根目录 `app/` 只是指向 `backend/app` 的桥。

### 神文件

`local_intelligence.py` + 其 8.5k 测试、`api/stocks.py`、`breakouts/repository.py`、`worker/tasks.py` 才是长期维护税。它们不完全是 AI 模板，但是 **agent 最容易继续往里堆** 的地方。拆分应按域（财报/自选/图表、T1/发布、任务种类），不要为「好看」抽单实现 ABC。

### 前端

`lib/` 纪律好于页面本地 helper。真正的 AI 味是「功能往外写」：卡片自写 `num`/`str`、market 双网关、类型住在 `mocks/`。i18n 两份约 3149 行字典是产品成本，不是 slop。Screener / Kline / drawings 大组件偏胖，但有契约测试托底。

### 安全与产品纪律（优点，不是债）

访问模式校验、body 上限、同源写、访客只读快照、模型钉死 `gpt-5.6-terra` / `max` / 并发 1、预算预留——这些偏执是正确的信任边界，Pattern 8/10/11 在这里多数该留。

### 文档

`docs/` 审计备忘极多（REVIEW / AUDIT_FIXES / performance r5–r7）。说明人工 review 密度高，也解释了为什么「吞异常约 280 处」里很多是有意降级，而不是模型乱写 `pass`。

### CI vs 本地 Oracle

CI 假设依赖锁、前端产物字节一致、容器离线 fixture。本环境 Massive 密钥让一部分「假想 Yahoo 是唯一源」的测试失真。这是测试设计问题，不是本次回归。

---

## 9. 建议修复顺序（若以后批准 Mode B）

只建议，不执行。一类 pattern 一个 commit，每步全绿。有行为风险的单独 `[BEHAVIOR]`。

1. **先补裁判**：图表 / 自选 / 价格新鲜度测试同时封 Massive（或强制未配置）；再谈清理。
2. **Pattern 8 行为项（单独、需维护者签字）**：`breakouts/worker.py` T1 overlay；`worker_actions` 算法解析；`earnings` 探测链日志化（先不要收窄到改变对外 status）。
3. **Pattern 1 日历**：删 `market_proxy.last_completed_trading_day`；`feature_engine` 改走 `previous_trading_day`；评估 `market_status` vs `MarketClock` 合并（行为对齐要测）。
4. **Pattern 1 加权分**：breakout / strength 抽共享聚合，域结果类型保留。
5. **Pattern 1 / 14 前端**：session mapper 合一；mock 类型迁出 `fixtures*`；删或接通 `strengthColor`。
6. **Pattern 16**：拆 `test_catalyst_local_intelligence.py`；合并 audit / remediation 重叠。
7. **噪音 12–13**：只在已打开的文件里顺手删，不要单独开清注释大战。

不要做：

- 为击杀数合并 clamp（`None→0` vs `None→50` 会改分数）
- 把公开读的宽捕改成抛
- 删「未使用」但被脚本 / 反射碰到的 helper

---

## 10. 总判

| 维度 | 意见 |
|------|------|
| 整体健康 | 中上。契约、版本戳、缺失语义、部署边界明显是人审过的。 |
| 最像 AI 的债 | **复制已有 helper**（日历、加权分、指纹缓存、前端 coerce/session），不是 registry / `_v2`。 |
| 最危险的债 | **静默 except**（T1、财报、自选、worker 算法），把可见失败做成错误行情 / 错误排序。 |
| 测试 | 数量与质量都强，但有神文件、编排计数、以及 **主源 Massive 未纳入假对象**。 |
| 前端 | 工具层收敛好；页面 / 网关仍向外重写；mock 进生产依赖图。 |
| 现在能不能 deslop | **不能。** Oracle 本环境 19 红 + 多项行为风险未签字。按 skill：只出这份报告。 |

若要进入 Mode B，需要明确批准，并指定先做哪一类 pattern。清理提交里不应改对外错误类型，也不应「顺手修」第 7 节的潜伏 bug。
