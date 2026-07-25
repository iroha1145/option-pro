# Optix 宏观环境 · 评分

`scoring_version = optix-macro-score-v1`

分数是**过去 5 年滚动历史分位**。高分表示当前金融环境相对自身历史更支持风险资产，
**不代表市场一定上涨**，不是预测概率，也不构成买入、卖出、仓位或目标价建议。

---

## 1. 统一日度 Score Grid

- 快照日期用**美国交易日**（NYSE 日历，含假日与提前收盘）。
- 每个因子在网格上的每一天都会取一次值，取值只做 **backward as-of join**：
  只看 `observation_date <= snapshot_date`，对未来观察日期**零容忍**。
- 不做线性插值，不向未来回填。
- 值只在其**注册的陈旧阈值内**被携带；超过阈值后停止携带，因子转为 `stale`。
  FRED 系列按自然日计（日度 7 天 / 周度 14 天），ETF 按交易日计（5 个交易日）。
- 混合来源因子的 `data_through` = **所有有效输入中最早**的观察日期。
- 绝不用 Task 的运行时间冒充数据截止时间。

---

## 2. 经验分位（确定性中位秩）

```
percentile = 100 × ( count(history < x) + 0.5 × count(history == x) ) / count(history)
0 ≤ score ≤ 100
```

- 窗口 = `(snapshot_date − 5 年, snapshot_date]`，**不含** snapshot_date 之后任何数据。
- 当前观察值计入自己的窗口，且**只计一次**。
- 排除 null，排除 NaN/Infinity。
- 中位秩让并列稳定：相同的值必然得到相同的分位。
- 相同输入重复运行结果完全一致（实现为一次遍历 + 有序窗口 + 过期 FIFO）。
- 分数发布保留一位小数；内部计算不提前四舍五入。显示层四舍五入采用远离零的
  half-up，避免银行家舍入让同一份数据在 `x.5` 边界上两次读出不同结果。

### 四种 score 方法

| 方法 | 计算 |
| --- | --- |
| `supportive_high_percentile` | `score = percentile(raw)` |
| `supportive_low_percentile` | `score = 100 − percentile(raw)` |
| `target_distance` | 先算与目标的距离，再按 `supportive_low_percentile` |
| `direct_score` | 直接使用注册公式产生的 0–100 分，**不做**历史分位 |

只有 `on_rrp_buffer_risk` 使用 `direct_score`：它的注册公式本身就输出有界 0–100 分，
再套一层分位等于"给分数打分"。

### 最小历史

`minimum_history` 统计的是**该因子在日度网格上的有效值个数**，不是原始发布次数：

- 日度因子：252
- 周度来源的因子：104
- 63 日 ETF 相对收益因子：252 个有效结果
- 252 日滚动因子（OVX 偏离）：252 个有效原始观察

不足时 `score = null`，**绝不用 50 代替**。

> 方法学说明：周度来源的因子被携带到日度网格上，因此它 5 年窗口里每个周度发布约
> 出现 5 次。每个发布重复相同次数不改变经验分布的形状，但这里如实写明，避免被
> 误读成"周度频率的分位"。完成 8 年回填后每个因子都拥有完整 5 年窗口，
> 上面的门槛只在历史最开端起作用。

---

## 3. 模块分

同一模块内**等权**（v1 不做相关性去重，不做机器学习权重）。

| 模块 | 因子数 | 最低有效因子 | 平滑 |
| --- | --- | --- | --- |
| liquidity 流动性 | 5 | 3 | — |
| funding 融资 | 6 | 4 | EMA(5) |
| treasury 国债 | 3 | 2 | — |
| rates 利率 | 3 | 2 | — |
| credit 信用 | 4 | 3 | — |
| risk 风险 | 4 | 3 | — |
| external 外部冲击 | 5 | 3 | — |

```
module_score = 有效因子 score 的等权均值
module_confidence = 有效因子数 / 该模块因子总数
```

低于最低有效因子数时 `module_score = null`，状态 `insufficient_factors`。
**不补 50，不用上一次的分数顶替。**

### Funding 的 EMA(5)

先算日度模块原始分，再应用标准 EMA：

```
alpha = 2 / (5 + 1)
ema[0] = raw[0]
ema[i] = alpha × raw[i] + (1 − alpha) × ema[i−1]
```

没有原始分的那一天保持 `null`，平滑状态不推进——空缺不会被平滑填成一个凭空的值。
只有 Funding 做平滑，因为融资价差的日间噪声最大；其余模块不平滑。

---

## 4. 综合分

```
composite_score = 有效模块 score 的等权均值
要求至少 5 / 7 个模块有效
composite_confidence = (有效模块数 / 7) × 有效模块内部因子覆盖率的均值
```

有效模块少于 5 个时**不输出正式综合分**：不给失效模块补 50，不用上一分数冒充当前分数。

### Regime 标签

| 综合分 | 标签 |
| --- | --- |
| `< 30` | 明显收紧 |
| `30 ≤ score < 45` | 偏紧 |
| `45 ≤ score < 55` | 中性 |
| `55 ≤ score < 70` | 偏松 |
| `≥ 70` | 明显宽松 |

这些标签只描述**相对自身历史**的环境松紧，不是未来收益判断。

---

## 5. 变化值

7 日比较：取 `snapshot_date ≤ current_date − 7 自然日` 的**最近一份有效**快照。

