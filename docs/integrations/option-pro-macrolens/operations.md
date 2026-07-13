# 运行与验收

## 同步节奏

Health 60 秒、Feed 120 秒、Calendar 600 秒、Job 5 秒。四套时钟独立，带抖动和持久退避。Worker 通过租约和 fencing token 保证单实例；远程请求期间续租。

## 手动刷新

POST /api/catalysts/refresh 只写本地 Outbox 并返回 202。它不在用户连接中执行远程同步，也不绕过熔断、Retry-After 或权限。

## 监控

服务健康端点、状态接口和结构化日志提供每个流的 data_through、最后尝试、最后成功、连续失败、下一次尝试、最近一轮 raw/inserted/duplicates、熔断截止、Worker 心跳、待处理任务、最老任务年龄、Token 用量和预算状态。生产监控必须另行采集这些数据，并补充数据库与 WAL 大小、磁盘余量、证书到期和时间偏差；仓库内存在状态数据不等于服务器告警已经生效。disabled、not_configured 与从未尝试的来源不生成虚假计数。

服务器侧至少建立并留存以下告警证据：焦点快照超过 60 分钟、Feed 超过 30 分钟未更新、Calendar 超过 2 小时未更新、连续 3 次远程错误、Resync 持续失败、事件投影积压、Worker 心跳过期、数据库所在磁盘超过 80%、每日 Token 达到预算 80%，以及 submission_outcome_unknown、invalid_structured_output、incomplete_output、外键检查失败。通知渠道、阈值、最近一次测试时间和恢复结果写入当次发布记录；未接通通知渠道时必须标为待办，不能写成已完成。

日志只含端点类别、状态、耗时、请求编号和安全错误码。Secret、签名、Nonce、完整查询、新闻正文、原始模型响应和 OpenAI Response ID 不进入日志。

## 健康检查

Option Pro /ready 不依赖 Catalyst。Catalyst Worker 健康检查只验证进程、租约、Schema 和本地数据库；远程不可用时容器仍健康，业务状态为 degraded、stale 或 unavailable。

MacroLens Integration Health 与分析 Worker 容器共用同一心跳阈值：心跳缺失、过期或 Worker 失败时，analysis_queue 标为 unavailable、analysis_trigger_enabled=false，并给出安全警告码；恢复新鲜心跳后自动恢复。持续集成必须实际启动 Backend 与 Worker，再从两个容器核对契约、共享数据库和心跳。

## 回滚

### 触发条件

出现以下任一情况即停止扩大上线范围：Option Pro `/ready` 异常、核心行情或强势接口异常、SQLite 迁移或外键检查失败、认证循环、重复付费任务、跨股票错配、Catalyst 令核心页面报错、付费能力无法关闭，或资源使用失控。

### 最小功能回滚

1. Option Pro 设置 `MACROLENS_ENABLED=false`、`CATALYST_MODE=disabled`，停止 `catalyst-sync-worker`、`focus-context-producer` 和新分析周期任务。保持 `backend`、`breakout-worker`、行情与强势功能运行；本地缓存和审计数据保留。
2. MacroLens 设置 `HOT_CYCLE_SCHEDULE_ENABLED=false`、`HOT_CYCLE_ENABLED=false`、`NEWS_LLM_AUTO_ANALYZE_ENABLED=false`。若异常仍持续，再停止 `analysis-worker`；原始新闻抓取和数据库继续保留。
3. 关闭开关后复查 Option Pro `/ready`、核心行情、强势、突破雷达，以及 MacroLens `/live`、`/health`。隔离成功时无需回滚核心服务。

### 代码与数据库回滚

1. 记录故障时间、当前精确提交、发布标签和镜像摘要，停止会写入相关数据库的 Worker。
2. 对故障后的数据库再做一份新备份；使用 SQLite Backup API 或 `.backup` 处理 WAL，不覆盖发布前备份。执行 `integrity_check`、`foreign_key_check` 并记录校验摘要。
3. 切回上一份已验证的不可变标签和镜像摘要，不使用漂移分支。

   ```sh
   git fetch --tags origin
   git switch --detach <previous-verified-tag>
   test "$(git rev-parse HEAD)" = "$(git rev-list -n 1 <previous-verified-tag>)"
   ```

   Option Pro 在干净工作区执行 `./scripts/deploy.sh`，由脚本核对 `/ready` 中的精确提交。MacroLens 先执行 `docker compose config -q`，再执行 `docker compose build` 和 `docker compose up -d --no-build --force-recreate --remove-orphans --wait`。两端均需另行核对实际镜像摘要；仓库目前没有独立回滚脚本。
4. 优先保留新库：只有旧代码无法读取时，才恢复与旧版本兼容且已验证的数据库副本。不得删除新库、清空任务表、丢弃未消费热点或清理 OpenAI Response Identity。
5. 先启动数据库依赖和核心后端，再启动只读服务，最后按需恢复写 Worker。可能已经提交的付费任务只能查询原身份，不得重新创建。
6. 依次验证数据库、版本、健康端点、Option Pro 核心接口、MacroLens 核心接口和只读页面；确认数据未丢失后才结束回滚。

### 非破坏性演练

发布前在独立 Compose 项目名、临时端口和数据库备份副本上完成一次演练：检出上一标签、启动旧镜像、打开备份副本、执行核心冒烟，再销毁临时容器。演练不得连接生产写入端点或修改生产水位。记录标签、镜像摘要、数据库校验、冒烟结果和耗时；失败即阻断发布。

生产备份至少保留发布前、发布后首次稳定和上一可用发布三份，目录权限仅限部署账户。

## 生产发布顺序

1. 两个仓库的主分支持续集成全部通过，契约文件逐字节一致，并分别创建不可变标签和 GitHub Release。
2. 先备份并部署 MacroLens，再备份并部署 Option Pro；生产只能使用精确标签或提交。
3. 先以付费能力全部关闭的状态启动，再配置三组相互独立的 HMAC 凭据和 HTTPS 白名单。
4. 完成正确签名、错误签名、权限、重放、过期时间、错误正文摘要、来源地址、HTTP、重定向和契约不一致测试。
5. 只读同步稳定至少一个周期后，才按用户批准的每日任务数与输出 Token 预算分阶段开放手动能力。自动逐条新闻分析始终保持关闭。
6. 每次发布证据写入 Release Notes：最终提交、标签、持续集成链接、备份位置、镜像摘要、数据库检查、接口与页面验收、关闭能力和已知限制。仓库文件本身不代表生产已经部署。

## 测试

持续集成不得访问真实 OpenAI、MacroLens 生产服务或新闻源。必须运行 Python、Node、node --check、契约、HMAC、Point-in-Time、迁移、故障、容器、安全和视觉测试。

模型真实测试必须由显式操作触发，记录模型、reasoning、延迟、输入/缓存/输出 Token 和结果状态，不把密钥写入命令输出。

## 推送前

1. 重新获取两边 origin/main，确认远端未移动。
2. 为两边当前远端主分支建立带时间的备份引用。
3. 确认原始工作区的用户修改未被包含。
4. 完整测试通过后提交并推送各自功能分支。
5. 分别核对远端提交、持续集成和分支树。

远程部署、证书、允许网段、服务密钥、故障注入和模型冒烟的实际结果必须以当次 GitHub Release 与生产验收记录为准，不得从仓库提交状态推断。
