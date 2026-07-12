# 服务间认证

## 请求头

X-Optix-Key-Id  
X-Optix-Timestamp  
X-Optix-Nonce  
X-Optix-Content-SHA256  
X-Optix-Signature

## 规范字符串

严格使用六行，末尾不加换行：

~~~text
METHOD
PATH
CANONICAL_QUERY
TIMESTAMP
NONCE
BODY_SHA256
~~~

签名为 HMAC-SHA256(secret, canonical_string) 的小写十六进制。GET 空正文使用空字节 SHA-256。正文签名针对实际发送字节，不能重新序列化。

查询键和值使用 UTF-8 和 RFC 3986 编码，空格为 %20，转义十六进制大写；保留重复键与空值，先按编码键、再按编码值排序。

## 权限

read key 可访问 health、feed、latest、news、catalysts、calendar 和任务状态。action key 可访问 read，并可创建、重试和取消任务。

Action key 未配置时，读取仍可用，analysis_trigger_enabled=false，界面不显示执行按钮。

## 服务端配置

OPTION_PRO_READ_KEY_ID  
OPTION_PRO_READ_SECRET  
OPTION_PRO_ACTION_KEY_ID  
OPTION_PRO_ACTION_SECRET  
OPTION_PRO_PREVIOUS_READ_SECRET  
OPTION_PRO_PREVIOUS_ACTION_SECRET  
OPTION_PRO_ALLOWED_CIDRS  
OPTION_PRO_TRUSTED_PROXY_CIDRS  
OPTION_PRO_SIGNATURE_CLOCK_SKEW_SECONDS=300  
OPTION_PRO_NONCE_TTL_SECONDS=600  
OPTION_PRO_ALLOW_LOCAL_HTTP=false

Option Pro 使用 MACROLENS_READ_KEY_ID、MACROLENS_READ_SECRET、MACROLENS_ACTION_KEY_ID 和 MACROLENS_ACTION_SECRET。

读写密钥编号只允许字母、数字、点、下划线、冒号和连字符，且两者不得相同。每个 HMAC Secret 至少 32 字节；当前值与 previous 值不得相同。

## 生产代理边界

生产边界代理必须终止传输层安全协议（TLS），并把 `/api/integrations/option-pro/v1/` 直接转发到 `127.0.0.1:8000`；其他网页和普通接口转发到 `127.0.0.1:3000`。前端 Nginx 不代转 Integration 路径。Docker Compose 的 3000 和 8000 端口都只绑定回环地址，防止绕过边界代理访问明文端口。

边界代理须覆盖客户端传来的 `X-Forwarded-For` 和 `X-Forwarded-Proto`，不能追加或照抄不可信协议头。`OPTION_PRO_TRUSTED_PROXY_CIDRS` 只列出实际边界代理地址；只有请求的直接来源命中该列表时，MacroLens 才采用转发来源和 `X-Forwarded-Proto: https`。该列表为空时不信任任何转发头。`OPTION_PRO_ALLOWED_CIDRS` 填写 Option Pro 服务器的固定出口地址，而不是代理地址；配置任一服务密钥后，该列表为空会拒绝启动，请求侧也会再次按关闭优先原则拒绝。

`OPTION_PRO_ALLOW_LOCAL_HTTP=true` 只用于回环地址上的显式本地测试。生产必须保持 `false`，并通过受信边界代理提供 HTTPS。

## 验证顺序

1. 检查头格式和时间。
2. 比较原始正文摘要。
3. 查找 key id 并用 secrets.compare_digest 校验签名。
4. 仅从直接来源命中 `OPTION_PRO_TRUSTED_PROXY_CIDRS` 的代理链确定 HTTPS 和来源地址。
5. 检查允许网段和 scope。
6. 以 key_id + nonce 原子写入持久 nonce 表。
7. 校验请求模型。

时间超过 300 秒、nonce 在 600 秒内重复、正文摘要错误或签名错误返回 401；scope 或网段错误返回 403。认证失败使用独立限速桶。日志不得记录 Secret、完整签名、认证头或完整查询串。

## 轮换

MacroLens 先把新 Secret 设为当前值，把旧值放入 previous；Option Pro 切换发送值；观察至少 nonce 和时间窗总时长后移除 previous。previous 只用于验证，不由客户端在 401 后盲试。
