# PR #174 第三轮正确性审计

审阅锚点：`797ac6a8ae8d0bf68755bb1b2e30bb1a72d40024`  
本轮 head 见 git。不合并、不晋升、不调门槛凑 Top-K、不把 24 个无效 EOD 快照算作已执行实验。

## 修复映射

| ID | 问题 | 代码 | 测试 / 命令 |
|---|---|---|---|
| F1 | 盘中抓取被写成当日收盘 | `calendar_asof.last_complete_eod_session`；真实 runner 用 `capture_as_of`，不再调用 `as_of_after_close` | `test_ny_1324_capture_is_not_same_day_eod`、`test_regular_close_boundary_and_vendor_late`、`test_next_day_replay_keeps_prior_complete_session`、`test_future_bars_do_not_change_complete_session` |
| F2 | 第一个平台永久锁定；B 用历史最大连续 | 失败后保留修复窗，超时终止再换新平台；超过 `max_sessions*3` 硬到期；`setup_b` 用当前连续收盘 | `test_failed_platform_can_repair_before_a_new_base_forms`、`test_event_log_keeps_distinct_setup_ids_for_sequential_platforms`、`test_failed_early_base_is_not_permanently_active`、`test_first_cross_is_frozen_after_known_at`、`test_breakout_gate_checks_current_streak_not_historical_maximum` |
| F3 | D 按下标回归 | 共同 session 网格；回归窗必须相邻合法日；内部缺日 → `MISSING_DAY_RETURN` | `test_residual_is_invariant_to_unused_benchmark_prefix`、`test_residual_unused_benchmark_prefix_seed_174`、`test_residual_internal_gap_does_not_treat_skip_as_one_day`、`test_residual_short_ipo_is_not_ok`、`test_residual_late_benchmark_uses_common_grid`、`test_residual_peers_with_different_starts_still_align` |
| F4 | 盯市混用几何价；价格比当净收益 | `_mark_price` 只用 `raw_close`；账本 `net_return` 含分红现金流；`plan_trade` 遇拆股/分红不报现金流净收益；缺 raw 不按几何价 sizing | `test_raw_shares_are_marked_at_raw_close`、`test_split_trade_return_reconciles_to_share_cash_flows`、`test_split_and_cash_dividend_same_day_keep_identity`、`test_special_dividend_uses_dated_pay_event`、`test_plan_trade_price_path_is_not_cashflow_through_a_split`、`test_missing_raw_close_does_not_size_from_geometry_close` |
| F5 | 缺 H/L/量/TRI 被补成已知 | `bars_to_series` 保留 NaN；Yahoo 丢掉 OHLC 违规根；快照带 vintage/adjustment/identity 来源 | `test_missing_high_low_are_not_fabricated_into_complete_bars`、`test_reconstruction_does_not_use_download_time_to_block_history`、`test_partial_bar_is_not_a_complete_session`、`test_offline_combined_parquet_roundtrip_hashes_bytes` |
| P2 | M3 不重排；M2 可省略 as_of | M3 每轮重算边际；M2 强制 `as_of`；M1 先折叠再比分歧 | `test_m3_reorders_by_marginal_score_after_first_selection`、`test_m2_cannot_accept_future_labels_with_no_evaluation_date`、`test_m1_duplicate_theme_rows_are_idempotent` |
| CI | parquet 无引擎 | `requirements-ci.in` 锁定 `pyarrow==25.0.1`，不进生产 `requirements.in` | `test_committed_synthetic_fixture_is_readable` |

`FEATURE_VERSION` → `us-eod-research-features-v1.2`。

## 采集时间与无效 EOD

- 旧 manifest `retrieved_at=2026-09-16T17:24:41.368841+00:00` = 纽约 **13:24:41**。
- 24 主题诊断 clock `2026-09-16T19:24:10Z` = 纽约 **15:24**，仍在常规 16:00 收盘前。
- `last_complete_eod_session` = **2026-09-15**。
- 207 根 2026-09-16 部分 bar 标 `PARTIAL` 并排除 EOD。收盘后重抓尚未做：当前时钟仍未过 16:00 ET。
- 旧 24 个 A/balanced/mid 当日快照：`INVALID_EOD_CAPTURE`，**不计入** `executed_snapshots`。
- 大批日线只在 gitignore 缓存；公开 Git 只有统计、哈希、合成夹具。

## 本轮计数（分开报）

