# 2026-09-25 全仓库审查后的修复记录

基线：`8798a689`（PR #195 合并后）。本轮对应 2026-09-25 的全仓库审查（code-humanizer 扫描模式：先跑测试建立基线，再按目录分区整读，每条缺陷配探针复现）。审查报告与探针不进仓库，保存在审查者本机；这里只记录处理结果与保留边界。

原则沿用代码整理技能的约束：行为缺陷与结构清理分开提交；清理按模式分批；每条修复配回归测试，绝大多数回归测试在基线代码上会失败。测试替身与真实后端形状不一致是本轮两条用户可见缺陷能通过 CI 的原因，所以相关夹具改用真实响应形状。

## 高严重度（6 条，全部修复）

| 编号 | 问题 | 修复 | 回归 |
|---|---|---|---|
| AI-1 | 匿名访客读焦点周期用瘦上下文重校验，含外文实体的周期对访客隐藏 | 本地层把周期行完整 payload 作为私有校验字段交给公开层，校验后再删；取任务失败退回瘦上下文时记诊断 | 真实匿名链路用例（绕开 owner 夹具） |
| FE-1 | 新闻分析轮询在真实后端下自停（任务响应无 `news_id`） | 守卫改用轮询开始时的新闻编号；截止时间按本次轮询重置；夹具与浏览器桩改为真实形状 | 真实形状响应下轮询到终态 |
| FE-2 | 已登录普通账户在催化剂页每 45 秒被误判身份失效 | 删除把 `owner_login_required` 当会话失效信号的逻辑，会话失效只由 401 触发 | 客户账户收到该 reason 不触发失效事件 |
| W-1 | 备份 `sleep` 只在忙时生效、源库写入时从头重来 | 改为单步读事务复制，删掉无效的 `sleep` | 并发写入方每 20 毫秒提交时备份完成且不重启（计数断言） |
| W-2 | 任一库备份失败就每 5 分钟全库重备、历史 35 分钟内轮换掉 | 按库记录成功时间只重试失败库；指数退避；按时间分层保留；清单缺文件时隔离该组；`accounts.db` 与运行设置进备份范围；复制前先清理与检查磁盘空间 | 持续失败时其余库不重备、历史副本保留 |
| W-3 | 交易日历没有临时休市表，下次临时休市让全市场加载器失败约 18 个月 | `market_calendar` 加临时休市表（2001-09、2004-06-11、2007-01-02、2012-10-29/30、2018-12-05、2025-01-09 等）；加载器对「日历交易日却为空」给带日期的原因码，整批失败即停并报真正的停止原因 | 2025-01-09 入表后加载器不再失败 |

## 中严重度

### AI 任务核心（`services/ai_jobs`、`api/ai.py`、`api/signals.py`）

- AI-2：「车道此刻能否提交」抽成仓库共用判定，`claim_due` 跳过不可提交车道上未提交的任务，不再每秒空转两次；unknown 期间手动任务及时认领。
- AI-3：本地写库失败单独归类为 `local_storage_error`：已有 response_id 的推迟，未提交的推迟；只有供应商返回的错误写终态。`record/complete/defer` 复用短重试。恢复工具收带 response_id 的 `provider_unavailable`/`local_storage_error` 行。
- AI-4：取回或取消出错按状态码分流（400/401/403/404 写终态，其余推迟）；持续取回失败受轮询超时约束；超过最长保留期先取回一次再判过期。
- AI-5：manual 来源在 `max_queued` 之上留 `MANUAL_QUEUE_RESERVE = 20`（api、手动新闻、手动财报三处入口一致）。
- AI-6：中文校验器加期权、技术指标、财务常用缩写白名单（IV、OI、PCR、ATM、OTM、ITM、Gamma、Delta、Theta、Vega、CEO、CFO、CTO、YTD、SPX、NDX、DXY、10-K、10-Q、8-K、Non-GAAP、Call、Put）；option_alerts 的 alerts 字段进源文本；`english_prose_not_allowed` 带被拒片段。提示词未改，避免待处理任务作废。
- AI-7：earnings_impact 的 name、sector 进源文本；`impacted` 下限从 4 降到 1，`schema_name` 升 v5、`PROMPT_VERSIONS` 升 v6；`runtime_configuration_changed` 进定时瞬态重试名单（只在提交前发生、不花钱）。本地层对「已完成但身份对不上」的付费周期不因此自动重做。
- AI-15 到 AI-24：同票单飞按票加锁；冷却只对 manual 来源同车道生效；崩溃后取消先落 unknown；`provider_incomplete` 移出瞬态名单；欠费熔断（10 分钟内出现欠费失败时推迟新提交，不消耗重试次数）；美元账与 token 账对零用量终态失败同一规则，释放改白名单；payload 大小只保留一个上限；`signal_ticker_mismatch` 进保留名单、入队 payload 不合法用 `invalid_job_payload`；访客重试只对瞬态失败；取消接口尊重 `confirm`；入队返回的财报行经装饰；证据包丢新闻块时同步删 `context_tickers`；`stock_ai_analysis` 里的仓库调用移出事件循环。
- 新增：钉住 `ai-jobs-v4` 四段建表文本校验和的测试；`AIJobRepository.prune_scheduled_history` 清理保留期外的 news/focus 终态任务（RetentionTask 调用，保留天数不低于新闻保留期）。

