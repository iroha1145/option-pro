# 第二轮：按线上访问记录和数据库清理闲置代码

基线：`f4b3cb0e`（PR #210 的头）。2026-10-05 只读查看了生产服务器，没有写入、刷新或部署。本轮未部署。

## 判定依据

- **访问日志**：站点的 nginx 日志覆盖 2026-07-25 至 2026-10-05，约 18.9 万行。去掉爬虫和扫描器后按接口归并，再与后端全部路由逐条对照。
- **数据库只读查询**：各库的表、行数、最后写入时间和迁移登记版本。查询都用 `mode=ro` 打开。
- **线上接口响应**：直接读 `/api/strength/scan`、`/api/strength/stocks/{t}`、`/api/stocks/{t}`、`/api/strength/sectors`、`/api/breakouts/current` 的字段。
- **研究分支**：#173、#174、#176、#184、#189、#209 仍在进行，所有删除都对照过它们相对 main 的改动，没有一处重叠。#163、#164、#166 已确认结束，#210 对旧扫描器的删除照旧。

判定规则：只有「72 天零访问」不算死代码，还要同时满足前端、后台任务、脚本都不调用，或者线上数据证明这项功能拿不到数据。只有站长才会用、但界面上有入口的功能，归为「用得少」，保留。

## 删除

### 没有调用方、72 天零访问的接口

| 接口 | 说明 |
| --- | --- |
| `GET /api/account/me` | 身份改由 `/api/access/status` 的 `account` 字段提供 |
| `POST /api/ai/analyze-alerts`、`GET /api/ai/earnings-correlation` | 兼容桩，只返回固定 409 |
| `POST /api/ai/jobs/earnings-impact` 及其请求整理函数 | 财报分析只经按报告提交的接口和定时任务入队 |
| `GET /api/sectors/{id}/heatmap` | 板块热力视图已在 #207 删除 |
| `GET /api/settings` | 密钥配置状态，没有界面 |
| `GET /api/worker/actions`（列表） | 界面只按编号轮询单个任务 |
| `GET /api/macro/conditions/factors/{id}/history` 及服务、仓库读取 | 前端只在注释里提到 |
| `PUT`、`POST /api/account/watchlist` 和 `DELETE /api/account/watchlist/{ticker}`，以及 `add_ticker`、`remove_ticker` | 前端只用 `PATCH` 和删除撤销接口；日志里 `PATCH` 从 09-07 开始出现，最后一次 `POST` 是 08-13，最后一次 `DELETE` 是 09-06 |
| `POST /api/catalysts/market-focus-cycles/{id}/cancel` 全链 | 没有入口 |
| `GET /api/options/unusual`、`GET /api/stocks/{t}/signals` | 见下一节 |

### 后台一直在生成、没人读的数据

首页快照里的 `unusual`（每 30 分钟约 30 次 Yahoo 请求）和 `focus_signals`（每 15 分钟一次），唯一读者是上表最后两个接口。生成逻辑、两个接口和它们在发布闸门里的必需项一起删除。

线上快照文件里还存着这两项，所以资源规格里的名字、校验器和 `unusual_seconds` 配置暂时保留，否则现有文件整份解析失败、首页公开读取全部 503。发布时现在只带走仍在生成的资源，部署后第一次发布就会把这两项清出文件，之后可以再删规格和配置（见「后续」）。

### 线上数据里没有的显示

收盘选股引擎的扫描行不带任何宏观字段（`/api/strength/scan` 响应里没有一个含 `macro` 的键），只有已删除的旧扫描器会附加它们。因此删除：

- 选股页的「宏观适配」开关、顺风/中性/逆风筛选、被排除数量提示、表格列和卡片徽标，展开行里的宏观面板。
- 「技术 − 结构性宏观」差值：前端字段、同期判断、面板里那一行；后端 `macro_technical_gap`、`shadow_ranking_adjustment`、`STRENGTH_SHADOW_CAP`、`MacroFitReader.structural_score`；`/api/strength/stocks/{t}` 信封里恒为空的 `macro_linkage`。

个股详情、板块、突破雷达的宏观读数都来自线上真实字段，保留。大盘页「技术 × 结构性宏观」卡片自己求差，保留。

### 已在生产执行完毕的一次性迁移

