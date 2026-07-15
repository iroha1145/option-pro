# 个人版正式切换与回滚

本文保留个人版试运行阶段的迁移要点，但命令已经更新为现行正式编排。独立的个人版编排文件已删除，所有命令均使用`docker-compose.yml`。

## 切换前

```bash
cp .env.example .env
chmod 600 .env
docker compose config -q
```

核对`.env`只含密钥、HTTPS 地址和机器网络边界。模型、推理等级和运行频率以`config/personal.toml`为准。

使用备份工具保存现有数据库：

```bash
mkdir -p backups/personal
docker compose run --rm --no-deps \
  --volume "$PWD/backups/personal:/backups" \
  worker python -m app.tools.sqlite_backup \
  --database optix=/data/optix.db \
  --database catalyst-cache=/data/catalyst-cache.db \
  --database ai-jobs=/data/ai-jobs.db \
  --destination /backups \
  --keep 7
```

某个数据库从未创建时，确认原因后从命令中去掉该项，不要创建空文件冒充数据。统一工作进程运行后，将`/data/optix-worker.db`加入日常备份。

## 正式切换

```bash
bash ./scripts/deploy.sh
```

脚本会先构建镜像，再停止并确认旧工作容器退出，最后启动`backend`与`worker`。它不会删除命名卷。

启动后检查：

```bash
docker compose ps
curl --fail http://127.0.0.1:${PORT:-2000}/ready
docker compose exec -T worker python -m app.worker --healthcheck
```

工作进程健康结果应包含`breakout`、`catalyst_sync`、`focus`、`ai_jobs`和`maintenance`。

## 观察与旧数据只读期

切换后观察一个完整美国交易周。旧 Catalyst 表、旧任务记录和旧分析投影至少保留 90 天，只供审计与兼容读取。不得在观察期清表、重写旧任务状态，或把旧备份覆盖到现行数据库。

## 回滚

```bash
docker compose down
git switch --detach <previous-verified-tag>
docker compose up -d --build
```

回滚继续使用同一数据卷，不附加`--volumes`。如需恢复备份，必须先停止所有写入者，核对摘要、清单、完整性检查和外键检查，再替换损坏文件。
