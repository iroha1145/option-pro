from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


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
def _reset_read_caches():
    """Isolate fingerprint/byte read caches between tests.

    These module-level caches are keyed by file identity and version, which is
    correct in production but lets one test's parsed document leak into the
    next when tmp paths or frozen clocks repeat.
    """

    from app import public_home_snapshot
    from app.api import sectors as sectors_api
    from app.api import stocks as stocks_api
    from app.api import strength as strength_api
    from app.services import http_read_cache

    def _clear() -> None:
        public_home_snapshot._parsed_documents.invalidate()
        strength_api._strength_documents.invalidate()
        sectors_api._sector_iv_documents.invalidate()
        http_read_cache.reset_serialized_response_cache()
        stocks_api._watchlist_owner_snapshot_observed = None

    _clear()
    yield
    _clear()
