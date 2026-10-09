# 后端按生产实际使用精简（2026-10-09）

分支 `claude/backend-slim-2026-10-09`，基线 origin/main `72f426f0`，也就是生产正在运行的版本。本轮改的是后端、测试、脚本、文档和配置示例；`frontend-src/` 只改了两份测试（见「测试的变化」），界面代码和 `frontend/` 没动。本轮没有部署。

本 PR 取代 #211 的后端部分。#211 与当前 main 冲突二十多处，这里没有合并或整批挑拣它的提交，而是逐项在当前 main 上重新核实后重做；#211 的前端部分由前端分支处理。#211 没有关闭，由站长决定。

## 判定依据

- **访问日志**：生产 nginx 日志从 2026-07-25 起没有轮转过。用了两段统计：
  - 07-25 至 10-05：#211 当时取回的全量路由表（约 18.9 万行），本轮按当前 main 的路由模板重新归并；
  - 09-25 至 10-09：本轮取证的前 70 个路由（54,406 行）。
  - 下文写成「07-25～10-05 命中 N 次；09-25～10-09 前 70 中没有」。10-05 之后命中少于约 50 次的路由，这四天的确切次数看不到。
- **前端调用**：在 `frontend-src/src` 里逐条 `grep` 接口路径。只出现在注释或缓存失效列表里的，不算调用。
- **研究与修复分支**：取 18 个仍开着的 PR 分支，以及本地研究分支 `claude/radar-replay-v1`、`claude/sharadar-data-gate-fixes` 相对 main 的改动行，对每个被删或被改的定义查同名命中。126 个被删定义在各自文件内零命中；在其他文件命中的都是同名不同物（`main`、`scan`、`__init__`、`_finite`、`produce`、`_present`）。`backend/app/services/research_eod_v1/` 一处没动。
  - 有一处按名字算命中、按符号算不是：`api/sectors.py` 的模块级 `_cache`。命中来自 `claude/radar-replay-v1` 回放框架里各个类自己的 `self._cache` 属性，没有任何分支改 `api/sectors.py`。这里照删，请主代理确认。
- **生产库状态**：2026-10-05 的只读查询（#211 记录）加 10-09 的取证。凡是靠「生产跑的就是 main、启动时会执行」推出来、没有直接查库的，下文标「推断」。

## 删除

### 接口（14 个）

| 接口 | 07-25～10-05 | 09-25～10-09 前 70 | 前端 | 一并删除 |
| --- | --- | --- | --- | --- |
| `GET /api/account/me` | 0 | 无 | 无调用；身份由 `/api/access/status` 的 `account` 提供 | 路由 |
| `POST /api/ai/analyze-alerts` | 0 | 无 | 无 | 固定返回 409 的兼容桩 |
| `GET /api/ai/earnings-correlation` | 0 | 无 | 无 | 同上 |
| `POST /api/ai/jobs/earnings-impact` | 0 | 无 | 无 | 路由与 `_normalized_earnings_submission`；财报分析只经按报告提交的接口和定时任务入队 |
| `GET /api/sectors/{id}/heatmap` | 0 | 无 | 无（视图在 #207 删除） | 路由与公开读白名单项 |
| `GET /api/settings` | 2（一次 401、一次 502，没有成功过） | 无 | 无 | `api/settings.py` 整个文件 |
| `GET /api/worker/actions`（列表） | 0 | 无 | 只按编号查单个任务 | 路由 |
| `GET /api/macro/conditions/factors/{id}/history` | 0 | 无 | 只在 `macro.ts` 头注释里提到 | 路由、服务与仓库读取 |
| `PUT /api/account/watchlist` | 0 | 无 | 只用 PATCH | 路由与 `WatchlistRequest` |
| `POST /api/account/watchlist` | 17，最后一次 08-13 | 无 | 09-07 起前端只用 PATCH 和删除撤销 | 路由与 `AccountStore.add_ticker` |
| `DELETE /api/account/watchlist/{t}` | 70，最后一次 09-06 | 无 | 同上 | 路由与 `AccountStore.remove_ticker` |
| `POST /api/catalysts/market-focus-cycles/{id}/cancel` | 0 | 无 | 无 | 路由、服务与本地情报层的取消实现 |
| `GET /api/options/unusual` | 0 | 无 | 无（只有浏览器测试的模拟数据） | 见下节 |
| `GET /api/stocks/{t}/signals` | 0 | 无 | 只出现在 `stocks.ts:375` 的缓存失效列表 | 见下节；`_cached_endpoint` 随之失去最后一个调用方 |

