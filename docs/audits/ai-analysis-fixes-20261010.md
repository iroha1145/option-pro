# 模型新闻分析与热点追踪报错修复（2026-10-10）

分支 `claude/ai-fixes-2026-10-10`，基于新闻分支 `claude/news-ingest-2026-10-09` 的头 `82e2312d`（最初在性能分支旧头 `1cbf7915` 上开发，2026-10-10 用 `git rebase --onto 82e2312d 1cbf7915` 变基，没有冲突）。取证文件在性能工作区的 `evidence/ai-errors-2026-10-10.txt` 与 `evidence/ai-failed-samples-2026-10-10.json`（2026-10-09 17:2x UTC 的只读快照）。生产运行 `72f426f0`。

| 提交 | 内容 |
| --- | --- |
| `6af90150` | 中文校验器的误判修正 |
| `10a7db09` | 输出上限、等待时限、日额度放宽，以及旧身份任务的放行规则 |
| `8e10dad9` | Luna 只在没拿到正文时联网 |
| `86a05b61` | 找回工具按时间批量重验本地回执 |
| `3f4507ca` | 热点周期失败时保留原因 |

分支开了 PR #237。独立审查之后追加了 10 个修正提交，复核之后又追加了 10 个，另有一个按要求收窄 N2 的补充提交，见「审查后的修正」与「复核后的修正」两节；下文各节写的都是修正后的行为。

## 结论

- 新闻分析失败的大头是中文校验器的规则把合规内容判成英文：7 天内 news_impact 因 `english_prose_not_allowed` 失败的有 Haiku 395 条、Terra 272 条、Luna 168 条，取证里能完整重放的 10 条 Luna 失败全部属于这种情况。
- Luna 被拒的片段几乎都是「(globenewswire.com)」这类括号域名。它们不是模型直接写的：模型写的是 Markdown 链接 `([sec.gov](https://www.sec.gov/…))`，我们自己的引用归一化要求标签与链接主机名完全相等，`sec.gov` 对不上 `www.sec.gov`，于是把链接退化成「(sec.gov)」，再被校验器拒掉。
- 修复后，取证里 10 条能完整重放的 Luna 失败回执全部通过新校验；热点周期样本里「股份5000万美元」等片段也通过。这些付费结果部署后可以用找回工具重新入库，不再花钱。
- Luna 现在只在任务没有正文时带联网工具；有正文的任务请求里不带工具，也不预留搜索费。
- 改动移动了 Luna、Terra 的结构身份（schema identity），但用确切的「上一版身份」名单放行，队列里的任务不会整批作废。

## 取证数字（最近 72 小时，非完成任务）

| 任务 | 状态与错误码 | 模型 | 条数 |
| --- | --- | --- | --- |
| news_impact | failed / schema_validation_failed | Haiku | 458 |
| news_impact | budget_blocked / daily_token_limit_reached（全部在 10-08） | Haiku | 387 |
| news_impact | failed / runtime_configuration_changed（10-09 09:12Z 一批） | Haiku | 363 |
| news_impact | failed / schema_validation_failed | Luna | 171 |
| news_impact | failed / schema_validation_failed | Terra | 110 |
| earnings_impact | failed / schema_validation_failed | Haiku 11、Terra 8 | 19 |
| news_impact | failed / submission_outcome_unknown | Haiku | 8 |
| news_impact | failed / news_identity_mismatch | Luna 4、Terra 3 | 7 |
| market_focus | failed / schema_validation_failed | Haiku 2、Sonnet 2 | 4 |

完成任务的输出词元（7 天，news_impact，4,826 条）：中位数 2,907，p90 6,950，p99 12,027，最大 25,392；`provider_incomplete_max_output_tokens` 1 条。

Luna 被拒片段（7 天）：globenewswire.com 31、zacks.com 9、tradingview.com 8、marketscreener.com 7、article 7、investing.com 6、sec.gov 5、internazionale.it 4、newswire.ca 3、http 3、HTTP 401 3、iPhone 18 Pro 2、A 2，其余各 1–2 条（多为域名，另有 unsupported、source）。

## 逐类说明

校验器原则沿用 2026-08 的教训：找到具体拒绝分支修语义，不加实体白名单；1–5 位全大写代码与证券语境仍要求代码绑定，中文输出要求不变。回归测试都在 `tests/test_ai_analysis_fixes_20261010.py`，Luna 样本原样存在 `tests/fixtures/ai_luna_news_failures_20261009.json`。

### 1. 括号里的来源域名（Luna 主因）

- 根因：见结论第二条。归一化在 `runtime._normalize_luna_news_citations`。
- 修法：标签按点号边界比对主机名（`sec.gov` 能匹配 `www.sec.gov`，`ec.gov` 不能），对得上的链接整段去掉；括号里受信的裸网址同样去掉；不在联网记录里的网址照旧以 `ai_news_unbound_or_unhandled_url` 拒绝，不为了过中文校验抹掉未核实的来源。括号里只写域名的来源标注（含「来源：」前缀、多个域名用顿号分隔），只有每个域名都与本次联网工具实际取回的来源主机按点号边界一致时才去掉。中文校验器本身不删除任何括号内容，但括号里的主机名不算「中文术语（外文标注）」，会被拒绝。主机名指以 www. 开头、或最后一段是 com、net、org、gov 等 25 个常见小写顶级域名之一的写法，可带路径，例如「（reuters.com）」「（www.nvidia.com/zh-cn）」「（GlobeNewswire.com）」；「（node.js）」「（Character.AI）」不算主机名。
- 测试：10 条生产回执重放；标签与链接不符时留下的「（ec.gov）」被拒；可信与不可信裸网址；不属于本次联网来源的括号主机名被拒而不是被删。

