# Optix 宏观环境 · 30 个因子

`scoring_version = optix-macro-score-v1`　`formula_version = optix-macro-factor-v1`

本文件的公式、方向、Score Method、单位、新鲜度全部与
`backend/app/services/macro_conditions/registry.py` 一一对应；因子说明由该注册表的
`description_zh` 逐字生成，前端 InfoHint 文案由同一来源生成，
`tests/test_macro_score_hints.py` 强制三方一致。

> 因子范围参考用户提供的公开报告结构，但**公式与评分均由 Optix 独立定义**。
> 不复制任何第三方私有权重或算法。

## v1 声明

- **等权**：同一模块内因子等权；有效模块之间等权。
- **不做相关性去重**。
- **不做机器学习权重**。
- **不复制 MacroDial 私有算法**。
- **Display Only**：不写入任何正式股票评分（选股排序、突破排名都不读宏观分）。

分数是**过去 5 年滚动历史分位**，不是预测概率，不代表市场一定上涨，
不构成买入、卖出、仓位或目标价建议。

## 汇总

| 模块 | 因子数 | 最低有效因子 | 平滑 |
| --- | --- | --- | --- |
| liquidity 流动性 | 5 | 3 | — |
| funding 融资 | 6 | 4 | EMA(5) |
| treasury 国债 | 3 | 2 | — |
| rates 利率 | 3 | 2 | — |
| credit 信用 | 4 | 3 | — |
| risk 风险 | 4 | 3 | — |
| external 外部冲击 | 5 | 3 | — |
| **合计** | **30** | 综合至少 5/7 模块 | |

Score Method 含义见 [评分](scoring.md)：

- `supportive_high_percentile` → `score = percentile(raw)`
- `supportive_low_percentile` → `score = 100 − percentile(raw)`
- `target_distance` → 先算与目标距离，再按 `supportive_low_percentile`
- `direct_score` → 注册公式直接给 0–100 分，不做历史分位

`minimum_history` 统计该因子在**日度 Score Grid** 上的有效值个数。

## 模块 流动性（`liquidity` · LIQUIDITY）

5 个因子，至少 3 个有效才出分。

### 1. 联储净流动性 · `fed_net_liquidity`

- **输入**：`WALCL`, `WTREGEN`, `RRPONTSYD`
- **公式**：`WALCL_b − WTREGEN_b − RRPONTSYD_b`
- **Score Method**：`supportive_high_percentile`
- **方向**：高→高分
- **单位**：十亿美元（`usd_billions`）
- **最小历史**：104
- **新鲜度**：取各输入最严
- **Formula Version**：`optix-macro-factor-v1`
- **说明**：联储总资产减去财政部一般账户与隔夜逆回购余额，单位十亿美元。数值越高表示可用于金融体系的储备越多。

### 2. 银行准备金 · `bank_reserves`

- **输入**：`WRESBAL`
- **公式**：原值直通
- **Score Method**：`supportive_high_percentile`
- **方向**：高→高分
- **单位**：十亿美元（`usd_billions`）
- **最小历史**：104
- **新鲜度**：周度 14 自然日
- **Formula Version**：`optix-macro-factor-v1`
- **说明**：存放在联储的银行准备金余额，单位十亿美元。准备金充裕时融资市场承压概率较低。

### 3. 净流动性 13 周动量 · `net_liquidity_momentum_13w`

- **输入**：`WALCL`, `WTREGEN`, `RRPONTSYD`
- **公式**：`净流动性_t − 净流动性_{t−13周}`（as-of 对齐）
- **Score Method**：`supportive_high_percentile`
- **方向**：高→高分
- **单位**：十亿美元（`usd_billions`）
- **最小历史**：104
- **新鲜度**：取各输入最严
- **Formula Version**：`optix-macro-factor-v1`
- **说明**：当前联储净流动性减去约 13 周前的净流动性（按 as-of 对齐取最近可用观察，不要求日期完全相同），单位十亿美元。

### 4. TGA 偏离一年中位数 · `tga_deviation_52w`

