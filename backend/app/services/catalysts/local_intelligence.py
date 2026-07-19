from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import sqlite3
import uuid
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Literal, Mapping, Sequence
from urllib.parse import quote
from zoneinfo import ZoneInfo

from app.access import current_request_is_owner
from app.services.ai_jobs import runtime as ai_runtime
from app.services.ai_jobs.models import (
    validate_result,
    validate_simplified_chinese_text,
)
from app.services.ai_jobs.repository import AIJobRepository

from .errors import CatalystError, InvalidCursorError


Mode = Literal["off", "read", "manual", "scheduled"]
SubmissionSource = Literal["manual", "scheduled"]
MODEL = "gpt-5.6-terra"
REASONING = "max"
EXECUTION_MODE = "background"
NEWS_PROMPT_VERSION = "news-impact-zh-cn-v6"
FOCUS_PROMPT_VERSION = "market-focus-zh-cn-v4"
NEWS_RESULT_AUDIT_VERSION = "news-result-validation-v2"
NEWS_PROMPT_FAMILY_RE = re.compile(r"^news-impact-zh-cn-v[1-9][0-9]*$")
NEWS_SCHEMA_FAMILY_RE = re.compile(r"^news_impact_zh_cn_v[1-9][0-9]*$")
FOCUS_PROMPT_FAMILY_RE = re.compile(r"^market-focus-zh-cn-v[1-9][0-9]*$")
FOCUS_SCHEMA_FAMILY_RE = re.compile(r"^market_focus_zh_cn_v[1-9][0-9]*$")
SCHEMA_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TITLE_WAITING = "中文标题等待生成"
SUMMARY_WAITING = "中文摘要等待生成"
HOTSPOT_WAITING = "热点标题等待中文分析"
AMBIGUOUS_TICKERS = frozenset({"AI", "ON", "CAT"})
TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-^]{0,11}$")
SCHEMA_VERSION = "optix-local-catalyst-v3"
NEWS_RESULT_CONTRACT_ID = (
    "news-impact-result:"
    f"{ai_runtime.RESULT_VALIDATION_CONTRACT_VERSION}:"
    f"{NEWS_RESULT_AUDIT_VERSION}"
)
FOCUS_RESULT_CONTRACT_ID = (
    "market-focus-result:"
    f"{ai_runtime.RESULT_VALIDATION_CONTRACT_VERSION}"
)
NEWS_LINK_AUDIT_CONTRACT_ID = "news-impact-link-v1"
FOCUS_BINDING_AUDIT_CONTRACT_ID = "market-focus-binding-v1"
SCHEDULE_CLAIM_TTL_SECONDS = 10 * 60
SCHEDULED_NEWS_BATCH_SIZE = 20
SCHEDULED_QUEUE_SOFT_LIMIT = 40
SCHEDULED_NEWS_WINDOW_HOURS = 72
SCHEDULED_NEWS_MAX_ATTEMPTS = 3
SCHEDULED_FOCUS_MAX_ATTEMPTS = 3
SCHEDULED_FOCUS_EVENT_LIMIT = 20
SCHEDULED_TRANSIENT_AI_ERRORS = frozenset(
    {
        "ai_empty_response",
        "provider_failed",
        "provider_incomplete",
        "provider_rate_limited",
        "provider_response_expired",
        "provider_server_error",
        "provider_unavailable",
    }
)
SCHEDULED_NEWS_RETRYABLE_ERRORS = SCHEDULED_TRANSIENT_AI_ERRORS
SCHEDULED_FOCUS_RETRYABLE_ERRORS = SCHEDULED_TRANSIENT_AI_ERRORS
MANUAL_REFRESH_CLAIM_TTL_SECONDS = 10 * 60
MANUAL_REFRESH_TYPES = ("news", "calendar", "source_health")
ANALYSIS_LINK_BUSY_TIMEOUT_MS = 250


