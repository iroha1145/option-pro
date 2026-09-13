# 实验记录

每轮写：瓶颈证据、假设、改动、前后指标与波动、功能验证、风险、保留/回滚、下一步。

## Round 0 — 建环境与基线（已冻结）

- **证据**：`origin/main` SHA `31e8955d`。本机 4 核 / 15 GiB。代码审查显示 feed 整窗物化 + 首屏双 feed。
- **假设**：10k 档新闻 feed 与新闻页 `news_content_ready` 会显著超过预算；status 本身有界。
- **改动**：仅文档、种子与测量脚本，无业务代码。
- **指标**（实验室，n=20 对，mobile-ref，CDP 一次限速）：冷 `news_content_ready` p50 2348 / p75 2381；热 p50 1084 / p75 1110（热预算 ≤1000 **未达标**）。LCP 冷 p75 2088 / 热 1044。API feed 72h/12 HTTP p50 98ms；进程内热 p50 60ms。原始结果：`/opt/cursor/artifacts/perf/browser-baseline-mobile-ref.json`、`api-baseline-n10000.json`、`feed-inprocess.json`。
- **决定**：先改挡住挂页的串行 RTT 与首屏抢带宽请求，再动 SQL 物化。
- **下一步**：Round 1 业务改动 → 同条件复测。

## Round 1 — 身份确认与非首屏请求让路（已改代码，复测中）

- **证据**：Owner `accessApi.liveStatus` 先 `/access/status` 再串行 `Promise.all(/ai/status, /runtime-settings)`，180ms RTT 下多挡一轮才挂 `<Outlet/>`。`QuoteConnection` 确认身份后立即 `quoteStore.start()`（基线约 516ms）。`StatusHero.newsToday` 与列表 feed 并行再打一次 24h/50。短 timeout 的 `requestIdleCallback` 会在 feed 仍传输时开火。
- **假设**：先确认主体再补 AI 点，并把行情探测 / 今日计数 / 其它页预取推迟到 `load`+idle，可在不缩数据的前提下压低热缓存 `news_content_ready`（目标 ≤1000ms p75）并避免冷启动抢带宽。
- **改动**：
  - `accessApi.identity()` 只读 `/access/status`；Owner 未补齐前 `aiReason: analysis_status_pending`，失败仍是 `analysis_status_unavailable`，禁止假绿灯。
  - `useAccess.readInto` 在 `hasConfirmedIdentity=true` 之后再 `enrichOwnerCapabilities`。
  - `afterLoadIdle`：有 `readyState` 且未 `complete` 时等 `load`，再 `requestIdleCallback`。
  - `StatusHero.newsToday`：手动刷新立即拉；否则 load+idle（timeout 3500）。
  - `QuoteConnection.start`：load+idle（timeout 2500）；可见性恢复逻辑不变。
  - `Layout` 预取跳过当前路由，timeout 4000。
  - `PersonalCatalystService.feed` 不再每次完整 `status()`。
- **功能验证**：`node --experimental-strip-types --test frontend-src/tests/*.test.mjs` → 928 pass / 1 skip；`pytest tests/test_personal_catalyst_service.py tests/test_catalyst_api.py tests/test_catalyst_local_intelligence.py` 通过。
- **代价**：生产 `index.js` 957 KiB / gzip 328 KiB（基线约 934 / 318）。复测时对照 transfer。
- **指标**：复测写入 `/opt/cursor/artifacts/perf/browser-r1-mobile-ref.json`。
- **决定**：待同条件 n=20 对复测后保留或回滚。
- **下一步**：热 p75 仍 >1000 或冷退化 >5% 则进入 feed 分页物化；否则测桌面/弱网/其它页。
