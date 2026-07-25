# Optix 宏观环境 · 现状集成地图

本文件是编码前对**执行时最新 main** 的真实审计结果，不引用任何旧审查报告里的路径。
后续所有实现都以本文件记录的真实事实为准。

- 起始 `origin/main` SHA：`603a7c733d1d5210464abe493ba72a390faffa38`
- 分支：`feat/optix-macro-conditions`
- 审计时工作树：干净（唯一未跟踪目录 `frontend-src/src/i18n/` 属其他会话在写，本任务不触碰、不提交）

---

## 1. 当前 Worker Task 清单（真实值，不是历史值）

`backend/app/worker/tasks.py::DEFAULT_TASK_NAMES` 当前 **12 项**：

| 任务 | 类型 | 调度 |
| --- | --- | --- |
| `breakout` | 定时 | `breakout.regular_seconds` |
| `catalyst_sync` | 定时 | `catalyst.sync_seconds` |
| `focus` | 定时 | `catalyst.focus_seconds` |
| `ai_jobs` | 定时 | 2s，`drain_on_shutdown` |
| `maintenance` | 定时 | 21600s（备份） |
| `stock_directory` | 定时 | 86400s（需 `MASSIVE_API_KEY`） |
| `public_home` | 定时 | `public_home.poll_seconds`（仅 password 模式） |
| `earnings_analysis` | 定时 | `public_home.earnings_seconds` |
| `focus_refresh` | `manual_only` | — |
| `strength_refresh` | 定时 86400s + 手动 | — |
| `breakout_refresh` | `manual_only` | — |
| `retention` | `manual_only` | — |

> README.md 与 `docs/personal-edition/*` 仍写「十项任务」，属既有漂移（缺 `stock_directory`、
> `earnings_analysis`）。本任务按「当前实际清单 + `macro_conditions`」修正被触及的段落。

**Inventory 的镜子（新增任务必须同步的位置）**

1. `backend/app/worker/tasks.py`：`DEFAULT_TASK_NAMES`、`build_default_tasks`、`__all__`
2. `backend/app/worker/__main__.py`：`--healthcheck` 直接读 `DEFAULT_TASK_NAMES`（自动跟随）
3. `.github/workflows/ci.yml`：就绪检查里硬编码的 `expected={...}` 集合
4. `tests/test_personal_worker.py`：`SCHEDULED_TASK_NAMES` / `MANUAL_TASK_NAMES`
5. `tests/test_breakout_deployment.py`、`tests/test_personal_compose.py`、`tests/test_shell_workflows.py`
6. `tests/test_worker_actions_api.py`：动作类型清单
7. `README.md`、`docs/personal-edition/{current-inventory,architecture,migration,pr2-migration}.md`、
   `docs/breakout-radar/operations.md`
8. `frontend-src/visual-tests/password-mode.spec.mjs`（worker status fixture 的 tasks 数组）

**运行时机制（实现依据）**

- `TaskSpec(name, runner, interval_seconds, …)`，`TaskResult(status, details, next_delay_seconds, error_code)`。
  `status ∈ {idle, paused, disabled, degraded}`，`next_delay_seconds ∈ [0, 86400]`。
- 非 `manual_only` 的定时循环用 `result.next_delay_seconds` 作为下一次延迟，
  且 `_wait_for_next()` **每 0.5s 轮询一次待处理手动动作**——因此「长间隔定时 + 手动即时唤醒」
  可以由同一个任务承担，无需第二个任务名。
- 手动动作：`WorkerStateRepository.request_action()` 原生实现幂等键复用、
  `already_running`、`cooldown` 三种复用语义；`claim_actions` → 若 runner 有
  `run_for_actions` 就调用它，否则调用 `runner()`。
- 健康：`health(expected_tasks=DEFAULT_TASK_NAMES)`。`degraded` 只统计
  `enabled and status ∈ {degraded, failed, interrupted}`——**返回 `disabled` 的任务不会让 worker unhealthy**，
  这正是 FRED Key 缺失时应走的路径。

---

## 2. 当前市场页面结构

`frontend-src/src/pages/Market.tsx`（159 行，注释里自带区块编号）：

