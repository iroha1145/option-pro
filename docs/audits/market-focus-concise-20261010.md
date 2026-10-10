# 热点分析输出简洁化（2026-10-10）

分支 `claude/focus-concise-2026-10-10`，基于 main `2a5f7d36`（已含第三轮 #242 与 #243）。只改后端输出；热点追踪卡（`FocusCycleCard.tsx`）由另一个分支（PR #246）改。

## 起因

用户反馈热点追踪卡可读性太差。取证用的是生产周期 `mfc_7b55740d768c41428129bcab78471413`：2026-10-10 06:28:17Z 完成，Sonnet 5.5 核实模式。取自 08:18Z 的 `/api/catalysts/market-focus-cycles/latest`，原样存成 `tests/fixtures/market_focus_cycle_20261010.json`。

- 标题（`title_zh`）是固定的「市场热点分析」。
- 导语（`headline_summary`）、总结（`summary_zh`）、市场摘要（`market_summary`）一字不差，都是 2,311 字。
- 不确定性（`market_uncertainties`）为空。
- 前一轮（2026-10-09 07:59Z 完成）同样如此，三段都是 1,935 字。

这些不是模型写出来的，而是后端公开投影 `focus_verification.public_focus_result` 的产物：

- 核实模式原来只公开已证实事件自己的摘要（`823c3a74` 定下的规则：模型写的全局文字可能混入未证实的事件，只留在内部审计）。投影把全部已证实事件的摘要用换行拼起来，同一段文字同时填进导语、总结和市场摘要，超过 3,000 字截断；标题写死，不确定性写死为空；核实记录不公开。
- 主导事件（`dominant_events`）取前 8 个已证实事件，摘要就是拼接文字里的前 8 行。8 条摘要互不相同（162 至 235 字），重复发生在主导事件和三段总文字之间。

所以只改提示词、只收紧模型输出长度，生产卡片不会有任何变化，必须同时改投影。

## 公开投影

按协调说明（2026-10-10）改为：新任务公开模型自己的各字段，并附每个事件的核验结论。

- 按任务自己的 schema 版本分流（`schema_version` 属于 `models.CONCISE_FOCUS_SCHEMA_VERSIONS` 即新任务）：
  - 新任务：标题、导语、总结、市场摘要、不确定性、主导事件、行业列表、两个状态标记都原样取模型输出。另加 `event_verifications`，每个候选事件一条，只有 `event_group_id`、`verdict`（`supported`、`contradicted`、`unverifiable`）和 `verified_at`（付费任务的完成时间，与热点列表条目的 `verified_at` 一致）。证据引用（工具编号、网址）不进公开结果；来源链接仍按原约定只经 `public_focus_sources` 给出（只取支持已证实事件、工具调用成功的来源，最多 10 个）。
  - 旧任务：原投影不变。
- 个股评估仍只发布全部引用都是已证实事件的那些，两种任务都一样。这是针对个股方向判断的规则，协调说明没有要求放开。
- 热点列表（`hotspots()`）的规则不受本分支影响。
- 字段名与 PR #246 的 `nCycleRecord` 对齐：`title_zh`、`headline_summary`、`summary_zh`、`market_summary`、`market_uncertainties`、`dominant_events[]`（`event_group_id`、`summary`、`affected_sectors`）、`event_verifications[]`（`event_group_id`、`verdict`）。

### 为什么要按 schema 版本分流

协调说明里说「发布表存的是投影输出，旧周期不会自动变好」，这一点与代码不符。`personal_service._project_focus_cycle` 每次读取都从内部结果重新校验、重新投影；`catalyst_local_verified_focus_publications.public_result_json` 只用来确认发布记录存在，不会原样返回前端。任务公开视图（`AIJobRepository.public`）同样每次重新投影。如果不分流，所有历史周期都会立刻公开旧提示词下写的全局文字，而那些文字没有「只依据已证实事件」的约束。

所以由任务自己的 schema 版本决定投影方式。三个调用点都传入付费任务的 `schema_version` 和完成时间：`AIJobRepository.public`、发布时的 `_publish_verified_focus`，以及 `_focus_cycle_from_row` 附带的私有字段 `_projection_job`（`personal_service` 在返回前去掉，访客和 owner 都看不到）。

### 风险