### 2. 字段名回显（article、source、http、unsupported）

- 根因：模型复述输入字段名与状态值，例如「输入article标记为……」「article_reason为http_403」「unsupported_encoding」。原有翻译只覆盖少数几个名字，且 `article` 只在正文不可用时翻译。
- 修法：新闻任务里，与本条载荷字段名、正文字段名、结果字段名或本条状态值逐字相同、而且在固定对照表里的词，译成中文后再做完整的中文校验。article、article_status、article_reason、text 译成新闻正文、正文状态、正文缺失原因、正文；source、url、title、summary 译成来源、网址、标题、摘要；confidence、classification、insufficient_context 译成置信度、判断类别、证据不足；状态值 available、unavailable、truncated、unsupported_encoding 等译成可用、不可用、已截断、编码不支持，`fetch_failed` 译成「抓取失败」，`http_403` 译成「状态码403」。译不掉的英文照旧拒绝；一个汉字都没有的文本不翻译，直接交给中文校验拒绝（「title summary source url」不会变成中文词串发布）。`my_article_status` 这类近似写法、「股票代码allowed_tickers」、对不上本条输入的 `http_404` 也照旧拒绝；其他任务类型不做这项翻译。Luna 提示词同时要求不照抄字段名。
- 测试：发布文本逐字断言为正确的中文；带正文时「article text truncated, source title available…」这类英文句子、不是本条状态值的 `available`、单独的 `status` 照旧被拒。

### 3. 状态码 HTTP 401

- 根因：只有 Luna 路径会把「HTTP 403」译成中文，且只认 403。
- 修法：状态码与本条输入的抓取失败（`article_status=unavailable`、`article_reason=http_NNN`）一致时译成「未获授权（状态码401）」这类说法，所有新闻模型通用，个股与商品理由也覆盖；不一致时照旧拒绝，因为那是与事实不符的复述。
- 测试：401 通过；输入是 403、没有失败记录或状态不对时拒绝；理由字段里的 403 被翻译。

### 4. 产品名与公司名（Hexa Creation、iPhone 18 Pro）

- 来源绑定已经包含正文：`_validation_source_texts` 收集标题、摘要、来源和 `article.text`，没有缺口。
- Hexa Creation 的真正原因：「拟收购Hexa Creation全部已发行及流通股份；Hexa Creation聚焦……」，分号前的「股份」被当成后一个名称的证券前缀，要求代码绑定。修法：分号结束证券前缀语境，逗号仍算同一分句。
- iPhone 18 Pro：标题里有这个机型时本来就能通过；被拒的两次是名称只出现在联网结果里。修法：已认可产品线加版本号后允许跟型号档位（Pro、Max、Plus、Ultra、Mini、Air、SE、Ti）。
- 测试：生产原句通过；「股票；TSLA上涨」「流通股份，Hexa Creation股价上涨」、Galaxy 18 Pro、未知档位照旧拒绝。

### 5. 标题里的 CPI

- 根因：不是标题路径的问题。「美国9月CPI上涨0.6%」里 CPI 后面紧跟「上涨」，被当成个股行情，要求代码绑定；CPI 出现在输入标题里时还会先走来源绑定分支，那里没有指数例外。
- 修法：沿用指数代码的规则，CPI、PCE、PPI、PMI、GDP、NFP、ISM、JOLTS 后接涨跌说的是指标本身，两个分支都适用。WTI、ADP、LNG 同时是股票代码，不在其中；后接「股价」「股票」仍要求绑定。

### 6. 字母评级、利率基准与通用缩写

- 「A+每股收益修正量化评级」：单个字母后接 +/- 与评级词视为等级；「A股价」「A公司」照旧拒绝。
- 「复合SOFR加1.730%」「SOFR上涨5个基点」：SOFR、LIBOR、EURIBOR、SONIA、ESTR、TONA、HIBOR、SHIBOR 这 8 个基准利率名与 CPI 等宏观指标代码走同一条例外，后接加点或涨跌说的是利率本身。证券语境（「股票代码SOFR」「SOFR股价」）仍要求绑定；代码后接公司、集团、企业时指的是公司，也要求绑定（「CPI公司股价下跌」原先能通过）。「盘前TSLA +3.5%」这类股票代码加涨跌幅照旧要求绑定。
- 「p<0.001」「n=712」「p值」：小写的 p、n、r、t、k、d 接比较号、等号或「值」等算统计量写法；大写的 P、N 接比较号再接数字、数字后不接币种时也算（「P<0.001」「N=712例」）。其他大写字母接比较号（「F>12美元」，F 是福特汽车的代码）、「P<10美元」照旧要求绑定。
- URL（「输入URL」）并入通用技术缩写。IT 不进通用名单，只有后接服务、支出、行业、系统、板块、部门、基础设施、预算、投入、架构、运维、人员、资产、解决方案且不在证券语境时放行（IT 也是高德纳的股票代码）。
- 涨跌词表保持原来的上涨、下跌、涨停、跌停、走强、走弱、收涨、收跌。第一轮审查后一度补了 14 个词，复核发现会误伤「IV飙升」「RSI反弹」这类市场术语，已撤回（见「复核后的修正」N1）。

### 7. 热点周期（market_focus，Sonnet）

