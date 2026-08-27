"""Customer accounts: sign-in credentials, sessions and personal watchlists.

Scope is deliberately narrow. These accounts exist so a visitor can keep their
own watchlist on the server instead of in the browser. They grant no owner
capability whatsoever: paid analysis, worker actions and runtime settings stay
behind the separate owner session in :mod:`app.access`.

The owner keeps signing in as ``admin`` with the password already configured
through ``APP_PASSWORD_HASH``; that path never touches this store.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import secrets
import sqlite3
import threading
import time
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from app.access import _b64decode, _b64encode  # shared encoding with owner hashes

_PBKDF2_ITERATIONS = 240_000
_PASSWORD_HASH_LENGTH = 32

#: The owner signs in under this name; it can never become a customer account.
RESERVED_USERNAMES = frozenset({"admin", "administrator", "root", "owner", "optix"})

#: The owner's watchlist rows need an ``accounts`` row to satisfy the foreign key,
#: so the owner gets exactly one, provisioned on demand. The id deliberately does
#: not use the ``usr_`` prefix that :meth:`AccountStore.register` mints, so it can
#: never collide with a customer, and its username key is already reserved above,
#: which makes "no customer can ever own this row" a UNIQUE-constraint guarantee
#: rather than a code convention.
OWNER_USER_ID = "own_local"
OWNER_ACCOUNT_USERNAME = "admin"

USERNAME_MAX_LENGTH = 32
PASSWORD_MAX_LENGTH = 256
WATCHLIST_MAX_TICKERS = 50
SESSION_SECONDS = 30 * 24 * 60 * 60

DRAWING_KINDS = frozenset(
    {"horizontal", "segment", "ray", "channel", "rectangle", "fibonacci", "text"}
)
CHART_RANGES = frozenset({"5m", "15m", "1h", "1d", "1w"})
CHART_ADJUSTMENTS = frozenset({"raw"})
DRAWING_ANCHOR_COUNTS = {
    "horizontal": 1,
    "segment": 2,
    "ray": 2,
    "channel": 3,
    "rectangle": 2,
    "fibonacci": 2,
    "text": 1,
}
DRAWING_WIDTHS = frozenset({1, 2, 3, 4})
DRAWING_DASHES = frozenset({"solid", "dashed", "dotted"})
DRAWING_PALETTE = frozenset(
    {
        "#2E46E0",
        "#3B59F2",
        "#6B82FF",
        "#0E9F6E",
        "#E5484D",
        "#E8930C",
        "#0B7285",
        "#3D4A68",
        "#8A94B0",
        "brand",
        "up",
        "down",
        "ink",
        "warn",
        "ai",
    }
)
DRAWINGS_PER_RANGE_MAX = 500
DRAWINGS_PER_ACCOUNT_MAX = 2000
DRAWING_PAYLOAD_MAX_BYTES = 16_384
DRAWING_TEXT_MAX = 240
DRAWING_PRICE_MAX = 10_000_000.0
DRAWING_SCHEMA_VERSION = 1

_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

_TICKER_PATTERN = re.compile(
    r"^(?:\^[A-Z0-9][A-Z0-9._-]{0,10}|[A-Z0-9][A-Z0-9._-]{0,11})$"
)
_DISALLOWED_USERNAME_CHARS = re.compile(r"[\s\x00-\x1f\x7f]")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    user_id TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    username_key TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_login_at TEXT
);
CREATE TABLE IF NOT EXISTS account_sessions (
    token_sha256 TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES accounts(user_id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_account_sessions_user
    ON account_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_account_sessions_expiry
    ON account_sessions(expires_at);
CREATE TABLE IF NOT EXISTS account_watchlist (
    user_id TEXT NOT NULL REFERENCES accounts(user_id) ON DELETE CASCADE,
    ticker TEXT NOT NULL,
    position INTEGER NOT NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY (user_id, ticker)
);
CREATE INDEX IF NOT EXISTS idx_account_watchlist_order
    ON account_watchlist(user_id, position);
CREATE TABLE IF NOT EXISTS account_chart_drawings (
    drawing_id TEXT NOT NULL,
    user_id TEXT NOT NULL
        REFERENCES accounts(user_id) ON DELETE CASCADE,
    ticker TEXT NOT NULL,
    chart_range TEXT NOT NULL,
    adjustment TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    revision INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    -- 主键带上 user_id：编号空间按账户隔离。全局唯一的话，别人占用的编号会
    -- 返回 404 而空闲编号返回 201，等于给外部账户的编号做了存在性探针；而且
    -- 同账户重放创建只能报 409，客户端 outbox 会卡死在这一条上。
    PRIMARY KEY (user_id, drawing_id)
);
CREATE INDEX IF NOT EXISTS idx_account_chart_drawings_scope
    ON account_chart_drawings(user_id, ticker, chart_range, adjustment);
CREATE TABLE IF NOT EXISTS account_chart_drawing_scopes (
    user_id TEXT NOT NULL
        REFERENCES accounts(user_id) ON DELETE CASCADE,
    ticker TEXT NOT NULL,
    chart_range TEXT NOT NULL,
    adjustment TEXT NOT NULL,
    scope_revision INTEGER NOT NULL,
    PRIMARY KEY (user_id, ticker, chart_range, adjustment)
);
"""


