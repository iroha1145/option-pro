# Optix Pro

Optix Pro 是一个面向个人使用的美股行情、期权链和信号观察工具。后端使用 FastAPI，前端是无构建步骤的 Vanilla JavaScript SPA；行情主要来自 Yahoo Finance / yfinance，并可选接入 OpenAI Responses API。

> 本项目不是实时行情终端，也不构成投资建议。Yahoo 数据可能延迟、缺失或临时不可用；下单前请用券商行情确认价格、交易时段和合约信息。

## 当前能力

- 自选总览、股票搜索、价格走势和公司详情
- 指定到期日的期权链、成交量/持仓量和异动提示
- 板块内当前 ATM IV 对比和热力图
- 顶部/底部程序化信号、强势股雷达
- 预设美股列表的财报日历
- 可选 AI 分析：
  - 期权异动和顶部/底部信号只分析应用提供的结构化数据，不联网补行情或事件
  - 财报关联/影响分析会启用模型的 web search 工具

### “期权异动扫描”的准确范围

`GET /api/options/unusual` 不是全市场实时扫描。它会扫描以下 10 个热门标的：

`NVDA, TSLA, AAPL, AMD, AMZN, META, MSFT, SPY, QQQ, GOOGL`

每个标的只检查 Yahoo 返回的前两个到期日，结果缓存 120 秒并最多返回 50 条。数据频率、延迟和可用性由 Yahoo/yfinance 决定；接口会在部分标的失败时返回降级状态。

## Docker 快速开始

要求 Docker Engine / Docker Desktop，以及 Docker Compose 2.24 或更高版本。

```bash
git clone https://github.com/iroha1145/option-pro.git
cd option-pro
cp .env.example .env
chmod 600 .env
./scripts/deploy.sh
```

部署脚本会读取当前 Git 提交号，将它写入镜像标签和运行环境；为避免版本标记失真，Git 工作区存在未提交源码时会拒绝部署。镜像完整构建后才一次性重建容器，并核对 `/ready` 返回的提交号和前端文件完整性。前端已经打入同一个镜像，不会出现“新前端配旧后端”的混合版本。这个过程会有短暂的容器重启窗口，不是双机零停机切换。

访问 <http://localhost:2000>。进程健康信息位于 <http://localhost:2000/health>，部署就绪检查位于 <http://localhost:2000/ready>，两者都会返回 `app_version`、`app_commit` 和前端完整性摘要。

也可以运行交互式安装脚本：

```bash
./setup.sh
```

脚本会在仓库根目录创建权限为 `0600` 的 `.env`，等待容器通过健康检查后才报告启动成功。

从 GitHub 更新已经部署的服务器时，使用：

```bash
git pull --ff-only
./scripts/deploy.sh
```

不要在拉取代码后只运行 `docker compose restart`；重启不会构建新的后端镜像。

### OpenAI 配置（可选）

默认不启用 AI。使用 OpenAI 官方服务时只填写 key，`OPENAI_BASE_URL` 保持为空：

```dotenv
OPENAI_API_KEY=
OPENAI_BASE_URL=
ALLOW_CUSTOM_OPENAI_BASE_URL=false
OPENAI_MODEL=gpt-5.4-mini-2026-03-17
OPENAI_REASONING=low
```

如果使用 OpenAI-compatible 代理，`OPENAI_BASE_URL` 才填写代理地址，同时必须显式设置 `ALLOW_CUSTOM_OPENAI_BASE_URL=true`，并且只能使用该代理签发的专属 key。自定义公网地址必须是 HTTPS（本机回环地址除外）。不要把 OpenAI 官方 key 交给第三方代理。兼容服务还需要支持 Responses API；财报联网分析需要支持 `web_search_preview` 工具。

从旧版升级且 `.env` 中已有非空 `OPENAI_BASE_URL` 时，部署前必须完成上述 key 确认并补上 `ALLOW_CUSTOM_OPENAI_BASE_URL=true`，否则应用会拒绝启动 AI 配置。

AI 请求默认总超时 45 秒、不自动重试、最多输出 1200 tokens，整个进程最多同时访问模型 2 次。可通过以下变量调整，但不建议为个人部署大幅提高：

```dotenv
OPENAI_TIMEOUT_SECONDS=45
OPENAI_MAX_RETRIES=0
OPENAI_MAX_OUTPUT_TOKENS=1200
OPENAI_MAX_CONCURRENCY=2
```

## 安全的远程访问

Compose 默认只发布到 `127.0.0.1:2000`。推荐保持这个默认值，并选择以下方式之一：

1. SSH 隧道：

   ```bash
   ssh -L 2000:127.0.0.1:2000 user@your-server
   ```

   然后在本机打开 <http://localhost:2000>。

