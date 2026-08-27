# Optix Pro

Optix Pro 是面向个人使用的美股期权、突破信号与新闻分析工作台。正式运行只有两个常驻容器：

- `backend`：提供网页、接口和查询，也接收所有者明确发起的任务。
- `worker`：统一管理突破扫描、新闻同步、焦点快照、模型任务、刷新、备份与清理。

两个容器共用同一镜像和数据卷。模型固定为 GPT-5.6 Terra，推理等级固定为 `max`，最大并发数固定为 1。新闻标题、摘要、等待提示和分析内容必须通过简体中文校验；来源原文只作为内部证据保留。

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

`./personal.sh doctor` 与密钥管理命令会使用一次性后台容器中的锁定依赖，主机不需要另建 Python 虚拟环境。首次执行会构建该容器，后续使用构建缓存。

部署脚本会校验配置、构建当前提交，停止同一编排项目中的旧工作容器，确认旧写入者已经退出，再启动 `backend` 与统一的 `worker`。它不会刷新新闻、运行扫描或创建模型任务，也不能用单纯重启容器代替新版本构建。

日常容器命令统一通过 `./scripts/compose.sh` 执行。这个入口会让 `.env` 与 `machine.env` 同时参与编排插值；直接运行原始 `docker compose` 会停止并提示使用安全入口，避免静默采用错误的监听地址、端口或 MacroLens 配置。

## 配置边界

`config/personal.toml` 管理访问模式、功能行为、任务频率、模型限制、预算、冷却与数据保留期。正式运行参数不能通过环境变量改成其他模型或更高并发。

`machine.env` 只保存七项机器配置：

- `HOST_BIND`
- `PORT`
- `MACROLENS_URL`
- `ALLOWED_HOSTS`
- `TRUST_PROXY_HEADERS`
- `TRUSTED_PROXY_CIDRS`
- `DATA_DIR`

`secrets.env` 只保存八项服务端密钥：

- `OPENAI_API_KEY`
- `FINNHUB_API_KEY`
- `MARKETDATA_TOKEN`
- `MASSIVE_API_KEY`
- `FMP_API_KEY`
- `FRED_API_KEY`
- `INTERNAL_API_TOKEN`
- `APP_PASSWORD_HASH`

进程已经导出的值优先级最高；`.env` 只保留一个版本迁移期的兼容用途，`machine.env` 只接收七个机器字段，`secrets.env` 只接收八个密钥。错放到其他文件的字段不会覆盖正式来源。

`FMP_API_KEY`（Financial Modeling Prep）是可选的第二财报日历来源与批量市值来源：
未配置时财报页完全走 Finnhub 主源，不影响启动与刷新；配置后双日历交叉验证
（日期冲突显式标注，不静默合并），市值批量补全并持久缓存，由 Worker 低频刷新。

旧名称 `MARKETDATA_API_TOKEN`、`MACROLENS_BASE_URL` 和 `MACROLENS_INTERNAL_TOKEN` 只供迁移工具识别。旧名与新名同时存在且值不一致时，迁移会停止，不会猜测采用哪一项。旧签名密钥、请求随机数（Nonce）、密钥编号（Key ID）、前一把密钥和浏览器令牌不会进入最终运行配置。

## 模型分析

默认没有模型密钥，因此不会提交付费任务。需要启用时，在服务器上执行：

```bash
./personal.sh secrets set OPENAI_API_KEY
```

个人版只连接 OpenAI 官方响应接口（Responses API），不接受自定义模型代理。固定参数为：

```toml
[ai]
model = "gpt-5.6-terra"
reasoning = "max"
max_concurrency = 1
execution_mode = "background"
```

供应商提交失败后的自动重试固定为零，不通过环境变量更改。

模型任务保留严格结构化输出、每日任务次数、每日美元预算与冷却限制。相同输入会先复用原任务，即使队列已满也不会重复计费；只有新任务在队列饱和时返回 429 和 `Retry-After: 60`。

每日美元预算是提交前的本地保守预留，不是供应商的实时账单，最终费用仍以供应商后台为准。

密码模式下，未登录访客只能浏览公开研究数据和已有分析，不能创建、重试或取消模型任务。所有者登录后可在页头手动关闭或开启分析：关闭会同时停止新的手动分析和定时分析；重新开启只恢复手动分析，不会自动创建任务。

## 访问安全

访问模式只在 `config/personal.toml` 中设置：

```toml
[access]
mode = "private_network"
```