新任务的全局文字原样公开后，被否定或无法核实的事件内容可能出现在卡片的全局文字里。提示词要求这些字段只依据已证实事件下结论，提及其他事件时写明「未证实」或「与来源不符」，但程序无法逐字核对。`test_verified_hotspot_publication.py` 的夹具就是例子：它的全局文字把被来源否定的并购写成了事实，按新规则会原样公开。

## 提示词与结构说明

两种模式共用一段字段分工，写在提示词里，结构说明（`claude_output_schema`）同步：

| 字段 | 职责 | 目标长度 | 新输出上限 |
| --- | --- | --- | --- |
| `title_zh` | 本轮主线，不得用「市场热点分析」这类泛称 | 30 字 | 60 |
| `headline_summary` | 一两句导语 | 120 字 | 240 |
| `summary_zh` | 市场层面的结论与限制，三到五句，不重复导语 | 500 字 | 1,200 |
| `market_summary` | 只写输入市场状态（如市场广度、宏观环境）与事件的关系 | 300 字 | 800 |
| `dominant_events[].summary` | 标题式短句，再写「已证实：」「部分证实：」或「未证实：」开头的事实句，最后写「推断：」开头的推断句 | 220 字 | 400 |
| `focus_ticker_assessments[].summary` | 证据与方向判断的理由 | 160 字 | 320 |
| `focus_ticker_assessments[].risks[]` | 每条一个风险 | 40 字 | 80 |
| `market_uncertainties[]` | 每条一个具体的不确定因素，存在不确定性时必须列出 | 60 字 | 120 |

另有一句「不同字段之间不得整段重复」。核实模式再加两段：

- `event_verifications` 每条的标题不超过 30 字，摘要按上面主导事件的写法、不超过 220 字。热点列表用的是这两个字段。
- 标题、导语、总结、市场摘要、不确定性和主导事件会原样公开：结论只能依据已证实事件；提及无法核实或被否定的事件时，必须写明「未证实」或「与来源不符」，不得当作事实。

## 新输出的长度上限

- 上限约为目标的两倍，只拦明显失控的输出。新增的严格模型是 `ConciseMarketFocusResult` 与 `ConciseVerifiedMarketFocusResult`，请求里的结构也由它们生成。
- 只在接收新输出时生效，按任务自己的 schema 版本判断：worker 的两条完成路径在原有校验（`receipt_result`、`response_result`）之后再调用 `runtime.enforce_new_output_limits`；找回已付费结果（`recover_schema_validation_failure`）校验提交的结果时传入任务的 `schema_version`。原有两个校验函数的签名没有改，避免和同时在改它们的分支冲突。
- 读取历史结果、发布前复核（`_verified_focus_paid_result`）仍用原来的宽松上限（标题 500 字，正文 2,000 至 3,000 字）。生产上已完成的周期按旧上限写入，读取时改用新上限会把它们整轮隐藏。
- 「不同字段之间不得整段重复」没有做成校验：拒收一次就要重付整轮 Sonnet 核实费用，只靠提示词约束。

## 版本与在途周期

- 提示词 `market-focus-zh-cn-v6` 升到 `v7`；schema `market_focus_zh_cn_v5` 升到 `v6`，`market_focus_verified_zh_cn_v1` 升到 `v2`。
- 处理方式：按确切的上一版身份放行，不退役。三种模型的上一版身份逐个列在 `runtime._IDENTITY_PREDECESSORS`，新的现行身份也钉成常量；`v6` 列入新的 `FOCUS_READABLE_PROMPT_VERSIONS`，引擎的身份闸门 `_has_current_job_identity` 改用这个集合。

  | 模型 | 上一版身份 | 新身份 |
  | --- | --- | --- |
  | Haiku | `market_focus_zh_cn_v5` / `25bf4cbe…` | `market_focus_zh_cn_v6` / `e95c0b1b…` |
  | Sonnet 核实 | `market_focus_verified_zh_cn_v1` / `0fb6fd7a…` | `market_focus_verified_zh_cn_v2` / `aae4f974…` |
  | OpenAI | `market_focus_zh_cn_v5` / `6c3d008c…` | `market_focus_zh_cn_v6` / `6fefeec2…` |

