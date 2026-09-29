from __future__ import annotations

import base64
import json
import http.client
import socket
import time

import pytest

from app.services.catalysts import article_content as article

TITLE = "Acme reports strong quarterly revenue growth"
BODY = "\n".join(
    f"Acme reported quarterly revenue growth of {number + 10} percent in division {number}. "
    "Management attributed the results to expanding customer demand and new product sales."
    for number in range(8)
)


def page(body=BODY, **extra):
    node = {"@type": "NewsArticle", "headline": TITLE, "articleBody": body, **extra}
    return ('<html><title>' + TITLE + '</title><script type="application/ld+json">' + json.dumps({"@graph": [node]}) + '</script></html>').encode()


def serve(monkeypatch, payload, status=200, headers=None):
    calls = []

    def download(url, host, path, deadline):
        calls.append(url)
        return status, headers or {}, payload

    monkeypatch.setattr(article, "_download", download)
    return calls


def test_json_ld_graph_body_and_timestamp(monkeypatch):
    serve(monkeypatch, page())
    result = article.fetch_article("https://example.com/news#fragment", expected_title=TITLE)
    assert result["status"] == "available"
    assert result["source_url"] == "https://example.com/news"
    assert "division 7" in result["text"]
    assert result["fetched_at"].endswith("+00:00")
    assert result["reason"] is None


def test_article_paragraphs_exclude_comments_and_recommendations(monkeypatch):
    payload = f'<title>{TITLE}</title><article><nav>Site navigation</nav><div class="related-news"><p>Other stories</p></div><p>{BODY}</p><section id="comments"><p>Comment contents</p></section><p hidden>Hidden text</p></article>'
    serve(monkeypatch, payload.encode())
    result = article.fetch_article("https://example.com", expected_title=TITLE)
    assert result["status"] == "available"
    assert "Other stories" not in result["text"]
    assert "Comment contents" not in result["text"]
    assert "Hidden text" not in result["text"]


@pytest.mark.parametrize("payload", [
    page(isAccessibleForFree=False),
    page(isAccessibleForFree="false"),
    page(body="Subscribe to continue reading. " + BODY),
    page(body="A short summary. " * 100),
    page(body=("A fairly long teaser without original reporting " * 8 + ". ") * 10),
    page(body="Short teaser."),
    b'<title>Just a moment</title><article><p>' + BODY.encode() + b'</p></article>',
    b'<meta name="description" content="' + BODY.encode() + b'"><div>' + BODY.encode() + b'</div>',
])
def test_rejects_paywalls_challenges_and_non_articles(monkeypatch, payload):
    serve(monkeypatch, payload)
    result = article.fetch_article("https://example.com")
    assert result["status"] == "unavailable"
    assert result["text"] == ""


def test_rejects_unrelated_title(monkeypatch):
    serve(monkeypatch, page())
    assert article.fetch_article("https://example.com", expected_title="Federal reserve announces interest rate decision")["status"] == "unavailable"


def test_truncates_long_body(monkeypatch):
    serve(monkeypatch, page(body="\n".join(f"Division {n} reported revenue growth driven by additional product sales and strong customer demand during the quarter ending September {n}." for n in range(200))))
    result = article.fetch_article("https://example.com")
    assert result["status"] == "available"
    assert len(result["text"]) == 12000
    assert result["truncated"] is True


@pytest.mark.parametrize("url", ["http://example.com", "https://u:p@example.com", "https://example.com:444/", "https://localhost/", "https://test.local/", "https://example.com/\r\nX-Header:hello", "https://example.com\\@other.com/"])
def test_invalid_urls_never_reach_network(monkeypatch, url):
    calls = serve(monkeypatch, page())
    assert article.fetch_article(url)["reason"] == "unsafe_url"
    assert calls == []


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "192.168.1.1", "100.64.0.1", "192.0.2.1", "224.0.0.1", "::1", "fc00::1", "::ffff:8.8.8.8"])
def test_rejects_nonpublic_dns(monkeypatch, ip):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(family, socket.SOCK_STREAM, 0, "", (ip, 443))])
    with pytest.raises(article._Unavailable, match="unsafe_address"):
        article._resolve("example.com", time.monotonic() + 1)


