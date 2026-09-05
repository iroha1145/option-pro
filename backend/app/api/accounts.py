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
import math
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.access import (
    request_is_owner_session,
    request_uses_https,
    require_same_origin_json,
    require_same_origin_request,
)
from app.services.accounts import (
    Account,
    AccountError,
    DRAWING_TEXT_MAX,
    DRAWINGS_PER_RANGE_MAX,
    WATCHLIST_MAX_TICKERS,
    get_account_store,
    normalize_chart_adjustment,
    normalize_chart_range,
    normalize_ticker,
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
    """Rate-limit key for one visitor.

    ``request.client.host`` is the socket peer, which behind nginx, Caddy or a
    Cloudflare tunnel is the proxy container -- one bucket for every visitor on
    the deployment, so ten wrong passwords from one person would put everyone
    into the five-minute cooldown. Owner login already resolves the address
    through the trusted-proxy allowlist; both identity systems now agree.
    """

    from app.api.access import _runtime

    address = _runtime(request).request_address(request)
    return str(address) if address is not None else "unknown"


def _prune(bucket: dict, now: float) -> None:
    """Drop expired entries, then evict the oldest if still over capacity.

    Clearing the whole table at the threshold wiped every visitor's cooldown at
    once, which is exactly the state an attacker wants: fill the table, and the
    next insert resets everyone's failure count.
    """

    if len(bucket) < _RATE_BUCKET_LIMIT:
        return
    for key, value in list(bucket.items()):
        if _entry_expired(value, now):
            del bucket[key]
    overflow = len(bucket) - _RATE_BUCKET_LIMIT + 1
    if overflow <= 0:
        return
    for key, _ in sorted(bucket.items(), key=lambda item: _entry_last_seen(item[1]))[
        :overflow
    ]:
        del bucket[key]


def _entry_last_seen(value: object) -> float:
    """Newest activity stamp of a bucket entry, whatever its shape."""

    if isinstance(value, list):
        return max(value) if value else 0.0
    if isinstance(value, tuple) and value:
        # (count, started_at, blocked_until) -- a live cooldown outranks its start.
        return max(float(part) for part in value[1:] if isinstance(part, (int, float)))
    return 0.0


def _entry_expired(value: object, now: float) -> bool:
    if isinstance(value, list):
        return not any(stamp > now - _REGISTER_WINDOW_SECONDS for stamp in value)
    if isinstance(value, tuple) and len(value) == 3:
        _count, started_at, blocked_until = value
        return blocked_until <= now and started_at < now - _LOGIN_FAILURE_WINDOW_SECONDS
    return False


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


# 绘图相关的码全部落在这里：客户端按 code 分支，不按状态码。409 上挤着配额和
# 版本冲突两类语义，只有 revision_conflict 才是「别的设备改过了」，配额满不能走
# 重载对话框那条路——那条路会重放注定失败的创建。
_ERROR_STATUS = {
    "username_taken": status.HTTP_409_CONFLICT,
    "registration_closed": status.HTTP_503_SERVICE_UNAVAILABLE,
    "invalid_credentials": status.HTTP_401_UNAUTHORIZED,
    "watchlist_full": status.HTTP_409_CONFLICT,
    "revision_conflict": status.HTTP_409_CONFLICT,
    "scope_revision_conflict": status.HTTP_409_CONFLICT,
    "drawing_id_conflict": status.HTTP_409_CONFLICT,
    "drawings_range_full": status.HTTP_409_CONFLICT,
    "drawings_full": status.HTTP_409_CONFLICT,
    "drawing_not_found": status.HTTP_404_NOT_FOUND,
    "scope_mismatch": status.HTTP_400_BAD_REQUEST,
}

_ERROR_MESSAGE = {
    "username_required": "请填写用户名",
    "username_too_long": "用户名过长",
    "username_invalid_characters": "用户名不能包含空格或控制字符",
    "username_reserved": "该用户名已被保留，请换一个",
    "username_taken": "该用户名已被占用",
    "password_required": "请填写密码",
    "password_too_short": "新密码至少需要 15 个字符，可使用容易记住的长短语",
    "password_too_common": "该密码过于常见，请换一个更难猜测的长短语",
    "password_too_long": "密码过长",
    "password_invalid_characters": "密码包含不支持的字符",
    "registration_closed": "注册名额已满",
    "invalid_credentials": "用户名或密码不正确",
    "invalid_ticker": "股票代码格式不正确",
    "watchlist_full": f"自选最多 {WATCHLIST_MAX_TICKERS} 只股票",
    "invalid_range": "图表周期不受支持",
    "invalid_adjustment": "复权口径不受支持",
    "invalid_kind": "绘图类型不受支持",
    "invalid_drawing_id": "绘图编号格式不正确",
    "invalid_color": "颜色必须是十六进制或调色板值",
    "invalid_anchors": "锚点数量或坐标无效",
    "invalid_price": "价格必须是有限正数",
    "invalid_time": "时间必须是有效的 ISO 时间",
    "invalid_style": "线条样式不受支持",
    "invalid_text": "文字内容无效",
    "text_too_long": f"文字最多 {DRAWING_TEXT_MAX} 个字符",
    "invalid_payload": "绘图数据格式无效",
    "payload_too_large": "绘图数据过大",
    "drawing_not_found": "绘图不存在",
    "revision_conflict": "绘图已被其他设备更新，请重新加载",
    "scope_revision_conflict": "当前范围已被其他设备更新，请重新加载",
    "drawing_id_conflict": "该绘图编号已用于不同内容或不同范围",
    "drawings_range_full": f"当前范围最多 {DRAWINGS_PER_RANGE_MAX} 个绘图",
    "drawings_full": "账户绘图数量已达上限",
    "scope_mismatch": "不能把绘图移动到其他标的或周期",
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


def require_personal_account(request: Request) -> Account:
    """Resolve the personal-data principal (watchlist, drawings, …).

    A customer cookie wins when present, so an owner who is also signed in as a
    customer in the same browser still edits the customer data they can see. An
    owner session otherwise resolves to the single reserved owner row. Order
    matters only for that overlap; the two never share personal data.
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


require_watchlist_account = require_personal_account


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


DrawingKind = Literal[
    "horizontal",
    "segment",
    "ray",
    "channel",
    "rectangle",
    "fibonacci",
    "text",
]
ChartRangeLiteral = Literal["5m", "15m", "1h", "1d", "1w"]
DashLiteral = Literal["solid", "dashed", "dotted"]


class DrawingAnchorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time: str = Field(min_length=1, max_length=64)
    barKey: str = Field(min_length=1, max_length=64)
    price: float

    @field_validator("price")
    @classmethod
    def price_must_be_finite_positive(cls, value: float) -> float:
        if not math.isfinite(value) or value <= 0:
            raise ValueError("invalid_price")
        return value


class DrawingStyleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    color: str = Field(min_length=1, max_length=16)
    width: Literal[1, 2, 3, 4]
    dash: DashLiteral
    fillOpacity: float | None = None

    @field_validator("fillOpacity")
    @classmethod
    def fill_must_be_unit_interval(cls, value: float | None) -> float | None:
        if value is None:
            return value
        if not math.isfinite(value) or value < 0 or value > 1:
            raise ValueError("invalid_style")
        return value


class ChartDrawingFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal[1]
    id: str = Field(min_length=36, max_length=36)
    ticker: str = Field(min_length=1, max_length=16)
    range: ChartRangeLiteral
    adjustment: Literal["raw"] = "raw"
    kind: DrawingKind
    anchors: list[DrawingAnchorBody] = Field(min_length=1, max_length=3)
    style: DrawingStyleBody
    text: str | None = Field(default=None, max_length=DRAWING_TEXT_MAX)
    locked: bool = False
    hidden: bool = False
    zOrder: int = 0


class ChartDrawingCreateBody(ChartDrawingFields):
    expected_scope_revision: int = Field(ge=0)


class ChartDrawingUpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal[1]
    id: str | None = Field(default=None, min_length=36, max_length=36)
    revision: int = Field(ge=1)
    expected_scope_revision: int = Field(ge=0)
    ticker: str = Field(min_length=1, max_length=16)
    range: ChartRangeLiteral
    adjustment: Literal["raw"] = "raw"
    kind: DrawingKind
    anchors: list[DrawingAnchorBody] = Field(min_length=1, max_length=3)
    style: DrawingStyleBody
    text: str | None = Field(default=None, max_length=DRAWING_TEXT_MAX)
    locked: bool = False
    hidden: bool = False
    zOrder: int = 0


class ChartDrawingReplaceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal[1]
    expected_scope_revision: int = Field(ge=0)
    drawings: list[ChartDrawingFields] = Field(max_length=DRAWINGS_PER_RANGE_MAX)


def _scope_query(
    ticker: str,
    chart_range: str,
    adjustment: str,
) -> tuple[str, str, str]:
    try:
        return (
            normalize_ticker(ticker),
            normalize_chart_range(chart_range),
            normalize_chart_adjustment(adjustment),
        )
    except AccountError as exc:
        raise account_http_error(exc) from exc


@router.get("/chart-drawings")
def list_chart_drawings(
    request: Request,
    ticker: Annotated[str, Query(min_length=1, max_length=16)],
    range: Annotated[ChartRangeLiteral, Query()],
    adjustment: Annotated[Literal["raw"], Query()] = "raw",
) -> Response:
    account = require_personal_account(request)
    symbol, range_key, adj = _scope_query(ticker, range, adjustment)
    try:
        drawings, scope_revision = get_account_store().list_drawings_page(
            account.user_id, symbol, range_key, adj
        )
    except AccountError as exc:
        raise account_http_error(exc) from exc
    return JSONResponse(
        {
            "drawings": drawings,
            "max_per_range": DRAWINGS_PER_RANGE_MAX,
            "scope_revision": scope_revision,
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post(
    "/chart-drawings/replace",
    dependencies=[Depends(require_same_origin_json)],
)
def replace_chart_drawings(
    request: Request,
    payload: Annotated[ChartDrawingReplaceBody, Body()],
    ticker: Annotated[str, Query(min_length=1, max_length=16)],
    range: Annotated[ChartRangeLiteral, Query()],
    adjustment: Annotated[Literal["raw"], Query()] = "raw",
) -> Response:
    account = require_personal_account(request)
    symbol, range_key, adj = _scope_query(ticker, range, adjustment)
    body = payload.model_dump()
    expected = int(body.pop("expected_scope_revision"))
    try:
        drawings, scope_revision = get_account_store().replace_drawings_in_scope(
            account.user_id,
            symbol,
            range_key,
            adj,
            body["drawings"],
            expected_scope_revision=expected,
        )
    except AccountError as exc:
        raise account_http_error(exc) from exc
    return JSONResponse(
        {
            "drawings": drawings,
            "max_per_range": DRAWINGS_PER_RANGE_MAX,
            "scope_revision": scope_revision,
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post(
    "/chart-drawings",
    dependencies=[Depends(require_same_origin_json)],
)
def create_chart_drawing(
    request: Request,
    payload: Annotated[ChartDrawingCreateBody, Body()],
) -> Response:
    account = require_personal_account(request)
    body = payload.model_dump()
    expected = int(body.pop("expected_scope_revision"))
    try:
        created, scope_revision = get_account_store().create_drawing(
            account.user_id,
            body,
            expected_scope_revision=expected,
        )
    except AccountError as exc:
        raise account_http_error(exc) from exc
    return JSONResponse(
        {**created, "scope_revision": scope_revision},
        status_code=status.HTTP_201_CREATED,
        headers={"Cache-Control": "no-store"},
    )


@router.put(
    "/chart-drawings/{drawing_id}",
    dependencies=[Depends(require_same_origin_json)],
)
def update_chart_drawing(
    request: Request,
    drawing_id: Annotated[str, Path(min_length=36, max_length=36)],
    payload: Annotated[ChartDrawingUpdateBody, Body()],
) -> Response:
    account = require_personal_account(request)
    body = payload.model_dump()
    expected = int(body.pop("revision"))
    expected_scope = int(body.pop("expected_scope_revision"))
    body_id = body.pop("id", None)
    if body_id and body_id.lower() != drawing_id.lower():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_drawing_id", "message": "绘图编号与路径不一致"},
        )
    try:
        updated, scope_revision = get_account_store().update_drawing(
            account.user_id,
            drawing_id,
            body,
            expected_revision=expected,
            expected_scope_revision=expected_scope,
        )
    except AccountError as exc:
        raise account_http_error(exc) from exc
    return JSONResponse(
        {**updated, "scope_revision": scope_revision},
        headers={"Cache-Control": "no-store"},
    )


@router.delete(
    "/chart-drawings/{drawing_id}",
    dependencies=[Depends(require_same_origin_request)],
)
def delete_chart_drawing(
    request: Request,
    drawing_id: Annotated[str, Path(min_length=36, max_length=36)],
    ticker: Annotated[str, Query(min_length=1, max_length=16)],
    range: Annotated[ChartRangeLiteral, Query()],
    expected_scope_revision: Annotated[int, Query(ge=0)],
    adjustment: Annotated[Literal["raw"], Query()] = "raw",
) -> Response:
    account = require_personal_account(request)
    symbol, range_key, adj = _scope_query(ticker, range, adjustment)
    try:
        _deleted, scope_revision = get_account_store().delete_drawing(
            account.user_id,
            drawing_id,
            ticker=symbol,
            chart_range=range_key,
            adjustment=adj,
            expected_scope_revision=expected_scope_revision,
        )
    except AccountError as exc:
        raise account_http_error(exc) from exc
    return JSONResponse(
        {"ok": True, "scope_revision": scope_revision},
        headers={"Cache-Control": "no-store"},
    )


@router.delete(
    "/chart-drawings",
    dependencies=[Depends(require_same_origin_request)],
)
def delete_chart_drawings_in_scope(
    request: Request,
    ticker: Annotated[str, Query(min_length=1, max_length=16)],
    range: Annotated[ChartRangeLiteral, Query()],
    expected_scope_revision: Annotated[int, Query(ge=0)],
    adjustment: Annotated[Literal["raw"], Query()] = "raw",
) -> Response:
    account = require_personal_account(request)
    symbol, range_key, adj = _scope_query(ticker, range, adjustment)
    try:
        deleted, scope_revision = get_account_store().delete_drawings_in_scope(
            account.user_id,
            symbol,
            range_key,
            adj,
            expected_scope_revision=expected_scope_revision,
        )
    except AccountError as exc:
        raise account_http_error(exc) from exc
    return JSONResponse(
        {"deleted": deleted, "scope_revision": scope_revision},
        headers={"Cache-Control": "no-store"},
    )
