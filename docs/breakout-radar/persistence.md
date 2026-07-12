# SQLite 持久化

数据库版本：breakout-db-v2
默认路径：/data/optix.db

## 连接规则

Worker 写连接：

- journal_mode=WAL
- foreign_keys=ON
- busy_timeout=5000
- synchronous=FULL
- 显式 BEGIN IMMEDIATE 发布事务

API 读连接：

- SQLite URI mode=ro
- PRAGMA query_only=ON
- 短读取事务
- 不使用 immutable=1

外部请求和特征计算全部在事务外。数据库路径必须是绝对路径，拒绝 SQLite URI、
目录穿越和符号链接逃逸。生产目录和文件由 app 用户拥有。

## 表

- breakout_schema_version：版本、校验和、应用时间。
- breakout_scan_runs：扫描身份、幂等键、session、状态、时间、计数、错误和版本。
- breakout_provider_snapshots：有界的新鲜与 stale Provider 快照。
- breakout_candidates：规范候选和有界调试字段。
- breakout_structures：pivot、结构、有效期和 cutoff。
- breakout_events：事件当前状态、分数、质量、来源、版本，以及
  first_seen_at、triggered_at、state_changed_at、last_seen_at 四种时间。
- breakout_transitions：显式幂等状态转换。
- breakout_scan_events：每个 completed scan 的不可变事件快照与排名。
- breakout_provider_health：成功、失败、熔断和 stale 状态。
- breakout_worker_lock：owner、fencing token、heartbeat 和 expires_at。
- breakout_worker_status：Worker 模式、状态、心跳、当前和最近扫描。
- range_persistence_shadow：生产分、假设分、排名差和版本。

关键索引覆盖 completed scan、ticker 最近事件、日期与状态分页、scan rank 和
transition 时间线。

## 原子发布

1. 扫描开始以短事务登记 running。
2. Worker 在内存完成网络和计算。
3. 发布事务验证 owner、fencing token 和租约未过期。
4. 幂等写候选、结构、事件、转换和影子数据。
5. 写不可变 scan_events。
6. 最后一条更新把 scan 改为 completed 并写 published_at。
7. 提交。

任一步失败全部回滚。API 永远不读取 running、failed 或 abandoned。Worker
重启会标记遗留 running 为 abandoned；已有 completed 不受影响。

## 幂等键

- scan：provider、session、scheduled_at、配置和版本哈希。
- event：trading_date、ticker、setup_type、pivot_id。
- transition：event_id、from_state、to_state、reason 和证据时点。

同一扫描重试不会重复事件或转换。前一事件进入终态后，同日新 pivot 产生新
event_id；仍在跟踪的主事件不会被 Discovery 旁路复制。

`event_at` 是兼容字段：已触发事件等于 `triggered_at`，尚未触发事件等于
`first_seen_at`。普通续扫只更新 `last_seen_at`；只有生命周期真正变化时才更新
`state_changed_at`。`triggered_at` 首次写入后不再被后续扫描覆盖。

## 租约

获取、心跳、释放和发布都匹配 owner 与 fencing token。租约使用 UTC 墙上时间，
本进程调度使用 monotonic time。第二 Worker 取锁失败后不得访问 Provider。
过期锁可恢复，旧 Worker 恢复后因 token 过期无法发布。

## 大小与保留

- Provider 响应最多 2 MB。
- 单候选调试字段最多 16 KB。
- JSON 使用 allow_nan=false 并设长度约束。
- 原始调试字段默认 24 小时清理。
- 规范扫描默认保留 30 日。
- 事件、转换和研究影子数据保留供重放。
- 被动 WAL checkpoint，不在活跃读期间强制 truncate。