- `unbound_numeric_security_code`：错误信息只显示字段开头「（一）电信与消费……」，真正被拒的是同一字段里的「发现矿业股份5000万美元」，另一处是「普通股5000万美元」。数字前是「股份」「普通股」，被当成证券代码。修法：数字后是「量级加币种或股」（5000万美元、300万股），或直接接币种、百分号时才算数量；「股」后接东、本、权、份、票、价不算单位。0 开头的五位数先按港股代码判定；数字前是股票、港股、个股、代码、编号时不豁免，但「股票」后接「量级加股或币种」、数字又不是六位数时仍算数量（「回购股票1000万股」）。「港股」后的四五位数按港股代码判定（「港股9888百度集团」）。「股票600519上涨」「港股09888百度集团」「腾讯00700股东大会」「证券代码700股价」照旧拒绝。中文序号「（一）」本来就不触发。
- `english_prose_not_allowed: 'as'`、`catalyst`、`(6)`：来自字段名 `as_of`、`catalyst_bias` 与「(6)公司类事件」。这一条失败在 06:03Z，早于 `ca7c174e`（翻译这些写法）上线；用现行代码重放该样本，这几处都已通过，只剩「IT服务」，见第 6 条。
- 同一周期还有一处 `english_prose_not_allowed: 'p'`，原文在取证里被截断，看不到；按统计量写法处理了，部署后的找回试运行会给出确认。

### 8. 不是误判、本次不改

- Haiku 新闻 53 条、热点 2+2 条空字段：模型返回了空字符串，没有可发布的内容。Haiku 已不再承担新闻与热点。
- `news_identity_mismatch`（Luna 4、Terra 3）：取证里没有结果与回执，无法判断，未改。
- 财报（earnings_impact）的失败不在本次范围；字段名豁免只对新闻生效。
- 10-09 09:12Z 的 363 条 `runtime_configuration_changed` 来自把新闻模型从 Haiku 换成 Luna：旧模型的待处理任务按设计作废，没有花钱，调度会按新模型重建，但每次作废会占用该条新闻最多 3 次的调度次数。本次没有换模型。

## Luna 联网门控

- 判定：`runtime.task_uses_web_search`。只有新闻任务、模型是 Luna、载荷里没有正文文本时联网；其他模型和任务类型的 OpenAI 请求一律不带工具。
- 缺正文：带 web_search，`tool_choice=required`，最多 3 次；有正文：请求里不带 tools、tool_choice、max_tool_calls、include，提示词写明只按正文分析、不联网。两种都要求自然语言字段不附网址、Markdown 链接或括号域名，不照抄字段名。
- 有没有正文只按一条规则判断（`runtime.news_article_available`：正文文本去掉空白后不为空）。请求是否联网、Claude 是否带工具、发布时是否补「原始正文未取得」的提示，都用这一条。
- 身份：现行 Luna 新闻身份分两种，缺正文 `cda8f76d…`（`LUNA_WEB_NEWS_IDENTITY`），有正文 `e64f4252…`（`LUNA_ARTICLE_NEWS_IDENTITY`），新建任务按载荷选。读取与提交时两个变体都算当前，因为调用方只拿得到模型的默认变体。缺正文身份在审查前是 `e46819f9…`，它只出现在本 PR 分支上，origin/main 里没有，没有部署过，所以不列进上一版名单。
- 输入上界：联网请求读进来的搜索内容没有公开的数字上限，只能按模型的上下文窗口算。2026-10-10 核对 OpenAI 的模型页（https://developers.openai.com/api/docs/models/gpt-5.6-luna），Luna 的上下文窗口是 1,050,000，最大输出 128,000，与 Terra 相同。缺正文任务的输入上界是 1,050,000 减去输出上限 65,536，即 984,464。审查前代码把 Luna 的窗口写成 128,000（其实是最大输出），上界只有 62,464。
- 预留：提交时按任务自己的载荷计算。缺正文任务预留 1,050,000 词元、640,197 微美元（输入超过 272K 按长上下文价：984,464 词元 × 每百万 0.50 美元，加 65,536 词元 × 每百万 1.80 美元，加 3 次搜索各 0.01 美元）。有正文任务预留 139,264 词元、97,076 微美元，不预留搜索费。代码里的 Luna 单价与模型页一致：输入每百万 0.20 美元，按写缓存价的 1.25 倍计为 0.25，长上下文翻倍；输出每百万 1.20 美元，长上下文 1.5 倍。
- 当日词元账：尚未结算的行按不带载荷的口径计入，Luna 新闻一律按缺正文的 1,050,000。缺正文任务记得准确；有正文任务在途时每条多记 910,736。这本账只在 `model_budget.daily_budget_usd = 0`（只用日词元额度拦截）时影响准入，多记的方向是保守的；仓库默认与生产都用只统计的共享预算，它不拦任务。审查给的处理是只改文档，代码没动。

### 旧任务的判定规则

一个任务存的身份算当前，当且仅当：等于现行身份（Luna 两个变体任一），或者是 `runtime._IDENTITY_PREDECESSORS` 里登记的、某个现行身份的确切上一版：

| 现行身份 | 放行的上一版 |
| --- | --- |
| Luna 缺正文 `cda8f76d…`、Luna 有正文 `e64f4252…` | `719aed21…`（10-09 起一律联网，32,768）、`d0e6936d…`（更早的不联网版） |
| Terra 新闻 `e2f66048…` | `d0e6936d…`；v6 提示词的 `e35f6bc0…` 先按原有规则映射到 `d0e6936d…` |
| OpenAI 财报 `07071987…` | `efcf4a6d…` |

