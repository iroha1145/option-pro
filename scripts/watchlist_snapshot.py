#!/usr/bin/env python3
"""Seed, validate, and warm the persisted full-watchlist snapshot."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from typing import Any
import urllib.request


SNAPSHOT_VERSION = 1
SNAPSHOT_PARAMETERS = {"tickers": None}
SNAPSHOT_MAX_BYTES = 2 * 1024 * 1024
SNAPSHOT_MAX_AGE_SECONDS = 24 * 60 * 60
WATCHLIST_URL = "http://127.0.0.1:8000/api/stocks/watchlist"
TICKER_PATTERN = re.compile(
    r"^(?:\^[A-Z0-9][A-Z0-9.^_=-]{0,30}|[A-Z0-9][A-Z0-9.^_=-]{0,31})$"
)
TRANSPORT_FIELDS = frozenset(
    {
        "_client_cached",
        "_stale",
        "as_of",
        "source_status",
        "stale_age_seconds",
        "stale_reason",
    }
)


def finite_number(value: Any, *, positive: bool = False) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return False
    return math.isfinite(number) and (not positive or number > 0)


def finite_json_tree(value: Any, *, depth: int = 0) -> bool:
    if depth > 64:
        return False
    if value is None or isinstance(value, (bool, str, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(finite_json_tree(item, depth=depth + 1) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str)
            and finite_json_tree(item, depth=depth + 1)
            for key, item in value.items()
        )
    return False


def clean_payload(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not finite_json_tree(value):
        return None
    groups = value.get("groups")
    succeeded = value.get("succeeded")
    if (
        not isinstance(groups, list)
        or not groups
        or isinstance(succeeded, bool)
        or not isinstance(succeeded, int)
        or succeeded <= 0
    ):
        return None

    group_ids: set[str] = set()
    tickers: set[str] = set()
    for group in groups:
        if not isinstance(group, dict):
            return None
        group_id = group.get("id")
        group_name = group.get("name")
        stocks = group.get("stocks")
        if (
            not isinstance(group_id, str)
            or not group_id.strip()
            or group_id in group_ids
            or not isinstance(group_name, str)
            or not group_name.strip()
            or not isinstance(stocks, list)
            or not stocks
        ):
            return None
        group_ids.add(group_id)
        group_tickers: set[str] = set()
        for stock in stocks:
            if not isinstance(stock, dict):
                return None
            ticker = stock.get("ticker")
            name = stock.get("name")
            spark = stock.get("spark")
            if (
                not isinstance(ticker, str)
                or not TICKER_PATTERN.fullmatch(ticker)
                or ticker in group_tickers
                or not isinstance(name, str)
                or not name.strip()
                or not finite_number(stock.get("price"), positive=True)
                or not finite_number(stock.get("change_percent"))
                or not isinstance(spark, list)
                or not spark
                or len(spark) > 7
                or not all(finite_number(point, positive=True) for point in spark)
            ):
                return None
            group_tickers.add(ticker)
            tickers.add(ticker)
    if len(tickers) != succeeded:
        return None
    return {key: item for key, item in value.items() if key not in TRANSPORT_FIELDS}


def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def reject_non_finite(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")


def read_existing_snapshot(path: Path, *, now: float) -> dict[str, Any] | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        with path.open("rb") as handle:
            raw = handle.read(SNAPSHOT_MAX_BYTES + 1)
        if not raw or len(raw) > SNAPSHOT_MAX_BYTES:
            return None
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_non_finite,
        )
        if not isinstance(document, dict):
            return None
        version = document.get("version")
        saved_at = document.get("saved_at")
        if (
            isinstance(version, bool)
            or not isinstance(version, int)
            or version != SNAPSHOT_VERSION
            or document.get("parameters") != SNAPSHOT_PARAMETERS
            or isinstance(saved_at, bool)
            or not isinstance(saved_at, (int, float))
            or not math.isfinite(float(saved_at))
        ):
            return None
        saved_at = float(saved_at)
        if saved_at <= 0 or saved_at > now or saved_at + SNAPSHOT_MAX_AGE_SECONDS <= now:
            return None
        payload = clean_payload(document.get("payload"))
        if payload is None:
            return None
        return {"version": version, "saved_at": saved_at, "payload": payload}
    except (OSError, RecursionError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        return None


def write_snapshot(path: Path, *, payload: Any, saved_at: float) -> None:
    cleaned = clean_payload(payload)
    if cleaned is None:
        raise ValueError("watchlist snapshot payload is incomplete")
    if not math.isfinite(saved_at) or saved_at <= 0:
        raise ValueError("watchlist snapshot saved_at is invalid")
    if path.is_symlink():
        raise ValueError("watchlist snapshot path must not be a symlink")
    encoded = json.dumps(
        {
            "version": SNAPSHOT_VERSION,
            "saved_at": saved_at,
            "parameters": SNAPSHOT_PARAMETERS,
            "payload": cleaned,
        },
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > SNAPSHOT_MAX_BYTES:
        raise ValueError("watchlist snapshot exceeds the size limit")

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def fetch_watchlist(*, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(WATCHLIST_URL)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if clean_payload(payload) is None:
        raise ValueError("live watchlist returned an invalid snapshot")
    return payload


def saved_at_from_payload(payload: dict[str, Any], *, now: float) -> float:
    raw_as_of = payload.get("as_of")
    try:
        saved_at = datetime.fromisoformat(str(raw_as_of).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        saved_at = now
    saved_at = min(saved_at, now)
    if saved_at <= 0 or saved_at + SNAPSHOT_MAX_AGE_SECONDS <= now:
        raise ValueError("live watchlist snapshot is outside the bounded age")
    return saved_at


def seed(path: Path) -> int:
    try:
        payload = fetch_watchlist(timeout=120)
        now = time.time()
        write_snapshot(path, payload=payload, saved_at=saved_at_from_payload(payload, now=now))
        print(json.dumps({"watchlist_seed_snapshot": True, "source": "live"}))
        return 0
    except Exception as error:
        if read_existing_snapshot(path, now=time.time()) is not None:
            print(json.dumps({"watchlist_seed_snapshot": True, "source": "existing"}))
            return 0
        print(
            "watchlist warm deployment requires a valid pre-switch snapshot: "
            f"{type(error).__name__}",
            file=sys.stderr,
        )
        return 1


def validate(path: Path) -> int:
    if read_existing_snapshot(path, now=time.time()) is None:
        print("shared watchlist snapshot is missing or invalid", file=sys.stderr)
        return 1
    print(json.dumps({"watchlist_seed_snapshot": True, "source": "existing"}))
    return 0


def wait_for_fresh(*, timeout_seconds: int) -> int:
    deadline = time.monotonic() + timeout_seconds
    last_status = "not_attempted"
    attempts = max(1, timeout_seconds // 5 + 1)
    for attempt in range(attempts):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            payload = fetch_watchlist(timeout=max(1, min(20, remaining)))
            if payload.get("_stale") is not True:
                print(
                    json.dumps(
                        {
                            "watchlist_warm": True,
                            "attempted": payload.get("attempted"),
                            "succeeded": payload.get("succeeded"),
                            "as_of": payload.get("as_of"),
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                )
                return 0
            last_status = "bounded_snapshot_refresh_pending"
        except Exception as error:
            last_status = type(error).__name__
        if attempt + 1 < attempts:
            time.sleep(min(5, max(0, deadline - time.monotonic())))
    print(
        "Watchlist background refresh did not finish during deployment; "
        f"the bounded snapshot remains active ({last_status}).",
        file=sys.stderr,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("seed", "validate", "wait"))
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    data_dir = Path(os.environ.get("DATA_DIR", "").strip() or "/data")
    if not data_dir.is_absolute() or ".." in data_dir.parts:
        parser.error("DATA_DIR must be an absolute path without parent traversal")
    path = data_dir / "watchlist-snapshot-v1.json"
    if args.action == "seed":
        return seed(path)
    if args.action == "validate":
        return validate(path)
    return wait_for_fresh(timeout_seconds=max(1, min(args.timeout, 600)))


if __name__ == "__main__":
    raise SystemExit(main())
