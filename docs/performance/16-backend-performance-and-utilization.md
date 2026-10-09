# 后端性能与服务器利用率

日期：2026-10-09 至 10-10。测量基线 `origin/main` `72f426f0`（即当时的生产版本）。分支 `claude/backend-perf-2026-10-09` 已变基到精简分支 `claude/backend-slim-2026-10-09`（`65ce111d`）之上，是链式 PR 的第二环。只改后端、测试、文档与 `config/personal.toml` 的注释。

文中「实验室」数字都在本机（macOS，Python 3.12，SQLite 3.53）测得，用的是按生产形态合成的数据，不是生产数据；「生产」数字来自部署前的源站计时与 worker 采样（`option-pro-perf-2026-10-09/evidence/`）。两者量级不同：生产慢得多，主要因为同一进程里多个请求争同一把全局解释器锁（GIL），实验室一次只量一个请求。部署后要按第十节在生产复测，才能下结论。

## 一、结论

- 慢接口的大头是重复计算：每个请求都重新校验整份首页快照、重算访客的技术结构、深拷贝两年日线、逐条跑新闻质量正则、全表扫两张大表算缓存指纹。改成按文件版本或写入版本记一次之后，实验室里这些接口的热读降到原来的几分之一到几十分之一。
- worker 空闲时每秒约 54 次读状态库、每 2 秒一轮 ai_jobs 与 catalyst_sync（各自开写事务），改为一个共享观察者每 0.5 秒读一次，加只读唤醒探针。按实测单次成本算，这部分空闲开销从约 1.5% 个核降到约 0.2%。**生产采样里 25–35% 的空闲底噪不是它造成的**（见第四节），要部署后复测才能定位剩下的来源。
- 锁：找到并去掉了三个长时间持有写锁的来源（每个后端请求在写锁里全表扫 ai-jobs.db 两遍；reconcile 每 120 秒在写锁里重算全部已结算任务；突破库修剪的探测按历史平方增长），另有一个只在首次清理时才会暴露的：删 AI 任务时外键逐行全表扫。
- 写盘：采样里 worker「每 20 秒 31MB 写完即作废」，量级与 reconcile 每 120 秒为排序整行任务建的约 100MB 临时文件相符，已改为不建临时表；催化剂连接的排序留在内存；公开个股数据与调度提示去掉 fsync。写入字节要部署后复测确认。
- 保留：新闻与焦点的 AI 历史从未清理过（手动 retention 从没运行），现在每轮备份成功后自动清理 30 天以前的，带付费回执的失败任务永不删除；突破库其实每次扫描都在按 90 天修剪，只是代价随历史平方增长，已修。首次运行与 VACUUM 见第七、十一节。
- 没有改成多进程 uvicorn，也没有上进程池，理由见第九节。

## 二、慢接口

| 接口 | 生产源站（部署前，匿名） | 实验室 改前 | 实验室 改后 | 提交 |
|---|---|---|---|---|
| `/api/stocks/{t}/technical` | 1.41s | 85.1 / 56.5 / 48.5 ms | 68.7 / 11.5 / 7.94 ms | d0028a02、9766dac1 |
| `/api/stocks/{t}/chart?range=1d` | 0.56s | 热 9.92 ms | 热 7.44 ms | 9766dac1 |
| `/api/market/indices` | 0.46s（最长 0.91s） | 67.5 / 70.2 / 0.36 ms | 23.4 / 23.2 / 0.36 ms | 1d0fe4bc |
| `/api/signals/market` | 0.37s | 76.1 / 95.7 / 0.47 ms | 21.8 / 23.8 / 0.42 ms | 1d0fe4bc |
| `/api/stocks/{t}` | 0.39s | 32.8 / 4.13 / 0.56 ms | 32.3 / 4.03 / 0.47 ms | （无实质变化） |
| `/api/breakouts/current` | 0.15s | 热 8.54 ms | 热 7.01 ms | 7c3c3050 |
| `/api/stocks/data/status`（200 只） | — | 768 / 52.5 / 34.1 ms | 735 / 43.0 / 23.3 ms | 7c3c3050 |
| `/api/catalysts/feed`（匿名） | 2.26s | 缓存冷 3.20s / 热 2.56s | 0.58s / 0.062s | 6a8be75a … fdbb2b7f |
| `/api/catalysts/tickers/NVDA` | 浏览器里 2.1s | 缓存冷 3.88s / 热 2.71s | 0.52s / 0.033s | 同上 |
| `/api/catalysts/hotspots` | 4.6s，最长 13.3s | 单请求几毫秒 | 任务快照改走主键 | 7519298d |