- **输入**：`WTREGEN`
- **公式**：`WTREGEN_b − rolling_median_52周(WTREGEN_b)`
- **Score Method**：`supportive_low_percentile`
- **方向**：低→高分
- **单位**：十亿美元（`usd_billions`）
- **最小历史**：104
- **新鲜度**：周度 14 自然日
- **Formula Version**：`optix-macro-factor-v1`
- **说明**：财政部一般账户余额减去最近 52 个周度观察的滚动中位数，单位十亿美元。负值表示 TGA 低于一年中位数，对应更多现金留在市场。

### 5. 隔夜逆回购缓冲风险 · `on_rrp_buffer_risk`

- **输入**：`RRPONTSYD`
- **公式**：`risk = (1 − clip(余额/100, 0, 1))²`；`score = clip(100×(1−risk), 0, 100)`
- **Score Method**：`direct_score`
- **方向**：低→高分
- **单位**：比值（`ratio`）
- **最小历史**：0
- **新鲜度**：日度 7 自然日
- **Formula Version**：`optix-macro-factor-v1`
- **说明**：以 ON RRP 余额衡量的缓冲耗尽风险：risk = (1 − clip(余额/1000 亿, 0, 1))²，分数 = 100 × (1 − risk)。该因子直接给分，不再做历史分位。


## 模块 融资（`funding` · FUNDING）

6 个因子，至少 4 个有效才出分；日度模块分再经 EMA(5)。

### 6. 抵押品回购摩擦 · `collateral_repo_friction`

- **输入**：`SOFR`, `OBFR`
- **公式**：`signed = A − B`；评分用 `abs(signed)`
- **Score Method**：`supportive_low_percentile`
- **方向**：低→高分
- **单位**：百分点（`percentage_points`）
- **最小历史**：252
- **新鲜度**：日度 7 自然日
- **Formula Version**：`optix-macro-factor-v1`
- **说明**：SOFR 减 OBFR，单位百分点。评分用其绝对值：偏离越小，担保与无担保隔夜市场越一致。界面同时显示带符号原值。

### 7. 利率走廊摩擦（SOFR−IORB） · `corridor_friction_1`

- **输入**：`SOFR`, `IORB`
- **公式**：`signed = A − B`；评分用 `abs(signed)`
- **Score Method**：`supportive_low_percentile`
- **方向**：低→高分
- **单位**：百分点（`percentage_points`）
- **最小历史**：252
- **新鲜度**：日度 7 自然日
- **Formula Version**：`optix-macro-factor-v1`
- **说明**：SOFR 减准备金余额利率，单位百分点。评分用绝对值：越贴近走廊中枢越健康。界面同时显示带符号原值。

### 8. 利率走廊摩擦（SOFR−ON RRP） · `corridor_friction_2`

- **输入**：`SOFR`, `RRPONTSYAWARD`
- **公式**：`signed = A − B`；评分用 `abs(signed)`
- **Score Method**：`supportive_low_percentile`
- **方向**：低→高分
- **单位**：百分点（`percentage_points`）
- **最小历史**：252
- **新鲜度**：日度 7 自然日
- **Formula Version**：`optix-macro-factor-v1`
- **说明**：SOFR 减隔夜逆回购中标利率，单位百分点。评分用绝对值，衡量对走廊下沿的偏离。界面同时显示带符号原值。

### 9. EFFR−IORB 价差 · `effr_iorb_spread`

- **输入**：`EFFR`, `IORB`
- **公式**：`signed = A − B`；评分用 `abs(signed)`
- **Score Method**：`supportive_low_percentile`
- **方向**：低→高分
- **单位**：百分点（`percentage_points`）
- **最小历史**：252
- **新鲜度**：日度 7 自然日
- **Formula Version**：`optix-macro-factor-v1`
- **说明**：联邦基金有效利率减准备金余额利率，单位百分点。评分用绝对值，衡量政策利率传导是否顺畅。界面同时显示带符号原值。

### 10. 商业票据−国库券价差 · `cp_tbill_spread`

- **输入**：`DCPF3M`, `DTB3`
- **公式**：`signed = DCPF3M − DTB3`；评分用 `max(signed, 0)`
- **Score Method**：`supportive_low_percentile`
- **方向**：低→高分
- **单位**：百分点（`percentage_points`）
- **最小历史**：252
- **新鲜度**：日度 7 自然日
- **Formula Version**：`optix-macro-factor-v1`
- **说明**：3 个月金融商业票据利率减 3 个月国库券贴现率，单位百分点。评分只取正值部分：正价差扩大代表短期信用融资变贵。界面同时显示带符号原值。

