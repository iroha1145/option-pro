# 模型新闻分析与热点追踪报错修复（2026-10-10）

分支 `claude/ai-fixes-2026-10-10`，基于新闻分支 `claude/news-ingest-2026-10-09` 的头 `82e2312d`（最初在性能分支旧头 `1cbf7915` 上开发，2026-10-10 用 `git rebase --onto 82e2312d 1cbf7915` 变基，没有冲突）。取证文件在性能工作区的 `evidence/ai-errors-2026-10-10.txt` 与 `evidence/ai-failed-samples-2026-10-10.json`（2026-10-09 17:2x UTC 的只读快照）。生产运行 `72f426f0`。

| 提交 | 内容 |
| --- | --- |
| `6af90150` | 中文校验器的误判修正 |
| `10a7db09` | 输出上限、等待时限、日额度放宽，以及旧身份任务的放行规则 |
| `8e10dad9` | Luna 只在没拿到正文时联网 |
| `86a05b61` | 找回工具按时间批量重验本地回执 |
| `3f4507ca` | 热点周期失败时保留原因 |

分支开了 PR #237。独立审查之后追加了 10 个修正提交，复核之后又追加了 10 个，另有一个按要求收窄 N2 的补充提交，最后一轮复核后再追加 3 个，见「审查后的修正」与「复核后的修正」两节；下文各节写的都是修正后的行为。

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
- 修法：标签按点号边界比对主机名（`sec.gov` 能匹配 `www.sec.gov`，`ec.gov` 不能），对得上的链接整段去掉；括号里受信的裸网址同样去掉；不在联网记录里的网址照旧以 `ai_news_unbound_or_unhandled_url` 拒绝，不为了过中文校验抹掉未核实的来源。括号里只写域名的来源标注（含「来源：」前缀、多个域名用顿号分隔），只有每个域名都与本次联网工具实际取回的来源主机按点号边界一致时才去掉。中文校验器本身不删除任何括号内容，但括号里的主机名不算「中文术语（外文标注）」，会被拒绝。主机名指以 www. 开头、或最后一段是 60 个常见顶级域名（com、net、org、gov 与 hk、uk、mx、kr 等国家后缀）之一的写法，不区分大小写，可带路径，co.uk、com.hk 这类多段后缀按最后一段判，例如「（reuters.com）」「（www.nvidia.com/zh-cn）」「（GlobeNewswire.com）」「（info.gov.hk）」「（Reuters.COM）」。js、py、md、sh、ts、go、rs、ai、io 这类同时是技术或产品后缀的不算，「（node.js）」「（Character.AI）」「（GitHub.io）」照常发布。全大写的写法先走缩写规则：「（ASP.NET）」「（TCP/IP）」放行，「（WWW.SEC.GOV）」因为以 www. 开头被拒，「（SEC.GOV）」这种不带 www. 的全大写主机名仍会通过。
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

- `unbound_numeric_security_code`：错误信息只显示字段开头「（一）电信与消费……」，真正被拒的是同一字段里的「发现矿业股份5000万美元」，另一处是「普通股5000万美元」。数字前是「股份」「普通股」，被当成证券代码。修法：数字后是「量级加币种或股」（5000万美元、300万股），或直接接币种、百分号时才算数量；「股」后接东、本、权、份、票、价不算单位。0 开头的五位数先按港股代码判定；数字前是股票、港股、个股、代码、编号时不豁免，但「股票」后接「量级加股或币种」、数字又不是六位数时仍算数量（「回购股票1000万股」）。「港股」后的四五位数按港股代码判定（「港股9888百度集团」），但数字后接只、家、个、名、点、余、多、亿、万、年、月、日、%、港元、美元时说的是市场概况，不按代码判定（「港股2600家上市公司」「港股26000点附近震荡」）。「股票600519上涨」「港股09888百度集团」「腾讯00700股东大会」「证券代码700股价」照旧拒绝。中文序号「（一）」本来就不触发。
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

`daily_token_limit` 的 0 不是「不限」：配置要求 102,400 至 100,000,000，0 会被校验直接拒绝；日额度只在 `model_budget.daily_budget_usd = 0` 时拦截，这时额度还不能低于单条任务的最大预留 1,050,000，否则配置校验报错（复核建议 8），通过运行设置接口写入（含回滚）时同样检查，接口返回 422 与错误码 `token_limit_below_task_reservation`。`budget_blocked` 是终态，不占队列；新闻调度在下一个 UTC 日重试前一天被拦的任务，财报同日内视为已存在、次日重试，10-08 的旧行不会再被当作活跃任务。

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
- N2：主机名的判定按复核要求收窄过一次，只认以 www. 开头、或最后一段是 com、net、org、gov 等 25 个常见顶级域名之一的写法，可带路径。复核原文要求整段全小写，但实测没有别的规则会拒「（GlobeNewswire.com）」「（SEC.gov）」，按全小写判定它们会原样发布，所以只要求顶级域名这一段是小写。结果：「（node.js）」「（Vue.js）」「（Character.AI）」照常发布；「（ASP.NET）」「（TCP/IP）」本来就由缩写规则放行，不经过这条规则（上一版说它们会被拒，说错了）；「（Booking.com）」这类以 .com 结尾的公司名作为注释会被拒，除非出现在来源文本里。（最后一轮复核又改为不区分大小写并补了国家后缀，见下一节。）Luna 回执里引用了本次没取回的站点、或带路径的网址时，结果判 `schema_validation_failed`，找回工具也救不回来，这是复核要的结果。10 条生产 Luna 回执的重放不受影响。