三个数字依次是：新进程第一次读、发布新版本后第一次读、同一版本的热读（中位数）。

做了什么：

- **首页快照**（1d0fe4bc）：解析后的 `public-home.json` 改为按资源懒校验，读指数不再先校验财报日历的上万个时间戳；每个资源的时间戳只解析一次。唯一的语义变化：某个资源的校验器抛「不可用」类异常时只丢这一项。
- **访客技术结构**（d0028a02）：访客路径不写 technical 缓存键，所以每个访客请求都重算一遍。改为按全部输入（日线、SPY 收盘、最后一根是否收盘、算法版本）记忆，最多 32 份。趋势线的穿越计数改成 numpy 数组运算，48 组随机行情的输出摘要与旧实现逐位相同。
- **个股资源回填**（9766dac1）：进程缓存还能用时，先读无载荷的摘要，只有磁盘上确有更新的版本才做完整读取（完整读取要深拷贝两年日线）。
- **调度状态与需求文件**（7c3c3050）：按 (inode, mtime_ns, size) 记忆解析结果。
- **催化剂读取**（路线 A 五项，各一个提交）：失效指纹改为触发器递增的版本行（单次 216ms → 不到 0.1ms）；新闻质量判定入库时算好存列；feed 只复制返回的那一页；窗口查询用 ETL 镜像表判定最新变更；入库按日志水位只读新变更。目标「本地 n10000 热路径 < 200ms」已达到（62ms）。
- **热点**：生产 4.6–13 秒是与 feed 的 CPU 计算争 GIL 排队，返回 126 字节是合法的「已核验热点为空」。feed 的 CPU 大头去掉后排队的根源就没了；另把任务快照查询从错选的 `idx_ai_jobs_ticker`（生产 132ms）改回按主键。

## 三、logo

`/api/stocks/{t}/logo`（ffaf4b5f）：访客未命中的 503 带 `private, max-age=300`；确认没有 logo 的 404 与非法代码带 `private, max-age=3600`；上游暂时不可用的 503 带 `Retry-After: 60` 与 `private, max-age=60`。成功路径原有的三天公共缓存不变。owner 回源慢是四个上游两路竞速、单请求 4 秒超时、整体 12 秒上限，访客路径不回源。

## 四、worker 唤醒、空闲 CPU 与锁

### 每小时唤醒次数（按代码推算）

| 来源 | 改前 | 改后 |
|---|---|---|
| 等待中的任务循环查手动动作表 | 15 个循环各每 0.5 秒一次（定时任务每次两条查询），约 54 次/秒、19.4 万次/小时 | 一个观察者每 0.5 秒一次：1 次状态库读 + 3 个唤醒探针读，7,200 轮/小时 |
| ai_jobs 空闲轮 | 每 2 秒，1,800 次/小时；每轮最多 4 个 `claim_due` 写事务 | 按下一条到期任务，最长 30 秒（有被车道挡住的任务时 5 秒复查），≤120 次/小时；`claim_due` 先只读判断，空队列不开写事务 |
| catalyst_sync 空闲轮 | 每 2 秒，1,800 次/小时，每轮一个 catalyst-cache.db 写事务 | 睡到下一个同步槽（默认 120 秒），30 次/小时 |
| sector_iv_refresh 空闲轮 | 每 5 秒，720 次/小时，每轮两个写事务 | 60 秒，有活时 5 秒，60 次/小时 |
| 心跳协程脉冲 | 每 50 毫秒，7.2 万次/小时 | 每 0.5 秒，7,200 次/小时 |
| 上面三项的任务状态写入（每轮两次，各一次 fsync） | 约 8,600 次/小时 | 约 420 次/小时 |

