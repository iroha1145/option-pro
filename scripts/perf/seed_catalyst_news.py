#!/usr/bin/env python3
"""Seed a deterministic catalyst-cache.db for isolated performance runs.

Does not call MacroLens, OpenAI, or market vendors. Writes only under DATA_DIR.
Visible feed rows use Simplified Chinese raw title/summary so the public
projection keeps them (same rule as production _displayable_zh). A subset is
left without summary (hidden), a subset has image URLs / multi-source /
near-duplicate titles, and a subset is older than the default 72h window.

Optional Round-6 flags attach analysis through the official
request_analysis → complete → reconcile path (not raw SQL), add a newest
English-only prefix so public pagination must skip hidden rows, and keep
long validated result bodies so the revision fingerprint is not an empty
audit table. Default flags keep the historical n100/n1000/n10000 shape.
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
from app.services.catalysts.config import CatalystSettings  # noqa: E402
from app.services.catalysts.local_intelligence import (  # noqa: E402
    LocalCatalystIntelligence,
    _reset_revision_cache,
)
from app.services.catalysts.personal_service import PersonalCatalystService  # noqa: E402

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
    language: str = "zh",
) -> dict:
    title_kind = ("short", "medium", "long")[news_id % 3]
    summary_kind = "missing" if news_id % 17 == 0 else ("long" if news_id % 5 == 0 else "medium")
    ticker_count = 1 + (news_id % 3 == 0)
    tickers = [TICKERS[(news_id + i) % len(TICKERS)] for i in range(ticker_count)]
    multi = news_id % 7 == 0
    sources = [SOURCES[news_id % len(SOURCES)]]
    if multi:
        sources.append(SOURCES[(news_id + 2) % len(SOURCES)])
    if language == "en":
        title = _en_title(news_id)
        summary = _en_summary(news_id)
    else:
        title = _zh_title(rng, news_id, title_kind)
        if news_id % 11 == 0:
            title = _zh_title(rng, max(1, news_id - 11), title_kind)  # near-duplicate candidate
        summary = _zh_summary(rng, news_id, summary_kind)
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
            "summary": summary,
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


def _en_title(news_id: int) -> str:
    return f"English-only headline {news_id} pending Chinese analysis"


def _en_summary(news_id: int) -> str:
    return f"English-only summary {news_id}. This row stays hidden on the public feed until analysis publishes Chinese copy."


def _finish_job(repository: AIJobRepository, job_id: str, result: dict) -> None:
    owner = f"perf-seed-{job_id}"
    claimed = repository.claim_due(owner, lease_seconds=60)
    if claimed is None or claimed["job_id"] != job_id:
        raise RuntimeError(f"could not claim job {job_id}")
    if repository.mark_submission_started(job_id, owner, daily_limit=4) != "started":
        raise RuntimeError(f"could not start submission {job_id}")
    repository.complete(
        job_id,
        owner,
        result,
        {
            "input_tokens": 80,
            "cached_input_tokens": 0,
            "output_tokens": 40,
            "reasoning_tokens": 0,
            "total_tokens": 120,
        },
    )


def _analysis_result(
    *,
    news_id: int,
    change_sequence: int,
    content_hash: str,
    ticker: str,
    long_body: bool,
    valid: bool,
) -> dict:
    body = (
        "公司进展可能先改变订单预期，再由产能、渠道库存与客户采用速度决定是否进入财报。"
        "宏观利率与风险偏好会放大或削弱同一条新闻的交易映射，不能把情绪分数当成胜率。"
    )
    if long_body:
        body = (body + "补充核对包括同业指引、供应链交货与历史估值分位。") * 12
    title = "芯片企业发布最新进展" if valid else "English only generated title"
    summary = "收入与交付预期同时存在，需继续观察。" if valid else "English only generated summary"
    return {
        "output_language": "zh-CN",
        "news_id": news_id,
        "change_sequence": change_sequence,
        "content_hash": content_hash,
        "title_zh": title,
        "summary_zh": summary,
        "headline_summary": "新品与交付预期同时存在，实际影响仍需观察。",
        "overall_sentiment": 15 if news_id % 2 else -10,
        "classification": "bullish" if news_id % 2 else "bearish",
        "confidence": 60 + (news_id % 20),
        "market_relevance": 70,
        "affected_stocks": [
            {
                "ticker": ticker,
                "company": "标的公司",
                "impact_score": 20 if news_id % 2 else -20,
                "confidence": 65,
                "horizon": "weeks",
                "mechanism": "direct_company",
                "reason": "新闻提到的进展可能改变收入预期，但缺少经审计订单。",
            }
        ],
        "affected_sectors": ["半导体"],
        "affected_commodities": [],
        "causal_summary": body,
        "key_factors": ["客户采用速度", "供应链交付能力", "宏观利率"],
        "uncertainty_notes": ["新闻没有提供经审计的订单数据。"],
        "insufficient_context": False,
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


def seed(
    data_dir: Path,
    count: int,
    rng_seed: int,
    *,
    hidden_newest: int = 0,
    analyze_every: int = 0,
    history_every: int = 0,
    clock: datetime | None = None,
) -> dict:
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
    as_of = clock or NOW
    hidden_newest = max(0, min(hidden_newest, count))
    batch: list[dict] = []
    analyzed = 0
    rejected_history = 0
    with request_owner_access_context(True):
        intelligence.initialize()
        for index in range(count):
            news_id = index + 1
            sequence = index + 1
            if news_id <= hidden_newest:
                available = as_of - timedelta(seconds=news_id)
                language = "en"
            elif news_id % 23 == 0:
                available = as_of - timedelta(hours=80 + (news_id % 40))  # outside 72h
                language = "zh"
            else:
                available = as_of - timedelta(minutes=10 + (news_id * 7) % (70 * 60))
                if hidden_newest and available > as_of - timedelta(seconds=hidden_newest + 5):
                    available = as_of - timedelta(minutes=30 + news_id % 180)
                language = "zh"
            batch.append(
                _change(
                    sequence,
                    news_id,
                    available_at=available,
                    rng=rng,
                    language=language,
                )
            )
            if len(batch) >= 250:
                _apply_page(etl, batch, as_of)
                batch = []
        if batch:
            _apply_page(etl, batch, as_of)
        intelligence.reconcile()
        if analyze_every > 0:
            with intelligence._connect() as connection:
                rows = intelligence._active_revisions(
                    connection, as_of=as_of, window_hours=72
                )
            for row in rows:
                news_id = int(row["news_id"])
                if news_id <= hidden_newest:
                    continue
                if news_id % analyze_every != 0:
                    continue
                tickers = list(row.get("canonical_tickers") or [])
                if not tickers:
                    tickers = json.loads(row.get("source_tickers_json") or "[]")
                ticker = str(tickers[0]) if tickers else "NVDA"
                change_sequence = int(row["change_sequence"])
                content_hash = str(row["content_hash"])
                try:
                    if history_every > 0 and news_id % history_every == 0:
                        rejected = intelligence.request_analysis(
                            news_id, force=False, as_of=as_of
                        )
                        _finish_job(
                            ai,
                            rejected["job_id"],
                            _analysis_result(
                                news_id=news_id,
                                change_sequence=change_sequence,
                                content_hash=content_hash,
                                ticker=ticker,
                                long_body=True,
                                valid=False,
                            ),
                        )
                        intelligence.reconcile()
                        rejected_history += 1
                        job = intelligence.request_analysis(
                            news_id, force=True, as_of=as_of
                        )
                    else:
                        job = intelligence.request_analysis(
                            news_id, force=False, as_of=as_of
                        )
                    _finish_job(
                        ai,
                        job["job_id"],
                        _analysis_result(
                            news_id=news_id,
                            change_sequence=change_sequence,
                            content_hash=content_hash,
                            ticker=ticker,
                            long_body=True,
                            valid=True,
                        ),
                    )
                except Exception as error:
                    print(f"analyze {news_id} failed: {error}", file=sys.stderr)
                    continue
                analyzed += 1
                if analyzed % 50 == 0:
                    print(f"analyzed {analyzed}/{len(rows)}", file=sys.stderr)
                    intelligence.reconcile()
            intelligence.reconcile()
        _reset_revision_cache()
        service = PersonalCatalystService(
            CatalystSettings(cache_db_path=cache_path),
            intelligence=intelligence,
        )
        with request_owner_access_context(False):
            payload = service.feed(as_of=as_of, window_hours=72, limit=12)
            visible = service.feed(
                as_of=as_of,
                window_hours=72,
                limit=12,
                page_mode="visible",
            )
    revisions = payload.get("summary", {}).get("count")
    manifest = {
        "seed": rng_seed,
        "count_requested": count,
        "hidden_newest": hidden_newest,
        "analyze_every": analyze_every,
        "history_every": history_every,
        "revisions_in_72h_window": revisions,
        "analyzed_attached": analyzed,
        "rejected_history": rejected_history,
        "feed_window_72h_count": payload.get("summary", {}).get("count"),
        "feed_first_page": len(payload.get("items") or []),
        "visible_first_page": len(visible.get("items") or []),
        "visible_first_ids": [item.get("news_id") for item in (visible.get("items") or [])],
        "has_more": payload.get("has_more"),
        "as_of": payload.get("as_of"),
        "clock": as_of.isoformat().replace("+00:00", "Z"),
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
    parser.add_argument("--hidden-newest", type=int, default=0)
    parser.add_argument(
        "--analyze-every",
        type=int,
        default=0,
        help="Attach official analysis when news_id %% N == 0; 0 keeps the historical unanalyzed seed",
    )
    parser.add_argument(
        "--history-every",
        type=int,
        default=0,
        help="For matching news_id, publish a rejected attempt before the accepted result",
    )
    parser.add_argument(
        "--wall-clock",
        action="store_true",
        help="Timestamp the window from now so a 72h guest read stays populated",
    )
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
    manifest = seed(
        data_dir,
        args.count,
        args.seed,
        hidden_newest=args.hidden_newest,
        analyze_every=args.analyze_every,
        history_every=args.history_every,
        clock=datetime.now(timezone.utc).replace(microsecond=0) if args.wall_clock else None,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
