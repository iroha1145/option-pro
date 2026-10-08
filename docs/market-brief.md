# 首页市场综合研判 · 运维

首页「市场综合研判」每个交易日生成两份：开盘前一份，收盘后一份。生成工作由统一工作进程（Worker）里的 `market_brief` 任务完成，模型是 Claude Opus 5.5（`claude-opus-5-5`），推理强度（effort）为 `xhigh`。

研判只解释公开数据：程序算数（广度、板块强弱、宏观分位），模型解释它们并找矛盾。它不给涨跌概率，也不构成买卖、仓位或目标价建议。

---

## 1. 启用

```bash
# 1) 在服务器上写入密钥（隐藏提示符，不回显）。密钥以 sk-ant- 开头。
./personal.sh secrets set ANTHROPIC_API_KEY

# 2) 校验：本地查格式，再请求一次 GET https://api.anthropic.com/v1/models。
#    列模型是免费的只读请求，不产生模型费用；输出只有布尔值与原因码。
./personal.sh secrets validate

# 3) 部署（会重建镜像）。只改密钥时，secrets set 已重建 backend 与 worker 两个容器。
./scripts/deploy.sh
```

没有配置密钥时：

- `market_brief` 任务报 `disabled`，错误码 `anthropic_api_key_missing`，Worker 整体仍然健康，部署校验照常通过；
- `GET /api/market-brief/latest` 照常返回 200，`status` 为 `missing`；
- `POST /api/market-brief/runs` 返回 409 `anthropic_api_key_missing`。

---

## 2. 配置

`config/personal.toml` 的 `[market_brief]` 段（改动需要重启进程）：

| 键 | 默认 | 含义 |
| --- | --- | --- |
| `enabled` | `true` | 总开关。关掉后任务报 `disabled`（`market_brief_disabled`），补发返回 409 |
| `model` | `claude-opus-5-5` | 只接受这一个值，换模型要改代码 |
| `effort` | `xhigh` | `low` / `medium` / `high` / `xhigh` / `max` |
| `pre_open_time_et` | `08:40` | 开盘前一份的开窗时刻，必须早于 09:30；排在宏观 08:30 刷新之后 |
| `post_close_offset_minutes` | `30` | 收盘后多少分钟开窗（0–240），早收盘日按 13:00 算 |
| `post_close_fallback_time_et` | `23:30` | 过了这个时刻不再等当日全市场批次；不能早于 16:00 加上一项 |
| `grace_minutes` | `150` | 开窗后允许补跑的时长（30–600） |
| `web_search_max_uses` / `web_fetch_max_uses` | `10` / `8` | 服务端联网工具每次请求的调用上限（0–20） |
| `web_fetch_max_content_tokens` | `12000` | 单次抓取网页的内容上限 |
| `code_execution_tool` | `false` | 独立代码执行工具，默认关 |
| `refusal_fallback` | `false` | 模型拒答时的回退，默认关 |
| `structured_output` | `false` | 默认把 JSON Schema 附在固定系统提示词中，并在本地校验完整结果；开启后增加供应商结构约束，内容与证据校验仍然保留 |
| `prompt_cache_ttl` | `5m` | 系统提示词显式缓存断点的 TTL（`5m` / `1h`）；顶层自动缓存固定 5 分钟。两份研判相隔数小时，只有缓存有效期内、前缀相同的续跑或手动重跑可能命中缓存 |
| `max_output_tokens` / `output_token_ceiling` | `48000` / `160000` | 单次输出上限与整次（含续跑）输出上限，后者不能小于前者；每次续跑按剩余额度缩小单次上限 |
| `max_continuations` | `4` | `pause_turn` 续跑次数上限（0–8） |
| `request_timeout_seconds` | `1500` | 整次运行的截止时间（含证据组装与续跑）；流式读取也受绝对超时控制，超时会关闭连接，不能超过任务的 1800 秒总超时 |
| `evidence_max_bytes` | `56000` | 证据包字节上限 |
| `daily_max_runs` | `6` | 每个 UTC 日最多启动几次运行；定时、手动和命令行共用持久准入，运行中和崩溃记录也计数 |
| `public_read` | `true` | 关掉后最新研判与历史只对 Owner 可见，访客读到 401 |

Owner 还可以在运行设置里暂停定时生成（不需要重启）：

```json
PUT /api/runtime-settings
{"expected_version": <当前版本>, "settings": {"market_brief": {"scheduled_enabled": false}}}
```

暂停只影响两个定时槽，任务状态显示 `paused`；手动补发照常可用。

---

## 3. 槽位与调度