`private_network` 只用于本机、安全外壳（SSH）转发、可信虚拟专用网络（VPN）或直接私网连接。该模式必须保持 `TRUST_PROXY_HEADERS=false`，不能放在 Nginx、Caddy、Cloudflare Tunnel 或公网负载均衡器后；监听地址和允许主机也只能使用本机或许可私网地址。

任何反向代理、公开域名或公网入口都必须使用 `password` 模式，并满足以下条件：

- `secrets.env` 中存在有效的 `APP_PASSWORD_HASH`；
- `machine.env` 明确列出允许访问的域名；
- 外层代理提供有效的超文本传输安全协议（HTTPS）；
- 允许主机中只要含有域名，就必须设置 `TRUST_PROXY_HEADERS=true`；
- `TRUSTED_PROXY_CIDRS` 只包含实际代理来源网段，不得使用公网全网段。

例如 `option.openweb-ui.xyz` 一类公开域名必须使用密码模式和 HTTPS。应用启动、`./personal.sh doctor` 与部署脚本共用同一校验器，配置不完整时都会停止。

密码模式只有一个所有者（Owner）密码，不提供用户、角色、注册或找回密码。密码会话使用有时效的 `HttpOnly`、`Secure`、`SameSite=Strict` Cookie。未登录访客可读取研究页面、静态文件和公开研究接口，但不能刷新数据、扫描、修改设置、维护数据或操作模型任务；这些动作仍要求所有者会话和同源 JSON 请求。`/health` 与 `/ready` 继续无需登录。

### 个人自选

自选列表按主体保存在 `accounts.db`：注册客户各持一份，所有者也持一份。所有者通过
`APP_PASSWORD_HASH` 登录、不持账号 Cookie，因此 `/api/account/watchlist` 同时受理这两种
主体——只认账号 Cookie 会让个人部署里唯一的真实用户成为唯一无法保存自选的人。同一浏览器
里两种会话并存时，账号 Cookie 优先，保证正在编辑的就是界面上看得见的那一份；两份列表互不
可见、互不影响。所有者那一行使用保留用户名 `admin` 与非 `usr_` 前缀的固定标识，注册接口永远
拿不到它。

个人自选为空时展示站点默认关注池，并由横幅明说这是默认池——空白页会让「还没添加过」和
「加载失败」长得一样，而不加标注地展示默认池等于把系统的池子冒充成用户自己的选择。行情只
覆盖默认关注池内的代码，超出范围的自选代码如实标注「暂无行情」，可在个股页手动获取。

访客接口只读取仍在允许保留时限内的进程缓存，或工作进程、轻量数据库（SQLite）和本地文件已经保存的快照。没有可用快照时返回 503，不会临时访问外部行情源、启动后台刷新、修改应用数据或保存刷新结果。新闻公开响应只说明“登录后可使用模型分析”，不会查询或公开所有者的预算、令牌用量和活动任务；所有者登录后才会读取这些运行信息。

有两个访客可发起面默认关闭，可在 `config/personal.toml` 的 `[access]` 中显式打开：
`visitor_live_pulls = true` 允许访客发起有限的实时拉取（个股手动拉取、板块 IV 冷启动扫描、
经济日历实际值补全；均保留每 IP 限流与失败冷却）；`visitor_ai_actions = true` 允许访客提交
财报影响分析（消耗模型预算；保留每 IP 每 10 分钟 3 次限流与同任务去重）。开关关闭时，这些
动作与其他写操作一样要求所有者会话，注册的朋友账号同样不例外。

## 密钥管理

```bash
./personal.sh secrets status
./personal.sh secrets set OPENAI_API_KEY
./personal.sh secrets set FINNHUB_API_KEY
./personal.sh secrets set MARKETDATA_TOKEN
./personal.sh secrets set MASSIVE_API_KEY
./personal.sh secrets set FMP_API_KEY
./personal.sh secrets set FRED_API_KEY
./personal.sh secrets set INTERNAL_API_TOKEN
./personal.sh secrets set APP_PASSWORD_HASH
./personal.sh secrets remove OPENAI_API_KEY
./personal.sh secrets validate
```

`status` 只显示是否配置，不回显值。`validate` 只做格式、安全权限和免计费连通性检查，不创建模型任务。密钥写入使用锁、私有临时文件和原子替换，避免并发修改相互覆盖。

部署与个人管理命令共用同一个主机操作锁，不能并行执行。密钥变化只会重建原本正在运行的受影响服务，并保留其正式提交镜像；脚本会等待服务恢复健康。无根模式（Rootless Mode）可用，开启用户命名空间重映射（User Namespace Remapping）的 Docker 主机不支持这些密钥管理命令。

## 健康检查