手动请求的拾起时延仍不超过一个观察周期（0.5 秒），满足「2 秒内」的要求；worker 启动后第一轮期间排进来的请求也一样（第十二节）。观察结果早于最近一次认领的不采信，避免认领后被同一条旧数据再唤醒一轮。

### 空闲 CPU 的算账

实验室单次成本（`time.process_time`，含所有线程）：状态库一次只读查询 0.23–0.30ms；一次任务状态写入 0.16–0.21ms（追加 1 个 WAL 帧）；空队列 `claim_due` 0.15–0.21ms；三个唤醒探针各 0.15–0.31ms；一次 `asyncio.to_thread` 往返 0.02–0.03ms；`get_effective_runtime_settings` 0.002ms（有缓存）。

- 改前：约 54 次读 × 0.25ms + 54 次线程往返 + 三个空闲轮的写事务与状态写入，约 15–20ms/秒，即约 1.5–2% 个核。
- 改后：每秒 2 轮 ×（1 次状态读 + 3 次探针读）+ 偶尔的空闲轮，约 2ms/秒，即约 0.2% 个核。

**这解释不了生产采样里 25–35% 的空闲底噪。** 采样的按线程拆分是：主线程 60 秒占 3 秒，一个线程 5.4 秒，十几个线程池线程各 0.7–1.2 秒。更可能的来源是不在任务轮次里显示的后台工作：`public_home` 每 30 秒 poll 时启动的公开个股刷新消费者（`asyncio.create_task`，不出现在 `running=` 里），一小时约 193 次拉取，每次拉三项资源、校验、编码、写盘；yfinance 自己的下载线程；以及 public_home 每轮重建。部署后按第十节复测，如果空闲仍高于 20%，临时在 `personal.toml` 关掉公开个股刷新十分钟再采样，就能确认是不是它。

### 写锁持有方与等待上限

| 写锁持有方 | 改前 | 改后 | 提交 |
|---|---|---|---|
| 每个后端请求新建 `AIJobRepository` 时的初始化（ai-jobs.db） | 写锁里全表扫两遍，6 万行库 240ms | 同进程同路径只初始化一次，按 `PRAGMA schema_version` 判定库结构没变；回填先在锁外只读判断，0.2ms | efb3dfa5 |
| 空队列的 `claim_due` | 每 2 秒最多 4 个空写事务 | 只读预检，不开写事务 | bd541176 |
| reconcile（catalyst-cache.db，每 120 秒） | 稳态 2.1–2.9 秒，最长写锁 1.7–2.4 秒 | 0.66–1.1 秒，最长写锁约 0.16 秒 | bfc3b18a、d7142087 |
| 突破库修剪（每次扫描后） | 每批 1.06 → 3.19 秒逐次变慢；空跑 0.31–0.33 秒 | 每批 0.49–0.55 秒持平；空跑 0.03 秒 | e0952c21 |
| AI 历史清理（每轮备份后） | 每批 500 行写锁 2.5–4.9 秒（外键逐行全表扫） | 每批中位 161ms、最长 244ms | e93bc8b6 |

各库的忙等上限不变（状态库 30 秒，其余按原连接设置）。按上表，改后单个写事务在实验室最长约 0.5 秒（突破库修剪一批），远低于这些上限。生产 24 小时里 ai_jobs 撞 `database is locked` 的三次（12:04Z、12:45Z 两次）都在 ai-jobs.db 上，对应前两行：后端每个催化剂请求新建仓库实例时都在写锁里全表扫（12:04Z 部署后 40 秒那次最典型），多个认领槽每 2 秒的空写事务与之相撞；reconcile 的写锁在 catalyst-cache.db 上，不是这几次的来源。

