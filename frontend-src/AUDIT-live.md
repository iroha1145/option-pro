# Live 模式契约审计报告（fix-v3-api）

基准：`/mnt/agents/output/api-contract.md`（唯一事实来源）。范围：`src/api/**`、
`src/components/catalysts/api.ts`、`src/components/detail/api.ts`、`src/mocks/session.ts`（只读核对，未改）。
mock 路径（`mockOr` fixture 分支）行为零改动；所有修正均在 live 分支与共享 client。

## 审计表

| 模块 | 端点 | 状态 | 备注 |
|---|---|---|---|
| client | 全局 | 已修 | 新增 `toQuery`（undefined/null/空串不发、数组重复键、布尔字符串化）；新增 `requestRaw`/`postCreate`/`idFromLocation` 支持 202 Location/Retry-After；X-Optix-Action 仅非 GET ✓、`credentials:'include'` ✓、`redirect:'error'` ✓（原有，确认） |
| access | GET /access/status · POST /access/login · POST /access/logout | 已修 | 登录/登出后改为重拉 GET /access/status（契约「前端只认 status」）；错误经 client 透传 bizCode（login_cooldown/https_required/owner_login_required）与 retryAfter，形状与 Login.tsx `mapError` 消费一致（Login.tsx 只读核对，未改） |
| market | GET /market/indices · GET /market/status | 已修 | 信封解包 `{indices}`；`symbol→code`、`change_percent→changePct`（change 由 price×pct 反推，真实算术）；status `market∈open/premarket/postmarket/closed` → session/label/nextEvent（next_open/next_close） |
| stocks | GET /stocks/watchlist | 已修 | 信封 `{groups[].stocks[]}` → 扁平 WatchlistItem；`change_percent/spark/quote_as_of` 对齐；契约无 strengthScore/signals → null/[]（不编造）；`?force=1` 保留（契约未列，后端忽略多余参数） |
| stocks | GET /stocks/{t}/chart | 已修 | **range 显式映射表** `CHART_RANGE_MAP`：1D→1d、5D→1w、1M/6M/1Y/ALL→1w（有损，类型注释标注）；`adjustment` 恒发 `raw`；响应 `{bars:[{t,o,h,l,c,v,quote_only}]}` 字段名 1:1 对齐（mock 的 StockChartEx 同名 ✓；`stocksApi.chart` 的 mock 为 candles 形状、无消费方，live 归一为 candles+ma20 由收盘真实推导） |
| stocks | GET /stocks/search | 已修 | 返回键 `results`/`items` 容错归一 |
| stocks | GET /stocks/{t} · /signals | ✓ | 路径正确；detail 概览契约未给字段清单，透传（见残余风险） |
| signals | GET /signals/market · /signals/stock/{t} · POST /signals/stock/{t}/ai-analysis | 已修 | stock 信封 `{signals}` 解包；ai-analysis 创建改走 `postAiJob`（202 Location 提取 job_id） |
| strength | GET /strength/scan · /market · /profiles | 已修 | scan 仅下发契约白名单参数（universe/timeframe/profile/top/sector_id/min_price/min_avg_dollar_volume/include_options；UI `sector`→`sector_id`），band/minScore/sort/order 客户端套用；StrengthRow→ScreenerRow 字段对齐（子分为周期分近似，注释标注；sparkline 契约无 → []） |
| breakouts | current · status · events · events/{id} · tickers/{t} | 已修 | current 解包 `{events}`；events 改发契约参数 `lifecycle_state/limit/cursor`（page/type/result 不下发），`{events,next_cursor}`→`{items,total,page}`（total 由 next_cursor 推算）；events/{id} `{event,structure,scores,transitions}` 拍平 |
| sectors | GET /sectors · /sectors/{id}/iv-ranking | 已修 | 信封 `{sectors}` / `{rankings}` 解包；行内 snake_case 由页面 `normalizeSector/normalizeIvRow` 兼容（既有） |
| earnings | GET /earnings/upcoming · POST /earnings/upcoming/refresh · GET /ai/earnings-impact/{t} | 已修 | 信封 `{earnings}` 解包 + snake→camel 行映射（actual 恒 null，留空纪律）；refresh 路径 ✓；409 `analysis_required` 由 client 透传 code+bizCode，ImpactCard 状态机消费 ✓ |
| options | GET /options/unusual · /{t}/expirations · /{t}/chain | 已修 | **参数名 `min_vol_oi`（下划线）** + `type∈all/call/put`；`{results}` 解包；chain `{underlying_price,calls,puts}` 按 strike 合腿为 OptionChainRow；direction 恒 null 不复活（sentiment 恒 neutral，契约 §3） |
| catalysts（模块） | status · feed · news · tickers/{t} · tickers/batch · calendar · hotspots · hotspots/status · market-focus-cycles/latest | 已修 | feed UI 参数→契约参数（sentiment→classification bullish/bearish/neutral、pageSize→limit、page 不下发）；batch body `{tickers≤50, include_neutral}`；calendar `{events}` 按日分组；hotspots `?limit=8` ✓ |
| catalysts（页网关） | feed · status · news · hotspots · calendar · sources · focus-cycles · tickers/batch · news/{id}/analysis · analysis-jobs/{id}(/cancel) | 已修 | 全量 snake→camel 归一；**tickers/batch body 修正**（原错发 camelCase 查询对象且无 tickers：现按 feed source_tickers ≤50 收集后 POST `{tickers,window_hours,limit,include_neutral:true}`）；分析/焦点任务创建走 `postCreate` 提取 Location；上一成功焦点周期由 `market-focus-cycles/latest` 的真实 `previous_successful_cycle` 历史字段提供。 |
| ai-jobs | POST /ai/jobs/earnings-impact · /option-alerts · GET /ai/jobs/{id} · POST /ai/jobs/{id}/cancel | 已修 | **202 + Location 提取 job_id**（body 无 job_id 时）；**状态归一**：preparing/pending→queued、processing/running/cancel_requested→in_progress、completed→succeeded、canceled→cancelled、其余失败类终态→failed；kind 由 job_type 映射；cancel body 补 `{confirm:true}`；option-alerts body 改契约 `{ticker,force,alerts,underlying_price,expiration}`；AIJobStatus 类型加 `in_progress` |
| runtime | GET/PUT /runtime-settings · /history · /rollback · GET /worker/status · POST /worker/actions/{action_type} | 已修 | **乐观锁**：先 GET 取真实 version → PUT `{expected_version, settings}`；409 `version_conflict` 取 payload.current_version（兼容 error 嵌套，缺失则重新 GET）自动重试一次再抛；rollback 同样处理 `{expected_version, target_version}`；worker action_type 与契约枚举一致（strength_refresh/breakout_refresh ✓） |

