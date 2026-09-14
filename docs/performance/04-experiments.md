# 实验记录

每轮写：瓶颈证据、假设、改动、前后指标与波动、功能验证、风险、保留/回滚、下一步。

## Round 0 — 建环境与基线（已冻结）

- **证据**：`origin/main` SHA `31e8955d`。本机 4 核 / 15 GiB。代码审查显示 feed 整窗物化 + 首屏双 feed。
- **假设**：10k 档新闻 feed 与新闻页 `news_content_ready` 会显著超过预算；status 本身有界。
- **改动**：仅文档、种子与测量脚本，无业务代码。
- **指标**（实验室，n=20 对，mobile-ref，CDP 一次限速）：冷 `news_content_ready` p50 2348 / p75 2381；热 p50 1084 / p75 1110（热预算 ≤1000 **未达标**）。LCP 冷 p75 2088 / 热 1044。API feed 72h/12 HTTP p50 98ms；进程内热 p50 60ms。原始结果：`/opt/cursor/artifacts/perf/browser-baseline-mobile-ref.json`、`api-baseline-n10000.json`、`feed-inprocess.json`。
- **决定**：先改挡住挂页的串行 RTT 与首屏抢带宽请求，再动 SQL 物化。
- **下一步**：Round 1 业务改动 → 同条件复测。

## Round 1 — 身份确认与非首屏请求让路

- **证据**：Owner `liveStatus` 先 `/access/status` 再串行 AI/runtime，180ms RTT 下多挡一轮才挂 `<Outlet/>`。
- **改动**：`accessApi.identity()` 只读 `/access/status`；`afterLoadIdle` 初版用 `requestIdleCallback`；feed 不再每次 `status()`。
- **指标**：`/opt/cursor/artifacts/perf/browser-r1-mobile-ref.json`。热 ready p75 802（达标）；冷 p75 2778（退化，超 2500）。72h feed duration p50 523ms（基线 228ms）。请求数 49 vs 31。
- **决定**：保留身份拆分。idle 在等网络时会立刻回调，必须改成固定延迟。

## Round 1b — idle 改为固定延迟（保留）

- **证据**：idle 抢连接；feed 从 ~190ms 被挤到 ~480ms。
- **改动**：`afterLoadIdle` 只用 `setTimeout(delayMs)`；预取 8s。
- **指标**：`/opt/cursor/artifacts/perf/browser-r1b-mobile-ref.json`（n=20）。

| 指标 | 基线 | R1 | R1b | 预算 |
|---|---|---|---|---|
| 冷 news_content_ready p50/p75 | 2348 / 2381 | 2713 / 2778 | **2317 / 2334** | ≤2500 |
| 热 news_content_ready p50/p75 | 1084 / 1110 | 781 / 802 | **772 / 775** | ≤1000 |
| 冷 LCP p75 | 2088 | 2140 | **2064** | ≤2500 |
| 热 LCP p75 | 1044 | 728 | **528** | ≤2500 |
| 冷 CLS p75 | 0.044 | 0.060 | **0.046** | ≤0.1 |
| 冷 72h feed duration p50 | 228 | 523 | **231** | — |
| 冷请求数 p50 | 31 | 49 | **29** | — |
| 冷 transfer p50 | 450KB | 495KB | **446KB** | — |

热路径相对基线 p75 下降 30.2%（1110→775），达到「原卡顿路径 ≥30%」目标。首条标题始终为 `第9600条快讯`，count 仍为 9566，未缩数据。热样本无 feed 请求：同上下文 120s 新鲜窗口命中内存缓存；「缓存内容可用」= 775ms，「按既有时效再刷新」见手动刷新测量。冷路径有 1/20 的 27.5s 离群（基线亦有 29.6s），p75 不受其拉动。
- **功能验证**：前端 928 pass / 1 skip；催化后端此前已通过。
- **决定**：保留。
- **下一步**：Owner feed 整窗 `_item()` 实验；交互/弱网/其它页。

## Round 2 — Owner 复用匿名 revision 缓存（回滚）

