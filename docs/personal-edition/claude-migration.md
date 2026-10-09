# 分任务模型与费用说明

本说明描述本次代码迁移的配置与验收要求，不是生产部署完成记录。真实密钥验证、真实分析与服务器切换须分别确认。

## 新分析配置

在 `config/personal.toml` 中设置：

```toml
[ai]
model = "claude-haiku-5-5"
reasoning = "xhigh"
news_model = "gpt-5.6-luna"
news_reasoning = "max"
market_focus_model = "claude-sonnet-5-5"
market_focus_reasoning = "xhigh"
max_concurrency = 4
execution_mode = "background"
```

新闻翻译与分析使用 GPT-5.6 Luna，推理强度为 `max`；热点使用 Claude Sonnet 5.5，推理强度为 `xhigh`。财报、期权和信号任务继承 Haiku 配置。首页盘前、盘后研判继续由 `[market_brief]` 的 Opus 5.5、`xhigh` 生成，`code_execution_tool = true`，同时启用搜索与网页抓取。缺少分任务配置的旧配置仍继承全局模型。

所有后台分析共用最多 4 个执行槽，OpenAI 任务在其中最多占 1 个；手动和定时任务共同计数。旧 OpenAI 全局配置省略 `max_concurrency` 时仍沿用默认值 1。已经发送的任务按照记录中的模型取回、恢复和结算，不因配置切换重新提交。