- `719aed21…` 同时是两个现行变体的上一版，这是有意的：生产上这一版的任务不分有无正文，它们已付费的结果照常可读，队列里的这类任务按载荷走现行策略（有正文不联网，缺正文联网）。
- 名单只按确切哈希放行。以后输出上限、提示词或结构再变，现行身份不再是名单里的键，旧任务照常判 `runtime_configuration_changed`（测试把上限改成 98,304 验证）。
- worker 提交前用任务自己的模型与载荷判定，通过后按现行策略构造请求：队列里的旧任务有正文就不带工具，缺正文就联网，输出上限都是 65,536。
- 已完成的旧身份结果在新闻列表里照常展示，也不会因此再建付费任务（测试覆盖）。
- Claude 各模型（Haiku、Sonnet）与热点的身份本次没有变化。

## 放宽的限制

| 项 | 原值 | 新值 | 为什么安全 |
| --- | --- | --- | --- |
| OpenAI 新闻、财报输出上限 | 32,768 | 65,536 | 思考词元计入上限；生产最大 25,392，原上限只剩不到 1.3 倍余量。Luna 官方输出上限 128,000、上下文 1,050,000。上限只影响预留与截断，不影响并发。 |
| 单任务预留（Luna 新闻） | 93,130 微美元 | 缺正文 640,197（含搜索 30,000）；有正文 97,076 | 结算时按实际用量改写；并发 4 时同时占用最多约 2.6 美元，仓库默认的共享预算只统计、不拦截。词元预留：缺正文 1,050,000（按上下文窗口，见「Luna 联网门控」），有正文 139,264。 |
| ai_jobs 一整轮的任务超时（worker 监督器用 `asyncio.wait_for` 包住整轮） | 2,000 秒 | 3,900 秒（`execution_limits.AI_JOBS_TASK_TIMEOUT_SECONDS`） | 必须盖过付费等待时限。否则 Claude 流（Sonnet 热点、Haiku 财报）超过 2,000 秒时整轮被取消：任务记 `submission_outcome_unknown`，已付费结果作废、预留不释放，同轮其他槽位一起被取消。 |
| Claude 流的截止 | 付费等待时限 | 付费等待时限与「任务超时减 300 秒余量」取较小者 | 即使等待时限被调到最大，流也先于监督器结束，只让这一条任务单独记失败。 |
| 付费分析最长等待 `openai_background_poll_timeout_seconds` | 1,800 秒 | 3,600 秒 | 到点会取消 OpenAI 后台响应、把 Claude 流（含 Sonnet 热点）记成结果未知，已付费的工作作废。最坏情况是一个卡住的响应多占一个并发槽半小时。 |
| 仓库默认预算 `[model_budget]` | 0（退回日词元额度 1000 万） | 10 美元、不强制 | 与生产一致：日词元只作统计、不拦截，费用照常记录。服务器部署时保留自己的 personal.toml，本来就是这组值。 |

`daily_token_limit` 的 0 不是「不限」：配置要求 102,400 至 100,000,000，0 会被校验直接拒绝；日额度只在 `model_budget.daily_budget_usd = 0` 时拦截，这时额度还不能低于单条任务的最大预留 1,050,000，否则配置校验报错（复核建议 8）。`budget_blocked` 是终态，不占队列；新闻调度在下一个 UTC 日重试前一天被拦的任务，财报同日内视为已存在、次日重试，10-08 的旧行不会再被当作活跃任务。

查过、维持不变的时限：

- `openai_job_max_age_seconds` 86,400 秒：只把 24 小时后仍在排队或运行的响应判过期；已完成的响应照常取回。
- 手动刷新冷却 30 秒：只限制手动触发频率。
- 热点意图 preparing 10 分钟：只作废从未产生付费任务的意图，已有付费任务的会被重新关联。
- 调度认领 10 分钟、控制请求 30 秒：不涉及付费结果。
- 单次请求读超时 900 秒：流式连接 15 分钟没有任何数据才触发，属于连接故障，不是过短的时限。

## 找回已付费结果

`python -m app.tools.recover_ai_schema_results` 新增 `--failed-since`（可配 `--job-type`、`--limit`）：选出该时刻之后失败、错误码属于可恢复类（`schema_validation_failed`、`provider_unavailable`、`local_storage_error`）、结果为空且本地存有供应商回执的任务，用本地回执按现行校验重验。不加 `--apply` 只试运行，逐条列出将要发布的叙述字段（`narrative`：结果里每个含汉字的字符串，按字段路径列出，例如 `summary_zh`、`key_factors[0]`、`affected_stocks[1].reason`）。加上 `--apply` 后写回为 completed，计费、用量与回执原样保留，不发任何供应商请求。标准输出是逐条结果；标准错误先给出按任务类型的可找回条数，超出 `--limit` 时说明还有多少条没选上，最后是按状态的计数。`--limit` 只接受 1 至 100,000 的整数，越界是用法错误（退出码 2）。不加 `--job-type` 时选中所有任务类型。

- `--apply` 只能在包含审查修正的版本上执行（至少到 B3 的 `98ee088a`）。审查实测，修正前的校验会把含「盘前TSLA +3.5%，港股09888百度集团盘中走高。」的回执原样发布。
- Luna（除最早的不联网旧版）、Haiku、Sonnet 的失败行都有本地回执。其中引用了本次没取回的站点、或带路径网址的 Luna 回执，复核 N2 之后重验仍会失败，试运行里会显示为 `validation_failed`。
- Terra 与最早的 Luna 旧版没有本地回执，只能逐个 `--job-id`，工具会向 OpenAI 取回已存的响应（不重新生成，不计费）；本次不建议批量做。
- 测试：用 3 条生产 Luna 回执构造失败行，试运行全部 validated，`--apply` 后 completed，结果通过新校验；没有回执的行不会被批量选中。
- 与历史清理一致：新闻与热点的清理（`prune_scheduled_history`）永不删除带回执的失败任务，找回工具能选中的行都在保留范围内。财报另有 30 天保留（`prune_earnings_retention`），旧回执会被删除，财报不在本次范围。
- 读取路径会按「校验函数、结果字节、载荷」缓存校验结论（`validate_result_cached`）。本次的规则只依赖这三样，部署重启会清空缓存，恢复后的结果和旧结果都按新规则重新判定。
- 恢复后的新闻在下一轮整理（约 2 分钟）出现在列表里。热点周期的付费任务恢复后，下一轮整理会按已完成的任务重新发布该周期（`_publish_completed_focus` 处理所有结果为空、未取消的周期，有回归测试）。

