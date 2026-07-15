# 个人版迁移说明

## 发布顺序

1. 记录当前提交、镜像摘要和数据库结构版本。
2. 使用轻量数据库（SQLite）备份接口保存全部业务数据库，并核对快速检查、完整性检查和外键检查。
3. 构建新镜像。构建期间旧后端仍可提供页面。
4. 停止同一容器编排项目中的旧工作容器，等待已经提交的模型任务到达安全边界，并确认所有旧进程已经退出。
5. 启动 `backend` 与统一的 `worker`，不得让新旧写入者同时连接同一数据卷。
6. 核对后端提交、前端完整性、统一工作进程锁和九类任务清单。
7. 观察一个完整的美国交易周，再清理旧运行代码与兼容入口。

`scripts/deploy.sh`负责第 3 至第 6 步。它不会运行一次性任务，不会请求新闻刷新，也不会创建模型任务。

## 数据保存

发布前备份：

- `/data/optix.db`
- `/data/catalyst-cache.db`
- `/data/ai-jobs.db`
- `/data/optix-worker.db`（首次统一运行后存在）

备份清单应记录创建时间、源路径、结构版本和摘要（SHA-256）。备份文件必须放在 `optix-data` 之外，以防数据卷或磁盘故障。

旧 Catalyst 表、旧分析投影和旧任务记录在迁移后至少保留 90 天。保留期内只允许兼容读取、审计和导出：

- 不删除旧行；
- 不把旧英文结果重新显示到页面；
- 不把导入旧结果计入新每日付费额度；
- 不重新提交状态不确定的模型任务；
- 不用旧备份覆盖已经出现新任务记录的数据库。

保留期结束后，只有在导出、审计抽查和恢复演练均完成时，才可另行安排数据清理。

## 环境文件迁移

正式运行统一读取仓库根目录的`.env`。旧环境文件可先转换：

```bash
PYTHONPATH=backend python -m app.tools.migrate_personal_config \
  .env --output-directory config/migrated
```

The command writes four files:

- `personal.toml`: behavior, including access mode, AI limits, schedule and retention;
- `secrets.env`: only `OPENAI_API_KEY`, `FINNHUB_API_KEY`, `MARKETDATA_TOKEN`, `INTERNAL_API_TOKEN` and `APP_PASSWORD_HASH`;
- `machine.env`: `HOST_BIND`, `PORT`, `MACROLENS_URL`, `ALLOWED_HOSTS`, `TRUST_PROXY_HEADERS`, `TRUSTED_PROXY_CIDRS` and `DATA_DIR`;
- `migration-report.json`: key names and migration status only.

The last three files use mode `0600`. The report never contains values, value lengths, hashes, URL values or secret fragments. `MARKETDATA_API_TOKEN` migrates to `MARKETDATA_TOKEN`, `MACROLENS_BASE_URL` migrates to `MACROLENS_URL`, and `MACROLENS_INTERNAL_TOKEN` migrates to `INTERNAL_API_TOKEN`. If either old and new name is present with a different non-empty value, conversion stops and records only the conflicting key names.

Old browser and HMAC credentials are not copied. They appear under `removed_keys` with status `removed_by_personal_edition`. If `APP_AUTH_TOKEN` exists without `APP_PASSWORD_HASH`, the report sets `requires_owner_password=true`; configure the replacement with:

```bash
./personal.sh secrets set APP_PASSWORD_HASH
```

Manage the other server-only values with the same command, for example `./personal.sh secrets set MARKETDATA_TOKEN`. After changing a secret, running containers are recreated so Compose rereads `secrets.env`; a plain restart is not used. Run `./personal.sh doctor` before deployment to apply the same access-boundary validation used by the application and deployment script.

## Access boundary

`private_network` is for direct loopback, SSH forwarding, RFC1918, Tailscale, WireGuard or IPv6 unique-local access. It requires `TRUST_PROXY_HEADERS=false`; `HOST_BIND` and `ALLOWED_HOSTS` may contain only approved private IP literals or localhost. DNS names and wildcard binds fail startup.

Every HTTP reverse proxy, public domain, Cloudflare Tunnel or public load balancer must use `password`. This mode requires a valid `APP_PASSWORD_HASH` and explicit `ALLOWED_HOSTS`. When proxy headers are enabled, `TRUSTED_PROXY_CIDRS` must contain only the actual private proxy source networks. Public catch-all networks such as `0.0.0.0/0` are rejected. A public domain such as `option.openweb-ui.xyz` therefore always uses password mode and HTTPS.

The compatibility adapter is removed after the first Personal Edition production release.

MacroLens 只保留 HTTPS 地址与一个内部不记名令牌（Bearer Token）。本机 HTTP、旧式签名密钥和反向焦点读取配置不得带入新环境文件。

## 回滚

回滚时先停止统一工作进程，再切换到上一份经过验证的发布标签与镜像。继续使用当前数据卷，优先依靠向前兼容读取；只有数据库损坏或完整性检查失败时，才从已核验的备份恢复。

停止服务时不要使用`--volumes`或`-v`。强制终止可能把正在保存响应身份的付费任务留在不确定状态，因此应保留 2100 秒停止宽限期。