- 只在交易日生成（按 `app.services.market_calendar` 的纽约证券交易所日历，含早收盘）。
- 开盘前：美东 `pre_open_time_et` 开窗，`grace_minutes` 后关窗。
- 收盘后：收盘后 `post_close_offset_minutes` 开窗。开窗后先等当日全市场批次（`strength_refresh` 每天美东 22:00 起发布），每 5 分钟看一次；到 `post_close_fallback_time_et` 还没等到，就用现有数据生成。窗口在兜底时刻再过 `grace_minutes` 后关闭（默认是次日美东 02:00）。
- 每个（交易日，槽）成功一份后，定时路径不再重跑。
- 一个槽跑过就等下一个槽，无论成败都不在窗口内自动重试；已准入的尝试持久保存，重启也不会重投。明确失败后可以手动补发。提交结果未知时，同一交易日、同一槽隔离 24 小时，独立时段仍可在每日额度内运行。
- 每轮结束都按下一个槽的美东开窗时刻重新排期，夏令时切换日按真实经过的秒数计算。
- 部署停机时会等进行中的那次请求完成或到达绝对超时后退出（`drain_on_shutdown`）。超时与断流会关闭本地连接，但不据此认定供应商免费；未确认总费用保留为未知。
- 任务设置了「重启不重跑」：重启后沿用状态库里记下的下一个槽。进程中途崩溃时，持久准入记录仍保留。下次工作进程检查或新的准入会恢复已保存结果；若已提交却没有完整回执，记录为提交结果未知，同槽隔离期间不重发。

模型或供应商失败（过载、拒答、输出不合格等）只记进研判存储和任务的 `details`，任务状态仍是 `idle`，不影响部署校验。只有程序异常才把任务标成 `degraded`。

---

## 4. 手动补发

Owner 在首页研判卡片上点「现在生成」，或直接调用：

```bash
curl -sS -X POST https://<host>/api/market-brief/runs \
  -H 'Content-Type: application/json' \
  -H 'X-Optix-Action: 1' \
  -H "Origin: https://<host>" \
  --cookie 'optix_owner_session=<session>' \
  -d '{"slot": "pre_open"}'
```

`slot` 可省略：Worker 在运行时按美东钟点决定，中午前补开盘前那份，之后补收盘后那份。交易日取美东当日，非交易日取上一个交易日。手动补发会重跑已经完成的槽，生成结果覆盖为最新一份。

| 返回 | 含义 |
| --- | --- |
| `202` + `reason=queued` | 已入队 |
| `200` + `reason=idempotent` | 同一 `idempotency_key` 复用既有请求 |
| `200` + `reason=already_running` + `error_code=market_brief_in_progress` | 已有补发在排队或在跑 |
| `200` + `reason=cooldown` + `error_code=market_brief_cooldown` | 上一次补发成功后 600 秒冷却中 |
| `409` `anthropic_api_key_missing` / `market_brief_disabled` / `worker_task_disabled` | 未配置或已关闭 |
| `429` `daily_run_limit_reached` | 当日（UTC）运行次数已到 `daily_max_runs` |
| `503` `worker_unavailable` / `worker_task_unavailable` / `worker_state_unavailable` | Worker 不可用 |

明确失败的补发不触发冷却，可以马上再试（仍受每日次数上限约束）；断流、超时或崩溃造成的未知提交，在同槽 24 小时隔离结束前不得重发。Worker 认领时会再查一次每日上限，超过就把这次请求记成失败（`daily_run_limit_reached`）。

也可以走通用的 Worker 动作接口 `POST /api/worker/actions/market_brief`，它不接受槽位参数，由 Worker 按钟点决定。

---

## 5. 接口

| 接口 | 身份 | 说明 |
| --- | --- | --- |
| `GET /api/market-brief/latest` | 访客可读 | 最新一份研判。没有任何成功记录时 `status=missing`，仍是 200。带 ETag，浏览器缓存 60 秒；Owner 额外看到用量与费用 |
| `GET /api/market-brief/history?limit=1..30` | 访客可读 | 最近几次运行的摘要 `{"runs": [...]}`，含失败运行的错误码，不含正文 |
| `GET /api/market-brief/status` | Owner | 开关、密钥是否配置、定时是否暂停、下一槽、最近一次运行、排队中的补发、冷却、当日次数 |
| `POST /api/market-brief/runs` | Owner | 见上一节 |

读接口只读研判存储，不调模型，也不触发任何供应商请求。

---

## 6. 首次上线检查

```bash
# Worker 应报告 15 项任务，market_brief 在其中
./scripts/compose.sh exec -T worker python -m app.worker --healthcheck

# 看研判任务这一行：status、error_code、next_run_at、details
./scripts/compose.sh exec -T worker python -m app.worker --status | python3 -c '
import json, sys
for task in json.load(sys.stdin)["tasks"]:
    if task["task_name"] == "market_brief":
        print(json.dumps(task, ensure_ascii=False, indent=2))
'
```

配置好密钥后，任务会等到下一个槽才第一次生成。不想等就手动补发一次，然后看 `GET /api/market-brief/status` 的 `pending_action` 与 `last_run`。

首跑建议在容器里用命令行做，能看到请求参数与失败原因（不要带 `--date`：它只改槽位标签，证据一律按当前时刻读取）：