def test_mixed_public_private_dns_is_rejected(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 443)) for ip in ("8.8.8.8", "127.0.0.1")])
    with pytest.raises(article._Unavailable, match="unsafe_address"):
        article._resolve("example.com", time.monotonic() + 1)


def test_dns_timeout_returns_without_waiting_for_resolver(monkeypatch):
    def slow(*a, **k):
        time.sleep(0.1)
        return []
    monkeypatch.setattr(socket, "getaddrinfo", slow)
    start = time.monotonic()
    assert article.fetch_article("https://example.com", timeout_seconds=0.01)["reason"] == "timeout"
    assert time.monotonic() - start < 0.09


def test_pinned_connection_uses_numeric_address_and_original_tls_name(monkeypatch):
    events = []

    class Socket:
        def settimeout(self, value):
            pass
        def connect(self, address):
            events.append(("connect", address))
        def close(self):
            pass

    class Context:
        def wrap_socket(self, sock, server_hostname):
            events.append(("tls", server_hostname))
            return sock

    connection = article._PinnedHTTPSConnection("example.com", (socket.AF_INET, ("8.8.8.8", 443)), time.monotonic() + 1)
    connection._context = Context()
    monkeypatch.setattr(socket, "socket", lambda *a: Socket())
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: pytest.fail("second DNS lookup"))
    connection.connect()
    assert events == [("connect", ("8.8.8.8", 443)), ("tls", "example.com")]


def test_redirect_to_private_address_revalidated(monkeypatch):
    real_download = article._download
    calls = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 443))])
    def download(url, host, path, deadline):
        calls.append(url)
        if len(calls) == 1:
            return 302, {"location": "https://127.0.0.1/secret"}, b""
        return real_download(url, host, path, deadline)
    monkeypatch.setattr(article, "_download", download)
    assert article.fetch_article("https://example.com")["reason"] == "unsafe_address"


def test_redirect_limit_and_final_url(monkeypatch):
    calls = serve(monkeypatch, b"", 302, {"location": "/again"})
    assert article.fetch_article("https://example.com")["reason"] == "redirect_limit"
    assert len(calls) == 4
    calls.clear()
    def redirect(url, *args):
        calls.append(url)
        return (302, {"location": "/final"}, b"") if len(calls) == 1 else (200, {}, page())
    monkeypatch.setattr(article, "_download", redirect)
    assert article.fetch_article("https://example.com/start")["source_url"] == "https://example.com/final"


@pytest.mark.parametrize("status", [403, 429, 500])
def test_http_failures_are_unavailable(monkeypatch, status):
    serve(monkeypatch, page(), status)
    assert article.fetch_article("https://example.com")["reason"] == f"http_{status}"


def test_unexpected_network_failure_does_not_escape(monkeypatch):
    def fail(*a):
        raise OSError("network failed")
    monkeypatch.setattr(article, "_download", fail)
    assert article.fetch_article("https://example.com")["status"] == "unavailable"


@pytest.mark.parametrize("headers,payload,reason", [
    ({"Content-Length": str(article._MAX_BYTES + 1)}, b"", "response_too_large"),
    ({}, b"a" * (article._MAX_BYTES + 1), "response_too_large"),
    ({"Content-Encoding": "gzip"}, b"", "unsupported_encoding"),
    ({"Content-Type": "application/json"}, b"{}", "unsupported_content_type"),
])
def test_transport_response_limits(monkeypatch, headers, payload, reason):
    class Socket:
        def settimeout(self, value):
            pass
        def shutdown(self, how):
            pass
    class Response:
        status = 200
        offset = 0
        def getheaders(self):
            return {"Content-Type": "text/html", **headers}.items()
        def isclosed(self):
            return self.offset >= len(payload)
        def close(self):
            pass
        def read1(self, size):
            chunk = payload[self.offset:self.offset + size]
            self.offset += len(chunk)
            return chunk
    class Connection:
        sock = Socket()
        def __init__(self, *args):
            pass
        def connect(self):
            pass
        def request(self, method, path, headers):
            assert headers["Accept-Encoding"] == "identity"
            assert not any(k.lower() in ("cookie", "authorization", "proxy-authorization") for k in headers)
        def getresponse(self):
            return Response()
        def close(self):
            pass
    monkeypatch.setattr(article, "_resolve", lambda *a: (socket.AF_INET, ("8.8.8.8", 443)))
    monkeypatch.setattr(article, "_PinnedHTTPSConnection", Connection)
    assert article.fetch_article("https://example.com")["reason"] == reason


