# Round 6 — 可见分页、按语言词典与财报按需图表

记录时间：2026-09-15。基线 `origin/main` `df1bd5d35e8128805d75291126341be06e38b1e6`（已合并 PR #162）。工作分支 `cursor/perf-news-i18n-earnings-ac0f`。对照树 `$HOME/option-pro-unoptimized` 停在同一 SHA。

本篇只写本轮方法与本轮测得的数字。历史 `docs/performance/` 中的 60–230ms 实验室 feed、1651/832 新闻页 ready **不能**冒充本轮结果。调研采样（新闻列表 517–556ms、指纹 229–235ms、五次空页 4.71s）只用于定向，已独立核验，不当作实现结论。

**当前产品提交** `f2c05331`（生产包 `index-DDX2TFDT.js`：图表失败边界英日词条 + lazy 工厂在 state 初始化/重试时创建）。`0804c22f` 是对齐 CI `npm ci` 的前一包（lint 因 render 期 `useMemo` 失败）。`12e78b87` 起后端未再改。进程内 feed 在 `12e78b87` 上测。surfaces / i18n / 静态包图在 `f2c05331` 上重测。n=20 交错已在该最终包上跑完。旧 `index-uc86EHir.js` 的 9436/2532 与本地增量包 `index-G7k80sIV.js` 的 3641/3592 只作版本限定历史。

## 发布边界

只开 PR，不合并，不自动合并，不部署，不改生产配置或生产数据库。隔离种子是合成数据，不含账户、密钥或私人业务数据。未对生产压测、长稳或故障注入。

## 环境

| 项 | 值 |
|---|---|
| 优化 | `/workspace`，生产 `frontend/`，uvicorn `127.0.0.1:2000`，`DATA_DIR=$HOME/optix-perf-data/n10000-r6` |
| 未优化 | `$HOME/option-pro-unoptimized` @ `df1bd5d3`，`:2001`，数据副本 `n10000-r6-unopt` |
| 访问 | `private_network`：回环 HTTP = Owner |
| 浏览器档 | 390×844，CPU 4×，下载 10Mbps，上传 2Mbps，RTT 180ms（CDP 一次） |
| 种子 | `--count 10000 --hidden-newest 48 --analyze-every 2 --history-every 15 --wall-clock` |
| 产品提交 | `f2c05331` / tree `d4ce739a89238149913de2d1c268b0ef27e3e930` |
| 入口哈希 | `frontend/assets/index-DDX2TFDT.js` sha256 `e77bd20eb0b447e8d0bdb09ec770beb5bff19aff8b4916a408c793df550b71b5` |
| 种子库 | `catalyst-cache.db` 239267840 字节，sha256 `c04b451343384990c099da24be3483680e393a0515e3e1243c5608124d785313` |

种子核验（访客投影）：72h 窗 9568；分析 4760；rejected history 318；legacy 首页 0 条 / hidden 12；visible 首页 12 条，首条 `news_id=9600`。库：links 5078，audits 5078，revisions 10000，`result_json` 约 8.9MB。

回环不能冒充访客。匿名缓存指纹必须用进程内 `request_owner_access_context(False)`。

## 可复现命令

```bash
# 进程内访客 / visible / 指纹
.venv/bin/python scripts/perf/measure_round6_feed.py \
  --data-dir "$HOME/optix-perf-data/n10000-r6" --repeats 8 \
  --out /opt/cursor/artifacts/perf/round6-feed-n10000.json

# 浏览器交错（冷只比冷、热只比热）
OPTIX_PERF_PAIRS=20 OPTIX_PERF_OUT=/opt/cursor/artifacts/perf/round6-interleaved-mobile-ref.json \
  OPTIX_PERF_PRODUCT_COMMIT=f2c05331 OPTIX_PERF_ENTRY=frontend/assets/index-DDX2TFDT.js \
  node scripts/perf/measure_round6_browser.mjs

# 首页 / 财报 / 切页 / 意图预取 / 屏外图表（拦截付费上游，财报日历本地 fulfill）
OPTIX_PERF_REPEATS=8 OPTIX_PERF_OUT=/opt/cursor/artifacts/perf/round6-surfaces.json \
  node scripts/perf/measure_round6_surfaces.mjs

# 提交 / 入口 / 种子 / 脚本哈希
DATA_DIR=$HOME/optix-perf-data/n10000-r6 \
  OPTIX_PERF_PRODUCT_COMMIT=f2c05331 \
  node scripts/perf/record_round6_provenance.mjs
```

