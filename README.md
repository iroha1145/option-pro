# Optix Pro

Optix Pro 是面向个人使用的美股期权、突破信号与新闻分析工作台。正式运行只有两个常驻容器：

- `backend`：提供网页、接口和查询，也接收所有者明确发起的任务。
- `worker`：统一管理突破扫描、新闻同步、焦点快照、模型任务、刷新、备份与清理。

两个容器共用同一镜像和数据卷。新分析默认使用 Claude Haiku 5.5，推理强度为 `xhigh`，Claude 默认总并发数为 4，可设置为 1 到 4。新闻标题、摘要、等待提示和分析内容必须通过简体中文校验；来源原文只作为内部证据保留。

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

`secrets.env` 只保存九项服务端密钥：

- `ANTHROPIC_API_KEY`
- `OPENAI_API_KEY`（旧任务取回与回滚兼容）
- `FINNHUB_API_KEY`
- `MARKETDATA_TOKEN`
- `MASSIVE_API_KEY`
- `FMP_API_KEY`
- `FRED_API_KEY`
- `INTERNAL_API_TOKEN`
- `APP_PASSWORD_HASH`

进程已经导出的值优先级最高；`.env` 只保留一个版本迁移期的兼容用途，`machine.env` 只接收七个机器字段，`secrets.env` 只接收九个密钥。错放到其他文件的字段不会覆盖正式来源。

`FMP_API_KEY`（Financial Modeling Prep）是可选的第二财报日历来源与批量市值来源：
未配置时财报页完全走 Finnhub 主源，不影响启动与刷新；配置后双日历交叉验证
（日期冲突显式标注，不静默合并），市值批量补全并持久缓存，由 Worker 低频刷新。

旧名称 `MARKETDATA_API_TOKEN`、`MACROLENS_BASE_URL` 和 `MACROLENS_INTERNAL_TOKEN` 只供迁移工具识别。旧名与新名同时存在且值不一致时，迁移会停止，不会猜测采用哪一项。旧签名密钥、请求随机数（Nonce）、密钥编号（Key ID）、前一把密钥和浏览器令牌不会进入最终运行配置。

## 实时行情

可选的实时行情通道支持顶部指数基金、突破雷达与当前页面股票，共享最多 50 个 Finnhub 订阅。报价逐位滚动，雷达可逐笔触发，正式确认仍使用完整 5 分钟行情。功能与公开展示均默认关闭，启用、授权边界及接口说明见[实时行情文档](docs/realtime-quotes.md)。

## 模型分析

默认没有模型密钥，因此不会提交付费任务。需要启用时，在服务器上执行：

```bash
./personal.sh secrets set ANTHROPIC_API_KEY
```

新分析只连接 Anthropic 官方消息接口（Messages API），不接受自定义地址或中转代理。默认参数为：

```toml
[ai]
model = "claude-haiku-5-5"
reasoning = "xhigh"
max_concurrency = 4
execution_mode = "background"
```

`background` 表示任务在应用后台排队，工作进程（Worker）通过流式调用取得 Claude 结果；它不表示 Anthropic 提供了可取回的后台响应。分析采用自适应思考（Adaptive Thinking）、`xhigh` 推理强度、严格结构化输出（Strict Structured Outputs），并为重复使用的系统提示设置 5 分钟提示缓存（Prompt Caching）。缓存是否命中以供应商返回的实际用量为准。

`OPENAI_API_KEY` 只用于迁移前已提交任务的取回、取消和回滚，须保留到旧任务处理完毕。旧结果继续记录原来的模型与推理强度，不会改标为 Claude。供应商提交失败后的自动重试固定为零；提交结果不明时停止重复提交，避免再次收费。

模型任务保留共享日预算与冷却限制。Claude 手动与定时任务共用 4 个并发名额，可通过 `max_concurrency` 设置为 1 到 4。旧 OpenAI 任务保留手动与定时各 1 个的恢复规则。相同输入会先复用原任务，即使队列已满也不会重复计费；只有新任务在队列饱和时返回 429 和 `Retry-After: 60`。

