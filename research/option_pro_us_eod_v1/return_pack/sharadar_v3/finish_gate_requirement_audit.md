# FINISH_GATE_AND_LIVE_PROBE 逐条审计

审阅锚点：`c4d0902013a2fbc94e75f2a2188be46151c978aa`。实现 head：`9c9e7ca7e898ffa20698b5a096356962a46476d3`。分支 `cursor/sharadar-data-first-4939`，PR #176，不合并 #174/#176。

当前 head CI 均为 success：push https://github.com/iroha1145/option-pro/actions/runs/35425631438 、PR https://github.com/iroha1145/option-pro/actions/runs/35425633627 。本地全量 pytest 4002 passed, 6 skipped。

credential_present=false，live_sharadar_request_count=0。本轮未向官方 API 发任何请求，未回退 Yahoo/Massive。

## 0 运行环境

| 项 | 证据 | 判定 |
| --- | --- | --- |
| 第一条命令只输出布尔 | `{'credential_present': False}` | 已证 |
| 不打印值/长度/前后缀/带密钥 URL | `provider_probe.json` `secret_present_in_report=false` | 已证 |
| 不从聊天/历史工件找凭据 | `chat_credentials_read=false`、`massive_key_used=false` | 已证 |
| 缺 key 停 AUTH_REQUIRED，不回退 | 终态 `AUTH_REQUIRED`，`yahoo_fallback_used=false` | 已证 |
| 不反复生成"已接通"报告 | 四表 0 行、分层全部非 PASS | 已证 |

## 1 保留已修部分

error/HTML 与合法空表区分、非 2xx bulk 不发布、页落盘先于 checkpoint、续传前缀、母组有限人数、B 首日子项、三轨换算、永久身份全部保留，测试仍在 `tests/test_research_eod_v1_connect_accept.py`。旧 B0/Round/冻结产物仍只是 `CONTROL_CURRENT_LIST_214`，未调参、未晋升。

## 2 正式下载使用真实历史区间

| 项 | 证据 | 判定 |
| --- | --- | --- |
| 日期表显式 from=2010-01-01 / to=2024-06-28 | `request_plan.json`；`download_request_plan()` | 已证 |
| 主表不臆造日期参数 | `tickers` `extra={}`，`date_bound=false` | 已证 |
| 日期参数进入 checkpoint 签名 | `query_signature` 含 extra；改区间返回 `CHECKPOINT_QUERY_MISMATCH` | 已证 |
| 稳定请求/分片/源版本策略 | `plan_version=sharadar-request-plan-v1`，`shard=pinned_from_to`，主键去重不假定供应商排序 | 已证 |
| 分页不靠最新默认日期 | 固定 [from,to] + skip；无凭据下无实际请求 | 代码已证，活读 AUTH_REQUIRED |
| READ_OK 空页不算样本通过 | `_sample_status` 返回 `EMPTY_NO_SAMPLE` | 已证 |
| AUTH_FAILED/权限不足不开始全量 | `full_download_allowed.blockers` 阻断 fetch | 已证 |
| bulk 元数据按表形状 | `parse_bulk_metadata` 支持 flat 与 `files`；`shape` 字段 | 已证 |

## 3 下载完成不等于验收通过

| 项 | 证据 | 判定 |
| --- | --- | --- |
| 九层各自状态与证据 | `gate_stages.json` AUTH/TABLE_ACCESS/DOWNLOAD/TRANSFORM/IDENTITY/RECONCILE/HISTORY/VOLUME_SCOPE/EXECUTION | 已证 |
| RAW_DOWNLOAD_COMPLETE 独立 | `raw_download_status` 与 `terminal_status` 分开 | 已证 |
| 空四表不能 accepted | `test_four_empty_tables_are_not_accepted`：`DATA_GATE_INSUFFICIENT` | 已证 |
| 必需项失败不能 accepted | `test_failed_required_check_blocks_acceptance`：`DATA_GATE_PARTIAL_REVIEW_REQUIRED` | 已证 |
| partial/insufficient/unsupported/fail 明确 | `stage_summary` 分列 failed/partial/insufficient/blocked | 已证 |
| UNSUPPORTED 不汇总成已核验 PASS | EXECUTION 层单独 UNSUPPORTED，`economic_settlement_blocked_only` | 已证 |
| 接通合法缓存读取入口 | `sharadar_reconcile_source.load_reconcile_rows` 读已捕获缓存，非实时 Yahoo | 已证 |
| 无缓存报 RECONCILIATION_MISSING | `test_missing_reconcile_source_is_not_a_pass` | 已证 |
| 每字段独立有效分母 | `return_coverage_n` / `volume_coverage_n` / `reason_class` | 已证 |
| 跳过行记数量/主键/理由 | `transform_skipped_rows.json`；`transform.skipped_rows` | 已证 |

