# 个人版运行结构

```text
浏览器 -> backend -> 本地 SQLite
                    -> 创建用户明确发起的任务

          worker -> 突破扫描
                 -> Catalyst 原始新闻与日历同步
                 -> 本地焦点快照
                 -> OpenAI Responses 后台任务
                 -> 备份、清理与健康记录

          worker -- HTTPS + Bearer Token --> MacroLens 只读接口
```

Option Pro 常驻两个容器。`backend` 不运行长循环和付费任务；`worker` 持有唯一进程锁，在同一进程中隔离九类任务。某类任务失败时只记录自身状态并退避，不拖垮其他任务。

MacroLens 只提供原始新闻、来源标的和日历。它不能回调 Option Pro，也不能创建 Option Pro 的模型任务。新闻归并、焦点快照、简体中文标题、摘要与分析结果都由 Option Pro 本地保存。

## 配置归属

`config/personal.toml` 管理功能、频率、模型、推理等级、预算和数据保留期。`.env` 只保存密钥、HTTPS 地址和机器网络边界。

MacroLens 连接只使用：

- `MACROLENS_BASE_URL`
- `MACROLENS_INTERNAL_TOKEN`
- 可选的 `MACROLENS_CA_BUNDLE`

连接只允许 HTTPS。旧式签名密钥、请求随机数、前一把密钥和反向拉取入口不再属于现行结构。

`config/personal.toml` 管理功能行为；`machine.env` 管理机器地址与数据路径；`secrets.env` 只保存五项正式服务端密钥。进程已经导出的值优先级最高，否则按 `.env`、`machine.env`、`secrets.env` 的顺序加载。各业务模块接收同一个类型化配置对象，不再自行解释零散环境开关。

MacroLens 连接只使用正式名称 `MACROLENS_URL` 和 `INTERNAL_API_TOKEN`。旧名称只由一个迁移版本的适配器识别；个人版运行链不再保留签名 Key ID、Nonce 或前一把密钥。

应用、`scripts/deploy.sh` 与 `./personal.sh doctor` 共用同一个 Python 部署校验器。直接私网模式绝不信任转发头；任何反向代理或公开域名都必须使用密码模式、明确主机名和收窄的代理来源网段。

## 数据边界

- `/data/optix.db`：突破与强势数据。
- `/data/catalyst-cache.db`：原始新闻修订、本地派生结果和焦点快照。
- `/data/ai-jobs.db`：模型任务、响应身份和用量。
- `/data/optix-worker.db`：统一工作进程锁、心跳和九类任务状态。

数据库仍分开保存，避免一次迁移同时改变数据结构和运行结构。统一工作进程负责日常备份与保留期清理。

## 模型与语言

模型固定为 GPT-5.6 Terra，推理等级为 `max`，后台执行，并发数为一。原始新闻可以保留来源语言，但页面标题、摘要、等待提示和分析内容必须通过简体中文契约。模型结果只有在新闻修订与焦点快照身份仍匹配时才会被采用。