def test_utf8_article_without_charset_declaration(monkeypatch):
    title = "公司发布季度收入增长报告"
    body = "\n".join(f"公司第{n}部门公布季度经营结果，客户需求增长带动营业收入上升，管理层在报告中解释了新产品推出和销售渠道扩大的具体影响，后续仍将持续投入研发并改善生产效率。" for n in range(10))
    serve(monkeypatch, f"<title>{title}</title><article><p>{body}</p></article>".encode())
    result = article.fetch_article("https://example.com", expected_title=title)
    assert result["status"] == "available"
    assert "季度经营结果" in result["text"]


@pytest.mark.parametrize("wrapper", [
    '<aside class="related">{}</aside>',
    '<nav>{}</nav>',
    '<footer>{}</footer>',
    '<div hidden>{}</div>',
    '<div aria-hidden="true">{}</div>',
    '<div class="recommended-stories">{}</div>',
    '<section id="comments">{}</section>',
    '<div style="display: none">{}</div>',
    '<div style="visibility: hidden">{}</div>',
])
def test_article_inside_excluded_ancestor_cannot_supply_main_body(monkeypatch, wrapper):
    unrelated = f'<article><h2>Unrelated company announces executive departure</h2><p>{BODY}</p></article>'
    payload = f'<title>{TITLE}</title><article><p>Short main article teaser.</p></article>{wrapper.format(unrelated)}'
    serve(monkeypatch, payload.encode())
    assert article.fetch_article("https://example.com", expected_title=TITLE)["status"] == "unavailable"


def test_hidden_article_itself_is_not_a_candidate(monkeypatch):
    serve(monkeypatch, f'<title>{TITLE}</title><article hidden><p>{BODY}</p></article>'.encode())
    assert article.fetch_article("https://example.com", expected_title=TITLE)["status"] == "unavailable"


@pytest.mark.parametrize("heading_tag", ["h1", "h2"])
def test_each_article_uses_own_heading_over_page_title(monkeypatch, heading_tag):
    payload = f'<title>{TITLE}</title><article><h1>{TITLE}</h1><p>Short teaser.</p></article><article><{heading_tag}>Federal reserve announces interest rate decision</{heading_tag}><p>{BODY}</p></article>'
    serve(monkeypatch, payload.encode())
    assert article.fetch_article("https://example.com", expected_title=TITLE)["status"] == "unavailable"


def test_nested_unrelated_article_is_not_folded_into_main_body(monkeypatch):
    payload = f'<title>{TITLE}</title><article><h1>{TITLE}</h1><p>Short teaser.</p><article><h2>Federal reserve announces interest rate decision</h2><p>{BODY}</p></article></article>'
    serve(monkeypatch, payload.encode())
    assert article.fetch_article("https://example.com", expected_title=TITLE)["status"] == "unavailable"


def test_matching_article_with_header_heading_is_accepted(monkeypatch):
    payload = f'<title>Company news</title><article><header><h1>{TITLE}</h1></header><p>{BODY}</p></article>'
    serve(monkeypatch, payload.encode())
    assert article.fetch_article("https://example.com", expected_title=TITLE)["status"] == "available"


@pytest.mark.parametrize("framing", ["length", "chunked", "eof"])
def test_connection_close_response_owns_socket_until_body_complete(monkeypatch, framing):
    # Use the real HTTPResponse/socket ownership rules, with a local socketpair
    # instead of a network server. getresponse() detaches and closes conn.sock;
    # the response's file handle keeps it alive until the final body read.
    client_socket, server_socket = socket.socketpair()
    payload = page()
    if framing == "length":
        headers = f"Content-Length: {len(payload)}\r\n".encode()
        wire_body = payload
    elif framing == "chunked":
        headers = b"Transfer-Encoding: chunked\r\n"
        wire_body = f"{len(payload):x}\r\n".encode() + payload + b"\r\n0\r\n\r\n"
    else:
        headers = b""
        wire_body = payload
    server_socket.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\n" + headers + b"\r\n" + wire_body)
    server_socket.shutdown(socket.SHUT_WR)
    observations = []

    class Connection(http.client.HTTPConnection):
        def __init__(self, host, address, deadline):
            super().__init__(host)
        def connect(self):
            self.sock = client_socket
        def getresponse(self):
            response = super().getresponse()
            observations.append((self.sock is None, response.fp is not None))
            return response

    monkeypatch.setattr(article, "_resolve", lambda *a: (socket.AF_INET, ("8.8.8.8", 443)))
    monkeypatch.setattr(article, "_PinnedHTTPSConnection", Connection)
    try:
        result = article.fetch_article("https://example.com", expected_title=TITLE)
        assert result["status"] == "available", result["reason"]
        assert observations == [(True, True)]
        assert client_socket.fileno() == -1
    finally:
        client_socket.close()
        server_socket.close()


