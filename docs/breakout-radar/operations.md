# 突破雷达运维

## 默认状态

- BREAKOUT_RADAR_ENABLED=false
- DEPLOY_REQUIRE_BREAKOUT=false
- RANGE_PERSISTENCE_MODE=shadow
- RANGE_PERSISTENCE_VALIDATION_VERSION 为空
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

`DEPLOY_REQUIRE_BREAKOUT=false` 时，安全默认的关闭状态可以正常部署；设为
`true` 才要求雷达开启并通过 Worker、数据库和版本检查。无论哪种模式，
`RANGE_PERSISTENCE_BREAKOUT_INTERACTION_ENABLED=false` 都是合法配置。

区间强势持续度从 shadow 切换为 enabled 前，必须完成研究门槛，并同时设置：

```text
RANGE_PERSISTENCE_MODE=enabled
RANGE_PERSISTENCE_VERSION=range-persistence-v1
RANGE_PERSISTENCE_VALIDATION_VERSION=range-persistence-v1
```

验证版本为空或与特征版本不一致时，后端和 Worker 都会拒绝启动。算法版本升级
后必须重新验证，不能沿用旧版本确认。

## 调度

- premarket：默认 600 秒。
- regular：默认 300 秒。
- postmarket/closed：默认 1800 秒。
- 使用 monotonic 绝对截止点和有界 jitter，扫描耗时不累加到下一个周期。
- Provider 故障执行有界退避；数据库或租约失败才影响 Worker 健康。
- postmarket/closed 进入中性暂停：不调用 Discovery Provider，不登记 failed
  scan，不修改 Provider 成功、失败或熔断计数。Worker 继续写心跳和下一交易时段；
  API 以 `paused + market_closed` 返回，并保留正常时段的最后 completed 快照。

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

本地处理失败：

- 根据 `failure_domain` 区分 price_data、strength、market_shape、persistence、
  database、local_processing 和 configuration。
- 这些错误不得增加 Provider 的失败计数。只有 Provider 传输、限速、响应格式或
  上游可用性错误才能改变 Provider 健康。

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

数据库升级：

- v1/v2 升级 v3 前停止旧 Worker，并先制作可恢复备份。
- 升级后核对 schema 为 `breakout-db-v3`、`foreign_key_check` 为空，并检查
  `breakout_migration_quarantine`。隔离记录表示数据已重建，仍需保留备份追溯。

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
