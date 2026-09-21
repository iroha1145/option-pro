"""Default EOD limited ranking, using the research PRICE_ONLY + M1 rules."""

from __future__ import annotations

from datetime import date

MODE_ID = "eod_limited_v1"
COMPUTE_VERSION = "limited-all-market-v1.3"
FEATURE_VERSION = "us-eod-research-features-v1.6"
PURPOSE_LIVE = "live_eod_inference"
PURPOSE_HISTORICAL = "historical_example"
PURPOSE_SYNTHETIC = "synthetic"
VOLUME_SCOPE = "VENDOR_DAILY_UNVERIFIED"
RETURN_BASIS = "close_price_return"
MOMENTUM_BASIS = "price_return_not_total_return"
SCORE_BASIS = "price_only_diagnostic + m1_consensus"
ALGORITHM_VERSION = "eod-limited-v1.3"
RESEARCH_SEALED_SESSION = date(2024, 6, 28)
LIST_KIND_OBSERVATION = "observation"
LIST_KIND_COMPOSITE = "composite"

__all__ = [
    "ALGORITHM_VERSION",
    "COMPUTE_VERSION",
    "FEATURE_VERSION",
    "LIST_KIND_COMPOSITE",
    "LIST_KIND_OBSERVATION",
    "MODE_ID",
    "PURPOSE_HISTORICAL",
    "PURPOSE_LIVE",
    "PURPOSE_SYNTHETIC",
    "RESEARCH_SEALED_SESSION",
    "SCORE_BASIS",
]