## 热点卡的失败原因

前端热点卡读周期的 `error_code`，按它显示原因（例如「模型返回的结果没有通过格式或语言检查，请重试」）。此前身份仍为现行的付费任务失败时，周期只改了状态、`error_code` 一直为空，卡片只能显示笼统的「这次分析没有完成」。现在失败与额度受限的周期记下任务的原因码；owner 读取的周期（`/api/catalysts/market-focus-cycles/latest`、`/{cycle_id}`）另带 `error_detail`，即任务落库的校验字段路径与被拒片段。访客投影不含这两项。前端未改。

## 审查后的修正

独立审查看的是变基前的 `9572206a`，给出 3 个阻塞项、4 个应修项和 5 条建议。修正都追加在 `b21cf87c` 之后，没有改写历史。下面每项列提交、改动和反例测试。

反例测试除注明的以外都在 `tests/test_ai_review_fixes_20261010.py`。每一条都在审查前的代码 `b21cf87c` 上跑过，确认会失败。同一文件里还有正向用例和红线用例，修正前后都通过，用来确认没有改过头。第一轮修完时整份文件 66 项，在 `b21cf87c` 上 46 项失败、20 项通过。复核后其中三处预期有改动，见「复核后的修正」。

### 阻塞项

| 项 | 提交 | 改动 | 反例测试 |
| --- | --- | --- | --- |
| B1 利差写法放行未绑定代码 | `f93b9dfa` | 原先缩写后只要接 +/-/加/减 与百分比或基点就豁免，也绕过了长于 4 个字母的拦截。现在只对 8 个基准利率名豁免。 | `test_b1_a_ticker_followed_by_a_percentage_move_still_needs_binding`（审查给的 6 句，新闻与热点各验一次）。正向：`test_b1_named_benchmarks_keep_their_spread_notation` |
| B2 数量写法放行数字证券代码 | `5f193007` | 0 开头的五位数先按港股代码判定；只有「量级加币种或股」或直接接币种、百分号才算数量；数字前是证券前缀时不豁免。 | `test_b2_unbound_numeric_codes_are_not_quantities`（10 句）。正向：`test_b2_amounts_after_share_nouns_still_pass` |
| B3 字段名回显原样发布 | `98ee088a` | 不再把字段名从扫描里屏蔽，改为译成固定的中文说法，替换后跑完整中文校验。 | `test_b3_field_names_are_published_in_chinese`（8 条）；`test_b3_words_outside_the_payload_vocabulary_are_still_rejected`（5 条，英文句子与单独的 `status` 2 条在旧代码上失败，其余 3 条是红线用例）；`tests/test_ai_analysis_fixes_20261010.py` 的 `test_exact_payload_field_names_are_published_in_chinese`（由原来的「不算英文」改为逐字断言中文译文，7 条在旧代码上失败） |

### 应修项

| 项 | 提交 | 改动 | 反例测试 |
| --- | --- | --- | --- |
| S1 付费等待被任务超时压住 | `d05f4564` | ai_jobs 任务超时 2,000 秒改为 3,900 秒；Claude 流截止取付费等待时限与「任务超时减 300 秒」的较小者。任务超时没有其他文档或测试镜像。 | `test_s1_ai_jobs_pass_outlasts_the_default_paid_wait`；`test_s1_paid_stream_deadline_stays_inside_the_pass`（3 种等待时限） |
| S2 IT 与涨跌词表的缺口 | `f6169672` | IT 移出通用缩写名单，只在信息技术搭配里放行；涨跌词表补上 14 个词（复核后撤回，见 N1）。 | 现名 `test_s2_it_outside_its_phrases_needs_binding`（复核后去掉「Apple暴跌拖累科技股」，余 4 句，「股票代码IT服务」「股票600519大涨」是红线用例）。正向：`test_s2_it_phrases_still_pass` |
| S3 大写字母接比较号一律放行 | `b7b6e517` | 比较号豁免只给小写的 p、n、r、t、k、d（复核后大写 P、N 在接数字时也放行，见建议 1）。 | 现名 `test_s3_ticker_letters_and_other_symbols_take_no_comparison_exemption`（复核后「试验结果P<0.001」移到通过一侧，余 3 句）。正向：`test_s3_statistic_notation_still_passes` |
| S4 找回工具看不到要发布的内容 | `c9e40ba1` | 试运行逐条列出 `narrative`；标准错误给出按任务类型的条数和超出 `--limit` 的剩余条数；`--limit` 在参数解析时校验 1 至 100,000。 | `test_s4_dry_run_shows_the_text_apply_then_publishes`；`test_s4_rows_beyond_the_limit_are_reported`；`test_s4_limit_outside_its_range_is_a_usage_error`（0、100001、-1 在旧代码上抛回溯；非数字修正前后都是用法错误）。`tests/test_ai_jobs.py` 里试运行的断言随之改为检查 `narrative` |

### 建议