## 五、磁盘写入与临时文件

| 来源 | 处理 | 提交 |
|---|---|---|
| reconcile 的任务快照 `SELECT j.* … ORDER BY created_at DESC`：没有 created_at 索引，为排序把近 31 天每一整行放进临时 B 树（生产约 2 万多行 × 4.6KB，约 100MB），落在数据卷上的 `/data/sqlite-tmp` 再删除 | SQL 去掉排序，取回后在 Python 里按时间倒序排（行本来就要整份进内存）；执行计划不再有 TEMP B-TREE | bb7f71f2 |
| 催化剂连接（网页与 worker）的修订视图排序、链接物化 | `temp_store=MEMORY`。默认 72 小时窗口的中间数据在实验室约 5MB；owner 最长可选 365 天窗口，那时临时内存接近两张表的大小（生产链接表 66MB、修订表含正文） | 7519298d |
| 公开个股数据 `public-stock-data-v1/{T}.json`（183 个、各约 110KB，一小时重写约 193 次） | worker 写这些文件不再 fsync（文件与目录各一次）；调度提示 `status/`、`demand/` 也不再 fsync。owner 手动拉取仍保持两次 fsync | 5d288506 |
| 任务状态写入（每次一个 WAL 帧加一次 fsync） | 次数降为约 1/20（第四节） | bd541176 |

按生产节奏估算，公开个股数据去掉 fsync 每小时少约 770 次同步写，但写入字节不变（约 21MB/小时，约 0.35MB/分钟）。「内容没变就不重写」没有做：每次写入都带新的 `saved_at` / `as_of`，调度又按 `saved_at` 推算下次到期，不写会让同一只票立刻再次到期、反复拉取。真正能降写入量的是刷新节奏（优先级 0/1 的票在 04:00–20:00 ET 每 5 分钟、其余每 30 分钟、休市 6 小时），这是产品层面的取舍。

「ai-jobs.db 主文件每分钟都在 checkpoint」：改前后端每个请求都在 ai-jobs.db 上开初始化写事务、worker 每 2 秒开空写事务，这些不写页面，但会让 checkpoint 更频繁地被触发与等待。真正追加页面的是任务状态流转本身。部署后看 `ai-jobs.db-wal` 的增长速度确认（第十节）。

## 六、新加的进程内记忆与内存上限

| 记忆 | 上限 | 实测或估算大小 |
|---|---|---|
| `LocalCatalystIntelligence._public_job_memo`（已结算任务的公开投影） | 2 万条，满了清空 | 实验室两轮 reconcile 后 192 条、每条约 1.7KB；只有未发布或无法发布的链接会反复投影。最坏约 2 万 × 2–5KB = 40–100MB |
| `_accepted_news_audits`（已接受审计的指纹） | 5 万条 | 实验室 9,566 条、每条约 200 字节；最坏约 10MB |
| `_rejected_news_audits` | 5 万条 | 每条约 150 字节；最坏约 7.5MB |
| `stocks._technical_visitor_results` | 32 份 | 每份约 0.3MB，约 10MB |
| `public_stock_data._metadata_cache` | 4,096 个路径 | 每个是一个小字典，约 4MB 以内 |
| `_PublicHomeDocument`（首页快照的懒校验文档） | 放在原有 4 个路径的缓存里 | 原始字典与校验后的条目共享嵌套对象；实验室快照 3.6MB |
| ai-jobs `_READY_SCHEMAS` | 每个库路径一个整数 | 可忽略 |

生产 worker RSS 从 6.08GB 涨到 6.31GB 的时间点与 catalyst_sync、breakout 开始运行重合（13:13–13:14），之后持平，更像大块临时分配的高水位，而不是只增不减的缓存；本分支没有发现新的无界缓存。基线里提到的「worker 把日线写进无人读的 `_endpoint_cache`」在基线已经修过（`publish_to_cache=False`）。