- 不退役的原因：核实热点列表（`hotspots()`）只采用身份为现行的周期，引擎身份闸门又只认现行提示词版本。直接退役会让生产上的核实热点列表在下一轮完成前整体变空。
- 放行的范围：
  - 已完成的 v6 周期与已发布的核实热点照常显示，投影保持原样。
  - 排队中还没提交的 v6 任务按 v7 请求提交。它们记的仍是旧 schema 版本，完成时按原上限校验，公开时也按原投影。
  - 升级前就不算现行的 v6 任务（身份不在前驱表里）照旧退役。`_recoverable_completed_focus_public_job` 对可读提示词仍要求确切身份，不会借这次升级复活旧任务；更早的提示词家族行为不变。
  - 输出上限等策略再变时，现行身份不再等于前驱表的键，旧任务照常判 `runtime_configuration_changed`。

## 回归测试的设计

协调要求「用生产那条结果做回归测试，新 schema 下必须被拒」。那三段 2,311 字是投影的产物，从不经过模型校验，所以拆成几件事来测：

1. 旧任务的投影与生产公开结果完全相同（用重建的内部结果投影，逐字段比较）。
2. 同一份内部结果按新任务投影时，公开模型字段和 20 条核验结论（12 条已证实、7 条无法核实、1 条与来源不符）。
3. 同样的三段如果作为新输出交上来，核实与非核实两种模式下都因超长被拒（`string_too_long`）；作为历史结果读取照常通过。
4. 协调要求的投影测试：三段各不相同、带核验记录的新输出，公开后各字段原样保留，每个事件带 `verdict` 和 `verified_at`，没有工具编号和网址，引用了被否定事件的个股评估不发布。

重建方法：fixture 的 `result` 原样保留生产公开结果。内部核实记录是重建的：12 条已证实事件的摘要逐行取自 `summary_zh`；事件 1 至 8 的编号与行业取自主导事件，事件 9、10 的编号取自个股评估的引用。事件 11、12 的编号、全部 12 个标题、事件 9 至 12 的行业，以及其余 8 个候选事件都是合成值，fixture 里写明了。重建后按旧规则投影，与生产公开结果完全一致。

## 部署注意

- 卡片分支（PR #246）按 `dominant_events` 与 `event_verifications[].verdict` 渲染徽标，应与本分支同时上线或先上。
- 不需要迁移数据，也不需要重跑历史任务。旧周期保持原样，新周期按新规则展示。

## 检查记录

- 新增 `tests/test_market_focus_concise_20261010.py`，42 项。
- 改动的既有断言 15 处：
  - 规则变化 7 处，都在 `test_verified_hotspot_publication.py`（5 个测试函数，其中一个参数化两次）：原来断言公开结果里没有「虚假」、没有 `event_verifications`、全部无法核实时总结是「当前暂无可展示热点。」、用主导事件个数判断已证实事件。新规则下改为断言模型字段原样公开、核验结论正确、没有证据引用和工具编号、私有字段不外泄。那份夹具的全局文字把被否定的并购写成了事实，按新规则会原样公开，见「风险」。
  - 版本号、钉住的身份和请求结构的来源 8 处：`test_ai_jobs.py` 1 处、`test_ai_jobs_zh_contract.py` 1 处、`test_worker_model_routing.py` 1 处、`test_macro_ai_context.py` 2 处（「输出 schema 名称不变」那个测试改名为「输出 schema 名称仍属可读家族」，并补一句旧名称 v5 仍在家族里）、`test_ai_jobs_audit_2026_09_25.py` 1 处、`test_runtime_schema_cache.py` 2 处（对照结构改由 `result_model_for(..., concise=True)` 生成，与请求一致）。
- 变异实验 18 个全部被新测试抓住，每次都核对源文件已复原：新任务套用旧投影、旧任务套用新投影、核验记录带出证据引用、个股评估不再过滤、读取路径不传 schema 版本、任务公开视图不传、发布记录不传、私有字段不去掉、校验忽略 schema 版本、删掉核实模式的前驱身份、可读提示词集合改回只认现行版本、可恢复闸门改回只认现行版本、Claude 完成路径跳过上限检查、请求结构改回宽松模型、找回路径不传 schema 版本、OpenAI 完成路径跳过上限检查、上限检查永不严格、删掉「公开字段只依据已证实事件」的提示。
- 完整后端测试（基于 `2a5f7d36`）：6,945 项通过，7 项跳过，7 个子测试通过，pytest 退出码 0。开发中在旧基点上跑过一次，有 2 项失败，都在 `test_runtime_schema_cache.py`（对照结构还是宽松模型），已改正。
- `python -m compileall -q backend/app` 通过；`git diff --check origin/main...HEAD -- . ':(top,exclude)frontend'` 干净。
- 合并检查（`git merge-tree`）：与 #246 无冲突；与 #244、#247 本分支不引入冲突。

