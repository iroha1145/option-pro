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

## 5.1 发布记录（`macro_snapshot_publications`，schema v2）

因子 / 模块 / 综合分三张快照表是**当前视图**：主键是
`snapshot_date + id + scoring_version`，后续刷新用 `ON CONFLICT DO UPDATE`
就地覆盖。它们能回答的是

> 按今天已知的最新数据，某个历史日期重新计算出来是多少？

不能回答

> 2026 年 9 月 1 日当天，系统当时正式发布的分数是多少？

前向验证问的是后一个问题，而用前一个答案回答它，等于把当时不可见的修订
悄悄喂进检验。因此 v2 增加一张**只追加**的发布表：每次 `publish()` 记录一行，
对应该次运行结束时最新的那个 `snapshot_date`。

| 字段 | 含义 |
| --- | --- |
| `publication_id` | 本次发布的唯一标识 |
| `run_id` | 产生它的同步运行；缺失直接拒绝写入 |
| `snapshot_date` | 本次发布时最新的快照日 |
| `published_at` | 发布时刻（bundle 的 `as_of`） |
| `available_at` | 该快照可计算的时刻 |
| `factor_payload_hash` / `module_payload_hash` | 当次发布的因子 / 模块取值摘要 |
| `composite_payload` | 综合分、置信度、regime、状态、`data_through` |

只记最新那一天，不记整段历史网格：bundle 里携带的历史区间是**重算**，
它已经在快照表里作为当前视图存在；把它整段追加一遍既是重复存储，
也回答不了「那天发布的是什么」。

前向验证按 `published_at <= backtest_as_of` 取当时最后一次正式发布，
而不是读今天重算后的历史行。

这张表**不参与展示路径**，因此一条发布记录不可能改变任何人已经看到的分数。

---

## 5.2 ETF 观测的身份（schema v2）

v1 的主键是 `(symbol, observation_date, provider, available_at)`，而
`available_at` 是写入时刻 —— 于是 `INSERT OR IGNORE` 从来没有忽略过任何东西：
同一个价格每次刷新都会重新落一行（8 只 ETF × 约 252 个交易日，每天两次）。

v2 改成与 `macro_series_revisions` 同构：身份是**值本身**
（`value_hash`），再次看到同一个价格只推进 `last_seen_at`；价格变化才追加
一条新的 Revision。

迁移按 `(symbol, observation_date, provider, adjusted_close)` 折叠 —— 也就是
新主键表达的那组身份，因此不可能把两个真正不同的价格合成一个。
`first_seen_at` 取历史上最早的那个 `available_at`：丢掉它等于改写已经存储的
点时可见性，而那正是这张表存在的理由。

`history_basis` **不参与分组**：同一个价格可能被十年回填记过一次、又被增量刷新
重新读到一次，那是一个价格而不是两个。合并后的行取**最早那次记录**的
`history_basis`，因为 `first_seen_at` 就来自那一次 —— 让标签跟着它描述的那个
时间戳走。按插入顺序决定既不确定，也会把回填得来的可见性标成本地实测。

一个真实后果：在当前生产库上，10,040 条合并后全部是
`latest_revised_backfill`。原来那 22,240 条 `local_point_in_time` 标记都长在
**重复行**上，它们的 `available_at` 只是后一次刷新的写入时刻，从来没有代表过
一次新的首次可见。迁移把这件事如实暴露出来。上线之后真正新观测到的价格，
仍然会正常记为 `local_point_in_time`。

迁移是幂等的，并且在折叠后表为空而折叠前非空时直接失败，
不会拿真实历史换一张更整齐的表。

**部署顺序**：`initialize()` 只在 `refresh()` 里调用，而 `refresh()` 只跑在
worker 里。API 进程以只读方式打开同一个库、并且和 worker 同一个版本发布 ——
如果读取路径只认 v2，那么从部署完成到 worker 下一次宏观刷新之间（可能是几小时），
宏观面板会一直显示不可用。因此 `active_etf` 按磁盘上**实际存在的列**读取：
有 `first_seen_at` 就用 v2 口径，否则用 v1 的 `available_at`。
这样不需要假设两个容器的启动顺序，窗口直接消失。

---

## 6. 已知限制

1. 上线时的 8 年历史是最新修订值回算的，因此上线初期的历史曲线全部是
   `latest_revised_backfill`；`local_point_in_time` 区间随部署时间自然增长。
2. 因为回填不伪造发布时间，历史区间的 `available_at` 没有前向验证价值——
   这是如实标注，而不是缺陷。
3. FRED 的部分系列（H.4.1 周度、NFCI）会被修订；本地 Revision 表保留每一版，
   但上线之前的修订轨迹无法追溯。
