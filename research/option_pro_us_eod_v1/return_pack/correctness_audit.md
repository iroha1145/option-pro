# PR #174 v2 正确性审阅

审阅锚点：`38e570e127d6d47e417c6dffbd09bb44f954c9e0`。
本轮正确性提交：见 `hashes.json` 的 `code_sha`。
测试日志：`engineering_tests_v2.log`。命令：`PYTHONPATH=backend pytest -q tests/test_research_eod_v1_*.py` → **68 passed**。

本文件记录已修复项、回归与未执行项。它不是收益结论，也不晋升算法。

## 1.1 候选 / 参考池

已修复。`compute_snapshot` 只把主题候选写入 `rows` / `candidate_ids`；`reference_ids` 供 G/D/横截面。人工 `extra_members` 不能绕过证券类型、场所或 ETF 分轨。

回归：`tests/test_research_eod_v1_correctness.py::test_candidate_and_reference_pools_are_separated`。

## 1.2 时点与完整性

已修复。缺 T 完整 OHLC 的名字不能进 T 候选或参考横截面。`source_available_at > as_of` 的序列被跳过。追加 T 之后行情仍由 `clip_panel_to_as_of` 截断。跨证券按 session 日期对齐，不假设数组下标同一天。

回归：`test_missing_t_bar_and_late_source_are_excluded`、`test_session_join_is_by_date_not_array_index`、既有 `test_appending_future_bars_does_not_change_t_raw_or_snapshot`。

未执行：供应商 vintage / 原始发布时点期刊。今天下载的历史仍标 `download_time_not_pit`。

## 1.3 平台状态机

已修复。`resolve_frozen_setup` 冻结首次形成的阻力；T+1 的 103 高点不能改写冻结的 ~100 平台。`current_consecutive_closes` 与 `max_consecutive_closes` 分开；跌回后再收复从 1 起算。

回归：`test_frozen_platform_survives_later_highs_and_resets_consecutive`。

## 1.4 订单与账本

已实现研究账本 `ledger.py`。成交价使用 `raw_open`。禁止用观察到的 T+1 开盘取消已登记开盘单。平价加成本为亏损；拆股财富守恒；分红入账；现金收购清仓；未知终值不按成本无限估值；零信号保留全期现金日。

回归：`tests/test_research_eod_v1_ledger.py`。

未执行：真实公司行动期刊上的市场组合回测。Yahoo 复权口径未核验，故 864/180/12 **没有**市场 PnL。

## 1.5 综合层

已修复。同家族跨主题先合并，再跨家族。R/G 取中位数，不取输入第一行。M2 拒绝无 `label_matured_at` / 未成熟 / 未来标签，并标明 `RESEARCH_POINT_ESTIMATE`。M3 高相关或缺失相关时跳过该名字，不结束搜索。M4 使用家族折叠后的分数，不被最后主题覆盖。

回归：`test_composite_permutation_dedup_and_m3_skip` 与既有 composite 测试。

## 1.6 字段 / 网络

ADV20 为 `mean(T-20:T-1)`，20 根，不含 T。`ma_distance_atr` / `platform_distance_atr` / `invalidation_distance_atr` 分列。快照行含 `profile` / `horizon` / `adv20` / `atr`。schema：`research/option_pro_us_eod_v1/schemas/`。

网络：`tests/test_research_eod_v1_network_spy.py` 拦截 `socket.socket.connect`。硬编码 `network_calls=0` 不再当作唯一证据。

失败发布保留旧快照：`test_failed_publish_keeps_previous_snapshot`。

## 1.7 CI 与边界

研究路由已从生产 `main.py` 卸下。生产只保留默认 `false` 的 `RESEARCH_EOD_V1_ENABLED`。对照：`production_boundary.diff`。

未在本环境跑完整 GitHub Actions 镜像/部署/worker 链。已跑研究回归与 `git diff --check`。

## 未执行 / 不得宣称完成

- 十年 PIT 全市场、退市并集、历史主题成员
- 常规时段分钟核验
- 研究源 vs 生产源同口径对照（`SOURCE_PARITY_PENDING`）
- 864 × 4 家族 × 剖面 × 周期的市场回测
- 解封 2024-07-01 holdout 或选赢家
