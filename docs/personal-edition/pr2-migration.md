# 个人版正式切换与回滚

本文保留试运行阶段的数据保护要求，命令已改为现行正式编排。所有命令均使用`docker-compose.yml`，服务清单只有`backend`与`worker`。

## 切换前

```bash
cp .env.example .env
cp machine.env.example machine.env
cp secrets.env.example secrets.env
chmod 600 .env machine.env secrets.env
docker compose config -q
```

核对`.env`只保留迁移兼容说明；`machine.env`只含监听地址、端口、MacroLens 地址、反向代理边界和`DATA_DIR`；`secrets.env`只含五个服务端密钥。访问模式、模型、推理等级、运行频率和预算以`config/personal.toml`为准。

密钥优先通过个人版命令行写入，避免出现在命令参数和终端输出中：

```bash
./personal.sh secrets set OPENAI_API_KEY
./personal.sh secrets set INTERNAL_API_TOKEN
./personal.sh secrets set APP_PASSWORD_HASH
./personal.sh secrets status
./personal.sh secrets validate
```

使用备份工具保存现有数据库：

```bash
mkdir -p backups/personal
docker compose run --rm --no-deps \
  --volume "$PWD/backups/personal:/backups" \
  worker python -m app.tools.sqlite_backup \
  --database optix=/data/optix.db \
  --database catalyst-cache=/data/catalyst-cache.db \
  --database ai-jobs=/data/ai-jobs.db \
  --database worker=/data/optix-worker.db \
  --destination /backups \
  --keep 7
```

某个数据库尚未创建时，确认原因后从命令中去掉该项，不创建空文件冒充数据。

## 正式切换

```bash
bash ./scripts/deploy.sh
```

发布脚本会从`config/personal.toml`读取访问模式，在启动前检查私网监听边界或 Owner 密码摘要。它只构建现行镜像，并通过容器编排（Docker Compose）的`--remove-orphans`移除已不在现行服务清单中的容器，不再按旧服务名逐个处理。命名卷不会被删除。

启动后检查：

```bash
docker compose ps
curl --fail http://127.0.0.1:${PORT:-2000}/ready
docker compose exec -T worker python -m app.worker --healthcheck
```

工作进程健康结果应且只应包含`breakout`、`catalyst_sync`、`focus`、`ai_jobs`、`maintenance`、`focus_refresh`、`strength_refresh`、`breakout_refresh`和`retention`。

## 观察与旧数据只读期

切换后观察一个完整美国交易周。旧 Catalyst 表、旧任务记录和旧分析修订至少保留 90 天，只供审计与兼容读取。观察期内不得清表、重写旧任务状态，或把旧备份覆盖到现行数据库。

## 回滚

```bash
docker compose down
git switch --detach <previous-verified-tag>
docker compose up -d --build
```

回滚继续使用同一数据卷，不附加`--volumes`。若需恢复备份，必须先停止所有写入者，核对摘要、清单、完整性检查和外键检查，再替换损坏文件。
