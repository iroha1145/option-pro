# 开发区结果（进行中，封存区未揭盲）

证据等级：选股排序数学 **B**，股票池与幸存者偏差 **C**。无 Grade A 真实发布快照。

正式数字以 `/opt/cursor/research/screener-radar-data/runs/` 原始 JSON 为准。本文件只在对应回放完成后填写；未完成的段落保持空白，禁止用 20 日冒烟样本冒充 988 日基线。

## 1. 选股原版 `all/balanced`

- 试验：`original-screener-balanced-all`
- 样本：开发区交易日，全部合格 `view_rows`，主期限 20 日，相对合格池超额
- 主指标每日 Rank IC：_待正式回放结束_
- 日期分块 Top10 超额与 95% CI：_待填_
- 相对 63 日动量对照：_待填_
- 是否达到事前门槛（IC>0.02 且 CI 不含 0；20 日超额>20bps，10bps 成本后）：_待填_

## 2. 六种预登记模式

分开报告，不合并胜率。short/mid/long 的 IC 使用生产排序键，不是 `ranking_score`。

| 模式 | mean IC | ICIR | 正 IC 日占比 | Top10 超额 |
|------|---------|------|--------------|------------|
| all/balanced | | | | |
| short/balanced | | | | |
| mid/balanced | | | | |
| long/balanced | | | | |
| all/conservative | | | | |
| all/aggressive | | | | |

## 3. 雷达日线平台

- 试验：`original-radar-daily-base-theme-universe`
- 无 Discovery，ORB/盘前不可验证
- 首次 TRIGGERED 后 20 日相对 SPY：_待填_

## 4. 联用

只使用触发日之前的选股快照。当日收盘名单不得为当日突破背书。

## 5. 可交易账本

T+1 开盘成交；缺开盘记 unavailable。成本 5/10/25 bps 分列。统计信号与可交易价值分表。

## 6. 当前决定

原版 / 修复 / 候选 / 执行协议全部锁定前，**不揭盲封存区**。
