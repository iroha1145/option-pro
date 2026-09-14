# Round 6 独立复查

审查对象：`cursor/perf-news-i18n-earnings-ac0f`，对照基线 `df1bd5d`。本篇只记本轮确认项，不把历史 1651/832 当成本轮证据。

## 并发与缓存

- 热命中：peek 完整 `anon_items` → 一次指纹 → 同一把锁拷贝 rows 与 items。rows/items 不能来自不同 cursor。
- 慢路径仍会再指纹；禁止把旧指纹传去写缓存。
- `_store_revision_cache`：同 cursor 直接 return（不刷新 TTL、不丢 items）；`existing.built_at >= started_at` 的迟到旧构建不覆盖。
- Owner 与历史 `as_of`（超出 90s）仍 `items=None`，未恢复 Round 2 的 Owner 复用。
- 未使用 `BEGIN IMMEDIATE`，未延长 TTL，未跳过损坏检查。

## 默认 visible 的边界

- 列表、`feedApiPath` 默认、theme-boot 预取必须带 `page_mode=visible`。
- `newsToday` 与 `tickerSummaries` 必须省略 `page_mode`。生产包 `useAccess-DbNUA1OH.js` 已是 `windowHours:24,limit:50,...,pageMode:null`。

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

`/catalysts` 实验室 ready 按标题 + `article h3` / 空态判定。vite mock 三语：zh 不装 runtime；en/ja 各只装一种。切换测试的 initScript 不得在 reload 时覆盖已写入的 `optix:locale`，否则会假阴性。已改：仅在键缺失时写入。zh→en 留在 `/earnings`，标题 `Earnings calendar`，`html lang=en-US`，`runtime-en=1`。

## 语言初始化

- `main.tsx` 先 `await prepareI18n()` 再 `import('./App.tsx')`。
- 中文不下载 runtime-en/ja；词典失败仍启动并回退中文。
- `setLocale` 仍落盘 + `location.reload()`。
- 不翻译模型生成的新闻/财报正文。
- 独立图例 harness 必须在 `renderLegend` 前 `installTranslations`。已修 visual CI 竞态。

## 财报时钟与屏外图表

- 页级不再 `useNow(1000)`。冷却只在按钮内走秒。
- 纽约日 15s 轮询。surfaces n=8：首开不拉 chart；近滚后 8/8 挂载且保持；占位 320px。
- 实验室行必须 `publicFeatured: true`。个人自选 fulfill 空名单。

## 意图预取

`MAX_INTENT=2` 按进行中的 `import()` 计数。1440 主导航 n=8：悬停 8/8 预取 Earnings 块、0 chart；`hover_then_click` 比立即点击快 222ms / 27%，按阈值 **保留**。

## 已知限制（不是待确认问题）

- Owner 热路径仍约 6.6s（整窗 `_item()`）。
- 未优化 Owner+10k 隐藏前缀在 180s 内几乎看不到首条；对照 `comparison_status=incomplete_samples`，不编造 p75 差值。
- n=20 交错用的是收口前的 `index-uc86EHir.js`；`newsToday` 去 visible 的生产包是收口后的 `index-CS02aZ40.js`。
- Navbar 纽约时钟仍是全站既有 1Hz。
- 新前端 + 旧后端可能先空页。
