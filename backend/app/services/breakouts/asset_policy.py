"""Radar excludes amplified fund exposure, while retaining ordinary ETFs.

Use explicit instrument metadata, never the size of a price move or a ticker
suffix. ProShares' Ultra / UltraShort / UltraPro series encode leverage even
when the provider's name omits a numeric multiplier (for example SBIT / ETHD).
Issuer reference: https://www.proshares.com/our-etfs/find-leveraged-and-inverse-etfs
"""

from __future__ import annotations

import re
from typing import Any, Mapping


_MULTIPLIER = re.compile(r"(?<![\w.])([+-]?\d+(?:\.\d+)?)\s*[- ]?(?:x|times)\b", re.I)
_FUND_NAME = re.compile(r"\b(?:etf|etn|exchange[ -]traded (?:fund|note))\b", re.I)
_PROSHARES_ULTRA = re.compile(r"\bproshares\s+ultra(?:short|pro)?\b", re.I)


def is_leveraged_etf(
    asset_type: Any,
    name: Any,
    raw_provider_fields: Mapping[str, Any] | None = None,
) -> bool:
    """Classify candidates and persisted events using the same evidence rules.

    Missing or ambiguous metadata is not evidence of leverage. In particular,
    ``short``, ``bear``, and ``ultra-short duration`` alone do not exclude a fund.
    The first two arguments also form the SQLite read-filter function.
    """
    raw = raw_provider_fields if isinstance(raw_provider_fields, Mapping) else {}
    names = " ".join(str(value or "") for value in (name, raw.get("description"))).replace("×", "x")
    if _PROSHARES_ULTRA.search(names):
        return True
    specs = raw.get("typespecs") or ()
    if isinstance(specs, str):
        specs = re.split(r"[\s,;|]+", specs)
    elif not isinstance(specs, (list, tuple, set)):
        specs = ()
    tokens = {str(value).strip().lower() for value in specs}
    kind = str(getattr(asset_type, "value", asset_type) or "").lower()
    is_fund = (
        kind in {"etf", "etn", "fund"}
        or bool(tokens & {"etf", "etn"})
        or bool(_FUND_NAME.search(names))
    )
    if not is_fund:
        return False
    if "leveraged" in tokens:
        return True
    if any(abs(float(match.group(1))) > 1 for match in _MULTIPLIER.finditer(names)):
        return True
    # Do not mistake an explicit non-leveraged description for leverage.
    positive_names = re.sub(r"\b(?:unleveraged|non[ -]?leveraged)\b", "", names, flags=re.I)
    return bool(re.search(r"\bleveraged\b", positive_names, re.I))