美国站的 Claude Haiku 5.5 与 Claude Opus 5.5 共用每日 9.5 美元的应用预算，配置在新的命名空间：

```toml
[model_budget]
daily_budget_usd = 9.5
# 从首次生效时刻起算时，accounting_start_at 填入实际 UTC 时间。
```

预算按世界协调时（UTC）日界划分，每天东京 09:00 重置。每次模型请求发送前先预留估算费用；Opus 续跑的每一轮也单独申请预留。管理员看到的“估算及预留”包含正在运行和结果未知的占用，剩余额度不足时不会开始下一轮。这是应用准入预算，不保证供应商账单硬性封顶；实际报告的费用完整对账，不截断为预算值。

本次切换按 `accounting_start_at` 从实际生效时刻重新起算，此前当天的已报费用和 6 条未知预留不占新 9.5 美元。旧记录保留，未知任务仍不得重复付款；起算后的新未知请求继续占用新窗口。省略 `accounting_start_at` 时沿用完整 UTC 日窗口。预算只合计两种 Claude 模型，旧 Terra 历史不计入。

`[model_budget].daily_budget_usd` 默认 0，供旧部署继续沿用原来的词元准入；启用正值共享美元预算后，`[ai].daily_token_limit` 只统计用量，不再以 1000 万词元阻断 Claude 分析。不同模型的词元计数可能不同，思考输出也计费；原生工具的内部多轮输入可累计超过单轮上下文容量。缓存和工具用量仍分别保存，最终费用以供应商账单为准。

市场综合研判的 `daily_max_runs = 6` 继续作为独立运行次数限制。预算、预留和证据条件可能使计划中的开盘前或收盘后研判无法生成，不能保证每天两份。配置与排障见[市场综合研判说明](docs/market-brief.md)。

网页搜索（Web Search）和网页抓取（Web Fetch）各设 `max_uses=1`，抓取内容预算约 8000 词元；代码执行（Code Execution）在本地流处理中最多接受 2 次，达到限制停止本次处理。单次请求暂停或中断时不自动续跑。最终结果始终使用 JSON 输出格式（JSON Output Format）；启用工具时，仅提供原生网页搜索、网页抓取和代码执行工具。原生工具与 JSON 格式的组合已通过实际请求验证，结果仍须通过中文与业务规则校验。已完成分析最多提供 10 个“核对来源”链接，不展示思考正文或中间工具内容。

旧 `[ai].daily_max_jobs` 和 `[ai].daily_budget_usd` 只保留历史读取与回滚兼容，不承担新的共享预算。不要把 9.5 写入旧字段或运行设置的 `ai` 段。运行设置接口拒绝这两个字段的新非零写入，返回 422 和 `retired_budget_setting`；可写入零清除旧值。

密码模式下，未登录访客只能浏览公开研究数据和已有分析，不能创建、重试或取消模型任务。所有者登录后可在页头手动关闭或开启分析：关闭会同时停止新的手动分析和定时分析；重新开启只恢复手动分析，不会自动创建任务。

迁移步骤、费用口径和验收要求见[Claude 迁移说明](docs/personal-edition/claude-migration.md)。配置与本地检查不代表真实调用验证或生产部署已经完成。

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

密码模式只有一个所有者（Owner）密码；朋友可注册独立账号保存个人自选和图表绘图，账号会话不能获得所有者权限。密码会话使用有时效的 `HttpOnly`、`Secure`、`SameSite=Strict` Cookie。未登录访客可读取研究页面、静态文件和公开研究接口，但不能刷新数据、扫描、修改设置、维护数据或操作模型任务；这些动作仍要求所有者会话和同源 JSON 请求，另有明确开放的例外见下文。`/health` 与 `/ready` 继续无需登录。

所有接口请求体在解析 JSON 前按实际接收字节数检查：注册、登录最多 4 KiB，其余接口最多 2 MiB，部分路由另有更严上限。分块传输或缺少长度声明也受相同限制，超限返回 413；图表绘图的 500 项批量保存仍受业务数量与单项大小限制。

