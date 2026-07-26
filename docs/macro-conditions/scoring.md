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

### 一次快照读，三个消费方共享（`linkage_reader.py`）

选股行、板块雷达、突破列表要的是同样三件东西：已发布的宏观快照、按板块的一份
适配分、结构性综合分。各自开一次仓库不只是重复读同一个文件，还可能**跨过一次
发布** —— 同一个页面上的三块面板于是会描述两个不同的宏观环境。因此每个请求
构造一个 `MacroFitReader`，`fit_for(sector_id)` 按板块记忆化。

只读到底：这里任何路径都不得创建、迁移或刷新宏观库。一次选股扫描或一次突破页
加载**永远不能触发 FRED 抓取**。所有失败都降级成「没有读数」而不是抛出 ——
这些字段是别人页面上的注解，宏观快照读不出来不构成让那个页面失败的理由。

`load_macro_fit_reader()` 按宏观库**及其 WAL** 的大小 + mtime 记忆化，另有 30 秒的
兜底上限。快照一天只发布两次，而每打开一次个股抽屉都要重跑三组查询。两条边界：

- **只缓存真实快照。** 缓存「不可用」会让 worker 发布完之后 30 秒内继续回答
  「没有宏观读数」；库文件连 stat 都失败时同样不缓存 —— 那正是最该重查的状态
  （worker 可能正在创建它）。
- 返回的 reader 在调用方之间**共享，视为只读**。`fit_for` 的记忆化是 `factors`
  的纯导出，多线程竞争最多多算一次，没有别的后果。

个股概览端点（`async def`）经 `asyncio.to_thread` 调用它。SQLite 读在磁盘空闲时
只要几毫秒，但直接跑在事件循环线程上时，磁盘繁忙、WAL 争用或卷卡住会阻塞
**所有无关请求**，不只是这一个。

### 板块宏观适配

`/api/strength/sectors` 每行附加 `macro_sector_fit` / `macro_sector_tailwind` /
`macro_sector_fit_confidence` / `macro_sector_supporting_factors` /
`macro_sector_opposing_factors`。

与 `avg_strength` **并列，绝不混入它**：两者不一致的板块（技术强而宏观逆风、
宏观先改善而价格没跟上）正是这张表值得看的地方，合成一个数字正好把它们藏掉。
排序不变。

### 突破提醒优先级影子

`/api/breakouts/*` 每个事件附加 `macro_fit_score` /
`macro_priority_adjustment_shadow`（±4 上限）/ `alert_priority_macro_shadow` /
`macro_supporting_factors` / `macro_opposing_factors` / `macro_shadow_status`。

**读取时计算，不落库。** 带 `macro_snapshot_id` 的持久化属于 Phase 2。

`alert_priority_score` 本身、`base_quality_score`、`breakout_confirmation_score`、
`liquidity_quality_score`、`breakout_quality_score`、`chase_risk_score` 以及事件
生命周期一律不动：**宏观逆风不会删除、降级或推迟一个真实发生的突破事件。**

`macro_shadow_status` 把三件都会让分数为 null 的事分开：

| 状态 | 含义 |
| --- | --- |
| `ok` | 有分数 |
| `macro_snapshot_unavailable` | 还没有已发布的宏观快照 |
| `sector_unclassified` | 这只票不在主题表里，没有暴露画像可用 |
| `exposure_coverage_low` | 该板块声明的暴露观测不足，不给分 |

少了这个字段，三件不同的事在 API 上长得一模一样。

个股的板块解析走 `app.services.sectors.primary_sector_id()`，与强度池自己的
`primary_sector_id` 同一约定（有测试断言两者一致，而不是假设一致）。
**已知限制**：主题表里 213 只票有 17 只属于两个主题（例如 NVDA 同时在半导体与
AI/云），「首次出现者胜」是确定的但在**语义上是任意的**，这 17 只票拿到的是其中
一个主题的暴露画像。改成跨主题合成 β 会改变 `macro-linkage-v1` 的含义，按本文
自己的规矩要一并升版本，因此留给后续。

### 界面口径

- 选股表的宏观适配是**可选列，默认关**，并配顺风/中性/逆风筛选；默认排序仍是
  确定性排序，宏观不出现在任何排序键里。没有读数的行被筛选**排除**而不是当中性
  留下，并明确说出被排除多少只。
- 二维象限（技术 × 结构性宏观）以 **50 分**为界，而顺风/逆风的分界线是
  **65 / 35**。两套线不同，所以象限措辞用「偏强 / 偏弱」而不复用「顺风 / 逆风」：
  否则一个 53 分的读数会在徽标上显示「中性」、正下方象限说「宏观顺风」。
- 驱动因素由后端下发 `{factor_id, label}`，中文名在 registry 解析。前端刻意不留
  第二份 id → 中文名的映射表：那样因子改名之后界面会继续显示旧名字，而且什么都
  不会失败。
- 移动端卡片流与桌面表格共用同一个 `showMacro`。漏传它的话筛选照样生效、列表照样
  被过滤，但卡片上一个读数都不显示 —— 用户看不出这些票为什么留下来了。

### 个股抽屉：不把两期快照拼成一份（`mergeMacroFields`）

抽屉同时拿两份数据：**实时概览**（`GET /api/stocks/{t}`，对每只在主题表里的票都
算得出分）和**落库的扫描行**（`GET /api/strength/stocks/{t}`，只回答公开快照 top
切片里的代码，其余 404）。合并规则收在 `components/detail/api.ts` 的
`mergeMacroFields` 里，两条：

1. **概览一旦回答了，它就是权威**，不再逐字段回填扫描行。逐字段 `??` 会犯两个方向
   相反的错：概览说「本次没有读数」（`macro_snapshot_unavailable` /
   `exposure_coverage_low`）时旧分被复活；概览说「本期没有负面因子」时空数组长度
   为 0，旧的负面因子被补回来。两者都是拿一份已经不成立的解释冒充当前读数 ——
   后端专门为此返回 null 而不是 50，前端不能在这一步把它抵消掉。概览必然带
   `macro_shadow_status`（四种取值都写），所以它非空就等于「答过了」；只有对接不带
   该字段的旧后端时才整组回退扫描行。
2. **`macro_technical_gap` 只在同期时才显示。** 差值只有扫描行算得出（它要该股的
   `market_fit_score`），而分数来自实时概览，扫描可能比最新一期宏观旧几个小时。
   两个数字各自都对，凑在一起却不是同一个测量时点。

为此 `macro_snapshot_date` 必须两边都有：概览在有快照可指名时随读数一起下发
（`ok` 与 `exposure_coverage_low` 两条路径都带），`/api/strength/stocks/{t}` 的信封
带上扫描当时的 `macro_linkage`（owner 与匿名两个分支都带）。
