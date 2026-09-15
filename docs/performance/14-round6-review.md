# Round 6 独立复查

审查对象：`cursor/perf-news-i18n-earnings-ac0f`，对照基线 `df1bd5d`。本篇只记本轮确认项，不把历史 1651/832 当成本轮证据。

2026-09-15 续修：R1–R5 / V1 已按失败回归先补再改实现。最终产品提交 `e3001fb0` / `index-G7k80sIV.js`（图表失败边界英日词条）。进程内 feed 仍用 `12e78b87`（后端未再改）。surfaces / i18n / 包图在最终包上重测；n=20 交错正在该包上重跑。`4fd819c0` 只改 batch 测试桩。

## 并发与缓存

- 热命中：peek 完整 `anon_items` → 一次指纹 → 同一把锁拷贝 rows 与 items。rows/items 不能来自不同 cursor。
- 慢路径仍会再指纹；禁止把旧指纹传去写缓存。
- `_store_revision_cache`：同 cursor 且仍新鲜（或并发写入的 `built_at >= started_at`）直接 return，不刷新 TTL、不丢 items。同 cursor 但已超过 300s 的过期条目必须用新 rows 覆盖并刷新 `built_at`，同时作废 `anon_items`：数据库版本不变时 24h/72h 窗口仍会前进，旧展示条目不能继续背书。迟到旧构建不得覆盖较新行缓存，也不得把自己的旧 items 写进新 cursor。行集合携带 `source_cursor`；回填 items 必须与当前缓存 rows 的 news_id 列表一致。
- Owner 与历史 `as_of`（超出 90s）仍 `items=None`，未恢复 Round 2 的 Owner 复用。
- 未使用 `BEGIN IMMEDIATE`，未延长 TTL，未跳过损坏检查。

## 默认 visible 的边界

- 列表、`feedApiPath` 默认、theme-boot 预取必须带 `page_mode=visible`。
- `newsToday` 与 `tickerSummaries` 必须省略 `page_mode`。生产包 `useAccess` 仍是 `windowHours:24,limit:50,...,pageMode:null`。

## 游标与新旧客户端

- `query_hash` 仅在提供 `page_mode` 时纳入该字段；旧客户端哈希与基线一致。
- 可见游标表示已消费的**原始**位置，并固定 `as_of`。
- 旧前端 + 新后端：不带 `page_mode`，旧切片，不放大扫描。
- 新前端 + 旧后端：带 `page_mode=visible`（旧后端忽略）且不再 hop，首页可能先空。记录为已知限制，不是静默兼容。

## 身份隔离

- 回环 HTTP = Owner。匿名热缓存必须进程内 guest 测，浏览器对照不能冒充访客。
- `_displayable_zh` 对 Owner/访客相同：无合法中文原文则隐藏。Owner 仍能看任务态等原有权限字段。
- 未混用管理员与访客缓存换速度。

## 语言冷启动

`/catalysts` 实验室 ready 按标题 + `article h3` / 空态判定。vite mock 三语：zh 不装 runtime；en/ja 各只装一种。切换测试的 initScript 不得在 reload 时覆盖已写入的 `optix:locale`，否则会假阴性。已改：仅在键缺失时写入。zh→en 留在 `/earnings`，标题 `Earnings calendar`，`html lang=en-US`，`runtime-en=1`。`e3001fb0` 上 9/9 `content`，门禁通过。

## 语言初始化

- `main.tsx` 先 `await prepareI18n()` 再 `import('./App.tsx')`。
- 中文不下载 runtime-en/ja；词典失败仍启动并回退中文。
- `setLocale` 仍落盘 + `location.reload()`。
- 不翻译模型生成的新闻/财报正文。
- 独立图例 harness 必须在 `renderLegend` 前 `installTranslations`。已修 visual CI 竞态。

## 财报时钟与屏外图表

- 页级不再 `useNow(1000)`。冷却在 `onRefresh` 内读 `cooldownUntil`，页头与失败横幅共用。
- 图表懒加载失败留在 `ChartLoadErrorBoundary`，重试换新 `lazy()`。
- 纽约日 15s 轮询。surfaces n=8（`e3001fb0`）：首开不拉 chart；近滚后 8/8 挂载且 DOM 保持；占位 320px。近滚按图槽位置进入 `rootMargin 100%`。
- 实验室行必须 `publicFeatured: true`。个人自选 fulfill 空名单。

## 意图预取

`MAX_INTENT=2` 按进行中的 `import()` 计数。命令面板关闭不预取。1440 主导航 n=8（`e3001fb0`）：悬停 8/8 预取 Earnings 块、0 chart、0 额外付费；`hover_then_click` 比立即点击快 210ms / 16%，按阈值 **保留**。关闭面板 / 无意图 0 额外 chunk。

## 测量门禁（V1）

- 汇总只把 `ready_class=content` 计为就绪；错误 / 超时 / 空态 / 未运行分列，超时的 `wall_ms` 不进入 `ready_n`。
- 意图 `keep` 要求 expectedN 全就绪、chunk_n === expectedN、0 次额外付费。
- 图表保持检查节点 / canvas，不只看曾经发生过的网络请求。
- 不满足门禁的 surfaces / i18n 运行以非零退出。

## 已知限制（不是待确认问题）

- Owner 热路径仍约 5.6s（整窗 `_item()`）。
- 未优化 Owner+10k 隐藏前缀在 180s 内几乎看不到首条；对照 `comparison_status=incomplete_samples`，不编造 p75 差值。
- 历史 n=20 交错用的是 `index-uc86EHir.js`。最终产品包是 `index-G7k80sIV.js`；V2 交错在该包上重跑，完成前不把 9436/2532 写成最终提交数字。
- 中文入口+App 约 94.6KB gzip 不是完整首次下载；公共壳 29 脚本约 224KB gzip9。
- Navbar 纽约时钟仍是全站既有 1Hz。
- 新前端 + 旧后端可能先空页。
