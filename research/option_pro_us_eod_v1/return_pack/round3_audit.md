# PR #174 第三轮正确性审计

审阅锚点：`797ac6a8ae8d0bf68755bb1b2e30bb1a72d40024`  
本轮不合并、不晋升、不调门槛凑 Top-K、不把 24 个无效 EOD 快照算作已执行实验。

## 修复映射

| ID | 问题 | 代码 | 测试 |
|---|---|---|---|
| F1 | 盘中抓取被写成当日收盘 | `calendar_asof.last_complete_eod_session`；runner 改传真实 clock，不再调用 `as_of_after_close` | `test_ny_1324_capture_is_not_same_day_eod`、`test_regular_close_boundary_and_vendor_late`、`test_committed_eod_capture_is_not_before_its_market_close` |
| F2 | 第一个平台永久锁定；B 用历史最大连续 | `factors.resolve_frozen_setup` 失败/到期后可换新平台；`setup_b` 用 `current_consecutive_closes` 与 `platform_distance_atr` | `test_failed_early_base_is_not_permanently_active`、`test_breakout_gate_checks_current_streak_not_historical_maximum` |
| F3 | D 按下标回归 | `residual.residual_raw_momentum` 显式共同 session 网格 | `test_residual_is_invariant_to_unused_benchmark_prefix`（种子 174） |
| F4 | 盯市用几何 close；两条执行路径 | `_mark_price` 只用 `raw_close`；`simulate_portfolio` → `simulate_ledger`；收购必须带日期；未预尺寸拒绝 | `test_raw_shares_are_marked_at_raw_close`、`test_split_trade_return_reconciles_to_share_cash_flows`、`test_one_unknown_mark_does_not_erase_other_known_positions`、`test_unsized_orders_are_rejected` |
| F5 | 缺 H/L/量/TRI 被补成已知 | `bars_to_series` 保留 NaN；`validate_research_bars`；LocalParquet 按列名/合并包；哈希文件字节 | `test_missing_high_low_are_not_fabricated_into_complete_bars`、`test_synthetic_parquet_roundtrip_and_byte_hash` |
| P2 | M3 不重排边际；M2 可省略 as_of；M1 看重复主题行 | M3 每轮重算边际；M2 强制 `as_of` 并删除 `utility_lower`；M1 先折叠再比分歧 | `test_m3_reorders_by_marginal_score_after_first_selection`、`test_m2_cannot_accept_future_labels_with_no_evaluation_date` |
| CI | `pd.read_parquet` 无引擎 | `backend/requirements-ci.in` 锁定 `pyarrow==25.0.1`；合成 4 行 parquet | `test_committed_synthetic_fixture_is_readable` |

`FEATURE_VERSION` → `us-eod-research-features-v1.2`。

## 采集时间与无效 EOD

- 旧 manifest `retrieved_at=2026-09-16T17:24:41.368841+00:00` = 纽约 **13:24:41**。
- 常规收盘 16:00 ET。距收盘约 9318 秒。
- 该时钟下 `last_complete_eod_session` = **2026-09-15**。
- 旧 24 个 A/balanced/mid 当日快照：`INVALID_EOD_CAPTURE`，**不计入** `executed_snapshots`。
- `daily_bars.parquet`（582198 行）已从公开 Git 删除；历史字节哈希仍在 `hashes.json`。

## 本轮计数（分开报）

| 项 | 数 |
|---|---:|
| registered | 1920 |
| revoked_invalid_eod_captures | 24 |
| executed_snapshots | 16（4 主题 × A/B/C/D，工程夹具，信号日 2026-09-15） |
| executed_backtests | 0 |
| outcomes_inspected | 16 |
| market_backtest_run | false |
| winners | [] |

16 次快照是合成面板上的信号诊断（`ENGINEERING_ONLY`），合格数均为 0。不是市场成交，也不是 864 矩阵回测。

主题卡（工程夹具，2026-09-15）：

| 主题 | 候选 | 参考 | A/B/C/D 合格 |
|---|---:|---:|---|
| semiconductors | 3 | 10 | 0/0/0/0 |
| software | 1 | 10 | 0/0/0/0 |
| energy | 1 | 10 | 0/0/0/0 |
| etfs | 2 | 2 | 0/0/0/0 |

其余 20 个主题本轮未再跑真实 Yahoo 日线（大磁带已离线）。旧 24 张主题卡全部 `revoked=true`。

## 平台 / 残差 / 账本证据

- 早期阻力 100 的平台在持续跌破 support=90 后失败；t=499 不再返回 100。
- 种子 174：SPY 多 300 根无关前缀与裁剪后 D 值一致。
- 原始价 100 / 几何价 50 盯市为 100；二拆一现金流净收益 0，期末权益 10000。
- 单票缺 `raw_close` 时总权益为 `null`，另一持仓仍留在 `known_positions_value` / `partial_value`。

## 数据契约与离线哈希

- 缺 high/low 的 bar 不能通过 `has_complete_session_bar`。
- `LocalParquetProvider.export_snapshot` 对文件字节做 sha256，不再哈希路径列表。
- 合成夹具：`return_pack/fixtures/synthetic_daily_bars.parquet`。
- Yahoo Close / Adj Close 仍未核验为可执行原始价或已验证 TRI。
- `theme[0]` / 默认 NASDAQ 只标为诊断假设，不是已核验身份或经济行业。

## CI

本环境已跑：

```
PYTHONPATH=backend pytest -q tests/test_research_eod_v1_*.py tests/test_pr174_review_regressions.py
```

结果：**88 passed**。

未在本轮重跑 GitHub Actions 的前端 / 镜像 / 部署 / worker 阶段。在那些作业完成前，不声称端到端 CI 已绿。

生产默认、A0、T1、日常股票/期权/账户路径未改。`RESEARCH_EOD_V1_ENABLED` 仍为 false。研究路由仍未挂到 `main.py`。

## 未执行

- 授权离线真实行情上的多日期 A/B/C/D 市场标签
- 864 主矩阵回测
- 退市并集 / 历史 PIT 成员
- Massive（环境无密钥且未使用）
- 合并、晋升、解封 holdout