def test_html_comments_do_not_break_body_extraction(monkeypatch):
    payload = f'<title>{TITLE}</title><article><!-- publisher annotation --><h1>{TITLE}</h1><p>{BODY}</p><!-- end article --></article>'
    serve(monkeypatch, payload.encode())
    assert article.fetch_article("https://example.com", expected_title=TITLE)["status"] == "available"


def test_fool_body_uses_page_title_instead_of_section_heading():
    payload = f'<title>{TITLE}</title><div class="article-body"><h2>What happens next?</h2><p>{BODY}</p></div><article><h2>Other news</h2><p>Related card.</p></article>'
    text, truncated = article._extract(payload.encode(), TITLE, source_url='https://www.fool.com/investing/story/')
    assert 'division 7' in text
    assert 'Related card' not in text
    assert truncated is False
    with pytest.raises(article._Unavailable):
        article._extract(payload.encode(), TITLE, source_url='https://unrelated.example/story')


def test_cnbc_public_preview_is_partial_and_does_not_use_script_body():
    payload = page(body=BODY + '\nScript only hidden ending.').decode() + f'<div class="ArticleBody-articleBody"><div class="ArticleBody-meteredPaywallPreview"><p>{BODY}</p><p hidden>Hidden DOM ending.</p></div></div>'
    text, truncated = article._extract(payload.encode(), TITLE, source_url='https://www.cnbc.com/story')
    assert truncated is True
    assert 'division 7' in text
    assert 'ending' not in text


@pytest.mark.parametrize('restriction', ['<meta itemprop="isAccessibleForFree" content="false">', '<p>Subscribe to continue reading.</p>', '<div class="Paywall-content"><p>Protected text.</p></div>'])
def test_cnbc_explicit_restrictions_are_rejected(restriction):
    payload = f'<title>{TITLE}</title><div class="ArticleBody-articleBody"><div class="ArticleBody-meteredPaywallPreview"><p>{BODY}</p>{restriction}</div></div>'
    with pytest.raises(article._Unavailable, match='paywall'):
        article._extract(payload.encode(), TITLE, source_url='https://www.cnbc.com/story')


def test_short_semantic_body_accepts_list_news_without_duplicating_nested_paragraphs():
    paragraphs = BODY.splitlines()[:2]
    payload = f'<title>{TITLE}</title><div itemprop="articleBody"><ul><li><p>{paragraphs[0]}</p></li><li>{paragraphs[1]}</li></ul><nav><li>Navigation text</li></nav></div>'
    text, truncated = article._extract(payload.encode(), TITLE)
    assert text.count('division 0') == 1
    assert text.count('division 1') == 1
    assert 'Navigation' not in text
    assert truncated is False


def test_short_repeated_teaser_in_body_is_rejected():
    payload = f'<title>{TITLE}</title><div itemprop="articleBody"><p>{"Acme reports growth. " * 15}</p><p>Another short sentence.</p></div>'
    with pytest.raises(article._Unavailable):
        article._extract(payload.encode(), TITLE)


def test_ambiguous_single_article_cannot_borrow_page_identity():
    payload = f'<title>{TITLE}</title><article><h2>What happens next?</h2><p>{BODY}</p></article>'
    with pytest.raises(article._Unavailable, match='no_matching_article_body'):
        article._extract(payload.encode(), TITLE)


def test_only_article_card_cannot_replace_short_main_body():
    payload = f'<title>{TITLE}</title><h1>{TITLE}</h1><div itemprop="articleBody"><p>Short main introduction.</p></div><article><h2><a href="/unrelated">Federal reserve announces interest rate decision</a></h2><p>{BODY}</p></article>'
    with pytest.raises(article._Unavailable, match='no_matching_article_body'):
        article._extract(payload.encode(), TITLE)


