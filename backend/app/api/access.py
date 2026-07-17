from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from app.access import (
    OWNER_COOKIE_NAME,
    OWNER_SESSION_SECONDS,
    LoginRejected,
    OwnerAccessRuntime,
    get_access_runtime,
    request_uses_https,
    require_public_read_or_owner_access,
    require_same_origin_action,
    require_same_origin_json,
)


_MAX_LOGIN_BODY_BYTES = 4 * 1024


class _BoundedLoginRoute(APIRoute):
    """Reject oversized login bodies before JSON or model parsing."""

    def get_route_handler(self):
        original_handler = super().get_route_handler()

        async def bounded_handler(request: Request):
            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    length = int(content_length)
                    if length < 0:
                        raise ValueError
                    if length > _MAX_LOGIN_BODY_BYTES:
                        raise HTTPException(
                            status_code=413,
                            detail="Login request body is too large",
                        )
                except ValueError as exc:
                    raise HTTPException(
                        status_code=400,
                        detail="Invalid Content-Length",
                    ) from exc
            chunks: list[bytes] = []
            total = 0
            async for chunk in request.stream():
                total += len(chunk)
                if total > _MAX_LOGIN_BODY_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="Login request body is too large",
                    )
                chunks.append(chunk)
            request._body = b"".join(chunks)
            return await original_handler(request)

        return bounded_handler


router = APIRouter(
    prefix="/api/access",
    tags=["access"],
    route_class=_BoundedLoginRoute,
)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, max_length=1024)


def _runtime(request: Request) -> OwnerAccessRuntime:
    runtime = getattr(request.app.state, "access_runtime", None)
    return runtime if isinstance(runtime, OwnerAccessRuntime) else get_access_runtime()


def _client_key(request: Request, runtime: OwnerAccessRuntime) -> str:
    address = runtime.request_address(request)
    return str(address) if address is not None else "unknown"


@router.get(
    "/status",
    dependencies=[Depends(require_public_read_or_owner_access)],
)
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


@router.post("/logout", dependencies=[Depends(require_same_origin_action)])
def logout(request: Request) -> Response:
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
