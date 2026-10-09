# 模型新闻分析与热点追踪报错修复（2026-10-10）

分支 `claude/ai-fixes-2026-10-10`，基于新闻分支 `claude/news-ingest-2026-10-09` 的头 `82e2312d`（最初在性能分支旧头 `1cbf7915` 上开发，2026-10-10 用 `git rebase --onto 82e2312d 1cbf7915` 变基，没有冲突）。取证文件在性能工作区的 `evidence/ai-errors-2026-10-10.txt` 与 `evidence/ai-failed-samples-2026-10-10.json`（2026-10-09 17:2x UTC 的只读快照）。生产运行 `72f426f0`。

| 提交 | 内容 |
| --- | --- |
| `6af90150` | 中文校验器的误判修正 |
| `10a7db09` | 输出上限、等待时限、日额度放宽，以及旧身份任务的放行规则 |
| `8e10dad9` | Luna 只在没拿到正文时联网 |
| `86a05b61` | 找回工具按时间批量重验本地回执 |
| `3f4507ca` | 热点周期失败时保留原因 |

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
- 修法：标签按点号边界比对主机名（`sec.gov` 能匹配 `www.sec.gov`，`ec.gov` 不能）；括号里受信的裸网址同样去掉；不在联网记录里的网址照旧以 `ai_news_unbound_or_unhandled_url` 拒绝，不为了过中文校验抹掉未核实的来源。校验器另外把括号里只有小写域名的来源标注（含「来源：」前缀、多个域名用顿号分隔）整段移除，所有任务类型通用。
- 测试：10 条生产回执重放；标签与链接不符、可信与不可信裸网址、括号外的域名照旧被拒。

### 2. 字段名回显（article、source、http、unsupported）

- 根因：模型复述输入字段名与状态值，例如「输入article标记为……」「article_reason为http_403」「unsupported_encoding」。原有翻译只覆盖少数几个名字，且 `article` 只在正文不可用时翻译。
- 修法：新闻任务里，与本条载荷字段名、正文字段名、结果字段名或抓取状态值逐字相同的小写标识符，在非证券语境下不计入英文。`my_article_status` 这类近似写法、「股票代码allowed_tickers」、对不上本条输入的 `http_404` 照旧拒绝；其他任务类型没有这项豁免。Luna 提示词同时要求不照抄字段名。
- 测试：正反各一组，含 `article` 缺席时、`not_requested` 时的 `unavailable`。

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
- 「复合SOFR加1.730%」：缩写后接加点（%、基点、bp）视为基准利率名，长于 4 个字母的 LIBOR 等也适用；「股票代码SOFR」照旧拒绝。
- 「p<0.001」「n=712」「p值」：统计量写法。
- IT（「企业IT服务」）、URL（「输入URL」）并入通用技术缩写，证券语境仍要求绑定（IT 也是股票代码）。

### 7. 热点周期（market_focus，Sonnet）

- `unbound_numeric_security_code`：错误信息只显示字段开头「（一）电信与消费……」，真正被拒的是同一字段里的「发现矿业股份5000万美元」，另一处是「普通股5000万美元」。数字前是「股份」「普通股」，被当成证券代码。修法：数字后紧跟万、亿、美元、元、%、倍或量词「股」时是数量；「股票600519上涨」「腾讯（00700）股价」「证券代码700股价」照旧拒绝。中文序号「（一）」本来就不触发。
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
- 身份：现行 Luna 新闻身份分两种，缺正文 `e46819f9…`（`LUNA_WEB_NEWS_IDENTITY`），有正文 `e64f4252…`（`LUNA_ARTICLE_NEWS_IDENTITY`），新建任务按载荷选。读取与提交时两个变体都算当前，因为调用方只拿得到模型的默认变体。
- 预留：提交时按任务自己的载荷计算，有正文的任务不预留 3 次搜索费。当日词元账里尚未结算的行仍按模型级（缺正文）口径计入，共享美元预算启用后它只作统计。

### 旧任务的判定规则

一个任务存的身份算当前，当且仅当：等于现行身份（Luna 两个变体任一），或者是 `runtime._IDENTITY_PREDECESSORS` 里登记的、某个现行身份的确切上一版：

| 现行身份 | 放行的上一版 |
| --- | --- |
| Luna 缺正文 `e46819f9…`、Luna 有正文 `e64f4252…` | `719aed21…`（10-09 起一律联网，32,768）、`d0e6936d…`（更早的不联网版） |
| Terra 新闻 `e2f66048…` | `d0e6936d…`；v6 提示词的 `e35f6bc0…` 先按原有规则映射到 `d0e6936d…` |
| OpenAI 财报 `07071987…` | `efcf4a6d…` |

- 名单只按确切哈希放行。以后输出上限、提示词或结构再变，现行身份不再是名单里的键，旧任务照常判 `runtime_configuration_changed`（测试把上限改成 98,304 验证）。
- worker 提交前用任务自己的模型与载荷判定，通过后按现行策略构造请求：队列里的旧任务有正文就不带工具，缺正文就联网，输出上限都是 65,536。
- 已完成的旧身份结果在新闻列表里照常展示，也不会因此再建付费任务（测试覆盖）。
- Claude 各模型（Haiku、Sonnet）与热点的身份本次没有变化。

## 放宽的限制