- **证据**：Owner `_active_revision_bundle` 对 `current_request_is_owner()` 直接 `items=None`，每次 `_item()` 整窗。
- **假设**：复用匿名 item 缓存、只叠 job 状态，10k 热路径会明显下降。
- **结果**：进程内 n=10000 window=72 limit=12：基线冷 184 / 热 60；整窗 overlay 冷 235 / 热 65；只叠当前页冷 224 / 热 50。热路径最多快约 10ms，冷路径更慢。浏览器侧 180ms RTT 下用户不可见。
- **决定**：**回滚**。无足够可感知收益，增加正确性表面积。
- **下一步**：不继续改 feed 物化，除非压力测试证明服务端 CPU 是瓶颈。转向交互、弱网、其它模块与长稳。

## Round 2b — 交互口径修正（仅脚本，业务未改）

- **证据**：旧脚本点覆盖层 button、筛选点 button「24 时」、只等到任意 `article h3`，会把骨架或旧 72h 列表算完成。
- **改动**：点 `article h3`；等到 dialog 内真实 `h2`；筛选等到 `window_hours=24` 的新 feed。
- **指标**（R1b 代码，mobile-ref，n=20，实验室非 INP）：`/opt/cursor/artifacts/perf/browser-interact-n20.json`

| 指标 | p50 | p75 |
|---|---|---|
| 抽屉真实标题 | 386 | 394 |
| 筛选到 24h 结果可用 | 437 | 445 |
| 滚动 longtask 合计 | 0 | 0 |

首条/筛选后标题均为 `第9600条快讯`。横向溢出：无。抽屉耗时含详情 GET（约 180ms RTT）。
- **决定**：保留脚本。下一轮用列表 seed 立刻画标题，并继续预取身份/默认 feed。

## Round 3 — 启动预取 + 路由块提前 + 抽屉 seed（待复测）

- **证据**：冷路径在 JS 解析后仍串行 identity → 挂页 → feed；抽屉先 `setItem(null)` 再等详情。
- **假设**：HTML 阶段发出 `/access/status` 与默认 72h/12 feed，主包执行时预取 Catalysts chunk，列表项作为抽屉 seed，可去掉两轮 RTT 等待且不缩数据。
- **改动**：`theme-boot.js` 预取；`consumeBootPrefetch` 按完整 URL 消费一次；`prefetchRouteChunk`；NewsDrawer 用 seed 立刻画真实标题，仍请求 `/catalysts/news/{id}`。
- **指标**（实验室 mobile-ref，n=20）：`/opt/cursor/artifacts/perf/browser-r3-mobile-ref.json`、`browser-interact-r3.json`

| 指标 | R1b | R3 | 预算 |
|---|---|---|---|
| 冷 news_content_ready p50/p75 | 2317 / 2334 | **1614 / 1642** | ≤2500 |
| 热 news_content_ready p50/p75 | 772 / 775 | 822 / 829 | ≤1000 |
| 冷 LCP p75 | 2064 | **1556** | ≤2500 |
| 热 LCP p75 | 528 | 780 | ≤2500 |
| 冷 CLS p75 | 0.046 | **0.0008** | ≤0.1 |
| 抽屉真实标题 p75 | 394 | **287** | 实验室，非 INP |
| 抽屉详情 GET p75 | （未分列） | 444 | 仍拉 `/news/{id}` |
| 筛选 24h p75 | 445 | 445 | 未改 |

冷路径相对 R1b p75 −29.6%（2334→1642），相对基线 2381 −31.0%。首条标题 20/20 为 `第9600条快讯`。热路径 p75 +54ms（+7%），仍 ≤1000；根因是 theme-boot 每次整页进入都会发出默认 feed，resource 计时可见该请求。冷路径仍有 1/20 的 37.8s 离群（基线同类），p75 不受其拉动。
- **功能验证**：前端 931 pass / 0 fail；静态断言通过；详情接口仍发出。
- **决定**：**保留**。热路径 7% 低于冷路径收益，且未破预算。
- **下一步**：弱网 n=20；筛选打开时预取 24h；其它页与压力。

## Round 3 弱网档（实验室，非日本实测）

- **条件**：390×844，CPU 6×，下载 1.6Mbps / 上传 0.75Mbps / RTT 300ms，CDP 一次限速。n=20。
- **指标**：`/opt/cursor/artifacts/perf/browser-r3-mobile-weak.json`

