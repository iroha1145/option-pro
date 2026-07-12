# 突破雷达架构冻结

状态：冻结于 2026-07-12  
基线：main 18420ae32c15d7572b20e901027eafaf46d08d03  
功能分支：feat/breakout-radar-range-persistence

## 目标和边界

突破雷达（Breakout Radar）是独立后端领域模块。它把第三方粗候选交给
Option Pro 自有行情重新验证，保存可重放的事件生命周期，并通过只读接口
发布最近一次完整快照。区间强势持续度（Range Persistence）是趋势背景，
默认运行影子模式，不代表机构行为。

首轮后端交付不改页面、样式、导航和图表。后续的用户界面接入阶段新增独立
`#breakouts` 路由，并且只读取本节定义的只读接口；页面刷新不会触发全市场
扫描。无论是否接入页面，都不把新闻、期权或大语言模型（LLM）结果写入
确定性评分。

## 进程和模块

1. FastAPI 进程
   - 只读 SQLite 已完成快照。
   - 不创建扫描线程，不调用 Discovery Provider，不下载全市场行情。
   - 数据库或 Worker 不可用时，仅突破接口降级；/ready 和旧接口保持原义。

2. 独立 Worker
   - 执行发现、规范化、日线增强、盘中增强、结构检测、评分、生命周期和发布。
   - 独占迁移与写入职责。
   - 支持常驻调度、--once、单实例租约、心跳、失效令牌和优雅退出。

3. SQLite /data/optix.db
   - 开启 WAL、foreign_keys、busy_timeout 和显式事务。
   - completed 是唯一对 API 可见的扫描状态。
   - 扫描结果在一次短写事务中原子发布。

## 目录边界

- app/services/technical/range_persistence.py：精确旧公式和改良连续特征。
- app/services/breakouts/models.py：公共领域模型与枚举。
- app/services/breakouts/protocols.py：窄端口协议。
- app/services/breakouts/providers：可替换发现层。
- app/services/breakouts/feature_engine.py：时点裁剪和技术特征。
- app/services/breakouts/base_detector.py：日线基底。
- app/services/breakouts/breakout_detector.py：事件触发和确认。
- app/services/breakouts/lifecycle.py：显式状态机。
- app/services/breakouts/scoring.py：独立评分与贡献分解。
- app/services/breakouts/repository.py：迁移、租约、事件和快照。
- app/services/breakouts/worker.py：独立运行入口。
- app/services/breakouts/adapters：行情、强势、大盘形态和规范股票池适配器。
- app/api/breakouts.py：Pydantic 只读响应。
- frontend/static/js/pages/breakouts.js：只读快照、筛选、详情和个股轨迹接线。
- frontend/static/css/optix-breakouts-v3.css：与 Optix Pro v3 隔离的响应式页面样式。

禁止把新逻辑堆入 strength/scanner.py、api/stocks.py、signals.py 或单个超大
service.py。旧模块只增加必要的窄入口。

## 冻结协议

DiscoveryProvider

- scan(session, as_of, profile) 返回 DiscoverySnapshot。
- 只发现粗候选，不确认结构、不计算最终分、不调用 LLM。

PriceDataPort

- 批量返回日线和盘中 OHLCV。
- 每份数据带 source、raw_as_of、cutoff、session、adjustment 和 completeness。
- 日线排除未完成交易日；盘中排除 event_at 之后和未完成 K 线。

StrengthScoringPort

- 接受显式 ticker 集合，关闭期权增强。
- 返回 score、score_scope、confidence、score_version、included_features、
  factor_breakdown、coverage 和 as_of。
- 只有 scope=intrinsic 才能填 intrinsic_strength_score。

MarketShapePort

- 返回 status、六态 state、confidence、transition_risk、as_of、rules、
  warnings 和 version。
- 当前仓库没有可靠六态引擎；第一版适配器返回 unavailable，market_fit 为
  null，不能把旧标量大盘分伪装成六态。

CanonicalUniversePort

- 返回稳定股票池、primary sector、多主题映射、as_of 和 universe_version。
- 第一版以项目固定主题池建立保守基线，不声称覆盖全美规范股票池。
- 股票池外动态候选仅使用自身历史百分位。

BreakoutRepository

- 保存扫描、候选、结构、事件、转换、Provider 健康、Worker 状态和影子数据。
- 写入幂等，发布原子，读取锚定 completed scan。
- 每轮用两条有界只读查询载入持续事件：TTL 内事件按最久未检查优先，已越界
  事件使用最多 40 条保留通道。查询只接受事件当前版本所属的 completed scan，
  并排除 as_of 之后写入的数据。

## 事件身份与阶段分类

- event_id、origin_setup_type、trading_date、pivot_id 和 first_seen_at 创建后固定。
- setup_type 是可演化的阶段分类。PREMARKET_GAP 可依次成为 GAP_HOLD、
  GAP_AND_GO 或 GAP_FADE；回踩与再加速可成为 RETEST_BREAKOUT 或
  RECOVERY_BREAKOUT，但都沿用原 event_id。
