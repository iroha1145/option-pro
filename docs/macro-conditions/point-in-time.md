# Optix 宏观环境 · 点时语义

这份文档说明一个容易被误读的问题：**图上那条历史曲线不是当时市场已知的分数。**

---

## 1. 两种历史基础

API 与 UI 都必须返回 `history_basis`。

### `latest_revised_backfill`

功能上线时从 FRED 拉取的 8 年历史。

- 表示**今天能看到的最新修订值**。
- 可以用作当前历史分位的基线（分位需要一个分布，最新修订值是可得的最好分布）。
- **不得声称是当时真实可见值**：H.4.1、NFCI 这类系列会被修订，当时市场看到的数字
  与今天不同。
- 历史图上该区间用**虚线**画，Tooltip 明确写「按当前修订值回算」。

### `local_point_in_time`

功能上线后，每次实际拉取形成的**不可变本地 Revision**。

- `first_seen_at` 记录本地首次看见该值的时间，这才是真实的可见时刻。
- 可用于未来的前向验证（walk-forward）。
- 历史图上该区间用**实线**画。

### `mixed`

一个快照的输入同时来自两种基础时，`history_basis = mixed`，界面写「混合：部分区间按
当前修订值回算」。

---

## 2. UI 上必须出现 / 绝不出现

必须出现：

- 「历史区间按当前修订值回算」
- 「分数是过去 5 年的历史分位，不是预测」
- 「本地部署后每次实际抓取形成的快照才具备真实的点时语义」

绝不出现：

- 「当时市场已知分数」
- 任何把历史分位说成上涨概率的表述

---

## 3. 时间字段

| 字段 | 含义 |
| --- | --- |
| `observation_date` | 观察期（FRED 的口径：周度系列是周结束日） |
| `first_seen_at` | 本地首次看见该值的时刻 —— 真实可见性 |
| `available_at` | 快照可计算的时刻 = 其所有输入 `first_seen_at` 的最大值 |
| `source_last_updated` | FRED 元数据自报的最后更新时间，**单独保存**，不与上面混用 |
| `as_of` | 这次刷新的时刻 |
| `snapshot_date` | 网格上的交易日 |
| `data_through` | 所有有效输入中**最早**的 `observation_date` |

约束：**所有实时快照满足 `available_at <= as_of`**（`service._assert_no_future_reference`
在发布前强制检查；违反时抛错而不是发布）。

回填**不伪造过去的精确发布时间**：回填行的 `first_seen_at` 就是回填那一刻，
因此历史快照的 `available_at` 晚于其 `snapshot_date` —— 这正是它必须标注
`latest_revised_backfill` 的原因。

### 滚动窗口里的可见性

一个 252 日滚动窗口的 `available_at` 取**窗口内实际用到的那些行**的最大
`first_seen_at`，不是"最新一条观察"的时间戳。原因：一条旧观察被晚期修订后，它的
`first_seen_at` 会晚于其后邻居，"最新一条"并不等于"最晚可见"。

---

## 4. ETF 日线

- 只使用**已完成交易日**：当日收盘（含提前收盘日，用 NYSE 日历判定）之前，
  今天的 K 线不算观察值。
- 陈旧阈值按**交易日**计（5 个交易日），与按自然日计的 FRED 系列区分。
- 记录实际服务的 Provider（Massive / Yahoo），不混用未复权与复权价格。

---

## 5. ALFRED Vintage

本轮**不**下载完整 ALFRED Vintage 历史。

Schema 已为未来追加 Vintage 留好位置：`macro_series_revisions` 的主键是
`(series_id, observation_date, value_hash)`，并带 `realtime_start` / `realtime_end`
两列。追加 Vintage 只需要往同一张表插入更多行，**不引入第二套运行路径**：
读取侧的"当前有效修订"查询按 `(last_seen_at, rowid)` 稳定收敛，天然容纳更多行。

后续如果启用 Vintage，`history_basis` 可以新增一个取值，而对齐、计算、评分三层完全
不用改。

---

## 6. 已知限制

1. 上线时的 8 年历史是最新修订值回算的，因此上线初期的历史曲线全部是
   `latest_revised_backfill`；`local_point_in_time` 区间随部署时间自然增长。
2. 因为回填不伪造发布时间，历史区间的 `available_at` 没有前向验证价值——
   这是如实标注，而不是缺陷。
3. FRED 的部分系列（H.4.1 周度、NFCI）会被修订；本地 Revision 表保留每一版，
   但上线之前的修订轨迹无法追溯。