### 最后一轮复核（0b7b68fa 之后）

最后一轮复核没有阻塞项，给出 1 个应修项和 2 条建议。修正追加在 `0b7b68fa` 之后。反例测试都在 `tests/test_ai_review_round3_20261010.py`，都在 `0b7b68fa` 的导出树上确认过会失败；正向和红线用例修正前后都通过。整份文件 38 项，在 `0b7b68fa` 上 22 项失败、16 项通过。

| 项 | 提交 | 改动 | 反例测试 |
| --- | --- | --- | --- |
| M1 「港股」后的数量被当成代码（应修） | `935ac71e` | 「港股」后的四五位数接只、家、个、名、点、余、多、亿、万、年、月、日、%、港元、美元时不按代码判定。 | `test_m1_market_overview_counts_after_hong_kong_stocks_publish`（复核给的 7 句，新闻与热点各验一次）。红线：`test_m1_hong_kong_codes_still_need_binding`（「港股1810小米集团盘中走高」「腾讯港股代码为0700」） |
| 建议 1 主机名的国家后缀与大小写 | `81a100b5` | 顶级域名表扩到 60 个（补国家后缀），比对不区分大小写；ai、io 移出表；缩写规则不再接受 www. 开头的写法。 | `test_r3s1_country_and_upper_case_hosts_are_not_published`（11 句，含 info.gov.hk、eleconomista.com.mx、Reuters.COM、WWW.SEC.GOV）；`test_r3s1_technical_and_product_suffixes_stay_glosses` 里的「（Fast.ai）」「（GitHub.io）」，其余 11 句是正向用例 |
| 建议 2 运行设置可以绕过额度下限 | `5ee435e4` | 共享预算为 0 时，运行设置写入（含回滚）后的 `daily_token_limit` 低于 1,050,000 就拒绝，接口返回 422 与 `token_limit_below_task_reservation`；共享预算为正时不检查。 | `test_r3s2_token_only_budget_rejects_a_low_daily_token_limit`、`test_r3s2_rollback_to_a_low_limit_is_refused_under_a_token_only_budget`。正向：`test_r3s2_limits_that_admit_one_task_or_a_shared_budget_are_saved` |

与最后一轮复核原文不同的地方：

- 建议 1：全大写、带点号的写法在到达主机名判定之前就由缩写规则放行（这是「（ASP.NET）」能通过的原因）。为了拒「（WWW.SEC.GOV）」，缩写规则不再接受 www. 开头的写法；但不带 www. 的全大写主机名「（SEC.GOV）」仍会通过，要拒它就得连「（ASP.NET）」一起拒。
- 建议 2：检查放在运行设置存储的写入路径里，存储从 personal.toml 拿共享预算。直接构造存储、不传共享预算时（只有测试会这样做）不检查。已经存下的低额度旧文档读取时不受影响，下一次写入才会被要求改正。

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
- 最后一轮复核修正后（代码到 `5ee435e4`）：完整后端测试 6,703 通过、7 跳过（7 个子测试通过），多出的 38 项就是新的反例文件；多出的 2 条警告是运行设置接口里既有的 `HTTP_422_UNPROCESSABLE_ENTITY` 弃用提示，被新测试走到。`compileall backend/app scripts` 通过，`git diff --check 82e2312d...HEAD` 干净；建表文本、版本名与校验和仍没有变动。

## 未覆盖

- 热点样本的回执被截断，只能逐段重放；`'p'` 那一处看不到原文。
- Haiku 新闻样本（MSCI ACWI、Bloomberg、WISeKey、SPAC、ATOMIC、TRADE）没有载荷，无法重放，部分可能仍不通过，以找回试运行为准。
- `news_identity_mismatch` 未诊断。
- 当日词元账对在途的 Luna 有正文任务按缺正文口径计入，每条多记 910,736 词元；只在只用日词元额度拦截时有影响，共享预算启用后该账只作统计（见「Luna 联网门控」）。
- 缺正文身份 `e46819f9…` 没有列进上一版名单，依据是它不在 origin/main 上。如果 PR 分支在合并前曾单独部署过，需要把它加进 `_IDENTITY_PREDECESSORS`，否则那段时间建的待处理任务会判 `runtime_configuration_changed`。
- 热点（market_focus）的翻译没有「无汉字不翻译」的检查，见「复核后的修正」。

## 部署后补充（2026-10-10）

分支 `claude/ai-prose-allowlist-2026-10-10`，基于 main 的 `8bdd394c`（已含 PR #237）。只改中文校验器，回归测试在 `tests/test_ai_prose_allowlist_20261010.py`。

### 生产证据

