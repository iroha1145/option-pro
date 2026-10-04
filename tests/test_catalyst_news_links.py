from __future__ import annotations

import base64
import json
import time
from urllib.parse import parse_qs

import pytest

from app.services.catalysts import news_links


ARTICLE_ID = "CBMi" + "A" * 36
GOOGLE_URL = f"https://news.google.com/rss/articles/{ARTICLE_ID}?oc=5"
PUBLISHER = "https://publisher.example/story?x=1&y=2"


def metadata(article_id=ARTICLE_ID, signature="AbIaSL_a-b_C", timestamp="1790698323"):
    return f'<div data-n-a-id="{article_id}" data-n-a-ts="{timestamp}" data-n-a-sg="{signature}"></div>'.encode()


def reply(value=PUBLISHER):
    frame = ["wrb.fr", "Fbv4je", json.dumps(["garturlres", value, 1]), None, None, None, "generic"]
    return b")]}\'\n\n" + json.dumps([frame]).encode()


def test_fixed_google_requests_and_escaped_signed_payload():
    calls = []
    signature = "AbIaSL_a-b_C"

    def request(url, *, method="GET", body=None, deadline):
        calls.append((url, method, body, deadline))
        return (200, {}, metadata(signature=signature)) if method == "GET" else (200, {}, reply())

    deadline = time.monotonic() + 5
    assert news_links.resolve_publisher_url(GOOGLE_URL, request=request, deadline=deadline) == PUBLISHER
    assert calls[0][:2] == (f"https://news.google.com/articles/{ARTICLE_ID}?hl=en-US&gl=US&ceid=US:en", "GET")
    assert calls[1][:2] == (news_links._BATCH, "POST")
    assert all(call[3] == deadline for call in calls)
    envelope = json.loads(parse_qs(calls[1][2].decode())["f.req"][0])
    inner = json.loads(envelope[0][0][1])
    assert inner[0] == "garturlreq"
    assert inner[-3:] == [ARTICLE_ID, 1790698323, signature]


def test_legacy_literal_url_decodes_without_network():
    raw_url = PUBLISHER.encode()
    article_id = base64.urlsafe_b64encode(b"\x08\x13\x22" + bytes([len(raw_url)]) + raw_url + b"\xd2\x01\x00").decode().rstrip("=")

    def no_network(*args, **kwargs):
        pytest.fail("legacy link must not use network")

    assert news_links.resolve_publisher_url(f"https://news.google.com/articles/{article_id}", request=no_network, deadline=time.monotonic() + 2) == PUBLISHER


@pytest.mark.parametrize("url", [
    "http://news.google.com/articles/" + ARTICLE_ID,
    "https://news.google.com.evil.test/articles/" + ARTICLE_ID,
    "https://u:p@news.google.com/articles/" + ARTICLE_ID,
    "https://news.google.com:444/articles/" + ARTICLE_ID,
    "https://news.google.com/rss/search?q=abc",
    "https://news.google.com/articles/" + ARTICLE_ID + "/extra",
    "https://news.google.com/articles/" + ARTICLE_ID + "%2Fother",
])
def test_unsupported_input_never_reaches_network(url):
    assert news_links.resolve_publisher_url(url, request=lambda *a, **k: pytest.fail("network"), deadline=time.monotonic() + 2) is None


@pytest.mark.parametrize("publisher", [
    "http://publisher.example/story",
    "https://127.0.0.1/admin",
    "https://169.254.169.254/latest/meta-data",
    "https://u:p@publisher.example/story",
    "https://localhost/story",
    "https://publisher.example:444/story",
    "javascript:alert(1)",
    "https://publisher.example/\r\nX-Evil: 1",
    "https://news.google.com/read/other",
])
def test_rejects_unsafe_batch_results(publisher):
    def request(url, *, method, body, deadline):
        return (200, {}, metadata()) if method == "GET" else (200, {}, reply(publisher))

    assert news_links.resolve_publisher_url(GOOGLE_URL, request=request, deadline=time.monotonic() + 2) is None


def test_ambiguous_metadata_fails_closed():
    def request(url, *, method, body, deadline):
        return 200, {}, metadata(signature="Signature1") + metadata(signature="Signature2")

    assert news_links.resolve_publisher_url(GOOGLE_URL, request=request, deadline=time.monotonic() + 2) is None


def test_unrelated_link_and_invalid_reply_are_not_used():
    def request(url, *, method, body, deadline):
        return 200, {}, b'<a href="https://publisher.example/possibly-unrelated">Read more</a>' if method == "GET" else b""

    assert news_links.resolve_publisher_url(GOOGLE_URL, request=request, deadline=time.monotonic() + 2) is None
    assert news_links._batch_url(b"[[\"wrb.fr\",\"Fbv4je\",\"https://publisher.example/guess\"]]") is None
    assert news_links._batch_url(b"x" * 100001) is None


def test_deadline_prevents_requests_and_timeout_is_fallback():
    assert news_links.resolve_publisher_url(GOOGLE_URL, request=lambda *a, **k: pytest.fail("network"), deadline=time.monotonic() - 1) is None

    def timeout(*args, **kwargs):
        raise TimeoutError

    assert news_links.resolve_publisher_url(GOOGLE_URL, request=timeout, deadline=time.monotonic() + 2) is None