轮询退避核对：ImpactCard `BACKOFF_MS=[2000,3000,5000,8000,10000]` ✓、NewsDrawer `BACKOFF=[2,3,5,8,10]s` ✓（与契约 §0.4 一致，未改）。

## 修复清单（对应已知必修项）

1. chart range 映射表（stocks 模块 + detail/api.ts 共用 `CHART_RANGE_MAP`）✅
2. stocks/search `results|items` 容错 ✅
3. runtime-settings 乐观锁 GET→PUT、409 自动重试一次；rollback 同处理 ✅
4. AI 任务 202 Location 提取 + 状态归一 + `{confirm:true}` 取消体 ✅（退避 [2,3,5,8,10]s 既有，确认一致）
5. 登录错误 bizCode/retryAfter 透传核对 ✅（access 模块登录后重拉 status，Login.tsx 消费形状匹配，未改页面）
6. options/unusual `min_vol_oi` 序列化 ✅
7. X-Optix-Action 仅非 GET / credentials:'include' / redirect:'error' 确认 ✅
8. 端点路径逐一核对（含 tickers/batch body 键、earnings/upcoming/refresh、worker/actions/{action_type}、ai/earnings-impact 409 透传）✅
9. 查询参数序列化 `toQuery`（undefined/null/空串不发、数组/布尔正确序列化）✅

## 残余风险（无法静态验证 / 需真实后端联调）

- **AI result 内层字段**：AIJobPublic.result 按 job_type 结构不同，契约未给内层清单；live 归一时取 text/summary/headline_summary，否则 JSON 序列化。earnings-impact 的 GET 结果内层字段（EarningsImpactResult 扩展）依赖页面 exNum 的 snake_case 兼容，需联调确认。
- **stocks/{t} 概览**、signals/market、strength/market、strength/profiles、worker/status 的契约字段清单未在 contract §1 展开，映射按 snake/camel 双名容错，缺失字段为 null/0（页面「—」纪律需联调目检）。
- **watchlist strengthScore/signals、strength scan 四维子分/sparkline、earnings timing**：契约无对应字段。strengthScore 置 null（StrengthBar 需目检）、子分以周期分近似（已注释）、timing 回退 'bmo' —— 均需 UI 联调确认呈现不误导。
- **breakouts events total**：契约无 total，由 next_cursor 推算（可能多算一页）；分页为 cursor 制，UI page 制仅近似。
- **catalysts feed 的 multi_source_only/theme 过滤**契约无参数，live 下该两项筛选不生效（不下发、不伪造结果）。
- **tickerSummaries** 无 ticker 入参时需先拉 feed 收集 source_tickers（两次请求）；batch 响应信封（map vs array）已双兼容，需联调确认。
- **runtime settings 内层形状**：契约为嵌套 `{ai:{...}, catalyst:{...}}`，UI RuntimeSettings 为扁平 mock 形状；当前无页面消费 settings/updateSettings/history/rollback（仅 workerAction 被用），将来接入设置页时需做嵌套映射。
- **detail 页 useAiJob**（不可改文件）：固定 2.5s 轮询（非契约退避序列），且 cancel 守卫仅认 queued/running —— live 归一后的 in_progress 任务在详情页不可取消（earnings/catalysts 页任务流不受影响）。
- **GET /worker/actions?action_type&limit 与 /worker/actions/{request_id}** 契约存在但无 UI 消费，模块未暴露。
