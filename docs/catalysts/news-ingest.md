# 新闻与经济日历的本地采集：运维说明

本文说明 worker 怎样直接抓取新闻源和经济日历、怎样切换与回滚，以及上线时需要在服务器上手动处理的事项。设计依据见 `option-pro-perf-2026-10-09/news-ingest-design.md`（路线 A）。

## 现在由谁写入

`catalyst_sync` 任务按 `config/personal.toml` 的 `[catalyst].news_source` 选择写入方：

- `local`（默认）：worker 自己抓取下表的来源，在本地判重、分配新闻编号（`news_id`）和变更序号（change sequence），整页写入 `macrolens_etl_*` 六张表。
- `macrolens`：沿用旧的 MacroLens ETL 远端增量同步，只为回滚保留，下一个版本删除。

两种写入方写的是同一组表、同样的格式，新闻流、热点、定时分析读取这些表的方式没有变化。采集器自己的记账表以 `catalyst_ingest_` 开头，有独立的版本号 `catalyst-ingest-v1` 和校验和，不影响 `macrolens-etl-local-v2` 的校验和。

## 来源清单

| 来源键 | 卡片名称 | 地址 | 密钥 | 默认间隔 | 增量方式 |
|---|---|---|---|---|---|
| `massive` | Massive | `api.massive.com/v2/reference/news` | `MASSIVE_API_KEY` | 300 秒 | 以已见最新发布时间减 6 小时为下限，每页 1000 条，跟随官方 `next_url` 游标，最多 5 页；首次回填 24 小时 |
| `finnhub_general`、`finnhub_forex`、`finnhub_merger` | Finnhub · general 等 | `finnhub.io/api/v1/news?category=…` | `FINNHUB_API_KEY` | 各 300 秒 | 每次请求先占账户共享额度（每分钟 60 次），占不到就顺延，不算失败；限流时向共享额度发布冷却 |
| `globenewswire` | GlobeNewswire | 官方「上市公司」RSS | 无 | 300 秒 | 条件请求（ETag、Last-Modified），只收英文稿件 |
| `google_news_stocks`、`google_news_commodities`、`google_news_macro` | Google News · stocks 等 | 三条固定搜索 RSS | 无 | 各 900 秒 | 每条取前 30 条，标题去掉末尾「 - 发布方」 |
| `seekingalpha_breaking` | Seeking Alpha · market currents | `seekingalpha.com/market_currents.xml` | 无 | 300 秒 | 条件请求；摘要留空，代码取自 symbol 分类 |
| `seekingalpha_daily` | Seeking Alpha · Wall St. Breakfast | `seekingalpha.com/tag/wall-st-breakfast.xml` | 无 | 21600 秒 | 同上 |
| `forexfactory` | Forex Factory | `nfs.faireconomy.media/ff_calendar_thisweek.json` | 无 | 600 秒 | 条件请求；只在事件集合变化时写新快照 |

没有配置密钥的来源照常列在来源卡里，状态为「异常」、备注 `not_configured`，不会被请求。NewsAPI、GNews 没有密钥，没有接入。

所有请求共用一个客户端：忽略环境里的代理设置，只跟随同一主机内的一次重定向，正文按上限读取（RSS 与日历 1MB，Massive 与 Finnhub 5MiB），单次请求 15 秒超时，一轮最多 60 秒，超时的来源顺延到下一轮。RSS 拒绝带文档类型声明（DTD）的文档。

2026-10-09 本机实测：Google News、Seeking Alpha、Forex Factory 可以访问；GlobeNewswire 对本机和另一出口都返回 403，生产出口能否访问还没有验证。Massive 的新闻里每天有约 100 条 GlobeNewswire 稿件，这个来源失败时覆盖面损失有限。

## 调度与失败

