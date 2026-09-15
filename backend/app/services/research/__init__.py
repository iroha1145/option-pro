"""Offline research harness that replays production screener and radar logic.

This package does not replace production scoring. It injects a clock and an
offline price panel into the existing scanners, then attaches separately
computed labels and execution ledgers.
"""

from .protocol import (
    EVIDENCE_GRADES,
    FROZEN_PROTOCOL,
    RESEARCH_PROTOCOL_VERSION,
    SplitName,
    assert_split_access,
)

__all__ = [
    "EVIDENCE_GRADES",
    "FROZEN_PROTOCOL",
    "RESEARCH_PROTOCOL_VERSION",
    "SplitName",
    "assert_split_access",
]
