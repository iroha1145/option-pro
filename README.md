# Optix Pro

Optix Pro 是一个面向个人使用的美股行情、期权链和信号观察工具。后端使用 FastAPI，前端是无构建步骤的 Vanilla JavaScript SPA；行情主要来自 Yahoo Finance / yfinance，并可选接入 OpenAI Responses API。

> 本项目不是实时行情终端，也不构成投资建议。Yahoo 数据可能延迟、缺失或临时不可用；下单前请用券商行情确认价格、交易时段和合约信息。

## 当前能力

- 自选总览、股票搜索、价格走势和公司详情
- 指定到期日的期权链、成交量/持仓量和异动提示
- 板块内当前 ATM IV 对比和热力图
- 顶部/底部程序化信号、强势股雷达
- 突破雷达：全市场粗筛、点时复核、生命周期跟踪和可解释评分
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
cp machine.env.example machine.env
cp secrets.env.example secrets.env
chmod 600 .env
chmod 600 machine.env secrets.env
./personal.sh doctor
./scripts/deploy.sh
```

部署脚本会读取当前 Git 提交号，将它写入镜像标签和运行环境；为避免版本标记失真，Git 工作区存在未提交源码时会拒绝部署。镜像完整构建后才一次性重建容器，并核对 `/ready` 返回的提交号和前端文件完整性。前端已经打入同一个镜像，不会出现“新前端配旧后端”的混合版本。这个过程会有短暂的容器重启窗口，不是双机零停机切换。

访问 <http://localhost:2000>。进程健康信息位于 <http://localhost:2000/health>，部署就绪检查位于 <http://localhost:2000/ready>，两者都会返回 `app_version`、`app_commit` 和前端完整性摘要。

也可以运行交互式安装脚本：

```bash
./setup.sh
```

脚本会在仓库根目录创建权限为 `0600` 的 `.env`，等待容器通过健康检查后才报告启动成功。

焦点快照生产器默认关闭。只有完成 MacroLens 对接配置并确认需要发布焦点数据时，才在 `.env` 中显式设置：

```dotenv
FOCUS_PRODUCER_ENABLED=true
FOCUS_PRODUCER_SNAPSHOT_GRACE_SECONDS=120
FOCUS_DAILY_STRENGTH_SETTLEMENT_DELAY_SECONDS=1800
FOCUS_DAILY_STRENGTH_MIN_COVERAGE=0.9
```

未设置启用变量、使用环境模板或运行交互式安装脚本时，焦点快照生产器均保持关闭。显式开启后，日线缓存会等待收盘数据结算 30 分钟，并且只有预期股票覆盖率达到 90% 才会写入全天缓存；覆盖不足或数据仍落后时只使用短时降级缓存。每日强度缓存保留 30 天；焦点快照最近 30 天保留全部半小时版本，30 至 90 天每个交易日保留代表版本，同时保留市场焦点周期和任务引用的完整输入。

`FOCUS_PRODUCER_SNAPSHOT_GRACE_SECONDS` 是焦点快照刷新时的健康宽限，默认 120 秒，可设置为 30 至 900 秒。旧快照刚超过正常刷新周期时，只要生产器仍在刷新且心跳正常，服务会在宽限期内保持可用并标记为降级；宽限结束后仍未发布新快照，健康检查才会判定失败。部署脚本会在启动前校验该范围。

从 GitHub 更新已经部署的服务器时，使用：

```bash
git pull --ff-only
./scripts/deploy.sh
```

不要在拉取代码后只运行 `docker compose restart`；重启不会构建新的后端镜像。

### OpenAI 配置（可选）

默认不启用模型分析。启用时，通过服务端密钥命令写入 OpenAI 密钥：

```bash
./personal.sh secrets set OPENAI_API_KEY
```

个人版只连接 OpenAI 官方响应接口（Responses API），不接受自定义模型代理。运行参数固定为：

```dotenv
OPENAI_MODEL=gpt-5.6-terra
OPENAI_REASONING=max
OPENAI_EXECUTION_MODE=background
OPENAI_MAX_RETRIES=0
OPENAI_MAX_CONCURRENCY=1
```

模型调用保留后台模式、严格结构化输出、每日任务次数和美元预算门禁。同一输入复用同一任务，不会因重复点击再次计费。新闻标题、新闻摘要、等待提示和分析结果都经过本地简体中文校验；来源原文只用于内部分析，不会回退到网页展示。

## 安全的远程访问

Compose 默认只发布到 `127.0.0.1:2000`。推荐保持这个默认值，并选择以下方式之一：

1. SSH 隧道：

   ```bash
   ssh -L 2000:127.0.0.1:2000 user@your-server
   ```

   然后在本机打开 <http://localhost:2000>。

2. 通过可信 VPN（例如 WireGuard/Tailscale）访问服务器内网。
3. 放在配置了 HTTPS 的反向代理后，并启用个人密码登录。

`private_network` 只允许直接的本机或私网连接，并且必须保持 `TRUST_PROXY_HEADERS=false`。它不能放在 Nginx、Caddy、Cloudflare Tunnel 或公网负载均衡器后，因为代理会遮住访客的真实来源。该模式的 `HOST_BIND` 和 `ALLOWED_HOSTS` 只能使用 localhost 或个人配置允许范围内的私网 IP 字面量。

任何公开域名或 HTTP 反向代理都必须在 `config/personal.toml` 中使用 `password` 模式，并设置有效的 `APP_PASSWORD_HASH`：

```toml
[access]
mode = "password"
```

```bash
./personal.sh secrets set APP_PASSWORD_HASH
```

反向代理部署还要把每个公开域名写入 `machine.env` 的 `ALLOWED_HOSTS`：

```dotenv
ALLOWED_HOSTS=option.example.com
```

浏览器只保存短期、仅存内存的所有者会话，不保存服务端密钥或旧 `APP_AUTH_TOKEN`。`/health` 和 `/ready` 保持公开，其他页面与接口均要求所有者访问。

`DEPLOY_WARM_WATCHLIST=true` 会在切换容器前，把当前服务最后一份有效自选行情原子保存到共享数据卷；`WATCHLIST_SNAPSHOT_PATH` 必须位于 `/data`。若既读不到当前行情，也没有 24 小时内的有效快照，部署会在流量切换前停止。新容器先用这份快照即时显示页面，并在后台更新；切换后脚本最多等待两分钟确认新行情。行情源暂时不可用时，部署会明确告警并继续保留有时限的旧快照，不会把已经运行的新容器误报成已回滚。

只有在代理已经覆盖并清洗转发头时才设置 `TRUST_PROXY_HEADERS=true`，并同时填写代理直连来源网段；不能填写访客网段或公网全网段：

```dotenv
TRUST_PROXY_HEADERS=true
TRUSTED_PROXY_CIDRS=127.0.0.1/32,10.0.0.0/24
```

`option.openweb-ui.xyz` 一类公网域名必须使用密码模式和有效 HTTPS。部署脚本、应用启动和 `./personal.sh doctor` 共用同一校验；任一边界配置不完整都会停止。

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
| `GET /api/stocks/{ticker}/signals` | 单标的 RSI、MACD、均线与成交量技术状态 |
| `GET /api/signals/market` | 大盘十五项顶部/底部技术分析 |
| `GET /api/signals/stock/{ticker}` | 程序化顶部/底部信号 |
| `GET /api/strength/scan` | 强势股扫描，含价格/成交额门槛与区间持续性影子结果 |
| `GET /api/strength/market` | 六维市场环境与六态大盘形态 |
| `GET /api/breakouts/current` | 最近一次完整突破快照 |
| `GET /api/breakouts/events` | 可筛选、可游标分页的突破事件 |
| `GET /api/breakouts/events/{event_id}` | 突破结构、评分和状态变化证据 |
| `GET /api/breakouts/tickers/{ticker}` | 单只股票的近期突破轨迹 |
| `GET /api/breakouts/status` | 雷达工作进程、数据源和数据库状态 |

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
