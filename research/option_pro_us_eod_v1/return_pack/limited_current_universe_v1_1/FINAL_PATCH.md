# LIMITED v1.1 最终补丁

保留已跑通的 v1.1 接线。本轮只修拒绝解释、计算身份和入口契约。不回写 v1 / v1.1 快照，不购买 Sharadar，不扩池，不搜权重，不重下行情，不解封 2024-07-01 起留出区，不启用生产。

## 改了什么

1. `apply_price_only_track()` 只在进入当前轨前删除过时的评分原因。重算后保留完整新原因，包括 `LOW_SCORE` 与 `DATA_INSUFFICIENT`。`gate_results` 标为 `gate_results_source=upstream_full_model`，避免预览把上游完整模型诊断当成当前轨判定。
2. `dataset_hash()` 对实际消费字段做投影：OHLCV、`raw_close`、`dollar_volume`、`volume_scope`、`price_adjustment`、`missing` / `partial` / `halted`、`vintage_status`、`economic_known_at`。未消费的 `raw_open` / `retrieved_at` / `source_published_at` / vendor TRI 不进入计算摘要。有效 `dollar_liquidity_verified` / `volume_session_verified` / `volume_verified` 与公开主题/家族范围进入 `limited_config_hash`、`run_signature` 和快照 cache key。真实免费缓存默认仍为 unverified。
3. 显式日期必须是开发区交易日，否则结构化 `INVALID_SESSION`，CLI 非 0。省略 `--session` 仍默认 `2024-06-28`。合成夹具缺日期只做带标签回退（`session_remapped` + `synthetic_fixture_end`）。回放汇总 `publish_failed` / attempted / served；发布失败时 run report 为 `PARTIAL` 或 `PUBLISH_FAILED`，不再用 `RAN` / exit 0 冒充成功。

未改分数线、权重、资格数学或 `COMPUTE_VERSION=limited-current-v1.1`。

## 2024-06-28 单日对照

同一份 gitignored 缓存，`fetched=0`，`CRWV` 仍缺失。输出写到临时目录，未覆盖本包已提交快照。

| 项 | 已提交 v1.1 | 本轮复算 |
| --- | --- | --- |
| eligible_n | 0 | 0 |
| watch_n | 25 | 25 |
| composite_stock_n / composite_etf_n | 0 / 0 | 0 / 0 |
| 观察证券集合 | 18 只 | 相同 |
| 因子 / 分数 / 资格 | — | 0 行数学变化 |
| 拒绝原因 | 缺当前轨评分原因 | +876 条（`LOW_SCORE` 807，其余为 `DATA_INSUFFICIENT` 等） |
| config_hash | `9e5c8559…9606bf` | `a9b5058b…2ad09e` |
| run_signature | `b376e889…2d98a5` | `b4245ce4…e33c0a` |
| dataset_hash | `058c56a4…ca68a76` | `b14e251c…35a2af` |
| capability_flags | 未写入 | 全部 false |

数学与名单未变，因此不重跑 20 日 + 864。空 M1 仍合法。UNKNOWN 未改成 PASS。

## 测试结果

| 测试 | 结果 |
| --- | --- |
| `test_fresh_low_score_reason_is_retained` | passed |
| `test_fresh_missing_required_factor_reason_is_retained` | passed |
| `test_consumed_quality_and_amount_fields_change_identity` | passed |
| `test_effective_capability_values_bind_published_identity` | passed |
| `test_explicit_non_trading_session_is_invalid` | passed |
| `test_replay_publish_failure_is_not_ran_success` | passed |
| `tests/test_research_eod_v1_limited_v1.py` | 38 passed |
| 相关 research_eod 回归 | 94 passed |
| CLI `--session 2024-06-01 --dataset yahoo_cache` | `INVALID_SESSION`，exit 2 |
| 2024-06-28 单日主路径 | 名单/分数不变；原因更完整；身份已更新 |

未把 verified 强行开在真实免费缓存上。下一步仍是本地查看受限历史工具；生产现时运行、流动性证据和完整数据研究需单独授权。