本机 Playwright 没有 bundled Chromium 时，脚本使用 `channel: 'chrome'`。

`measure_round6_surfaces.mjs` / `measure_round6_i18n.mjs` 只把 `ready_class=content` 计为就绪；错误、超时、空态、未运行分别记账。不满足门禁则 `process.exit(1)`。意图预取 `keep` 要求全样本就绪、8/8 chunk、0 次额外付费。图表保持检查 `[data-eps-chart]` / canvas，不只看网络计数。近滚按图槽位置滚到 `rootMargin 100%` 内，不固定滚 0.9 视口。

## 候选结论

### A. 匿名完整缓存热命中一次指纹 — 保留

一次 peek 完整 `anon_items` 后，单次 `_revision_store_cursor`，同一把锁校验 cursor / TTL / items，从同一条目拷贝 rows 与 items。未命中或不完整走原慢路径（会再指纹）。同 cursor 且仍新鲜再写不刷新 TTL、不清 `anon_items`。同 cursor 但已过 300s 的过期条目用新 rows 刷新 `built_at` 并作废 `anon_items`，再按当前窗口重建展示条目；否则窗口前进后会继续吐出已过期新闻。迟到旧构建若 `existing.built_at >= started_at` 不得覆盖，也不得把旧 items 挂到新 cursor 的行缓存上。未延长 TTL，未混用 Owner/访客缓存，未跳过损坏检查。未对冷路径加只读事务或 `BEGIN IMMEDIATE`。

进程内 n=10000 访客 visible 热路径指纹 **1**；legacy hop 热路径指纹 **5**（五次请求各一次）。n=100 访客 visible 热路径指纹也是 1。

### B. `page_mode=visible` — 保留

一次候选集构建最多扫 108 条原始候选，最多返回 `limit` 条可见项。未指定 `page_mode` 保持旧切片。前端默认 visible，不再九页 hop。游标 = 已消费原始位置 + 固定 `as_of`。扫满仍空则 `has_more=true` / `status=active`。摘要仍是整窗。未分析但合法中文原文可展示。

进程内 n=10000 访客（`12e78b87`）：visible 热 p50 **203ms** vs legacy 5 hop **795ms**。首条 ids 均为 `[9600, 9000, 8400]`。legacy 单页 0 条可见。Owner visible 热 p50 **5564ms**（整窗 `_item()`，Round 2 已回滚 Owner 复用匿名缓存，本轮不再引入）。

n=100 上 visible 比 2 次 hop 慢（扫描更多项 + 方差）。B 的收益在「连续多页不可见」的大窗，不在 n=100。

浏览器 Owner mobile-ref 交错 **n=20**：

| 测速包 | 状态 | 说明 |
|---|---|---|
| `index-uc86EHir.js` | 历史 | 冷 20/20 p75 **9436** / 热 20/20 p75 **2532**；未优化冷 19/20 超时。版本限定，见 `artifacts/r6-interleaved-n20.json` |
| `index-G7k80sIV.js` @ `e3001fb0` | 历史 | 本地增量 Vite（1409 模块），与 CI `npm ci` 不一致，不作最终包 |
| `index-DDX2TFDT.js` @ `f2c05331` | 完成 | 冷 20/20 p75 **7717** / 热 20/20 p75 **1596**；未优化冷 20/20 p75 **112241** / 热 20/20 p75 **733**。标题均为「芯片企业发布最新进展」。见 `artifacts/r6-interleaved-n20-v2.json` |