- 当前存在持续事件时，同一 ticker 不从 Discovery 旁路再建事件。新事件通道
  始终经过平均成交额门槛；持续事件通道只能更新已有编号。
- 持续事件只用截止时点前已完成的本地 K 线做状态判断。Discovery 价格仍可
  展示为粗筛上下文，但不能触发失败、回补、回踩或恢复。

## 公共模型

枚举冻结为附件约定：

- MarketSession：premarket、regular、postmarket、closed。
- DiscoveryProfile：regular_movers、premarket_gappers。
- AssetType：common_stock、etf、adr、preferred、warrant、unit、fund、unknown。
- BreakoutSetupType：DAILY_BASE_BREAKOUT、OPENING_RANGE_BREAKOUT、
  PREMARKET_GAP、GAP_AND_GO、GAP_HOLD、GAP_FADE、RETEST_BREAKOUT、
  MOMENTUM_SPIKE、RECOVERY_BREAKOUT。
- BreakoutLifecycleState：DISCOVERED、WATCHING、TRIGGERED、CONFIRMED、
  HOLDING、RETESTING、RETEST_HELD、REACCELERATING、EXTENDED、FAILED、
  EXPIRED。

所有时间内部存 UTC，API 输出带时区 ISO 8601，并带 market_timezone。
无时区 datetime 在模型入口拒绝。

## 评分字段

互相独立：

- intrinsic_strength_score
- base_quality_score
- breakout_confirmation_score
- liquidity_quality_score
- chase_risk_score
- sector_fit_score
- market_fit_score
- breakout_quality_score
- alert_priority_score
- data_confidence_score

缺失组件的活跃权重为零，不补 50。活跃权重不足时分数为 null。
追高风险不降低 breakout_quality，只在 alert_priority 中形成惩罚。
市场形态只影响 eligibility、market_fit 和 priority。

## 版本

- breakout-api-v1
- tradingview-discovery-v1
- breakout-features-v1
- breakout-detector-v1
- breakout-score-v1
- range-persistence-v1
- market-shape-adapter-v1
- strength-intrinsic-v1
- canonical-universe-v1
- breakout-db-v1

版本、阈值配置哈希、ticker 集合哈希、session 和 source date 都进入缓存键。

## 数据库和接口

数据库表及索引见 persistence.md。API 路径冻结为：

- GET /api/breakouts/current
- GET /api/breakouts/events
- GET /api/breakouts/events/{event_id}
- GET /api/breakouts/tickers/{ticker}
- GET /api/breakouts/status

接口模型不含 raw_provider_fields 或 provider_payload_json。

## 降级顺序

1. Provider 失败：有界重试、合格旧快照、熔断；`unavailable` 或
   `degraded + 空候选` 不发布，保留上一 completed scan。只有无告警的 active
   空结果才作为真实空扫描发布。
2. 个股行情缺失：该股票组件为 null，降低覆盖与置信度。
3. Strength 缺失：intrinsic 为 null，priority 重归一。
4. Market shape 缺失：market_fit 为 null，不补 50。
5. SQLite 忙或不存在：突破接口 degraded/unavailable，旧接口不受影响。
6. Range Persistence 缺失：不产生贡献；shadow 记录缺失原因。

## 文件所有权和波次

- Wave 0：根任务拥有文档、许可证和基线。
- Wave 1A：technical/range_persistence.py 及专项测试。
- Wave 1B：模型、协议、配置、Provider、规范化及专项测试。
- Wave 2A：特征、基底、检测、生命周期、评分及专项测试。
- Wave 2B：仓储、时钟、健康、Worker 及专项测试。
- Wave 3：四类适配器、score_ticker_set 和兼容测试。
- Wave 4：API、主路由、Docker、.env.example 和运维文档。
- Wave 5：影子研究、导出和验证。
- Wave 6：完整回归、终审和修复。

共享文件只有根任务写入：app/config.py、app/main.py、docker-compose.yml、
Dockerfile、.env.example、依赖锁、迁移版本和公共 API 模型。

## 向后兼容

- 旧字段不删除，旧列表和市场接口不改路径。
- 旧 strength 扫描保持行为；新增显式 ticker 集合入口供 Worker 使用。
- 公共单股 strength 保留旧整池评分、profile 和 market_regime 语义；Worker
  只调用新增的显式 ticker 集合入口，并复用本轮日线快照。
- 新功能默认 BREAKOUT_RADAR_ENABLED=false。
- Range Persistence 默认 shadow，不改变正式分数、分类和排序。
- 后续用户界面阶段只新增只读接线，不改变扫描、评分或发布语义。

## 已知限制

- TradingView America 扫描器不是官方稳定 API，也不等于规范全美股票池。
- 第一版规范股票池来自项目固定主题池，股票池外候选缺少全局和行业百分位。
- 当前项目没有可靠六态大盘形态，market_fit 会降级为 null。
- 历史盘前逐时段数据不足时 premarket_rvol 为 null。
- 市场时钟复用现有假日和提前收盘规则；临时休市需后续引入可靠日历源。
- 所有阈值和权重是 bootstrap defaults，未经历史最优性证明。
