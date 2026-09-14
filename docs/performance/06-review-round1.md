# 独立复查第 1 轮

复查者视角：对照代码、原始 JSON 与完成判定，不采信实施者口头总结。日期 2026-09-13。本轮由同一工作区完成，**不能**算作与实施者隔离的独立审查；只作为第一份对照清单。

## 查过的原始结果

- `/opt/cursor/artifacts/perf/browser-baseline-mobile-ref.json`
- `/opt/cursor/artifacts/perf/browser-r1b-mobile-ref.json`
- `/opt/cursor/artifacts/perf/browser-r3-mobile-ref.json`
- `/opt/cursor/artifacts/perf/browser-r3-mobile-weak.json`
- `/opt/cursor/artifacts/perf/browser-interact-n20.json`
- `/opt/cursor/artifacts/perf/browser-interact-r3.json`
- `/opt/cursor/artifacts/perf/browser-interact-r4.json`（**作废**：误等 24h/50）
- `/opt/cursor/artifacts/perf/browser-interact-r4b.json`
- `/opt/cursor/artifacts/perf/browser-pages-r4.json`
- `/opt/cursor/artifacts/perf/load-rps1.json` / `load-rps5.json` / `load-rps10.json`

## 没有刷指标 / 少加载 / 牺牲刷新

- 冷/热/弱网标题 20/20 为 `第9600条快讯`；feed `count` 在既有基线里为 9566。
- 抽屉仍请求 `/api/catalysts/news/{id}`；`drawer_detail_p75` 约 444ms。
- `afterLoadIdle` 仍用 `setTimeout`，`newsToday` 3500ms 未取消。
- 10 rps 探索性压测 11/300 为 **429**，已计入，未从结果中剔除。

## 已证实的收益（实验室）

- 冷 `news_content_ready` p75：2381 → 1642（mobile-ref，n=20）。
- 热 p75：1110 → 829，仍 ≤1000；相对 R1b 775 慢 54ms（+7%），因整页进入总会发出默认 feed。
- 筛选 24h/12（展开后思考 800ms）p75：445 → 244，预取 20/20 命中。立刻连点未优化。

## 本轮仍不成立的完成条件

1. 360 / 430 / 桌面关键场景不足 n=20（桌面仅 n=8）。
2. 无优化前后交错对照。
3. 无站内 SPA 导航专用样本。
4. 2h 长稳刚启动，未出最终摘要。
5. 无故障注入（上游慢/429/断网/缓存失效/重启）。
6. 独立三轮复查未完成（本文件不是隔离审查者）。
7. 真机 Safari、日本→美国、生产 16 核：**未验证**。
8. CTA / 个股 / 404 无专项实验室样本。

## 建议下一轮（有证据再改）

- 交错对照 `$HOME/option-pro-unoptimized` 与当前分支，同档 n=20。
- 桌面 / 360 / 430 补到 n=20。
- SPA 导航（内存缓存 vs 整页热）。
- 等 soak 满 2h 后再看延迟/错误趋势。
- 无新证据不要再动 feed 整窗物化或主包拆 framer。
