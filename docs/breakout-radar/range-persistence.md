# 区间强势持续度

正式名称为区间强势持续度（Range Persistence）。代码、接口和文档不得把它
解释成机构行为。

## 改良连续特征

R_t = 100 × (Close_t - LLV_N) / (HHV_N - LLV_N)

fast_t = RMA_3(RMA_3(R_t))

slow_t = RMA_N(R_t)

range_persistence_t = (fast_t + slow_t) / 2

默认 N=35，fast_length=3，输出范围 0 至 100。另输出 range_position、
fast、slow、五日斜率、十日达标比例、自身历史百分位，以及可用时的固定
规范股票池全局和行业百分位。

五日斜率使用最近五个完整日线值对交易日序号的普通最小二乘斜率。
十日比例是最近十个完整日中 persistence >= 60 的占比，乘 100。

平坦窗口 HHV=LLV 时返回 status=uninformative，核心值为 null，quality=0；
不能伪造 0 或 50。默认至少需要 max(5N, N+5, N+10)=175 根有效完整日线。

## Pine 精确旧公式

原始 Pine 在 rolling 尚未满 N 或区间为零时，b1 和 b3 都走零输入。令：

- q_t：当前 rolling 区间有效且非零时为 1，否则为 0。
- r_t：有效时的区间位置，无效时为 0。
- S_n：以首个输入初始化的递归平滑。

逐步精确式：

a_t = q_t × (100 - M - r_t)

fast_t = S_3(S_3(r))_t

legacy_control_t = 2.5 × max(fast_t - S_N(a)_t - N1, 0)

广义等价式：

legacy_control_t =
2.5 × max(fast_t + S_N(r)_t - (100-M) × S_N(q)_t - N1, 0)

附件中的稳态化简：

2.5 × max(fast_t + slow_t + M - 100 - N1, 0)

只有 S_N(q)=1 时才严格成立。预热期、平坦区间及其递归恢复期不满足该条件。
因此“M 与 N1 同增不变”也只属于稳态性质。实现以逐步 Pine 复现和广义式为
exact 权威，并以稳态夹具单独验证附件化简式。

默认 M=35、N1=3 时，legacy_control 理论上可到 330；它不是百分比，
大于 100 合法。legacy_control 只用于对照、迁移、测试和研究。

## 清洗和时点

- High、Low、Close 先转数值，NaN 和无穷值按缺失处理。
- 先按 cutoff 裁剪，再排序、去重、清洗、rolling、递归和尾部取值。
- 日线盘中只使用上一完整正常交易日。
- 向尾部追加未来数据后，以原 cutoff 重算必须逐字段不变。
- 非法 OHLC、样本数、状态、质量和 calculation_cutoff_at 都进入结果。
- 所有公共浮点必须有限。

## 模式和权重

- disabled：不计算。
- shadow：默认；计算正式特征和 hypothetical score，但不改 production score、
  classification 或 rank。
- enabled：只有验证文档的十项门槛全部满足后才允许配置启用。配置时必须让
  `RANGE_PERSISTENCE_VALIDATION_VERSION` 与 `RANGE_PERSISTENCE_VERSION`
  完全一致；版本升级后必须重新验证，旧确认不能沿用。

它只能进入 trend factor family，族内有效权重不超过 0.15，对最终强势分有效
贡献不超过 0.04。加入时从 RSI、MACD、均线距离或高点接近度等同类权重中
让出，不在总分外叠加。

突破模块中只作为趋势背景、衰减提醒、追高交互和影子确认调整。确认调整绝对值
最多 4 分；若 strength included_features 已含它，不得再次正向计票。

强势选股接口也返回每只股票的 `range_persistence`、
`range_persistence_shadow`、模式和受上限约束的分差。`shadow` 模式下页面可查看
数值与假设分，但生产分、分类和排序保持不变；只有通过版本绑定闸门的
`enabled` 模式才把分差用于排序。

突破交互拆成两条独立审计记录：斜率与近期保持比例最多调整确认分 ±4；只有
自身历史百分位不低于 90 且突破距离不低于 2 倍平均真实波幅时，才提高追高
风险。价格接近区间高位、快线低于慢线且斜率为负时增加衰减提醒。任何一种
区间持续性状态都不能自行创建突破触发。

## 研究门槛

采用滚动步进、purge 和 embargo。比较 baseline、直接叠加、同类权重替换三组，
重点判断替换是否优于叠加。期限覆盖 5、20、63 日，并检查 Rank IC、ICIR、
Top-K 超额收益、分位单调性、换手、回撤、命中率、保持率和假突破率。

只有样本外增量为正、多数窗口方向一致、跨市场状态、成本后仍有效、换手可控、
参数扰动稳健、消融后确实变差且最终测试集未参与选择，才可启用 enabled。

当前只读标签、滚动窗口、清除期、隔离期和成对消融的输入格式及审计字段见
[突破雷达研究验证](research-validation.md)。外部行情覆盖不足时保持
`unavailable`，不会据此解除影子模式。