```bash
# 1) 只组装证据、不请求：看 coverage 里缺了哪些块、证据多少字节
./scripts/compose.sh exec -T worker python -m app.tools.market_brief_run --slot pre_open --dry-run

# 2) 真跑一次（约 1.5–3 美元）；发送前会打印 model / effort / tools / max_tokens 等参数
./scripts/compose.sh exec -T worker python -m app.tools.market_brief_run --slot pre_open

# 3) 跑通后想试结构化输出：加 --structured-output；被 400 拒绝就不加
./scripts/compose.sh exec -T worker python -m app.tools.market_brief_run --slot post_close --structured-output
```

退出码：0 成功，1 失败（错误码与详情已打印并写进运行记录），2 当天不是交易日。命令行与 Worker 共用同一份存储，跑成功后首页立刻能读到。

---

## 7. 费用口径

按设计时的 Opus 5.5 价目估算（以 Anthropic 官方价目为准）：输入每百万词元 4 美元，5 分钟缓存写入 5 美元、一小时缓存写入 8 美元，缓存读取 0.20 美元，输出 20 美元；联网搜索每次 0.01 美元。单次研判约 1.5–3 美元，一个交易日两份约 3–6 美元。`daily_max_runs = 6` 是定时、手动与命令行合计的每日启动上限；只有通过原子准入的尝试计数，进程崩溃也不会释放该次数。所有入口同一时刻只准入一次实际运行。工作进程的手动动作编号同时作为持久幂等键；若结果已落盘而动作结算前进程退出，重启后只读取旧回执，不再付费调用。

每次运行的用量与估算费用（微美元精度）存在运行记录里。SDK 自动重试已关闭。完整轮次与中途已报告用量均保留；`usage_complete=false` 表示总用量未确认，此时 `cost_microusd` / `cost_usd` 为 `null`，不是零费用。缓存期限明细缺失时按保守写入价格估算，所有费用仍以供应商账单为准。Owner 读 `/latest` 时能看到最新一份的费用；Worker 任务状态的 `details.cost_usd` 是最近一次运行的费用。

---

## 8. 数据文件

研判存储在 `DATA_DIR/market-brief/`：每次运行一份完整记录（含证据包、原始输出、用量），另有最新一份的公开投影与最近运行的索引。`admissions.sqlite3` 保存每日计数、提交标记和已尝试槽位，`.run.lock` 串行化工作进程与命令行的实际运行。此目录尚不在 `maintenance` 备份清单内，发布前应整体备份；不能只清空准入库来解除限制，否则会丢失防止重复付费的记录。

---

## 9. 排障

| 现象（任务状态 / 错误码） | 处理 |
| --- | --- |
| `disabled` + `anthropic_api_key_missing` | 配置 `ANTHROPIC_API_KEY`，见第 1 节 |
| `disabled` + `market_brief_disabled` | `[market_brief].enabled = false` |
| `paused` + `scheduled_disabled` | 运行设置里关了定时生成 |
| `idle` + `details.waiting = eod_batch` | 当日全市场批次还没发布；到兜底时刻会用现有数据生成 |
| `idle` + `details.result = slot_attempted` | 这个窗口已经跑过但失败；原因看 `/latest` 的 `latest_attempt` 或 `/history` 的 `error_code`，需要时手动补发 |
| `idle` + 运行错误码（如 `provider_rate_limited`、`provider_server_error`、`provider_refusal`、`schema_validation_failed`、`evidence_unavailable`） | 模型、供应商或证据不足导致的失败，不影响 Worker 健康；等下一个槽或手动补发 |
| `idle` + `submission_outcome_unknown` / `provider_stream_incomplete` | 提交结果或完整用量未确认；不要重复提交同一槽，查看持久记录与供应商账单。其他独立槽不受这条未知记录阻塞 |
| `idle` + `provider_invalid_tool_response` | 工具调用与结果不配对或出现未声明的客户端工具，未发布研判；保留实际已报告用量，不自动重试 |
| `degraded` + `market_brief_run_failed` | 程序异常。看 Worker 日志里的 `market brief run failed`；它会让 Worker 整体变成 degraded，部署校验会拒绝，修复后重启 Worker |
| `degraded` + `runtime_settings_unavailable` | 运行设置文件读不出来，看 `runtime-settings.json` |
| 续跑过的运行记录里 `usage.cache_read_input_tokens` 为 0 | 缓存前缀在上游被改了（系统提示词里混进了随运行变化的内容，或工具列表不稳定）；对照 `request_meta` 排查 |
| `./personal.sh secrets validate` 里 `ANTHROPIC_API_KEY` 为 `format_invalid` | 密钥不是 `sk-ant-` 开头，多半粘贴错了位置 |
| 同上为 `authentication_failed`（401） | Anthropic 拒绝了这把密钥 |

日志与任务状态只含错误码与异常类型，不含密钥、请求内容或供应商响应正文。
