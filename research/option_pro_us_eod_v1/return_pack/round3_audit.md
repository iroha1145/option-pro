# PR #174 第三轮正确性审计

审阅锚点：`797ac6a8ae8d0bf68755bb1b2e30bb1a72d40024`  
本轮 head 见 git。不合并、不晋升、不调门槛凑 Top-K、不把 24 个无效 EOD 快照算作已执行实验。

## 修复映射

| ID | 问题 | 代码 | 测试 / 命令 |
|---|---|---|---|
| F1 | 盘中抓取被写成当日收盘 | `calendar_asof.last_complete_eod_session`；真实 runner 用 `capture_as_of`，不再调用 `as_of_after_close` | `test_ny_1324_capture_is_not_same_day_eod`、`test_regular_close_boundary_and_vendor_late`、`test_next_day_replay_keeps_prior_complete_session`、`test_future_bars_do_not_change_complete_session` |
| F2 | 第一个平台永久锁定；B 用历史最大连续 | `resolve_frozen_setup` 失败/到期后换新平台；超过 `max_sessions*3` 硬到期；`setup_b` 用 `current_consecutive_closes` | `test_failed_early_base_is_not_permanently_active`、`test_failed_platform_events_include_later_base`、`test_wide_old_platform_expires_by_age_so_later_base_can_form`、`test_first_cross_is_frozen_after_known_at`、`test_breakout_gate_checks_current_streak_not_historical_maximum` |
| F3 | D 按下标回归 | 共同 session 网格；回归窗必须相邻合法日；内部缺日 → `MISSING_DAY_RETURN`；市场/行业输入先过滤 | `test_residual_is_invariant_to_unused_benchmark_prefix`、`test_residual_unused_benchmark_prefix_seed_174`、`test_residual_internal_gap_does_not_treat_skip_as_one_day`、`test_residual_short_ipo_is_not_ok`、`test_residual_late_benchmark_uses_common_grid`、`test_residual_peers_with_different_starts_still_align` |
| F4 | 盯市混用几何价；未预尺寸暗仓 | `_mark_price` 只用 `raw_close`；`simulate_portfolio` → `simulate_ledger`；收购必须带日期 | `test_raw_shares_are_marked_at_raw_close`、`test_split_trade_return_reconciles_to_share_cash_flows`、`test_split_and_cash_dividend_same_day_keep_identity`、`test_acquisition_does_not_cash_out_the_next_day`、`test_unsized_orders_are_rejected`、`test_incomplete_exit_is_right_censored_or_deferred` |
| F5 | 缺 H/L/量/TRI 被补成已知 | `bars_to_series` 保留 NaN；Yahoo 丢掉 OHLC 违规根；哈希文件字节 | `test_missing_high_low_are_not_fabricated_into_complete_bars`、`test_reconstruction_does_not_use_download_time_to_block_history`、`test_partial_bar_is_not_a_complete_session`、`test_offline_combined_parquet_roundtrip_hashes_bytes`、`test_yahoo_rejects_inconsistent_ohlc_without_repair` |
| P2 | M3 不重排；M2 可省略 as_of | M3 每轮重算边际；M2 强制 `as_of`；M1 先折叠再比分歧 | `test_m3_reorders_by_marginal_score_after_first_selection`、`test_m2_cannot_accept_future_labels_with_no_evaluation_date`、`test_m1_duplicate_theme_rows_are_idempotent` |
| CI | parquet 无引擎 | `requirements-ci.in` 锁定 `pyarrow==25.0.1`，不进生产 `requirements.in` | `test_committed_synthetic_fixture_is_readable` |

`FEATURE_VERSION` → `us-eod-research-features-v1.2`。

## 采集时间与无效 EOD

- 旧 manifest `retrieved_at=2026-09-16T17:24:41.368841+00:00` = 纽约 **13:24:41**。
- 本轮真实 Yahoo 诊断 clock ≈ `2026-09-16T18:51:11Z` = 纽约 **14:51**，仍在常规 16:00 收盘前。
- `last_complete_eod_session` = **2026-09-15**。
- 47 根 2026-09-16 部分 bar 标 `PARTIAL` 并排除 EOD；XLE 当日因 `OHLC_VIOLATION` 丢弃，未补数。
- 旧 24 个 A/balanced/mid 当日快照：`INVALID_EOD_CAPTURE`，**不计入** `executed_snapshots`。
- `daily_bars.parquet` 不进公开 Git。诊断缓存只写 gitignore 的 `research/option_pro_us_eod_v1/data/cache/`。