## 七、数据保留

### AI 历史（ai-jobs.db）

- 改前：只有手动 retention 会调 `prune_scheduled_history`，它从没运行过。
- 改后（ccd94528）：maintenance 一轮里所有库都备份成功后清理一次，删除创建与完成都早于 max(`catalyst.journal_retention_days`, 30) 天的终态新闻与焦点任务；有备份失败要等重试成功。候选在写锁外只读选出，每 500 条一个短事务，批内按同一条件复核（期间被重排的不删）。
- 带付费回执（`provider_result_json IS NOT NULL`）的失败任务永不删除：被本地校验误判失败的结果只能靠这份回执用 `recover_ai_schema_results` 离线找回，生产 `backup_keep=1`，删了就无从恢复。生产失败任务共 4,108 行且仍在增加，其中带回执的行数要按第十一节的只读查询确认；它们会一直留在库里，量级是每行几 KB。
- 不会重排付费任务、不会让已发布的分析消失（8cf54530）：已发布结果与审计存在 catalyst-cache.db 的分析链接里。实验室把时钟拨到新闻 60 天后，清理 9,758 行，两轮带定时排队的 reconcile 排队 0；链接、审计、修订三张表的内容摘要与不清理的对照组相同；以过去的 as_of 看 owner 与访客 feed，摘要相同。
- 首轮清理的锁（e93bc8b6）：`retry_of_job_id` 是指向 ai_jobs 自身的外键，没有索引时每删一行就整表扫一遍。已加部分索引（独立版本 `ai-job-retry-lineage-index-v1`），部署后第一次初始化时建索引（要扫一遍 280MB 的表，生产预计几秒以内，只此一次）。
- 2,540 条 `budget_blocked`：是终态行，不占队列、不影响认领，到 30 天后按同一规则清掉。代码不批量改它们的状态。

### 突破库（optix.db）

- `prune_retention` 一直在每次扫描发布后按 `storage.retention_days`（90 天）修剪快照、结构、候选、上游快照与 T1 评估，且每个事件至少留下当前快照；问题是两处「有没有更新的完成快照」探测按全部扫描数乘以过期事件数增长，已改为按事件索引走（e0952c21）。改后删后各表行数、每事件保留行数分布、外键检查与改前逐项一致。
- `range_persistence_shadow`（生产 421MB、205,116 行，约每交易日 6MB）没有任何修剪路径；实验室里修剪扫描附件后它留下 59,130 行孤儿。本分支没动，建议另开一项：按 `scan_run_id` 跟随扫描附件一起删。

### 首次运行会删多少（按证据里的月度行数估算，需生产只读计数核实）

| 对象 | 规则 | 首次删除 | 释放的空闲页 |
|---|---|---|---|
| ai_jobs 新闻/焦点历史 | 30 天，带回执的失败任务除外 | 约 3.8 万行减去其中带回执的失败任务（news_impact 共 60,475 行；失败共 4,108 行，不全在 30 天以前，也不全带回执） | 约 130–150MB |
| 突破库扫描附件 | 90 天（现值） | 生产数据从 7 月中旬开始，10 月中旬起才陆续有可删行 | 约 0，上限约 0.3GB |
| 同上 | 若改为 30 天 | 约 12.7–13.5 万行快照（约 15.3KB/行）及其附件 | 2.3–2.5GB |
| 同上 | 若改为 14 天 / 7 天 | — | 2.9–3.1GB / 3.2–3.4GB |
| catalyst-cache.db | 已有的日志修剪 | — | 已有 563MB 空闲页 |

删除后文件不会变小，SQLite 只把页面标为空闲、留给以后的写入复用。要缩小文件需要停服务后手动 VACUUM（第十一节）。

### 备份的 I/O

