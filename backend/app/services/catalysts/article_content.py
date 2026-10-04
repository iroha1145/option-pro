"""Best-effort extraction of public news bodies, without proxy or credential use."""
from __future__ import annotations

import copy
import http.client
import ipaddress
import json
import math
import queue
import re
import socket
import ssl
import threading
import time
from datetime import datetime, timezone
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from lxml import html

from app.services.ai_jobs.models import NEWS_ARTICLE_TEXT_MAX_CHARS

from .news_links import resolve_publisher_url

_MAX_BYTES = 1024 * 1024
_GOOGLE_ARTICLE_PATH = re.compile(r"/articles/[A-Za-z0-9_-]{16,4096}(?:\?hl=en-US&gl=US&ceid=US:en)?\Z")
_NOISE = re.compile(r"(?:^|[-_\s])(nav|menu|footer|header|related|recommend\w*|comment\w*|subscribe\w*|subscription|newsletter|advert\w*|social|share|paywall)(?:$|[-_\s])", re.I)
_CHALLENGE = re.compile(r"just a moment|verify (?:that )?you are human|access denied|captcha|checking your browser|enable javascript and cookies", re.I)
_PAYWALL = re.compile(r"subscribe to (?:continue reading|read (?:the )?(?:full|rest))|subscription (?:is )?required|already a subscriber\??\s*(?:sign|log) in", re.I)


class _Unavailable(Exception):
    pass


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _Unavailable("timeout")
    return remaining


def _validated_url(url: str) -> tuple[str, str, str]:
    if not isinstance(url, str) or len(url) > 4096 or re.search(r"[\x00-\x20\x7f\\]", url):
        raise _Unavailable("unsafe_url")
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
        # This feed still supplies legacy HTTP links for Business Wire. Only
        # upgrade its known article route; never make a plaintext request.
        if parts.scheme == "http" and host in {"businesswire.com", "www.businesswire.com"} and parts.path.startswith("/news/home/") and parts.port in (None, 80):
            parts = parts._replace(scheme="https", netloc=host) if parts.username is None and parts.password is None else parts
        if parts.scheme != "https" or not host or parts.username is not None or parts.password is not None or parts.port not in (None, 443):
            raise ValueError
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")) or "%" in host:
            raise ValueError
        authority = f"[{host}]" if ":" in host else host
        path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
        query = quote(parts.query, safe="/%?:@!$&'()*+,;=-._~")
        normalized = urlunsplit(("https", authority, path, query, ""))
        return normalized, host, path + ("?" + query if query else "")
    except (ValueError, UnicodeError):
        raise _Unavailable("unsafe_url") from None


def _resolve(host: str, deadline: float) -> tuple[int, tuple]:
    # getaddrinfo has no timeout. A daemon prevents a stalled resolver from
    # blocking a news job; only validated numeric addresses reach connect().
    results: queue.Queue = queue.Queue(maxsize=1)

    def lookup():
        try:
            results.put(socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM))
        except Exception as exc:
            results.put(exc)

    threading.Thread(target=lookup, daemon=True).start()
    try:
        addresses = results.get(timeout=_remaining(deadline))
    except queue.Empty:
        raise _Unavailable("timeout") from None
    if isinstance(addresses, Exception) or not addresses:
        raise _Unavailable("dns_error")
    for family, _, _, _, address in addresses:
        ip = ipaddress.ip_address(address[0])
        if family not in (socket.AF_INET, socket.AF_INET6) or not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified or getattr(ip, "ipv4_mapped", None):
            raise _Unavailable("unsafe_address")
    return addresses[0][0], addresses[0][4]


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, address: tuple[int, tuple], deadline: float):
        super().__init__(host, 443, timeout=_remaining(deadline), context=ssl.create_default_context())
        self._address = address
        self._deadline = deadline

    def connect(self):
        family, address = self._address
        raw = socket.socket(family, socket.SOCK_STREAM)
        try:
            raw.settimeout(_remaining(self._deadline))
            raw.connect(address)
            raw.settimeout(_remaining(self._deadline))
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


