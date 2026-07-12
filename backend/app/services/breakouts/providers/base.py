"""Provider-specific bounded failure types."""

from __future__ import annotations


class ProviderError(RuntimeError):
    code = "provider_error"
    retryable = False


class ProviderRetryableError(ProviderError):
    retryable = True


class ProviderRateLimited(ProviderRetryableError):
    code = "provider_rate_limited"

    def __init__(self, retry_after: float = 0.0) -> None:
        super().__init__(self.code)
        self.retry_after = max(0.0, float(retry_after))


class ProviderServerError(ProviderRetryableError):
    code = "provider_server_error"


class ProviderTimeout(ProviderRetryableError):
    code = "provider_timeout"


class ProviderPayloadTooLarge(ProviderError):
    code = "provider_payload_too_large"


class ProviderSchemaError(ProviderError):
    code = "provider_schema_error"
