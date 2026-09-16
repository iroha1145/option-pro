"""Standalone config tests. Not a return verification."""

from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reference"))
from registry import load_registry, resolve_weights  # type: ignore


def test_registry_shape() -> None:
    data = load_registry(ROOT / "config" / "registry.json")
    assert len(data["sectors"]) == 24
    example = resolve_weights(data, "semiconductors", "A_trend_quality")
    assert math.isclose(sum(example.values()), 1.0, abs_tol=1e-9)