```
B0 PageHeader（section="MKT"，SessionLED + 更新时间）
B1 指数概览 6 卡        IndexCards        indices 60s
B2 市场状态 + B3 形态六维  StatusCard / RegimePanel   status 60s / regime 300s
B4 信号解读 + B5 强度分布  SignalsReading / BreadthHistogram  300s
B6 联动卡              LinkCards
SourceNote
```

宏观面板插入位置：**B3 之后、B4 之前**，成为新的 B4，原 B4/B5/B6 顺延为 B5/B6/B7。

现有可复用件（全部已核对签名）：

- `components/shared/`：`PageHeader`、`EmptyState`、`InfoHint`、`SourceNote`、
  `Skeleton{Block,Card,Text,Rows}`、`ChangeBadge`、`StrengthBar` + `strengthBarClass`
- `components/charts/ReactECharts.tsx`（`option/className/style/onClick/ariaLabel`）
- `lib/chart.ts`：`baseGrid`、`categoryAxis`、`valueAxis`、`glassTooltip`、`CH` 调色、
  `strengthColor`；echarts 已 `use([LineChart, BarChart, …, MarkLineComponent])`——
  50 中性参考线用 `markLine` 无需新增组件
- `hooks/`：`usePolling(fetcher, intervalMs)`（页面隐藏暂停）、`useCountUp`（自带
  `prefers-reduced-motion` 分支）、`useAccess()` → `{ isOwner, isVisitor, … }`
- `lib/scoreHints.ts`：`SCORE_HINTS` 单一事实源 + `ScoreHint {title, body, note?}`
- `lib/utils.ts::cn`、`lib/format.ts`（`fmtPrice/fmtSigned/fmtPct/fmtCompact/…`）

**主题现状（重要）**：当前 SPA 是**单主题**「纸面终端 Paper Terminal」。
`tailwind.config.js` 写了 `darkMode: ["class"]`，但代码里没有任何地方给根元素加 `.dark`，
`index.css` 只定义了亮色 `:root`，仓库内也没有主题切换器；`dark:` 前缀只出现在未被业务
使用的 `components/ui/*` shadcn 基座里。
→ 因此「深色主题通过」的可验证含义是：**宏观组件零硬编码颜色、只用既有 token/CSS 变量**，
在 `prefers-color-scheme: dark` 下渲染不破版。新增一套暗色调色板会违反
「不新增独立 Design Token」，故不做。此点在最终报告中如实标注。

---

## 3. 当前公开读与 Owner 动作边界

`backend/app/main.py` 中间件（不是网关白名单的唯一依赖，路由自身仍带依赖）：

- `_PUBLIC_READ_API_PATHS`（精确路径集合）+ `_PUBLIC_READ_API_PATTERNS`（正则）
  → `_is_public_read_api_path()`
- `_is_public_read_request(path, method)`：GET/HEAD 走上面两者；POST 只放行
  `_PUBLIC_READ_POST_PATHS` 与两个具名判定函数
- 限速桶：`_is_cached_market_read_path()` 内部直接复用 `_is_public_read_api_path()`
  → **宏观 GET 加进 `_PUBLIC_READ_API_PATHS` 即同时进入 `_RL_MARKET_READ_LIMIT=200/60s` 桶**，
  无需另外改限速代码
- 路由注册：`_PUBLIC_READ_DEPENDENCIES` vs `_OWNER_DEPENDENCIES`

`backend/app/access.py` 提供 `require_owner_access`、`require_public_read_or_owner_access`、
`require_same_origin_action`、`require_same_origin_json`。
同源写入三重校验：`Origin==Host` + `sec-fetch-site` + `X-Optix-Action: 1`。

Owner 手动动作现有出口：`backend/app/api/worker_actions.py`
`POST /api/worker/actions/{action_type}`，`ActionType` 目前 5 种，
`_ACTION_TASKS` / `_ACTION_COOLDOWNS` 两张表。

---

## 4. 当前市场数据 Provider 路径

**Massive 优先 → Yahoo 回落**，已经存在，不再造第二套：

