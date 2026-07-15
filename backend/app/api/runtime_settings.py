"""Owner-facing API for non-secret runtime settings."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from app.services.runtime_settings import (
    RuntimeSettingsDocument,
    RuntimeSettingsPatch,
    RuntimeSettingsRevision,
    RuntimeSettingsRevisionNotFound,
    RuntimeSettingsStorageError,
    RuntimeSettingsStore,
    RuntimeSettingsValidationError,
    RuntimeSettingsVersionConflict,
    get_runtime_settings_store,
)


_MAX_RUNTIME_SETTINGS_BODY_BYTES = 16 * 1024
_FORBIDDEN_FIELD_FRAGMENTS = ("secret", "token", "password", "key")

router = APIRouter(prefix="/api/runtime-settings", tags=["runtime-settings"])


class _StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class RuntimeSettingsUpdateRequest(_StrictRequest):
    expected_version: int = Field(ge=1)
    settings: RuntimeSettingsPatch


class RuntimeSettingsRollbackRequest(_StrictRequest):
    expected_version: int = Field(ge=1)
    target_version: int = Field(ge=1)


class RuntimeSettingsHistoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revisions: list[RuntimeSettingsRevision]


def _contains_forbidden_field(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).casefold()
            if any(fragment in normalized for fragment in _FORBIDDEN_FIELD_FRAGMENTS):
                return True
            if _contains_forbidden_field(nested):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_forbidden_field(item) for item in value)
    return False


def _safe_error(status_code: int, code: str, message: str, **details: Any) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message, **details},
    )


async def _read_bounded_json(request: Request) -> dict[str, Any]:
    media_type = (
        request.headers.get("content-type", "").partition(";")[0].strip().casefold()
    )
    if media_type != "application/json":
        raise _safe_error(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "unsupported_media_type",
            "运行设置写请求只接受 application/json",
        )

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            length = int(content_length)
        except ValueError as exc:
            raise _safe_error(400, "invalid_body", "请求内容格式无效") from exc
        if length < 0:
            raise _safe_error(400, "invalid_body", "请求内容格式无效")
        if length > _MAX_RUNTIME_SETTINGS_BODY_BYTES:
            raise _safe_error(413, "body_too_large", "运行设置请求不能超过 16 KiB")

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > _MAX_RUNTIME_SETTINGS_BODY_BYTES:
            raise _safe_error(413, "body_too_large", "运行设置请求不能超过 16 KiB")
        chunks.append(chunk)
    try:
        payload = json.loads(b"".join(chunks))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _safe_error(400, "invalid_body", "请求内容格式无效") from exc
    if not isinstance(payload, dict):
        raise _safe_error(400, "invalid_body", "请求内容必须是 JSON 对象")
    if _contains_forbidden_field(payload):
        # Never echo the submitted field name or value in a response.
        raise _safe_error(
            400,
            "sensitive_field_rejected",
            "运行设置不能包含密钥、令牌、密码或其他敏感字段",
        )
    return payload


def _translate_store_error(exc: Exception) -> HTTPException:
    if isinstance(exc, RuntimeSettingsVersionConflict):
        return _safe_error(
            status.HTTP_409_CONFLICT,
            "version_conflict",
            "运行设置已被其他请求更新，请重新读取后再保存",
            current_version=exc.current_version,
        )
    if isinstance(exc, RuntimeSettingsRevisionNotFound):
        return _safe_error(
            status.HTTP_404_NOT_FOUND,
            "revision_not_found",
            "找不到可回滚的设置版本",
        )
    if isinstance(exc, RuntimeSettingsValidationError):
        return _safe_error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "invalid_settings",
            "运行设置的格式或取值无效",
        )
    return _safe_error(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "settings_storage_unavailable",
        "运行设置暂时无法读取或保存",
    )


@router.get("", response_model=RuntimeSettingsDocument)
def read_runtime_settings(
    store: RuntimeSettingsStore = Depends(get_runtime_settings_store),
) -> RuntimeSettingsDocument:
    try:
        return store.read()
    except RuntimeSettingsStorageError as exc:
        raise _translate_store_error(exc) from exc


@router.get("/history", response_model=RuntimeSettingsHistoryResponse)
def read_runtime_settings_history(
    store: RuntimeSettingsStore = Depends(get_runtime_settings_store),
) -> RuntimeSettingsHistoryResponse:
    try:
        return RuntimeSettingsHistoryResponse(revisions=store.history())
    except RuntimeSettingsStorageError as exc:
        raise _translate_store_error(exc) from exc


@router.put("", response_model=RuntimeSettingsDocument)
async def update_runtime_settings(
    request: Request,
    store: RuntimeSettingsStore = Depends(get_runtime_settings_store),
) -> RuntimeSettingsDocument:
    payload = await _read_bounded_json(request)
    try:
        update = RuntimeSettingsUpdateRequest.model_validate(payload)
    except ValidationError as exc:
        raise _safe_error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "invalid_settings",
            "运行设置的格式或取值无效",
        ) from exc
    try:
        return await run_in_threadpool(
            store.update,
            update.settings,
            expected_version=update.expected_version,
        )
    except (
        RuntimeSettingsStorageError,
        RuntimeSettingsValidationError,
        RuntimeSettingsVersionConflict,
    ) as exc:
        raise _translate_store_error(exc) from exc


@router.post("/rollback", response_model=RuntimeSettingsDocument)
async def rollback_runtime_settings(
    request: Request,
    store: RuntimeSettingsStore = Depends(get_runtime_settings_store),
) -> RuntimeSettingsDocument:
    payload = await _read_bounded_json(request)
    try:
        rollback = RuntimeSettingsRollbackRequest.model_validate(payload)
    except ValidationError as exc:
        raise _safe_error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "invalid_settings",
            "运行设置的格式或取值无效",
        ) from exc
    try:
        return await run_in_threadpool(
            store.rollback,
            rollback.target_version,
            expected_version=rollback.expected_version,
        )
    except (
        RuntimeSettingsRevisionNotFound,
        RuntimeSettingsStorageError,
        RuntimeSettingsVersionConflict,
    ) as exc:
        raise _translate_store_error(exc) from exc
