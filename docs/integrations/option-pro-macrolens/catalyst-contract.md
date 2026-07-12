# Catalyst Integration v1 契约

路径前缀：/api/integrations/option-pro/v1  
契约文件：contracts/macrolens-option-pro-v1.json  
所有模型 extra=forbid、allow_inf_nan=false，时间带时区并统一为 UTC。

## 端点

- GET /health
- GET /feed
- GET /latest
- GET /news/{news_id}
- GET /catalysts/{ticker}
- POST /catalysts/batch
- GET /calendar
- POST /analysis-jobs
- GET /analysis-jobs/{job_id}
- POST /analysis-jobs/{job_id}/cancel

## Feed

GET /feed 支持 as_of、window_hours、limit、cursor、source、classification、min_confidence、min_abs_impact 和 analysis_status。

每项包含 news_id、content_hash、source、title、summary、url、published_at、fetched_at、updated_at、source_tickers、analysis_status、analysis、analyzed_at、available_at 和 is_stale。

未分析新闻可返回，但 analysis、analyzed_at 和 available_at 为 null；不得从原始新闻派生 classification、impact_score 或 confidence，也不得生成中性方向。

## Health 与来源状态

GET /health 的 sources 每项固定包含 status、last_attempt_at、last_success_at、data_through、consecutive_failures、next_attempt_at、raw_count、inserted_count、duplicates_count 和 detail。计数字段表示最近一次真实抓取；disabled、not_configured 或从未尝试时为 null，不用 0 假装已执行。

## 单股与批量

GET /catalysts/{ticker} 支持 as_of、window_hours、limit、cursor、min_confidence 和 include_neutral。

POST /catalysts/batch 最多 50 个股票代码，整批共用 as_of，每只股票返回独立 active、empty、stale 或 unavailable 状态。批量查询是只读操作，使用 read key。

## 增量同步

GET /latest 支持 updated_after、cursor 和最多七天窗口。响应必须带：

- snapshot_token
- data_through
- next_updated_after
- next_cursor
- has_more
- 每项 updated_at 和 change_sequence

游标绑定 snapshot_token、筛选摘要和最后排序键。完整分页成功前，Option Pro 不推进本地水位；水位使用 change_sequence，updated_after 仅用于兼容和重叠恢复。

空的完整快照也返回权威 next_updated_after，并推进到该快照冻结的 as_of；存在未完成分页时只能推进到当前页最后一项，不能越过尚未读取的变更。

## Calendar

GET /calendar 支持 date_from、date_to、as_of、currencies 和 min_impact。事件保留 forecast、previous、actual、is_stale、source_fetched_at 和 available_at。Actual 的后到更新生成新版本，不覆盖历史。

## 错误

错误体含 code、message、retryable、retry_after_seconds 和 request_id。401/403 不盲重试；429 遵守 Retry-After；5xx 和网络错误有限退避；Schema 版本或摘要不匹配时停止发布并保留旧快照。

## 契约校验

MacroLens 从 Pydantic 生成契约；Option Pro 固定字节相同的副本。持续集成校验生成结果、schema_version、schema_sha256、成功样本、错误样本和未知字段拒绝。
