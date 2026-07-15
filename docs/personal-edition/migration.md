# 个人版迁移说明

## 发布边界

正式编排只有 `backend` 与 `worker`。发布时应依次完成：

1. 记录当前提交、镜像摘要和数据库结构版本。
2. 使用轻量数据库（SQLite）备份接口保存全部业务数据库，并核对快速检查、完整性检查和外键检查。
3. 构建新镜像。构建期间旧后端仍可提供页面。
4. 停止同一容器编排项目中的旧工作容器，等待已提交的模型任务到达安全边界，并确认所有旧进程已经退出。
5. 启动 `backend` 与统一的 `worker`，不得让新旧写入者同时连接同一数据卷。
6. 核对后端提交、前端完整性、统一工作进程锁和九类任务清单。
7. 观察一个完整的美国交易周，再清理旧运行代码与兼容入口。

`scripts/deploy.sh` 负责校验配置、构建镜像、停止旧写入者、启动两个服务并完成就绪核对。它不会运行一次性任务，不请求新闻刷新，不扫描行情，也不创建模型任务。发布脚本会拒绝未提交的工作树，避免服务运行内容与记录的提交不一致。

## 环境文件迁移

现行配置分为三层：

- `config/personal.toml`：访问模式、功能模式、调度、模型、预算与保留期；
- `machine.env`：七项机器配置，包括统一数据目录；
- `secrets.env`：五项服务端密钥。

`.env` 只保留一个迁移版本的兼容用途。旧环境文件可先转换为人工核对草稿：

```bash
PYTHONPATH=backend python -m app.tools.migrate_personal_config \
  .env --output-directory config/migrated
```

该命令写入四个文件：

- `personal.toml`：访问模式、模型限制、调度与保留期等行为配置；
- `machine.env`：`HOST_BIND`、`PORT`、`MACROLENS_URL`、`ALLOWED_HOSTS`、`TRUST_PROXY_HEADERS`、`TRUSTED_PROXY_CIDRS` 和 `DATA_DIR`；
- `secrets.env`：`OPENAI_API_KEY`、`FINNHUB_API_KEY`、`MARKETDATA_TOKEN`、`INTERNAL_API_TOKEN` 和 `APP_PASSWORD_HASH`；
- `migration-report.json`：只记录字段名称与迁移状态。

后三个文件权限均为 `0600`。迁移报告只含 `mapped_keys`、`deprecated_keys`、`removed_keys`、`conflicting_keys`、`unmapped_keys`、`requires_owner_password` 和 `warnings`，不得出现值、值长度、摘要、网址或任何密钥片段。

旧名称按以下规则迁移：

- `MARKETDATA_API_TOKEN` 转为 `MARKETDATA_TOKEN`；
- `MACROLENS_BASE_URL` 转为 `MACROLENS_URL`；
- `MACROLENS_INTERNAL_TOKEN` 转为 `INTERNAL_API_TOKEN`。

旧名与新名同时存在且非空值不同，转换会停止，并且只把冲突字段名写入报告。旧浏览器令牌、签名密钥、请求随机数和密钥编号不会复制；它们只会以 `removed_by_personal_edition` 状态列入 `removed_keys`。

若存在 `APP_AUTH_TOKEN` 而没有 `APP_PASSWORD_HASH`，报告会把 `requires_owner_password` 设为 `true`。旧浏览器令牌不会迁移为所有者密码，应在服务器终端另行设置：

```bash
./personal.sh secrets set APP_PASSWORD_HASH
```

建立正式文件：

```bash
cp .env.example .env
cp machine.env.example machine.env
cp secrets.env.example secrets.env
chmod 600 .env machine.env secrets.env
```

只把已经核对的字段写入对应文件。MacroLens 连接统一为 `machine.env` 中的 `MACROLENS_URL` 与 `secrets.env` 中的 `INTERNAL_API_TOKEN`，两者必须同时配置或同时留空；正式部署只接受超文本传输安全协议（HTTPS）地址。运行时不再读取旧访问开关、旧 MacroLens 名称或独立数据路径。

## 访问边界

`private_network` 用于本机回环、安全外壳（SSH）转发、RFC1918 私网、Tailscale、WireGuard 或 IPv6 本地地址。它要求 `TRUST_PROXY_HEADERS=false`；`HOST_BIND` 和 `ALLOWED_HOSTS` 只能包含获准的私网地址或本机名称，域名和通配监听会使启动失败。

任何反向代理、公开域名、Cloudflare Tunnel 或公网负载均衡器都必须使用 `password`。该模式要求有效的 `APP_PASSWORD_HASH` 和明确的 `ALLOWED_HOSTS`。启用代理头时，`TRUSTED_PROXY_CIDRS` 只能包含实际代理来源网段，`0.0.0.0/0` 一类公网全网段会被拒绝。

## 数据保存

发布前备份 `DATA_DIR` 中的业务文件：

- `optix.db`；
- `catalyst-cache.db`；
- `ai-jobs.db`；
- `optix-worker.db`；
- `runtime-settings.json`；
- `watchlist-snapshot-v1.json`。

备份清单应记录创建时间、源路径、结构版本和 SHA-256 摘要。备份必须放在 `optix-data` 之外，防止数据卷或磁盘故障同时损坏正本与备份。

旧 Catalyst 表、旧分析修订和旧任务记录在迁移后至少保留 90 天。保留期内不删除旧行、不重新提交状态不确定的模型任务，也不用旧备份覆盖已经产生新任务的数据库。

## 发布与核对

```bash
docker compose config -q
bash ./scripts/deploy.sh
```

启动后核对：

```bash
docker compose ps
curl --fail http://127.0.0.1:${PORT:-2000}/ready
docker compose exec -T worker python -m app.worker --healthcheck
```

工作进程结果应且只应包含 `breakout`、`catalyst_sync`、`focus`、`ai_jobs`、`maintenance`、`focus_refresh`、`strength_refresh`、`breakout_refresh` 和 `retention`。

## 回滚

回滚时先停止现行编排，再切换到上一个经过验证的发布标签与镜像。继续使用当前数据卷，只有数据库损坏或完整性检查失败时，才从已核验的备份恢复。

停止服务时不得附加 `--volumes` 或 `-v`。统一工作进程的停止宽限期为 2100 秒，强制终止可能使正在保存响应身份的付费任务留在不确定状态。