| 指标 | p50 | p75 | 备注 |
|---|---|---|---|
| 冷 news_content_ready | 4052 | 4089 | 弱网档，不适用 mobile-ref 的 2.5s 预算 |
| 热 news_content_ready | 1364 | 1371 | 弱网档，不适用 1.0s 预算 |
| 冷 LCP p75 | — | 3964 | |
| 冷 CLS p75 | — | 0.0008 | |

离散很小（冷 max 4124），标题 20/20 `第9600条快讯`。这是人工弱网档，不是日本到美国实测。
- **决定**：记录缺口；不把弱网超 2.5s 写成 mobile-ref 失败。

## Round 4 — 筛选展开预取 24h（测量口径已修正，待 r4b）

- **改动**：点开「筛选」时 `offerBootPrefetch` 默认 24h/12；切换时 `consumeBootPrefetch`。
- **作废**：`browser-interact-r4.json` 的 filter≈1830ms。当时 `waitForResponse(window_hours=24)` 误等到 `newsToday` 的 24h/50，不是列表。
- **指标**（实验室，展开筛选后思考 800ms，n=20）：`/opt/cursor/artifacts/perf/browser-interact-r4b.json`

| 指标 | R3 | R4b | 说明 |
|---|---|---|---|
| 抽屉标题 p75 | 287 | 293 | 噪声内 |
| 抽屉详情 GET p75 | 444 | 444 | 仍拉详情 |
| 筛选 24h/12 p75 | 445 | **244** | 预取命中 20/20 |

筛选相对 R3 −45%。思考 800ms 是用户看选项的时间，不计入 filter_ms。立刻连点路径预取没有提前量，耗时仍接近一轮 RTT，不把那条写成已优化。
- **决定**：**保留**。

## Round 4 补测 — 其它页 / 桌面 / 探索性负载

- **其它页**（mobile-ref，n=8，真实 h1 文案）：`browser-pages-r4.json`。首页 1722 / 自选 1848 / 选股 1694 / 大盘 1727 / 雷达 1601 / 财报 1567 / 板块 1541。登录首次 0/8（Owner 已登录无 form）；修正后 p75 1213。
- **桌面** n=8（不足 20）：冷 ready p75 442，热 196。1/8 冷离群 30s。
- **探索性到达率**（非生产需求）：1 rps 0 错；5 rps 150/150；10 rps 289/300，11 次 **429**。
- **2h soak**：已启动，结果 `/opt/cursor/artifacts/perf/soak-2h.jsonl`。未完成前不得写成通过。

## Round 5 — 补齐完成判定所需脚本（业务未改）

- **证据**：第 2 轮隔离复查确认完成判定 1–6 仍不成立，缺口是测量而不是再改 feed / framer / Owner 缓存。
- **改动**：`measure_interleaved.mjs`、`run_faults.mjs`、`run_restart_recovery.py`、`watch_rss.py`、`analyze_soak.py`、`run_post_soak_suite.sh`；`measure_spa.mjs` 改为点「首页 / 新闻催化」；`measure_pages.mjs` 增加 `/cta` `/stock/NVDA` / 404；`measure_interact.mjs` 可加代码过滤与利多分类。
- **指标**：等 soak 满 2h 后再跑套件。本轮没有新的业务性能数字。
- **决定**：保留脚本。无新证据不改业务代码。
- **冒烟（未优化 :2001，n=1，不是验收）**：移动端新闻在 Dock「更多」里，不是一级 link。修正后站内回新闻 512ms，标题仍为 `第9600条快讯`。筛选二次点「筛选」会关上面板，已改为看 `aria-expanded`。

## Round 5b — 用户刷新绕过失败退避（保留上次列表）

- **证据**：`resourceCache.ensure` 失败后 `retryAt` 至少 15s；`invalidate()` 故意不消退避（后台 SSE 测试已锁）。页头「刷新」与状态条「重试」都走同一条失效路径，用户点击会被 15s 吃掉。
- **改动**：`invalidate({ userInitiated: true })` 清 `retryAt`；页头 / hook.refresh / 写操作传 `userInitiated`。SSE 后台失效仍保留退避。失败时 UI 仍是「更新失败，保留上次数据」，不把已可见列表清空。
- **验证**：`calendar-cache.test.mjs` 增加用户刷新立即重试用例；原「后台通知不得提前重试」保留。生产构建等 soak 结束后再打，避免和长稳抢 CPU。