- 任务每个同步周期（`[catalyst].sync_seconds`，默认 120 秒）醒来一次。到期的来源在这一轮抓取，所以 300 秒的来源实际约每 360 秒抓一次。
- 同一主机两次请求至少相隔 60 秒。Google 的三条搜索、Finnhub 的三个类别、Seeking Alpha 的两条订阅因此分散在相邻的几轮里。
- 失败的来源按间隔指数退避，最长一小时（间隔本身更长的来源按间隔），服务端给出 `Retry-After` 时至少等那么久。
- 站长在页面上手动刷新新闻时，所有新闻源立即视为到期（忽略间隔和退避，但仍遵守 60 秒的主机间隔），日历不动；刷新日历只跑日历；刷新来源状态两者都跑。
- 任务状态：
  - 本轮到期的新闻源至少一个成功，任务就是 `idle`，失败的来源写在 `details.errors["source:<来源键>"]`，状态表 news 行照常推进。
  - 本轮到期的新闻源全部失败，任务 `degraded`，状态表 news 行记下第一个错误码。
  - 日历抓取失败时最新快照继续有效；只有最近一次成功确认超过 24 小时（或从未有过快照）才让任务降级。
- 部署脚本要求 `catalyst_sync` 不是 `degraded`。所以单个来源挂掉不会挡住部署，全部新闻源同时失败才会。

## 判重与来源印证

判重先按内容哈希（标题规范化后加 UTC 发布日期，公式与 News-feed 一致）和规范网址精确查找，再在同一个 UTC 日期内按标题相似度查找。切换前已入库的新闻再被抓到时算出同一个哈希，挂到原来的编号上。模糊比较前会去掉标题末尾形如「 - Reuters」的发布方名称，这一步不影响内容哈希。

另一个来源报道同一条新闻时，采集器只记一条观察记录。只有同时满足下面三条，才给这条新闻追加一条变更（只增加来源和代码，标题、摘要、网址不变）：

1. 这条新闻还没有任何分析链接；
2. 首次出现不超过 6 小时；
3. 之前没有因为印证追加过。

切换前已经存在的新闻永远不追加。这样做是为了不让已付费的分析和它对应的修订脱钩。

日志修剪删掉一条新闻后，采集器随即删掉它的判重键和模糊比较记录；观察记录再保留到保留期（`journal_retention_days`）满，这样仍挂在订阅里的旧稿不会在每次修剪后被当成新稿重新入库。代价是：分析之后才到的来源不会提高这条新闻的「来源数」，热点的来源广度分和「只看多来源」筛选会略偏低。观察记录里保留了完整的来源，以后需要时可以改为从观察记录汇总。

## 经济日历

- 周文件只包含本周，没有实际值字段；实际值仍由读取接口从 TradingView 补齐（`economic_calendar_actuals.py`）。
- 只保留八个主要币种、影响为高或中（或已有实际值）的事件。`event_id` 公式与 News-feed 相同，焦点周期里的日历事件组编号在切换前后保持连续。
- 事件集合没变时，只更新最新快照的 `data_through` 和状态表的检查点，不改快照的 `as_of`。
- 每次成功刷新后删除 7 天前的快照，每轮最多删 500 份，读者正在用的那份永远保留。生产上积压的约 162MB 会在上线后的几个小时内删完。

## 配置

```toml
[catalyst]
news_source = "local"

[catalyst.sources]
massive = { enabled = true, interval_seconds = 300 }
finnhub = { enabled = true, interval_seconds = 300 }
globenewswire = { enabled = true, interval_seconds = 300 }
google_news = { enabled = true, interval_seconds = 900 }
seekingalpha_breaking = { enabled = true, interval_seconds = 300 }
seekingalpha_daily = { enabled = true, interval_seconds = 21600 }
calendar = { enabled = true, interval_seconds = 600 }
```

- 间隔范围是 60 到 86400 秒；只写 `enabled = false` 时沿用默认间隔。
- 服务器上的 `personal.toml` 没有这些行时，按上面的默认值运行，也就是直接使用本地采集。
- 密钥只用已有的 `MASSIVE_API_KEY` 与 `FINNHUB_API_KEY`（`secrets.env`），本路线不需要新密钥。

## 首次启动

worker 第一次以 `local` 启动时，在一个事务里完成：

1. 建采集器私有表；
2. 新闻编号从镜像表、日志、墓碑表、修订表、分析链接表里的最大值往后续，变更序号同理（也包括状态表的已完成序号）；
3. 把镜像表里未删除的新闻登记为「切换前已存在」，写入判重键和模糊比较池；
4. 清掉状态表里远端同步留下的翻页游标。

