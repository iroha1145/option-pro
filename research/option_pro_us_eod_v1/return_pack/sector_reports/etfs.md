# 宽基 ETF（入口保留，底层拆资产） (`etfs`)

状态：`DATA_INSUFFICIENT` / `INSUFFICIENT_PIT_HISTORY`。

本轮没有可核验的十年 PIT 日线、退市样本或历史行业标签，因此 **没有收益数字**，也 **没有赢家**。

- 资产轨道：`etf`
- 基准提示：`ASSET_CLASS_MATCHED`
- 事件规则：`fund`（无 PIT 事件日历时该变体不可测）
- 子组（仅诊断，未调参）：宽基股票 ETF, 行业 ETF, 主题 ETF, 黄金 ETF, 长债 ETF

## 36 个主评估

|算法|风险档|期限|状态|暂留/淘汰|
|---|---|---|---|---|
|A_trend_quality|conservative|short|DATA_INSUFFICIENT|无赢家（允许空结果）|
|A_trend_quality|conservative|mid|DATA_INSUFFICIENT|无赢家（允许空结果）|
|A_trend_quality|conservative|long|DATA_INSUFFICIENT|无赢家（允许空结果）|
|A_trend_quality|balanced|short|DATA_INSUFFICIENT|无赢家（允许空结果）|
|A_trend_quality|balanced|mid|DATA_INSUFFICIENT|无赢家（允许空结果）|
|A_trend_quality|balanced|long|DATA_INSUFFICIENT|无赢家（允许空结果）|
|A_trend_quality|aggressive|short|DATA_INSUFFICIENT|无赢家（允许空结果）|
|A_trend_quality|aggressive|mid|DATA_INSUFFICIENT|无赢家（允许空结果）|
|A_trend_quality|aggressive|long|DATA_INSUFFICIENT|无赢家（允许空结果）|
|B_confirmed_base_breakout|conservative|short|DATA_INSUFFICIENT|无赢家（允许空结果）|
|B_confirmed_base_breakout|conservative|mid|DATA_INSUFFICIENT|无赢家（允许空结果）|
|B_confirmed_base_breakout|conservative|long|DATA_INSUFFICIENT|无赢家（允许空结果）|
|B_confirmed_base_breakout|balanced|short|DATA_INSUFFICIENT|无赢家（允许空结果）|
|B_confirmed_base_breakout|balanced|mid|DATA_INSUFFICIENT|无赢家（允许空结果）|
|B_confirmed_base_breakout|balanced|long|DATA_INSUFFICIENT|无赢家（允许空结果）|
|B_confirmed_base_breakout|aggressive|short|DATA_INSUFFICIENT|无赢家（允许空结果）|
|B_confirmed_base_breakout|aggressive|mid|DATA_INSUFFICIENT|无赢家（允许空结果）|
|B_confirmed_base_breakout|aggressive|long|DATA_INSUFFICIENT|无赢家（允许空结果）|
|C_trend_pullback|conservative|short|DATA_INSUFFICIENT|无赢家（允许空结果）|
|C_trend_pullback|conservative|mid|DATA_INSUFFICIENT|无赢家（允许空结果）|
|C_trend_pullback|conservative|long|DATA_INSUFFICIENT|无赢家（允许空结果）|
|C_trend_pullback|balanced|short|DATA_INSUFFICIENT|无赢家（允许空结果）|
|C_trend_pullback|balanced|mid|DATA_INSUFFICIENT|无赢家（允许空结果）|
|C_trend_pullback|balanced|long|DATA_INSUFFICIENT|无赢家（允许空结果）|
|C_trend_pullback|aggressive|short|DATA_INSUFFICIENT|无赢家（允许空结果）|
|C_trend_pullback|aggressive|mid|DATA_INSUFFICIENT|无赢家（允许空结果）|
|C_trend_pullback|aggressive|long|DATA_INSUFFICIENT|无赢家（允许空结果）|
|D_residual_momentum|conservative|short|DATA_INSUFFICIENT|无赢家（允许空结果）|
|D_residual_momentum|conservative|mid|DATA_INSUFFICIENT|无赢家（允许空结果）|
|D_residual_momentum|conservative|long|DATA_INSUFFICIENT|无赢家（允许空结果）|
|D_residual_momentum|balanced|short|DATA_INSUFFICIENT|无赢家（允许空结果）|
|D_residual_momentum|balanced|mid|DATA_INSUFFICIENT|无赢家（允许空结果）|
|D_residual_momentum|balanced|long|DATA_INSUFFICIENT|无赢家（允许空结果）|
|D_residual_momentum|aggressive|short|DATA_INSUFFICIENT|无赢家（允许空结果）|
|D_residual_momentum|aggressive|mid|DATA_INSUFFICIENT|无赢家（允许空结果）|
|D_residual_momentum|aggressive|long|DATA_INSUFFICIENT|无赢家（允许空结果）|

主表 36 行状态为 `SEE_SUBASSETS` 语义：金/债不得与股票超额收益混排。180 个子资产切片见 `etf_subasset_experiments.csv`。