- PR #237 部署后 20 分钟内，GPT-5.6 Luna 的新任务完成 16 条，另有 2 条仍以 `english_prose_not_allowed` 被拒。被拒的都是正常的中文句子：
  - 「公司普通股收盘买价……不符合纳斯达克资本市场规则5550(a)(2)的继续上市最低买价要求」，被拒片段 `'a'`，是规则条款编号括号里的小写字母。
  - 「阿莱恩特医疗保健披露，其加州HMO合同预计由2026年的4.0星降至2027年的3.5星」，被拒片段 `'HMO'`。同一条结果里「健康结果调查和处方药D部分若干三倍权重指标走弱」被拒 `'D'`，说的是联邦医疗保险 D 部分（Medicare Part D）。
- 找回工具对 10-03 起失败任务的试运行选中 880 条，重验仍不通过的有 481 条，长尾也是同类：单个字母 A、B、C、D、F、G 约 60 次，CNBC 7 次，MHz 7 次，III 5 次，SUV 3 次，REIT 2 次，ESG、FCC、NBC、LSEG 各 1 到 2 次。

### 放行规则

规则判断都在 `backend/app/services/ai_jobs/models.py` 的 `_foreign_span_context` 里，括号字母和频率另有小函数 `_is_rule_clause_letter`、`_is_frequency_quantity`。五条都只增加放行的分支，原来能通过的写法照旧通过。每条都有正例和反例测试。

1. 规则条款里的小写字母。ASCII 括号里只有一个小写字母，并且左括号前紧挨数字或上一个括号组，或者右括号后紧跟下一个条款括号组（括号里是 1 到 4 个字母或数字）时，不算英文。例如「规则5550(a)(2)」「规则10b5-1(c)」「第(2)(a)项」「根据(a)(2)款」。
   - 仍被拒：两边都是中文的「根据(a)款」「方案(a)更优」，全角括号「（a）」，两个字母「(ab)」，大写「(A)」，英文冠词「a deal」。
2. 单个大写字母作标签。字母后面紧跟部分、型、级、组、区，中间最多隔空格，并且不在证券语境时放行，例如「处方药D部分」「V型反转」「A级」「B组」「C区」。
   - 「类」「轮」原有规则不变，「B类股」「A轮」原来就能通过。
   - 「股」仍只认 A、B、H：「A股」照常通过，「A股价」「A股票」「F股」「C股」「A公司」照旧被拒。
   - 字母和名词之间隔着标点时不算。「福特汽车（F），部分分析师下调评级」里的「部分」是另一个词，F 是福特汽车的代码，仍要求绑定。新名词没有并进「类」「轮」那条判断，原因就在这里：那条判断先去掉标点再比对，并进去就会放过这句。
3. 常用缩写进名单。白名单加入 9 个名称，从 191 个变为 200 个。这与「不加实体白名单」的原则不一致，是按本次要求做的。
   - 媒体与机构：美国消费者新闻与商业频道（CNBC）、美国全国广播公司（NBC）、美国联邦通信委员会（FCC）、伦敦证券交易所集团（LSEG）。
   - 医保计划：健康维护组织（HMO）、优选医疗机构（PPO）。PPO 不在取证里，它和 HMO 是并列的两类计划，一并加入。
   - 行业通用词：房地产投资信托（REIT）、运动型多用途车（SUV），以及环境、社会和公司治理（ESG）。
   - 名单里的名称在证券语境仍要求绑定：「ESG股价上涨」「股票代码CNBC」「SUV股票下跌」被拒；ESG 是本条绑定的代码时照常通过。
4. 频率数量。兆赫（MHz）、吉赫（GHz）没有进名单，只在紧跟数字时放行，例如「600 MHz」「600MHz」「3.5GHz」。数量前面不能紧挨代码、编号、公司、集团、企业、股价，后面不能紧跟代码、编号、公司、集团、企业，也不能在证券语境。
   - 为什么不进名单：2026-10-09 的提交 `e33e96f5` 给经核验的热点结果加了单位翻译（`_translate_verified_focus_prose`），把带数字的频率译成「兆赫」「吉赫」。同时有一条红线测试（`tests/test_verified_focus_prose_compatibility.py` 的 `test_frequency_translation_does_not_relax_language_or_security_binding`），要求校验器继续拒绝不带数字的「频率MHz已公布」和标签后的「公司800MHz发布公告」。最初把 MHz、GHz 直接加进名单时，完整后端测试在这条测试上失败 2 项，所以收窄成现在这样，测试没有改。
   - 标签判断和那段翻译共用一个函数 `_next_to_code_or_company_label`。它是从翻译代码里原样抽出来的，翻译的行为不变。
   - 经核验的热点结果先翻译再校验，发布的仍是「600兆赫」；新闻和其他任务现在会原样发布「600 MHz」。把翻译扩到新闻是另一种做法，本次不做。
5. 罗马数字序号。II、III、IV 前面紧挨「第」，或后面紧跟期、级、类、代，或用斜杠、连字符接在数字后面，并且不在证券语境时放行，例如「第III期临床试验」「III期临床试验」「II类医疗器械」「第III代芯片」「年报第II部分」「1/II期」。
   - III 也是股票代码，所以不进名单。「III上涨」「II股价上涨」「股票代码III期」「买入III 300股」照旧被拒。
   - 「紧挨数字」只按斜杠和连字符算。直接相连的「5III」「III5」会被切成同一个片段，到不了这条规则；用空格隔开的「III 300股」可能是代码加股数，不放行。「II/III期」「III-2」原来就由缩写规则放行。
   - IV 早在名单里（隐含波动率），「IV期」「第IV代」原来就能通过，这条规则不改变它的结果。