```bash
curl --fail http://127.0.0.1:2000/ready
./scripts/compose.sh exec -T worker python -m app.worker --healthcheck
```

统一工作进程应且只应报告十三项任务：

- `breakout`
- `catalyst_sync`
- `focus`
- `ai_jobs`
- `maintenance`
- `stock_directory`
- `public_home`
- `earnings_analysis`
- `macro_conditions`
- `focus_refresh`
- `strength_refresh`
- `breakout_refresh`
- `retention`

健康检查只读取本地进程锁、心跳和任务状态，不会运行扫描、请求真实新闻源或创建模型任务，也不会消耗付费额度。

## 数据与迁移

所有运行数据保存在 `optix-data` 命名卷，并从统一的 `DATA_DIR` 派生：

- `optix.db`
- `catalyst-cache.db`
- `macro-conditions.db`
- `public-home-snapshot-v1.json`
- `ai-jobs.db`
- `optix-worker.db`
- `runtime-settings.json`
- `watchlist-snapshot-v1.json`
- `backups/`

升级和回滚时不要附加 `--volumes` 或 `-v`。迁移工具会生成 `personal.toml`、`machine.env`、`secrets.env` 和不含任何值的 `migration-report.json`。详细边界见[个人版迁移说明](docs/personal-edition/migration.md)。

## 期权异动范围

`GET /api/options/unusual` 不是全市场实时扫描。它只扫描 `NVDA`、`TSLA`、`AAPL`、`AMD`、`AMZN`、`META`、`MSFT`、`SPY`、`QQQ`、`GOOGL`，每个标的检查 Yahoo 返回的前两个到期日，结果缓存 120 秒并最多返回 50 条。

## Optix 宏观环境

`/market` 页第 B4 区块（六维形态之后、信号解读之前）展示 **Optix 宏观环境**：
用 FRED、纽约联储、联储理事会、芝加哥联储、Cboe 的 24 个公开时间序列，加上 8 个走现有
股票日线链的跨资产 ETF 代理，计算 30 个因子、7 个模块和一个等权综合分。

**分数是过去 5 年的滚动历史分位，不是预测概率。** 高分表示当前金融环境相对自身历史
更支持风险资产，不代表市场一定上涨，也不构成买入、卖出、仓位或目标价建议。
历史区间按当前修订值回算，不是当时市场已知的分数；本地部署后每次实际抓取形成的快照
才具备真实的点时语义。缺失的因子不会按中性 50 计入，而是移出权重重新归一。
v1 只用于展示与研究，**不写入任何正式股票评分**。

启用：

```bash
./personal.sh secrets set FRED_API_KEY   # 未配置时功能显示「未启用」，Worker 仍然健康
```

- 数据库：`macro-conditions.db`（独立于 `optix.db`，已进入自动备份与 Retention 前备份）
- 工作进程任务：`macro_conditions`（定时与手动共用同一个任务）
- 默认刷新时刻：每日 America/New_York `08:30` 与 `18:30`，手动刷新冷却 300 秒
- 只读接口对匿名访客与 Customer Account 开放；手动刷新仅 Owner
- 配置：`config/personal.toml` 的 `[macro]` 段

详见 [docs/macro-conditions/](docs/macro-conditions/)：
[架构](docs/macro-conditions/architecture.md) ·
[数据源](docs/macro-conditions/data-sources.md) ·
[30 个因子](docs/macro-conditions/factors.md) ·
[评分](docs/macro-conditions/scoring.md) ·
[点时语义](docs/macro-conditions/point-in-time.md) ·
[运维](docs/macro-conditions/operations.md)

个股图的手动画线、账户同步和自动技术形态见 [docs/chart-drawings.md](docs/chart-drawings.md)。

## 本地验证

```bash
bash -n setup.sh personal.sh scripts/compose.sh scripts/deploy.sh scripts/lock-dependencies.sh
./scripts/compose.sh config -q
PYTHONPATH=backend python -m pytest -q
node --test frontend/tests/*.test.mjs
node frontend/tests/static_assertions.mjs
npm --prefix frontend run test:visual
```

持续集成（CI）只使用本地夹具和模拟连接，不访问真实 OpenAI、新闻源、行情源或生产数据库。检查通过只说明该提交通过测试与容器验证，不代表生产服务器已经更新。

## 日常管理

```bash
./scripts/compose.sh ps
./scripts/compose.sh logs -f backend worker
./scripts/compose.sh restart backend worker
./scripts/compose.sh down
```

工作进程的停止宽限期为 2100 秒，避免把正在保存响应身份的模型任务留在未知状态。

## 许可证

[MIT](LICENSE)
