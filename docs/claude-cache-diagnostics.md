# Claude 缓存诊断

Haiku 五类分析和 Opus 首页研判默认开启缓存诊断（Cache Diagnostics）。每次请求都带 `diagnostics.previous_message_id`；首次是 `null`，之后使用同一逻辑任务最近接收到的消息编号。分组为 `ai_jobs:earnings_impact`、`ai_jobs:option_alerts`、`ai_jobs:signal_analysis`、`ai_jobs:news_impact`、`ai_jobs:market_focus` 和 `market_brief`，不会按提示词哈希分组。Opus 以 `pause_turn` 暂停后的每一轮，都使用刚结束一轮的编号。

这项诊断不修改缓存断点、缓存时长、预算、计费、准入、回执或重试规则。已安装的 Anthropic Python 库原生支持 `diagnostics`，无需新增测试版请求头。诊断信息只用于管理员排查，普通页面接口不返回内部消息编号。

管理员在容器或后端环境读取本地记录：

```sh
python -m app.tools.claude_cache_diagnostics --limit 20
python -m app.tools.claude_cache_diagnostics --data-dir /data --task market_brief
```

命令只读文件，不创建模型客户端、不调用模型。输出包含所有保留记录的分类汇总与最近记录；`available=false` 表示文件不存在、损坏或无法读取，并不表示模型调用失败。

文件位于 `DATA_DIR/claude-cache-diagnostics.json`，最多保留 200 条记录。只保存逻辑任务、模型、前后消息编号、时间、诊断原因、估计丢失的输入词元、实际缓存读取/写入词元和用量完整性；不保存提示词、输入、输出、密钥或工具参数。未知诊断字段被丢弃，未知原因统一为 `unknown`。字段缺失与显式空值分别处理：只有供应商明确返回整个诊断字段为 `null`，才表示比较无差异；缺失诊断字段或缺失对象内的原因字段都归为未知。模型请求通过单个后台线程和最多64项的队列读写诊断文件。读取最多异步等待10毫秒，超时或队列满时退为无比较基线；写入不等待磁盘，队列满时丢弃记录。后台线程随进程退出，不使用会拖延事件循环关闭的默认线程池。文件写入使用非阻塞文件锁与原子替换，锁被占用时跳过本次记录；文件与锁权限为0600，读取拒绝管道和符号链接，只读取普通文件且不超过大小上限。诊断记录允许丢失，业务请求不等待锁。运行目录须由现有部署流程建立；目录不存在、权限不足或文件损坏都不会打断模型请求。损坏文件会在下一次可成功写入时恢复。

| 分类 | 含义 |
| --- | --- |
| `baseline` | 没有前一个消息编号，只建立比较基线，不能称为缓存命中。 |
| `pending` | 诊断对象的 `cache_miss_reason` 为 `null`，比较结果尚未可用。 |
| `prefix_changed` | 模型、系统提示、工具或消息前缀发生变化。 |
| `comparison_unavailable` | 前一个编号找不到、服务不可用或出现未知原因；不能据此判断提示词变化。 |
| `usage_incomplete` | 比较显示无差异，但流式响应未完整结束。 |
| `cache_read_observed` | 整个诊断字段为 `null`，比较显示无差异，完整响应实际读取了缓存。 |
| `no_cache_read_observed` | 比较显示无差异，但实际缓存读取为零或未报告；不能称为命中。 |

`cache_missed_input_tokens` 是供应商给出的估计值，不是账单或新增费用。诊断比较与真实缓存是否可用是不同信息：供应商只短时保留比较所需的指纹，缓存条目也可能到期，排查时必须同时查看真实用量。并发请求可能引用同一个之前编号；每轮记录在消息开始时建立、结束时更新，较早请求晚完成不会把最近编号退回去。

官方参考：[缓存诊断文档](https://platform.claude.com/docs/en/build-with-claude/cache-diagnostics)。本地验证使用真实软件开发工具包（SDK）与模拟传输，覆盖三种返回状态、原因白名单、跨进程读取、四线程写入、故障隔离以及 Opus 普通/测试版接口的续跑编号链；这些检查不产生付费请求。
