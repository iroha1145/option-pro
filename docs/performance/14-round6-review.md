# Round 6 独立复查（进行中）

审查对象：`cursor/perf-news-i18n-earnings-ac0f`，对照基线 `df1bd5d`。本篇只记本轮确认项，不把历史 1651/832 当成本轮证据。

## 并发与缓存

- 热命中：peek 完整 `anon_items` → 一次指纹 → 同一把锁拷贝 rows 与 items。rows/items 不能来自不同 cursor。
- 慢路径仍会再指纹；禁止把旧指纹传去写缓存。
- `_store_revision_cache`：同 cursor 直接 return（不刷新 TTL、不丢 items）；`existing.built_at >= started_at` 的迟到旧构建不覆盖。
- Owner 与历史 `as_of`（超出 90s）仍 `items=None`，未恢复 Round 2 的 Owner 复用。
- 未使用 `BEGIN IMMEDIATE`，未延长 TTL，未跳过损坏检查。

## 默认 visible 的边界

- 列表、`feedApiPath` 默认、theme-boot 预取必须带 `page_mode=visible`。
- `newsToday` 与 `tickerSummaries` 必须省略 `page_mode`，否则会在首条 feed 落地后再扫 108 条。已修；生产包待 n=20 结束后同步。

## 游标与新旧客户端

- `query_hash` 仅在提供 `page_mode` 时纳入该字段；旧客户端哈希与基线一致。
- 可见游标表示已消费的**原始**位置，并固定 `as_of`。
- 旧前端 + 新后端：不带 `page_mode`，旧切片，不放大扫描。
- 新前端 + 旧后端：带 `page_mode=visible`（旧后端忽略）且不再 hop，首页可能先空。记录为已知限制，不是静默兼容。

## 身份隔离

- 回环 HTTP = Owner。匿名热缓存必须进程内 guest 测，浏览器对照不能冒充访客。
- `_displayable_zh` 对 Owner/访客相同：无合法中文原文则隐藏。Owner 仍能看任务态等原有权限字段。
- 未混用管理员与访客缓存换速度。

## 语言冷启动测速

`/catalysts` 原先没有 laboratory ready 分类，三种语言各会空等 45s。已按标题 + `article h3` / 空态判定 content/empty/shell。这只影响测速脚本，不改业务 ready 区。

## 语言初始化

- `main.tsx` 先 `await prepareI18n()` 再 `import('./App.tsx')`。
- 中文不下载 runtime-en/ja；词典失败仍启动并回退中文。
- `setLocale` 仍落盘 + `location.reload()`。
- 不翻译模型生成的新闻/财报正文。
- 独立图例 harness 必须在 `renderLegend` 前 `installTranslations`；App 壳用例必须等首屏标题后再 Tab / Ctrl+K。已修 visual CI 竞态（en/ja 图例、跳过链接、命令面板快捷键）。

## 财报时钟

- 页级不再 `useNow(1000)`。冷却只在按钮内走秒；`cooldownUntil` 到期清零。
- 纽约日 15s 轮询；未钉住的周起始随跨日更新。最多晚约 15s 感知午夜，不做秒级整页重绘。

## 意图预取并发

`MAX_INTENT=2` 必须按「正在下载的路由块」计数。用 `queueMicrotask` 立刻清 `inflight` 时，连扫三个导航项会同时开三个 import。已改为等 `import()` settle 再释放名额。已预取过的路径不再重复下载。

## surfaces 本地 fulfill

首页会打指数、时段、雷达、自选、报价。浏览器 abort `finnhub|yahoo|…` 只挡页面直连，挡不住 uvicorn 出站。对照脚本在到达 `:2000` 之前 fulfill `/api/market/indices`、`/status`、`/strength/market`、`/signals/market`、`/breakouts/*`、`/stocks/watchlist`、`/account/watchlist`、`/market/cta`、`/quotes`，并 abort `/api/quotes/stream`。财报日历仍本地 fulfill，不打 upcoming 刷新。尚未跑浏览器。

重点口径只认 `publicFeatured` 或账号自选，市值再大也不会自动入选。实验室财报行必须带 `publicFeatured: true`，否则默认「重点公司」列表为空，`DeferredEpsChart` 直接 `return null`，滚动样本找不到 `[data-eps-chart-slot]`。个人自选 fulfill 为空名单，不依赖隔离库里碰巧有的账号数据。

## surfaces 选择器（已修脚本，尚未跑浏览器）

390px 主导航是 `hidden xl:flex`。DOM 里第一个 `a[href="/earnings"]` 不可见；财报在 Dock「更多」里是 `button`。首页可见入口是 `section[aria-label="财报临近"]` 的「查看全部」。意图预取挂在主导航 / Dock / 命令面板，不挂首页卡片。对照改为：切页点首页卡片；E 三案在 1440 主导航上悬停/点击。旧脚本会在隐藏链上超时或点到未挂预取的节点，不能用来决定是否回退 E。

## 待浏览器收口

- n=20 交错对照（进行中，已到 13/20；不得把未完成样本写成最终 n=20）
- 意图预取三案：立即点击 / 停留后点击 / 划过不进入（必须用桌面主导航）
- 财报快滚、弱网、占位高度、图表保持挂载
- 三种语言冷启动与深链接（源码运行时测试已补，浏览器样本未齐）

确认的问题修完并复验后，才能把 Goal 标完成。
