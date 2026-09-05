"""Document fallbacks and cache policy through the actual static app/gateway."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import main


class _Runtime:
    mode = "password"
    visitor_ai_actions = False
    visitor_live_pulls = False

    def __init__(self, owner=False):
        self.owner = owner

    def request_is_owner(self, _request):
        return self.owner


def _client(tmp_path, *, owner=False):
    (tmp_path / "index.html").write_text("<!doctype html><html><head></head><body>review shell</body></html>")
    (tmp_path / "assets").mkdir(exist_ok=True)
    (tmp_path / "assets" / "present-abc123.js").write_text("export const value = 1;")
    (tmp_path / "logo.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    app = FastAPI()
    app.mount("/", main._SPAStaticFiles(directory=str(tmp_path), html=True))
    return TestClient(main._GatewayMiddleware(app, access_runtime=_Runtime(owner)), base_url="https://testserver")


@pytest.mark.parametrize("symbol", ["BRK.B", "BF.B", "0700.HK", "^GSPC", "AAPL"])
@pytest.mark.parametrize("owner", [False, True])
def test_stock_deep_links_return_a_noncacheable_document(tmp_path, symbol, owner):
    with _client(tmp_path, owner=owner) as client:
        response = client.get(f"/stock/{symbol}")
        assert response.status_code == 200
        assert "review shell" in response.text
        assert "no-store" in response.headers["cache-control"]
        assert response.headers["cdn-cache-control"] == "no-store"
        assert response.headers["cloudflare-cdn-cache-control"] == "no-store"
        assert "content-security-policy" in response.headers


@pytest.mark.parametrize("path", ["/assets/missing.js", "/static/missing.css", "/favicon.ico.js", "/api/example.js"])
def test_asset_and_api_namespaces_are_not_stock_documents(path):
    assert main._is_spa_document_path(path) is False


def test_missing_asset_returns_uncacheable_404_not_the_page_shell(tmp_path):
    with _client(tmp_path) as client:
        response = client.get("/assets/missing.js")
    assert response.status_code == 404
    assert "review shell" not in response.text
    for header in ["cache-control", "cdn-cache-control", "cloudflare-cdn-cache-control"]:
        assert response.headers[header] == "no-store"


def test_existing_hashed_asset_and_304_keep_long_cache_policy(tmp_path):
    with _client(tmp_path) as client:
        response = client.get("/assets/present-abc123.js")
        conditional = client.get("/assets/present-abc123.js", headers={"If-None-Match": response.headers["etag"]})
        icon = client.get("/logo.svg")
    assert response.status_code == 200
    assert conditional.status_code == 304
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert conditional.headers["cache-control"] == response.headers["cache-control"]
    assert icon.headers["cache-control"] == "public, max-age=300, stale-while-revalidate=60"


@pytest.mark.parametrize("status", [302, 403, 404, 500, 503])
def test_static_error_overrides_existing_browser_and_cdn_cache_headers(status):
    async def app(_scope, _receive, send):
        await send({
            "type": "http.response.start", "status": status,
            "headers": [(header, b"public, max-age=31536000, immutable") for header in [
                b"cache-control", b"cdn-cache-control", b"cloudflare-cdn-cache-control",
            ]],
        })
        await send({"type": "http.response.body", "body": b"test response"})

    client = TestClient(main._GatewayMiddleware(app, access_runtime=_Runtime()))
    try:
        response = client.get("/assets/example.js", follow_redirects=False)
    finally:
        client.close()
    assert response.status_code == status
    for header in ["cache-control", "cdn-cache-control", "cloudflare-cdn-cache-control"]:
        assert response.headers[header] == "no-store"