提交：`0ab65fd6`（前 12 个）、`c6e2db27`（后 2 个）。

### 首页快照不再生成 `unusual` 与 `focus_signals`（`c6e2db27`）

上表最后两个接口是这两项资源仅有的读者，后台却一直在生成：`unusual` 约每 30 分钟一次、每次约 30 个 Yahoo 请求，`focus_signals` 每 15 分钟一次。删除了生成逻辑、只为 `unusual` 服务的盘后延迟与期权收盘阶段、「财报与异动轮流」的重资源调度、恒为空的 `deferred` 状态字段，以及两项在发布必需清单里的名字。

发布现在只带走仍在生成的资源。生产快照文件里还存着这两项，解析器遇到未知资源名会拒绝整份文件，所以资源规格、参数和校验器暂留；`unusual_seconds` 也暂留，因为生产 `personal.toml` 设置了它，而配置模型禁止未知键。见「部署后」第 1 条。

### 已在生产执行完毕的一次性迁移

| 提交 | 删除 | 生产事实 |
| --- | --- | --- |
| `6bffb6ed` | 旧催化剂表导入：`_import_legacy_from_database`、`import_verified_legacy_rows` 与结果计数 | 10-05：审计表 2007 行全部是 rejected，从未导入成功；此前每次同步都要整表重读两张旧表 |
| `843e7eb2` | 催化剂时间戳统一 `_normalize_local_news_timestamps` | 10-05：timestamps-v1 登记行已在 |
| `ad9b6395` | ai_jobs 的 v1→v2 重建、`budget_charge_microusd`/`error_detail` 补列、跨来源身份改写 | 10-05：登记到 ai-jobs-v4，三列都在；之后 main 升到 v5（推断：生产跑 main，启动时已登记） |
| `bba35555` | 宏观 ETF 表 v1→v2 重建，以及每次 `active_etf` 读取都做的表结构探测 | 10-05：v2 已登记，表已有 `value_hash` |
| `281cba01` | 运行设置 V1 模型、V1→V2 迁移、`.pre-v2` 快照的保存与恢复，以及每次读取都做的旧字段剥离 | 10-09 只读核实：`runtime-settings.json`、`.runtime-settings.json.backups/`（只有 v10 一份）与 `runtime-settings.json.pre-v2` 都不含 `manual_force_reanalysis`、`manual_refresh_enabled` |
| `f80f30ad` | `legacy_env_adapter.py`、两个旧 `.env` 迁移工具、`setup.sh` 的迁移分支 | 生产已有 `machine.env`、`secrets.env`、`personal.toml` |
| `46e50586` | `deploy.sh` 停旧后台服务 | 10-05：四个旧服务都没有容器 |

守住的边界：

- 催化剂库 `_SCHEMA` 里审计表的建表语句一字不动，它参与校验和。三个库的建表文本、版本名和校验和都与 main 逐字节相同；新增的「新建库」测试把催化剂三条、ai_jobs 七条、宏观一条登记的校验和钉成生产值。
- 被删迁移对应的登记行（`ai-job-identities-v2`、`optix-local-catalyst-timestamps-v1`）：已有时照旧核对校验和，不一致就拒绝启动；没有时写入，新库与生产库的登记表一致（`122d2aa9`，两条测试钉住）。
- 10-05 之后才加的迁移没有生产证据，保留：ai_jobs 的 Claude 列补列循环、`ai-job-provider-progress-v1`、催化剂的 `optix-verified-focus-publication-v1`。
- 催化剂的补列函数 `_add_missing_columns` 和它的三组旧列保留。旧列早已在生产里，但性能分支正在用同一个函数给修订表补新的质量列。

