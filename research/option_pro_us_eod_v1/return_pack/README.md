# 第一轮回传包

状态：`DATA_INSUFFICIENT`。没有许可的十年 PIT 行情，因此 **没有市场收益数字，没有赢家，不晋升生产**。

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