推理强度（Effort）为 `xhigh`，配合自适应思考（Adaptive Thinking）。系统提示使用 5 分钟提示缓存（Prompt Caching），分析使用严格结构化输出（Strict Structured Outputs）；缓存命中仍取决于提示内容、长度和请求间隔。网页搜索（Web Search）、网页抓取（Web Fetch）和代码执行（Code Execution）按任务需要启用，来源与实际用量须保存。提示缓存使用显式断点（Explicit Cache Breakpoints），只缓存重复使用的前缀。[模型说明](https://platform.claude.com/docs/en/models/haiku-5-5/overview)、[推理强度说明](https://platform.claude.com/docs/en/build-with-claude/effort)、[缓存说明](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)、[结构化输出说明](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)。

`background` 表示应用中的后台队列。工作进程（Worker）通过流式调用等待 Claude 完成，不将任务提交为 Anthropic 的远程后台响应。取消和超时必须结合实际任务状态判断；供应商是否接收请求无法确定时，系统保留未知状态，避免自动重复收费。

## 工具与最终结果

Luna 新闻可使用实时网页搜索（Web Search），每次分析最多调用 3 次工具。未取得正文时必须尝试联网补查同一事件，优先寻找原文、公司公告和监管来源；没有可靠来源时保留“资料不足”，不补造正文或市场影响。页面按实际输入显示正文、标题摘要及联网来源，并提供可点击的来源链接。搜索费用按真实搜索次数计入共享账目，打开网页与页内查找不重复计搜索费。工具失败或用量不明时保留费用预留。终态回执先保存，再处理和展示结果；旧的无联网结果仍可读取，不会因为启用搜索而自动重新付费。[联网工具说明](https://developers.openai.com/api/docs/guides/tools-web-search)。

热点启用网页搜索（Web Search）、网页抓取（Web Fetch）和代码执行（Code Execution），搜索和抓取各最多 12 次；其他 Haiku 任务保留原来的工具边界。Opus 研判保留搜索 10 次、抓取 8 次的边界，并开放独立代码执行。服务端工具暂停可以在同一执行时限内续跑；请求结果未知或进程重启后，不重新发送可能已计费的请求。

Sonnet 热点使用独立版本的输入和结果结构。每个候选事件都必须给出核验结论，并绑定事件版本及本次真实网络工具回执；自由文本引用、错误回执和仅执行代码均不能充当联网证据。公开热点只采用有支持证据的事件，无法核实或被证据否定的事件不会进入热点、摘要、行业描述或股票评估。全部无法核实时，任务可以正常完成并显示空列表；旧的未核验内容不会补入新热点列表。旧周期保留为“历史分析”。

核验提示要求来源支持新闻的核心主体、时间、数值和统计口径；背景材料、自行估算或改写后的较弱说法不能替代原断言。程序只将已知字段名、速率单位和中文标题序号转为中文叙述，保留原回执和事实字段。格式及来源绑定通过，并不等于内容已经全面核实；仍需区分来源所述事实与影响推断。

公开摘要由通过核验的事件段落组成，来源最多展示 10 个链接，不展示详细核验过程。原始模型回复、工具证据索引、各轮用量和费用保留在内部记录。结构检查能确认引用来自本次调用，但事实判断仍由模型结合来源完成；不把引用格式正确等同于事实绝对可靠。

## 密钥与旧任务

在服务器上通过隐藏输入录入独立密钥：

```bash
./personal.sh secrets set ANTHROPIC_API_KEY
./personal.sh secrets status
./personal.sh secrets validate
```

密钥保存到权限为 `0600` 的 `secrets.env`；状态和验证结果不输出密钥值。验证只读取 Anthropic 官方模型列表，不发送付费分析请求。不得配置中转地址；非空 `ANTHROPIC_BASE_URL` 会使运行配置校验失败。[模型列表接口](https://platform.claude.com/docs/en/api/models/list)。

沿用已有 `OPENAI_API_KEY` 和 `ANTHROPIC_API_KEY`，分别用于 Luna 新闻和 Claude 分析。迁移前的 `gpt-5.6-terra/max` 个人配置仍可读取；发布时保存原配置备份。旧结果继续保留自己的模型身份；页面缺少模型信息时不会补上当前默认值。修复已有完整回执只重新解析和保存，不重新生成内容。

## 用量与费用

不同模型对同一段文本的词元（Token）计数可能不同，不能将旧用量当作新模型的准确估算。Claude 的思考输出也计入输出用量并收费，即使思考正文没有显示在页面上。[思考与费用说明](https://platform.claude.com/docs/en/build-with-claude/thinking)。

应用分别记录普通输入、缓存读取和缓存写入。公开用量中的 `input_tokens` 是输入总量，已包含缓存读取和写入；`cached_input_tokens` 与 `cache_creation_input_tokens` 是其中的部分，不能再加到总量上。写入细分保留为 `cache_creation_5m_input_tokens` 和 `cache_creation_1h_input_tokens`；未返回的用量保持缺失，不推算为零。工具请求分别记录为 `web_search_requests`、`web_fetch_requests` 和 `code_execution_requests`，不能将请求次数当作词元数。

设置 `[model_budget].daily_budget_usd = 10.0`、`enforce_limit = false` 时，所有模型共用每日 10 美元统计参考，超过后继续运行，不再设置分类金额门槛。账目包括 Luna、Sonnet、Haiku、原 Terra 任务与 Opus 研判，按各模型价格估算；未知请求继续保留预留。OpenAI 未报告缓存写入明细时，按较高的写入单价估算未命中缓存的输入，这不是供应商最终账单。

预算按 UTC 日界、东京 09:00 重置。原 `accounting_start_at` 保持不变，起算前费用和未知记录保留但不计入新窗口；调整模型或参考金额不会清零。输出量、任务时限、并发和市场研判的每日 6 次运行限制继续生效。详见[市场综合研判说明](../market-brief.md)。

## 发布验收

发布前保存数据库、运行设置、个人配置和旧密钥的回滚备份，清查旧任务状态。完成本地与持续集成（CI）检查后，还需确认服务器实际运行的提交、新密钥的官方只读验证，以及一笔经授权的真实分析从排队、完成到持久保存和页面刷新全程一致。检查旧结果的模型标注、缓存用量和共享主机其他服务，才能记录切换完成。
