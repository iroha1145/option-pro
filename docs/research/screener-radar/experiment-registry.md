# 实验台账

权威登记文件在数据目录，不进 Git：

`/opt/cursor/research/screener-radar-data/experiment_registry.jsonl`

恢复上下文时先读本文件、`status.md` 与 `findings.md`。不要把已失败搜索重新包装成独立验证。封存区未揭盲。

## 开发区决定摘要

| trial_id | layer | 决定 |
|----------|-------|------|
| original-screener-balanced-all | original | 主 IC 过门槛；相对 63 日动量无经济增量 |
| original-screener-all-profiles | original | 六种模式已分列；short IC 未过 0.02；long 最好但仍弱于动量头部 |
| baseline-momentum-63d | baseline | 同池对照：头部超额与账本都强于生产排序 |
| candidate-disable-market-fit | candidate | 无增量 |
| candidate-unadjusted-min-price | candidate | 在已通过复权过滤的池上无差异；不另跑 min_price=0 |
| original-radar-daily-base-theme-universe | original | 相对 SPY 超额过门槛，但集中在 2020 |
| candidate-radar-exclude-chase-extended | candidate | 超额与账本都略差，不晋升 |
| original-combo-screener-then-radar | original | 更早快照交集无预测增量 |

未跑：验证区选择、封存揭盲、新权重搜索。
