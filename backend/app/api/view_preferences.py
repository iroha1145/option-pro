"""Read and save the current principal's algorithm view preferences."""

from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from app.access import (
    current_request_is_owner,
    request_account_session,
    require_same_origin_json,
)
from app.services.algorithm_modes import (
    FOLLOW_DEFAULT,
    UnknownAlgorithmError,
)
from app.services.view_preferences import (
    ViewPreferenceStore,
    ViewPreferenceStorageError,
    get_view_preference_store,
    normalize_view_preferences,
    principal_for_request,
)


router = APIRouter(prefix="/api/view-preferences", tags=["view-preferences"])


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ViewPreferencesPatch(_StrictModel):
    screener_ranking_algorithm: Optional[
        Literal["follow_default", "production", "a0_mid_long", "eod_limited_v1"]
    ] = None
    radar_sort_algorithm: Optional[
        Literal["follow_default", "production", "t1_daily_priority"]
    ] = None


class ViewPreferencesResponse(_StrictModel):
    principal: Optional[str] = None
    persisted: bool
    screener_ranking_algorithm: str = FOLLOW_DEFAULT
    radar_sort_algorithm: str = FOLLOW_DEFAULT


def _store() -> ViewPreferenceStore:
    return get_view_preference_store()


def _principal(request: Request) -> str | None:
    account = request_account_session(request)
    account_id = getattr(account, "user_id", None) if account is not None else None
    return principal_for_request(
        is_owner=current_request_is_owner(),
        account_id=str(account_id) if account_id else None,
    )


def _storage_unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "code": "view_preferences_storage_unavailable",
            "message": "算法选择暂时无法读取或保存",
        },
    )


def current_view_preferences(request: Request) -> dict[str, Any]:
    principal = _principal(request)
    if principal is None:
        prefs = normalize_view_preferences({})
        return {"principal": None, "persisted": False, **prefs.as_dict()}
    prefs = _store().read(principal)
    return {"principal": principal, "persisted": True, **prefs.as_dict()}


@router.get("", response_model=ViewPreferencesResponse)
def read_view_preferences(request: Request) -> ViewPreferencesResponse:
    try:
        return ViewPreferencesResponse(**current_view_preferences(request))
    except ViewPreferenceStorageError as exc:
        raise _storage_unavailable() from exc


@router.put(
    "",
    response_model=ViewPreferencesResponse,
    dependencies=[Depends(require_same_origin_json)],
)
def update_view_preferences(
    request: Request,
    patch: ViewPreferencesPatch,
) -> ViewPreferencesResponse:
    principal = _principal(request)
    if principal is None:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "view_preferences_login_required",
                "message": "登录后才能把算法选择保存到账号",
            },
        )
    try:
        updates = patch.model_dump(exclude_unset=True)
        saved = _store().patch(principal, updates)
    except UnknownAlgorithmError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except ViewPreferenceStorageError as exc:
        raise _storage_unavailable() from exc
    return ViewPreferencesResponse(
        principal=principal,
        persisted=True,
        **saved.as_dict(),
    )
