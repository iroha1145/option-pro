# 故障与降级语义

## 页面数据状态

- active：最近同步成功且数据在新鲜窗口内。
- degraded：仍有可用数据，但远程、来源或部分能力异常。
- stale：显示最后有效快照，并明确数据截止时间。
- unavailable：从未有有效快照，或超过 stale TTL。
- disabled：功能未启用。
- empty：有效查询确认没有匹配记录。

empty 不是 unavailable；disabled 和 not_configured 使用中性样式。

## 隔离

Option Pro /ready 只检查自身核心条件，不检查 MacroLens、Catalyst 缓存、同步进程或 AI Worker。Catalyst 路由导入不得打开数据库或远程连接。

MacroLens 宕机、DNS、TLS、401、403、429、5xx、超时和 Schema 错误都不得让行情、期权、强势、突破、财报或市场形态失败。

## 保留规则

远程失败不清空旧数据、不推进水位。Feed、Calendar、Health、Read key、Action key 和 Job Poll 分别记录状态；一个端点的熔断不阻断其他端点。

MACROLENS_STALE_TTL_SECONDS 从最后一次成功发布 Feed 计算，不能由失败尝试、Health 成功或 Worker 心跳刷新。

MacroLens 输出侧另用 OPTION_PRO_SOURCE_STALE_AFTER_SECONDS 标记来源过久未成功后的新闻项；来源进入 degraded 或 unavailable 时立即标记。Option Pro 仍以自身缓存的 MACROLENS_STALE_TTL_SECONDS 决定何时从 stale 升级为 unavailable。

## 熔断

连续三次可计数故障后打开 300 秒。按端点族和 read/action 分开；半开只允许一个探针。401、403、TLS 和 Schema 错误属于配置或安全故障，不做快速重试。

## 公共错误

浏览器只收到有界错误码、retry_after 和安全说明，不收到堆栈、上游响应正文、完整 URL 查询、密钥或 OpenAI Response ID。AbortError 不显示为业务失败。
