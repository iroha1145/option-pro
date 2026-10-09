from __future__ import annotations

import pytest

from app.services.catalysts.news_dedup import (
    compute_content_hash,
    fuzzy_title,
    normalize_title,
    normalize_url,
    publication_bucket,
    similar_titles,
    strip_publisher_suffix,
    titles_match,
)


# 设计稿附录 D：前 6 条内容哈希向量与 News-feed utils/dedup.py 的公式逐字一致。
@pytest.mark.parametrize(
    ("title", "url", "published_at", "expected"),
    [
        (
            "Fed Holds Rates Steady",
            "https://example.com/a",
            "2026-07-15T00:00:00Z",
            "2b17006271ceef7a617c20556163ca7a048a7f4aee2d6d871c492666eab84378",
        ),
        (
            "Fed holds rates steady!!",
            "https://other.com/b",
            "2026-07-15T20:00:00-04:00",
            "7091f8f8f93dcfbaa3ed4808734aacb3eaafea5ed4574b71fe05808f16bb08c8",
        ),
        (
            "Fed holds rates steady",
            "https://example.com/a?utm_source=1",
            None,
            "f0b59ec6cd4bfe76573fb4cbf8d47a82d2605efe6f771e5215fa239bda7e406c",
        ),
        (
            "Fed holds rates steady",
            "",
            None,
            "c0221051fe196fe01ae4f38e014a11d005fc4c36b84f213d64f28beaf24f2595",
        ),
        (
            "",
            "https://example.com/a",
            None,
            "04eb124138012504ac9b90b7b3d7a6f87b05316bcdf951dada78199878214305",
        ),
        (
            "",
            "",
            None,
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        ),
    ],
)
def test_content_hash_matches_news_feed_vectors(title, url, published_at, expected):
    assert compute_content_hash(title, url, published_at) == expected


# 附录 D 的标题相似度用例：规范化后比较，阈值 0.92。
@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        (
            "Nvidia stock jumps after earnings beat expectations on strong data center demand today",
            "Nvidia stock jumps after earnings beat expectations on strong data center demand",
            True,
        ),
        (
            "Nvidia stock jumps after earnings beat expectations",
            "Nvidia stock surges after earnings beat expectations",
            True,
        ),
        ("Fed raises rates by 0.25%", "Fed raises rates by 0.75%", False),
        ("Tesla recalls 100 cars", "Tesla recalls 1000 cars", False),
    ],
)
def test_title_similarity_matches_news_feed_vectors(left, right, expected):
    assert similar_titles(normalize_title(left), normalize_title(right)) is expected
    assert titles_match(left, right) is expected


def test_publisher_suffix_only_changes_fuzzy_matching():
    left = "Stocks fall as yields rise - Reuters"
    right = "Stocks fall as yields rise"

    # News-feed 原样的比较判为不重复；新实现去掉「 - 发布方」后判为重复。
    assert similar_titles(normalize_title(left), normalize_title(right)) is False
    assert titles_match(left, right) is True
    # 后缀不进入内容哈希。
    assert compute_content_hash(left, "", "2026-10-09T12:00:00Z") != compute_content_hash(
        right, "", "2026-10-09T12:00:00Z"
    )


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        (
            "S&P 500, Nasdaq end lower as crude prices jump, chip stocks weigh - Reuters",
            "S&P 500, Nasdaq end lower as crude prices jump, chip stocks weigh",
        ),
        ("Gold hits record high - Investing.com", "Gold hits record high"),
        ("Markets slide on tariff fears - The Wall Street Journal", "Markets slide on tariff fears"),
        # 后缀含数字、首字母小写或标题主体不足三个词时不算发布方。
        ("Apple earnings - Q3 2026", "Apple earnings - Q3 2026"),
        ("Fed holds rates - analysts expect cut", "Fed holds rates - analysts expect cut"),
        ("Stocks rally - CNBC", "Stocks rally - CNBC"),
    ],
)
def test_strip_publisher_suffix_is_conservative(title, expected):
    assert strip_publisher_suffix(title) == expected


def test_fuzzy_title_normalizes_after_stripping():
    assert fuzzy_title("Gold Hits RECORD high!! - Reuters") == "gold hits record high"


def test_normalize_url_drops_tracking_and_sorts_query():
    assert (
        normalize_url(
            "HTTPS://Example.COM:443//a//b?utm_source=x&b=2&a=1&fbclid=z#frag"
        )
        == "https://example.com/a/b?a=1&b=2"
    )
    assert normalize_url("https://www.example.com/a") == "https://www.example.com/a"
    assert normalize_url("http://example.com:8080/a") == "http://example.com:8080/a"
    assert normalize_url("mailto:someone@example.com") == "mailto:someone@example.com"


def test_publication_bucket_uses_the_utc_day():
    assert publication_bucket("2026-07-15T20:00:00-04:00") == "2026-07-16"
    assert publication_bucket("2026-07-15T23:59:59Z") == "2026-07-15"
    assert publication_bucket(None) == "unknown"
    assert publication_bucket("2026-07-15 garbage") == "2026-07-15"