maintenance 每 6 小时全量备份 4.4GB 的 optix.db（服务器 `backup_keep=1`）。可以做但本分支没做的两件事：库没变化就跳过（备份 API 照样要读全库，判定可以用文件身份加 WAL 大小）；大库每天一次、小库每轮。先做 VACUUM 与保留窗口的决定，备份量会随之下降。

## 八、2026-09-25 审查点名的事件循环阻塞点

- `worker_actions.py:327`：基线里这些路由已经是同步 `def`，在线程池里跑，不占事件循环。
- `earnings.py:916`：基线里已改为 `asyncio.to_thread(_queue_earnings_calendar_refresh)`。
- `/api/stocks/data/status` 的逐只深拷贝：基线已改为无载荷摘要；本分支又去掉了逐只解析状态文件（7c3c3050）。冷进程那一次 735ms 主要花在 200 份个股文档的首次结构校验上，没有改。

## 九、评估后没有做的

- **多进程 uvicorn**：进程内有 `_endpoint_cache`、`_endpoint_locks`、`_stock_pull_tasks` 的单飞、`_public_stock_pull_*` 的访客限流桶、logo 内存缓存、催化剂修订缓存、`main.py` 的限流器与 QuoteHub。多进程下单飞与限流会按进程数放大，缓存失效互不可见。逐一改造的成本远大于收益，而本分支的改动已让单个请求的 CPU 降到原来的几分之一。
- **进程池跑全市场扫描**：采样里 catalyst_sync 约 110%、breakout 100–130%，确实是 CPU 密集；但这两项的大头已经在本分支变小（reconcile 2.5s → 0.7–1.1s），而进程池要把数千只股票的两年日线来回序列化，每个子进程另带 200–400MB 的 pandas/numpy，同机还有 open-webui 4GB 与日股 worker 9GB。建议部署后复测，breakout 仍长时间超过 100% 时再只把逐只计算（纯函数）放进进程池。
- **调高 anyio 线程令牌（默认 40）**：后端常态 1–4% CPU，慢请求是 CPU 计算争 GIL，不是线程池排队；加线程只会让争抢更重。
- **`synchronous=NORMAL`**：ai-jobs.db 存着付费任务的提交与计费状态，掉电丢最后几次提交可能导致重复提交、重复花钱；状态库的写入次数已降为约 1/20，再改收益很小。
- **统一连接工厂、放大 `cache_size`、`mmap_size`**：后端有 17 个模块各自建连接，统一是大范围重构；排序最大的两处已单独处理（任务快照、催化剂连接）。突破库的读连接按「临时文件系统有界」设计并有专门测试，没有加 `temp_store=MEMORY`；它的研究与历史查询的临时数据量没有量过，不贸然放进内存。
- **放宽外部拉取并发**：yfinance 每批 8 只、最多 4 批并发，Massive 进程级 4 路。CPU 密集的环节不受它们限制，放宽只缩短等网络的部分，却有被供应商限流的风险；没有供应商额度信息前不动。
- **催化剂冷路径「先分页再物化」、热点绑定缓存、去掉自定义函数、`sys.setswitchinterval` 止血、worker 预计算 feed**：路线 A 的五项已经把热路径降到 62ms、冷路径降到约 0.5 秒；这几项要么改动大、要么要改 feed 的契约，留给新闻拉取路线定下来以后再看。

## 十、部署后在生产怎么确认

1. `/ready` 正常，`python -m app.worker --status` 里 15 个任务都在；maintenance 的 details 在第一轮备份成功后出现 `ai_history: {status: completed, retain_days: 30, deleted: N}`。
2. 源站接口计时：对第二节的接口各连打 5 次（`curl -s -o /dev/null -w '%{time_total}\n'`），与部署前的源站表比；催化剂 feed 与 tickers 要各在缓存失效后（worker 写入后）再量一次。
3. worker 空闲 CPU：用部署前同样的 `/proc` 采样法采 10–20 分钟，看没有任务在跑的采样点。
4. 写入：60 秒内 `/proc/<worker pid>/io` 的 `write_bytes` 与 `cancelled_write_bytes` 增量；`ls -l /proc/<worker pid>/fd | grep -c etilqs` 应长期为 0；`ai-jobs.db-wal` 的增长速度。
5. 锁：worker 日志里 `database is locked` 与 `worker task status write dropped` 的次数，与部署前 24 小时比。
6. 首轮清理期间：maintenance 那一轮的耗时，以及同一时段 ai_jobs 认领是否有等锁日志。