### 11. 融资分化度（21 日） · `funding_fragmentation_21d`

- **输入**：`SOFR`, `OBFR`, `IORB`, `RRPONTSYAWARD`, `EFFR`, `DCPF3M`, `DTB3`
- **公式**：`rolling_mean_21(population_std(5 个带符号价差))`，当日至少 4 个价差可用
- **Score Method**：`supportive_low_percentile`
- **方向**：低→高分
- **单位**：百分点（`percentage_points`）
- **最小历史**：252
- **新鲜度**：日度 7 自然日
- **Formula Version**：`optix-macro-factor-v1`
- **说明**：每日先算五个带符号融资价差的总体标准差（至少 4 个价差可用才计算），再取最近 21 个有效值的均值，单位百分点。数值越低表示各融资市场越同步。


## 模块 国债（`treasury` · TREASURY）

3 个因子，至少 2 个有效才出分。

### 12. 30 年−10 年期限斜率 · `term_premium_30y_10y`

- **输入**：`DGS30`, `DGS10`
- **公式**：`A − B`
- **Score Method**：`supportive_high_percentile`
- **方向**：高→高分
- **单位**：百分点（`percentage_points`）
- **最小历史**：252
- **新鲜度**：日度 7 自然日
- **Formula Version**：`optix-macro-factor-v1`
- **说明**：30 年期减 10 年期国债收益率，单位百分点。这是 Optix 对曲线长端斜率的代理，不是学术期限溢价模型。

### 13. 10 年期利率波动（21 日） · `rate_volatility_10y_21d`

- **输入**：`DGS10`
- **公式**：`population_std(daily_change(DGS10), 最近 21 个有效值)`，不年化
- **Score Method**：`supportive_low_percentile`
- **方向**：低→高分
- **单位**：百分点（`percentage_points`）
- **最小历史**：252
- **新鲜度**：日度 7 自然日
- **Formula Version**：`optix-macro-factor-v1`
- **说明**：最近 21 个有效日度变化的总体标准差，单位百分点，不做年化。数值越低表示长端定价越稳定。

### 14. 曲线曲率绝对值 · `curve_curvature_abs`

- **输入**：`DGS10`, `DGS2`, `DGS30`
- **公式**：`abs(2×DGS10 − DGS2 − DGS30)`
- **Score Method**：`supportive_low_percentile`
- **方向**：低→高分
- **单位**：百分点（`percentage_points`）
- **最小历史**：252
- **新鲜度**：日度 7 自然日
- **Formula Version**：`optix-macro-factor-v1`
- **说明**：abs(2×10 年 − 2 年 − 30 年)，单位百分点。这是 Optix 自定义的 2s10s30s 蝶式曲率代理，数值越小代表曲线形态越常规。


## 模块 利率（`rates` · RATES）

3 个因子，至少 2 个有效才出分。

### 15. 实际利率水平 · `real_rate_level`

- **输入**：`DFII5`, `DFII10`
- **公式**：`0.6×DFII5 + 0.4×DFII10`
- **Score Method**：`supportive_low_percentile`
- **方向**：低→高分
- **单位**：百分点（`percentage_points`）
- **最小历史**：252
- **新鲜度**：日度 7 自然日
- **Formula Version**：`optix-macro-factor-v1`
- **说明**：0.6×5 年期实际收益率 + 0.4×10 年期实际收益率，单位百分点。实际利率越低，对风险资产估值的压制越小。

### 16. 实际利率曲线（10 年−5 年） · `real_curve_10y_5y`

- **输入**：`DFII10`, `DFII5`
- **公式**：`A − B`
- **Score Method**：`supportive_high_percentile`
- **方向**：高→高分
- **单位**：百分点（`percentage_points`）
- **最小历史**：252
- **新鲜度**：日度 7 自然日
- **Formula Version**：`optix-macro-factor-v1`
- **说明**：10 年期减 5 年期实际收益率，单位百分点。正斜率通常对应对长期增长的定价更高。

### 17. 10 年期通胀预期 · `breakeven_10y`

