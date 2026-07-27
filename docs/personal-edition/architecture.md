# 个人版运行结构

## 现行结构

```text
浏览器
  │
  ├─ private_network：来源地址属于允许私网，直接成为所有者
  └─ password
      ├─ 未登录：只读公开研究页面、数据和已有分析
      └─ 登录后：取得服务端所有者会话，可执行分析与维护动作
  │
  ▼
backend ── 同源 JSON 动作 ──> 本地任务队列
  │                              │
  └─ 页面、查询、任务状态         ▼
                              worker
                                ├─ 突破与强度扫描
                                ├─ 新闻、日历和来源状态同步
                                ├─ 焦点快照
                                ├─ OpenAI 后台任务
                                ├─ 公共首页快照
                                ├─ 备份、清理和健康记录
                                └─ 焦点、强度、突破与保留期手动任务
                                  │
                                  └─ HTTPS + INTERNAL_API_TOKEN
                                      └─> MacroLens 只读新闻 ETL
```

Option Pro 常驻两个容器。`backend` 不运行长循环或付费任务；`worker` 持有唯一进程锁，在同一进程内隔离十三类任务。私有网络模式运行八类定时与后台循环（含 `macro_conditions`）；密码模式再启用`public_home`，共九类。其余四类分别消费 `focus_refresh`、`strength_refresh`、`breakout_refresh` 和 `retention` 手动请求。某类任务失败时只更新自身状态并退避，不拖垮其他任务。

MacroLens 只提供原始新闻、来源标的和日历。它不能创建 Option Pro 的模型任务，也不能回调 Option Pro。新闻归并、简体中文标题与摘要、分析修订和热点周期全部保存在 Option Pro。

## 所有者边界

`config/personal.toml` 的 `[access].mode` 只允许两种值：

- `private_network`：无需登录，但部署和应用启动都会拒绝通配、公网或未批准的监听地址。
- `password`：要求 `APP_PASSWORD_HASH`，只实现一个所有者（Owner）会话。

`/health` 与 `/ready` 保持开放。密码模式同时提供公开只读研究面和所有者动作面：公开面只允许读取页面、静态文件、研究数据与已有分析；刷新、扫描、设置、维护，以及模型任务的创建、重试和取消仍要求所有者会话。系统不再使用浏览器令牌或独立动作密钥。

公开研究接口采用明确的路由清单，不按整段路径放行。访客只读取仍在允许时限内的进程缓存或已经保存的工作进程、轻量数据库（SQLite）和文件快照；首页所需的指数、默认焦点股、图表、信号、财报和异常期权由 `public_home` 定时保存。没有可用快照时返回 503，不会补做外部请求、后台刷新、修改应用数据或保存刷新结果。公开新闻响应也不会初始化模型任务库、查询预算和令牌用量，或公开活动任务信息。

例外面由 `[access]` 的两个开关控制且默认关闭：`visitor_live_pulls`（个股手动拉取、板块 IV
冷启动扫描、经济日历实际值补全）与 `visitor_ai_actions`（财报影响分析提交）。开关打开时仍
保留每 IP 限流、失败冷却与同源校验；关闭时这些动作要求所有者会话，朋友账号亦不例外。

密码会话使用有时效的随机服务端会话、`HttpOnly`、`Secure` 和 `SameSite=Strict` Cookie。修改数据和创建付费任务仍要求同源 JSON 请求；单用户并不意味着删除跨站操作保护。

所有者可在页头手动关闭或开启模型分析。关闭会同时禁止新的手动与定时分析；重新开启只恢复手动分析，定时分析仍需在运行设置中单独启用。

## 配置归属

`config/personal.toml` 保存访问模式、功能、频率、模型、推理等级、预算、冷却和保留期。

`machine.env` 只保存七项机器配置：

- `HOST_BIND`
- `PORT`
- `MACROLENS_URL`
- `ALLOWED_HOSTS`
- `TRUST_PROXY_HEADERS`
- `TRUSTED_PROXY_CIDRS`
- `DATA_DIR`

`secrets.env` 只保存七项服务端密钥：

- `OPENAI_API_KEY`
- `FINNHUB_API_KEY`
- `MARKETDATA_TOKEN`
- `MASSIVE_API_KEY`
- `FRED_API_KEY`
- `INTERNAL_API_TOKEN`
- `APP_PASSWORD_HASH`

`.env` 只保留一个迁移版本的兼容用途。进程已经导出的值优先级最高；`machine.env` 只提供七个机器字段，`secrets.env` 只提供七个密钥，错放字段不能覆盖正式来源。各业务模块接收同一个类型化配置对象，不再自行解释零散环境开关。

MacroLens 连接只使用正式名称 `MACROLENS_URL` 和 `INTERNAL_API_TOKEN`。旧名称只由迁移工具识别；个人版运行链不再保留签名密钥、密钥编号（Key ID）、请求随机数（Nonce）或前一把密钥。

网页、健康接口、日志和错误响应只能显示密钥是否已配置，不能读取、裁剪后展示或回显任何密钥值。

应用、`scripts/deploy.sh` 与 `./personal.sh doctor` 共用同一个 Python 部署校验器。直接私网模式绝不信任转发头；任何反向代理或公开域名都必须使用密码模式、明确主机名、`TRUST_PROXY_HEADERS=true` 和收窄的代理来源网段。

## 数据边界

所有持久文件从一个 `DATA_DIR` 派生，不再为每个数据库设置独立路径：

- `optix.db`：突破与强势数据；
- `catalyst-cache.db`：新闻修订、本地派生结果和焦点快照；
- `ai-jobs.db`：模型任务、响应身份、令牌用量和预算预留；
- `optix-worker.db`：进程锁、心跳和十类任务状态；
- `runtime-settings.json`：非敏感运行设置；
- `watchlist-snapshot-v1.json`：自选快照；
- `public-home-snapshot-v1.json`：匿名首页六类数据快照；
- `backups/`：版本化备份。

数据库继续分开保存，避免运行结构收束与数据结构迁移相互放大风险。

## 模型、语言与费用

模型固定为 GPT-5.6 Terra，推理等级为 `max`，后台并发为一。新闻来源可以保留原文，但页面标题、摘要、等待提示和分析内容必须使用简体中文。

每日美元预算是调用前的本地硬上限。系统按每类任务的最大输入、最大输出和工具上限预留最坏费用，取得供应商终态用量后保守回填；用量缺失或提交结果不明时不释放预留。它与每日任务数、并发、幂等、冷却和未知提交保护共同决定是否接受新任务，最终对账仍以供应商账单为准。
