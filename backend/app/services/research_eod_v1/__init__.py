"""US EOD research v1 — isolated from production ranking.

This package implements the registered research protocol. It is not a
production default, not an options backtester, and does not fetch market
data from ``compute_snapshot``.
"""

from __future__ import annotations

FEATURE_VERSION = "us-eod-research-features-v1.3"
ALGORITHM_FAMILY_VERSION = "us-eod-research-abcd-v1"
COMPOSITE_VERSION = "us-eod-research-m1m4-v1"
SCORE_VERSION = "us-eod-research-score-v1"
RESEARCH_FLAG = "RESEARCH_EOD_V1_ENABLED"

__all__ = [
    "ALGORITHM_FAMILY_VERSION",
    "COMPOSITE_VERSION",
    "FEATURE_VERSION",
    "RESEARCH_FLAG",
    "SCORE_VERSION",
]