## Round 5c — 桌面悬停预取 24h/12（不在挂载时打）

- **证据**：移动端展开筛选预取已把 24h/12 从 445ms 降到 244ms。桌面 FilterBar 常开，挂载时预取会与默认 72h 首屏抢整窗物化。
- **改动**：`Segmented` 增加 `onOptionIntent`（pointerenter/focus）；只在意图切到 24 时且当前窗不是 24 时预取。不预取 6/168。
- **指标**：等 soak 后桌面 interact n=20。立刻连点（thinkMs=0）仍接近一轮 RTT。

## Round 5d — soak 诚实窗口 + 视口 n=20（测量节奏，不改产品限流）

- **证据**：`run_soak.py` 临近 stop 时空转把 heavy 30/60s 打满；`analyze_soak.py` 若跳过空转时不推进 prev，会把空转第一行的 429 算进 `error_sum`。桌面 n=20 在 4s 对间隔下仍有 3/20 冷样本 36–43s，feed 首次 `transferSize≈374` 后重试成功，标题仍为 `第9600条快讯`。
- **改动**：分析脚本推进 prev 并按 soak 墙钟裁 RSS；测量脚本默认对间隔 8s，遇到 HTTP 429 再冷却 60s；新增 `run_remaining_suite.sh`。不放宽产品限流。
- **指标**（实验室，生产 SPA + :2000，不是 RUM / 不是日本实测）

| 档 | 冷 ready p75 | 热 ready p75 | 冷 LCP p75 | 离群 | 预算 |
|---|---|---|---|---|---|
| 桌面 n=20 | 449 | 217 | 436 | 3/20 冷 36–43s（429） | 冷≤2500 热≤1000 |
| 360 n=20 | 1654 | 823 | 1552 | 2–4 对 + 第14对冷 429 | 同上 |
| 430 n=20 | 1644 | 834 | 1544 | 1/20 冷 36s、1/20 热 6s | 同上 |
| soak 诚实 480 轮 | — | — | — | 0 错；RSS +26MB | 有界 |

- **决定**：保留节奏修复。p75 达预算，离群记为实验室自砸限流，不写成产品回归。

## Round 5e — 站内 SPA 回新闻 n=20（测量，无新业务改动）

- **口径**：mobile-ref 节流；Dock「更多」→ button「新闻催化」；`news_content_ready` 仍是首条真实标题。对间隔 8s，本轮 **rate_limited_n=0**。
- **指标**：`/opt/cursor/artifacts/perf/browser-spa.json`。站内回新闻 p50 **519** / p75 **531**。整页冷启动约 2.5s（含首次 goto）。20/20 标题 `第9600条快讯`。未优化树仅有 n=1 冒烟 512ms，不能当对照验收。
- **决定**：保留现有导航与预取。不把 SPA 531ms 写成 INP。

## Round 5f — 其它页 n=20（测量，无新业务改动）

- **口径**：mobile-ref；真实 heading / 已登录文案 / 404「页面不存在」。对间隔 8s。全部路由 `ready_n=20/20`、`rate_limited_n=0`。
- **指标**：`/opt/cursor/artifacts/perf/browser-pages-n20.json`

| 路由 | p75 (ms) | n=8 对照 |
|---|---|---|
| `/` | 1638 | 1722 |
| `/watchlist` | 1795 | 1848 |
| `/screener` | 1689 | 1694 |
| `/market` | 1715 | 1727 |
| `/breakouts` | 1626 | 1601 |
| `/earnings` | 1606 | 1567 |
| `/sectors` | 1571 | 1541 |
| `/login` | 1239 | 1213 |
| `/cta` | 2167 | （此前未 n=20） |
| `/stock/NVDA` | 2148 | （此前未 n=20） |
| 404 | 1154 | （此前未 n=20） |

雷达/财报/板块相对 n=8 有 1–2% 波动，未超过 5% 复查线。CTA / 个股首屏约 2.1s，仍低于 2.5s 冷启动参考。
- **决定**：不因 n=8→n=20 的小幅波动改业务代码。

