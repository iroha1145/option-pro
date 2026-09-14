# 当前状态

更新：2026-09-14。不要依赖其他对话的记忆。

## 已完成

- 固定 `origin/main` = `31e8955d89dc2b9b51a5bea1c47f5cfa6ea8cabc`。
- 独立分支 `cursor/screener-radar-backtest-5ee5` 与 worktree。
- 对照选股 / 雷达实现与 `docs/breakout-radar/*`、`docs/strength-scoring-v2.md`。
- 向生产 `_scan_sync` 注入 `as_of`、`raw_history`、`enrich_live`；默认生产行为保持 enrich_live=True。
- 研究执行层：离线数据、选股回放、雷达日线重建、标签、组合账本、封存禁看、CLI、实验登记。
- 数据能力表与冻结协议。
- 回放 CLI 在工作进程内压缩行、按日落盘、支持 `--resume`；压缩行可重算六种预登记模式。
- 联用改为「严格更早的选股快照」，禁止用当日收盘名单给当日突破背书。

## 进行中

- Yahoo 日线缓存已完成覆盖审计（228 标的，479,397 根有效 K 线）。
- 已修复研究面板 `object` 索引导致 `_complete_daily_frame` 不裁剪、未来 K 线泄漏的问题。修复后 2019-03-20 截断面板与全样本裁剪排名一致，ARM/RDDT 不再出现在 2019 年结果中。
- 该问题记为 **修复版**：原函数在非 DatetimeIndex 上静默跳过裁剪；生产 yfinance 路径本身是 DatetimeIndex，线上默认分不变。
- Top20 截断回放已作废，不能当正式基线。
- 正式开发区全合格池选股回放 **已完成**（988 日）。主 IC 0.037，CI 不含 0；相对 63 日动量无经济增量。详见 `findings.md`。
- 雷达日线重建进行中（按标的 213 只 × 988 日）。

## 未完成 / 受阻

- Grade A：无生产扫描库。
- 盘中 / 盘前雷达类型：无历史分钟与盘前数据。
- 封存区：按协议未揭盲。
- 独立审查 agent：将在有原始结果文件后进行。

## 不要做的事

- 不要看封存区收益。
- 不要打开 Range Persistence 生产权重。
- 不要把候选权重写进 `personal.toml` 默认值。
- 不要把下载塞进回放 CLI。
