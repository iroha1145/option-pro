# ACTIONS 供应商脱敏诊断

审查锚点 `3de4bdf5`。本轮实现 head：见同目录 JSON 的 `code_sha`。密钥只记布尔，未打印值。未自动购买，未下载 bulk zip。

官方文档：<https://sharadar.com/docs/actions>  
文档把 ACTIONS 列在 Fundamentals / Prices / Bundle 内。文档可读 ≠ 本账户已授权。

## 实际 HTTP（未把 401 与 403 混成“必需升级套餐”）

| 请求形状 | HTTP | 厂商 message | 页状态 | 行数 |
| --- | --- | --- | --- | --- |
| `actions?ticker=MSFT,AAPL,SPY&from=2024-06-24&to=2024-06-28` | **403** | Exceeds free tier | AUTH_FAILED | 0 |
| `actions?ticker=AAPL`（文档示例，无日期） | **200** | — | READ_OK | 20（含允许区之后的日期，不能当研究行） |
| `actions?ticker=AAPL&from=2023-01-01&to=2024-06-28` | **200** | — | READ_OK | 6，全在允许区内 |
| `actions?ticker=MSFT&from=2023-01-01&to=2024-06-28` | **200** | — | READ_OK | 7，全在允许区内 |
| `actions?ticker=AAPL&from=2024-06-24&to=2024-06-28` | **200** | — | READ_OK | 0（空窗口，不是鉴权失败） |
| `actions?ticker=BBBYQ&from=2023-01-01&to=2023-05-31` | **403** | Exceeds free tier | AUTH_FAILED | 0 |
| bulk `status=True` years=5/10/full | **403** | Forbidden | AUTH_FAILED | — |
| `/v1.0/schema/actions?format=json` | **400** | Bad request | HTTP_ERROR | — |

本轮 **没有出现 HTTP 401**。同一把 key 已经能读 stocks / funds / tickers。

重试：上述失败请求的 `retry_count=0`（401/403 不在客户端重试码里）。受控换形状后，单标的 + 允许区日期得到非空分红/收购行。

## 允许区内真实非空行动（摘要，不是全量）

- AAPL：6 条 `dividend`（2023-02-10 … 2024-05-10）
- MSFT：6 条 `dividend` + 1 条 `acquisitionof`（2023-10-12，contraticker=`ATVI`，contraname=`ACTIVISION BLIZZARD INC`）

`acquisitionof` 不是已登记的现金对价码，不能当作经济结算通过。原始行已落在授权私人目录，未进公开 git。

## 需要负责人向供应商确认的权限信息

1. 本 API key 的产品是否包含 ACTIONS 的 **paged** 与 **bulk**；bulk 的 403 Forbidden 与 paged 的 403 Exceeds free tier 是否同一限制。
2. 多 ticker、跨年窗口、以及退市后代码（供应商返回的 `BBBYQ`）触发 Exceeds free tier 的具体配额字段。
3. 空的 2010/2016/2020 价量页（HTTP 200、0 行）是历史档未授权，还是该档在免费查询里被静默截断。
4. schema 端点 400 是否需要别的 `format`（文档给的是 postgres/sqlite/mysql）。

不要绕过鉴权，不要换错密钥，不要自动下单。
