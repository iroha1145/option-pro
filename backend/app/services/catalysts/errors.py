from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class CatalystError(Exception):
    """Safe, classified catalyst error.

    ``message`` is suitable for server logs but must not contain an upstream
    response body, signed URL, secret, nonce, or signature.
    """

    code: str
    message: str
    retryable: bool = False
    retry_after_seconds: Optional[int] = None
    counts_for_circuit: bool = True
    resync_from: Optional[datetime] = None

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class InvalidCursorError(CatalystError):
    def __init__(self) -> None:
        super().__init__(
            code="invalid_cursor",
            message="The catalyst cursor is invalid or does not match this query",
            retryable=False,
            counts_for_circuit=False,
        )
