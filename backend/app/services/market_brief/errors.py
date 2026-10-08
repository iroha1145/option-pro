"""市场综合研判的错误码与证据块缺失原因码。

运行记录、worker 状态与 /api/market-brief 响应共用这些字符串；全部满足
worker 的错误码格式 ``^[a-z][a-z0-9_]{0,119}$``，前端按原样做 i18n 键。
"""

from __future__ import annotations

# ---- 运行失败（BriefRunRecord.error_code）----
# 证据不足以成文：指数与市场内部结构（均线信号、市场状态）同时缺失。
EVIDENCE_UNAVAILABLE = "evidence_unavailable"
# 供应商侧：鉴权、限流、请求被拒（400 及其他 4xx）、服务端错误（≥500）、连接或超时。
PROVIDER_AUTH_FAILED = "provider_auth_failed"
PROVIDER_RATE_LIMITED = "provider_rate_limited"
PROVIDER_REQUEST_REJECTED = "provider_request_rejected"
PROVIDER_SERVER_ERROR = "provider_server_error"
PROVIDER_UNAVAILABLE = "provider_unavailable"
# 模型侧：安全分类器拒答、输出被截断、结尾不是 JSON、意料之外的停止原因。
PROVIDER_REFUSAL = "provider_refusal"
OUTPUT_TRUNCATED = "output_truncated"
OUTPUT_NOT_JSON = "output_not_json"
UNEXPECTED_STOP_REASON = "unexpected_stop_reason"
# 续跑控制：累计输出 token 超过上限；pause_turn 续跑次数用尽仍未结束；
# 整次运行的墙钟预算（request_timeout_seconds）所剩不足以再发一次请求。
BUDGET_EXCEEDED = "budget_exceeded"
CONTINUATION_LIMIT = "continuation_limit"
RUN_DEADLINE_EXCEEDED = "run_deadline_exceeded"
# 校验：标量字段（标题、结论段等）未通过契约或简体中文校验。
SCHEMA_VALIDATION_FAILED = "schema_validation_failed"
# 程序错误：记录后照常向上抛，交给 worker 记 degraded。
RUNTIME_ERROR = "runtime_error"
# 密钥缺失：worker 据此返回 disabled，命令行据此退出。
ANTHROPIC_API_KEY_MISSING = "anthropic_api_key_missing"

RUN_ERROR_CODES = frozenset({
    EVIDENCE_UNAVAILABLE,
    PROVIDER_AUTH_FAILED,
    PROVIDER_RATE_LIMITED,
    PROVIDER_REQUEST_REJECTED,
    PROVIDER_SERVER_ERROR,
    PROVIDER_UNAVAILABLE,
    PROVIDER_REFUSAL,
    OUTPUT_TRUNCATED,
    OUTPUT_NOT_JSON,
    UNEXPECTED_STOP_REASON,
    BUDGET_EXCEEDED,
    CONTINUATION_LIMIT,
    RUN_DEADLINE_EXCEEDED,
    SCHEMA_VALIDATION_FAILED,
    RUNTIME_ERROR,
    ANTHROPIC_API_KEY_MISSING,
})

# ---- 证据块缺失原因（coverage.missing_blocks[].reason）----
SNAPSHOT_MISSING = "snapshot_missing"  # 上游快照文件或条目不存在、已过硬期限、参数不匹配
READ_FAILED = "read_failed"  # 读取或解析时出现意料之外的异常
DISABLED = "disabled"  # 上游功能在配置里关闭
UNAVAILABLE = "unavailable"  # 上游明确返回不可用（含未配置密钥）
EMPTY = "empty"  # 读取成功但没有可用条目
OVER_BUDGET = "over_budget"  # 逐级裁剪后仍超字节预算，整块撤下

BLOCK_REASONS = frozenset({SNAPSHOT_MISSING, READ_FAILED, DISABLED, UNAVAILABLE, EMPTY, OVER_BUDGET})

__all__ = [
    "ANTHROPIC_API_KEY_MISSING",
    "BLOCK_REASONS",
    "BUDGET_EXCEEDED",
    "CONTINUATION_LIMIT",
    "DISABLED",
    "EMPTY",
    "EVIDENCE_UNAVAILABLE",
    "OUTPUT_NOT_JSON",
    "OUTPUT_TRUNCATED",
    "OVER_BUDGET",
    "PROVIDER_AUTH_FAILED",
    "PROVIDER_RATE_LIMITED",
    "PROVIDER_REFUSAL",
    "PROVIDER_REQUEST_REJECTED",
    "PROVIDER_SERVER_ERROR",
    "PROVIDER_UNAVAILABLE",
    "READ_FAILED",
    "RUNTIME_ERROR",
    "RUN_DEADLINE_EXCEEDED",
    "RUN_ERROR_CODES",
    "SCHEMA_VALIDATION_FAILED",
    "SNAPSHOT_MISSING",
    "UNAVAILABLE",
    "UNEXPECTED_STOP_REASON",
]