def test_publisher_body_matches_visible_headline_when_browser_title_is_shortened():
    payload = f'<title>Acme earnings</title><header id="main-article-header"><h1>{TITLE}</h1></header><div class="article-body"><!-- note --><p>{BODY}</p></div>'
    assert article._extract(payload.encode(), TITLE, source_url='https://www.fool.com/story')[0]


def test_google_legacy_link_fetches_original_and_revalidates_redirects(monkeypatch):
    publisher = b'https://www.fool.com/story'
    article_id = base64.urlsafe_b64encode(b'\x08\x13\x22' + bytes([len(publisher)]) + publisher).decode().rstrip('=')
    payload = f'<title>{TITLE}</title><div class="article-body"><p>{BODY}</p></div>'.encode()
    calls = serve(monkeypatch, payload)
    result = article.fetch_article('https://news.google.com/rss/articles/' + article_id, expected_title=TITLE)
    assert result['status'] == 'available'
    assert result['source_url'] == publisher.decode()
    assert calls == [publisher.decode()]


def test_google_rpc_and_publisher_share_one_deadline(monkeypatch):
    article_id = 'C' * 64
    calls = []
    metadata = f'<div data-n-a-id="{article_id}" data-n-a-ts="1790697600" data-n-a-sg="signature123"></div>'.encode()
    rpc = (")]}'\n" + json.dumps([['wrb.fr', 'Fbv4je', json.dumps(['garturlres', 'https://www.cnbc.com/story'])]])).encode()
    payload = f'<title>{TITLE}</title><div class="ArticleBody-articleBody"><div class="ArticleBody-meteredPaywallPreview"><p>{BODY}</p></div></div>'.encode()

    def download(url, host, path, deadline, *, method='GET', body=None):
        calls.append((url, deadline, method, body))
        return 200, {}, (metadata, rpc, payload)[len(calls) - 1]

    monkeypatch.setattr(article, '_download', download)
    result = article.fetch_article('https://news.google.com/rss/articles/' + article_id, expected_title=TITLE)
    assert result['status'] == 'available'
    assert result['source_url'] == 'https://www.cnbc.com/story'
    assert result['truncated'] is True
    assert [call[2] for call in calls] == ['GET', 'POST', 'GET']
    assert calls[0][0] == 'https://news.google.com/articles/' + article_id + '?hl=en-US&gl=US&ceid=US:en'
    assert calls[1][0] == 'https://news.google.com/_/DotsSplashUi/data/batchexecute?rpcids=Fbv4je'
    assert calls[1][3].startswith(b'f.req=')
    assert len({call[1] for call in calls}) == 1


def test_unresolved_google_page_is_not_treated_as_body(monkeypatch):
    monkeypatch.setattr(article, '_download', lambda *args, **kwargs: (200, {}, page()))
    result = article.fetch_article('https://news.google.com/articles/' + 'C' * 64, expected_title=TITLE)
    assert result['reason'] == 'publisher_url_unavailable'
    assert result['text'] == ''


def test_resolved_publisher_dns_is_checked(monkeypatch):
    monkeypatch.setattr(article, 'resolve_publisher_url', lambda *args, **kwargs: 'https://publisher.example/story')
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 0, '', ('127.0.0.1', 443))])
    result = article.fetch_article('https://news.google.com/articles/' + 'C' * 64)
    assert result['reason'] == 'unsafe_address'


def test_google_resolution_cannot_reset_timeout(monkeypatch):
    def resolve(*args, **kwargs):
        time.sleep(0.02)
        return 'https://publisher.example/story'

    monkeypatch.setattr(article, 'resolve_publisher_url', resolve)
    monkeypatch.setattr(article, '_download', lambda *args, **kwargs: pytest.fail('expired request'))
    assert article.fetch_article('https://news.google.com/articles/' + 'C' * 64, timeout_seconds=0.01)['reason'] == 'timeout'