def _download(url: str, host: str, path: str, deadline: float, *, method: str = "GET", body: bytes | None = None) -> tuple[int, dict, bytes]:
    headers = {"User-Agent": "OptionPro-NewsReader/1.0", "Accept": "text/html, application/xhtml+xml", "Accept-Encoding": "identity", "Connection": "close"}
    allowed_types = {"text/html", "application/xhtml+xml"}
    max_bytes = _MAX_BYTES
    if method == "POST":
        if host != "news.google.com" or path != "/_/DotsSplashUi/data/batchexecute?rpcids=Fbv4je" or not isinstance(body, bytes) or not 0 < len(body) <= 32768:
            raise _Unavailable("unsafe_request")
        headers.update({"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json, text/plain"})
        allowed_types = {"application/json", "text/plain"}
        max_bytes = 100_000
    elif method != "GET" or body is not None:
        raise _Unavailable("unsafe_request")
    elif host == "news.google.com" and _GOOGLE_ARTICLE_PATH.fullmatch(path):
        # The public metadata page can exceed 1 MiB. Publisher pages retain
        # their original limit and all hops share the same wall-clock budget.
        max_bytes = 2_000_000
    address = _resolve(host, deadline)
    connection = _PinnedHTTPSConnection(host, address, deadline)
    timer = None
    response = None
    try:
        connection.connect()
        transport_socket = connection.sock

        def expire():
            try:
                transport_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        timer = threading.Timer(_remaining(deadline), expire)
        timer.daemon = True
        timer.start()
        if body is None:
            connection.request(method, path, headers=headers)
        else:
            connection.request(method, path, body=body, headers=headers)
        connection.sock.settimeout(_remaining(deadline))
        response = connection.getresponse()
        headers = {key.lower(): value for key, value in response.getheaders()}
        if response.status != 200:
            return response.status, headers, b""
        if headers.get("content-encoding", "identity").lower() not in ("identity", ""):
            raise _Unavailable("unsupported_encoding")
        if headers.get("content-type", "").split(";")[0].strip().lower() not in allowed_types:
            raise _Unavailable("unsupported_content_type")
        size = headers.get("content-length")
        if size and (not size.isdigit() or int(size) > max_bytes):
            raise _Unavailable("response_too_large")
        chunks, total = [], 0
        while not response.isclosed():
            # http.client may close the response (and the last socket file
            # handle) as soon as Content-Length bytes have been read.
            # Never touch that socket again after the response reaches EOF.
            # read1 does at most one buffered socket read, so a trickling server
            # cannot reset the entire budget on every byte of a large read().
            transport_socket.settimeout(_remaining(deadline))
            chunk = response.read1(min(65536, max_bytes + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise _Unavailable("response_too_large")
            chunks.append(chunk)
        if size and total != int(size):
            raise _Unavailable("incomplete_response")
        return response.status, headers, b"".join(chunks)
    finally:
        if timer is not None:
            timer.cancel()
        if response is not None:
            response.close()
        connection.close()


def _google_request(url: str, *, method: str = "GET", body: bytes | None = None, deadline: float) -> tuple[int, dict, bytes]:
    normalized, host, path = _validated_url(url)
    article_page = method == "GET" and body is None and _GOOGLE_ARTICLE_PATH.fullmatch(path)
    batch = method == "POST" and path == "/_/DotsSplashUi/data/batchexecute?rpcids=Fbv4je"
    if host != "news.google.com" or not (article_page or batch):
        raise _Unavailable("unsafe_request")
    return _download(normalized, host, path, deadline, method=method, body=body)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _title_matches(expected: str, actual: str) -> bool:
    def tokens(text):
        words = set(re.findall(r"[a-z0-9]{3,}", text.lower())) - {"the", "and", "for", "with", "from", "that", "this", "news"}
        for run in re.findall(r"[\u3400-\u9fff]+", text):
            words.update(run[i:i + 2] for i in range(max(1, len(run) - 1)))
        return words
    wanted = tokens(expected)
    return not expected.strip() or bool(wanted and len(wanted & tokens(actual)) / len(wanted) >= 0.4)


def _excluded_element(element, *, allow_header: bool = False) -> bool:
    if not isinstance(element.tag, str):
        return True
    style = re.sub(r"\s+", "", element.get("style", "").lower())
    labels = element.get("class", "") + " " + element.get("id", "")
    if allow_header and element.tag == "header":
        labels = re.sub(r"(?:^|[-_\s])header(?=$|[-_\s])", " ", labels, flags=re.I)
    return bool(
        element.tag in ("script", "style", "nav", "aside", "footer", "form", "button", "noscript")
        or element.tag == "header" and not allow_header
        or element.get("hidden") is not None
        or element.get("aria-hidden", "").lower() == "true"
        or "display:none" in style
        or "visibility:hidden" in style
        or _NOISE.search(labels)
    )


def _excluded_candidate(element) -> bool:
    return any(_excluded_element(node) for node in [element, *element.iterancestors()])


def _publisher_bodies(root, source_url: str):
    """Use exact, observed publisher containers, never whole-page paragraphs."""
    host = (urlsplit(source_url).hostname or "").lower().rstrip(".")
    classes = {
        "www.fool.com": "article-body",
        "fool.com": "article-body",
        "www.cnbc.com": "ArticleBody-articleBody",
        "cnbc.com": "ArticleBody-articleBody",
    }
    body_class = classes.get(host)
    if not body_class:
        return []
    return root.xpath('//*[contains(concat(" ", normalize-space(@class), " "), $token)]', token=f" {body_class} ")


def _extract(payload: bytes, expected_title: str, *, source_url: str = "") -> tuple[str, bool]:
    # Most public news is UTF-8, including pages without an HTML charset tag.
    # Let lxml honor legacy declarations only when the bytes are not UTF-8.
    try:
        payload.decode("utf-8")
        parser = html.HTMLParser(encoding="utf-8", no_network=True)
    except UnicodeDecodeError:
        parser = html.HTMLParser(no_network=True)
    root = html.fromstring(payload, parser=parser)
    page_title = " ".join(root.xpath("//head/title/text()"))
    if not page_title:
        page_title = " ".join(node.text_content() for node in root.xpath("//h1") if not _excluded_candidate(node))
    if _CHALLENGE.search(page_title):
        raise _Unavailable("challenge_page")
    if any((element.get("content") or element.text_content()).strip().lower() == "false" for element in root.xpath('//*[@itemprop="isAccessibleForFree"]')):
        raise _Unavailable("paywall")
    visible_headings = [node for node in root.xpath("//h1") if not any(_excluded_element(ancestor, allow_header=True) for ancestor in [node, *node.iterancestors()])]
    publisher_title = _clean(visible_headings[0].text_content()) if len(visible_headings) == 1 else page_title
    publisher_bodies = [element for element in _publisher_bodies(root, source_url) if not _excluded_candidate(element)]
    # CNBC uses this wrapper for publicly readable articles as well. Preserve
    # only its DOM text and label it partial; never fill it from script bodies.
    preview_nodes = [node for body in publisher_bodies for node in body.iterdescendants()
                     if "ArticleBody-meteredPaywallPreview" in (node.get("class") or "").split()]
    for body in publisher_bodies:
        if any(("paywall" in (node.get("class") or "").lower() or "paywall" in (node.get("id") or "").lower())
               and node not in preview_nodes for node in [body, *body.iterdescendants()]):
            raise _Unavailable("paywall")
    candidates = []
    for script in root.xpath('//script[@type="application/ld+json"]'):
        try:
            nodes = list(_walk(json.loads(script.text or "")))
        except (ValueError, RecursionError):
            continue
        if any(node.get("isAccessibleForFree") in (False, "false", "False") for node in nodes):
            raise _Unavailable("paywall")
        for node in nodes:
            types = node.get("@type", [])
            types = [types] if isinstance(types, str) else types
            if isinstance(types, list) and any(t in ("NewsArticle", "Article", "https://schema.org/NewsArticle", "https://schema.org/Article") for t in types) and isinstance(node.get("articleBody"), str) and not preview_nodes:
                candidates.append((node["articleBody"], str(node.get("headline") or page_title), False, False))
    elements = list(dict.fromkeys(publisher_bodies + [element for element in root.xpath('//article | //*[@itemprop="articleBody"]') if not _excluded_candidate(element)]))
    if preview_nodes:
        elements = publisher_bodies
    multiple_articles = sum(element.tag == "article" for element in elements) > 1
    for original in elements:
        # A card's own heading takes precedence over the shared browser title.
        # Article headers may contain the legitimate h1, so inspect headings
        # before removing header content, but never borrow nested-card headings.
        own_title = ""
        is_body = original in publisher_bodies or original.get('itemprop') == 'articleBody'
        headings = original.xpath('.//h1')
        if not is_body:
            headings += original.xpath('.//h2')
        for heading in headings:
            blocked = _excluded_element(heading)
            for ancestor in heading.iterancestors():
                if ancestor is original:
                    break
                if ancestor.tag == "article" or _excluded_element(ancestor, allow_header=True):
                    blocked = True
                    break
            if not blocked:
                own_title = _clean(heading.text_content())
                break
        element = copy.deepcopy(original)
        if _PAYWALL.search(_clean(element.text_content())):
            raise _Unavailable("paywall")
        for child in list(element.iterdescendants()):
            if child.tag == "article" or _excluded_element(child):
                if child.getparent() is not None:
                    child.drop_tree()
        paragraphs = [_clean(" ".join(p.itertext())) for p in element.xpath('.//p[not(ancestor::li)] | .//li[not(ancestor::li)] | .//h2 | .//h3')]
        candidates.append(("\n\n".join(p for p in paragraphs if p), own_title or (publisher_title if is_body else page_title if not multiple_articles else ""), is_body, bool(preview_nodes)))
    for body, title, is_body, partial in candidates:
        if not _title_matches(expected_title, title or body[:1500]):
            continue
        paragraphs = [_clean(p) for p in body.splitlines() if _clean(p)]
        unique = list(dict.fromkeys(paragraphs))
        text = "\n\n".join(unique)
        # A titled, explicit body container can hold a short public news brief.
        # Keep the stricter threshold for generic articles and structured data.
        short_brief = is_body and bool(expected_title.strip()) and len(unique) >= 2
        minimum_length = 200 if short_brief else 400
        if len(text) < minimum_length or _CHALLENGE.search(text[:1000]) or _PAYWALL.search(text):
            continue
        # Repeating a short teaser is not an article, even in one JSON string.
        sentences = [_clean(p).lower() for p in re.split(r"[.!?。！？]\s*", text) if _clean(p)]
        unique_sentence_length = len(" ".join(dict.fromkeys(sentences)))
        if sentences and (unique_sentence_length < (180 if short_brief else 300) or unique_sentence_length < len(text) * 0.5):
            continue
        return text[:NEWS_ARTICLE_TEXT_MAX_CHARS], partial or len(text) > NEWS_ARTICLE_TEXT_MAX_CHARS
    raise _Unavailable("no_matching_article_body")


def fetch_article(url: str, *, expected_title: str = "", timeout_seconds: float = 6.0) -> dict:
    """Return a bounded public body, or an unavailable result on every failure.

    No cookies, credentials, environment proxies, JavaScript, or paywall bypass.
    source_url records the last validated URL (including redirects).
    """
    result = {"status": "unavailable", "text": "", "source_url": "", "fetched_at": datetime.now(timezone.utc).isoformat(), "reason": None, "truncated": False}
    deadline = None
    try:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise _Unavailable("timeout")
        deadline = time.monotonic() + min(timeout_seconds, 30.0)
        for redirects in range(4):
            normalized, host, path = _validated_url(url)
            result["source_url"] = normalized
            if host == "news.google.com":
                publisher = resolve_publisher_url(normalized, b"", request=_google_request, deadline=deadline)
                _remaining(deadline)
                if not publisher:
                    raise _Unavailable("publisher_url_unavailable")
                if redirects == 3:
                    raise _Unavailable("redirect_limit")
                url = publisher
                continue
            status, headers, payload = _download(normalized, host, path, deadline)
            _remaining(deadline)
            if status in (301, 302, 303, 307, 308):
                if redirects == 3 or not headers.get("location"):
                    raise _Unavailable("redirect_limit")
                url = urljoin(normalized, headers["location"])
                continue
            if status != 200:
                raise _Unavailable(f"http_{status}")
            text, truncated = _extract(payload, expected_title, source_url=normalized)
            _remaining(deadline)
            result.update(status="available", text=text, truncated=truncated)
            return result
    except _Unavailable as exc:
        result["reason"] = str(exc)
    except (TimeoutError, socket.timeout):
        result["reason"] = "timeout"
    except Exception:
        result["reason"] = "timeout" if deadline is not None and time.monotonic() >= deadline else "fetch_failed"
    return result