### AI 上下游（`services/catalysts`、`signal_context`、`earnings_enrichment`、`api/catalysts.py`）

- AI-8：入队前按字节预算截断摘要与代码提示并标注；`run_scheduled` 对单个候选的 `ValueError` 计为跳过并记诊断，一条超大新闻不再让整轮与焦点铸造崩掉。
- AI-9：热点排序分在每次计划时重算，事件组版本只承载身份与内容；旧闻的「发布时间较近」分量随时间衰减。
- AI-10：`LocalCatalystIntelligence.initialize` 与 `reconcile` 不再初始化 AI 仓库；AI 仓库不可用时分析相关读写降级，新闻摄入与热点计算不受影响；`CatalystSyncTask` 先做 ETL 同步，AI 初始化失败只让分析步骤降级。
- AI-12：reconcile 的任务快照只取新闻保留期内的任务，先查修订是否存在再校验，校验移出写锁；RetentionTask 调用 `prune_scheduled_history`，保留天数不低于新闻保留期。
- AI-13：调度扫描先按 (news_id, change_sequence, content_hash) 建索引，不再对每对候选解析 JSON。
- AI-14：`focus_prepare_expired` 的意图允许以新意图重建，定时路径对它可重试，手动请求不再复用失败周期。
- AI-25 到 AI-29：证据包超时后取消未完成的协程并记汇总诊断；`api/catalysts.py` 会写库的调用移出事件循环；`batch()` 只返回有中文文本的条目；ETL 时间戳归一化为 `Z` 结尾；日历实际值匹配加标题门槛；焦点乐观锁在写事务内复核修订；市值兜底预算按「今天最紧急」排序；FMP 行缺 year/quarter 时按同一报告已知期次回填（`api/earnings.py` 在 FMP 合并后调用）。
- 追加：`runtime_configuration_changed` 对「已完成但身份对不上」的付费周期不自动重做；手动新闻入队同样留 `MANUAL_QUEUE_RESERVE`。本地库版本升到 v6，旧库自动补列。

### worker 与备份（`worker/`、`tools/sqlite_backup.py`）

- W-4：手动动作结果过不了校验时标失败（`action_result_invalid`）并截断结果，不再让任务循环死亡后重启重放。
- W-5：`honor_persisted_schedule` 任务在运行开始时就写下次计划时间，备份进行中重启不再立即重跑。
- W-6：全市场作业进入批次时记本轮计划时间，超时后仍完成的作业按成功处理，不再连续重跑。
- W-7、W-8：备份表加 `accounts.db` 与运行设置文件；复制前按 `keep-1` 清理并检查可用空间。
- W-9：默认选股快照按收盘后美东绝对时刻刷新，上次计划时间持久化。
- W-14：yfinance 分批下载把失败批次的代码清单随结果返回，不再静默丢批。
- W-17：`_requeue_or_exhaust` 走 `_state_call`；认领失败时状态行不停在 running，非锁类 `OperationalError` 用 `worker_state_error`；`_state_call` 只对锁冲突重试；监督器锁路径默认用生产路径；`renew` 拿锁后再取时间。
- AI-11：终版分析只要求「该报告不存在进行中的预发布」，被 `EARNINGS_PRE_RELEASE_ACTIVE_LIMIT` 挤掉或预发布失败的报告也能拿到终版。首次部署后会为日历窗口内已发布但没有终版的财报补跑一轮终版，数量以日历窗口内的已发布条目为限，队列深度受 `EARNINGS_FINAL_QUEUE_RESERVE` 约束。
- M-4：`BreakoutTask` 跨扫描持有一个发现源实例并在停止时关闭。
- 清理：全市场作业与附带变体的异常记诊断；Yahoo 取现价失败记诊断且缺现价的期权链不按有效结果缓存；成功判定与需求完成调用抽成助手；`isoformat` 内联改用已有助手；`quick_check` 与 `integrity_check` 只保留后者。`StrengthRefreshTask` 的 `scanner`、`writer` 参数仍被 `tests/legacy_strength_support.py` 读取，未删。