## 十一、需要手动做的事

### 部署前：只读核实首轮清理的行数

在正在运行的后端容器里只读查询（不要用 `compose run`）：

```bash
printf '%s' "import sqlite3
c = sqlite3.connect('file:/data/ai-jobs.db?mode=ro', uri=True)
sql = '''SELECT job_type, status, COUNT(*) FROM ai_jobs
WHERE job_type IN ('news_impact','market_focus')
  AND status IN ('completed','failed','cancelled','insufficient_context','budget_blocked')
  AND created_at < strftime('%Y-%m-%dT%H:%M:%SZ','now','-30 days')
  AND COALESCE(completed_at,updated_at,created_at) < strftime('%Y-%m-%dT%H:%M:%SZ','now','-30 days')
  AND NOT (status='failed' AND provider_result_json IS NOT NULL)
GROUP BY 1,2'''
print('will_delete', c.execute(sql).fetchall())
print('kept_failed_with_receipt', c.execute(
    '''SELECT COUNT(*) FROM ai_jobs WHERE status='failed' AND provider_result_json IS NOT NULL''').fetchone())
print(c.execute('SELECT COUNT(*) FROM ai_jobs WHERE retry_of_job_id IS NOT NULL').fetchone())" \
  | ./scripts/compose.sh exec -T backend python -
```

### 部署后：VACUUM 回收空间

首轮清理完成（maintenance details 里出现 `ai_history`）之后再做。VACUUM 会临时生成一份与原库差不多大的副本，先确认数据卷所在磁盘的剩余空间大于要处理的最大那个库。

```bash
# 1. 停后端与 worker
./scripts/compose.sh stop worker backend

# 2. 查当前镜像标签
docker inspect --format '{{.Config.Image}}' option-pro-worker-1

# 3. 逐个 VACUUM（把 <镜像> 换成上一步的结果；只处理需要的库）
for db in ai-jobs.db catalyst-cache.db; do
  docker run --rm -v option-pro_optix-data:/data -e SQLITE_TMPDIR=/data/sqlite-tmp \
    --entrypoint python <镜像> -c "import sqlite3
c = sqlite3.connect('/data/$db', isolation_level=None, timeout=60)
c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
c.execute('VACUUM')
print('$db', c.execute('PRAGMA page_count').fetchone()[0] * c.execute('PRAGMA page_size').fetchone()[0])
c.close()"
done

# 4. 按原来的方式恢复（保留 APP_COMMIT 版本戳）
./scripts/deploy.sh
```

- **绝不能用 `compose run` 做这件事**：它会触发整套镜像重建并丢掉 APP_COMMIT 构建参数。
- 预计：ai-jobs.db 从约 316MB 降到 170MB 左右；catalyst-cache.db 回收约 563MB。
- optix.db 只有在改小 `storage.retention_days` 并等一次扫描修剪完之后才值得 VACUUM（4.4GB，需要同等大小的临时空间，耗时数分钟）。

### 保留窗口要不要改小

`config/personal.toml` 的 `[storage] retention_days`（默认 90）决定突破库扫描附件留多久。改成 30 天约释放 2.3–2.5GB，但雷达的历史回看与研究读取会跟着变短。这是产品取舍，代码没有替你改。

## 十二、审查后的修正

独立审查之后补了下面几项，各自一个提交，都带回归测试，并在旧代码上确认过测试会失败：

