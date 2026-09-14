# Round 6 独立复查（进行中）

审查对象：`cursor/perf-news-i18n-earnings-ac0f`，对照基线 `df1bd5d`。本篇只记本轮确认项，不把历史 1651/832 当成本轮证据。

## 并发与缓存

- 热命中：peek 完整 `anon_items` → 一次指纹 → 同一把锁拷贝 rows 与 items。rows/items 不能来自不同 cursor。
- 慢路径仍会再指纹；禁止把旧指纹传去写缓存。
- `_store_revision_cache`：同 cursor 直接 return（不刷新 TTL、不丢 items）；`existing.built_at >= started_at` 的迟到旧构建不覆盖。
- Owner 与历史 `as_of`（超出 90s）仍 `items=None`，未恢复 Round 2 的 Owner 复用。
- 未使用 `BEGIN IMMEDIATE`，未延长 TTL，未跳过损坏检查。

## 游标与新旧客户端

- `query_hash` 仅在提供 `page_mode` 时纳入该字段；旧客户端哈希与基线一致。
- 可见游标表示已消费的**原始**位置，并固定 `as_of`。
- 旧前端 + 新后端：不带 `page_mode`，旧切片，不放大扫描。
- 新前端 + 旧后端：带 `page_mode=visible`（旧后端忽略）且不再 hop，首页可能先空。记录为已知限制，不是静默兼容。

## 身份隔离

- 回环 HTTP = Owner。匿名热缓存必须进程内 guest 测，浏览器对照不能冒充访客。
- `_displayable_zh` 对 Owner/访客相同：无合法中文原文则隐藏。Owner 仍能看任务态等原有权限字段。
- 未混用管理员与访客缓存换速度。

## 语言初始化

- `main.tsx` 先 `await prepareI18n()` 再 `import('./App.tsx')`。
- 中文不下载 runtime-en/ja；词典失败仍启动并回退中文。
- `setLocale` 仍落盘 + `location.reload()`。
- 不翻译模型生成的新闻/财报正文。
- 独立图例 harness 必须在 `renderLegend` 前 `installTranslations`；App 壳用例必须等首屏标题后再 Tab / Ctrl+K。已修 visual CI 竞态（en/ja 图例、跳过链接、命令面板快捷键）。

## 财报时钟

- 页级不再 `useNow(1000)`。冷却只在按钮内走秒；`cooldownUntil` 到期清零。
- 纽约日 15s 轮询；未钉住的周起始随跨日更新。最多晚约 15s 感知午夜，不做秒级整页重绘。

## 待浏览器收口

- n=20 交错对照（进行中）
- 意图预取三案：立即点击 / 停留后点击 / 划过不进入
- 财报快滚、弱网、占位高度、图表保持挂载
- 三种语言冷启动与深链接（源码契约已有，浏览器样本未齐）

确认的问题修完并复验后，才能把 Goal 标完成。