### 其他没有生产调用方的代码

- `c82de9b1`：`macro_technical_gap`、`shadow_ranking_adjustment`、`STRENGTH_SHADOW_CAP`、`MacroFitReader.structural_score`。只有 #210 删掉的旧扫描器用过；收盘选股的扫描行没有任何宏观字段。
- `6d808ca4`：`DiscoveryProvider`（鸭子类型，没有注解或 `isinstance` 用它）、`fred_client.build_client`、`market_proxy.require_symbols`、宏观 `SyncRun` 与 `iso_date`、`a0_companion_for_admin_default`、`_expected_move_from_chain_snapshot`、`main._configured_allowed_hosts`、`yahoo.get_expirations`、`auto_patterns.apply_volume_confirmation`。后几个只被测试用，测试改为直接调用共享实现。
- `f30c4bf2`：`api/sectors.py` 的进程内缓存（`_cache`、`_locks`、`_lock_for`、`_with_cache_status`、`_cached`、`_MAX_STALE_SECONDS`）。代码注释自己写着「生产不调用，只因测试在用而保留」。
- `29b28bdc`：突破雷达工作进程的独立命令行入口（`_parser`、`_async_main`、`main`、`__main__`），以及只有它驱动的连续循环（`run_forever`、`_wait_until`、`_sleep`、`request_stop`、停止标志、`jitter` 参数）。生产只经统一后台的 breakout 任务调用 `run_once()`。`BreakoutWorker` 类本身被本地回放研究分支使用，保留；默认的 `"continuous"` 模式值也保留，回放框架直接驱动 `_run_cycle` 时会读到它。

## 合并的重复实现

| 提交 | 合并 | 依据 |
| --- | --- | --- |
| `252053b2` | 11 处参数完全相同的规范化 JSON 序列化调用（`ensure_ascii=False, sort_keys=True, separators=(",",":"), allow_nan=False`）改用 `json_validation.canonical_json_text` | 用固定样本（中文、浮点、空值、嵌套）在 main 与本分支上比对，各写入方输出逐字节相同；新测试钉住样本的完整字节、sha256 和非数（NaN）拒绝，`earnings_input_hash` 对照 main 算出的值 |
| `866f9ce1` | 板块与个股快照的三份逐字节相同的原子写改用 `file_identity.replace_file_atomically` | 临时文件名、落盘同步（fsync）、失败清理全部照旧；断言「失败后无 `.{name}.*.tmp` 残留」的测试照常通过 |
| `28c1ea07` | 三份按列表计算的平均真实波幅（ATR）改用 `technical/indicators.mean_true_range` | 随机比对 24 万次（长度 0～40、窗口 -3～50、含 NaN 与零价），与 main 的三份实现完全相同 |

有意不合并的：

- 规范化 JSON 的其他参数组：转义 ASCII、允许 NaN、带 `default=`、带缩进的各组；被开放修复分支 #172 改过的 `ai_jobs/runtime._bounded_untrusted_json`；新闻拉取分支和性能分支正在重写的 `catalysts/etl_repository._json` 与 `local_intelligence._json`。
- 其余 16 处原子写：目录 fsync、权限、符号链接检查、清理范围或临时文件名各不相同，合并会改变行为。`api/strength.py` 的强度快照写入还在写入过程里修剪变体文件，而且该文件被开放研究分支改过。`scripts/watchlist_snapshot.py` 里还有第五份同样的写法，它是不导入 app 的独立脚本，没动。
- 交易时段分类（5 处）：四处算术一致、只是标签不同；`realtime_quotes.market_session` 把收盘那一秒判为盘中，有测试钉住；唯一合适的公共位置 `market_calendar.py` 正被研究分支修改。
- 突破缓冲：公式已经只有一份（`breakouts/config.break_buffer`），10 处调用用 6 种基准是有意的，统一会改变阈值。
- 按 pandas 滚动计算的 ATR、Wilder 平滑的 ATR、雷达要求满窗口的 `feature_engine.compute_atr`：算法不同；后者还被本地研究分支改过。