| 项 | 提交 | 改动 | 反例测试 |
| --- | --- | --- | --- |
| 括号域名会删掉不是域名的内容 | `f9251db5` | 中文校验器不再删除括号内容；只有 Luna 回执里与本次联网来源主机一致的括号域名才去掉。 | 现名 `test_bracketed_hosts_that_are_not_retrieved_sites_are_rejected_not_deleted`（复核后只留「（sec.gov/news.html）」并改为断言拒绝，「（node.js）」移到第二轮的正向测试，见 N2）；`tests/test_ai_analysis_fixes_20261010.py` 的 `test_bracketed_domains_without_a_retrieved_site_stay_for_the_language_gate`（2 句）与 `test_domain_label_must_name_the_linked_site` |
| 「有没有正文」两处口径不同 | `79f31734` | 发布时的两处判断与 Claude 工具判断都改用 `news_article_available`。 | `test_a_blank_article_body_counts_as_missing_when_publishing` |
| Luna 上下文窗口 | `5bfbd921` | 核对官方模型页后，联网输入上界按 1,050,000 计；缺正文身份随之变为 `cda8f76d…`。依据与数字见「Luna 联网门控」。 | `test_luna_search_reservation_uses_the_published_context_window` |
| 当日词元账的方向写反 | 本次文档提交 | 审查时是每条在途的有正文任务少记 11,264（提交预留 139,264，账按 128,000）。上下文窗口修正后，同一口径变为每条多记 910,736。按审查意见只改文档。 | 无（未改代码） |
| 旧「一律联网」身份对两个变体都放行 | 本次文档提交 | 有意保留，已在「旧任务的判定规则」写明。 | 无（未改代码） |

### 与审查原文不同的地方

- B1：审查给了两种做法（只认基准利率名，或要求前面有「利率、基准、浮动、复合」语境），这里只用了前一种。名单之外的利率名仍要求绑定。
- B2：审查列的数量写法是「量级加币种或股」和「直接接币种」。这里另外保留了百分号（「股份5%」），因为百分比不会是证券代码。证券前缀与 0 开头五位数的检查排在它前面，「股票600519%」「港股09888%」仍按代码判定。
- S4：审查没有要求改「不加 `--job-type` 时选中所有任务类型」，这里保留这个行为，只在标准错误里按任务类型列出条数。

## 复核后的修正

复核在 `414be099` 上跑了 134 条样本，并用 183 个白名单名称逐个接 14 个新增涨跌词扫描，给出 1 个阻塞项、1 个应修项和 9 条建议。修正追加在 `414be099` 之后，没有改写历史。反例测试除注明的以外都在 `tests/test_ai_review_round2_20261010.py`，都在 `414be099` 的导出树上确认过会失败；正向和红线用例修正前后都通过。整份文件 141 项（含 N2 收窄时加的 8 项），在 `414be099` 上 104 项失败、37 项通过，在现在的代码上全部通过。

| 项 | 提交 | 改动 | 反例测试 |
| --- | --- | --- | --- |
| N1 涨跌词表扩充误伤市场术语（阻塞） | `e15f4277` | 撤回 `f6169672` 补的 14 个涨跌词，恢复原来的 8 个；IT 的后缀限定保留，「高德纳（IT）暴跌20%」照旧被拒。 | `test_n1_review_sentences_publish_in_every_task`（复核给的 17 句，期权、信号、新闻各验一次）；`test_n1_market_vocabulary_takes_movement_words_in_every_task`（市场术语、宏观指标代码、基准利率名、技术术语接大涨、暴跌、飙升、反弹、走高、走低）；`test_n1_no_whitelisted_name_is_rejected_for_a_common_movement_word`（白名单逐个名称接 10 个常见涨跌词）。红线：`test_n1_unbound_codes_stay_rejected` |
| N2 紧跟中文的括号主机名照常发布（应修） | `b9b04f04`，之后的补充提交按要求收窄 | 「中文术语（外文标注）」通道拒绝括号里的主机名：以 www. 开头、或最后一段是常见小写顶级域名，可带路径。最初的写法（点号后接两个以上字母，或含斜杠）误伤「（node.js）」，补充提交收窄成现在这样。Luna 的引用归一化不变。 | `test_n2_bracketed_host_names_are_not_published`（6 句，新闻与热点各验一次）；`test_n2_luna_hosts_other_than_a_retrieved_bare_domain_are_rejected`（2 句）；`test_n2_mixed_case_and_country_hosts_are_not_published`（「（GlobeNewswire.com）」「（SEC.gov）」「（bbc.co.uk）」）。正向：`test_n2_a_retrieved_bare_domain_is_still_removed`、`test_n2_term_glosses_still_publish`（含「（node.js）」「（Vue.js）」「（ASP.NET）」「（TCP/IP）」「（Character.AI）」；收窄前 node.js、Vue.js、Character.AI 三句会失败） |
| 建议 1 大写 P、N 统计写法 | `203da258` | P、N 接比较号再接数字、数字后不接币种时放行。 | `test_s1r2_upper_case_p_and_n_statistics_publish`（4 句）。红线：`test_s1r2_a_letter_compared_with_a_price_still_needs_binding` |
| 建议 2 「股票」后的股数 | `deca6ddd` | 「股票」后接量级加股或币种、数字不是六位数时算数量。 | `test_s2r2_share_counts_after_the_word_stock_publish`（4 句）。红线：`test_s2r2_code_shaped_numbers_after_the_word_stock_still_need_binding` |
| 建议 3 IT 搭配 | `004264fa` | 补基础设施、预算、投入、架构、运维、人员、资产、解决方案。 | `test_s3r2_more_it_phrases_publish`（8 句）。红线：`test_s3r2_it_in_security_context_still_needs_binding` |
| 建议 4 fetch_failed | `b4947b62` | 译成「抓取失败」，只在它是本条载荷自己的原因时翻译。 | `test_s4r2_fetch_failed_is_published_in_chinese`。红线：`test_s4r2_fetch_failed_is_translated_only_when_it_is_this_payloads_reason` |
| 建议 5 四位港股代码 | `2c27b78a` | 「港股」「港股代码」后的四五位数、后面不是数量写法时要求绑定。 | `test_s5r2_four_digit_hong_kong_codes_need_binding`（2 句，新闻与热点各验一次）。正向：`test_s5r2_months_counts_and_years_after_hong_kong_stocks_publish` |
| 建议 6 没有汉字的文本不翻译 | `db1c5773` | `_translate_news_metadata` 遇到一个汉字都没有的文本原样返回，交给中文校验拒绝。 | `test_s6r2_text_without_chinese_is_rejected_not_translated`（3 条）。正向：`test_s6r2_field_names_inside_chinese_text_are_still_translated` |
| 建议 7 基准利率名接涨跌 | `013b925e` | 8 个基准利率名同时列入白名单与宏观指标代码，走同一条例外；B1 加的利差分支不再会被走到，删除；代码后接公司、集团、企业时不算指标。 | `test_s7_benchmark_rates_take_the_original_movement_words`（4 句，期权、信号、新闻各验一次）；`test_s7_security_context_still_needs_binding` 里的「CPI公司股价下跌」，其余 4 句是红线用例 |
| 建议 8 只用日词元额度时的门槛 | `e23f0fdf` | 共享预算为 0 时，`ai.daily_token_limit` 低于 1,050,000 是配置错误，报错写明两个数和两种改法。 | `test_s8r2_token_only_budget_below_one_task_is_a_config_error`（102,400 与 1,049,999）；`test_s8r2_the_config_mirror_is_the_largest_task_reservation`。正向：`test_s8r2_limits_that_admit_one_task_or_a_shared_budget_are_accepted` |
| 建议 9 停机时的等待 | 本次文档提交 | 写进「部署后操作」。 | 无（未改代码） |