### 仍保留的红线和拒绝

- 审计红线不变，新测试逐句验证：「A股价上涨」「股票600519上涨」「IT股价上涨」「TSLA上涨」「盘前TSLA +3.5%」照旧被拒，1 到 5 位全大写代码在证券语境仍要求代码绑定。
- 名单里的名称后接上涨、下跌、涨停、跌停、走强、走弱、收涨、收跌时，仍按个股行情要求绑定，例如「REIT上涨」。只有 CPI 一类宏观指标代码和 8 个基准利率名例外，REIT 没有加进那一组。
- 频率：不带数字的「频率MHz已公布」，紧挨标签的「公司800MHz发布公告」「编号800MHz继续有效」「800MHz公司宣布交易」，以及证券语境的「600MHz股价上涨」「股票代码为800MHz」照旧被拒。代价是「该公司600MHz频谱出售给运营商」这种「公司」紧挨数字的正常句子也会被拒。
- 结构身份不变。校验版本名仍是 `simplified-chinese-v4`，名单内容不进身份哈希，队列里的任务不会因此作废。

### 未覆盖

- 「规则10b-5(b)」：括号里的 b 能过，但「10b-5」本身仍被拒。它不在名单里，又不含大写字母，缩写规则不认。英文的「Rule」也照旧被拒，只有「Rule 10b5-1」会先换成「规则10b5-1」。
- 「巴塞尔协议III」：III 不在序号位置，仍被拒。
- 「I期临床试验」：单个字母 I 后接「期」，不在本次的名词表里。「II型糖尿病」：罗马数字后接「型」，不在本次给定的期、级、类、代里。
- 「A系列融资」：「系列」只在新闻任务里、来源文本有「X series」写法时放行，是既有规则，本次不改。
- 「AAA级」这类多个字母的评级不在本次范围。
- 试运行里约 60 次单字母被拒的具体语境没有逐条核对。「B类股」「A轮」在现行代码上已经能通过，其余语境要看部署后的下一次找回试运行。
- 试运行里 7 次被拒的片段都是单独的「MHz」，说明原文要么数字和单位之间有空格，要么没有数字，看不出前面是否紧挨「公司」。下一次找回试运行里如果 MHz 还在，先看是不是这两种写法。
- 本次没有在生产上重跑找回试运行。部署后按「部署后操作」第 2 步再跑一次，可以看出 481 条里有多少转为 validated。

### 检查记录

- 新测试文件 75 项。在 main 的 `8bdd394c` 导出树上，31 项新正例全部失败，44 项通过（38 项红线和收窄反例，6 项原本就能通过的写法）；在本分支全部通过。
- 变异实验 7 个：把规则故意放宽，对应的反例都会失败。字母标签改成先去掉标点再比对时，「福特汽车（F），部分分析师」两句失败；字母标签、罗马数字、频率各自去掉证券语境检查时，「股票代码F组」「股票代码III期」「600MHz股价上涨」失败（「股票代码为800MHz」由标签判断兜住，不依赖这条检查）；括号字母不看左右时，「根据(a)款」「方案(a)更优」失败；频率去掉标签判断时，「公司800MHz发布公告」「编号800MHz继续有效」「800MHz公司宣布交易」失败；把 MHz 放回名单时，「频率MHz已公布」失败。
- 既有测试：`test_ai_analysis_fixes_20261010.py` 78 项、`test_ai_review_round2_20261010.py` 141 项（含白名单逐个名称接 10 个涨跌词的扫描，新加的 9 个名称都在里面）、`test_ai_review_fixes_20261010.py` 64 项、`test_ai_review_round3_20261010.py` 38 项、`test_ai_jobs_zh_contract.py` 765 项、`test_ai_jobs.py` 91 项、`test_ai_jobs_audit_2026_09_25.py` 108 项、`test_claude_provider.py` 63 项，全部通过；`test_verified_focus_prose_compatibility.py` 221 项通过、1 项跳过。
- 完整后端测试跑了两次。第一次 MHz、GHz 还在名单里：6,771 项通过、2 项失败、7 项跳过，失败的就是上面那条频率红线测试。只跑指定的几个测试文件时没有发现这个冲突，是完整测试抓到的。收窄后：6,778 项通过、7 项跳过（7 个子测试通过），退出码 0。
- `python -m compileall -q backend/app` 通过，`git diff --check origin/main...HEAD -- . ':(top,exclude)frontend'` 干净。

## 口径变更（2026-10-10）

分支 `claude/ai-prose-terms-2026-10-10`，基于 main 的 `94cf52e2`（PR #240 已合并部署）。

### 用户决定

审查可以不用那么严格，专有词等即使不是中文也可以。落到规则上：自然语言字段仍然要求中文，但拉丁字母的专名和术语可以保留原文；`english_prose_not_allowed` 只拦真正的英文散文，不拦词条。热点、财报和新闻共用同一条规则。

### 新规则

实现在 `backend/app/services/ai_jobs/models.py` 的 `_is_term_like_span`。原有的放行规则一条没删，都没认下的片段才走到这里，在主循环里最后判一次。

