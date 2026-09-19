# Sharadar 接通与验收回传

当前 head 由 git 记录。feature version 仍为 `us-eod-research-features-v1.6`，旧 B0 不按新定义重算。
旧 B0/R1/R1b/R2/Freeze/固定矩阵标为 `CONTROL_CURRENT_LIST_214`，不回写历史 JSON。

credential_present=false。
live_sharadar_request_count=0。
未读取聊天凭据，未向 Nasdaq Data Link 或 Massive 试送密钥，未回退 Yahoo。
官方渠道为 `https://api.sharadar.com/v1.0/data/<table>`；跨域签名下载不再附 key。
分页先持久提交页再推进游标；max_pages/中断为 PARTIAL。HTTP 200 error/HTML 与 503 不能当 READ_OK。

日期表请求区间固定为 from=2010-01-01 to=2024-06-28；主表不臆造日期参数。
探针是否允许全量下载：{"allowed": false, "blockers": ["credential_absent"], "empty_page_is_not_sample_proof": true}。

四表状态：{"stocks": {"status": "AUTH_REQUIRED", "row_count": 0, "pages": 0, "complete": false, "session_row_count": 0, "request": {"extra": {"from": "2010-01-01", "to": "2024-06-28"}, "limit": 10000, "date_bound": true, "plan_version": "sharadar-request-plan-v1", "source_version": "api.sharadar.com/v1.0", "shard": "pinned_from_to", "order_not_assumed_dedupe_by_primary_key": true}, "download_started": false, "not_started_reason": "credential_absent"}, "funds": {"status": "AUTH_REQUIRED", "row_count": 0, "pages": 0, "complete": false, "session_row_count": 0, "request": {"extra": {"from": "2010-01-01", "to": "2024-06-28"}, "limit": 10000, "date_bound": true, "plan_version": "sharadar-request-plan-v1", "source_version": "api.sharadar.com/v1.0", "shard": "pinned_from_to", "order_not_assumed_dedupe_by_primary_key": true}, "download_started": false, "not_started_reason": "credential_absent"}, "tickers": {"status": "AUTH_REQUIRED", "row_count": 0, "pages": 0, "complete": false, "session_row_count": 0, "request": {"extra": {}, "limit": 10000, "date_bound": false, "plan_version": "sharadar-request-plan-v1", "source_version": "api.sharadar.com/v1.0", "shard": "full_master_no_date_param", "order_not_assumed_dedupe_by_primary_key": true}, "download_started": false, "not_started_reason": "credential_absent"}, "actions": {"status": "AUTH_REQUIRED", "row_count": 0, "pages": 0, "complete": false, "session_row_count": 0, "request": {"extra": {"from": "2010-01-01", "to": "2024-06-28"}, "limit": 10000, "date_bound": true, "plan_version": "sharadar-request-plan-v1", "source_version": "api.sharadar.com/v1.0", "shard": "pinned_from_to", "order_not_assumed_dedupe_by_primary_key": true}, "download_started": false, "not_started_reason": "credential_absent"}}。
原始下载状态 `AUTH_REQUIRED`；下载完成不等于验收通过。
分层状态：{"AUTH": "AUTH_REQUIRED", "TABLE_ACCESS": "AUTH_REQUIRED", "DOWNLOAD": "AUTH_REQUIRED", "TRANSFORM": "AUTH_REQUIRED", "IDENTITY": "AUTH_REQUIRED", "RECONCILE": "INSUFFICIENT", "HISTORY": "AUTH_REQUIRED", "VOLUME_SCOPE": "UNSUPPORTED", "EXECUTION": "UNSUPPORTED"}。
预登记必需项：["AUTH", "TABLE_ACCESS", "DOWNLOAD", "TRANSFORM", "IDENTITY", "RECONCILE", "HISTORY"]；accepted=false。

16 个身份案例按永久身份与事件年份解析；无行时为 fixture_list_only / AUTH_REQUIRED。
Yahoo 对账状态 `INSUFFICIENT`；对照源 {"kind": "captured_bar_cache", "available": true, "paths": [{"path": "/workspace/research/option_pro_us_eod_v1/data/cache/offline_replay/daily_bars.parquet", "rows": 10, "mapped_rows": 0}], "identity_alignment": "cache_ticker_to_sharadar_permaticker", "unmapped_cache_ids": ["NVDA", "SPY"], "unmapped_n": 2, "rows_outside_allowed_window": 0, "return_basis": "total_return_index_tri_vs_sharadar_closeadj", "volume_basis": "tape_volume_from_split_volume_and_raw_close_ratio", "yahoo_is_not_truth": true, "live_yahoo_request": false}。
volume scope `UNSUPPORTED`。
历史预算状态 `AUTH_REQUIRED`；覆盖不等于授权，权限状态 `AUTH_REQUIRED`，authorized_range_unknown=true。
转换跳过行 0 条，逐条记录主键与原因。

终态：`AUTH_REQUIRED`。这不是策略赢家状态。全市场选优未启动。
