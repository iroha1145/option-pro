# Claude Haiku 5.5 迁移说明

本说明描述本次代码迁移的配置与验收要求，不是生产部署完成记录。真实密钥验证、真实分析与服务器切换须分别确认。

## 新分析配置

在 `config/personal.toml` 中设置：

```toml
[ai]
model = "claude-haiku-5-5"
reasoning = "xhigh"
max_concurrency = 4
execution_mode = "background"
```

Claude 默认最多同时运行 4 个任务，可设置为 1 到 4（包括 3）。手动和定时任务共用这个上限，不是各自 4 个。旧 OpenAI 任务仍保留手动和定时各 1 个的历史规则，旧配置省略 `max_concurrency` 时仍沿用默认值 1，显式设置为 1 也可继续读取；旧模型显式设置为 2 到 4 会被拒绝。

推理强度（Effort）为 `xhigh`，配合自适应思考（Adaptive Thinking）。系统提示使用 5 分钟提示缓存（Prompt Caching），分析使用严格结构化输出（Strict Structured Outputs）；缓存命中仍取决于提示内容、长度和请求间隔。网页搜索（Web Search）、网页抓取（Web Fetch）和代码执行（Code Execution）按任务需要启用，来源与实际用量须保存。提示缓存使用显式断点（Explicit Cache Breakpoints），只缓存重复使用的前缀。[模型说明](https://platform.claude.com/docs/en/models/haiku-5-5/overview)、[推理强度说明](https://platform.claude.com/docs/en/build-with-claude/effort)、[缓存说明](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)、[结构化输出说明](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)。

`background` 表示应用中的后台队列。工作进程（Worker）通过流式调用等待 Claude 完成，不将任务提交为 Anthropic 的远程后台响应。取消和超时必须结合实际任务状态判断；供应商是否接收请求无法确定时，系统保留未知状态，避免自动重复收费。

## 工具与最终结果

网页搜索（Web Search）与网页抓取（Web Fetch）各设 `max_uses=1`，网页抓取内容预算约 8000 词元。代码执行（Code Execution）在流处理中最多接受 2 次，这是应用本地的止损边界。单次请求暂停、停止或中断后，不自动发下一请求续跑；无法确认供应商接收结果时，也不重新提交同一分析。

最终结果始终使用 JSON 输出格式（JSON Output Format），启用工具时仅提供原生网页搜索、网页抓取和代码执行工具。此组合已通过实际请求验证；这项验证不代表用户提供的文档或搜索结果所启用的引用（Citations）也可与 JSON 格式共用。只提取最后一次原生工具调用和结果之后的文本作为最终 JSON，且必须正常结束、工具完整配对并通过原有中文与业务校验。来源与实际工具请求用量随原任务保存；已完成结果旁最多显示 10 个“核对来源”链接。重分析任务不会覆盖旧结果的来源。思考正文、中间工具内容与供应商消息编号不向页面公开。

## 密钥与旧任务

在服务器上通过隐藏输入录入独立密钥：

```bash
./personal.sh secrets set ANTHROPIC_API_KEY
./personal.sh secrets status
./personal.sh secrets validate
```

密钥保存到权限为 `0600` 的 `secrets.env`；状态和验证结果不输出密钥值。验证只读取 Anthropic 官方模型列表，不发送付费分析请求。不得配置中转地址；非空 `ANTHROPIC_BASE_URL` 会使运行配置校验失败。[模型列表接口](https://platform.claude.com/docs/en/api/models/list)。

保留 `OPENAI_API_KEY`，供迁移前已送出的任务取回、取消及回滚使用。迁移前的 `gpt-5.6-terra/max` 个人配置仍可读取；升级不会静默重写这份文件，发布时须明确修改上述模型与推理强度，并保存原配置备份。旧结果继续保留自己的模型身份；页面缺少模型信息时不会补上当前默认值。

## 用量与费用

不同模型对同一段文本的词元（Token）计数可能不同，不能将旧用量当作新模型的准确估算。Claude 的思考输出也计入输出用量并收费，即使思考正文没有显示在页面上。[思考与费用说明](https://platform.claude.com/docs/en/build-with-claude/thinking)。

应用分别记录普通输入、缓存读取和缓存写入。公开用量中的 `input_tokens` 是输入总量，已包含缓存读取和写入；`cached_input_tokens` 与 `cache_creation_input_tokens` 是其中的部分，不能再加到总量上。写入细分保留为 `cache_creation_5m_input_tokens` 和 `cache_creation_1h_input_tokens`；未返回的用量保持缺失，不推算为零。工具请求分别记录为 `web_search_requests`、`web_fetch_requests` 和 `code_execution_requests`，不能将请求次数当作词元数。

启用 `[model_budget].daily_budget_usd = 9.5` 后，Haiku 与 Opus 共用应用日预算，词元上限只用于统计。每轮请求先预留估算费用，已报费用完整对账，未知预留继续占用所属窗口；这不保证供应商账单硬性封顶。预算按 UTC 日界、东京 09:00 重置。设置 `accounting_start_at` 后，首次窗口从实际生效时刻起算；本次起算前的 6 条未知记录保留但不占新窗口，仍不得重复付款。旧 `[ai].daily_budget_usd` 不承担共享预算，市场研判的 `daily_max_runs = 6` 仍是独立安全限制。详见[市场综合研判说明](../market-brief.md)。

## 发布验收

发布前保存数据库、运行设置、个人配置和旧密钥的回滚备份，清查旧任务状态。完成本地与持续集成（CI）检查后，还需确认服务器实际运行的提交、新密钥的官方只读验证，以及一笔经授权的真实分析从排队、完成到持久保存和页面刷新全程一致。检查旧结果的模型标注、缓存用量和共享主机其他服务，才能记录切换完成。
