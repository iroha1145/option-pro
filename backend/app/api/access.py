from __future__ import annotations

import time
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


#: The owner signs in under this name. Everything else is a customer account.
OWNER_USERNAME = "admin"


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Optional so older clients that only posted a password still reach the
    #: owner path unchanged.
    username: str = Field(default=OWNER_USERNAME, min_length=1, max_length=64)
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
    from app.api.accounts import current_account

    runtime = _runtime(request)
    account = current_account(request)
    return {
        "access_mode": runtime.mode,
        # ``logged_in`` stays owner-only: existing clients read it to decide
        # whether owner controls may render.
        "logged_in": runtime.request_is_owner(request),
        "account": (
            {"logged_in": True, "username": account.username}
            if account is not None
            else {"logged_in": False, "username": None}
        ),
    }


def _customer_login(request: Request, payload: LoginRequest) -> Response:
    """Sign in a customer account.

    Deliberately separate from the owner branch: this issues only the account
    cookie, so a customer session can never satisfy an owner-gated route.
    """

    from app.api.accounts import (
        attach_account_cookie,
        account_http_error,
        check_login_cooldown,
        record_login_failure,
    )
    from app.services.accounts import AccountError, get_account_store

    if not request_uses_https(request):
        raise HTTPException(
            status_code=status.HTTP_426_UPGRADE_REQUIRED,
            detail={"code": "https_required", "message": "登录需要 HTTPS"},
        )
    check_login_cooldown(request)
    try:
        result = get_account_store().authenticate(payload.username, payload.password)
    except AccountError as exc:
        record_login_failure(request)
        raise account_http_error(exc) from exc
    # This bucket covers all usernames from one source address. A successful
    # login to an attacker's own account must not erase guesses against others;
    # failures expire through the normal rolling window instead.
    response = JSONResponse(
        {
            "access_mode": "password",
            "logged_in": False,
            "account": {"logged_in": True, "username": result.account.username},
        },
        headers={"Cache-Control": "no-store"},
    )
    attach_account_cookie(
        response,
        result.token,
        int(max(1, result.expires_at - time.time())),
    )
    return response


@router.post(
    "/login",
    dependencies=[Depends(require_same_origin_json)],
)
def login(
    request: Request,
    payload: Annotated[LoginRequest, Body()],
) -> Response:
    runtime = _runtime(request)
    if payload.username.strip().casefold() != OWNER_USERNAME:
        return _customer_login(request, payload)
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
    from app.api.accounts import ACCOUNT_COOKIE_NAME, clear_account_cookie
    from app.services.accounts import get_account_store

    runtime = _runtime(request)
    runtime.logout(request.cookies.get(OWNER_COOKIE_NAME, ""))
    # One sign-out button clears whichever session the visitor holds.
    get_account_store().revoke_session(request.cookies.get(ACCOUNT_COOKIE_NAME, ""))
    response = JSONResponse(
        {"access_mode": runtime.mode, "logged_in": runtime.mode == "private_network"},
        headers={"Cache-Control": "no-store"},
    )
    clear_account_cookie(response)
    response.delete_cookie(
        OWNER_COOKIE_NAME,
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )
    return response
