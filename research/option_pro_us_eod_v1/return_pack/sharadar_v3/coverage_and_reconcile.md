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

口径：Sharadar `closeunadj` 简单收益 vs Massive `adjusted=false` 日线；币种 USD；未与 Yahoo 静默拼接。成交量分母/时段未知，**不宣称量能实验或经济账本可用**。

2016 Massive 对照为 `UNSUPPORTED`（控制源没有给出该窗）。对账只用 2024 窗。

跟进后：`reconcile.status=PASS`，`return_coverage_n=297`，`securities_n=11`（门槛 250 / 10）。前一期价格不足的日子不算有效收益对。marked 收益/量偏差未当作真值。

Yahoo 合法缓存本环境不存在，未参与。
