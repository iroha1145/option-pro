#!/usr/bin/env python3
"""Seed a deterministic catalyst-cache.db for isolated performance runs.

Does not call MacroLens, OpenAI, or market vendors. Writes only under DATA_DIR.
Visible feed rows use Simplified Chinese raw title/summary so the public
projection keeps them (same rule as production _displayable_zh). A subset is
left without summary (hidden), a subset has image URLs / multi-source /
near-duplicate titles, and a subset is older than the default 72h window.
Analysis rows are not fabricated here: attaching fake job/audit rows would
bypass the publish contract. Classification-filter benches use the visible
unanalyzed majority plus official analysis helpers in later experiments.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.access import request_owner_access_context  # noqa: E402
from app.services.ai_jobs.repository import AIJobRepository  # noqa: E402
from app.services.catalysts.etl_client import NewsChangesPage  # noqa: E402
from app.services.catalysts.etl_repository import CatalystEtlRepository  # noqa: E402
from app.services.catalysts.local_intelligence import (  # noqa: E402
    LocalCatalystIntelligence,
    _reset_revision_cache,
)

TICKERS = ("NVDA", "AMD", "AAPL", "TSLA", "MSFT", "AMZN", "META", "GOOGL", "SPY", "QQQ")
SOURCES = ("路透", "彭博", "华尔街日报", "财联社", "第一财经")
NOW = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _zh_title(rng: random.Random, news_id: int, kind: str) -> str:
    base = (
        f"第{news_id}条：芯片与期权市场关注后续交付与定价"
        if kind != "short"
        else f"第{news_id}条快讯"
    )
    if kind == "long":
        return base + "，供应链、客户采用与库存去化需要持续观察，不能把标题情绪当成收益承诺。"
    return base


def _zh_summary(rng: random.Random, news_id: int, kind: str) -> str | None:
    if kind == "missing":
        return None
    text = (
        f"编号{news_id}的报道描述公司进展与行业读数。"
        "具体订单、出货与指引仍待后续披露，不能把情绪分数当成胜率。"
    )
    if kind == "long":
        text += "补充背景包括产能爬坡、渠道库存与宏观利率对风险资产的影响，需与行情交叉核对。"
    return text


def _change(
    sequence: int,
    news_id: int,
    *,
    available_at: datetime,
    rng: random.Random,
) -> dict:
    title_kind = ("short", "medium", "long")[news_id % 3]
    summary_kind = "missing" if news_id % 17 == 0 else ("long" if news_id % 5 == 0 else "medium")
    ticker_count = 1 + (news_id % 3 == 0)
    tickers = [TICKERS[(news_id + i) % len(TICKERS)] for i in range(ticker_count)]
    multi = news_id % 7 == 0
    sources = [SOURCES[news_id % len(SOURCES)]]
    if multi:
        sources.append(SOURCES[(news_id + 2) % len(SOURCES)])
    title = _zh_title(rng, news_id, title_kind)
    if news_id % 11 == 0:
        title = _zh_title(rng, max(1, news_id - 11), title_kind)  # near-duplicate candidate
    return {
        "sequence": sequence,
        "operation": "upsert",
        "changed_at": _iso(available_at),
        "source_updated_at": _iso(available_at),
        "available_at": _iso(available_at),
        "news_id": news_id,
        "news": {
            "id": news_id,
            "source": sources[0],
            "title": title,
            "summary": _zh_summary(rng, news_id, summary_kind),
            "url": f"https://example.test/news/{news_id}/{sequence}",
            "image_url": f"https://example.test/img/{news_id}.jpg" if news_id % 4 == 0 else None,
            "published_at": _iso(available_at - timedelta(minutes=5)),
            "fetched_at": _iso(available_at),
            "updated_at": _iso(available_at),
            "source_tickers": tickers,
            "sources": sources,
            "source_count": len(sources),
            "source_observations": [],
            "content_hash": hashlib.sha256(f"{news_id}-{sequence}-{title}".encode()).hexdigest()[:24],
        },
    }


def _apply_page(etl: CatalystEtlRepository, changes: list[dict], as_of: datetime) -> None:
    state = etl.state("news")
    sequence = max(int(item["sequence"]) for item in changes)
    page = NewsChangesPage.model_validate(
        {
            "items": changes,
            "has_more": False,
            "next_cursor": None,
            "watermark": {"sequence": sequence, "as_of": _iso(as_of)},
            "next_updated_after": _iso(as_of),
            "next_after_sequence": sequence,
        }
    )
    etl.apply_news_page(
        page,
        expected_cursor=state.cursor,
        expected_generation=state.generation,
    )


def seed(data_dir: Path, count: int, rng_seed: int) -> dict:
    data_dir.mkdir(parents=True, exist_ok=True)
    cache_path = data_dir / "catalyst-cache.db"
    ai_path = data_dir / "ai-jobs.db"
    for leftover in (cache_path, ai_path, Path(str(cache_path) + "-wal"), Path(str(cache_path) + "-shm")):
        if leftover.exists():
            leftover.unlink()
    _reset_revision_cache()
    etl = CatalystEtlRepository(cache_path)
    etl.initialize()
    ai = AIJobRepository(ai_path)
    intelligence = LocalCatalystIntelligence(
        cache_path,
        ai,
        mode="manual",
        canonical_tickers=TICKERS,
        max_queued=max(200, count),
    )
    rng = random.Random(rng_seed)
    batch: list[dict] = []
    with request_owner_access_context(True):
        intelligence.initialize()
        for index in range(count):
            news_id = index + 1
            sequence = index + 1
            if news_id % 23 == 0:
                available = NOW - timedelta(hours=80 + (news_id % 40))  # outside 72h
            else:
                available = NOW - timedelta(minutes=10 + (news_id * 7) % (70 * 60))
            batch.append(_change(sequence, news_id, available_at=available, rng=rng))
            if len(batch) >= 250:
                _apply_page(etl, batch, NOW)
                batch = []
        if batch:
            _apply_page(etl, batch, NOW)
        intelligence.reconcile()
        _reset_revision_cache()
        payload = intelligence.feed(as_of=NOW, window_hours=72, limit=12)
    revisions = payload.get("summary", {}).get("count")
    manifest = {
        "seed": rng_seed,
        "count_requested": count,
        "revisions_in_72h_window": revisions,
        "analyzed_attached": 0,
        "feed_window_72h_count": payload.get("summary", {}).get("count"),
        "feed_first_page": len(payload.get("items") or []),
        "has_more": payload.get("has_more"),
        "as_of": payload.get("as_of"),
        "data_dir": str(data_dir),
    }
    (data_dir / "seed-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, choices=(100, 1000, 10000), required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Absolute directory; default $HOME/optix-perf-data/n{count}",
    )
    args = parser.parse_args()
    data_dir = args.data_dir or Path.home() / "optix-perf-data" / f"n{args.count}"
    if not data_dir.is_absolute():
        raise SystemExit("data-dir must be absolute")
    manifest = seed(data_dir, args.count, args.seed)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