此后每次启动都会补登记还没登记过的镜像行，并清掉残留游标。第一轮 Massive 从 24 小时前开始回填，大部分会命中已有新闻、只记观察；News-feed 漏抓的会以新编号出现，并进入 72 小时的定时分析候选，这一次会多一些分析。

## 状态与排障

- `/api/catalysts/status` 的 `sources` 每个来源一张卡：`key`、`source`（卡片名称）、`status`（`active` 或 `degraded`）、`lag_ms`（距最近一次成功的毫秒数）、`last_success_at`、`items_last_24h`（近 24 小时首次见到的条目数，日历为近 24 小时的事件数）、`note`（最近的错误码）。前端在 `sources` 非空时改用来源卡。
- worker 状态里 `catalyst_sync` 的 `details.streams.news` 有本轮到期、成功、失败、顺延的来源数，以及新增、印证追加、观察的条数；`details.streams.calendar` 有是否写了新快照、事件数和清理掉的快照数。
- 常见错误码：`http_403`、`http_503` 等（来源返回的状态码）、`timeout`、`network_error`、`rate_limited`、`invalid_response`（格式不对，例如被拦截页替换）、`response_too_large`、`redirect_refused`（跨主机重定向）、`not_configured`（没配密钥）。

## 回滚到 MacroLens

本地铸造的新闻编号和变更序号会与远端 ETL 自己的编号相撞，而本地已推进的检查点会让远端同步跳过切换期间的变更。所以回滚不能只改配置：

1. 部署本版本之前，先手动备份 `catalyst-cache.db`。服务器的 `storage.backup_keep = 1`，自动备份只保留最新一份，很快就会被切换后的内容覆盖。
2. 需要回滚时：停 worker，用上线前的备份恢复 `catalyst-cache.db`，把服务器 `personal.toml` 的 `news_source` 改为 `"macrolens"`，再用 `./scripts/deploy.sh` 恢复服务。macrolens-etl 容器要仍在运行，`MACROLENS_URL` 与 `INTERNAL_API_TOKEN` 要仍然配置。
3. 如果没有恢复备份就切回 `macrolens`，而采集器已经写过本地表，`catalyst_sync` 会以 `catalyst_rollback_requires_restore` 降级，不会向远端发任何请求。采集器还没写过任何东西（例如第一轮所有来源都失败）时，可以直接切回。

## 上线时的手动事项

1. 部署前备份 `catalyst-cache.db`（见上一节第 1 条）。
2. 本版本修改了 `config/personal.toml`（新增 `news_source` 与 `[catalyst.sources]`）。服务器上的这个文件带着生产修改，更新代码时先保存本地修改，切到新提交后再恢复，不要用 `git checkout --force`。服务器文件里不加新行也可以，默认就是本地采集。
3. 部署后看 `catalyst_sync` 第一轮的状态：至少一个新闻源成功才是 `idle`；如果全部失败，部署脚本的 worker 检查会失败，先查各来源的错误码（多半是出口网络）。
4. 确认本地采集稳定一周后再停 macrolens-etl 容器（127.0.0.1:8000）。在删除远端代码的下一个版本上线之前，不要删 `MACROLENS_URL` 和 `INTERNAL_API_TOKEN`：旧代码要求两者成对出现，`./personal.sh secrets validate` 在配置了令牌时还会检查远端健康，容器停了会报失败。
5. 日历旧快照删完之后（上线约半天），择机停机做一次 `VACUUM`：先停 backend 和 worker，再用当前部署的镜像起一个裸容器执行 `docker run --rm -v option-pro_optix-data:/data --entrypoint python option-pro:<当前部署的提交号> -c "import sqlite3; c = sqlite3.connect('/data/catalyst-cache.db'); c.execute('VACUUM'); c.close()"`（镜像标签是 `option-pro:` 加完整提交号，用 `docker ps` 看正在运行的那个），最后用 `./scripts/deploy.sh` 恢复。不要用 `compose run` 做这类维护。
6. nginx 里 MacroLens 的 426 守卫与 focus-context 的 location（宝塔下的 `optix.conf`）在删除远端代码之后清理。