## 围栏：读取校验改为缓存结论，语义不变（`3b8d1cea`）

**问题**：公开新闻流、按代码查询和批量查询，每读一次都对每条已发布分析重跑 `validate_result`（pydantic 模型校验加简体中文校验器）；Owner 读新闻流时还会经 `AIJobRepository.public` 对每条再跑一次。本机按 1.2 万字正文测，112 条约 195 毫秒；2026-07-26 的生产剖析记为每次读取约 0.8 秒。

**做法**：读取时照旧跑完整校验，只把结论记下来。`validate_result` 不读时钟、不读可变状态，结论只由校验函数、任务类型、结果字节和载荷决定。`models.validate_result_cached` 以这四样为键（结果字节和规范化载荷各取 sha256），每个进程保留最近 2048 条结论：

- 通过的结果每次交出一份深拷贝，调用方改动自己的副本不会影响缓存；
- 拒绝也记下来，下次照样抛出同一个错误；
- 载荷无法规范化时不进缓存，每次都完整校验；
- 部署会重启进程，规则改了总是从空缓存开始。

接入三处读取：`AIJobRepository.public`（Owner 新闻流逐条的任务投影，以及任务、财报的读取）、`PersonalCatalystService._project_news_analysis`（公开新闻流、按代码与批量查询）、`news_result_audit_states`（`/api/catalysts/analysis-progress`）。各处把本模块里的 `validate_result` 传进去，测试替换它时缓存键随之不同，不会拿到旧结论。

**效果**：同一组 112 条，第一次读约 195 毫秒，之后约 5.5 毫秒（算键加深拷贝）。2048 条结论按本机样本约占 8～30 MB 内存（单条结果的 JSON 为 1～11 KB）。

**放松了什么**：没有。显示与隐藏的结果、错误类型和错误信息都与 main 相同。

**撤回的做法**：`e748396a` 曾让「结构标识为当前值」的行只做轻量检查。审查指出它的前提不成立：公开新闻流认的 `_analysis_current` 来自审计契约号下的 accepted 记录，这个契约号自 08-27 没变；`RESULT_VALIDATION_CONTRACT_VERSION` 自 08-09 没升过，其间多次改过校验行为；指纹测试也抓不到这类改动。`3b8d1cea` 撤回了这套做法，`check_stored_result`、`trust_current_identity` 和指纹测试都已删除，`validate_result` 恢复为 main 的原文。

### 逐个读过、不动的六个模块

| 模块 | 运行时机 | 结论 |
| --- | --- | --- |
| `json_validation.py` | 快照文件变化后的解析，都在文件指纹缓存之后 | 不动（本轮只在里面新增共享序列化函数） |
| `execution_limits.py` | 一个常量 | 不动 |
| `failure_diagnostics.py` | 只在失败或降级路径上 | 不动 |
| `document_policy.py` | 每个文档请求一次正则、每个静态响应一次 | 不动 |
| `deployment_boundary.py` | 启动时一次；每请求只做 Host 规范化（约 20 微秒） | 不动 |
| `request_limits.py` | 每个 API 请求一次请求体边界 | 不动，属于攻击防护 |

身份与口令、同源三重校验、Host 校验、限流、只读访客边界都没有改。

## 配置（`638f33cf`）

生产没有设置、仓库里也没有任何文件提到的 12 个环境变量，读取方一直拿的是字段默认值。改为直接用这个默认值：

