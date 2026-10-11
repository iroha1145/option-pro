"""Server-only secrets that belong in ``secrets.env``.

``runtime_environment`` and ``tools.personal_secrets`` read this tuple.
``personal.sh`` keeps a shell copy that a test compares against it.
"""

from __future__ import annotations


SECRET_KEYS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "FINNHUB_API_KEY",
    "MARKETDATA_TOKEN",
    "MASSIVE_API_KEY",
    "FRED_API_KEY",
    "INTERNAL_API_TOKEN",
    "APP_PASSWORD_HASH",
)


__all__ = ["SECRET_KEYS"]