### EOD 数据层与日历（`market_calendar`、`eod_limited`、`research_eod_v1`、`massive`）

- W-10：门槛结果统一为 Python 布尔写盘，诊断面板不再显示「未核实」。
- W-11：诊断库保留最近三代加当前批次引用的一代，年龄只作一小时下限；超过一小时的临时文件一并清理；回滚日志按所属临时库判断年龄。
- W-12：截止日一致时复用原序列，不再整份复制面板。
- W-13：生产作业与上下文快照都在收盘后 60 分钟才把当天算作完成；`api/strength.py` 的过期判定同口径。
- W-15：期权收盘时刻拆成个股与指数两种口径，默认个股（半日市 13:00）。
- W-16：Massive 客户端 3xx 显式报 `redirect`；期权到期日与期权链分页；重试按 `Retry-After` 加抖动退避。
- W-19：「缺少当日日线」按缺目标日、无历史、坏数据分别计数，守恒检查为真检查；batch.json 与上下文快照写盘 fsync；`VOLUME_SCOPE` 只定义一处。
- 保留：`research_eod_v1/runs.py`、`source_bind.py`、`capture_store.py` 仍被未合并的研究分支导入，未删。

### HTTP 层与应用核心

- H-1：worker 状态库的读写在异步路由里改走线程，不再卡住事件循环最长 30 秒。
- H-2：首页快照写入用描述符所有权标记，不再二次关闭；`os.replace` 之后的失败单独记诊断。
- H-3：新增只读元数据的股票摘要读取，数据状态与自选日线趋势不再深拷贝整份日线；首页快照只校验被请求的资源。
- H-4：非日线 K 线与日线请求对非法代码统一返回 400。
- H-5：worker 复用拉取函数时不写进程缓存。
- 低：登录时间均衡用预先算好的哑哈希；搜索索引按目录版本缓存；线程里只读进程缓存；`saved_at` 给 5 秒容差；匿名技术面兜底补相对大盘强弱；财报快照读盘尊重传入日期；多处静默捕获补诊断。

### 分析与算法模块

- breakouts：T1 落库接受真实事件并对未完成尝试幂等；去重不再泄漏被淘汰副本的迁移记录，存储的触发迁移只回填已触发状态；延续快照逐条校验，坏行跳过；`BreakoutWorker` 复用任务持有的发现源，熔断与陈旧回退在生产生效；六处适配器故障与主失败路径记诊断，状态详情带 `source_status`、`source_errors`、`per_event_errors`；`_clean_frame` 补 Open 边界；状态机循环遇重复状态即停；T1 重试次数不被读失败重置；读取错误不再静默。
- macro_conditions：系列写入逐条隔离故障并计入 `series_failed`；`structural_macro_score` 改为诚实的等权平均；`_funding_fragmentation` 加最小样本量校验并逐系列标记缺失；恒真条件删除；`factor_confidence` 真正填充。
- strength：扫描主循环与三个兜底数据源的异常记录 ticker 与类型；三个下载函数尾部共用。
- API：突破雷达列表逐条构造事件，一条脏记录不再整页 500；板块 `price` 字段过有限数校验并 sanitize；`_finite` 改用 `numeric.finite_number`。
- 保留：`api/sectors.py` 的进程缓存簇与 `scanner._ret/_rsi` 仍被测试引用（`test_backend_cache_and_iv.py`、`test_backend_calculations.py`），未删。

### 前端