- **带付费回执的失败任务永不删除**（a5d56a41）：见第七节。
- **启动第一轮期间排进来的手动刷新要等 120 秒**（f7440ae0）：唤醒探针原来要等任务第一轮跑完才启用，启用后第一次读数只记基线，于是第一轮期间排进来的请求被当成基线。现在监督器先读一次全部探针的基线再启动任务循环；本轮开始之前发出的读数整次丢弃，不再覆盖基线。测试里第一轮结束后两次观察以内就处理了该请求。
- **探针抛出任何异常都会让 worker 退出**（b289c8a3）：探针原来只捕获 `sqlite3.Error` 与 `OSError`，别的异常会结束共享观察者，监督器随即退出。现在任何异常都记一次 fallback 并跳过这次读数。
- **修订入库的水位可能越过尚未到齐的变更**（8b83b470）：水位原来取日志最大序号，同一次同步跨页序号不递增时（先 90 后 80），80 要等进程重启才入库。现在水位不超过新闻流的已完成水位。
- **版本表与触发器的建表脚本不是原子的**（37caa1f4）：脚本中途失败会留下版本表而缺触发器，读取方会信任一个某些写入不推高的版本。现在整段脚本在一个事务里执行。
- **回滚再前滚后质量存列可能失准**（f1125301）：存列时一并记下所依据的 `article_checked_at`，与当前值不一致就现算并回填。

变基时的处理：精简分支删掉了 ai_jobs 的旧迁移（`_migrate_v2`、补列、跨来源身份重写）和催化剂的旧数据导入，本分支在这些位置的改动按精简分支的结构合入：初始化不再有 `migrated` 标记，「新库或预读失败」时才做锁内全量回填；reconcile 写事务之后只保留水位的采纳。两份新库注册表的固定清单分别登记了 `ai-job-retry-lineage-index-v1` 与 `optix-local-catalyst-store-version-v1`。

## 附：提交清单

| 提交 | 内容 |
|---|---|
| bd541176 | worker 共享需求观察者与唤醒探针，空闲间隔，`claim_due` 只读预检，心跳放宽 |
| efb3dfa5 | ai-jobs.db 初始化按进程记一次，回填先锁外只读判断 |
| ffaf4b5f | logo 未命中与错误应答的缓存头 |
| e0952c21 | 突破库修剪的「更新快照」探测按事件索引走 |
| 6a8be75a | 催化剂修订视图的失效指纹改为版本行 |
| ea13032e | 新闻质量判定存列 |
| 8cac1029 | feed 与 batch 只复制返回的那一页 |
| fdbb2b7f | 窗口查询用 ETL 镜像表判定最新变更 |
| bfc3b18a | 修订入库按日志水位 |
| d7142087 | reconcile 不再每轮重算已结算任务的投影与审计 |
| 7519298d | 热点任务快照按主键取；催化剂连接排序留在内存 |
| ccd94528 | maintenance 定时清理 AI 历史 |
| 1d0fe4bc | 首页快照按资源懒校验 |
| d0028a02 | 访客技术结构按输入记忆；穿越计数数组化 |
| 9766dac1 | 个股资源回填先看无载荷摘要 |
| 7c3c3050 | 调度状态与需求文件按文件版本记忆 |
| 8cf54530 | 回归测试：清理历史不重排、不隐藏分析 |
| 5d288506 | 公开个股数据与调度提示不再 fsync |
| bb7f71f2 | reconcile 任务快照在 Python 里排序 |
| e93bc8b6 | `retry_of_job_id` 部分索引 |
| 866cbafe | 性能记录 16 与 personal.toml 注释 |
| b289c8a3 | 唤醒探针的任何异常只跳过这次读数 |
| f7440ae0 | 探针在第一轮之前读基线，本轮开始前的读数不当基线 |
| a5d56a41 | AI 历史清理永不删除带付费回执的失败任务 |
| 8b83b470 | 修订入库水位不超过已完成水位 |
| 37caa1f4 | 版本行与触发器一个事务建好 |
| f1125301 | 质量存列记下正文检查时间，不一致就现算并回填 |
