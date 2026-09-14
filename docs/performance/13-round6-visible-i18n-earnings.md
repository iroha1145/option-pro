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

一次 peek 完整 `anon_items` 后，单次 `_revision_store_cursor`，同一把锁校验 cursor / TTL / items，从同一条目拷贝 rows 与 items。未命中或不完整走原慢路径（会再指纹）。同 cursor 再写不刷新 TTL、不清 `anon_items`。迟到旧构建若 `existing.built_at >= started_at` 不得覆盖。未延长 TTL，未混用 Owner/访客缓存，未跳过损坏检查。未对冷路径加只读事务或 `BEGIN IMMEDIATE`。

进程内 n=10000 访客 visible 热路径指纹 **1**；legacy hop 热路径指纹 **5**（五次请求各一次）。n=100 访客 visible 热路径指纹也是 1。

### B. `page_mode=visible` — 保留

一次候选集构建最多扫 108 条原始候选，最多返回 `limit` 条可见项。未指定 `page_mode` 保持旧切片。前端默认 visible，不再九页 hop。游标 = 已消费原始位置 + 固定 `as_of`。扫满仍空则 `has_more=true` / `status=active`。摘要仍是整窗。未分析但合法中文原文可展示。

进程内 n=10000 访客：visible 热 p50 **240ms** vs legacy 5 hop **1018ms**。首条 ids 均为 `[9600, 9000, 8400]`。legacy 单页 0 条可见。Owner visible 仍约 **6.6s**（整窗 `_item()`，Round 2 已回滚 Owner 复用匿名缓存，本轮不再引入）。

n=100 上 visible 比 2 次 hop 慢（扫描更多项 + 方差）。B 的收益在「连续多页不可见」的大窗，不在 n=100。

浏览器 1 对探针（Owner，mobile-ref）：优化冷 **9189ms** / 热 **2732ms**，标题「芯片企业发布最新进展」，feed 2 次（72h/12 visible + 既有 24h/50）。未优化 120s 内 18 次 legacy hop 仍无首条。随后把等待放到 180s，n=20 交错进行中。

### C. 按语言装入词典 — 保留

中文不下载英日词典；en/ja 各只装一种。`prepareI18n()` 后再 `import('./App.tsx')`。缺译回退中文；切换语言整页重载。不翻译模型正文。

相对 `df1bd5d` 已提交 `frontend/`（不是把独立词典 gzip 当主包节省）：

| 文件 | raw | gzip9/vite |
|---|---|---|
| 基线 `index-Cnp05EGF.js` | 963385 | 326472（含日文） |
| 本轮 `index-uc86EHir.js` | 243870 | 79117（不含英日译文） |
| `App-D-zt9l2I.js` | 47628 | 15470 |
| `runtime-en` / `runtime-ja` | 239441 / 277389 | 92124 / 94594（仅 en/ja 下载） |
| `chart-D1l-hU5q.js` | 643516 | 218205（不进财报首屏块） |

中文关键 JS 约 79+15KB gzip vs 基线入口 326KB。独立词典 gzip 不能当成主包节省量。扫描非 `runtime-en`/`runtime-ja` 的提交产物，没有 `Skip to main content` / `サポート`。

### D. 财报屏外图表 + 秒级更新局部化 — 保留（正确性已测，浏览器滚动样本待 n=8）

`DeferredEpsChart`：`rootMargin: 100%`，占位 320px，挂载后不卸。`Earnings.tsx` 去掉顶层 `useNow(1000)`。冷却只在按钮内走秒；`cooldownUntil` 到期后清零，避免冷却结束后仍 1Hz。纽约日 15s 轮询，未钉住的周起始随跨日更新。隔离库无财报日历，滚动/弱网样本用本地 fulfill，不打 Finnhub/Yahoo/FMP。

### B2. 今日计数不再套用 visible — 保留（源码已改，产物待同步）

复查发现 `qs()` 把 `newsToday`（24h/50）和 `tickerSummaries`（候选发现）也默认成了 `page_mode=visible`。这两处要的是窗口摘要 / 候选 ticker，不是可见列表；feed 落地后会再占一条 uvicorn。现已显式 `pageMode: null` 省略该参数，旧客户端哈希与旧切片不变。列表、预取、theme-boot 仍是 visible。契约测试已改为断言 24h 计数 URL 不含 `page_mode`。

### E. 有限导航意图预取 — 暂留，等 surfaces 对照

只预取路由 chunk，`saveData`、跳过当前路径、`MAX_INTENT=2`。立即点击 / 停留后点击 / 划过不进入的计时尚未完成。收益不明显将回退该提交。

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

完整 n=20 交错与 surfaces 原始 JSON 放 `/opt/cursor/artifacts/perf/`，体量可控的摘要会再拷回 `artifacts/`。