## 本轮计数（分开报）

| 项 | 数 |
|---|---:|
| registered | 1920 |
| revoked_invalid_eod_captures | 24 |
| executed_snapshots | 48 = 16 工程夹具 + 32 Yahoo A/B/C/D |
| executed_backtests | 0 |
| outcomes_inspected | 48 |
| market_backtest_run | false |
| winners | [] |

工程夹具（合成，2026-09-15，4 主题 × A/B/C/D）：合格均为 0，状态 `ENGINEERING_ONLY`。

Yahoo 当前池小样本（48 只，4 主题 × A/B/C/D × 2 个完整日）：

| 信号日 | 主题 | A | B | C | D |
|---|---|---:|---:|---:|---:|
| 2026-09-15 | semiconductors | 0 | 0 | 0 | 0 |
| 2026-09-15 | software | 0 | 0 | 0 | 1（MSFT，信号不是成交） |
| 2026-09-15 | energy | 0 | 0 | 0 | 0 |
| 2026-09-15 | etfs | 0 | 0 | 0 | 0 |
| 2026-09-11 | semiconductors | 0 | 0 | 0 | 0 |
| 2026-09-11 | software | 0 | 0 | 0 | 1（MSFT） |
| 2026-09-11 | energy | 1（PSX） | 0 | 0 | 2（CVX, PSX） |
| 2026-09-11 | etfs | 0 | 0 | 0 | 0 |

以上合格行是**未验证成交的信号诊断**。Yahoo Close 不是已核验原始成交价；没有订单、没有账本 PnL、没有赢家。其余 20 个主题本轮未再跑 Yahoo。

主要拒绝：`LOW_SCORE`、`WEAK_STRUCTURE`、`TREND_DIRECTION`、`UNRESOLVED_UPTHRUST`、`BREAKOUT_TRACK_EXPIRED`。D 残差 376 行 `OK`，8 行 `INSUFFICIENT_MATCHED_BENCHMARK`（SPY 相对自身）。

平台事件：MSFT / CVX / PSX 的当前 `setup_id` 都在 2026 年；更早的宽平台按 `max_sessions*3` 硬到期后被替换。不是只测 100→103。

## 数据契约与离线哈希

- 缺 high/low 的 bar 不能通过 `has_complete_session_bar`。
- 下载时间不能阻断按 bar 的历史重建切片。
- `LocalParquetProvider.export_snapshot` 对文件字节做 sha256。
- 合成夹具：`return_pack/fixtures/synthetic_daily_bars.parquet`。
- `theme[0]` / 默认 NASDAQ 只标诊断假设。

## 测试命令

```
PYTHONPATH=backend pytest -q tests/test_research_eod_v1_*.py tests/test_pr174_review_regressions.py
```

结果：**104 passed**。日志：`/opt/cursor/artifacts/research_eod_v1_round3_tests.log`。

Yahoo 诊断：

```
PYTHONPATH=backend python research/option_pro_us_eod_v1/scripts/run_round3_yahoo_abcd.py
```

## CI

本环境未重跑 GitHub Actions 的前端 / 镜像 / 部署 / worker。`17d8c3bc` 上两个 `CI / test` 作业在推送后仍为 in_progress。那些作业完成前，不声称端到端 CI 已绿。

生产默认、A0、T1、日常股票/期权/账户路径未改。`RESEARCH_EOD_V1_ENABLED` 仍为 false。研究路由仍未挂到 `main.py`。

## 未执行

- 其余 20 个主题的真实 Yahoo 多日期矩阵
- 864 主矩阵回测
- 退市并集 / 历史 PIT 成员
- Massive（未使用；若曾在聊天里粘贴密钥，应轮换，密钥不得写入仓库）
- 合并、晋升、解封 holdout
