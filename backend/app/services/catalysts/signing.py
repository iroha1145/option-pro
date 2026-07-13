from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections.abc import Iterable, Mapping
from typing import Any, Optional, Union
from urllib.parse import quote


EMPTY_BODY_SHA256 = hashlib.sha256(b"").hexdigest()


def sha256_hex(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _encode(value: Any) -> str:
    if value is None:
        text = ""
    elif isinstance(value, bool):
        text = "true" if value else "false"
    else:
        text = str(value)
    return quote(text, safe="-._~", encoding="utf-8", errors="strict")


def canonical_query(
    params: Optional[Union[Mapping[str, Any], Iterable[tuple[str, Any]]]],
) -> str:
    """Return the frozen RFC 3986 query representation.

    Repeated keys and empty values are retained.  Sequences in a mapping are
    expanded into repeated keys.  Sorting happens after percent encoding.
    Python's ``quote`` emits uppercase escape hex, as required by the contract.
    """

    if params is None:
        return ""
    raw_items = params.items() if isinstance(params, Mapping) else params
    encoded: list[tuple[str, str]] = []
    for key, value in raw_items:
        values = value if isinstance(value, (list, tuple)) else (value,)
        for item in values:
            encoded.append((_encode(key), _encode(item)))
    encoded.sort(key=lambda item: (item[0], item[1]))
    return "&".join(f"{key}={value}" for key, value in encoded)


def canonical_string(
    *,
    method: str,
    path: str,
    query: str,
    timestamp: str,
    nonce: str,
    body_sha256: str,
) -> str:
    if not path.startswith("/") or "?" in path or "#" in path:
        raise ValueError("path must be an absolute path without query or fragment")
    return "\n".join(
        (method.upper(), path, query, timestamp, nonce, body_sha256.lower())
    )


def sign_request(
    *,
    method: str,
    path: str,
    params: Optional[Union[Mapping[str, Any], Iterable[tuple[str, Any]]]],
    body: bytes,
    key_id: str,
    secret: str,
    timestamp: Optional[int] = None,
    nonce: Optional[str] = None,
) -> dict[str, str]:
    if not key_id or not secret:
        raise ValueError("key id and secret are required")
    timestamp_text = str(int(time.time()) if timestamp is None else int(timestamp))
    nonce_text = nonce or secrets.token_urlsafe(24)
    digest = sha256_hex(body)
    message = canonical_string(
        method=method,
        path=path,
        query=canonical_query(params),
        timestamp=timestamp_text,
        nonce=nonce_text,
        body_sha256=digest,
    )
    signature = hmac.new(
        secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return {
        "X-Optix-Key-Id": key_id,
        "X-Optix-Timestamp": timestamp_text,
        "X-Optix-Nonce": nonce_text,
        "X-Optix-Content-SHA256": digest,
        "X-Optix-Signature": signature,
    }
