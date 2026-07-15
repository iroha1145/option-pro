# PR2 个人版迁移与回滚

## 变更边界

本阶段把原有四个独立工作器合并为一个 `worker` 服务。个人版容器编排（Docker Compose）文件只保留 `backend`、`worker` 和共享的 `optix-data` 数据卷。旧 `docker-compose.yml` 暂不删除，作为一个发布周期内的回滚入口。

两个编排文件使用同名数据卷。只要在同一项目目录、使用相同的项目名执行命令，切换编排不会复制或覆盖数据库。

## 上线前备份

备份工具使用轻量数据库（SQLite）的在线备份接口（SQLite Backup API），可在旧服务仍运行时取得一致快照。每份备份在发布前都会执行快速检查、完整性检查和外键检查，并生成摘要（SHA-256）、校验文件与清单文件。

旧 `.env` 尚未转换时，先生成只含密钥和内部令牌的环境文件。个人版编排默认读取 `config/migrated/secrets.env`，不会把旧环境文件中的行为开关带入容器：

```bash
PYTHONPATH=backend .venv/bin/python -m app.tools.migrate_personal_config \
  .env --output-directory config/migrated
```

先构建个人版镜像并准备宿主机备份目录：

```bash
docker compose -f docker-compose.personal.yml build
mkdir -p backups/pr2
```

备份已经存在的业务数据库：

```bash
docker compose -f docker-compose.personal.yml run --rm --no-deps \
  --volume "$PWD/backups/pr2:/backups" \
  worker python -m app.tools.sqlite_backup \
  --database optix=/data/optix.db \
  --database catalyst-cache=/data/catalyst-cache.db \
  --database ai-jobs=/data/ai-jobs.db \
  --destination /backups \
  --keep 7
```

某个可选功能从未启用时，对应数据库可能尚未创建。确认这一点后，从命令中移除该数据库参数再执行；不要用空文件冒充数据库。统一工作器首次启动后，还应把 `/data/optix-worker.db` 加入日常备份：

```bash
--database worker=/data/optix-worker.db
```

工具返回成功状态后，备份目录中每个 `.sqlite3` 文件都有同名的 `.json` 清单和 `.sha256` 校验文件。可在备份目录再次核对摘要：

```bash
cd backups/pr2
shasum -a 256 -c ./*.sha256
```

保留数量按数据库标签分别计算。`--keep 7` 表示 `optix`、`catalyst-cache`、`ai-jobs` 和 `worker` 各保留最新七份，清理旧备份时会一并删除其清单和校验文件。

## 切换到个人版编排

先检查展开后的配置，尤其是端口、数据库路径、模型和推理等级：

```bash
docker compose -f docker-compose.personal.yml config
```

个人版默认使用 `gpt-5.6-terra`，推理等级为 `max`。正式切换前停止旧编排，禁止旧工作器与统一工作器同时写入同一数据卷：

```bash
docker compose -f docker-compose.yml down
docker compose -f docker-compose.personal.yml up -d --build
```

停止旧编排时不要添加 `--volumes` 或 `-v`，否则会删除共享数据卷。

启动后检查两个服务：

```bash
docker compose -f docker-compose.personal.yml ps
curl --fail http://127.0.0.1:${PORT:-2000}/ready
docker compose -f docker-compose.personal.yml exec worker \
  python -m app.worker --status
```

验收范围包括：后端就绪、统一工作器健康、五类任务都有状态记录、模型与推理等级没有被环境文件覆盖。观察期间保留旧镜像标签、旧编排文件和上线前备份。

统一工作器收到终止信号后会等待已提交的模型任务结束，单个任务上限为 2000 秒。因此 `worker` 的停止宽限期设为 2100 秒；运维脚本不要用强制终止缩短这一时间，否则可能留下结果未知的付费任务。

## 正常回滚

代码或运行行为不符合预期时，先停止个人版，再从上一个发布标签启动旧编排：

```bash
docker compose -f docker-compose.personal.yml down
git switch --detach <PR1_RELEASE_TAG>
docker compose -f docker-compose.yml up -d --build
```

正常回滚继续使用现有数据卷，不恢复旧数据库。数据库迁移必须保持向前兼容；把旧备份覆盖到有新任务记录的数据库，会丢失上线后的状态和作业历史。

旧服务恢复后检查后端就绪状态，并逐一确认原有四个工作器处于健康状态。确认稳定后再处理失败的个人版容器和镜像。

## 数据恢复

只有数据库损坏、误删或完整性检查失败时才从备份恢复，并先停止所有读写该数据卷的服务。恢复前必须完成三项检查：

1. `.sha256` 校验通过；
2. `.json` 清单中的数据库标签、创建时间和源路径符合预期；
3. 用只读连接执行 `PRAGMA integrity_check`，结果为 `ok`。

恢复时先把当前损坏文件另存为取证副本，再把选定备份复制回清单记录的数据库路径。恢复完成后重新执行完整性检查，随后只启动后端验证读取，最后再启动工作器。不要在工作器仍运行时替换数据库文件。

## 日常备份建议

由宿主机定时任务调用同一备份命令，并把 `/backups` 绑定到数据卷以外的磁盘或远端同步目录。备份与业务数据库放在同一个 `optix-data` 卷无法抵御数据卷误删或磁盘故障。

每次发布前保留一份不会被日常保留周期立即清理的备份，并记录发布标签、清单文件名和摘要。定期在隔离目录做恢复演练，成功生成文件并不等于已经验证恢复流程。