## Round 5g — 交互 extra 与桌面悬停预取 n=20

- **口径**：实验室，不是 INP。extra：代码过滤 NVDA + 利多分类。桌面 thinkMs=800 且 hover「24 时」。
- **指标**

| 项 | extra mobile-ref | 桌面 | 对照 |
|---|---|---|---|
| 抽屉标题 p75 | 317 | **71** | R4b 293 |
| 筛选 24h/12 p75 | 256 | **72** | R4b 244 |
| 预取命中 | 20/20 | **20/20** | — |
| 搜索 p75 | 130 | — | 选择器可用 |
| 利多分类 p75 | 575（空态合法） | — | 文案「这个角度暂时没有新闻」 |
| 滚动 longtask p75 | 0 | 0 | 无横向溢出 |
| 429 | 0 | 0 | — |

原始：`browser-interact-extra.json`、`browser-interact-desktop.json`。桌面悬停预取 20/20 命中，证实 R5c 没有在挂载时打 24h，又能在思考 800ms 内命中。
- **决定**：保留。不把 71ms 写成生产 INP。

## Round 5h — 交错对照 n=20 与故障注入

- **口径**：同一台机器、同一 mobile-ref 档，opt `:2000` 与 unopt `:2001` 交错；禁止冷比热。对间隔 8s。`rate_limited_n=0`，无 >5s 离群。
- **指标**：`browser-interleaved-mobile-ref.json`

| 侧 | 冷 ready p75 | 热 ready p75 | 冷 LCP p75 | 标题 |
|---|---|---|---|---|
| 优化 | **1651** | **832** | 1556 | 第9600条快讯 |
| 未优化 | 2392 | 1093 | 2116 | 第9600条快讯 |
| 差 | **−741（−31%）** | **−260（−24%）** | −560 | 相同 |

未优化热 1093 仍高于 1000ms 预算，与基线 1110 同量级。优化热 832 达标。

- **故障**：`faults.json` 五案全部 ok。断网/429 刷新保留「第9600条快讯」并出现 stale-while-error；恢复后标题不变。首屏断网后重试恢复。慢 feed 2500ms 仍等到真实标题。清缓存再进 645ms。
- **决定**：对照收益来自已保留的调度/预取，不是缩数据。不据此再改 feed SQL。

## Round 5i — 最终累计回归

- **口径**：`run_final_regression.sh`。套件结束后才跑，避免和测量重叠。
- **功能**：前端 935 pass / 0 fail；催化 pytest 228 passed；`frontend/` 与 `frontend-src/dist` 无差异。
- **性能**：`browser-final-mobile-ref.json` 冷 p75 1698 / 热 838 / LCP 1580 / CLS 0.0008，0 次 429，0 个 >5s 离群。`browser-final-interact.json` 抽屉 314 / 筛选 249，预取 20/20。
- **相对 R3**：冷 1642→1698（+3.4%，低于 5% 复查线），热 829→838。仍远低于基线 2381/1110。
- **决定**：保留当前累计版本。不把实验室数字写成 RUM。

## Round 6 — 可见分页 / 按语言词典 / 财报按需

完整方法、本轮原始数字与限制见 [13-round6-visible-i18n-earnings.md](13-round6-visible-i18n-earnings.md)。不覆盖本文件更早轮次的结论。

- **A/B**：访客 visible 热 240ms / 指纹 1；legacy hop 1018ms / 指纹 5。Owner 整窗投影仍约 6.6s，不恢复 Owner 匿名缓存。
- **C**：中文入口 gzip 约 79KB vs 基线 326KB；词典块只在 en/ja 下载。三语冷启动与 zh→en 深链接已测。
- **D**：surfaces n=8 首开不拉 chart，近滚后 8/8 挂载且保持。
- **E**：1440 主导航悬停后再点 p75 601 vs 立即 823（快 222ms / 27%），按阈值保留。
- **浏览器交错 n=20**：优化冷/热 20/20，p75 9436 / 2532；未优化冷 19/20 超时。`incomplete_samples`，不写差值。
- **决定**：A/B/B2/C/D/E 全部保留。生产包同步为 `index-CS02aZ40.js`。