- **输入**：`T10YIE`
- **公式**：显示 `T10YIE`；评分用 `abs(T10YIE − 2.0)`
- **Score Method**：`target_distance`
- **方向**：近目标→高分
- **单位**：百分点（`percentage_points`）
- **最小历史**：252
- **新鲜度**：日度 7 自然日
- **Formula Version**：`optix-macro-factor-v1`
- **说明**：显示 10 年期 Breakeven 原值，评分用其与 2% 的绝对偏离：越贴近 2% 得分越高。界面同时显示原值与偏离。


## 模块 信用（`credit` · CREDIT）

4 个因子，至少 3 个有效才出分。

### 18. 全国金融条件指数 · `nfci`

- **输入**：`NFCI`
- **公式**：原值直通
- **Score Method**：`supportive_low_percentile`
- **方向**：低→高分
- **单位**：指数点（`index_points`）
- **最小历史**：104
- **新鲜度**：周度 14 自然日
- **Formula Version**：`optix-macro-factor-v1`
- **说明**：芝加哥联储 NFCI 原值（指数点）。零为长期均值，正值代表金融条件收紧，故数值越低得分越高。

### 19. 高收益债相对强度 · `hy_credit`

- **输入**：`HYG`, `IEI`
- **公式**：`100 × [ln(A_t/A_{t−63}) − ln(B_t/B_{t−63})]`，至少 64 个共同交易日
- **Score Method**：`supportive_high_percentile`
- **方向**：高→高分
- **单位**：%（`percent`）
- **最小历史**：252
- **新鲜度**：ETF 5 交易日
- **Formula Version**：`optix-macro-factor-v1`
- **说明**：HYG 相对 IEI 的 63 交易日对数收益差×100，单位百分点。至少需要 64 个共同有效交易日。数值越高代表高收益信用风险偏好越强。

### 20. 投资级债相对强度 · `ig_credit`

- **输入**：`LQD`, `IEF`
- **公式**：`100 × [ln(A_t/A_{t−63}) − ln(B_t/B_{t−63})]`，至少 64 个共同交易日
- **Score Method**：`supportive_high_percentile`
- **方向**：高→高分
- **单位**：%（`percent`）
- **最小历史**：252
- **新鲜度**：ETF 5 交易日
- **Formula Version**：`optix-macro-factor-v1`
- **说明**：LQD 相对 IEF 的 63 交易日对数收益差×100，单位百分点。至少需要 64 个共同有效交易日。

### 21. 区域银行相对大盘 · `regional_banks_vs_spy`

- **输入**：`KRE`, `SPY`
- **公式**：`100 × [ln(A_t/A_{t−63}) − ln(B_t/B_{t−63})]`，至少 64 个共同交易日
- **Score Method**：`supportive_high_percentile`
- **方向**：高→高分
- **单位**：%（`percent`）
- **最小历史**：252
- **新鲜度**：ETF 5 交易日
- **Formula Version**：`optix-macro-factor-v1`
- **说明**：KRE 相对 SPY 的 63 交易日对数收益差×100，单位百分点。区域银行走弱常与信用供给收缩同步。


## 模块 风险（`risk` · RISK）

4 个因子，至少 3 个有效才出分。

### 22. VIX 波动率 · `vix`

- **输入**：`VIXCLS`
- **公式**：原值直通
- **Score Method**：`supportive_low_percentile`
- **方向**：低→高分
- **单位**：指数点（`index_points`）
- **最小历史**：252
- **新鲜度**：日度 7 自然日
- **Formula Version**：`optix-macro-factor-v1`
- **说明**：VIX 收盘值（指数点）。数值越低表示期权市场定价的短期波动越低。

### 23. VIX 期限结构 · `vix_term_structure`

- **输入**：`VIXCLS`, `VXVCLS`
- **公式**：`VIXCLS / VXVCLS`（VXVCLS ≤ 0 视为缺失）
- **Score Method**：`supportive_low_percentile`
- **方向**：低→高分
- **单位**：比值（`ratio`）
- **最小历史**：252
- **新鲜度**：日度 7 自然日
- **Formula Version**：`optix-macro-factor-v1`
- **说明**：VIX 除以 3 个月 VIX（VXV）。VXV 小于或等于零时视为缺失。比值越低（曲线越正向）表示近端压力越小。

