# 突破信号定义

## 三阶段流水线

1. Discovery：最多 150 个粗候选，只做校验、资产过滤、成交额过滤和去重。
2. Daily Enrichment：最多 60 个候选，读取完整日线、SPY、行业 ETF、强势、
   大盘快照、基底和区间强势持续度。
3. Intraday Refinement：最多 30 个候选，读取 5 分钟 K 线、VWAP、开盘区间、
   同时点相对成交量和盘前数据。

期权、新闻和 LLM 不阻塞快照，也不修改确定性字段。

## TemporalCutoff

每次计算携带：

- event_at
- market_timezone
- session
- include_current_bar
- completed_daily_session

日线只读 event_at 前已完成的正常交易日；盘中只读 bar_end <= event_at；
默认不使用未完成 K 线。枢轴右侧确认、阻力、市场形态和 strength 都必须在
当时时点已可见。突破 K 线不能参与自身阻力生成。

## 日线基底

- 固定窗口 10、15、20、30、40、60、80 日。
- 至少两个已确认阻力触点及有效支撑结构。
- 聚类容差：max(price × 1%, ATR20 × 0.50)。
- 突破缓冲：max(price × 0.25%, ATR20 × 0.10)。
- 评估波幅、成交额收缩、支撑完整度、抬高低点和相对强弱。
- 候选选择是确定性的；pivot_id 由 ticker、基底日期、阻力区和版本生成。

## Setup 类型

DAILY_BASE_BREAKOUT

- 先有有效基底。
- 完整事件 K 线收于阻力上沿加缓冲之上。
- 高延伸仍可是真突破，但提高 chase risk。

OPENING_RANGE_BREAKOUT

- 默认开盘区间为纽约时间 09:30 至 10:00。
- 区间结束后，完整 5 分钟 K 线越过区间高点加缓冲才触发。
- 若同一根完整 K 线也越过预先存在的日线平台阻力，开盘区间突破与日线平台
  突破使用不同 event_id 分别记录；盘前缺口的延续阶段仍保持原事件身份。

PREMARKET_GAP

- 相对上一正常交易日收盘计算。
- 只使用真实盘前价格和成交额。
- 没有历史盘前数据时 premarket_rvol 为 null。
- 不能直接标记为已确认日线突破。
- 进入正常时段后沿用原 event_id、pivot_id、trading_date 和 first_seen_at；
  setup_type 只表示当前阶段，不参与重新建立事件身份。

GAP_HOLD / GAP_AND_GO / GAP_FADE

- 开盘区间结束后，完整 K 线仍高于前收加缓冲、但尚未确认越过开盘区间，
  标为 GAP_HOLD。
- 连续完整 K 线保持在开盘区间高点加缓冲之上，或单根 K 线满足强确认条件，
  标为 GAP_AND_GO。
- 任一完整正常时段 K 线收回前收价或跌破失效位，标为 GAP_FADE，并进入
  FAILED。
- 三者都是既有 PREMARKET_GAP 的阶段标签，不能脱离历史事件单独创建。

RETEST_BREAKOUT

- 既有事件先进入 RETESTING，随后完整 K 线重新收于原阻力加缓冲之上。
- 沿用原 event_id，生命周期进入 RETEST_HELD。

MOMENTUM_SPIKE

- 涨幅、成交额或相对量异常，但没有预先存在的有效结构阻力突破。
- 可进入观察，不得命名为 confirmed breakout。

RECOVERY_BREAKOUT

- 既有事件完成回踩并重新创事件新高，同时相对成交量不少于 1 时使用；
  生命周期进入 REACCELERATING。
- Market Shape 为 CAPITULATION_RECOVERY 且价格用完整 K 线收复关键水平时，
  可辅助使用该阶段标签；市场形态本身不会绕过结构、流动性或回踩证据。

## 确认和生命周期