@pytest.mark.parametrize('url', [
    'https://example.com/_/DotsSplashUi/data/batchexecute?rpcids=Fbv4je',
    'https://news.google.com/elsewhere',
    'https://news.google.com/_/DotsSplashUi/data/batchexecute?rpcids=another',
])
def test_google_callback_rejects_other_post_targets(monkeypatch, url):
    monkeypatch.setattr(article, '_resolve', lambda *args: pytest.fail('unapproved network request'))
    with pytest.raises(article._Unavailable, match='unsafe_request'):
        article._google_request(url, method='POST', body=b'f.req=value', deadline=time.monotonic() + 1)


@pytest.mark.parametrize('url', [
    'http://www.businesswire.com:81/news/home/123',
    'http://www.businesswire.com.evil.example/news/home/123',
    'http://user:password@www.businesswire.com/news/home/123',
    'http://www.businesswire.com/unrelated',
])
def test_http_upgrade_is_limited_to_known_public_article_route(monkeypatch, url):
    calls = serve(monkeypatch, page())
    assert article.fetch_article(url)['reason'] == 'unsafe_url'
    assert calls == []


def test_businesswire_http_url_is_upgraded_without_plaintext_request(monkeypatch):
    calls = serve(monkeypatch, page())
    result = article.fetch_article('http://www.businesswire.com/news/home/123', expected_title=TITLE)
    assert result['status'] == 'available'
    assert calls == ['https://www.businesswire.com/news/home/123']


@pytest.mark.parametrize('host,path,method,payload,content_type,reason', [
    ('news.google.com', '/articles/' + 'C' * 64, 'GET', b'a' * 1_100_000, 'text/html', None),
    ('news.google.com', '/articles/' + 'C' * 64 + '?hl=en-US&gl=US&ceid=US:en', 'GET', b'a' * 1_100_000, 'text/html', None),
    ('example.com', '/articles/' + 'C' * 64, 'GET', b'a' * 1_100_000, 'text/html', 'response_too_large'),
    ('news.google.com', '/articles/' + 'C' * 64, 'GET', b'a' * 2_000_001, 'text/html', 'response_too_large'),
    ('news.google.com', '/_/DotsSplashUi/data/batchexecute?rpcids=Fbv4je', 'POST', b'{}', 'application/json', None),
    ('news.google.com', '/_/DotsSplashUi/data/batchexecute?rpcids=Fbv4je', 'POST', b'a' * 100_001, 'application/json', 'response_too_large'),
    ('news.google.com', '/_/DotsSplashUi/data/batchexecute?rpcids=Fbv4je', 'POST', b'{}', 'text/html', 'unsupported_content_type'),
    ('example.com', '/_/DotsSplashUi/data/batchexecute?rpcids=Fbv4je', 'POST', b'{}', 'application/json', 'unsafe_request'),
])
def test_google_transport_exceptions_are_bounded(monkeypatch, host, path, method, payload, content_type, reason):
    class Socket:
        def settimeout(self, value):
            pass
        def shutdown(self, how):
            pass

    class Response:
        status = 200
        offset = 0
        def getheaders(self):
            return {'Content-Type': content_type}.items()
        def isclosed(self):
            return self.offset >= len(payload)
        def read1(self, size):
            chunk = payload[self.offset:self.offset + size]
            self.offset += len(chunk)
            return chunk
        def close(self):
            pass

    class Connection:
        sock = Socket()
        def __init__(self, *args):
            pass
        def connect(self):
            pass
        def request(self, request_method, request_path, *, headers, body=None):
            assert request_method == method
            assert request_path == path
            assert not any(key.lower() in {'cookie', 'authorization', 'proxy-authorization'} for key in headers)
            if method == 'POST':
                assert headers['Content-Type'] == 'application/x-www-form-urlencoded'
                assert body == b'f.req=value'
        def getresponse(self):
            return Response()
        def close(self):
            pass

    monkeypatch.setattr(article, '_resolve', lambda *args: (socket.AF_INET, ('8.8.8.8', 443)))
    monkeypatch.setattr(article, '_PinnedHTTPSConnection', Connection)
    kwargs = {'method': method, 'body': b'f.req=value'} if method == 'POST' else {}
    if reason:
        with pytest.raises(article._Unavailable, match=reason):
            article._download('https://' + host + path, host, path, time.monotonic() + 1, **kwargs)
    else:
        assert article._download('https://' + host + path, host, path, time.monotonic() + 1, **kwargs)[2] == payload