### 24. 风险资产相对避险资产 · `risk_vs_safe`

- **输入**：`SPY`, `TLT`
- **公式**：`100 × [ln(A_t/A_{t−63}) − ln(B_t/B_{t−63})]`，至少 64 个共同交易日
- **Score Method**：`supportive_high_percentile`
- **方向**：高→高分
- **单位**：%（`percent`）
- **最小历史**：252
- **新鲜度**：ETF 5 交易日
- **Formula Version**：`optix-macro-factor-v1`
- **说明**：SPY 相对 TLT 的 63 交易日对数收益差×100，单位百分点。数值越高代表资金更偏好风险资产。

### 25. 高贝塔偏好 · `high_beta_preference`

- **输入**：`IWM`, `SPY`
- **公式**：`100 × [ln(A_t/A_{t−63}) − ln(B_t/B_{t−63})]`，至少 64 个共同交易日
- **Score Method**：`supportive_high_percentile`
- **方向**：高→高分
- **单位**：%（`percent`）
- **最小历史**：252
- **新鲜度**：ETF 5 交易日
- **Formula Version**：`optix-macro-factor-v1`
- **说明**：IWM 相对 SPY 的 63 交易日对数收益差×100，单位百分点。小盘跑赢通常对应更强的风险承担意愿。


## 模块 外部冲击（`external` · EXTERNAL）

5 个因子，至少 3 个有效才出分。

### 26. 美元广义指数 · `broad_dollar_index`

- **输入**：`DTWEXBGS`
- **公式**：原值直通
- **Score Method**：`supportive_low_percentile`
- **方向**：低→高分
- **单位**：指数点（`index_points`）
- **最小历史**：252
- **新鲜度**：日度 7 自然日
- **Formula Version**：`optix-macro-factor-v1`
- **说明**：美联储广义名义美元指数（指数点）。美元走强通常收紧全球美元流动性，故数值越低得分越高。

### 27. 美元已实现波动（63 日） · `fx_realized_volatility_63d`

- **输入**：`DTWEXBGS`
- **公式**：`population_std(最近 63 个有效对数收益) × √252`
- **Score Method**：`supportive_low_percentile`
- **方向**：低→高分
- **单位**：比值（`ratio`）
- **最小历史**：252
- **新鲜度**：日度 7 自然日
- **Formula Version**：`optix-macro-factor-v1`
- **说明**：美元指数最近 63 个有效日对数收益率的总体标准差×√252，无量纲比值。数值越低表示汇率环境越平稳。

### 28. WTI 原油价格 · `wti_oil`

- **输入**：`DCOILWTICO`
- **公式**：原值直通
- **Score Method**：`supportive_low_percentile`
- **方向**：低→高分
- **单位**：美元/桶（`usd_per_barrel`）
- **最小历史**：252
- **新鲜度**：日度 7 自然日
- **Formula Version**：`optix-macro-factor-v1`
- **说明**：WTI 现货价（美元/桶）。该分数衡量能源成本压力，不代表油价低就一定利好经济增长。

### 29. 原油波动率偏离 · `oil_volatility_deviation`

- **输入**：`OVXCLS`
- **公式**：`max(OVXCLS − rolling_median_252(OVXCLS), 0)`
- **Score Method**：`supportive_low_percentile`
- **方向**：低→高分
- **单位**：指数点（`index_points`）
- **最小历史**：252
- **新鲜度**：日度 7 自然日
- **Formula Version**：`optix-macro-factor-v1`
- **说明**：max(OVX − 最近 252 个有效观察的滚动中位数, 0)，单位指数点。只在原油波动率高于自身一年中位数时计入压力。

### 30. 天然气价格 · `natural_gas`

- **输入**：`DHHNGSP`
- **公式**：原值直通
- **Score Method**：`supportive_low_percentile`
- **方向**：低→高分
- **单位**：美元/百万英热（`usd_per_mmbtu`）
- **最小历史**：252
- **新鲜度**：日度 7 自然日
- **Formula Version**：`optix-macro-factor-v1`
- **说明**：亨利枢纽天然气现货价（美元/百万英热）。该分数衡量能源成本压力，不是天然气产业景气度评分。

