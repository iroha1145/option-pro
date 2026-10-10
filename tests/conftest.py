from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


# 收集测试前丢掉环境里的行情供应商密钥。否则本机 secrets 会让夹具打到真实 API。
_PROVIDER_SECRET_PREFIXES = ("MASSIVE_", "SHARADAR_")


def _strip_ambient_provider_secrets() -> None:
    for key in list(os.environ):
        if key.startswith(_PROVIDER_SECRET_PREFIXES):
            del os.environ[key]


def _clear_settings_cache() -> None:
    from app.config import get_settings

    # 有的用例会把 get_settings 换成普通函数。夹具收尾时补丁还在，
    # 不能对着替身找 cache_clear；改清最初那个 lru 缓存。
    clear = getattr(get_settings, "cache_clear", None)
    if clear is None:
        clear = getattr(_clear_settings_cache, "saved", None)
    else:
        _clear_settings_cache.saved = clear
    if clear is not None:
        clear()


_strip_ambient_provider_secrets()


def pytest_configure(config) -> None:
    # 再清一次：插件导入可能已经构造过 Settings 缓存。
    del config
    _strip_ambient_provider_secrets()
    _clear_settings_cache()


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


@pytest.fixture(autouse=True)
def _isolated_provider_secrets(monkeypatch):
    """每个用例开始前再丢掉供应商密钥，并清掉 Settings 缓存。

    用例若要覆盖密钥，应在本夹具之后自行 setenv，并再次 cache_clear。
    """
    for key in list(os.environ):
        if key.startswith(_PROVIDER_SECRET_PREFIXES):
            monkeypatch.delenv(key, raising=False)
    _clear_settings_cache()
    yield
    _clear_settings_cache()


@pytest.fixture(autouse=True)
def _isolated_runtime_data(monkeypatch, tmp_path):
    """Give ordinary tests a writable runtime root without touching /data.

    Tests can still set or delete DATA_DIR to exercise the real resolver.
    The default settings store caches its resolved path, so reset that cache
    on both sides of the test as well as restoring the environment.
    """
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "runtime-data"))
    from app.services.runtime_settings import get_runtime_settings_store

    get_runtime_settings_store.cache_clear()
    yield
    get_runtime_settings_store.cache_clear()


@pytest.fixture(autouse=True)
def _isolated_public_option_store(monkeypatch, tmp_path, _isolated_runtime_data):
    """HTTP and worker option snapshots must never leak between tests."""
    from app import public_option_data

    monkeypatch.setattr(public_option_data, "default_option_root", lambda: tmp_path / "public-options")


@pytest.fixture
def isolated_option_accounts(monkeypatch, tmp_path):
    """Exercise trusted option coverage without opening the runtime account DB."""
    from app.services import accounts

    store = accounts.AccountStore(tmp_path / "option-accounts.sqlite")
    monkeypatch.setattr(accounts, "_store", store)
    return store


@pytest.fixture(autouse=True)
def _isolated_finnhub_budget(monkeypatch, tmp_path, _isolated_runtime_data):
    """Provider mocks share the real limiter, with a fresh per-test database."""
    from app.services import finnhub_budget

    monkeypatch.setattr(finnhub_budget, "default_budget_path", lambda: tmp_path / "finnhub-budget.sqlite")


@pytest.fixture(autouse=True)
def _offline_news_sources(monkeypatch):
    """The news collector's default client never reaches a real source in tests.

    A catalyst task built from the repository configuration collects news
    locally; a test that forgets to inject fetchers fails here instead of
    polling Google News or Forex Factory.
    """
    import httpx

    from app.services.catalysts import news_sources

    attempts: list[str] = []

    def refuse(request: httpx.Request) -> httpx.Response:
        attempts.append(f"{request.url.scheme}://{request.url.host}{request.url.path}")
        raise httpx.ConnectError("network is disabled in tests", request=request)

    real_client = news_sources.http_client

    def offline_client(*, transport=None):
        return real_client(transport=transport or httpx.MockTransport(refuse))

    monkeypatch.setattr(news_sources, "http_client", offline_client)
    yield
    assert not attempts, f"tests must inject catalyst source fetchers: {attempts}"


@pytest.fixture
def anchor_ai_jobs_clock(monkeypatch):
    """把 ai_jobs 仓储的私有时钟锚到用例的夹具时钟上。

    `latest_for_report` 与 `latest_completed` 的「近 30 天任务」滑动窗口读的是
    `repository._utcnow`（真实时钟），而用例的夹具行按冻结日期写入：真实日期
    一旦走出那 30 天，这些查询就再也看不到夹具行，用例会在某个具体日历日之后
    **确定性**变红——与任何代码改动无关的时间炸弹（2026-08-24 已咬过一次）。

    锚定后时钟自 anchor 起随真实流逝走动：完全冻死会让同一批行共享 created_at，
    `latest_*` 的按时间排序就失去依据。

    这里收成一个共享夹具是因为同样的 monkeypatch 已经在多个测试文件里被各写
    各的（有冻死的、有流动的）——需要它的新用例请调用本夹具，不要再抄一份。
    """

    def _anchor(anchor: datetime) -> datetime:
        real_start = datetime.now(timezone.utc)
        monkeypatch.setattr(
            "app.services.ai_jobs.repository._utcnow",
            lambda: anchor + (datetime.now(timezone.utc) - real_start),
        )
        return anchor

    return _anchor


@pytest.fixture(autouse=True)
def _reset_read_caches(_isolated_runtime_data):
    """Isolate fingerprint/byte read caches between tests.

    These module-level caches are keyed by file identity and version, which is
    correct in production but lets one test's parsed document leak into the
    next when tmp paths or frozen clocks repeat.
    """

    from app import public_home_snapshot, public_stock_data
    from app.api import sectors as sectors_api
    from app.api import stocks as stocks_api
    from app.api import strength as strength_api
    from app.services import http_read_cache
    from app.services.ai_jobs import models as ai_job_models

    from app.services import watchlist_six_month

    def _clear() -> None:
        watchlist_six_month.reset_cache()
        ai_job_models._result_verdicts.clear()
        public_home_snapshot._parsed_documents.invalidate()
        strength_api._strength_documents.invalidate()
        sectors_api._sector_iv_documents.invalidate()
        http_read_cache.reset_serialized_response_cache()
        stocks_api._watchlist_owner_snapshot_observed = None
        stocks_api._technical_visitor_results.clear()
        public_stock_data._metadata_cache.clear()

    _clear()
    yield
    _clear()


@pytest.fixture(autouse=True)
def _isolated_company_logo_cache(monkeypatch, tmp_path, _isolated_runtime_data):
    """Logo disk/memory caches cannot leak provider fixtures between tests."""
    from app.services import company_logo_cache
    from app.api import stocks

    monkeypatch.setattr(company_logo_cache, "cache_path", lambda: tmp_path / "company-logos.sqlite")
    for key in list(stocks._endpoint_cache):
        if key.startswith("logo:"):
            stocks._endpoint_cache.pop(key, None)
    stocks._logo_retry_after.clear()


@pytest.fixture(autouse=True)
def _isolated_sector_iv_refresh(monkeypatch, tmp_path, _isolated_runtime_data):
    """Sector snapshots and their persistent public demand stay local to a test."""
    from app.api import sectors

    monkeypatch.setattr(sectors, "_SECTOR_IV_SNAPSHOT_DIR", tmp_path / "sector-iv")
    sectors._public_sector_iv_recent.clear()