- 放行两种词条：
  - 单个词元，由字母、数字和连字符、点号、`&`、撇号组成。例如 efgartigimod、BRAFTOVI、THAAD、VEGF-A、CoreWeave、node.js、SThree、Polymesh。
  - 不超过 5 个词的名称，每个词是首字母大写、全大写、数字或版本号，或者是连接词（of、and、&、de、the、for、plc、Inc、Ltd 等）。例如 Simply Good Foods、PAC-3 Edge、Gooch & Housego plc、Panmure Liberum、Genesis Mission、Rule 10b-5。
  - 单字母评级（A+、B、D、F）、系列（B系列）、档（G档）、单位（300°C、MHz）、货币前缀（C$、US$、HK$），只要不在证券语境，都按这条放行。
- 仍按散文拒绝：
  - 超过 5 个词的片段。
  - 多词片段里有小写的普通词。
  - 出现两个以上普通英文词。普通英文词指三个字母以上的小写词，或者英文标题词表里的词，不分大小写。用连字符、斜杠、点号连起来的也拆开来数，所以「market-rally」「COVID-19-investors-flee」「MARKET/RALLY」「Apple Beats Estimates」仍被拒。
  - 裸的 JSON 字面量 true、false、null。
- 不算词条：
  - 下划线连起来的字段名，例如「my_article_status」。
  - 网址和主机名，例如「https://foo.io/bar」「（GlobeNewswire.com）」。
  - 照抄来源英文标题的片段。
  - 所在句子没有汉字的片段。
- 新闻的字段名翻译：原文含英文散文时不再逐词翻译。不然「article text truncated, source title available…」译完只剩一个「status」，会被当成词条放行。
- 校验版本名 `simplified-chinese-v4` 没改，任务身份不变，队列不作废。经核验热点的已知标签翻译 `_translate_verified_focus_prose` 没动。

### 保留的红线

协调者要求的红线全部照旧，新测试 `tests/test_ai_prose_terms_policy_20261010.py` 逐条验证：

- 「TSLA上涨」「A股价上涨」「IT股价上涨」「ESG股价上涨」「A公司宣布回购」被拒。
- 「股票600519上涨」按数字代码要求绑定。
- 网址、新闻身份不符、空字段和「x」占位、日文假名等其他文字，结果不变。

原来这些红线有一部分是靠「不认识的全大写片段一律拒绝」顺带守住的。放开词条以后，改成在 `_term_in_security_context` 里显式判断：

- 所有片段都适用：
  - 原有的证券语境判断（股价、股票、证券等名词，八个涨跌词，股票代码等前缀）。
  - 前后出现「代码」「编号」，中间只隔标点、空格或数字。
  - 后面两个字以内接涨跌词，可以先跳过紧跟的括号别名。涨跌词是原有 8 个，加上第一轮审查补过、复核撤回的 14 个（大涨、大跌、暴涨、暴跌、急涨、急跌、飙升、重挫、跳水、拉升、走高、走低、下挫、反弹）。这 14 个当初撤回是因为会误伤名单里的「IV飙升」「RSI反弹」；名单里的术语走不到这一步，所以这里可以用。这一条守住「T-Mobile US此前下跌约5.4%」「IT大涨后回落」。
- 代码样片段（1 到 5 个大写字母）另外适用：
  - 后面是「股」「涨」「跌」，中间只隔标点、空格或数字，例如「F股受到关注」。
  - 后接公司、集团、企业，例如「A公司宣布回购」「TSLA公司」。
  - 独自放在中文后的括号里，例如未绑定的「英伟达（NVDA）」「福特汽车（F）」。这是既有测试 `test_zh_prose_gloss_does_not_launder_ticker_shaped_codes` 的要求：代码不得借括号漂白。
  - 后接带符号的百分比或基点，例如「盘前TSLA +3.5%」。
  - 后接价格比较，例如「F>12美元」。

### 仍会被拒的写法

下面这些是规则的代价，都写进了测试（新测试文件的 `test_known_costs_of_the_guards_are_still_rejected` 和英文碎片拒绝清单）：

- 前后出现「代码」的正常写法：「用Rust代码重写」「用Go代码实现」，中间隔着逗号也一样。
- 后面两个字以内接涨跌词、但涨跌的是市场或价格：「Fed Pauses市场上涨」「Polymesh价格下跌」「Investors.flee市场下跌」。
- 括号里 1 到 5 个大写字母的术语注释：「系统（THAAD）」。不带括号的「部署THAAD系统」能过。
- 含两个以上小写普通词的术语：「risk-off」「efgartigimod alfa」。
- 超过 5 个词的名称：「Bank of New York Mellon Trust Company」。

### 顺带放松的检查

下面几类原来靠语言校验顺带拦住，按新口径都是词条，现在原样发布。翻译规则本身没变，对不上输入的照旧不翻译，只是不再报错。

- 与输入不符的复述：
  - 状态码对不上输入的「HTTP 401」「HTTP 403」「HTTP 404」：`test_http_status_is_kept_only_when_it_matches_the_input_failure`、`test_http_label_requires_matching_input_failure`、`test_trusted_links_do_not_relax_english_or_unknown_metadata`。
  - 状态值对不上输入的「unavailable」「available」：`test_a_status_value_that_differs_from_the_input_is_left_untranslated`、`test_b3_untranslated_single_words_are_terms_after_the_2026_10_10_policy`、`test_metadata_conversion_does_not_touch_url_or_non_news_validation`。
  - 宏观状态不是「active」时的「active」：`test_market_focus_does_not_translate_macro_status_without_exact_input` 等。