- `backend/app/services/signals.py`
  - `_massive_daily(symbol, period)`：`massive.ticker_range(..., adjusted=True)`，
    provider 标签写 `frame.attrs["price_provider"]="Massive"`
  - `_yahoo_history(symbol, period)`：`yf.Ticker().history(auto_adjust=True)`，标签 `"Yahoo/yfinance"`
  - `_history(symbol, period)`：Massive 空 → Yahoo，外层 300s TTL 缓存
  - `_MASSIVE_PERIOD_DAYS = {"1y":405,"2y":770,"6mo":200,"3mo":105,"1mo":40}`
    → **缺长周期档位**，8 年回填需要补一档（对现有调用者无行为变化）
- `backend/app/services/strength/scanner.py::_download_history`（选股/突破共用）
- `backend/app/services/market_calendar.py`：`is_trading_day`、`next_trading_day`、
  `early_close_minutes`、`market_datetime`、`ET` —— 判定「已完成交易日」的唯一事实源

两条路都是 `auto_adjust=True` / `adjusted=True`，**同为复权价**，不会混用。

---

## 5. Market Focus Prompt 与 Schema 位置

| 关注点 | 真实位置 |
| --- | --- |
| Prompt 版本常量 | `backend/app/services/catalysts/local_intelligence.py::FOCUS_PROMPT_VERSION = "market-focus-zh-cn-v5"` |
| 输入 payload 构造 | 同文件 `_enqueue_focus()`，`input_hash = _sha({"prepared_revision":…, "events":…})`，payload 含 `cycle_id/as_of/input_hash/prepared_revision/allowed_*/events/force` |
| 建任务 | 同文件 `_create_focus_job()` → `ai_repository.create_job(prompt_version=FOCUS_PROMPT_VERSION, schema_version, schema_sha256)` |
| Prompt 正文 + 输出 schema | `backend/app/services/ai_jobs/runtime.py::build_runtime_request()` 的 `job_type == "market_focus"` 分支（`schema_name = "market_focus_zh_cn_v5"`） |
| Prompt Cache Key | `ai_jobs/runtime.py::schema_identity()` = sha256(instructions + schema + 各上限)；`ai_jobs/repository.py::_request_hash(job_type, payload_json, model, reasoning, execution_mode, prompt_version, schema_version, schema_sha256)` |
| 输入校验 | `ai_jobs/models.py::validate_job_payload("market_focus", …)` |
| 输出校验 | `ai_jobs/models.py::validate_result` + `MarketFocusResult` |

**历史付费任务兼容性**：`create_job` 的复用查询同时匹配当前与四个 legacy request hash，
但都要求 `prompt_version` / `schema_version` / `schema_sha256` 相等。
→ 只要不改输出 schema 名，旧结果行原样保留、可继续读取；新输入产生新 job，
**不会重跑历史付费任务**。

---

## 6. Secret 管理的五面镜子

新增服务端密钥必须同步（漏一处 CI 或 CLI 就拒）：

1. `personal.sh` → `select_affected_services()` 的 `case` 连续字面量
2. `backend/app/tools/personal_secrets.py::SECRET_KEYS`
3. `backend/app/legacy_env_adapter.py::SECRET_KEYS`
4. `tests/test_personal_secrets.py::test_option_pro_secret_allowlist_is_exact`（exact set）
5. `tests/test_personal_secrets.py`（shell 连续字面量断言，约 L934）

外加 `secrets.env.example`、`backend/app/config.py`、`backend/app/api/settings.py`
（`settings_status()` 只返回 `{"<name>": {"configured": bool}}`，从不回值）。

`setup.sh` 当前不维护 Secret 清单（只生成骨架），故不需要改动其清单。

---

## 7. 当前数据文件与备份/Retention 逻辑

`backend/app/data_paths.py::DataPaths` 字段：`ai_jobs_db`、`catalyst_cache_db`、`optix_db`、
`worker_db`、`worker_lock`、`watchlist_snapshot`、`strength_snapshot`、
`public_home_snapshot`、`backups_dir`、`runtime_settings`、`accounts_db`。

备份：`tasks.py::build_default_tasks()` 构造 `MaintenanceTask({label: path})`，
当前 4 个 label（`optix`、`catalyst-cache`、`ai-jobs`、`optix-worker`），
`RetentionTask` 复用同一张表在裁剪前先备份。
→ 新库需要同时进入 `maintenance` 与 `retention` 的这张表（同一 dict 复制两份）。

