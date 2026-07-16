from __future__ import annotations

import os

from fastapi import APIRouter, Depends

from app.access import require_owner_access


router = APIRouter(
    prefix="/api/settings",
    tags=["settings"],
    dependencies=[Depends(require_owner_access)],
)


def _configured(name: str) -> dict[str, bool]:
    return {"configured": bool(os.environ.get(name, "").strip())}


@router.get("")
def settings_status() -> dict[str, object]:
    return {
        "openai": _configured("OPENAI_API_KEY"),
        "finnhub": _configured("FINNHUB_API_KEY"),
        "marketdata": _configured("MARKETDATA_TOKEN"),
        "internal_api": _configured("INTERNAL_API_TOKEN"),
    }
