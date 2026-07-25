"""Deterministic display metadata for macro values.

The UI must never guess a unit from a factor name. Every factor carries a
``display_unit`` from the registry; this module turns that unit plus a raw value
into a stable ``formatted_value`` string and a unit descriptor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

from .models import finite


@dataclass(frozen=True, slots=True)
class UnitFormat:
    unit: str
    #: Short symbol/suffix suitable for a chart axis or a compact table cell.
    symbol_zh: str
    decimals: int
    #: ``prefix`` renders "$1.23"; ``suffix`` renders "1.23%"; ``none`` renders bare.
    placement: str
    #: When set, the value is divided by this before rendering (display only).
    display_divisor: float = 1.0


UNIT_FORMATS: Mapping[str, UnitFormat] = {
    "usd_billions": UnitFormat("usd_billions", "十亿美元", 1, "suffix"),
    "percentage_points": UnitFormat("percentage_points", "个百分点", 3, "suffix"),
    "percent": UnitFormat("percent", "%", 2, "suffix"),
    "index_points": UnitFormat("index_points", "", 2, "none"),
    "ratio": UnitFormat("ratio", "", 4, "none"),
    "usd_per_barrel": UnitFormat("usd_per_barrel", "美元/桶", 2, "suffix"),
    "usd_per_mmbtu": UnitFormat("usd_per_mmbtu", "美元/百万英热", 2, "suffix"),
    "score": UnitFormat("score", "分", 1, "suffix"),
}


def _round_half_up(value: float, decimals: int) -> float:
    """Round for display only, away from zero on exact halves.

    Python's ``round`` uses banker's rounding, which makes two runs of the same
    data disagree on ``x.5`` boundaries when read by a human.
    """

    from decimal import ROUND_HALF_UP, Decimal

    quantum = Decimal(1).scaleb(-decimals)
    return float(Decimal(repr(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def format_value(value: Optional[float], display_unit: str) -> Optional[str]:
    """Render one raw value, or ``None`` when the value is missing.

    A missing value is never rendered as ``0``.
    """

    number = finite(value)
    if number is None:
        return None
    spec = UNIT_FORMATS.get(display_unit)
    if spec is None:
        return f"{number:.4f}"
    scaled = number / spec.display_divisor if spec.display_divisor else number
    rendered = f"{_round_half_up(scaled, spec.decimals):.{spec.decimals}f}"
    if spec.placement == "prefix":
        return f"{spec.symbol_zh}{rendered}"
    if spec.placement == "suffix" and spec.symbol_zh:
        separator = "" if spec.symbol_zh in {"%"} else " "
        return f"{rendered}{separator}{spec.symbol_zh}"
    return rendered


def format_change(value: Optional[float], display_unit: str) -> Optional[str]:
    """Render a signed change; ``None`` stays ``None`` and never becomes ``0``."""

    number = finite(value)
    if number is None:
        return None
    body = format_value(abs(number), display_unit)
    if body is None:
        return None
    sign = "+" if number >= 0 else "−"
    return f"{sign}{body}"


def unit_descriptor(display_unit: str) -> dict[str, object]:
    spec = UNIT_FORMATS.get(display_unit)
    if spec is None:
        return {"unit": display_unit, "symbol_zh": "", "decimals": 4}
    return {
        "unit": spec.unit,
        "symbol_zh": spec.symbol_zh,
        "decimals": spec.decimals,
    }


def round_score(value: Optional[float]) -> Optional[float]:
    """Publish scores at one decimal. Internal maths never rounds early."""

    number = finite(value)
    if number is None:
        return None
    return _round_half_up(number, 1)


def round_confidence(value: Optional[float]) -> Optional[float]:
    number = finite(value)
    if number is None:
        return None
    return _round_half_up(number, 4)


__all__ = [
    "UNIT_FORMATS",
    "UnitFormat",
    "format_change",
    "format_value",
    "round_confidence",
    "round_score",
    "unit_descriptor",
]
