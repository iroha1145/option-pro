# 当前状态

更新：2026-09-13 视口 n=20 与第 3 轮复查已落盘；剩余套件在跑 SPA。Goal **未**标记完成。

## 已完成

- 拉取并核对 `origin/main` = `31e8955d89dc2b9b51a5bea1c47f5cfa6ea8cabc`
- 分支 `cursor/perf-sitewide-1d7a`；对照工作区 `$HOME/option-pro-unoptimized`
- 全站矩阵初稿、冻结预算、环境诚实记录（4 核 / 15 GiB）
- 性能种子与测量脚本落在 `scripts/perf/`
- 实验室基线 n=20（mobile-ref 冷/热）
- Round 1 / 1b：身份拆分 + load 后固定延迟。热 ready p75 775ms（基线 1110，−30%），冷 2334ms（预算 ≤2500）
- Round 2 Owner 缓存实验已回滚
- 交互实验室（R1b 代码，修好口径，n=20）：抽屉标题 p75 394ms，筛选 24h p75 445ms，滚动无 longtask
- Round 3 保留：冷 ready p75 1642（基线 2381，−31%）；热 829（预算 ≤1000）；抽屉标题 p75 287
- Round 3 弱网档（实验室）：冷 p75 4089，热 p75 1371；不是日本实测
- Round 4 保留：展开筛选后预取 24h/12，思考 800ms 后切换 p75 244（R3 445，−45%）
- soak 结束后已重建生产 `frontend/`（与 R5b/R5c 源码对齐）
- 桌面 n=20：`/opt/cursor/artifacts/perf/browser-r5-desktop.json`。冷 ready p75 **449** / 热 **217**。3/20 冷离群 36–43s，根因是 heavy API 30/60s 的 429 重试，不是渲染回归。首条标题 20/20 `第9600条快讯`
- 360 n=20：`browser-r5-mobile-360.json`。冷 p75 **1654** / 热 **823**。2–4 对及第14对受 429 污染（max 冷 38844 / 热 7025）；p75 仍在预算内
- 430 n=20：`browser-r5-mobile-430.json`。冷 p75 **1644** / 热 **834**。1/20 冷 36s、1/20 热 6s（429）；p75 在预算内
- 2h soak 诚实窗口（丢弃 gap&lt;1s 空转，且不再把空转第一行算进来）：480 轮、**0 错**、早/晚 p95 中位 149→155ms、RSS 572→599MB（+26MB，有界）。原始 `soak-2h.summary.json` 含空转 429，**不得**当结论
- 站内 SPA n=20：`browser-spa.json`。回新闻 p75 **531ms**，`rate_limited_n=0`，标题 20/20 `第9600条快讯`

## 未完成（完成判定第 1–6 条仍未同时满足）

- 其它页 n=20 / 交互 extra / 桌面交互 / 交错对照 / 故障注入（`run_remaining_suite.sh` 正在跑 pages）
- 重启恢复（:2001）已通过：`restart-recovery.json`，`ready` 1.7s，首条仍为 `第9600条快讯`，`summary.count` 重启前后均为 9199
- 测量脚本已加对间 8s 间隔 + 遇到 429 冷却 60s；旧 soak 后套件在 profile 间几乎无冷却，剩余项改走 `run_remaining_suite.sh`
- 独立三轮复查：`06-review-round1.md` / `07-review-round2.md` / `08-review-round3.md`。第 3 轮确认无刷指标、30s+ 离群是 429；完成判定仍只是部分成立
- 最终累计版本未重跑：前端全量 + 催化 pytest + mobile-ref 冷热 n=20 + 交互 n=20
- 真机、Safari、日本到美国链路：**待验证，未执行**

滚动 72h 窗随墙钟滑动：种子 `published_at` 锚定 2026-09-13T12:00Z，墙钟越往后，`summary.count` 会从基线 9566 下降（14:50Z 9446，16:35Z 未优化树 9199）。这是产品时效，不是缩数据。对照与复测必须记当时 `count` / `as_of`。

## 受阻项

| 项 | 原因 | 剩余步骤 |
|----|------|----------|
| 按生产 16 核容量下结论 | 本 VM 4 核 / 15 GiB | 在报告中保持实验室标注；不改配置假装有 16 核 |
| 真机 iPhone Safari | 环境无真机 / 非 iOS WebKit | 交付可重复的 Chromium 移动档脚本 |
| 日本→美国 RTT | 无该链路权限 | CDP 180ms/300ms 实验室档 + 缺口说明 |
| Docker 双容器 | 无 Docker daemon | uvicorn 单进程对齐生产 CMD |
