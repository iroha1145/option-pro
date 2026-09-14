# Round 6 — 可见分页、按语言词典与财报按需图表

记录时间：2026-09-14。基线 `origin/main` `df1bd5d35e8128805d75291126341be06e38b1e6`（已合并 PR #162）。工作分支 `cursor/perf-news-i18n-earnings-ac0f`。对照树 `$HOME/option-pro-unoptimized` 停在同一 SHA。

本篇只写本轮方法与本轮测得的数字。历史 `docs/performance/` 中的 60–230ms 实验室 feed、1651/832 新闻页 ready **不能**冒充本轮结果。调研采样（新闻列表 517–556ms、指纹 229–235ms、五次空页 4.71s）只用于定向，已独立核验，不当作实现结论。

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
  node scripts/perf/measure_round6_browser.mjs

# 首页 / 财报 / 切页 / 意图预取 / 屏外图表（拦截付费上游，财报日历本地 fulfill）
OPTIX_PERF_REPEATS=8 OPTIX_PERF_OUT=/opt/cursor/artifacts/perf/round6-surfaces.json \
  node scripts/perf/measure_round6_surfaces.mjs
```

本机 Playwright 没有 bundled Chromium 时，脚本使用 `channel: 'chrome'`。

## 候选结论

### A. 匿名完整缓存热命中一次指纹 — 保留

一次 peek 完整 `anon_items` 后，单次 `_revision_store_cursor`，同一把锁校验 cursor / TTL / items，从同一条目拷贝 rows 与 items。未命中或不完整走原慢路径（会再指纹）。同 cursor 且仍新鲜再写不刷新 TTL、不清 `anon_items`。同 cursor 但已过 300s 的过期条目用新 rows 刷新 `built_at` 并保留 `anon_items`（Codex P2：否则过期后每次都重扫）。迟到旧构建若 `existing.built_at >= started_at` 不得覆盖。未延长 TTL，未混用 Owner/访客缓存，未跳过损坏检查。未对冷路径加只读事务或 `BEGIN IMMEDIATE`。

进程内 n=10000 访客 visible 热路径指纹 **1**；legacy hop 热路径指纹 **5**（五次请求各一次）。n=100 访客 visible 热路径指纹也是 1。

### B. `page_mode=visible` — 保留

一次候选集构建最多扫 108 条原始候选，最多返回 `limit` 条可见项。未指定 `page_mode` 保持旧切片。前端默认 visible，不再九页 hop。游标 = 已消费原始位置 + 固定 `as_of`。扫满仍空则 `has_more=true` / `status=active`。摘要仍是整窗。未分析但合法中文原文可展示。

进程内 n=10000 访客：visible 热 p50 **240ms** vs legacy 5 hop **1018ms**。首条 ids 均为 `[9600, 9000, 8400]`。legacy 单页 0 条可见。Owner visible 仍约 **6.6s**（整窗 `_item()`，Round 2 已回滚 Owner 复用匿名缓存，本轮不再引入）。

n=100 上 visible 比 2 次 hop 慢（扫描更多项 + 方差）。B 的收益在「连续多页不可见」的大窗，不在 n=100。

浏览器 Owner mobile-ref 交错 **n=20**（冷只比冷、热只比热；测速包仍是 `index-uc86EHir.js`）：

| 侧 | 冷 ready | 冷 p50 / p75 | 热 ready | 热 p50 / p75 | 标题 |
|---|---|---|---|---|---|
| 优化 | **20/20** | **9225 / 9436** | **20/20** | **2444 / 2532** | 芯片企业发布最新进展 |
| 未优化 | 1/20 | 仅 1 次 176229ms | 3/20 | 762 / 74210 / 79300 | 同上（成功样本） |

优化侧 0 超时，0 次 429，冷热 feed 恒为 **2/2**。未优化 19/20 冷超时、17/20 热超时（180s 内 22–30 次 legacy hop 仍无首条）。`comparison_status=incomplete_samples`，**不写**冷/热 p75 差值。未优化成功样本标题与优化相同，说明不是缩数据。

### C. 按语言装入词典 — 保留

中文不下载英日词典；en/ja 各只装一种。`prepareI18n()` 后再 `import('./App.tsx')`。缺译回退中文；切换语言整页重载。不翻译模型正文。

相对 `df1bd5d` 已提交 `frontend/`（不是把独立词典 gzip 当主包节省）：

| 文件 | raw | gzip9 |
|---|---|---|
| 基线 `index-Cnp05EGF.js` | 963385 | 326472（含日文） |
| 测速包 `index-uc86EHir.js` | 243870 | 79117（n=20 对照用） |
| 收口后 `index-CS02aZ40.js` | 243867 | 79107（不含英日译文） |
| 收口后 `App-BlA5eVcV.js` | 47628 | 15479 |
| `runtime-en` / `runtime-ja` | 239441 / 277389 | 92107 / 94486（仅 en/ja 下载） |
| `chart-NPG-xq8z.js` | 643516 | 218261（不进财报首屏块） |

中文关键 JS 约 79+15KB gzip vs 基线入口 326KB。独立词典 gzip 不能当成主包节省量。扫描非 `runtime-en`/`runtime-ja` 的提交产物，没有 `Skip to main content` / `サポート`。

浏览器三语冷启动（vite mock `:3021`）：zh 不下载 runtime-en/ja；en 只装 en；ja 只装 ja。zh→en 重载留在 `/earnings`，标题变为 `Earnings calendar`，`html lang=en-US`。

### D. 财报屏外图表 + 秒级更新局部化 — 保留

`DeferredEpsChart`：`rootMargin: 100%`，占位 320px，挂载后不卸。`Earnings.tsx` 去掉顶层 `useNow(1000)`。冷却只在按钮内走秒；`cooldownUntil` 到期后清零，避免冷却结束后仍 1Hz。纽约日 15s 轮询，未钉住的周起始随跨日更新。隔离库无财报日历，滚动/弱网样本用本地 fulfill，不打 Finnhub/Yahoo/FMP。实验室行必须带 `publicFeatured: true`：重点列表不按市值自动入选，缺标注则图表槽不挂载。

surfaces n=8（付费上游 abort，日历/首页本地 fulfill）：首页 ready p75 **2721ms**，财报 **2699ms**，首开 `chart_loaded=0`。切页：首页卡片 795ms / 桌面主导航 788ms。滚动：屏外前 0/8 拉 chart，近滚后 8/8，占位高度 320，挂载后 8/8 保持。

### B2. 今日计数不再套用 visible — 保留（生产包已同步）

复查发现 `qs()` 把 `newsToday`（24h/50）和 `tickerSummaries`（候选发现）也默认成了 `page_mode=visible`。这两处要的是窗口摘要 / 候选 ticker，不是可见列表；feed 落地后会再占一条 uvicorn。现已显式 `pageMode: null` 省略该参数，旧客户端哈希与旧切片不变。列表、预取、theme-boot 仍是 visible。契约测试已改为断言 24h 计数 URL 不含 `page_mode`。

### E. 有限导航意图预取 — 保留

只预取路由 chunk，`saveData`、跳过当前路径、`MAX_INTENT=2`（按进行中的 `import()` 计数）。1440 主导航 n=8：立即点击 p75 **823ms**；悬停后再点 **601ms**（快 222ms / 27%）；划过不进入 8/8 预取到 Earnings 块、0 次拉 chart、0 次额外付费。阈值见 `scripts/perf/lib/round6_intent_decision.mjs`（≥150ms 且 ≥8%）。390px 主导航隐藏，首页「查看全部」不挂预取。

## 明确不做 / 回退过的方向

- Owner 复用匿名 revision 缓存（Round 2 已回滚）
- 延长缓存 TTL、混用管理员/访客缓存
- 服务端循环调用 `feed()` 九次
- 物化整窗 feed SQL、拆 framer-motion 主包
- 为测速降低断言或缩小种子质量

Owner 热路径仍约 6.6s：瓶颈是整窗投影/复制，不是第二次指纹。本轮不继续无边界重构。

## 正确性

已覆盖：热命中一次指纹、不完整走慢路径、迟到旧构建不覆盖、同 cursor 不丢 `anon_items`、前 12 / 前 108 隐藏、未分析中文原文、游标按原始位置、固定 `as_of`、旧客户端省略 `page_mode`、非法 `page_mode` 422、PR #162 抽屉恢复与有界重试、词典不静态合并、财报不再整页 `useNow(1000)`。

已知兼容限制：新前端 + 旧后端会带上 `page_mode=visible`（旧后端忽略）且不再 hop，首页可能先空，需用户点继续加载。旧前端 + 新后端保持旧切片，不放大扫描。

## 原始小型数据

- `artifacts/r6-feed-n100.json`
- `artifacts/r6-feed-n10000.json`
- `artifacts/r6-bundle-sizes.json`
- `artifacts/r6-interleaved-probe.json`
- `artifacts/r6-interleaved-n20.json`
- `artifacts/r6-summary.json`
- `artifacts/r6-bundles.json`

完整 n=20 交错与 surfaces 原始 JSON 放 `/opt/cursor/artifacts/perf/`。
