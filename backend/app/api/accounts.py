"""Customer account endpoints: sign-up, identity and the personal watchlist.

These sessions are strictly separate from the owner session. Nothing here can
grant owner capability, and no owner-only route consults the account cookie.

The watchlist routes are the one place both principals meet, and only in the
inbound direction: an owner session may read and edit *its own* watchlist. That
does not leak owner capability into this module -- the owner already holds every
capability -- and without it the owner, who is the only account on a personal
deployment, would be the one user unable to keep a watchlist.
"""

from __future__ import annotations

import threading
import time
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.access import (
    request_is_owner_session,
    request_uses_https,
    require_same_origin_json,
    require_same_origin_request,
)
from app.services.accounts import (
    Account,
    AccountError,
    WATCHLIST_MAX_TICKERS,
    get_account_store,
)

ACCOUNT_COOKIE_NAME = "optix_user_session"

router = APIRouter(prefix="/api/account", tags=["account"])

_REGISTER_WINDOW_SECONDS = 60 * 60
_REGISTER_MAX_PER_WINDOW = 5
_LOGIN_FAILURE_LIMIT = 10
_LOGIN_FAILURE_WINDOW_SECONDS = 15 * 60
_LOGIN_COOLDOWN_SECONDS = 5 * 60
_RATE_BUCKET_LIMIT = 4096

_rate_lock = threading.Lock()
_register_hits: dict[str, list[float]] = {}
_login_failures: dict[str, tuple[int, float, float]] = {}


def _client_key(request: Request) -> str:
    client = request.client
    return client.host if client and client.host else "unknown"


def _prune(bucket: dict, now: float) -> None:
    if len(bucket) >= _RATE_BUCKET_LIMIT:
        bucket.clear()


def enforce_registration_rate(request: Request) -> None:
    """A public sign-up form is an open door; keep the hinge tight."""

    key = _client_key(request)
    now = time.time()
    with _rate_lock:
        _prune(_register_hits, now)
        hits = [
            stamp
            for stamp in _register_hits.get(key, [])
            if stamp > now - _REGISTER_WINDOW_SECONDS
        ]
        if len(hits) >= _REGISTER_MAX_PER_WINDOW:
            retry_after = int(hits[0] + _REGISTER_WINDOW_SECONDS - now) + 1
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "code": "registration_rate_limited",
                    "message": "注册过于频繁，请稍后再试",
                },
                headers={"Retry-After": str(max(1, retry_after))},
            )
        hits.append(now)
        _register_hits[key] = hits


def check_login_cooldown(request: Request) -> None:
    key = _client_key(request)
    now = time.time()
    with _rate_lock:
        _prune(_login_failures, now)
        _count, _started_at, blocked_until = _login_failures.get(key, (0, now, 0.0))
        if blocked_until > now:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={"code": "login_cooldown", "message": "登录尝试过多，请稍后再试"},
                headers={"Retry-After": str(max(1, int(blocked_until - now) + 1))},
            )


def record_login_failure(request: Request) -> None:
    key = _client_key(request)
    now = time.time()
    with _rate_lock:
        count, started_at, _blocked = _login_failures.get(key, (0, now, 0.0))
        if started_at < now - _LOGIN_FAILURE_WINDOW_SECONDS:
            count, started_at = 0, now
        count += 1
        blocked_until = (
            now + _LOGIN_COOLDOWN_SECONDS if count >= _LOGIN_FAILURE_LIMIT else 0.0
        )
        _login_failures[key] = (count, started_at, blocked_until)


def clear_login_failures(request: Request) -> None:
    with _rate_lock:
        _login_failures.pop(_client_key(request), None)


def reset_rate_limits() -> None:
    """Test seam."""

    with _rate_lock:
        _register_hits.clear()
        _login_failures.clear()


class CredentialsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class TickerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: str = Field(min_length=1, max_length=16)


class WatchlistRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tickers: list[str] = Field(default_factory=list, max_length=WATCHLIST_MAX_TICKERS)


_ERROR_STATUS = {
    "username_taken": status.HTTP_409_CONFLICT,
    "registration_closed": status.HTTP_503_SERVICE_UNAVAILABLE,
    "invalid_credentials": status.HTTP_401_UNAUTHORIZED,
    "watchlist_full": status.HTTP_409_CONFLICT,
}

_ERROR_MESSAGE = {
    "username_required": "请填写用户名",
    "username_too_long": "用户名过长",
    "username_invalid_characters": "用户名不能包含空格或控制字符",
    "username_reserved": "该用户名已被保留，请换一个",
    "username_taken": "该用户名已被占用",
    "password_required": "请填写密码",
    "password_too_long": "密码过长",
    "password_invalid_characters": "密码包含不支持的字符",
    "registration_closed": "注册名额已满",
    "invalid_credentials": "用户名或密码不正确",
    "invalid_ticker": "股票代码格式不正确",
    "watchlist_full": f"自选最多 {WATCHLIST_MAX_TICKERS} 只股票",
}