生产查询结果：ai-jobs 已登记到 `ai-jobs-v4`，催化剂本地库到 `optix-local-catalyst-v7`（含时间戳统一），宏观库到 v2 且 ETF 表已有 `value_hash`，运行设置是第 2 版且唯一一份备份也是第 2 版，四个旧后台服务的容器都不存在。

| 删除 | 生产事实 |
| --- | --- |
| 旧催化剂表导入 | 审计表 2007 行全部是 rejected，从未导入成功；此前每次同步（约 2 分钟）都要整表重读两张旧表 |
| 催化剂补列和时间戳统一 | 列已存在，版本行已写入 |
| ai_jobs 的 v2 表迁移、补列、来源身份迁移 | 已登记 v4，三列都在 |
| 宏观 ETF v1→v2 | v2 已登记，`value_hash` 已有 |
| 运行设置 V1→V2 与 `.pre-v2` 保存、恢复 | 当前文件和备份都是第 2 版，没有旧字段 |
| 旧 `.env` 迁移工具、`setup.sh` 的迁移分支 | 生产已有 `machine.env` |
| 后台任务布尔型刷新请求 | 真实实现只返回字典或空 |
| `deploy.sh` 停旧后台服务 | 四个旧服务没有容器；`--remove-orphans` 本来就会清 |

保留的边界：催化剂库 `_SCHEMA` 里审计表的建表语句不动，它参与校验和，改了会让生产启动失败；被删迁移对应的登记行仍按原样写入，新库与生产库的登记表一致。每个库都新增了「新建库结构与线上一致」的测试。

### 其他

没有生产调用方的薄包装和声明：`DiscoveryProvider`、`build_client`、`require_symbols`、`SyncRun`、`iso_date`、`a0_companion_for_admin_default`、`_expected_move_from_chain_snapshot`、`_configured_allowed_hosts`、`get_expirations`、`apply_volume_confirmation`、`_cached_endpoint`。只有测试在用的，测试改为直接调用共享实现。

## 保留

- **用得少但有入口**：运行设置的修改、回滚、历史（站长管理面板）；K 线画线的写入接口（线上 0 行，但功能可用）；退出登录；自选删除撤销（#208 刚上线）。
- `/api/strength/stocks/{t}` 的 404 占九成以上：它只回答已发布选股名单里的代码，属设计行为。
- `GET /api/diagnostics/cache` 及缓存计数：站长可能在服务器上直接调用，日志看不到；只删接口会留下只写不读的计数代码，要删就整簇删。
- 旧强度快照读写链（`strength-snapshot-v1-*.json`）：写入端已无调用，但后台每天仍按这些冻结文件刷新 4 组参数变体（`variant_refresh_published: 4`），删了会改变线上行为。#210 也有意保留。
- 突破雷达 `_migrate_to_v3`：生产已是 v3，但当前建表语句由两份旧语句推导，删除要先证明校验和逐字节不变，收益不抵风险。
- `config.py` 里旧 MacroLens 名称的拦截字段：三个研究分支改过这个文件。

## 后续

1. 部署本轮后，确认线上快照文件里已没有 `unusual`、`focus_signals`，再删资源规格、校验器、参数分支和 `unusual_seconds`（同时改 `config/personal.toml` 和生产机上的同名行）。
2. 生产数据（需要站长确认后执行，先备份）：
   - `catalyst-cache.db` 的 26 张旧表和审计表，迁移文档承诺保留到 2026-10-15；删后 VACUUM，库现在 1.38 GB，其中旧热点表 37 万行。
   - `catalyst-local.db`、`catalysts.db`、`macrolens.db`（各 4 KB）和 `optix.sqlite3`（0 字节），代码不再引用。
   - `runtime-settings.json.pre-v2`。
3. `retention` 任务只能手动触发，界面没有按钮，所以 AI 任务历史从未清理过（ai-jobs.db 285 MB、约 6.5 万行）。应改为定时运行。
4. 仓库默认 `range_persistence_mode = "shadow"`，生产是 `active`。
5. 未登录的访问会请求 `/api/view-preferences` 并得到 401（72 天 6,803 次，成功只有 42 次）；`/favicon.ico` 也返回 401。

## 检查记录

- 后端：5,002 通过，6 跳过（基线 5,066 通过，6 跳过；差额都是只测已删代码的用例）。
- 前端：1,178 通过；正式构建、产物逐字节一致、静态断言、代码规范通过（2 条原有雷达依赖提示）。
- 浏览器测试：见 PR 说明。
