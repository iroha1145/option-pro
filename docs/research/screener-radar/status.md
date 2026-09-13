# 当前状态

更新：2026-09-13。不要依赖其他对话的记忆。

## 已完成

- 固定 `origin/main` = `31e8955d89dc2b9b51a5bea1c47f5cfa6ea8cabc`。
- 独立分支 `cursor/screener-radar-backtest-5ee5` 与 worktree。
- 对照选股 / 雷达实现与 `docs/breakout-radar/*`、`docs/strength-scoring-v2.md`。
- 向生产 `_scan_sync` 注入 `as_of`、`raw_history`、`enrich_live`；默认生产行为保持 enrich_live=True。
- 研究执行层：离线数据、选股回放、雷达日线重建、标签、组合账本、封存禁看、CLI、实验登记。
- 数据能力表与冻结协议。

## 进行中

- Yahoo 日线缓存已完成覆盖审计（228 标的，479,397 根有效 K 线）。
- 开发区原版选股日频回放：首轮因每日重建面板过慢已中止，改为一次加载面板后重跑。
- 雷达日线重建与组合账本尚未出开发区结果。
- 研究单元测试 9 passed；连同既有 research/strength 相关 79 passed。

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
