# 突破 API 契约

版本：breakout-api-v1

## 共同规则

- 只读 status=completed 的扫描。
- GET 不调用 Provider、行情、Strength 或 Market Shape。
- 所有时间带时区；根响应包含 as_of、market_timezone、session、status、
  versions、source_status 和 events。
- status 取 active、degraded、stale、unavailable 或 disabled。
- 原始 Provider JSON 永不返回。
- limit 有上限，游标绑定 scan_run_id 和稳定排序键。

## GET /api/breakouts/current

返回最近一次完整快照。没有数据库、没有 completed scan 或功能关闭时返回空
events 和明确状态，不创建数据库。

## GET /api/breakouts/events

过滤项：

- date
- ticker
- setup_type
- lifecycle_state
- session
- min_priority
- limit
- cursor

排序以 event_at、alert_priority_score 和 event_id 形成稳定复合键。游标包含
scan_run_id，分页期间出现新扫描不会污染同一分页序列。

## GET /api/breakouts/events/{event_id}

返回事件、结构、评分、状态转换、数据来源和版本。不存在时为 404。

## GET /api/breakouts/tickers/{ticker}

返回该 ticker 最近事件和当前状态。ticker 统一校验后才进入仓储查询。

## GET /api/breakouts/status

返回：

- enabled 和 range_persistence_mode
- worker 状态及 heartbeat
- latest_completed_scan
- provider_health
- stale 状态
- database 状态
- strength_adapter 状态
- market_shape_adapter 状态
- versions

Provider 失败不改变 /ready；只体现在此接口和突破根响应。

## Event 最小字段

event_id、ticker、name、exchange、asset_type、sector、session、setup_type、
lifecycle_state、event_at、event_age_seconds、event_price、current_price、
session_change_pct、gap_pct、rvol_time_of_day、pivot_price、support_zone、
resistance_zone、invalidation_price、十个评分字段、三个 range_persistence
字段、range_persistence_status、effective_weights、contribution_breakdown、
market_shape、warnings、source_status、provenance、versions。

数值只能是有限数或 null；分数范围为 0 至 100，置信度为 0 至 1。