每个朋友账号最多保留 20 个有效登录会话，继续登录会撤销最旧会话；其他账号的会话不受影响。

新注册账号的密码须为 15 至 256 个字符，支持中文、空格与长短语，不要求大小写、数字和符号组合。服务端会拒绝少量完整命中本地常见口令列表的密码及全空白密码；这不是全量泄露密码库检查。原有账号的密码验证保持兼容，不会因这项注册规则无法登录。

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
`visitor_live_pulls = true` 允许访客发起有限的实时拉取（个股手动拉取、期权到期日和期权链冷查询、板块 IV 冷启动扫描、
经济日历实际值补全；均保留每 IP 限流与失败冷却）；`visitor_ai_actions = true` 允许访客提交
财报影响分析（消耗模型预算；保留每 IP 每 10 分钟 3 次限流与同任务去重）。开关关闭时，这些
动作与其他写操作一样要求所有者会话；注册的朋友账号仅可额外发起受账号限额约束的个股手动拉取。期权到期日和期权链接口在开关关闭时只向访客提供未过期缓存，没有缓存返回 503，不会临时访问 Yahoo。

## 密钥管理

```bash
./personal.sh secrets status
./personal.sh secrets set ANTHROPIC_API_KEY
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

统一工作进程应且只应报告十五项任务：

- `breakout`
- `catalyst_sync`
- `focus`
- `ai_jobs`
- `maintenance`
- `stock_directory`
- `public_home`
- `sector_iv_refresh`
- `earnings_analysis`
- `macro_conditions`
- `market_brief`
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
- `market-brief/`
- `public-home-snapshot-v1.json`
- `ai-jobs.db`
- `optix-worker.db`
- `runtime-settings.json`
- `watchlist-snapshot-v1.json`
- `stock-chart-snapshots-v1/`
- `backups/`

非日线图表快照的目录上限、各周期 `max_age` 以及与日线手动拉取分桶的说明见 [stock-chart-snapshots.md](docs/stock-chart-snapshots.md)。

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

## 首页市场综合研判

首页每个交易日生成两份市场综合研判（开盘前、收盘后），由 Claude Opus 5.5 解释程序算好的指数、广度、板块、宏观与新闻证据。它不给涨跌概率，也不构成买卖、仓位或目标价建议。

```bash
./personal.sh secrets set ANTHROPIC_API_KEY   # 未配置时任务报 disabled，Worker 仍然健康
```

- 工作进程任务：`market_brief`（定时两个槽与 Owner 手动补发共用同一个任务）
- 只读接口 `/api/market-brief/latest`、`/api/market-brief/history` 对访客开放；状态与手动补发仅 Owner
- 配置：`config/personal.toml` 的 `[market_brief]` 段

槽位、手动补发、费用口径与排障见 [docs/market-brief.md](docs/market-brief.md)。

## 本地验证

```bash
bash -n setup.sh personal.sh scripts/compose.sh scripts/deploy.sh scripts/lock-dependencies.sh
./scripts/compose.sh config -q
PYTHONPATH=backend python -m pytest -q
node --experimental-strip-types --test frontend-src/tests/*.test.mjs
node frontend-src/tests/static_assertions.mjs
npm --prefix frontend-src run lint
npm --prefix frontend-src run test:review
npm --prefix frontend-src run test:quotes
```

界面源码与浏览器用例都在 `frontend-src/`，`frontend/` 只保存构建产物。`test:review` 与 `test:quotes` 自带本地开发服务器与模拟接口；`npm --prefix frontend-src run test:visual` 另外需要 `OPTIX_VISUAL_BASE_URL` 指向一个已经在运行的部署。

持续集成（CI）只使用本地夹具和模拟连接，不访问真实 Anthropic、OpenAI、新闻源、行情源或生产数据库。检查通过只说明该提交通过测试与容器验证，不代表生产服务器已经更新。

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
