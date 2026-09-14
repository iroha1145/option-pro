# 全站性能专项

本目录记录基于 `origin/main` `31e8955d89dc2b9b51a5bea1c47f5cfa6ea8cabc` 的隔离性能优化任务。原始 trace / 视频 / 大批量 JSON 不入库，放在 `/opt/cursor/artifacts/perf/`。

| 文件 | 内容 |
|------|------|
| [00-plan.md](00-plan.md) | 目标、约束、闭环与交付 |
| [01-environment.md](01-environment.md) | 机器、运行时、对照工作区、启动命令 |
| [02-module-matrix.md](02-module-matrix.md) | 全站模块—功能—数据链路—测试覆盖 |
| [03-baseline-and-budget.md](03-baseline-and-budget.md) | 冻结测量配置与验收预算 |
| [04-experiments.md](04-experiments.md) | 逐轮实验记录 |
| [05-status.md](05-status.md) | 当前状态、缺口与下一步 |
| [06-review-round1.md](06-review-round1.md) | 第 1 轮对照复查（非隔离审查者） |
| [07-review-round2.md](07-review-round2.md) | 第 2 轮隔离复查 |
| [08-review-round3.md](08-review-round3.md) | 第 3 轮隔离复查（只读；完成判定仍部分成立） |
| [09-review-fixes.md](09-review-fixes.md) | PR 审查 R1–R4 修复与复验边界 |
| [10-review-fixes.md](10-review-fixes.md) | 抽屉恢复 / 详情重试 / ready 业务区 |
| [artifacts/](artifacts/) | 体量可控的复测原始 JSON |

对照未优化树：`$HOME/option-pro-unoptimized`（同一 SHA，detached HEAD）。

测量与种子脚本：`scripts/perf/`。