SQLite 规范（照 `services/breakouts/repository.py` 抄）：
`journal_mode=WAL` 强校验、`busy_timeout`、`foreign_keys=ON`、`synchronous=FULL`、
读连接 `mode=ro` + `query_only=ON`、`_SCHEMA` 元组 + `SCHEMA_VERSION` + `SCHEMA_CHECKSUM`、
迁移在 `BEGIN IMMEDIATE` 内跑并在提交前 `PRAGMA foreign_key_check`。

---

## 8. 本功能计划修改/新增的文件

**新增（后端）**

```
backend/app/services/macro_conditions/{__init__,models,registry,fred_client,
  market_proxy,repository,alignment,calculations,scoring,service,formatting}.py
backend/app/api/macro_conditions.py
```

**修改（后端）**

- `backend/app/config.py`：`fred_api_key`
- `backend/app/data_paths.py`：`macro_conditions_db`
- `backend/app/personal_config.py`：`MacroConfig` + `PersonalConfig.macro`
- `backend/app/tools/personal_secrets.py`、`backend/app/legacy_env_adapter.py`：`FRED_API_KEY`
- `backend/app/api/settings.py`：`fred` configured 布尔
- `backend/app/api/worker_actions.py`：`macro_conditions` 动作类型
- `backend/app/worker/tasks.py`：`MacroConditionsTask` + inventory + 备份表
- `backend/app/main.py`：宏观 GET 进公开研究读白名单 + router 注册
- `backend/app/services/signals.py`：长周期档位 + 一个公开的日线取数入口
- `backend/app/services/catalysts/local_intelligence.py`：Focus payload 加紧凑宏观块、
  `input_hash` 纳入宏观身份、`FOCUS_PROMPT_VERSION` → v6
- `backend/app/services/ai_jobs/runtime.py`：market_focus instructions 增加宏观纪律句

**新增（前端）**

```
frontend-src/src/api/modules/macro.ts
frontend-src/src/components/market/macro/{MacroConditionsPanel,CompositeCard,
  MacroHistoryChart,ModuleGrid,ModuleCard,DriverList,FactorDetails,FactorRow}.tsx
frontend-src/src/mocks/macro.ts
```

**修改（前端）**

- `frontend-src/src/pages/Market.tsx`：插入面板 + 区块编号注释
- `frontend-src/src/lib/scoreHints.ts`：8 个模块/综合 hint + 30 个 factor hint
- `frontend-src/src/api/modules/index.ts`：导出
- `frontend-src/visual-tests/*`：宏观取证
- `frontend/**`：构建产物（CI 逐字节 diff 闸门要求同提交）

**配置/CI/文档**

- `config/personal.toml`、`secrets.env.example`
- `.github/workflows/ci.yml`（inventory + 离线容器 smoke + 视觉产物）
- `README.md`、`docs/macro-conditions/*.md`、`docs/personal-edition/*`（仅触及段落）
- `tests/`（后端）、`frontend-src/tests/`（前端）

---

## 9. 不应修改的领域边界

- **News-feed**：`~/Downloads/Claude/News-feed` 与 MacroLens 远端（`/internal/v1`）完全不动
- 不新增容器、不新增服务、不改 `docker-compose.yml` 的服务拓扑
- 不新增模型 Provider、不新增 AI Job 类型、不新增 `/api/macro/ai`
- 不改 `MarketFocusResult` 输出 schema 名（除非确有新输出字段——本次没有）
- 不把宏观分写入 `strength` 选股排序或 `breakouts` 排名（v1 仅展示）
- 不改 `range_persistence` 现状
- 不抓取 bhadial.com、不解析 MacroDial 页面 JSON、不把参考 PDF 提交进仓库
- 不 `git reset --hard`、不 `git clean`、不改写已有提交、不动未跟踪的
  `frontend-src/src/i18n/`
- 不直接部署生产、不合并 PR、不调用真实 OpenAI / 真实 FRED

---

## 10. 命名与法律边界

- 中文名「Optix 宏观环境」，英文名「Optix Macro Conditions」
- 因子范围参考用户提供的公开报告结构，但**公式与评分均由 Optix 独立定义**
- 不使用 MacroDial 名称/Logo/品牌色/版式，不声称复刻任何第三方分数
- 分数是**过去 5 年历史分位**，不是预测概率、不是上涨概率、不构成交易建议
