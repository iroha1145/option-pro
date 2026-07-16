# 已删除组件

## PR 1：旧版前端

| 删除范围 | 原用途 | 替代路径 | 引用证据 | 回滚提交 |
|---|---|---|---|---|
| `frontend/static/css/styles.css`、八个 `optix-*-v3.css` 与 `optix-nightday-v4.css` | 旧版页面及第三、四版样式 | `optix-deck.css`、`optix-catalysts.css` | `frontend/index.html` 在删除前只加载夜间工作台样式；删除后全仓引用扫描为零 | `d04ef67703316c52279fb020e10278eb7e3e82f5` |
| `frontend/static/js/app.js`、`api.js`、`pages/`、`components/`、`utils/` | 旧路由、旧页面及旧组件树 | `deck-app.js`、`deck-api.js`、`deck-ai-jobs.js`、`deck-catalysts.js` | 首页无旧脚本标签；后端完整性清单和静态断言已改为现行九个文件；删除后全仓引用扫描为零 | `d04ef67703316c52279fb020e10278eb7e3e82f5` |
| `frontend/static/js/icons.js`、`theme-toggle.js`、`frontend/static/icons.svg` | 旧图标和主题切换入口 | 夜间工作台内建图标和 `theme-init.js` | 首页无引用；删除后全仓引用扫描为零 | `d04ef67703316c52279fb020e10278eb7e3e82f5` |
| 九个仅覆盖旧页面的 Node.js 测试 | 验证已下线页面状态 | `catalyst-deck.test.mjs` 与重写后的 `static_assertions.mjs` | 被测模块已删除；现行 Node.js 测试 32 项通过 | `d04ef67703316c52279fb020e10278eb7e3e82f5` |

删除前逐项核对了首页脚本、静态导入、后端挂载、容器复制、文档与测试引用。删除后再次扫描旧路径，没有发现生产引用。恢复时应整体回到上述提交，不应单独复制旧资源回现行镜像。

## 第五阶段：部署与配置收束

| 删除范围 | 原用途 | 替代路径 | 数据处理 |
|---|---|---|---|
| 四个独立工作容器与逐进程健康门禁 | 分别运行模型、新闻同步、焦点和突破任务 | 单一`worker`服务及九类任务清单 | 原数据库继续挂载，不改写历史行 |
| `docker-compose.personal.yml` | 个人版试运行入口 | 正式`docker-compose.yml` | 继续使用同名`optix-data`卷 |
| 多组签名、随机数和反向焦点凭据 | 双向服务认证 | 超文本传输安全协议（HTTPS）与`INTERNAL_API_TOKEN` | 旧审计数据至少只读保留 90 天 |
| 环境文件中的行为开关 | 分散控制模型、频率和功能 | `config/personal.toml` | 转换报告只用于人工核对 |

## 第六阶段：Owner 访问与运行配置收尾

| 已删除项 | 原问题 | 现行替代 |
|---|---|---|
| `APP_AUTH_TOKEN`、`PUBLIC_READ_API_ENABLED`、`ALLOW_INSECURE_PUBLIC_BIND` | 页面读取、浏览器令牌和动作权限分属不同路径，状态组合过多 | `config/personal.toml` 中的`private_network`或`password`，共用一条 Owner 边界 |
| 前端`sessionStorage`令牌和`Authorization`请求头 | 密钥需进入浏览器 | 密码模式的服务端会话 Cookie；私有网络模式不传递浏览器密钥 |
| `MACROLENS_BASE_URL`、`MACROLENS_INTERNAL_TOKEN` 及旧式读取、动作、焦点凭据 | MacroLens 连接名称与方向不统一 | `machine.env` 中的`MACROLENS_URL`与`secrets.env`中的`INTERNAL_API_TOKEN`，仅限 Option Pro 读取 MacroLens |
| `OPENAI_JOB_DB_PATH`、`MACROLENS_CACHE_DB_PATH`、`BREAKOUT_DB_PATH`、`WATCHLIST_SNAPSHOT_PATH` | 同一数据卷内的文件路径可分别漂移 | 只配置`DATA_DIR`，数据库、锁、快照与备份均由程序派生 |
| `ai-worker`等旧工作服务的独立部署、检查和停止逻辑 | 发布脚本需同时理解多套工作进程 | `backend`+`worker`的两服务发布，统一核对九类任务 |
| 旧变量的运行时兼容读取层 | 迁移后仍可能被旧配置影响 | 运行时只读新配置；旧名称只留在迁移说明与回归断言中 |

本阶段不删除业务数据。数据卷、数据库文件和历史任务保留原位；删除的是运行时入口、变量别名与重复权限链。
