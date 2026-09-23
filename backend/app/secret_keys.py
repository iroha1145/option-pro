"""Server-only secrets that belong in ``secrets.env``.

Standard library only: host-side migration tools import this list before any
application dependency is installed. ``personal.sh`` keeps a shell copy that a
test compares against this tuple.
"""

from __future__ import annotations


SECRET_KEYS = (
    "OPENAI_API_KEY",
    "FINNHUB_API_KEY",
    "MARKETDATA_TOKEN",
    "MASSIVE_API_KEY",
    "FMP_API_KEY",
    "FRED_API_KEY",
    "INTERNAL_API_TOKEN",
    "APP_PASSWORD_HASH",
)


__all__ = ["SECRET_KEYS"]