_SCHEMA = """
CREATE TABLE IF NOT EXISTS catalyst_local_schema (
    version TEXT PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS catalyst_local_news_revisions (
    news_id INTEGER NOT NULL CHECK(news_id >= 1),
    change_sequence INTEGER NOT NULL CHECK(change_sequence >= 1),
    content_hash TEXT NOT NULL,
    source TEXT NOT NULL,
    raw_title TEXT NOT NULL,
    raw_summary TEXT,
    url TEXT NOT NULL,
    image_url TEXT,
    published_at TEXT,
    fetched_at TEXT NOT NULL,
    source_available_at TEXT NOT NULL,
    source_tickers_json TEXT NOT NULL,
    canonical_tickers_json TEXT NOT NULL,
    source_names_json TEXT NOT NULL,
    source_count INTEGER NOT NULL CHECK(source_count >= 1),
    ingested_at TEXT NOT NULL,
    PRIMARY KEY(news_id,change_sequence,content_hash)
);
CREATE INDEX IF NOT EXISTS idx_local_news_available
    ON catalyst_local_news_revisions(source_available_at DESC,news_id DESC);

CREATE TABLE IF NOT EXISTS catalyst_local_analysis_links (
    news_id INTEGER NOT NULL,
    change_sequence INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    job_id TEXT NOT NULL UNIQUE,
    result_json TEXT,
    result_available_at TEXT,
    verified_at TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(news_id,change_sequence,content_hash,job_id),
    FOREIGN KEY(news_id,change_sequence,content_hash)
      REFERENCES catalyst_local_news_revisions(news_id,change_sequence,content_hash)
);
CREATE INDEX IF NOT EXISTS idx_local_analysis_revision
    ON catalyst_local_analysis_links(news_id,change_sequence,content_hash,result_available_at DESC);

CREATE TABLE IF NOT EXISTS catalyst_local_analysis_result_audit (
    job_id TEXT NOT NULL,
    contract_id TEXT NOT NULL,
    result_sha256 TEXT NOT NULL CHECK(length(result_sha256)=64),
    outcome TEXT NOT NULL CHECK(outcome IN ('accepted','rejected')),
    reason TEXT,
    result_json TEXT NOT NULL,
    result_available_at TEXT,
    verified_at TEXT,
    observed_at TEXT NOT NULL,
    PRIMARY KEY(job_id,contract_id,result_sha256)
);
CREATE INDEX IF NOT EXISTS idx_local_analysis_result_audit_outcome
    ON catalyst_local_analysis_result_audit(contract_id,outcome,observed_at);

CREATE TABLE IF NOT EXISTS catalyst_local_event_groups (
    event_group_id TEXT NOT NULL,
    event_group_version INTEGER NOT NULL CHECK(event_group_version >= 1),
    input_hash TEXT NOT NULL CHECK(length(input_hash)=64),
    event_type TEXT NOT NULL,
    representative_news_id INTEGER NOT NULL,
    representative_change_sequence INTEGER NOT NULL,
    representative_content_hash TEXT NOT NULL,
    representative_title_zh TEXT NOT NULL,
    representative_summary_zh TEXT NOT NULL,
    first_published_at TEXT,
    last_published_at TEXT,
    available_at TEXT NOT NULL,
    source_count INTEGER NOT NULL CHECK(source_count >= 1),
    source_names_json TEXT NOT NULL,
    validated_tickers_json TEXT NOT NULL,
    news_identities_json TEXT NOT NULL,
    hot_score REAL NOT NULL CHECK(hot_score >= 0 AND hot_score <= 100),
    component_scores_json TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(event_group_id,event_group_version),
    UNIQUE(event_group_id,input_hash)
);

CREATE TABLE IF NOT EXISTS catalyst_local_hotspot_revisions (
    prepared_revision INTEGER PRIMARY KEY CHECK(prepared_revision >= 1),
    input_hash TEXT NOT NULL UNIQUE CHECK(length(input_hash)=64),
    prepared_at TEXT NOT NULL,
    data_through TEXT,
    item_count INTEGER NOT NULL CHECK(item_count >= 0)
);
CREATE TABLE IF NOT EXISTS catalyst_local_hotspot_items (
    prepared_revision INTEGER NOT NULL
      REFERENCES catalyst_local_hotspot_revisions(prepared_revision),
    ordinal INTEGER NOT NULL CHECK(ordinal >= 1),
    event_group_id TEXT NOT NULL,
    event_group_version INTEGER NOT NULL,
    PRIMARY KEY(prepared_revision,ordinal),
    UNIQUE(prepared_revision,event_group_id),
    FOREIGN KEY(event_group_id,event_group_version)
      REFERENCES catalyst_local_event_groups(event_group_id,event_group_version)
);

CREATE TABLE IF NOT EXISTS catalyst_local_focus_cycles (
    cycle_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    prepared_revision INTEGER NOT NULL,
    snapshot_as_of TEXT NOT NULL,
    input_hash TEXT NOT NULL CHECK(length(input_hash)=64),
    job_id TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    result_json TEXT,
    error_code TEXT,
    retry_of_cycle_id TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_local_focus_latest
    ON catalyst_local_focus_cycles(created_at DESC);

CREATE TABLE IF NOT EXISTS catalyst_local_focus_result_audit (
    cycle_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    contract_id TEXT NOT NULL,
    result_sha256 TEXT NOT NULL CHECK(length(result_sha256)=64),
    outcome TEXT NOT NULL CHECK(outcome IN ('accepted','rejected')),
    reason TEXT,
    result_json TEXT NOT NULL,
    result_available_at TEXT,
    observed_at TEXT NOT NULL,
    PRIMARY KEY(cycle_id,job_id,contract_id,result_sha256)
);
CREATE INDEX IF NOT EXISTS idx_local_focus_result_audit_outcome
    ON catalyst_local_focus_result_audit(contract_id,outcome,observed_at);

CREATE TABLE IF NOT EXISTS catalyst_local_refresh_requests (
    request_id TEXT PRIMARY KEY,
    requested_at TEXT NOT NULL,
    consumed_at TEXT
);

CREATE TABLE IF NOT EXISTS catalyst_local_manual_operations (
    request_id TEXT PRIMARY KEY,
    operation_type TEXT NOT NULL CHECK(operation_type IN (
        'news','calendar','source_health'
    )),
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
        'queued','running','completed','failed'
    )),
    requested_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    cooldown_until TEXT,
    error_code TEXT,
    UNIQUE(operation_type,idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_local_manual_operations_due
    ON catalyst_local_manual_operations(status,requested_at);

CREATE TABLE IF NOT EXISTS catalyst_local_schedule_runs (
    slot_key TEXT PRIMARY KEY,
    scheduled_for TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    queued INTEGER NOT NULL CHECK(queued >= 0),
    skipped INTEGER NOT NULL CHECK(skipped >= 0)
);

CREATE TABLE IF NOT EXISTS catalyst_local_legacy_import_audit (
    legacy_identity TEXT PRIMARY KEY,
    outcome TEXT NOT NULL,
    reason TEXT,
    observed_at TEXT NOT NULL
);
""".strip()
SCHEMA_CHECKSUM = hashlib.sha256(_SCHEMA.encode("utf-8")).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utc_now()).astimezone(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _recent_analysis_count(
    items: Sequence[Mapping[str, Any]],
    *,
    as_of: datetime,
) -> int:
    cutoff = as_of - timedelta(hours=24)
    return sum(
        1
        for item in items
        if (completed_at := _parse_time(item.get("analyzed_at"))) is not None
        and cutoff <= completed_at <= as_of
    )


def _is_sqlite_write_contention(error: BaseException) -> bool:
    if not isinstance(error, sqlite3.OperationalError):
        return False
    code = getattr(error, "sqlite_errorcode", None)
    if isinstance(code, int) and (code & 0xFF) in {
        sqlite3.SQLITE_BUSY,
        sqlite3.SQLITE_LOCKED,
    }:
        return True
    message = str(error).casefold()
    return "database is locked" in message or "database table is locked" in message


def _minute_bucket(value: datetime) -> str:
    observed = value.astimezone(timezone.utc).replace(second=0, microsecond=0)
    return _iso(observed)


def _hour_bucket(value: datetime) -> str:
    observed = value.astimezone(timezone.utc).replace(
        minute=0,
        second=0,
        microsecond=0,
    )
    return _iso(observed)


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _sha(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _news_result_identity_matches(
    result: Any,
    payload: Mapping[str, Any],
) -> bool:
    if not isinstance(result, dict):
        return False
    if (
        result.get("news_id") != payload.get("news_id")
        or result.get("change_sequence") != payload.get("change_sequence")
        or result.get("content_hash") != payload.get("content_hash")
    ):
        return False
    allowed_tickers = {
        str(ticker).strip().upper()
        for ticker in payload.get("allowed_tickers") or []
        if isinstance(ticker, str) and str(ticker).strip()
    }
    affected = result.get("affected_stocks")
    if not isinstance(affected, list) or any(
        not isinstance(item, dict) for item in affected
    ):
        return False
    output_tickers = {
        str(item.get("ticker") or "").strip().upper()
        for item in affected
    }
    return "" not in output_tickers and output_tickers <= allowed_tickers


def _market_focus_result_identity_matches(
    result: Any,
    payload: Mapping[str, Any],
) -> bool:
    if not isinstance(result, dict):
        return False
    result_as_of = _parse_time(result.get("as_of"))
    payload_as_of = _parse_time(payload.get("as_of"))
    if (
        result.get("cycle_id") != payload.get("cycle_id")
        or result.get("input_hash") != payload.get("input_hash")
        or result_as_of is None
        or payload_as_of is None
        or result_as_of != payload_as_of
    ):
        return False
    allowed_event_ids = {
        str(value).strip()
        for value in payload.get("allowed_event_group_ids") or []
        if isinstance(value, str) and str(value).strip()
    }
    allowed_tickers = {
        str(value).strip().upper()
        for value in payload.get("allowed_tickers") or []
        if isinstance(value, str) and str(value).strip()
    }
    dominant_events = result.get("dominant_events")
    assessments = result.get("focus_ticker_assessments")
    if (
        not isinstance(dominant_events, list)
        or not isinstance(assessments, list)
        or any(not isinstance(item, dict) for item in dominant_events)
        or any(not isinstance(item, dict) for item in assessments)
    ):
        return False
    output_event_ids = {
        str(item.get("event_group_id") or "").strip()
        for item in dominant_events
    }
    output_tickers: set[str] = set()
    for item in assessments:
        output_tickers.add(str(item.get("ticker") or "").strip().upper())
        for field in ("supporting_event_ids", "conflicting_event_ids"):
            values = item.get(field)
            if not isinstance(values, list) or any(
                not isinstance(value, str) for value in values
            ):
                return False
            output_event_ids.update(str(value).strip() for value in values)
    return (
        "" not in output_event_ids
        and "" not in output_tickers
        and output_event_ids <= allowed_event_ids
        and output_tickers <= allowed_tickers
    )


def _public_chinese_text(value: Any, fallback: str) -> str:
    try:
        return validate_simplified_chinese_text(str(value or ""), None)
    except ValueError:
        return fallback


_CALENDAR_INITIALISM_RE = re.compile(
    r"(?<![A-Za-z0-9])([A-Z][A-Z0-9&./+\-]{1,11})(?![A-Za-z0-9])"
)
_CALENDAR_NON_EVENT_CODES = frozenset(
    {
        "AUD",
        "CAD",
        "CHF",
        "CNY",
        "EU",
        "EUR",
        "GBP",
        "JPY",
        "NZD",
        "UK",
        "US",
        "USA",
        "USD",
    }
)
_CALENDAR_GENERIC_ENGLISH_WORDS = frozenset(
    {
        "claims",
        "confidence",
        "consumer",
        "crude",
        "decision",
        "employment",
        "housing",
        "index",
        "inventories",
        "inventory",
        "manufacturing",
        "oil",
        "rate",
        "release",
        "report",
        "retail",
        "sales",
        "services",
        "starts",
    }
)


def _public_calendar_title(value: Any) -> str:
    """Keep useful event acronyms while removing untranslated English prose."""

    raw = str(value or "").strip()
    if not raw or "<" in raw or ">" in raw:
        return "经济日历事件"
    try:
        return validate_simplified_chinese_text(raw, None)
    except ValueError:
        pass
    acronyms = [
        match.group(1)
        for match in _CALENDAR_INITIALISM_RE.finditer(raw)
        if match.group(1) not in _CALENDAR_NON_EVENT_CODES
        and match.group(1).casefold() not in _CALENDAR_GENERIC_ENGLISH_WORDS
        and (not match.group(1).isalpha() or len(match.group(1)) <= 5)
    ]
    if not acronyms:
        return "经济日历事件"
    acronym = acronyms[0]
    folded = raw.casefold()
    if (
        any(word in folded for word in ("inventory", "inventories", "stockpile"))
        and any(
            word in folded
            for word in ("crude", "oil", "petroleum", "gas", "energy")
        )
    ):
        category = "能源库存数据"
    elif any(
        word in folded
        for word in ("cpi", "pce", "inflation", "price index")
    ):
        category = "通胀数据"
    elif any(
        word in folded
        for word in ("employment", "jobs", "payroll", "unemployment", "adp", "jolts")
    ):
        category = "就业数据"
    elif any(
        word in folded
        for word in ("interest rate", "rate decision", "fomc", "central bank")
    ):
        category = "利率事件"
    elif any(word in folded for word in ("manufacturing", "pmi", "ism")):
        category = "制造业数据"
    else:
        category = "经济数据"
    return f"{acronym}{category}"


def _cursor_encode(offset: int, anchor: str, query_hash: str) -> str:
    raw = _json(
        {"v": 1, "offset": offset, "anchor": anchor, "query_hash": query_hash}
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _cursor_decode(value: str | None, query_hash: str) -> tuple[int, str | None]:
    if not value:
        return 0, None
    try:
        padding = "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(value + padding))
        if (
            not isinstance(payload, dict)
            or payload.get("v") != 1
            or payload.get("query_hash") != query_hash
            or _parse_time(payload.get("anchor")) is None
            or type(payload.get("offset")) is not int
            or payload["offset"] < 0
        ):
            raise ValueError
        return int(payload["offset"]), str(payload["anchor"])
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        raise InvalidCursorError() from None


def _event_type(title: str, summary: str | None) -> str:
    text = f"{title} {summary or ''}".casefold()
    groups = (
        ("earnings", ("earnings", "revenue", "profit", "guidance", "财报", "业绩")),
        ("macro", ("federal reserve", "inflation", "interest rate", "fed ", "央行", "通胀", "利率")),
        ("merger", ("merger", "acquisition", "takeover", "收购", "合并")),
        ("regulatory", ("regulator", "regulation", "antitrust", "lawsuit", "监管", "反垄断", "诉讼")),
        ("product", ("launch", "product", "approval", "release", "发布", "获批", "产品")),
        ("commodity", ("oil", "gold", "copper", "commodity", "原油", "黄金", "大宗商品")),
    )
    for name, needles in groups:
        if any(needle in text for needle in needles):
            return name
    return "company"


def _title_tokens(value: str) -> frozenset[str]:
    tokens: set[str] = set()
    for raw in re.findall(r"[a-z0-9]+|[\u3400-\u9fff]{1,8}", value.casefold()):
        token = raw
        if len(token) > 4 and token.endswith("ies"):
            token = token[:-3] + "y"
        elif len(token) > 4 and token.endswith("es"):
            token = token[:-2]
        elif len(token) > 3 and token.endswith("s"):
            token = token[:-1]
        if token not in {"the", "and", "for", "with", "from", "that", "this"}:
            tokens.add(token)
    return frozenset(tokens)


@dataclass(frozen=True, slots=True)
class _ClusterFeatures:
    kind: str
    tokens: frozenset[str]
    tickers: frozenset[str]


def _cluster_features(row: dict[str, Any]) -> _ClusterFeatures:
    return _ClusterFeatures(
        kind=_event_type(
            str(row.get("raw_title") or ""),
            row.get("raw_summary"),
        ),
        tokens=_title_tokens(str(row.get("raw_title") or "")),
        tickers=frozenset(row.get("canonical_tickers") or []),
    )


def _cluster_features_similar(
    left: _ClusterFeatures,
    right: _ClusterFeatures,
) -> bool:
    if not left.tokens or not right.tokens:
        return False
    if left.tickers or right.tickers:
        if not left.tickers.intersection(right.tickers):
            return False
        threshold = 0.55
    else:
        threshold = 0.72
    union = left.tokens | right.tokens
    return bool(union) and len(left.tokens & right.tokens) / len(union) >= threshold


def _similar_titles(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return _cluster_features_similar(
        _cluster_features(left),
        _cluster_features(right),
    )


def _required_token_overlap(
    left_size: int,
    right_size: int,
    threshold: float,
) -> int | None:
    """Return the smallest token intersection that can satisfy Jaccard."""

    maximum = min(left_size, right_size)
    if maximum <= 0:
        return None
    # Starting from the closed-form lower bound keeps this loop constant-time
    # for normal headlines. The explicit ratio check preserves the exact
    # floating-point comparison used by _cluster_features_similar.
    estimate = math.ceil(
        threshold * (left_size + right_size) / (1.0 + threshold)
    )
    for overlap in range(max(1, estimate - 1), maximum + 1):
        if overlap / (left_size + right_size - overlap) >= threshold:
            return overlap
    return None


def _cluster_rows(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    ordered = sorted(
        rows,
        key=lambda row: (str(row.get("source_available_at") or ""), int(row["news_id"])),
    )
    features = [_cluster_features(row) for row in ordered]
    token_frequency = Counter(
        token for item in features for token in item.tokens
    )
    ordered_tokens = [
        tuple(sorted(item.tokens, key=lambda token: (token_frequency[token], token)))
        for item in features
    ]

    clusters: list[list[dict[str, Any]]] = []
    cluster_features: list[list[_ClusterFeatures]] = []
    # Each posting records clusters, not individual rows. Repeated syndicated
    # headlines therefore keep the candidate set bounded even when a cluster
    # contains thousands of source revisions.
    postings: dict[tuple[str, bool, int, str, str | None], set[int]] = {}
    indexed_lengths: dict[tuple[str, bool], set[int]] = {}

    for row, item, tokens_by_rarity in zip(
        ordered,
        features,
        ordered_tokens,
        strict=True,
    ):
        has_tickers = bool(item.tickers)
        category = (item.kind, has_tickers)
        threshold = 0.55 if has_tickers else 0.72
        candidates: set[int] = set()
        left_size = len(item.tokens)

        if left_size:
            for right_size in indexed_lengths.get(category, ()):
                required = _required_token_overlap(
                    left_size,
                    right_size,
                    threshold,
                )
                if required is None:
                    continue
                # If a prior title shares `required` tokens, at least one must
                # appear in this rarity-ordered prefix. Indexing all tokens on
                # prior rows makes this an exact filter, not an approximation.
                prefix_length = left_size - required + 1
                for token in tokens_by_rarity[:prefix_length]:
                    if has_tickers:
                        for ticker in item.tickers:
                            candidates.update(
                                postings.get(
                                    (
                                        item.kind,
                                        True,
                                        right_size,
                                        token,
                                        ticker,
                                    ),
                                    (),
                                )
                            )
                    else:
                        candidates.update(
                            postings.get(
                                (
                                    item.kind,
                                    False,
                                    right_size,
                                    token,
                                    None,
                                ),
                                (),
                            )
                        )

        selected: int | None = None
        for cluster_index in sorted(candidates):
            if any(
                _cluster_features_similar(item, member)
                for member in cluster_features[cluster_index]
            ):
                selected = cluster_index
                break

        if selected is None:
            selected = len(clusters)
            clusters.append([row])
            cluster_features.append([item])
        else:
            clusters[selected].append(row)
            cluster_features[selected].append(item)

        if left_size:
            indexed_lengths.setdefault(category, set()).add(left_size)
            ticker_keys: tuple[str | None, ...] = (
                tuple(sorted(item.tickers)) if has_tickers else (None,)
            )
            for token in item.tokens:
                for ticker in ticker_keys:
                    postings.setdefault(
                        (
                            item.kind,
                            has_tickers,
                            left_size,
                            token,
                            ticker,
                        ),
                        set(),
                    ).add(selected)
    return clusters


def _cluster_key(members: list[dict[str, Any]]) -> str:
    representative = min(
        members,
        key=lambda row: (
            " ".join(sorted(_title_tokens(str(row.get("raw_title") or "")))),
            int(row["news_id"]),
        ),
    )
    kind = _event_type(
        str(representative.get("raw_title") or ""), representative.get("raw_summary")
    )
    tickers = sorted(
        {ticker for row in members for ticker in row.get("canonical_tickers") or []}
    )
    signature = "-".join(
        sorted(_title_tokens(str(representative.get("raw_title") or "")))[:10]
    ) or f"news-{representative['news_id']}"
    return f"{kind}:{','.join(tickers[:5])}:{signature}"


class LocalCatalystIntelligence:
    """Local-only news intelligence and Chinese presentation store.

    Source-language fields stay inside SQLite and model input. Public methods
    expose only validated Simplified Chinese fields or Chinese waiting copy.
    """

    def __init__(
        self,
        db_path: str | Path,
        ai_repository: AIJobRepository,
        mode: Mode,
        canonical_tickers: Iterable[str],
        *,
        model: str = MODEL,
        reasoning: str = REASONING,
        max_queued: int = 200,
        manual_refresh_cooldown_seconds: int = 30,
    ) -> None:
        if mode not in {"off", "read", "manual", "scheduled"}:
            raise ValueError("invalid catalyst mode")
        if model != MODEL or reasoning != REASONING:
            raise ValueError("local catalyst model configuration is fixed")
        if isinstance(max_queued, bool) or not 1 <= max_queued <= 10_000:
            raise ValueError("max_queued is invalid")
        self.db_path = Path(db_path)
        self.ai_repository = ai_repository
        self.mode = mode
        self.model = model
        self.reasoning = reasoning
        self.max_queued = max_queued
        if (
            isinstance(manual_refresh_cooldown_seconds, bool)
            or not 0 <= int(manual_refresh_cooldown_seconds) <= 3600
        ):
            raise ValueError("manual refresh cooldown is invalid")
        self.manual_refresh_cooldown_seconds = int(
            manual_refresh_cooldown_seconds
        )
        normalized = {
            str(value).strip().upper()
            for value in canonical_tickers
            if TICKER_RE.fullmatch(str(value).strip().upper())
        }
        self.canonical_tickers = frozenset(normalized - AMBIGUOUS_TICKERS)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        owner_access = current_request_is_owner()
        if owner_access:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.db_path, timeout=5.0)
        else:
            uri = f"file:{quote(self.db_path.resolve().as_posix(), safe='/')}?mode=ro"
            connection = sqlite3.connect(uri, uri=True, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        if not owner_access:
            connection.execute("PRAGMA query_only=ON")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.ai_repository.initialize()
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.executescript(_SCHEMA)
            row = connection.execute(
                "SELECT checksum FROM catalyst_local_schema WHERE version=?",
                (SCHEMA_VERSION,),
            ).fetchone()
            if row is not None and str(row["checksum"]) != SCHEMA_CHECKSUM:
                raise RuntimeError("local_catalyst_schema_checksum_mismatch")
            connection.execute(
                """INSERT OR IGNORE INTO catalyst_local_schema(
                       version,checksum,applied_at
                   ) VALUES(?,?,?)""",
                (SCHEMA_VERSION, SCHEMA_CHECKSUM, _iso()),
            )
            connection.commit()

    def validate_tickers(self, values: Iterable[Any]) -> list[str]:
        output: list[str] = []
        for value in values:
            ticker = str(value or "").strip().upper()
            if (
                ticker in self.canonical_tickers
                and ticker not in AMBIGUOUS_TICKERS
                and ticker not in output
            ):
                output.append(ticker)
        return output

    @staticmethod
    def _change_news(raw_json: str) -> dict[str, Any] | None:
        raw = _loads(raw_json, None)
        if not isinstance(raw, dict) or raw.get("operation") != "upsert":
            return None
        news = raw.get("news")
        return news if isinstance(news, dict) else None

    def _ingest_revisions(self, connection: sqlite3.Connection) -> int:
        try:
            rows = connection.execute(
                """SELECT c.change_sequence,c.news_id,c.available_at,c.raw_json
                   FROM macrolens_etl_news_changes c
                   WHERE c.operation='upsert'
                     AND NOT EXISTS (
                         SELECT 1 FROM catalyst_local_news_revisions r
                         WHERE r.news_id=c.news_id
                           AND r.change_sequence=c.change_sequence
                     )
                   ORDER BY c.change_sequence"""
            ).fetchall()
        except sqlite3.OperationalError:
            return 0
        inserted = 0
        observed = _iso()
        for row in rows:
            news = self._change_news(str(row["raw_json"]))
            if news is None:
                continue
            source = str(news.get("source") or "未知来源")[:500]
            sources = [
                str(value)[:500]
                for value in news.get("sources") or [source]
                if str(value).strip()
            ] or [source]
            canonical = self.validate_tickers(news.get("source_tickers") or [])
            content_hash = str(news.get("content_hash") or "").strip()
            title = str(news.get("title") or "").strip()
            fetched_at = str(news.get("fetched_at") or "").strip()
            url = str(news.get("url") or "").strip()
            if not content_hash or not title or not fetched_at or not url:
                continue
            changed = connection.execute(
                """INSERT OR IGNORE INTO catalyst_local_news_revisions(
                       news_id,change_sequence,content_hash,source,raw_title,
                       raw_summary,url,image_url,published_at,fetched_at,
                       source_available_at,source_tickers_json,
                       canonical_tickers_json,source_names_json,source_count,
                       ingested_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    int(row["news_id"]),
                    int(row["change_sequence"]),
                    content_hash,
                    source,
                    title,
                    news.get("summary"),
                    url,
                    news.get("image_url"),
                    news.get("published_at"),
                    fetched_at,
                    str(row["available_at"]),
                    _json(news.get("source_tickers") or []),
                    _json(canonical),
                    _json(sources),
                    len(sources),
                    observed,
                ),
            ).rowcount
            inserted += int(changed)
        return inserted

    @staticmethod
    def _job_payload(row: dict[str, Any]) -> dict[str, Any] | None:
        payload = _loads(row.get("payload_json"), None)
        return payload if isinstance(payload, dict) else None

    def _read_ai_job(self, job_id: str) -> dict[str, Any] | None:
        path = Path(self.ai_repository.path)
        if not path.is_file():
            return None
        uri = f"file:{quote(path.resolve().as_posix(), safe='/')}?mode=ro"
        try:
            connection = sqlite3.connect(uri, uri=True, timeout=2.0)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            row = connection.execute(
                "SELECT * FROM ai_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
        except sqlite3.Error:
            return None
        finally:
            if "connection" in locals():
                connection.close()
        return dict(row) if row is not None else None

    def _ai_job_snapshot(
        self,
        *,
        allow_write_contention: bool = True,
        job_ids: Iterable[str] | None = None,
        news_ids: Iterable[int] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Read current Catalyst AI rows before taking the Catalyst write lock."""

        if job_ids is not None and news_ids is not None:
            raise ValueError("job_ids and news_ids are mutually exclusive")
        requested_ids = (
            sorted({str(job_id) for job_id in job_ids if str(job_id)})
            if job_ids is not None
            else None
        )
        requested_news_ids = (
            sorted(
                {
                    int(news_id)
                    for news_id in news_ids
                    if type(news_id) is int and news_id > 0
                }
            )
            if news_ids is not None
            else None
        )
        if requested_ids == [] or requested_news_ids == []:
            return {}
        path = Path(self.ai_repository.path)
        if not path.is_file():
            return {}
        uri = f"file:{quote(path.resolve().as_posix(), safe='/')}?mode=ro"
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(uri, uri=True, timeout=2.0)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            if requested_ids is None and requested_news_ids is None:
                rows = connection.execute(
                    """SELECT j.*,COALESCE(s.submission_source,'manual')
                              AS submission_source
                       FROM ai_jobs j
                       LEFT JOIN ai_job_sources s ON s.job_id=j.job_id
                       WHERE j.job_type IN ('news_impact','market_focus')
                       ORDER BY j.created_at DESC"""
                ).fetchall()
            elif requested_ids is not None:
                rows = []
                for offset in range(0, len(requested_ids), 500):
                    chunk = requested_ids[offset : offset + 500]
                    placeholders = ",".join("?" for _item in chunk)
                    rows.extend(
                        connection.execute(
                            f"""SELECT j.*,COALESCE(
                                        s.submission_source,'manual'
                                    ) AS submission_source
                                FROM ai_jobs j
                                LEFT JOIN ai_job_sources s
                                  ON s.job_id=j.job_id
                                WHERE j.job_id IN ({placeholders})
                                  AND j.job_type IN (
                                      'news_impact','market_focus'
                                  )""",
                            tuple(chunk),
                        ).fetchall()
                    )
            else:
                assert requested_news_ids is not None
                rows = []
                for offset in range(0, len(requested_news_ids), 500):
                    chunk = requested_news_ids[offset : offset + 500]
                    placeholders = ",".join("?" for _item in chunk)
                    rows.extend(
                        connection.execute(
                            f"""SELECT j.*,COALESCE(
                                        s.submission_source,'manual'
                                    ) AS submission_source
                                FROM ai_jobs j
                                LEFT JOIN ai_job_sources s
                                  ON s.job_id=j.job_id
                                WHERE j.job_type='news_impact'
                                  AND json_extract(
                                      j.payload_json,'$.news_id'
                                  ) IN ({placeholders})""",
                            tuple(chunk),
                        ).fetchall()
                    )
        except sqlite3.OperationalError as error:
            if allow_write_contention and _is_sqlite_write_contention(error):
                return {}
            raise
        finally:
            if connection is not None:
                connection.close()
        return {str(row["job_id"]): dict(row) for row in rows}

    def _has_current_job_identity(
        self,
        row: dict[str, Any] | None,
        *,
        expected_type: Literal["news_impact", "market_focus"],
        expected_schema: tuple[str, str] | None = None,
    ) -> bool:
        if row is None or row.get("job_type") != expected_type:
            return False
        schema_version, schema_hash = (
            expected_schema
            if expected_schema is not None
            else ai_runtime.schema_identity(expected_type)
        )
        prompt = (
            NEWS_PROMPT_VERSION if expected_type == "news_impact" else FOCUS_PROMPT_VERSION
        )
        return not (
            row.get("model") != self.model
            or row.get("reasoning") != self.reasoning
            or row.get("execution_mode") != EXECUTION_MODE
            or row.get("prompt_version") != prompt
            or row.get("schema_version") != schema_version
            or row.get("schema_sha256") != schema_hash
        )

    def _identity_public_job(
        self,
        row: dict[str, Any] | None,
        *,
        expected_type: Literal["news_impact", "market_focus"],
        expected_schema: tuple[str, str] | None = None,
    ) -> dict[str, Any] | None:
        if not self._has_current_job_identity(
            row,
            expected_type=expected_type,
            expected_schema=expected_schema,
        ):
            return None
        assert row is not None
        return AIJobRepository.public(row)

    def _verified_public_job(
        self,
        row: dict[str, Any] | None,
        *,
        expected_type: Literal["news_impact", "market_focus"],
    ) -> dict[str, Any] | None:
        public = self._identity_public_job(row, expected_type=expected_type)
        return public if public is not None and public.get("result") is not None else None

    def _compatible_news_public_job(
        self,
        row: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if (
            row is None
            or row.get("job_type") != "news_impact"
            or row.get("model") != self.model
            or row.get("reasoning") != self.reasoning
            or row.get("execution_mode") != EXECUTION_MODE
            or NEWS_PROMPT_FAMILY_RE.fullmatch(
                str(row.get("prompt_version") or "")
            )
            is None
            or NEWS_SCHEMA_FAMILY_RE.fullmatch(
                str(row.get("schema_version") or "")
            )
            is None
            or SCHEMA_SHA256_RE.fullmatch(
                str(row.get("schema_sha256") or "")
            )
            is None
        ):
            return None
        try:
            return AIJobRepository.public(row)
        except (KeyError, TypeError, ValueError):
            return None

    def _recoverable_completed_legacy_news_public_job(
        self,
        row: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        public = self._compatible_news_public_job(row)
        if (
            row is None
            or public is None
            or row.get("status") != "completed"
            or not isinstance(row.get("result_json"), str)
            or not row.get("result_json")
        ):
            return None
        return public

    def _recoverable_completed_focus_public_job(
        self,
        row: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if (
            row is None
            or row.get("job_type") != "market_focus"
            or row.get("status") != "completed"
            or row.get("model") != self.model
            or row.get("reasoning") != self.reasoning
            or row.get("execution_mode") != EXECUTION_MODE
            or FOCUS_PROMPT_FAMILY_RE.fullmatch(
                str(row.get("prompt_version") or "")
            )
            is None
            or FOCUS_SCHEMA_FAMILY_RE.fullmatch(
                str(row.get("schema_version") or "")
            )
            is None
            or SCHEMA_SHA256_RE.fullmatch(
                str(row.get("schema_sha256") or "")
            )
            is None
            or not isinstance(row.get("result_json"), str)
            or not row.get("result_json")
        ):
            return None
        try:
            return AIJobRepository.public(row)
        except (KeyError, TypeError, ValueError):
            return None

    def _scheduled_focus_attempt_count(
        self,
        jobs: Iterable[Mapping[str, Any]],
        *,
        payload: Mapping[str, Any],
        observed: datetime,
    ) -> int:
        expected_payload = _json(payload)
        attempts = 0
        for candidate in jobs:
            created_at = _parse_time(str(candidate.get("created_at") or ""))
            if (
                candidate.get("job_type") != "market_focus"
                or candidate.get("submission_source") != "scheduled"
                or candidate.get("status") == "budget_blocked"
                or candidate.get("model") != self.model
                or candidate.get("reasoning") != self.reasoning
                or candidate.get("execution_mode") != EXECUTION_MODE
                or created_at is None
                or created_at > observed
                or FOCUS_PROMPT_FAMILY_RE.fullmatch(
                    str(candidate.get("prompt_version") or "")
                )
                is None
                or FOCUS_SCHEMA_FAMILY_RE.fullmatch(
                    str(candidate.get("schema_version") or "")
                )
                is None
                or SCHEMA_SHA256_RE.fullmatch(
                    str(candidate.get("schema_sha256") or "")
                )
                is None
            ):
                continue
            candidate_payload = self._job_payload(dict(candidate))
            if (
                isinstance(candidate_payload, dict)
                and _json(candidate_payload) == expected_payload
            ):
                attempts += 1
        return attempts

    def _news_job_revision_key(
        self,
        row: Mapping[str, Any],
    ) -> tuple[int, int, str] | None:
        payload = self._job_payload(row)
        if payload is None:
            return None
        news_id = payload.get("news_id")
        change_sequence = payload.get("change_sequence")
        content_hash = payload.get("content_hash")
        if (
            type(news_id) is not int
            or news_id <= 0
            or type(change_sequence) is not int
            or change_sequence <= 0
            or not isinstance(content_hash, str)
            or not content_hash
        ):
            return None
        return news_id, change_sequence, content_hash

    def _current_news_job_revision_keys(
        self,
        jobs: Iterable[Mapping[str, Any]],
        *,
        expected_schema: tuple[str, str] | None = None,
    ) -> set[tuple[int, int, str]]:
        current_schema = expected_schema or ai_runtime.schema_identity("news_impact")
        keys: set[tuple[int, int, str]] = set()
        for job in jobs:
            candidate = dict(job)
            if not self._has_current_job_identity(
                candidate,
                expected_type="news_impact",
                expected_schema=current_schema,
            ):
                continue
            key = self._news_job_revision_key(candidate)
            if key is not None:
                keys.add(key)
        return keys

    def _linked_news_job_at(
        self,
        connection: sqlite3.Connection,
        row: dict[str, Any],
        *,
        as_of: datetime,
        jobs: Mapping[str, dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Return the job state visible then, plus a safe detail projection.

        The second value is omitted when the current mutable job row has moved
        beyond ``as_of``. This keeps PersonalCatalystService from re-reading a
        future terminal state through ``analysis_job``.
        """

        if "analysis_job_id" in row:
            job_id = row.get("analysis_job_id")
            link_created_at = row.get("analysis_job_created_at")
            if job_id is None or link_created_at is None:
                return None, None
        else:
            link = connection.execute(
                """SELECT job_id,created_at FROM catalyst_local_analysis_links
                   WHERE news_id=? AND change_sequence=? AND content_hash=?
                     AND created_at<=?
                   ORDER BY created_at DESC,job_id DESC LIMIT 1""",
                (
                    row["news_id"],
                    row["change_sequence"],
                    row["content_hash"],
                    _iso(as_of),
                ),
            ).fetchone()
            if link is None:
                return None, None
            job_id = link["job_id"]
            link_created_at = link["created_at"]
        job = (
            jobs.get(str(job_id))
            if jobs is not None
            else self._read_ai_job(str(job_id))
        )
        public = self._identity_public_job(job, expected_type="news_impact")
        if job is None or public is None:
            return None, None
        created_at = _parse_time(str(job.get("created_at") or link_created_at))
        if created_at is None or created_at > as_of:
            return None, None
        updated_at = _parse_time(str(job.get("updated_at") or ""))
        if updated_at is not None and updated_at > as_of:
            submitted_at = _parse_time(str(job.get("submitted_at") or ""))
            projected = dict(public)
            projected.update(
                {
                    "status": (
                        "in_progress"
                        if submitted_at is not None and submitted_at <= as_of
                        else "pending"
                    ),
                    "completed_at": None,
                    "error_code": None,
                    "result": None,
                    "cached": False,
                    "cancellable": False,
                }
            )
            return projected, None
        return public, public

    @staticmethod
    def _archive_misbound_news_link(
        connection: sqlite3.Connection,
        link: Mapping[str, Any] | sqlite3.Row,
        *,
        observed_at: str,
    ) -> None:
        raw_result = link["result_json"]
        if not isinstance(raw_result, str) or not raw_result:
            return
        digest = hashlib.sha256(raw_result.encode("utf-8")).hexdigest()
        connection.execute(
            """INSERT OR IGNORE INTO catalyst_local_analysis_result_audit(
                   job_id,contract_id,result_sha256,outcome,reason,
                   result_json,result_available_at,verified_at,observed_at
               ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                str(link["job_id"]),
                NEWS_LINK_AUDIT_CONTRACT_ID,
                digest,
                "rejected",
                "news_job_link_identity_mismatch",
                raw_result,
                link["result_available_at"],
                link["verified_at"],
                observed_at,
            ),
        )

    def _ensure_news_job_link(
        self,
        connection: sqlite3.Connection,
        revision: Mapping[str, Any] | sqlite3.Row,
        job: dict[str, Any],
    ) -> bool:
        """Create or repair the one local pointer owned by an AI job."""

        payload = self._job_payload(job)
        if payload is None or not self._news_payload_matches_revision(
            payload,
            revision,
        ):
            raise RuntimeError("ai_job_payload_invalid")
        created_at = _parse_time(str(job.get("created_at") or ""))
        if created_at is None:
            raise RuntimeError("ai_job_created_at_invalid")
        identity = (
            int(revision["news_id"]),
            int(revision["change_sequence"]),
            str(revision["content_hash"]),
        )
        existing = connection.execute(
            """SELECT * FROM catalyst_local_analysis_links
               WHERE job_id=?""",
            (str(job["job_id"]),),
        ).fetchone()
        if existing is None:
            connection.execute(
                """INSERT INTO catalyst_local_analysis_links(
                       news_id,change_sequence,content_hash,job_id,created_at
                   ) VALUES(?,?,?,?,?)""",
                (*identity, str(job["job_id"]), _iso(created_at)),
            )
            return True
        existing_identity = (
            int(existing["news_id"]),
            int(existing["change_sequence"]),
            str(existing["content_hash"]),
        )
        if existing_identity == identity:
            return False
        self._archive_misbound_news_link(
            connection,
            existing,
            observed_at=_iso(),
        )
        repaired = connection.execute(
            """UPDATE catalyst_local_analysis_links SET
                   news_id=?,change_sequence=?,content_hash=?,
                   result_json=NULL,result_available_at=NULL,verified_at=NULL,
                   created_at=?
               WHERE job_id=? AND news_id=? AND change_sequence=?
                 AND content_hash=?""",
            (
                *identity,
                _iso(created_at),
                str(job["job_id"]),
                *existing_identity,
            ),
        ).rowcount
        if repaired != 1:
            raise RuntimeError("news_job_link_repair_conflict")
        return True

    def _recover_unlinked_news_jobs(
        self,
        connection: sqlite3.Connection,
        jobs: Mapping[str, dict[str, Any]],
    ) -> int:
        existing_links = {
            str(link["job_id"]): link
            for link in connection.execute(
                """SELECT job_id,news_id,change_sequence,content_hash
                   FROM catalyst_local_analysis_links"""
            ).fetchall()
        }
        recovered = 0
        candidates = sorted(
            jobs.values(),
            key=lambda row: (
                str(row.get("created_at") or ""),
                str(row.get("job_id") or ""),
            ),
        )
        for job in candidates:
            job_id = str(job.get("job_id") or "")
            if not job_id or job.get("job_type") != "news_impact":
                continue
            payload = self._job_payload(job)
            if payload is None:
                continue
            news_id = payload.get("news_id")
            change_sequence = payload.get("change_sequence")
            content_hash = payload.get("content_hash")
            if (
                type(news_id) is not int
                or type(change_sequence) is not int
                or news_id < 1
                or change_sequence < 1
                or not isinstance(content_hash, str)
                or not content_hash
            ):
                continue
            existing = existing_links.get(job_id)
            if existing is not None and (
                int(existing["news_id"]),
                int(existing["change_sequence"]),
                str(existing["content_hash"]),
            ) == (news_id, change_sequence, content_hash):
                continue
            try:
                public = self._identity_public_job(
                    job,
                    expected_type="news_impact",
                )
            except (KeyError, TypeError, ValueError):
                continue
            if public is None:
                continue
            revision = connection.execute(
                """SELECT news_id,change_sequence,content_hash,source,raw_title,
                          raw_summary,url,published_at,fetched_at,source_names_json,
                          source_count,source_tickers_json,canonical_tickers_json
                   FROM catalyst_local_news_revisions
                   WHERE news_id=? AND change_sequence=? AND content_hash=?""",
                (news_id, change_sequence, content_hash),
            ).fetchone()
            if revision is None or not self._news_payload_matches_revision(
                payload,
                revision,
            ):
                continue
            recovered += int(
                self._ensure_news_job_link(connection, revision, job)
            )
        return recovered

    def _focus_job_matches_intent(
        self,
        job: Mapping[str, Any],
        intent: Mapping[str, Any] | sqlite3.Row,
    ) -> bool:
        """Verify that an existing paid job belongs to one durable intent."""

        if job.get("job_type") != "market_focus":
            return False
        try:
            public = self._identity_public_job(
                dict(job),
                expected_type="market_focus",
            )
        except (KeyError, TypeError, ValueError):
            return False
        payload = self._job_payload(dict(job))
        stored_payload = _loads(intent["payload_json"], None)
        cycle_id = str(intent["cycle_id"])
        return bool(
            public is not None
            and isinstance(payload, dict)
            and isinstance(stored_payload, dict)
            and payload.get("cycle_id") == cycle_id
            and str(intent["job_id"]) == f"intent:{cycle_id}"
            and _json(stored_payload) == _json(payload)
        )

    def _existing_focus_job_for_intent(
        self,
        intent: Mapping[str, Any] | sqlite3.Row,
    ) -> dict[str, Any] | None:
        """Find an already-created exact job without creating work on GET."""

        jobs = self._ai_job_snapshot()
        candidates = sorted(
            jobs.values(),
            key=lambda row: (
                str(row.get("created_at") or ""),
                str(row.get("job_id") or ""),
            ),
        )
        return next(
            (
                job
                for job in candidates
                if self._focus_job_matches_intent(job, intent)
            ),
            None,
        )

    def _recover_unlinked_focus_jobs(
        self,
        connection: sqlite3.Connection,
        jobs: Mapping[str, dict[str, Any]],
    ) -> int:
        """Relink a paid focus job after its local commit was interrupted."""

        intents = {
            str(row["cycle_id"]): row
            for row in connection.execute(
                """SELECT cycle_id,job_id,payload_json
                   FROM catalyst_local_focus_cycles
                   WHERE status='preparing'"""
            ).fetchall()
        }
        if not intents:
            return 0
        linked_job_ids = {
            str(row["job_id"])
            for row in connection.execute(
                """SELECT job_id FROM catalyst_local_focus_cycles
                   WHERE job_id NOT LIKE 'intent:%'"""
            ).fetchall()
        }
        recovered = 0
        candidates = sorted(
            jobs.values(),
            key=lambda row: (
                str(row.get("created_at") or ""),
                str(row.get("job_id") or ""),
            ),
        )
        for job in candidates:
            job_id = str(job.get("job_id") or "")
            if not job_id or job_id in linked_job_ids:
                continue
            payload = self._job_payload(job)
            cycle_id = payload.get("cycle_id") if payload is not None else None
            if not isinstance(cycle_id, str):
                continue
            intent = intents.get(cycle_id)
            if intent is None or not self._focus_job_matches_intent(job, intent):
                continue
            public = self._identity_public_job(
                job,
                expected_type="market_focus",
            )
            assert public is not None
            updated = connection.execute(
                """UPDATE catalyst_local_focus_cycles SET
                       status=?,job_id=?,updated_at=?
                   WHERE cycle_id=? AND status='preparing' AND job_id=?""",
                (
                    str(public["status"]),
                    job_id,
                    str(public.get("updated_at") or _iso()),
                    cycle_id,
                    f"intent:{cycle_id}",
                ),
            ).rowcount
            recovered += int(updated)
            if updated:
                linked_job_ids.add(job_id)
        return recovered

    @staticmethod
    def _news_payload_matches_revision(
        payload: Mapping[str, Any],
        revision: Mapping[str, Any] | sqlite3.Row,
    ) -> bool:
        required_keys = {
            "news_id",
            "change_sequence",
            "content_hash",
            "source",
            "title",
            "summary",
            "url",
            "published_at",
            "fetched_at",
            "sources",
            "source_count",
            "source_ticker_hints",
            "allowed_tickers",
            "analysis_revision",
        }
        allowed_keys = required_keys | {"manual_force_bucket"}
        if set(payload) - allowed_keys or not required_keys <= set(payload):
            return False
        analysis_revision = payload.get("analysis_revision")
        if type(analysis_revision) is not int or analysis_revision < 1:
            return False
        if "manual_force_bucket" in payload:
            force_bucket = payload["manual_force_bucket"]
            if not isinstance(force_bucket, str):
                return False
            parsed_bucket = _parse_time(force_bucket)
            if parsed_bucket is None or _minute_bucket(parsed_bucket) != force_bucket:
                return False
        expected = {
            "news_id": int(revision["news_id"]),
            "change_sequence": int(revision["change_sequence"]),
            "content_hash": str(revision["content_hash"]),
            "source": str(revision["source"]),
            "title": str(revision["raw_title"]),
            "summary": revision["raw_summary"],
            "url": str(revision["url"]),
            "published_at": revision["published_at"],
            "fetched_at": revision["fetched_at"],
            "sources": _loads(revision["source_names_json"], []),
            "source_count": int(revision["source_count"]),
            "source_ticker_hints": _loads(revision["source_tickers_json"], []),
            "allowed_tickers": _loads(revision["canonical_tickers_json"], []),
        }
        return all(payload.get(key) == value for key, value in expected.items())

    @staticmethod
    def _news_result_was_previously_accepted(
        connection: sqlite3.Connection,
        *,
        job_id: str,
        raw_result: str,
    ) -> bool:
        digest = hashlib.sha256(raw_result.encode("utf-8")).hexdigest()
        return (
            connection.execute(
                """SELECT 1 FROM catalyst_local_analysis_result_audit
                   WHERE job_id=? AND result_sha256=? AND result_json=?
                     AND outcome='accepted' LIMIT 1""",
                (job_id, digest, raw_result),
            ).fetchone()
            is not None
        )

    @staticmethod
    def _focus_result_was_previously_accepted(
        connection: sqlite3.Connection,
        *,
        cycle_id: str,
        job_id: str,
        raw_result: str,
    ) -> bool:
        digest = hashlib.sha256(raw_result.encode("utf-8")).hexdigest()
        return (
            connection.execute(
                """SELECT 1 FROM catalyst_local_focus_result_audit
                   WHERE cycle_id=? AND job_id=? AND result_sha256=?
                     AND result_json=? AND outcome='accepted' LIMIT 1""",
                (cycle_id, job_id, digest, raw_result),
            ).fetchone()
            is not None
        )

    @staticmethod
    def _audit_news_result(
        connection: sqlite3.Connection,
        *,
        job_id: str,
        raw_result: str,
        payload: dict[str, Any],
        result_available_at: str | None,
        verified_at: str | None,
        observed_at: str,
    ) -> tuple[str, bool]:
        digest = hashlib.sha256(raw_result.encode("utf-8")).hexdigest()
        audited = connection.execute(
            """SELECT outcome FROM catalyst_local_analysis_result_audit
               WHERE job_id=? AND contract_id=? AND result_sha256=?""",
            (job_id, NEWS_RESULT_CONTRACT_ID, digest),
        ).fetchone()
        if audited is not None:
            outcome = str(audited["outcome"])
            if outcome == "accepted" and not _news_result_identity_matches(
                _loads(raw_result, None),
                payload,
            ):
                return "rejected", False
            return outcome, False
        try:
            validate_result("news_impact", raw_result, payload)
        except (TypeError, ValueError):
            outcome = "rejected"
            reason = "current_news_result_contract_rejected"
        else:
            outcome = "accepted"
            reason = None
        inserted = connection.execute(
            """INSERT OR IGNORE INTO catalyst_local_analysis_result_audit(
                   job_id,contract_id,result_sha256,outcome,reason,
                   result_json,result_available_at,verified_at,observed_at
               ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                job_id,
                NEWS_RESULT_CONTRACT_ID,
                digest,
                outcome,
                reason,
                raw_result,
                result_available_at,
                verified_at,
                observed_at,
            ),
        ).rowcount
        return outcome, inserted == 1

    @staticmethod
    def _audit_focus_result(
        connection: sqlite3.Connection,
        *,
        cycle_id: str,
        job_id: str,
        raw_result: str,
        payload: dict[str, Any],
        result_available_at: str | None,
        observed_at: str,
    ) -> tuple[str, bool]:
        digest = hashlib.sha256(raw_result.encode("utf-8")).hexdigest()
        audited = connection.execute(
            """SELECT outcome FROM catalyst_local_focus_result_audit
               WHERE cycle_id=? AND job_id=? AND contract_id=?
                 AND result_sha256=?""",
            (
                cycle_id,
                job_id,
                FOCUS_RESULT_CONTRACT_ID,
                digest,
            ),
        ).fetchone()
        if audited is not None:
            return str(audited["outcome"]), False
        try:
            validate_result("market_focus", raw_result, payload)
        except (TypeError, ValueError):
            outcome = "rejected"
            reason = "current_focus_result_contract_rejected"
        else:
            outcome = "accepted"
            reason = None
        inserted = connection.execute(
            """INSERT OR IGNORE INTO catalyst_local_focus_result_audit(
                   cycle_id,job_id,contract_id,result_sha256,outcome,
                   reason,result_json,result_available_at,observed_at
               ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                cycle_id,
                job_id,
                FOCUS_RESULT_CONTRACT_ID,
                digest,
                outcome,
                reason,
                raw_result,
                result_available_at,
                observed_at,
            ),
        ).rowcount
        return outcome, inserted == 1

    @staticmethod
    def _archive_focus_binding_failure(
        connection: sqlite3.Connection,
        *,
        cycle: Mapping[str, Any] | sqlite3.Row,
        job: Mapping[str, Any],
        reason: str,
        observed_at: str,
    ) -> None:
        raw_result = job.get("result_json")
        if not isinstance(raw_result, str) or not raw_result:
            return
        digest = hashlib.sha256(raw_result.encode("utf-8")).hexdigest()
        connection.execute(
            """INSERT OR IGNORE INTO catalyst_local_focus_result_audit(
                   cycle_id,job_id,contract_id,result_sha256,outcome,
                   reason,result_json,result_available_at,observed_at
               ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                str(cycle["cycle_id"]),
                str(cycle["job_id"]),
                FOCUS_BINDING_AUDIT_CONTRACT_ID,
                digest,
                "rejected",
                reason,
                raw_result,
                job.get("completed_at") or job.get("updated_at"),
                observed_at,
            ),
        )

    def _retire_focus_binding_failure(
        self,
        connection: sqlite3.Connection,
        *,
        cycle: Mapping[str, Any] | sqlite3.Row,
        job: Mapping[str, Any],
        reason: str,
        error_code: str,
        observed_at: str,
    ) -> None:
        self._archive_focus_binding_failure(
            connection,
            cycle=cycle,
            job=job,
            reason=reason,
            observed_at=observed_at,
        )
        retired = connection.execute(
            """UPDATE catalyst_local_focus_cycles SET
                   status='failed',error_code=?,completed_at=NULL,updated_at=?
               WHERE cycle_id=? AND job_id=? AND result_json IS NULL
                 AND status!='cancelled'""",
            (
                error_code,
                observed_at,
                str(cycle["cycle_id"]),
                str(cycle["job_id"]),
            ),
        ).rowcount
        if retired != 1:
            raise RuntimeError("focus_binding_retirement_conflict")

    def _audit_published_news_results(
        self,
        connection: sqlite3.Connection,
    ) -> tuple[int, int]:
        """Audit paid results without retiring a previously accepted display."""

        rows = connection.execute(
            """SELECT link.job_id,link.news_id,link.change_sequence,
                      link.content_hash,link.result_json,
                      link.result_available_at,link.verified_at,
                      revision.canonical_tickers_json
               FROM catalyst_local_analysis_links link
               JOIN catalyst_local_news_revisions revision
                 ON revision.news_id=link.news_id
                AND revision.change_sequence=link.change_sequence
                AND revision.content_hash=link.content_hash
               WHERE link.result_json IS NOT NULL"""
        ).fetchall()
        accepted = 0
        rejected = 0
        observed_at = _iso()
        for row in rows:
            raw_result = str(row["result_json"])
            payload = {
                "news_id": int(row["news_id"]),
                "change_sequence": int(row["change_sequence"]),
                "content_hash": str(row["content_hash"]),
                "allowed_tickers": _loads(
                    row["canonical_tickers_json"],
                    [],
                ),
            }
            outcome, inserted = self._audit_news_result(
                connection,
                job_id=str(row["job_id"]),
                raw_result=raw_result,
                payload=payload,
                result_available_at=row["result_available_at"],
                verified_at=row["verified_at"],
                observed_at=observed_at,
            )
            if outcome == "accepted":
                accepted += int(inserted)
                continue
            prior_result = _loads(raw_result, None)
            if (
                _news_result_identity_matches(prior_result, payload)
                and self._news_result_was_previously_accepted(
                    connection,
                    job_id=str(row["job_id"]),
                    raw_result=raw_result,
                )
            ):
                rejected += int(inserted)
                continue
            connection.execute(
                """UPDATE catalyst_local_analysis_links SET
                       result_json=NULL,result_available_at=NULL,verified_at=NULL
                   WHERE job_id=? AND result_json=?""",
                (row["job_id"], raw_result),
            )
            rejected += int(inserted)
        return accepted, rejected

    def _audit_published_focus_results(
        self,
        connection: sqlite3.Connection,
    ) -> tuple[int, int]:
        """Keep a previously accepted paid focus result across style changes."""

        rows = connection.execute(
            """SELECT cycle_id,job_id,payload_json,result_json,
                      completed_at,updated_at
               FROM catalyst_local_focus_cycles
               WHERE result_json IS NOT NULL"""
        ).fetchall()
        accepted = 0
        rejected = 0
        observed_at = _iso()
        for row in rows:
            raw_result = str(row["result_json"])
            payload = _loads(row["payload_json"], None)
            if not isinstance(payload, dict):
                payload = {}
            outcome, inserted = self._audit_focus_result(
                connection,
                cycle_id=str(row["cycle_id"]),
                job_id=str(row["job_id"]),
                raw_result=raw_result,
                payload=payload,
                result_available_at=row["completed_at"] or row["updated_at"],
                observed_at=observed_at,
            )
            if outcome == "accepted":
                accepted += int(inserted)
                continue
            prior_result = _loads(raw_result, None)
            if (
                _market_focus_result_identity_matches(prior_result, payload)
                and self._focus_result_was_previously_accepted(
                    connection,
                    cycle_id=str(row["cycle_id"]),
                    job_id=str(row["job_id"]),
                    raw_result=raw_result,
                )
            ):
                rejected += int(inserted)
                continue
            retired = connection.execute(
                """UPDATE catalyst_local_focus_cycles SET
                       status='failed',error_code='legacy_output_hidden',
                       result_json=NULL,completed_at=NULL,updated_at=?
                   WHERE cycle_id=? AND job_id=? AND result_json=?""",
                (
                    observed_at,
                    row["cycle_id"],
                    row["job_id"],
                    raw_result,
                ),
            ).rowcount
            if retired != 1:
                raise RuntimeError("focus_result_retirement_conflict")
            rejected += int(inserted)
        return accepted, rejected

    def _publish_completed_news(
        self,
        connection: sqlite3.Connection,
        jobs: Mapping[str, dict[str, Any]],
        *,
        target_job_id: str | None = None,
    ) -> int:
        if target_job_id is None:
            rows = connection.execute(
                """SELECT * FROM catalyst_local_analysis_links
                   WHERE result_json IS NULL ORDER BY created_at"""
            ).fetchall()
        else:
            rows = connection.execute(
                """SELECT * FROM catalyst_local_analysis_links
                   WHERE result_json IS NULL AND job_id=? ORDER BY created_at""",
                (target_job_id,),
            ).fetchall()
        current_schema = ai_runtime.schema_identity("news_impact")
        current_revision_keys = self._current_news_job_revision_keys(
            jobs.values(),
            expected_schema=current_schema,
        )
        published = 0
        for link in rows:
            job = jobs.get(str(link["job_id"]))
            raw_result = (
                job.get("result_json") if isinstance(job, dict) else None
            )
            previously_accepted = bool(
                isinstance(raw_result, str)
                and raw_result
                and self._news_result_was_previously_accepted(
                    connection,
                    job_id=str(link["job_id"]),
                    raw_result=raw_result,
                )
            )
            public = self._identity_public_job(
                job,
                expected_type="news_impact",
                expected_schema=current_schema,
            )
            if public is None:
                revision_key = (
                    int(link["news_id"]),
                    int(link["change_sequence"]),
                    str(link["content_hash"]),
                )
                if revision_key in current_revision_keys and not previously_accepted:
                    continue
                public = self._recoverable_completed_legacy_news_public_job(job)
            if public is None or public.get("status") != "completed":
                continue
            assert job is not None
            payload = self._job_payload(job)
            revision = connection.execute(
                """SELECT news_id,change_sequence,content_hash,source,raw_title,
                          raw_summary,url,published_at,fetched_at,source_names_json,
                          source_count,source_tickers_json,canonical_tickers_json
                   FROM catalyst_local_news_revisions
                   WHERE news_id=? AND change_sequence=? AND content_hash=?""",
                (
                    link["news_id"],
                    link["change_sequence"],
                    link["content_hash"],
                ),
            ).fetchone()
            if (
                payload is None
                or revision is None
                or not self._news_payload_matches_revision(payload, revision)
            ):
                continue
            if public.get("result") is None:
                if isinstance(raw_result, str) and raw_result:
                    self._audit_news_result(
                        connection,
                        job_id=str(link["job_id"]),
                        raw_result=raw_result,
                        payload=payload,
                        result_available_at=(
                            str(
                                public.get("completed_at")
                                or public.get("updated_at")
                                or ""
                            )
                            or None
                        ),
                        verified_at=None,
                        observed_at=_iso(),
                    )
                    recovered_result = _loads(raw_result, None)
                    available = str(
                        public.get("completed_at")
                        or public.get("updated_at")
                        or ""
                    )
                    if (
                        previously_accepted
                        and _news_result_identity_matches(
                            recovered_result,
                            payload,
                        )
                        and _parse_time(available) is not None
                    ):
                        connection.execute(
                            """UPDATE catalyst_local_analysis_links SET
                                   result_json=?,result_available_at=?,verified_at=?
                               WHERE job_id=? AND result_json IS NULL""",
                            (
                                raw_result,
                                available,
                                _iso(),
                                str(link["job_id"]),
                            ),
                        )
                        published += 1
                continue
            result = public["result"]
            if (
                result.get("news_id") != int(link["news_id"])
                or result.get("change_sequence") != int(link["change_sequence"])
                or result.get("content_hash") != str(link["content_hash"])
            ):
                continue
            available = str(public.get("completed_at") or public.get("updated_at") or "")
            if _parse_time(available) is None:
                continue
            connection.execute(
                """UPDATE catalyst_local_analysis_links SET
                       result_json=?,result_available_at=?,verified_at=?
                   WHERE job_id=? AND result_json IS NULL""",
                (_json(result), available, _iso(), str(link["job_id"])),
            )
            published += 1
        return published

    def _publish_completed_focus(
        self,
        connection: sqlite3.Connection,
        jobs: Mapping[str, dict[str, Any]],
    ) -> int:
        cycles = connection.execute(
            """SELECT * FROM catalyst_local_focus_cycles
               WHERE result_json IS NULL AND status NOT IN ('cancelled')
               ORDER BY created_at"""
        ).fetchall()
        published = 0
        for cycle in cycles:
            job = jobs.get(str(cycle["job_id"]))
            public = self._identity_public_job(
                job,
                expected_type="market_focus",
            )
            if public is None:
                public = self._recoverable_completed_focus_public_job(job)
            if public is None:
                if job and job.get("status") == "completed":
                    self._retire_focus_binding_failure(
                        connection,
                        cycle=cycle,
                        job=job,
                        reason="market_focus_runtime_identity_mismatch",
                        error_code="runtime_configuration_changed",
                        observed_at=_iso(),
                    )
                elif job and job.get("status") in {
                    "failed",
                    "cancelled",
                    "budget_blocked",
                }:
                    connection.execute(
                        """UPDATE catalyst_local_focus_cycles SET
                               status=?,error_code=?,updated_at=? WHERE cycle_id=?""",
                        (
                            str(job["status"]),
                            str(job.get("error_code") or "focus_job_failed")[:120],
                            str(job.get("updated_at") or _iso()),
                            str(cycle["cycle_id"]),
                        ),
                    )
                continue
            if public.get("status") != "completed":
                connection.execute(
                    "UPDATE catalyst_local_focus_cycles SET status=?,updated_at=? WHERE cycle_id=?",
                    (str(public["status"]), str(public["updated_at"]), str(cycle["cycle_id"])),
                )
                continue
            assert job is not None
            job_payload = self._job_payload(job)
            cycle_payload = _loads(cycle["payload_json"], None)
            if (
                not isinstance(job_payload, dict)
                or not isinstance(cycle_payload, dict)
                or _json(job_payload) != _json(cycle_payload)
            ):
                self._retire_focus_binding_failure(
                    connection,
                    cycle=cycle,
                    job=job,
                    reason="market_focus_payload_mismatch",
                    error_code="market_focus_payload_mismatch",
                    observed_at=_iso(),
                )
                continue
            if public.get("result") is None:
                raw_result = job.get("result_json")
                if isinstance(raw_result, str) and raw_result:
                    observed_at = _iso()
                    result_available_at = (
                        str(
                            public.get("completed_at")
                            or public.get("updated_at")
                            or ""
                        )
                        or None
                    )
                    outcome, _inserted = self._audit_focus_result(
                        connection,
                        cycle_id=str(cycle["cycle_id"]),
                        job_id=str(cycle["job_id"]),
                        raw_result=raw_result,
                        payload=job_payload,
                        result_available_at=result_available_at,
                        observed_at=observed_at,
                    )
                    recovered_result = _loads(raw_result, None)
                    if (
                        outcome == "rejected"
                        and result_available_at is not None
                        and _parse_time(result_available_at) is not None
                        and _market_focus_result_identity_matches(
                            recovered_result,
                            job_payload,
                        )
                        and self._focus_result_was_previously_accepted(
                            connection,
                            cycle_id=str(cycle["cycle_id"]),
                            job_id=str(cycle["job_id"]),
                            raw_result=raw_result,
                        )
                    ):
                        connection.execute(
                            """UPDATE catalyst_local_focus_cycles SET
                                   status='completed',result_json=?,error_code=NULL,
                                   completed_at=?,updated_at=? WHERE cycle_id=?
                                     AND job_id=? AND result_json IS NULL""",
                            (
                                raw_result,
                                result_available_at,
                                result_available_at,
                                str(cycle["cycle_id"]),
                                str(cycle["job_id"]),
                            ),
                        )
                        published += 1
                        continue
                    if outcome == "rejected":
                        retired = connection.execute(
                            """UPDATE catalyst_local_focus_cycles SET
                                   status='failed',
                                   error_code='legacy_output_hidden',
                                   updated_at=?
                               WHERE cycle_id=? AND job_id=?
                                 AND result_json IS NULL
                                 AND status!='cancelled'""",
                            (
                                observed_at,
                                str(cycle["cycle_id"]),
                                str(cycle["job_id"]),
                            ),
                        ).rowcount
                        if retired != 1:
                            raise RuntimeError(
                                "focus_result_retirement_conflict"
                            )
                continue
            result = public["result"]
            if (
                result.get("cycle_id") != str(cycle["cycle_id"])
                or result.get("input_hash") != str(cycle["input_hash"])
            ):
                continue
            completed = str(public.get("completed_at") or public["updated_at"])
            connection.execute(
                """UPDATE catalyst_local_focus_cycles SET
                       status='completed',result_json=?,error_code=NULL,
                       completed_at=?,updated_at=? WHERE cycle_id=?""",
                (_json(result), completed, completed, str(cycle["cycle_id"])),
            )
            published += 1
        return published

    def _active_revisions(
        self,
        connection: sqlite3.Connection,
        *,
        as_of: datetime,
        window_hours: int | None = None,
    ) -> list[dict[str, Any]]:
        cutoff = _iso(as_of)
        params: list[Any] = [cutoff, cutoff]
        time_clause = ""
        if window_hours is not None:
            time_clause = " AND r.source_available_at>=?"
            params.append(_iso(as_of - timedelta(hours=window_hours)))
        params.extend(
            [
                cutoff,
                cutoff,
                NEWS_RESULT_CONTRACT_ID,
            ]
        )
        try:
            rows = connection.execute(
                f"""WITH latest AS (
                         SELECT news_id,MAX(change_sequence) AS change_sequence
                         FROM macrolens_etl_news_changes
                         WHERE available_at<=? GROUP BY news_id
                     ), active AS (
                         SELECT c.news_id,c.change_sequence
                         FROM latest l JOIN macrolens_etl_news_changes c
                           ON c.news_id=l.news_id
                          AND c.change_sequence=l.change_sequence
                         WHERE c.operation='upsert' AND c.available_at<=?
                     ), active_revisions AS (
                         SELECT r.* FROM active a
                         JOIN catalyst_local_news_revisions r
                           ON r.news_id=a.news_id
                          AND r.change_sequence=a.change_sequence
                         WHERE 1=1 {time_clause}
                     ), latest_link AS (
                         SELECT l.*,
                                ROW_NUMBER() OVER (
                                    PARTITION BY l.news_id,l.change_sequence,
                                                 l.content_hash
                                    ORDER BY l.created_at DESC,l.job_id DESC
                                ) AS rank
                         FROM catalyst_local_analysis_links l
                         JOIN active_revisions r
                           ON r.news_id=l.news_id
                          AND r.change_sequence=l.change_sequence
                          AND r.content_hash=l.content_hash
                         WHERE l.created_at<=?
                     ), latest_analysis AS (
                         SELECT l.*,
                                ROW_NUMBER() OVER (
                                    PARTITION BY l.news_id,l.change_sequence,
                                                 l.content_hash
                                    ORDER BY l.result_available_at DESC,
                                             l.created_at DESC,l.job_id DESC
                                ) AS rank
                         FROM catalyst_local_analysis_links l
                         JOIN active_revisions r
                           ON r.news_id=l.news_id
                          AND r.change_sequence=l.change_sequence
                          AND r.content_hash=l.content_hash
                         WHERE l.result_json IS NOT NULL
                           AND l.result_available_at<=?
                     )
                     SELECT r.*,
                            analysis.result_json AS analysis_result_json,
                            analysis.result_available_at
                                AS analysis_result_available_at,
                            analysis.job_id AS analysis_result_job_id,
                            job_link.job_id AS analysis_job_id,
                            job_link.created_at AS analysis_job_created_at,
                            CASE WHEN audit.job_id IS NULL THEN 0 ELSE 1 END
                                AS analysis_result_audited
                     FROM active_revisions r
                     LEFT JOIN latest_analysis analysis
                       ON analysis.news_id=r.news_id
                      AND analysis.change_sequence=r.change_sequence
                      AND analysis.content_hash=r.content_hash
                      AND analysis.rank=1
                     LEFT JOIN latest_link job_link
                       ON job_link.news_id=r.news_id
                      AND job_link.change_sequence=r.change_sequence
                      AND job_link.content_hash=r.content_hash
                      AND job_link.rank=1
                     LEFT JOIN catalyst_local_analysis_result_audit audit
                       ON audit.job_id=analysis.job_id
                      AND audit.contract_id=?
                      AND audit.outcome='accepted'
                      AND audit.result_json=analysis.result_json
                     ORDER BY COALESCE(r.published_at,r.fetched_at) DESC,
                              r.news_id DESC""",
                tuple(params),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        output: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["canonical_tickers"] = _loads(item["canonical_tickers_json"], [])
            item["source_names"] = _loads(item["source_names_json"], [])
            output.append(item)
        return output

    def _active_revision(
        self,
        connection: sqlite3.Connection,
        *,
        news_id: int,
        as_of: datetime,
    ) -> dict[str, Any] | None:
        """Read one visible revision without scanning every active news item."""

        cutoff = _iso(as_of)
        try:
            row = connection.execute(
                """WITH latest AS (
                       SELECT MAX(change_sequence) AS change_sequence
                       FROM macrolens_etl_news_changes
                       WHERE news_id=? AND available_at<=?
                   )
                   SELECT r.*,
                          EXISTS(
                              SELECT 1 FROM catalyst_local_analysis_links link
                              WHERE link.news_id=r.news_id
                                AND link.change_sequence=r.change_sequence
                                AND link.content_hash=r.content_hash
                          ) AS has_analysis_links
                   FROM latest l
                   JOIN macrolens_etl_news_changes c
                     ON c.news_id=? AND c.change_sequence=l.change_sequence
                   JOIN catalyst_local_news_revisions r
                     ON r.news_id=c.news_id
                    AND r.change_sequence=c.change_sequence
                   WHERE c.operation='upsert' AND c.available_at<=?
                   LIMIT 1""",
                (news_id, cutoff, news_id, cutoff),
            ).fetchone()
        except sqlite3.OperationalError:
            return None
        if row is None:
            return None
        item = dict(row)
        item["canonical_tickers"] = _loads(item["canonical_tickers_json"], [])
        item["source_names"] = _loads(item["source_names_json"], [])
        return item

    @staticmethod
    def _hot_score(row: dict[str, Any], result: dict[str, Any] | None, now: datetime) -> tuple[float, dict[str, float], list[str]]:
        components: dict[str, tuple[float, float, str]] = {}
        published = _parse_time(row.get("published_at")) or _parse_time(row.get("fetched_at"))
        if published is not None:
            age_hours = max(0.0, (now - published).total_seconds() / 3600)
            components["recency"] = (max(0.0, 100.0 - age_hours * 2.0), 0.35, "发布时间较近")
        source_count = int(row.get("source_count") or 0)
        if source_count > 0:
            components["source_breadth"] = (min(100.0, 30.0 + 18.0 * source_count), 0.20, "多来源交叉出现")
        tickers = list(row.get("canonical_tickers") or [])
        if tickers:
            components["ticker_breadth"] = (min(100.0, 45.0 + 12.0 * len(tickers)), 0.15, "关联本地正式股票代码")
        if result is not None:
            components["market_relevance"] = (float(result["market_relevance"]), 0.30, "中文分析显示市场相关性")
        total_weight = sum(weight for _score, weight, _reason in components.values())
        if total_weight <= 0:
            return 0.0, {}, ["可用证据不足，未补成中性分数"]
        scores = {name: round(score, 2) for name, (score, _weight, _reason) in components.items()}
        value = sum(score * weight for score, weight, _reason in components.values()) / total_weight
        reasons = [reason for _score, _weight, reason in components.values()]
        return round(max(0.0, min(100.0, value)), 2), scores, reasons

    def _analysis_for_revision(
        self,
        connection: sqlite3.Connection,
        row: dict[str, Any],
        *,
        as_of: datetime,
    ) -> tuple[dict[str, Any] | None, str | None]:
        if "analysis_result_json" in row:
            raw_value = row.get("analysis_result_json")
            result_available_at = row.get("analysis_result_available_at")
            result_job_id = row.get("analysis_result_job_id")
            audited = bool(row.get("analysis_result_audited"))
            if raw_value is None or result_available_at is None:
                return None, None
        else:
            link = connection.execute(
                """SELECT link.job_id AS analysis_result_job_id,
                          link.result_json,link.result_available_at,
                          EXISTS(
                              SELECT 1
                              FROM catalyst_local_analysis_result_audit audit
                              WHERE audit.job_id=link.job_id
                                AND audit.contract_id=?
                                AND audit.outcome='accepted'
                                AND audit.result_json=link.result_json
                          ) AS result_audited
                   FROM catalyst_local_analysis_links link
                   WHERE link.news_id=? AND link.change_sequence=?
                     AND link.content_hash=? AND link.result_json IS NOT NULL
                     AND link.result_available_at<=?
                   ORDER BY link.result_available_at DESC,
                            link.created_at DESC,link.job_id DESC LIMIT 1""",
                (
                    NEWS_RESULT_CONTRACT_ID,
                    row["news_id"],
                    row["change_sequence"],
                    row["content_hash"],
                    _iso(as_of),
                ),
            ).fetchone()
            if link is None:
                return None, None
            raw_value = link["result_json"]
            result_available_at = link["result_available_at"]
            result_job_id = link["analysis_result_job_id"]
            audited = bool(link["result_audited"])
        payload = {
            "news_id": row.get("news_id"),
            "change_sequence": row.get("change_sequence"),
            "content_hash": row.get("content_hash"),
            "allowed_tickers": list(row.get("canonical_tickers") or []),
        }
        raw_result = str(raw_value)
        if audited:
            result = _loads(raw_result, None)
            if _news_result_identity_matches(result, payload):
                return result, str(result_available_at)
        try:
            result = validate_result("news_impact", raw_result, payload)
        except (TypeError, ValueError):
            result = _loads(raw_result, None)
            if (
                not isinstance(result_job_id, str)
                or not _news_result_identity_matches(result, payload)
                or not self._news_result_was_previously_accepted(
                    connection,
                    job_id=result_job_id,
                    raw_result=raw_result,
                )
            ):
                return None, None
        return result, str(result_available_at)

    def _plan_hotspots(
        self,
        connection: sqlite3.Connection,
        *,
        now: datetime,
    ) -> list[dict[str, Any]]:
        """Build the next hotspot snapshot without holding a SQLite write lock."""

        rows = self._active_revisions(connection, as_of=now, window_hours=72)
        planned: list[dict[str, Any]] = []
        for members in _cluster_rows(rows):
            key = _cluster_key(members)
            members.sort(key=lambda item: (item.get("source_available_at") or "", item["news_id"]), reverse=True)
            representative = members[0]
            result, result_available = self._analysis_for_revision(connection, representative, as_of=now)
            tickers = sorted({ticker for item in members for ticker in item.get("canonical_tickers") or []})
            source_map: dict[str, str] = {}
            for item in members:
                for name in item.get("source_names") or []:
                    normalized = str(name).strip()
                    if normalized:
                        source_map.setdefault(normalized.casefold(), normalized)
            sources = [source_map[key] for key in sorted(source_map)]
            identity = [
                {
                    "news_id": int(item["news_id"]),
                    "change_sequence": int(item["change_sequence"]),
                    "content_hash": str(item["content_hash"]),
                }
                for item in members
            ]
            group_input = {
                "key": key,
                "identities": identity,
                "analysis_available_at": result_available,
            }
            input_hash = _sha(group_input)
            group_id = "evt_" + hashlib.sha256(key.encode()).hexdigest()[:32]
            score, components, reasons = self._hot_score(representative, result, now)
            title_zh = str(result.get("title_zh") if result else HOTSPOT_WAITING)
            summary_zh = str(result.get("headline_summary") if result else SUMMARY_WAITING)
            published_values = [item.get("published_at") or item.get("fetched_at") for item in members]
            available = max(
                [str(item["source_available_at"]) for item in members]
                + ([str(result_available)] if result_available else [])
            )
            planned.append(
                {
                    "event_group_id": group_id,
                    "input_hash": input_hash,
                    "event_type": _event_type(
                        str(representative["raw_title"]),
                        representative.get("raw_summary"),
                    ),
                    "representative_news_id": representative["news_id"],
                    "representative_change_sequence": representative[
                        "change_sequence"
                    ],
                    "representative_content_hash": representative["content_hash"],
                    "representative_title_zh": title_zh,
                    "representative_summary_zh": summary_zh,
                    "first_published_at": min(published_values),
                    "last_published_at": max(published_values),
                    "available_at": available,
                    "source_count": max(1, len(sources)),
                    "source_names_json": _json(sources),
                    "validated_tickers_json": _json(tickers),
                    "news_identities_json": _json(identity),
                    "hot_score": score,
                    "component_scores_json": _json(components),
                    "reasons_json": _json(reasons),
                    "created_at": _iso(now),
                }
            )
        return planned

    def _commit_hotspots(
        self,
        connection: sqlite3.Connection,
        planned: Sequence[Mapping[str, Any]],
        *,
        expected_base_revision: int,
        now: datetime,
    ) -> tuple[int, int, bool]:
        """Persist a prepared plan in one short, caller-owned transaction."""

        current_snapshot = connection.execute(
            """SELECT prepared_revision,item_count
               FROM catalyst_local_hotspot_revisions
               ORDER BY prepared_revision DESC LIMIT 1"""
        ).fetchone()
        current_revision = int(
            current_snapshot["prepared_revision"] if current_snapshot else 0
        )
        if current_revision != expected_base_revision:
            return (
                current_revision,
                int(current_snapshot["item_count"] if current_snapshot else 0),
                False,
            )

        prepared: list[dict[str, Any]] = []
        for proposal in planned:
            event = connection.execute(
                """SELECT * FROM catalyst_local_event_groups
                   WHERE event_group_id=? AND input_hash=? LIMIT 1""",
                (proposal["event_group_id"], proposal["input_hash"]),
            ).fetchone()
            if event is None:
                previous = connection.execute(
                    """SELECT event_group_version FROM catalyst_local_event_groups
                       WHERE event_group_id=?
                       ORDER BY event_group_version DESC LIMIT 1""",
                    (proposal["event_group_id"],),
                ).fetchone()
                version = int(previous["event_group_version"] if previous else 0) + 1
                connection.execute(
                    """INSERT INTO catalyst_local_event_groups(
                           event_group_id,event_group_version,input_hash,event_type,
                           representative_news_id,representative_change_sequence,
                           representative_content_hash,representative_title_zh,
                           representative_summary_zh,first_published_at,last_published_at,
                           available_at,source_count,source_names_json,
                           validated_tickers_json,news_identities_json,hot_score,
                           component_scores_json,reasons_json,created_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        proposal["event_group_id"],
                        version,
                        proposal["input_hash"],
                        proposal["event_type"],
                        proposal["representative_news_id"],
                        proposal["representative_change_sequence"],
                        proposal["representative_content_hash"],
                        proposal["representative_title_zh"],
                        proposal["representative_summary_zh"],
                        proposal["first_published_at"],
                        proposal["last_published_at"],
                        proposal["available_at"],
                        proposal["source_count"],
                        proposal["source_names_json"],
                        proposal["validated_tickers_json"],
                        proposal["news_identities_json"],
                        proposal["hot_score"],
                        proposal["component_scores_json"],
                        proposal["reasons_json"],
                        proposal["created_at"],
                    ),
                )
                event = connection.execute(
                    """SELECT * FROM catalyst_local_event_groups
                       WHERE event_group_id=? AND event_group_version=?""",
                    (proposal["event_group_id"], version),
                ).fetchone()
            assert event is not None
            prepared.append(dict(event))
        prepared.sort(key=lambda item: (-float(item["hot_score"]), str(item["event_group_id"])))
        snapshot = [
            (item["event_group_id"], int(item["event_group_version"]), item["input_hash"])
            for item in prepared
        ]
        input_hash = _sha(snapshot)
        previous_snapshot = connection.execute(
            """SELECT prepared_revision,input_hash FROM catalyst_local_hotspot_revisions
               ORDER BY prepared_revision DESC LIMIT 1"""
        ).fetchone()
        if previous_snapshot is not None and str(previous_snapshot["input_hash"]) == input_hash:
            return int(previous_snapshot["prepared_revision"]), len(prepared), True
        # Invalidating a published analysis can temporarily restore an older
        # waiting-state plan. The snapshot hash is historically unique, so
        # retain the latest prepared view until the replacement analysis lands
        # instead of attempting to insert the old hash again.
        historical_snapshot = connection.execute(
            """SELECT prepared_revision FROM catalyst_local_hotspot_revisions
               WHERE input_hash=? LIMIT 1""",
            (input_hash,),
        ).fetchone()
        if historical_snapshot is not None and current_snapshot is not None:
            return (
                int(current_snapshot["prepared_revision"]),
                int(current_snapshot["item_count"]),
                True,
            )
        revision = int(previous_snapshot["prepared_revision"] if previous_snapshot else 0) + 1
        data_through = max((str(item["available_at"]) for item in prepared), default=None)
        connection.execute(
            """INSERT INTO catalyst_local_hotspot_revisions(
                   prepared_revision,input_hash,prepared_at,data_through,item_count
               ) VALUES(?,?,?,?,?)""",
            (revision, input_hash, _iso(now), data_through, len(prepared)),
        )
        connection.executemany(
            """INSERT INTO catalyst_local_hotspot_items(
                   prepared_revision,ordinal,event_group_id,event_group_version
               ) VALUES(?,?,?,?)""",
            [
                (
                    revision,
                    ordinal,
                    item["event_group_id"],
                    item["event_group_version"],
                )
                for ordinal, item in enumerate(prepared, start=1)
            ],
        )
        return revision, len(prepared), True

    def reconcile(self, *, allow_scheduled_jobs: bool = False) -> dict[str, int]:
        self.initialize()
        now = _utc_now()
        ai_jobs = self._ai_job_snapshot()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                ingested = self._ingest_revisions(connection)
                recovered_links = self._recover_unlinked_news_jobs(
                    connection,
                    ai_jobs,
                )
                recovered_focus_links = self._recover_unlinked_focus_jobs(
                    connection,
                    ai_jobs,
                )
                analyses = self._publish_completed_news(connection, ai_jobs)
                focus_results = self._publish_completed_focus(connection, ai_jobs)
                self._audit_published_news_results(connection)
                self._audit_published_focus_results(connection)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        legacy = self._import_legacy_from_database()
        for _plan_attempt in range(3):
            plan_now = max(now, _utc_now())
            with self._connect() as connection:
                connection.execute("BEGIN")
                try:
                    base_snapshot = connection.execute(
                        """SELECT prepared_revision
                           FROM catalyst_local_hotspot_revisions
                           ORDER BY prepared_revision DESC LIMIT 1"""
                    ).fetchone()
                    base_revision = int(
                        base_snapshot["prepared_revision"] if base_snapshot else 0
                    )
                    hotspot_plan = self._plan_hotspots(connection, now=plan_now)
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    revision, hotspots, plan_committed = self._commit_hotspots(
                        connection,
                        hotspot_plan,
                        expected_base_revision=base_revision,
                        now=plan_now,
                    )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
            if plan_committed:
                break
        else:
            raise RuntimeError("hotspot_plan_contention")
        queued = 0
        if allow_scheduled_jobs and self.mode == "scheduled":
            queued = int(self.run_scheduled()["queued"])
        return {
            "ingested": ingested,
            "analysis_links_recovered": recovered_links,
            "focus_links_recovered": recovered_focus_links,
            "analyses_published": analyses,
            "focus_results_published": focus_results,
            "legacy_imported": int(legacy["imported"]),
            "legacy_rejected": int(legacy["rejected"]),
            "prepared_revision": revision,
            "hotspots": hotspots,
            "queued": queued,
        }

    def _item(
        self,
        connection: sqlite3.Connection,
        row: dict[str, Any],
        *,
        as_of: datetime,
        jobs: Mapping[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        result, available = self._analysis_for_revision(connection, row, as_of=as_of)
        if current_request_is_owner():
            job_public, _detail_job = self._linked_news_job_at(
                connection,
                row,
                as_of=as_of,
                jobs=jobs,
            )
            status = str(
                job_public.get("status") if job_public else "not_requested"
            )
        else:
            # Published local analysis is enough for the visitor view. Do not
            # read the mutable AI job store merely to expose queue state.
            status = "not_requested"
        if result is not None:
            status = "completed"
        elif status == "completed":
            status = "pending"
        item = {
            "news_id": int(row["news_id"]),
            "change_sequence": int(row["change_sequence"]),
            "content_hash": str(row["content_hash"]),
            "source": str(row["source"]),
            "title": str(result.get("title_zh") if result else TITLE_WAITING),
            "title_zh": str(result.get("title_zh") if result else TITLE_WAITING),
            "summary": str(result.get("headline_summary") if result else SUMMARY_WAITING),
            "summary_zh": str(result.get("summary_zh") if result else SUMMARY_WAITING),
            "url": str(row["url"]),
            "image_url": row.get("image_url"),
            "published_at": row.get("published_at"),
            "fetched_at": row.get("fetched_at"),
            "updated_at": row.get("source_available_at"),
            "source_tickers": list(row.get("canonical_tickers") or []),
            "analysis_status": status,
            "analysis": result,
            "analyzed_at": available,
            "available_at": available,
            "is_stale": False,
        }
        if result:
            item.update(
                {
                    "classification": result.get("classification"),
                    "confidence": result.get("confidence"),
                    "market_relevance": result.get("market_relevance"),
                    "overall_sentiment": result.get("overall_sentiment"),
                    "trusted_stock_impacts": result.get("affected_stocks") or [],
                }
            )
        return item

    def status(
        self,
        *,
        now: datetime | None = None,
        include_manual_refreshes: bool | None = None,
    ) -> dict[str, Any]:
        observed = now or _utc_now()
        if include_manual_refreshes is None:
            include_manual_refreshes = current_request_is_owner()
        if self.mode == "off":
            return {
                "enabled": False,
                "status": "disabled",
                "as_of": _iso(observed),
                "data_through": None,
                "last_sync_at": None,
                "remote_status": None,
                "analysis_trigger_enabled": False,
                "model": self.model,
                "reasoning": self.reasoning,
                "execution_mode": EXECUTION_MODE,
                "expected_model": self.model,
                "expected_reasoning": self.reasoning,
                "schema_version": SCHEMA_VERSION,
                "sources": [],
                "streams": {},
                "manual_refreshes": (
                    self._empty_manual_refreshes("disabled")
                    if include_manual_refreshes
                    else {}
                ),
                "warnings": [],
            }
        if not self.db_path.is_file():
            return {
                "enabled": True,
                "status": "unavailable",
                "as_of": _iso(observed),
                "data_through": None,
                "last_sync_at": None,
                "remote_status": None,
                "analysis_trigger_enabled": False,
                "model": self.model,
                "reasoning": self.reasoning,
                "execution_mode": EXECUTION_MODE,
                "expected_model": self.model,
                "expected_reasoning": self.reasoning,
                "schema_version": SCHEMA_VERSION,
                "sources": [],
                "streams": {},
                "manual_refreshes": (
                    self._empty_manual_refreshes("unavailable")
                    if include_manual_refreshes
                    else {}
                ),
                "warnings": ["cache_unavailable"],
            }
        try:
            with self._connect() as connection:
                states = connection.execute(
                    "SELECT * FROM macrolens_etl_state ORDER BY stream"
                ).fetchall()
        except sqlite3.Error:
            states = []
        streams = {
            str(row["stream"]): {
                "last_success_at": row["last_success_at"],
                "data_through": row["completed_as_of"],
                "consecutive_failures": 1 if row["last_error_code"] else 0,
                "last_error_code": row["last_error_code"],
                "remote_status": "ok" if row["last_success_at"] else "unavailable",
            }
            for row in states
        }
        successes = [str(row["last_success_at"]) for row in states if row["last_success_at"]]
        through = [str(row["completed_as_of"]) for row in states if row["completed_as_of"]]
        ready = bool(states) and all(row["last_success_at"] for row in states)
        if include_manual_refreshes:
            try:
                manual_refreshes = self.manual_refresh_statuses(now=observed)
            except sqlite3.Error:
                manual_refreshes = self._empty_manual_refreshes("unavailable")
        else:
            # Visitor reads must not recover stale operations or expose Owner
            # request identifiers. This helper opens a write transaction.
            manual_refreshes = {}
        return {
            "enabled": True,
            "status": "active" if ready else "degraded",
            "as_of": _iso(observed),
            "data_through": min(through) if through else None,
            "last_sync_at": max(successes) if successes else None,
            "remote_status": "ok" if ready else "degraded",
            "analysis_trigger_enabled": self.mode in {"manual", "scheduled"},
            "model": self.model,
            "reasoning": self.reasoning,
            "execution_mode": EXECUTION_MODE,
            "expected_model": self.model,
            "expected_reasoning": self.reasoning,
            "schema_version": SCHEMA_VERSION,
            "sources": [],
            "streams": streams,
            "manual_refreshes": manual_refreshes,
            "warnings": [] if ready else ["first_sync_pending"],
        }

    def feed(self, **kwargs: Any) -> dict[str, Any]:
        as_of = kwargs.get("as_of") or _utc_now()
        if not isinstance(as_of, datetime) or as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        window_hours = int(kwargs.get("window_hours") or 72)
        limit = min(100, max(1, int(kwargs.get("limit") or 50)))
        query_hash = _sha(
            {
                key: kwargs.get(key)
                for key in (
                    "window_hours",
                    "ticker",
                    "source",
                    "classification",
                    "analysis_status",
                    "min_confidence",
                    "include_unanalyzed",
                    "min_abs_impact",
                    "include_neutral",
                    "horizon",
                    "mechanism",
                    "multi_source_only",
                )
            }
        )
        offset, cursor_anchor = _cursor_decode(kwargs.get("cursor"), query_hash)
        if cursor_anchor is not None:
            parsed_anchor = _parse_time(cursor_anchor)
            assert parsed_anchor is not None
            as_of = parsed_anchor
        anchor = _iso(as_of)
        with self._connect() as connection:
            rows = self._active_revisions(connection, as_of=as_of, window_hours=window_hours)
            jobs = (
                self._ai_job_snapshot(
                    job_ids=(
                        str(row["analysis_job_id"])
                        for row in rows
                        if row.get("analysis_job_id") is not None
                    )
                )
                if current_request_is_owner()
                else None
            )
            items = [
                self._item(connection, row, as_of=as_of, jobs=jobs)
                for row in rows
            ]
        ticker = str(kwargs.get("ticker") or "").upper()
        source = str(kwargs.get("source") or "").casefold()
        classification = kwargs.get("classification")
        analysis_status = kwargs.get("analysis_status")
        min_confidence = int(kwargs.get("min_confidence") or 0)
        min_abs_impact = kwargs.get("min_abs_impact")
        horizon = kwargs.get("horizon")
        mechanism = kwargs.get("mechanism")
        multi_source_only = bool(kwargs.get("multi_source_only"))
        rows_by_news_id = {
            int(row["news_id"]): row
            for row in rows
        }
        filtered: list[dict[str, Any]] = []
        for item in items:
            result = item.get("analysis") or {}
            if ticker and ticker not in item.get("source_tickers", []):
                if not any(stock.get("ticker") == ticker for stock in result.get("affected_stocks") or []):
                    continue
            if source and source not in str(item.get("source") or "").casefold():
                continue
            if classification and result.get("classification") != classification:
                continue
            if analysis_status and item.get("analysis_status") != analysis_status:
                continue
            if not kwargs.get("include_unanalyzed", True) and not result:
                continue
            if result and int(result.get("confidence") or 0) < min_confidence:
                continue
            if not kwargs.get("include_neutral", True) and result.get("classification") == "neutral":
                continue
            impacts = result.get("affected_stocks") or []
            if min_abs_impact is not None and not any(abs(int(value.get("impact_score") or 0)) >= int(min_abs_impact) for value in impacts):
                continue
            if horizon and not any(value.get("horizon") == horizon for value in impacts):
                continue
            if mechanism and not any(value.get("mechanism") == mechanism for value in impacts):
                continue
            if multi_source_only:
                row = rows_by_news_id.get(int(item["news_id"]))
                if not row or int(row.get("source_count") or 0) < 2:
                    continue
            filtered.append(item)
        page = filtered[offset : offset + limit]
        has_more = offset + limit < len(filtered)
        analyzed = [item for item in filtered if item.get("analysis")]
        return {
            "status": "active" if page else "empty",
            "as_of": anchor,
            "data_through": self.status(now=as_of).get("data_through"),
            "items": page,
            "summary": {
                "news_6h": sum(1 for item in filtered if (_parse_time(item.get("published_at")) or as_of) >= as_of - timedelta(hours=6)),
                "analyzed_24h": _recent_analysis_count(analyzed, as_of=as_of),
                "bullish": sum(1 for item in analyzed if item.get("classification") == "bullish"),
                "bearish": sum(1 for item in analyzed if item.get("classification") == "bearish"),
                "pending": sum(1 for item in filtered if not item.get("analysis")),
                "high_impact_macro": None,
            },
            "stock_impacts": [],
            "next_cursor": (
                _cursor_encode(offset + limit, anchor, query_hash) if has_more else None
            ),
            "has_more": has_more,
            "warnings": [],
        }

    def news(self, news_id: int, *, as_of: datetime) -> dict[str, Any] | None:
        include_owner_state = current_request_is_owner()
        with self._connect() as connection:
            row = self._active_revision(
                connection,
                news_id=news_id,
                as_of=as_of,
            )
            if row is None:
                return None
            item = self._item(connection, row, as_of=as_of)
            if include_owner_state:
                _job_at_time, detail_job = self._linked_news_job_at(
                    connection,
                    row,
                    as_of=as_of,
                )
                history_links = connection.execute(
                    """SELECT job_id FROM catalyst_local_analysis_links
                       WHERE news_id=? AND change_sequence=? AND content_hash=?
                         AND created_at<=?
                       ORDER BY created_at DESC,job_id DESC""",
                    (
                        row["news_id"],
                        row["change_sequence"],
                        row["content_hash"],
                        _iso(as_of),
                    ),
                ).fetchall()
            else:
                detail_job = None
                history_links = []
        analysis_revisions: list[dict[str, Any]] = []
        for link in history_links:
            job = self._read_ai_job(str(link["job_id"]))
            public = self._verified_public_job(job, expected_type="news_impact")
            if public is None or public.get("status") != "completed":
                continue
            completed_at = _parse_time(str(public.get("completed_at") or ""))
            if completed_at is None or completed_at > as_of:
                continue
            analysis_revisions.append(public)
        return {
            "status": "active",
            "as_of": _iso(as_of),
            "item": item,
            "analysis_job": detail_job,
            "analysis_revisions": analysis_revisions,
            "analysis_trigger_enabled": self.mode in {"manual", "scheduled"},
        }

    def ticker(self, ticker: str, **kwargs: Any) -> dict[str, Any]:
        normalized = ticker.strip().upper()
        result = self.feed(ticker=normalized, **kwargs)
        result["ticker"] = normalized
        return result

    def batch(self, tickers: Sequence[str], **kwargs: Any) -> dict[str, Any]:
        as_of = kwargs.get("as_of") or _utc_now()
        if not isinstance(as_of, datetime) or as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        window_hours = int(kwargs.get("window_hours") or 72)
        limit = min(100, max(1, int(kwargs.get("limit") or 20)))
        min_confidence = int(kwargs.get("min_confidence") or 0)
        include_unanalyzed = bool(kwargs.get("include_unanalyzed", True))
        include_neutral = bool(kwargs.get("include_neutral", False))
        with self._connect() as connection:
            rows = self._active_revisions(
                connection,
                as_of=as_of,
                window_hours=window_hours,
            )
            jobs = (
                self._ai_job_snapshot(
                    job_ids=(
                        str(row["analysis_job_id"])
                        for row in rows
                        if row.get("analysis_job_id") is not None
                    )
                )
                if current_request_is_owner()
                else None
            )
            items = [
                self._item(connection, row, as_of=as_of, jobs=jobs)
                for row in rows
            ]
        data_through = self.status(now=as_of).get("data_through")
        results: dict[str, dict[str, Any]] = {}
        for raw_ticker in tickers:
            ticker = raw_ticker.strip().upper()
            filtered: list[dict[str, Any]] = []
            for item in items:
                result = item.get("analysis") or {}
                affected = result.get("affected_stocks") or []
                if ticker not in item.get("source_tickers", []) and not any(
                    stock.get("ticker") == ticker
                    for stock in affected
                    if isinstance(stock, dict)
                ):
                    continue
                if not include_unanalyzed and not result:
                    continue
                if result and int(result.get("confidence") or 0) < min_confidence:
                    continue
                if not include_neutral and result.get("classification") == "neutral":
                    continue
                filtered.append(item)
            page = filtered[:limit]
            analyzed = [item for item in filtered if item.get("analysis")]
            results[ticker] = {
                "ticker": ticker,
                "status": "active" if page else "empty",
                "as_of": _iso(as_of),
                "data_through": data_through,
                "items": page,
                "summary": {
                    "news_6h": sum(
                        1
                        for item in filtered
                        if (_parse_time(item.get("published_at")) or as_of)
                        >= as_of - timedelta(hours=6)
                    ),
                    "analyzed_24h": _recent_analysis_count(
                        analyzed,
                        as_of=as_of,
                    ),
                    "bullish": sum(
                        1
                        for item in analyzed
                        if item.get("classification") == "bullish"
                    ),
                    "bearish": sum(
                        1
                        for item in analyzed
                        if item.get("classification") == "bearish"
                    ),
                    "pending": sum(
                        1 for item in filtered if not item.get("analysis")
                    ),
                    "high_impact_macro": None,
                },
                "stock_impacts": [],
                "next_cursor": None,
                "has_more": len(filtered) > limit,
                "warnings": [],
            }
        return {
            "as_of": _iso(as_of),
            "status": "active",
            "results": results,
            "warnings": [],
        }

    def calendar(
        self,
        *,
        date_from: date,
        date_to: date,
        as_of: datetime,
        currencies: Sequence[str] | None,
        min_impact: str | None,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            snapshot = connection.execute(
                """SELECT * FROM macrolens_etl_calendar_snapshots
                   WHERE complete=1 AND as_of<=? ORDER BY snapshot_sequence DESC LIMIT 1""",
                (_iso(as_of),),
            ).fetchone()
            if snapshot is None:
                rows: list[sqlite3.Row] = []
            else:
                rows = connection.execute(
                    """SELECT raw_json FROM macrolens_etl_calendar_events
                       WHERE snapshot_sequence=? ORDER BY ordinal""",
                    (snapshot["snapshot_sequence"],),
                ).fetchall()
        rank = {"low": 1, "medium": 2, "high": 3, "holiday": 4}
        wanted = {str(value).upper() for value in currencies or []}
        items: list[dict[str, Any]] = []
        for row in rows:
            event = _loads(row["raw_json"], {})
            scheduled = _parse_time(event.get("scheduled_at_utc"))
            if scheduled is None or not date_from <= scheduled.date() <= date_to:
                continue
            currency = str(event.get("currency") or event.get("country_code") or "").upper()
            impact = str(event.get("impact") or "low").lower()
            if wanted and currency not in wanted:
                continue
            if min_impact and rank.get(impact, 0) < rank.get(min_impact, 0):
                continue
            impact_zh = _public_chinese_text(
                event.get("impact_zh"),
                {
                    "low": "低",
                    "medium": "中",
                    "high": "高",
                    "holiday": "假日",
                }.get(impact, "未知"),
            )
            items.append(
                {
                    "event_id": event.get("event_id"),
                    "country_code": event.get("country_code"),
                    "country": _public_chinese_text(
                        event.get("country"),
                        "未知地区",
                    ),
                    "title": _public_calendar_title(event.get("title")),
                    "impact": impact,
                    "impact_zh": impact_zh,
                    "scheduled_at": event.get("scheduled_at_utc"),
                    "scheduled_at_utc": event.get("scheduled_at_utc"),
                    "forecast": event.get("forecast"),
                    "previous": event.get("previous"),
                    "actual": event.get("actual"),
                    "is_stale": bool(event.get("is_stale")),
                    "source_fetched_at": event.get("source_fetched_at"),
                    "available_at": event.get("available_at"),
                    "ordinal": event.get("ordinal"),
                    "currency": currency,
                }
            )
        return {
            "status": "active" if items else "empty",
            "as_of": _iso(as_of),
            "data_through": snapshot["data_through"] if snapshot else None,
            "items": items,
            "warnings": [],
        }

    def hotspot_status(self, *, now: datetime | None = None) -> dict[str, Any]:
        observed = now or _utc_now()
        with self._connect() as connection:
            revision = connection.execute(
                """SELECT * FROM catalyst_local_hotspot_revisions
                   WHERE prepared_at<=? ORDER BY prepared_revision DESC LIMIT 1""",
                (_iso(observed),),
            ).fetchone()
            last_cycle = connection.execute(
                """SELECT * FROM catalyst_local_focus_cycles
                   WHERE created_at<=? ORDER BY created_at DESC LIMIT 1""",
                (_iso(observed),),
            ).fetchone()
            consumed_cycle = connection.execute(
                """SELECT MAX(prepared_revision) AS prepared_revision
                   FROM catalyst_local_focus_cycles
                   WHERE status='completed' AND completed_at<=?
                     AND COALESCE(json_extract(payload_json,'$.force'),0)=0
                """,
                (_iso(observed),),
            ).fetchone()
        prepared_revision = int(revision["prepared_revision"]) if revision else 0
        last_consumed = (
            int(consumed_cycle["prepared_revision"])
            if consumed_cycle is not None
            and consumed_cycle["prepared_revision"] is not None
            else 0
        )
        count = int(revision["item_count"]) if revision else 0
        return {
            "prepared_revision": prepared_revision,
            "last_consumed_revision": last_consumed,
            "has_new_hotspots": prepared_revision > last_consumed and count > 0,
            "prepared_hot_count": count,
            "prepared_since": revision["prepared_at"] if revision else None,
            "last_cycle_at": last_cycle["created_at"] if last_cycle else None,
            "next_scheduled_at": None,
            "model": self.model,
            "reasoning": self.reasoning,
            "data_through": revision["data_through"] if revision else None,
            "status": "active" if revision else "empty",
            "as_of": _iso(observed),
            "last_sync_at": self.status(now=observed).get("last_sync_at"),
            "manual_enabled": self.mode in {"manual", "scheduled"},
            "warnings": [],
        }

    def hotspots(self, *, limit: int, now: datetime | None = None) -> dict[str, Any]:
        observed = now or _utc_now()
        with self._connect() as connection:
            revision = connection.execute(
                """SELECT * FROM catalyst_local_hotspot_revisions
                   WHERE prepared_at<=? ORDER BY prepared_revision DESC LIMIT 1""",
                (_iso(observed),),
            ).fetchone()
            if revision is None:
                rows: list[sqlite3.Row] = []
            else:
                rows = connection.execute(
                    """SELECT g.*,i.prepared_revision FROM catalyst_local_hotspot_items i
                       JOIN catalyst_local_event_groups g
                         ON g.event_group_id=i.event_group_id
                        AND g.event_group_version=i.event_group_version
                       WHERE i.prepared_revision=? AND g.available_at<=?
                       ORDER BY i.ordinal LIMIT ?""",
                    (
                        revision["prepared_revision"],
                        _iso(observed),
                        min(100, max(1, limit)),
                    ),
                ).fetchall()
        items = self._project_hotspot_rows(
            rows,
            prepared_at=revision["prepared_at"] if revision else None,
        )
        return {
            "status": "active" if items else "empty",
            "as_of": _iso(observed),
            "data_through": revision["data_through"] if revision else None,
            "items": items,
            "warnings": [],
        }

    @staticmethod
    def _project_hotspot_rows(
        rows: Sequence[sqlite3.Row],
        *,
        prepared_at: str | None,
    ) -> list[dict[str, Any]]:
        return [
            {
                "prepared_revision": int(row["prepared_revision"]),
                "event_group_id": str(row["event_group_id"]),
                "event_group_version": int(row["event_group_version"]),
                "gate_version": SCHEMA_VERSION,
                "hot_score": float(row["hot_score"]),
                "component_scores": _loads(row["component_scores_json"], {}),
                "reasons": _loads(row["reasons_json"], []),
                "status": "prepared",
                "prepared_at": prepared_at,
                "representative_title": str(row["representative_title_zh"]),
                "event_type": str(row["event_type"]),
                "available_at": str(row["available_at"]),
                "first_published_at": row["first_published_at"],
                "last_published_at": row["last_published_at"],
                "source_count": int(row["source_count"]),
                "source_names": _loads(row["source_names_json"], []),
                "validated_tickers": _loads(row["validated_tickers_json"], []),
                "summary_zh": str(row["representative_summary_zh"]),
                "representative_news_id": int(row["representative_news_id"]),
            }
            for row in rows
        ]

    def _hotspots_for_revision(
        self,
        revision: int,
        *,
        limit: int,
    ) -> tuple[sqlite3.Row | None, list[dict[str, Any]]]:
        with self._connect() as connection:
            revision_row = connection.execute(
                """SELECT * FROM catalyst_local_hotspot_revisions
                   WHERE prepared_revision=?""",
                (revision,),
            ).fetchone()
            if revision_row is None:
                return None, []
            rows = connection.execute(
                """SELECT g.*,i.prepared_revision FROM catalyst_local_hotspot_items i
                   JOIN catalyst_local_event_groups g
                     ON g.event_group_id=i.event_group_id
                    AND g.event_group_version=i.event_group_version
                   WHERE i.prepared_revision=?
                   ORDER BY i.ordinal LIMIT ?""",
                (revision, min(100, max(1, limit))),
            ).fetchall()
        return revision_row, self._project_hotspot_rows(
            rows,
            prepared_at=str(revision_row["prepared_at"]),
        )

    def _current_revision(self, news_id: int, *, now: datetime | None = None) -> dict[str, Any] | None:
        observed = now or _utc_now()
        with self._connect() as connection:
            return self._active_revision(
                connection,
                news_id=news_id,
                as_of=observed,
            )

    def _unlinked_manual_force_job(
        self,
        row: Mapping[str, Any],
        *,
        expected_analysis_revision: int,
    ) -> dict[str, Any] | None:
        jobs = self._ai_job_snapshot(allow_write_contention=False)
        if not jobs:
            return None
        with self._connect() as connection:
            linked = {
                str(item["job_id"])
                for item in connection.execute(
                    """SELECT job_id FROM catalyst_local_analysis_links
                       WHERE news_id=? AND change_sequence=?
                         AND content_hash=?""",
                    (
                        int(row["news_id"]),
                        int(row["change_sequence"]),
                        str(row["content_hash"]),
                    ),
                ).fetchall()
            }
        candidates = sorted(
            jobs.values(),
            key=lambda job: (
                str(job.get("created_at") or ""),
                str(job.get("job_id") or ""),
            ),
            reverse=True,
        )
        for job in candidates:
            job_id = str(job.get("job_id") or "")
            created_at = _parse_time(str(job.get("created_at") or ""))
            if (
                not job_id
                or created_at is None
                or job_id in linked
                or job.get("job_type") != "news_impact"
                or job.get("submission_source") != "manual"
            ):
                continue
            payload = self._job_payload(job)
            if (
                payload is None
                or "manual_force_bucket" not in payload
                or not self._news_payload_matches_revision(payload, row)
                or payload.get("analysis_revision") != expected_analysis_revision
            ):
                continue
            try:
                public = self._identity_public_job(
                    job,
                    expected_type="news_impact",
                )
            except (KeyError, TypeError, ValueError):
                continue
            if public is not None:
                return job
        return None

    def _link_analysis_job(
        self,
        row: Mapping[str, Any],
        job: dict[str, Any],
    ) -> bool:
        try:
            with self._connect() as connection:
                connection.execute(
                    f"PRAGMA busy_timeout={ANALYSIS_LINK_BUSY_TIMEOUT_MS}"
                )
                connection.execute("BEGIN IMMEDIATE")
                try:
                    self._ensure_news_job_link(connection, row, job)
                    if job.get("status") == "completed":
                        self._publish_completed_news(
                            connection,
                            {str(job["job_id"]): job},
                            target_job_id=str(job["job_id"]),
                        )
                    exact = connection.execute(
                        """SELECT 1 FROM catalyst_local_analysis_links
                           WHERE job_id=? AND news_id=? AND change_sequence=?
                             AND content_hash=?""",
                        (
                            str(job["job_id"]),
                            int(row["news_id"]),
                            int(row["change_sequence"]),
                            str(row["content_hash"]),
                        ),
                    ).fetchone()
                    if exact is None:
                        raise RuntimeError("news_job_link_missing")
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
        except sqlite3.OperationalError as error:
            if not _is_sqlite_write_contention(error):
                raise
            return False
        return True

    def _linked_recoverable_completed_legacy_news_job(
        self,
        row: Mapping[str, Any],
        *,
        jobs: Mapping[str, dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        available_jobs = (
            jobs
            if jobs is not None
            else self._ai_job_snapshot(
                allow_write_contention=False,
                news_ids=(int(row["news_id"]),),
            )
        )
        target_key = (
            int(row["news_id"]),
            int(row["change_sequence"]),
            str(row["content_hash"]),
        )
        if target_key in self._current_news_job_revision_keys(
            available_jobs.values()
        ):
            return None
        with self._connect() as connection:
            linked_job_ids = {
                str(link["job_id"])
                for link in connection.execute(
                    """SELECT job_id FROM catalyst_local_analysis_links
                       WHERE news_id=? AND change_sequence=? AND content_hash=?""",
                    target_key,
                ).fetchall()
            }
        candidates: list[tuple[str, str, dict[str, Any], dict[str, Any]]] = []
        for job_id in linked_job_ids:
            job = available_jobs.get(job_id)
            public = self._recoverable_completed_legacy_news_public_job(job)
            if (
                job is None
                or public is None
                or not self._news_payload_matches_revision(
                    self._job_payload(job) or {},
                    row,
                )
            ):
                continue
            candidates.append(
                (
                    str(job.get("completed_at") or job.get("updated_at") or ""),
                    job_id,
                    job,
                    public,
                )
            )
        if not candidates:
            return None
        _completed_at, _job_id, job, public = max(candidates)
        return job, public

    def request_analysis(
        self,
        news_id: int,
        *,
        force: bool,
        as_of: datetime | None = None,
        expected_change_sequence: int | None = None,
        expected_content_hash: str | None = None,
        submission_source: SubmissionSource = "manual",
        _job_snapshot: Mapping[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if self.mode not in {"manual", "scheduled"}:
            raise CatalystError(
                "read_only_mode",
                "News analysis is disabled in read mode",
                counts_for_circuit=False,
            )
        observed = as_of or _utc_now()
        row = self._current_revision(news_id, now=observed)
        if row is None:
            raise CatalystError("news_not_found", "News item is not in the local ETL store", counts_for_circuit=False)
        if (
            expected_change_sequence is not None
            and int(row["change_sequence"]) != expected_change_sequence
        ) or (
            expected_content_hash is not None
            and str(row["content_hash"]) != expected_content_hash
        ):
            raise CatalystError(
                "news_revision_changed",
                "The selected news revision changed before analysis was queued",
                counts_for_circuit=False,
            )
        payload = {
            "news_id": int(row["news_id"]),
            "change_sequence": int(row["change_sequence"]),
            "content_hash": str(row["content_hash"]),
            "source": str(row["source"]),
            "title": str(row["raw_title"]),
            "summary": row.get("raw_summary"),
            "url": str(row["url"]),
            "published_at": row.get("published_at"),
            "fetched_at": row.get("fetched_at"),
            "sources": list(row.get("source_names") or []),
            "source_count": int(row.get("source_count") or 1),
            "source_ticker_hints": _loads(row.get("source_tickers_json"), []),
            "allowed_tickers": list(row.get("canonical_tickers") or []),
            "analysis_revision": 1,
        }
        scheduled_retry = bool(
            force
            and expected_change_sequence is not None
            and expected_content_hash is not None
        )
        if force and not scheduled_retry:
            bucket = _minute_bucket(observed)
            with self._connect() as connection:
                links = connection.execute(
                    """SELECT job_id FROM catalyst_local_analysis_links
                       WHERE news_id=? AND change_sequence=? AND content_hash=?
                       ORDER BY created_at,job_id""",
                    (
                        row["news_id"],
                        row["change_sequence"],
                        row["content_hash"],
                    ),
                ).fetchall()
            highest_revision = 1 if links else 0
            for link in links:
                linked_job = self.ai_repository.get_job(str(link["job_id"]))
                if linked_job is None:
                    continue
                linked_payload = self._job_payload(linked_job) or {}
                revision_value = linked_payload.get("analysis_revision")
                if isinstance(revision_value, int) and revision_value >= 1:
                    highest_revision = max(highest_revision, revision_value)
                public = self._identity_public_job(
                    linked_job,
                    expected_type="news_impact",
                )
                if public is None:
                    continue
                if public["status"] in {"pending", "queued", "in_progress"}:
                    return public
                if linked_payload.get("manual_force_bucket") == bucket:
                    return public
            orphan = self._unlinked_manual_force_job(
                row,
                expected_analysis_revision=highest_revision + 1,
            )
            if orphan is not None:
                public = AIJobRepository.public(
                    orphan,
                    cached=orphan.get("status") == "completed",
                )
                if not self._link_analysis_job(row, orphan):
                    public["local_link_pending"] = True
                return public
            payload["analysis_revision"] = highest_revision + 1
            payload["manual_force_bucket"] = bucket
        if not force and bool(row.get("has_analysis_links")):
            legacy = self._linked_recoverable_completed_legacy_news_job(
                row,
                jobs=_job_snapshot,
            )
            if legacy is not None:
                legacy_job, legacy_public = legacy
                public = dict(legacy_public)
                public["cached"] = True
                if not self._link_analysis_job(row, legacy_job):
                    public["local_link_pending"] = True
                return public
        schema_version, schema_hash = ai_runtime.schema_identity("news_impact")
        job, created = self.ai_repository.create_job(
            job_type="news_impact",
            payload=payload,
            model=self.model,
            reasoning=self.reasoning,
            execution_mode=EXECUTION_MODE,
            prompt_version=NEWS_PROMPT_VERSION,
            schema_version=schema_version,
            schema_sha256=schema_hash,
            max_queued=self.max_queued,
            submission_source=submission_source,
            priority=70,
            # A forced request is a new immutable analysis revision. The
            # minute bucket above keeps repeated confirmation clicks idempotent.
            force_retry=scheduled_retry,
        )
        public = AIJobRepository.public(
            job,
            cached=(not created and job["status"] == "completed"),
        )
        if not self._link_analysis_job(row, job):
            public["local_link_pending"] = True
        return public

    def analysis_job(self, job_id: str) -> dict[str, Any] | None:
        row = self._read_ai_job(job_id)
        return self._identity_public_job(row, expected_type="news_impact")

    def cancel_analysis_job(self, job_id: str) -> dict[str, Any] | None:
        row = self.ai_repository.get_job(job_id)
        if row is None or row.get("job_type") != "news_impact":
            return None
        updated = self.ai_repository.request_cancel(job_id)
        return AIJobRepository.public(updated) if updated else None

    @staticmethod
    def _scheduled_slot(
        now: datetime,
        scheduled_times_et: Sequence[str],
    ) -> tuple[str, str] | None:
        eastern = ZoneInfo("America/New_York")
        local_now = now.astimezone(eastern)
        candidates: list[datetime] = []
        for value in scheduled_times_et:
            try:
                hour_text, minute_text = str(value).split(":", 1)
                hour, minute = int(hour_text), int(minute_text)
            except (TypeError, ValueError):
                continue
            if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                continue
            candidate = local_now.replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            )
            if candidate <= local_now:
                candidates.append(candidate)
        if not candidates:
            return None
        scheduled = max(candidates)
        # The worker normally wakes every 30 minutes. A bounded two-hour grace
        # allows one delayed run without replaying yesterday's last slot.
        if local_now - scheduled > timedelta(hours=2):
            return None
        scheduled_utc = scheduled.astimezone(timezone.utc)
        scheduled_text = _iso(scheduled_utc)
        return scheduled_text, scheduled_text

    def _scheduled_news_candidates(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Select recent active revisions without a published Chinese result."""

        if limit <= 0:
            return []
        with self._connect() as connection:
            try:
                rows = connection.execute(
                    """WITH latest AS (
                           SELECT news_id,MAX(change_sequence) AS change_sequence
                           FROM macrolens_etl_news_changes
                           WHERE available_at<=? GROUP BY news_id
                       ), active AS (
                           SELECT c.news_id,c.change_sequence
                           FROM latest l JOIN macrolens_etl_news_changes c
                             ON c.news_id=l.news_id
                            AND c.change_sequence=l.change_sequence
                           WHERE c.operation='upsert' AND c.available_at<=?
                       )
                       SELECT r.*,
                              EXISTS(
                                  SELECT 1
                                  FROM catalyst_local_analysis_links existing
                                  WHERE existing.news_id=r.news_id
                                    AND existing.change_sequence=r.change_sequence
                                    AND existing.content_hash=r.content_hash
                              ) AS has_analysis_links
                       FROM active a
                       JOIN catalyst_local_news_revisions r
                         ON r.news_id=a.news_id
                        AND r.change_sequence=a.change_sequence
                       WHERE COALESCE(r.published_at,r.fetched_at)>=?
                         AND NOT EXISTS (
                             SELECT 1 FROM catalyst_local_analysis_links link
                             WHERE link.news_id=r.news_id
                               AND link.change_sequence=r.change_sequence
                               AND link.content_hash=r.content_hash
                               AND link.result_json IS NOT NULL
                         )
                       ORDER BY COALESCE(r.published_at,r.fetched_at) DESC,
                                r.news_id DESC
                       LIMIT ?""",
                    (
                        _iso(now),
                        _iso(now),
                        _iso(now - timedelta(hours=SCHEDULED_NEWS_WINDOW_HOURS)),
                        min(100, int(limit)),
                    ),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
        output: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["canonical_tickers"] = _loads(
                item["canonical_tickers_json"],
                [],
            )
            item["source_names"] = _loads(item["source_names_json"], [])
            output.append(item)
        return output

    def _scheduled_job_for_revision(
        self,
        row: dict[str, Any],
        *,
        now: datetime,
        jobs: Mapping[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            revision_row = connection.execute(
                """SELECT news_id,change_sequence,content_hash,source,raw_title,
                          raw_summary,url,published_at,fetched_at,source_names_json,
                          source_count,source_tickers_json,
                          canonical_tickers_json
                   FROM catalyst_local_news_revisions
                   WHERE news_id=? AND change_sequence=? AND content_hash=?""",
                (
                    row["news_id"],
                    row["change_sequence"],
                    row["content_hash"],
                ),
            ).fetchone()
            linked_job_ids = {
                str(link["job_id"])
                for link in connection.execute(
                    """SELECT job_id FROM catalyst_local_analysis_links
                       WHERE news_id=? AND change_sequence=? AND content_hash=?
                         AND created_at<=?
                       ORDER BY created_at,job_id""",
                    (
                        row["news_id"],
                        row["change_sequence"],
                        row["content_hash"],
                        _iso(now),
                    ),
                ).fetchall()
            }
        if revision_row is None:
            return None
        matching: list[
            tuple[datetime, int, int, str, dict[str, Any], dict[str, Any]]
        ] = []
        for job in jobs.values():
            if job.get("job_type") != "news_impact":
                continue
            payload = self._job_payload(job)
            created_at = _parse_time(str(job.get("created_at") or ""))
            if (
                payload is None
                or created_at is None
                or created_at > now
                or not self._news_payload_matches_revision(
                    payload,
                    revision_row,
                )
            ):
                continue
            try:
                public = self._identity_public_job(
                    job,
                    expected_type="news_impact",
                )
                current_identity = public is not None
                if public is None:
                    public = self._compatible_news_public_job(job)
            except (KeyError, TypeError, ValueError):
                continue
            if public is None:
                continue
            matching.append(
                (
                    created_at,
                    int(current_identity),
                    int(job.get("execution_number") or 0),
                    str(job.get("job_id") or ""),
                    job,
                    public,
                )
            )
        if not matching:
            return None
        matching.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
        (
            _created_at,
            _current_identity,
            _execution,
            job_id,
            latest_job,
            latest_public,
        ) = matching[-1]
        scheduled_attempts = sum(
            1
            for _created, _current, _execution, _job_id, job, _public in matching
            if job.get("submission_source") == "scheduled"
            and job.get("status") != "budget_blocked"
        )
        output = dict(latest_public)
        updated_at = _parse_time(str(latest_job.get("updated_at") or ""))
        if updated_at is not None and updated_at > now:
            submitted_at = _parse_time(
                str(latest_job.get("submitted_at") or "")
            )
            output.update(
                {
                    "status": (
                        "in_progress"
                        if submitted_at is not None and submitted_at <= now
                        else "pending"
                    ),
                    "completed_at": None,
                    "error_code": None,
                    "result": None,
                    "cached": False,
                    "cancellable": False,
                }
            )
        output["_scheduled_attempts"] = scheduled_attempts
        if job_id not in linked_job_ids:
            output["local_link_pending"] = True
            return output
        raw_result = latest_job.get("result_json")
        if (
            output.get("status") == "completed"
            and isinstance(raw_result, str)
            and raw_result
        ):
            digest = hashlib.sha256(raw_result.encode("utf-8")).hexdigest()
            with self._connect() as connection:
                rejected = connection.execute(
                    """SELECT 1 FROM catalyst_local_analysis_result_audit
                       WHERE job_id=? AND contract_id=? AND result_sha256=?
                         AND outcome='rejected'""",
                    (
                        job_id,
                        NEWS_RESULT_CONTRACT_ID,
                        digest,
                    ),
                ).fetchone()
            if rejected is not None:
                output["status"] = "failed"
                output["error_code"] = "schema_validation_failed"
        return output

    def run_scheduled(
        self,
        *,
        scheduled_times_et: Sequence[str] | None = None,
        now: datetime | None = None,
    ) -> dict[str, int]:
        if self.mode != "scheduled":
            return {"queued": 0, "skipped": 0}
        observed = now or _utc_now()
        slot: tuple[str, str] | None = None
        claim_marker: str | None = None
        if scheduled_times_et is not None:
            slot = self._scheduled_slot(observed, scheduled_times_et)
            if slot is None:
                return {"queued": 0, "skipped": 0}
            claim_now = _utc_now()
            claim_marker = "claim:" + _iso(claim_now)
            with self._connect() as connection:
                claimed = connection.execute(
                    """INSERT OR IGNORE INTO catalyst_local_schedule_runs(
                           slot_key,scheduled_for,completed_at,queued,skipped
                       ) VALUES(?,?,?,?,?)""",
                    (slot[0], slot[1], claim_marker, 0, 0),
                ).rowcount
                if claimed != 1:
                    existing = connection.execute(
                        """SELECT completed_at FROM catalyst_local_schedule_runs
                           WHERE slot_key=?""",
                        (slot[0],),
                    ).fetchone()
                    previous_marker = (
                        str(existing["completed_at"])
                        if existing is not None
                        else ""
                    )
                    claimed_at = (
                        _parse_time(previous_marker.removeprefix("claim:"))
                        if previous_marker.startswith("claim:")
                        else None
                    )
                    if (
                        claimed_at is not None
                        and (claim_now - claimed_at).total_seconds()
                        >= SCHEDULE_CLAIM_TTL_SECONDS
                    ):
                        claimed = connection.execute(
                            """UPDATE catalyst_local_schedule_runs SET
                                   scheduled_for=?,completed_at=?,queued=0,skipped=0
                               WHERE slot_key=? AND completed_at=?""",
                            (
                                slot[1],
                                claim_marker,
                                slot[0],
                                previous_marker,
                            ),
                        ).rowcount
                connection.commit()
            if claimed != 1:
                return {"queued": 0, "skipped": 0}
        snapshot = self.hotspots(limit=SCHEDULED_FOCUS_EVENT_LIMIT, now=observed)
        queue_health = self.ai_repository.health()
        active_queue = queue_health.get("pending")
        if not queue_health.get("healthy") or not isinstance(active_queue, int):
            active_queue = SCHEDULED_QUEUE_SOFT_LIMIT
        # Keep one queue position available for a ready market-focus cycle. A
        # continuous news backlog must not fill the soft limit and starve the
        # hourly aggregate indefinitely.
        batch_capacity = max(
            0,
            min(
                SCHEDULED_NEWS_BATCH_SIZE,
                SCHEDULED_QUEUE_SOFT_LIMIT - 1 - active_queue,
            ),
        )
        queued = 0
        skipped = 0
        seen: set[int] = set()
        candidates: list[dict[str, Any]] = []
        focus_pending_news_ids: set[int] = set()
        with self._connect() as connection:
            for item in snapshot["items"]:
                news_id = int(item["representative_news_id"])
                if news_id in seen:
                    continue
                seen.add(news_id)
                revision = self._active_revision(
                    connection,
                    news_id=news_id,
                    as_of=observed,
                )
                if revision is None:
                    skipped += 1
                    focus_pending_news_ids.add(news_id)
                    continue
                analysis, _available_at = self._analysis_for_revision(
                    connection,
                    revision,
                    as_of=observed,
                )
                if analysis is not None:
                    continue
                focus_pending_news_ids.add(news_id)
                candidates.append(revision)
        recent = self._scheduled_news_candidates(
            now=observed,
            limit=SCHEDULED_NEWS_BATCH_SIZE + len(seen),
        )
        for row in recent:
            if len(candidates) >= SCHEDULED_NEWS_BATCH_SIZE:
                break
            news_id = int(row["news_id"])
            if news_id in seen:
                continue
            seen.add(news_id)
            candidates.append(row)
        if not candidates and not snapshot["items"]:
            if slot is not None and claim_marker is not None:
                with self._connect() as connection:
                    connection.execute(
                        """DELETE FROM catalyst_local_schedule_runs
                           WHERE slot_key=? AND completed_at=?""",
                        (slot[0], claim_marker),
                    )
                    connection.commit()
            return {"queued": 0, "skipped": 0}
        scheduled_job_snapshot = self._ai_job_snapshot(
            allow_write_contention=False,
            news_ids={int(row["news_id"]) for row in candidates},
        )
        for row in candidates:
            news_id = int(row["news_id"])
            previous_job = self._scheduled_job_for_revision(
                row,
                now=observed,
                jobs=scheduled_job_snapshot,
            )
            if isinstance(previous_job, dict) and previous_job.get(
                "local_link_pending"
            ):
                skipped += 1
                continue
            previous_updated = (
                _parse_time(str(previous_job.get("updated_at") or ""))
                if isinstance(previous_job, dict)
                else None
            )
            retry_previous_budget = bool(
                isinstance(previous_job, dict)
                and previous_job.get("status") == "budget_blocked"
                and previous_updated is not None
                and previous_updated.date()
                < observed.astimezone(timezone.utc).date()
            )
            retry_previous_failure = bool(
                isinstance(previous_job, dict)
                and previous_job.get("status") == "failed"
                and previous_job.get("error_code")
                in SCHEDULED_NEWS_RETRYABLE_ERRORS
                and int(previous_job.get("_scheduled_attempts") or 0)
                < SCHEDULED_NEWS_MAX_ATTEMPTS
                and previous_updated is not None
                and _hour_bucket(previous_updated) < _hour_bucket(observed)
            )
            retry_previous = bool(
                retry_previous_budget
                or retry_previous_failure
            )
            if isinstance(previous_job, dict) and not retry_previous:
                status = str(previous_job.get("status") or "")
                error_code = str(previous_job.get("error_code") or "")
                attempts = int(previous_job.get("_scheduled_attempts") or 0)
                permanently_skipped = bool(
                    status in {"cancelled", "insufficient_context"}
                    or (
                        status == "completed"
                        and previous_job.get("result") is None
                    )
                    or (
                        status == "failed"
                        and (
                            error_code not in SCHEDULED_NEWS_RETRYABLE_ERRORS
                            or attempts >= SCHEDULED_NEWS_MAX_ATTEMPTS
                        )
                    )
                )
                if permanently_skipped:
                    focus_pending_news_ids.discard(news_id)
                skipped += 1
                continue
            if queued >= batch_capacity:
                skipped += 1
                continue
            try:
                job = self.request_analysis(
                    news_id,
                    force=retry_previous,
                    as_of=observed,
                    expected_change_sequence=int(row["change_sequence"]),
                    expected_content_hash=str(row["content_hash"]),
                    submission_source="scheduled",
                    _job_snapshot=scheduled_job_snapshot,
                )
            except CatalystError as error:
                if error.code == "news_revision_changed":
                    skipped += 1
                    continue
                raise
            except RuntimeError as error:
                if str(error) == "ai_job_queue_full":
                    skipped += 1
                    break
                raise
            if job.get("status") in {"pending", "queued", "in_progress"}:
                queued += 1
            else:
                skipped += 1
        prepared_revision = int(
            snapshot["items"][0]["prepared_revision"]
            if snapshot["items"]
            else 0
        )
        if (
            prepared_revision
            and not focus_pending_news_ids
            and active_queue + queued < SCHEDULED_QUEUE_SOFT_LIMIT
        ):
            with self._connect() as connection:
                existing_focus = connection.execute(
                    """SELECT 1 FROM catalyst_local_focus_cycles
                       WHERE prepared_revision=?
                         AND status IN ('pending','queued','in_progress','completed')
                       LIMIT 1""",
                    (prepared_revision,),
                ).fetchone()
            if existing_focus is None:
                retry_cycle_id, retry_blocked = self._scheduled_focus_retry_cycle(
                    prepared_revision,
                    observed=observed,
                )
                if retry_blocked:
                    skipped += 1
                else:
                    try:
                        focus = self.request_market_focus_cycle(
                            expected_prepared_revision=(
                                None
                                if retry_cycle_id is not None
                                else prepared_revision
                            ),
                            retry_cycle_id=retry_cycle_id,
                            as_of=observed,
                            submission_source="scheduled",
                        )
                    except CatalystError:
                        skipped += 1
                    else:
                        if focus.get("status") in {
                            "pending",
                            "queued",
                            "in_progress",
                        }:
                            queued += 1
                        else:
                            skipped += 1
            else:
                skipped += 1
        if slot is not None:
            with self._connect() as connection:
                connection.execute(
                    """UPDATE catalyst_local_schedule_runs
                       SET completed_at=?,queued=?,skipped=?
                       WHERE slot_key=? AND completed_at=?""",
                    (_iso(observed), queued, skipped, slot[0], claim_marker),
                )
                connection.commit()
        return {"queued": queued, "skipped": skipped}

    def _scheduled_focus_retry_cycle(
        self,
        prepared_revision: int,
        *,
        observed: datetime,
    ) -> tuple[str | None, bool]:
        """Return one failed focus cycle that is due for scheduled recovery.

        Transient provider failures may retry once per hour. Deterministic
        schema, identity and binding failures never retry automatically; a
        deployment must not turn the same paid intent into fresh work. Daily
        budget blocks wait until the next UTC date because the allowance cannot
        recover within the same day.
        """

        with self._connect() as connection:
            candidates = connection.execute(
                """SELECT cycle_id,job_id,status,error_code,payload_json,updated_at
                   FROM catalyst_local_focus_cycles
                   WHERE prepared_revision=?
                     AND status IN ('failed','budget_blocked')
                   ORDER BY updated_at DESC,created_at DESC""",
                (prepared_revision,),
            ).fetchall()
        jobs = self._ai_job_snapshot(allow_write_contention=False)
        observed_hour = _hour_bucket(observed)
        for candidate in candidates:
            job = jobs.get(str(candidate["job_id"]))
            if job is None or job.get("error_code") == "submission_outcome_unknown":
                return None, True
            if job.get("submission_source") != "scheduled":
                return None, True
            payload = _loads(candidate["payload_json"], None)
            job_payload = self._job_payload(job)
            if (
                not isinstance(payload, dict)
                or not isinstance(job_payload, dict)
                or _json(payload) != _json(job_payload)
            ):
                return None, True
            attempts = self._scheduled_focus_attempt_count(
                jobs.values(),
                payload=payload,
                observed=observed,
            )
            try:
                current_identity = self._identity_public_job(
                    job,
                    expected_type="market_focus",
                )
            except (KeyError, TypeError, ValueError):
                current_identity = None
            updated_at = _parse_time(
                str(job.get("updated_at") or candidate["updated_at"] or "")
            )
            if attempts >= SCHEDULED_FOCUS_MAX_ATTEMPTS:
                return None, True
            if str(candidate["status"]) == "budget_blocked":
                if (
                    updated_at is not None
                    and updated_at.astimezone(timezone.utc).date()
                    < observed.astimezone(timezone.utc).date()
                ):
                    return str(candidate["cycle_id"]), False
                return None, True
            error_code = str(
                job.get("error_code") or candidate["error_code"] or ""
            )
            if (
                error_code not in SCHEDULED_FOCUS_RETRYABLE_ERRORS
            ):
                return None, True
            if (
                current_identity is None
                or updated_at is None
                or _hour_bucket(updated_at) < observed_hour
            ):
                return str(candidate["cycle_id"]), False
            return None, True
        return None, False

    def _focus_calendar_events(
        self,
        *,
        as_of: datetime,
        limit: int,
    ) -> list[dict[str, Any]]:
        cutoff = as_of + timedelta(days=7)
        with self._connect() as connection:
            try:
                snapshot = connection.execute(
                    """SELECT snapshot_sequence FROM macrolens_etl_calendar_snapshots
                       WHERE complete=1 AND as_of<=?
                       ORDER BY snapshot_sequence DESC LIMIT 1""",
                    (_iso(as_of),),
                ).fetchone()
                if snapshot is None:
                    return []
                rows = connection.execute(
                    """SELECT raw_json FROM macrolens_etl_calendar_events
                       WHERE snapshot_sequence=? AND scheduled_at_utc>=?
                         AND scheduled_at_utc<=?
                       ORDER BY scheduled_at_utc,event_id LIMIT ?""",
                    (
                        snapshot["snapshot_sequence"],
                        _iso(as_of - timedelta(hours=2)),
                        _iso(cutoff),
                        min(50, max(1, limit)),
                    ),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
        output: list[dict[str, Any]] = []
        for row in rows:
            event = _loads(row["raw_json"], None)
            if not isinstance(event, dict):
                continue
            source_id = str(event.get("event_id") or "")
            if not source_id:
                continue
            output.append(
                {
                    "event_group_id": "cal_"
                    + hashlib.sha256(source_id.encode()).hexdigest()[:32],
                    "event_group_version": 1,
                    "source_event_id": source_id[:256],
                    "source_title": str(event.get("title") or "")[:2_000],
                    "scheduled_at": event.get("scheduled_at_utc"),
                    "currency": event.get("currency")
                    or event.get("country_code"),
                    "impact": event.get("impact"),
                    "event_type": "calendar",
                    "validated_tickers": [],
                }
            )
        return output

    def _cycle_with_job(
        self,
        cycle_id: str,
        *,
        job: dict[str, Any] | None = None,
        created: bool = False,
    ) -> dict[str, Any]:
        cycle = self.market_focus_cycle(cycle_id)
        assert cycle is not None
        current_job = job or self.ai_repository.get_job(str(cycle["job_id"]))
        public_job = self._identity_public_job(
            current_job,
            expected_type="market_focus",
        )
        if public_job is not None:
            public_job["cached"] = bool(
                not created and public_job["status"] == "completed"
            )
        cycle["job"] = public_job
        return cycle

    def _create_focus_job(
        self,
        payload: dict[str, Any],
        *,
        submission_source: SubmissionSource = "manual",
    ) -> tuple[dict[str, Any], bool]:
        schema_version, schema_hash = ai_runtime.schema_identity("market_focus")
        return self.ai_repository.create_job(
            job_type="market_focus",
            payload=payload,
            model=self.model,
            reasoning=self.reasoning,
            execution_mode=EXECUTION_MODE,
            prompt_version=FOCUS_PROMPT_VERSION,
            schema_version=schema_version,
            schema_sha256=schema_hash,
            max_queued=self.max_queued,
            submission_source=submission_source,
            priority=75 if submission_source == "scheduled" else 60,
            force_retry=False,
        )

    def _resume_focus_intent(
        self,
        cycle_id: str,
        *,
        submission_source: SubmissionSource = "manual",
        existing_job: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create or relink the paid job for one durable local intent.

        The local intent is committed before the AI job. If the later local
        relink commit fails, the immutable payload remains available and a
        retry asks the AI repository for that exact same job identity.
        """

        with self._connect() as connection:
            intent = connection.execute(
                "SELECT * FROM catalyst_local_focus_cycles WHERE cycle_id=?",
                (cycle_id,),
            ).fetchone()
        if intent is None:
            raise RuntimeError("market_focus_intent_missing")
        if str(intent["status"]) != "preparing":
            return self._cycle_with_job(cycle_id)
        payload = _loads(intent["payload_json"], None)
        if not isinstance(payload, dict):
            raise RuntimeError("market_focus_intent_payload_invalid")

        if existing_job is None:
            job, created = self._create_focus_job(
                payload,
                submission_source=submission_source,
            )
        else:
            if not self._focus_job_matches_intent(existing_job, intent):
                raise RuntimeError("market_focus_intent_job_mismatch")
            job, created = existing_job, False
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    current = connection.execute(
                        "SELECT status,payload_json FROM catalyst_local_focus_cycles WHERE cycle_id=?",
                        (cycle_id,),
                    ).fetchone()
                    if current is None:
                        raise RuntimeError("market_focus_intent_missing")
                    if str(current["status"]) == "preparing":
                        current_payload = _loads(current["payload_json"], None)
                        if not isinstance(current_payload, dict) or _json(
                            current_payload
                        ) != _json(payload):
                            raise RuntimeError("market_focus_intent_payload_changed")
                        connection.execute(
                            """UPDATE catalyst_local_focus_cycles SET
                                   status=?,job_id=?,updated_at=?
                               WHERE cycle_id=? AND status='preparing'""",
                            (job["status"], job["job_id"], _iso(), cycle_id),
                        )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
        except Exception as error:
            if not _is_sqlite_write_contention(error):
                raise
            cycle = self._focus_cycle_from_row(
                intent,
                include_owner_state=current_request_is_owner(),
            )
            assert cycle is not None
            public_job = self._identity_public_job(
                job,
                expected_type="market_focus",
            )
            assert public_job is not None
            public_job["cached"] = bool(
                not created and public_job["status"] == "completed"
            )
            projected_status = str(public_job["status"])
            if projected_status in {
                "completed",
                "failed",
                "cancelled",
                "budget_blocked",
            }:
                projected_status = "in_progress"
            public_job["status"] = projected_status
            cycle.update(
                {
                    "status": projected_status,
                    "job_id": public_job["job_id"],
                    "updated_at": public_job["updated_at"],
                    "local_link_pending": True,
                    "job": public_job,
                }
            )
            return cycle
        return self._cycle_with_job(cycle_id, job=job, created=created)

    def _enqueue_focus(
        self,
        revision: int,
        *,
        force: bool = False,
        as_of: datetime | None = None,
        submission_source: SubmissionSource = "manual",
        published_news_only: bool = False,
    ) -> dict[str, Any]:
        revision_row, items = self._hotspots_for_revision(revision, limit=20)
        if published_news_only:
            items = [
                item
                for item in items
                if item.get("representative_title") != HOTSPOT_WAITING
                and item.get("summary_zh") != SUMMARY_WAITING
            ]
        observed = as_of or _utc_now()
        prepared_at = (
            _parse_time(str(revision_row["prepared_at"]))
            if revision_row is not None
            else None
        )
        if prepared_at is None or prepared_at > observed:
            raise CatalystError(
                "prepared_revision_changed",
                "Prepared hotspot revision is not visible at the requested time",
                counts_for_circuit=False,
            )
        cycle_id = "mfc_" + uuid.uuid4().hex
        snapshot_as_of = _iso(observed)
        calendar_events = self._focus_calendar_events(as_of=observed, limit=20)
        if not items and not calendar_events:
            raise CatalystError(
                "no_new_hot_events",
                "No prepared hotspot events are available",
                counts_for_circuit=False,
            )
        event_snapshot = [
            {
                "event_group_id": item["event_group_id"],
                "event_group_version": item["event_group_version"],
                "title_zh": item["representative_title"],
                "summary_zh": item["summary_zh"],
                "hot_score": item["hot_score"],
                "event_type": item["event_type"],
                "validated_tickers": item["validated_tickers"],
            }
            for item in items
        ]
        event_snapshot.extend(calendar_events)
        input_hash = _sha({"prepared_revision": revision, "events": event_snapshot})
        allowed_events = [str(item["event_group_id"]) for item in event_snapshot]
        allowed_tickers = sorted({ticker for item in items for ticker in item["validated_tickers"]})
        payload = {
            "cycle_id": cycle_id,
            "as_of": snapshot_as_of,
            "input_hash": input_hash,
            "prepared_revision": revision,
            "allowed_event_group_ids": allowed_events,
            "allowed_tickers": allowed_tickers,
            "events": event_snapshot,
            "force": bool(force),
        }
        force_bucket = _minute_bucket(observed) if force else None
        if force_bucket is not None:
            payload["force_bucket"] = force_bucket
        resume_cycle_id: str | None = None
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if force:
                    existing = connection.execute(
                        """SELECT cycle_id FROM catalyst_local_focus_cycles
                           WHERE prepared_revision=?
                             AND json_extract(payload_json,'$.force_bucket')=?
                           ORDER BY created_at DESC LIMIT 1""",
                        (revision, force_bucket),
                    ).fetchone()
                    if existing is None:
                        existing = connection.execute(
                            """SELECT cycle_id FROM catalyst_local_focus_cycles
                               WHERE prepared_revision=? AND status='preparing'
                                 AND COALESCE(json_extract(payload_json,'$.force'),0)=1
                               ORDER BY created_at DESC LIMIT 1""",
                            (revision,),
                        ).fetchone()
                else:
                    existing = connection.execute(
                        """SELECT cycle_id FROM catalyst_local_focus_cycles
                           WHERE prepared_revision=?
                             AND COALESCE(json_extract(payload_json,'$.force'),0)=0
                           ORDER BY created_at DESC LIMIT 1""",
                        (revision,),
                    ).fetchone()
                if existing is not None:
                    resume_cycle_id = str(existing["cycle_id"])
                else:
                    active = connection.execute(
                        """SELECT cycle_id FROM catalyst_local_focus_cycles
                           WHERE status IN ('preparing','pending','queued','in_progress')
                           ORDER BY created_at LIMIT 1"""
                    ).fetchone()
                    if active is not None:
                        raise CatalystError(
                            "analysis_in_progress",
                            "已有市场焦点分析正在运行",
                            retryable=True,
                            counts_for_circuit=False,
                        )
                    else:
                        revision_count = int(
                            connection.execute(
                                """SELECT COUNT(*) FROM catalyst_local_focus_cycles
                                   WHERE prepared_revision=?""",
                                (revision,),
                            ).fetchone()[0]
                        )
                        payload["cycle_revision"] = revision_count + 1
                        created_at = _iso()
                        connection.execute(
                            """INSERT INTO catalyst_local_focus_cycles(
                                   cycle_id,status,prepared_revision,snapshot_as_of,input_hash,
                                   job_id,payload_json,retry_of_cycle_id,created_at,updated_at
                               ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                            (
                                cycle_id,
                                "preparing",
                                revision,
                                snapshot_as_of,
                                input_hash,
                                f"intent:{cycle_id}",
                                _json(payload),
                                None,
                                created_at,
                                created_at,
                            ),
                        )
                        resume_cycle_id = cycle_id
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        assert resume_cycle_id is not None
        return self._resume_focus_intent(
            resume_cycle_id,
            submission_source=submission_source,
        )

    def _retry_focus(
        self,
        cycle_id: str,
        *,
        submission_source: SubmissionSource = "manual",
    ) -> dict[str, Any]:
        with self._connect() as connection:
            preparing = connection.execute(
                "SELECT * FROM catalyst_local_focus_cycles WHERE cycle_id=?",
                (cycle_id,),
            ).fetchone()
        if preparing is not None and str(preparing["status"]) == "preparing":
            payload = _loads(preparing["payload_json"], None)
            if (
                str(preparing["job_id"]) != f"intent:{cycle_id}"
                or not isinstance(payload, dict)
            ):
                raise CatalystError(
                    "market_focus_cycle_not_retryable",
                    "Focus cycle intent cannot be safely resumed",
                    counts_for_circuit=False,
                )
            # This is an explicit owner action. It may create the previously
            # unpaid immutable job even if newer hotspot revisions now exist.
            return self._resume_focus_intent(
                cycle_id,
                submission_source=submission_source,
            )

        schema_version, schema_hash = ai_runtime.schema_identity("market_focus")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                previous = connection.execute(
                    "SELECT * FROM catalyst_local_focus_cycles WHERE cycle_id=?",
                    (cycle_id,),
                ).fetchone()
                if previous is None:
                    raise CatalystError(
                        "market_focus_cycle_not_found",
                        "Focus cycle was not found",
                        counts_for_circuit=False,
                    )
                current_job = self.ai_repository.get_job(str(previous["job_id"]))
                if str(previous["status"]) not in {
                    "failed",
                    "cancelled",
                    "budget_blocked",
                }:
                    if (
                        current_job is not None
                        and current_job.get("retry_of_job_id") is not None
                        and str(previous["status"])
                        in {"pending", "queued", "in_progress", "completed"}
                    ):
                        connection.commit()
                        return self._cycle_with_job(cycle_id, job=current_job)
                    raise CatalystError(
                        "market_focus_cycle_not_retryable",
                        "Focus cycle is not retryable",
                        counts_for_circuit=False,
                    )
                if current_job is None or current_job.get("error_code") == (
                    "submission_outcome_unknown"
                ):
                    raise CatalystError(
                        "market_focus_cycle_not_retryable",
                        "Focus cycle retry cannot safely identify the prior submission",
                        counts_for_circuit=False,
                    )
                payload = _loads(previous["payload_json"], None)
                if not isinstance(payload, dict):
                    raise CatalystError(
                        "market_focus_cycle_not_retryable",
                        "Focus cycle snapshot is unavailable",
                        counts_for_circuit=False,
                    )
                job, created = self.ai_repository.create_job(
                    job_type="market_focus",
                    payload=payload,
                    model=self.model,
                    reasoning=self.reasoning,
                    execution_mode=EXECUTION_MODE,
                    prompt_version=FOCUS_PROMPT_VERSION,
                    schema_version=schema_version,
                    schema_sha256=schema_hash,
                    max_queued=self.max_queued,
                    submission_source=submission_source,
                    priority=75 if submission_source == "scheduled" else 60,
                    force_retry=True,
                )
                updated_at = _iso()
                connection.execute(
                    """UPDATE catalyst_local_focus_cycles SET
                           status=?,job_id=?,result_json=NULL,error_code=NULL,
                           completed_at=NULL,updated_at=? WHERE cycle_id=?""",
                    (job["status"], job["job_id"], updated_at, cycle_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self._cycle_with_job(cycle_id, job=job, created=created)

    def request_market_focus_cycle(
        self,
        *,
        expected_prepared_revision: int | None,
        retry_cycle_id: str | None = None,
        force: bool = False,
        as_of: datetime | None = None,
        submission_source: SubmissionSource = "manual",
    ) -> dict[str, Any]:
        if self.mode not in {"manual", "scheduled"}:
            raise CatalystError(
                "read_only_mode",
                "Market focus is disabled in read mode",
                counts_for_circuit=False,
            )
        if retry_cycle_id:
            if force:
                raise CatalystError(
                    "invalid_market_focus_request",
                    "A retry cannot also be a forced cycle",
                    counts_for_circuit=False,
                )
            return self._retry_focus(
                retry_cycle_id,
                submission_source=submission_source,
            )
        observed = as_of or _utc_now()
        status = self.hotspot_status(now=observed)
        if expected_prepared_revision is None:
            raise CatalystError("invalid_market_focus_request", "Prepared revision is required", counts_for_circuit=False)
        if int(status["prepared_revision"]) != expected_prepared_revision:
            raise CatalystError("prepared_revision_changed", "Prepared hotspot revision changed", counts_for_circuit=False)
        if force and bool(status.get("has_new_hotspots")):
            raise CatalystError(
                "invalid_market_focus_request",
                "Forced reanalysis requires an already-consumed prepared revision",
                counts_for_circuit=False,
            )
        return self._enqueue_focus(
            expected_prepared_revision,
            force=force,
            as_of=observed,
            submission_source=submission_source,
            published_news_only=submission_source == "scheduled",
        )

    def _focus_cycle_from_row(
        self,
        row: sqlite3.Row | dict[str, Any],
        *,
        include_owner_state: bool,
    ) -> dict[str, Any] | None:
        payload = dict(row)
        result = _loads(payload.pop("result_json"), None)
        cycle_payload = _loads(payload.pop("payload_json", None), {})
        if not include_owner_state and (
            payload.get("status") != "completed" or result is None
        ):
            return None
        cancel_requested = False
        if include_owner_state:
            linked_job = self.ai_repository.get_job(str(payload["job_id"]))
            public_job = self._identity_public_job(
                linked_job,
                expected_type="market_focus",
            )
            cancel_requested = bool(
                public_job and public_job.get("cancel_requested")
            )
        payload.update(
            {
                "status": (
                    "cancel_requested" if cancel_requested else payload["status"]
                ),
                "cancel_requested": cancel_requested,
                "no_new_hot_events": False,
                "focus_revision": payload["prepared_revision"],
                "cycle_revision": int(cycle_payload.get("cycle_revision") or 1),
                "force": bool(cycle_payload.get("force")),
                "consumes_prepared_revision": not bool(cycle_payload.get("force")),
                "event_group_count": len(
                    cycle_payload.get("allowed_event_group_ids", [])
                ),
                "focus_symbol_count": len(cycle_payload.get("allowed_tickers", [])),
                "validation_allowed_tickers": list(
                    cycle_payload.get("allowed_tickers", [])
                ),
                "validation_allowed_event_group_ids": list(
                    cycle_payload.get("allowed_event_group_ids", [])
                ),
                "model": self.model,
                "reasoning_effort": self.reasoning,
                "result": result,
            }
        )
        if not include_owner_state:
            for field in (
                "job_id",
                "error_code",
                "retry_of_cycle_id",
                "updated_at",
                "cancel_requested",
            ):
                payload.pop(field, None)
        return payload

    def market_focus_cycle(self, cycle_id: str) -> dict[str, Any] | None:
        include_owner_state = current_request_is_owner()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM catalyst_local_focus_cycles WHERE cycle_id=?", (cycle_id,)
            ).fetchone()
        if row is None:
            return None
        if include_owner_state and str(row["status"]) == "preparing":
            # A confirmed POST may have created the idempotent paid job before
            # the local link commit met a transient SQLite writer. Owner polling
            # may relink that exact job, but must never create unpaid work.
            existing_job = self._existing_focus_job_for_intent(row)
            if existing_job is None:
                awaiting_submission = self._focus_cycle_from_row(
                    row,
                    include_owner_state=True,
                )
                assert awaiting_submission is not None
                awaiting_submission["awaiting_submission"] = True
                return awaiting_submission
            return self._resume_focus_intent(
                cycle_id,
                existing_job=existing_job,
            )
        if (
            include_owner_state
            and row["result_json"] is None
            and str(row["status"]) != "cancelled"
        ):
            job_id = str(row["job_id"])
            current_job = self._read_ai_job(job_id)
            if current_job is not None and current_job.get("status") in {
                "completed",
                "failed",
                "cancelled",
                "budget_blocked",
            }:
                try:
                    with self._connect() as connection:
                        connection.execute("BEGIN IMMEDIATE")
                        try:
                            self._publish_completed_focus(
                                connection,
                                {job_id: current_job},
                            )
                            refreshed = connection.execute(
                                "SELECT * FROM catalyst_local_focus_cycles WHERE cycle_id=?",
                                (cycle_id,),
                            ).fetchone()
                            connection.commit()
                        except Exception:
                            connection.rollback()
                            raise
                except Exception as error:
                    if not _is_sqlite_write_contention(error):
                        raise
                    # The paid task is already terminal, so a transient local
                    # writer must not turn polling into a false failed cycle.
                    # Keep the client polling and publish on its next read.
                    deferred = self._focus_cycle_from_row(
                        row,
                        include_owner_state=True,
                    )
                    assert deferred is not None
                    deferred.update(
                        {
                            "status": "in_progress",
                            "local_publish_pending": True,
                        }
                    )
                    return deferred
                if refreshed is not None:
                    row = refreshed
        return self._focus_cycle_from_row(
            row,
            include_owner_state=include_owner_state,
        )

    def latest_market_focus_cycle(self, *, now: datetime | None = None) -> dict[str, Any]:
        observed = now or _utc_now()
        include_owner_state = current_request_is_owner()
        with self._connect() as connection:
            row = connection.execute(
                """SELECT cycle_id FROM catalyst_local_focus_cycles
                   WHERE created_at<=? ORDER BY created_at DESC LIMIT 1""",
                (_iso(observed),),
            ).fetchone()
            successful_row = connection.execute(
                """SELECT cycle_id FROM catalyst_local_focus_cycles
                   WHERE status='completed' AND result_json IS NOT NULL
                     AND completed_at<=?
                   ORDER BY completed_at DESC,created_at DESC LIMIT 1""",
                (_iso(observed),),
            ).fetchone()
        cycle = (
            self.market_focus_cycle(str(row["cycle_id"]))
            if row and include_owner_state
            else None
        )
        latest_successful_cycle = (
            self.market_focus_cycle(str(successful_row["cycle_id"]))
            if successful_row is not None
            else None
        )
        if not include_owner_state:
            cycle = latest_successful_cycle
        if (
            cycle is not None
            and cycle.get("completed_at")
            and str(cycle["completed_at"]) > _iso(observed)
        ):
            cycle = dict(cycle)
            cycle["result"] = None
            cycle["completed_at"] = None
            cycle["status"] = "in_progress"
        return {
            "status": "active" if cycle else "empty",
            "as_of": _iso(observed),
            "data_through": self.hotspot_status(now=observed).get("data_through"),
            "cycle": cycle,
            "latest_successful_cycle": latest_successful_cycle,
            "warnings": [],
        }

    def cancel_market_focus_cycle(self, cycle_id: str) -> dict[str, Any] | None:
        for _attempt in range(5):
            with self._connect() as connection:
                target = connection.execute(
                    """SELECT job_id,status FROM catalyst_local_focus_cycles
                       WHERE cycle_id=?""",
                    (cycle_id,),
                ).fetchone()
            if target is None:
                return None
            target_job_id = str(target["job_id"])
            updated = self.ai_repository.request_cancel(target_job_id)
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    current = connection.execute(
                        """SELECT job_id,status
                           FROM catalyst_local_focus_cycles
                           WHERE cycle_id=?""",
                        (cycle_id,),
                    ).fetchone()
                    if current is None:
                        connection.commit()
                        return None
                    if str(current["job_id"]) != target_job_id:
                        connection.commit()
                        continue
                    if (
                        updated is not None
                        and str(current["status"])
                        in {"pending", "queued", "in_progress"}
                    ):
                        changed = connection.execute(
                            """UPDATE catalyst_local_focus_cycles SET
                                   status=?,updated_at=?
                               WHERE cycle_id=? AND job_id=?""",
                            (
                                str(updated["status"]),
                                str(updated["updated_at"]),
                                cycle_id,
                                target_job_id,
                            ),
                        ).rowcount
                        if changed != 1:
                            raise RuntimeError(
                                "market_focus_cancel_contention"
                            )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
            return self.market_focus_cycle(cycle_id)
        raise RuntimeError("market_focus_cancel_contention")

    @staticmethod
    def _manual_operation_public(
        row: sqlite3.Row | dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        payload = dict(row)
        payload.pop("idempotency_key", None)
        observed = now or _utc_now()
        cooldown_until = _parse_time(payload.get("cooldown_until"))
        cooldown_active = bool(
            cooldown_until is not None and cooldown_until > observed
        )
        payload["cooldown_active"] = cooldown_active
        payload["retry_after_seconds"] = (
            max(1, int((cooldown_until - observed).total_seconds()) + 1)
            if cooldown_active and cooldown_until is not None
            else None
        )
        if (
            payload.get("status") == "completed"
            and cooldown_active
        ):
            payload["status"] = "cooldown"
        return payload

    @staticmethod
    def _recover_stale_manual_operations(
        connection: sqlite3.Connection,
        *,
        now: datetime,
    ) -> int:
        cutoff = _iso(
            now - timedelta(seconds=MANUAL_REFRESH_CLAIM_TTL_SECONDS)
        )
        return int(
            connection.execute(
                """UPDATE catalyst_local_manual_operations
                   SET status='queued',started_at=NULL,completed_at=NULL,
                       cooldown_until=NULL,error_code=NULL
                   WHERE status='running'
                     AND (started_at IS NULL OR started_at<=?)""",
                (cutoff,),
            ).rowcount
        )

    @staticmethod
    def _empty_manual_refreshes(status: str = "idle") -> dict[str, dict[str, Any]]:
        return {
            operation_type: {
                "operation_type": operation_type,
                "status": status,
                "request_id": None,
                "requested_at": None,
                "started_at": None,
                "completed_at": None,
                "cooldown_until": None,
                "cooldown_active": False,
                "retry_after_seconds": None,
                "error_code": None,
            }
            for operation_type in MANUAL_REFRESH_TYPES
        }

    def manual_refresh_statuses(
        self,
        *,
        now: datetime | None = None,
    ) -> dict[str, dict[str, Any]]:
        observed = now or _utc_now()
        output = self._empty_manual_refreshes()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._recover_stale_manual_operations(connection, now=observed)
            for operation_type in MANUAL_REFRESH_TYPES:
                row = connection.execute(
                    """SELECT * FROM catalyst_local_manual_operations
                       WHERE operation_type=?
                       ORDER BY requested_at DESC,request_id DESC LIMIT 1""",
                    (operation_type,),
                ).fetchone()
                if row is not None:
                    output[operation_type] = self._manual_operation_public(
                        row,
                        now=observed,
                    )
            connection.commit()
        return output

    def request_refresh(
        self,
        operation_type: Literal["news", "calendar", "source_health"] = "news",
        *,
        idempotency_key: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if operation_type not in {"news", "calendar", "source_health"}:
            raise CatalystError(
                "invalid_refresh_type",
                "Unsupported Catalyst refresh type",
                counts_for_circuit=False,
            )
        observed = now or _utc_now()
        normalized_key = str(idempotency_key or "").strip()
        if normalized_key and (
            len(normalized_key) > 128
            or re.fullmatch(r"[A-Za-z0-9._:-]+", normalized_key) is None
        ):
            raise CatalystError(
                "invalid_idempotency_key",
                "Refresh idempotency key is invalid",
                counts_for_circuit=False,
            )
        if not normalized_key:
            window = max(1, self.manual_refresh_cooldown_seconds or 30)
            bucket = int(observed.timestamp()) // window
            normalized_key = f"auto:{operation_type}:{bucket}"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._recover_stale_manual_operations(connection, now=observed)
            active = connection.execute(
                """SELECT * FROM catalyst_local_manual_operations
                   WHERE operation_type=? AND status IN ('queued','running')
                   ORDER BY requested_at LIMIT 1""",
                (operation_type,),
            ).fetchone()
            if active is not None:
                connection.commit()
                return self._manual_operation_public(active, now=observed)
            latest = connection.execute(
                """SELECT * FROM catalyst_local_manual_operations
                   WHERE operation_type=? ORDER BY requested_at DESC LIMIT 1""",
                (operation_type,),
            ).fetchone()
            if latest is not None:
                cooldown_until = _parse_time(latest["cooldown_until"])
                if cooldown_until is not None and cooldown_until > observed:
                    connection.commit()
                    return self._manual_operation_public(latest, now=observed)
            request_id = "refresh_" + uuid.uuid4().hex
            try:
                connection.execute(
                    """INSERT INTO catalyst_local_manual_operations(
                           request_id,operation_type,idempotency_key,status,
                           requested_at
                       ) VALUES(?,?,?,?,?)""",
                    (
                        request_id,
                        operation_type,
                        normalized_key,
                        "queued",
                        _iso(observed),
                    ),
                )
            except sqlite3.IntegrityError:
                duplicate = connection.execute(
                    """SELECT * FROM catalyst_local_manual_operations
                       WHERE operation_type=? AND idempotency_key=?""",
                    (operation_type, normalized_key),
                ).fetchone()
                connection.commit()
                if duplicate is None:
                    raise
                return self._manual_operation_public(duplicate, now=observed)
            created = connection.execute(
                """SELECT * FROM catalyst_local_manual_operations
                   WHERE request_id=?""",
                (request_id,),
            ).fetchone()
            connection.commit()
        assert created is not None
        return self._manual_operation_public(created, now=observed)

    def manual_operation(self, request_id: str) -> dict[str, Any] | None:
        observed = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._recover_stale_manual_operations(connection, now=observed)
            row = connection.execute(
                """SELECT * FROM catalyst_local_manual_operations
                   WHERE request_id=?""",
                (request_id,),
            ).fetchone()
            connection.commit()
        return (
            self._manual_operation_public(row, now=observed)
            if row is not None
            else None
        )

    def consume_refresh_requested(self) -> dict[str, Any] | None:
        observed = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._recover_stale_manual_operations(connection, now=observed)
            row = connection.execute(
                """SELECT * FROM catalyst_local_manual_operations
                   WHERE status='queued' ORDER BY requested_at LIMIT 1"""
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            started_at = _iso(observed)
            updated = connection.execute(
                """UPDATE catalyst_local_manual_operations
                   SET status='running',started_at=?
                   WHERE request_id=? AND status='queued'""",
                (started_at, row["request_id"]),
            ).rowcount
            current = connection.execute(
                """SELECT * FROM catalyst_local_manual_operations
                   WHERE request_id=?""",
                (row["request_id"],),
            ).fetchone()
            connection.commit()
        return (
            self._manual_operation_public(current)
            if updated == 1 and current is not None
            else None
        )

    def complete_refresh_request(
        self,
        request_id: str,
        *,
        error_code: str | None = None,
    ) -> dict[str, Any] | None:
        completed_at = _utc_now()
        cooldown_until = completed_at + timedelta(
            seconds=self.manual_refresh_cooldown_seconds
        )
        status = "failed" if error_code else "completed"
        safe_error = (
            str(error_code)[:120]
            if error_code and str(error_code).replace("_", "").isalnum()
            else "refresh_failed"
            if error_code
            else None
        )
        with self._connect() as connection:
            connection.execute(
                """UPDATE catalyst_local_manual_operations SET
                       status=?,completed_at=?,cooldown_until=?,error_code=?
                   WHERE request_id=? AND status='running'""",
                (
                    status,
                    _iso(completed_at),
                    _iso(cooldown_until),
                    safe_error,
                    request_id,
                ),
            )
            row = connection.execute(
                """SELECT * FROM catalyst_local_manual_operations
                   WHERE request_id=?""",
                (request_id,),
            ).fetchone()
            connection.commit()
        return (
            self._manual_operation_public(row, now=completed_at)
            if row is not None
            else None
        )

    def import_verified_legacy_rows(self, rows: Iterable[dict[str, Any]]) -> dict[str, int]:
        """Import only exact current-identity, current-schema Chinese results."""

        self.initialize()
        candidates = [dict(row) for row in rows if isinstance(row, dict)]
        latest: dict[tuple[int, int, str], tuple[datetime, str]] = {}
        for raw in candidates:
            news_id = raw.get("news_id")
            sequence = raw.get("change_sequence")
            content_hash = raw.get("content_hash")
            completed_at = _parse_time(str(raw.get("completed_at") or ""))
            if (
                type(news_id) is not int
                or type(sequence) is not int
                or not isinstance(content_hash, str)
                or completed_at is None
            ):
                continue
            identity = str(raw.get("legacy_identity") or _sha(raw))
            key = (news_id, sequence, content_hash)
            rank = (completed_at, identity)
            if key not in latest or rank > latest[key]:
                latest[key] = rank
        expected_schema, expected_schema_hash = ai_runtime.schema_identity(
            "news_impact"
        )
        imported = 0
        rejected = 0
        with self._connect() as connection:
            for raw in candidates:
                identity = str(raw.get("legacy_identity") or _sha(raw))
                news_id = raw.get("news_id")
                sequence = raw.get("change_sequence")
                content_hash = raw.get("content_hash")
                result = raw.get("result")
                outcome = "rejected"
                reason = "identity_or_language_invalid"
                completed_at = _parse_time(str(raw.get("completed_at") or ""))
                key = (
                    (news_id, sequence, content_hash)
                    if type(news_id) is int
                    and type(sequence) is int
                    and isinstance(content_hash, str)
                    else None
                )
                is_latest = bool(
                    key is not None
                    and completed_at is not None
                    and latest.get(key) == (completed_at, identity)
                    and raw.get("_is_latest", True) is not False
                )
                metadata_matches = (
                    raw.get("model") == self.model
                    and raw.get("reasoning") == self.reasoning
                    and raw.get("prompt_version") == NEWS_PROMPT_VERSION
                    and (
                        raw.get("schema_version")
                        or raw.get("analysis_schema_version")
                    )
                    == expected_schema
                    and (
                        raw.get("schema_sha256") in {None, expected_schema_hash}
                    )
                )
                if not is_latest:
                    reason = "superseded_legacy_result"
                elif not metadata_matches:
                    reason = "legacy_metadata_mismatch"
                elif type(news_id) is int and type(sequence) is int and isinstance(content_hash, str) and isinstance(result, dict):
                    revision = connection.execute(
                        """SELECT r.canonical_tickers_json
                           FROM catalyst_local_news_revisions r
                           JOIN macrolens_etl_news current
                             ON current.news_id=r.news_id
                            AND current.change_sequence=r.change_sequence
                            AND current.content_hash=r.content_hash
                            AND current.deleted=0
                           WHERE r.news_id=? AND r.change_sequence=?
                             AND r.content_hash=?""",
                        (news_id, sequence, content_hash),
                    ).fetchone()
                    allowed_tickers = self.validate_tickers(
                        raw.get("allowed_tickers") or []
                    )
                    if revision is not None and allowed_tickers != self.validate_tickers(
                        _loads(revision["canonical_tickers_json"], [])
                    ):
                        revision = None
                    fake_job = {
                        "job_type": "news_impact",
                        "model": raw.get("model"),
                        "reasoning": raw.get("reasoning"),
                        "execution_mode": EXECUTION_MODE,
                        "prompt_version": raw.get("prompt_version"),
                        "schema_version": raw.get("schema_version")
                        or raw.get("analysis_schema_version"),
                        "schema_sha256": raw.get("schema_sha256")
                        or expected_schema_hash,
                        "payload_json": _json(
                            {
                                "news_id": news_id,
                                "change_sequence": sequence,
                                "content_hash": content_hash,
                                "allowed_tickers": allowed_tickers,
                            }
                        ),
                        "result_json": _json(result),
                        "status": "completed",
                        "job_id": "legacy_" + hashlib.sha256(identity.encode()).hexdigest()[:32],
                        "submitted_at": raw.get("completed_at"),
                        "updated_at": str(raw.get("completed_at") or _iso()),
                        "completed_at": str(raw.get("completed_at") or _iso()),
                        "error_code": None,
                    }
                    public = self._verified_public_job(fake_job, expected_type="news_impact") if revision else None
                    if public is not None:
                        job_id = str(fake_job["job_id"])
                        inserted = connection.execute(
                            """INSERT OR IGNORE INTO catalyst_local_analysis_links(
                                   news_id,change_sequence,content_hash,job_id,result_json,
                                   result_available_at,verified_at,created_at
                               ) VALUES(?,?,?,?,?,?,?,?)""",
                            (
                                news_id,
                                sequence,
                                content_hash,
                                job_id,
                                _json(public["result"]),
                                fake_job["completed_at"],
                                _iso(),
                                _iso(),
                            ),
                        ).rowcount
                        outcome = "imported"
                        reason = None
                        imported += int(inserted)
                if outcome == "rejected":
                    rejected += 1
                connection.execute(
                    """INSERT OR REPLACE INTO catalyst_local_legacy_import_audit(
                           legacy_identity,outcome,reason,observed_at
                       ) VALUES(?,?,?,?)""",
                    (identity, outcome, reason, _iso()),
                )
            connection.commit()
        return {"imported": imported, "rejected": rejected}

    def _import_legacy_from_database(self) -> dict[str, int]:
        """Audit same-database legacy projections once and import only zh-CN rows."""

        source_candidates: list[dict[str, Any]] = []
        with self._connect() as connection:
            tables = {
                str(row["name"])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            audited = {
                str(row["legacy_identity"])
                for row in connection.execute(
                    "SELECT legacy_identity FROM catalyst_local_legacy_import_audit"
                ).fetchall()
            }
            definitions = (
                (
                    "catalyst_analysis_projections",
                    "projection_id",
                    "item_change_sequence",
                ),
                (
                    "catalyst_analysis_revisions",
                    "analysis_revision_id",
                    "item_change_sequence",
                ),
            )
            for table, identity_column, sequence_column in definitions:
                if table not in tables:
                    continue
                columns = {
                    str(row["name"])
                    for row in connection.execute(
                        f"PRAGMA table_info({table})"
                    ).fetchall()
                }
                metadata_select = ",".join(
                    (
                        name
                        if name in columns
                        else f"NULL AS {name}"
                    )
                    for name in (
                        "model",
                        "reasoning",
                        "prompt_version",
                        "analysis_schema_version",
                    )
                )
                rows = connection.execute(
                    f"""SELECT {identity_column} AS legacy_id,news_id,
                                {sequence_column} AS change_sequence,
                                content_hash,available_at,raw_json,
                                {metadata_select}
                         FROM {table} ORDER BY available_at DESC"""
                ).fetchall()
                for row in rows:
                    legacy_identity = f"{table}:{row['legacy_id']}"
                    raw_result = _loads(row["raw_json"], None)
                    if not isinstance(raw_result, dict):
                        raw_result = {}
                    nested = raw_result.get("result")
                    if isinstance(nested, dict):
                        raw_result = nested
                    revision = connection.execute(
                        """SELECT canonical_tickers_json
                           FROM catalyst_local_news_revisions
                           WHERE news_id=? AND change_sequence=? AND content_hash=?""",
                        (
                            row["news_id"],
                            row["change_sequence"],
                            row["content_hash"],
                        ),
                    ).fetchone()
                    source_candidates.append(
                        {
                            "legacy_identity": legacy_identity,
                            "news_id": int(row["news_id"]),
                            "change_sequence": int(row["change_sequence"]),
                            "content_hash": str(row["content_hash"]),
                            "allowed_tickers": (
                                _loads(revision["canonical_tickers_json"], [])
                                if revision is not None
                                else []
                            ),
                            "completed_at": str(row["available_at"]),
                            "result": raw_result,
                            "model": row["model"],
                            "reasoning": row["reasoning"],
                            "prompt_version": row["prompt_version"],
                            "schema_version": row["analysis_schema_version"],
                            "_already_audited": legacy_identity in audited,
                        }
                    )
        latest: dict[tuple[int, int, str], tuple[datetime, str]] = {}
        for candidate in source_candidates:
            completed_at = _parse_time(str(candidate.get("completed_at") or ""))
            if completed_at is None:
                continue
            key = (
                int(candidate["news_id"]),
                int(candidate["change_sequence"]),
                str(candidate["content_hash"]),
            )
            rank = (completed_at, str(candidate["legacy_identity"]))
            if key not in latest or rank > latest[key]:
                latest[key] = rank
        candidates: list[dict[str, Any]] = []
        for candidate in source_candidates:
            if candidate.pop("_already_audited", False):
                continue
            completed_at = _parse_time(str(candidate.get("completed_at") or ""))
            key = (
                int(candidate["news_id"]),
                int(candidate["change_sequence"]),
                str(candidate["content_hash"]),
            )
            candidate["_is_latest"] = bool(
                completed_at is not None
                and latest.get(key)
                == (completed_at, str(candidate["legacy_identity"]))
            )
            candidates.append(candidate)
        if not candidates:
            return {"imported": 0, "rejected": 0}
        return self.import_verified_legacy_rows(candidates)


__all__ = [
    "AMBIGUOUS_TICKERS",
    "HOTSPOT_WAITING",
    "LocalCatalystIntelligence",
    "SCHEMA_VERSION",
    "SUMMARY_WAITING",
    "TITLE_WAITING",
]
