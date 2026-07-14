# 远程部署边界

## 拓扑

MacroLens 使用 https://<macrolens-domain>，Option Pro 使用 https://<option-pro-domain>。Option Pro 的 MACROLENS_BASE_URL 只允许固定 Origin，不接受用户输入。

禁止 localhost、Docker 服务名、共享网络、共享数据库挂载、Git Submodule 和跨仓库 depends_on。localhost HTTP 仅供显式开启的本地测试。

## 传输要求

- 生产证书和主机名必须验证。
- MACROLENS_VERIFY_TLS=true 为生产固定值。
- 私有证书颁发机构通过 MACROLENS_CA_BUNDLE 指定绝对路径。
- 客户端禁止重定向、代理环境继承和带凭据的 URL。
- 反向代理只开放 /api/integrations/option-pro/v1。
- 边界代理关闭该路径的访问日志，或使用明确去除查询串和五个 `X-Optix-*` 签名请求头的安全日志格式。
- 边界代理将该路径直接送到 127.0.0.1:8000；普通网页送到 127.0.0.1:3000。前端容器不代转 Integration 路径。
- 3000 和 8000 都只绑定回环地址，不允许公网明文旁路。
- OPTION_PRO_TRUSTED_PROXY_CIDRS 只包含边界代理地址，OPTION_PRO_ALLOWED_CIDRS 只包含 Option Pro 固定出口地址。
- 浏览器不访问 MacroLens，因此不增加跨域资源共享和外部 connect-src。
- 防火墙应只允许 Option Pro 服务器出口地址访问 Integration 路径。
- 两台服务器使用网络时间协议同步；超过 300 秒签名时间窗时停止请求并告警。

## Option Pro 远程配置

```dotenv
MACROLENS_ENABLED=false
MACROLENS_BASE_URL=https://macrolens.example.com
MACROLENS_VERIFY_TLS=true
MACROLENS_CA_BUNDLE=
MACROLENS_CONNECT_TIMEOUT_SECONDS=3
MACROLENS_READ_TIMEOUT_SECONDS=12
MACROLENS_TOTAL_TIMEOUT_SECONDS=20
MACROLENS_MAX_RESPONSE_BYTES=5000000
MACROLENS_FAILURE_THRESHOLD=3
MACROLENS_CIRCUIT_OPEN_SECONDS=300
MACROLENS_STALE_TTL_SECONDS=86400
MACROLENS_CACHE_DB_PATH=/data/catalyst-cache.db
DEPLOY_REQUIRE_CATALYST=false
DEPLOY_REQUIRE_CATALYST_ACTIONS=false
```

`DEPLOY_REQUIRE_CATALYST` 是只读发布门禁。启用后，部署脚本要求 `MACROLENS_ENABLED=true`、`CATALYST_MODE=display`、远程 HTTPS 与证书校验、读密钥，以及与仓库一致的契约摘要；不要求 action 密钥或 `APP_AUTH_TOKEN`。

`DEPLOY_REQUIRE_CATALYST_ACTIONS` 是动作发布门禁，默认关闭。它只能在只读门禁开启时使用，并额外要求 action 密钥、`APP_AUTH_TOKEN`，以及部署后的 `analysis_trigger_enabled=true`。两个开关由宿主机上的部署脚本读取；部署后的临时验收命令由脚本单独传值，因此 `docker-compose.yml` 不新增业务配置项。

## 部署次序

1. 两边先部署代码和数据库迁移，但保持 `MACROLENS_ENABLED=false`，两个部署门禁均为 false。Option Pro 必须在备份后用新版镜像显式运行 `python -m app.services.catalysts.worker --migrate`；关闭状态的长驻 Worker 不会隐式执行迁移。
2. MacroLens 配置 HTTPS、允许网段、读密钥和 action 密钥。
3. Option Pro 只开启读能力，设置 `DEPLOY_REQUIRE_CATALYST=true`、`DEPLOY_REQUIRE_CATALYST_ACTIONS=false`，验证 Health、Feed、Calendar、缓存和重启恢复。
4. 再配置 action 密钥与 `APP_AUTH_TOKEN`，设置 `DEPLOY_REQUIRE_CATALYST_ACTIONS=true`，验证 `analysis_trigger_enabled=true`、幂等创建、查询和取消任务。
5. 最后开放界面按钮，CATALYST_MODE 仍为 display。

MacroLens 服务器尚未提供。本阶段只交付代码、容器配置、模拟契约和本地故障测试，不将“预留完成”描述为真实跨服务器联调完成。
