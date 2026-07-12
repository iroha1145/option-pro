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

PREMARKET_GAP

- 相对上一正常交易日收盘计算。
- 只使用真实盘前价格和成交额。
- 没有历史盘前数据时 premarket_rvol 为 null。
- 不能直接标记为已确认日线突破。

MOMENTUM_SPIKE

- 涨幅、成交额或相对量异常，但没有预先存在的有效结构阻力突破。
- 可进入观察，不得命名为 confirmed breakout。

RECOVERY_BREAKOUT

- 深度回撤后收复关键水平，供 CAPITULATION_RECOVERY 市场形态使用。

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
transition。同日新 pivot 可以创建新事件。

## 同时点相对成交量

RVOL_TOD(t) =
当天截至 t 的累计成交量 /
过去 N 个完整正常交易日同一分钟累计成交量中位数

默认 N=20。只比较 America/New_York 正常交易时段；历史会话缺少目标分钟、
伪零量或样本不足时返回 null。盘前不得用完整日均量冒充盘前相对量。

## 评分分离

- breakout_quality = base 45% + confirmation 45% + liquidity 10%。
- chase_risk 独立表达追高风险，不降低 breakout_quality。
- alert_priority 组合 breakout quality、intrinsic strength、market fit、
  sector fit、data confidence 和 freshness，再扣追高惩罚。
- market shape 不进入 intrinsic、base 或原始 confirmation。
- Provider 技术指标不进入任何正式分数。
