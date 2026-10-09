"""JSON helpers shared by persisted writers and readers.

The 64-level finite-tree check is for the API snapshots. Public-home and
stock-pull documents retain their stricter depth, size, and node budgets.
"""

from __future__ import annotations

import json
import math
from typing import Any


def canonical_json_text(value: Any) -> str:
    """Sorted, compact, UTF-8-preserving JSON that refuses NaN and Infinity.

    Hashes, identities and stored documents are computed from these bytes, so
    the arguments must never change.
    """

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def reject_duplicate_json_keys(
    pairs: list[tuple[str, Any]], *, error_message: str | None = None
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError(error_message if error_message is not None else f"duplicate JSON key: {key}")
        output[key] = value
    return output


def reject_non_finite_json(value: str, *, error_message: str | None = None) -> None:
    raise ValueError(error_message if error_message is not None else f"non-finite JSON value: {value}")


def is_finite_json_tree(value: Any, *, depth: int = 0) -> bool:
    if depth > 64:
        return False
    if value is None or isinstance(value, (bool, str, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(is_finite_json_tree(item, depth=depth + 1) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str)
            and is_finite_json_tree(item, depth=depth + 1)
            for key, item in value.items()
        )
    return False