| 变量 | 去向 |
| --- | --- |
| `YAHOO_OPTION_MAX_IN_FLIGHT`、`_MAX_QUEUE`、`_QUEUE_WAIT_SECONDS`、`_CALL_TIMEOUT_SECONDS` | 删除；`YahooOptionIO()` 本身默认就是 3 / 8 / 8.0 / 20.0 |
| `OPTION_EMPTY_DISCOVERY_SECONDS` | `yahoo._EXPIRATIONS_TTL_SECONDS = 900` |
| `FINNHUB_CANDLE_FALLBACK_ENABLED`/`_LIMIT`、`MARKETDATA_STOCK_CANDLE_FALLBACK_ENABLED`/`_LIMIT`、`STOOQ_PRICE_FALLBACK_ENABLED`/`_LIMIT`、`REQUEST_TIMEOUT` | 只有强度扫描器的历史兜底在读；兜底保持常开，上限 80 / 260 / 260，超时 6.0 秒（原来 `min(REQUEST_TIMEOUT=20, 6.0)` 恒为 6.0）。进程内大盘强度缓存键去掉三个恒定的开关后缀 |

保留：生产设置了的所有字段（各密钥、`MACROLENS_URL`、由 `personal.toml` 驱动的 `QUOTES_*`）、多个模块共用的地址、研究分支直接从环境读的 `MASSIVE_*` 名称、旧 MacroLens 名称的拦截字段，以及与 FMP 整个可选数据源一起留下的 `FMP_BASE_URL`。

## 保留与理由

- 运行设置的修改、回滚、历史，K 线画线的写入，两个退出登录接口，自选删除撤销：有界面入口，用得少。
- `GET /api/diagnostics/cache` 与缓存计数：站长可能在服务器上直接调用，访问日志看不到；只删接口会留下只写不读的计数代码。
- `/api/strength/stocks/{t}` 信封里的 `macro_linkage` 键：开放研究分支 #163、#164、#166 改过这个名字。
- `AccountStore.replace_watchlist`：生产不再调用，但十几个账户测试用它准备数据。
- 旧强度快照写入 `_write_strength_snapshot`：#211 已说明后台仍按这些冻结文件刷新参数变体。
- #211 的「后台任务布尔型刷新请求」（`27fc0aec`）：只是测试替身里的分支，但它在 `CatalystSyncTask` 里，正是性能分支改唤醒节奏的地方，本轮不做。
- `config.py` 旧 MacroLens 名称的拦截字段：上一轮决定保留。
- FMP（Financial Modeling Prep）第二财报日历：生产没有配置密钥，整条路径永远短路，但删除属于功能取舍，列入「需要站长决定」。
- 2026-09-25 审查点名的 `ai_jobs/worker.py:775-876` 独立入口，已在 2026-10-04 删除（`deslop-remediation-20261004.md`），当前 main 那几行是 `process_job` 的中段，本轮无事可做；同类的突破雷达独立入口本轮删除。
- `local_intelligence.py` 的拆分：这个文件正被性能分支改动，拆分会让两边几乎整文件冲突，本轮不做。
- 简体中文校验器仍在读取路径上运行：上面三处缓存未命中时、焦点周期投影、热点核验发布、本地情报层的新闻流与单条和批量读取遇到没有当前审计记录的分析时（`_analysis_state_for_revision`；有审计记录的只核对身份，与 main 相同，本轮没有接缓存），以及新闻条目的源标题与源摘要清洗（每条 0.01～0.08 毫秒，校验的是上游原文而不是落库的 AI 输出）。
- 已过时的历史审计记录（如 `full-review-remediation-20260925.md` 里「AI 任务独立入口未删」「板块缓存为测试保留」两句）不改，以本文为准。

## 测试的变化

后端被删的测试定义 70 条：62 条只测已删代码，8 条以改写后的新名字保留（ai_jobs 登记表两条、宏观未知模块一条、首页快照四条、`setup.sh` 补模板一条）。另新增 17 条，钉住登记表的校验和与核对、规范化 JSON 字节、原子写、首页快照旧资源暂留和读取校验的结论缓存。测试项从 main 的 6214 个变为 6165 个。前端只改了两份测试：`macro-fit-presentation.test.mjs` 不再读已删的 `STRENGTH_SHADOW_CAP`（`90bd00c0`），`customer-chart-periods.spec.mjs` 改用 PATCH 准备自选（`7f389e73`）。被删的 70 条按提交列出：