| 项 | 原值 | 新值 | 为什么安全 |
| --- | --- | --- | --- |
| OpenAI 新闻、财报输出上限 | 32,768 | 65,536 | 思考词元计入上限；生产最大 25,392，原上限只剩不到 1.3 倍余量。Luna 官方输出上限 128,000、上下文 1,050,000。上限只影响预留与截断，不影响并发。 |
| 单任务预留（Luna 新闻） | 93,130 微美元 | 缺正文 124,260（含搜索 30,000）；有正文 97,076 | 结算时按实际用量改写；并发 4 时同时占用最多约 0.5 美元。词元预留：缺正文 128,000，有正文 139,264。 |
| 付费分析最长等待 `openai_background_poll_timeout_seconds` | 1,800 秒 | 3,600 秒 | 到点会取消 OpenAI 后台响应、把 Claude 流（含 Sonnet 热点）记成结果未知，已付费的工作作废。最坏情况是一个卡住的响应多占一个并发槽半小时。 |
| 仓库默认预算 `[model_budget]` | 0（退回日词元额度 1000 万） | 10 美元、不强制 | 与生产一致：日词元只作统计、不拦截，费用照常记录。服务器部署时保留自己的 personal.toml，本来就是这组值。 |

`daily_token_limit` 的 0 不是「不限」：配置要求 102,400 至 100,000,000，0 会被校验直接拒绝；日额度只在 `model_budget.daily_budget_usd = 0` 时拦截。`budget_blocked` 是终态，不占队列；新闻调度在下一个 UTC 日重试前一天被拦的任务，财报同日内视为已存在、次日重试，10-08 的旧行不会再被当作活跃任务。

查过、维持不变的时限：

- `openai_job_max_age_seconds` 86,400 秒：只把 24 小时后仍在排队或运行的响应判过期；已完成的响应照常取回。
- 手动刷新冷却 30 秒：只限制手动触发频率。
- 热点意图 preparing 10 分钟：只作废从未产生付费任务的意图，已有付费任务的会被重新关联。
- 调度认领 10 分钟、控制请求 30 秒：不涉及付费结果。
- 单次请求读超时 900 秒：流式连接 15 分钟没有任何数据才触发，属于连接故障，不是过短的时限。

## 找回已付费结果

`python -m app.tools.recover_ai_schema_results` 新增 `--failed-since`（可配 `--job-type`、`--limit`）：选出该时刻之后失败、错误码属于可恢复类（`schema_validation_failed`、`provider_unavailable`、`local_storage_error`）、结果为空且本地存有供应商回执的任务，用本地回执按现行校验重验。不加 `--apply` 只试运行；加上后写回为 completed，计费、用量与回执原样保留，不发任何供应商请求。标准输出是逐条结果，标准错误输出是按状态的计数。

- Luna（除最早的不联网旧版）、Haiku、Sonnet 的失败行都有本地回执。
- Terra 与最早的 Luna 旧版没有本地回执，只能逐个 `--job-id`，工具会向 OpenAI 取回已存的响应（不重新生成，不计费）；本次不建议批量做。
- 测试：用 3 条生产 Luna 回执构造失败行，试运行全部 validated，`--apply` 后 completed，结果通过新校验；没有回执的行不会被批量选中。
- 与历史清理一致：新闻与热点的清理（`prune_scheduled_history`）永不删除带回执的失败任务，找回工具能选中的行都在保留范围内。财报另有 30 天保留（`prune_earnings_retention`），旧回执会被删除，财报不在本次范围。
- 读取路径会按「校验函数、结果字节、载荷」缓存校验结论（`validate_result_cached`）。本次的规则只依赖这三样，部署重启会清空缓存，恢复后的结果和旧结果都按新规则重新判定。
- 恢复后的新闻在下一轮整理（约 2 分钟）出现在列表里。热点周期的付费任务恢复后，下一轮整理会按已完成的任务重新发布该周期（`_publish_completed_focus` 处理所有结果为空、未取消的周期，有回归测试）。

## 热点卡的失败原因

前端热点卡读周期的 `error_code`，按它显示原因（例如「模型返回的结果没有通过格式或语言检查，请重试」）。此前身份仍为现行的付费任务失败时，周期只改了状态、`error_code` 一直为空，卡片只能显示笼统的「这次分析没有完成」。现在失败与额度受限的周期记下任务的原因码；owner 读取的周期（`/api/catalysts/market-focus-cycles/latest`、`/{cycle_id}`）另带 `error_detail`，即任务落库的校验字段路径与被拒片段。访客投影不含这两项。前端未改。

## 部署后操作

1. 部署后核对服务器 `config/personal.toml` 仍是 `[model_budget] daily_budget_usd = 10.0`、`enforce_limit = false`。
2. 找回试运行（只读），在挂载 `/data`、能导入 `app` 的后端容器里执行（容器内若找不到 `app` 模块，按服务进程的工作目录补 `-w` 或 `PYTHONPATH`）：

   ```
   docker exec option-pro-backend-1 \
     python -m app.tools.recover_ai_schema_results \
     --failed-since 2026-10-03T00:00:00Z \
     --job-type news_impact --job-type market_focus > /tmp/ai-recover-dry.json
   ```

   看标准错误输出里的计数，以及 `validation_failed` 各条的 `error`。按取证的 72 小时数字估算，有本地回执的约为 Luna 新闻 171 条、Haiku 新闻 458 条、Sonnet 热点 2 条（7 天窗口会更多；默认最多选 2,000 条，超过时调大 `--limit`）；Terra 新闻 110 条没有回执，不会被选中。
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

## 未覆盖

- 热点样本的回执被截断，只能逐段重放；`'p'` 那一处看不到原文。
- Haiku 新闻样本（MSCI ACWI、Bloomberg、WISeKey、SPAC、ATOMIC、TRADE）没有载荷，无法重放，部分可能仍不通过，以找回试运行为准。
- `news_identity_mismatch` 未诊断。
- 当日词元账对未结算的 Luna 有正文任务按缺正文口径计入，少记约 1.1 万词元/条；共享美元预算启用后该账只作统计。
