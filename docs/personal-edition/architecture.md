# 个人版运行结构

## 现行结构

```text
浏览器
  │
  ├─ private_network：来源地址属于允许私网，直接成为所有者
  └─ password：登录后取得服务端所有者会话
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
                                ├─ 备份、清理和健康记录
                                └─ 焦点、强度、突破与保留期手动任务
                                  │
                                  └─ HTTPS + INTERNAL_API_TOKEN
                                      └─> MacroLens 只读新闻 ETL
```

Option Pro 常驻两个容器。`backend` 不运行长循环或付费任务；`worker` 持有唯一进程锁，在同一进程内隔离九类任务。其中五类负责定时与后台循环，四类分别消费 `focus_refresh`、`strength_refresh`、`breakout_refresh` 和 `retention` 手动请求。某类任务失败时只更新自身状态并退避，不拖垮其他任务。

MacroLens 只提供原始新闻、来源标的和日历。它不能创建 Option Pro 的模型任务，也不能回调 Option Pro。新闻归并、简体中文标题与摘要、分析修订和热点周期全部保存在 Option Pro。

## 所有者边界

`config/personal.toml` 的 `[access].mode` 只允许两种值：

- `private_network`：无需登录，但部署和应用启动都会拒绝通配、公网或未批准的监听地址。
- `password`：要求 `APP_PASSWORD_HASH`，只实现一个所有者（Owner）会话。

`/health` 与 `/ready` 保持开放。其他页面和接口使用同一访问边界，不再拆成公开读取、浏览器令牌和动作密钥三条路径。

密码会话使用有时效的随机服务端会话、`HttpOnly`、`Secure` 和 `SameSite=Strict` Cookie。修改数据和创建付费任务仍要求同源 JSON 请求；单用户并不意味着删除跨站操作保护。

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

`secrets.env` 只保存五项服务端密钥：

- `OPENAI_API_KEY`
- `FINNHUB_API_KEY`
- `MARKETDATA_TOKEN`
- `INTERNAL_API_TOKEN`
- `APP_PASSWORD_HASH`

`.env` 只保留一个迁移版本的兼容用途。进程已经导出的值优先级最高，否则按 `.env`、`machine.env`、`secrets.env` 的顺序加载。各业务模块接收同一个类型化配置对象，不再自行解释零散环境开关。

MacroLens 连接只使用正式名称 `MACROLENS_URL` 和 `INTERNAL_API_TOKEN`。旧名称只由迁移工具识别；个人版运行链不再保留签名密钥、密钥编号（Key ID）、请求随机数（Nonce）或前一把密钥。

网页、健康接口、日志和错误响应只能显示密钥是否已配置，不能读取、裁剪后展示或回显任何密钥值。

应用、`scripts/deploy.sh` 与 `./personal.sh doctor` 共用同一个 Python 部署校验器。直接私网模式绝不信任转发头；任何反向代理或公开域名都必须使用密码模式、明确主机名和收窄的代理来源网段。

## 数据边界

所有持久文件从一个 `DATA_DIR` 派生，不再为每个数据库设置独立路径：

- `optix.db`：突破与强势数据；
- `catalyst-cache.db`：新闻修订、本地派生结果和焦点快照；
- `ai-jobs.db`：模型任务、响应身份、令牌用量和预算预留；
- `optix-worker.db`：进程锁、心跳和九类任务状态；
- `runtime-settings.json`：非敏感运行设置；
- `watchlist-snapshot-v1.json`：自选快照；
- `backups/`：版本化备份。

数据库继续分开保存，避免运行结构收束与数据结构迁移相互放大风险。

## 模型、语言与费用

模型固定为 GPT-5.6 Terra，推理等级为 `max`，后台并发为一。新闻来源可以保留原文，但页面标题、摘要、等待提示和分析内容必须使用简体中文。

每日美元预算是调用前的本地硬上限。系统按每类任务的最大输入、最大输出和工具上限预留最坏费用，取得供应商终态用量后保守回填；用量缺失或提交结果不明时不释放预留。它与每日任务数、并发、幂等、冷却和未知提交保护共同决定是否接受新任务，最终对账仍以供应商账单为准。