## 未覆盖

- 新任务全局文字里的事件内容只靠提示词约束，见「风险」。
- 上限按目标的两倍估计，没有用生产输出的长度分布核对。最可能误拒的是标题：上限 60 字，Sonnet 写一个偏长的跨事件主题标题就会让整轮付费结果作废。若上线后出现 `title_zh` 的 `string_too_long`，可以只放宽标题的上限；其他字段同样可以单独放宽。
- 非核实模式（Haiku）目前没有在生产上运行。提示词和上限都覆盖了，但没有生产样本验证。
- 「存在不确定性时必须列出」无法机器判断，只在提示词里。
- 前端没有改，也没有跑 PR #246 的测试；只按它的 `nCycleRecord` 对齐了字段名。
- 中文校验器的词条规则没有改动。

## 来源绑定放宽（2026-10-10）

分支 `claude/focus-evidence-lenient-2026-10-10`，基于 main `858707ed`。用户反馈「热点追踪又失败了，把条件放的再松一点看看」。

### 取证

- 第六批上线后第一轮 Sonnet 核实周期 `aij_af48b5a12957427c9f0cad78c5a533b9`（schema `market_focus_verified_zh_cn_v2`）整轮失败，`schema_validation_failed`，原因 `market_focus_verification_evidence_ambiguous`。
- 来自 `models.validate_market_focus_evidence`：引用没有调用编号时，按规范化网址在回执里找成功调用，候选数不等于 1 就整轮报错（候选为 0 也报这个码）。
- 这轮回执有 114 次成功调用（搜索 113、抓取 1），没有同一网址被调用多次。核验 20 条（已证实 11、无法核实 9），没有编号的引用 33 个：32 个唯一命中，1 个为 0。没命中的那个是模型把网址截断了：引用 `…/stock-market-today-oct-9-at-and-t-slides-on-spacex`，回执里是 `…-slides-on-spacex-spectrum-deal-threat/?source=…`。
- 篇幅没有碰到本 PR 前面定的任何上限（标题 21 字、导语 73、总结 243、市场摘要 204），这次没改上限。

### 新规则

只改了 `validate_market_focus_evidence` 这一个校验函数。提示词、结构和任务身份都不变，有测试钉住三种模型的身份。worker 完成路径、找回已付费结果（`recover_schema_validation_failure`）、任务公开视图和发布前复核都调这个函数，所以走同一套规则。

1. 没有调用编号的引用：先按规范化网址精确匹配；同一网址被调用多次时，取回执里的第一次。精确匹配不到时，找「回执网址以引用网址为前缀」的唯一一条：比较前去掉查询串、片段和末尾斜杠，协议和主机必须相同，引用必须带域名之外的路径（只写域名不会命中）。绑定后，引用的网址换成回执里的完整网址。
2. 仍对不上的引用直接丢弃，不再整轮报错：网址无效、候选为 0、前缀命中多条网址、带编号但「编号加网址」不在回执里。重复引用只留第一条。
3. 丢弃之后，判 supported 的事件没有任何 supports 引用、判 contradicted 的事件没有任何 contradicts 引用时，把判定降为 unverifiable。热点条带只展示 supported 事件，所以这样是保守的。
4. 输入绑定保持严格：核验列表必须与输入事件逐一对应（编号与版本都对），否则仍报 `market_focus_verification_event_mismatch`。`validate_result` 本身也做同样的检查。
5. 规则是幂等的：已绑定的结果再绑定一次不变。发布前复核会分别绑定库里的结果和付费回执原文，再比较两者是否相同，这一点由测试覆盖。

### 代价