| 项 | 数 |
|---|---:|
| registered | 1920 |
| revoked_invalid_eod_captures | 24 |
| executed_snapshots | 144 = 16 工程 + 32 四主题两日 + 96 二十四主题一日 |
| unique theme-date-algo | 128（16 次 9/15 四主题与 24 主题卡重叠） |
| executed_backtests | 0 |
| outcomes_inspected | 144 |
| market_backtest_run | false |
| winners | [] |

24 主题 × A/B/C/D，信号日 2026-09-15，214/214 当前名单有日线：

| 主题 | A | B | C | D |
|---|---:|---:|---:|---:|
| semiconductors | 0 | 0 | 0 | 0 |
| software | 0 | 0 | 0 | 2（MSFT, CRM） |
| ai_cloud | 0 | 0 | 0 | 0 |
| biotech | 0 | 0 | 0 | 0 |
| healthcare | 0 | 0 | 0 | 0 |
| consumer_electronics | 0 | 0 | 0 | 0 |
| automotive | 0 | 0 | 0 | 1（TM） |
| ev_supply | 0 | 0 | 0 | 0 |
| finance | 0 | 0 | 0 | 1（JPM） |
| fintech | 0 | 0 | 0 | 1（MA） |
| retail | 0 | 0 | 0 | 0 |
| luxury | 0 | 0 | 0 | 0 |
| media_streaming | 0 | 0 | 0 | 0 |
| social_internet | 0 | 0 | 0 | 0 |
| energy | 0 | 0 | 0 | 0 |
| utilities | 0 | 0 | 0 | 0 |
| defense_aero | 0 | 0 | 0 | 0 |
| airlines | 0 | 0 | 0 | 0 |
| real_estate | 0 | 0 | 0 | 0 |
| crypto | 0 | 0 | 0 | 0 |
| china_adr | 0 | 0 | 0 | 0 |
| telecom | 0 | 0 | 0 | 0 |
| industrials | 0 | 0 | 0 | 0 |
| etfs | 0 | 0 | 0 | 0 |

合格行是未验证成交的信号诊断，不是赢家。四主题两日结果见 `round3_yahoo_abcd.json`。

## 平台 / 残差 / 账本

- 早期平台跌破 support 后进入 failed；修复窗内收复区间可 `repaired`，超时 `terminated` 后才允许新平台。
- 事件日志保留多个 `setup_id`；`first_cross` 在回撤后再突破时不改。
- 种子 174：SPY 多 300 根无关前缀与裁剪后 D 值一致。
- 原始价 100 / 几何价 50 盯市为 100。二拆一现金流净收益 0。同日拆股+现金分红期末权益 10100，交易净收益含分红。
- `plan_trade` 在持有期内遇到拆股时 `net_return=None`，不把价格比当成现金流。
- 特别分红按 `ex_date` 记应收、`pay_date` 转现金。

## 数据契约与离线哈希

- 缺 high/low 的 bar 不能通过 `has_complete_session_bar`。
- 快照行带 `price_adjustment` / `vintage_status` / `tri_verified` / `identity_confidence` / `industry_source`。
- 断网回放命令：`PYTHONPATH=backend python research/option_pro_us_eod_v1/scripts/export_offline_replay.py`
- 校验字节哈希：`f8de36f35ffdfcd532f84fecbb2258d0aa99dd2d06a2de3337f09e34bff77044`（见 `offline_replay_hash.json`）。文件不进 Git。

## 测试命令

```
PYTHONPATH=backend pytest -q tests/test_research_eod_v1_*.py tests/test_pr174_review_regressions.py
```

结果：**109 passed**。

本机前端（不是 GitHub CI 终态）：

- `npm --prefix frontend-src run lint`：0 error，2 个既有 hooks warning。
- `node --experimental-strip-types --test frontend-src/tests/*.test.mjs`：1050 pass，2 fail（`built recovery chunk` / `production chunks keep en/ja dictionaries`），1 skip。这两项是打包产物断言，本轮未改前端。

## CI

GitHub 单个 `test` job 串行包含 pytest → 前端构建/lint/Playwright → compose 镜像 → worker。`8fbdb800` 推送时两个 `CI / test` 仍为 in_progress。镜像与 worker 阶段在 pytest/前端失败时会被跳过。在该 job 变绿之前，不声称端到端 CI 已完成。

生产默认、A0、T1、日常股票/期权/账户路径未改。`RESEARCH_EOD_V1_ENABLED` 仍为 false。

## 未执行

- 2026-09-16 收盘后重抓末根并生成新数据版本
- 864 主矩阵回测
- 退市并集 / 历史 PIT 成员
- Massive（未使用；若曾在聊天里粘贴密钥，应轮换）
- GitHub 前端 / 镜像 / worker 终态
- 合并、晋升、解封 holdout
