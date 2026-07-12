# 突破雷达运维

## 默认状态

- BREAKOUT_RADAR_ENABLED=false
- RANGE_PERSISTENCE_MODE=shadow
- Worker 独立进程，不暴露端口。
- Backend 只读 completed SQLite 快照。

## 运行

从 backend 目录运行一次：

PYTHONPATH=. python -m app.services.breakouts.worker --once

常驻：

PYTHONPATH=. python -m app.services.breakouts.worker

健康检查：

PYTHONPATH=. python -m app.services.breakouts.worker --healthcheck

功能关闭时 --once 安全退出，健康状态为 disabled。启用真实 Provider 的一次
扫描不是离线测试，不能与夹具测试混为一谈。

## 调度

- premarket：默认 600 秒。
- regular：默认 300 秒。
- postmarket/closed：默认 1800 秒。
- 使用 monotonic 绝对截止点和有界 jitter，扫描耗时不累加到下一个周期。
- Provider 故障执行有界退避；数据库或租约失败才影响 Worker 健康。
- postmarket/closed 当前没有可用的 Discovery Provider，会记录降级和 failed
  扫描，但不会用空 completed 覆盖正常时段的最后结果。

## 候选增强门槛

- Discovery 候选先完成日线下载，再按最近 20 个已完成交易日的平均成交量乘
  最新完成日收盘价计算平均成交额。
- 计算值缺失或低于 BREAKOUT_MIN_AVG_DOLLAR_VOLUME 时，候选立即停止，不调用
  Strength，也不下载该候选的盘中行情。
- BREAKOUT_OPENING_RANGE_MINUTES 必须为 5 分钟的整数倍。只有从 09:30 开始的
  每根完整 5 分钟 K 线全部存在，且事件时间达到区间终点，开盘区间才算完成。

## 持续事件队列

- Worker 每轮只读载入最多 BREAKOUT_PROVIDER_RESULT_LIMIT 条未终态事件，
  不按 ticker 循环查询。
- 最多 40 个名额优先处理已经越过 BREAKOUT_EVENT_TTL_SECONDS 的事件并写入
  EXPIRED；其余名额按 last_seen_at 从旧到新轮转。
- 队列超出单轮上限时快照仍可发布，并带 carryover_truncated 警告；不得因此
  把 Provider 误报为 unavailable，也不得让后续扫描持续失败。
- 掉出 Discovery 或低于当前流动性门槛的既有事件仍会更新，但只能沿用原
  event_id，不能借持续通道创建新事件。

## 状态判断

查看 /api/breakouts/status：

- worker heartbeat 和最近完成时间。
- Provider active/degraded/stale/unavailable。
- 最近 completed scan。
- 数据库、strength、market shape 和 Range Persistence 模式。
- 全部版本。

/ready 不依赖突破数据库或 Provider。突破异常不能通过修改 /ready 掩盖。

## 故障处理

Provider 失败：

- 检查错误码、熔断截止时间和 stale_snapshot_available。
- 不删除数据库，不手工把空结果标成 completed。
- `unavailable`且没有合格缓存时，本次 scan 状态应为 failed，Worker 状态应为
  degraded，`latest_completed_scan`仍指向上一次成功发布。
- `stale`快照仍可按陈旧状态发布；Provider 正常响应但候选数为零，才是有效的
  completed 空快照。运维检查时不能把这三种情况合并为“没有信号”。

数据库 busy：

- 等待短 busy_timeout；API 返回突破降级。
- 检查是否有超长写事务。网络调用不得持有写锁。

Worker 失锁：

- 当前扫描不得发布。
- 等待新 owner 完成；不要无条件删除锁行。

WAL 增长：

- 检查长时间读连接和磁盘余量。
- 运行被动 checkpoint；活跃读取时不要强制 truncate。

数据库损坏或权限错误：

- 保留文件作诊断。
- 恢复最近备份或重新建立新库；旧接口仍应可用。

## Docker

- Backend 和 Worker 使用相同镜像与 APP_COMMIT。
- 两者挂载 optix-data:/data；根文件系统保持只读。
- Worker 不携带 OpenAI 密钥和 APP_AUTH_TOKEN，不暴露端口。
- 两者均为 app 用户，cap_drop ALL，no-new-privileges。
- 镜像包含 THIRD_PARTY_NOTICES.md 和 BreakoutAnalysis-LICENSE。

必须在真实命名卷上验证 WAL、-shm、并发读和原子发布，不能只看 Compose 文本。

## 数据保留

- 原始 Provider 调试子集：默认 24 小时。
- completed scan：默认 30 日。
- 事件、transition 和 shadow 研究数据：长期保留。
- 清理任务不得删除仍被事件或研究引用的结构。

## 安全日志

只记录 provider、状态码、内部错误码、耗时和计数。禁止记录请求头、Cookie、
查询串、响应正文、原始 JSON、OpenAI 密钥或完整异常对象。
