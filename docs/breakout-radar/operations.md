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