- DISCOVERED：Provider 首次发现。
- WATCHING：已增强但未越过结构。
- TRIGGERED：首根完整有效 K 线越过触发区。
- CONFIRMED：两根完整 K 线保持，或一根满足高 CLV、足够 RVOL、有限上影
  和明显越阻力的强确认 K 线。
- HOLDING：确认后继续保持。
- RETESTING：回到突破区附近。
- RETEST_HELD：未破失效位并重新收上突破区。
- REACCELERATING：回踩成功后创事件新高并有动能支持。
- EXTENDED：距 pivot 超过配置 ATR 阈值；不等于失败。
- FAILED：完整 K 线跌破失效位、连续收回阻力下、Upthrust 或 Gap Fade。
- EXPIRED：超时、收市、结构替换或长期无数据。

FAILED 和 EXPIRED 是终态。重复扫描只更新 last_seen_at；状态变化才写
transition。前一事件进入终态后，同日新 pivot 可以创建新事件；非终态主事件
保持单一身份。

## 候选掉出后的延续扫描

- 新事件必须来自本轮 Discovery，并通过 20 日平均成交额门槛。
- 已发布且未终结的事件即使掉出 Discovery 或后来低于流动性门槛，也会按
  固定 event_id 继续读取本地完整日线和 5 分钟线；这条通道不能创建新事件。
- Worker 每轮从当前版本属于 completed scan 的事件中读取有界批次。仍在 TTL
  内的事件按 last_seen_at 从旧到新轮转；已越过 TTL 的事件使用保留配额写入
  EXPIRED，避免停机后遗留永久非终态记录。
- 查询排除 first_seen_at、last_seen_at 或 published_at 晚于 as_of 的记录，
  并从不可变扫描快照恢复当时时点的最新事件版本；同一事件后来更新也不会让
  历史回放漏掉旧版本或读到未来状态。
- 缺少完整本地盘中 K 线时，不回退使用 Discovery 价格判断失败、缺口回补、
  回踩或再加速。
- 恢复扫描会检查截止时点前全部完整 K 线。停机期间任一 K 线已回补缺口或跌破
  失效位时，后续反弹不能抹去失败证据；事件最高价也取完整可见区间，而非只取
  最新一根 K 线。
- 持续事件每轮按当前完整 K 线重算确认、流动性、追高风险、数据置信度、事件
  新鲜度和告警优先级。区间强势持续度在同一交易日沿用上一完整日线背景，影子
  模式仍不改变正式排序。
- 影子研究行仍锚定事件首次进入 Discovery 排名时的生产分与假设分；续扫只更新
  同一事件的正式当前分，不复制新的影子身份或改变生产权重。

## 同时点相对成交量

RVOL_TOD(t) =
当天截至 t 的累计成交量 /
过去 N 个完整正常交易日同一分钟累计成交量中位数

默认 N=20。只比较 America/New_York 正常交易时段；历史会话缺少目标分钟、
伪零量或样本不足时返回 null。盘前不得用完整日均量冒充盘前相对量。

## 相对强弱、行业与流动性

- 结构相对强弱使用个股相对 SPY 的 20 日和 63 日完整日线超额收益；确认相对
  强弱使用 5 日完整日线超额收益。
- 行业适配只在上游行业字段或唯一主题映射能确定流动性行业 ETF 时计算，结合
  个股相对行业、行业相对 SPY 与规范股票池行业广度；无法可靠归属时返回 null。
- 成交额百分位来自固定、版本化规范股票池，并按最后完整交易日缓存；不得使用
  当轮突破候选集合。可靠价差不可得时 spread_quality 保持 null。

## 评分分离

- breakout_quality = base 45% + confirmation 45% + liquidity 10%。
- chase_risk 独立表达追高风险，不降低 breakout_quality。
- alert_priority 组合 breakout quality、intrinsic strength、market fit、
  sector fit、data confidence 和 freshness，再扣追高惩罚。
- market shape 不进入 intrinsic、base 或原始 confirmation。
- Provider 技术指标不进入任何正式分数。
