# CLOSEOUT_AND_REAL_SAMPLE 逐条审计

审查锚点：`183b5d29f6d9e2c10b39b06ba2eb5f4f6c1cc5f9`（Fable 5.1 co-authored 的代码提交）。实现 head：`b5c6669c1d953c95cb45f960af1b025f8a4df763`。分支 `cursor/sharadar-data-first-4939`，PR #176。#174/#176 未合并，未切 main。

实现 head CI 均为 success：push https://github.com/iroha1145/option-pro/actions/runs/35433685791 、PR https://github.com/iroha1145/option-pro/actions/runs/35433688282 。`Run Python tests` 与 `Audit locked runtime dependencies` 步骤均 success。锚点 `183b5d29` 的 CI 同为 success（35431370360 / 35431373010）。

Fable 的改动全部保留：bulk 优先、分页恢复、分桶转换、实际日期请求、隔离视图、母组与 B 首日修复、sqlite 主键索引、append-only 合并文件、DELL1 后缀身份、`fake_sharadar_server.py`。本轮只补有限验收边界。

## 0 实际运行环境

在执行脚本的同一进程回报：`{'credential_present': False}`。未打印密钥、长度、前后缀、完整环境变量或含密钥 URL；未检索聊天或旧文件找密钥。

真实小样本因此未执行，`live_sharadar_request_count=0`。运行环境记录：

- 工作区 `/workspace`，分支 `cursor/sharadar-data-first-4939`
- 进程：`python research/option_pro_us_eod_v1/scripts/run_sharadar_data_gate.py`，与布尔检查同一解释器
- 变量名 `SHARADAR_API_KEY`，作用域为进程环境变量；本 agent 的 VM 环境中不存在该名字
- 启动方式：Cursor Cloud Agent VM；Runtime Secret 未注入本 VM

停止凭据猜测。负责人确认已配置后，需在注入了 Runtime Secret（不是 Build Secret）的新 agent 上继续本分支。

## A 已完成 bulk 的快返不得把 checkpoint 当实际数据

`ingest_bulk_archive` 的同签名 complete 快返现在先跑 `verify_ingested_state`：核验已提交页哈希、合并文件行数、sqlite 主键索引行数、以及 checkpoint 里记录的源归档 sha256。

| 场景 | 结果 | 测试 |
| --- | --- | --- |
| 第一次成功 | `READ_OK`，checkpoint 记 `source_archive.sha256` | `test_bulk_first_ingest_records_a_verifiable_payload` |
| 同查询重复恢复 | `READ_OK`，`verification.problems == []` | 同上 |
| 删 merged | 从已验证页重建后 `READ_OK`，记 `rebuilt_from=committed_pages` | `test_missing_merged_file_rebuilds_from_pages` |
| 删 page | `MISSING` | `test_deleted_or_altered_page_is_not_read_ok` |
| 改 page 内容 | `CORRUPT`（哈希不符） | 同上 |
| checkpoint-only | `MISSING/CORRUPT/PARTIAL`，`complete=false`，`row_count=0`，不返回 READ_OK | `test_checkpoint_alone_is_not_a_payload` |
| 归档变化 | `source_archive=changed` → `CORRUPT`，重新摄入而不是复用 | `test_changed_archive_is_not_assumed_to_be_the_same_dataset` |

同名 zip 能打开不再等于同一 dataset：签名与 manifest 绑定已验证的源版本（`same_name_is_not_same_dataset=true`）。缺源状态不会被修成 PASS。数据不进公开 git。

## B 总验收使用 PASS 白名单

`summarize_stages` 改为白名单：required 每项 status 必须严格等于 `PASS`。缺字段、缺 status、`None`、`SKIPPED`、`PENDING`、任何未知字符串都 fail-closed，并进 `unknown` 桶。输出新增 `observed_status` 与 `not_pass`（逐项 stage/status/reason），保留结构化原因而不是维护错误黑名单。既有「已知 FAIL / 空表不 accepted」控制测试保留并通过。测试：`test_acceptance_needs_an_explicit_pass_on_every_required_stage`。

## C 最后报价、法律事件、经济结算三分

