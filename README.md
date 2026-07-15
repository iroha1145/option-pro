# Optix Pro

Optix Pro 是面向个人使用的美股期权、突破信号与新闻分析工作台。网页、接口和后台任务共用同一份代码与数据卷，长期运行的容器只有两个：

- `backend`：提供网页、接口和只读查询，也接收所有者明确发起的任务。
- `worker`：统一管理突破扫描、新闻同步、焦点快照、模型任务、刷新、备份与清理。

模型固定为 GPT-5.6 Terra，推理等级固定为 `max`，最大并发数固定为 1。新闻标题、摘要、等待提示和分析内容必须通过简体中文校验；来源原文只作为内部证据保留。

本项目不是实时行情终端，也不构成投资建议。Yahoo 等公开数据可能延迟、缺失或临时不可用，下单前仍需用券商行情核对。

## 运行条件

- Docker Desktop，或带有容器编排（Docker Compose）2.24 以上版本的 Docker Engine
- 建议至少 4 GB 可用内存
- 默认端口 `2000`

## 安装

交互式安装：

```bash
./setup.sh
```

手动安装：

```bash
cp .env.example .env
cp machine.env.example machine.env
cp secrets.env.example secrets.env
chmod 600 .env machine.env secrets.env
./personal.sh doctor
./scripts/deploy.sh
```

完成后访问 <http://localhost:2000>。进程健康信息位于 `/health`，部署就绪检查位于 `/ready`。

部署脚本会构建当前提交，停止同一编排项目中的旧工作容器，确认旧写入者已经退出，再启动 `backend` 与统一的 `worker`。不要用 `docker compose restart` 代替新版本构建。

## 配置边界

`config/personal.toml` 管理功能行为、任务频率、模型限制、预算与数据保留期。正式运行参数不能通过环境变量改成其他模型或更高并发。

`machine.env` 只保存七项机器配置：

- `HOST_BIND`
- `PORT`
- `MACROLENS_URL`
- `ALLOWED_HOSTS`
- `TRUST_PROXY_HEADERS`
- `TRUSTED_PROXY_CIDRS`
- `DATA_DIR`

`secrets.env` 只保存五项服务端密钥：

- `OPENAI_API_KEY`
- `FINNHUB_API_KEY`
- `MARKETDATA_TOKEN`
- `INTERNAL_API_TOKEN`
- `APP_PASSWORD_HASH`

进程已经导出的值优先级最高；文件加载顺序为 `.env`、`machine.env`、`secrets.env`。`.env` 只保留一个版本迁移期的兼容用途，新安装应把机器配置和密钥放入对应文件。

旧名称 `MARKETDATA_API_TOKEN`、`MACROLENS_BASE_URL` 和 `MACROLENS_INTERNAL_TOKEN` 只供迁移工具识别。旧名与新名同时存在且值不一致时，迁移会停止，不会猜测采用哪一项。旧签名密钥、Nonce、Key ID、前一把密钥和浏览器令牌不会进入最终运行配置。

## 模型分析

默认没有模型密钥，因此不会提交付费任务。需要启用时，在服务器上执行：

```bash
./personal.sh secrets set OPENAI_API_KEY
```

个人版只连接 OpenAI 官方响应接口（Responses API），不接受自定义模型代理。固定参数为：

```dotenv
OPENAI_MODEL=gpt-5.6-terra
OPENAI_REASONING=max
OPENAI_EXECUTION_MODE=background
OPENAI_MAX_RETRIES=0
OPENAI_MAX_CONCURRENCY=1
```

模型任务保留严格结构化输出、每日任务次数、每日美元预算与冷却限制。相同输入会先复用原任务，即使队列已经满也不会重复计费；只有新任务在队列饱和时返回 429 和 `Retry-After: 60`。

## 访问安全

默认只发布到 `127.0.0.1:2000`。

`private_network` 只用于本机、SSH 转发、可信虚拟专用网络（VPN）或直接私网连接。该模式必须保持 `TRUST_PROXY_HEADERS=false`，不能放在 Nginx、Caddy、Cloudflare Tunnel 或公网负载均衡器后；监听地址和允许主机也只能使用本机或许可私网地址。

任何反向代理、公开域名或公网入口都必须使用 `password` 模式，并满足以下条件：

- `secrets.env` 中存在有效的 `APP_PASSWORD_HASH`；
- `machine.env` 明确列出允许访问的域名；
- 外层代理提供有效的 HTTPS；
- 只有代理确实清洗转发头时才启用 `TRUST_PROXY_HEADERS`；
- `TRUSTED_PROXY_CIDRS` 只包含实际代理来源网段，不得使用公网全网段。

例如 `option.openweb-ui.xyz` 一类公开域名必须使用密码模式和 HTTPS。应用启动、`./personal.sh doctor` 与部署脚本共用同一校验器，配置不完整时都会停止。

## 健康检查

```bash
curl --fail http://127.0.0.1:2000/ready
docker compose exec -T worker python -m app.worker --healthcheck
```

最终统一工作进程应报告九项任务：

- `breakout`
- `catalyst_sync`
- `focus`
- `ai_jobs`
- `maintenance`
- `focus_refresh`
- `strength_refresh`
- `breakout_refresh`
- `retention`

健康检查只读取本地进程锁、心跳和任务状态，不会创建模型任务，不会请求真实新闻源，也不会消耗付费额度。

## 数据与迁移

所有运行数据保存在 `optix-data` 命名卷。升级和回滚时不要附加 `--volumes` 或 `-v`。

主要数据库位于统一的 `DATA_DIR`：

- `optix.db`
- `catalyst-cache.db`
- `ai-jobs.db`
- `optix-worker.db`

迁移工具会生成 `personal.toml`、`machine.env`、`secrets.env` 和不含任何值的 `migration-report.json`。详细边界见[个人版迁移说明](docs/personal-edition/migration.md)。

## 期权异动范围

`GET /api/options/unusual` 不是全市场实时扫描。它只扫描 `NVDA`、`TSLA`、`AAPL`、`AMD`、`AMZN`、`META`、`MSFT`、`SPY`、`QQQ`、`GOOGL`，每个标的检查 Yahoo 返回的前两个到期日，结果缓存 120 秒并最多返回 50 条。

## 本地验证

```bash
bash -n setup.sh personal.sh scripts/deploy.sh scripts/lock-dependencies.sh
docker compose config -q
PYTHONPATH=backend python -m pytest -q
node --test frontend/tests/*.test.mjs
node frontend/tests/static_assertions.mjs
npm --prefix frontend run test:visual
```

持续集成（CI）只使用本地夹具和模拟连接，不访问真实 OpenAI、新闻源、行情源或生产数据库。

## 日常管理

```bash
docker compose ps
docker compose logs -f backend worker
docker compose restart backend
docker compose down
```

持续集成通过只说明该提交通过测试与容器检查，不代表生产服务器已经更新。

## 许可证

[MIT](LICENSE)