- FE-3 到 FE-8：新闻分析失败按错误码给文案且不露原始码；抽屉旧补丁不再盖掉列表更新；焦点周期保留取消与预算受限终态并按原因给文案；焦点读取失败按退避重试、进行中低频轮询并禁用触发按钮、刷新失败保留旧数据；财报卡停表后给重试按钮。
- FE-9：`WatchlistItem`、`StockDetail` 的 12 个数值字段改为 `number | null`，27 处类型转换删除，消费点按 tsc 逐个补空值处理。
- AI 面板：404 后可重置；页面隐藏暂停、回前台补查；创建响应的 `Retry-After` 作首拍；创建失败按业务码给文案且英日界面不显示中文原文；排队原因与已请求取消可见；共用错误码文案表 `aiJobErrorMessage`，催化剂表对未知码回落到共用表。
- 其它：新闻抽屉提交/取消响应核对抽屉仍是原新闻；新闻轮询隐藏暂停并尊重 `Retry-After`；进度卡退避重试；翻页被拒时提示并禁用按钮；推送流加 error 监听按退避重连；定时器随卸载清理；`RowExpansion` 手动拉取补 alive 守卫；`liveQuotes` 隐藏时中止轮询请求；注册表恢复路径做查询串规范化；`refresh` 属性签名与事件对象分开。
- 清理：mocks 旧版六函数删除并去掉 V2 后缀；`EASE_SNAP`、`getEarningsRefreshCount`、`submitTickersBatch`、三处多余重导出删除；`LeadBigCard` 改用 `fmtRelative` 与共用的 `num`/`str`；画线分析共用 `clamp`/`finite`；13 个零引用的字典键删除。

## 有意未做或留待拍板

- 提示词文本（Call/Put 改「认购/认沽」）：会让 `schema_identity` 变化，待处理任务整批作废，本轮只加白名单。
- `runtime_configuration_changed` 进瞬态名单后，焦点周期里「已完成但身份对不上」的付费结果不自动重做（本地层单独排除）。
- `ai_jobs/worker.py` 的独立入口与从未启用的联网搜索分支未删（后者参与 `schema_identity` 哈希）。
- `VOLUME_SCOPE` 对全部来源统一为 Massive 标签只影响展示，未改契约。
- 大文件拆分（`local_intelligence.py`、`api/stocks.py`、`breakouts/service.py` 的 `build_snapshot`、`strength/scanner.py` 的 `_scan_sync`、`worker/tasks.py`）、`config.py` 供应商字段瘦身、`_endpoint_cache` 与 `TTLCache` 合并、worker 容器内存上限：需要单独立项。
- 前端 `MacroConditionsPanel` 的 refresh 属性签名、`colorPreference`/`themePreference` 骨架合并未做。

## 验证

- 后端全量 `pytest`：4606 项通过、6 项跳过（基线 4245 项通过，本轮新增约 360 项回归）。`python -m compileall -q backend/app` 无错。
- 前端：`VITE_API_MODE=live npm run build` 后 `frontend/` 与 `frontend-src/dist` 逐字节一致；`tsc -p tsconfig.app.json --noEmit` 0 错误；eslint 0 错误（2 条既有 Hook 依赖警告）；node 测试 1146 项通过；静态断言通过。
- 各区域的新回归测试在基线代码上绝大多数会失败（各子任务做了逐处退回验证）；审计时的探针在修复后不再复现问题。
- 没有做浏览器预览与生产验证；Playwright 套件由 CI 执行。

## 2026-09-26 合并前复核

- 补修美东绝对时刻调度的重启边界：夏令时结束时相邻两次 22:00 实际相隔 25 小时，原有 24 小时上限会保存提前一小时的唤醒时间。任务现在保存真实的下一时刻；监督器在任务开始前也保存该时刻，重启后以它限制异常远期时间。普通间隔任务的周期与恢复上限保持原值。
- 必要回归覆盖 23、24、25 小时的日期，以及任务完成后、运行中重启、手动唤醒、失败重试和异常远期时间。相关 32 项检查通过。新增用例在修正前的 `e4c6c62` 上复现了问题；本机未重复运行整套测试。
- 原提交 `e4c6c62` 的仓库自动检查实际为后端 4,612 项及 7 项子测试、前端 1,146 项通过；浏览器各组共 319 次通过、1 次跳过。上文 4,606 项是作者较早的本地记录。修正提交的最终检查以仓库结果为准。
- T1 重试状态的最大值保护防止持久化计数倒退；读取重试状态失败期间仍可能额外请求数据。这是既有问题尚未完全修复，不能把计数保护解释为故障期间也严格限制了请求次数。
- 新闻任务取消前发出的查询若晚于取消响应返回，取消提示仍可能短暂被旧状态覆盖，下一次查询会纠正。该界面竞态在基线版本已存在，服务端取消不受影响。
- 新闻库 v5 → v6 → v5 的读写兼容探针通过；旧任务已有付费响应编号时，新版能只取回结果而不重新提交。新版允许少于四家公司的财报影响结果，回退旧应用会使这些结果暂时无法通过旧校验；回退时必须保留发布后的任务账库，不能用旧备份覆盖新增付费记录。