- 来源绑定。名称不必再出现在付费输入里：
  - 小写药名「berobenatide」。
  - 公司名「Hormel Foods」「Micron Technology」。
  - 期权提醒里的「Sweep」。
  - 产品系列「V系列」。
  - 没有萨班斯法案来源的「SOX」。
  - 读取投影：输入没提到的名称不再让热点周期对访客整体隐藏（`test_visitor_focus_cycle_shows_unnamed_terms_after_the_2026_10_10_policy`），任务行缺失时用精简上下文的投影也不再隐藏结果（`test_focus_projection_recovers_write_time_sources_from_linked_job`）。
- 未绑定的股票代码：不在证券语境时也按词条放行，例如信号分析里的「相关新闻显示NVDA供应链改善」，原先按幻觉实体拒绝（`test_result_may_reference_tickers_outside_security_context`）。在证券语境里照旧要求绑定，例如「NVDA股价上涨」。

### 改动的测试

红线用例一条没改。改动的都是断言「词条被拒」的用例：从拒绝清单移到新的「按新口径放行」测试里，断言原样发布，不翻译。共 136 项：

- `tests/test_ai_jobs_zh_contract.py`，95 项：
  - `test_chinese_text_rejects_english_fragments` 拆成两张清单。仍被拒的 77 句：英文散文 56、证券语境 9、其他文字 8、涨跌窗口 4。放行的 72 句移到 `test_term_fragments_are_published_after_the_2026_10_10_policy`，分五类：
    - 专名和公司名 22 句，例如 Tesla、Bank of America、Johnson & Johnson、Apple-Inc。
    - 单个英文词或标题词 24 句，例如 Breaking、reports、Investors、Market's。
    - 首字母大写、只含一个标题词的两词短语 13 句，例如 Crypto Crash、Trade War、H100 Markets。
    - 普通词加公司后缀 3 句：Market Inc.、Report LLC、Company Corp。
    - 连字符或点号连起来、只含一个普通词的复合词 10 句，例如 BANK.RUN、F-35-crash、PANIC-2026。
  - NYSEX 1 项。
  - 宏观状态词 17 项。
  - 10b5-2、11b5-1 共 2 项。
  - Hormel Foods 没有来源 1 项。
  - HELLO WORLD 1 项。
  - berobenatide 没有来源 1 项。
- `tests/test_ai_analysis_fixes_20261010.py`，7 项：
  - 状态值对不上 1 项。
  - 热点里的「article」1 项。
  - 状态码对不上 3 项。
  - 「Galaxy 18 Pro」「iPhone 18 Pro Deluxe」2 项。这两句原先放在红线测试函数里，但没有证券语境。
- `tests/test_ai_review_fixes_20261010.py`，3 项：单独的「text」「available」「status」。
- `tests/test_ai_jobs_audit_2026_09_25.py`，3 项：
  - 报错带出被拒片段的例子从「Foobar」改成一段英文散文。
  - 没有来源的「Sweep」。
  - 没有公司名的「Micron Technology」。
- `tests/test_claude_provider.py`，1 项：「同为BDC」。
- `tests/test_news_output_metadata.py`，6 项：
  - 状态值对不上 1 项。
  - 来源不符的「V系列」4 项。
  - 没有来源的「SOX认证」1 项。
- `tests/test_verified_focus_prose_compatibility.py`，4 项：「公司800MHz」「频率MHz」「800MHzExtra」「8GbpsExtra」。翻译照旧不改写它们。
- `tests/test_ai_prose_allowlist_20261010.py`，9 项：PR #240 用来检验各条规则够窄的反例。
  - 括号或单独的字母 5 句。
  - 「福特汽车F，部分分析师」1 句。它和留下的「福特汽车（F），部分分析师」是一对：括号版仍按代码别名被拒；裸版没有协调者定义的证券标记，放行。
  - 「频率MHz」「公司800MHz」「800MHz公司」3 句。
- `tests/test_catalysts_audit_2026_09_25.py`，1 项：访客读热点周期，摘要里有输入没提到的药名时不再隐藏整个周期。
- `tests/test_luna_news_receipt_normalization.py`，4 项：状态码对不上输入的「HTTP 403」3 项，输入没有失败记录时的「HTTP 404」1 项。
- `tests/test_personal_catalyst_service.py`，1 项：任务行缺失时的投影不再隐藏结果。
- `tests/test_signal_context.py`，2 项：不在上下文代码表里的「NVDA」（非证券语境），没有来源的「Hormel Foods」。

改名的测试函数有七个，旧名字在新口径下不成立：

- `test_zh_prose_still_rejects_unbound_lowercase_entity_without_source`
- `test_candidate_retains_local_rejection_of_empty_placeholders_and_unapproved_abbreviations`
- `test_product_series_requires_exact_series_source_and_local_context`
- PR #240 的两个反例测试
- `test_visitor_focus_cycle_still_hides_entities_the_payload_never_named`
- `test_result_may_reference_context_tickers_but_not_strangers`