`comparison_status=complete`（两侧冷/热均 20/20 无超时）。冷 p75 差 **−104524ms**（优化更快；feed hops p50 2 vs 21）。热 p75 差 **+863ms**（未优化热更快），**不把热路径写成收益**。

### C. 按语言装入词典 — 保留

中文不下载英日词典；en/ja 各只装一种。`prepareI18n()` 后再 `import('./App.tsx')`。缺译回退中文；切换语言整页重载。不翻译模型正文。

相对 `df1bd5d` 已提交 `frontend/`（不是把独立词典 gzip 当主包节省）。`f2c05331` 静态导入图 gzip9（Node 22.17.1 + `npm ci`，1407 模块）：

| 文件 / 图 | raw | gzip9 |
|---|---|---|
| 基线 `index-Cnp05EGF.js` | 963385 | 326472（含日文） |
| 历史测速包 `index-uc86EHir.js` | 243870 | 79117（旧 n=20） |
| 当前入口 `index-DDX2TFDT.js` | 243867 | 79101（不含英日译文） |
| 当前 `App-DXA7FE0e.js` | 47643 | 15480 |
| `runtime-en` / `runtime-ja` | 239658 / 277664 | 92179 / 94550（仅 en/ja 下载） |
| `chart-vlH3NqY3.js` | 643516 | 218262（不进财报首屏块） |
| 公共壳 30 个静态脚本 | 555497 | **188063** |
| 首页 = 公共壳 + 路由 | — | **216758** |
| 财报 = 公共壳 + 路由 | — | **217875** |
| 新闻 = 公共壳 + 路由 | — | **268519** |

入口+App 约 94.6KB gzip **不是**完整首次下载。上述数字仍不含 CSS、JSON、字体、数据或后续意图预取。扫描非 `runtime-en`/`runtime-ja` 的提交产物，没有 `Skip to main content` / `サポート`。中文入口不含英日跳过链接；`runtime-en` 含 `Skip to main content` 是词典块本身，只在英文模式下载。

浏览器三语冷启动（vite mock `:3021`，9/9 `content`，门禁通过）：zh 不下载 runtime-en/ja；en 只装 en；ja 只装 ja。zh→en 重载留在 `/earnings`，标题变为 `Earnings calendar`，`html lang=en-US`。

### D. 财报屏外图表 + 秒级更新局部化 — 保留

`DeferredEpsChart`：`rootMargin: 100%`，占位 320px，挂载后不卸。懒加载失败由 `ChartLoadErrorBoundary` 留在图槽；重试在 `setState` 里换新 `lazy()`，不在 render 里 `useMemo` 出组件。`Earnings.tsx` 去掉顶层 `useNow(1000)`。冷却在 `onRefresh` 内读 `cooldownUntil`，页头按钮与失败横幅共用；按钮仍做局部秒级更新。纽约日 15s 轮询，未钉住的周起始随跨日更新。隔离库无财报日历，滚动/弱网样本用本地 fulfill，不打 Finnhub/Yahoo/FMP。实验室行必须带 `publicFeatured: true`：重点列表不按市值自动入选，缺标注则图表槽不挂载。

surfaces n=8（`f2c05331`，付费上游 abort，日历/首页本地 fulfill，门禁通过）：首页 ready p75 **2641ms**，财报 **2487ms**，首开 `chart_loaded=0`。切页：首页卡片 751ms / 桌面主导航 754ms。滚动：屏外前 0/8 拉 chart，近滚后 8/8，占位高度 320，DOM `[data-eps-chart]` / canvas 8/8 保持。本地增量包 `index-G7k80sIV.js` 公共壳曾到 224KB、首页 p75 3641；CI 同口径包回到 188KB 壳，按最终包记账。

