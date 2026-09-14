# 独立复查第 2 轮

复查者视角：对照代码、原始 JSON 与完成判定，不采信实施者口头总结。日期 2026-09-13。本轮由隔离 explore 子代理完成（`bc-64b1812a-c19c-5732-b0a9-00dfccff1ac7`），**仍不是第三轮**，不能单独宣布完成。

## 查过的原始结果

- `/opt/cursor/artifacts/perf/browser-baseline-mobile-ref.json`
- `/opt/cursor/artifacts/perf/browser-r1b-mobile-ref.json`
- `/opt/cursor/artifacts/perf/browser-r3-mobile-ref.json`
- `/opt/cursor/artifacts/perf/browser-r3-mobile-weak.json`
- `/opt/cursor/artifacts/perf/browser-interact-n20.json`
- `/opt/cursor/artifacts/perf/browser-interact-r3.json`
- `/opt/cursor/artifacts/perf/browser-interact-r4.json`（作废）
- `/opt/cursor/artifacts/perf/browser-interact-r4b.json`
- `/opt/cursor/artifacts/perf/browser-pages-r4.json`
- `/opt/cursor/artifacts/perf/load-rps1.json` / `load-rps5.json` / `load-rps10.json`
- `/opt/cursor/artifacts/perf/soak-2h.jsonl`（复查当时未满 2h）

## 没有刷指标 / 少加载 / 牺牲刷新

- 72h/12 仍 `total=9566`；冷启动标题 20/20 为 `第9600条快讯`。
- 抽屉仍请求 `/api/catalysts/news/{id}`；`drawer_detail_p75` 约 444ms。
- `newsToday` / 报价 SSE 是延迟不是取消。
- 10 rps 的 11 次 429 已计入，未剔除。
- 热路径 R3 相对 R1b +7%（775→829）已写明，根因是 theme-boot 整页再进总会发默认 feed。不得把热路径写成「全面变快」。

## 已证实的收益（实验室）

- 冷 `news_content_ready` p75：2381 → 1642（mobile-ref，n=20）。
- 抽屉标题 p75：394 → 287。
- 筛选 24h/12（展开后思考 800ms）p75：445 → 244，预取 20/20。

## 本轮仍不成立的完成条件

1. 360 / 430 / 桌面关键场景不足 n=20。
2. 无优化前后交错对照 JSON。
3. 无站内 SPA 导航专用样本。
4. 2h 长稳未出最终摘要。
5. 无故障注入脚本结果。
6. 独立三轮复查未完成（本文件是第 2 轮）。
7. 真机 Safari、日本→美国、生产 16 核：**未验证**。
8. CTA / 个股 / 404 当时无专项样本（脚本已补，结果待跑）。

## 剩余高收益项（有证据再改业务）

- 桌面筛选预取 24h/12（移动 R4 已证实；桌面 FilterBar 常开，须先测 hover/思考路径，避免首屏再抢一条整窗物化）。
- 跑通 SPA / 交错 / 视口 n=20 / 故障恢复。
- 带 query 的 `/catalysts` 深链不走 theme-boot 预取：先 A/B，再决定是否对齐默认 query。

## 明确不要再做

无新定量证据不要动 feed 整窗物化、主包拆 framer-motion、Owner 匿名 revision 缓存 overlay。