### 检查记录

- 新测试 `tests/test_ai_prose_terms_policy_20261010.py` 70 项，在本分支全部通过。在 main 的 `94cf52e2` 导出树上跑的是加最后一句已知代价之前的 69 项：31 项正例里 29 项失败（另 2 句「600 MHz」「600MHz」在 PR #240 已放行），其余 38 项反例、红线和已知代价全部通过。后加的「Fed Pauses市场上涨」在 main 上本来就在英文碎片拒绝清单里。
- 变异实验 14 个，每个守卫一个，都被专门的反例抓住：
  - 去掉原有证券语境判断：「CoreWeave股票受到关注」。
  - 去掉涨跌窗口：「T-Mobile US此前下跌约5.4%」「IT大涨后回落」。
  - 去掉括号别名：「特斯拉（TSLA）」。
  - 去掉下划线判断：「my_article_status」。
  - 去掉主机名判断：「（GlobeNewswire.com）」。
  - 子词只按空格拆：「market-rally」。
  - 去掉 JSON 字面量：true、false、null。
  - 去掉代码标签：「active（代码）」。
  - 去掉公司后缀：「A公司宣布回购」。
  - 去掉带符号涨跌幅：「盘前TSLA +3.5%」。
  - 去掉价格比较：「F>12美元」。
  - 去掉汉字要求：「Polymesh。」。
  - 去掉「股」字：「F股受到关注」。
  - 去掉翻译前的散文检查：那句 B3 英文。
- 定向测试 16 个文件全部通过。
- 完整后端测试跑了两次：
  - 第一次在更新既有测试之前：6,834 项通过、8 项失败。失败都在没有列入定向范围的四个文件里（热点访客投影、Luna 引用归一化、催化剂投影、信号分析），都是词条被拒的断言，已按新口径更新，算在上面的 136 项里。只跑定向文件发现不了它们。
  - 最终版本：6,847 项通过、7 项跳过（7 个子测试通过），退出码 0。之后只改了一处代码注释，并在新测试文件里加了一句已知代价，新文件 70 项与 `test_ai_jobs_zh_contract.py` 765 项单独重跑通过。
- `python -m compileall -q backend/app` 通过，`git diff --check origin/main...HEAD -- . ':(top,exclude)frontend'` 干净。

### 未覆盖

- 证券标记不紧挨名称时不按证券语境判断。例如「CoreWeave发布财报后股价大涨」现在能通过。这和协调者「紧挨」的定义一致。
- 单个普通英文词（「Breaking」「reports」「Investors」）和只含一个标题词的两词标题（「Crypto Crash」）会原样发布。要拦它们需要一份常用英文词表，本次没有加。
- 两字窗口之外的涨跌说法（「收于250美元」「股价随后走低」）不另作判断。
- 部署后要跑一次找回试运行（「部署后操作」第 2 步），看第二轮剩下的失败里有多少转为 validated。

## 热点条带可见性（2026-10-10）

分支 `claude/hotspot-strip-visibility-2026-10-10`（PR #244），基于 main 的 `77013b02`。

### 两层原因

新闻页「市场热点」条带一直显示「当前时段暂无热点」。生产的热点模型是 Sonnet，访客读取走经核验分支，只展示最近 72 小时内 Sonnet 核验周期判为有支持证据的事件。生产只读诊断（10-10）：核验原始条目 0 条，投影后 0 条。

1. 展示层二次校验。核验结果写入发布表时去掉了来源字段，`PersonalCatalystService._project_hotspots` 再做中文校验时没有上下文。「通信塔REIT股票因……下跌」这类标题因为 REIT 绑定不到来源被丢掉。这些文字在每次读取时已经按付费回执和完整输入重新校验过。
2. 核验覆盖跟不上。原规则要求核验周期覆盖的事件版本和当前快照完全一致。事件每加入一条报道就换一个版本；每小时的定时周期又要等前 20 个热点的代表新闻全部分析完才启动（`run_scheduled` 的 `focus_pending_news_ids`）。最近一次周期 10-09 16:31Z 创建，10-10 06:28Z 才完成，之后没有新周期，条带长期为空。

### 用户决定

- 核验结论沿用到同一事件的新版本：按事件编号（`event_group_id`）匹配，72 小时窗口不变。同一事件在窗口内有多轮核验时取最新一轮；最新一轮判「有矛盾」或「无法核实」时不展示。这取代了原来「新版本不继承旧核验结论」的规则。
- 没有新鲜核验时，用代表新闻有已发布分析的热点补齐，并标明「未核验」。

原因：每来一条报道事件就换版本，定时周期又要等前 20 条分析齐，按版本严格匹配时条带长期为空。

### 新规则

实现在 `backend/app/services/catalysts/local_intelligence.py` 的 `LocalCatalystIntelligence.hotspots`（经核验分支）和 `backend/app/services/catalysts/personal_service.py` 的 `_project_hotspots`。