2. 通过可信 VPN（例如 WireGuard/Tailscale）访问服务器内网。
3. 放在配置了 HTTPS 的反向代理后，并设置强随机 `APP_AUTH_TOKEN`。

不要把 `HOST_BIND` 改成 `0.0.0.0` 后直接通过公网 HTTP 暴露服务。HTTP 不加密浏览器 token；内置 token 也只是个人部署的轻量保护，不是多用户权限系统。应用默认会拒绝“非本机监听且 `APP_AUTH_TOKEN` 为空”的组合。

只有服务位于外部防火墙或 VPN 保护的私有网络、并且确实需要无 token 访问时，才可显式设置 `ALLOW_INSECURE_PUBLIC_BIND=true`。它只是解除启动保护，不会自动提供加密或访问控制，不能用于普通公网 HTTP。

设置 `APP_AUTH_TOKEN` 后，在同源页面的浏览器控制台保存同一个 token：

```js
sessionStorage.setItem('optix.app.token', 'same-strong-random-token');
location.reload();
```

只有部署在可信 HTTPS/SSH/VPN 链路上时才这样使用。若反向代理与前端跨域，再精确设置 `ALLOWED_ORIGINS`；只有在可信代理已经覆盖并清洗转发头时才设置 `TRUST_PROXY_HEADERS=true`。

## 本地开发

本地开发使用 Python 3.12。根目录 `.env` 会按绝对路径加载，因此从 `backend/` 启动时也能可靠读取配置：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r backend/requirements.txt
cp .env.example .env
cd backend
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

访问 <http://localhost:8000>，API 文档位于 <http://localhost:8000/docs>。

### 依赖锁定

- `backend/requirements.in` 保存直接运行依赖。
- `backend/requirements.txt` 保存完整间接依赖和 PyPI 发行文件哈希，Docker 使用它安装。
- `backend/requirements-ci.in` 与 `backend/requirements-ci.txt` 另外锁定测试和依赖审计工具。

修改 `.in` 文件后，安装 `uv` 并重新生成锁文件：

```bash
./scripts/lock-dependencies.sh
```

持续集成会以 Python 3.12.13 安装哈希锁定文件，运行依赖漏洞审计、测试、镜像构建、容器就绪检查，以及首页和压缩响应的冒烟测试。

## 常用接口

| 接口 | 说明 |
| --- | --- |
| `GET /health` | 进程健康、版本和前端完整性信息 |
| `GET /ready` | 容器部署就绪检查；前端文件不完整时返回 503 |
| `GET /api/stocks/watchlist` | 预设自选行情 |
| `GET /api/stocks/search?q=nvidia` | 搜索股票 |
| `GET /api/stocks/{ticker}` | 股票概况 |
| `GET /api/stocks/{ticker}/chart?range=1d` | 日 K 线数据 |
| `GET /api/options/{ticker}/expirations` | 可用到期日 |
| `GET /api/options/{ticker}/chain?expiration=2026-07-17` | 指定期权链 |
| `GET /api/options/unusual` | 10 个热门标的的有限异动扫描 |
| `GET /api/earnings/upcoming` | 预设列表财报日历 |
| `GET /api/sectors/{id}/iv-ranking` | 板块当前 IV 对比 |
| `GET /api/signals/stock/{ticker}` | 程序化顶部/底部信号 |
| `GET /api/strength/scan` | 强势股扫描 |

## 运维

```bash
docker compose ps
docker compose logs -f backend
docker compose restart backend
docker compose down
```

`restart` 只适合重启当前镜像；部署新提交应运行 `./scripts/deploy.sh`。

容器以非 root 用户运行，应用源码由 root 所有且根文件系统只读；运行时缓存限制在 `/tmp` 内存文件系统。Docker 日志使用轮转，健康检查访问 `/ready`。镜像构建上下文通过 `.dockerignore` 排除 `.env`、Git 历史、缓存和本地虚拟环境。

当前仓库没有绑定某台服务器的自动部署任务。持续集成通过只能证明该提交通过测试与容器冒烟检查；服务器是否已经更新，应以服务器 `/ready` 返回的 `app_commit` 为准。

## 项目结构

```text
option-pro/
├── backend/
│   ├── app/
│   │   ├── api/          # FastAPI 路由
│   │   ├── services/     # 行情、评分和 AI 服务
│   │   ├── models/       # Pydantic 模型
│   │   └── main.py
│   ├── requirements.in
│   ├── requirements.txt
│   ├── requirements-ci.in
│   ├── requirements-ci.txt
│   └── Dockerfile
├── frontend/
│   ├── index.html
│   └── static/
├── docker-compose.yml
├── scripts/
│   ├── deploy.sh
│   └── lock-dependencies.sh
├── setup.sh
└── .env.example
```

## License

[MIT](LICENSE)