- `0ab65fd6`：`test_ai_jobs.py` 的 `test_job_post_is_fast_local_and_idempotent`、`test_paid_job_route_has_no_extra_action_capability`；`test_ai_safety.py::test_legacy_paid_route_validates_body_but_never_runs_model`；`test_analytics_audit_2026_09_25.py::test_m12_heatmap_endpoint_sanitizes_non_finite_values`；`test_catalyst_local_intelligence.py::test_focus_cancel_follows_a_concurrent_retry_to_its_new_job`；`test_macro_conditions_api.py` 的 `test_factor_history_returns_only_that_factor`、`test_every_registered_factor_is_addressable`，以及改写为只测未知模块的 `test_unknown_module_and_factor_return_not_found`；`test_personal_secrets.py::test_browser_settings_expose_only_option_pro_configuration_booleans`。
- `c6e2db27`：`test_api_data_states.py` 的端点缓存、异动期权三条与旧个股信号四条；`test_chart_contract.py::test_stock_technical_signals_use_massive_daily_history_first`；`test_options_financial_semantics.py` 的异动期权五条；`test_public_home_snapshot.py` 的重资源轮流、异动盘后、旧信号构建等八条（其中四条改写保留）；`test_remediation_access_errors_unusual.py` 的四条。
- `c82de9b1`：`test_macro_linkage.py::test_the_gap_separates_price_running_ahead_from_macro_leading`。
- `6bffb6ed`：`test_catalyst_local_intelligence.py` 的旧导入三条。
- `843e7eb2`：`test_catalyst_local_intelligence.py` 的时间戳统一两条。
- `ad9b6395`：`test_ai_jobs_zh_contract.py::test_v1_database_migration_preserves_and_disables_sync_history`、`test_manual_analysis_controls.py` 的身份改写三条；`test_ai_jobs.py` 的补列测试与 v3/v4 登记表测试改写为钉住登记表的两条。
- `bba35555`：`test_macro_repository.py` 的 v1 表四条。
- `281cba01`：`test_runtime_settings.py` 的 V1 与 `.pre-v2` 七条。
- `f80f30ad`：`test_personal_config.py` 的旧 `.env` 迁移七条、`test_shell_workflows.py` 的 `setup.sh` 迁移四条（其中一条改写保留）。
- `f30c4bf2`：`test_backend_cache_and_iv.py::test_sector_cache_stale_fallback_is_marked_and_expires`。

## 与其他分支的合并冲突

按 2026-10-10 01:50（日本时间）的分支头用 `git merge-tree` 逐个试合并。性能分支和新闻拉取分支还在提交，合并前要再试一次：

- 性能分支 `claude/backend-perf-2026-10-09`（`1cbf7915`）：
  - `ai_jobs/repository.py` 的 `_initialize_database` 两处。两边都改了初始化：本分支删掉三段迁移，性能分支重排了结构并在迁移分支里加了 `migrated = True`。合并时以性能分支的结构为准，删掉 `_migrate_v2`、两列补列和身份改写调用（`migrated` 只在新建表时为真）；身份登记行的校验和核对两边相同，保留。
  - `catalysts/local_intelligence.py` 的 `reconcile` 一处：性能分支把 `self._import_legacy_from_database()` 移到了事务外，本分支删了这个方法。合并时删掉这一行即可，结果计数里的两个旧导入键本分支已经删了。
- 新闻拉取分支 `claude/news-ingest-2026-10-09`（`855297ae`）：它合并过性能分支的较早版本，所以有上面同样的三处；另有 `README.md` 一处，两边都改了旧变量名那一段，合并时保留它新加的新闻来源说明和本分支改写的旧变量名说明。
- 开放修复分支 #160：`api/ai.py` 一处。它在本分支删掉的 `create_earnings_impact_job` 正下方插入 `_public_ai_job`；合并时保留它新增的函数即可。
- 前端分支 `claude/perf-frontend-2026-10-09`（`3718d903`）、其余 17 个开放分支和两个本地研究分支：没有新增冲突。

## 部署后

