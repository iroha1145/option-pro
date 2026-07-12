# 突破 API 契约

版本：breakout-api-v1

## 共同规则

- 只读 status=completed 的扫描。
- GET 不调用 Provider、行情、Strength 或 Market Shape。
- 所有时间带时区；根响应包含 as_of、market_timezone、session、status、
  versions、source_status 和 events。
- status 取 active、degraded、stale、paused、unavailable 或 disabled。
- 根响应和状态响应可增加 `runtime_status`、`runtime_reason`、`market_session`、
  `next_session_at`、`failure_domain`；旧字段保持不变。
- 原始 Provider JSON 永不返回。
- limit 有上限，游标绑定 scan_run_id 和稳定排序键。

## GET /api/breakouts/current

返回最近一次完整快照。没有数据库、没有 completed scan 或功能关闭时返回空
events 和明确状态，不创建数据库。

市场关闭时返回 `status=paused`。已有 completed 快照时继续返回其事件和明确的
快照时间；尚无快照时返回空 events，但不会伪装成 Provider 故障。

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

date 必须是实际存在的 ISO 日期；setup_type、lifecycle_state 和 session 只接受
公开枚举值，非法值在进入仓储前返回 422。

排序以稳定的 event_at、alert_priority_score 和 event_id 形成复合键。游标包含
scan_run_id，分页期间出现新扫描不会污染同一分页序列。

## GET /api/breakouts/events/{event_id}

返回事件、结构、评分、状态转换、数据来源和版本。不存在时为 404。

## GET /api/breakouts/tickers/{ticker}

返回该 ticker 最近事件和当前状态。ticker 统一校验后才进入仓储查询。数据库健康
但没有事件时为 `status=empty`；数据库读取故障时为 `status=unavailable`。

## GET /api/breakouts/status

返回：

- enabled 和 range_persistence_mode
- worker 状态及 heartbeat
- latest_completed_scan
- provider_health
- stale 状态
- database 状态
- strength_adapter 状态
- market_shape_adapter 实现状态；运行时数据状态以最近完整扫描为准
- versions

Provider 失败不改变 /ready；只体现在此接口和突破根响应。

## Event 最小字段

event_id、ticker、name、exchange、asset_type、sector、session、setup_type、
lifecycle_state、event_at、first_seen_at、triggered_at、state_changed_at、last_seen_at、
event_age_seconds、state_age_seconds、observation_age_seconds、event_price、current_price、
session_change_pct、gap_pct、rvol_time_of_day、pivot_price、support_zone、
resistance_zone、invalidation_price、十个评分字段、区间持续性数值、斜率、比例、
自身/全局/行业百分位、range_persistence_status、交互证据、configured_weights、
effective_weights、contribution_breakdown、penalties、missing_components、score_version、
market_shape（含 state、confidence、transition_risk、eligibility 和 rules）、
warnings、source_status、provenance、versions。

数值只能是有限数或 null；分数范围为 0 至 100，置信度为 0 至 1。

三种年龄分别锚定首次触发或首次发现、最近状态变化、最近成功复核。普通续扫只会
降低 observation_age_seconds，不会重置 event_age_seconds 或 state_age_seconds。

## 大盘形态时间

大盘形态的 `as_of`、`entered_at` 和交易日计数使用最后一个完整日线的实际收盘
时刻，周末和盘前请求不会制造虚假的状态切换时间。`history_truncated=true` 表示
状态机只重放配置窗口；若 `days_in_state_is_lower_bound=true`，`days_in_state` 表示
“至少已持续”的天数，避免把窗口首日误写成已知的真实起点。
