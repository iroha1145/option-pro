from __future__ import annotations

import logging
import threading

from fastapi import Request

from app.access import require_same_origin_action


REMOVAL_VERSION = "Personal Edition 2.0"
_logger = logging.getLogger(__name__)
_warning_lock = threading.Lock()
_warning_recorded = False


def require_expensive_action(request: Request) -> None:
    """One-release adapter for routes renamed in the final cleanup PR."""

    global _warning_recorded
    if not _warning_recorded:
        with _warning_lock:
            if not _warning_recorded:
                _logger.warning(
                    "require_expensive_action is deprecated and will be removed in %s",
                    REMOVAL_VERSION,
                )
                _warning_recorded = True
    require_same_origin_action(request)
