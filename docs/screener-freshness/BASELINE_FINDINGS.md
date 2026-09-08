# 基线核实（BUG-01–07）

- 基础提交：`55419c8e0f5822457f140761de98c4aa90e10c6a`（`origin/main`）
- 线上具体失败原因：**尚未证实**。没有生产日志或生产密钥。用户截图只是问题线索。
- 截图中的「数据未刷新 · 2026/08/02 11:01」在已核对代码里来自快照保存时间，不能自动等同于行情截至时间。右上角「退出」也不能证明当时主体是管理员。

| 编号 | HEAD 文件/函数 | 复现步骤 | 实际（基线） | 期望 | 确认 | 证据 |
|---|---|---|---|---|---|---|
| BUG-01 | `frontend-src/src/pages/Screener.tsx` `runScan` | 管理员对 `200 + stale=true` 点「开始扫描」 | 只读快照；仅 `503/strength_snapshot_unavailable` 才 POST `strength_refresh`。基线 `runScan` 在成功读后没有 `result.stale` 分支 | 过期成功响应也要提交/合流对应参数任务，核验后再读 | 确认（源码） | `git show 55419c8e:frontend-src/src/pages/Screener.tsx`：只匹配 `strength_snapshot_unavailable`；`frontend-src/tests/screener-freshness-flow.test.mjs` `A01` |
| BUG-02 | 同上，`setLastScanAt(Date.now())` | 读完任意快照 | 点击/读取时间被标成扫描时间（基线约 L249） | 使用发布完成时间；读取时间单独展示 | 确认（源码） | 基线 `setLastScanAt(Date.now())`；现 `setLastScanAt(times.scanCompletedAt)` |
| BUG-03 | `backend/app/worker/tasks.py` `StrengthRefreshTask.__call__`；`backend/app/api/strength.py` `_strength_snapshot_path` | 默认快照新、半导体变体旧 | 定时任务只刷默认参数文件 | 陈旧变体不得当当前结果；近期变体有上限刷新 | 确认（源码+测试） | `tests/test_strength_variant_lifecycle.py` `test_b01_*` / `test_scheduled_refresh_updates_recent_variants_only` |
| BUG-04 | `backend/app/api/strength.py` `_read_strength_snapshot` / `scan` | 保存未满 26h，但日线落后很多交易日 | 只按文件保存时间 TTL 标过期，旧输入仍可当新鲜正文 | 按完整交易日与输入时间裁决；极老为 historical | 确认（源码+测试） | `tests/test_strength_freshness.py` `test_recent_save_cannot_hide_old_daily_input` |
| BUG-05 | `ResultCards.tsx` / `ResultTable.tsx` `LivePrice` | 移动卡片与桌面表比较报价 | 只传扫描价，不传 `fallbackAt` | 按成交/数据时间比较 | 确认（源码） | 基线无 `fallbackAt`；现 `fallbackAt={r.priceAsOf ?? r.dailyDataThrough}` |
| BUG-06 | `frontend-src/src/lib/liveQuotes.ts` `displayedQuoteLabel` | 采用备用价 | 固定「定时更新」 | 显示扫描价/日线及真实日期 | 确认（源码+测试） | `live-quotes-behavior.test.mjs`；`fallbackQuoteLabel` |
| BUG-07 | `frontend-src/src/api/marketRead.ts` `marketGet` | 任务完成后 force 读 | force 仍可能合流在途 GET；底层 fetch 无 `cache:'reload'` | force 不与旧 GET 合流，并穿透 HTTP 缓存 | 确认可复现（内存层）；HTTP 层见 C01 | `market-read.test.mjs` `force read does not join...`；C01 需无 `page.route` 的浏览器上下文 |

未拿到生产 Worker 日志，不能把「用户看到 0.1 秒返回」写成线上任务失败。0.1 秒更符合读已有快照，而不是一次全池重算。
