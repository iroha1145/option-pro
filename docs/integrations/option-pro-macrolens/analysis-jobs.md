# 分析任务

## 公开状态

pending → queued → in_progress → completed

终态还包括 failed、cancelled、insufficient_context 和 budget_blocked。取消请求通过内部 cancel_requested_at 表达，不向界面伪造进度百分比。

## 创建语义

POST /analysis-jobs 请求包含 news_id、expected_content_hash、可选的 expected_change_sequence 和 force。版本前置条件必须在创建事务内与当前新闻核对；不一致返回 409 news_version_conflict，不得排队或调用模型。

已完成时即使 force=true 也返回现有结果；已有活跃任务时返回同一任务；失败或已取消任务只有在 force=true 时才创建新的 job_id。旧任务、Response ID、错误、用量和预算记录保持不变。submission_outcome_unknown 不得自动或强制重提。响应公开冻结的 content_hash、input_hash、model 和 reasoning，但不公开 OpenAI Response ID。内部任务还冻结 execution_mode 与 max_output_tokens，部署切换模式或上调额度不能改变旧任务。

幂等键至少包含 news_id、input_hash、model、reasoning、prompt_version 和 schema_version。相同输入不得重复计费；显式失败重试通过新的追加式执行记录保留完整审计。

Option Pro 自有任务使用 POST /api/ai/jobs/earnings-impact、
POST /api/ai/jobs/option-alerts，以及兼容路径
POST /api/signals/stock/{ticker}/ai-analysis。兼容路径也只返回本地 Job，
不在用户请求内等待模型。

## Background 模式

Worker 创建 background Response，先持久化 OpenAI Response ID，再轮询 queued 和 in_progress。Response ID 只在服务端数据库保存，任何对浏览器和 Option Pro 的响应都不得包含它。

存在 Response ID 的任务只能 retrieve，不得重新 create。轮询窗口结束后保留 ID 和 next_attempt_at，下一轮继续查询。应用重启后优先恢复这些任务。

提交请求已发出但 Response ID 未成功保存时，任务转为 submission_outcome_unknown，不自动重提。

## worker_sync

要求零数据保留时使用 OPENAI_EXECUTION_MODE=worker_sync、background=false、store=false。请求仍只在后台 Worker 内运行；默认超时 900 秒，可配置到 1800 秒。浏览器只轮询本地任务。Worker 中断时标为 worker_interrupted，必须由用户显式重试。

## 取消

取消幂等。所有终态任务收到取消请求时原样返回，不清除错误、用量或审计字段。带 Response ID 的任务调用 responses.cancel，不受当前进程执行模式影响；若完成与取消竞态中上游已完成，严格校验并发布完成结果，不能改写为 cancelled。worker_sync 只能在提交前完成本地取消；请求已经发出后无法向上游撤回，失败或中断通过公开错误码表达，不虚构不存在的能力字段。

## 事务

分析、股票影响、新闻状态、用量、任务终态和租约清理必须在一个数据库事务内提交。校验失败不得发布部分结果。

## 成本门禁

去重和低上下文判断先于排队；默认并发 2、队列 200。每日任务或输出 Token 限额未配置时状态显示 budget_unbounded，不伪装为无限额度。每个已接受的在途任务按冻结的 max_output_tokens 预留额度，完成后以实际输出用量结算；结果未知或取消未确认时不释放预留。用户请求可提高优先级，但不能绕过总预算。
