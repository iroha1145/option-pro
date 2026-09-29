from __future__ import annotations

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