- 被降级事件的标题和摘要不进热点条带，个股评估也只发布引用全部已证实的那些。但模型的全局文字（标题、导语、总结、市场摘要、不确定性、主导事件）是按原文公开的，里面仍可能提到被降级的事件，只靠提示词约束。
- 前缀匹配只要求唯一。模型若把网址截得很短，恰好只命中一条不相干的回执，也会绑上去；引用必须带路径，并且协议和主机要相同，可以挡住只写域名的情况，但挡不住所有误配。
- 带了编号、但网址被截断的引用不做前缀匹配，直接丢弃。这是有意收窄的：编号和网址必须同时对上。
- 丢弃引用和降级判定都不记日志、不计数，看不出模型引用质量是否在变差。

### 仍会整轮失败的情况

- 核验列表与输入事件对不上：漏写、重复、编号或版本写错（`market_focus_verification_event_mismatch`）。事件编号是 32 位十六进制，模型抄错一位就会触发，和新闻摘要抄错是同一类问题。生产上是否发生过，我没有查到数据。
- 主导事件或个股评估引用了输入之外的事件编号或股票（`market_focus_event_binding_mismatch`、`market_focus_ticker_binding_mismatch`），以及结构、长度和中文校验不过（`schema_validation_failed` 的其他原因）。

### 找回

失败的那一轮有付费回执，错误码是 `schema_validation_failed`，部署后可以用找回工具按现行规则重验落库：先 `--job-id aij_af48b5a12957427c9f0cad78c5a533b9` 试运行，确认后加 `--apply`。命令写法同 `ai-analysis-fixes-20261010.md` 的「部署后操作」。

注意：找回工具的试运行只做结构和中文校验，不做来源绑定；来源绑定在 `--apply` 时才执行。所以试运行显示 validated 并不代表绑定后的结果，哪些引用被丢弃、哪些判定被降级，要在 `--apply` 后看库里的结果。这轮在本 PR 之前试运行也会显示 validated，`--apply` 才会失败。

### 测试

- 新增 `tests/test_focus_evidence_lenient_20261010.py`，20 项，夹具按这轮的形状合成（20 个事件、114 次调用、33 个无编号引用，截断的 fool.com 网址取自生产）：
  - 整轮不再报错，截断网址按前缀唯一命中、绑到正确的调用；再绑定一次结果不变。
  - 同一网址多次调用取第一次；前缀命中多条时丢弃；只写域名、主机或协议不同、比回执更长的引用都不会命中。
  - 唯一的支持引用被丢弃后，该事件降为 unverifiable，其他事件不受影响；contradicted 没有反证引用时也降级。
  - 重复引用去重；编号错误或编号与网址不配的引用丢弃。
  - 输入绑定的四种错误仍然整轮报错；任务身份不变。
  - worker 完成路径入库成功；找回工具 `--job-id` 试运行为 validated，`--apply` 为 recovered，用量、费用和回执不变。
- 改动的既有测试 3 个，都是规则改变：
  - `test_verified_focus_contract.py::test_invalid_verification_cannot_pass` 改名为 `test_invalid_verification_cannot_support_an_event`：输入绑定的 4 种错误仍断言报错；证据类的 7 种（回执为空、编号捏造、只有代码执行、调用失败、缺内容摘要、关系写反、私有网址）改为断言该事件降为 unverifiable、没有 supports 引用。
  - `test_null_id_resolution_cannot_guess_or_repair_false_id` 改名为 `test_null_id_resolution_never_guesses_or_repairs_a_false_id`：同一网址两次成功调用时改为绑到第一次；找不到和编号写错时改为断言丢弃并降级。
  - `test_verified_hotspot_publication.py::test_missing_tool_receipt_cannot_publish_even_if_job_was_marked_complete` 改名为 `test_missing_tool_receipt_downgrades_every_verdict_instead_of_failing`：回执里没有工具调用时，所有判定降为 unverifiable，周期正常完成，热点条带为空，公开来源为空。
- 变异实验 8 个全部被抓住：不做前缀匹配、允许只写域名、不降级、不去重、同网址取最后一次、错编号改按网址找、不比较协议和主机、去掉证据函数自己的输入绑定检查。
- 完整后端测试：7,100 项通过，7 项跳过，7 个子测试通过，pytest 退出码 0。`compileall` 通过，`git diff --check` 干净。
