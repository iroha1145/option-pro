# 设计说明

## 方案选择：B（维护参数变体生命周期）

评估了方案 A（共享完整基础快照再生成筛选视图）。现有 `scan_strength` **已经先对完整主题宇宙评分，再套板块/价格/Top N 视图**（`scanner.py`：「Always score the complete canonical universe」）。同一输入下，半导体过滤不会重新标准化 NVDA 的分数（`test_b03_sector_filter_does_not_rescore_the_same_ticker`）。

方案 A 若改成「一份全池快照 + 客户端/读路径再过滤」，还要处理现有 4MB 快照上限、按参数分文件的兼容路径和 120 行返回上限，改动面大于这次故障。因此采用方案 B：

- 继续按完整筛选参数落独立快照。
- 读取时用输入截至交易日 + TTL + 评分版本判断新鲜度，而不是只看文件 mtime。
- 管理员查询陈旧/未知/历史变体时提交 `strength_refresh`，同参数合流已受理任务。
- 定时 `__call__`：默认参数 + 最多 4 个最近使用的非默认可变体。变体降级或异常会计入部分失败，不再把默认定时成功当成整轮成功；默认定时截止时间不因此推迟。
- 变体磁盘仍受 `_STRENGTH_SNAPSHOT_VARIANT_LIMIT = 24` 与原子替换约束。
- 访客 GET 不 `utime`、不提交任务、不打供应商。

没有为每个参数组合增加新的定时器，也没有在 Web 进程加第二套扫描循环。

## 新鲜度政策

实现：`backend/app/services/strength/freshness.py`。

- 日期-only 按 **NYSE 常规/提前收盘** 解析，不用 UTC 午夜。
- `expected_complete_session` = 最近已完成交易日；收盘后 6 小时供应商回填缓冲内仍接受上一交易日。
- 输入落后期望交易日 → `stale` / `score_data_behind_session`。
- 落后超过 7 个交易日 → `historical` / `score_data_too_old`。
- 无 `score_data_through`：不填 `Date.now()`；TTL 内 `_stale=false` 且 `source_status=unknown`。
- 文件今天写入不能掩盖旧日线。
- 全部供应商失败或整池 `data_error` / `insufficient_history`：不可发布，保留上一份快照（`kept_previous_snapshot`）。
- 汇总日期反映评分池中最早的输入，读取时也核查各行日期；旧或缺失评分版本不能标为当前结果。
- 迟到写入的 `saved_at` 更旧时 `kept_newer_publish`。
- 原子发布前比较保守输入截止时间、评分版本和可用覆盖度；更早行情不能只凭更晚保存时间覆盖良好快照。同交易日重算且覆盖度未下降可以成功。版本迁移或明确降级必须带可追踪原因。
- 合法休市且输入覆盖期望交易日：可以 `active`，价格/分数不变是合法结果。

## 前端状态机

阶段来自真实任务状态：`reading` / `queued` / `running` / `verifying` / `reused` / `done` / `failed`。不用计时器伪造百分比（演示 mock 仍保留 `minMs` 可见等待，live 为 0）。

- 管理员：`stale` / `missing` / `unknown` / `historical` / 显式强制刷新 → 提交任务。
- 访客与 mock：只读。身份以 `isOwner` 为准，不用 `isSignedIn`。
- `sessionStorage` 保存已受理任务；同参数先查状态，不盲重 POST。
- 任务完成后 `resetMarketReadPaths` + `marketGet(..., { force: true })`，force 使用 `cache: 'reload'`，且不与非 force 在途 GET 合流。
- 可见页 45s 只读轮询发现新发布；`document.hidden` 时暂停。
- 扫描中若草稿仍是当前在飞参数，主按钮保持锁定；改成另一组条件后解锁，新点击提升世代，迟到的 A 写回被丢弃。
- `lastScanAt` = 快照完成时间；`queryCheckedAt` = 本轮确认时间。
- 选股备用价标签为「扫描价」/「扫描价 · 日线」；共享组件在其他页面显示「参考价」。
- 报价只有交易日期时按纽约日期保守比较；有准确时刻则比较时刻，不固定推算为 20:00 UTC。
- 涨跌幅只在同一路报价可用时使用；不把另一时点的涨跌幅粘到新价格上。

## 权限与不变项

- `POST /api/worker/actions/*` 仍走 `require_same_origin_action` → 非 owner 为 401/403。
- GET `/strength/scan` 仍只读已发布快照，不在请求里访问供应商。
- 未改画线、副图、评分权重、宏观公式、新闻模型、日历布局。
- 未提高外部订阅或付费预算。
