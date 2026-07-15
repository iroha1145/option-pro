from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.access import (
    OWNER_COOKIE_NAME,
    OWNER_SESSION_SECONDS,
    LoginRejected,
    OwnerAccessRuntime,
    get_access_runtime,
    request_uses_https,
    require_same_origin_action,
    require_same_origin_json,
)


router = APIRouter(prefix="/api/access", tags=["access"])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, max_length=1024)


def _runtime(request: Request) -> OwnerAccessRuntime:
    runtime = getattr(request.app.state, "access_runtime", None)
    return runtime if isinstance(runtime, OwnerAccessRuntime) else get_access_runtime()


def _client_key(request: Request, runtime: OwnerAccessRuntime) -> str:
    address = runtime.request_address(request)
    return str(address) if address is not None else "unknown"


@router.get("/status", dependencies=[Depends(require_same_origin_action)])
def access_status(request: Request) -> dict[str, object]:
    runtime = _runtime(request)
    return {
        "access_mode": runtime.mode,
        "logged_in": runtime.request_is_owner(request),
    }


@router.post(
    "/login",
    dependencies=[Depends(require_same_origin_json)],
)
def login(
    request: Request,
    payload: Annotated[LoginRequest, Body()],
) -> Response:
    runtime = _runtime(request)
    if runtime.mode != "password":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "login_not_required", "message": "Login is not required"},
        )
    if not request_uses_https(request):
        raise HTTPException(
            status_code=status.HTTP_426_UPGRADE_REQUIRED,
            detail={"code": "https_required", "message": "HTTPS is required"},
        )
    try:
        result = runtime.login(payload.password, client_key=_client_key(request, runtime))
    except LoginRejected as exc:
        headers = (
            {"Retry-After": str(exc.retry_after)}
            if exc.retry_after is not None
            else None
        )
        raise HTTPException(
            status_code=(
                status.HTTP_429_TOO_MANY_REQUESTS
                if exc.code == "login_cooldown"
                else status.HTTP_401_UNAUTHORIZED
            ),
            detail={"code": exc.code, "message": "Owner login failed"},
            headers=headers,
        ) from exc
    response = JSONResponse(
        {"access_mode": "password", "logged_in": True},
        headers={"Cache-Control": "no-store"},
    )
    response.set_cookie(
        OWNER_COOKIE_NAME,
        result.session_token,
        max_age=OWNER_SESSION_SECONDS,
        expires=OWNER_SESSION_SECONDS,
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )
    return response


@router.post("/logout")
def logout(request: Request) -> Response:
    require_same_origin_action(request)
    runtime = _runtime(request)
    runtime.logout(request.cookies.get(OWNER_COOKIE_NAME, ""))
    response = JSONResponse(
        {"access_mode": runtime.mode, "logged_in": runtime.mode == "private_network"},
        headers={"Cache-Control": "no-store"},
    )
    response.delete_cookie(
        OWNER_COOKIE_NAME,
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )
    return response