1. **首页快照第二步**：部署后第一次发布会把 `unusual`、`focus_signals` 从 `public-home-snapshot-v1.json` 里清掉。确认文件里已没有这两项后，再删资源规格、校验器、参数分支和 `unusual_seconds`（同时改仓库与生产机的 `personal.toml`，配置模型禁止未知键）。
2. 读取校验的结论缓存不改变任何结果，不需要核对数据。想看效果，可以对同一个 `/api/catalysts/feed` 连续请求两次并比较耗时；生产只有一个后端进程，第二次的结果校验走缓存。
3. 后台状态里 public_home 任务不再有 `deferred` 字段，没有任何读取方。
4. 突破雷达不再能用 `python -m app.services.breakouts.worker` 单独启动；生产本来就不用。

## 需要前端配合

- `frontend-src/src/api/modules/stocks.ts:375`：拉取后清缓存的列表里还有 `/stocks/{t}/signals`，接口已删，这一项可以去掉（留着无害）。
- 注释里提到已删接口：`frontend-src/src/components/detail/SignalList.tsx:2`（`stocks/{t}/signals`，组件实际读 `/api/signals/stock/{t}`）、`frontend-src/src/api/modules/macro.ts:6`（因子历史接口）。
- `frontend-src/visual-tests/password-mode.spec.mjs` 里已删接口的模拟返回：第 80 行 `/api/options/unusual`、第 86 行 `/api/stocks/NVDA/signals`、第 204 行 `/api/worker/actions` 列表。
- `frontend-src/AUDIT-live.md` 第 23、26、38 行的 `options/unusual`、`ai/jobs/earnings-impact`。
- #211 的前端部分（选股页宏观适配视图、`macroTechnicalGap` 字段，以及 `frontend-src/src/lib/macroFit.ts:114` 说后端已算出差值的注释）：后端从不下发这些字段。

## 需要站长决定的生产数据

- `catalyst-cache.db` 的 26 张旧表和旧导入审计表：迁移文档承诺保留到 2026-10-15。删后需要 VACUUM（库 1.38 GB，其中 563 MB 是空闲页，从未 VACUUM）。VACUUM 要停后台和后端，用裸 `docker run`，不要用 `compose run`。
- 空库文件 `catalyst-local.db`、`catalysts.db`、`macrolens.db`（各 4 KB）和 `optix.sqlite3`（0 字节），代码不再引用。
- `runtime-settings.json.pre-v2`：程序不再读取。
- `retention` 任务只能手动触发、从未跑过，`ai-jobs.db` 316 MB。改成定时要动后台任务清单的五面镜子，不在本 PR。
- FMP 功能是否整体删除（涉及财报页、快照、密钥清单和约十个测试文件）。

## 检查记录

在代码提交 `122d2aa9` 上检查（本文所在的提交只改文档）：

- 后端全量 `pytest tests/`：6158 通过、7 跳过。main 是 6207 通过、7 跳过；测试项 6214 → 6165。
- 前端：`VITE_API_MODE=live npm run build --prefix frontend-src` 的产物与已提交的 `frontend/` 逐字节相同，产物没有提交；`node --experimental-strip-types --test frontend-src/tests/*.test.mjs` 1293 条全部通过；`static_assertions.mjs` 通过；`npm run lint` 没有错误，两条警告在本轮没碰的 `Breakouts.tsx`。
- 浏览器（本机 Chrome）：`test:customer-charts` 5 条、`test:screener` 两段共 20 条，全部通过。只有这两组会启动真实的 Python 后端；其余浏览器测试的配置不启动后端，本轮没有跑。
- `python -m compileall -q backend/app scripts`、对 `setup.sh`、`personal.sh`、`scripts/*.sh`、`scripts/lib/*.sh` 逐个 `bash -n`、`git diff --check origin/main...HEAD -- . ':(top,exclude)frontend'`：都没有报错。
- 规模：OpenAPI 里 `/api` 下的接口（方法加路径）106 → 92；`Settings` 字段 63 → 51；`backend/` 50 个文件，增 245 行、删 3548 行；`tests/` 41 个文件，增 688 行、删 2526 行。
- 没有做：没有在本机起 Docker 编排跑部署脚本，也没有连生产机。
