"""Typed runtime failures for Breakout Radar processing stages."""

from __future__ import annotations


FAILURE_DOMAINS = frozenset(
    {
        "provider",
        "price_data",
        "strength",
        "market_shape",
        "persistence",
        "database",
        "local_processing",
        "configuration",
    }
)


class BreakoutStageError(RuntimeError):
    """Attach an operations-safe domain and code to a fatal stage failure."""

    def __init__(
        self,
        failure_domain: str,
        code: str,
        message: str | None = None,
    ) -> None:
        if failure_domain not in FAILURE_DOMAINS:
            raise ValueError(f"unsupported breakout failure domain: {failure_domain}")
        self.failure_domain = failure_domain
        self.code = str(code or f"{failure_domain}_failed")[:120]
        super().__init__(message or self.code)