class AccountError(RuntimeError):
    """A request the caller can correct; ``code`` is the machine identifier."""

    def __init__(self, code: str, *, retry_after: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


@dataclass(frozen=True)
class Account:
    user_id: str
    username: str
    created_at: str


@dataclass(frozen=True)
class SessionResult:
    token: str
    expires_at: float
    account: Account


def hash_account_password(password: str, *, salt: bytes | None = None) -> str:
    """Hash with the owner's PBKDF2 parameters but no length floor.

    Customer passwords carry no complexity requirement by product decision, so
    only characters that would corrupt storage or headers are rejected. The
    work factor stays identical to the owner hash: a weak password must not
    also get weak stretching.
    """

    validate_password(password)
    resolved_salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        resolved_salt,
        _PBKDF2_ITERATIONS,
        dklen=_PASSWORD_HASH_LENGTH,
    )
    return "$".join(
        (
            "pbkdf2_sha256",
            str(_PBKDF2_ITERATIONS),
            _b64encode(resolved_salt),
            _b64encode(digest),
        )
    )


def verify_account_password(password: str, encoded_hash: str) -> bool:
    try:
        algorithm, raw_iterations, raw_salt, raw_digest = encoded_hash.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(raw_iterations)
        salt = _b64decode(raw_salt)
        expected = _b64decode(raw_digest)
        if len(salt) < 16 or len(expected) != _PASSWORD_HASH_LENGTH:
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            iterations,
            dklen=len(expected),
        )
    except Exception:
        return False
    return hmac.compare_digest(actual, expected)


def validate_password(password: str) -> str:
    """No length or complexity floor — only reject unstorable characters."""

    if not password:
        raise AccountError("password_required")
    if len(password) > PASSWORD_MAX_LENGTH:
        raise AccountError("password_too_long")
    if any(character in password for character in ("\x00", "\r", "\n")):
        raise AccountError("password_invalid_characters")
    return password


def normalize_username(username: str) -> tuple[str, str]:
    """Return ``(display, key)``; the key is what uniqueness is judged on.

    NFKC folding first, so visually identical names cannot become two
    accounts, then case folding for the lookup key.
    """

    display = unicodedata.normalize("NFKC", str(username or "")).strip()
    if not display:
        raise AccountError("username_required")
    if len(display) > USERNAME_MAX_LENGTH:
        raise AccountError("username_too_long")
    if _DISALLOWED_USERNAME_CHARS.search(display):
        raise AccountError("username_invalid_characters")
    key = display.casefold()
    if key in RESERVED_USERNAMES:
        raise AccountError("username_reserved")
    return display, key


def normalize_ticker(ticker: str) -> str:
    symbol = unicodedata.normalize("NFKC", str(ticker or "")).strip().upper()
    if not _TICKER_PATTERN.fullmatch(symbol):
        raise AccountError("invalid_ticker")
    return symbol


def normalize_chart_range(value: str) -> str:
    key = str(value or "").strip()
    if key not in CHART_RANGES:
        raise AccountError("invalid_range")
    return key


def normalize_chart_adjustment(value: str) -> str:
    key = str(value or "").strip()
    if key not in CHART_ADJUSTMENTS:
        raise AccountError("invalid_adjustment")
    return key


def normalize_drawing_id(value: str) -> str:
    drawing_id = str(value or "").strip()
    if not _UUID_RE.fullmatch(drawing_id):
        raise AccountError("invalid_drawing_id")
    return drawing_id.lower()


def normalize_drawing_color(value: str) -> str:
    color = str(value or "").strip()
    if color in DRAWING_PALETTE:
        return color if not color.startswith("#") else color.upper()
    if _HEX_COLOR.fullmatch(color):
        return color.upper()
    raise AccountError("invalid_color")


