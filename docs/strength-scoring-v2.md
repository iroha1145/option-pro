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
普通转换需要连续确认和最短停留期；达到版本化极端风险条件时可立即进入
`BEAR_TREND`。默认工程参数为：进入确认 2 日、退出确认 2 日、最短停留 3 日、
重放窗口 20 日。这些参数不是最优性声明。

核心趋势、动量或宽度缺失时形态为 `unavailable`。波动率、信用、利率等可选证据
缺失时形态为 `degraded`，仍生成状态但降低置信度。`transition_risk` 是由历史状态、
待确认进度、边界距离和证据分歧合成的转换压力指标，不是概率。
