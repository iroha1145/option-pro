# 回传包（v1 + v2）

v1：`DATA_INSUFFICIENT`，无许可 PIT，无市场收益。
v2：正确性修复 + Yahoo 当前池诊断。**仍无十年 PIT 回测，无赢家，不晋升。**

v2 增量：`correctness_audit.md`、`data_source_decision.md`、`provider_probe.json`、`data_capabilities.json`、`history_budget.json`、`download_coverage.csv`、`theme_membership_crosswalk.csv`、`theme_characteristics.csv`、`theme_diagnostic.json`、`v2_trial_counts.json`、`engineering_tests_v2.log`、`production_boundary.diff`。

必含文件：

- `audit.md`、`data_audit.json`、`hashes.json`、`trial_ledger.json`
- `all_experiments.csv`（864 行 + 表头）
- `etf_subasset_experiments.csv`（180）
- `composite_experiments.csv`（12）
- `annual_oos.csv` / `regime_oos.csv` / `subgroup_oos.csv` / `capital_cost_scenarios.csv`
- `signals.parquet` / `trades.parquet` / `daily_equity.parquet` / `rejections.parquet` + `*.schema.json`
- `engineering_fixture_trades.parquet`：合成工程夹具，不是市场回测
- `sector_reports/*.md`（24）
- `return_summary.json`
- `engineering_tests.log`、`performance_network.json`、`code_diff_*`

台账另含 864 条 `technical_plus_event_guard` overlay，全部 `DATA_INSUFFICIENT`。