def _parse_iso_time(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise AccountError("invalid_time")
    # RFC 3339：必须带 Z 或显式偏移。naive 本地时间不能偷偷当成 UTC。
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    # 换算也得包进来：0001-01-01T00:00:00+00:01 这种格式合法但换到 UTC 会越过
    # datetime.min，astimezone 抛的是 OverflowError，漏在外面就是一个 500。
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            raise AccountError("invalid_time")
        return parsed.astimezone(timezone.utc).isoformat()
    except AccountError:
        raise
    except (ValueError, OverflowError, OSError) as exc:
        raise AccountError("invalid_time") from exc


def _require_bool(value: Any, code: str) -> bool:
    if isinstance(value, bool):
        return value
    raise AccountError(code)


def _require_finite_price(value: Any) -> float:
    try:
        price = float(value)
    except (TypeError, ValueError) as exc:
        raise AccountError("invalid_price") from exc
    if not math.isfinite(price) or price <= 0 or price > DRAWING_PRICE_MAX:
        raise AccountError("invalid_price")
    return price


def _validate_anchor(raw: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise AccountError("invalid_anchors")
    extra = set(raw.keys()) - {"time", "barKey", "price"}
    if extra:
        raise AccountError("invalid_anchors")
    bar_key = str(raw.get("barKey") or "").strip()
    if not bar_key or len(bar_key) > 64:
        raise AccountError("invalid_anchors")
    if any(ord(ch) < 32 for ch in bar_key):
        raise AccountError("invalid_anchors")
    return {
        "time": _parse_iso_time(str(raw.get("time") or "")),
        "barKey": bar_key,
        "price": _require_finite_price(raw.get("price")),
    }


def validate_drawing_payload(raw: Mapping[str, Any], *, require_id: bool = True) -> dict[str, Any]:
    """Strict drawing body: whitelist only, no ECharts options or expressions."""

    if not isinstance(raw, Mapping):
        raise AccountError("invalid_payload")
    extra_fields = set(raw.keys()) - {
        "schemaVersion",
        "id",
        "ticker",
        "range",
        "adjustment",
        "kind",
        "anchors",
        "style",
        "text",
        "locked",
        "hidden",
        "zOrder",
    }
    if extra_fields:
        raise AccountError("invalid_payload")
    kind = str(raw.get("kind") or "").strip()
    if kind not in DRAWING_KINDS:
        raise AccountError("invalid_kind")
    anchors_raw = raw.get("anchors")
    expected = DRAWING_ANCHOR_COUNTS[kind]
    if not isinstance(anchors_raw, list) or len(anchors_raw) != expected:
        raise AccountError("invalid_anchors")
    anchors = [_validate_anchor(item) for item in anchors_raw]
    style_raw = raw.get("style")
    if not isinstance(style_raw, Mapping):
        raise AccountError("invalid_style")
    extra_style = set(style_raw.keys()) - {"color", "width", "dash", "fillOpacity"}
    if extra_style:
        raise AccountError("invalid_style")
    try:
        width = int(style_raw.get("width"))
    except (TypeError, ValueError) as exc:
        raise AccountError("invalid_style") from exc
    if width not in DRAWING_WIDTHS:
        raise AccountError("invalid_style")
    dash = str(style_raw.get("dash") or "").strip()
    if dash not in DRAWING_DASHES:
        raise AccountError("invalid_style")
    fill_opacity = style_raw.get("fillOpacity")
    if fill_opacity is not None:
        try:
            fill_opacity = float(fill_opacity)
        except (TypeError, ValueError) as exc:
            raise AccountError("invalid_style") from exc
        if not math.isfinite(fill_opacity) or fill_opacity < 0 or fill_opacity > 1:
            raise AccountError("invalid_style")
    text = raw.get("text")
    if text is None:
        text_out = None
    else:
        if not isinstance(text, str):
            raise AccountError("invalid_text")
        if len(text) > DRAWING_TEXT_MAX:
            raise AccountError("text_too_long")
        if "<" in text or ">" in text or "\x00" in text:
            raise AccountError("invalid_text")
        text_out = text
    if kind == "text" and text_out is None:
        text_out = ""
    try:
        z_order = int(raw.get("zOrder", 0))
    except (TypeError, ValueError) as exc:
        raise AccountError("invalid_payload") from exc
    if z_order < -1_000_000 or z_order > 1_000_000:
        raise AccountError("invalid_payload")
    schema_version = raw.get("schemaVersion", DRAWING_SCHEMA_VERSION)
    try:
        schema_version = int(schema_version)
    except (TypeError, ValueError) as exc:
        raise AccountError("invalid_payload") from exc
    if schema_version != DRAWING_SCHEMA_VERSION:
        raise AccountError("invalid_payload")
    drawing_id = normalize_drawing_id(str(raw.get("id") or "")) if require_id else None
    ticker = normalize_ticker(str(raw.get("ticker") or ""))
    chart_range = normalize_chart_range(str(raw.get("range") or ""))
    adjustment = normalize_chart_adjustment(str(raw.get("adjustment") or "raw"))
    payload = {
        "schemaVersion": DRAWING_SCHEMA_VERSION,
        "kind": kind,
        "anchors": anchors,
        "style": {
            "color": normalize_drawing_color(str(style_raw.get("color") or "")),
            "width": width,
            "dash": dash,
            **({} if fill_opacity is None else {"fillOpacity": fill_opacity}),
        },
        "text": text_out,
        "locked": _require_bool(raw.get("locked", False), "invalid_payload"),
        "hidden": _require_bool(raw.get("hidden", False), "invalid_payload"),
        "zOrder": z_order,
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > DRAWING_PAYLOAD_MAX_BYTES:
        raise AccountError("payload_too_large")
    return {
        "id": drawing_id,
        "ticker": ticker,
        "range": chart_range,
        "adjustment": adjustment,
        "kind": kind,
        "payload": payload,
        "payload_json": encoded,
    }


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_expected_scope_revision(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AccountError("invalid_payload")
    return value


_DRAWING_COLUMNS = (
    "drawing_id, user_id, ticker, chart_range, adjustment, "
    "kind, payload_json, revision, created_at, updated_at"
)


class AccountStore:
    """SQLite-backed accounts, sessions and personal watchlists."""

    def __init__(
        self,
        path: str | Path,
        *,
        clock: Any = time.time,
        max_accounts: int = 2000,
    ) -> None:
        self.path = Path(path)
        self._clock = clock
        self._max_accounts = int(max_accounts)
        self._lock = threading.Lock()
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self) -> None:
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            with self._connect() as connection:
                connection.executescript(_SCHEMA)
                self._migrate_chart_drawings(connection)
                connection.commit()
            self._initialized = True

    def _migrate_chart_drawings(self, connection: sqlite3.Connection) -> None:
        """Copy-forward: global drawing_id PK → (user_id, drawing_id); backfill scopes.

        旧路径直接 DROP 会丢掉已有绘图。这里改名、建新表、INSERT SELECT，再删
        临时表。scopes 表按已有行补 revision=1；已有 scope 行不覆盖。
        """

        pk_columns = [
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(account_chart_drawings)")
            if int(row["pk"]) > 0
        ]
        if pk_columns == ["drawing_id"]:
            # RENAME keeps the old index name, so CREATE INDEX IF NOT EXISTS
            # on the new table would skip and the drop would leave the table
            # without idx_account_chart_drawings_scope.
            connection.execute("DROP INDEX IF EXISTS idx_account_chart_drawings_scope")
            connection.execute(
                "ALTER TABLE account_chart_drawings "
                "RENAME TO account_chart_drawings_legacy_pk"
            )
            connection.executescript(_SCHEMA)
            connection.execute(
                f"""INSERT INTO account_chart_drawings
                        ({_DRAWING_COLUMNS})
                    SELECT {_DRAWING_COLUMNS}
                      FROM account_chart_drawings_legacy_pk"""
            )
            connection.execute("DROP TABLE account_chart_drawings_legacy_pk")
            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_account_chart_drawings_scope
                       ON account_chart_drawings(user_id, ticker, chart_range, adjustment)"""
            )
        connection.executescript(_SCHEMA)
        connection.execute(
            """INSERT INTO account_chart_drawing_scopes
                   (user_id, ticker, chart_range, adjustment, scope_revision)
               SELECT user_id, ticker, chart_range, adjustment, 1
                 FROM account_chart_drawings
                GROUP BY user_id, ticker, chart_range, adjustment
                ON CONFLICT(user_id, ticker, chart_range, adjustment)
                DO NOTHING"""
        )

    # ---------------- accounts ----------------

    def register(self, username: str, password: str) -> SessionResult:
        display, key = normalize_username(username)
        password_hash = hash_account_password(password)
        self.initialize()
        now_iso = _utcnow_iso()
        user_id = f"usr_{uuid.uuid4().hex}"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            total = int(
                connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
            )
            if total >= self._max_accounts:
                connection.rollback()
                raise AccountError("registration_closed")
            existing = connection.execute(
                "SELECT 1 FROM accounts WHERE username_key=?",
                (key,),
            ).fetchone()
            if existing is not None:
                connection.rollback()
                raise AccountError("username_taken")
            connection.execute(
                """INSERT INTO accounts
                       (user_id, username, username_key, password_hash, created_at)
                   VALUES (?,?,?,?,?)""",
                (user_id, display, key, password_hash, now_iso),
            )
            connection.commit()
        account = Account(user_id=user_id, username=display, created_at=now_iso)
        return self._issue_session(account)

    def ensure_owner_account(self) -> Account:
        """Return the owner's watchlist principal, creating its row on first use.

        The owner authenticates through ``APP_PASSWORD_HASH`` and never holds an
        account cookie, so this row exists only to anchor watchlist rows against
        the foreign key. Its password hash is derived from a secret that is
        discarded immediately, so the customer login path cannot authenticate as
        it even if the reserved-name guard were ever removed.
        """

        self.initialize()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT user_id, username, created_at FROM accounts WHERE user_id=?",
                (OWNER_USER_ID,),
            ).fetchone()
            if row is not None:
                connection.commit()
                return Account(
                    user_id=str(row[0]),
                    username=str(row[1]),
                    created_at=str(row[2]),
                )
            now_iso = _utcnow_iso()
            connection.execute(
                """INSERT INTO accounts
                       (user_id, username, username_key, password_hash, created_at)
                   VALUES (?,?,?,?,?)""",
                (
                    OWNER_USER_ID,
                    OWNER_ACCOUNT_USERNAME,
                    OWNER_ACCOUNT_USERNAME,
                    hash_account_password(secrets.token_urlsafe(32)),
                    now_iso,
                ),
            )
            connection.commit()
        return Account(
            user_id=OWNER_USER_ID,
            username=OWNER_ACCOUNT_USERNAME,
            created_at=now_iso,
        )

    def authenticate(self, username: str, password: str) -> SessionResult:
        try:
            _display, key = normalize_username(username)
        except AccountError:
            # Never leak which half was wrong.
            raise AccountError("invalid_credentials") from None
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                """SELECT user_id, username, password_hash, created_at
                     FROM accounts WHERE username_key=?""",
                (key,),
            ).fetchone()
        if row is None:
            # Spend comparable time so a missing user is not faster than a
            # wrong password.
            verify_account_password(password, hash_account_password("placeholder"))
            raise AccountError("invalid_credentials")
        if not verify_account_password(password, str(row["password_hash"])):
            raise AccountError("invalid_credentials")
        account = Account(
            user_id=str(row["user_id"]),
            username=str(row["username"]),
            created_at=str(row["created_at"]),
        )
        with self._connect() as connection:
            connection.execute(
                "UPDATE accounts SET last_login_at=? WHERE user_id=?",
                (_utcnow_iso(), account.user_id),
            )
            connection.commit()
        return self._issue_session(account)

    def account_count(self) -> int:
        self.initialize()
        with self._connect() as connection:
            return int(
                connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
            )

    # ---------------- sessions ----------------

    def _issue_session(self, account: Account) -> SessionResult:
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        expires_at = float(self._clock()) + SESSION_SECONDS
        with self._connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO account_sessions
                       (token_sha256, user_id, created_at, expires_at)
                   VALUES (?,?,?,?)""",
                (digest, account.user_id, _utcnow_iso(), expires_at),
            )
            connection.execute(
                "DELETE FROM account_sessions WHERE expires_at<=?",
                (float(self._clock()),),
            )
            connection.commit()
        return SessionResult(token=token, expires_at=expires_at, account=account)

    def resolve_session(self, token: str) -> Account | None:
        if not token:
            return None
        self.initialize()
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = float(self._clock())
        with self._connect() as connection:
            row = connection.execute(
                """SELECT a.user_id, a.username, a.created_at
                     FROM account_sessions AS s
                     JOIN accounts AS a ON a.user_id=s.user_id
                    WHERE s.token_sha256=? AND s.expires_at>?""",
                (digest, now),
            ).fetchone()
        if row is None:
            return None
        return Account(
            user_id=str(row["user_id"]),
            username=str(row["username"]),
            created_at=str(row["created_at"]),
        )

    def revoke_session(self, token: str) -> None:
        if not token:
            return
        self.initialize()
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM account_sessions WHERE token_sha256=?",
                (digest,),
            )
            connection.commit()

    # ---------------- watchlist ----------------

    def watchlist(self, user_id: str) -> list[str]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT ticker FROM account_watchlist
                    WHERE user_id=? ORDER BY position, ticker""",
                (user_id,),
            ).fetchall()
        return [str(row["ticker"]) for row in rows]

    def replace_watchlist(self, user_id: str, tickers: Iterable[str]) -> list[str]:
        ordered: list[str] = []
        for value in tickers:
            symbol = normalize_ticker(value)
            if symbol not in ordered:
                ordered.append(symbol)
        if len(ordered) > WATCHLIST_MAX_TICKERS:
            raise AccountError("watchlist_full")
        self.initialize()
        now_iso = _utcnow_iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM account_watchlist WHERE user_id=?",
                (user_id,),
            )
            connection.executemany(
                """INSERT INTO account_watchlist
                       (user_id, ticker, position, added_at)
                   VALUES (?,?,?,?)""",
                [
                    (user_id, symbol, index, now_iso)
                    for index, symbol in enumerate(ordered)
                ],
            )
            connection.commit()
        return ordered

    def add_ticker(self, user_id: str, ticker: str) -> list[str]:
        symbol = normalize_ticker(ticker)
        self.initialize()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """SELECT ticker, position FROM account_watchlist
                    WHERE user_id=? ORDER BY position, ticker""",
                (user_id,),
            ).fetchall()
            current = [str(row["ticker"]) for row in rows]
            if symbol in current:
                connection.rollback()
                return current
            if len(current) >= WATCHLIST_MAX_TICKERS:
                connection.rollback()
                raise AccountError("watchlist_full")
            next_position = (
                max((int(row["position"]) for row in rows), default=-1) + 1
            )
            connection.execute(
                """INSERT INTO account_watchlist
                       (user_id, ticker, position, added_at)
                   VALUES (?,?,?,?)""",
                (user_id, symbol, next_position, _utcnow_iso()),
            )
            connection.commit()
        return [*current, symbol]

    def remove_ticker(self, user_id: str, ticker: str) -> list[str]:
        symbol = normalize_ticker(ticker)
        self.initialize()
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM account_watchlist WHERE user_id=? AND ticker=?",
                (user_id, symbol),
            )
            connection.commit()
        return self.watchlist(user_id)

    # ---------------- chart drawings ----------------

    def _row_to_drawing(self, row: sqlite3.Row) -> dict[str, Any]:
        try:
            payload = json.loads(str(row["payload_json"]))
        except json.JSONDecodeError as exc:
            raise AccountError("invalid_payload") from exc
        if not isinstance(payload, dict):
            raise AccountError("invalid_payload")
        parsed = validate_drawing_payload(
            {
                "schemaVersion": payload.get("schemaVersion", DRAWING_SCHEMA_VERSION),
                "id": str(row["drawing_id"]),
                "ticker": str(row["ticker"]),
                "range": str(row["chart_range"]),
                "adjustment": str(row["adjustment"]),
                "kind": payload.get("kind", row["kind"]),
                "anchors": payload.get("anchors"),
                "style": payload.get("style"),
                "text": payload.get("text"),
                "locked": payload.get("locked", False),
                "hidden": payload.get("hidden", False),
                "zOrder": payload.get("zOrder", 0),
            },
            require_id=True,
        )
        return self._parsed_to_drawing(
            parsed,
            revision=int(row["revision"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def _read_scope_revision(
        self,
        connection: sqlite3.Connection,
        user_id: str,
        ticker: str,
        chart_range: str,
        adjustment: str,
    ) -> int:
        row = connection.execute(
            """SELECT scope_revision FROM account_chart_drawing_scopes
                WHERE user_id=? AND ticker=? AND chart_range=? AND adjustment=?""",
            (user_id, ticker, chart_range, adjustment),
        ).fetchone()
        return 0 if row is None else int(row[0])

    def _bump_scope_revision(
        self,
        connection: sqlite3.Connection,
        user_id: str,
        ticker: str,
        chart_range: str,
        adjustment: str,
        expected: int,
    ) -> int:
        current = self._read_scope_revision(
            connection, user_id, ticker, chart_range, adjustment
        )
        if current != expected:
            raise AccountError("scope_revision_conflict")
        new_revision = current + 1
        connection.execute(
            """INSERT INTO account_chart_drawing_scopes
                   (user_id, ticker, chart_range, adjustment, scope_revision)
               VALUES (?,?,?,?,?)
               ON CONFLICT(user_id, ticker, chart_range, adjustment)
               DO UPDATE SET scope_revision=excluded.scope_revision""",
            (user_id, ticker, chart_range, adjustment, new_revision),
        )
        return new_revision

    def scope_revision(
        self,
        user_id: str,
        ticker: str,
        chart_range: str,
        adjustment: str = "raw",
    ) -> int:
        symbol = normalize_ticker(ticker)
        range_key = normalize_chart_range(chart_range)
        adj = normalize_chart_adjustment(adjustment)
        self.initialize()
        with self._connect() as connection:
            return self._read_scope_revision(
                connection, user_id, symbol, range_key, adj
            )

    def _parsed_to_drawing(
        self,
        parsed: Mapping[str, Any],
        *,
        revision: int,
        created_at: str,
        updated_at: str,
    ) -> dict[str, Any]:
        """用手上的解析结果拼响应。

        提交后再开第二条连接回读，并发删除会让回读落空；旧代码用裸 assert 兜着，
        既可能变成 500，在 ``python -O`` 下又会直接返回 null 正文。
        """

        payload = parsed["payload"]
        return {
            "schemaVersion": DRAWING_SCHEMA_VERSION,
            "id": str(parsed["id"]),
            "ticker": str(parsed["ticker"]),
            "range": str(parsed["range"]),
            "adjustment": str(parsed["adjustment"]),
            "kind": str(parsed["kind"]),
            "anchors": payload["anchors"],
            "style": payload["style"],
            "text": payload["text"],
            "locked": bool(payload["locked"]),
            "hidden": bool(payload["hidden"]),
            "zOrder": int(payload["zOrder"]),
            "revision": int(revision),
            "createdAt": created_at,
            "updatedAt": updated_at,
        }

    def list_drawings_page(
        self,
        user_id: str,
        ticker: str,
        chart_range: str,
        adjustment: str = "raw",
    ) -> tuple[list[dict[str, Any]], int]:
        symbol = normalize_ticker(ticker)
        range_key = normalize_chart_range(chart_range)
        adj = normalize_chart_adjustment(adjustment)
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT {_DRAWING_COLUMNS}
                      FROM account_chart_drawings
                     WHERE user_id=? AND ticker=? AND chart_range=? AND adjustment=?
                     ORDER BY created_at, drawing_id""",
                (user_id, symbol, range_key, adj),
            ).fetchall()
            drawings = [self._row_to_drawing(row) for row in rows]
            revision = self._read_scope_revision(
                connection, user_id, symbol, range_key, adj
            )
        return drawings, revision

    def list_drawings(
        self,
        user_id: str,
        ticker: str,
        chart_range: str,
        adjustment: str = "raw",
    ) -> list[dict[str, Any]]:
        drawings, _revision = self.list_drawings_page(
            user_id, ticker, chart_range, adjustment
        )
        return drawings

    def get_drawing(self, user_id: str, drawing_id: str) -> dict[str, Any] | None:
        drawing_id = normalize_drawing_id(drawing_id)
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                f"""SELECT {_DRAWING_COLUMNS}
                      FROM account_chart_drawings
                     WHERE user_id=? AND drawing_id=?""",
                (user_id, drawing_id),
            ).fetchone()
        return None if row is None else self._row_to_drawing(row)

    def create_drawing(
        self,
        user_id: str,
        body: Mapping[str, Any],
        *,
        expected_scope_revision: int,
    ) -> tuple[dict[str, Any], int]:
        expected = _require_expected_scope_revision(expected_scope_revision)
        parsed = validate_drawing_payload(body, require_id=True)
        drawing_id = parsed["id"]
        self.initialize()
        now_iso = _utcnow_iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                f"""SELECT {_DRAWING_COLUMNS}
                      FROM account_chart_drawings
                     WHERE user_id=? AND drawing_id=?""",
                (user_id, drawing_id),
            ).fetchone()
            if existing is not None:
                same_scope = (
                    str(existing["ticker"]) == parsed["ticker"]
                    and str(existing["chart_range"]) == parsed["range"]
                    and str(existing["adjustment"]) == parsed["adjustment"]
                )
                same_payload = (
                    str(existing["kind"]) == parsed["kind"]
                    and str(existing["payload_json"]) == parsed["payload_json"]
                )
                if same_scope and same_payload:
                    # 丢失响应后的重放：同一条 payload 仍按成功返回，且不抬
                    # scope_revision，这样客户端还能拿着旧 expected 重试。
                    revision = self._read_scope_revision(
                        connection,
                        user_id,
                        parsed["ticker"],
                        parsed["range"],
                        parsed["adjustment"],
                    )
                    connection.rollback()
                    return self._row_to_drawing(existing), revision
                connection.rollback()
                raise AccountError("drawing_id_conflict")
            try:
                new_revision = self._bump_scope_revision(
                    connection,
                    user_id,
                    parsed["ticker"],
                    parsed["range"],
                    parsed["adjustment"],
                    expected,
                )
            except AccountError:
                connection.rollback()
                raise
            range_count = int(
                connection.execute(
                    """SELECT COUNT(*) FROM account_chart_drawings
                        WHERE user_id=? AND ticker=? AND chart_range=? AND adjustment=?""",
                    (user_id, parsed["ticker"], parsed["range"], parsed["adjustment"]),
                ).fetchone()[0]
            )
            if range_count >= DRAWINGS_PER_RANGE_MAX:
                connection.rollback()
                raise AccountError("drawings_range_full")
            total = int(
                connection.execute(
                    "SELECT COUNT(*) FROM account_chart_drawings WHERE user_id=?",
                    (user_id,),
                ).fetchone()[0]
            )
            if total >= DRAWINGS_PER_ACCOUNT_MAX:
                connection.rollback()
                raise AccountError("drawings_full")
            connection.execute(
                """INSERT INTO account_chart_drawings
                       (drawing_id, user_id, ticker, chart_range, adjustment,
                        kind, payload_json, revision, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    drawing_id,
                    user_id,
                    parsed["ticker"],
                    parsed["range"],
                    parsed["adjustment"],
                    parsed["kind"],
                    parsed["payload_json"],
                    1,
                    now_iso,
                    now_iso,
                ),
            )
            connection.commit()
        return (
            self._parsed_to_drawing(
                parsed,
                revision=1,
                created_at=now_iso,
                updated_at=now_iso,
            ),
            new_revision,
        )

    def update_drawing(
        self,
        user_id: str,
        drawing_id: str,
        body: Mapping[str, Any],
        *,
        expected_revision: int,
        expected_scope_revision: int,
    ) -> tuple[dict[str, Any], int]:
        drawing_id = normalize_drawing_id(drawing_id)
        expected_scope = _require_expected_scope_revision(expected_scope_revision)
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 1
        ):
            # 版本号本身不合法是请求写错了，不是版本冲突：只有 revision_conflict
            # 这一个码代表「别处改过了」，客户端要靠它决定是否弹重载对话框。
            raise AccountError("invalid_payload")
        parsed = validate_drawing_payload(
            {**body, "id": drawing_id},
            require_id=True,
        )
        self.initialize()
        now_iso = _utcnow_iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT ticker, chart_range, adjustment, revision, created_at
                     FROM account_chart_drawings
                    WHERE user_id=? AND drawing_id=?""",
                (user_id, drawing_id),
            ).fetchone()
            if row is None:
                # 别人的编号在本账户下就是不存在，和自己删掉的行同一个码，客户端
                # 拿到 drawing_not_found 就该丢掉这个任务而不是无限重试。
                connection.rollback()
                raise AccountError("drawing_not_found")
            if (
                str(row["ticker"]) != parsed["ticker"]
                or str(row["chart_range"]) != parsed["range"]
                or str(row["adjustment"]) != parsed["adjustment"]
            ):
                connection.rollback()
                raise AccountError("scope_mismatch")
            try:
                new_scope_revision = self._bump_scope_revision(
                    connection,
                    user_id,
                    parsed["ticker"],
                    parsed["range"],
                    parsed["adjustment"],
                    expected_scope,
                )
            except AccountError:
                connection.rollback()
                raise
            current_revision = int(row["revision"])
            if current_revision != expected_revision:
                connection.rollback()
                raise AccountError("revision_conflict")
            connection.execute(
                """UPDATE account_chart_drawings
                      SET kind=?, payload_json=?, revision=?, updated_at=?
                    WHERE drawing_id=? AND user_id=? AND revision=?""",
                (
                    parsed["kind"],
                    parsed["payload_json"],
                    current_revision + 1,
                    now_iso,
                    drawing_id,
                    user_id,
                    expected_revision,
                ),
            )
            connection.commit()
        return (
            self._parsed_to_drawing(
                parsed,
                revision=current_revision + 1,
                created_at=str(row["created_at"]),
                updated_at=now_iso,
            ),
            new_scope_revision,
        )

    def delete_drawing(
        self,
        user_id: str,
        drawing_id: str,
        *,
        ticker: str,
        chart_range: str,
        adjustment: str,
        expected_scope_revision: int,
    ) -> tuple[bool, int]:
        """删除是幂等的：行本来就不在，也算删成功，且不抬 scope_revision。

        没有墓碑表，重放的删除和多设备重复删除永远找不到行；报 404 的话客户端
        的 outbox 就再也丢不掉这个任务。别人的行照样删不到——user_id 是条件之一。
        行还在时必须对上 expected_scope_revision，否则 409，避免清掉别人刚画的。
        指名 scope 的真实 revision 始终返回，前端不得猜 expected+1。
        """

        expected = _require_expected_scope_revision(expected_scope_revision)
        drawing_id = normalize_drawing_id(drawing_id)
        symbol = normalize_ticker(ticker)
        range_key = normalize_chart_range(chart_range)
        adj = normalize_chart_adjustment(adjustment)
        self.initialize()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._read_scope_revision(
                connection, user_id, symbol, range_key, adj
            )
            row = connection.execute(
                """SELECT ticker, chart_range, adjustment
                     FROM account_chart_drawings
                    WHERE user_id=? AND drawing_id=?""",
                (user_id, drawing_id),
            ).fetchone()
            if row is None:
                if current != expected:
                    connection.rollback()
                    raise AccountError("scope_revision_conflict")
                connection.commit()
                return False, current
            if (
                str(row["ticker"]) != symbol
                or str(row["chart_range"]) != range_key
                or str(row["adjustment"]) != adj
            ):
                connection.rollback()
                raise AccountError("scope_mismatch")
            try:
                new_revision = self._bump_scope_revision(
                    connection,
                    user_id,
                    symbol,
                    range_key,
                    adj,
                    expected,
                )
            except AccountError:
                connection.rollback()
                raise
            connection.execute(
                "DELETE FROM account_chart_drawings WHERE user_id=? AND drawing_id=?",
                (user_id, drawing_id),
            )
            connection.commit()
        return True, new_revision

    def replace_drawings_in_scope(
        self,
        user_id: str,
        ticker: str,
        chart_range: str,
        adjustment: str,
        drawings: Sequence[Mapping[str, Any]],
        *,
        expected_scope_revision: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """Validate all, then replace the current ticker+range+adjustment set in one transaction."""

        expected = _require_expected_scope_revision(expected_scope_revision)
        symbol = normalize_ticker(ticker)
        range_key = normalize_chart_range(chart_range)
        adj = normalize_chart_adjustment(adjustment)
        if len(drawings) > DRAWINGS_PER_RANGE_MAX:
            raise AccountError("drawings_range_full")
        parsed_rows: list[dict[str, Any]] = []
        for body in drawings:
            parsed = validate_drawing_payload(body, require_id=True)
            if (
                parsed["ticker"] != symbol
                or parsed["range"] != range_key
                or parsed["adjustment"] != adj
            ):
                raise AccountError("scope_mismatch")
            parsed_rows.append(parsed)
        self.initialize()
        now_iso = _utcnow_iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                new_revision = self._bump_scope_revision(
                    connection, user_id, symbol, range_key, adj, expected
                )
            except AccountError:
                connection.rollback()
                raise
            other_count = int(
                connection.execute(
                    """SELECT COUNT(*) FROM account_chart_drawings
                        WHERE user_id=? AND NOT (
                            ticker=? AND chart_range=? AND adjustment=?
                        )""",
                    (user_id, symbol, range_key, adj),
                ).fetchone()[0]
            )
            if other_count + len(parsed_rows) > DRAWINGS_PER_ACCOUNT_MAX:
                connection.rollback()
                raise AccountError("drawings_full")
            connection.execute(
                """DELETE FROM account_chart_drawings
                    WHERE user_id=? AND ticker=? AND chart_range=? AND adjustment=?""",
                (user_id, symbol, range_key, adj),
            )
            # 只探这一批候选编号，而且限定在本账户内：原来是全表扫 drawing_id，
            # 在 BEGIN IMMEDIATE 里握着唯一的写锁做 O(全表) 的工作，登录/会话/
            # 自选的写入都得排队等它。本作用域的行刚被删掉，命中的只可能是本账户
            # 其他周期占着的编号，仍旧改发新编号。
            candidate_ids = [str(parsed["id"]) for parsed in parsed_rows]
            taken: set[str] = set()
            if candidate_ids:
                placeholders = ",".join("?" * len(candidate_ids))
                taken = {
                    str(row[0])
                    for row in connection.execute(
                        f"""SELECT drawing_id FROM account_chart_drawings
                             WHERE user_id=? AND drawing_id IN ({placeholders})""",
                        (user_id, *candidate_ids),
                    )
                }
            for parsed in parsed_rows:
                drawing_id = parsed["id"]
                if drawing_id in taken:
                    drawing_id = str(uuid.uuid4())
                taken.add(drawing_id)
                connection.execute(
                    """INSERT INTO account_chart_drawings
                           (drawing_id, user_id, ticker, chart_range, adjustment,
                            kind, payload_json, revision, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        drawing_id,
                        user_id,
                        parsed["ticker"],
                        parsed["range"],
                        parsed["adjustment"],
                        parsed["kind"],
                        parsed["payload_json"],
                        1,
                        now_iso,
                        now_iso,
                    ),
                )
            rows = connection.execute(
                f"""SELECT {_DRAWING_COLUMNS}
                      FROM account_chart_drawings
                     WHERE user_id=? AND ticker=? AND chart_range=? AND adjustment=?
                     ORDER BY created_at, drawing_id""",
                (user_id, symbol, range_key, adj),
            ).fetchall()
            listed = [self._row_to_drawing(row) for row in rows]
            connection.commit()
        return listed, new_revision

    def delete_drawings_in_scope(
        self,
        user_id: str,
        ticker: str,
        chart_range: str,
        adjustment: str = "raw",
        *,
        expected_scope_revision: int,
    ) -> tuple[int, int]:
        expected = _require_expected_scope_revision(expected_scope_revision)
        symbol = normalize_ticker(ticker)
        range_key = normalize_chart_range(chart_range)
        adj = normalize_chart_adjustment(adjustment)
        self.initialize()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                new_revision = self._bump_scope_revision(
                    connection, user_id, symbol, range_key, adj, expected
                )
            except AccountError:
                connection.rollback()
                raise
            cursor = connection.execute(
                """DELETE FROM account_chart_drawings
                    WHERE user_id=? AND ticker=? AND chart_range=? AND adjustment=?""",
                (user_id, symbol, range_key, adj),
            )
            deleted = int(cursor.rowcount)
            connection.commit()
        return deleted, new_revision

    def delete_account(self, user_id: str) -> None:
        """Hard-delete one customer account; the FK cascade takes its rows too.

        没有 HTTP 入口，只给测试和人工运维用。owner 那一行是 owner 全部个人数据
        （自选、绘图）的外键锚点，删掉就等于清空，所以直接挡住。
        """

        if str(user_id) == OWNER_USER_ID:
            raise AccountError("account_delete_forbidden")
        self.initialize()
        with self._connect() as connection:
            connection.execute("DELETE FROM accounts WHERE user_id=?", (user_id,))
            connection.commit()


_store: AccountStore | None = None
_store_lock = threading.Lock()


def get_account_store() -> AccountStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                from app.data_paths import get_data_paths

                _store = AccountStore(get_data_paths().accounts_db)
    return _store


def set_account_store(store: AccountStore | None) -> None:
    """Test seam: swap the process-wide store."""

    global _store
    with _store_lock:
        _store = store
