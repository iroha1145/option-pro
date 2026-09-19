# CONNECT_AND_ACCEPT 完成审计

实现 head: `02fa0a2ceb19f274ed81777a57936c1853c45a1f`
PR: https://github.com/iroha1145/option-pro/pull/176
credential_present: false
live_sharadar_request_count: 0

## 规格对照

| 要求 | 权威证据 | 判定 |
| --- | --- | --- |
| 无 key 不请求 / AUTH_REQUIRED | replay `no_key_no_request`；本环境 `credential_present=false` | 已证 |
| error envelope 不当空表 | replay + `test_valid_page_and_error_envelope` | 已证 |
| 503 bulk 不晋级 | `test_bulk_http_error_and_html_not_committed` | 已证 |
| bulk_status 非 2xx | 同上 `NETWORK_UNAVAILABLE` | 已证 |
| 截断 zip / HTML 不晋级 | 同上 | 已证 |
| max_pages=PARTIAL | replay `page_limit_is_explicitly_partial` | 已证 |
| resume 保留 10002 前缀 | replay `resume_preserves_prior_durable_pages` | 已证 |
| 10000+2 完整页 | replay `uninterrupted_pagination_control` | 已证 |
| 崩溃前后 / HTTP 中断 / query mismatch | `test_checkpoint_query_change_and_crashes` 与 `test_http_error_mid_pagination_keeps_prefix` | 已证 |
| 缺失对账 INSUFFICIENT | replay `reconcile_missing_fields_not_pass` | 已证 |
| 19+NaN/None 回退同轨 | replay `finite_parent_count` | 已证 |
| B 首日不受当前 rvol=None 取消 | replay `B_first_day_no_current_dependency` | 已证 |
| B 首日非有限拒绝 | `test_setup_b_rejects_nonfinite_first_day_inputs` | 已证 |
| mock 四表 client→落库→验收 | `test_mock_four_table_pipeline` | 已证 |
| 数据门不硬编码空表 | `run_sharadar_data_gate.py` 已改为 `execute_data_gate` | 已证 |
| ACCESS_TESTED 仅成功请求后 | `test_provider_access_tested_only_after_success` | 已证 |
| key 只附官网 HTTPS | `test_api_key_stays_on_official_https_origin_only` | 已证 |
| listed_at 不塞 firstpricedate | `test_actions_keep_non_numeric_and_identity_not_listed_at` | 已证 |
| action value 不改 0.0 | 同上 | 已证 |
| ADV20 不含 T | `unadj_adv20` 只用 `session_date < T` | 已证 |
| FEATURE_VERSION v1.6 | `__init__.py` + 测试 | 已证 |
| 不回退 Yahoo | pack `yahoo_fallback_used=false` | 已证 |
| 16 案例无实行是清单 | `delist_verification` 全 `fixture_list_only` | 已证 |
| 本地全量 pytest @ 02fa0a2c | `3990 passed, 6 skipped` | 已证 |
| 当前实现头 GitHub CI | push 35419099251 / PR 35419101191 均为 success | 已证 |

## 环境限制（精确终态，不是失败掩盖）

- 真实 Sharadar 样本与完整下载：无 `SHARADAR_API_KEY`，不能称 READ_OK。
- 16 身份案例真实核验：无实行。
- Yahoo live 对账：无重叠样本，AUTH_REQUIRED。
- volume scope：UNKNOWN / UNSUPPORTED。
- 数字历史预算：无实际日期，AUTH_REQUIRED。
