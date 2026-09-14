# 第 3 轮隔离复查

复查者：独立 explore 子代理（只读）。
对照：`/workspace` HEAD（当时 `d61e9414`）vs `$HOME/option-pro-unoptimized` SHA `31e8955d89dc2b9b51a5bea1c47f5cfa6ea8cabc`。
Soak 以 `soak-2h.analysis.json` 诚实窗口为准，不采信 `soak-2h.summary.json` 的错误率。

Goal **不可**据此标记完成。

## 1. 有没有刷指标？

**未发现**少加载、缩数据范围/精度/刷新频率、改交易或指标语义、关鉴权、或从原始 JSON 剔除回归。

首屏让路（`newsToday` load+3.5s、报价 SSE +2.5s、路由预取 +8s）是调度，不是取消请求。Feed 仍是 72h/12，newsToday 仍是 24h/50，polling 间隔仍是 120s。主题 boot 预取反而使热路径相对 R1b 大约 +7%，已写在 Round 3 记录里。

`summary.count` 从 9566 降到 9199 是 72h 滚动窗随墙钟滑动，不是缩库。鉴权路径仍在。离群样本留在 `browser-r5-*.json`，没有删。

## 2. 不要再做 / 尚未闭环

不要再做（已有定量或回滚证据）：

- feed 整窗物化下推 SQL
- 主包拆 framer-motion
- Owner 复用匿名 revision 缓存（Round 2 已回滚）
- 放宽 heavy API 30/60s

尚未闭环（脚本已有，原始 JSON 当时未齐）：SPA n=20、交错对照、其它页 n=20、交互 extra / 桌面交互、故障注入、深链带 query 的 theme-boot A/B。

## 3. 完成判定 1–6

当时均为 **部分成立**。缺交错 JSON、pages n=20、SPA n=20、faults、最终累计回归原始日志、本文件落盘。新闻页移动/桌面/360/430 的 p75 已达实验室预算。

## 4. 30s+ 离群是 429，不是产品回归

正常 72h/12 feed `transferSize` ≈ 1975。429 首次约为 374。桌面 pair 2、360 pair 4、430 pair 5 都是先 374 再成功 1975，标题仍为 `第9600条快讯`。机制：`backend/app/main.py` heavy 30/60s + 客户端退避。修复方向是测量间隔，不是放宽限流。
