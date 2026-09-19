# 长历史覆盖与跨源对账

预登记窗口（均 ≤ 2024-06-28）：2010-01-04..2010-02-12、2016-01-04..2016-02-12、2020-03-02..2020-04-09、2024-05-20..2024-06-28。

主表先核实 MSFT/AAPL/JNJ/XOM/JPM/WMT/PG/KO/INTC/IBM/SPY（及后续 HD/CSCO）。`firstpricedate` 多为 1997-12-31，**不能替代真实长历史行**。

## 覆盖

| 窗口 | 典型 HTTP | 典型行数 | 说明 |
| --- | --- | --- | --- |
| 2010 / 2016 / 2020 | 200 | 0 | 空页。主表声称覆盖该期，但没有价量行。 |
| 2024-05-20..06-28 | 200 | 28 | MSFT/AAPL/JNJ/JPM/WMT/PG/KO/IBM/SPY/HD/CSCO 连续；最大间隔 4 日（周末）。 |
| 同上 INTC/XOM/BBBYQ | 403 或 0 行 | 0 | 403 message=`Exceeds free tier`，或空页。 |

字段有效数：2024 非空页为 9/9（ticker,date,open,high,low,close,volume,closeadj,closeunadj）。未启动全市场 bulk。付费行在 `$HOME/optix-data/authorized_sharadar_samples/actions_history_identity/`。

## 对账

对照源是 Massive `adjusted=false`，不是 Yahoo。未与 Yahoo 静默拼接。`yahoo_n` 只是旧字段别名。

2016 Massive 对照为 `UNSUPPORTED`。对账只用 2024 窗。

| 字段 | 状态 |
| --- | --- |
| 未复权简单收益 | PASS，297 对 / 11 证券（门槛 250 / 10） |
| 拆股复权几何价格 | UNKNOWN |
| 含分红总回报 | UNKNOWN |
| 真实股份口径成交量与成交额 | UNKNOWN |
| 时段口径 | UNKNOWN |
| 经济账本 | UNKNOWN |

一个总 PASS 不宣称其他字段也已通过。不修改 Sharadar 原始值去贴合对照源。Yahoo 合法缓存本环境不存在，未参与。