第一轮测试随之改了三处预期：

- S2 去掉「Apple暴跌拖累科技股」：词表恢复后它与基线一样通过。测试改名为 `test_s2_it_outside_its_phrases_needs_binding`。
- S3 的「试验结果P<0.001」从拒绝一侧移到通过一侧。拒绝一侧改名为 `test_s3_ticker_letters_and_other_symbols_take_no_comparison_exemption`。
- 括号里的「（sec.gov/news.html）」从「原样保留」改为断言拒绝，测试改名为 `test_bracketed_hosts_that_are_not_retrieved_sites_are_rejected_not_deleted`；「（node.js）」在 N2 收窄后仍原样发布，移到第二轮的 `test_n2_term_glosses_still_publish`。`test_domain_label_must_name_the_linked_site` 里标签对不上链接时留下的「（ec.gov）」也改为断言拒绝。

### 与复核原文不同的地方

- 建议 5：没有把「港股」整个加进最后一步的前缀表，只对四五位数这样判定。数字规则检查 1 至 12 位的所有数字，整个加进去会误伤「港股10月以来累计上涨」「港股3只科技股走强」。
- 建议 7：按「走宏观指标代码那套例外」做，8 个名称列进了白名单，白名单从 183 个名称变为 191 个。这与「不加实体白名单」的原则不一致，是按复核的决定做的。
- 建议 8：配置层按既有约定不引用服务层，门槛写成字面量 1,050,000（所有任务类型与模型里的最大预留），由测试核对它与 `runtime.token_reservation` 一致。只配 Claude 模型时实际最大预留是 1,000,000，这时 1,000,000 至 1,049,999 之间的额度也会被拒。
- 建议 6：只改了新闻的翻译。热点的 as_of、catalyst_bias 等翻译是基线代码，没有加这项检查。
- N2：主机名的判定按复核要求收窄过一次，只认以 www. 开头、或最后一段是 com、net、org、gov 等 25 个常见顶级域名之一的写法，可带路径。复核原文要求整段全小写，但实测没有别的规则会拒「（GlobeNewswire.com）」「（SEC.gov）」，按全小写判定它们会原样发布，所以只要求顶级域名这一段是小写。结果：「（node.js）」「（Vue.js）」「（Character.AI）」照常发布；「（ASP.NET）」「（TCP/IP）」本来就由缩写规则放行，不经过这条规则（上一版说它们会被拒，说错了）；「（Booking.com）」这类以小写 .com 结尾的公司名作为注释会被拒，除非出现在来源文本里；「（Sec.Gov）」这种顶级域名也大写的写法会通过。Luna 回执里引用了本次没取回的站点、或带路径的网址时，结果判 `schema_validation_failed`，找回工具也救不回来，这是复核要的结果。10 条生产 Luna 回执的重放不受影响。

## 部署后操作

部署或重启时，worker 会等正在跑的付费分析做完再退出。ai_jobs 一整轮的任务超时是 3,900 秒，碰上长的 Claude 流最长要等约 65 分钟；compose 给 worker 的 `stop_grace_period` 是 7,500 秒，不会中途被强制结束。

