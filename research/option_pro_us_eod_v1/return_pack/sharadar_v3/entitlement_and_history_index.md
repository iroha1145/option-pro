# 权限与长历史索引（账户范围 FREE_SAMPLE_ONLY）

索引 head：`bd55c890e463ba8bd9eb5e081a65768feea4f0cf`。映射版本 `sharadar-access-class-v1`。账户范围 `FREE_SAMPLE_ONLY`（`account_scope`，不是供应商错误码）。不倒写历史原始响应。本轮无新的供应商失败探针。

## 各次运行（分别记账）

| 文件 | code_sha | 时间 | 凭据 | 真实请求 | 缓存 | mock |
| --- | --- | --- | --- | ---: | ---: | ---: |
| `authorized_sample_report.json` | `9421b1dad14f824d4b2601d4a7101833e144ee1d` | 2026-09-19T13:04:54.580462+00:00 | True | 5 | 0 | 0 |
| `runtime_credential_report.json` | `4395a09fe5f0c79c23864a570c9e6908fbdb14f1` | 2026-09-19T12:37:30.844507+00:00 | False | 0 | 0 | 0 |
| `actions_history_identity_report.json` | `42be3ea02a45d84ea671c572cdc81140025dfd19` | 2026-09-19T16:13:04.317821+00:00 | True | 69 | 0 | 0 |
| `actions_followup_report.json` | `42be3ea02a45d84ea671c572cdc81140025dfd19` | 2026-09-19T16:14:37.763057+00:00 | True | 11 | 0 | 0 |

`runtime_credential_report.json` 是另一次 Cloud Agent（无环境变量）。它不覆盖后续已授权运行。

## AUTH_FAILED 包装的解释（原始 HTTP 保留）

| 请求 | HTTP | 厂商 message | 记录的页状态 | access_class |
| --- | --- | --- | --- | --- |
| paged_ticker_and_window | 403 | Exceeds free tier | AUTH_FAILED | `observed_access_or_quota_limit` |
| docs_example_ticker_only | 200 | — | READ_OK | `not_an_access_failure` |
| bulk_status years=5 | 403 | Forbidden | AUTH_FAILED | `forbidden_reason_unknown` |
| bulk_status years=10 | 403 | Forbidden | AUTH_FAILED | `forbidden_reason_unknown` |
| bulk_status years=full | 403 | Forbidden | AUTH_FAILED | `forbidden_reason_unknown` |
| schema/actions?format=json | 400 | Bad request | HTTP_ERROR | `unsupported_schema_format` |

- `observed_access_or_quota_limit`：已观察到的访问范围/配额限制，不推导“用户没订阅”。
- `forbidden_reason_unknown`：bulk `403 Forbidden`，原因未知。
- `unsupported_schema_format`：`format=json` 的 400，不是订阅证据。官方 format 为 postgres/sqlite/mysql；已有 `ACTIONS_FIELDS`，不再试错。
- 本轮没有 HTTP 401。

## 分层状态

- 主表身份：PARTIAL: BBBYQ/197799 resolved; 16-case set not done
- 价格覆盖：PARTIAL: 2024 accessible names have rows; 2010/2016/2020 empty; BBBYQ/XOM/INTC limited
- 行动：PARTIAL: AAPL/MSFT single-ticker allowed-window rows; multi-ticker and BBBYQ 403
- 经济结算：BLOCKED: acquisitionof is not a documented cash unit; share-basis proof now binds the target

## 对账字段

对照源是 Massive `adjusted=false`，不是 Yahoo。

| 字段 | 状态 |
| --- | --- |
| 未复权简单收益 | PASS，297 对 / 11 证券 |
| 拆股复权几何价格 | UNKNOWN |
| 含分红总回报 | UNKNOWN |
| 真实股份口径成交量与成交额 | UNKNOWN |
| 时段口径 | UNKNOWN |
| 经济账本 | UNKNOWN |

一个总 PASS 不宣称其他字段也已通过。

## 工件

- `/home/ubuntu/optix-data/authorized_sharadar_samples/actions_history_identity/followup/actions_AAPL_2023-01-01_2024-06-28.jsonl` sha256=`fa44e9ac5a1c75a23f1d50af849dcd7d3fd96867ee51102181ac9907cb713298` bytes=858（不进公开 git）
- `/home/ubuntu/optix-data/authorized_sharadar_samples/actions_history_identity/followup/actions_MSFT_2023-01-01_2024-06-28.jsonl` sha256=`da1c766f7252771b2468dd5ce129b6eec8ac4325aa682f2b31bef45daecc32d6` bytes=1065（不进公开 git）
- `/home/ubuntu/optix-data/authorized_sharadar_samples/actions_history_identity/coverage/stocks_MSFT_2024.jsonl` sha256=`095ef7440b917430b0b644a5dd8dbcf323bf5575caf8d7ee922da0240f9b5179` bytes=5500（不进公开 git）
- `/home/ubuntu/optix-data/authorized_sharadar_samples/actions_history_identity/coverage/funds_SPY_2024.jsonl` sha256=`4423003a4f5d0f89fe4726306253b271bdb5e0a57b7f93e21035f6e665cd7a01` bytes=5473（不进公开 git）

## 结论

负责人已确认：本账户仅免费 Sample，未购买任何套餐。当前 Sharadar 能力标记为 **FREE_SAMPLE_ONLY**。这是账户范围标记，不是新的供应商错误码。

已记录的 403 / 空长历史 / bulk Forbidden 是免费 Sample 的范围外访问，不再当作已购权限异常排查，不再重复失败探针，也不再要求更换 agent 去补覆盖。

适配器与既有工程修复保留。不自动购买，不恢复旧 214 小池权重搜索，不降低十年以上正式验证要求。数据来源与预算确定后，再恢复正式数据阶段。

先前终态 B（等账户持有人向供应商确认产品）已由负责人确认关闭。

`full_backfill_started=false`，`executed_backtests=0`，`purchase_attempted=false`。
