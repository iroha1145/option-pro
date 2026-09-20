# BBBY 退市永久身份（一次 smoke，不是 16 案例全过）

`ticker=BBBY` 直接查询：HTTP 200，**空页**。只记录这次 lookup 没命中，不写成“无历史”。

官方 tickers 文档：`name` 不是 query filter；`permaticker`、`ticker`、`table`、以及可选的 lastpricedate `from`/`to` 才是。本轮用 `table=stocks` + lastpricedate 窗口扫主表，只接受供应商实际返回的代码/名称。

## 供应商返回的候选

在 lastpricedate `2023-04-01..2023-05-31`（135 行）和 `2023-01-01..2023-12-31`（970 行）里各命中同一行：

| 字段 | 供应商值 |
| --- | --- |
| table | stocks（另有 fundamentals / insiders 同行） |
| permaticker | 197799 |
| ticker | BBBYQ |
| name | BED BATH & BEYOND INC |
| isdelisted | Y |
| relatedtickers | BBBY |
| firstpricedate | 1997-12-31 |
| lastpricedate | 2023-05-02 |
| exchange | NASDAQ |
| category | Domestic Common Stock |
| security_id | sharadar:197799 |

`BBBYQ` 是主表返回的代码，不是本轮猜的后缀。`relatedtickers=BBBY` 只作待核提示。`permaticker=197799` 回查得到 3 行（stocks / fundamentals / insiders），永久身份一致。

## 价格与行动

按供应商代码 `BBBYQ` 拉 `2010-01-01..2024-06-28` 或短窗口 `2023-04-03..2023-05-02`：HTTP **403**，厂商 message=`Exceeds free tier`。行动表同样 403。

因此：永久身份已从主表解析；价量/行动尚未接到该身份上。复杂结算未知，不删除该证券历史。这是 1 个 smoke，不声称 16 个退市案例通过。
