# 强势评分与大盘形态语义

版本：strength-v2 / market-shape-v3
状态：生产评分语义；区间强势持续度仍为影子模式（shadow）

## 四类分数

- `intrinsic_score`：只使用股票自身价格、成交量、技术结构及相对 SPY 的价格强弱。
  不读取页面筛选、期权、大盘形态或临时候选列表。
- `market_fit_score`：大盘形态对当前环境的适配度。形态降级时按置信度向 50 收缩；
  形态不可用时为 `null`，不会制造中性证据。
- `profile_fit_score`：对保守、均衡或进取风险档案的适配度。
- `ranking_score`：用于当前强势雷达排序。兼容字段 `final_score` 和
  `strength_score` 均等于它，且 `score_scope="ranking"`。

显式股票集合入口只返回内在语义：`score=intrinsic_score`，
`score_scope="intrinsic"`。两个入口调用同一内在评分函数。

## 缺失数据

所有因子先保留 `null`。缺失因子的有效权重为零，其余有效权重重新归一化；不会将
未知值改写为 50。有效权重不足时分数为 `null`，状态为 `insufficient_data`。
`confidence` 表示有效证据占配置权重的比例；`configured_weights`、
`effective_weights`、`contributions` 和 `missing_components` 共同构成评分审计。

期权活动、期权风险和期权方向仅作为展示资料，不进入内在强度。未知期权数据既不
加分，也不占有效权重。

## 规范股票池与排名

强势扫描先对完整 themes 股票池去重，固定每只股票的 primary sector，并单独保存
多主题标签。随后按以下顺序处理：

1. 计算全池原始特征与内在强度；
2. 计算固定全池和固定 primary sector 的百分位；
3. 计算环境与风险档案适配；
4. 最后应用 `sector_id`、价格、流动性、`top` 等视图筛选。

因此 `intrinsic_score`、全局百分位和行业百分位不会随页面视图变化；只有
`selected_view_rank` 可以变化。临时突破候选集合不会被冒充为正式横截面分布，
其横截面状态为 `cross_section_unavailable`。

## 区间强势持续度

`RANGE_PERSISTENCE_MODE=shadow` 时仍会计算特征、假设分数和研究记录，但生产内在
分数、贡献、分类和排序使用完全独立的固定权重。只有通过独立样本外验证且配置明确
启用后，才允许进入假设分支；本次修订没有启用该模式。

## 大盘六态

市场形态每天先生成 `raw_state`，再按交易日顺序重放滞回状态机，得到稳定 `state`。
普通转换先按 `exit_confirm_days` 确认退出原状态，再按 `enter_confirm_days` 确认
进入候选状态，并单独满足最短停留期；`pending_phase`、`exit_pending_days` 和
`enter_pending_days` 公开当前阶段。候选改变或原始状态回归时，相关计数会重置。
达到版本化极端风险条件时可立即进入
`BEAR_TREND`。默认工程参数为：进入确认 2 日、退出确认 2 日、最短停留 3 日、
重放窗口 20 日。这些参数不是最优性声明。

核心趋势、动量或宽度缺失时形态为 `unavailable`。波动率、信用、利率等可选证据
缺失时形态为 `degraded`，仍生成状态但降低置信度。`transition_risk` 是由历史状态、
待确认进度、边界距离和证据分歧合成的转换压力指标，不是概率。

## 基础金融信号

- 相对强弱指数（RSI）按 0、35、50、68、78、100 六个节点连续线性插值，边界
  不跳变，结果限制在 0 至 100。
- 能量潮（OBV）背离使用固定窗口内的有符号成交量占比与价格收益之差，结果乘
  100 后限制在 -100 至 100；窗口不足或数据无效时返回 `null`。
- 平盘日收盘位置、零方差成交量、缺少均线距离及缺少市场环境均保持 `null`，
  不用 0、50 或固定 65 分替代。顶部、底部和回调评分只对有效权重重归一化。
- 当前期权快照只有平值隐含波动率百分比，不冒充历史波动率排名（IV Rank）；
  看涨或看跌合约类型也不代表成交方向，缺少主动买卖方时方向保持未知。Yahoo 与
  MarketData.app 的期权热度在隐含波动率缺失时移除该分量，并按其余有效证据重新
  归一化，不再注入 0.35、50 分或“中性隐波”。

## 特征口径 v3（strength-features-v3，2026-07-27 审计批）

评分公式不变，特征提取口径收紧，旧快照行值不可与 v3 混排对比：

- 52 周高位（`high_52w` / `ath_proximity`）要求至少 240 根真实日线；不足一年
  时缺失并按缺失重新配权，不再用短历史最高价冒充，「接近52周高位」标签随之
  不再对上市不久的标的出现。
- 量价匹配（`vol_price_match`）把缺失成交量的交易日整行剔除后再取中位数，
  不再把「没观测到」当「零成交」计入真空判定。
- 期权热度：无成交、无持仓、无横截面样本（单票池）时分量如实缺失，剩余
  有效权重不足则整个 `option_heat_score` 为 `null` 且 `source_status`
  标 `insufficient_data`；put/call 在 call 侧为零时为 `null`，不再输出 99.0。
- `price_action` 数据不足时 `score` 为 `null`（原为 50.0 占位）。
- 板块聚合（`/api/strength/sectors` 与扫描信封 `sectors`）改按完整
  `theme_ids` 归组，与板块筛选共用同一套成员口径；重叠主题的成员会同时计入
  其所属的每个主题。
- 市场广度（`sectors_above_50dma` / `sectors_above_200dma`）要求至少
  `ceil(11×0.6)=7` 只行业 ETF 有数据，低于门槛读数缺失并记入
  `optional_missing`。
- 行级 `data_sources.prices/technicals` 使用批量下载的真实数据源标签。
- Finnhub 基本面补充不再抬高整行 `data_quality`。