### B2. 今日计数不再套用 visible — 保留（生产包已同步）

复查发现 `qs()` 把 `newsToday`（24h/50）和 `tickerSummaries`（候选发现）也默认成了 `page_mode=visible`。这两处要的是窗口摘要 / 候选 ticker，不是可见列表；feed 落地后会再占一条 uvicorn。现已显式 `pageMode: null` 省略该参数，旧客户端哈希与旧切片不变。列表、预取、theme-boot 仍是 visible。契约测试已改为断言 24h 计数 URL 不含 `page_mode`。

### E. 有限导航意图预取 — 保留

只预取路由 chunk，`saveData`、跳过当前路径、`MAX_INTENT=2`（按进行中的 `import()` 计数）。命令面板关闭时 effect 不预取。1440 主导航 n=8（`f2c05331`，门禁通过）：立即点击 p75 **743ms**；悬停后再点 **538ms**（快 205ms / 28%）；划过不进入 8/8 预取到 Earnings 块、0 次拉 chart、0 次额外付费。关闭面板 / 无意图各 8 次：0 额外 chunk。阈值见 `scripts/perf/lib/round6_intent_decision.mjs`（≥150ms 且 ≥8%，且全样本 / 全 chunk / 无额外付费）。390px 主导航隐藏，首页「查看全部」不挂预取。

## 明确不做 / 回退过的方向

- Owner 复用匿名 revision 缓存（Round 2 已回滚）
- 延长缓存 TTL、混用管理员/访客缓存
- 服务端循环调用 `feed()` 九次
- 物化整窗 feed SQL、拆 framer-motion 主包
- 为测速降低断言或缩小种子质量
- 过期同 cursor 缓存「保留旧 `anon_items`」——窗口前进后会吐过期新闻

Owner 热路径仍约 5.6s：瓶颈是整窗投影/复制，不是第二次指纹。本轮不继续无边界重构。

## 正确性

已覆盖：热命中一次指纹、不完整走慢路径、迟到旧构建不覆盖且不污染新缓存 items、过期同 cursor 作废 `anon_items` 并跟随窗口、同 cursor 新鲜命中不丢 `anon_items`、前 12 / 前 108 隐藏、未分析中文原文、游标按原始位置、固定 `as_of`、旧客户端省略 `page_mode`、非法 `page_mode` 422、PR #162 抽屉恢复与有界重试、词典不静态合并、财报不再整页 `useNow(1000)`、图表懒加载失败只留在图槽、刷新冷却由 `onRefresh` 内读 `cooldownUntil`、命令面板关闭不预取。

已知兼容限制：新前端 + 旧后端会带上 `page_mode=visible`（旧后端忽略）且不再 hop，首页可能先空，需用户点继续加载。旧前端 + 新后端保持旧切片，不放大扫描。

## 原始小型数据

- `artifacts/r6-feed-n100.json`（历史 n=100）
- `artifacts/r6-feed-n10000.json`（`12e78b87` 重测）
- `artifacts/r6-bundle-sizes.json`（收口前对照，入口仍写 `index-CS02aZ40.js`）
- `artifacts/r6-bundles.json`（当前静态导入图）
- `artifacts/r6-interleaved-probe.json`
- `artifacts/r6-interleaved-n20.json`（`index-uc86EHir.js` 历史）
- `artifacts/r6-surfaces.json`（`f2c05331`，含门禁与 extras）
- `artifacts/r6-i18n.json`（`f2c05331`）
- `artifacts/r6-provenance.json`（提交 / 入口 / 种子 / 脚本哈希）
- `artifacts/r6-summary.json`

完整 n=20 交错原始 JSON：`artifacts/r6-interleaved-n20-v2.json`（`f2c05331` / `index-DDX2TFDT.js`，含全部 20 对样本）。历史 `index-uc86EHir.js` 仍在 `r6-interleaved-n20.json`。
