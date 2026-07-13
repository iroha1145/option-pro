"""Dedicated, signed server-to-server integration endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.services.catalysts.errors import CatalystError
from app.services.catalysts.focus_auth import (
    FOCUS_CONTEXT_PATH,
    authenticate_focus_request,
)
from app.services.catalysts.focus_config import (
    FocusContextSettings,
    get_focus_context_settings,
)
from app.services.catalysts.focus_models import FocusContextResponse
from app.services.catalysts.repository import CatalystRepository


router = APIRouter(tags=["integrations"])


def _status_for_error(error: CatalystError) -> int:
    if error.code in {"focus_capability_disabled", "focus_cache_unavailable"}:
        return 503
    if error.code in {"focus_source_forbidden", "focus_https_required"}:
        return 403
    if error.code == "focus_replay":
        return 409
    return 401


@router.get(
    FOCUS_CONTEXT_PATH,
    response_model=FocusContextResponse,
    response_model_exclude_none=False,
)
def focus_context(
    request: Request,
    settings: FocusContextSettings = Depends(get_focus_context_settings),
) -> FocusContextResponse:
    if not settings.cache_db_path.is_file():
        raise HTTPException(
            status_code=503,
            detail={"code": "focus_cache_unavailable", "message": "No focus snapshot is available"},
        )
    repository = CatalystRepository(settings.cache_db_path)
    try:
        repository.check_schema()
        authenticate_focus_request(request, settings=settings, repository=repository)
        snapshot = repository.current_focus_context()
    except CatalystError as error:
        raise HTTPException(
            status_code=_status_for_error(error),
            detail={"code": error.code, "message": "Focus context request was rejected"},
        ) from error
    if snapshot is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "focus_context_unavailable", "message": "No focus snapshot is available"},
        )
    return snapshot