- `_quote_bounds` 仍受 `lastpricedate` 约束；`_event_bounds` 不再被它截断，事件窗口可晚于最后报价，上界为「同名后继身份开始前一日」与 `2024-06-28` 的较小者。
- 虚构 EXAMPLE1：最后报价 `2023-06-02`，现金事件 `2023-06-05` 保留；2025 动作被封存区拦下；同名新证券事件不混入。测试：`test_settlement_event_after_the_final_quote_is_kept`、`test_cash_event_after_final_quote_survives_the_full_gate`。
- 裸 ticker 行只有在「另一个同名身份确实覆盖该日」时才排除，并作为 `unresolved_bare_ticker_actions` 保留，不强行归属任一方。
- `bankruptcy_last_trade` 保留为观察事实，但不再是已核验清算值：新增 `settlement_evidence`、`observed_last_quote`，`economic_settlement_blocked_only` 在无 action 结算证据时为 true。
- 现金+股票/选择权保持分量证据，通用 merger 数值不先验当美元现金；纯现金对照仍可核验。
- 规则分版 `delist-terminal-rule-v2` 并给理由：`determinate_terminal`（标签已解析，供 IDENTITY）与 `concrete_terminal`（有 action 结算证据，供 EXECUTION）分开。IDENTITY 门槛仍为 12，未下调；EXECUTION 由结算证据单独把关。离线整链实测：16 个身份全解析、`determinate=16`、`settlement_evidenced=10`，6 个仅有最后报价的破产案例把 EXECUTION 锁在 `UNSUPPORTED`，而 raw 数据层 `DATA_GATE_ACCEPTED`。

## D 历史预算用实际有效日期

流式处理中为每个证券记录每个预热阈值实际达成的日期（`warmup_hits`）与最大缺口（`max_gap_sessions`），不再用「首日 + 日历 need-1」。

- 稀疏观测 Jan 3 / Jan 6 / Jan 11、预热 3：首可评分日为 `2023-01-11`，不是 `2023-01-05`。
- 连续对照 Jan 3/4/5：仍为 `2023-01-05`。
- 只有 first/last/n 的摘要：`status=NOT_COMPUTED`、`securities_without_observation_dates` 计数，不声明已精确核算。
- 逐家族首可评分日、5/20/63 成熟日、末有效信号日与覆盖数分别输出；`pool_earliest_is_not_every_security_ready=true`。
- 缺报价不改变独立日历的 T+H，只影响是否可评价。
- 未改动任何算法预热门槛（`FAMILY_WARMUP_SESSIONS` 不变；`warmup` 仅为测试可注入的短依赖）。

测试：`test_warmup_date_counts_valid_observations_not_calendar_offset`、`test_gapped_history_budget_does_not_report_a_contiguous_first_score_day`、`test_pipeline_records_the_date_each_warmup_count_was_reached`。

## E 真实证据与停止条件

- 新增 `tests/test_research_eod_v1_closeout_gate.py`（12 项），按真实函数写，不是草案伪代码。
- 本地全量 pytest：见 `closeout_ci.json`。
- 离线整链（`fake_sharadar_server.py`，合成数据、非真实供应商）：bulk 优先与 `--mode paged` 两种模式均 exit 0 / `DATA_GATE_ACCEPTED`，`EXECUTION` 仍 `UNSUPPORTED`。这是管路验证，不替代真实取数。
- 真实运行环境 `credential_present=false`：四表 0 行、0 次真实请求、终态 `AUTH_REQUIRED`，未生成 accepted。
- 未追加新算法，未回到 214 池调参，未解封 `2024-07-01`，未改生产/实时/期权/日股/网页。

## 尚未通过项

- 真实小样本（四表、有界日期、非免费权限、历史身份案例）：无凭据，未执行。
- 全量可恢复下载：待小样本通过后由负责人决定。
- 16 身份案例活体核验：本环境为 `fixture_list_only`。
- Yahoo 有效覆盖对账：缓存中 `SPY`/`NVDA` 无 Sharadar 身份可对齐，`INSUFFICIENT`。
- volume session scope：`UNKNOWN` / `UNSUPPORTED`。
- 经济结算层：即使拿到价格，破产类案例在缺结算/可执行退出证据前继续 blocked。
