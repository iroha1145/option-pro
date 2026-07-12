# 运行与验收

## 同步节奏

Health 60 秒、Feed 120 秒、Calendar 600 秒、Job 5 秒。四套时钟独立，带抖动和持久退避。Worker 通过租约和 fencing token 保证单实例；远程请求期间续租。

## 手动刷新

POST /api/catalysts/refresh 只写本地 Outbox 并返回 202。它不在用户连接中执行远程同步，也不绕过熔断、Retry-After 或权限。

## 监控

记录每个流的 data_through、最后尝试、最后成功、连续失败、下一次尝试、最近一轮 raw/inserted/duplicates、熔断截止、Worker 心跳、待处理任务、最老任务年龄、Token 用量、预算状态、数据库大小、证书到期和时间偏差。disabled、not_configured 与从未尝试的来源不生成虚假计数。

日志只含端点类别、状态、耗时、请求编号和安全错误码。Secret、签名、Nonce、完整查询、新闻正文、原始模型响应和 OpenAI Response ID 不进入日志。

## 健康检查

Option Pro /ready 不依赖 Catalyst。Catalyst Worker 健康检查只验证进程、租约、Schema 和本地数据库；远程不可用时容器仍健康，业务状态为 degraded、stale 或 unavailable。

MacroLens Integration Health 与分析 Worker 容器共用同一心跳阈值：心跳缺失、过期或 Worker 失败时，analysis_queue 标为 unavailable、analysis_trigger_enabled=false，并给出安全警告码；恢复新鲜心跳后自动恢复。持续集成必须实际启动 Backend 与 Worker，再从两个容器核对契约、共享数据库和心跳。

## 回滚

将 MACROLENS_ENABLED=false 和 CATALYST_MODE=disabled，即可隔离新功能并保留缓存审计数据。核心服务无需随 MacroLens 一同回滚。数据库恢复前先停相关 Worker。

## 测试

持续集成不得访问真实 OpenAI、MacroLens 生产服务或新闻源。必须运行 Python、Node、node --check、契约、HMAC、Point-in-Time、迁移、故障、容器、安全和视觉测试。

模型真实测试必须由显式操作触发，记录模型、reasoning、延迟、输入/缓存/输出 Token 和结果状态，不把密钥写入命令输出。

## 推送前

1. 重新获取两边 origin/main，确认远端未移动。
2. 为两边当前远端主分支建立带时间的备份引用。
3. 确认原始工作区的用户修改未被包含。
4. 完整测试通过后提交并推送各自功能分支。
5. 分别核对远端提交、持续集成和分支树。

真实服务器部署、HTTPS 证书、允许网段、服务密钥、故障注入和模型 Smoke Test 等待用户提供 MacroLens 服务器后完成。