1. 部署后核对服务器 `config/personal.toml` 仍是 `[model_budget] daily_budget_usd = 10.0`、`enforce_limit = false`。
2. 找回试运行（只读），在挂载 `/data`、能导入 `app` 的后端容器里执行（容器内若找不到 `app` 模块，按服务进程的工作目录补 `-w` 或 `PYTHONPATH`）：

   ```
   docker exec option-pro-backend-1 \
     python -m app.tools.recover_ai_schema_results \
     --failed-since 2026-10-03T00:00:00Z \
     --job-type news_impact --job-type market_focus > /tmp/ai-recover-dry.json
   ```

   看标准错误输出里的计数，逐条看 `narrative` 里将要发布的文字，以及 `validation_failed` 各条的 `error`。按取证的 72 小时数字估算，有本地回执的约为 Luna 新闻 171 条、Haiku 新闻 458 条、Sonnet 热点 2 条（7 天窗口会更多；默认最多选 2,000 条，超过时标准错误会给出剩余条数，再调大 `--limit`）；Terra 新闻 110 条没有回执，不会被选中。
   确认后加 `--apply`，在任务少的时段执行；要分批就按 `--job-type` 分开跑（重验不通过的行会留在失败状态，下次仍会被选中，所以不要靠 `--limit` 反复跑来分批）。每条恢复各占一次很短的 ai-jobs.db 写事务，worker 的 `claim_due` 近期有过 `database is locked`，执行时留意 worker 日志。
   退出码只有在选中的每一条都 validated 或 recovered 时才是 0：有任何一条 `validation_failed` 是 1，选不出任何行（输出 `[]`，例如已经处理完）也是 1。
3. 观察（只读 SQL，`:deployed` 填部署时刻）：

   ```sql
   -- 新闻按模型、状态、错误码
   SELECT model, status, COALESCE(error_code, '') AS code, COUNT(*)
   FROM ai_jobs WHERE job_type = 'news_impact' AND created_at >= :deployed
   GROUP BY 1, 2, 3 ORDER BY 4 DESC;

   -- Luna 有无正文与是否联网（有正文的一行应为 0 次联网）
   SELECT CASE WHEN json_extract(payload_json, '$.article.text') IS NULL
               THEN 'no_article' ELSE 'article' END AS body,
          SUM(COALESCE(json_extract(provider_result_json, '$.usage.web_search_requests'), 0) > 0) AS searched,
          COUNT(*) AS jobs
   FROM ai_jobs WHERE model = 'gpt-5.6-luna' AND job_type = 'news_impact'
     AND submitted_at >= :deployed GROUP BY 1;

   -- 应为 0：部署后因策略变化作废的任务
   SELECT COUNT(*) FROM ai_jobs
   WHERE error_code = 'runtime_configuration_changed' AND updated_at >= :deployed;

   -- 输出用量与截断
   SELECT MAX(usage_output_tokens), SUM(error_code = 'provider_incomplete_max_output_tokens')
   FROM ai_jobs WHERE job_type = 'news_impact' AND updated_at >= :deployed;
   ```

## 检查记录

- 变基前（基线 `1cbf7915`）：修改前 6,330 通过、7 跳过，修改后 6,406 通过、7 跳过；前四个代码提交各自跑过 AI、新闻与热点相关的约 2,200 项测试。
- 变基到 `82e2312d` 并补上密钥并发测试的修正后，完整后端测试 6,458 通过、7 跳过（7 个子测试通过）；`compileall backend/app scripts` 通过，`git diff --check 82e2312d...HEAD` 干净。
- 同一分支另有一个与本次修复无关的测试提交：`tests/test_personal_secrets.py` 放宽 spawn 子进程的等待，失败时不再留下孤儿进程（原因见该提交说明）。
- 新增回归测试 76 项；更新了 9 个已有测试文件里写死旧策略的断言（32,768 上限、Luna 一律联网、Terra 旧身份哈希、默认无共享预算、预留函数的测试替身签名）。
- `python -m compileall -q backend/app` 通过。
- 审查修正后（代码到 `5bfbd921`）：完整后端测试 6,526 通过、7 跳过（7 个子测试通过），比变基后多 68 项；`compileall backend/app scripts` 通过，`git diff --check 82e2312d...HEAD` 干净。审查反例文件 66 项，在 `b21cf87c` 上 46 项失败、20 项通过（正向与红线用例），在现在的代码上全部通过。三个库的建表文本、版本名与校验和没有变动。
- 复核修正后（代码到 `e23f0fdf`）：完整后端测试 6,658 通过、7 跳过（7 个子测试通过），比第一轮修正后多 132 项；`compileall backend/app scripts` 通过，`git diff --check 82e2312d...HEAD` 干净；建表文本、版本名与校验和仍没有变动。
- N2 收窄后：完整后端测试 6,665 通过、7 跳过（7 个子测试通过），多出的 7 项是新增的 8 项减去第一轮移走的「（node.js）」一项；`compileall backend/app scripts` 通过，`git diff --check 82e2312d...HEAD` 干净。

## 未覆盖

- 热点样本的回执被截断，只能逐段重放；`'p'` 那一处看不到原文。
- Haiku 新闻样本（MSCI ACWI、Bloomberg、WISeKey、SPAC、ATOMIC、TRADE）没有载荷，无法重放，部分可能仍不通过，以找回试运行为准。
- `news_identity_mismatch` 未诊断。
- 当日词元账对在途的 Luna 有正文任务按缺正文口径计入，每条多记 910,736 词元；只在只用日词元额度拦截时有影响，共享预算启用后该账只作统计（见「Luna 联网门控」）。
- 缺正文身份 `e46819f9…` 没有列进上一版名单，依据是它不在 origin/main 上。如果 PR 分支在合并前曾单独部署过，需要把它加进 `_IDENTITY_PREDECESSORS`，否则那段时间建的待处理任务会判 `runtime_configuration_changed`。
- 热点（market_focus）的翻译没有「无汉字不翻译」的检查，见「复核后的修正」。