## 4 先隔离再给验收消费者

| 项 | 证据 | 判定 |
| --- | --- | --- |
| 退市案例用允许窗口视图 | `IsolatedTable` 供 actions/stocks；不再传 raw_rows | 已证 |
| 消费者不直接访问无边界 raw_rows | 原始仅进 store；视图逐行过滤 | 已证 |
| 永久身份 + 事件范围 | `_delist_identity` + `_event_bounds`（上界取解析到的 lastpricedate） | 已证 |
| 复用 ticker 不取同名最后一条 | `test_reused_ticker_resolves_by_permaticker_and_event_year`：DELL 2013 → `sharadar:24420`，终值 13.75 | 已证 |
| relatedtickers 不作已验证别名 | 仅记 `relatedticker_hints_not_verified_aliases` | 已证 |
| 2025 追加不改允许区结果 | `test_holdout_appends_never_reach_acceptance_inputs` | 已证 |
| 元数据盘点可记物理覆盖 | `raw_row_counts` 与 `isolated_row_counts` 分列 | 已证 |
| 不宣称过去污染真实数据 | `live_sharadar_request_count=0` | 已证 |

## 5 历史预算与权限分开

| 项 | 证据 | 判定 |
| --- | --- | --- |
| 观察起点不推断 10 年授权 | `test_observed_earliest_is_not_subscription_proof`：2010-01-04 → `ACCESS_VERIFIED_RANGE_UNKNOWN` | 已证 |
| verified_access / observed_coverage / authorized_range_unknown 分列 | `classify_entitlement` 三列 | 已证 |
| 起点不被改成 2016-09-01 | `research_start` 为观察起点；2016-09-01 仅在 `bulk_years="10"` 时出现 | 已证 |
| 数字首评分日/成熟日/最后有效信号日 | `history_budget.families[*]`；5/20/63 各自 `mature_label_day` | 已证 |
| 输入不足为 NOT_COMPUTED | 无日历 → `NOT_COMPUTED`，不填表达式 | 已证 |
| 交易日历独立于价格记录 | `trading_calendar_sessions` 用 `market_calendar` | 已证 |
| 逐证券有效记录 | `per_security.evaluable_n/insufficient_n` | 已证 |

## 6 全量前的规模约束

`pagination_scale.json`（确定性样本，0 次真实请求）：

| 页数 | 旧逐页前缀重扫读盘行 | 新每页 key index 读盘行 | 比值 | 恢复正确 |
| --- | --- | --- | --- | --- |
| 8 | 30000 | 8000 | 3.75 | 是 |
| 16 | 92000 | 16000 | 5.75 | 是 |

比值随页数增长，确认旧路径按页数平方增长。峰值 traced 内存 8 页 2.9MB / 16 页 4.3MB。落库后转换改为逐行流式读取合并文件，不再整表载入再复制。`test_pagination_cost_is_per_page_not_per_prefix` 同时断言页读量与峰值内存。未为性能绕过字段或内容校验。

## 7 尚未通过项（精确终态）

- 真实 Sharadar 小样本与全量下载：无 `SHARADAR_API_KEY`，`AUTH_REQUIRED`。
- 16 身份案例活体核验：`fixture_list_only`。
- 对账：缓存存在但 `SPY`/`NVDA` 无 Sharadar 身份可对齐，`INSUFFICIENT`，未映射 id 已记录。
- volume scope：`UNKNOWN` / `UNSUPPORTED`。
- 数字历史预算：无实际覆盖，`AUTH_REQUIRED`。
- 全市场选优、权重网格、成员构造：未启动。
