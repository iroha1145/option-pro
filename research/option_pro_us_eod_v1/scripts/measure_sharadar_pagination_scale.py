"""Measure paging cost on a deterministic sample before any full download."""

from __future__ import annotations

import json
import sys
import tracemalloc
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from app.services.research_eod_v1.data import sharadar as sharadar_module  # noqa: E402
from app.services.research_eod_v1.data import sharadar_store as store_module  # noqa: E402
from app.services.research_eod_v1.data.sharadar import SharadarClient  # noqa: E402
from app.services.research_eod_v1.data.sharadar_schema import ENV_KEY_NAME, TICKERS_FIELDS  # noqa: E402
from app.services.research_eod_v1.paths import RETURN_PACK_DIR  # noqa: E402

OUT = RETURN_PACK_DIR / "sharadar_v3" / "pagination_scale.json"
PAGE_LIMIT = 500
PAGE_COUNTS = (8, 16)


def _ticker_row(index: int) -> dict:
    row = {field: "" for field in TICKERS_FIELDS}
    row.update({
        "permaticker": str(index),
        "ticker": f"S{index:06d}",
        "name": f"S{index:06d}",
        "exchange": "NASDAQ",
        "isdelisted": "N",
        "category": "Domestic Common Stock",
        "currency": "USD",
        "firstpricedate": "2010-01-04",
        "lastpricedate": "2024-06-28",
    })
    return row


def _pages(page_count: int) -> dict[int, list[dict]]:
    pages = {
        index * PAGE_LIMIT: [_ticker_row(index * PAGE_LIMIT + offset) for offset in range(PAGE_LIMIT)]
        for index in range(page_count)
    }
    pages[page_count * PAGE_LIMIT] = []
    return pages


def _opener(pages: dict[int, list[dict]], counter: dict[str, int]):
    def opener(url: str, follow_redirects: bool = False):
        parts = urlsplit(url)
        if not parts.path.endswith("/tickers"):
            return 200, b"[]", parts.path
        skip = int(parse_qs(parts.query).get("skip", ["0"])[0])
        counter["http_pages"] += 1
        return 200, json.dumps(pages.get(skip, [])).encode(), parts.path

    return opener


def _measure(*, page_count: int, legacy_prefix_rescan: bool) -> dict:
    pages = _pages(page_count)
    counter = {"http_pages": 0, "disk_rows_read": 0}
    real_iter = store_module.iter_jsonl

    def counting_iter(path: Path):
        for row in real_iter(path):
            counter["disk_rows_read"] += 1
            yield row

    real_advance = store_module.advance_checkpoint

    def legacy_advance(*args, **kwargs):
        kwargs["key_index"] = None
        return real_advance(*args, **kwargs)

    store_module.iter_jsonl = counting_iter
    sharadar_module.advance_checkpoint = legacy_advance if legacy_prefix_rescan else real_advance
    try:
        with TemporaryDirectory() as tmp:
            store = Path(tmp)
            client = SharadarClient(
                allow_network=True,
                opener=_opener(pages, counter),
                sleep=lambda _s: None,
            )
            tracemalloc.start()
            payload = client.fetch_all("tickers", store=store, limit=PAGE_LIMIT)
            peak = tracemalloc.get_traced_memory()[1]
            tracemalloc.stop()
            merged_rows = store_module.count_jsonl(store / "tickers.jsonl")
            resumed = client.fetch_all("tickers", store=store, limit=PAGE_LIMIT)
            return {
                "http_pages": counter["http_pages"],
                "disk_rows_read": counter["disk_rows_read"],
                "peak_traced_bytes": peak,
                "row_count": payload["row_count"],
                "complete": payload["complete"],
                "merged_rows": merged_rows,
                "resume_session_rows": resumed["session_row_count"],
                "resume_row_count": resumed["row_count"],
            }
    finally:
        store_module.iter_jsonl = real_iter
        sharadar_module.advance_checkpoint = real_advance


def main() -> int:
    import os

    os.environ.setdefault(ENV_KEY_NAME, "measurement-only-not-a-real-secret")
    samples = []
    for page_count in PAGE_COUNTS:
        legacy = _measure(page_count=page_count, legacy_prefix_rescan=True)
        current = _measure(page_count=page_count, legacy_prefix_rescan=False)
        expected_rows = PAGE_LIMIT * page_count
        samples.append({
            "pages": page_count,
            "rows": expected_rows,
            "prefix_rescan_per_page": legacy,
            "per_page_key_index": current,
            "disk_rows_read_ratio": (
                None if not current["disk_rows_read"] else legacy["disk_rows_read"] / current["disk_rows_read"]
            ),
            "resume_correct": (
                current["row_count"] == expected_rows
                and current["merged_rows"] == expected_rows
                and current["resume_row_count"] == expected_rows
                and current["resume_session_rows"] == 0
            ),
        })
    report = {
        "sample": {
            "page_limit": PAGE_LIMIT,
            "page_counts": list(PAGE_COUNTS),
            "deterministic": True,
            "live_sharadar_request_count": 0,
        },
        "samples": samples,
        "ratio_grows_with_page_count": [item["disk_rows_read_ratio"] for item in samples],
        "resume_correct": all(item["resume_correct"] for item in samples),
        "note": "prefix rescan grows with pages squared; the durable key index reads each page once",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
