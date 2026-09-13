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
- **下一步**：只用 `limit=12` 的 24h 响应（含思考时间内完成的预取）重测。
