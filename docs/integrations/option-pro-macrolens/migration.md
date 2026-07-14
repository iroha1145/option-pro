# 数据迁移

## 原则

迁移采用新增表与字段、幂等分批回填、核对、切换读取、稳定后清理的顺序。不得在启动时做破坏性大表重建。

## MacroLens

本轮数据库迁移号为 `PRAGMA user_version=4`。v4 将股票关联身份与验证状态拆开：`news_ticker_mentions` 只保留自然键身份和当前状态缓存，`ticker_validation_revisions` 追加保存状态变化，`focus_validation_state` 保存有界重验证游标与统计。模型关联绑定产生它的 `analysis_revision_id`，新分析版本不会删除旧关联。

新增 analysis_jobs、analysis_revisions、analysis_stock_impacts、calendar_analysis_jobs、calendar_snapshots、calendar_event_revisions、integration_changes、integration_nonces、analysis_worker_state 和持久来源健康。

现有 analyses 继续作为旧界面最新投影；新分析追加 revision。旧 affected_stocks JSON 幂等回填，坏 JSON 跳过并记录。旧记录缺失 confidence、horizon 和 mechanism 时使用 0、uncertain、other，并标记 legacy schema，不假装来自 Terra/max。

旧 logic_chain 不作为隐藏推理继续公开；兼容读取映射为有界用户摘要，无法安全映射时留空。

旧 processing 没有 Response ID，迁移回 pending；system 低上下文记录映射为 insufficient_context；failed 保留失败；skipped 不自动重跑，以免产生费用。

数据库 settings 中旧的 default_llm_provider 和 default_llm_model 覆盖在幂等迁移中删除。Terra 队列只采用 Web 与 Worker 共同的环境配置，网页设置接口拒绝再次写入这两个覆盖值。

升级时按自然键合并旧 Mention：保留最早创建时间、最高置信度和最新检查状态。初始验证版本优先使用旧 `validated_at`；缺失时使用迁移时刻，绝不回填到新闻发布时间，并标记 `legacy_backfill`。迁移前未保存的验证变化无法恢复；无法唯一对应分析版本的旧模型关联保留为 `legacy association`，不会猜测归属。迁移失败会整体回滚并恢复外键检查。

## Option Pro

独立 `/data/catalyst-cache.db` 当前升级到 v7；不得改写 `/data/optix.db` 或 `breakout-db-v3`。

缓存继续包含同步 Run、水位、Staging、原始新闻、追加分析、股票影响、日历版本、来源健康、本地任务、刷新 Outbox、Worker 状态和单实例锁。v5 新增按完成交易日与算法版本隔离的 `focus_daily_strength_snapshots`；v6 为该派生缓存补齐负载摘要、覆盖率、各算法版本、数据截至时间和租约隔离字段。v7 新增 `catalyst_analysis_projections` 与 `catalyst_stock_impact_projections`：同一远端分析版本按新闻 `change_sequence` 形成独立投影，股票聚合只接受 `canonical` 和 `valid_external`，缺失验证一律失败关闭。原始分析和 `stock_validations` 继续完整保留在分析投影中。

v6→v7 在 `BEGIN IMMEDIATE` 内读取现有 `catalyst_item_revisions.raw_json`，重建可信投影并执行 `quick_check`、`integrity_check` 和 `foreign_key_check`，最后才切换 Schema 元数据。迁移统计保存在 `catalyst_projection_migration_stats`。坏 JSON 只计数并保留原始新闻；迁移不访问 MacroLens 或模型，不推进 Feed 水位，失败会完整回滚。相同远端分析版本和序列若出现不同负载，将返回 `projection_payload_conflict`，保留旧快照并进入有界重新同步。

Focus Producer 的有效快照期限为 `FOCUS_CONTEXT_REFRESH_SECONDS + FOCUS_PRODUCER_SNAPSHOT_GRACE_SECONDS`。默认宽限为 120 秒；宽限期内若 Worker 仍在运行且心跳新鲜，健康状态保持可用并标记 `focus_refresh_in_progress`。

Option Pro 自有 AI Job 使用独立 /data/ai-jobs.db。

## 备份

迁移前使用 SQLite Backup API，或停写、checkpoint 后完整保存 db、wal 和 shm。备份记录提交、Schema 版本、校验和和时间。

## 验证

覆盖真实旧结构升级、重复执行、故障回滚、坏 JSON、重复股票、历史 as_of、日历 Actual 后到、旧模型覆盖、事务一致性和 foreign_key_check。