- 覆盖判定：核验周期的输入里有这个事件编号就算覆盖，不再比较版本。「最新一轮决定」，以及「无法校验的较新一轮占住位置、不让更早的支持复活」，这两条照旧。
- 显示为已核验的条件：最新一轮通过付费回执和发布记录的全部校验，结论是 supported；被核验版本的输入和当时快照的行一致；当时快照和当前快照都通过完整性校验。
- 已核验条目的标题和摘要用核验周期里的中文文字；代码、热度分、来源、代表新闻等元数据取当前快照的事件组；`verified_at`（卡片上的「核验于…」）和 `verification_as_of` 照旧。
- 补齐：已核验条目排在前面，后面按当前排名补上代表新闻有已发布分析的热点，按事件编号去重，带 `verification_status: "unverified"`，没有 `verified_at`。访客和站长相同。字段沿用仓库已有的 `verification_status`：已核验是 `"verified"`，旧周期是 `"legacy_unverified"`。
- 两处取舍：
  - 补齐只取核验读取的候选窗口（当前快照前 100 名、72 小时内），不取未开核验时访客读取用的前 200 名。只有候选窗口里的事件读过核验状态，这样判了「有矛盾」的事件不会以「未核验」混进来。
  - 最新一轮无法校验（记录损坏、回执对不上）时，事件按「没有新鲜核验」处理，有已发布分析就标「未核验」显示。「无法校验即不算核验」这条只用于「已核验」这一档。
- 前端 `HotspotsStrip.tsx`：有核验时间的卡片显示「核验于…」，没有的显示中性色「未核验」小徽标；两种状态行同高。卡片按 `verifiedAt` 判断，因为热点类型（`src/mocks/fixtures2.ts`）和映射（`api.ts` 的 `nHotspot`）不在这次允许改的文件里。对生产接口两者等价：已核验条目都带 `verified_at`，补齐条目都不带。代价是未开核验的配置和演示数据里，所有卡片都显示「未核验」。英文 Unverified，日文 未確認。

### 测试

后端 `tests/test_hotspot_strip_visibility_20261010.py`，共 13 项：

- 旧版本的支持结论在新版本上仍显示：文字来自核验周期，版本和快照编号是当前的，核验时间不变。
- 最新一轮判「有矛盾」或「无法核实」（参数化）时，即使事件有已发布分析也不显示，站长和访客都查；没被覆盖的事件照常补齐。
- 核验不足时用已发布分析补齐：已核验条目排前，即使补齐的条目排名更高；同一事件只出现一次；补齐条目带 `verification_status: "unverified"`，没有 `verified_at`。访客、站长各一条。
- 此前的 8 项：Sonnet 核验标题「通信塔REIT股票因太空探索技术公司与格兰管理公司的频谱交易下跌」访客和站长都能看到；未开核验时访客只读有已发布分析的热点，SQL 里先过滤再截断；英文原标题和占位文案不显示。

按用户决定改的既有测试（`tests/test_verified_hotspot_publication.py`）：

- `test_same_id_new_version_never_inherits_old_paid_support` 改为 `test_same_id_new_version_keeps_the_newest_round_support`。
- `test_changed_event_cannot_reuse_support_but_unchanged_event_remains` 改为 `test_changed_event_keeps_its_newest_round_copy_until_the_next_round`，测试里写明代价。
- `test_unrelated_revision_preserves_paid_event_identity_and_original_verification_time` 只比较核验相关的字段，快照编号改为当前。
- `test_paid_haiku_news_and_legacy_cycle_survive_new_model_configuration`：旧周期仍不产生已核验条目，新闻 11 的已发布分析以「未核验」补上。

前端：`frontend-src/tests/catalysts-source-ui-contract.test.mjs` 的卡片渲染测试加了「未核验」徽标的断言。

变异检查：去掉「被否定的事件不进补齐」，两项否定测试失败；恢复按版本匹配，「旧版本支持仍显示」失败。

### 检查记录

- 热点相关 14 个测试文件 719 项通过；完整后端 6,861 项通过、7 项跳过；`python -m compileall -q backend/app`、`git diff --check` 通过。
- 前端：`tsc -p tsconfig.app.json --noEmit` 通过；lint 通过，只有 `pages/Breakouts.tsx` 两条既有警告；node 测试 1,293 项通过；产物断言通过。`VITE_API_MODE=live` 构建后同步到 `frontend/`，`diff -r frontend-src/dist frontend` 为空。本机 Node 是 24.6，CI 是 22.17，锁文件相同，以 CI 的字节闸门为准。
- 浏览器面板用演示数据看过：卡片标题下显示「未核验」，各卡片底部对齐。

### 未覆盖

- 同一事件里来了更正或撤回报道时，下一轮核验之前仍显示旧的已核验标题。这是按事件编号沿用结论的代价。
- 最新一轮的记录损坏、而它其实判了「有矛盾」时，事件会标「未核验」显示。
- 生产目前关了定时分析（运行设置 `catalyst.scheduled_analysis_enabled=false`）。部署后条带先靠补齐显示，已核验条目要等恢复定时分析后的下一轮。
- 首页研判（`backend/app/services/market_brief/evidence.py`）读同一个热点接口，现在也会拿到「未核验」的补齐条目，它的证据行不带核验字段。
- 定时周期的启动条件（前 20 个热点的代表新闻全部分析完）没改。
- 给热点类型和映射加上核验状态字段，卡片就不用靠 `verifiedAt` 推断。这要改 `api.ts` 和 `src/mocks/fixtures2.ts`，这次没做。
