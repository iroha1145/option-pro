from __future__ import annotations

import os

from fastapi import HTTPException, Request


def require_expensive_action(request: Request) -> None:
    """Require the app token for every operation capable of spending money."""

    if not os.environ.get("APP_AUTH_TOKEN", "").strip():
        raise HTTPException(
            status_code=503,
            detail={
                "code": "capability_disabled",
                "message": "Expensive AI actions require APP_AUTH_TOKEN",
            },
        )
    state = request.scope.get("state") or {}
    if not state.get("app_authenticated"):
        raise HTTPException(status_code=401, detail="Authentication required")