def account_http_error(error: AccountError) -> HTTPException:
    code = error.code
    return HTTPException(
        status_code=_ERROR_STATUS.get(code, status.HTTP_400_BAD_REQUEST),
        detail={"code": code, "message": _ERROR_MESSAGE.get(code, "请求无法完成")},
    )


def current_account(request: Request) -> Account | None:
    token = request.cookies.get(ACCOUNT_COOKIE_NAME, "")
    if not token:
        return None
    return get_account_store().resolve_session(token)


def require_watchlist_account(request: Request) -> Account:
    """Resolve whichever principal owns the watchlist being addressed.

    A customer cookie wins when present, so an owner who is also signed in as a
    customer in the same browser still edits the customer list they can see. An
    owner session otherwise resolves to the single reserved owner row. Order
    matters only for that overlap; the two never share a list.
    """

    account = current_account(request)
    if account is not None:
        return account
    if request_is_owner_session(request):
        return get_account_store().ensure_owner_account()
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "account_login_required", "message": "请先登录"},
    )


def attach_account_cookie(response: Response, token: str, max_age: int) -> None:
    response.set_cookie(
        ACCOUNT_COOKIE_NAME,
        token,
        max_age=max_age,
        expires=max_age,
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )


def clear_account_cookie(response: Response) -> None:
    response.delete_cookie(
        ACCOUNT_COOKIE_NAME,
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )


def _account_payload(account: Account) -> dict:
    return {
        "logged_in": True,
        "username": account.username,
        "created_at": account.created_at,
    }


@router.post(
    "/register",
    dependencies=[Depends(require_same_origin_json)],
)
def register(request: Request, payload: Annotated[CredentialsRequest, Body()]) -> Response:
    if not request_uses_https(request):
        raise HTTPException(
            status_code=status.HTTP_426_UPGRADE_REQUIRED,
            detail={"code": "https_required", "message": "注册需要 HTTPS"},
        )
    enforce_registration_rate(request)
    try:
        result = get_account_store().register(payload.username, payload.password)
    except AccountError as exc:
        raise account_http_error(exc) from exc
    response = JSONResponse(
        _account_payload(result.account),
        status_code=status.HTTP_201_CREATED,
        headers={"Cache-Control": "no-store"},
    )
    attach_account_cookie(
        response,
        result.token,
        int(max(1, result.expires_at - time.time())),
    )
    return response


@router.get("/me")
def me(request: Request) -> Response:
    account = current_account(request)
    body = (
        _account_payload(account)
        if account is not None
        else {"logged_in": False, "username": None}
    )
    return JSONResponse(body, headers={"Cache-Control": "no-store"})


@router.post("/logout", dependencies=[Depends(require_same_origin_request)])
def logout(request: Request) -> Response:
    get_account_store().revoke_session(request.cookies.get(ACCOUNT_COOKIE_NAME, ""))
    response = JSONResponse(
        {"logged_in": False, "username": None},
        headers={"Cache-Control": "no-store"},
    )
    clear_account_cookie(response)
    return response


@router.get("/watchlist")
def read_watchlist(request: Request) -> Response:
    account = require_watchlist_account(request)
    return JSONResponse(
        {
            "tickers": get_account_store().watchlist(account.user_id),
            "max_tickers": WATCHLIST_MAX_TICKERS,
        },
        headers={"Cache-Control": "no-store"},
    )


@router.put(
    "/watchlist",
    dependencies=[Depends(require_same_origin_json)],
)
def replace_watchlist(
    request: Request,
    payload: Annotated[WatchlistRequest, Body()],
) -> Response:
    account = require_watchlist_account(request)
    try:
        tickers = get_account_store().replace_watchlist(
            account.user_id,
            payload.tickers,
        )
    except AccountError as exc:
        raise account_http_error(exc) from exc
    return JSONResponse(
        {"tickers": tickers, "max_tickers": WATCHLIST_MAX_TICKERS},
        headers={"Cache-Control": "no-store"},
    )


@router.post(
    "/watchlist",
    dependencies=[Depends(require_same_origin_json)],
)
def add_watchlist_ticker(
    request: Request,
    payload: Annotated[TickerRequest, Body()],
) -> Response:
    account = require_watchlist_account(request)
    try:
        tickers = get_account_store().add_ticker(account.user_id, payload.ticker)
    except AccountError as exc:
        raise account_http_error(exc) from exc
    return JSONResponse(
        {"tickers": tickers, "max_tickers": WATCHLIST_MAX_TICKERS},
        headers={"Cache-Control": "no-store"},
    )


@router.delete(
    "/watchlist/{ticker}",
    dependencies=[Depends(require_same_origin_request)],
)
def remove_watchlist_ticker(
    request: Request,
    ticker: Annotated[str, Path(min_length=1, max_length=16)],
) -> Response:
    account = require_watchlist_account(request)
    try:
        tickers = get_account_store().remove_ticker(account.user_id, ticker)
    except AccountError as exc:
        raise account_http_error(exc) from exc
    return JSONResponse(
        {"tickers": tickers, "max_tickers": WATCHLIST_MAX_TICKERS},
        headers={"Cache-Control": "no-store"},
    )