返回 `raw_change_7d`、`score_change_7d`、模块与综合的 `score_change_7d`。
找不到可比快照时返回 **`null`**，绝不用 `0` 代替缺失的比较。

UI 上分数变化以**分数点**呈现（"−3.5 分"），不是百分比——0–100 的分位点差值写成
"−3.5%" 会被读成百分比，属于事实错误。原值变化则用该因子自己的单位
（"+0.012 个百分点"）。

---

## 6. 状态语义

| 状态 | 含义 |
| --- | --- |
| `disabled` | `macro.enabled = false` 或 `FRED_API_KEY` 未配置 |
| `active` | 最新正式快照有效且数据新鲜 |
| `degraded` | 部分 Series 失败，但有效模块门槛仍满足 |
| `stale` | 存在旧快照，但最新刷新失败或数据超出整体新鲜度（快照 > 7 天 / 数据 > 14 天） |
| `unavailable` | 没有任何可读的正式快照 |
| `insufficient_history` | 数据存在，但 5 年分位历史不足 |

错误码：`fred_api_key_missing`、`fred_api_key_invalid`、`fred_rate_limited`、`fred_unavailable`、
`fred_schema_mismatch`、`fred_units_mismatch`、`fred_response_too_large`、
`etf_history_unavailable`、`macro_insufficient_history`、`macro_insufficient_modules`、
`macro_refresh_in_progress`、`macro_refresh_cooldown`、`macro_store_unavailable`、
`macro_snapshot_unavailable`。

FRED 失败、ETF 失败、SQLite 失败**不共用**同一个错误码。

---

## 7. v1 明确不做

- 不做相关性去重
- 不做机器学习权重
- 不复制任何第三方私有算法
- 不把宏观分写进正式选股或突破排名
- 不让模型计算或修正因子分数

---

## 宏观联动（Phase 1，影子字段）

### 为什么不能把综合分加给所有股票

给每只股票都加同一个 53 分，不会改变任何两只股票的相对顺序，只会把整张表平移。
选股需要的是「**当前宏观环境与这只股票的行业、风格和风险暴露是否匹配**」，
也就是同一个宏观读数必须对软件、银行、能源、航空产生**不同方向**的影响。

### 暴露注册表（`exposures.py`）

按板块给出每个**因子**的 β ∈ [-1, 1]：某个因子的「支持性读数」对这类公司
是顺风还是逆风、有多强。三条约束：

1. **确定性** —— 不让模型推断公司的宏观暴露。β 是版本控制里可以逐条复核的数字。
2. **落到因子，绝不落到模块** —— 外部冲击模块是最清楚的例子：`wti_oil` 把
   **低油价**记为对广义风险资产的支持，而这对能源生产商恰好相反。用模块总分
   会让生产商和航空公司拿到同一个符号。
3. **版本化** —— 改一个 β 就改变了宏观适配分的含义，因此同时改
   `EXPOSURE_VERSION`，分数带着版本一起输出。

没有条目的板块（例如 `etfs`）**不给分**，而不是给 50 —— ETF 是它持有的东西，
没有可辩护的单一暴露。

### 计算（`linkage.py`）

```
z_f        = (score_f - 50) / 50                       ∈ [-1, 1]
MacroFit_i = 50 + 50 · Σ(w_f q_f β_if z_f) / Σ(w_f q_f |β_if|)
MacroFit*  = 50 + (MacroFit_i - 50) · confidence_i
```

- `w_f`：该因子在综合分里的份额。模块等权、模块内因子等权，所以
  `w_f = 1/模块数 × 1/该模块的因子数` —— 按因子数平铺会让融资模块（6 个因子）
  仅凭数量压过国债模块（3 个），而综合分并不是那样算的。
- `q_f`：因子自身的置信度。
- `confidence_i`：该板块**声明的**暴露里实际观测到的比例 —— 不是「看到的那些
  有多可信」。只看到八个因子里的一个时，后者会算出 100%。
- 覆盖率低于 `MIN_EXPOSURE_COVERAGE` 时返回 **null**，不返回 50：
  50 是对环境的判断，null 才是「说不了」。

### 影子字段，不改任何生产分数

扫描结果每行附加：`macro_fit_shadow` / `macro_fit_confidence` /
`macro_fit_version` / `macro_tailwind` / `macro_supporting_factors` /
`macro_opposing_factors` / `ranking_score_macro_shadow` / `macro_technical_gap`。

`ranking_score_macro_shadow = clip(ranking_score + clip((MacroFit*-50)/50×3, -3, +3), 0, 100)`

内在强度、突破质量、市场适配、风格适配、`ranking_score` 一律不动，
默认排序仍用 `ranking_score`。附加发生在 `_sort_scored` 之后，
避免它有机会渗进排序键。

### 结构性宏观 vs 市场隐含确认

`structural_macro_score` 只取**流动性 / 融资 / 国债 / 利率**四个模块。
信用与风险模块用的 HYG、LQD、KRE、VIX、SPY/TLT、IWM/SPY 与技术市场形态
读的是同一批价格，再算一次等于把一个信号按两个名字计两次权。

`macro_technical_gap = 技术市场适配 - 结构性宏观`：
差值大为正说明价格跑在环境前面，大为负说明宏观先行改善而价格没跟上。
任一侧缺失时为 null，而不是 0。
