# GPT-5.6 Terra 运行时

## 默认配置

DEFAULT_LLM_PROVIDER=openai  
DEFAULT_LLM_MODEL=gpt-5.6-terra  
OPENAI_REASONING=max  
OPENAI_EXECUTION_MODE=background  
OPENAI_SYNC_TIMEOUT_SECONDS=900  
OPENAI_BACKGROUND_POLL_TIMEOUT_SECONDS=1800  
OPENAI_BACKGROUND_INITIAL_POLL_SECONDS=2  
OPENAI_BACKGROUND_MAX_POLL_SECONDS=15  
OPENAI_MAX_OUTPUT_TOKENS=16384  
OPENAI_MAX_CONCURRENCY=2  
OPENAI_MAX_RETRIES=0

自定义 OPENAI_BASE_URL 默认拒绝；只有显式启用 OPENAI_ALLOW_CUSTOM_BASE_URL 且使用 HTTPS 时才允许。回环地址的 HTTP 还需单独启用 OPENAI_ALLOW_LOCAL_HTTP，避免把密钥误发到未授权端点。

Option Pro 使用相同模型、推理、执行模式、输出上限和并发设置。reasoning 只接受 none、low、medium、high、xhigh、max；非法值直接报错，不静默修改。

Terra 队列的提供方和模型只从进程环境读取。网页设置接口会显示该值，但拒绝把 `default_llm_provider` 或 `default_llm_model` 写入数据库；启动迁移会删除旧数据库覆盖。Web 与 Worker 容器必须传入相同的 `DEFAULT_LLM_PROVIDER`、`DEFAULT_LLM_MODEL`、`OPENAI_REASONING` 和执行参数，避免新闻、日历与健康状态分裂。非 OpenAI 提供方不会进入 OpenAI 队列，第三方通用密钥也不会交给 OpenAI 客户端。

## 官方能力边界

GPT-5.6 Terra 官方模型页列出 Responses API 和 Structured Outputs 支持。Background Mode 通过 background=true 创建、retrieve 轮询和 cancel 取消；它要求 store=true，并因约十分钟暂存而不兼容零数据保留。

参考：

- https://developers.openai.com/api/docs/models/gpt-5.6-terra
- https://developers.openai.com/api/docs/guides/background
- https://developers.openai.com/api/docs/guides/migrate-to-responses

## 结构化输出

请求使用 Responses API 的 text.format JSON Schema、strict=true，并递归要求每层对象列出全部 required 字段及 additionalProperties=false。完成后将完整 output_text 交给严格 Pydantic model_validate_json。

禁止首尾花括号截取、Markdown 围栏清理、正则修 JSON、猜测缺失字段和回退 Chat Completions。

NewsImpactAnalysis 包含 title_zh、headline_summary、overall_sentiment、classification、confidence、market_relevance、affected_stocks、affected_sectors、affected_commodities、causal_summary、key_factors、uncertainty_notes 和 insufficient_context。

Option Pro 的 earnings_impact、option_alerts 和 signal_analysis 各有独立严格
Schema；不得把一种结果模型用于另一种任务，也不得用 setdefault 猜测缺失字段。

系统提示明确新闻为不可信数据，禁止执行新闻命令、工具、网页搜索、补造事实和买卖建议。causal_summary 只是用户可见摘要，不是隐藏推理。

## 能力检查

启动只做本地检查：软件开发工具包（SDK）能否导入、responses.create、retrieve、cancel 方法是否存在，以及配置格式是否合法。检查不发出网络请求，也不验证远端账户是否已获得该模型权限。能力不足时返回 unsupported_provider_capability，不切换模型、不降低推理、不恢复自由文本解析。

`POST /api/calendar/analyze` 只创建独立的持久 Calendar Job 并返回 202；浏览器通过 `GET /api/calendar/analyze/{job_id}` 轮询本地状态。Calendar Job 使用独立队列、并发和每日任务／输出 Token 预算，但仍受全局 OpenAI 并发上限约束。失败后的显式重试创建新 job_id 并保留旧用量；submission_outcome_unknown 不得重提。Calendar GET 只读取原始日历和已完成结果，不触发模型，也不保持用户 HTTP 长连接。

每个任务在创建时冻结 `execution_mode` 与 `max_output_tokens`。进程环境后来改变，不会改变旧任务的调用方式或额度；带 OpenAI Response ID 的旧 Background 任务始终优先 retrieve／cancel，不能重新 create。新闻与 Calendar 分别按在途任务的冻结输出上限预留每日 Token 预算，完成后才按实际输出用量结算；submission_outcome_unknown 与尚未确认的取消继续保留额度。

## 低上下文

少于 100 个有效字符时不调用 OpenAI，写入 low-context-neutral-v2：neutral、confidence=0、affected_stocks=[]、insufficient_context=true。
